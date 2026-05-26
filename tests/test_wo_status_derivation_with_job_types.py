"""Phase C — WO status rollup spans queue_items + non-Internal jobs.

Locks the new ``sync_work_order_status`` invariant: a Work Order
completes only when both its member queue_items AND its member
non-Internal (External / Design) jobs are completed/cancelled.
Internal jobs are NOT pulled into the rollup directly — they're
already represented via their queue_items.

These tests poke ``status_sync.sync_work_order_status`` directly so
the derivation rules are pinned independent of any service-layer
orchestration.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService
from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository


def _build_stack(db_path):
    """Initialize sibling repos in FK-friendly order."""
    job_repo = JobRepository(db_path)
    QueueExecutionRepository(db_path)
    QueueRepository(db_path)
    wo_repo = WorkOrderRepository(db_path)
    service = WorkOrderService(
        work_order_repository=wo_repo,
        job_repository=job_repo,
    )
    return job_repo, wo_repo, service


def _open_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _set_queue_items_status(db_path, wo_id, new_status):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE queue_items SET status = ? WHERE wo_id = ?",
        (new_status, wo_id),
    )
    conn.commit()
    conn.close()


def _set_job_status(db_path, job_id, new_status):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE jobs SET status = ? WHERE job_id = ?",
        (new_status, job_id),
    )
    conn.commit()
    conn.close()


def _sync(db_path, wo_id):
    """Run the function under test and return the persisted WO status."""
    conn = _open_conn(db_path)
    try:
        status_sync.sync_work_order_status(conn, wo_id)
        conn.commit()
        row = conn.execute(
            "SELECT status FROM work_orders WHERE wo_id = ?", (wo_id,)
        ).fetchone()
        return row["status"]
    finally:
        conn.close()


class WoStatusDerivationWithJobTypesTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "work_orders.db")
        self.job_repo, self.wo_repo, self.service = _build_stack(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    # ------------------------------------------------------------------
    # 1. Regression — Internal-only path unchanged
    # ------------------------------------------------------------------

    def test_wo_open_when_only_internal_job_open(self):
        """No non-Internal jobs, all queue_items queued → WO 'open'."""
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 2}],
        )
        self.assertEqual(_sync(self.db_path, wo["wo_id"]), "open")

    # ------------------------------------------------------------------
    # 2. External 'open' alone — WO 'open'
    # ------------------------------------------------------------------

    def test_wo_open_when_external_job_open(self):
        """WO with one External job (open), no queue_items → WO 'open'."""
        wo = self.wo_repo.create_work_order("Acme", [])
        self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        self.assertEqual(_sync(self.db_path, wo["wo_id"]), "open")

    # ------------------------------------------------------------------
    # 3. External started → WO 'in_progress'
    # ------------------------------------------------------------------

    def test_wo_in_progress_when_external_job_started(self):
        wo = self.wo_repo.create_work_order("Acme", [])
        result = self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        self.service.start_non_internal_job(result["job_id"])
        self.assertEqual(_sync(self.db_path, wo["wo_id"]), "in_progress")

    # ------------------------------------------------------------------
    # 4. Happy path — Internal items + non-Internal job both done
    # ------------------------------------------------------------------

    def test_wo_completed_when_all_internal_items_and_external_jobs_done(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 2}],
        )
        ext = self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        _set_queue_items_status(self.db_path, wo["wo_id"], "completed")
        _set_job_status(self.db_path, ext["job_id"], "completed")

        self.assertEqual(_sync(self.db_path, wo["wo_id"]), "completed")

    # ------------------------------------------------------------------
    # 5. Critical case — External blocks WO completion
    # ------------------------------------------------------------------

    def test_wo_not_completed_when_external_job_incomplete_but_queue_items_done(self):
        """Queue_items all completed; External still 'open'. WO must
        NOT be 'completed' — it's still 'in_progress' because the
        External job's projected status ('queued') keeps the pool
        non-terminal alongside the completed queue_items."""
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        _set_queue_items_status(self.db_path, wo["wo_id"], "completed")

        rolled_up = _sync(self.db_path, wo["wo_id"])
        self.assertNotEqual(rolled_up, "completed",
                            "WO must not complete while External job is open")
        self.assertEqual(rolled_up, "in_progress")

    # ------------------------------------------------------------------
    # 6. Design-only WO — Design completed → WO 'completed'
    # ------------------------------------------------------------------

    def test_wo_completed_when_design_only_workorder_design_complete(self):
        wo = self.wo_repo.create_work_order("Acme", [])
        result = self.service.create_job(
            wo["wo_id"], job_type="Design", designer="Jonathan",
        )
        # Design lifecycle: start then complete.
        self.service.start_non_internal_job(result["job_id"])
        self.service.complete_non_internal_job(result["job_id"])

        # complete_non_internal_job already triggered the rollup,
        # but re-syncing here pins that the derivation alone (without
        # the service path) lands the WO at 'completed'.
        row = self.wo_repo.get_work_order(wo["wo_id"])
        self.assertEqual(row["status"], "completed")
        self.assertEqual(_sync(self.db_path, wo["wo_id"]), "completed")

    # ------------------------------------------------------------------
    # 7. Cancellation path unchanged
    # ------------------------------------------------------------------

    def test_wo_cancelled_path_unchanged(self):
        """Regression: all queue_items cancelled, no non-Internal jobs
        → WO 'cancelled'."""
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 2}],
        )
        _set_queue_items_status(self.db_path, wo["wo_id"], "cancelled")
        self.assertEqual(_sync(self.db_path, wo["wo_id"]), "cancelled")

    # ------------------------------------------------------------------
    # 8. Internal job status not double-counted
    # ------------------------------------------------------------------

    def test_internal_job_status_not_double_counted(self):
        """The WO query filters ``job_type != 'Internal'``. To prove
        the filter is in place: corrupt the Internal job's status
        field to 'attention'. If it were being unioned into the
        rollup, the WO would resolve to 'attention'. With the filter
        in place, the WO still rolls up to 'completed' from
        queue_items alone.
        """
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        # Fetch queue_ids so we can attach to an Internal job.
        conn = sqlite3.connect(self.db_path)
        try:
            queue_ids = [r[0] for r in conn.execute(
                "SELECT queue_id FROM queue_items WHERE wo_id = ?",
                (wo["wo_id"],),
            ).fetchall()]
        finally:
            conn.close()

        internal = self.service.create_job(
            wo["wo_id"], queue_ids=queue_ids,
        )
        _set_queue_items_status(self.db_path, wo["wo_id"], "completed")
        # Corrupt the Internal job's status field — if the rollup
        # ever queried it, this would poison the WO status.
        _set_job_status(self.db_path, internal["job_id"], "attention")

        self.assertEqual(_sync(self.db_path, wo["wo_id"]), "completed")


if __name__ == "__main__":
    unittest.main()
