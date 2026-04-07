"""Transition side-effect orchestration for monitoring poll events."""

import os
from contextlib import nullcontext
from datetime import datetime, timezone

from filament_usage import (
    FILAMENT_SOURCE_API,
    FILAMENT_SOURCE_FILENAME,
    FILAMENT_SOURCE_MM_ESTIMATE,
    FILAMENT_SOURCE_NONE,
    coerce_nonnegative_float,
    coerce_positive_float,
    estimate_grams_from_mm,
    resolve_total_filament_usage,
)

from app.domains.monitoring.runtime_state import (
    build_filename_candidates,
    normalize_print_filename,
)
from app.shared.constants import (
    EventType,
    MachineEventType,
    PrinterStatus,
    QueueItemStatus,
)


class TransitionHandler:
    """Apply side effects for printer status transitions."""

    def __init__(self, history_db=None, production_db=None,
                 work_order_db=None, filament_db=None, assignment_db=None,
                 upload_session_db=None, event_service=None,
                 runtime_state=None, snapshots_dir=None, state_lock=None):
        self.history_db = history_db
        self.production_db = production_db
        self.work_order_db = work_order_db
        self.filament_db = filament_db
        self.assignment_db = assignment_db
        self.upload_session_db = upload_session_db
        self.event_service = event_service
        self.runtime_state = runtime_state
        self.snapshots_dir = snapshots_dir
        self.state_lock = state_lock

    def _locked(self):
        return self.state_lock if self.state_lock else nullcontext()

    def _print_start_times(self):
        return self.runtime_state.print_start_times if self.runtime_state else {}

    def _active_job_ids(self):
        return self.runtime_state.active_job_ids if self.runtime_state else {}

    def _active_queue_job_ids(self):
        return (
            self.runtime_state.active_queue_job_ids
            if self.runtime_state else {}
        )

    # ------------------------------------------------------------------
    # Transition Dispatch
    # ------------------------------------------------------------------

    def handle_print_started(self, printer_id, printer_name, state, client,
                             event):
        """Handle a printer entering the printing state."""
        event["type"] = EventType.PRINT_STARTED
        self._print_start_times()[printer_id] = datetime.now(timezone.utc)

        self._record_transition_event(event, add_pending=False)
        self.production_start(printer_id, client, state)
        print(f"[EVENT] Print started on {printer_name}: "
              f"{state['job']['filename']}")

    def handle_print_completed(self, printer_id, printer_name, state, client,
                               event):
        """Handle a printer completing a print."""
        event["type"] = EventType.PRINT_COMPLETE
        start = self._print_start_times().pop(printer_id, None)
        if start:
            event["duration_sec"] = int(
                (datetime.now(timezone.utc) - start).total_seconds()
            )

        self._record_transition_event(event, add_pending=True)
        self.auto_deduct_filament(printer_id, state, client)
        self.production_complete(
            printer_id, client, state, event["duration_sec"]
        )
        self.work_order_complete(printer_id, state)
        print(f"[EVENT] Print complete on {printer_name}: "
              f"{state['job']['filename']}")

    def handle_print_failed(self, printer_id, printer_name, state, client,
                            event):
        """Handle a printer entering an error state."""
        event["type"] = EventType.PRINTER_ERROR

        self._record_transition_event(event, add_pending=True)
        self.production_fail(printer_id, state)
        self.work_order_fail(printer_id, state)
        print(f"[EVENT] Error on {printer_name}!")

    def handle_print_stopped(self, printer_id, printer_name, state, client,
                             event):
        """Handle an operator-stopped print without completion side effects."""
        event["type"] = EventType.PRINT_STOPPED
        start = self._print_start_times().pop(printer_id, None)
        if start:
            event["duration_sec"] = int(
                (datetime.now(timezone.utc) - start).total_seconds()
            )

        self._record_transition_event(event, add_pending=True)
        self.production_stop(printer_id, state, event["duration_sec"])
        self.work_order_fail(printer_id, state)
        print(f"[EVENT] Print stopped on {printer_name}: "
              f"{state['job']['filename']}")

    # ------------------------------------------------------------------
    # Event Logging
    # ------------------------------------------------------------------

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
                        and existing.get("filename") == event.get("filename")):
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

    # ------------------------------------------------------------------
    # Filament Usage
    # ------------------------------------------------------------------

    def auto_deduct_filament(self, printer_id: str, state: dict, client=None):
        """Deduct estimated filament usage from assigned spools."""
        if not self.assignment_db or not self.filament_db or not client:
            return

        model = self._get_printer_model(client)
        details = client.get_job_details()
        if details.get("error"):
            details = {}
        job = dict(state.get("job", {}))
        job.update(details)
        production_job = self._get_active_production_job_record(printer_id)

        if model == "xl":
            per_tool_g = job.get("filament_used_g_per_tool", [])
            per_tool_mm = job.get("filament_used_mm_per_tool", [])
            deducted_any = False

            for tool_idx, value in enumerate(per_tool_g or []):
                grams = coerce_positive_float(value)
                if grams is None:
                    continue
                assignment = self.assignment_db.get_assignment(
                    printer_id, tool_index=tool_idx)
                if assignment:
                    self.filament_db.deduct_weight(
                        assignment["spool_id"], grams)
                    print(f"[FILAMENT] Deducted {grams:g}g from spool "
                          f"{assignment['spool_id']} (T{tool_idx + 1}) "
                          f"on {state['name']} [source={FILAMENT_SOURCE_API}]")
                    deducted_any = True

            if deducted_any:
                return

            total_usage = self._resolve_total_job_filament_usage(
                state.get("job", {}),
                details,
                production_job,
                include_mm_estimate=False,
            )
            if total_usage["source"] in (
                FILAMENT_SOURCE_API,
                FILAMENT_SOURCE_FILENAME,
            ):
                assignment = self.assignment_db.get_assignment(
                    printer_id, tool_index=0)
                if assignment:
                    self.filament_db.deduct_weight(
                        assignment["spool_id"], total_usage["grams"])
                    print(f"[FILAMENT] Deducted {total_usage['grams']:g}g "
                          f"from spool {assignment['spool_id']} on "
                          f"{state['name']} "
                          f"[source={total_usage['source']}]")
                return

            for tool_idx, value in enumerate(per_tool_mm or []):
                mm_used = coerce_positive_float(value)
                grams = estimate_grams_from_mm(mm_used)
                if grams is None:
                    continue
                assignment = self.assignment_db.get_assignment(
                    printer_id, tool_index=tool_idx)
                if assignment:
                    self.filament_db.deduct_weight(
                        assignment["spool_id"], grams)
                    print(f"[FILAMENT] Deducted {grams:g}g from spool "
                          f"{assignment['spool_id']} (T{tool_idx + 1}) "
                          f"on {state['name']} "
                          f"[source={FILAMENT_SOURCE_MM_ESTIMATE}]")
                    deducted_any = True

            if deducted_any:
                return

        assignment = self.assignment_db.get_assignment(
            printer_id, tool_index=0)
        if not assignment:
            return
        spool_id = assignment["spool_id"]

        usage = self._resolve_total_job_filament_usage(
            state.get("job", {}),
            details,
            production_job,
        )
        grams_used = usage["grams"]
        if grams_used > 0:
            self.filament_db.deduct_weight(spool_id, grams_used)
            print(f"[FILAMENT] Deducted {grams_used:g}g from spool "
                  f"{spool_id} on {state['name']} "
                  f"[source={usage['source']}]")

    # ------------------------------------------------------------------
    # Production Logging
    # ------------------------------------------------------------------

    def production_start(self, printer_id, client, state):
        """Log a print start to the production database."""
        if not self.production_db:
            return
        try:
            details = client.get_job_details()
            if details.get("error"):
                details = {}

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

            tool_spools = {}
            if self.assignment_db and self.filament_db:
                assignments = self.assignment_db.get_printer_assignments(
                    printer_id)
                for assignment in assignments:
                    spool = self.filament_db.get_by_id(
                        assignment["spool_id"])
                    tool_spools[assignment["tool_index"]] = {
                        "spool_id": assignment["spool_id"],
                        "material": spool.get("material") if spool else None,
                        "brand": spool.get("brand") if spool else None,
                        "color": spool.get("color") if spool else None,
                    }

            pending_start = self._get_pending_print_start_entry(
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
            self._active_job_ids()[printer_id] = job_id

            if self.upload_session_db and upload_session_id:
                self.upload_session_db.set_status(
                    upload_session_id,
                    QueueItemStatus.PRINTING,
                    last_error=None,
                    operator_initials=operator_initials,
                    completed=True,
                )

            self._link_queue_job_on_start(
                printer_id,
                state,
                job_id,
                pending_start,
                upload_session,
            )

            if pending_start:
                self._clear_pending_print_start(
                    printer_id,
                    upload_session_id=upload_session_id,
                    remote_filename=(
                        upload_session.get("remote_filename")
                        if upload_session else state["job"]["filename"]
                    ),
                )

            self.production_db.log_machine_event(
                printer_id, state["name"], MachineEventType.PRINT_START,
                details={"job_id": job_id,
                         "file": state["job"]["filename"]},
            )
            print(f"[PRODUCTION] Job #{job_id} created for "
                  f"{state['name']}")
        except Exception as exc:
            print(f"[PRODUCTION] Error logging start: {exc}")

    def production_complete(self, printer_id, client, state, duration_sec):
        """Log a print completion to the production database."""
        if not self.production_db:
            return
        job_id = self._active_job_ids().pop(printer_id, None)
        if not job_id:
            return
        try:
            details = client.get_job_details()
            if details.get("error"):
                details = {}
            job = self.production_db.get_job(job_id)
            model = self._get_printer_model(client)

            total_usage = self._resolve_total_job_filament_usage(
                state.get("job", {}),
                details,
                job,
            )
            filament_g = total_usage["grams"]
            filament_mm = total_usage["mm_used"]
            filament_source = total_usage["source"]
            material_usage_rows = []

            per_tool_g = details.get("filament_used_g_per_tool", [])
            per_tool_mm = details.get("filament_used_mm_per_tool", [])

            if model == "xl":
                for tidx, g_val in enumerate(per_tool_g or []):
                    grams = coerce_positive_float(g_val)
                    if grams is None:
                        continue
                    mm_used = (coerce_nonnegative_float(per_tool_mm[tidx])
                               if tidx < len(per_tool_mm) else 0.0)
                    assignment = (
                        self.assignment_db.get_assignment(
                            printer_id, tool_index=tidx
                        ) if self.assignment_db else None
                    )
                    material_usage_rows.append({
                        "spool_id": assignment["spool_id"]
                        if assignment else None,
                        "grams_used": grams,
                        "mm_used": mm_used,
                        "tool_index": tidx,
                        "usage_source": FILAMENT_SOURCE_API,
                    })

                if material_usage_rows:
                    filament_g = (
                        coerce_positive_float(details.get("filament_used_g"))
                        or self._sum_positive_values(per_tool_g)
                    )
                    filament_mm = (
                        coerce_positive_float(details.get("filament_used_mm"))
                        or self._sum_positive_values(per_tool_mm)
                    )
                    filament_source = FILAMENT_SOURCE_API
                elif filament_source not in (
                    FILAMENT_SOURCE_API,
                    FILAMENT_SOURCE_FILENAME,
                ):
                    for tidx, mm_val in enumerate(per_tool_mm or []):
                        mm_used = coerce_positive_float(mm_val)
                        grams = estimate_grams_from_mm(mm_used)
                        if grams is None:
                            continue
                        assignment = (
                            self.assignment_db.get_assignment(
                                printer_id, tool_index=tidx
                            ) if self.assignment_db else None
                        )
                        material_usage_rows.append({
                            "spool_id": assignment["spool_id"]
                            if assignment else None,
                            "grams_used": grams,
                            "mm_used": mm_used,
                            "tool_index": tidx,
                            "usage_source": FILAMENT_SOURCE_MM_ESTIMATE,
                        })
                    if material_usage_rows:
                        filament_g = sum(
                            row["grams_used"] for row in material_usage_rows
                        )
                        filament_mm = sum(
                            row["mm_used"] for row in material_usage_rows
                        )
                        filament_source = FILAMENT_SOURCE_MM_ESTIMATE

            snapshot_path = self._save_completion_snapshot(
                printer_id, client)

            self.production_db.complete_job(
                job_id, duration_sec=duration_sec,
                filament_used_g=filament_g,
                filament_used_mm=filament_mm,
                filament_used_source=filament_source,
                snapshot_path=snapshot_path,
            )

            if (not material_usage_rows and job and job.get("spool_id")
                    and filament_g > 0
                    and filament_source != FILAMENT_SOURCE_NONE):
                material_usage_rows.append({
                    "spool_id": job["spool_id"],
                    "grams_used": filament_g,
                    "mm_used": filament_mm,
                    "tool_index": 0,
                    "usage_source": filament_source,
                })

            for row in material_usage_rows:
                self.production_db.log_material_usage(
                    spool_id=row["spool_id"],
                    job_id=job_id,
                    printer_id=printer_id,
                    grams_used=row["grams_used"],
                    mm_used=row["mm_used"],
                    tool_index=row["tool_index"],
                    usage_source=row["usage_source"],
                )

            self.production_db.log_machine_event(
                printer_id, state["name"], MachineEventType.PRINT_COMPLETE,
                details={"job_id": job_id, "duration_sec": duration_sec},
            )
            print(f"[PRODUCTION] Job #{job_id} completed")
        except Exception as exc:
            print(f"[PRODUCTION] Error logging completion: {exc}")

    def production_fail(self, printer_id, state):
        """Log a print failure to the production database."""
        if not self.production_db:
            return
        job_id = self._active_job_ids().pop(printer_id, None)
        if not job_id:
            return
        try:
            start = self._print_start_times().get(printer_id)
            duration = 0
            if start:
                duration = int(
                    (datetime.now(timezone.utc) - start).total_seconds())

            self.production_db.fail_job(job_id, duration_sec=duration)
            self.production_db.log_machine_event(
                printer_id, state["name"], MachineEventType.PRINT_FAIL,
                details={"job_id": job_id,
                         "error": state.get("error", PrinterStatus.UNKNOWN)},
            )
            print(f"[PRODUCTION] Job #{job_id} failed")
        except Exception as exc:
            print(f"[PRODUCTION] Error logging failure: {exc}")

    def production_stop(self, printer_id, state, duration_sec=0):
        """Log an operator stop to the production database."""
        if not self.production_db:
            return
        job_id = self._active_job_ids().pop(printer_id, None)
        if not job_id:
            return
        try:
            if not duration_sec:
                start = self._print_start_times().get(printer_id)
                if start:
                    duration_sec = int(
                        (datetime.now(timezone.utc) - start).total_seconds()
                    )

            self.production_db.stop_job(job_id, duration_sec=duration_sec)
            self.production_db.log_machine_event(
                printer_id, state["name"], MachineEventType.PRINT_STOP,
                details={"job_id": job_id,
                         "file": state.get("job", {}).get("filename")},
            )
            print(f"[PRODUCTION] Job #{job_id} stopped")
        except Exception as exc:
            print(f"[PRODUCTION] Error logging stop: {exc}")

    # ------------------------------------------------------------------
    # Work Order Queue Integration
    # ------------------------------------------------------------------

    def work_order_complete(self, printer_id, state):
        """Auto-complete a work order queue item when a print finishes."""
        if not self.work_order_db:
            return
        filename = state.get("job", {}).get("filename", "")
        queue_job_id = self._active_queue_job_ids().pop(printer_id, None)
        if queue_job_id:
            try:
                queue_job = self.work_order_db.get_queue_job(queue_job_id)
                queued_file = normalize_print_filename(
                    queue_job.get("gcode_file") if queue_job else ""
                )
                current_file = normalize_print_filename(filename)
                if (queue_job
                        and queue_job.get("status") == QueueItemStatus.PRINTING
                        and (not queued_file or not current_file
                             or queued_file == current_file)):
                    if self.work_order_db.complete_queue_job(queue_job_id):
                        print(f"[WORKORDER] Queue job #{queue_job_id} "
                              "completed")
                        return
            except Exception as exc:
                print(f"[WORKORDER] Error completing queue job: {exc}")
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

            queue_item = self.work_order_db.find_printing_item_by_filename(
                printer_id, filename)
            if queue_item:
                job_id = self._active_job_ids().get(printer_id)
                self.work_order_db.complete_queue_item(
                    queue_item["queue_id"], print_job_id=job_id)
                print(f"[WORKORDER] Queue item #{queue_item['queue_id']} "
                      f"completed ({queue_item['part_name']} "
                      f"{queue_item['sequence_number']}/"
                      f"{queue_item['total_quantity']} "
                      f"for {queue_item['customer_name']})")
        except Exception as exc:
            print(f"[WORKORDER] Error completing queue item: {exc}")

    def work_order_fail(self, printer_id, state):
        """Auto-fail a work order queue item when a printer errors."""
        if not self.work_order_db:
            return
        filename = state.get("job", {}).get("filename", "")
        queue_job_id = self._active_queue_job_ids().pop(printer_id, None)
        if queue_job_id:
            try:
                queue_job = self.work_order_db.get_queue_job(queue_job_id)
                queued_file = normalize_print_filename(
                    queue_job.get("gcode_file") if queue_job else ""
                )
                current_file = normalize_print_filename(filename)
                if (queue_job
                        and queue_job.get("status") == QueueItemStatus.PRINTING
                        and (not queued_file or not current_file
                             or queued_file == current_file)):
                    if self.work_order_db.fail_queue_job(queue_job_id):
                        print(f"[WORKORDER] Queue job #{queue_job_id} failed")
                        return
            except Exception as exc:
                print(f"[WORKORDER] Error failing queue job: {exc}")
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

            queue_item = self.work_order_db.find_printing_item_by_filename(
                printer_id, filename)
            if queue_item:
                self.work_order_db.fail_queue_item(queue_item["queue_id"])
                print(f"[WORKORDER] Queue item #{queue_item['queue_id']} "
                      f"failed ({queue_item['part_name']})")
        except Exception as exc:
            print(f"[WORKORDER] Error failing queue item: {exc}")

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_printer_model(client) -> str:
        return getattr(client, "model", "unknown") if client else "unknown"

    @staticmethod
    def _sum_positive_values(values):
        total = 0.0
        for value in values or []:
            number = coerce_positive_float(value)
            if number is not None:
                total += number
        return total

    def _get_active_production_job_record(self, printer_id: str):
        if not self.production_db:
            return None
        job_id = self._active_job_ids().get(printer_id)
        if job_id:
            return self.production_db.get_job(job_id)
        return self.production_db.get_active_job(printer_id)

    def _resolve_total_job_filament_usage(self, state_job: dict,
                                          details: dict = None,
                                          production_job: dict = None,
                                          include_mm_estimate: bool = True):
        merged = dict(state_job or {})
        merged.update(details or {})
        return resolve_total_filament_usage(
            filament_used_g=merged.get("filament_used_g"),
            filament_used_mm=merged.get("filament_used_mm"),
            filename_candidates=build_filename_candidates(
                merged.get("file_display_name"),
                merged.get("file_name"),
                (production_job or {}).get("file_display_name"),
                (production_job or {}).get("file_name"),
                (state_job or {}).get("filename"),
            ),
            include_mm_estimate=include_mm_estimate,
        )

    def _get_pending_print_start_entry(self, printer_id: str,
                                       file_name: str = None,
                                       upload_session_id: str = None):
        if not self.runtime_state or not printer_id:
            return None
        with self._locked():
            self.runtime_state.prune_pending_print_starts()
            return self.runtime_state.match_pending_print_start(
                printer_id,
                file_name=file_name,
                upload_session_id=upload_session_id,
            )

    def _clear_pending_print_start(self, printer_id: str,
                                   upload_session_id: str = None,
                                   remote_filename: str = None):
        if not self.runtime_state:
            return
        with self._locked():
            self.runtime_state.clear_pending_print_start(
                printer_id,
                upload_session_id=upload_session_id,
                remote_filename=remote_filename,
            )

    def _link_queue_job_on_start(self, printer_id, state, job_id,
                                 pending_start=None, upload_session=None):
        if not self.work_order_db:
            return

        queue_job = None
        pending_queue_job_id = (
            pending_start.get("queue_job_id") if pending_start else None
        )
        if pending_queue_job_id:
            queue_job = self.work_order_db.get_queue_job(pending_queue_job_id)
            if queue_job and queue_job.get("status") not in (
                QueueItemStatus.UPLOADING,
                QueueItemStatus.UPLOADED,
                QueueItemStatus.STARTING,
                QueueItemStatus.PRINTING,
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
            self._active_queue_job_ids()[printer_id] = (
                queue_job["queue_job_id"]
            )
            self.work_order_db.link_print_job_to_queue_job(
                queue_job["queue_job_id"], job_id
            )
        else:
            self._active_queue_job_ids().pop(printer_id, None)

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
