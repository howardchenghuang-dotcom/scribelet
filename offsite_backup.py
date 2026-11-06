"""Encrypt the newest verified local snapshot and stage it off this machine.

The unattended job uses only the age public key.  A separate 0600 recovery-key
file remains on the Mac for loss prevention, but this module never reads it.
Nothing here reads private.json, the API key or note contents.
"""
from pathlib import Path
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config' / 'offsite.json'
STALE_AFTER = 26 * 3600          # 一天一次 + 两小时宽限
FREE_SPACE_FLOOR = 5 * 1024**3
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY = 7, 4, 3
MIN_VERIFIED_REMOTE = 2          # 永远不把最后的可用副本删掉
SNAPSHOT_RE = re.compile(r'^scribelet-(\d{8})-(\d{6})-(\d{6})\.sqlite3$')
SEALED_RE = re.compile(r'^scribelet-(\d{8})-(\d{6})-(\d{6})\.tar\.age$')
MAX_CLOCK_SKEW = 5 * 60


def tool(name):
    """launchd jobs get a minimal PATH without Homebrew; look in the usual places."""
    found = shutil.which(name)
    if found:
        return found
    for folder in ('/opt/homebrew/bin', '/usr/local/bin'):
        candidate = Path(folder) / name
        if candidate.exists():
            return str(candidate)
    raise SystemExit('找不到 %s。请安装（brew install age）或把它加入 PATH。' % name)


def settings(config=None):
    config = config or CONFIG
    if not config.exists():
        raise SystemExit('缺少 %s；请先运行 setup-offsite 生成配置。' % config)
    try:
        values = json.loads(config.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SystemExit('offsite.json 无法读取或格式损坏：' + type(error).__name__)
    for key in ('recipient', 'destination'):
        if not values.get(key):
            raise SystemExit('offsite.json 缺少 %s' % key)
    return values


def digest(path, chunk=1024 * 1024):
    total = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(chunk), b''):
            total.update(block)
    return total.hexdigest()


def readonly_uri(path):
    # immutable=1: finished snapshots never change, and it keeps SQLite from
    # littering backups/ with -wal/-shm side files on every read.
    return 'file:%s?mode=ro&immutable=1' % urllib.parse.quote(str(path))


def data_age(taken_utc):
    taken = datetime.datetime.fromisoformat(taken_utc)
    if taken.tzinfo is None:
        raise ValueError('时间戳缺少时区')
    return (datetime.datetime.now(datetime.timezone.utc) - taken).total_seconds()


def snapshot_time(path):
    """Use the immutable backup name as data time; mtime can change on copy/touch."""
    match = SNAPSHOT_RE.fullmatch(path.name)
    if not match:
        return None
    local = datetime.datetime.strptime(''.join(match.groups()), '%Y%m%d%H%M%S%f')
    epoch = time.mktime(local.timetuple()) + local.microsecond / 1_000_000
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)


def recorded_digest(sidecar):
    try:
        fields = sidecar.read_text(encoding='utf-8').split()
    except (OSError, UnicodeError):
        return None
    if not fields or not re.fullmatch(r'[0-9a-fA-F]{64}', fields[0]):
        return None
    return fields[0].lower()


def newest_verified_snapshot(backups):
    """Newest local snapshot whose recorded digest and integrity check both pass."""
    snapshots = [path for path in backups.glob('scribelet-*.sqlite3') if snapshot_time(path) is not None]
    for snapshot in sorted(snapshots, key=snapshot_time, reverse=True):
        sidecar = snapshot.with_suffix('.sha256')
        if not sidecar.exists():
            continue
        recorded = recorded_digest(sidecar)
        if recorded != digest(snapshot):
            continue
        db = sqlite3.connect(readonly_uri(snapshot), uri=True, timeout=10)
        try:
            # A badly damaged file raises rather than answering: both mean "skip it",
            # and neither may take the whole job down with it.
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                continue
        except sqlite3.DatabaseError:
            continue
        finally:
            db.close()
        return snapshot
    return None


