"""Triage board — Ready to Ship + Design · Awaiting Customer lanes.

These two lanes were stubbed ('Phase B' placeholder, returned empty).
The functionality they describe now exists (Phase F delivery, Phase C
Design jobs), so they are wired to the data already reachable from
work_orders.db, and the placeholder empty-state copy is replaced.
"""

import os
import shutil
import sys
import tempfile
import unittest

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.triage.service import TriageService
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository

TEMPLATES_DIR = os.path.join(ROOT_DIR, "templates")


class TriageLaneDataTests(unittest.TestCase):
    """Service-level: the lanes populate from work_orders.db."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "wo.db")
        self.wo_repo = WorkOrderRepository(self.db)
        self.job_repo = JobRepository(self.db)
        QueueRepository(self.db)
        QueueExecutionRepository(self.db)
        self.svc = TriageService(
            queue_repository=None,
            work_order_repository=self.wo_repo,
            print_job_repository=None,
            inventory_repository=None,
            farm_manager=None,
            work_order_db_path=self.db,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _set_wo_status(self, wo_id, status):
        conn = self.wo_repo._get_conn()
        try:
            conn.execute(
                "UPDATE work_orders SET status=? WHERE wo_id=?",
                (status, wo_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ---- Ready to Ship -----------------------------------------------

    def test_ready_ship_lists_completed_wo(self):
        wo = self.wo_repo.create_work_order("Acme Corp", [])
        self._set_wo_status(wo["wo_id"], "completed")
        lane = self.svc._lane_ready_ship()
        self.assertEqual(lane["kind"], "ready_ship")
        self.assertEqual(lane["count"], 1)
        item = lane["items"][0]
        self.assertEqual(item["wo_id"], wo["wo_id"])
        self.assertEqual(item["title"], "Acme Corp")

    def test_ready_ship_excludes_delivered(self):
        wo = self.wo_repo.create_work_order("Acme", [])
        self._set_wo_status(wo["wo_id"], "delivered")
        self.assertEqual(self.svc._lane_ready_ship()["count"], 0)

    def test_ready_ship_excludes_in_progress(self):
        wo = self.wo_repo.create_work_order("Acme", [])
        self._set_wo_status(wo["wo_id"], "in_progress")
        self.assertEqual(self.svc._lane_ready_ship()["count"], 0)

    def test_ready_ship_empty_state(self):
        lane = self.svc._lane_ready_ship()
        self.assertEqual(lane["count"], 0)
        self.assertEqual(lane["items"], [])

    # ---- Design · Awaiting Customer ----------------------------------

    def test_design_await_lists_done_unapproved(self):
        wo = self.wo_repo.create_work_order("DesignCo", [])
        job = self.job_repo.create_job(
            wo["wo_id"], job_type="Design", designer="Dee"
        )
        self.job_repo.update_design_job_fields(
            job["job_id"], design_completed_at="2026-05-01T00:00:00+00:00"
        )
        lane = self.svc._lane_design_await()
        self.assertEqual(lane["kind"], "design_await")
        self.assertEqual(lane["count"], 1)
        item = lane["items"][0]
        self.assertEqual(item["wo_id"], wo["wo_id"])
        self.assertEqual(item["job_id"], job["job_id"])
        self.assertIn("Dee", item["title"])
        self.assertEqual(item["customer"], "DesignCo")

    def test_design_await_excludes_approved(self):
        wo = self.wo_repo.create_work_order("DesignCo", [])
        job = self.job_repo.create_job(
            wo["wo_id"], job_type="Design", designer="Dee"
        )
        self.job_repo.update_design_job_fields(
            job["job_id"],
            design_completed_at="2026-05-01T00:00:00+00:00",
            approved_by="Customer Jane",
        )
        self.assertEqual(self.svc._lane_design_await()["count"], 0)

    def test_design_await_excludes_not_yet_done(self):
        # Created Design job, no design_completed_at, status still 'open'.
        wo = self.wo_repo.create_work_order("DesignCo", [])
        self.job_repo.create_job(
            wo["wo_id"], job_type="Design", designer="Dee"
        )
        self.assertEqual(self.svc._lane_design_await()["count"], 0)

    def test_design_await_excludes_internal_jobs(self):
        wo = self.wo_repo.create_work_order("Acme", [])
        # An Internal job, even if completed, is not a design-approval item.
        job = self.job_repo.create_job(wo["wo_id"], job_type="Internal")
        self._set_wo_status(wo["wo_id"], "completed")
        # (Internal job has no design fields; must not appear.)
        self.assertEqual(self.svc._lane_design_await()["count"], 0)

    def test_design_await_empty_state(self):
        lane = self.svc._lane_design_await()
        self.assertEqual(lane["count"], 0)
        self.assertEqual(lane["items"], [])

    # ---- Payload shape -----------------------------------------------

    def test_payload_includes_all_five_lanes(self):
        payload = self.svc.get_triage_payload()
        kinds = [lane["kind"] for lane in payload["lanes"]]
        self.assertEqual(
            kinds,
            ["failed", "qc", "ready_ship", "design_await", "external_spool"],
        )


class TriageTemplateEmptyStateTests(unittest.TestCase):
    """The stale 'Phase B' placeholder is gone; honest empty-state copy
    is rendered for both lanes."""

    @classmethod
    def setUpClass(cls):
        cls.env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=select_autoescape(["html"]),
        )

    def test_workorders_partial_has_no_phase_b_placeholder(self):
        html = self.env.get_template(
            "partials/pages/workorders.html"
        ).render()
        self.assertNotIn("Phase B", html)
        self.assertIn("No completed orders awaiting delivery.", html)
        self.assertIn("No design jobs awaiting approval.", html)

    def test_lane_bodies_carry_new_empty_message_dataset(self):
        html = self.env.get_template(
            "partials/pages/workorders.html"
        ).render()
        # The lane macro writes empty_message into data-lane-empty-message,
        # which triage.js reads when a lane has no items.
        self.assertIn(
            'data-lane-empty-message="No completed orders awaiting '
            'delivery."', html,
        )
        self.assertIn(
            'data-lane-empty-message="No design jobs awaiting approval."',
            html,
        )


if __name__ == "__main__":
    unittest.main()
