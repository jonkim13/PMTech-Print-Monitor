"""Phase E1 — quality service + repository: NCR/CA lifecycle.

Exercises QualityService validation and the CA state machine on top of
QualityRepository, against tempdir work_orders.db + quality.db. NCR↔job
existence is checked across the two DB files at the service layer (no
SQL join).
"""

import os
import shutil
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.quality.repository import QualityRepository
from app.domains.quality.service import (
    QualityService,
    QualityStateError,
    QualityValidationError,
)
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService


class _QualitySetup(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.wo_db = os.path.join(self.tmp, "wo.db")
        self.q_db = os.path.join(self.tmp, "quality.db")
        self.wo_repo = WorkOrderRepository(self.wo_db)
        self.job_repo = JobRepository(self.wo_db)
        # queue_items / queue_jobs schema used by create_work_order + sync.
        QueueRepository(self.wo_db)
        QueueExecutionRepository(self.wo_db)
        self.q_repo = QualityRepository(self.q_db)
        self.svc = QualityService(
            quality_repository=self.q_repo,
            job_repository=self.job_repo,
            work_order_repository=self.wo_repo,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _make_job(self, job_type="Internal"):
        wo = self.wo_repo.create_work_order("Cust", [])
        job = self.job_repo.create_job(wo["wo_id"], job_type=job_type)
        return wo["wo_id"], job["job_id"]

    def _ncr(self, corrective_action_needed="N", **over):
        wo_id, job_id = self._make_job()
        kwargs = dict(
            job_id=job_id, wo_id=wo_id, description="surface defect",
            reported_by="QC", corrective_action_needed=corrective_action_needed,
        )
        kwargs.update(over)
        return self.svc.create_ncr(**kwargs)


class NcrLifecycleTests(_QualitySetup):

    def test_create_ncr_persists_all_fields(self):
        wo_id, job_id = self._make_job()
        ncr = self.svc.create_ncr(
            job_id=job_id, wo_id=wo_id, description="surface defect",
            reported_by="QC", affected_parts="2 of 5",
            remedial_action="rework", corrective_action_needed="Y",
        )
        self.assertEqual(ncr["status"], "open")
        self.assertIsNotNone(ncr["created_at"])
        self.assertIsNone(ncr["closed_at"])
        self.assertEqual(ncr["job_id"], job_id)
        self.assertEqual(ncr["wo_id"], wo_id)
        self.assertEqual(ncr["description"], "surface defect")
        self.assertEqual(ncr["reported_by"], "QC")
        self.assertEqual(ncr["affected_parts"], "2 of 5")
        self.assertEqual(ncr["remedial_action"], "rework")
        self.assertEqual(ncr["corrective_action_needed"], "Y")

    def test_count_open_ncrs_tracks_open_then_close(self):
        ncr = self._ncr()
        wo_id = ncr["wo_id"]
        self.assertEqual(self.q_repo.count_open_ncrs_for_wo(wo_id), 1)
        self.svc.close_ncr(ncr["ncr_id"])
        self.assertEqual(self.q_repo.count_open_ncrs_for_wo(wo_id), 0)

    def test_close_ncr_with_no_cas_succeeds(self):
        ncr = self._ncr(corrective_action_needed="N")
        closed = self.svc.close_ncr(ncr["ncr_id"])
        self.assertEqual(closed["status"], "closed")
        self.assertIsNotNone(closed["closed_at"])

    def test_close_ncr_with_verified_ca_succeeds(self):
        ncr = self._ncr(corrective_action_needed="Y")
        ca = self.svc.create_ca(ncr["ncr_id"], root_cause_actions="new jig")
        self.svc.verify_ca(ca["ca_id"], verifying_person="Insp")
        closed = self.svc.close_ncr(ncr["ncr_id"])
        self.assertEqual(closed["status"], "closed")
        self.assertIsNotNone(closed["closed_at"])


class CorrectiveActionTests(_QualitySetup):

    def test_create_ca_for_needed_ncr_succeeds(self):
        ncr = self._ncr(corrective_action_needed="Y")
        ca = self.svc.create_ca(
            ncr["ncr_id"], root_cause_actions="recalibrate fixture",
            responsible_persons="Lead", resources_needed="fixture stock",
        )
        self.assertEqual(ca["status"], "open")
        self.assertEqual(ca["ncr_id"], ncr["ncr_id"])
        self.assertEqual(ca["root_cause_actions"], "recalibrate fixture")
        self.assertEqual(ca["responsible_persons"], "Lead")

    def test_ca_full_status_chain(self):
        """open → in_progress → verified → closed, each step adjacent."""
        ncr = self._ncr(corrective_action_needed="Y")
        ca = self.svc.create_ca(ncr["ncr_id"], root_cause_actions="x")
        self.assertEqual(ca["status"], "open")

        r1 = self.svc.set_ca_status(ca["ca_id"], "in_progress")
        self.assertEqual(r1["status"], "in_progress")

        r2 = self.svc.verify_ca(ca["ca_id"], verifying_person="Insp")
        self.assertEqual(r2["status"], "verified")
        self.assertEqual(r2["verifying_person"], "Insp")

        r3 = self.svc.set_ca_status(ca["ca_id"], "closed")
        self.assertEqual(r3["status"], "closed")
        self.assertIsNotNone(r3["closed_at"])

    def test_verify_ca_directly_from_open(self):
        ncr = self._ncr(corrective_action_needed="Y")
        ca = self.svc.create_ca(ncr["ncr_id"], root_cause_actions="x")
        verified = self.svc.verify_ca(ca["ca_id"], verifying_person="Insp")
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["verifying_person"], "Insp")


class NcrValidationTests(_QualitySetup):

    def test_blank_description_rejected(self):
        wo_id, job_id = self._make_job()
        with self.assertRaises(QualityValidationError):
            self.svc.create_ncr(
                job_id=job_id, wo_id=wo_id, description="  ",
                reported_by="QC",
            )

    def test_blank_reported_by_rejected(self):
        wo_id, job_id = self._make_job()
        with self.assertRaises(QualityValidationError):
            self.svc.create_ncr(
                job_id=job_id, wo_id=wo_id, description="defect",
                reported_by="",
            )

    def test_invalid_corrective_action_needed_rejected(self):
        wo_id, job_id = self._make_job()
        with self.assertRaises(QualityValidationError):
            self.svc.create_ncr(
                job_id=job_id, wo_id=wo_id, description="defect",
                reported_by="QC", corrective_action_needed="maybe",
            )

    def test_nonexistent_job_rejected(self):
        with self.assertRaises(LookupError):
            self.svc.create_ncr(
                job_id=999999, wo_id="WO-001", description="defect",
                reported_by="QC",
            )

    def test_get_missing_ncr_raises_lookup(self):
        with self.assertRaises(LookupError):
            self.svc.get_ncr(424242)


class CorrectiveActionValidationTests(_QualitySetup):

    def test_create_ca_when_not_needed_is_state_error(self):
        ncr = self._ncr(corrective_action_needed="N")
        with self.assertRaises(QualityStateError):
            self.svc.create_ca(ncr["ncr_id"], root_cause_actions="x")

    def test_create_ca_blank_root_cause_rejected(self):
        ncr = self._ncr(corrective_action_needed="Y")
        with self.assertRaises(QualityValidationError):
            self.svc.create_ca(ncr["ncr_id"], root_cause_actions="   ")

    def test_close_ncr_with_unverified_ca_is_state_error(self):
        ncr = self._ncr(corrective_action_needed="Y")
        self.svc.create_ca(ncr["ncr_id"], root_cause_actions="x")  # open
        with self.assertRaises(QualityStateError):
            self.svc.close_ncr(ncr["ncr_id"])

    def test_illegal_ca_transition_open_to_closed_is_state_error(self):
        ncr = self._ncr(corrective_action_needed="Y")
        ca = self.svc.create_ca(ncr["ncr_id"], root_cause_actions="x")
        with self.assertRaises(QualityStateError):
            self.svc.set_ca_status(ca["ca_id"], "closed")

    def test_verify_ca_blank_person_rejected(self):
        ncr = self._ncr(corrective_action_needed="Y")
        ca = self.svc.create_ca(ncr["ncr_id"], root_cause_actions="x")
        with self.assertRaises(QualityValidationError):
            self.svc.verify_ca(ca["ca_id"], verifying_person="")

    def test_verify_missing_ca_raises_lookup(self):
        with self.assertRaises(LookupError):
            self.svc.verify_ca(987654, verifying_person="Insp")


class WoDetailNcrSummaryTests(_QualitySetup):
    """The WO-detail payload carries an ncr_summary (the data E2 renders)."""

    def setUp(self):
        super().setUp()
        self.wo_svc = WorkOrderService(
            work_order_repository=self.wo_repo,
            job_repository=self.job_repo,
            quality_repository=self.q_repo,
        )

    def test_get_work_order_includes_ncr_summary(self):
        ncr = self._ncr(corrective_action_needed="Y")
        wo_id = ncr["wo_id"]
        wo = self.wo_svc.get_work_order(wo_id)
        self.assertIn("ncr_summary", wo)
        summary = wo["ncr_summary"]
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["ncrs"][0]["ncr_id"], ncr["ncr_id"])
        self.assertEqual(summary["ncrs"][0]["job_id"], ncr["job_id"])
        self.assertEqual(summary["ncrs"][0]["status"], "open")
        self.assertEqual(
            summary["ncrs"][0]["corrective_action_needed"], "Y"
        )

    def test_ncr_summary_open_count_drops_after_close(self):
        ncr = self._ncr(corrective_action_needed="N")
        wo_id = ncr["wo_id"]
        self.svc.close_ncr(ncr["ncr_id"])
        summary = self.wo_svc.get_work_order(wo_id)["ncr_summary"]
        self.assertEqual(summary["open_count"], 0)
        self.assertEqual(summary["total"], 1)


if __name__ == "__main__":
    unittest.main()
