"""Status propagation bug — failing-test pins.

This file pins the specific predicate-gating sites that prevent the
queue-side rollup from advancing when the queue_job never reaches
'printing' status before completion. While these tests are red, the
audit's hypothesis (in `docs/audit/02-printer-monitoring.md` and
`docs/audit/05-domain-work-orders.md`) is confirmed at the DB level.

Predicates being pinned (file:line):
- queue_handler.py:294-305  `_matches_printing_queue_job` requires
  `queue_job.status == 'printing'`.
- execution_lifecycle.py:67-79  `get_active_queue_job_for_printer`
  queries `WHERE status='printing'`.
- execution_lifecycle.py:81-103  `find_printing_queue_job_by_filename`
  queries `WHERE status='printing'`.
- queue/repository.py:583-605  `find_printing_item_by_filename`
  queries `WHERE status='printing'`.
- execution_lifecycle.py:265-271  `complete_queue_job` queue_items
  UPDATE filters `WHERE status='printing'` — asymmetric vs the
  permissive `_set_queue_job_status` UPDATE at lines 36-41 which uses
  `WHERE status NOT IN ('completed', 'cancelled')`.

When the queue_job is stuck in 'starting' or 'uploading' at completion
time (because `link_print_job_on_start` couldn't find it via any of
the four `_find_queue_job_on_start` fallbacks), every search above
returns nothing and `complete_queue_job`'s queue_items UPDATE matches
zero rows. The production-side `print_jobs.status` still advances to
'completed' correctly because it doesn't depend on queue lookup.

Do not modify production code to make these pass — write the fix in a
separate task. These tests document the bug; the green/red transition
on the fix is the proof.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.monitoring.runtime_state import MonitoringRuntimeState
from app.domains.monitoring.transition_handler import TransitionHandler
from app.domains.production.job_repository import PrintJobRepository
from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository


class _FakeClient:
    """Minimal printer-client stub.

    Real `PrusaLinkClient` is HTTP-bound and irrelevant to the rollup
    chain. The completion handler needs only `get_job_details()`,
    `get_camera_snapshot()`, and `model`.
    """

    model = "core_one"

    def get_job_details(self):
        return {}

    def get_camera_snapshot(self):
        return None


class _FakeMachineRepo:
    """No-op machine log — completion handler logs an event we don't care about."""

    def log_machine_event(self, *args, **kwargs):
        return None


