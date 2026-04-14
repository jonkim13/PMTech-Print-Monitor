"""Production material usage helpers."""

import json as _json

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

from app.domains.monitoring.runtime_state import build_filename_candidates


class ProductionMaterialUsage:
    """Resolve and persist production material usage rows."""

    def __init__(self, material_repository=None, assignment_db=None):
        self.material_repository = material_repository
        self.assignment_db = assignment_db

    def resolve_completion_usage(self, printer_id, client, state, details, job):
        usage = self._resolve_total_usage(state.get("job", {}), details, job)
        grams = usage["grams"]
        mm_used = usage["mm_used"]
        source = usage["source"]
        rows = []
        if getattr(client, "model", "unknown") == "xl":
            rows, xl_grams, xl_mm, xl_source = self._xl_usage(
                printer_id, details, source
            )
            if rows:
                grams, mm_used, source = xl_grams, xl_mm, xl_source
        if (not rows and job and job.get("spool_id") and grams > 0
                and source != FILAMENT_SOURCE_NONE):
            # Attribute the fallback row to the tool that actually holds
            # this spool, not hardcoded tool 0 — XL prints can start on
            # any extruder.
            tool_index = self._resolve_tool_index_for_spool(
                printer_id, job.get("spool_id"), job.get("tool_spools")
            )
            rows.append(self._row(
                job["spool_id"], grams, mm_used, tool_index, source
            ))
        return grams, mm_used, source, rows

    def log_rows(self, job_id, printer_id, rows):
        for row in rows:
            self.material_repository.log_material_usage(
                spool_id=row["spool_id"], job_id=job_id,
                printer_id=printer_id, grams_used=row["grams_used"],
                mm_used=row["mm_used"], tool_index=row["tool_index"],
                usage_source=row["usage_source"],
            )

    def _xl_usage(self, printer_id, details, total_source):
        rows = []
        per_tool_g = details.get("filament_used_g_per_tool", [])
        per_tool_mm = details.get("filament_used_mm_per_tool", [])
        for tidx, g_val in enumerate(per_tool_g or []):
            grams = coerce_positive_float(g_val)
            if grams is None:
                continue
            mm_used = (coerce_nonnegative_float(per_tool_mm[tidx])
                       if tidx < len(per_tool_mm) else 0.0)
            rows.append(self._row(
                self._assigned_spool(printer_id, tidx), grams, mm_used,
                tidx, FILAMENT_SOURCE_API,
            ))
        if rows:
            return (
                rows,
                coerce_positive_float(details.get("filament_used_g"))
                or self._sum_positive_values(per_tool_g),
                coerce_positive_float(details.get("filament_used_mm"))
                or self._sum_positive_values(per_tool_mm),
                FILAMENT_SOURCE_API,
            )
        if total_source in (FILAMENT_SOURCE_API, FILAMENT_SOURCE_FILENAME):
            return [], 0.0, 0.0, total_source
        for tidx, mm_val in enumerate(per_tool_mm or []):
            mm_used = coerce_positive_float(mm_val)
            grams = estimate_grams_from_mm(mm_used)
            if grams is not None:
                rows.append(self._row(
                    self._assigned_spool(printer_id, tidx), grams, mm_used,
                    tidx, FILAMENT_SOURCE_MM_ESTIMATE,
                ))
        return rows, sum(r["grams_used"] for r in rows), sum(
            r["mm_used"] for r in rows), FILAMENT_SOURCE_MM_ESTIMATE

    def _assigned_spool(self, printer_id, tool_index):
        assignment = (
            self.assignment_db.get_assignment(
                printer_id, tool_index=tool_index
            ) if self.assignment_db else None
        )
        return assignment["spool_id"] if assignment else None

    def _resolve_tool_index_for_spool(self, printer_id, spool_id,
                                      tool_spools_json=None):
        """Locate which tool_index is currently (or was) holding spool_id.

        Checks current assignments first, then the tool_spools JSON
        snapshot captured on the print_jobs row at print start, then
        falls back to 0 (logged) so legacy rows stay consistent with
        prior behavior.
        """
        if not spool_id:
            return 0
        if self.assignment_db:
            for row in (self.assignment_db.get_printer_assignments(
                    printer_id) or []):
                if str(row.get("spool_id")) == str(spool_id):
                    return int(row.get("tool_index", 0) or 0)
        if tool_spools_json:
            try:
                data = (_json.loads(tool_spools_json)
                        if isinstance(tool_spools_json, str)
                        else tool_spools_json)
                for tool_idx, info in (data or {}).items():
                    if str((info or {}).get("spool_id")) == str(spool_id):
                        return int(tool_idx)
            except (ValueError, TypeError):
                pass
        print(f"[PRODUCTION] Could not resolve tool_index for spool "
              f"{spool_id} on {printer_id}; defaulting to T1")
        return 0

    @staticmethod
    def _row(spool_id, grams, mm_used, tool_index, source):
        return {"spool_id": spool_id, "grams_used": grams, "mm_used": mm_used,
                "tool_index": tool_index, "usage_source": source}

    @staticmethod
    def _sum_positive_values(values):
        return sum(
            number for number in (coerce_positive_float(v) for v in values or [])
            if number is not None
        )

    @staticmethod
    def _resolve_total_usage(state_job, details=None, production_job=None):
        merged = dict(state_job or {})
        merged.update(details or {})
        return resolve_total_filament_usage(
            filament_used_g=merged.get("filament_used_g"),
            filament_used_mm=merged.get("filament_used_mm"),
            filename_candidates=build_filename_candidates(
                merged.get("file_display_name"), merged.get("file_name"),
                (production_job or {}).get("file_display_name"),
                (production_job or {}).get("file_name"),
                (state_job or {}).get("filename"),
            ),
        )
