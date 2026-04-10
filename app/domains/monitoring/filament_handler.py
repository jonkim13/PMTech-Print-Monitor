"""Filament deduction side effects for print transitions."""

from filament_usage import (
    FILAMENT_SOURCE_API,
    FILAMENT_SOURCE_FILENAME,
    FILAMENT_SOURCE_MM_ESTIMATE,
    coerce_positive_float,
    estimate_grams_from_mm,
    resolve_total_filament_usage,
)

from app.domains.monitoring.runtime_state import build_filename_candidates


class FilamentHandler:
    """Deduct consumed filament from assigned spools."""

    def __init__(self, filament_db=None, assignment_db=None,
                 job_repository=None, runtime_state=None):
        self.filament_db = filament_db
        self.assignment_db = assignment_db
        self.job_repository = job_repository
        self.runtime_state = runtime_state

    def auto_deduct_filament(self, printer_id: str, state: dict, client=None):
        """Deduct estimated filament usage from assigned spools."""
        if not self.assignment_db or not self.filament_db or not client:
            return

        details = client.get_job_details()
        if details.get("error"):
            details = {}
        job = dict(state.get("job", {}))
        job.update(details)
        production_job = self._get_active_production_job_record(printer_id)

        if getattr(client, "model", "unknown") == "xl":
            if self._deduct_xl_usage(printer_id, state, job, production_job):
                return

        assignment = self.assignment_db.get_assignment(
            printer_id, tool_index=0)
        if not assignment:
            return

        usage = self._resolve_total_job_filament_usage(
            state.get("job", {}), details, production_job
        )
        grams_used = usage["grams"]
        if grams_used > 0:
            spool_id = assignment["spool_id"]
            self.filament_db.deduct_weight(spool_id, grams_used)
            print(f"[FILAMENT] Deducted {grams_used:g}g from spool "
                  f"{spool_id} on {state['name']} "
                  f"[source={usage['source']}]")

    def _deduct_xl_usage(self, printer_id, state, job, production_job):
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
                self.filament_db.deduct_weight(assignment["spool_id"], grams)
                print(f"[FILAMENT] Deducted {grams:g}g from spool "
                      f"{assignment['spool_id']} (T{tool_idx + 1}) "
                      f"on {state['name']} [source={FILAMENT_SOURCE_API}]")
                deducted_any = True
        if deducted_any:
            return True

        total_usage = self._resolve_total_job_filament_usage(
            state.get("job", {}), job, production_job,
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
                      f"{state['name']} [source={total_usage['source']}]")
            return True

        for tool_idx, value in enumerate(per_tool_mm or []):
            mm_used = coerce_positive_float(value)
            grams = estimate_grams_from_mm(mm_used)
            if grams is None:
                continue
            assignment = self.assignment_db.get_assignment(
                printer_id, tool_index=tool_idx)
            if assignment:
                self.filament_db.deduct_weight(assignment["spool_id"], grams)
                print(f"[FILAMENT] Deducted {grams:g}g from spool "
                      f"{assignment['spool_id']} (T{tool_idx + 1}) "
                      f"on {state['name']} "
                      f"[source={FILAMENT_SOURCE_MM_ESTIMATE}]")
                deducted_any = True
        return deducted_any

    def _get_active_production_job_record(self, printer_id: str):
        if not self.job_repository or not self.runtime_state:
            return None
        job_id = self.runtime_state.active_job_ids.get(printer_id)
        if job_id:
            return self.job_repository.get_job(job_id)
        return self.job_repository.get_active_job(printer_id)

    @staticmethod
    def _resolve_total_job_filament_usage(state_job: dict,
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
