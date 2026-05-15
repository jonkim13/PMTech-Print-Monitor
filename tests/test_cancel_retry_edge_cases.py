"""Cancel/retry edge-case scenarios pulled from the WO status + UX audit.

These are the six scenarios the spec called out as must-pass. They
used to live as a standalone script; protecting them under pytest
keeps regressions from reappearing after future refactors.
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

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.queue.service import QueueService
from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService


class _FakeClient:
    def __init__(self, printer_id):
        self.printer_id = printer_id
        self.stop_calls = 0

    def stop_job(self):
        self.stop_calls += 1
        return {"ok": True}


class _FakeFarmManager:
    def __init__(self):
        self.active_jobs = {}
        self.clients = {}
        self.stop_pending = set()

    def get_printer_client(self, printer_id):
        self.clients.setdefault(printer_id, _FakeClient(printer_id))
        return self.clients[printer_id]

    def mark_stop_pending(self, printer_id):
        self.stop_pending.add(printer_id)

    def get_active_job_id(self, printer_id):
        return self.active_jobs.get(printer_id)

    def clear_active_job(self, printer_id):
        self.active_jobs.pop(printer_id, None)

    def set_active_job(self, printer_id, job_id):
        self.active_jobs[printer_id] = job_id


class _FakeProductionRepo:
    def __init__(self):
        self.jobs = {}

    def track(self, job_id, printer_id):
        self.jobs[job_id] = {"job_id": job_id, "printer_id": printer_id,
                             "status": "started", "outcome": "unknown"}

    def stop_job(self, job_id, duration_sec=0):
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = "stopped"

    def update_job_qc(self, job_id, outcome=None, operator=None, notes=None):
        if job_id in self.jobs and outcome:
            self.jobs[job_id]["outcome"] = outcome
        return True


class EdgeCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "wo.db")
        self.wo_repo = WorkOrderRepository(self.db)
        self.job_repo = JobRepository(self.db)
        self.q_repo = QueueRepository(self.db)
        self.exec_repo = QueueExecutionRepository(self.db)
        self.farm = _FakeFarmManager()
        self.prod = _FakeProductionRepo()
        self.queue_svc = QueueService(
            queue_repository=self.q_repo,
            execution_repository=self.exec_repo,
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
            farm_manager=self.farm,
            production_job_repository=self.prod,
        )
        self.wo_svc = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
            queue_repository=self.q_repo,
            queue_execution_repository=self.exec_repo,
            farm_manager=self.farm,
            production_job_repository=self.prod,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _qids(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT queue_id FROM queue_items WHERE wo_id=? ORDER BY queue_id",
            (wo_id,),
        ).fetchall()
        conn.close()
        return [r["queue_id"] for r in rows]

    def _qi_status(self, queue_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM queue_items WHERE queue_id=?", (queue_id,)
        ).fetchone()
        conn.close()
        return row["status"] if row else None

    def _wo_status(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM work_orders WHERE wo_id=?", (wo_id,)
        ).fetchone()
        conn.close()
        return row["status"] if row else None

    def _start_print(self, queue_ids, printer_id="P1", job_id=None):
        res = self.exec_repo.start_queue_job_execution(
            queue_ids, printer_id, f"Printer {printer_id}", "p.gcode",
            operator_initials="TS", job_id=job_id,
        )
        qjid = res["queue_job_id"]
        self.exec_repo.mark_queue_job_uploaded(qjid)
        self.exec_repo.mark_queue_job_starting(qjid)
        self.exec_repo.mark_queue_job_printing(qjid)
        self.prod.track(100 + qjid, printer_id=printer_id)
        self.farm.set_active_job(printer_id, 100 + qjid)
        return qjid

    # ------------------------------------------------------------------
    # Edge 1: job with completed + printing + queued, cancel_job
    # ------------------------------------------------------------------
    def test_edge1_cancel_mixed_state_job(self):
        wo = self.wo_repo.create_work_order("E1", [
            {"part_name": "P", "material": "PLA", "quantity": 3},
        ])
        qids = self._qids(wo["wo_id"])
        job = self.job_repo.create_job(wo["wo_id"], queue_ids=qids)
        job_id = job["job_id"]

        qja = self._start_print([qids[0]], job_id=job_id)
        self.exec_repo.complete_queue_job(qja, print_job_id=101)
        qjb = self._start_print([qids[1]], job_id=job_id)

        result = self.wo_svc.cancel_job(job_id)
        self.assertEqual(result["cancelled_count"], 2)
        self.assertEqual(self._qi_status(qids[0]), "completed")
        self.assertEqual(self._qi_status(qids[1]), "cancelled")
        self.assertEqual(self._qi_status(qids[2]), "cancelled")
        self.assertGreaterEqual(self.farm.clients["P1"].stop_calls, 1)
        self.assertEqual(self.prod.jobs[100 + qjb]["outcome"], "cancelled")

    # ------------------------------------------------------------------
    # Edge 2: retry single part whose parent job is completed
    # ------------------------------------------------------------------
    def test_edge2_retry_when_sibling_is_completed(self):
        wo = self.wo_repo.create_work_order("E2", [
            {"part_name": "P", "material": "PLA", "quantity": 2},
        ])
        wo_id = wo["wo_id"]
        qids = self._qids(wo_id)
        job = self.job_repo.create_job(wo_id, queue_ids=qids)
        job_id = job["job_id"]

        qja = self._start_print([qids[0]], job_id=job_id)
        self.exec_repo.complete_queue_job(qja, print_job_id=101)
        qjb = self._start_print([qids[1]], job_id=job_id)
        self.exec_repo.complete_queue_job(qjb, print_job_id=102)
        self.assertEqual(self._wo_status(wo_id), "completed")

        # Retry on a genuinely completed part is a no-op by policy.
        r = self.queue_svc.retry_queue_item(qids[0])
        self.assertEqual(r["requeued_count"], 0)

        # Admin flips one back to failed, then retry does move it to
        # queued and rolls up the job + WO.
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE queue_items SET status='failed' WHERE queue_id=?",
                     (qids[0],))
        status_sync.sync_work_order_status(conn, wo_id)
        conn.commit(); conn.close()
        self.queue_svc.retry_queue_item(qids[0])
        self.assertEqual(self._wo_status(wo_id), "in_progress")

    # ------------------------------------------------------------------
    # Edge 3: cancel WO mid-print
    # ------------------------------------------------------------------
    def test_edge3_cancel_wo_mid_print(self):
        wo = self.wo_repo.create_work_order("E3", [
            {"part_name": "P", "material": "PLA", "quantity": 3},
        ])
        wo_id = wo["wo_id"]
        qids = self._qids(wo_id)
        qja = self._start_print([qids[0]])
        r = self.wo_svc.cancel_work_order(wo_id)
        self.assertEqual(r["cancelled_count"], 3)
        self.assertEqual(self._wo_status(wo_id), "cancelled")
        for qid in qids:
            self.assertEqual(self._qi_status(qid), "cancelled")
        self.assertGreaterEqual(self.farm.clients["P1"].stop_calls, 1)
        self.assertEqual(self.prod.jobs[100 + qja]["outcome"], "cancelled")

    # ------------------------------------------------------------------
    # Edge 4: requeue after failed production — new production record
    # ------------------------------------------------------------------
    def test_edge4_retry_creates_new_production_record(self):
        wo = self.wo_repo.create_work_order("E4", [
            {"part_name": "P", "material": "PLA", "quantity": 1},
        ])
        qid = self._qids(wo["wo_id"])[0]
        qja = self._start_print([qid])
        self.q_repo.fail_queue_item(qid)
        self.prod.jobs[100 + qja]["status"] = "failed"

        r = self.queue_svc.retry_queue_item(qid)
        self.assertEqual(r["requeued_count"], 1)

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute(
            "SELECT * FROM queue_items WHERE queue_id=?", (qid,)
        ).fetchone())
        conn.close()
        self.assertIsNone(row["queue_job_id"])
        self.assertIsNone(row["print_job_id"])

        qjb = self._start_print([qid])
        self.assertNotEqual(qjb, qja)
        self.assertIn(100 + qjb, self.prod.jobs)
        # Old production record untouched.
        self.assertEqual(self.prod.jobs[100 + qja]["status"], "failed")

    # ------------------------------------------------------------------
    # Edge 5: retry queued part is idempotent
    # ------------------------------------------------------------------
    def test_edge5_retry_queued_is_noop(self):
        wo = self.wo_repo.create_work_order("E5", [
            {"part_name": "P", "material": "PLA", "quantity": 1},
        ])
        qid = self._qids(wo["wo_id"])[0]
        r = self.queue_svc.retry_queue_item(qid)
        self.assertTrue(r["found"])
        self.assertEqual(r["requeued_count"], 0)

    # ------------------------------------------------------------------
    # Edge 6: cancel already-cancelled is idempotent
    # ------------------------------------------------------------------
    def test_edge6_cancel_cancelled_is_noop(self):
        wo = self.wo_repo.create_work_order("E6", [
            {"part_name": "P", "material": "PLA", "quantity": 1},
        ])
        qid = self._qids(wo["wo_id"])[0]
        r1 = self.queue_svc.cancel_queue_item(qid)
        r2 = self.queue_svc.cancel_queue_item(qid)
        self.assertEqual(r1["cancelled_count"], 1)
        self.assertEqual(r2["cancelled_count"], 0)
        self.assertEqual(self._qi_status(qid), "cancelled")


if __name__ == "__main__":
    unittest.main()
