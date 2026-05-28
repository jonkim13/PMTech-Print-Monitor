"""Migration 006 — add ``inspection_outcome`` to ``jobs`` (Phase D).

Runs the migration script against a tempdir work_orders.db whose
``jobs`` table is bootstrapped WITHOUT the Phase D column, so the
ALTER ADD path is actually exercised (not just the already-present
skip). Never touches data/*.db.

Cases
-----
- ``test_dry_run_reports_missing_column``   no flag → preview only,
                                             column reported missing,
                                             zero writes.
- ``test_apply_adds_column_with_pending_default``
                                             --apply adds the column and
                                             existing rows pick up
                                             'pending' via the SQLite
                                             DEFAULT (no data UPDATE).
- ``test_idempotent_no_op_on_rerun``        layer 1: second --apply
                                             short-circuits on
                                             MigrationRunner.is_applied.
- ``test_idempotent_when_registry_row_missing``
                                             layer 2: drop the
                                             schema_version row → re-apply
                                             finds the column present,
                                             skips the DDL, re-records.
- ``test_new_columns_mirror_job_repository``  the migration's NEW_COLUMNS
                                             matches JobRepository's
                                             _PHASE_D_JOB_COLUMNS
                                             byte-for-byte (drift guard).
"""

import importlib.util
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.work_orders.job_repository import _PHASE_D_JOB_COLUMNS

MIGRATION_SCRIPT = os.path.join(
    ROOT_DIR, "scripts", "migrations",
    "006_add_inspection_outcome.py",
)
MIGRATION_ID = "006_add_inspection_outcome"


def _load_migration_module():
    """Import the numbered migration script for direct symbol access."""
    spec = importlib.util.spec_from_file_location(
        "migration_006", MIGRATION_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service_port_bound():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", 5001)) == 0
    finally:
        sock.close()


def _has_column(db_path, table, column):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "PRAGMA table_info({})".format(table)
        ).fetchall()
        return any(r[1] == column for r in rows)
    finally:
        conn.close()


class Migration006Tests(unittest.TestCase):

    def setUp(self):
        if _service_port_bound():
            self.skipTest(
                "Port 5001 is bound; migration --apply refuses to run."
            )
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "work_orders.db")
        # Bootstrap a pre-Phase-D jobs table by hand: base + Phase C
        # columns, but deliberately NO inspection_outcome, so the
        # migration's ALTER ADD path runs.
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript("""
                CREATE TABLE jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wo_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    job_type TEXT DEFAULT 'Internal' NOT NULL
                );
            """)
            conn.execute(
                "INSERT INTO jobs (wo_id, status, created_at, job_type) "
                "VALUES ('WO-001', 'open', '2026-01-01T00:00:00+00:00', "
                "'Internal')"
            )
            conn.execute(
                "INSERT INTO jobs (wo_id, status, created_at, job_type) "
                "VALUES ('WO-001', 'completed', "
                "'2026-01-02T00:00:00+00:00', 'External')"
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, MIGRATION_SCRIPT, "--db", self.db_path, *extra],
            capture_output=True, text=True,
        )

    # ------------------------------------------------------------------

    def test_dry_run_reports_missing_column(self):
        self.assertFalse(
            _has_column(self.db_path, "jobs", "inspection_outcome")
        )
        result = self._run()  # no flag → dry-run default
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Planned column additions (1 missing):", result.stdout)
        self.assertIn("inspection_outcome", result.stdout)
        self.assertIn("Dry run complete. No writes performed.", result.stdout)
        # Dry run must not have touched the schema.
        self.assertFalse(
            _has_column(self.db_path, "jobs", "inspection_outcome")
        )

    def test_apply_adds_column_with_pending_default(self):
        result = self._run("--apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "Columns added to jobs: 1 (skipped 0 already present)",
            result.stdout,
        )
        self.assertTrue(
            _has_column(self.db_path, "jobs", "inspection_outcome")
        )
        # Existing rows pick up 'pending' via the column DEFAULT — no
        # per-row UPDATE was issued.
        conn = sqlite3.connect(self.db_path)
        try:
            outcomes = [
                r[0] for r in conn.execute(
                    "SELECT inspection_outcome FROM jobs"
                ).fetchall()
            ]
        finally:
            conn.close()
        self.assertEqual(outcomes, ["pending", "pending"])

    def test_idempotent_no_op_on_rerun(self):
        first = self._run("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Columns added to jobs: 1", first.stdout)

        second = self._run("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already applied", second.stdout.lower())
        # No fresh apply summary on the short-circuited run.
        self.assertNotIn("Columns added to jobs:", second.stdout)

    def test_idempotent_when_registry_row_missing(self):
        first = self._run("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(
            _has_column(self.db_path, "jobs", "inspection_outcome")
        )

        # Purge the registry row to force the apply path back open while
        # the column already exists — layer 2 of idempotence.
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "DELETE FROM schema_version WHERE migration_id = ?",
                (MIGRATION_ID,),
            )
            conn.commit()
        finally:
            conn.close()

        # Two --apply runs in the same wall-clock second would collide on
        # the timestamped backup filename (a test artifact, not a real
        # operational path). Clear the first run's backup so the second
        # can create its own.
        for name in os.listdir(self.tmpdir.name):
            if name.startswith("work_orders.db.bak-"):
                os.remove(os.path.join(self.tmpdir.name, name))

        second = self._run("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        # add_column_if_missing guard makes the DDL a no-op.
        self.assertIn(
            "Columns added to jobs: 0 (skipped 1 already present)",
            second.stdout,
        )
        # Registry row reinstated so a third run short-circuits again.
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM schema_version WHERE migration_id = ?",
                (MIGRATION_ID,),
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_new_columns_mirror_job_repository(self):
        """Drift guard: the migration's NEW_COLUMNS must stay
        byte-identical to JobRepository._PHASE_D_JOB_COLUMNS so fresh
        installs and migrated DBs converge."""
        module = _load_migration_module()
        self.assertEqual(module.NEW_COLUMNS, _PHASE_D_JOB_COLUMNS)


if __name__ == "__main__":
    unittest.main()
