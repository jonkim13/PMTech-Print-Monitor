"""Phase 2.5c — WorkOrderService.get_work_order extended payload.

Validates the 2.5c additions:
- inspection summary per Internal job (passed/failed/pending/total/state)
- counts block (total/done/printing/queued/failed/pending/in_transit)
- activity timeline synthesized from queue_items + WO transitions
"""

import os
import sqlite3
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.service import WorkOrderService
from app.domains.queue.repository import QueueRepository
from app.domains.queue.execution_repository import QueueExecutionRepository


class _StubProductionJobRepo:
    """Production-log repo stub that lets us drive QC outcomes per
    print_job_id without standing up the real production DB.

    Important: stash the caller's dict by reference (not `or {}`,
    which falsy-replaces an empty dict with a new one) so later
    mutations on _Fixture.prod_jobs are visible here.
    """
    def __init__(self, jobs_by_id=None):
        self._jobs = jobs_by_id if jobs_by_id is not None else {}

    def get_job(self, job_id):
        return self._jobs.get(job_id)


class _Fixture:
    """Builds a minimal in-memory work_orders.db + service, then exposes
    helpers to set queue-item status + production outcomes."""
    def __init__(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "wo.db")
        # init order matters for FKs
        self.job_repo = JobRepository(self.db_path)
        self.queue_exec = QueueExecutionRepository(self.db_path)
        self.queue_repo = QueueRepository(self.db_path)
        self.wo_repo = WorkOrderRepository(self.db_path)
        self.prod_jobs = {}
        self.service = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
            queue_repository=self.queue_repo,
            queue_execution_repository=self.queue_exec,
            production_job_repository=_StubProductionJobRepo(self.prod_jobs),
        )

    def close(self):
        self.tmpdir.cleanup()

    def create_wo(self, customer="Acme", line_items=None,
                  due_date=None) -> str:
        items = line_items or [
            {"part_name": "widget", "material": "PLA", "quantity": 3},
        ]
        result = self.wo_repo.create_work_order(customer, items,
                                                 due_date=due_date)
        return result["wo_id"]

    def set_queue_item(self, queue_id, **fields):
        """UPDATE queue_items SET ... WHERE queue_id=?"""
        if not fields:
            return
        cols = ", ".join("{}=?".format(k) for k in fields.keys())
        params = list(fields.values()) + [queue_id]
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE queue_items SET {} WHERE queue_id=?".format(cols),
            params,
        )
        conn.commit()
        conn.close()

    def assign_to_job(self, queue_ids, wo_id):
        """Create a job for the given queue_ids."""
        return self.job_repo.create_job(wo_id, queue_ids=list(queue_ids))

    def set_production_outcome(self, queue_id, print_job_id,
                               outcome="pass", operator="JR"):
        """Link a queue_item to a (stubbed) print_job and assign QC."""
        self.set_queue_item(queue_id, print_job_id=print_job_id)
        self.prod_jobs[print_job_id] = {
            "job_id": print_job_id, "outcome": outcome,
            "operator": operator, "notes": "",
        }


