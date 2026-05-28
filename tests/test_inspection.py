"""Phase D — inspection gate: service + repository behaviour.

Covers ``WorkOrderService.record_inspection`` and the underlying
``JobRepository.record_inspection`` write. The inspection gate holds
Internal and External jobs at ``in_progress`` while the recorded
outcome is ``pending``; a ``pass`` completes the job (and rolls the WO
up to ``completed``), a ``fail`` drops both to ``attention``. Design
jobs are rejected — they skip inspection per Philip's process diagram.

The headline contract test is
``test_external_in_progress_pass_completes_job_and_wo``: an External
job in ``in_progress`` plus a passing inspection completes the job AND
the work order in the one ``record_inspection`` transaction.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.work_orders import status_sync
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService


class _InspectionSetup(unittest.TestCase):
    """Shared tempdir DB + minimal service for every test here.

    ``record_inspection`` only touches the work-order and job repos, so
    a service wired with just those two is enough to exercise the gate
    end to end (write + sync_job_status + sync_work_order_status).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "wo.db")
        self.wo_repo = WorkOrderRepository(self.db)
        self.job_repo = JobRepository(self.db)
        # These two own the queue_items / queue_jobs schema that
        # create_work_order + the rollup helpers rely on.
        self.q_repo = QueueRepository(self.db)
        self.qe_repo = QueueExecutionRepository(self.db)
        self.svc = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _first_qid(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT queue_id FROM queue_items WHERE wo_id=? "
            "ORDER BY queue_id LIMIT 1",
            (wo_id,),
        ).fetchone()
        conn.close()
        return row["queue_id"] if row else None

    def _wo_status(self, wo_id):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM work_orders WHERE wo_id=?", (wo_id,)
        ).fetchone()
        conn.close()
        return row["status"] if row else None

    def _make_internal_queue_complete_job(self):
        """WO + Internal job whose only queue_item is completed.

        Leaves the job held at ``in_progress`` by the inspection gate
        (queue rollup says 'completed', outcome is still 'pending').
        Returns (wo_id, job_id).
        """
        wo = self.wo_repo.create_work_order(
            "Cust", [{"part_name": "P", "material": "PLA", "quantity": 1}]
        )
        wo_id = wo["wo_id"]
        qid = self._first_qid(wo_id)
        job = self.svc.create_job(
            wo_id, queue_ids=[qid], job_type="Internal"
        )
        job_id = job["job_id"]
        # Drive the queue_item to 'completed' and re-roll both layers,
        # mirroring what the completion handler does in production.
        conn = self.job_repo._get_conn()
        try:
            conn.execute(
                "UPDATE queue_items SET status='completed' WHERE job_id=?",
                (job_id,),
            )
            status_sync.sync_job_status(conn, job_id)
            status_sync.sync_work_order_status(conn, wo_id)
            conn.commit()
        finally:
            conn.close()
        return wo_id, job_id

    def _make_external_in_progress_job(self):
        """WO (no queue_items) + External job in 'in_progress'.

        Returns (wo_id, job_id).
        """
        wo = self.wo_repo.create_work_order("Cust", [])
        wo_id = wo["wo_id"]
        job = self.svc.create_job(
            wo_id, job_type="External",
            vendor="Acme", external_process="Anodize",
        )
        job_id = job["job_id"]
        self.svc.start_non_internal_job(job_id)
        return wo_id, job_id


class ExternalInspectionContractTests(_InspectionSetup):

    def test_external_in_progress_pass_completes_job_and_wo(self):
        """Contract: External in_progress + pass → job AND WO completed
        in one record_inspection transaction.

        This pins the repurposed External "Complete" flow: the UI
        submits while the job is still 'in_progress', record_inspection
        nudges it to 'completed', the gate keeps it 'completed' on a
        pass, and the WO rollup — run in the same transaction — reaches
        'completed' because the External job is its only work.
        """
        wo_id, job_id = self._make_external_in_progress_job()
        self.assertEqual(self.job_repo.get_job(job_id)["status"],
                         "in_progress")

        result = self.svc.record_inspection(
            job_id, outcome="pass", inspector="QC"
        )

        # Returned row reflects the gate outcome.
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["inspection_outcome"], "pass")
        self.assertEqual(result["inspector"], "QC")
        # Persisted job status + WO rollup both committed together.
        self.assertEqual(self.job_repo.get_job(job_id)["status"],
                         "completed")
        self.assertEqual(self._wo_status(wo_id), "completed")

    def test_external_in_progress_fail_sets_attention(self):
        """External in_progress + fail → job and WO both 'attention'."""
        wo_id, job_id = self._make_external_in_progress_job()

        result = self.svc.record_inspection(
            job_id, outcome="fail", inspector="QC"
        )

        self.assertEqual(result["status"], "attention")
        self.assertEqual(result["inspection_outcome"], "fail")
        self.assertEqual(self._wo_status(wo_id), "attention")


