"""Phase D — inspection HTTP route + server-rendered UI markup.

Two concerns, two classes:

- ``RecordInspectionRouteTests`` drives ``POST /api/jobs/<id>/inspection``
  against an isolated work-order service wired into a bare Flask app
  (no dev DB, no full container). Covers the 200 happy path plus the
  400/404 error mappings the route declares.

- ``InspectionMarkupRenderTests`` renders the job-card macros and the
  inspection modal partial through a standalone Jinja environment
  (templates dir as loader, no DB) and asserts the Phase D controls —
  the Internal "Inspect" button, the External "Complete & Inspect"
  button, the failed-inspection pill, and the modal scaffolding — land
  in the HTML. This verifies markup only; live-browser click behaviour
  is not exercised here.
"""

import os
import shutil
import sys
import tempfile
import unittest

from flask import Flask
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.routes import register_work_order_routes
from app.domains.work_orders.service import WorkOrderService

TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates")


class RecordInspectionRouteTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "wo.db")
        self.wo_repo = WorkOrderRepository(self.db)
        self.job_repo = JobRepository(self.db)
        # Create the queue_items / queue_jobs schema used by WO creation.
        self.q_repo = QueueRepository(self.db)
        self.qe_repo = QueueExecutionRepository(self.db)
        self.svc = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
        )
        self.app = Flask(__name__)
        # Re-register per test so the route module's globals point at this
        # isolated service for the duration of these requests.
        register_work_order_routes(
            self.app, farm_manager=None, work_order_service=self.svc
        )
        self.client = self.app.test_client()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _external_in_progress_job(self):
        wo = self.wo_repo.create_work_order("Cust", [])
        job = self.svc.create_job(
            wo["wo_id"], job_type="External",
            vendor="Acme", external_process="Anodize",
        )
        self.svc.start_non_internal_job(job["job_id"])
        return job["job_id"]

    def test_post_pass_returns_200_with_completed_job(self):
        job_id = self._external_in_progress_job()
        r = self.client.post(
            "/api/jobs/{}/inspection".format(job_id),
            json={"outcome": "pass", "inspector": "QC"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["job"]["status"], "completed")
        self.assertEqual(body["job"]["inspection_outcome"], "pass")

    def test_post_invalid_outcome_returns_400(self):
        job_id = self._external_in_progress_job()
        r = self.client.post(
            "/api/jobs/{}/inspection".format(job_id),
            json={"outcome": "maybe", "inspector": "QC"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())

    def test_post_missing_inspector_returns_400(self):
        job_id = self._external_in_progress_job()
        r = self.client.post(
            "/api/jobs/{}/inspection".format(job_id),
            json={"outcome": "pass", "inspector": "  "},
        )
        self.assertEqual(r.status_code, 400)

    def test_post_design_job_returns_400(self):
        wo = self.wo_repo.create_work_order("Cust", [])
        job = self.svc.create_job(
            wo["wo_id"], job_type="Design", designer="DZ"
        )
        r = self.client.post(
            "/api/jobs/{}/inspection".format(job["job_id"]),
            json={"outcome": "pass", "inspector": "QC"},
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Design", r.get_json()["error"])

    def test_post_missing_job_returns_404(self):
        r = self.client.post(
            "/api/jobs/999999/inspection",
            json={"outcome": "pass", "inspector": "QC"},
        )
        self.assertEqual(r.status_code, 404)


class InspectionMarkupRenderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html"]),
        )

    def _render_internal(self, job):
        tpl = self.env.from_string(
            "{% from 'components/_job_card.html' import job_card_internal %}"
            "{{ job_card_internal(job, queue_items) }}"
        )
        return tpl.render(job=job, queue_items=[])

    def _render_external(self, job):
        tpl = self.env.from_string(
            "{% from 'components/_job_card.html' import job_card_external %}"
            "{{ job_card_external(job) }}"
        )
        return tpl.render(job=job)

    def test_internal_queue_complete_pending_shows_inspect_button(self):
        job = {
            "job_id": 1, "wo_id": "WO-001", "type": "internal",
            "status": "in_progress",
            "part_count": 1, "completed_parts": 1,
            "printing_parts": 0, "queued_parts": 0, "failed_parts": 0,
            "inspection": {"outcome": "pending", "pending": 0,
                           "passed": 1, "failed": 0, "total": 1,
                           "inspector": "QC", "state": "passed"},
        }
        html = self._render_internal(job)
        self.assertIn("openInspectionModal(1, 'Internal')", html)
        self.assertIn("clipboard-check", html)
        self.assertNotIn("Create NCR", html)

    def test_internal_failed_inspection_shows_pill_and_ncr_button(self):
        job = {
            "job_id": 1, "wo_id": "WO-001", "type": "internal",
            "status": "attention",
            "part_count": 1, "completed_parts": 1,
            "printing_parts": 0, "queued_parts": 0, "failed_parts": 0,
            "inspection": {"outcome": "fail", "pending": 0,
                           "passed": 0, "failed": 1, "total": 1,
                           "inspector": "QC", "state": "failed"},
        }
        html = self._render_internal(job)
        self.assertIn("INSPECTION FAILED", html)
        self.assertIn("Create NCR", html)
        # The Inspect button must NOT also render once we're in the
        # failed-controls branch.
        self.assertNotIn("openInspectionModal(1, 'Internal')", html)

    def test_external_in_progress_shows_complete_and_inspect(self):
        job = {
            "job_id": 2, "wo_id": "WO-001", "type": "external",
            "status": "in_progress", "part_count": 0, "completed_parts": 0,
            "vendor": "Acme", "external_process": "Anodize",
        }
        html = self._render_external(job)
        self.assertIn("Complete &amp; Inspect", html)
        self.assertIn("openInspectionModal(2, 'External')", html)

    def test_external_attention_shows_failed_controls(self):
        job = {
            "job_id": 2, "wo_id": "WO-001", "type": "external",
            "status": "attention", "part_count": 0, "completed_parts": 0,
            "vendor": "Acme", "external_process": "Anodize",
        }
        html = self._render_external(job)
        self.assertIn("INSPECTION FAILED", html)
        self.assertIn("Create NCR", html)

    def test_inspection_modal_partial_renders_scaffolding(self):
        html = self.env.get_template(
            "partials/modals/inspection.html"
        ).render()
        self.assertIn('id="inspectionModal"', html)
        self.assertIn('id="inspectionOutcome"', html)
        self.assertIn('id="inspectionInspector"', html)
        self.assertIn("WoDetail.submitInspection()", html)
        self.assertIn("Save inspection", html)


if __name__ == "__main__":
    unittest.main()
