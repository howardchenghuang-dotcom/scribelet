"""Check the saved private key against the configured public key.

Reads the key from the clipboard so the owner never has to write it to disk,
never prints it, and deletes the temporary copy it needs for age-keygen.
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

from offsite_backup import tool

ROOT = Path(__file__).resolve().parent.parent


def configured_recipient(root=None):
    config = (root or ROOT)/'config'/'offsite.json'
    if not config.exists():
        raise SystemExit('还没有配置异地备份：%s 不存在。' % config)
    return json.loads(config.read_text(encoding='utf-8'))['recipient']


def public_key_of(secret):
    """Derive the public key without ever printing or keeping the private one."""
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='scribelet-verify-') as workspace:
        identity = Path(workspace)/'identity.txt'
        identity.write_text(secret.strip() + '\n', encoding='utf-8')
        result = subprocess.run([tool('age-keygen'), '-y', str(identity)],
                                capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def main(root=None):
    expected = configured_recipient(root)
    print('请先在密码管理器里把那条 age 私钥复制到剪贴板，然后按回车继续。')
    print('（私钥不会显示，也不会写进任何文件。）')
    input('准备好了就按回车：')
    pasted = subprocess.run(['pbpaste'], capture_output=True, text=True)
    if pasted.returncode != 0:
        print('\n✗ 无法读取剪贴板。请确认当前会话可以访问 macOS 剪贴板。', file=sys.stderr)
        return 2
    secret = pasted.stdout
    if 'AGE-SECRET-KEY' not in secret.upper():
        print('\n✗ 剪贴板里不是 age 私钥。请确认复制的是以 AGE-SECRET-KEY-1 开头的那一段。',
              file=sys.stderr)
        return 2
    actual = public_key_of(secret)
    if actual is None:
        print('\n✗ 这段内容不是有效的 age 私钥，无法解析。', file=sys.stderr)
        return 2
    print('\n配置里的公钥：' + expected)
    print('你保存的私钥对应：' + actual)
    if actual == expected:
        print('\n✓ 对得上。这把私钥能解开异地备份，保存正确。')
        print('现在可以复制点别的东西，把私钥从剪贴板清掉。')
        return 0
    print('\n✗ 对不上。你保存的是另一把私钥，现有的异地备份用它解不开。', file=sys.stderr)
    print('处理办法：先保留现有 config/offsite-identity.txt 和异地备份；不要只删 offsite.json。'
          '确认旧密钥无法找回后，再同时归档旧密钥与旧备份并执行换钥。',
          file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
