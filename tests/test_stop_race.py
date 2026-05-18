"""Stop-pending race tests.

Validates the poller's behavior when a printing->idle transition fires
while a service-initiated cancel is still in flight, using the
`_stop_pending` flag that farm_manager sets before calling `stop_job`.

The three scenarios:

1. Flag set + transition observed within window: poller routes through
   the cancel handler. Filament NOT deducted, production outcome
   cancelled, queue_item cancelled.
2. Flag NOT set + transition observed: normal completion path runs
   (filament deducted, production completed, queue_item completed).
3. Flag set but stale (>120s): flag is cleared and the normal
   completion path runs.
"""

import os
import sys
import threading
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from farm_manager import PrintFarmManager
from app.domains.monitoring.runtime_state import MonitoringRuntimeState
from app.shared.constants import PrinterStatus


class FakePollingClient:
    def __init__(self, state):
        self.state = state

    def poll(self):
        return dict(self.state)


class RecordingHandler:
    """TransitionHandler stand-in. Tracks which branch fired."""
    def __init__(self):
        self.calls = []

    def handle_print_completed(self, *args, **kwargs):
        self.calls.append("completed")

    def handle_print_stopped(self, *args, **kwargs):
        self.calls.append("stopped")

    def handle_print_cancelled(self, *args, **kwargs):
        self.calls.append("cancelled")

    def handle_print_started(self, *args, **kwargs):
        self.calls.append("started")

    def handle_print_failed(self, *args, **kwargs):
        self.calls.append("failed")


def build_manager(client, handler):
    runtime_state = MonitoringRuntimeState()
    m = PrintFarmManager.__new__(PrintFarmManager)
    m.printers = {
        "printer-1": {
            "client": client,
            "previous_status": PrinterStatus.PRINTING,
        }
    }
    m.runtime_state = runtime_state
    m._print_start_times = runtime_state.print_start_times
    m._active_job_ids = runtime_state.active_job_ids
    m._active_queue_job_ids = runtime_state.active_queue_job_ids
    m._pending_print_starts = runtime_state.pending_print_starts
    m._stopped_printers = runtime_state.stopped_printers
    m._stop_pending = {}
    m.history_db = None
    m._lock = threading.Lock()
    m.transition_handler = handler
    return m


class StopRaceTests(unittest.TestCase):
    def _poll_after_idle(self, handler=None):
        handler = handler or RecordingHandler()
        client = FakePollingClient({
            "name": "Printer 1",
            "status": PrinterStatus.IDLE,
            "job": {"filename": "part.gcode"},
        })
        manager = build_manager(client, handler)
        return manager, handler, client

    def test_stop_pending_set_routes_to_cancel_handler(self):
        manager, handler, _ = self._poll_after_idle()
        manager.mark_stop_pending("printer-1")
        manager.poll_printer("printer-1")
        self.assertEqual(handler.calls, ["cancelled"])
        self.assertNotIn("printer-1", manager._stop_pending)

    def test_stop_pending_not_set_routes_to_completed_handler(self):
        manager, handler, _ = self._poll_after_idle()
        manager.poll_printer("printer-1")
        self.assertEqual(handler.calls, ["completed"])

    def test_stale_stop_pending_is_dropped_and_completion_runs(self):
        manager, handler, _ = self._poll_after_idle()
        # Simulate a marker older than the 120s window.
        manager._stop_pending["printer-1"] = time.time() - 121
        manager.poll_printer("printer-1")
        self.assertEqual(handler.calls, ["completed"])
        self.assertNotIn("printer-1", manager._stop_pending)


