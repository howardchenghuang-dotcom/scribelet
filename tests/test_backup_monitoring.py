"""Backup, monitoring and setup regression tests.

Run with SCRIBELET_APP_ROOT pointing at the tree under test.  Every fixture is
synthetic and lives in TemporaryDirectory; no production config/data is read.
"""
import contextlib
import datetime
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

APP_ROOT = Path(os.environ.get("SCRIBELET_APP_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(APP_ROOT))
offsite = importlib.import_module("offsite_backup")
setup = importlib.import_module("setup_offsite")
finish = importlib.import_module("mac_finish_setup")
app_module = importlib.import_module("app")
verify_key = importlib.import_module("verify_offsite_key")


def make_snapshot(folder, name, payload=b"audio"):
    path = folder / name
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE records(id TEXT PRIMARY KEY, title TEXT);"
        "CREATE TABLE parts(record TEXT, idx INTEGER, data BLOB);"
        "CREATE TABLE segments(record TEXT);"
        "CREATE TABLE revisions(record TEXT);"
        "CREATE TABLE sessions(id TEXT);"
    )
    db.execute("INSERT INTO records VALUES('r','synthetic')")
    db.execute("INSERT INTO parts VALUES('r',0,?)", (payload,))
    db.commit()
    db.close()
    path.with_suffix(".sha256").write_text(offsite.digest(path) + "  " + path.name + "\n")
    return path


class OffsiteAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.backups = self.root / "backups"
        self.logs = self.root / "logs"
        self.dest = Path(self.tmp.name) / "remote"
        self.backups.mkdir(parents=True)
        self.logs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_malformed_sidecars_are_skipped_and_uppercase_digest_is_accepted(self):
        good = make_snapshot(self.backups, "scribelet-20260924-031000-000001.sqlite3")
        good.with_suffix(".sha256").write_text(offsite.digest(good).upper() + "  " + good.name + "\n")
        empty = make_snapshot(self.backups, "scribelet-20260924-031002-000003.sqlite3")
        empty.with_suffix(".sha256").write_text("")
        multiline = make_snapshot(self.backups, "scribelet-20260924-031001-000002.sqlite3")
        multiline.with_suffix(".sha256").write_text("garbage\n" + offsite.digest(multiline) + "\n")
        chosen = offsite.newest_verified_snapshot(self.backups)
        self.assertEqual(chosen, good)

    def test_wal_shm_and_bad_timestamp_names_do_not_break_selection(self):
        good = make_snapshot(self.backups, "scribelet-20260924-031000-000001.sqlite3")
        (self.backups / (good.name + "-wal")).write_bytes(b"ignored")
        (self.backups / (good.name + "-shm")).write_bytes(b"ignored")
        bad = make_snapshot(self.backups, "scribelet-ZZZ.sqlite3")
        self.assertEqual(offsite.newest_verified_snapshot(self.backups), good)

    def test_timestamp_not_mtime_controls_snapshot_freshness(self):
        snap = make_snapshot(self.backups, "scribelet-20000101-031000-000001.sqlite3")
        now = datetime.datetime.now().timestamp()
        os.utime(snap, (now, now))
        report = offsite.manifest(snap)
        self.assertGreater(offsite.data_age(report["taken_utc"]), 20 * 365 * 86400)

    def test_future_success_timestamp_is_a_failure_not_fresh_forever(self):
        future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=30)
        (self.logs / "offsite-last-success.json").write_text(json.dumps({"finished_utc": future.isoformat()}))
        config = self.root / "offsite.json"
        config.write_text(json.dumps({"recipient": "synthetic", "destination": str(self.dest)}))
        with patch.object(offsite, "notify"), patch.object(offsite, "heartbeat"):
            self.assertEqual(offsite.check(root=self.root, config=config), 1)

    def test_truncated_success_record_reports_failure_instead_of_crashing(self):
        (self.logs / "offsite-last-success.json").write_text('{"finished_utc":')
        config = self.root / "offsite.json"
        config.write_text(json.dumps({"recipient": "synthetic", "destination": str(self.dest)}))
        alerts = []
        with patch.object(offsite, "notify", alerts.append), patch.object(offsite, "heartbeat"):
            self.assertEqual(offsite.check(root=self.root, config=config), 1)
        self.assertTrue(alerts)

    def test_truncated_config_still_notifies_instead_of_dying_before_error_handler(self):
        config = self.root / "offsite.json"
        config.write_text('{"recipient":')
        alerts = []
        with patch.object(offsite, "notify", alerts.append), patch.object(offsite, "heartbeat"):
            with self.assertRaises(SystemExit):
                offsite.run(root=self.root, config=config)
        self.assertEqual(len(alerts), 1)
        alerts.clear()
        with patch.object(offsite, "notify", alerts.append):
            self.assertEqual(offsite.check(root=self.root, config=config), 1)
        self.assertEqual(len(alerts), 1)

    def test_prune_cleans_abandoned_partial_even_with_too_few_verified_copies(self):
        self.dest.mkdir()
        (self.dest / "old.tar.age.partial").write_bytes(b"abandoned")
        self.assertEqual(offsite.prune(self.dest), [])
        self.assertFalse(list(self.dest.glob("*.partial")))

    def test_daily_retention_means_distinct_days_not_seven_same_day_runs(self):
        names = [f"scribelet-20260924-{hour:02d}0000-000001.tar.age" for hour in range(10, 20)]
        names += [f"scribelet-202609{day:02d}-030000-000001.tar.age" for day in range(17, 24)]
        keep = offsite.keepers(names)
        kept_days = {name[len("scribelet-"):len("scribelet-") + 8] for name in keep}
        self.assertGreaterEqual(len(kept_days), 7)

    def test_publish_without_a_sidecar_never_counts_as_verified(self):
        self.dest.mkdir()
        target = self.dest / "scribelet-20260924-031000-000001.tar.age"
        target.write_bytes(b"ciphertext")
        names = [p.name for p in self.dest.glob("*.tar.age")]
        self.assertEqual(len(names), 1)
        self.assertEqual(offsite.verified(self.dest, names), set(),
                         "密文和摘要不能原子发布，但缺摘要时绝不能算恢复点")

    def test_age_failure_removes_plaintext_workspace(self):
        make_snapshot(self.backups, datetime.datetime.now().strftime(
            "scribelet-%Y%m%d-%H%M%S-%f.sqlite3"))
        config = self.root / "offsite.json"
        config.write_text(json.dumps({"recipient": "not-an-age-recipient",
                                      "destination": str(self.dest)}))
        seen = {}
        real_temp = tempfile.TemporaryDirectory
        class Watched(real_temp):
            def __enter__(inner):
                value = super().__enter__()
                seen["workspace"] = Path(value)
                return value
        with patch.object(offsite.tempfile, "TemporaryDirectory", Watched), \
             patch.object(offsite, "notify"), patch.object(offsite, "heartbeat"):
            with self.assertRaises(subprocess.CalledProcessError):
                offsite.run(root=self.root, config=config)
        self.assertFalse(seen["workspace"].exists())


class SetupAndMigrationAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        for item in ("config", "logs", "venv/bin", "app"):
            (self.root / item).mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_launch_agents_force_private_log_umask(self):
        specs = [setup.agent_plist(setup.DAILY, self.root, "run", {}),
                 setup.agent_plist(setup.HOURLY, self.root, "check", {})]
        self.assertTrue(all(spec.get("Umask") == 0o077 for spec in specs))

    def test_partial_launchagent_install_rolls_back_files_and_loaded_jobs(self):
        agent_dir = Path(self.tmp.name) / "LaunchAgents"
        calls = []
        def fake_run(command, **kwargs):
            calls.append(command)
            if command[:2] == ["launchctl", "bootstrap"] and setup.HOURLY in command[-1]:
                raise subprocess.CalledProcessError(5, command)
            return subprocess.CompletedProcess(command, 0, b"", b"")
        with patch.object(setup.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(subprocess.CalledProcessError):
                setup.install_agents(self.root, agent_dir=agent_dir, bootstrap=True)
        self.assertEqual(list(agent_dir.glob("*.plist")), [])
        rolled_back = {command[-1] for command in calls if command[:2] == ["launchctl", "bootout"]}
        self.assertIn("gui/%d/%s" % (os.getuid(), setup.DAILY), rolled_back)
        self.assertIn("gui/%d/%s" % (os.getuid(), setup.HOURLY), rolled_back)

    def test_setup_install_failure_keeps_key_but_allows_retry(self):
        destination = Path(self.tmp.name) / "iCloud/offsite"
        destination.parent.mkdir()
        def fake_run(command, **kwargs):
            if [Path(command[0]).name, command[1]] == ["age-keygen", "-o"]:
                Path(command[command.index("-o") + 1]).write_text("AGE-SECRET-KEY-1SYNTHETIC\n")
                return subprocess.CompletedProcess(command, 0, "", "")
            if [Path(command[0]).name, command[1]] == ["age-keygen", "-y"]:
                return subprocess.CompletedProcess(command, 0, "age1synthetic\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch.object(setup.os, "geteuid", return_value=501), \
             patch.object(setup.subprocess, "run", side_effect=fake_run), \
             patch.object(setup, "install_agents", side_effect=RuntimeError("synthetic")):
            with self.assertRaises(RuntimeError):
                setup.main(root=self.root, destination=destination)
        self.assertTrue((self.root / "config/offsite-identity.txt").exists())
        self.assertFalse((self.root / "config/offsite.json").exists(),
                         "失败后的配置哨兵不能阻止下一次运行")

    def test_rerun_after_config_loss_reuses_existing_identity(self):
        identity = self.root / "config/offsite-identity.txt"
        identity.write_text("AGE-SECRET-KEY-1SYNTHETIC\n")
        identity.chmod(0o600)
        destination = Path(self.tmp.name) / "iCloud/offsite"
        destination.parent.mkdir()
        commands = []
        def fake_run(command, **kwargs):
            commands.append(command)
            if [Path(command[0]).name, command[1]] == ["age-keygen", "-y"]:
                return subprocess.CompletedProcess(command, 0, "age1existing\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")
        with patch.object(setup.os, "geteuid", return_value=501), \
             patch.object(setup.subprocess, "run", side_effect=fake_run), \
             patch.object(setup, "install_agents"):
            self.assertEqual(setup.main(root=self.root, destination=destination), 0)
        self.assertFalse(any([Path(command[0]).name, command[1]] == ["age-keygen", "-o"] for command in commands))
        values = json.loads((self.root / "config/offsite.json").read_text())
        self.assertEqual(values["recipient"], "age1existing")

    def test_migrated_requires_jobs_loaded_in_system_domain(self):
        daemons = Path(self.tmp.name) / "LaunchDaemons"
        daemons.mkdir()
        for label in finish.LABELS:
            (daemons / (label + ".plist")).write_text("placeholder")
        with patch.object(finish, "loaded", return_value=False):
            self.assertFalse(finish.migrated(daemons))

    def test_stop_waits_for_asynchronous_bootout(self):
        outcomes = iter([True, True, False])
        with patch.object(finish, "loaded", side_effect=lambda _target: next(outcomes)), \
             patch.object(finish.subprocess, "run"):
            self.assertTrue(finish.stop("system/synthetic"))

    def test_stage_failure_during_chown_removes_staged_file(self):
        target = Path(self.tmp.name) / "service.plist"
        with patch.object(finish.os, "chown", side_effect=PermissionError("synthetic")):
            with self.assertRaises(SystemExit):
                finish.stage_plist({"Label": "synthetic"}, target)
        self.assertFalse(target.with_name(target.name + ".new").exists())

    def test_main_does_not_claim_success_when_reload_fails(self):
        config = self.root / "config/private.json"
        config.write_text(json.dumps({"SCRIBELET_OWNER": "owner"}))
        output = io.StringIO()
        with patch.object(finish.os, "geteuid", return_value=501), \
             patch.object(finish, "migrated", return_value=True), \
             patch.object(finish.getpass, "getpass", side_effect=["abcdefghij", "abcdefghij"]), \
             patch.object(finish, "store_password"), \
             patch.object(finish, "reload_app", return_value=False), \
             contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit):
                finish.main(root=self.root)
        self.assertNotIn("设置完成", output.getvalue())


class KeyVerificationAdversarialTests(unittest.TestCase):
    def test_surrounding_clipboard_whitespace_does_not_invalidate_a_real_key(self):
        with tempfile.TemporaryDirectory() as temp:
            identity = Path(temp) / "identity.txt"
            subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
            expected = subprocess.run(["age-keygen", "-y", str(identity)], check=True,
                                      capture_output=True, text=True).stdout.strip()
            secret = identity.read_text(encoding="utf-8")
            self.assertEqual(verify_key.public_key_of(" \n" + secret + "\n  "), expected)


class AppAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.owner = "owner"
        self.password = app_module.password_hash("abcdefghij")
        self.app = app_module.create_app(root=Path(self.tmp.name) / "data", origin="http://testserver",
                                         owner=self.owner, password=self.password,
                                         start_worker=False, preview=True)
        from fastapi.testclient import TestClient
        self.client = TestClient(self.app)
        response = self.client.post("/api/login", headers={"Origin": "http://testserver"},
                                    json={"username": self.owner, "password": "abcdefghij"})
        self.csrf = response.json().get("csrf") or ""
        self.headers = {"Origin": "http://testserver", "X-CSRF-Token": self.csrf}
        # Login response intentionally omits csrf; read it from /api/me.
        self.csrf = self.client.get("/api/me").json()["csrf"]
        self.headers["X-CSRF-Token"] = self.csrf

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_text_only_record_has_no_downloadable_audio(self):
        rid = "a" * 32
        response = self.client.post("/api/import-note", headers=self.headers,
                                    json={"id": rid, "title": "synthetic", "note": "text"})
        self.assertEqual(response.status_code, 200)
        response = self.client.get(f"/api/records/{rid}/audio")
        self.assertIn(response.status_code, (404, 409))

    def test_app_surfaces_missing_or_corrupt_offsite_success_state(self):
        root = Path(self.tmp.name)
        (root / "config").mkdir(exist_ok=True)
        (root / "logs").mkdir(exist_ok=True)
        (root / "config/offsite.json").write_text("{}")
        response = self.client.get("/api/me")
        self.assertIn("尚未成功", response.json()["backup_warning"])
        (root / "logs/offsite-last-success.json").write_text('{"finished_utc":')
        response = self.client.get("/api/me")
        self.assertIn("损坏", response.json()["backup_warning"])

    def test_blank_note_exactly_matches_ecmascript_trim_set(self):
        es = "\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
        self.assertTrue(app_module.blank_note(es))
        for non_es in ("\u0085", "\u180e", "x"):
            self.assertFalse(app_module.blank_note(non_es), repr(non_es))

    def test_stream_closes_database_before_yielding(self):
        class Cursor:
            def fetchone(inner):
                return (b"synthetic",)
        class DB:
            def execute(inner, *_args):
                return Cursor()
        state = {"open": False}
        class Store:
            @contextlib.contextmanager
            def db(inner):
                state["open"] = True
                try:
                    yield DB()
                finally:
                    state["open"] = False
        stream = app_module.stream_parts(Store(), "r", 1)
        self.assertEqual(next(stream), b"synthetic")
        self.assertFalse(state["open"])

    def test_two_edits_of_the_same_version_have_one_winner(self):
        rid = "b" * 32
        self.client.post("/api/import-note", headers=self.headers,
                         json={"id": rid, "title": "synthetic", "note": "original"})
        barrier = threading.Barrier(2)
        def edit(suffix):
            from fastapi.testclient import TestClient
            with TestClient(self.app) as client:
                client.post("/api/login", headers={"Origin": "http://testserver"},
                            json={"username": self.owner, "password": "abcdefghij"})
                csrf = client.get("/api/me").json()["csrf"]
                barrier.wait()
                return client.post(f"/api/records/{rid}/edit",
                                   headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
                                   json={"title": "synthetic " + suffix,
                                         "note": "note " + suffix, "version": 0}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(edit, ("A", "B")))
        self.assertEqual(statuses, [200, 409])
        row = self.client.get(f"/api/records/{rid}").json()
        self.assertEqual(row["version"], 1)

    def test_revision_prune_failure_rolls_back_the_entire_edit(self):
        rid = "c" * 32
        self.client.post("/api/import-note", headers=self.headers,
                         json={"id": rid, "title": "synthetic", "note": "original"})
        store = self.app.state.store
        with store.db() as db:
            for version in range(-25, 0):
                db.execute("INSERT INTO revisions VALUES(?,?,?,?,?)",
                           (rid, version, "old", "old", float(version)))
            db.execute("CREATE TRIGGER reject_revision_delete BEFORE DELETE ON revisions "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic prune failure'); END")
        before = store.record(rid)
        with self.assertRaises(sqlite3.IntegrityError):
            self.client.post(f"/api/records/{rid}/edit", headers=self.headers,
                             json={"title": "changed", "note": "changed", "version": 0})
        after = store.record(rid)
        self.assertEqual((after["title"], after["note"], after["version"]),
                         (before["title"], before["note"], before["version"]))
        with store.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM revisions WHERE record=?", (rid,)).fetchone()[0], 25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
