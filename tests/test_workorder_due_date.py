"""Migration 003 + work-order due_date plumbing.

Covers:
- Fresh DB has the due_date column from CREATE TABLE.
- Legacy DB (pre-003) gets the column added on repository init.
- create_work_order accepts and persists due_date.
- list + detail reads surface due_date.
- count_late_work_orders enforces the rule (NOT NULL AND < today AND
  status NOT IN ('completed', 'cancelled')).
- Migration script: dry-run on a legacy DB describes the change;
  --apply adds the column and records itself in schema_version.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.queue.repository import QueueRepository
from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.work_orders.job_repository import JobRepository


def _has_column(db_path, table, column):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("PRAGMA table_info({})".format(table))
        return any(row[1] == column for row in cur.fetchall())
    finally:
        conn.close()


class DueDateColumnTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "work_orders.db")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_repo(self):
        # Initialize sibling tables first so FKs in queue_items resolve.
        JobRepository(self.db_path)
        QueueExecutionRepository(self.db_path)
        QueueRepository(self.db_path)
        return WorkOrderRepository(self.db_path)

    def test_fresh_db_has_due_date_column(self):
        self._make_repo()
        self.assertTrue(_has_column(self.db_path, "work_orders", "due_date"))

    def test_legacy_db_gets_column_on_repo_init(self):
        # Pre-003 DB: work_orders without due_date.
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE work_orders (
                wo_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                completed_at TEXT
            );
            INSERT INTO work_orders (wo_id, customer_name, created_at)
            VALUES ('WO-001', 'Legacy', '2026-05-01T00:00:00+00:00');
            """
        )
        conn.commit()
        conn.close()
        self.assertFalse(_has_column(self.db_path, "work_orders", "due_date"))

        # Opening with the repo should add the column.
        self._make_repo()
        self.assertTrue(_has_column(self.db_path, "work_orders", "due_date"))

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT due_date FROM work_orders WHERE wo_id='WO-001'"
        ).fetchone()
        conn.close()
        self.assertIsNone(row[0])

    def test_create_work_order_persists_due_date(self):
        repo = self._make_repo()
        result = repo.create_work_order(
            "Acme Robotics",
            [{"part_name": "widget", "material": "PLA", "quantity": 2}],
            due_date="2026-05-22",
        )
        self.assertEqual(result["due_date"], "2026-05-22")

        detail = repo.get_work_order(result["wo_id"])
        self.assertEqual(detail["due_date"], "2026-05-22")

    def test_create_work_order_without_due_date_stores_null(self):
        repo = self._make_repo()
        result = repo.create_work_order(
            "Acme Robotics",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        self.assertIsNone(result["due_date"])
        detail = repo.get_work_order(result["wo_id"])
        self.assertIsNone(detail["due_date"])

    def test_list_work_orders_includes_due_date(self):
        repo = self._make_repo()
        repo.create_work_order(
            "Acme",
            [{"part_name": "w", "material": "PLA", "quantity": 1}],
            due_date="2026-06-01",
        )
        repo.create_work_order(
            "Pinecone",
            [{"part_name": "w", "material": "PLA", "quantity": 1}],
        )
        rows = repo.get_all_work_orders()
        by_customer = {r["customer_name"]: r for r in rows}
        self.assertEqual(by_customer["Acme"]["due_date"], "2026-06-01")
        self.assertIsNone(by_customer["Pinecone"]["due_date"])


class CountLateWorkOrdersTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "work_orders.db")
        JobRepository(self.db_path)
        QueueExecutionRepository(self.db_path)
        QueueRepository(self.db_path)
        self.repo = WorkOrderRepository(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_counts_only_open_past_due(self):
        # Late (past due, still open) — counts.
        self.repo.create_work_order(
            "Late Co",
            [{"part_name": "p", "material": "PLA", "quantity": 1}],
            due_date="2026-05-01",
        )
        # Future due — doesn't count.
        self.repo.create_work_order(
            "Future Co",
            [{"part_name": "p", "material": "PLA", "quantity": 1}],
            due_date="2030-01-01",
        )
        # No due_date — doesn't count.
        self.repo.create_work_order(
            "No-Date Co",
            [{"part_name": "p", "material": "PLA", "quantity": 1}],
        )
        self.assertEqual(self.repo.count_late_work_orders("2026-05-18"), 1)

    def test_excludes_terminal_status(self):
        result = self.repo.create_work_order(
            "Closed Co",
            [{"part_name": "p", "material": "PLA", "quantity": 1}],
            due_date="2026-05-01",
        )
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE work_orders SET status='completed' WHERE wo_id=?",
            (result["wo_id"],),
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.repo.count_late_work_orders("2026-05-18"), 0)

    def test_excludes_cancelled_status(self):
        result = self.repo.create_work_order(
            "Cancelled Co",
            [{"part_name": "p", "material": "PLA", "quantity": 1}],
            due_date="2026-05-01",
        )
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE work_orders SET status='cancelled' WHERE wo_id=?",
            (result["wo_id"],),
        )
        conn.commit()
        conn.close()
        self.assertEqual(self.repo.count_late_work_orders("2026-05-18"), 0)


class Migration003Tests(unittest.TestCase):
    """Verify the migration script against a hand-crafted legacy DB."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "wo.db")
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE work_orders (
                wo_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                completed_at TEXT
            );
            """
        )
        conn.commit()
        conn.close()
        self.script = os.path.join(
            ROOT_DIR, "scripts", "migrations",
            "003_add_due_date_to_work_orders.py",
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_dry_run_describes_without_writing(self):
        self.assertFalse(_has_column(self.db_path, "work_orders", "due_date"))
        result = subprocess.run(
            [sys.executable, self.script, "--db-path", self.db_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Migration 003", result.stdout)
        self.assertFalse(_has_column(self.db_path, "work_orders", "due_date"))

    def test_apply_adds_column_and_records(self):
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        port_bound = sock.connect_ex(("127.0.0.1", 5001)) == 0
        sock.close()
        if port_bound:
            self.skipTest(
                "Port 5001 is bound; migration --apply refuses to run."
            )

        result = subprocess.run(
            [sys.executable, self.script, "--apply", "--db-path", self.db_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(_has_column(self.db_path, "work_orders", "due_date"))

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT migration_id, description FROM schema_version "
            "WHERE migration_id='003'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "Add due_date column to work_orders")

        # Re-running is a no-op.
        again = subprocess.run(
            [sys.executable, self.script, "--apply", "--db-path", self.db_path],
            capture_output=True, text=True,
        )
        self.assertEqual(again.returncode, 0)
        self.assertIn("already applied", again.stdout.lower())


if __name__ == "__main__":
    unittest.main()
