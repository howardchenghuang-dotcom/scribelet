"""Interactive one-time setup for the offsite backup."""
from pathlib import Path
import json
import os
import plistlib
import subprocess
import sys
import tempfile

from offsite_backup import tool

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DESTINATION = Path.home()/'Library/Mobile Documents/com~apple~CloudDocs/Scribelet-offsite'
DAILY = 'com.scribelet.offsite'
HOURLY = 'com.scribelet.offsite-check'


def agent_plist(label, root, mode, schedule):
    """User agents, not system daemons: iCloud sync and notifications both need
    the owner's login session."""
    spec = {'Label': label,
            'ProgramArguments': [str(root/'venv/bin/python'), str(root/'app/offsite_backup.py'), mode],
            'WorkingDirectory': str(root/'app'),
            'StandardOutPath': str(root/'logs'/(label+'.out.log')),
            'StandardErrorPath': str(root/'logs'/(label+'.err.log')),
            'Umask': 0o077,
            'EnvironmentVariables': {'PATH': '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'},
            'ProcessType': 'Background'}
    spec.update(schedule)
    return spec


def install_agents(root, agent_dir=None, bootstrap=True):
    agent_dir = agent_dir or Path.home()/'Library/LaunchAgents'
    agent_dir.mkdir(parents=True, exist_ok=True)
    written = []
    entries = (
            (DAILY, 'run', {'StartCalendarInterval': {'Hour': 3, 'Minute': 25}}),
            (HOURLY, 'check', {'StartInterval': 3600}))
    for label, mode, schedule in entries:
        target = agent_dir/(label+'.plist')
        target.write_bytes(plistlib.dumps(agent_plist(label, root, mode, schedule)))
        target.chmod(0o600)
        written.append(target)
    if bootstrap:
        try:
            for label, _mode, _schedule in entries:
                target = agent_dir/(label+'.plist')
                subprocess.run(['launchctl', 'bootout', 'gui/%d/%s' % (os.getuid(), label)], capture_output=True)
                subprocess.run(['launchctl', 'bootstrap', 'gui/%d' % os.getuid(), str(target)], check=True)
        except Exception:
            for label, _mode, _schedule in entries:
                subprocess.run(['launchctl', 'bootout', 'gui/%d/%s' % (os.getuid(), label)],
                               capture_output=True)
            for target in written:
                target.unlink(missing_ok=True)
            raise
    return written


def write_config(root, recipient, destination, heartbeat=''):
    config = root/'config'/'offsite.json'
    config.write_text(json.dumps({'recipient': recipient,
                                  'destination': str(destination),
                                  'heartbeat_url': heartbeat}, ensure_ascii=False, indent=1),
                      encoding='utf-8')
    config.chmod(0o600)
    return config


def keep_identity(root, secret):
    """Keep the private key on this Mac, 0600.

    The Mac already holds every note in the clear, so an attacker who owns it
    gains nothing from this file; losing the only copy of the key, on the other
    hand, makes every offsite snapshot permanently unreadable. This file is
    never added to a backup and never written into the iCloud folder.
    """
    identity = root/'config'/'offsite-identity.txt'
    fd = os.open(identity, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(secret)
        handle.flush()
        os.fsync(handle.fileno())
    return identity


def copy_identity(root=None):
    """Put the stored key on the clipboard so it can be escrowed off-machine."""
    identity = (root or ROOT)/'config'/'offsite-identity.txt'
    if not identity.exists():
        raise SystemExit('找不到 %s' % identity)
    subprocess.run(['pbcopy'], input=identity.read_bytes(), check=True)
    print('恢复密钥已复制到剪贴板（未显示）。粘贴到你想保存的地方，然后复制别的内容清掉剪贴板。')
    return 0


def main(root=None, destination=None):
    root = root or ROOT
    if os.geteuid() == 0:
        raise SystemExit('请用自己的账户运行，不要加 sudo。')
    destination = Path(destination or DEFAULT_DESTINATION)
    if not destination.parent.exists():
        raise SystemExit('找不到 iCloud Drive 目录：%s' % destination.parent)
    config = root/'config'/'offsite.json'
    if config.exists():
        raise SystemExit('%s 已存在，未覆盖。普通故障请保留现有配置和恢复密钥；若必须换钥，'
                         '先归档旧恢复密钥与旧备份，再同时移走 offsite.json 和 '
                         'offsite-identity.txt 后运行。' % config)
    os.umask(0o077)
    identity = root/'config'/'offsite-identity.txt'
    if identity.exists():
        if identity.is_symlink() or oct(identity.stat().st_mode)[-3:] != '600':
            raise SystemExit('现有恢复密钥不是普通 0600 文件，未改动配置：%s' % identity)
        derived = subprocess.run([tool('age-keygen'), '-y', str(identity)],
                                 capture_output=True, text=True)
        if derived.returncode != 0:
            raise SystemExit('现有恢复密钥无法解析，未改动配置：%s' % identity)
        recipient = derived.stdout.strip()
    else:
        with tempfile.TemporaryDirectory(prefix='scribelet-key-') as workspace:
            generated = Path(workspace)/'recovery-key.txt'
            subprocess.run([tool('age-keygen'), '-o', str(generated)], check=True, capture_output=True)
            secret = generated.read_text(encoding='utf-8')
            recipient = subprocess.run([tool('age-keygen'), '-y', str(generated)],
                                       check=True, capture_output=True, text=True).stdout.strip()
        identity = keep_identity(root, secret)
    destination.mkdir(parents=True, exist_ok=True)
    write_config(root, recipient, destination)
    try:
        install_agents(root)
    except Exception:
        # Keep the identity so a retry reuses the same recovery key, but remove
        # the config sentinel that would otherwise make the retry impossible.
        config.unlink(missing_ok=True)
        raise
    print('\n恢复密钥已保存在本机：%s（权限 0600，不会进入备份或 iCloud）' % identity)
    print('公钥（不是秘密，可以记录）：' + recipient)
    print('配置：%s' % config)
    print('异地目录：%s' % destination)
    print('每日 03:25 加密上传，每小时检查新鲜度；两个任务都是用户级 LaunchAgent。')
    print('\n正在立刻跑一次验证……')
    result = subprocess.run([str(root/'venv/bin/python'), str(root/'app/offsite_backup.py'), 'run'])
    if result.returncode != 0:
        print('首次备份失败，请把上面的报错发回对话。', file=sys.stderr)
        return result.returncode
    print('完成。')
    print('⚠ 这台 Mac 报废/失窃时，异地备份要靠上面那个密钥文件才能打开。')
    print('  有空把它抄一份到别处：venv/bin/python app/setup_offsite.py copy-key')
    return 0


if __name__ == '__main__':
    sys.exit(copy_identity() if len(sys.argv) > 1 and sys.argv[1] == 'copy-key' else main())
