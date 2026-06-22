"""Option (b) — a per-part QC write drives the job inspection gate.

Before this change the per-part QC path (PATCH /api/production/jobs/<id>)
wrote only print_jobs.outcome and never advanced the work order: the
job-level gate (jobs.inspection_outcome) stayed 'pending', so the job
lingered at in_progress and the WO never reached 'completed'.

Philip's decided model: inspection is PER PART. A job's gate is satisfied
AUTOMATICALLY when every part has passed QC; any failed part fails the
gate. WorkOrderService.propagate_part_qc recomputes the gate from the
parts and re-rolls job + WO status through the existing status_sync
helpers.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

from flask import Flask

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.production.job_repository import PrintJobRepository
from app.domains.production.routes import register_production_routes
from app.domains.production.service import ProductionService
from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService


class _PropagationSetup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "wo.db")
        self.prod_db = os.path.join(self.tmp, "prod.db")
        self.wo_repo = WorkOrderRepository(self.db)
        self.job_repo = JobRepository(self.db)
        self.q_repo = QueueRepository(self.db)
        self.qe_repo = QueueExecutionRepository(self.db)
        self.prod_repo = PrintJobRepository(self.prod_db)
        self.svc = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
            queue_repository=self.q_repo,
            queue_execution_repository=self.qe_repo,
            production_job_repository=self.prod_repo,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _wo_with_internal_job(self, n=2):
        """A WO with n parts, all assigned to one Internal job (queued)."""
        wo = self.wo_repo.create_work_order(
            "Cust",
            [{"part_name": "P", "material": "PLA", "quantity": n}],
        )
        wo_id = wo["wo_id"]
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        qids = [
            r["queue_id"] for r in conn.execute(
                "SELECT queue_id FROM queue_items WHERE wo_id=? "
                "ORDER BY queue_id", (wo_id,)
            ).fetchall()
        ]
        conn.close()
        job = self.svc.create_job(wo_id, queue_ids=qids, job_type="Internal")
        return wo_id, job["job_id"], qids

    def _new_print_job(self):
        """Insert a production print_jobs row, return its job_id."""
        conn = self.prod_repo._get_connection()
        cur = conn.execute(
            "INSERT INTO print_jobs (printer_id, printer_name, status, "
            "started_at, created_at) VALUES (?,?,?,?,?)",
            ("p1", "Printer 1", "completed",
             "2026-05-20T12:00:00", "2026-05-20T12:00:00"),
        )
        conn.commit()
        pjid = cur.lastrowid
        conn.close()
        return pjid

    def _complete_part(self, queue_id, outcome=None):
        """Mark a part completed, link a print_job, set its QC outcome.

        Returns the print_job_id linked to the part.
        """
        pjid = self._new_print_job()
        conn = self.job_repo._get_conn()
        conn.execute(
            "UPDATE queue_items SET status='completed', print_job_id=? "
            "WHERE queue_id=?",
            (pjid, queue_id),
        )
        conn.commit()
        conn.close()
        if outcome is not None:
            self.prod_repo.update_job_qc(pjid, outcome=outcome)
        return pjid

    def _set_status(self, queue_id, status):
        conn = self.job_repo._get_conn()
        conn.execute(
            "UPDATE queue_items SET status=? WHERE queue_id=?",
            (status, queue_id),
        )
        conn.commit()
        conn.close()

    def _reroll(self, wo_id, job_id):
        """Re-roll job + WO from current queue_items (no gate change),
        mirroring what the print-completion handler does."""
        conn = self.job_repo._get_conn()
        try:
            status_sync.sync_job_status(conn, job_id)
            status_sync.sync_work_order_status(conn, wo_id)
            conn.commit()
        finally:
            conn.close()

    def _job(self, job_id):
        return self.job_repo.get_job(job_id)

    def _wo_status(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM work_orders WHERE wo_id=?", (wo_id,)
        ).fetchone()
        conn.close()
        return row["status"] if row else None


class PartDrivenGateContractTests(_PropagationSetup):

    def test_all_parts_pass_completes_job_and_wo(self):
        # CONTRACT: every completed part passes QC → gate 'pass', job
        # 'completed', WO 'completed' (reachable Ready to Ship / Mark
        # Delivered). This is the core fix.
        wo_id, job_id, qids = self._wo_with_internal_job(n=2)
        self._complete_part(qids[0], "pass")
        self._complete_part(qids[1], "pass")

        result = self.svc.propagate_part_qc(self._part_pjid(qids[1]))

        self.assertEqual(result["inspection_outcome"], "pass")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self._job(job_id)["inspection_outcome"], "pass")
        self.assertEqual(self._job(job_id)["status"], "completed")
        self.assertEqual(self._wo_status(wo_id), "completed")

    def test_one_part_fail_fails_gate_and_blocks_wo(self):
        # CONTRACT: any part QC='fail' → gate 'fail' and the WO does NOT
        # reach 'completed'.
        wo_id, job_id, qids = self._wo_with_internal_job(n=2)
        self._complete_part(qids[0], "pass")
        self._complete_part(qids[1], "fail")

        self.svc.propagate_part_qc(self._part_pjid(qids[1]))

        self.assertEqual(self._job(job_id)["inspection_outcome"], "fail")
        self.assertEqual(self._job(job_id)["status"], "attention")
        self.assertNotEqual(self._wo_status(wo_id), "completed")
        self.assertEqual(self._wo_status(wo_id), "attention")

    def test_uninspected_completed_part_leaves_gate_pending(self):
        # CONTRACT: a part still pending QC (completed, outcome unknown)
        # leaves the gate 'pending' and the WO not 'completed'.
        wo_id, job_id, qids = self._wo_with_internal_job(n=2)
        self._complete_part(qids[0], "pass")
        self._complete_part(qids[1], None)  # completed, QC still unknown

        result = self.svc.propagate_part_qc(self._part_pjid(qids[0]))

        self.assertIsNone(result)  # gate unchanged → no re-roll
        self.assertEqual(self._job(job_id)["inspection_outcome"], "pending")
        self.assertNotEqual(self._wo_status(wo_id), "completed")

    def test_unprinted_part_leaves_gate_pending(self):
        # CONTRACT: a part still printing means the job isn't done; the
        # gate must NOT read 'pass' off the already-inspected parts (no
        # stale pass). WO stays out of 'completed'.
        wo_id, job_id, qids = self._wo_with_internal_job(n=2)
        self._complete_part(qids[0], "pass")
        self._set_status(qids[1], "printing")

        result = self.svc.propagate_part_qc(self._part_pjid(qids[0]))

        self.assertIsNone(result)
        self.assertEqual(self._job(job_id)["inspection_outcome"], "pending")
        self.assertNotEqual(self._wo_status(wo_id), "completed")

    def test_cancelled_part_does_not_block_pass(self):
        # CONTRACT: a cancelled part is excluded from the gate (it's out
        # of the queue rollup too), so a job whose remaining parts all
        # pass still completes.
        wo_id, job_id, qids = self._wo_with_internal_job(n=2)
        self._complete_part(qids[0], "pass")
        self._set_status(qids[1], "cancelled")

        result = self.svc.propagate_part_qc(self._part_pjid(qids[0]))

        self.assertEqual(result["inspection_outcome"], "pass")
        self.assertEqual(self._wo_status(wo_id), "completed")

    # WITNESS helper: read back the print_job_id a part is linked to.
    def _part_pjid(self, queue_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT print_job_id FROM queue_items WHERE queue_id=?",
            (queue_id,),
        ).fetchone()
        conn.close()
        return row["print_job_id"]


class PartDrivenGateWitnessTests(_PropagationSetup):

    def test_no_change_returns_none(self):
        # WITNESS: propagate_part_qc returns None (no re-roll) when the
        # recomputed gate equals the stored one. A correct fix may change
        # this witness without breaking the contract.
        _wo_id, _job_id, qids = self._wo_with_internal_job(n=1)
        self._complete_part(qids[0], "pass")
        first = self.svc.propagate_part_qc(self._part_pjid(qids[0]))
        self.assertIsNotNone(first)            # pending → pass
        second = self.svc.propagate_part_qc(self._part_pjid(qids[0]))
        self.assertIsNone(second)              # pass → pass, no change

    def test_unlinked_print_job_is_a_noop(self):
        # WITNESS: a print_job not linked to any queue_item (no parent
        # job) propagates to nothing.
        self.assertIsNone(self.svc.propagate_part_qc(987654))

    def _part_pjid(self, queue_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT print_job_id FROM queue_items WHERE queue_id=?",
            (queue_id,),
        ).fetchone()
        conn.close()
        return row["print_job_id"]


class PatchRoutePropagationWitnessTests(_PropagationSetup):
    """WITNESS: the PATCH /api/production/jobs/<id> route — the single
    chokepoint every per-part QC entry point funnels through — fires the
    propagation end to end."""

    def setUp(self):
        super().setUp()
        prod_service = ProductionService(self.prod_repo, None, None)
        app = Flask(__name__)
        register_production_routes(
            app, prod_service, None, None, work_order_service=self.svc,
        )
        self.client = app.test_client()

    def test_patch_qc_pass_advances_wo_to_completed(self):
        wo_id, job_id, qids = self._wo_with_internal_job(n=1)
        # Part done printing, awaiting QC: WO held at in_progress.
        pjid = self._complete_part(qids[0], None)
        self._reroll(wo_id, job_id)
        self.assertEqual(self._job(job_id)["status"], "in_progress")
        self.assertEqual(self._wo_status(wo_id), "in_progress")

        resp = self.client.patch(
            "/api/production/jobs/{}".format(pjid),
            json={"outcome": "pass", "operator": "QC"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("success"))
        # The per-part QC write propagated through to the gate + WO.
        self.assertEqual(self._job(job_id)["inspection_outcome"], "pass")
        self.assertEqual(self._job(job_id)["status"], "completed")
        self.assertEqual(self._wo_status(wo_id), "completed")


if __name__ == "__main__":
    unittest.main()
