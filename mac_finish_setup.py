"""Run locally in the owner's Mac terminal; password entry never crosses chat."""
from pathlib import Path
import getpass
import hashlib
import json
import os
import plistlib
import pwd
import secrets
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
LABELS = ('com.scribelet.app','com.scribelet.tunnel','com.scribelet.backup')
# Cloudflare Access (email code) sits in front and the app rate-limits logins
# globally, so length is not what stops an attacker here; reuse of a common
# password is. Keep a floor, but a livable one.
MIN_PASSWORD = 10
MAX_PASSWORD = 256
PASSWORD_PROMPT = '设置 Scribelet 登录密码（至少 %d 个字符，输入不显示）：' % MIN_PASSWORD

def acceptable_password(first, second):
    return first == second and MIN_PASSWORD <= len(first) <= MAX_PASSWORD

def loaded(target):
    """Ask launchd whether a job is actually there, instead of trusting a return code."""
    return subprocess.run(['launchctl', 'print', target], capture_output=True).returncode == 0


def stop(target):
    """Stop a job and prove it stopped. 'Was never loaded' also counts as stopped."""
    subprocess.run(['launchctl', 'bootout', target], capture_output=True)
    for _ in range(20):
        if not loaded(target):
            return True
        time.sleep(0.1)
    return False


def stage_plist(spec, target):
    """Write and verify the new file beside the target, before anything is stopped,
    so a failed write can never replace a working service with a truncated one."""
    staged = target.with_name(target.name + '.new')
    try:
        staged.write_bytes(plistlib.dumps(spec))
        readable = plistlib.loads(staged.read_bytes())
        if readable != spec:
            raise ValueError('plist round-trip mismatch')
        os.chown(staged, 0, 0)
        os.chmod(staged, 0o644)
    except Exception:
        # A short write, a full disk or a corrupt round-trip all mean the same
        # thing here: say so plainly rather than raising a traceback at root.
        staged.unlink(missing_ok=True)
        raise SystemExit('写入 %s 校验失败，未改动任何服务。' % target.name)
    return staged


def archive_power_settings(service_root, account):
    """Keep the pre-change machine-wide power policy so it can be restored."""
    archive=service_root/'config'/'pmset-before.txt'
    if archive.exists():
        return archive
    reading=subprocess.run(['pmset','-g','custom'],capture_output=True,text=True)
    if reading.returncode!=0:
        raise SystemExit('无法读取当前电源策略，未做任何更改。')
    archive.write_text('# Scribelet 修改前的整机电源策略原值 · 还原示例：sudo pmset -a autorestart 0\n'+reading.stdout,encoding='utf-8')
    os.chown(archive,account.pw_uid,account.pw_gid)
    os.chmod(archive,0o600)
    return archive

def install_system(service_root=None, daemon_dir=None, agent_dir=None):
    # System launchd runs these jobs as the owner, never as root.
    # Under sudo the installer runs as root; SUDO_USER is the person who ran it.
    account = pwd.getpwnam(os.environ.get('SUDO_USER') or getpass.getuser())
    home = Path(account.pw_dir)
    service_root = service_root or home/'Library/Application Support/Scribelet'
    daemon_dir = daemon_dir or Path('/Library/LaunchDaemons')
    agent_dir = agent_dir or home/'Library/LaunchAgents'
    if service_root.is_symlink() or not service_root.is_dir():
        raise SystemExit('Unexpected installation path')
    # Validate every file before touching launchd. A rejection after the first
    # bootstrap would leave a system daemon installed beside a surviving user
    # agent, and the next login would start a second consumer and second tunnel.
    specs={}
    agents={}
    for label in LABELS:
        source=service_root/'config'/(label+'.plist')
        spec=plistlib.loads(source.read_bytes())
        if spec.get('Label') != label:
            raise SystemExit('Invalid service label')
        spec['UserName']=account.pw_name
        spec['GroupName']='staff'
        specs[label]=spec
        agent=agent_dir/(label+'.plist')
        if agent.exists():
            if agent.is_symlink() or plistlib.loads(agent.read_bytes()).get('Label')!=label:
                raise SystemExit('Unexpected user service file; leaving it untouched')
            agents[label]=agent
    archive_power_settings(service_root,account)
    domain=f'gui/{account.pw_uid}'
    migrated_now=[]
    for label in LABELS:
        agent=agents.get(label)
        target=daemon_dir/(label+'.plist')
        # No shell execution or root-owned secrets in the plist.
        staged=stage_plist(specs[label],target)
        # A bootout that reports failure, or silently leaves the job running, would
        # put a second consumer on the same database and a second tunnel on the
        # same ID once the daemon starts. Verify, do not assume.
        done='；此前已迁移完成的：'+'、'.join(migrated_now) if migrated_now else ''
        if not stop(domain+'/'+label):
            staged.unlink(missing_ok=True)
            raise SystemExit(label+' 的用户级任务没能停下，已中止。该服务保持原样未改动'+done+'。')
        if target.exists() and not stop('system/'+label):
            staged.unlink(missing_ok=True)
            # The user-level job for this label has just been stopped and is NOT
            # restarted here: the system-level one is still running, and starting
            # both is the double consumer this whole check exists to prevent.
            raise SystemExit(label+' 的系统级任务没能停下，已中止，未写入新配置。它的系统级任务仍在运行；'
                             '若原先还有用户级任务，该用户级任务已被停止'+done+'。')
        os.replace(staged,target)
        try:
            subprocess.run(['launchctl','bootstrap','system',str(target)],check=True)
        except subprocess.CalledProcessError:
            # Never end the migration with the service missing from both domains.
            stop('system/'+label)
            if agent is not None:
                # The user agent is still on disk: drop the daemon and put the
                # service back where it came from — and only claim a rollback
                # that actually happened.
                target.unlink(missing_ok=True)
                if subprocess.run(['launchctl','bootstrap',domain,str(agent)],
                                  capture_output=True).returncode==0:
                    raise SystemExit(label+' 安装失败，该服务已回滚到原来的用户级启动项，其余服务未改动。请重新运行本脚本。')
                raise SystemExit(label+' 安装失败，而且回滚也失败了：这个服务现在没有在运行。请执行\n'
                                 '  launchctl bootstrap '+domain+' '+str(agent)+'\n'
                                 '或者注销后重新登录来恢复；其余服务未改动。')
            # Re-run after an earlier successful migration: there is no user agent
            # left to fall back to, so keep the daemon plist rather than deleting
            # the only way the service can come back.
            raise SystemExit(label+' 重新安装失败，但系统启动项文件已保留；重启本机或手动 '
                             'sudo launchctl bootstrap system '+str(target)+' 即可恢复。其余服务未改动。')
        # Prevent a second consumer/tunnel when the owner next logs into macOS.
        if agent is not None:
            agent.unlink()
        migrated_now.append(label)
    subprocess.run(['pmset','-a','sleep','0','autorestart','1'],check=True)
    print('Scribelet 开机自启及断电后来电启动已配置。其他 launchd 服务未修改。')
    print('注意：pmset 是整机电源策略，同机其他服务一并生效；原值已存档到 config/pmset-before.txt。')

