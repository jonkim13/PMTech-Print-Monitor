"""Phase D — pure derivation: the inspection gate + WO rollup.

These tests exercise the two ``status_sync`` siblings with no DB:

- ``derive_job_status_combined(queue_statuses, job_type, outcome)``
  layers the inspector pass/fail gate on top of the base queue-only
  deriver. The gate only fires when the base deriver lands on
  'completed'; otherwise the base status passes through untouched.
  Design jobs always skip the gate. No new job-status enum values are
  introduced — pass→completed, fail→attention, pending→in_progress.

- ``derive_work_order_status_combined(queue_statuses, job_statuses)``
  projects job statuses into the queue-item vocabulary and rolls the
  combined pool up. Phase D feeds Internal job statuses into this pool
  too, so the gate is visible above the job level.
"""

import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.work_orders.status_sync import (
    derive_job_status,
    derive_job_status_combined,
    derive_work_order_status_combined,
)


class JobStatusGateTests(unittest.TestCase):
    """The pass/fail/pending gate on derive_job_status_combined."""

    def test_internal_queue_complete_pending_holds_at_in_progress(self):
        self.assertEqual(
            derive_job_status_combined(["completed"], "Internal", "pending"),
            "in_progress",
        )

    def test_internal_queue_complete_pass_completes(self):
        self.assertEqual(
            derive_job_status_combined(["completed"], "Internal", "pass"),
            "completed",
        )

    def test_internal_queue_complete_fail_goes_to_attention(self):
        self.assertEqual(
            derive_job_status_combined(["completed"], "Internal", "fail"),
            "attention",
        )

    def test_external_complete_pending_holds_at_in_progress(self):
        # External jobs pass their stored status as a single-element list.
        self.assertEqual(
            derive_job_status_combined(["completed"], "External", "pending"),
            "in_progress",
        )

    def test_external_complete_pass_completes(self):
        self.assertEqual(
            derive_job_status_combined(["completed"], "External", "pass"),
            "completed",
        )

    def test_external_complete_fail_goes_to_attention(self):
        self.assertEqual(
            derive_job_status_combined(["completed"], "External", "fail"),
            "attention",
        )

    def test_design_skips_the_gate_even_when_complete(self):
        # Design jobs never go through QC — the gate is bypassed and the
        # base queue-only status stands regardless of outcome.
        self.assertEqual(
            derive_job_status_combined(["completed"], "Design", "pending"),
            "completed",
        )
        self.assertEqual(
            derive_job_status_combined(["completed"], "Design", "fail"),
            "completed",
        )

    def test_gate_only_applies_when_base_is_completed(self):
        # A still-printing job is 'in_progress' from the base deriver; the
        # gate must not touch it even with a recorded outcome.
        for outcome in ("pending", "pass", "fail"):
            self.assertEqual(
                derive_job_status_combined(
                    ["printing", "completed"], "Internal", outcome
                ),
                "in_progress",
            )

    def test_failure_base_passes_through_gate_untouched(self):
        self.assertEqual(
            derive_job_status_combined(["failed"], "Internal", "pass"),
            "attention",
        )

    def test_open_base_passes_through_gate_untouched(self):
        self.assertEqual(
            derive_job_status_combined(["queued"], "Internal", "fail"),
            "open",
        )

    def test_no_gate_case_equals_base_deriver(self):
        """Safety property: when the base deriver is NOT 'completed', the
        combined deriver is byte-identical to derive_job_status for
        Internal/External — proving the gate is a no-op off the
        completed branch."""
        samples = [
            ["queued"],
            ["printing", "queued"],
            ["failed", "queued"],
            ["cancelled"],
            [],
        ]
        for statuses in samples:
            base = derive_job_status(statuses)
            for job_type in ("Internal", "External"):
                self.assertEqual(
                    derive_job_status_combined(statuses, job_type, "pending"),
                    base,
                    "combined should equal base off the completed branch "
                    "for {!r}/{}".format(statuses, job_type),
                )


class WorkOrderRollupTests(unittest.TestCase):
    """derive_work_order_status_combined projection + gate visibility."""

    def test_gated_internal_job_keeps_wo_in_progress(self):
        # Queue side complete, but the job is held at in_progress by the
        # gate — the WO must reflect that pending state, not 'completed'.
        self.assertEqual(
            derive_work_order_status_combined(["completed"], ["in_progress"]),
            "in_progress",
        )

    def test_passed_job_lets_wo_complete(self):
        self.assertEqual(
            derive_work_order_status_combined(["completed"], ["completed"]),
            "completed",
        )

    def test_failed_inspection_surfaces_attention_on_wo(self):
        self.assertEqual(
            derive_work_order_status_combined(["completed"], ["attention"]),
            "attention",
        )

    def test_external_only_wo_completes_from_job_status(self):
        # No queue_items (External has none); the completed job status
        # alone rolls the WO up.
        self.assertEqual(
            derive_work_order_status_combined([], ["completed"]),
            "completed",
        )

    def test_redundant_consistent_job_status_is_idempotent(self):
        # Phase D safety property: a NON-gated job's status equals
        # derive_job_status(its queue_items). Adding that redundant
        # summary to the pool must not change the rollup. Here both the
        # queue_item and the (ungated) job report 'completed'.
        with_job = derive_work_order_status_combined(
            ["completed"], ["completed"]
        )
        without_job = derive_work_order_status_combined(["completed"], [])
        self.assertEqual(with_job, without_job)
        self.assertEqual(with_job, "completed")

    def test_open_job_projects_to_queued(self):
        # An untouched External/Design job ('open') projects to 'queued',
        # leaving the WO 'open' when nothing else is in flight.
        self.assertEqual(
            derive_work_order_status_combined([], ["open"]),
            "open",
        )

    def test_cancelled_everything_rolls_to_cancelled(self):
        self.assertEqual(
            derive_work_order_status_combined(["cancelled"], ["cancelled"]),
            "cancelled",
        )


if __name__ == "__main__":
    unittest.main()
