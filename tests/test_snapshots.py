"""Phase 3.5 — DB snapshot + retention.

Covers the eight behaviors enumerated in the Phase 3.5 brief:

1. A snapshot folder is produced with every present DB + reason.txt.
2. Missing DBs are reported in ``skipped`` instead of raising.
3. ``sqlite3.Connection.backup`` is in use (proved by capturing data
   that lives only in the WAL sidecar at copy time).
4. A crash mid-snapshot leaves only the ``.tmp`` staging dir behind.
5. Prune keeps the 100 most recent.
6. Prune drops anything older than 30 days.
7. Prune intersection rule: 200-in-30d → 100; 50-all-old → 0.
8. A snapshot failure at startup does not block ``create_app``.

Tests use ``tmp_path`` for everything except the create_app test, which
exercises the real factory after resetting its module-level cache.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.config.settings import AppSettings  # noqa: E402
from app.shared.snapshots import runner as snapshot_runner  # noqa: E402
from app.shared.snapshots.runner import (  # noqa: E402
    PruneResult,
    SnapshotResult,
    prune_snapshots,
    snapshot_all_dbs,
)


def _make_settings(tmp_path: Path) -> AppSettings:
    """Build an AppSettings whose DBs live under tmp_path/data."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return AppSettings(
        base_dir=str(tmp_path),
        config_path="",
        env_path="",
        data_dir=str(data_dir),
        config={},
    )


