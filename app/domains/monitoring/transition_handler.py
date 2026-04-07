"""Transition side-effect dispatcher for monitoring poll events."""

from contextlib import nullcontext
from datetime import datetime, timezone

from app.domains.monitoring.filament_handler import FilamentHandler
from app.domains.monitoring.production_handler import ProductionHandler
from app.domains.monitoring.queue_handler import QueueHandler
from app.shared.constants import EventType


class TransitionHandler:
    """Dispatch detected printer transitions to focused side-effect handlers."""

    def __init__(self, history_db=None, production_db=None,
                 work_order_db=None, filament_db=None, assignment_db=None,
                 upload_session_db=None, event_service=None,
                 runtime_state=None, snapshots_dir=None, state_lock=None,
                 production_handler=None, queue_handler=None,
                 filament_handler=None):
        self.history_db = history_db
        self.event_service = event_service
        self.runtime_state = runtime_state
        self.state_lock = state_lock
        self.queue_handler = queue_handler or QueueHandler(
            work_order_db=work_order_db,
            runtime_state=runtime_state,
        )
        self.production_handler = production_handler or ProductionHandler(
            production_db=production_db,
            filament_db=filament_db,
            assignment_db=assignment_db,
            upload_session_db=upload_session_db,
            runtime_state=runtime_state,
            snapshots_dir=snapshots_dir,
            state_lock=state_lock,
            queue_handler=self.queue_handler,
        )
        self.filament_handler = filament_handler or FilamentHandler(
            filament_db=filament_db,
            assignment_db=assignment_db,
            production_db=production_db,
            runtime_state=runtime_state,
        )

    def _locked(self):
        return self.state_lock if self.state_lock else nullcontext()

    def _print_start_times(self):
        return self.runtime_state.print_start_times if self.runtime_state else {}

    # ------------------------------------------------------------------
    # Transition Dispatch
    # ------------------------------------------------------------------

    def handle_print_started(self, printer_id, printer_name, state, client,
                             event):
        """Handle a printer entering the printing state."""
        event["type"] = EventType.PRINT_STARTED
        self._print_start_times()[printer_id] = datetime.now(timezone.utc)
        self._record_transition_event(event, add_pending=False)
        self.production_handler.start(printer_id, client, state)
        print(f"[EVENT] Print started on {printer_name}: "
              f"{state['job']['filename']}")

    def handle_print_completed(self, printer_id, printer_name, state, client,
                               event):
        """Handle a printer completing a print."""
        event["type"] = EventType.PRINT_COMPLETE
        self._set_duration_and_clear_start(printer_id, event)
        self._record_transition_event(event, add_pending=True)
        self.filament_handler.auto_deduct_filament(printer_id, state, client)
        self.production_handler.complete(
            printer_id, client, state, event["duration_sec"]
        )
        self.queue_handler.complete(printer_id, state)
        print(f"[EVENT] Print complete on {printer_name}: "
              f"{state['job']['filename']}")

    def handle_print_failed(self, printer_id, printer_name, state, client,
                            event):
        """Handle a printer entering an error state."""
        event["type"] = EventType.PRINTER_ERROR
        self._record_transition_event(event, add_pending=True)
        self.production_handler.fail(printer_id, state)
        self.queue_handler.fail(printer_id, state)
        print(f"[EVENT] Error on {printer_name}!")

    def handle_print_stopped(self, printer_id, printer_name, state, client,
                             event):
        """Handle an operator-stopped print without completion side effects."""
        event["type"] = EventType.PRINT_STOPPED
        self._set_duration_and_clear_start(printer_id, event)
        self._record_transition_event(event, add_pending=True)
        self.production_handler.stop(
            printer_id, state, event["duration_sec"]
        )
        self.queue_handler.fail(printer_id, state)
        print(f"[EVENT] Print stopped on {printer_name}: "
              f"{state['job']['filename']}")

    # ------------------------------------------------------------------
    # Event Logging
    # ------------------------------------------------------------------

    def _set_duration_and_clear_start(self, printer_id, event):
        start = self._print_start_times().pop(printer_id, None)
        if start:
            event["duration_sec"] = int(
                (datetime.now(timezone.utc) - start).total_seconds()
            )

    def _record_transition_event(self, event, add_pending=False):
        """Persist history and in-memory events with existing dedupe rules."""
        if self._is_duplicate_history_event(event):
            return

        with self._locked():
            if add_pending and self.event_service:
                if not self.event_service.is_duplicate_pending_event(event):
                    self.event_service.add_event(event)
            if self.event_service:
                self.event_service.add_job_history(event)

        if self.history_db:
            self.history_db.log_event(event)

    def _is_duplicate_history_event(self, event):
        """Check if a similar event was already logged recently."""
        if not self.history_db:
            return False
        try:
            recent = self.history_db.get_history(limit=20)
            for existing in recent:
                if (existing.get("printer_id") == event.get("printer_id")
                        and existing.get("event_type") == event.get("type")
                        and existing.get("filename") == event.get("filename")
                        and self._is_within_duplicate_window(existing, event)):
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _is_within_duplicate_window(existing, event):
        try:
            existing_time = datetime.fromisoformat(existing["timestamp"])
            event_time = datetime.fromisoformat(event["timestamp"])
            return abs((event_time - existing_time).total_seconds()) < 60
        except (ValueError, KeyError):
            return False
