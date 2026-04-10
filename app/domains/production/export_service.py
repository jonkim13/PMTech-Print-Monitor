"""
Production Export Service
=========================
CSV export for print jobs, machine log, and material usage.
Queries the job, machine, and material repositories and formats rows
as CSV strings suitable for download.
"""

import csv
import io
import sqlite3


class ExportService:
    """Build CSV exports from the production repositories."""

    JOB_COLUMNS = [
        "job_id", "printer_id", "printer_name", "file_name",
        "file_display_name", "status", "started_at", "completed_at",
        "print_duration_sec", "filament_type", "filament_used_g",
        "filament_used_mm", "filament_used_source", "spool_id",
        "spool_material", "spool_brand", "layer_height",
        "nozzle_diameter", "fill_density", "nozzle_temp", "bed_temp",
        "operator_initials", "operator", "notes", "outcome",
        "tool_spools",
    ]

    MACHINE_COLUMNS = [
        "log_id", "printer_id", "printer_name", "event_type",
        "event_timestamp", "details", "total_print_hours_at_event",
    ]

    MATERIAL_COLUMNS = [
        "usage_id", "spool_id", "job_id", "printer_id",
        "printer_name", "file_name", "grams_used", "mm_used",
        "tool_index", "usage_source", "timestamp",
    ]

    def __init__(self, job_repository, machine_repository,
                 material_repository):
        self.job_repository = job_repository
        self.machine_repository = machine_repository
        self.material_repository = material_repository

    # ------------------------------------------------------------------
    # Exports
    # ------------------------------------------------------------------

    def export_jobs_csv(self, date_from=None, date_to=None):
        """Export print jobs as a CSV string."""
        jobs = self.job_repository.get_jobs(
            date_from=date_from, date_to=date_to,
            limit=100000, offset=0,
        )
        return self._to_csv(jobs, self.JOB_COLUMNS)

    def export_machines_csv(self, date_from=None, date_to=None):
        """Export machine log as a CSV string."""
        logs = self.machine_repository.get_machine_log(
            date_from=date_from, date_to=date_to,
            limit=100000, offset=0,
        )
        return self._to_csv(logs, self.MACHINE_COLUMNS)

    def export_materials_csv(self, date_from=None, date_to=None):
        """Export material usage as a CSV string.

        Joins material_usage with print_jobs to include file_name and
        printer_name on each row.
        """
        conn = sqlite3.connect(self.material_repository.db_path)
        conn.row_factory = sqlite3.Row
        query = """
            SELECT mu.*, pj.file_name, pj.printer_name
            FROM material_usage mu
            LEFT JOIN print_jobs pj ON mu.job_id = pj.job_id
            WHERE 1=1
        """
        params = []
        if date_from:
            query += " AND mu.timestamp >= ?"
            params.append(date_from)
        if date_to:
            query += " AND mu.timestamp <= ?"
            params.append(date_to)
        query += " ORDER BY mu.usage_id DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        data = [dict(r) for r in rows]
        return self._to_csv(data, self.MATERIAL_COLUMNS)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_csv(rows, columns):
        """Convert a list of dicts to a CSV string."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])
        return output.getvalue()
