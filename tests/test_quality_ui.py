"""Phase E2 — quality UI: server-render markup + route-shape contract.

Mirrors the Phase D UI test approach: DB-free Jinja rendering of the
job-card macros and modal partials, static template-wiring checks, a
JS-source contract guard (the NCR indicator + handlers are JS-rendered
and exercised in the browser, which is the user's manual step), and a
route-shape test pinning the NCR-detail GET response the UI consumes.
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
from app.domains.quality.repository import QualityRepository
from app.domains.quality.routes import register_quality_routes
from app.domains.quality.service import QualityService
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository

TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates")
JS_DIR = os.path.join(ROOT_DIR, "static", "js", "pages", "wo-detail")


class CreateNcrButtonRenderTests(unittest.TestCase):
    """The Phase D failed-inspection card now emits an ENABLED button."""

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

    def test_internal_failed_card_has_enabled_create_ncr(self):
        job = {
            "job_id": 7, "wo_id": "WO-009", "type": "internal",
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
        self.assertIn("WoDetail.openNcrModal(7, 'WO-009')", html)
        # The old Phase D disabled placeholder must be gone.
        self.assertNotIn("NCR workflow — Phase E", html)

    def test_external_failed_card_has_enabled_create_ncr(self):
        job = {
            "job_id": 8, "wo_id": "WO-009", "type": "external",
            "status": "attention", "part_count": 0, "completed_parts": 0,
            "vendor": "Acme", "external_process": "Anodize",
        }
        html = self._render_external(job)
        self.assertIn("Create NCR", html)
        self.assertIn("WoDetail.openNcrModal(8, 'WO-009')", html)
        self.assertNotIn("NCR workflow — Phase E", html)


class ModalPartialRenderTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html"]),
        )

    def _render(self, path):
        return self.env.get_template(path).render()

    def test_create_ncr_modal(self):
        html = self._render("partials/modals/create_ncr.html")
        self.assertIn('id="createNcrModal"', html)
        self.assertIn('id="ncrDescription"', html)
        self.assertIn('id="ncrReportedBy"', html)
        self.assertIn('id="ncrAffectedParts"', html)
        self.assertIn('name="ncrCaNeeded"', html)
        self.assertIn("WoDetail.submitNcr()", html)

    def test_create_ca_modal(self):
        html = self._render("partials/modals/create_ca.html")
        self.assertIn('id="createCaModal"', html)
        self.assertIn('id="caRootCause"', html)
        self.assertIn('id="caResponsible"', html)
        self.assertIn('id="caEffectiveness"', html)
        self.assertIn("WoDetail.submitCa()", html)

    def test_verify_ca_modal(self):
        html = self._render("partials/modals/verify_ca.html")
        self.assertIn('id="verifyCaModal"', html)
        self.assertIn('id="verifyCaPerson"', html)
        self.assertIn("WoDetail.submitCaVerify()", html)

    def test_ncr_detail_modal(self):
        html = self._render("partials/modals/ncr_detail.html")
        self.assertIn('id="ncrDetailModal"', html)
        self.assertIn('id="ncrDetailBody"', html)
        self.assertIn('id="ncrDetailError"', html)


class TemplateWiringTests(unittest.TestCase):
    """Static checks that wo_detail.html wires the host + modal includes."""

    def setUp(self):
        with open(os.path.join(TEMPLATES_DIR, "wo_detail.html"),
                  encoding="utf-8") as fh:
            self.body = fh.read()

    def test_ncr_rail_host_present(self):
        self.assertIn('id="wo-ncr-body"', self.body)
        self.assertIn('id="wo-ncr-count"', self.body)

    def test_modal_includes_present(self):
        for partial in ("create_ncr.html", "create_ca.html",
                        "verify_ca.html", "ncr_detail.html"):
            self.assertIn(
                'partials/modals/{}'.format(partial), self.body,
                "wo_detail.html must include {}".format(partial),
            )


class JsSourceContractTests(unittest.TestCase):
    """Source-level guards for the JS-rendered NCR indicator + handlers
    (the DOM behaviour itself is the manual browser-smoke step)."""

    def _read(self, name):
        with open(os.path.join(JS_DIR, name), encoding="utf-8") as fh:
            return fh.read()

    def test_render_module_renders_open_ncr_with_attention_styling(self):
        src = self._read("detail-render.js")
        self.assertIn("function renderNcrs", src)
        self.assertIn("ncr_summary", src)
        self.assertIn("openNcrDetail", src)
        # Open NCRs render with attention (tone-err) styling.
        self.assertIn("tone-err", src)

    def test_actions_module_exposes_ncr_ca_handlers(self):
        src = self._read("detail-actions.js")
        for handler in ("W.openNcrModal", "W.submitNcr", "W.openNcrDetail",
                        "W.openCaModal", "W.submitCa", "W.openCaVerifyModal",
                        "W.submitCaVerify", "W.closeNcr"):
            self.assertIn(handler, src,
                          "detail-actions.js must define {}".format(handler))

    def test_failed_controls_button_is_no_longer_disabled(self):
        src = self._read("detail-render.js")
        # The JS mirror of the failed-controls block must emit the
        # enabled NCR button, not the old disabled placeholder.
        self.assertIn("WoDetail.openNcrModal(", src)
        self.assertNotIn("NCR workflow — Phase E", src)


class NcrDetailRouteShapeTests(unittest.TestCase):
    """Pin the GET /api/ncrs/<id> response shape the UI depends on:
    {ncr: {..., corrective_actions: [...]}}. Guards against a backend
    rename silently breaking the detail view."""

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

    def test_ncr_detail_shape_has_nested_corrective_actions(self):
        wo_id, job_id = self._make_job()
        r = self.client.post("/api/ncrs", json={
            "job_id": job_id, "wo_id": wo_id, "description": "defect",
            "reported_by": "QC", "corrective_action_needed": "Y",
        })
        ncr_id = r.get_json()["ncr"]["ncr_id"]
        self.client.post(
            "/api/ncrs/{}/corrective-actions".format(ncr_id),
            json={"root_cause_actions": "new jig"},
        )

        g = self.client.get("/api/ncrs/{}".format(ncr_id))
        self.assertEqual(g.status_code, 200)
        payload = g.get_json()
        self.assertIn("ncr", payload)
        ncr = payload["ncr"]
        # Fields the detail view reads directly.
        for key in ("ncr_id", "status", "corrective_action_needed",
                    "description", "reported_by", "affected_parts",
                    "remedial_action", "created_at"):
            self.assertIn(key, ncr)
        # CAs nested under ncr.corrective_actions (not top-level).
        self.assertIn("corrective_actions", ncr)
        self.assertEqual(len(ncr["corrective_actions"]), 1)
        ca = ncr["corrective_actions"][0]
        for key in ("ca_id", "status", "root_cause_actions"):
            self.assertIn(key, ca)


if __name__ == "__main__":
    unittest.main()
