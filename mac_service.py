"""Native macOS entry point; secrets stay in a mode-0600 local config."""
from pathlib import Path
import datetime
import json
import os
import sys
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config' / 'private.json'
FREE_SPACE_FLOOR = 5 * 1024**3

def warn_on_disk_pressure(root):
    """Notes, text imports and revisions never touch the audio quota, so total
    disk use is only ever checked here, on the daily backup run."""
    database=sum(f.stat().st_size for f in (root/'data').glob('scribelet.sqlite3*'))
    snapshots=sum(f.stat().st_size for f in (root/'backups').glob('scribelet-*'))
    volume=os.statvfs(root)
    free=volume.f_bavail*volume.f_frsize
    quota=int(os.environ.get('SCRIBELET_STORAGE_BYTES',2*1024**3))
    print('Disk: database %.0f MB, backups %.0f MB, free %.1f GB'
          %(database/1024**2,snapshots/1024**2,free/1024**3),flush=True)
    if free<FREE_SPACE_FLOOR:
        print('WARNING 磁盘可用空间不足 %d GB，请清理后再继续录音。'%(FREE_SPACE_FLOOR//1024**3),
              file=sys.stderr,flush=True)
    if database>quota*1.5:
        print('WARNING 数据库体积已超过音频配额的 1.5 倍；笔记正文与修订不计入配额，'
              '请检查回收站与长笔记编辑历史。',file=sys.stderr,flush=True)

def configure():
    values = json.loads(CONFIG.read_text(encoding='utf-8'))
    for key, value in values.items():
        if key.startswith('SCRIBELET_') or key == 'SILICONFLOW_API_KEY':
            os.environ[key] = str(value)
    os.environ['SCRIBELET_DATA'] = str(ROOT/'data')
    os.environ['SCRIBELET_FFMPEG'] = '/opt/homebrew/bin/ffmpeg'
    os.environ['PATH'] = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    os.umask(0o077)

def main():
    configure()
    mode = sys.argv[1] if len(sys.argv)>1 else 'serve'
    if mode == 'serve':
        import uvicorn
        from app import from_env
        uvicorn.run(from_env(), host='127.0.0.1', port=8876, workers=1,
                    limit_concurrency=30, timeout_keep_alive=5, access_log=False)
    elif mode == 'backup':
        from backup import backup
        try:
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            destination = backup(ROOT/'data/scribelet.sqlite3', ROOT/'backups'/f'scribelet-{stamp}.sqlite3')
            # Bound local backup storage to the latest seven valid snapshots.
            snapshots = sorted((ROOT/'backups').glob('scribelet-*.sqlite3'), reverse=True)
            for old in snapshots[7:]:
                old.unlink()
                old.with_suffix('.sha256').unlink(missing_ok=True)
                for side in ('-wal', '-shm'):
                    Path(str(old) + side).unlink(missing_ok=True)
            print('Backup verified:', destination.name, flush=True)
        finally:
            # A failed snapshot or retention cleanup must not skip disk diagnostics.
            # Independent freshness monitoring is still required if this job never runs.
            try:
                warn_on_disk_pressure(ROOT)
            except Exception as error:
                print('WARNING 磁盘检查失败：'+type(error).__name__,file=sys.stderr,flush=True)
    elif mode == 'health':
        request=urllib.request.Request('http://127.0.0.1:8876/api/health',
                        headers={'Host':urlsplit(os.environ.get('SCRIBELET_ORIGIN','http://localhost')).netloc})
        with urllib.request.urlopen(request,timeout=5) as response:
            assert response.status==200
        print('Scribelet healthy')
    else:
        raise SystemExit('Unknown mode')

if __name__=='__main__':
    main()
