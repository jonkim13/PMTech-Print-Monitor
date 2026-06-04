"""Phase 2.5c — WO Detail HTML route + JSON polling alias.

Validates:
- GET /work-orders/<wo_id> renders 200 with the new template
- 404 with friendly body for missing WO
- Breadcrumb back_url reflects ?from=triage|all|dashboard
- DEEP-LINKED pill renders only when ?focus= is set
- GET /api/work-orders/<wo_id> mirrors /api/workorders/<wo_id>
- Existing /api/workorders/<wo_id> still works
"""

import os
import sys
import unittest
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


class WoDetailRouteTests(unittest.TestCase):
    """Render the route against the dev DB. Since the dev DB may have
    no work orders, we create one programmatically per test via the
    work_order_service exposed on app.extensions['print_farm_container']."""

    @classmethod
    def setUpClass(cls):
        from app.main import create_app
        cls.app = create_app(start_poller=False)
        cls.container = cls.app.extensions["print_farm_container"]

    def _make_wo(self, customer="Test Co", due_date=None):
        return self.container.work_order_service.create_work_order(
            customer, [
                {"part_name": "test-widget", "material": "PLA",
                 "quantity": 2},
            ],
            due_date=due_date,
        )

    def test_route_returns_404_for_missing_wo(self):
        with self.app.test_client() as c:
            r = c.get("/work-orders/WO-DOES-NOT-EXIST")
            self.assertEqual(r.status_code, 404)
            body = r.data.decode("utf-8")
            self.assertIn("Work order not found", body)
            self.assertIn("tab=workorders", body)

    def test_route_renders_200_for_existing_wo(self):
        result = self._make_wo()
        wo_id = result["wo_id"]
        with self.app.test_client() as c:
            r = c.get("/work-orders/" + wo_id)
            self.assertEqual(r.status_code, 200)
            body = r.data.decode("utf-8")
            self.assertIn('id="page-wo-detail"', body)
            self.assertIn(wo_id, body)
            self.assertIn("Test Co", body)
            self.assertIn("Back to All Orders", body)

    def test_breadcrumb_back_label_follows_from_query(self):
        result = self._make_wo()
        wo_id = result["wo_id"]
        with self.app.test_client() as c:
            r = c.get("/work-orders/" + wo_id + "?from=triage")
            body = r.data.decode("utf-8")
            self.assertIn("Back to Triage", body)
            self.assertIn("tab=workorders", body)

            r = c.get("/work-orders/" + wo_id + "?from=dashboard")
            body = r.data.decode("utf-8")
            self.assertIn("Back to Dashboard", body)
            self.assertIn("tab=dashboard", body)

    def test_deep_link_pill_only_when_focus_is_set(self):
        result = self._make_wo()
        wo_id = result["wo_id"]
        with self.app.test_client() as c:
            no_focus = c.get("/work-orders/" + wo_id).data.decode("utf-8")
            self.assertNotIn("DEEP-LINKED", no_focus)

            with_focus = c.get(
                "/work-orders/" + wo_id + "?focus=JOB-99"
            ).data.decode("utf-8")
            self.assertIn("DEEP-LINKED", with_focus)
            self.assertIn("JOB-99", with_focus)

    def test_api_work_orders_hyphenated_mirrors_legacy_endpoint(self):
        result = self._make_wo(customer="Mirror Co")
        wo_id = result["wo_id"]
        with self.app.test_client() as c:
            legacy = c.get("/api/workorders/" + wo_id).get_json()
            hyphenated = c.get("/api/work-orders/" + wo_id).get_json()
            self.assertEqual(legacy["wo_id"], hyphenated["wo_id"])
            self.assertEqual(legacy["customer_name"],
                             hyphenated["customer_name"])
            for key in ("counts", "activity", "jobs"):
                self.assertIn(key, legacy)
                self.assertIn(key, hyphenated)

    def test_api_returns_404_for_missing_wo(self):
        with self.app.test_client() as c:
            r1 = c.get("/api/workorders/WO-MISSING")
            r2 = c.get("/api/work-orders/WO-MISSING")
            self.assertEqual(r1.status_code, 404)
            self.assertEqual(r2.status_code, 404)

    def test_phase_tracker_renders_synthetic_terminals(self):
        result = self._make_wo()
        wo_id = result["wo_id"]
        with self.app.test_client() as c:
            body = c.get("/work-orders/" + wo_id).data.decode("utf-8")
            self.assertIn("WO sign-off", body)
            self.assertIn("Deliver", body)

    def test_sidebar_uses_href_links_on_wo_detail(self):
        result = self._make_wo()
        wo_id = result["wo_id"]
        with self.app.test_client() as c:
            body = c.get("/work-orders/" + wo_id).data.decode("utf-8")
            self.assertIn('href="/?tab=dashboard"', body)
            self.assertIn('href="/?tab=workorders"', body)
            self.assertIn('href="/?tab=inventory"', body)

    # ------------------------------------------------------------------
    # Unassigned (loose) queue_items must get a selectable render path.
    # Regression for: a WO with queued parts but zero jobs showed only the
    # "No jobs created yet" message — no checkboxes — so "Create job from
    # selected" stayed permanently disabled and the WO dead-ended.
    # ------------------------------------------------------------------

    def test_unassigned_queued_parts_render_as_selectable_rows(self):
        """A WO with queued parts and no jobs renders the parts as
        selectable rows (not just the empty-state message)."""
        result = self._make_wo()
        wo_id = result["wo_id"]
        wo = self.container.work_order_service.get_work_order(wo_id)
        self.assertEqual(wo["jobs"], [])
        qids = [qi["queue_id"] for qi in wo["queue_items"]]
        self.assertTrue(qids)
        with self.app.test_client() as c:
            body = c.get("/work-orders/" + wo_id).data.decode("utf-8")
            self.assertIn("Unassigned parts", body)
            self.assertIn("test-widget", body)
            # Each loose part is a checkbox wired to the existing
            # selection handler the "Create job from selected" button reads.
            for qid in qids:
                self.assertIn(
                    'class="wo-part-select" data-queue-id="{}"'.format(qid),
                    body,
                )
            self.assertIn("WoDetail.togglePartSelection", body)
            # The bare empty-state must not show when parts exist.
            self.assertNotIn("No jobs created yet", body)

    def test_mixed_assigned_and_loose_parts_both_render(self):
        """A WO with one part in a job and another still loose renders
        the job card AND the loose part as a selectable row."""
        result = self._make_wo()
        wo_id = result["wo_id"]
        wo = self.container.work_order_service.get_work_order(wo_id)
        qids = [qi["queue_id"] for qi in wo["queue_items"]]
        self.assertGreaterEqual(len(qids), 2)
        assigned_qid, loose_qid = qids[0], qids[1]
        self.container.work_order_service.create_job(
            wo_id, job_type="Internal", queue_ids=[assigned_qid]
        )
        with self.app.test_client() as c:
            body = c.get("/work-orders/" + wo_id).data.decode("utf-8")
            # Job card is present...
            self.assertIn("data-job-id=", body)
            # ...and the loose part is still rendered as selectable.
            self.assertIn("Unassigned parts", body)
            self.assertIn(
                'class="wo-part-select" data-queue-id="{}"'.format(loose_qid),
                body,
            )

    def test_empty_state_message_when_no_jobs_and_no_parts(self):
        """Regression guard: a WO with no jobs and no queue_items still
        shows the empty-state message and no unassigned-parts list."""
        result = self._make_wo()
        wo_id = result["wo_id"]
        wo = self.container.work_order_service.get_work_order(wo_id)
        empty = dict(wo)
        empty["jobs"] = []
        empty["queue_items"] = []
        with mock.patch.object(
            self.container.work_order_service,
            "get_work_order",
            return_value=empty,
        ):
            with self.app.test_client() as c:
                body = c.get("/work-orders/" + wo_id).data.decode("utf-8")
                self.assertIn("No jobs created yet", body)
                self.assertNotIn("Unassigned parts", body)


if __name__ == "__main__":
    unittest.main()
