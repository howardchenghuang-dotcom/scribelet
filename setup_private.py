"""One-time setup on the machine that will run Scribelet. Secrets never leave it."""
import getpass
import hashlib
import os
from pathlib import Path
import re
import secrets

DEFAULT_API_BASE = 'https://api.siliconflow.cn/v1'


def ask(prompt, default=''):
    answer = input(f'{prompt} [{default}]: ' if default else f'{prompt}: ').strip()
    return answer or default


def main():
    target = Path(__file__).resolve().parent / 'private.env'
    if target.exists():
        raise SystemExit('private.env already exists; not overwritten. / private.env 已存在，未覆盖。')
    domain = ask('Domain, without https:// / 域名（不含 https://）')
    if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', domain) or '.' not in domain:
        raise SystemExit('Invalid domain / 域名格式无效')
    owner = ask('Login name / 登录账号')
    if not owner or len(owner) > 200 or any(c in owner for c in '\r\n='):
        raise SystemExit('Invalid login name / 账号格式无效')
    password = getpass.getpass('Password, 16+ characters / 登录密码（至少 16 个字符）: ')
    if len(password) < 16 or len(password) > 256 or password != getpass.getpass('Again / 再输入一次: '):
        raise SystemExit('Too short, or the two entries differ / 密码长度不符或两次输入不一致')
    print('\nModel provider: any OpenAI-compatible API with /audio/transcriptions and /chat/completions.')
    print('模型服务：任何兼容 OpenAI 接口、同时提供语音转写和对话的服务都可以。\n')
    base = ask('API base URL / 接口地址', DEFAULT_API_BASE).rstrip('/')
    if not re.fullmatch(r'https?://[^\s]+', base):
        raise SystemExit('Invalid API base URL / 接口地址无效')
    key = getpass.getpass('API key, hidden / API Key（不回显）: ').strip()
    if not key or any(c.isspace() for c in key):
        raise SystemExit('Invalid API key / API Key 格式无效')
    default_asr, default_model = ('FunAudioLLM/SenseVoiceSmall', 'Qwen/Qwen3.5-35B-A3B') \
        if base == DEFAULT_API_BASE else ('whisper-1', '')
    asr = ask('Speech-to-text model / 转写模型', default_asr)
    model = ask('Notes and translation model / 笔记和翻译模型', default_model)
    if not asr or not model:
        raise SystemExit('Both models are required / 两个模型都要填')
    salt = secrets.token_hex(16)
    hashed = 'scrypt$' + salt + '$' + hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                                                     n=16384, r=8, p=1).hex()
    values = {'SCRIBELET_DOMAIN': domain, 'SCRIBELET_ORIGIN': 'https://' + domain, 'SCRIBELET_OWNER': owner,
              'SCRIBELET_PASSWORD_HASH': hashed, 'SCRIBELET_API_BASE': base, 'SCRIBELET_API_KEY': key,
              'SCRIBELET_ASR_MODEL': asr, 'SCRIBELET_MODEL': model, 'SCRIBELET_DAILY_JOBS': '12',
              'SCRIBELET_DAILY_AUDIO_SECONDS': '14400', 'SCRIBELET_DAILY_TOKENS': '1200000',
              'SCRIBELET_STORAGE_BYTES': str(2 * 1024**3)}
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as file:
        file.write('\n'.join(k + '=' + v for k, v in values.items()) + '\n')
    (target.parent / '.env').write_text('SCRIBELET_DOMAIN=' + domain + '\n', encoding='utf-8')
    print('\nSaved to private.env (password stored as a hash only). Next: docker compose up -d --build')
    print('已写入 private.env（密码只存哈希）。下一步：docker compose up -d --build')


if __name__ == '__main__':
    main()