class CancelHandlerIntegrationTests(unittest.TestCase):
    """TransitionHandler.handle_print_cancelled must leave consistent state.

    Verifies the critical invariants in isolation from the poll loop:
    - queue_item transitions 'printing' -> 'cancelled' (not 'completed')
    - production print_job: status='stopped', outcome='cancelled'
    - no filament deduction
    """

    def _build(self):
        import tempfile, sqlite3, shutil
        from app.domains.queue.repository import QueueRepository
        from app.domains.queue.execution_repository import QueueExecutionRepository
        from app.domains.work_orders.repository import WorkOrderRepository
        from app.domains.work_orders.job_repository import JobRepository
        from app.domains.production.job_repository import PrintJobRepository
        from app.domains.monitoring.transition_handler import TransitionHandler
        from app.domains.monitoring.runtime_state import MonitoringRuntimeState

        tmp = tempfile.mkdtemp()
        wo_db = os.path.join(tmp, "wo.db")
        prod_db = os.path.join(tmp, "prod.db")

        wo_repo = WorkOrderRepository(wo_db)
        job_repo = JobRepository(wo_db)
        q_repo = QueueRepository(wo_db)
        qe_repo = QueueExecutionRepository(wo_db)
        prod_repo = PrintJobRepository(prod_db)
        runtime = MonitoringRuntimeState()

        class FilamentAsserter:
            """Errors if auto_deduct is called — catches filament leaks."""
            def auto_deduct_filament(self, printer_id, state, client):
                raise AssertionError(
                    "Filament deducted on a cancelled print!"
                )

        class NoopFilamentDB:
            pass

        class NoopAssignmentDB:
            pass

        handler = TransitionHandler(
            history_db=None,
            filament_db=NoopFilamentDB(),
            assignment_db=NoopAssignmentDB(),
            upload_session_db=None,
            event_service=None,
            runtime_state=runtime,
            state_lock=threading.Lock(),
            snapshots_dir=None,
            job_repository=prod_repo,
            machine_repository=_FakeMachineRepo(),
            material_repository=None,
            queue_repository=q_repo,
            queue_execution_repository=qe_repo,
        )
        handler.filament_handler = FilamentAsserter()

        return {
            "tmp": tmp, "wo_db": wo_db, "prod_db": prod_db,
            "wo_repo": wo_repo, "job_repo": job_repo,
            "q_repo": q_repo, "qe_repo": qe_repo,
            "prod_repo": prod_repo, "runtime": runtime, "handler": handler,
        }

    def test_cancel_handler_writes_consistent_state(self):
        import shutil, sqlite3
        ctx = self._build()
        try:
            wo = ctx["wo_repo"].create_work_order("Race", [
                {"part_name": "P", "material": "PLA", "quantity": 1},
            ])
            wo_id = wo["wo_id"]
            qid = sqlite3.connect(ctx["wo_db"]).execute(
                "SELECT queue_id FROM queue_items WHERE wo_id=?", (wo_id,)
            ).fetchone()[0]

            # Simulate the item through printing
            res = ctx["qe_repo"].start_queue_job_execution(
                [qid], "printer-1", "Printer 1", "part.gcode",
                operator_initials="TS",
            )
            qjid = res["queue_job_id"]
            ctx["qe_repo"].mark_queue_job_uploaded(qjid)
            ctx["qe_repo"].mark_queue_job_starting(qjid)
            ctx["qe_repo"].mark_queue_job_printing(qjid)

            # Create a production record in 'started' state
            prod_job_id = ctx["prod_repo"].create_job(
                printer_id="printer-1", printer_name="Printer 1",
                file_name="part.gcode",
            )
            ctx["runtime"].active_job_ids["printer-1"] = prod_job_id
            ctx["runtime"].active_queue_job_ids["printer-1"] = qjid

            # Invoke the cancel handler directly
            ctx["handler"].handle_print_cancelled(
                "printer-1", "Printer 1",
                {"name": "Printer 1", "job": {"filename": "part.gcode"}},
                client=None,
                event={"printer_id": "printer-1",
                       "filename": "part.gcode",
                       "timestamp": "2026-04-17T00:00:00+00:00"},
            )

            # queue_item must be cancelled, not completed
            status = sqlite3.connect(ctx["wo_db"]).execute(
                "SELECT status FROM queue_items WHERE queue_id=?", (qid,)
            ).fetchone()[0]
            self.assertEqual(status, "cancelled")

            # Production job must be stopped + outcome=cancelled
            prod_row = ctx["prod_repo"].get_job(prod_job_id)
            self.assertEqual(prod_row["status"], "stopped")
            self.assertEqual(prod_row["outcome"], "cancelled")

            # Filament wasn't deducted — the FilamentAsserter would have
            # raised if it had been called.
        finally:
            shutil.rmtree(ctx["tmp"])


class _FakeMachineRepo:
    def log_machine_event(self, *args, **kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
