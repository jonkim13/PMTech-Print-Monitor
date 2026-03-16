"""
Print Farm Manager
===================
Manages all printers, runs the background polling loop,
tracks job history, detects state transitions, and logs
production data for ISO 9001 traceability.
"""

import os
import time
import threading
import copy
from datetime import datetime, timezone

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
                 production_db=None, snapshots_dir=None):
        self.printers = {}
        self.job_history = []       # in-memory recent events
        self.poll_interval = config.get("poll_interval_sec", 5)
        self.history_db = history_db
        self.filament_db = filament_db
        self.assignment_db = assignment_db
        self.production_db = production_db
        self.snapshots_dir = snapshots_dir
        self._lock = threading.Lock()

        # Track elapsed time per printer for duration logging
        self._print_start_times = {}
        # Track active production job IDs per printer
        self._active_job_ids = {}

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

    def poll_all(self):
        """Poll all printers and detect state changes."""
        for pid, printer_data in self.printers.items():
            client = printer_data["client"]
            prev_status = printer_data["previous_status"]

            state = client.poll()
            new_status = state["status"]

            # Detect state transitions
            if prev_status != new_status:
                event = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "printer_id": pid,
                    "printer_name": state["name"],
                    "from_status": prev_status,
                    "to_status": new_status,
                    "filename": state["job"]["filename"],
                    "duration_sec": 0,
                }

                # Print finished -- this is what triggers the drone
                if new_status == "finished" or (
                    prev_status == "printing" and new_status == "idle"
                ):
                    event["type"] = "print_complete"
                    # Calculate duration
                    start = self._print_start_times.pop(pid, None)
                    if start:
                        event["duration_sec"] = int(
                            (datetime.now(timezone.utc) - start
                             ).total_seconds()
                        )
                    with self._lock:
                        self.pending_events.append(event)
                        self.job_history.append(event)
                    self.history_db.log_event(event)

                    # Auto-deduct filament from assigned spool
                    self._auto_deduct_filament(pid, state)

                    # Production logging: complete job
                    self._production_complete(pid, client, state,
                                              event["duration_sec"])

                    print(f"[EVENT] Print complete on {state['name']}: "
                          f"{state['job']['filename']}")

                elif new_status == "printing" and prev_status != "printing":
                    event["type"] = "print_started"
                    self._print_start_times[pid] = datetime.now(timezone.utc)
                    with self._lock:
                        self.job_history.append(event)
                    self.history_db.log_event(event)

                    # Production logging: create job
                    self._production_start(pid, client, state)

                    print(f"[EVENT] Print started on {state['name']}: "
                          f"{state['job']['filename']}")

                elif new_status in ("error",):
                    event["type"] = "printer_error"
                    with self._lock:
                        self.pending_events.append(event)
                        self.job_history.append(event)
                    self.history_db.log_event(event)

                    # Production logging: fail job
                    self._production_fail(pid, state)

                    print(f"[EVENT] Error on {state['name']}!")

                printer_data["previous_status"] = new_status

    def _auto_deduct_filament(self, printer_id: str, state: dict):
        """Deduct estimated filament usage from the assigned spool."""
        if not self.assignment_db or not self.filament_db:
            return
        assignment = self.assignment_db.get_assignment(printer_id)
        if not assignment:
            return
        spool_id = assignment["spool_id"]

        # PrusaLink may report filament_used_g in the job data
        job = state.get("job", {})
        grams_used = 0

        # Try direct grams first (some firmware versions)
        if job.get("filament_used_g"):
            grams_used = int(float(job["filament_used_g"]))
        # Try converting from mm of filament (assume 1.75mm PLA ~2.98g/m)
        elif job.get("filament_used_mm"):
            mm_used = float(job["filament_used_mm"])
            grams_used = int(mm_used * 0.00298)  # rough PLA estimate

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

            # Get assigned spool info
            spool_id = None
            spool_material = None
            spool_brand = None
            if self.assignment_db and self.filament_db:
                assignment = self.assignment_db.get_assignment(printer_id)
                if assignment:
                    spool_id = assignment["spool_id"]
                    spool = self.filament_db.get_by_id(spool_id)
                    if spool:
                        spool_material = spool.get("material")
                        spool_brand = spool.get("brand")

            job_id = self.production_db.create_job(
                printer_id=printer_id,
                printer_name=state["name"],
                file_name=details.get("file_name",
                                      state["job"]["filename"]),
                file_display_name=details.get("file_display_name",
                                              state["job"]["filename"]),
                filament_type=details.get("filament_type"),
                filament_used_g=details.get("filament_used_g", 0),
                filament_used_mm=details.get("filament_used_mm", 0),
                spool_id=spool_id,
                spool_material=spool_material,
                spool_brand=spool_brand,
                layer_height=details.get("layer_height"),
                nozzle_diameter=details.get("nozzle_diameter"),
                fill_density=details.get("fill_density"),
                nozzle_temp=details.get("nozzle_temp"),
                bed_temp=details.get("bed_temp"),
            )
            self._active_job_ids[printer_id] = job_id

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
                filament_g = details.get("filament_used_g", 0)
                filament_mm = details.get("filament_used_mm", 0)

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

            # Material usage log
            job = self.production_db.get_job(job_id)
            if job and job.get("spool_id"):
                self.production_db.log_material_usage(
                    spool_id=job["spool_id"],
                    job_id=job_id,
                    printer_id=printer_id,
                    grams_used=filament_g or job.get("filament_used_g", 0),
                    mm_used=filament_mm or job.get("filament_used_mm", 0),
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

    def _enrich_with_spool(self, printer_id: str, status: dict) -> dict:
        """Attach assigned spool info to a printer status dict."""
        if not self.assignment_db or not self.filament_db:
            status["assigned_spool"] = None
            return status
        assignment = self.assignment_db.get_assignment(printer_id)
        if assignment:
            spool = self.filament_db.get_by_id(assignment["spool_id"])
            status["assigned_spool"] = spool  # full spool dict or None
        else:
            status["assigned_spool"] = None
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
