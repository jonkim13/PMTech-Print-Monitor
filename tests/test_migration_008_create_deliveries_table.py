"""Migration 008 — create the deliveries table (Phase F).

Runs against a tempdir work_orders.db bootstrapped with a hand-built
work_orders table (the FK target) but WITHOUT the deliveries table, so
the create path is exercised. Never touches data/*.db.

Cases
-----
- dry-run reports the table to create, no schema change.
- --apply creates deliveries + the FK; backup taken (file pre-exists).
- layer-1 registry short-circuit on re-apply.
- layer-2 table-existence short-circuit (registry purged).
- DDL byte-identical to the repository _init_tables mirror.
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

from app.domains.work_orders import repository as wo_repo_module

MIGRATION_SCRIPT = os.path.join(
    ROOT_DIR, "scripts", "migrations", "008_create_deliveries_table.py"
)
MIGRATION_ID = "008_create_deliveries_table"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "migration_008", MIGRATION_SCRIPT
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


class Migration008Tests(unittest.TestCase):

    def setUp(self):
        if _service_port_bound():
            self.skipTest(
                "Port 5001 is bound; migration --apply refuses to run."
            )
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "work_orders.db")
        # Bootstrap the FK target by hand — a minimal work_orders table,
        # but NO deliveries table, so the migration's create path runs.
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript("""
                CREATE TABLE work_orders (
                    wo_id TEXT PRIMARY KEY,
                    customer_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    completed_at TEXT,
                    due_date TEXT
                );
            """)
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

    def _clear_backups(self):
        for name in os.listdir(self.tmpdir.name):
            if name.startswith("work_orders.db.bak-"):
                os.remove(os.path.join(self.tmpdir.name, name))

    # ------------------------------------------------------------------

    def test_dry_run_reports_table_no_write(self):
        self.assertFalse(self._table_exists("deliveries"))
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Planned table additions (1 missing):", r.stdout)
        self.assertIn("deliveries", r.stdout)
        self.assertIn("Dry run complete. No writes performed.", r.stdout)
        self.assertFalse(self._table_exists("deliveries"))

    def test_apply_creates_table_with_fk_and_backup(self):
        r = self._run("--apply")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Tables created: 1 (skipped 0 already present)",
                      r.stdout)
        # Backup is taken because work_orders.db already existed.
        self.assertIn("Backup created:", r.stdout)
        self.assertTrue(self._table_exists("deliveries"))

        conn = sqlite3.connect(self.db_path)
        try:
            fks = conn.execute(
                "PRAGMA foreign_key_list(deliveries)"
            ).fetchall()
        finally:
            conn.close()
        referenced = {row[2] for row in fks}  # 'table' column
        self.assertIn("work_orders", referenced)

    def test_layer1_registry_short_circuit(self):
        first = self._run("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Tables created: 1", first.stdout)

        second = self._run("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already applied", second.stdout.lower())
        self.assertNotIn("Tables created:", second.stdout)

    def test_layer2_table_existence_short_circuit(self):
        first = self._run("--apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(self._table_exists("deliveries"))

        self._purge_registry()
        self._clear_backups()  # avoid same-second backup filename clash

        second = self._run("--apply")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Tables created: 0 (skipped 1 already present)",
                      second.stdout)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM schema_version WHERE migration_id = ?",
                (MIGRATION_ID,),
            ).fetchone()
            self.assertIsNotNone(row)
        finally:
            conn.close()

    def test_ddl_mirror_byte_identical(self):
        module = _load_migration_module()
        self.assertEqual(
            module.DELIVERIES_SCHEMA_STATEMENTS,
            wo_repo_module.DELIVERIES_SCHEMA_STATEMENTS,
        )
        self.assertEqual(
            module.DELIVERIES_TABLES, wo_repo_module.DELIVERIES_TABLES
        )


if __name__ == "__main__":
    unittest.main()
