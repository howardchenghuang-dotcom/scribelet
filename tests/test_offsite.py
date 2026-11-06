import datetime
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offsite_backup
from offsite_backup import (newest_verified_snapshot, manifest, encrypt, publish,
                            keepers, prune, run, check, digest)


def snapshot(folder, name, rows=1, corrupt=False, sidecar=True):
    path = folder / name
    db = sqlite3.connect(str(path))
    db.executescript('CREATE TABLE records(id TEXT PRIMARY KEY, title TEXT);'
                     'CREATE TABLE parts(record TEXT, idx INTEGER, data BLOB);'
                     'CREATE TABLE segments(record TEXT);'
                     'CREATE TABLE revisions(record TEXT);'
                     'CREATE TABLE sessions(id TEXT);')
    for n in range(rows):
        db.execute('INSERT INTO records VALUES(?,?)', ('r%d' % n, '合成标题 %d' % n))
        db.execute('INSERT INTO parts VALUES(?,?,?)', ('r%d' % n, 0, b'audio' * 10))
    db.commit()
    db.close()
    if sidecar:
        recorded = 'deadbeef' if corrupt else digest(path)
        path.with_suffix('.sha256').write_text(recorded + '  ' + path.name + '\n', encoding='utf-8')
    return path


def fresh_snapshot_name():
    return datetime.datetime.now().strftime('scribelet-%Y%m%d-%H%M%S-%f.sqlite3')


class OffsiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'Scribelet'
        (self.root / 'backups').mkdir(parents=True)
        (self.root / 'logs').mkdir()
        (self.root / 'config').mkdir()
        self.destination = Path(self.temp.name) / 'iCloud'
        self.identity = Path(self.temp.name) / 'key.txt'
        subprocess.run(['age-keygen', '-o', str(self.identity)], check=True, capture_output=True)
        recipient = subprocess.run(['age-keygen', '-y', str(self.identity)],
                                   check=True, capture_output=True, text=True).stdout.strip()
        self.config = self.root / 'config' / 'offsite.json'
        self.config.write_text(json.dumps({'recipient': recipient,
                                           'destination': str(self.destination)}), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_only_a_snapshot_that_passes_both_checks_is_shipped(self):
        backups = self.root / 'backups'
        snapshot(backups, 'scribelet-20260901-010101-000001.sqlite3')
        snapshot(backups, 'scribelet-20260902-010101-000002.sqlite3', corrupt=True)
        snapshot(backups, 'scribelet-20260903-010101-000003.sqlite3', sidecar=False)
        # 摘要对得上，但库本身已损坏：只查摘要不查 integrity 会放它过去
        broken = snapshot(backups, 'scribelet-20260904-010101-000004.sqlite3')
        with broken.open('r+b') as handle:
            handle.seek(1024)
            handle.write(b'\xff' * 4096)
        broken.with_suffix('.sha256').write_text(digest(broken) + '  ' + broken.name + '\n', encoding='utf-8')
        chosen = newest_verified_snapshot(backups)
        self.assertEqual(chosen.name, 'scribelet-20260901-010101-000001.sqlite3',
                         '摘要对不上或没有 .sha256 的快照绝不能被当成可用备份')

    def test_no_verified_snapshot_fails_loudly_instead_of_reporting_success(self):
        snapshot(self.root / 'backups', 'scribelet-20260901-010101-000001.sqlite3', corrupt=True)
        alerts = []
        with patch.object(offsite_backup, 'notify', lambda message: alerts.append(message)):
            with self.assertRaises(SystemExit):
                run(root=self.root, config=self.config)
        self.assertEqual(len(alerts), 1, '最该报警的失败不能是静默的（SystemExit 不是 Exception）')
        self.assertFalse((self.root / 'logs' / 'offsite-last-success.json').exists())
        self.assertEqual(list(self.destination.glob('*')) if self.destination.exists() else [], [])

    def test_round_trip_encrypts_and_restores_with_the_private_key_only(self):
        source = snapshot(self.root / 'backups', fresh_snapshot_name(), rows=3)
        with patch.object(offsite_backup, 'notify'):
            run(root=self.root, config=self.config)
        sealed = list(self.destination.glob('scribelet-*.tar.age'))
        self.assertEqual(len(sealed), 1)
        blob = sealed[0].read_bytes()
        self.assertNotIn(b'SQLite format 3', blob, '产物必须是密文，不能是明文数据库')
        self.assertNotIn('合成标题'.encode(), blob)
        recorded = sealed[0].with_suffix('.sha256').read_text(encoding='utf-8').split()[0]
        self.assertEqual(recorded, digest(sealed[0]))
        # 只用私钥还原，模拟另一台设备
        restored = Path(self.temp.name) / 'restore'
        restored.mkdir()
        subprocess.run(['age', '--decrypt', '-i', str(self.identity),
                        '-o', str(restored / 'snapshot.tar'), str(sealed[0])], check=True)
        with tarfile.open(restored / 'snapshot.tar') as bundle:
            names = bundle.getnames()
            self.assertEqual(sorted(names), ['manifest.json', 'scribelet.sqlite3'])
            for member in bundle.getmembers():
                self.assertTrue(member.isfile() and not member.name.startswith('/')
                                and '..' not in member.name, '归档里不能有绝对路径或链接')
            bundle.extractall(restored, filter='data')
        report = json.loads((restored / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(report['sha256'], digest(source))
        self.assertEqual(digest(restored / 'scribelet.sqlite3'), digest(source), '还原出的库必须逐字节一致')
        db = sqlite3.connect('file:%s?mode=ro' % (restored / 'scribelet.sqlite3'), uri=True)
        try:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM records').fetchone()[0],
                             report['counts']['records'])
        finally:
            db.close()

    def test_a_partial_upload_never_becomes_a_recovery_point(self):
        sealed = Path(self.temp.name) / 'x.tar.age'
        sealed.write_bytes(b'ciphertext')
        with patch.object(offsite_backup.shutil, 'copyfileobj', side_effect=OSError('磁盘满')):
            with self.assertRaises(OSError):
                publish(sealed, self.destination)
        self.assertEqual(list(self.destination.glob('*.tar.age')), [])
        self.assertTrue(list(self.destination.glob('*.partial')), '半成品应当以 .partial 留下，不得改名')

    def test_retention_keeps_daily_weekly_monthly_and_never_empties_the_shelf(self):
        names = ['scribelet-2026%02d%02d-010101-00000%d.tar.age' % (month, day, day % 10)
                 for month in (7, 8, 9) for day in (1, 8, 15, 22)]
        keep = keepers(names)
        self.assertGreaterEqual(len(keep), offsite_backup.KEEP_DAILY)
        self.assertIn(max(names), keep, '最新一份必须保留')
        self.destination.mkdir()
        for name in names:
            (self.destination / name).write_bytes(b'ciphertext-' + name.encode())
            (self.destination / name).with_suffix('.sha256').write_text(
                digest(self.destination / name) + '\n', encoding='utf-8')
        removed = prune(self.destination)
        left = {path.name for path in self.destination.glob('*.tar.age')}
        self.assertEqual(left, keep)
        self.assertGreaterEqual(len(left), offsite_backup.MIN_VERIFIED_REMOTE)
        self.assertTrue(removed)

    def test_retention_refuses_to_thin_when_copies_cannot_be_verified(self):
        """近期副本损坏时，绝不能拿它当理由去删旧的好副本。"""
        self.destination.mkdir()
        names = ['scribelet-2026%02d%02d-010101-00000%d.tar.age' % (month, day, day % 10)
                 for month in (7, 8, 9) for day in (1, 8, 15, 22)]
        for name in names:
            (self.destination / name).write_bytes(b'ciphertext-' + name.encode())
            # 只有最新一份的摘要是对的，其余全部损坏
            recorded = (digest(self.destination / name) if name == max(names) else 'deadbeef')
            (self.destination / name).with_suffix('.sha256').write_text(recorded + '\n', encoding='utf-8')
        self.assertEqual(prune(self.destination), [], '可用恢复点不足两份时不得删除任何副本')
        self.assertEqual(len(list(self.destination.glob('*.tar.age'))), len(names))
        # 补齐第二份可用副本后，裁剪才允许进行
        second = sorted(names, reverse=True)[1]
        (self.destination / second).with_suffix('.sha256').write_text(
            digest(self.destination / second) + '\n', encoding='utf-8')
        self.assertTrue(prune(self.destination), '两份可用副本齐了就应当正常裁剪')

    def test_freshness_check_alerts_when_the_job_never_ran(self):
        alerts = []
        with patch.object(offsite_backup, 'notify', lambda message: alerts.append(message)):
            self.assertEqual(check(root=self.root, config=self.config), 1)
        self.assertIn('尚未有一次成功的异地备份', alerts[0])
        stale = {'finished_utc': (datetime.datetime.now(datetime.timezone.utc)
                                  - datetime.timedelta(hours=30)).isoformat()}
        (self.root / 'logs' / 'offsite-last-success.json').write_text(json.dumps(stale), encoding='utf-8')
        alerts.clear()
        with patch.object(offsite_backup, 'notify', lambda message: alerts.append(message)):
            self.assertEqual(check(root=self.root, config=self.config), 1)
        self.assertIn('距今', alerts[0])
        fresh = {'finished_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        (self.root / 'logs' / 'offsite-last-success.json').write_text(json.dumps(fresh), encoding='utf-8')
        alerts.clear()
        with patch.object(offsite_backup, 'notify', lambda message: alerts.append(message)):
            self.assertEqual(check(root=self.root, config=self.config), 0)
        self.assertEqual(alerts, [])

    def test_stale_local_snapshot_is_never_recorded_as_success(self):
        """本地备份停摆后，异地任务天天重传同一份旧快照——那不是成功。"""
        old = snapshot(self.root / 'backups', 'scribelet-20260901-010101-000001.sqlite3')
        ten_days_ago = (datetime.datetime.now() - datetime.timedelta(days=10)).timestamp()
        os.utime(old, (ten_days_ago, ten_days_ago))
        alerts = []
        with patch.object(offsite_backup, 'notify', lambda message: alerts.append(message)):
            with self.assertRaises(SystemExit):
                run(root=self.root, config=self.config)
            self.assertEqual(check(root=self.root, config=self.config), 1)
        self.assertTrue(any('停摆' in message for message in alerts), alerts)
        self.assertFalse((self.root / 'logs' / 'offsite-last-success.json').exists(),
                         '旧数据不得记为一次成功')
        self.assertTrue(list(self.destination.glob('scribelet-*.tar.age')),
                        '旧快照仍应送出去——有一份旧的也好过没有')

    def test_heartbeat_failure_never_breaks_the_backup(self):
        snapshot(self.root / 'backups', fresh_snapshot_name())
        self.config.write_text(json.dumps(json.loads(self.config.read_text(encoding='utf-8'))
                                          | {'heartbeat_url': 'http://127.0.0.1:1/ping'}), encoding='utf-8')
        with patch.object(offsite_backup, 'notify'):
            run(root=self.root, config=self.config)
        self.assertTrue(list(self.destination.glob('scribelet-*.tar.age')), '心跳打不通也必须完成备份')


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'Scribelet'
        for folder in ('config','logs','venv/bin','app'):
            (self.root/folder).mkdir(parents=True)
        self.agents = Path(self.temp.name)/'LaunchAgents'

    def tearDown(self):
        self.temp.cleanup()

    def test_agents_run_the_right_modes_on_the_right_schedule(self):
        import plistlib
        import setup_offsite
        written = setup_offsite.install_agents(self.root, agent_dir=self.agents, bootstrap=False)
        self.assertEqual(len(written), 2)
        specs = {plistlib.loads(path.read_bytes())['Label']: plistlib.loads(path.read_bytes())
                 for path in written}
        daily = specs[setup_offsite.DAILY]
        hourly = specs[setup_offsite.HOURLY]
        self.assertEqual(daily['ProgramArguments'][-1], 'run')
        self.assertEqual(daily['StartCalendarInterval'], {'Hour': 3, 'Minute': 25})
        self.assertNotIn('StartInterval', daily)
        self.assertEqual(hourly['ProgramArguments'][-1], 'check')
        self.assertEqual(hourly['StartInterval'], 3600)
        for spec in (daily, hourly):
            self.assertEqual(spec['ProgramArguments'][0], str(self.root/'venv/bin/python'))
            self.assertEqual(spec['ProgramArguments'][1], str(self.root/'app/offsite_backup.py'))
        for path in written:
            self.assertEqual(oct(path.stat().st_mode)[-3:], '600')

    def test_config_holds_the_public_key_and_nothing_secret(self):
        import setup_offsite
        config = setup_offsite.write_config(self.root, 'age1publickeyonly', Path('/tmp/where'))
        values = json.loads(config.read_text(encoding='utf-8'))
        self.assertEqual(values['recipient'], 'age1publickeyonly')
        self.assertEqual(values['destination'], '/tmp/where')
        self.assertEqual(oct(config.stat().st_mode)[-3:], '600')
        self.assertNotIn('AGE-SECRET-KEY', config.read_text(encoding='utf-8'),
                         '私钥绝不能落到这台机器上')

    def test_identity_is_kept_private_and_never_group_readable(self):
        import setup_offsite
        identity = setup_offsite.keep_identity(self.root, 'AGE-SECRET-KEY-1TEST\n')
        self.assertEqual(oct(identity.stat().st_mode)[-3:], '600')
        self.assertEqual(identity.read_text(encoding='utf-8'), 'AGE-SECRET-KEY-1TEST\n')
        with self.assertRaises(FileExistsError):
            setup_offsite.keep_identity(self.root, 'another')

    def test_setup_keeps_the_key_locally_and_out_of_the_cloud_folder(self):
        import setup_offsite
        def fake_run(command, **kwargs):
            if Path(command[0]).name == 'age-keygen' and '-o' in command:
                Path(command[command.index('-o')+1]).write_text(
                    'AGE-SECRET-KEY-1TESTTESTTEST\n', encoding='utf-8')
                return subprocess.CompletedProcess(command, 0, '', '')
            if Path(command[0]).name == 'age-keygen' and '-y' in command:
                return subprocess.CompletedProcess(command, 0, 'age1recipient\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')
        destination = Path(self.temp.name)/'iCloud'/'offsite'
        destination.parent.mkdir()
        with patch('setup_offsite.subprocess.run', side_effect=fake_run), \
             patch('setup_offsite.install_agents'):
            setup_offsite.main(root=self.root, destination=destination)
        identity = self.root/'config'/'offsite-identity.txt'
        self.assertIn('AGE-SECRET-KEY', identity.read_text(encoding='utf-8'))
        self.assertEqual(oct(identity.stat().st_mode)[-3:], '600')
        config = (self.root/'config'/'offsite.json').read_text(encoding='utf-8')
        self.assertIn('age1recipient', config)
        self.assertNotIn('AGE-SECRET-KEY', config, '公钥配置里绝不能混进私钥')
        for path in destination.rglob('*'):
            self.assertNotIn('AGE-SECRET-KEY', path.read_text(encoding='utf-8', errors='ignore'),
                             '私钥绝不能出现在 iCloud 目录里')

    def test_setup_refuses_to_silently_replace_an_existing_key(self):
        import setup_offsite
        (self.root/'config'/'offsite.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(SystemExit):
            setup_offsite.main(root=self.root, destination=Path(self.temp.name)/'x'/'y')


class KeyVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'Scribelet'
        (self.root/'config').mkdir(parents=True)
        self.identity = Path(self.temp.name)/'key.txt'
        subprocess.run(['age-keygen', '-o', str(self.identity)], check=True, capture_output=True)
        self.recipient = subprocess.run(['age-keygen', '-y', str(self.identity)],
                                        check=True, capture_output=True, text=True).stdout.strip()
        (self.root/'config'/'offsite.json').write_text(
            json.dumps({'recipient': self.recipient, 'destination': '/tmp/x'}), encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def run_with_clipboard(self, content):
        import verify_offsite_key
        real = subprocess.run
        def fake(command, **kwargs):
            if command == ['pbpaste']:
                return subprocess.CompletedProcess(command, 0, content, '')
            return real(command, **kwargs)
        with patch('verify_offsite_key.subprocess.run', side_effect=fake), \
             patch('builtins.input', return_value=''):
            return verify_offsite_key.main(root=self.root)

    def test_matching_key_is_accepted(self):
        self.assertEqual(self.run_with_clipboard(self.identity.read_text(encoding='utf-8')), 0)

    def test_a_different_key_is_rejected(self):
        other = Path(self.temp.name)/'other.txt'
        subprocess.run(['age-keygen', '-o', str(other)], check=True, capture_output=True)
        self.assertEqual(self.run_with_clipboard(other.read_text(encoding='utf-8')), 1,
                         '存错私钥必须报错，不能放过')

    def test_clipboard_without_a_key_is_rejected(self):
        self.assertEqual(self.run_with_clipboard('这不是密钥'), 2)

    def test_malformed_key_is_rejected(self):
        self.assertEqual(self.run_with_clipboard('AGE-SECRET-KEY-1NOTREALLYAKEY'), 2)

    def test_the_private_key_is_never_left_on_disk(self):
        import verify_offsite_key
        seen = {}
        real_temp = tempfile.TemporaryDirectory
        class Watched(real_temp):
            def __enter__(inner):
                path = real_temp.__enter__(inner)
                seen['workspace'] = Path(path)
                return path
        with patch('verify_offsite_key.tempfile.TemporaryDirectory', Watched):
            verify_offsite_key.public_key_of(self.identity.read_text(encoding='utf-8'))
        self.assertFalse(seen['workspace'].exists(), '临时私钥目录必须已被清除')


class LaunchdEnvironmentTests(unittest.TestCase):
    """The 03:25 job failed three nights in a row with FileNotFoundError: 'age'."""

    def test_age_is_found_without_homebrew_on_path(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
            self.assertTrue(offsite_backup.tool('age').endswith('/age'))
            self.assertTrue(offsite_backup.tool('age-keygen').endswith('/age-keygen'))

    def test_missing_tool_is_a_clear_error_not_a_traceback(self):
        with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}), \
             patch('offsite_backup.shutil.which', return_value=None), \
             patch('offsite_backup.Path.exists', return_value=False):
            with self.assertRaises(SystemExit) as stopped:
                offsite_backup.tool('age')
        self.assertIn('brew install age', str(stopped.exception))

    def test_agent_plists_carry_a_path_that_includes_homebrew(self):
        import plistlib
        import setup_offsite
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)/'Scribelet'
            for folder in ('config','logs','venv/bin','app'): (root/folder).mkdir(parents=True)
            for written in setup_offsite.install_agents(root, agent_dir=Path(temp.name)/'agents', bootstrap=False):
                spec = plistlib.loads(written.read_bytes())
                self.assertIn('/opt/homebrew/bin', spec['EnvironmentVariables']['PATH'])
        finally:
            temp.cleanup()

    def test_reading_a_finished_snapshot_leaves_no_wal_or_shm_behind(self):
        temp = tempfile.TemporaryDirectory()
        try:
            folder = Path(temp.name)
            path = snapshot(folder, 'scribelet-20260901-031000-000001.sqlite3')
            db = sqlite3.connect(str(path)); db.execute('PRAGMA journal_mode=WAL'); db.close()
            for side in ('-wal','-shm'): Path(str(path)+side).unlink(missing_ok=True)
            path.with_suffix('.sha256').write_text(digest(path)+'  '+path.name+'\n', encoding='utf-8')
            self.assertEqual(newest_verified_snapshot(folder), path)
            offsite_backup.manifest(path)
            self.assertEqual(sorted(p.name for p in folder.glob('*.sqlite3*')), [path.name])
        finally:
            temp.cleanup()


if __name__ == '__main__':
    unittest.main()