class InternalInspectionGateTests(_InspectionSetup):

    def test_internal_queue_complete_is_held_at_in_progress(self):
        """Witness: a queue-complete Internal job awaits QC at
        'in_progress' before any inspection is recorded."""
        wo_id, job_id = self._make_internal_queue_complete_job()
        self.assertEqual(self.job_repo.get_job(job_id)["status"],
                         "in_progress")
        self.assertEqual(self._wo_status(wo_id), "in_progress")

    def test_internal_pass_completes_job_and_wo(self):
        wo_id, job_id = self._make_internal_queue_complete_job()

        result = self.svc.record_inspection(
            job_id, outcome="pass", inspector="QC"
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["inspection_outcome"], "pass")
        self.assertEqual(self.job_repo.get_job(job_id)["status"],
                         "completed")
        self.assertEqual(self._wo_status(wo_id), "completed")

    def test_internal_fail_sets_attention(self):
        wo_id, job_id = self._make_internal_queue_complete_job()

        result = self.svc.record_inspection(
            job_id, outcome="fail", inspector="QC"
        )

        self.assertEqual(result["status"], "attention")
        self.assertEqual(self._wo_status(wo_id), "attention")


class InspectionValidationTests(_InspectionSetup):

    def test_design_inspection_is_rejected(self):
        """Design jobs skip inspection — record_inspection raises."""
        wo = self.wo_repo.create_work_order("Cust", [])
        job = self.svc.create_job(
            wo["wo_id"], job_type="Design", designer="DZ"
        )
        with self.assertRaises(ValueError):
            self.svc.record_inspection(
                job["job_id"], outcome="pass", inspector="QC"
            )

    def test_invalid_outcome_is_rejected(self):
        _wo_id, job_id = self._make_external_in_progress_job()
        with self.assertRaises(ValueError):
            self.svc.record_inspection(
                job_id, outcome="maybe", inspector="QC"
            )

    def test_pending_is_not_a_recordable_outcome(self):
        _wo_id, job_id = self._make_external_in_progress_job()
        with self.assertRaises(ValueError):
            self.svc.record_inspection(
                job_id, outcome="pending", inspector="QC"
            )

    def test_blank_inspector_is_rejected(self):
        _wo_id, job_id = self._make_external_in_progress_job()
        with self.assertRaises(ValueError):
            self.svc.record_inspection(
                job_id, outcome="pass", inspector="   "
            )

    def test_missing_job_raises_lookup_error(self):
        with self.assertRaises(LookupError):
            self.svc.record_inspection(
                999999, outcome="pass", inspector="QC"
            )


class InspectionPersistenceTests(_InspectionSetup):

    def test_report_and_date_are_persisted(self):
        _wo_id, job_id = self._make_external_in_progress_job()
        result = self.svc.record_inspection(
            job_id, outcome="pass", inspector="QC",
            report="Looks good", date="2026-05-01",
        )
        self.assertEqual(result["inspection_report"], "Looks good")
        self.assertEqual(result["inspection_date"], "2026-05-01")

    def test_date_defaults_to_today_utc(self):
        _wo_id, job_id = self._make_external_in_progress_job()
        today = datetime.now(timezone.utc).date().isoformat()
        result = self.svc.record_inspection(
            job_id, outcome="pass", inspector="QC"
        )
        self.assertEqual(result["inspection_date"], today)

    def test_repository_writes_all_four_inspection_columns(self):
        """Repo-level write is type-agnostic — it persists whatever the
        service validated. Status derivation is the service's job."""
        wo = self.wo_repo.create_work_order("Cust", [])
        job = self.svc.create_job(
            wo["wo_id"], job_type="External",
            vendor="Acme", external_process="Anodize",
        )
        job_id = job["job_id"]
        row = self.job_repo.record_inspection(
            job_id, outcome="fail", inspector="Inez",
            report="2 dims out of tol", date="2026-04-15",
        )
        self.assertEqual(row["inspection_outcome"], "fail")
        self.assertEqual(row["inspector"], "Inez")
        self.assertEqual(row["inspection_report"], "2 dims out of tol")
        self.assertEqual(row["inspection_date"], "2026-04-15")


if __name__ == "__main__":
    unittest.main()
