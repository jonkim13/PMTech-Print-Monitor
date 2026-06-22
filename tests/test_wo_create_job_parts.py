"""Batch 3 — Job→Parts model at Work Order creation.

Philip's entity diagram: a Work Order contains Jobs; each Job has a type
and contains Parts. The New WO flow now POSTs every group as a job in
``jobs``, and an Internal job nests its parts under ``parts``. The backend
creates a real jobs row per group (including Internal) and links each
part's queue_items to that job_id at CREATION time — no more loose,
job_id-NULL Internal parts from this flow. Because parts are pre-linked,
the print path adopts the existing job instead of auto-creating a second
one.
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

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.routes import register_work_order_routes
from app.domains.work_orders.service import WorkOrderService


class _CreateJobPartsSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "work_orders.db")
        self.job_repo = JobRepository(self.db)
        self.exec_repo = QueueExecutionRepository(self.db)
        self.q_repo = QueueRepository(self.db)
        self.wo_repo = WorkOrderRepository(self.db)
        self.service = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
        )
        app = Flask(__name__)
        app.config["TESTING"] = True
        register_work_order_routes(
            app, farm_manager=None, work_order_service=self.service,
        )
        self.client = app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def _post(self, **body):
        return self.client.post("/api/workorders", json=body)

    def _jobs(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM jobs WHERE wo_id=? ORDER BY job_id", (wo_id,)
            ).fetchall()]
        finally:
            conn.close()

    def _line_items(self, wo_id):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM line_items WHERE wo_id=?", (wo_id,)
            ).fetchone()[0]
        finally:
            conn.close()

    def _queue_items(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT queue_id, job_id, status FROM queue_items "
                "WHERE wo_id=? ORDER BY queue_id", (wo_id,)
            ).fetchall()]
        finally:
            conn.close()

    def _wo_status(self, wo_id):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT status FROM work_orders WHERE wo_id=?", (wo_id,)
            ).fetchone()[0]
        finally:
            conn.close()


class InternalJobPartsContractTests(_CreateJobPartsSetup):

    def test_internal_job_with_3_parts_creates_one_job_linked(self):
        # CONTRACT: one Internal job with 3 parts → ONE jobs row
        # (job_type Internal), 3 line_items, N queue_items all carrying
        # that job_id, WO status 'open'.
        r = self._post(
            customer_name="Acme",
            jobs=[{"job_type": "Internal", "parts": [
                {"part_name": "A", "material": "PLA", "quantity": 1},
                {"part_name": "B", "material": "PLA", "quantity": 2},
                {"part_name": "C", "material": "PETG", "quantity": 1},
            ]}],
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        wo_id = r.get_json()["wo_id"]

        jobs = self._jobs(wo_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_type"], "Internal")

        self.assertEqual(self._line_items(wo_id), 3)
        qis = self._queue_items(wo_id)
        self.assertEqual(len(qis), 4)  # 1 + 2 + 1
        # Every queue_item is linked to the one job at creation time.
        self.assertTrue(all(qi["job_id"] == jobs[0]["job_id"] for qi in qis))
        self.assertEqual(self._wo_status(wo_id), "open")

    def test_print_adopts_precreated_job_no_second_row(self):
        # CONTRACT: printing a part of the pre-created job adopts the
        # existing job_id and creates NO second jobs row.
        r = self._post(
            customer_name="Acme",
            jobs=[{"job_type": "Internal", "parts": [
                {"part_name": "A", "material": "PLA", "quantity": 2},
            ]}],
        )
        wo_id = r.get_json()["wo_id"]
        job_id = self._jobs(wo_id)[0]["job_id"]
        qids = [qi["queue_id"] for qi in self._queue_items(wo_id)]

        # Print with no requested_job_id — the print path must adopt the
        # job already on the parts rather than auto-creating one.
        result = self.exec_repo.start_queue_job_execution(
            qids, printer_id="P1", printer_name="Printer 1",
            gcode_file="a.gcode",
        )
        self.assertEqual(result["job_id"], job_id)
        self.assertFalse(result["auto_created_job"])
        self.assertEqual(len(self._jobs(wo_id)), 1)  # still ONE jobs row

    def test_mixed_internal_and_external_two_jobs(self):
        # CONTRACT: a WO mixing an Internal job (2 parts) + an External
        # job creates two jobs rows of the correct types.
        r = self._post(
            customer_name="Acme",
            jobs=[
                {"job_type": "Internal", "parts": [
                    {"part_name": "A", "material": "PLA", "quantity": 1},
                    {"part_name": "B", "material": "PLA", "quantity": 1},
                ]},
                {"job_type": "External", "vendor": "MachiningCo",
                 "external_process": "CNC Mill"},
            ],
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        wo_id = r.get_json()["wo_id"]

        jobs = self._jobs(wo_id)
        self.assertEqual(sorted(j["job_type"] for j in jobs),
                         ["External", "Internal"])
        # The 2 Internal parts link to the Internal job; the External job
        # carries none.
        internal = next(j for j in jobs if j["job_type"] == "Internal")
        qis = self._queue_items(wo_id)
        self.assertEqual(len(qis), 2)
        self.assertTrue(all(qi["job_id"] == internal["job_id"] for qi in qis))
        self.assertEqual(self._wo_status(wo_id), "open")


class CreateJobPartsWitnessTests(_CreateJobPartsSetup):

    def test_payload_shape_and_response_counts(self):
        # WITNESS: the exact nested POST payload shape and the response's
        # intermediate counts (parts_created = queue_items, job_count,
        # line_item_count = line_items rows). A correct fix may change
        # these specifics without breaking the contracts above.
        r = self._post(
            customer_name="Acme",
            jobs=[{"job_type": "Internal", "parts": [
                {"part_name": "A", "material": "PLA", "quantity": 2},
                {"part_name": "B", "material": "PLA", "quantity": 1},
            ]}],
        )
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        self.assertEqual(body["parts_created"], 3)   # queue_items
        self.assertEqual(body["line_item_count"], 2)  # line_items rows
        self.assertEqual(body["job_count"], 1)

    def test_part_missing_material_rejects_whole_create(self):
        # WITNESS: a bad nested part fails the request atomically — no WO
        # row leaks.
        before = self._wo_count()
        r = self._post(
            customer_name="Acme",
            jobs=[{"job_type": "Internal", "parts": [
                {"part_name": "A", "material": "", "quantity": 1},
            ]}],
        )
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertIn("material", r.get_json()["error"].lower())
        self.assertEqual(self._wo_count(), before)

    def _wo_count(self):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM work_orders"
            ).fetchone()[0]
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
