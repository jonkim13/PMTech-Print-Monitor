"""Phase F — delivery HTTP route.

POST /api/workorders/<wo>/deliver against an isolated WorkOrderService
wired into a bare Flask app via register_work_order_routes (tempdir DBs,
no container). Verifies the 200 happy path and the 404/409 mappings.
"""

import os
import shutil
import sys
import tempfile
import unittest

from flask import Flask

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.routes import register_work_order_routes
from app.domains.work_orders.service import WorkOrderService


class DeliveryRouteTests(unittest.TestCase):

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
        self.app = Flask(__name__)
        register_work_order_routes(
            self.app, None, work_order_service=self.svc
        )
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _wo_in_status(self, qi_status):
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

    def test_deliver_completed_wo_returns_200(self):
        wo_id = self._wo_in_status("completed")
        r = self.client.post(
            "/api/workorders/{}/deliver".format(wo_id),
            json={"received_by": "Acme", "recorded_by": "JK"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["work_order"]["status"], "delivered")
        self.assertEqual(body["delivery"]["received_by"], "Acme")

    def test_deliver_in_progress_wo_returns_409(self):
        wo_id = self._wo_in_status("printing")
        r = self.client.post("/api/workorders/{}/deliver".format(wo_id),
                             json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn("error", r.get_json())

    def test_deliver_already_delivered_returns_409(self):
        wo_id = self._wo_in_status("completed")
        self.client.post("/api/workorders/{}/deliver".format(wo_id), json={})
        r = self.client.post("/api/workorders/{}/deliver".format(wo_id),
                             json={})
        self.assertEqual(r.status_code, 409)

    def test_deliver_missing_wo_returns_404(self):
        r = self.client.post("/api/workorders/WO-MISSING/deliver", json={})
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
