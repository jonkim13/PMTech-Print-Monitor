"""Cancel/retry behaviour at WO, Job, and Part levels.

Validates that every state transition runs the canonical status
rollup and that printer-side side effects fire only when they should.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.bulk_operations import QueueBulkOperations
from app.domains.queue.repository import QueueRepository
from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.service import QueueService
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.service import WorkOrderService


class FakeClient:
    def __init__(self, printer_id):
        self.printer_id = printer_id
        self.stop_calls = 0

    def stop_job(self):
        self.stop_calls += 1
        return {"ok": True}


class FakeFarmManager:
    def __init__(self):
        self.active_jobs = {}
        self.clients = {}
        self.stop_pending = set()
        self.cleared = set()

    def get_printer_client(self, printer_id):
        self.clients.setdefault(printer_id, FakeClient(printer_id))
        return self.clients[printer_id]

    def mark_stop_pending(self, printer_id):
        self.stop_pending.add(printer_id)

    def get_active_job_id(self, printer_id):
        return self.active_jobs.get(printer_id)

    def clear_active_job(self, printer_id):
        self.cleared.add(printer_id)
        self.active_jobs.pop(printer_id, None)

    def set_active_job(self, printer_id, job_id):
        self.active_jobs[printer_id] = job_id


class FakeProductionRepo:
    def __init__(self):
        self.jobs = {}

    def track(self, job_id, printer_id):
        self.jobs[job_id] = {
            "job_id": job_id, "printer_id": printer_id,
            "status": "started", "outcome": "unknown",
        }

    def stop_job(self, job_id, duration_sec=0):
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = "stopped"

    def update_job_qc(self, job_id, outcome=None, operator=None, notes=None):
        if job_id in self.jobs and outcome:
            self.jobs[job_id]["outcome"] = outcome
        return True


class CancelRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db = os.path.join(self.tmp, "wo.db")
        self.wo_repo = WorkOrderRepository(db)
        self.job_repo = JobRepository(db)
        self.queue_repo = QueueRepository(db)
        self.bulk_ops = QueueBulkOperations(db)
        self.exec_repo = QueueExecutionRepository(db)
        self.farm = FakeFarmManager()
        self.prod = FakeProductionRepo()
        self.queue_svc = QueueService(
            queue_repository=self.queue_repo,
            queue_bulk_operations=self.bulk_ops,
            execution_repository=self.exec_repo,
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
            farm_manager=self.farm,
            production_job_repository=self.prod,
        )
        self.wo_svc = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
            queue_repository=self.queue_repo,
            queue_bulk_operations=self.bulk_ops,
            queue_execution_repository=self.exec_repo,
            farm_manager=self.farm,
            production_job_repository=self.prod,
        )
        self.db = db

    def tearDown(self):
        shutil.rmtree(self.tmp)

    # ---- helpers ----
    def _qids(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT queue_id FROM queue_items WHERE wo_id=? ORDER BY queue_id",
            (wo_id,),
        ).fetchall()
        conn.close()
        return [r["queue_id"] for r in rows]

    def _wo_status(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM work_orders WHERE wo_id=?", (wo_id,)
        ).fetchone()
        conn.close()
        return row["status"] if row else None

    def _qi_status(self, queue_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM queue_items WHERE queue_id=?", (queue_id,)
        ).fetchone()
        conn.close()
        return row["status"] if row else None

    def _job_id_for_qid(self, queue_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT job_id FROM queue_items WHERE queue_id=?", (queue_id,)
        ).fetchone()
        conn.close()
        return row["job_id"] if row else None

    def _start_print(self, queue_ids, printer_id="P1", job_id=None):
        result = self.exec_repo.start_queue_job_execution(
            queue_ids, printer_id, f"Printer {printer_id}", "p.gcode",
            operator_initials="TS", job_id=job_id,
        )
        qjid = result["queue_job_id"]
        self.exec_repo.mark_queue_job_uploaded(qjid)
        self.exec_repo.mark_queue_job_starting(qjid)
        self.exec_repo.mark_queue_job_printing(qjid)
        self.prod.track(100 + qjid, printer_id=printer_id)
        self.farm.set_active_job(printer_id, 100 + qjid)
        return qjid

    # ---- tests ----
    def test_wo_status_rolls_up_when_print_starts(self):
        wo = self.wo_repo.create_work_order(
            "Cust", [{"part_name": "P", "material": "PLA", "quantity": 2}],
        )
        wo_id = wo["wo_id"]
        self.assertEqual(self._wo_status(wo_id), "open")

        qids = self._qids(wo_id)
        self._start_print([qids[0]])
        self.assertEqual(self._wo_status(wo_id), "in_progress")

    def test_cancel_wo_stops_printer_and_closes_production(self):
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 2}],
        )
        wo_id = wo["wo_id"]
        qids = self._qids(wo_id)
        qja = self._start_print([qids[0]])

        result = self.wo_svc.cancel_work_order(wo_id)
        self.assertTrue(result["found"])
        self.assertEqual(result["cancelled_count"], 2)
        self.assertEqual(result["printing_count"], 1)
        self.assertEqual(self._wo_status(wo_id), "cancelled")
        for qid in qids:
            self.assertEqual(self._qi_status(qid), "cancelled")
        self.assertGreaterEqual(self.farm.clients["P1"].stop_calls, 1)
        self.assertEqual(self.prod.jobs[100 + qja]["outcome"], "cancelled")

    def test_cancel_wo_leaves_completed_items_untouched(self):
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 2}],
        )
        wo_id = wo["wo_id"]
        qids = self._qids(wo_id)
        qja = self._start_print([qids[0]])
        self.exec_repo.complete_queue_job(qja, print_job_id=101)
        self.wo_svc.cancel_work_order(wo_id)
        self.assertEqual(self._qi_status(qids[0]), "completed")
        self.assertEqual(self._qi_status(qids[1]), "cancelled")
        # Phase D: inspection gate holds Internal jobs at in_progress until pass.
        # 1 completed + 1 cancelled, but the completed item's Internal job
        # is still awaiting inspection, so the WO rolls up to in_progress.
        self.assertEqual(self._wo_status(wo_id), "in_progress")
        # Passing inspection releases the gate → WO completes.
        self.wo_svc.record_inspection(
            self._job_id_for_qid(qids[0]), outcome="pass", inspector="QC"
        )
        self.assertEqual(self._wo_status(wo_id), "completed")

    def test_cancel_job_scopes_to_that_job(self):
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 3}],
        )
        wo_id = wo["wo_id"]
        qids = self._qids(wo_id)
        job = self.job_repo.create_job(wo_id, queue_ids=[qids[0], qids[1]])
        # qids[2] is unassigned.
        self._start_print([qids[0]], job_id=job["job_id"])
        result = self.wo_svc.cancel_job(job["job_id"])
        self.assertTrue(result["found"])
        self.assertEqual(result["cancelled_count"], 2)
        self.assertEqual(self._qi_status(qids[0]), "cancelled")
        self.assertEqual(self._qi_status(qids[1]), "cancelled")
        self.assertEqual(self._qi_status(qids[2]), "queued")
        # derive_work_order_status returns 'open' when no item has
        # started/failed/completed — a queued-only remainder is "fresh".
        self.assertEqual(self._wo_status(wo_id), "open")

    def test_cancel_queue_item_stops_printer_only_if_printing(self):
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 2}],
        )
        wo_id = wo["wo_id"]
        qids = self._qids(wo_id)
        self._start_print([qids[0]])
        # Cancel the printing item → stops
        r1 = self.queue_svc.cancel_queue_item(qids[0])
        self.assertEqual(r1["printing_count"], 1)
        self.assertEqual(self.farm.clients["P1"].stop_calls, 1)
        # Cancel the queued item → no stop
        r2 = self.queue_svc.cancel_queue_item(qids[1])
        self.assertEqual(r2["printing_count"], 0)
        self.assertEqual(self.farm.clients["P1"].stop_calls, 1)

    def test_cancel_idempotent(self):
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 1}],
        )
        qid = self._qids(wo["wo_id"])[0]
        self.assertEqual(
            self.queue_svc.cancel_queue_item(qid)["cancelled_count"], 1,
        )
        self.assertEqual(
            self.queue_svc.cancel_queue_item(qid)["cancelled_count"], 0,
        )

    def test_retry_queue_item_clears_assignment(self):
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 1}],
        )
        qid = self._qids(wo["wo_id"])[0]
        qja = self._start_print([qid])
        self.queue_repo.fail_queue_item(qid)
        self.queue_svc.retry_queue_item(qid)

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute(
            "SELECT * FROM queue_items WHERE queue_id=?", (qid,)
        ).fetchone())
        conn.close()
        self.assertIsNone(row["queue_job_id"])
        self.assertIsNone(row["print_job_id"])
        self.assertEqual(row["status"], "queued")

    def test_retry_queued_item_is_noop(self):
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 1}],
        )
        qid = self._qids(wo["wo_id"])[0]
        r = self.queue_svc.retry_queue_item(qid)
        self.assertTrue(r["found"])
        self.assertEqual(r["requeued_count"], 0)

    def test_retry_after_cancel_reverts_wo_to_non_cancelled(self):
        """Retrying the sole cancelled item should un-cancel the WO.

        With only a queued item remaining the canonical derivation
        returns 'open' — the WO hasn't touched any active/completed/
        failure state. The key property is that retry moves the WO
        *off* 'cancelled'.
        """
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 1}],
        )
        wo_id = wo["wo_id"]
        qid = self._qids(wo_id)[0]
        self.queue_svc.cancel_queue_item(qid)
        self.assertEqual(self._wo_status(wo_id), "cancelled")
        self.queue_svc.retry_queue_item(qid)
        self.assertIn(self._wo_status(wo_id), ("open", "in_progress"))
        self.assertNotEqual(self._wo_status(wo_id), "cancelled")

    def test_retry_part_of_completed_wo_reverts_to_in_progress(self):
        """If a WO has completed + newly-queued, rollup is in_progress."""
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 2}],
        )
        wo_id = wo["wo_id"]
        qids = self._qids(wo_id)
        qja = self._start_print([qids[0]])
        self.exec_repo.complete_queue_job(qja, print_job_id=101)
        qjb = self._start_print([qids[1]])
        self.exec_repo.complete_queue_job(qjb, print_job_id=102)
        # Phase D: inspection gate holds Internal jobs at in_progress until pass.
        # Both parts are queue-complete but their Internal jobs await QC.
        self.assertEqual(self._wo_status(wo_id), "in_progress")
        for qid in qids:
            self.wo_svc.record_inspection(
                self._job_id_for_qid(qid), outcome="pass", inspector="QC"
            )
        self.assertEqual(self._wo_status(wo_id), "completed")

        # Use the admin path to flip one to failed so retry picks it up.
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE queue_items SET status='failed' WHERE queue_id=?",
                     (qids[0],))
        from app.domains.work_orders import status_sync
        status_sync.sync_work_order_status(conn, wo_id)
        conn.commit(); conn.close()
        self.queue_svc.retry_queue_item(qids[0])
        # completed + queued → in_progress
        self.assertEqual(self._wo_status(wo_id), "in_progress")

    def test_retry_wo_requeues_all_cancelled_and_failed(self):
        wo = self.wo_repo.create_work_order(
            "C", [{"part_name": "P", "material": "PLA", "quantity": 3}],
        )
        wo_id = wo["wo_id"]
        qids = self._qids(wo_id)
        self.queue_svc.cancel_queue_item(qids[0])
        self.queue_svc.cancel_queue_item(qids[1])
        # qids[2] stays queued
        r = self.wo_svc.retry_work_order(wo_id)
        self.assertEqual(r["requeued_count"], 2)
        for qid in qids:
            self.assertEqual(self._qi_status(qid), "queued")


if __name__ == "__main__":
    unittest.main()
