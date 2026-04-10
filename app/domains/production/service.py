"""
Production Service
==================
Business logic and orchestration for production traceability.
Encapsulates filter/validation rules and delegates persistence to
the job, machine, and material usage repositories.
"""


class ProductionService:
    """Coordinate production reads and QC/maintenance writes."""

    QC_OUTCOMES = ("pass", "fail", "unknown")
    MAINTENANCE_EVENTS = ("maintenance", "calibration")

    def __init__(self, job_repository, machine_repository,
                 material_repository):
        self.job_repository = job_repository
        self.machine_repository = machine_repository
        self.material_repository = material_repository

    # ------------------------------------------------------------------
    # Print Jobs
    # ------------------------------------------------------------------

    def list_jobs(self, printer_id=None, status=None, outcome=None,
                  material=None, date_from=None, date_to=None,
                  limit=100, offset=0):
        """Return jobs matching the given filters."""
        return self.job_repository.get_jobs(
            printer_id=printer_id,
            status=status,
            outcome=outcome,
            material=material,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )

    def get_job(self, job_id):
        """Return a single job by ID, or None if not found."""
        return self.job_repository.get_job(job_id)

    def update_job_qc(self, job_id, outcome=None, operator=None, notes=None):
        """Validate and update QC fields on a job.

        Returns a tuple: (success: bool, error: str | None).
        """
        if outcome is not None and outcome not in self.QC_OUTCOMES:
            return False, "outcome must be pass, fail, or unknown"
        updated = self.job_repository.update_job_qc(
            job_id, outcome=outcome, operator=operator, notes=notes,
        )
        if not updated:
            return False, "Job not found or no changes"
        return True, None

    def get_job_snapshot_path(self, job_id):
        """Return the snapshot file path for a job, or None if unavailable."""
        job = self.job_repository.get_job(job_id)
        if not job:
            return None
        return job.get("snapshot_path") or None

    # ------------------------------------------------------------------
    # Machine Summaries and Log
    # ------------------------------------------------------------------

    def list_machine_summaries(self, printer_ids, printer_name_by_id=None):
        """Return summaries for the given printers, enriched with names.

        printer_name_by_id is an optional dict mapping printer_id → name.
        """
        summaries = self.machine_repository.get_all_machine_summaries(
            printer_ids
        )
        result = []
        name_lookup = printer_name_by_id or {}
        for pid, summary in summaries.items():
            name = name_lookup.get(pid)
            if name:
                summary["printer_name"] = name
            result.append(summary)
        return result

    def get_machine_log(self, printer_id, event_type=None,
                        date_from=None, date_to=None,
                        limit=100, offset=0):
        """Return machine log entries for a printer with filters."""
        return self.machine_repository.get_machine_log(
            printer_id=printer_id,
            event_type=event_type,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )

    def log_maintenance_event(self, printer_id, printer_name,
                              event_type="maintenance", notes=""):
        """Validate and log a maintenance or calibration event.

        Returns a tuple: (success: bool, error: str | None).
        """
        if event_type not in self.MAINTENANCE_EVENTS:
            return False, "event_type must be maintenance or calibration"
        self.machine_repository.log_machine_event(
            printer_id, printer_name, event_type,
            details={"notes": notes or ""},
        )
        return True, None

    # ------------------------------------------------------------------
    # Material Usage
    # ------------------------------------------------------------------

    def get_spool_usage(self, spool_id, limit=100):
        """Return spool usage rows and totals in a single payload."""
        usage = self.material_repository.get_spool_usage(
            spool_id, limit=limit
        )
        totals = self.material_repository.get_spool_totals(spool_id)
        return {"usage": usage, "totals": totals}