def _init_db(path: Path, *, wal: bool = False) -> None:
    """Create an empty SQLite DB with a single ``t`` table at ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS t (v INTEGER)")
        conn.commit()
    finally:
        conn.close()


def _make_snapshot_dir(root: Path, when: datetime) -> Path:
    """Materialize a fake ``data/recovery/<timestamp>/`` directory."""
    name = when.strftime("%Y%m%d-%H%M%S")
    path = root / name
    path.mkdir(parents=True, exist_ok=False)
    (path / "reason.txt").write_text("test", encoding="utf-8")
    return path


class SnapshotAllDbsTests(unittest.TestCase):
    """``snapshot_all_dbs`` behavior under happy + edge cases."""

    def setUp(self) -> None:
        import tempfile
        self._tempdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tempdir.name)
        self.settings = _make_settings(self.tmp_path)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_snapshot_creates_folder_with_all_dbs(self) -> None:
        # Seed three of the six known DBs; leave the other three absent
        # so the test exercises both branches in the same run.
        _init_db(Path(self.settings.inventory_db_path))
        _init_db(Path(self.settings.history_db_path))
        _init_db(Path(self.settings.work_order_db_path))

        result = snapshot_all_dbs(self.settings, reason="startup")

        self.assertIsInstance(result, SnapshotResult)
        self.assertTrue(result.path.exists())
        self.assertEqual(result.db_count, 3)
        self.assertGreater(result.bytes_written, 0)
        self.assertEqual(
            (result.path / "reason.txt").read_text(encoding="utf-8"),
            "startup",
        )
        for expected in ("FilamentInventory.db", "print_history.db",
                         "work_orders.db"):
            self.assertTrue((result.path / expected).is_file(),
                            "snapshot is missing " + expected)

    def test_snapshot_skips_missing_db(self) -> None:
        _init_db(Path(self.settings.inventory_db_path))

        result = snapshot_all_dbs(self.settings, reason="startup")

        self.assertEqual(result.db_count, 1)
        # The five absent DBs land in ``skipped`` by basename.
        self.assertEqual(set(result.skipped), {
            "print_history.db",
            "assignments.db",
            "production_log.db",
            "work_orders.db",
            "upload_sessions.db",
        })
        self.assertTrue((result.path / "FilamentInventory.db").is_file())

    def test_snapshot_uses_backup_api_not_filecopy(self) -> None:
        # Write a row through a WAL connection and leave the WAL
        # uncheckpointed. A plain shutil.copy of the .db file would
        # miss the row; sqlite3 .backup() will pick it up because it
        # reads through the WAL.
        src = Path(self.settings.work_order_db_path)
        _init_db(src, wal=True)
        live = sqlite3.connect(str(src))
        try:
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("INSERT INTO t (v) VALUES (42)")
            live.commit()
            # Sanity check the row is currently parked in the WAL.
            self.assertTrue((src.parent / (src.name + "-wal")).exists())

            result = snapshot_all_dbs(self.settings, reason="manual")
        finally:
            live.close()

        snap_db = result.path / "work_orders.db"
        # The snapshot file must stand alone — no WAL sidecar exists
        # next to it — so the row can only be there if backup() picked
        # it up rather than a raw byte-copy of the main .db file.
        self.assertFalse((snap_db.parent / (snap_db.name + "-wal")).exists())
        snap_conn = sqlite3.connect(str(snap_db))
        try:
            rows = snap_conn.execute("SELECT v FROM t").fetchall()
        finally:
            snap_conn.close()
        self.assertEqual(rows, [(42,)])

    def test_snapshot_atomic_rename(self) -> None:
        _init_db(Path(self.settings.inventory_db_path))
        _init_db(Path(self.settings.history_db_path))

        original = snapshot_runner._snapshot_one_db
        call_count = {"n": 0}

        def flaky(src: Path, dst: Path) -> int:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated crash")
            return original(src, dst)

        with mock.patch.object(snapshot_runner, "_snapshot_one_db", flaky):
            with self.assertRaises(RuntimeError):
                snapshot_all_dbs(self.settings, reason="startup")

        recovery_root = Path(self.settings.data_dir) / "recovery"
        children = list(recovery_root.iterdir())
        # No final timestamped folder should exist — only the staging
        # ``.tmp`` directory survives a mid-snapshot crash.
        self.assertEqual(len(children), 1)
        self.assertTrue(children[0].name.endswith(".tmp"))


class PruneSnapshotsTests(unittest.TestCase):
    """``prune_snapshots`` retention rule."""

    def setUp(self) -> None:
        import tempfile
        self._tempdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tempdir.name)
        self.settings = _make_settings(self.tmp_path)
        self.recovery_root = Path(self.settings.data_dir) / "recovery"
        self.recovery_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_prune_keeps_100_most_recent(self) -> None:
        # 150 snapshots all within the last 2.5 hours — pure count test.
        now = datetime.now()
        for i in range(150):
            _make_snapshot_dir(self.recovery_root,
                               now - timedelta(minutes=i))

        result = prune_snapshots(self.settings)

        self.assertIsInstance(result, PruneResult)
        self.assertEqual(result.kept, 100)
        self.assertEqual(len(result.removed), 50)
        remaining = [p for p in self.recovery_root.iterdir() if p.is_dir()]
        self.assertEqual(len(remaining), 100)

    def test_prune_drops_anything_older_than_30_days(self) -> None:
        now = datetime.now()
        young = [now - timedelta(days=d) for d in range(20)]  # 20 in-window
        old = [now - timedelta(days=31 + d) for d in range(30)]  # 30 stale
        for ts in young + old:
            _make_snapshot_dir(self.recovery_root, ts)

        result = prune_snapshots(self.settings)

        self.assertEqual(result.kept, 20)
        self.assertEqual(len(result.removed), 30)

    def test_prune_intersection_rule(self) -> None:
        # Case A: 200 fresh snapshots → count window wins, 100 remain.
        now = datetime.now()
        for i in range(200):
            _make_snapshot_dir(self.recovery_root,
                               now - timedelta(minutes=i))

        result_a = prune_snapshots(self.settings)
        self.assertEqual(result_a.kept, 100)

        # Clean slate.
        for entry in self.recovery_root.iterdir():
            if entry.is_dir():
                import shutil
                shutil.rmtree(entry)

        # Case B: 50 snapshots, all > 30 days old → age window wins, 0
        # remain even though they fit inside the count window.
        for d in range(50):
            _make_snapshot_dir(self.recovery_root,
                               now - timedelta(days=40 + d))

        result_b = prune_snapshots(self.settings)
        self.assertEqual(result_b.kept, 0)
        self.assertEqual(len(result_b.removed), 50)


class StartupResilienceTests(unittest.TestCase):
    """``create_app`` must survive a broken snapshot layer."""

    def test_startup_failure_does_not_block_app(self) -> None:
        # Force the global container cache to rebuild so our patch is
        # actually exercised — otherwise an earlier test's cached
        # container would short-circuit the snapshot hook.
        import app.main as main_module
        main_module._runtime_container = None

        with mock.patch.object(
            main_module, "snapshot_all_dbs",
            side_effect=RuntimeError("disk on fire"),
        ):
            app = main_module.create_app(start_poller=False)

        self.assertIsNotNone(app)
        # The Flask app should be fully constructed even though the
        # snapshot raised — that's the whole policy.
        self.assertIn("print_farm_container", app.extensions)


if __name__ == "__main__":
    unittest.main()