class WoDetailExtendedPayloadTests(unittest.TestCase):
    def setUp(self):
        self.fx = _Fixture()
        self.wo_id = self.fx.create_wo()
        # 3 queue items get auto-created by line_items quantity=3
        rows = sqlite3.connect(self.fx.db_path).execute(
            "SELECT queue_id, status FROM queue_items WHERE wo_id=? "
            "ORDER BY queue_id", (self.wo_id,)
        ).fetchall()
        self.queue_ids = [r[0] for r in rows]

    def tearDown(self):
        self.fx.close()

    def test_counts_block_sums_correctly(self):
        # 1 done (with QC pass), 1 printing, 1 queued
        self.fx.set_queue_item(self.queue_ids[0],
                                status="completed",
                                completed_at="2026-05-20T12:00:00")
        self.fx.set_queue_item(self.queue_ids[1], status="printing")
        # queue_ids[2] stays 'queued'
        self.fx.set_production_outcome(self.queue_ids[0], 101, "pass")

        wo = self.fx.service.get_work_order(self.wo_id)
        self.assertIn("counts", wo)
        c = wo["counts"]
        self.assertEqual(c["total"], 3)
        self.assertEqual(c["done"], 1)
        self.assertEqual(c["printing"], 1)
        self.assertEqual(c["queued"], 1)
        self.assertEqual(c["failed"], 0)
        # 'pending' is completed-but-unknown-outcome. Only QI 0 is
        # completed and it's pass → pending should be 0.
        self.assertEqual(c["pending"], 0)
        self.assertEqual(c["in_transit"], 0)

    def test_counts_counts_pending_qc_correctly(self):
        # Mark one as completed without setting QC outcome.
        self.fx.set_queue_item(self.queue_ids[0],
                                status="completed",
                                completed_at="2026-05-20T12:00:00")
        # No production_outcome → defaults to unknown → counts as pending.

        wo = self.fx.service.get_work_order(self.wo_id)
        self.assertEqual(wo["counts"]["done"], 1)
        self.assertEqual(wo["counts"]["pending"], 1)

    def test_counts_groups_uploading_under_printing(self):
        self.fx.set_queue_item(self.queue_ids[0], status="uploading")
        self.fx.set_queue_item(self.queue_ids[1], status="starting")
        wo = self.fx.service.get_work_order(self.wo_id)
        # uploading + starting both count under 'printing' for the
        # design's stacked bar.
        self.assertEqual(wo["counts"]["printing"], 2)

    def test_inspection_summary_for_internal_job(self):
        # Create a job spanning all 3 queue items, then mark 2 done
        # with QC pass/fail and one still printing.
        job = self.fx.assign_to_job(self.queue_ids, self.wo_id)
        jid = job["job_id"]
        self.fx.set_queue_item(self.queue_ids[0],
                                status="completed",
                                completed_at="2026-05-20T10:00:00")
        self.fx.set_queue_item(self.queue_ids[1],
                                status="completed",
                                completed_at="2026-05-20T11:00:00")
        self.fx.set_queue_item(self.queue_ids[2], status="printing")
        self.fx.set_production_outcome(self.queue_ids[0], 200, "pass", "JR")
        self.fx.set_production_outcome(self.queue_ids[1], 201, "fail", "JR")

        wo = self.fx.service.get_work_order(self.wo_id)
        jobs = wo["jobs"]
        self.assertEqual(len(jobs), 1)
        job_dict = jobs[0]
        self.assertEqual(job_dict["job_id"], jid)
        self.assertEqual(job_dict.get("type"), "internal")
        insp = job_dict["inspection"]
        self.assertEqual(insp["passed"], 1)
        self.assertEqual(insp["failed"], 1)
        self.assertEqual(insp["pending"], 0)
        self.assertEqual(insp["total"], 2)  # only completed items count
        self.assertEqual(insp["state"], "failed")  # any failure dominates
        self.assertEqual(insp["inspector"], "JR")

    def test_inspection_state_pending_when_completed_but_no_outcome(self):
        job = self.fx.assign_to_job(self.queue_ids, self.wo_id)
        # Mark all 3 completed, give two of them an unknown outcome via
        # a stub that returns outcome=unknown, leave the third with no
        # production_job_id linkage at all.
        for qi in self.queue_ids:
            self.fx.set_queue_item(qi, status="completed",
                                    completed_at="2026-05-20T12:00:00")
        self.fx.set_production_outcome(self.queue_ids[0], 300, "unknown")
        # queue_ids[1] gets a link too but the stub doesn't know about it
        self.fx.set_queue_item(self.queue_ids[1], print_job_id=301)

        wo = self.fx.service.get_work_order(self.wo_id)
        insp = wo["jobs"][0]["inspection"]
        self.assertEqual(insp["passed"], 0)
        self.assertEqual(insp["failed"], 0)
        self.assertEqual(insp["pending"], 3)  # all 3 completed-unknown
        self.assertEqual(insp["total"], 3)
        self.assertEqual(insp["state"], "in-progress")

    def test_inspection_state_passed_when_all_pass(self):
        job = self.fx.assign_to_job(self.queue_ids, self.wo_id)
        for i, qi in enumerate(self.queue_ids):
            self.fx.set_queue_item(qi, status="completed",
                                    completed_at="2026-05-20T12:00:00")
            self.fx.set_production_outcome(qi, 400 + i, "pass", "MK")
        wo = self.fx.service.get_work_order(self.wo_id)
        insp = wo["jobs"][0]["inspection"]
        self.assertEqual(insp["passed"], 3)
        self.assertEqual(insp["state"], "passed")
        self.assertEqual(insp["inspector"], "MK")

    def test_activity_timeline_includes_wo_created(self):
        wo = self.fx.service.get_work_order(self.wo_id)
        self.assertIn("activity", wo)
        kinds = [e["kind"] for e in wo["activity"]]
        self.assertIn("wo-created", kinds)

    def test_activity_timeline_synthesizes_events_from_queue_items(self):
        self.fx.set_queue_item(self.queue_ids[0],
                                status="completed",
                                started_at="2026-05-20T10:00:00",
                                completed_at="2026-05-20T11:00:00",
                                assigned_printer_name="Core One #1")
        self.fx.set_queue_item(self.queue_ids[1],
                                status="failed",
                                started_at="2026-05-20T10:30:00",
                                completed_at="2026-05-20T10:45:00",
                                assigned_printer_name="XL #1")
        self.fx.set_production_outcome(self.queue_ids[0], 500, "pass", "JR")

        wo = self.fx.service.get_work_order(self.wo_id)
        kinds = [e["kind"] for e in wo["activity"]]
        # Each completed/failed item emits at least one event.
        self.assertIn("started", kinds)
        self.assertIn("completed", kinds)
        self.assertIn("failed", kinds)
        # QC pass becomes its own event when there's a production outcome.
        self.assertIn("qc-pass", kinds)

    def test_activity_timeline_sorted_newest_first(self):
        self.fx.set_queue_item(self.queue_ids[0],
                                status="completed",
                                started_at="2026-05-20T10:00:00",
                                completed_at="2026-05-20T11:00:00")
        wo = self.fx.service.get_work_order(self.wo_id)
        ts_list = [e["ts"] for e in wo["activity"]]
        self.assertEqual(ts_list, sorted(ts_list, reverse=True))


if __name__ == "__main__":
    unittest.main()
