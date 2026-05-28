"""Phase E1 — the open-NCR gate on the WO rollup.

Mirrors the Phase D inspection-gate rollup tests. The NCR gate lives
only in derive_work_order_status_combined (the base
derive_work_order_status stays NCR-unaware) and is threaded into
sync_work_order_status via an injected quality repository.
"""

import inspect
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.quality.repository import QualityRepository
from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository


class PureDeriverGateTests(unittest.TestCase):
    """The gate is a thin layer on the combined deriver only."""

    def test_gate_only_flips_the_completed_branch(self):
        combined = status_sync.derive_work_order_status_combined
        # completed → attention only when blocked.
        self.assertEqual(
            combined(["completed"], [], has_blocking_ncr=False), "completed"
        )
        self.assertEqual(
            combined(["completed"], [], has_blocking_ncr=True), "attention"
        )
        # Non-completed branches are returned untouched by the flag.
        self.assertEqual(
            combined(["printing"], [], has_blocking_ncr=True), "in_progress"
        )
        self.assertEqual(
            combined(["failed"], [], has_blocking_ncr=True), "attention"
        )
        self.assertEqual(
            combined(["queued"], [], has_blocking_ncr=True), "open"
        )

    def test_base_deriver_is_ncr_unaware(self):
        # Negative: the base pure function must never gain an NCR arg;
        # the gate lives only in the combined deriver.
        base_params = inspect.signature(
            status_sync.derive_work_order_status
        ).parameters
        self.assertNotIn("has_blocking_ncr", base_params)
        combined_params = inspect.signature(
            status_sync.derive_work_order_status_combined
        ).parameters
        self.assertIn("has_blocking_ncr", combined_params)


class SyncRollupWithNcrTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.wo_db = os.path.join(self.tmp, "wo.db")
        self.q_db = os.path.join(self.tmp, "quality.db")
        self.wo_repo = WorkOrderRepository(self.wo_db)
        self.job_repo = JobRepository(self.wo_db)
        QueueRepository(self.wo_db)
        QueueExecutionRepository(self.wo_db)
        self.q_repo = QualityRepository(self.q_db)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    # ---- helpers -----------------------------------------------------

    def _first_qid(self, wo_id):
        conn = sqlite3.connect(self.wo_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT queue_id FROM queue_items WHERE wo_id=? "
            "ORDER BY queue_id LIMIT 1",
            (wo_id,),
        ).fetchone()
        conn.close()
        return row["queue_id"] if row else None

    def _completed_wo(self):
        """WO with a single completed queue_item and no job.

        Base rollup → 'completed', so the NCR gate is the only thing
        that can change the outcome (no inspection gate in play).
        """
        wo = self.wo_repo.create_work_order(
            "Cust", [{"part_name": "P", "material": "PLA", "quantity": 1}]
        )
        wo_id = wo["wo_id"]
        conn = self.wo_repo._get_conn()
        try:
            conn.execute(
                "UPDATE queue_items SET status='completed' WHERE wo_id=?",
                (wo_id,),
            )
            conn.commit()
        finally:
            conn.close()
        return wo_id

    def _sync(self, wo_id):
        conn = self.wo_repo._get_conn()
        try:
            status = status_sync.sync_work_order_status(
                conn, wo_id, quality_repository=self.q_repo
            )
            conn.commit()
            return status
        finally:
            conn.close()

    def _set_qi_status_and_sync_job(self, job_id, qi_status):
        conn = self.job_repo._get_conn()
        try:
            conn.execute(
                "UPDATE queue_items SET status=? WHERE job_id=?",
                (qi_status, job_id),
            )
            status_sync.sync_job_status(conn, job_id)
            conn.commit()
        finally:
            conn.close()

    # ---- contract ----------------------------------------------------

    def test_all_complete_no_ncr_is_completed(self):
        wo_id = self._completed_wo()
        self.assertEqual(self._sync(wo_id), "completed")

    def test_open_ncr_holds_wo_at_attention(self):
        wo_id = self._completed_wo()
        self.q_repo.create_ncr(
            job_id=1, wo_id=wo_id, description="defect", reported_by="QC"
        )
        self.assertEqual(self._sync(wo_id), "attention")

    def test_closing_ncr_releases_wo_to_completed(self):
        wo_id = self._completed_wo()
        ncr = self.q_repo.create_ncr(
            job_id=1, wo_id=wo_id, description="defect", reported_by="QC"
        )
        self.assertEqual(self._sync(wo_id), "attention")
        self.q_repo.close_ncr(ncr["ncr_id"])
        self.assertEqual(self._sync(wo_id), "completed")

    # ---- witness: the two gates are independent ----------------------

    def test_inspection_gate_and_ncr_gate_are_independent(self):
        """A failed-inspection job (Phase D 'attention') with an open NCR
        rolls the WO to 'attention'. Closing the NCR alone does NOT
        complete the WO while the job itself is still 'attention' — the
        queue/inspection gate and the NCR gate are independent."""
        wo = self.wo_repo.create_work_order(
            "Cust", [{"part_name": "P", "material": "PLA", "quantity": 1}]
        )
        wo_id = wo["wo_id"]
        qid = self._first_qid(wo_id)
        job = self.job_repo.create_job(
            wo_id, queue_ids=[qid], job_type="Internal"
        )
        # Drive the job to 'attention' via a failed queue_item.
        self._set_qi_status_and_sync_job(job["job_id"], "failed")
        self.assertEqual(self._sync(wo_id), "attention")

        # Raise an open NCR on the same job — WO stays 'attention'.
        ncr = self.q_repo.create_ncr(
            job_id=job["job_id"], wo_id=wo_id,
            description="dims out of tol", reported_by="QC",
        )
        self.assertEqual(self._sync(wo_id), "attention")

        # Closing the NCR does not complete the WO — the job is still
        # 'attention' on the queue/inspection side.
        self.q_repo.close_ncr(ncr["ncr_id"])
        self.assertEqual(self._sync(wo_id), "attention")


if __name__ == "__main__":
    unittest.main()
