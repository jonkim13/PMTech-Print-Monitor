"""Phase E1 — quality HTTP routes (NCR + Corrective Action).

Drives the quality blueprint against an isolated QualityService wired
into a bare Flask app (tempdir DBs, no full container). Verifies the
201/200 happy paths and the 400/404/409 error mappings.
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
from app.domains.quality.repository import QualityRepository
from app.domains.quality.routes import register_quality_routes
from app.domains.quality.service import QualityService
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository


class QualityRouteTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.wo_db = os.path.join(self.tmp, "wo.db")
        self.q_db = os.path.join(self.tmp, "quality.db")
        self.wo_repo = WorkOrderRepository(self.wo_db)
        self.job_repo = JobRepository(self.wo_db)
        QueueRepository(self.wo_db)
        QueueExecutionRepository(self.wo_db)
        self.q_repo = QualityRepository(self.q_db)
        self.svc = QualityService(
            quality_repository=self.q_repo,
            job_repository=self.job_repo,
            work_order_repository=self.wo_repo,
        )
        self.app = Flask(__name__)
        register_quality_routes(self.app, self.svc)
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _make_job(self):
        wo = self.wo_repo.create_work_order("Cust", [])
        job = self.job_repo.create_job(wo["wo_id"], job_type="Internal")
        return wo["wo_id"], job["job_id"]

    def _create_ncr(self, corrective_action_needed="N"):
        wo_id, job_id = self._make_job()
        r = self.client.post("/api/ncrs", json={
            "job_id": job_id, "wo_id": wo_id, "description": "defect",
            "reported_by": "QC",
            "corrective_action_needed": corrective_action_needed,
        })
        return wo_id, job_id, r

    # ---- contract ----------------------------------------------------

    def test_create_then_get_and_list_ncr(self):
        wo_id, job_id, r = self._create_ncr()
        self.assertEqual(r.status_code, 201)
        ncr = r.get_json()["ncr"]
        self.assertEqual(ncr["status"], "open")
        ncr_id = ncr["ncr_id"]

        g = self.client.get("/api/ncrs/{}".format(ncr_id))
        self.assertEqual(g.status_code, 200)
        body = g.get_json()["ncr"]
        self.assertEqual(body["ncr_id"], ncr_id)
        self.assertIn("corrective_actions", body)

        lst = self.client.get("/api/ncrs?wo_id={}".format(wo_id))
        self.assertEqual(lst.status_code, 200)
        self.assertEqual(len(lst.get_json()["ncrs"]), 1)

    def test_full_ncr_ca_verify_close_happy_path(self):
        _wo_id, _job_id, r = self._create_ncr(corrective_action_needed="Y")
        ncr_id = r.get_json()["ncr"]["ncr_id"]

        c = self.client.post(
            "/api/ncrs/{}/corrective-actions".format(ncr_id),
            json={"root_cause_actions": "new jig"},
        )
        self.assertEqual(c.status_code, 201)
        ca_id = c.get_json()["corrective_action"]["ca_id"]

        v = self.client.post(
            "/api/corrective-actions/{}/verify".format(ca_id),
            json={"verifying_person": "Insp"},
        )
        self.assertEqual(v.status_code, 200)
        self.assertEqual(
            v.get_json()["corrective_action"]["status"], "verified"
        )

        cl = self.client.post("/api/ncrs/{}/close".format(ncr_id))
        self.assertEqual(cl.status_code, 200)
        self.assertEqual(cl.get_json()["ncr"]["status"], "closed")

    def test_patch_ca_updates_fields(self):
        _wo_id, _job_id, r = self._create_ncr(corrective_action_needed="Y")
        ncr_id = r.get_json()["ncr"]["ncr_id"]
        c = self.client.post(
            "/api/ncrs/{}/corrective-actions".format(ncr_id),
            json={"root_cause_actions": "x"},
        )
        ca_id = c.get_json()["corrective_action"]["ca_id"]
        p = self.client.patch(
            "/api/corrective-actions/{}".format(ca_id),
            json={"responsible_persons": "Lead"},
        )
        self.assertEqual(p.status_code, 200)
        self.assertEqual(
            p.get_json()["corrective_action"]["responsible_persons"], "Lead"
        )

    # ---- negative ----------------------------------------------------

    def test_create_ncr_invalid_ca_needed_is_400(self):
        wo_id, job_id = self._make_job()
        r = self.client.post("/api/ncrs", json={
            "job_id": job_id, "wo_id": wo_id, "description": "d",
            "reported_by": "QC", "corrective_action_needed": "maybe",
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_create_ncr_blank_description_is_400(self):
        wo_id, job_id = self._make_job()
        r = self.client.post("/api/ncrs", json={
            "job_id": job_id, "wo_id": wo_id, "description": "  ",
            "reported_by": "QC",
        })
        self.assertEqual(r.status_code, 400)

    def test_create_ncr_unknown_job_is_404(self):
        r = self.client.post("/api/ncrs", json={
            "job_id": 999999, "wo_id": "WO-001", "description": "d",
            "reported_by": "QC",
        })
        self.assertEqual(r.status_code, 404)

    def test_get_missing_ncr_is_404(self):
        r = self.client.get("/api/ncrs/424242")
        self.assertEqual(r.status_code, 404)

    def test_close_missing_ncr_is_404(self):
        r = self.client.post("/api/ncrs/424242/close")
        self.assertEqual(r.status_code, 404)

    def test_create_ca_when_not_needed_is_409(self):
        _wo_id, _job_id, r = self._create_ncr(corrective_action_needed="N")
        ncr_id = r.get_json()["ncr"]["ncr_id"]
        c = self.client.post(
            "/api/ncrs/{}/corrective-actions".format(ncr_id),
            json={"root_cause_actions": "x"},
        )
        self.assertEqual(c.status_code, 409)

    def test_close_ncr_with_unverified_ca_is_409(self):
        _wo_id, _job_id, r = self._create_ncr(corrective_action_needed="Y")
        ncr_id = r.get_json()["ncr"]["ncr_id"]
        self.client.post(
            "/api/ncrs/{}/corrective-actions".format(ncr_id),
            json={"root_cause_actions": "x"},
        )
        cl = self.client.post("/api/ncrs/{}/close".format(ncr_id))
        self.assertEqual(cl.status_code, 409)


if __name__ == "__main__":
    unittest.main()
