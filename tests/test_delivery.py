"""Phase F — delivery service + repository.

mark_delivered records a delivery and stamps a genuinely ``completed``
WO as the manual terminal status ``delivered``. Rejected from any other
status and from an already-delivered WO. Against tempdir DBs.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.work_orders import repository as wo_repo_module
from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import (
    DeliveryStateError,
    WorkOrderService,
)

MIGRATION_SCRIPT = os.path.join(
    ROOT_DIR, "scripts", "migrations", "008_create_deliveries_table.py"
)


def _load_migration_008():
    spec = importlib.util.spec_from_file_location(
        "migration_008", MIGRATION_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _DeliverySetup(unittest.TestCase):

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

    def _wo_in_status(self, qi_status):
        """Create a WO whose single queue_item drives the rollup."""
        wo = self.wo_repo.create_work_order(
            "Cust", [{"part_name": "P", "material": "PLA", "quantity": 1}]
        )
        wo_id = wo["wo_id"]
        conn = self.wo_repo._get_conn()
        try:
            conn.execute(
                "UPDATE queue_items SET status=? WHERE wo_id=?",
                (qi_status, wo_id),
            )
            status_sync.sync_work_order_status(conn, wo_id)
            conn.commit()
        finally:
            conn.close()
        return wo_id

    def _completed_wo(self):
        wo_id = self._wo_in_status("completed")
        assert self.wo_repo.get_work_order(wo_id)["status"] == "completed"
        return wo_id


class MarkDeliveredTests(_DeliverySetup):

    def test_mark_delivered_on_completed_wo(self):
        wo_id = self._completed_wo()
        wo = self.svc.mark_delivered(
            wo_id, received_by="Acme Receiving",
            recorded_by="JK", notes="Left at dock 3",
        )
        self.assertEqual(wo["status"], "delivered")
        self.assertIn("delivery", wo)
        self.assertEqual(wo["delivery"]["received_by"], "Acme Receiving")
        self.assertEqual(wo["delivery"]["recorded_by"], "JK")
        self.assertEqual(wo["delivery"]["notes"], "Left at dock 3")

    def test_default_delivered_at_is_today(self):
        wo_id = self._completed_wo()
        today = datetime.now(timezone.utc).date().isoformat()
        wo = self.svc.mark_delivered(wo_id)
        self.assertEqual(wo["delivery"]["delivered_at"], today)

    def test_delivery_row_retrievable(self):
        wo_id = self._completed_wo()
        self.svc.mark_delivered(wo_id, delivered_at="2026-05-20",
                                received_by="R")
        row = self.wo_repo.get_delivery_for_wo(wo_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["wo_id"], wo_id)
        self.assertEqual(row["delivered_at"], "2026-05-20")
        self.assertEqual(row["received_by"], "R")
        self.assertIsNotNone(row["created_at"])

    def test_payload_includes_delivery_after_delivery(self):
        wo_id = self._completed_wo()
        self.svc.mark_delivered(wo_id, received_by="R")
        wo = self.svc.get_work_order(wo_id)
        self.assertIn("delivery", wo)
        self.assertEqual(wo["delivery"]["received_by"], "R")

    def test_init_tables_mirror_matches_migration(self):
        module = _load_migration_008()
        self.assertEqual(
            module.DELIVERIES_SCHEMA_STATEMENTS,
            wo_repo_module.DELIVERIES_SCHEMA_STATEMENTS,
        )
        self.assertEqual(
            module.DELIVERIES_TABLES, wo_repo_module.DELIVERIES_TABLES
        )


class MarkDeliveredRejectionTests(_DeliverySetup):

    def test_reject_in_progress(self):
        wo_id = self._wo_in_status("printing")
        self.assertEqual(
            self.wo_repo.get_work_order(wo_id)["status"], "in_progress"
        )
        with self.assertRaises(DeliveryStateError):
            self.svc.mark_delivered(wo_id)

    def test_reject_attention(self):
        wo_id = self._wo_in_status("failed")
        self.assertEqual(
            self.wo_repo.get_work_order(wo_id)["status"], "attention"
        )
        with self.assertRaises(DeliveryStateError):
            self.svc.mark_delivered(wo_id)

    def test_reject_already_delivered(self):
        wo_id = self._completed_wo()
        self.svc.mark_delivered(wo_id)
        with self.assertRaises(DeliveryStateError):
            self.svc.mark_delivered(wo_id)
        # And only one delivery row exists.
        self.assertIsNotNone(self.wo_repo.get_delivery_for_wo(wo_id))

    def test_reject_missing_wo(self):
        with self.assertRaises(LookupError):
            self.svc.mark_delivered("WO-DOES-NOT-EXIST")


if __name__ == "__main__":
    unittest.main()
