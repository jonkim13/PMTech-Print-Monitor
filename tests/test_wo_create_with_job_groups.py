"""Phase G — declare job types at Work Order creation.

Drives the extended ``POST /api/workorders`` endpoint, which now accepts
Internal ``line_items`` (expanded into queue_items, unchanged) AND/OR a
``jobs`` list of non-Internal specs (External: vendor + external_process;
Design: designer + optional requirements). Everything is created in one
transaction; an invalid spec rejects the whole request and creates
nothing.

Each test stands up a fresh Flask app + tempdir DB (mirrors
test_job_type_routes.py) so route state is isolated.
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


def _build_app(db_path):
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
    return app


def _wo_count(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]
    finally:
        conn.close()


def _queue_count_for_wo(db_path, wo_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE wo_id = ?", (wo_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def _jobs_for_wo(db_path, wo_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE wo_id = ? ORDER BY job_id ASC", (wo_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class CreateWorkOrderWithJobGroupsTests(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "work_orders.db")
        self.client = _build_app(self.db_path).test_client()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _post(self, **body):
        return self.client.post("/api/workorders", json=body)

    # ------------------------------------------------------------------
    # 1. Internal-only — same result as the pre-Phase-G endpoint.
    # ------------------------------------------------------------------

    def test_internal_only_matches_legacy_behavior(self):
        r = self._post(
            customer_name="Acme",
            line_items=[{"part_name": "Bracket", "material": "PLA",
                         "quantity": 2}],
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        wo_id = body["wo_id"]
        self.assertEqual(body["parts_created"], 2)
        self.assertEqual(_queue_count_for_wo(self.db_path, wo_id), 2)
        self.assertEqual(_jobs_for_wo(self.db_path, wo_id), [])

    def test_internal_only_with_empty_jobs_list(self):
        # An explicit empty `jobs` must behave identically to omitting it.
        r = self._post(
            customer_name="Acme",
            line_items=[{"part_name": "Bracket", "material": "PLA",
                         "quantity": 1}],
            jobs=[],
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertEqual(
            _queue_count_for_wo(self.db_path, r.get_json()["wo_id"]), 1
        )

    # ------------------------------------------------------------------
    # 2. External-only — no throwaway part, vendor/process persisted.
    # ------------------------------------------------------------------

    def test_external_only_no_throwaway_part(self):
        r = self._post(
            customer_name="Acme",
            jobs=[{"job_type": "External", "vendor": "MachiningCo",
                   "external_process": "CNC Mill"}],
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        wo_id = body["wo_id"]
        self.assertEqual(body["parts_created"], 0)
        self.assertEqual(body["job_count"], 1)
        self.assertEqual(_queue_count_for_wo(self.db_path, wo_id), 0)

        jobs = _jobs_for_wo(self.db_path, wo_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_type"], "External")
        self.assertEqual(jobs[0]["vendor"], "MachiningCo")
        self.assertEqual(jobs[0]["external_process"], "CNC Mill")
        self.assertEqual(jobs[0]["status"], "open")

    # ------------------------------------------------------------------
    # 3. Design-only — designer + requirements persisted.
    # ------------------------------------------------------------------

    def test_design_only_persists_fields(self):
        r = self._post(
            customer_name="Acme",
            jobs=[{"job_type": "Design", "designer": "Sam",
                   "requirements": "Lightweight bracket"}],
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        wo_id = r.get_json()["wo_id"]
        self.assertEqual(_queue_count_for_wo(self.db_path, wo_id), 0)

        jobs = _jobs_for_wo(self.db_path, wo_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_type"], "Design")
        self.assertEqual(jobs[0]["designer"], "Sam")
        self.assertEqual(jobs[0]["requirements"], "Lightweight bracket")

    # ------------------------------------------------------------------
    # 4. Mixed Internal + External + Design in one submit, atomic.
    # ------------------------------------------------------------------

    def test_mixed_types_created_atomically(self):
        r = self._post(
            customer_name="Acme",
            line_items=[{"part_name": "Bracket", "material": "PLA",
                         "quantity": 3}],
            jobs=[
                {"job_type": "External", "vendor": "MachiningCo",
                 "external_process": "CNC Mill"},
                {"job_type": "Design", "designer": "Sam"},
            ],
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        wo_id = body["wo_id"]
        self.assertEqual(body["parts_created"], 3)
        self.assertEqual(body["job_count"], 2)
        self.assertEqual(_queue_count_for_wo(self.db_path, wo_id), 3)

        jobs = _jobs_for_wo(self.db_path, wo_id)
        types = sorted(j["job_type"] for j in jobs)
        self.assertEqual(types, ["Design", "External"])

    # ------------------------------------------------------------------
    # 5. Invalid spec rejects the whole create — nothing persisted.
    # ------------------------------------------------------------------

    def test_external_missing_vendor_creates_nothing(self):
        before = _wo_count(self.db_path)
        r = self._post(
            customer_name="Acme",
            line_items=[{"part_name": "Bracket", "material": "PLA",
                         "quantity": 1}],
            jobs=[{"job_type": "External", "external_process": "CNC Mill"}],
        )
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertIn("vendor", r.get_json()["error"].lower())
        # Atomic: no WO row, no queue_items leaked from the Internal group.
        self.assertEqual(_wo_count(self.db_path), before)

    def test_invalid_job_type_creates_nothing(self):
        before = _wo_count(self.db_path)
        r = self._post(
            customer_name="Acme",
            jobs=[{"job_type": "Bogus"}],
        )
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertEqual(_wo_count(self.db_path), before)

    def test_design_missing_designer_creates_nothing(self):
        before = _wo_count(self.db_path)
        r = self._post(
            customer_name="Acme",
            jobs=[{"job_type": "Design", "requirements": "x"}],
        )
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertEqual(_wo_count(self.db_path), before)

    # ------------------------------------------------------------------
    # 6. Neither line items nor jobs → 400.
    # ------------------------------------------------------------------

    def test_empty_order_rejected(self):
        r = self._post(customer_name="Acme")
        self.assertEqual(r.status_code, 400)
        self.assertIn("line item or job", r.get_json()["error"].lower())

    def test_missing_customer_rejected(self):
        r = self._post(
            jobs=[{"job_type": "Design", "designer": "Sam"}],
        )
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
