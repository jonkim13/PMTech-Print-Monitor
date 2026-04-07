"""
Print Farm Manager
===================
Manages all printers, runs the background polling loop,
tracks job history, detects state transitions, and logs
production data for ISO 9001 traceability.
"""

import os
import json
import time
import threading
from datetime import datetime, timezone

from prusalink import PrusaLinkClient
from database import PrintHistoryDB, FilamentInventoryDB, FilamentAssignmentDB

from app.shared.constants import PrinterStatus


class PrintFarmManager:
    """
    Manages all printers, runs the polling loop,
    tracks job history, and detects state changes.
    """

    def __init__(self, config: dict, history_db: PrintHistoryDB,
                 filament_db: FilamentInventoryDB = None,
                 assignment_db: FilamentAssignmentDB = None,
                 production_db=None, snapshots_dir=None,
                 data_dir=None, work_order_db=None,
                 upload_session_db=None,
                 event_service=None, transition_handler=None,
                 runtime_state=None, state_lock=None):
        self.printers = {}
        self.poll_interval = config.get("poll_interval_sec", 5)
        self.history_db = history_db
        self.filament_db = filament_db
        self.assignment_db = assignment_db
        self.production_db = production_db
        self.work_order_db = work_order_db
        self.upload_session_db = upload_session_db
        if event_service is None:
            from app.domains.monitoring.event_service import EventService
            event_service = EventService()
        self.event_service = event_service
        self.snapshots_dir = snapshots_dir
        self.data_dir = data_dir
        self._lock = state_lock or threading.Lock()

        from app.domains.monitoring.runtime_state import (
            MonitoringRuntimeState,
        )
        from app.domains.printers.service import PrinterStatusService

        self.runtime_state = runtime_state or MonitoringRuntimeState()
        # Backward-compatible aliases used by the current orchestration code.
        self._print_start_times = self.runtime_state.print_start_times
        self._active_job_ids = self.runtime_state.active_job_ids
        self._active_queue_job_ids = (
            self.runtime_state.active_queue_job_ids
        )
        self._pending_print_starts = (
            self.runtime_state.pending_print_starts
        )
        self._stopped_printers = self.runtime_state.stopped_printers
        self.printer_service = PrinterStatusService(self.printers, self._lock)
        self.transition_handler = (
            transition_handler or self._build_transition_handler()
        )

        # Initialize printer clients
        for pid, pcfg in config.get("printers", {}).items():
            client = PrusaLinkClient(
                printer_id=pid,
                name=pcfg["name"],
                host=pcfg["host"],
                username=pcfg.get("username", "maker"),
                password=pcfg.get("password", ""),
                model=pcfg.get("model", "unknown"),
                upload_storage=pcfg.get("upload_storage", "usb"),
            )
            self.printers[pid] = {
                "client": client,
                "previous_status": PrinterStatus.UNKNOWN,
            }

        # Restore previous state so first poll doesn't create false events
        self._restore_previous_state()

    def _get_runtime_state(self):
        """Return the monitoring runtime state container."""
        runtime_state = getattr(self, "runtime_state", None)
        if runtime_state is None:
            from app.domains.monitoring.runtime_state import (
                MonitoringRuntimeState,
            )

            runtime_state = MonitoringRuntimeState(
                print_start_times=getattr(self, "_print_start_times", {}),
                active_job_ids=getattr(self, "_active_job_ids", {}),
                active_queue_job_ids=getattr(
                    self, "_active_queue_job_ids", {}
                ),
                pending_print_starts=getattr(
                    self, "_pending_print_starts", {}
                ),
                stopped_printers=getattr(self, "_stopped_printers", set()),
            )
            self.runtime_state = runtime_state
        self._print_start_times = runtime_state.print_start_times
        self._active_job_ids = runtime_state.active_job_ids
        self._active_queue_job_ids = runtime_state.active_queue_job_ids
        self._pending_print_starts = runtime_state.pending_print_starts
        self._stopped_printers = runtime_state.stopped_printers
        return runtime_state

    def _get_printer_service(self):
        """Return the printer status service facade."""
        service = getattr(self, "printer_service", None)
        if service is None or service.printers is not self.printers:
            from app.domains.printers.service import PrinterStatusService

            service = PrinterStatusService(
                self.printers,
                getattr(self, "_lock", None),
            )
            self.printer_service = service
        return service

    def _build_transition_handler(self):
        """Create the side-effect handler for status transitions."""
        from app.domains.monitoring.transition_handler import TransitionHandler

        return TransitionHandler(
            history_db=getattr(self, "history_db", None),
            production_db=getattr(self, "production_db", None),
            work_order_db=getattr(self, "work_order_db", None),
            filament_db=getattr(self, "filament_db", None),
            assignment_db=getattr(self, "assignment_db", None),
            upload_session_db=getattr(self, "upload_session_db", None),
            event_service=getattr(self, "event_service", None),
            runtime_state=self._get_runtime_state(),
            snapshots_dir=getattr(self, "snapshots_dir", None),
            state_lock=getattr(self, "_lock", None),
        )

    def _get_transition_handler(self):
        """Return the transition side-effect handler."""
        handler = getattr(self, "transition_handler", None)
        if handler is None:
            handler = self._build_transition_handler()
            self.transition_handler = handler
        return handler

    # ------------------------------------------------------------------
    # State Persistence & Restoration
    # ------------------------------------------------------------------

    def _state_file_path(self):
        """Path to the server state JSON file."""
        if self.data_dir:
            return os.path.join(self.data_dir, "server_state.json")
        return None

    def _restore_previous_state(self):
        """
        Restore each printer's previous_status so the first poll
        doesn't create false state-change events.
        Tries JSON state file first, falls back to database query.
        """
        restored = {}
        runtime_state = self._get_runtime_state()

        # Step 1: Try loading from state file
        state_path = self._state_file_path()
        if state_path and os.path.exists(state_path):
            try:
                with open(state_path, "r") as f:
                    saved = json.load(f)
                for pid in self.printers:
                    if pid in saved:
                        self.printers[pid]["previous_status"] = saved[pid]
                        restored[pid] = saved[pid]
                print(f"[STARTUP] Restored printer states from state file")
            except Exception as e:
                print(f"[STARTUP] Could not read state file: {e}")

        # Step 2: For any printer not restored, try database
        for pid in self.printers:
            if pid in restored:
                continue
            status = self._get_last_status_from_db(pid)
            if status:
                self.printers[pid]["previous_status"] = status
                restored[pid] = status

        # Step 3: Restore active job IDs from production DB
        if self.production_db:
            for pid in self.printers:
                active_job = self.production_db.get_active_job(pid)
                if active_job:
                    runtime_state.active_job_ids[pid] = active_job["job_id"]
                    # Also restore the start time for duration tracking
                    try:
                        started = datetime.fromisoformat(
                            active_job["started_at"])
                        runtime_state.print_start_times[pid] = started
                    except (ValueError, KeyError):
                        pass

        if self.work_order_db:
            for pid in self.printers:
                try:
                    queue_job = self.work_order_db.get_active_queue_job_for_printer(
                        pid
                    )
                except Exception:
                    queue_job = None
                if queue_job:
                    runtime_state.active_queue_job_ids[pid] = (
                        queue_job["queue_job_id"]
                    )

        if restored:
            print(f"[STARTUP] Restored states: {restored}")
        else:
            print("[STARTUP] No previous state found, starting fresh")

    def _get_last_status_from_db(self, printer_id):
        """Query the most recent event for a printer to find its last status."""
        try:
            history = self.history_db.get_history(limit=50)
            for event in history:
                if event.get("printer_id") == printer_id:
                    return event.get("to_status")
        except Exception:
            pass
        return None

    def _save_state(self):
        """Save current printer statuses to JSON for next startup."""
        state_path = self._state_file_path()
        if not state_path:
            return
        try:
            states = {
                pid: p["previous_status"]
                for pid, p in self.printers.items()
            }
            with open(state_path, "w") as f:
                json.dump(states, f)
        except Exception as e:
            print(f"[STATE] Error saving state: {e}")

    def _prune_pending_print_starts_locked(self):
        """Drop stale pending print-start metadata."""
        self._get_runtime_state().prune_pending_print_starts()

    def _match_pending_print_start_locked(self, printer_id: str,
                                          file_name: str = None,
                                          upload_session_id: str = None):
        """Resolve the best pending start match for a printer."""
        return self._get_runtime_state().match_pending_print_start(
            printer_id,
            file_name=file_name,
            upload_session_id=upload_session_id,
        )

    def record_stopped_printer(self, printer_id: str):
        """Record that an operator requested a stop for a printer."""
        with self._lock:
            self._get_runtime_state().record_stopped_printer(printer_id)

    def _consume_stopped_printer(self, printer_id: str):
        """Return whether this printer's completion transition was a stop."""
        with self._lock:
            return self._get_runtime_state().consume_stopped_printer(
                printer_id
            )

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll_printer(self, printer_id: str) -> dict:
        """Poll one printer, update transition state, and return the state."""
        from app.domains.monitoring.transition_detector import (
            PRINT_COMPLETE,
            PRINT_STARTED,
            PRINTER_ERROR,
            build_transition_event,
            detect_status_transition,
        )

        printer_data = self.printers.get(printer_id)
        if not printer_data:
            return {"error": "Unknown printer"}

        client = printer_data["client"]
        prev_status = printer_data["previous_status"]
        state = client.poll()
        new_status = state["status"]

        if prev_status != new_status:
            transition_type = detect_status_transition(
                prev_status,
                new_status,
            )
            event = build_transition_event(
                printer_id,
                prev_status,
                state,
                datetime.now(timezone.utc).isoformat(),
            )

            if transition_type == PRINT_COMPLETE:
                if self._consume_stopped_printer(printer_id):
                    self._get_transition_handler().handle_print_stopped(
                        printer_id, state["name"], state, client, event
                    )
                else:
                    self._get_transition_handler().handle_print_completed(
                        printer_id, state["name"], state, client, event
                    )

            elif transition_type == PRINT_STARTED:
                self._get_transition_handler().handle_print_started(
                    printer_id, state["name"], state, client, event
                )

            elif transition_type == PRINTER_ERROR:
                self._get_transition_handler().handle_print_failed(
                    printer_id, state["name"], state, client, event
                )

            printer_data["previous_status"] = new_status

        return state

    def poll_all(self):
        """Poll all printers and detect state changes."""
        for printer_id in self.printers:
            self.poll_printer(printer_id)

        # Save state after every poll cycle
        self._save_state()

    def _get_printer_model(self, printer_id: str) -> str:
        """Get the model of a printer from its client state."""
        return self._get_printer_service().get_model(printer_id)

    def _get_tool_count(self, printer_id: str) -> int:
        """Return the number of tool heads for a printer model."""
        return self._get_printer_service().get_tool_count(printer_id)

    def _auto_deduct_filament(self, printer_id: str, state: dict):
        """Compatibility wrapper for moved transition side effect."""
        client = self.printers.get(printer_id, {}).get("client")
        return self._get_transition_handler().auto_deduct_filament(
            printer_id, state, client
        )

    def _production_complete(self, printer_id, client, state, duration_sec):
        """Compatibility wrapper for moved transition side effect."""
        return self._get_transition_handler().production_complete(
            printer_id, client, state, duration_sec
        )

    def _enrich_with_spool(self, printer_id: str, status: dict) -> dict:
        """Attach assigned spool info to a printer status dict.

        For multi-tool printers, includes assigned_spools list with
        per-tool assignments. Also keeps backward-compatible
        assigned_spool (tool 0) field.
        """
        if not self.assignment_db or not self.filament_db:
            status["assigned_spool"] = None
            status["assigned_spools"] = []
            status["tool_count"] = self._get_tool_count(printer_id)
            return status

        tool_count = self._get_tool_count(printer_id)
        status["tool_count"] = tool_count

        assignments = self.assignment_db.get_printer_assignments(printer_id)
        # Build per-tool spool list
        spools_by_tool = {}  # type: dict
        for a in assignments:
            spool = self.filament_db.get_by_id(a["spool_id"])
            if spool:
                spools_by_tool[a["tool_index"]] = spool

        assigned_spools = []
        for t in range(tool_count):
            spool = spools_by_tool.get(t)
            assigned_spools.append({
                "tool_index": t,
                "spool": spool,  # full spool dict or None
            })
        status["assigned_spools"] = assigned_spools

        # Backward compat: assigned_spool = tool 0 spool
        status["assigned_spool"] = spools_by_tool.get(0)
        return status

    def get_all_status(self) -> list:
        """Return current status of all printers."""
        return self._get_printer_service().get_all_status(
            enrich_status=self._enrich_with_spool
        )

    def get_printer_status(self, printer_id: str) -> dict:
        """Return status of a specific printer."""
        return self._get_printer_service().get_status(
            printer_id,
            enrich_status=self._enrich_with_spool,
        )

    def get_printer_client(self, printer_id: str):
        """Return the PrusaLinkClient for a specific printer."""
        return self._get_printer_service().get_client(printer_id)

    def record_pending_print_start(self, printer_id: str,
                                   upload_session_id: str,
                                   remote_filename: str,
                                   original_filename: str,
                                   operator_initials: str,
                                   queue_job_id: int = None,
                                   job_id: int = None):
        """Store structured start metadata until polling confirms printing."""
        with self._lock:
            self._get_runtime_state().record_pending_print_start(
                printer_id,
                upload_session_id,
                remote_filename,
                original_filename,
                operator_initials,
                queue_job_id=queue_job_id,
                job_id=job_id,
            )

    def clear_pending_print_start(self, printer_id: str,
                                  upload_session_id: str = None,
                                  remote_filename: str = None):
        """Remove pending print-start metadata when a start fails."""
        with self._lock:
            self._get_runtime_state().clear_pending_print_start(
                printer_id,
                upload_session_id=upload_session_id,
                remote_filename=remote_filename,
            )

    def get_pending_print_start(self, printer_id: str, file_name: str = None,
                                upload_session_id: str = None):
        """Read initials for the next matching polling-detected start."""
        entry = self.get_pending_print_start_entry(
            printer_id, file_name=file_name,
            upload_session_id=upload_session_id
        )
        if not entry:
            return None
        return entry.get("operator_initials")

    def get_pending_print_start_entry(self, printer_id: str,
                                      file_name: str = None,
                                      upload_session_id: str = None):
        """Read pending print-start metadata for a matching printer."""
        if not printer_id:
            return None
        with self._lock:
            self._prune_pending_print_starts_locked()
            return self._match_pending_print_start_locked(
                printer_id,
                file_name=file_name,
                upload_session_id=upload_session_id,
            )

    def wait_for_print_confirmation(self, printer_id: str,
                                    upload_session_id: str,
                                    timeout_sec: int = 30) -> dict:
        """Poll the printer until printing is observed or the timeout expires."""
        deadline = time.monotonic() + max(1, int(timeout_sec or 0))
        while time.monotonic() < deadline:
            state = self.poll_printer(printer_id)
            if state.get("status") == PrinterStatus.PRINTING:
                return {
                    "ok": True,
                    "success": True,
                    "message": "Printer entered printing state",
                    "details": {
                        "printer_id": printer_id,
                        "upload_session_id": upload_session_id,
                        "filename": state.get("job", {}).get("filename"),
                    },
                }
            time.sleep(min(2, self.poll_interval))
        return {
            "ok": False,
            "success": False,
            "message": "Printer never entered printing state after the start request",
            "error_type": "start_timeout",
            "details": {
                "printer_id": printer_id,
                "upload_session_id": upload_session_id,
                "last_status": self.get_printer_status(printer_id).get("status"),
            },
        }

    def get_pending_events(self) -> list:
        """Get and clear pending events."""
        return self.event_service.consume_events()

    def peek_pending_events(self) -> list:
        """Get pending events without clearing them."""
        return self.event_service.peek_events()

    def get_job_history(self) -> list:
        """Return recent in-memory events."""
        return self.event_service.get_job_history()

    def start_polling(self):
        """Start the background polling thread."""
        def _poll_loop():
            while True:
                try:
                    self.poll_all()
                except Exception as e:
                    print(f"[ERROR] Poll loop exception: {e}")
                time.sleep(self.poll_interval)

        thread = threading.Thread(target=_poll_loop, daemon=True)
        thread.start()
        print(f"Polling {len(self.printers)} printers every "
              f"{self.poll_interval}s")
