"""Phase F crux — a delivered WO must never be re-derived.

``delivered`` is a manual terminal status. sync_work_order_status
fires on every queue write, inspection, and NCR mutation; its
early-return guard must keep a delivered WO delivered. The base
derivers never emit ``delivered`` for any input.
"""

import itertools
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
from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService


class DerivedStatusNeverDeliveredTests(unittest.TestCase):
    """The derivers top out at completed — never delivered."""

    _STATUSES = ["queued", "printing", "completed", "failed", "cancelled"]

    def test_base_deriver_never_emits_delivered(self):
        for r in range(0, 4):
            for combo in itertools.product(self._STATUSES, repeat=r):
                self.assertNotEqual(
                    status_sync.derive_work_order_status(list(combo)),
                    "delivered",
                    "derive_work_order_status emitted 'delivered' for "
                    "{!r}".format(combo),
                )

    def test_combined_deriver_never_emits_delivered(self):
        for r in range(0, 3):
            for combo in itertools.product(self._STATUSES, repeat=r):
                for ncr in (False, True):
                    self.assertNotEqual(
                        status_sync.derive_work_order_status_combined(
                            list(combo), [], has_blocking_ncr=ncr
                        ),
                        "delivered",
                    )


class DeliveredSurvivesResyncTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "wo.db")
        self.wo_repo = WorkOrderRepository(self.db)
        self.job_repo = JobRepository(self.db)
        QueueRepository(self.db)
        QueueExecutionRepository(self.db)
        self.svc = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _deliver_a_wo(self):
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
            status_sync.sync_work_order_status(conn, wo_id)
            conn.commit()
        finally:
            conn.close()
        self.svc.mark_delivered(wo_id, received_by="R")
        self.assertEqual(self.wo_repo.get_work_order(wo_id)["status"],
                         "delivered")
        return wo_id

    def _status(self, wo_id):
        return self.wo_repo.get_work_order(wo_id)["status"]

    def test_direct_resync_keeps_delivered(self):
        wo_id = self._deliver_a_wo()
        conn = self.wo_repo._get_conn()
        try:
            result = status_sync.sync_work_order_status(conn, wo_id)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(result, "delivered")
        self.assertEqual(self._status(wo_id), "delivered")

    def test_queue_write_then_resync_keeps_delivered(self):
        """Witness: a later queue-item change + sync must NOT revert a
        delivered WO to completed/attention."""
        wo_id = self._deliver_a_wo()
        conn = self.wo_repo._get_conn()
        try:
            # Simulate a stray queue write that would otherwise re-derive.
            conn.execute(
                "UPDATE queue_items SET status='failed' WHERE wo_id=?",
                (wo_id,),
            )
            status_sync.sync_work_order_status(conn, wo_id)
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._status(wo_id), "delivered")

    def test_resync_with_quality_repo_keeps_delivered(self):
        # Even with the NCR-gate path active, the delivered guard wins.
        wo_id = self._deliver_a_wo()

        class _StubQuality:
            def count_open_ncrs_for_wo(self, _wo_id):
                return 1
        conn = self.wo_repo._get_conn()
        try:
            result = status_sync.sync_work_order_status(
                conn, wo_id, quality_repository=_StubQuality()
            )
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(result, "delivered")
        self.assertEqual(self._status(wo_id), "delivered")


if __name__ == "__main__":
    unittest.main()
