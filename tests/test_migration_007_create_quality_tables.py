"""Migration 007 — create the quality.db tables (Phase E1).

Runs the migration against a tempdir path that does NOT exist yet, so
the create-from-scratch path is exercised. Never touches data/*.db.

Cases
-----
- ``test_dry_run_reports_tables_and_writes_nothing``  no flag → preview
                                                       both tables, file
                                                       is not created.
- ``test_apply_creates_both_tables_with_fk``          --apply creates
                                                       quality.db, both
                                                       tables, and the CA
                                                       → NCR foreign key.
- ``test_layer1_registry_short_circuit``              second --apply hits
                                                       MigrationRunner.is_applied.
- ``test_layer2_table_existence_short_circuit``       registry purged →
                                                       re-apply skips DDL
                                                       (tables already
                                                       exist) and
                                                       re-records.
- ``test_backup_skipped_when_new_taken_when_exists``  new file → no
                                                       backup; existing
                                                       file → backup.
- ``test_schema_statements_mirror_repository``        migration DDL is the
                                                       repository's
                                                       (imported, so
                                                       byte-identical).
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

from app.domains.quality import repository as quality_repo_module

MIGRATION_SCRIPT = os.path.join(
    ROOT_DIR, "scripts", "migrations",
    "007_create_quality_tables.py",
)
MIGRATION_ID = "007_create_quality_tables"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "migration_007", MIGRATION_SCRIPT
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


class Migration007Tests(unittest.TestCase):

    def setUp(self):
        if _service_port_bound():
            self.skipTest(
                "Port 5001 is bound; migration --apply refuses to run."
            )
        self.tmpdir = tempfile.TemporaryDirectory()
        # Deliberately do NOT create the file — the migration must create
        # quality.db from scratch.
        self.db_path = os.path.join(self.tmpdir.name, "quality.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, MIGRATION_SCRIPT, "--db", self.db_path, *extra],
            capture_output=True, text=True,
        )

    def _table_exists(self, table):
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def _purge_registry(self):
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "DELETE FROM schema_version WHERE migration_id = ?",
                (MIGRATION_ID,),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------

    def test_dry_run_reports_tables_and_writes_nothing(self):
        self.assertFalse(os.path.exists(self.db_path))
        r = self._run()  # dry-run default
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Planned table additions (2 missing):", r.stdout)
        self.assertIn("non_conformances", r.stdout)
        self.assertIn("corrective_actions", r.stdout)
        self.assertIn("Dry run complete. No writes performed.", r.stdout)
        # A brand-new path must not be written during a dry run.
        self.assertFalse(os.path.exists(self.db_path))

    def test_apply_creates_both_tables_with_fk(self):
        r = self._run("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(
            "Tables created: 2 (skipped 0 already present)", r.stdout
        )
        self.assertTrue(os.path.exists(self.db_path))
        self.assertTrue(self._table_exists("non_conformances"))
        self.assertTrue(self._table_exists("corrective_actions"))

        # corrective_actions.ncr_id → non_conformances(ncr_id) FK present.
        conn = sqlite3.connect(self.db_path)
        try:
            fks = conn.execute(
                "PRAGMA foreign_key_list(corrective_actions)"
            ).fetchall()
        finally:
            conn.close()
        referenced = {row[2] for row in fks}  # 'table' column
        self.assertIn("non_conformances", referenced)

    def test_layer1_registry_short_circuit(self):
        first = self._run("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Tables created: 2", first.stdout)

        second = self._run("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already applied", second.stdout.lower())
        self.assertNotIn("Tables created:", second.stdout)

    def test_layer2_table_existence_short_circuit(self):
        first = self._run("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(self._table_exists("non_conformances"))

        # Purge the registry row so the apply path runs again while the
        # tables already exist — the table-existence guard must no-op.
        self._purge_registry()

        second = self._run("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn(
            "Tables created: 0 (skipped 2 already present)", second.stdout
        )
        # Registry reinstated so a third run short-circuits on layer 1.
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM schema_version WHERE migration_id = ?",
                (MIGRATION_ID,),
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_backup_skipped_when_new_taken_when_exists(self):
        first = self._run("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        # New file → no backup taken.
        self.assertIn("New DB file — no backup needed.", first.stdout)
        baks = [n for n in os.listdir(self.tmpdir.name)
                if n.startswith("quality.db.bak-")]
        self.assertEqual(baks, [])

        # File now exists; force the apply path open again and confirm a
        # backup is taken this time.
        self._purge_registry()
        second = self._run("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Backup created:", second.stdout)
        baks = [n for n in os.listdir(self.tmpdir.name)
                if n.startswith("quality.db.bak-")]
        self.assertTrue(baks)

    def test_schema_statements_mirror_repository(self):
        """The migration imports its DDL from the repository, so the
        fresh-install mirror and the migration cannot drift."""
        module = _load_migration_module()
        self.assertEqual(
            module.QUALITY_SCHEMA_STATEMENTS,
            quality_repo_module.QUALITY_SCHEMA_STATEMENTS,
        )
        self.assertEqual(
            module.QUALITY_TABLES, quality_repo_module.QUALITY_TABLES
        )


if __name__ == "__main__":
    unittest.main()