def launch_root_installer():
    source=Path(__file__).resolve()
    python=Path(sys.executable).resolve()
    temporary=Path('/private/tmp')/('scribelet-system-installer-'+secrets.token_hex(12)+'.py')
    subprocess.run(['sudo','/usr/bin/install','-o','root','-g','wheel','-m','0500',str(source),str(temporary)],check=True)
    try:
        subprocess.run(['sudo',str(python),str(temporary),'--install-system'],check=True)
    finally:
        subprocess.run(['sudo','/bin/rm','-f',str(temporary)],check=False)

def migrated(daemon_dir=None):
    """True once the services live in the system domain."""
    daemon_dir = daemon_dir or Path('/Library/LaunchDaemons')
    return all((daemon_dir/(label+'.plist')).exists() and loaded('system/'+label)
               for label in LABELS)


def store_password(config, password):
    values=json.loads(config.read_text(encoding='utf-8'))
    salt=secrets.token_hex(16)
    values['SCRIBELET_PASSWORD_HASH']='scrypt$'+salt+'$'+hashlib.scrypt(
        password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
    temporary=config.with_suffix('.new')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as file:
        json.dump(values,file)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary,config)


def reload_app(system):
    """Restart whichever domain actually owns the app, and say so if it fails."""
    label='com.scribelet.app'
    if system:
        command=['sudo','launchctl','kickstart','-k','system/'+label]
    else:
        command=['launchctl','kickstart','-k','gui/%d/%s'%(os.getuid(),label)]
    if subprocess.run(command,capture_output=True).returncode!=0:
        print('注意：新密码已保存，但重启 Scribelet 失败。请手动执行 `'+' '.join(command)+
              '`，在此之前运行中的服务仍在用旧密码。',file=sys.stderr,flush=True)
        return False
    return True


def main(root=None):
    if len(sys.argv)>1 and sys.argv[1]=='--install-system':
        if os.geteuid()!=0: raise SystemExit('This operation requires sudo.')
        install_system()
        return
    if os.geteuid()==0: raise SystemExit('请先以自己的账户运行，不要给整条命令加 sudo。')
    root = root or ROOT
    config=root/'config/private.json'
    installed=migrated()
    if installed:
        print('系统服务已安装。本次只更改 Scribelet 密码，不会改动 launchd 服务或整机电源策略。')
    else:
        print('Scribelet 最后设置：输入你自己的笔记登录密码，随后仅在本机输入 Mac 管理员密码以安装开机服务。')
    first=getpass.getpass(PASSWORD_PROMPT)
    second=getpass.getpass('再输入一次 Scribelet 密码：')
    if not acceptable_password(first,second):
        raise SystemExit('两次密码不一致或长度不符（至少 %d 个字符），未更改。请重新运行。'%MIN_PASSWORD)
    if not installed:
        # Migrate before touching credentials: a failed install must leave the
        # password exactly as it was, so "未更改" is true for the whole command.
        print('下面的 Password: 是你的 Mac 管理员密码，只在这台 Mac 输入。')
        launch_root_installer()
        installed=True
    store_password(config,first)
    (root/'config/initial-password.txt').unlink(missing_ok=True)
    if not reload_app(installed):
        raise SystemExit('密码已保存，但服务重载失败；修复并重载服务后新密码才会生效。')
    values=json.loads(config.read_text(encoding='utf-8'))
    print('设置完成。登录账号：'+values['SCRIBELET_OWNER'])
    if values.get('SCRIBELET_ORIGIN'):
        print('打开：'+values['SCRIBELET_ORIGIN'])

if __name__=='__main__':
    main()