class _BugSetup(unittest.TestCase):
    """Shared setup for every test in this module."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.wo_db_path = os.path.join(self.tmp, "wo.db")
        self.prod_db_path = os.path.join(self.tmp, "prod.db")

        self.wo_repo = WorkOrderRepository(self.wo_db_path)
        self.job_repo = JobRepository(self.wo_db_path)
        self.q_repo = QueueRepository(self.wo_db_path)
        self.qe_repo = QueueExecutionRepository(self.wo_db_path)
        self.prod_repo = PrintJobRepository(self.prod_db_path)

        self.runtime = MonitoringRuntimeState()
        # Passing filament_db / assignment_db / material_repository as
        # None makes FilamentHandler.auto_deduct_filament a no-op and
        # ProductionMaterialUsage.log_rows a no-op — both are off the
        # critical path for the queue-rollup bug we're pinning.
        self.handler = TransitionHandler(
            history_db=None,
            filament_db=None,
            assignment_db=None,
            upload_session_db=None,
            event_service=None,
            runtime_state=self.runtime,
            state_lock=threading.Lock(),
            snapshots_dir=None,
            job_repository=self.prod_repo,
            machine_repository=_FakeMachineRepo(),
            material_repository=None,
            queue_repository=self.q_repo,
            queue_execution_repository=self.qe_repo,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _qid(self, wo_id):
        conn = sqlite3.connect(self.wo_db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT queue_id FROM queue_items WHERE wo_id=? "
            "ORDER BY queue_id LIMIT 1",
            (wo_id,),
        ).fetchone()
        conn.close()
        return row["queue_id"] if row else None

    def _row(self, table, key, value):
        conn = sqlite3.connect(self.wo_db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM {} WHERE {} = ?".format(table, key),
            (value,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def _prod_row(self, job_id):
        return self.prod_repo.get_job(job_id)

    def _setup_wo_with_queue_job(self, status_target,
                                 gcode_filename="part.gcode"):
        """Create a WO+queue_job and drive the queue_job to ``status_target``.

        status_target ∈ {'uploading', 'uploaded', 'starting', 'printing'}.
        Returns (wo_id, queue_id, queue_job_id, prod_job_id).
        """
        wo = self.wo_repo.create_work_order(
            "Cust",
            [{"part_name": "P", "material": "PLA", "quantity": 1}],
        )
        wo_id = wo["wo_id"]
        qid = self._qid(wo_id)

        res = self.qe_repo.start_queue_job_execution(
            [qid], "printer-1", "Printer 1", gcode_filename,
            operator_initials="TS",
        )
        qjid = res["queue_job_id"]
        # start_queue_job_execution already left queue_job in 'uploading'.
        if status_target in ("uploaded", "starting", "printing"):
            self.qe_repo.mark_queue_job_uploaded(qjid)
        if status_target in ("starting", "printing"):
            self.qe_repo.mark_queue_job_starting(qjid)
        if status_target == "printing":
            self.qe_repo.mark_queue_job_printing(qjid)

        prod_job_id = self.prod_repo.create_job(
            printer_id="printer-1",
            printer_name="Printer 1",
            file_name=gcode_filename,
        )
        # The monitoring runtime is what queue_handler.complete reads to
        # locate the cached queue_job. Populate it as if a successful
        # start handler had run.
        self.runtime.active_job_ids["printer-1"] = prod_job_id
        self.runtime.active_queue_job_ids["printer-1"] = qjid
        return wo_id, qid, qjid, prod_job_id

    def _drive_handle_complete(self, gcode_filename="part.gcode"):
        """Invoke the same transition entry point the poller would call.

        Note: real events are built by `transition_detector.build_transition_event`
        which always sets `duration_sec=0`. `handle_print_completed` reads
        it before passing into the production handler, so we include the
        same key here.
        """
        self.handler.handle_print_completed(
            "printer-1", "Printer 1",
            {"name": "Printer 1", "job": {"filename": gcode_filename}},
            client=_FakeClient(),
            event={
                "printer_id": "printer-1",
                "filename": gcode_filename,
                "timestamp": "2026-05-15T00:00:00+00:00",
                "duration_sec": 0,
            },
        )


class StatusPropagationBugTests(_BugSetup):

    def test_completion_with_queue_job_stuck_in_starting(self):
        """queue_job stuck in 'starting' at completion — rollup should fire.

        Today, every search predicate in `queue_handler.complete` filters
        on `status='printing'`, so the completion silently no-ops on the
        queue side while `print_jobs.status` advances correctly.
        """
        wo_id, qid, qjid, prod_job_id = self._setup_wo_with_queue_job(
            "starting"
        )

        self._drive_handle_complete()

        prod = self._prod_row(prod_job_id)
        qi = self._row("queue_items", "queue_id", qid)
        qj = self._row("queue_jobs", "queue_job_id", qjid)
        job = self._row("jobs", "job_id", qi["job_id"])
        wo = self._row("work_orders", "wo_id", wo_id)

        # Production-side sanity: independent of the queue chain. Today
        # this passes — confirms the bug is queue-side only.
        self.assertEqual(
            prod["status"], "completed",
            "print_jobs.status should be 'completed' after "
            "handle_print_completed (production-side is independent of "
            "the queue rollup). Got: '{}'.".format(prod["status"]),
        )

        # The four bug-pinning assertions in order of most-direct evidence:
        self.assertEqual(
            qi["status"], "completed",
            "queue_items.status (queue_id={}) should be 'completed' after "
            "handle_print_completed, got '{}'. Cause: every search "
            "predicate in queue_handler.complete + complete_queue_job's "
            "queue_items UPDATE filter WHERE status='printing', and the "
            "queue_job was left in 'starting' because "
            "link_print_job_on_start never advanced it."
            .format(qid, qi["status"]),
        )
        self.assertEqual(
            qj["status"], "completed",
            "queue_jobs.status (queue_job_id={}) should be 'completed', "
            "got '{}'. complete_queue_job was never invoked because the "
            "queue_handler.complete fallbacks all require status='printing'."
            .format(qjid, qj["status"]),
        )
        self.assertEqual(
            job["status"], "completed",
            "jobs.status (job_id={}) should be 'completed' (derived from "
            "queue_items rollup via status_sync.derive_job_status), got "
            "'{}'.".format(job["job_id"], job["status"]),
        )
        self.assertEqual(
            wo["status"], "completed",
            "work_orders.status (wo_id={}) should be 'completed' "
            "(derived from queue_items rollup via "
            "status_sync.derive_work_order_status), got '{}'.".format(
                wo_id, wo["status"]),
        )

    def test_completion_with_filename_mismatch_in_link_start(self):
        """Filename-mismatch scenario must still roll up to 'completed'.

        Models the production failure mode the bug-fix targets: no
        `pending_print_start`, no `upload_session` linkage, and a state
        filename that doesn't match `queue_jobs.gcode_file`. Pre-fix this
        left the queue_job stuck in 'uploading' and completion silently
        no-opped on the queue chain. Post-fix the widened
        `_get_active_queue_job_for_printer` predicate lets the printer-only
        fallback adopt the in-flight queue_job, advance it to 'printing',
        and the completion rolls everything to 'completed'. This test
        verifies the *final* state — the prior intermediate-state check
        was witnessing buggy pre-fix behavior and has been removed.
        """
        wo_id, qid, qjid, prod_job_id = self._setup_wo_with_queue_job(
            "uploading", gcode_filename="part-correct.gcode"
        )

        # Drive the start linker with a state filename that doesn't
        # match queue_job.gcode_file. No pending_start, no upload_session.
        # Post-fix, link's printer-only fallback adopts the in-flight
        # queue_job for printer-1 and advances it to 'printing'.
        self.handler.queue_handler.link_print_job_on_start(
            printer_id="printer-1",
            state={"name": "Printer 1",
                   "job": {"filename": "part-WRONG.gcode"}},
            job_id=prod_job_id,
            pending_start=None,
            upload_session=None,
        )

        # Drive completion with the same wrong filename the printer
        # reports.
        self._drive_handle_complete(gcode_filename="part-WRONG.gcode")

        qi = self._row("queue_items", "queue_id", qid)
        qj = self._row("queue_jobs", "queue_job_id", qjid)
        wo = self._row("work_orders", "wo_id", wo_id)

        self.assertEqual(
            qi["status"], "completed",
            "queue_items.status (queue_id={}) should be 'completed' "
            "after the filename-mismatch scenario. Got '{}'. The "
            "completion chain must not depend on filename matching."
            .format(qid, qi["status"]),
        )
        self.assertEqual(
            qj["status"], "completed",
            "queue_jobs.status (queue_job_id={}) should be 'completed', "
            "got '{}'. complete_queue_job should fire via the widened "
            "_get_active_queue_job_for_printer fallback."
            .format(qjid, qj["status"]),
        )
        self.assertEqual(
            wo["status"], "completed",
            "work_orders.status (wo_id={}) should be 'completed', got "
            "'{}'.".format(wo_id, wo["status"]),
        )

    def test_predicate_asymmetry_in_complete_queue_job(self):
        """complete_queue_job's queue_items UPDATE filter is the asymmetry.

        Bypasses the queue_handler search to isolate the raw UPDATE.
        `_set_queue_job_status` (the canonical helper used by every
        other mark_* method) accepts any non-terminal status; the raw
        UPDATE in `complete_queue_job` requires `status='printing'`.
        This test demonstrates that even if the search succeeds in
        finding the queue_job, the completion write still drops the
        queue_items when they aren't in 'printing'.
        """
        wo_id, qid, qjid, prod_job_id = self._setup_wo_with_queue_job(
            "starting"
        )

        # Direct call — bypasses queue_handler.complete's search and
        # exposes the raw UPDATE predicate.
        self.qe_repo.complete_queue_job(qjid, print_job_id=prod_job_id)

        qi = self._row("queue_items", "queue_id", qid)
        qj = self._row("queue_jobs", "queue_job_id", qjid)
        job = self._row("jobs", "job_id", qi["job_id"])
        wo = self._row("work_orders", "wo_id", wo_id)

        # Baseline: queue_jobs UPDATE has no status filter — passes today.
        self.assertEqual(
            qj["status"], "completed",
            "Baseline: queue_jobs UPDATE has no status filter and should "
            "complete unconditionally. queue_jobs.status "
            "(queue_job_id={}) was '{}'. If this fails, the test premise "
            "is wrong.".format(qjid, qj["status"]),
        )

        # The asymmetry — queue_items UPDATE requires 'printing'.
        self.assertEqual(
            qi["status"], "completed",
            "queue_items.status (queue_id={}) should be 'completed' "
            "after complete_queue_job, but the UPDATE at "
            "execution_lifecycle.py:265-271 filters WHERE "
            "status='printing' and the queue_item is '{}'. The "
            "canonical _set_queue_job_status helper at lines 36-41 "
            "uses the permissive `status NOT IN ('completed', "
            "'cancelled')` predicate — these two should agree."
            .format(qid, qi["status"]),
        )
        self.assertEqual(
            job["status"], "completed",
            "jobs.status should be 'completed' (derived after rollup). "
            "Got '{}'.".format(job["status"]),
        )
        self.assertEqual(
            wo["status"], "completed",
            "work_orders.status should be 'completed' (derived after "
            "rollup). Got '{}'.".format(wo["status"]),
        )

    def test_baseline_happy_path_still_works(self):
        """Sanity baseline. Pins the existing happy-path behaviour.

        When the queue_job IS in 'printing' (i.e. link_print_job_on_start
        + mark_queue_job_printing have both fired correctly), the
        completion rolls up the whole chain. This test passes today; it
        guards against a fix that fixes the bug but breaks the happy
        path.
        """
        wo_id, qid, qjid, prod_job_id = self._setup_wo_with_queue_job(
            "printing"
        )

        self._drive_handle_complete()

        prod = self._prod_row(prod_job_id)
        qi = self._row("queue_items", "queue_id", qid)
        qj = self._row("queue_jobs", "queue_job_id", qjid)
        job = self._row("jobs", "job_id", qi["job_id"])
        wo = self._row("work_orders", "wo_id", wo_id)

        self.assertEqual(
            prod["status"], "completed",
            "Happy path regression: print_jobs.status should be "
            "'completed'. Got '{}'.".format(prod["status"]),
        )
        self.assertEqual(
            qi["status"], "completed",
            "Happy path regression: queue_items.status (queue_id={}) "
            "should be 'completed'. Got '{}'.".format(qid, qi["status"]),
        )
        self.assertEqual(
            qj["status"], "completed",
            "Happy path regression: queue_jobs.status "
            "(queue_job_id={}) should be 'completed'. Got '{}'.".format(
                qjid, qj["status"]),
        )
        self.assertEqual(
            job["status"], "completed",
            "Happy path regression: jobs.status should be 'completed'. "
            "Got '{}'.".format(job["status"]),
        )
        self.assertEqual(
            wo["status"], "completed",
            "Happy path regression: work_orders.status (wo_id={}) "
            "should be 'completed'. Got '{}'.".format(wo_id, wo["status"]),
        )


if __name__ == "__main__":
    unittest.main()
