"""Production lifecycle side effects for print transitions."""

import os
from contextlib import nullcontext
from datetime import datetime, timezone

from app.domains.monitoring.production_materials import ProductionMaterialUsage
from app.shared.constants import (
    MachineEventType,
    PrinterStatus,
    ProductionJobStatus,
    QueueItemStatus,
)


class ProductionHandler:
    """Apply production DB lifecycle side effects."""

    def __init__(self, job_repository=None, machine_repository=None,
                 material_repository=None, filament_db=None,
                 assignment_db=None, upload_session_db=None,
                 runtime_state=None, snapshots_dir=None,
                 state_lock=None, queue_handler=None):
        self.job_repository = job_repository
        self.machine_repository = machine_repository
        self.material_repository = material_repository
        self.filament_db = filament_db
        self.assignment_db = assignment_db
        self.upload_session_db = upload_session_db
        self.runtime_state = runtime_state
        self.snapshots_dir = snapshots_dir
        self.state_lock = state_lock
        self.queue_handler = queue_handler
        self.materials = ProductionMaterialUsage(
            material_repository=self.material_repository,
            assignment_db=assignment_db,
        )

    def _locked(self):
        return self.state_lock if self.state_lock else nullcontext()

    def _starts(self):
        return self.runtime_state.print_start_times if self.runtime_state else {}

    def _active_jobs(self):
        return self.runtime_state.active_job_ids if self.runtime_state else {}

    def start(self, printer_id, client, state):
        """Log a print start to the production database."""
        if not self.job_repository:
            return
        try:
            details = self._job_details(client)
            pending_start, upload_session_id, upload_session = (
                self._start_context(printer_id, state)
            )
            file_name, display_name = self._job_names(
                state, details, upload_session
            )
            operator_initials = self._operator_initials(
                pending_start, upload_session
            )
            spool_id, spool_material, spool_brand = self._primary_spool(
                printer_id, details
            )
            job_id = self.job_repository.create_job(
                printer_id=printer_id, printer_name=state["name"],
                file_name=file_name, file_display_name=display_name,
                filament_type=details.get("filament_type"),
                filament_used_g=float(details.get("filament_used_g") or 0),
                filament_used_mm=float(details.get("filament_used_mm") or 0),
                spool_id=spool_id, spool_material=spool_material,
                spool_brand=spool_brand,
                layer_height=details.get("layer_height"),
                nozzle_diameter=details.get("nozzle_diameter"),
                fill_density=details.get("fill_density"),
                nozzle_temp=details.get("nozzle_temp"),
                bed_temp=details.get("bed_temp"),
                tool_spools=self._tool_spools(printer_id) or None,
                operator_initials=operator_initials,
            )
            self._active_jobs()[printer_id] = job_id
            self._mark_upload_session_printing(
                upload_session_id, operator_initials
            )
            if self.queue_handler:
                self.queue_handler.link_print_job_on_start(
                    printer_id, state, job_id, pending_start, upload_session
                )
            if pending_start:
                remote_filename = (
                    upload_session.get("remote_filename")
                    if upload_session else state["job"]["filename"]
                )
                self._clear_pending_print_start(
                    printer_id, upload_session_id, remote_filename
                )
            self.machine_repository.log_machine_event(
                printer_id, state["name"], MachineEventType.PRINT_START,
                details={"job_id": job_id, "file": state["job"]["filename"]},
            )
            print(f"[PRODUCTION] Job #{job_id} created for {state['name']}")
        except Exception as exc:
            print(f"[PRODUCTION] Error logging start: {exc}")

    def complete(self, printer_id, client, state, duration_sec):
        """Log a print completion to the production database."""
        if not self.job_repository:
            return
        job_id = self._active_jobs().pop(printer_id, None)
        if not job_id:
            return
        try:
            details = self._job_details(client)
            job = self.job_repository.get_job(job_id)
            filament_g, filament_mm, filament_source, material_rows = (
                self.materials.resolve_completion_usage(
                    printer_id, client, state, details, job
                )
            )
            self.job_repository.complete_job(
                job_id, duration_sec=duration_sec,
                filament_used_g=filament_g, filament_used_mm=filament_mm,
                filament_used_source=filament_source,
                snapshot_path=self._save_completion_snapshot(printer_id, client),
            )
            self.materials.log_rows(job_id, printer_id, material_rows)
            self.machine_repository.log_machine_event(
                printer_id, state["name"], MachineEventType.PRINT_COMPLETE,
                details={"job_id": job_id, "duration_sec": duration_sec},
            )
            print(f"[PRODUCTION] Job #{job_id} completed")
        except Exception as exc:
            print(f"[PRODUCTION] Error logging completion: {exc}")

    def fail(self, printer_id, state):
        """Log a print failure to the production database."""
        if not self.job_repository:
            return
        self._close_in_production(
            printer_id, state, self.job_repository.fail_job,
            MachineEventType.PRINT_FAIL, ProductionJobStatus.FAILED,
            {"error": state.get("error", PrinterStatus.UNKNOWN)},
            error_label="failure",
        )

    def stop(self, printer_id, state, duration_sec=0):
        """Log an operator stop to the production database."""
        if not self.job_repository:
            return
        self._close_in_production(
            printer_id, state, self.job_repository.stop_job,
            MachineEventType.PRINT_STOP, ProductionJobStatus.STOPPED,
            {"file": state.get("job", {}).get("filename")},
            duration_sec=duration_sec, error_label="stop",
        )

    def _close_in_production(self, printer_id, state, close_job, event_type,
                             label, details, duration_sec=0,
                             error_label=None):
        if not self.job_repository:
            return
        job_id = self._active_jobs().pop(printer_id, None)
        if not job_id:
            return
        try:
            duration = duration_sec or self._duration_since_start(printer_id)
            close_job(job_id, duration_sec=duration)
            event_details = {"job_id": job_id}
            event_details.update(details)
            self.machine_repository.log_machine_event(
                printer_id, state["name"], event_type, details=event_details,
            )
            print(f"[PRODUCTION] Job #{job_id} {label}")
        except Exception as exc:
            print(f"[PRODUCTION] Error logging {error_label or label}: {exc}")

    def _start_context(self, printer_id, state):
        pending_start = self._get_pending_print_start_entry(
            printer_id, state["job"]["filename"]
        )
        upload_session_id = (
            pending_start.get("upload_session_id") if pending_start else None
        )
        upload_session = (
            self.upload_session_db.get_session(upload_session_id)
            if self.upload_session_db and upload_session_id else None
        )
        return pending_start, upload_session_id, upload_session

    def _primary_spool(self, printer_id, details=None):
        """Pick the spool that goes on the `print_jobs` primary columns.

        XL prints can run on any extruder, not just tool 0. We use the
        gcode per-tool filament arrays (exposed in the job meta) to find
        the active tool; if multiple tools are active we leave the
        primary spool unset so completion-time per-tool rows in
        `material_usage` remain authoritative and `tool_spools` (the
        full per-tool snapshot) carries traceability. Single-tool
        printers keep their existing behavior via the only-one-assigned
        branch.
        """
        if not self.assignment_db or not self.filament_db:
            return None, None, None
        tool_index = self._active_print_tool(printer_id, details)
        if tool_index is None:
            print(f"[PRODUCTION] {printer_id}: multi-tool print detected, "
                  f"primary spool deferred to per-tool usage rows")
            return None, None, None
        assignment = self.assignment_db.get_assignment(
            printer_id, tool_index=tool_index)
        if not assignment:
            return None, None, None
        spool = self.filament_db.get_by_id(assignment["spool_id"])
        print(f"[PRODUCTION] {printer_id}: primary spool "
              f"{assignment['spool_id']} (T{tool_index + 1})")
        return (
            assignment["spool_id"],
            spool.get("material") if spool else None,
            spool.get("brand") if spool else None,
        )

    def _active_print_tool(self, printer_id, details):
        """Return the tool index consuming filament, or None if ambiguous.

        Uses the PrusaLink /api/v1/job meta (`filament_used_g_per_tool`
        / `filament_used_mm_per_tool`) to identify the single active
        extruder for a print. If the meta lists multiple nonzero tools,
        the print is multi-tool and no single primary can be picked.
        If the meta is empty but exactly one tool has a spool assigned,
        that tool is the primary. Otherwise falls back to tool 0 for
        backward compatibility.
        """
        details = details or {}
        active = self._nonzero_indices(
            details.get("filament_used_g_per_tool")
        )
        if not active:
            active = self._nonzero_indices(
                details.get("filament_used_mm_per_tool")
            )
        if len(active) == 1:
            return active[0]
        if len(active) > 1:
            return None
        assignments = self.assignment_db.get_printer_assignments(
            printer_id) or []
        if len(assignments) == 1:
            return int(assignments[0].get("tool_index", 0) or 0)
        return 0

    @staticmethod
    def _nonzero_indices(values):
        indices = []
        for idx, val in enumerate(values or []):
            try:
                if float(val) > 0:
                    indices.append(idx)
            except (TypeError, ValueError):
                continue
        return indices

    def _tool_spools(self, printer_id):
        if not self.assignment_db or not self.filament_db:
            return {}
        result = {}
        for assignment in self.assignment_db.get_printer_assignments(printer_id):
            spool = self.filament_db.get_by_id(assignment["spool_id"])
            result[assignment["tool_index"]] = {
                "spool_id": assignment["spool_id"],
                "material": spool.get("material") if spool else None,
                "brand": spool.get("brand") if spool else None,
                "color": spool.get("color") if spool else None,
            }
        return result

    def _get_pending_print_start_entry(self, printer_id, file_name=None):
        if not self.runtime_state or not printer_id:
            return None
        with self._locked():
            self.runtime_state.prune_pending_print_starts()
            return self.runtime_state.match_pending_print_start(
                printer_id, file_name=file_name
            )

    def _clear_pending_print_start(self, printer_id, upload_session_id=None,
                                   remote_filename=None):
        if self.runtime_state:
            with self._locked():
                self.runtime_state.clear_pending_print_start(
                    printer_id, upload_session_id=upload_session_id,
                    remote_filename=remote_filename,
                )

    @staticmethod
    def _job_details(client):
        details = client.get_job_details()
        return {} if details.get("error") else details

    @staticmethod
    def _job_names(state, details, upload_session):
        file_name = details.get("file_name", state["job"]["filename"])
        display_name = details.get(
            "file_display_name", state["job"]["filename"]
        )
        if upload_session:
            file_name = upload_session.get("remote_filename") or file_name
            display_name = upload_session.get("original_filename") or display_name
        return file_name, display_name

    @staticmethod
    def _operator_initials(pending_start, upload_session):
        initials = pending_start.get("operator_initials") if pending_start else None
        return initials or (
            upload_session.get("operator_initials") if upload_session else None
        )

    def _mark_upload_session_printing(self, upload_session_id,
                                      operator_initials):
        if self.upload_session_db and upload_session_id:
            self.upload_session_db.set_status(
                upload_session_id, QueueItemStatus.PRINTING, last_error=None,
                operator_initials=operator_initials, completed=True,
            )

    def _save_completion_snapshot(self, printer_id, client):
        if not self.snapshots_dir:
            return None
        try:
            snap_data = client.get_camera_snapshot()
            if snap_data:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{printer_id}_{timestamp}.png"
                snapshot_path = os.path.join(self.snapshots_dir, filename)
                with open(snapshot_path, "wb") as output:
                    output.write(snap_data)
                print(f"[PRODUCTION] Snapshot saved: {filename}")
                return snapshot_path
        except Exception as exc:
            print(f"[PRODUCTION] Snapshot failed: {exc}")
        return None

    def _duration_since_start(self, printer_id):
        start = self._starts().get(printer_id)
        return int((datetime.now(timezone.utc) - start).total_seconds()) if start else 0
