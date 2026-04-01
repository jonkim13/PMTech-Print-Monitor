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
import copy
from datetime import datetime, timedelta, timezone

from prusalink import PrusaLinkClient
from database import PrintHistoryDB, FilamentInventoryDB, FilamentAssignmentDB


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
                 upload_session_db=None):
        self.printers = {}
        self.job_history = []       # in-memory recent events
        self.poll_interval = config.get("poll_interval_sec", 5)
        self.history_db = history_db
        self.filament_db = filament_db
        self.assignment_db = assignment_db
        self.production_db = production_db
        self.work_order_db = work_order_db
        self.upload_session_db = upload_session_db
        self.snapshots_dir = snapshots_dir
        self.data_dir = data_dir
        self._lock = threading.Lock()

        # Track elapsed time per printer for duration logging
        self._print_start_times = {}
        # Track active production job IDs per printer
        self._active_job_ids = {}
        # Track active work-order queue job sessions per printer
        self._active_queue_job_ids = {}
        # Pending operator initials for UI-initiated print starts
        self._pending_print_starts = {}

        # Initialize printer clients
        for pid, pcfg in config.get("printers", {}).items():
            client = PrusaLinkClient(
                printer_id=pid,
                name=pcfg["name"],
                host=pcfg["host"],
                username=pcfg.get("username", "maker"),
                password=pcfg.get("password", ""),
                model=pcfg.get("model", "unknown"),
            )
            self.printers[pid] = {
                "client": client,
                "previous_status": "unknown",
            }

        # Events that the drone system will care about
        self.pending_events = []

        # Restore previous state so first poll doesn't create false events
        self._restore_previous_state()

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
                    self._active_job_ids[pid] = active_job["job_id"]
                    # Also restore the start time for duration tracking
                    try:
                        started = datetime.fromisoformat(
                            active_job["started_at"])
                        self._print_start_times[pid] = started
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
                    self._active_queue_job_ids[pid] = queue_job["queue_job_id"]

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

    @staticmethod
    def _normalize_print_filename(file_name):
        """Normalize filenames for pending print-start matching."""
        return os.path.basename(str(file_name or "")).strip().lower()

    def _prune_pending_print_starts_locked(self):
        """Drop stale pending print-start metadata."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=12)
        empty_printers = []
        for printer_id, entries in self._pending_print_starts.items():
            fresh_entries = []
            for entry in entries:
                created_at = entry.get("created_at")
                try:
                    created = datetime.fromisoformat(created_at)
                except (TypeError, ValueError):
                    continue
                if created >= cutoff:
                    fresh_entries.append(entry)
            if fresh_entries:
                self._pending_print_starts[printer_id] = fresh_entries
            else:
                empty_printers.append(printer_id)
        for printer_id in empty_printers:
            self._pending_print_starts.pop(printer_id, None)

    def _match_pending_print_start_locked(self, printer_id: str,
                                          file_name: str = None,
                                          upload_session_id: str = None):
        """Resolve the best pending start match for a printer."""
        entries = self._pending_print_starts.get(printer_id, [])
        if not entries:
            return None

        if upload_session_id:
            for entry in reversed(entries):
                if entry.get("upload_session_id") == upload_session_id:
                    return dict(entry)

        normalized = self._normalize_print_filename(file_name)
        if normalized:
            for entry in reversed(entries):
                remote_name = self._normalize_print_filename(
                    entry.get("remote_filename")
                )
                original_name = self._normalize_print_filename(
                    entry.get("original_filename")
                )
                if normalized in (remote_name, original_name):
                    return dict(entry)

        if len(entries) == 1:
            return dict(entries[-1])
        return None

    # ------------------------------------------------------------------
    # Deduplication Helpers
    # ------------------------------------------------------------------

    def _is_duplicate_history_event(self, event):
        """Check if a similar event was already logged recently."""
        try:
            recent = self.history_db.get_history(limit=20)
            for existing in recent:
                if (existing.get("printer_id") == event.get("printer_id")
                        and existing.get("event_type") == event.get("type")
                        and existing.get("filename") == event.get("filename")):
                    # Check if within 60 seconds
                    try:
                        existing_time = datetime.fromisoformat(
                            existing["timestamp"])
                        event_time = datetime.fromisoformat(
                            event["timestamp"])
                        if abs((event_time - existing_time
                                ).total_seconds()) < 60:
                            return True
                    except (ValueError, KeyError):
                        pass
        except Exception:
            pass
        return False

    def _is_duplicate_pending_event(self, event):
        """Check if a similar event already exists in pending_events."""
        for existing in self.pending_events:
            if (existing.get("printer_id") == event.get("printer_id")
                    and existing.get("type") == event.get("type")):
                try:
                    existing_time = datetime.fromisoformat(
                        existing["timestamp"])
                    event_time = datetime.fromisoformat(
                        event["timestamp"])
                    if abs((event_time - existing_time
                            ).total_seconds()) < 60:
                        return True
                except (ValueError, KeyError):
                    pass
        return False

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll_printer(self, printer_id: str) -> dict:
        """Poll one printer, update transition state, and return the state."""
        printer_data = self.printers.get(printer_id)
        if not printer_data:
            return {"error": "Unknown printer"}

        client = printer_data["client"]
        prev_status = printer_data["previous_status"]
        state = client.poll()
        new_status = state["status"]

        if prev_status != new_status:
            event = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "printer_id": printer_id,
                "printer_name": state["name"],
                "from_status": prev_status,
                "to_status": new_status,
                "filename": state["job"]["filename"],
                "duration_sec": 0,
            }

            if new_status == "finished" or (
                prev_status == "printing" and new_status == "idle"
            ):
                event["type"] = "print_complete"
                start = self._print_start_times.pop(printer_id, None)
                if start:
                    event["duration_sec"] = int(
                        (datetime.now(timezone.utc) - start).total_seconds()
                    )

                if not self._is_duplicate_history_event(event):
                    with self._lock:
                        if not self._is_duplicate_pending_event(event):
                            self.pending_events.append(event)
                        self.job_history.append(event)
                    self.history_db.log_event(event)

                self._auto_deduct_filament(printer_id, state)
                self._production_complete(
                    printer_id, client, state, event["duration_sec"]
                )
                self._wo_complete(printer_id, state)
                print(f"[EVENT] Print complete on {state['name']}: "
                      f"{state['job']['filename']}")

            elif new_status == "printing" and prev_status != "printing":
                event["type"] = "print_started"
                self._print_start_times[printer_id] = datetime.now(timezone.utc)

                if not self._is_duplicate_history_event(event):
                    with self._lock:
                        self.job_history.append(event)
                    self.history_db.log_event(event)

                self._production_start(printer_id, client, state)
                print(f"[EVENT] Print started on {state['name']}: "
                      f"{state['job']['filename']}")

            elif new_status in ("error",):
                event["type"] = "printer_error"

                if not self._is_duplicate_history_event(event):
                    with self._lock:
                        if not self._is_duplicate_pending_event(event):
                            self.pending_events.append(event)
                        self.job_history.append(event)
                    self.history_db.log_event(event)

                self._production_fail(printer_id, state)
                self._wo_fail(printer_id, state)
                print(f"[EVENT] Error on {state['name']}!")

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
        printer_data = self.printers.get(printer_id)
        if printer_data:
            return printer_data["client"].model
        return "unknown"

    def _get_tool_count(self, printer_id: str) -> int:
        """Return the number of tool heads for a printer model."""
        model = self._get_printer_model(printer_id)
        if model == "xl":
            return 5
        return 1

    def _auto_deduct_filament(self, printer_id: str, state: dict):
        """Deduct estimated filament usage from assigned spools.

        For multi-tool printers (XL), deducts per-tool usage from
        the spool assigned to each tool. For single-tool printers,
        deducts from the tool 0 spool assignment.
        """
        if not self.assignment_db or not self.filament_db:
            return

        model = self._get_printer_model(printer_id)

        # Fetch detailed job metadata (includes per-tool arrays)
        client = self.printers[printer_id]["client"]
        details = client.get_job_details()
        if details.get("error"):
            details = {}
        # Merge basic job data with detailed metadata
        job = dict(state.get("job", {}))
        job.update(details)

        # For XL printers, try per-tool deduction first
        if model == "xl":
            per_tool_g = job.get("filament_used_g_per_tool", [])
            per_tool_mm = job.get("filament_used_mm_per_tool", [])
            deducted_any = False

            for tool_idx in range(len(per_tool_g) if per_tool_g
                                  else len(per_tool_mm) if per_tool_mm
                                  else 0):
                grams = 0
                if per_tool_g and tool_idx < len(per_tool_g):
                    val = per_tool_g[tool_idx]
                    if val:
                        grams = int(float(val))
                elif per_tool_mm and tool_idx < len(per_tool_mm):
                    val = per_tool_mm[tool_idx]
                    if val:
                        grams = int(float(val) * 0.00298)

                if grams > 0:
                    assignment = self.assignment_db.get_assignment(
                        printer_id, tool_index=tool_idx)
                    if assignment:
                        self.filament_db.deduct_weight(
                            assignment["spool_id"], grams)
                        print(f"[FILAMENT] Deducted ~{grams}g from spool "
                              f"{assignment['spool_id']} (T{tool_idx + 1}) "
                              f"on {state['name']}")
                        deducted_any = True

            if deducted_any:
                return
            # Fall through to single-spool logic if no per-tool data

        # Single-tool deduction (Core One, or XL fallback)
        assignment = self.assignment_db.get_assignment(
            printer_id, tool_index=0)
        if not assignment:
            return
        spool_id = assignment["spool_id"]

        grams_used = 0
        if job.get("filament_used_g"):
            grams_used = int(float(job["filament_used_g"]))
        elif job.get("filament_used_mm"):
            mm_used = float(job["filament_used_mm"])
            grams_used = int(mm_used * 0.00298)

        if grams_used > 0:
            self.filament_db.deduct_weight(spool_id, grams_used)
            print(f"[FILAMENT] Deducted ~{grams_used}g from spool "
                  f"{spool_id} on {state['name']}")

    # ------------------------------------------------------------------
    # Production Logging Helpers
    # ------------------------------------------------------------------

    def _production_start(self, printer_id, client, state):
        """Log a print start to the production database."""
        if not self.production_db:
            return
        try:
            # Fetch detailed job info from PrusaLink
            details = client.get_job_details()
            if details.get("error"):
                details = {}

            # Get assigned spool info (tool 0 for backward compat)
            spool_id = None
            spool_material = None
            spool_brand = None
            if self.assignment_db and self.filament_db:
                assignment = self.assignment_db.get_assignment(
                    printer_id, tool_index=0)
                if assignment:
                    spool_id = assignment["spool_id"]
                    spool = self.filament_db.get_by_id(spool_id)
                    if spool:
                        spool_material = spool.get("material")
                        spool_brand = spool.get("brand")

            # Build per-tool spool snapshot for traceability
            tool_spools = {}  # type: dict
            if self.assignment_db and self.filament_db:
                assignments = self.assignment_db.get_printer_assignments(
                    printer_id)
                for a in assignments:
                    s = self.filament_db.get_by_id(a["spool_id"])
                    tool_spools[a["tool_index"]] = {
                        "spool_id": a["spool_id"],
                        "material": s.get("material") if s else None,
                        "brand": s.get("brand") if s else None,
                        "color": s.get("color") if s else None,
                    }

            pending_start = self.get_pending_print_start_entry(
                printer_id, file_name=state["job"]["filename"]
            )
            upload_session = None
            upload_session_id = None
            if pending_start:
                upload_session_id = pending_start.get("upload_session_id")
            if self.upload_session_db and upload_session_id:
                upload_session = self.upload_session_db.get_session(
                    upload_session_id
                )

            file_name = details.get("file_name", state["job"]["filename"])
            file_display_name = details.get(
                "file_display_name", state["job"]["filename"]
            )
            if upload_session:
                file_name = upload_session.get("remote_filename") or file_name
                file_display_name = (
                    upload_session.get("original_filename")
                    or file_display_name
                )
            operator_initials = (
                pending_start.get("operator_initials")
                if pending_start else None
            )
            if not operator_initials and upload_session:
                operator_initials = upload_session.get("operator_initials")

            job_id = self.production_db.create_job(
                printer_id=printer_id,
                printer_name=state["name"],
                file_name=file_name,
                file_display_name=file_display_name,
                filament_type=details.get("filament_type"),
                filament_used_g=float(details.get("filament_used_g") or 0),
                filament_used_mm=float(details.get("filament_used_mm") or 0),
                spool_id=spool_id,
                spool_material=spool_material,
                spool_brand=spool_brand,
                layer_height=details.get("layer_height"),
                nozzle_diameter=details.get("nozzle_diameter"),
                fill_density=details.get("fill_density"),
                nozzle_temp=details.get("nozzle_temp"),
                bed_temp=details.get("bed_temp"),
                tool_spools=tool_spools if tool_spools else None,
                operator_initials=operator_initials,
            )
            self._active_job_ids[printer_id] = job_id

            if self.upload_session_db and upload_session_id:
                self.upload_session_db.set_status(
                    upload_session_id,
                    "printing",
                    last_error=None,
                    operator_initials=operator_initials,
                    completed=True,
                )

            if self.work_order_db:
                queue_job = None
                pending_queue_job_id = (
                    pending_start.get("queue_job_id")
                    if pending_start else None
                )
                if pending_queue_job_id:
                    queue_job = self.work_order_db.get_queue_job(
                        pending_queue_job_id
                    )
                    if queue_job and queue_job.get("status") not in (
                        "uploading", "uploaded", "starting", "printing"
                    ):
                        queue_job = None
                if not queue_job:
                    queue_job = self.work_order_db.get_active_queue_job_for_printer(
                        printer_id
                    )
                if (not queue_job and upload_session
                        and upload_session.get("queue_job_id")):
                    queue_job = self.work_order_db.get_queue_job(
                        upload_session["queue_job_id"]
                    )
                if not queue_job:
                    queue_job = self.work_order_db.find_printing_queue_job_by_filename(
                        printer_id, state["job"]["filename"]
                    )
                if queue_job:
                    self.work_order_db.mark_queue_job_printing(
                        queue_job["queue_job_id"]
                    )
                    self._active_queue_job_ids[printer_id] = (
                        queue_job["queue_job_id"]
                    )
                    self.work_order_db.link_print_job_to_queue_job(
                        queue_job["queue_job_id"], job_id
                    )
                else:
                    self._active_queue_job_ids.pop(printer_id, None)
            if pending_start:
                self.clear_pending_print_start(
                    printer_id,
                    upload_session_id=upload_session_id,
                    remote_filename=(
                        upload_session.get("remote_filename")
                        if upload_session else state["job"]["filename"]
                    ),
                )

            # Machine log
            self.production_db.log_machine_event(
                printer_id, state["name"], "print_start",
                details={"job_id": job_id,
                         "file": state["job"]["filename"]},
            )
            print(f"[PRODUCTION] Job #{job_id} created for "
                  f"{state['name']}")
        except Exception as e:
            print(f"[PRODUCTION] Error logging start: {e}")

    def _production_complete(self, printer_id, client, state, duration_sec):
        """Log a print completion to the production database."""
        if not self.production_db:
            return
        job_id = self._active_job_ids.pop(printer_id, None)
        if not job_id:
            return
        try:
            # Try to get final job details for actual filament usage
            details = client.get_job_details()
            filament_g = 0
            filament_mm = 0
            if not details.get("error"):
                filament_g = float(details.get("filament_used_g") or 0)
                filament_mm = float(details.get("filament_used_mm") or 0)

            # Camera snapshot
            snapshot_path = None
            if self.snapshots_dir:
                try:
                    snap_data = client.get_camera_snapshot()
                    if snap_data:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        fname = f"{printer_id}_{ts}.png"
                        snapshot_path = os.path.join(
                            self.snapshots_dir, fname)
                        with open(snapshot_path, "wb") as f:
                            f.write(snap_data)
                        print(f"[PRODUCTION] Snapshot saved: {fname}")
                except Exception as e:
                    print(f"[PRODUCTION] Snapshot failed: {e}")

            self.production_db.complete_job(
                job_id, duration_sec=duration_sec,
                filament_used_g=filament_g,
                filament_used_mm=filament_mm,
                snapshot_path=snapshot_path,
            )

            # Material usage log — per-tool for XL, single for Core One
            job = self.production_db.get_job(job_id)
            model = self._get_printer_model(printer_id)
            per_tool_g = (details.get("filament_used_g_per_tool", [])
                          if not details.get("error") else [])
            per_tool_mm = (details.get("filament_used_mm_per_tool", [])
                           if not details.get("error") else [])

            if model == "xl" and per_tool_g and self.assignment_db:
                for tidx, g_val in enumerate(per_tool_g):
                    g = float(g_val) if g_val else 0
                    mm = (float(per_tool_mm[tidx])
                          if per_tool_mm and tidx < len(per_tool_mm)
                          and per_tool_mm[tidx] else 0)
                    if g > 0:
                        a = self.assignment_db.get_assignment(
                            printer_id, tool_index=tidx)
                        sid = a["spool_id"] if a else None
                        self.production_db.log_material_usage(
                            spool_id=sid,
                            job_id=job_id,
                            printer_id=printer_id,
                            grams_used=g,
                            mm_used=mm,
                            tool_index=tidx,
                        )
            elif job and job.get("spool_id"):
                self.production_db.log_material_usage(
                    spool_id=job["spool_id"],
                    job_id=job_id,
                    printer_id=printer_id,
                    grams_used=filament_g or job.get("filament_used_g", 0),
                    mm_used=filament_mm or job.get("filament_used_mm", 0),
                    tool_index=0,
                )

            # Machine log
            self.production_db.log_machine_event(
                printer_id, state["name"], "print_complete",
                details={"job_id": job_id, "duration_sec": duration_sec},
            )
            print(f"[PRODUCTION] Job #{job_id} completed")
        except Exception as e:
            print(f"[PRODUCTION] Error logging completion: {e}")

    def _production_fail(self, printer_id, state):
        """Log a print failure to the production database."""
        if not self.production_db:
            return
        job_id = self._active_job_ids.pop(printer_id, None)
        if not job_id:
            return
        try:
            start = self._print_start_times.get(printer_id)
            duration = 0
            if start:
                duration = int(
                    (datetime.now(timezone.utc) - start).total_seconds())

            self.production_db.fail_job(job_id, duration_sec=duration)
            self.production_db.log_machine_event(
                printer_id, state["name"], "print_fail",
                details={"job_id": job_id,
                         "error": state.get("error", "unknown")},
            )
            print(f"[PRODUCTION] Job #{job_id} failed")
        except Exception as e:
            print(f"[PRODUCTION] Error logging failure: {e}")

    # ------------------------------------------------------------------
    # Work Order Queue Integration
    # ------------------------------------------------------------------

    def _wo_complete(self, printer_id, state):
        """Auto-complete a work order queue item when a print finishes."""
        if not self.work_order_db:
            return
        filename = state.get("job", {}).get("filename", "")
        queue_job_id = self._active_queue_job_ids.pop(printer_id, None)
        if queue_job_id:
            try:
                queue_job = self.work_order_db.get_queue_job(queue_job_id)
                queued_file = self._normalize_print_filename(
                    queue_job.get("gcode_file") if queue_job else ""
                )
                current_file = self._normalize_print_filename(filename)
                if (queue_job and queue_job.get("status") == "printing"
                        and (not queued_file or not current_file
                             or queued_file == current_file)):
                    if self.work_order_db.complete_queue_job(queue_job_id):
                        print(f"[WORKORDER] Queue job #{queue_job_id} completed")
                        return
            except Exception as e:
                print(f"[WORKORDER] Error completing queue job: {e}")
        if not filename:
            return
        try:
            queue_job = self.work_order_db.get_active_queue_job_for_printer(
                printer_id
            )
            if queue_job:
                self.work_order_db.complete_queue_job(
                    queue_job["queue_job_id"],
                    print_job_id=queue_job.get("print_job_id"),
                )
                print(f"[WORKORDER] Queue job #{queue_job['queue_job_id']} "
                      f"completed")
                return

            queue_job = self.work_order_db.find_printing_queue_job_by_filename(
                printer_id, filename)
            if queue_job:
                self.work_order_db.complete_queue_job(
                    queue_job["queue_job_id"],
                    print_job_id=queue_job.get("print_job_id"),
                )
                print(f"[WORKORDER] Queue job #{queue_job['queue_job_id']} "
                      f"completed")
                return

            qi = self.work_order_db.find_printing_item_by_filename(
                printer_id, filename)
            if qi:
                # Link the production job if we have one
                job_id = self._active_job_ids.get(printer_id)
                self.work_order_db.complete_queue_item(
                    qi["queue_id"], print_job_id=job_id)
                print(f"[WORKORDER] Queue item #{qi['queue_id']} "
                      f"completed ({qi['part_name']} "
                      f"{qi['sequence_number']}/{qi['total_quantity']} "
                      f"for {qi['customer_name']})")
        except Exception as e:
            print(f"[WORKORDER] Error completing queue item: {e}")

    def _wo_fail(self, printer_id, state):
        """Auto-fail a work order queue item when a printer errors."""
        if not self.work_order_db:
            return
        filename = state.get("job", {}).get("filename", "")
        queue_job_id = self._active_queue_job_ids.pop(printer_id, None)
        if queue_job_id:
            try:
                queue_job = self.work_order_db.get_queue_job(queue_job_id)
                queued_file = self._normalize_print_filename(
                    queue_job.get("gcode_file") if queue_job else ""
                )
                current_file = self._normalize_print_filename(filename)
                if (queue_job and queue_job.get("status") == "printing"
                        and (not queued_file or not current_file
                             or queued_file == current_file)):
                    if self.work_order_db.fail_queue_job(queue_job_id):
                        print(f"[WORKORDER] Queue job #{queue_job_id} failed")
                        return
            except Exception as e:
                print(f"[WORKORDER] Error failing queue job: {e}")
        if not filename:
            return
        try:
            queue_job = self.work_order_db.get_active_queue_job_for_printer(
                printer_id
            )
            if queue_job:
                self.work_order_db.fail_queue_job(queue_job["queue_job_id"])
                print(f"[WORKORDER] Queue job #{queue_job['queue_job_id']} "
                      f"failed")
                return

            queue_job = self.work_order_db.find_printing_queue_job_by_filename(
                printer_id, filename)
            if queue_job:
                self.work_order_db.fail_queue_job(queue_job["queue_job_id"])
                print(f"[WORKORDER] Queue job #{queue_job['queue_job_id']} "
                      f"failed")
                return

            qi = self.work_order_db.find_printing_item_by_filename(
                printer_id, filename)
            if qi:
                self.work_order_db.fail_queue_item(qi["queue_id"])
                print(f"[WORKORDER] Queue item #{qi['queue_id']} "
                      f"failed ({qi['part_name']})")
        except Exception as e:
            print(f"[WORKORDER] Error failing queue item: {e}")

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
        with self._lock:
            result = []
            for pid, p in self.printers.items():
                s = copy.deepcopy(p["client"].state)
                self._enrich_with_spool(pid, s)
                result.append(s)
            return result

    def get_printer_status(self, printer_id: str) -> dict:
        """Return status of a specific printer."""
        printer_data = self.printers.get(printer_id)
        if printer_data:
            with self._lock:
                s = copy.deepcopy(printer_data["client"].state)
                return self._enrich_with_spool(printer_id, s)
        return {"error": f"Unknown printer: {printer_id}"}

    def get_printer_client(self, printer_id: str):
        """Return the PrusaLinkClient for a specific printer."""
        printer_data = self.printers.get(printer_id)
        if printer_data:
            return printer_data["client"]
        return None

    def record_pending_print_start(self, printer_id: str,
                                   upload_session_id: str,
                                   remote_filename: str,
                                   original_filename: str,
                                   operator_initials: str,
                                   queue_job_id: int = None,
                                   job_id: int = None):
        """Store structured start metadata until polling confirms printing."""
        normalized_remote = self._normalize_print_filename(remote_filename)
        normalized_original = self._normalize_print_filename(original_filename)
        initials = str(operator_initials or "").strip()
        if not printer_id or not upload_session_id or not initials:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._prune_pending_print_starts_locked()
            entries = self._pending_print_starts.setdefault(printer_id, [])
            for entry in reversed(entries):
                if entry.get("upload_session_id") != upload_session_id:
                    continue
                entry["created_at"] = now
                entry["remote_filename"] = normalized_remote
                entry["original_filename"] = normalized_original
                entry["operator_initials"] = initials
                if queue_job_id is not None:
                    entry["queue_job_id"] = queue_job_id
                if job_id is not None:
                    entry["job_id"] = job_id
                return
            entries.append({
                "upload_session_id": upload_session_id,
                "remote_filename": normalized_remote,
                "original_filename": normalized_original,
                "operator_initials": initials,
                "queue_job_id": queue_job_id,
                "job_id": job_id,
                "created_at": now,
            })

    def clear_pending_print_start(self, printer_id: str,
                                  upload_session_id: str = None,
                                  remote_filename: str = None):
        """Remove pending print-start metadata when a start fails."""
        normalized_remote = self._normalize_print_filename(remote_filename)
        if not printer_id:
            return
        with self._lock:
            self._prune_pending_print_starts_locked()
            entries = self._pending_print_starts.get(printer_id, [])
            for index in range(len(entries) - 1, -1, -1):
                entry = entries[index]
                if upload_session_id and (
                    entry.get("upload_session_id") != upload_session_id
                ):
                    continue
                if normalized_remote and (
                    entry.get("remote_filename") != normalized_remote
                ):
                    continue
                entries.pop(index)
                break
            if not entries:
                self._pending_print_starts.pop(printer_id, None)

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
            if state.get("status") == "printing":
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
        """
        Get and clear pending events.
        The drone system will call this to know what needs attention.
        """
        with self._lock:
            events = self.pending_events.copy()
            self.pending_events.clear()
        return events

    def peek_pending_events(self) -> list:
        """Get pending events without clearing them."""
        with self._lock:
            return self.pending_events.copy()

    def get_job_history(self) -> list:
        """Return recent in-memory events."""
        with self._lock:
            return self.job_history.copy()

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
