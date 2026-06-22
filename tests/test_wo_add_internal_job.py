"""Batch 4 — add an Internal job (with new parts) to an existing WO.

POST /api/workorders/<wo_id>/jobs with {job_type:'Internal', parts:[...]}
creates one Internal jobs row, inserts its NEW parts (line_items →
queue_items linked to that job_id), and re-rolls status via status_sync.
This is distinct from the queue_ids assign path, which only groups
pre-existing loose parts and creates none.

The status-clobber gate: adding fresh queued work can move a 'completed'
WO to 'in_progress' (correct — there's now unfinished work), but a
'delivered' WO must NOT be reopened (status_sync's delivered guard).
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
from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.routes import register_work_order_routes
from app.domains.work_orders.service import WorkOrderService


class _AddInternalJobSetup(unittest.TestCase):
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

    # ----- helpers -----

    def _post_internal(self, wo_id, parts):
        return self.client.post(
            "/api/workorders/{}/jobs".format(wo_id),
            json={"job_type": "Internal", "parts": parts},
        )

    def _open_wo(self, quantity=1):
        return self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "loose", "material": "PLA", "quantity": quantity}],
        )["wo_id"]

    def _empty_wo(self):
        return self.wo_repo.create_work_order("Acme", [])["wo_id"]

    def _jobs(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM jobs WHERE wo_id=? ORDER BY job_id", (wo_id,)
            ).fetchall()]
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

    def _line_item_count(self, wo_id):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM line_items WHERE wo_id=?", (wo_id,)
            ).fetchone()[0]
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

    def _set_all_completed_and_sync(self, wo_id):
        """Drive every queue_item to 'completed' and re-roll → 'completed'.
        Uses loose parts (no jobs) so no inspection gate is involved."""
        conn = self.job_repo._get_conn()
        try:
            conn.execute(
                "UPDATE queue_items SET status='completed' WHERE wo_id=?",
                (wo_id,),
            )
            status_sync.sync_work_order_status(conn, wo_id)
            conn.commit()
        finally:
            conn.close()

    def _deliver(self, wo_id):
        conn = self.job_repo._get_conn()
        try:
            status_sync.set_work_order_status_terminal(
                conn, wo_id, "delivered"
            )
            conn.commit()
        finally:
            conn.close()


class AddInternalJobContractTests(_AddInternalJobSetup):

    def test_add_internal_job_to_open_wo_creates_linked_parts(self):
        # CONTRACT: add an Internal job with 2 new parts to an existing
        # 'open' WO → ONE Internal jobs row + 2 line_items + N queue_items
        # linked to it; WO stays 'open'.
        wo_id = self._empty_wo()
        r = self._post_internal(wo_id, [
            {"part_name": "A", "material": "PLA", "quantity": 1},
            {"part_name": "B", "material": "PLA", "quantity": 2},
        ])
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))

        jobs = self._jobs(wo_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["job_type"], "Internal")
        self.assertEqual(self._line_item_count(wo_id), 2)

        qis = self._queue_items(wo_id)
        self.assertEqual(len(qis), 3)  # 1 + 2
        self.assertTrue(all(qi["job_id"] == jobs[0]["job_id"] for qi in qis))
        self.assertEqual(self._wo_status(wo_id), "open")

    def test_add_to_completed_wo_moves_to_in_progress(self):
        # CONTRACT: adding fresh unfinished work to a 'completed' (not
        # delivered) WO moves it back to 'in_progress' — correct.
        wo_id = self._open_wo(quantity=1)
        self._set_all_completed_and_sync(wo_id)
        self.assertEqual(self._wo_status(wo_id), "completed")

        r = self._post_internal(wo_id, [
            {"part_name": "New", "material": "PLA", "quantity": 1},
        ])
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.assertEqual(self._wo_status(wo_id), "in_progress")

    def test_add_to_delivered_wo_does_not_reopen(self):
        # CONTRACT (the guard): adding an Internal job to a 'delivered' WO
        # must NOT reopen it — the delivered guard in status_sync holds.
        wo_id = self._open_wo(quantity=1)
        self._set_all_completed_and_sync(wo_id)
        self._deliver(wo_id)
        self.assertEqual(self._wo_status(wo_id), "delivered")

        r = self._post_internal(wo_id, [
            {"part_name": "New", "material": "PLA", "quantity": 1},
        ])
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        # The job/parts are added, but the WO stays terminal.
        self.assertEqual(self._wo_status(wo_id), "delivered")

    def test_parts_are_created_not_assigned(self):
        # CONTRACT: the parts are NEW (created); a pre-existing loose part
        # is left untouched (job_id stays NULL) — this is NOT the
        # create_job assign-existing-parts path.
        wo_id = self._open_wo(quantity=1)  # 1 loose part, job_id NULL
        loose_before = self._queue_items(wo_id)
        self.assertEqual(len(loose_before), 1)
        self.assertIsNone(loose_before[0]["job_id"])

        r = self._post_internal(wo_id, [
            {"part_name": "New", "material": "PLA", "quantity": 2},
        ])
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))

        qis = self._queue_items(wo_id)
        self.assertEqual(len(qis), 3)  # 1 loose + 2 newly created
        # the original loose part was NOT assigned to the new job
        loose_after = next(q for q in qis
                           if q["queue_id"] == loose_before[0]["queue_id"])
        self.assertIsNone(loose_after["job_id"])
        # the 2 new parts carry the new Internal job_id
        new_job_id = self._jobs(wo_id)[0]["job_id"]
        new_parts = [q for q in qis if q["job_id"] == new_job_id]
        self.assertEqual(len(new_parts), 2)


class AddInternalJobWitnessTests(_AddInternalJobSetup):

    def test_payload_shape_and_response_counts(self):
        # WITNESS: the POST body shape {job_type, parts:[...]} and the
        # response counts. A correct fix may change these specifics
        # without breaking the contracts above.
        wo_id = self._empty_wo()
        r = self._post_internal(wo_id, [
            {"part_name": "A", "material": "PLA", "quantity": 2},
            {"part_name": "B", "material": "PETG", "quantity": 1},
        ])
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        body = r.get_json()
        self.assertTrue(body["success"])
        self.assertIn("job_id", body)
        self.assertEqual(body["parts_created"], 3)   # queue_items
        self.assertEqual(body["line_item_count"], 2)  # line_items rows

    def test_bad_part_rejects_with_400(self):
        # WITNESS: a malformed part fails the request (no job created).
        wo_id = self._empty_wo()
        r = self._post_internal(wo_id, [
            {"part_name": "A", "material": "", "quantity": 1},
        ])
        self.assertEqual(r.status_code, 400, r.get_data(as_text=True))
        self.assertIn("material", r.get_json()["error"].lower())
        self.assertEqual(self._jobs(wo_id), [])

    def test_missing_wo_returns_404(self):
        # WITNESS: unknown WO → 404 from add_internal_job.
        r = self._post_internal("WO-NOPE", [
            {"part_name": "A", "material": "PLA", "quantity": 1},
        ])
        self.assertEqual(r.status_code, 404, r.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
