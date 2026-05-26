"""Phase C — Job Type route endpoints.

Drives the new + extended HTTP surface in
``app/domains/work_orders/routes.py``:

- POST /api/workorders/<wo_id>/jobs (extended)
- POST /api/jobs/<job_id>/start
- POST /api/jobs/<job_id>/complete
- PATCH /api/jobs/<job_id>/external
- PATCH /api/jobs/<job_id>/design
- PATCH /api/jobs/<job_id>/inspection

Each test stands up a fresh Flask app + tempdir DB so route state is
isolated. Routes are registered against the test app via
``register_work_order_routes`` — module globals get re-bound per
test, which is fine because unittest runs sequentially.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

from flask import Flask

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService
from app.domains.work_orders.routes import register_work_order_routes
from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository


def _build_app_with_service(db_path):
    job_repo = JobRepository(db_path)
    QueueExecutionRepository(db_path)
    QueueRepository(db_path)
    wo_repo = WorkOrderRepository(db_path)
    service = WorkOrderService(
        work_order_repository=wo_repo,
        job_repository=job_repo,
    )
    app = Flask(__name__)
    app.config["TESTING"] = True
    register_work_order_routes(
        app,
        farm_manager=None,
        work_order_service=service,
    )
    return app, service, wo_repo, job_repo


def _queue_ids_for_wo(db_path, wo_id):
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT queue_id FROM queue_items WHERE wo_id = ? "
            "ORDER BY queue_id ASC", (wo_id,),
        ).fetchall()]
    finally:
        conn.close()


def _job_row(db_path, job_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


class JobTypeRouteTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "work_orders.db")
        self.app, self.service, self.wo_repo, self.job_repo = (
            _build_app_with_service(self.db_path)
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    # ----- helpers -----

    def _create_wo(self, customer="Acme", quantity=1):
        return self.wo_repo.create_work_order(
            customer,
            [{"part_name": "widget", "material": "PLA",
              "quantity": quantity}]
            if quantity > 0 else [],
        )

    def _post_create_job(self, wo_id, **body):
        return self.client.post(
            "/api/workorders/{}/jobs".format(wo_id),
            json=body,
        )

    # ------------------------------------------------------------------
    # 1. Back-compat: POST without job_type creates Internal
    # ------------------------------------------------------------------

    def test_post_workorder_jobs_internal_unchanged(self):
        wo = self._create_wo(quantity=2)
        queue_ids = _queue_ids_for_wo(self.db_path, wo["wo_id"])
        r = self._post_create_job(wo["wo_id"], queue_ids=queue_ids)
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["assigned_count"], 2)

        row = _job_row(self.db_path, body["job"]["job_id"])
        self.assertEqual(row["job_type"], "Internal")

    # ------------------------------------------------------------------
    # 2. POST External with full payload succeeds
    # ------------------------------------------------------------------

    def test_post_workorder_jobs_external_succeeds(self):
        wo = self._create_wo()
        r = self._post_create_job(
            wo["wo_id"],
            job_type="External",
            vendor="MachiningCo",
            external_process="CNC Mill",
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        row = _job_row(self.db_path, body["job"]["job_id"])
        self.assertEqual(row["job_type"], "External")
        self.assertEqual(row["vendor"], "MachiningCo")
        self.assertEqual(row["external_process"], "CNC Mill")

    # ------------------------------------------------------------------
    # 3. POST External missing fields → 400
    # ------------------------------------------------------------------

    def test_post_workorder_jobs_external_missing_fields_400(self):
        wo = self._create_wo()
        r = self._post_create_job(
            wo["wo_id"],
            job_type="External",
            external_process="CNC Mill",  # vendor missing
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("vendor", r.get_json()["error"].lower())

    # ------------------------------------------------------------------
    # 4. POST Design with designer succeeds
    # ------------------------------------------------------------------

    def test_post_workorder_jobs_design_succeeds(self):
        wo = self._create_wo()
        r = self._post_create_job(
            wo["wo_id"],
            job_type="Design",
            designer="Jonathan",
            requirements="Redesign bracket.",
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        row = _job_row(self.db_path, r.get_json()["job"]["job_id"])
        self.assertEqual(row["job_type"], "Design")
        self.assertEqual(row["designer"], "Jonathan")
        self.assertEqual(row["requirements"], "Redesign bracket.")

    # ------------------------------------------------------------------
    # 5. Invalid job_type → 400
    # ------------------------------------------------------------------

    def test_post_workorder_jobs_invalid_type_400(self):
        wo = self._create_wo()
        r = self._post_create_job(wo["wo_id"], job_type="Bogus")
        self.assertEqual(r.status_code, 400)

    # ------------------------------------------------------------------
    # 6. POST /start External → in_progress
    # ------------------------------------------------------------------

    def test_post_jobs_start_external_succeeds(self):
        wo = self._create_wo()
        post = self._post_create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.post("/api/jobs/{}/start".format(job_id))
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["status"], "in_progress")

        row = _job_row(self.db_path, job_id)
        self.assertEqual(row["status"], "in_progress")
        self.assertIsNotNone(row["started_at"])

    # ------------------------------------------------------------------
    # 7. POST /start Internal → 400
    # ------------------------------------------------------------------

    def test_post_jobs_start_internal_rejected(self):
        wo = self._create_wo()
        queue_ids = _queue_ids_for_wo(self.db_path, wo["wo_id"])
        post = self._post_create_job(wo["wo_id"], queue_ids=queue_ids)
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.post("/api/jobs/{}/start".format(job_id))
        self.assertEqual(r.status_code, 400)

    # ------------------------------------------------------------------
    # 8. POST /complete Design → completed; WO rolled up
    # ------------------------------------------------------------------

    def test_post_jobs_complete_design_succeeds(self):
        wo = self._create_wo(quantity=0)  # Design-only WO
        post = self._post_create_job(
            wo["wo_id"], job_type="Design", designer="Jonathan",
        )
        job_id = post.get_json()["job"]["job_id"]
        self.client.post("/api/jobs/{}/start".format(job_id))

        r = self.client.post("/api/jobs/{}/complete".format(job_id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "completed")

        row = _job_row(self.db_path, job_id)
        self.assertEqual(row["status"], "completed")
        wo_after = self.wo_repo.get_work_order(wo["wo_id"])
        self.assertEqual(wo_after["status"], "completed")

    # ------------------------------------------------------------------
    # 9. PATCH /external partial update
    # ------------------------------------------------------------------

    def test_patch_jobs_external_partial(self):
        wo = self._create_wo()
        post = self._post_create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.patch(
            "/api/jobs/{}/external".format(job_id),
            json={"date_delivered": "2026-05-26"},
        )
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

        row = _job_row(self.db_path, job_id)
        self.assertEqual(row["date_delivered"], "2026-05-26")
        self.assertEqual(row["vendor"], "MachiningCo")
        self.assertEqual(row["external_process"], "CNC Mill")

    # ------------------------------------------------------------------
    # 10. PATCH /external on a Design job → 400
    # ------------------------------------------------------------------

    def test_patch_jobs_external_on_design_rejected(self):
        wo = self._create_wo()
        post = self._post_create_job(
            wo["wo_id"], job_type="Design", designer="Jonathan",
        )
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.patch(
            "/api/jobs/{}/external".format(job_id),
            json={"vendor": "Hijack"},
        )
        self.assertEqual(r.status_code, 400)

        # Side effect check — vendor must NOT have been written.
        row = _job_row(self.db_path, job_id)
        self.assertIsNone(row["vendor"])

    # ------------------------------------------------------------------
    # 11. PATCH /design partial update
    # ------------------------------------------------------------------

    def test_patch_jobs_design_partial(self):
        wo = self._create_wo()
        post = self._post_create_job(
            wo["wo_id"], job_type="Design",
            designer="Jonathan", requirements="Original brief.",
        )
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.patch(
            "/api/jobs/{}/design".format(job_id),
            json={"design_completed_at": "2026-05-26T12:00:00+00:00"},
        )
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

        row = _job_row(self.db_path, job_id)
        self.assertEqual(row["design_completed_at"],
                         "2026-05-26T12:00:00+00:00")
        self.assertEqual(row["designer"], "Jonathan")
        self.assertEqual(row["requirements"], "Original brief.")

    # ------------------------------------------------------------------
    # 12. PATCH /design on an External job → 400
    # ------------------------------------------------------------------

    def test_patch_jobs_design_on_external_rejected(self):
        wo = self._create_wo()
        post = self._post_create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.patch(
            "/api/jobs/{}/design".format(job_id),
            json={"designer": "Hijack"},
        )
        self.assertEqual(r.status_code, 400)
        row = _job_row(self.db_path, job_id)
        self.assertIsNone(row["designer"])

    # ------------------------------------------------------------------
    # 13. PATCH /inspection on Internal succeeds
    # ------------------------------------------------------------------

    def test_patch_jobs_inspection_internal_succeeds(self):
        wo = self._create_wo()
        queue_ids = _queue_ids_for_wo(self.db_path, wo["wo_id"])
        post = self._post_create_job(wo["wo_id"], queue_ids=queue_ids)
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.patch(
            "/api/jobs/{}/inspection".format(job_id),
            json={
                "inspection_report": "Pass — all in spec.",
                "inspector": "JK",
                "inspection_date": "2026-05-26",
            },
        )
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

        row = _job_row(self.db_path, job_id)
        self.assertEqual(row["inspection_report"], "Pass — all in spec.")
        self.assertEqual(row["inspector"], "JK")
        self.assertEqual(row["inspection_date"], "2026-05-26")

    # ------------------------------------------------------------------
    # 14. PATCH /inspection on External succeeds
    # ------------------------------------------------------------------

    def test_patch_jobs_inspection_external_succeeds(self):
        wo = self._create_wo()
        post = self._post_create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.patch(
            "/api/jobs/{}/inspection".format(job_id),
            json={"inspector": "JK", "inspection_date": "2026-05-26"},
        )
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))

        row = _job_row(self.db_path, job_id)
        self.assertEqual(row["inspector"], "JK")
        self.assertEqual(row["inspection_date"], "2026-05-26")
        # Original External fields untouched.
        self.assertEqual(row["vendor"], "MachiningCo")
        self.assertEqual(row["external_process"], "CNC Mill")

    # ------------------------------------------------------------------
    # 15. PATCH /inspection on Design → 400
    # ------------------------------------------------------------------

    def test_patch_jobs_inspection_design_rejected(self):
        wo = self._create_wo()
        post = self._post_create_job(
            wo["wo_id"], job_type="Design", designer="Jonathan",
        )
        job_id = post.get_json()["job"]["job_id"]

        r = self.client.patch(
            "/api/jobs/{}/inspection".format(job_id),
            json={"inspector": "JK"},
        )
        self.assertEqual(r.status_code, 400)
        row = _job_row(self.db_path, job_id)
        self.assertIsNone(row["inspector"])


if __name__ == "__main__":
    unittest.main()