def manifest(snapshot):
    """Row counts and audio digests, so a restore can be verified without opening any note text."""
    db = sqlite3.connect(readonly_uri(snapshot), uri=True, timeout=10)
    try:
        counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                  for table in ('records', 'parts', 'segments', 'revisions', 'sessions')}
        audio = [{'record': rid, 'parts': parts, 'bytes': size}
                 for rid, parts, size in db.execute(
                     'SELECT record,COUNT(*),COALESCE(SUM(LENGTH(data)),0) FROM parts GROUP BY record ORDER BY record')]
    finally:
        db.close()
    taken = snapshot_time(snapshot)
    if taken is None:
        raise ValueError('本地快照文件名格式无效：' + snapshot.name)
    return {'snapshot': snapshot.name,
            'taken_utc': taken.isoformat(),
            'bytes': snapshot.stat().st_size,
            'sha256': digest(snapshot),
            'counts': counts,
            'audio': audio}


def encrypt(snapshot, recipient, stage):
    """tar(db + manifest) | age -r <public key>. No secret is read here."""
    report = manifest(snapshot)
    (stage / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    archive = stage / 'snapshot.tar'
    with tarfile.open(archive, 'w') as bundle:
        bundle.add(snapshot, arcname='scribelet.sqlite3')
        bundle.add(stage / 'manifest.json', arcname='manifest.json')
    sealed = stage / (snapshot.stem + '.tar.age')
    subprocess.run([tool('age'), '-r', recipient, '-o', str(sealed), str(archive)], check=True)
    archive.unlink()
    return sealed, report


def publish(sealed, destination):
    """Write .partial, fsync, then rename: only the final name counts as staged."""
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / sealed.name
    partial = destination / (sealed.name + '.partial')
    with sealed.open('rb') as source, partial.open('wb') as sink:
        shutil.copyfileobj(source, sink)
        sink.flush()
        os.fsync(sink.fileno())
    os.replace(partial, target)
    sidecar = target.with_suffix('.sha256')
    sidecar_partial = sidecar.with_name(sidecar.name + '.partial')
    with sidecar_partial.open('w', encoding='utf-8') as output:
        output.write(digest(target) + '  ' + target.name + '\n')
        output.flush()
        os.fsync(output.fileno())
    os.replace(sidecar_partial, sidecar)
    return target


def keepers(names):
    """Newest 7 days, then 4 weekly, then 3 monthly, counted from the file stamps."""
    keep, days, weeks, months = set(), [], [], []
    ordered = sorted((name for name in names if SEALED_RE.fullmatch(name)), reverse=True)
    for name in ordered:
        stamp = SEALED_RE.fullmatch(name).group(1)
        moment = datetime.datetime.strptime(stamp, '%Y%m%d')
        day = moment.date()
        if day not in days and len(days) < KEEP_DAILY:
            days.append(day)
            keep.add(name)
        week = moment.isocalendar()[:2]
        if week not in weeks and len(weeks) < KEEP_WEEKLY:
            weeks.append(week)
            keep.add(name)
        month = (moment.year, moment.month)
        if month not in months and len(months) < KEEP_MONTHLY:
            months.append(month)
            keep.add(name)
    return keep


def verified(destination, names):
    """A copy only counts as a recovery point when its recorded digest matches."""
    good = set()
    for name in names:
        sidecar = (destination / name).with_suffix('.sha256')
        if sidecar.exists() and recorded_digest(sidecar) == digest(destination / name):
            good.add(name)
    return good


def prune(destination):
    for leftover in destination.glob('*.partial'):
        leftover.unlink()
    sealed = [path.name for path in destination.glob('scribelet-*.tar.age')]
    keep = keepers(sealed)
    # Never thin the shelf while the recent copies cannot be verified: a corrupt
    # new upload must not be a reason to delete the last good old one.
    if len(verified(destination, keep)) < MIN_VERIFIED_REMOTE:
        return []
    removed = []
    for name in sealed:
        if name not in keep:
            (destination / name).unlink()
            (destination / name).with_suffix('.sha256').unlink(missing_ok=True)
            removed.append(name)
    return removed


def write_json_atomic(path, value):
    temporary = path.with_name(path.name + '.partial')
    with temporary.open('w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=1)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def heartbeat(url, suffix=''):
    if not url:
        return
    try:
        urllib.request.urlopen(url.rstrip('/') + suffix, timeout=10).close()
    except Exception as error:                       # 心跳失败绝不能拖垮备份
        print('WARNING 心跳发送失败：' + type(error).__name__, file=sys.stderr, flush=True)


def notify(message):
    """A log nobody reads is not an alert; put it on the owner's screen."""
    subprocess.run(['osascript', '-e',
                    'display notification %s with title "Scribelet 备份"' % json.dumps(message)],
                   capture_output=True)


def run(root=None, config=None):
    root = root or ROOT
    url = ''
    try:
        values = settings(config)
        url = values.get('heartbeat_url', '')
        heartbeat(url, '/start')
        snapshot = newest_verified_snapshot(root / 'backups')
        if snapshot is None:
            raise SystemExit('没有通过校验的本地快照可供上传；本地备份任务可能已失败。')
        with tempfile.TemporaryDirectory(prefix='scribelet-offsite-') as workspace:
            stage = Path(workspace)
            stage.chmod(0o700)
            sealed, report = encrypt(snapshot, values['recipient'], stage)
            target = publish(sealed, Path(values['destination']).expanduser())
        removed = prune(Path(values['destination']).expanduser())
        # Shipping the newest snapshot is still worth doing when it is old, but an
        # old snapshot means the LOCAL backup has stopped: that is a failure to
        # report, never a success to record.
        if data_age(report['taken_utc']) > STALE_AFTER:
            raise SystemExit('最新可用的本地快照已是 %.0f 小时前的数据，本地备份任务可能已停摆。'
                             % (data_age(report['taken_utc']) / 3600))
        write_json_atomic(root / 'logs' / 'offsite-last-success.json',
            {'sealed': target.name, 'sha256': digest(target), 'bytes': target.stat().st_size,
             'source': report['snapshot'], 'taken_utc': report['taken_utc'],
             'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'pruned': removed})
        print('Offsite staged:', target.name, '| pruned:', len(removed), flush=True)
        heartbeat(url)
    except (Exception, SystemExit) as error:
        # SystemExit is how this module reports its own most important failures;
        # catching only Exception would make exactly those the silent ones.
        heartbeat(url, '/fail')
        notify('异地备份失败：' + (str(error) or type(error).__name__)[:120])
        raise
    return 0


def check(root=None, config=None):
    """Hourly freshness gate: catches the job never running at all."""
    root = root or ROOT
    try:
        values = settings(config)
    except SystemExit as error:
        message = str(error)
        print('WARNING ' + message, file=sys.stderr, flush=True)
        notify(message)
        return 1
    record = root / 'logs' / 'offsite-last-success.json'
    problems = []
    if not record.exists():
        problems.append('尚未有一次成功的异地备份')
    else:
        try:
            last = json.loads(record.read_text(encoding='utf-8'))
            finished_age = data_age(last['finished_utc'])
            if finished_age < -MAX_CLOCK_SKEW:
                problems.append('最近成功时间在未来，系统时钟或状态文件异常')
            elif finished_age > STALE_AFTER:
                problems.append('最近一次异地备份距今 %.1f 小时' % (finished_age / 3600))
            # A job that runs daily but keeps shipping the same old snapshot is stale too.
            if 'taken_utc' in last:
                taken_age = data_age(last['taken_utc'])
                if taken_age < -MAX_CLOCK_SKEW:
                    problems.append('异地副本的数据时间在未来，系统时钟或状态文件异常')
                elif taken_age > 2 * STALE_AFTER:
                    problems.append('异地副本里的数据已是 %.1f 小时前的' % (taken_age / 3600))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            problems.append('异地备份成功记录损坏或格式无效')
    volume = os.statvfs(root)
    free = volume.f_bavail * volume.f_frsize
    if free < FREE_SPACE_FLOOR:
        problems.append('磁盘可用空间不足 %d GB' % (FREE_SPACE_FLOOR // 1024**3))
    if problems:
        message = '；'.join(problems)
        print('WARNING ' + message, file=sys.stderr, flush=True)
        notify(message)
        heartbeat(values.get('heartbeat_url', ''), '/fail')
        return 1
    heartbeat(values.get('heartbeat_url', ''))
    return 0


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'run'
    if mode == 'run':
        return run()
    if mode == 'check':
        return check()
    raise SystemExit('Unknown mode')


if __name__ == '__main__':
    sys.exit(main())
