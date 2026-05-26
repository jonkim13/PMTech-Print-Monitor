"""Work order business logic."""

from datetime import datetime, timezone
from typing import Optional

from app.domains.work_orders import status_sync


_VALID_JOB_TYPES = ("Internal", "External", "Design")


class WorkOrderService:
    """Orchestrates work-order and job operations."""

    def __init__(self, *,
                 work_order_repository,
                 job_repository,
                 queue_repository=None,
                 queue_bulk_operations=None,
                 queue_execution_repository=None,
                 farm_manager=None,
                 production_job_repository=None):
        self.work_order_repository = work_order_repository
        self.job_repository = job_repository
        self.queue_repository = queue_repository
        self.queue_bulk_operations = queue_bulk_operations
        self.queue_execution_repository = queue_execution_repository
        self.farm_manager = farm_manager
        self.production_job_repository = production_job_repository

    # ------------------------------------------------------------------
    # Work Orders
    # ------------------------------------------------------------------

    def create_work_order(self, customer_name: str, line_items: list,
                          due_date: Optional[str] = None) -> dict:
        return self.work_order_repository.create_work_order(
            customer_name, line_items, due_date=due_date
        )

    def get_work_orders(self, status: Optional[str] = None,
                        limit: int = 100, offset: int = 0) -> list:
        return self.work_order_repository.get_all_work_orders(
            status=status, limit=limit, offset=offset
        )

    def get_work_order(self, wo_id: str) -> Optional[dict]:
        wo = self.work_order_repository.get_work_order(wo_id)
        if wo is not None:
            self._attach_production_outcome(wo)
            self._attach_inspection_summaries(wo)
            self._attach_normalized_counts(wo)
            self._attach_activity_timeline(wo)
        return wo

    # ------------------------------------------------------------------
    # Phase 2.5c enrichment helpers
    # ------------------------------------------------------------------

    def _attach_inspection_summaries(self, wo: dict) -> None:
        """Aggregate per-job inspection counts onto each job in wo['jobs'].

        For Internal jobs, each member queue_item carries a
        ``production_outcome`` populated by _attach_production_outcome.
        We aggregate per job_id and emit:
            inspection: {passed, failed, pending, total, inspector, state}
        """
        queue_items = wo.get("queue_items") or []
        by_job: dict = {}
        for qi in queue_items:
            jid = qi.get("job_id")
            if jid is None:
                continue
            bucket = by_job.setdefault(jid, {
                "passed": 0, "failed": 0, "pending": 0, "total": 0,
                "operators": {},
            })
            if qi.get("status") != "completed":
                continue
            bucket["total"] += 1
            outcome = qi.get("production_outcome")
            if outcome == "pass":
                bucket["passed"] += 1
            elif outcome == "fail":
                bucket["failed"] += 1
            else:
                bucket["pending"] += 1
            op = qi.get("production_operator") or qi.get(
                "queue_job_operator_initials"
            )
            if op:
                bucket["operators"][op] = bucket["operators"].get(op, 0) + 1

        for job in wo.get("jobs") or []:
            bucket = by_job.get(job.get("job_id"), {
                "passed": 0, "failed": 0, "pending": 0, "total": 0,
                "operators": {},
            })
            inspector = None
            if bucket["operators"]:
                inspector = max(
                    bucket["operators"].items(), key=lambda x: x[1]
                )[0]
            if bucket["failed"] > 0:
                state = "failed"
            elif bucket["total"] > 0 and bucket["passed"] == bucket["total"]:
                state = "passed"
            elif bucket["total"] > 0:
                state = "in-progress"
            else:
                state = "pending"
            job["inspection"] = {
                "passed": bucket["passed"],
                "failed": bucket["failed"],
                "pending": bucket["pending"],
                "total": bucket["total"],
                "inspector": inspector,
                "state": state,
            }
            # Phase C — surface the discriminator as a lowercase
            # template-friendly `type`. `or 'Internal'` defends
            # against rows that somehow lack the column (legacy DBs
            # that haven't picked up the mirror yet).
            job["type"] = (job.get("job_type") or "Internal").lower()

    def _attach_normalized_counts(self, wo: dict) -> None:
        """Emit the design's `counts` block for the stacked progress bar.

        Keys: total, done, printing, queued, failed, pending, in_transit.
        Sourced from queue_items statuses + production_outcome.
        """
        queue_items = wo.get("queue_items") or []
        counts = {
            "total": len(queue_items),
            "done": 0,
            "printing": 0,
            "queued": 0,
            "failed": 0,
            "pending": 0,    # completed but outcome=unknown (awaiting QC)
            "in_transit": 0,  # Phase B (External jobs)
        }
        for qi in queue_items:
            status = qi.get("status")
            if status == "completed":
                counts["done"] += 1
                if (qi.get("production_outcome") or "unknown") == "unknown":
                    counts["pending"] += 1
            elif status in ("printing", "starting", "uploading", "uploaded"):
                counts["printing"] += 1
            elif status == "queued":
                counts["queued"] += 1
            elif status in ("failed", "upload_failed", "start_failed",
                            "cancelled"):
                counts["failed"] += 1
        wo["counts"] = counts

    def _attach_activity_timeline(self, wo: dict, limit: int = 12) -> None:
        """Synthesize activity events from queue_items timestamps.

        PrintHistoryDB doesn't tag events by wo_id, so we derive
        per-WO activity from the queue_items' own timestamps. Each
        item contributes up to four synthetic events: queued, started,
        completed, qc-pass/fail. Sorted newest-first, capped to `limit`.
        """
        queue_items = wo.get("queue_items") or []
        events: list = []
        for qi in queue_items:
            part_label = "{} {}/{}".format(
                qi.get("part_name") or "part",
                qi.get("sequence_number") or "?",
                qi.get("total_quantity") or "?",
            )
            printer = qi.get("assigned_printer_name") or ""
            if qi.get("started_at"):
                events.append({
                    "ts": qi["started_at"],
                    "kind": "started",
                    "tone": "info",
                    "text": "Started {}".format(part_label),
                    "where": printer,
                })
            if qi.get("completed_at"):
                status = qi.get("status")
                if status == "completed":
                    events.append({
                        "ts": qi["completed_at"],
                        "kind": "completed",
                        "tone": "ok",
                        "text": "Completed {}".format(part_label),
                        "where": printer,
                    })
                    outcome = qi.get("production_outcome")
                    if outcome == "pass":
                        events.append({
                            "ts": qi["completed_at"],
                            "kind": "qc-pass",
                            "tone": "ok",
                            "text": "Inspection passed · {}".format(
                                part_label),
                            "where": qi.get("production_operator") or "",
                        })
                    elif outcome == "fail":
                        events.append({
                            "ts": qi["completed_at"],
                            "kind": "qc-fail",
                            "tone": "err",
                            "text": "Inspection failed · {}".format(
                                part_label),
                            "where": qi.get("production_operator") or "",
                        })
                elif status in ("failed", "upload_failed", "start_failed"):
                    events.append({
                        "ts": qi["completed_at"],
                        "kind": "failed",
                        "tone": "err",
                        "text": "Failed {}".format(part_label),
                        "where": printer,
                    })
                elif status == "cancelled":
                    events.append({
                        "ts": qi["completed_at"],
                        "kind": "cancelled",
                        "tone": "warn",
                        "text": "Cancelled {}".format(part_label),
                        "where": printer,
                    })
        # WO-level transitions
        if wo.get("created_at"):
            events.append({
                "ts": wo["created_at"],
                "kind": "wo-created",
                "tone": "neutral",
                "text": "{} created".format(wo.get("wo_id") or "WO"),
                "where": wo.get("customer_name") or "",
            })
        if wo.get("completed_at"):
            events.append({
                "ts": wo["completed_at"],
                "kind": "wo-completed",
                "tone": "ok",
                "text": "{} marked {}".format(
                    wo.get("wo_id") or "WO", wo.get("status") or "completed"
                ),
                "where": "",
            })
        events.sort(key=lambda e: e.get("ts") or "", reverse=True)
        wo["activity"] = events[:limit]

    def cancel_work_order(self, wo_id: str) -> dict:
        """Cancel every non-terminal queue item in a WO.

        Stops any printer that was actively running one of the WO's
        parts and closes the in-flight production record as
        ``cancelled``. DB cancellation and status rollup happen even if
        the printer-side stop fails — those are logged, not raised.
        """
        if not self.queue_repository:
            raise RuntimeError("queue_repository is required to cancel")
        if not self.work_order_repository.get_work_order(wo_id):
            return {"found": False, "cancelled_count": 0, "printing_count": 0}

        affected = self.queue_bulk_operations.cancel_queue_items_for_wo(wo_id)
        self._close_printing_side_effects(affected)
        return {
            "found": True,
            "cancelled_count": len(affected),
            "printing_count": sum(1 for a in affected if a["was_printing"]),
            "affected": affected,
        }

    def cancel_job(self, job_id: int) -> dict:
        """Cancel every non-terminal queue item belonging to a job."""
        if not self.queue_repository:
            raise RuntimeError("queue_repository is required to cancel")
        if not self.job_repository.get_job_queue_items(job_id) and \
                not self._job_exists(job_id):
            return {"found": False, "cancelled_count": 0, "printing_count": 0}

        affected = self.queue_bulk_operations.cancel_queue_items_for_job(
            job_id
        )
        self._close_printing_side_effects(affected)
        return {
            "found": True,
            "cancelled_count": len(affected),
            "printing_count": sum(1 for a in affected if a["was_printing"]),
            "affected": affected,
        }

    def retry_work_order(self, wo_id: str) -> dict:
        """Requeue every cancelled/failed item in a WO."""
        if not self.queue_repository:
            raise RuntimeError("queue_repository is required to retry")
        if not self.work_order_repository.get_work_order(wo_id):
            return {"found": False, "requeued_count": 0}

        affected = self.queue_bulk_operations.requeue_queue_items_for_wo(
            wo_id
        )
        return {
            "found": True,
            "requeued_count": len(affected),
            "affected": affected,
        }

    def retry_job(self, job_id: int) -> dict:
        """Requeue every cancelled/failed item in a job."""
        if not self.queue_repository:
            raise RuntimeError("queue_repository is required to retry")
        if not self._job_exists(job_id):
            return {"found": False, "requeued_count": 0}

        affected = self.queue_bulk_operations.requeue_queue_items_for_job(
            job_id
        )
        return {
            "found": True,
            "requeued_count": len(affected),
            "affected": affected,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _job_exists(self, job_id: int) -> bool:
        # Cheap existence probe: get_job_queue_items returns None for
        # missing jobs, [] for an empty job.
        return self.job_repository.get_job_queue_items(job_id) is not None

    def _close_printing_side_effects(self, affected: list) -> None:
        """Stop printers + close production records for printing items.

        ``affected`` is the list returned by queue_repository cancel
        methods; each entry that was printing has an assigned printer
        whose print we need to stop. Failures on the printer side are
        logged, never raised — the queue cancellation has already
        committed and should not roll back.
        """
        if not self.farm_manager:
            return

        stopped_printers = set()
        for item in affected:
            if not item.get("was_printing"):
                continue
            printer_id = item.get("assigned_printer_id")
            if not printer_id or printer_id in stopped_printers:
                continue
            stopped_printers.add(printer_id)
            self._stop_printer_and_close_production(printer_id)

    def _stop_printer_and_close_production(self, printer_id: str) -> None:
        """Tear down a printer + its in-flight production record.

        ``mark_stop_pending`` is set BEFORE ``client.stop_job()`` so the
        polling loop can't observe the printing->idle transition and
        classify it as a completion while we're still waiting on the
        printer's HTTP response. See the race analysis in
        farm_manager.poll_printer.
        """
        try:
            self.farm_manager.mark_stop_pending(printer_id)
        except Exception as exc:
            print("[WO CANCEL] mark_stop_pending failed for {}: "
                  "{}".format(printer_id, exc))
        client = self.farm_manager.get_printer_client(printer_id)
        if client:
            try:
                client.stop_job()
            except Exception as exc:
                print("[WO CANCEL] stop_job raised on {}: {}".format(
                    printer_id, exc))
        try:
            active_job_id = self.farm_manager.get_active_job_id(printer_id)
        except Exception:
            active_job_id = None
        if active_job_id is not None and self.production_job_repository:
            try:
                self.production_job_repository.stop_job(active_job_id)
                self.production_job_repository.update_job_qc(
                    active_job_id, outcome="cancelled"
                )
            except Exception as exc:
                print("[WO CANCEL] production close failed for "
                      "job_id={}: {}".format(active_job_id, exc))
        try:
            self.farm_manager.clear_active_job(printer_id)
        except Exception as exc:
            print("[WO CANCEL] clear_active_job failed for {}: "
                  "{}".format(printer_id, exc))

    def _attach_production_outcome(self, wo: dict) -> None:
        """Enrich queue_items with the production-log QC outcome.

        Cross-DB lookup: work_orders.db → production_log.db. Done in
        the service layer so repositories can stay single-DB.
        """
        if not self.production_job_repository:
            return
        queue_items = wo.get("queue_items") or []
        for qi in queue_items:
            pjid = qi.get("print_job_id")
            if not pjid:
                continue
            try:
                row = self.production_job_repository.get_job(pjid)
            except Exception:
                row = None
            if row:
                qi["production_outcome"] = row.get("outcome")
                qi["production_operator"] = row.get("operator")
                qi["production_notes"] = row.get("notes")

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def get_work_order_jobs(self, wo_id: str) -> Optional[list]:
        return self.job_repository.get_work_order_jobs(wo_id)

    def create_job(self, wo_id: str, queue_ids=None,
                   job_type: str = "Internal",
                   vendor: Optional[str] = None,
                   external_process: Optional[str] = None,
                   requirements: Optional[str] = None,
                   designer: Optional[str] = None) -> dict:
        if job_type not in _VALID_JOB_TYPES:
            raise ValueError(
                "job_type must be one of {}; got {!r}".format(
                    _VALID_JOB_TYPES, job_type
                )
            )

        if job_type == "External":
            if not (vendor and vendor.strip()):
                raise ValueError("External jobs require a vendor")
            if not (external_process and external_process.strip()):
                raise ValueError(
                    "External jobs require an external_process"
                )
        elif job_type == "Design":
            if not (designer and designer.strip()):
                raise ValueError("Design jobs require a designer")

        return self.job_repository.create_job(
            wo_id,
            queue_ids=queue_ids if job_type == "Internal" else None,
            job_type=job_type,
            vendor=vendor,
            external_process=external_process,
            requirements=requirements,
            designer=designer,
        )

    def _require_job_type(self, job_id: int, expected_type: str) -> dict:
        """Look up a job and assert its job_type. Raises for mismatches."""
        job = self.job_repository.get_job(job_id)
        if job is None:
            raise LookupError("Job not found")
        if job.get("job_type") != expected_type:
            raise ValueError(
                "Job {} is type {!r}, not {!r}".format(
                    job_id, job.get("job_type"), expected_type
                )
            )
        return job

    def update_external_job_fields(self, job_id: int, **kwargs) -> None:
        self._require_job_type(job_id, "External")
        self.job_repository.update_external_job_fields(job_id, **kwargs)

    def update_design_job_fields(self, job_id: int, **kwargs) -> None:
        self._require_job_type(job_id, "Design")
        self.job_repository.update_design_job_fields(job_id, **kwargs)

    def update_internal_job_fields(self, job_id: int, **kwargs) -> None:
        self._require_job_type(job_id, "Internal")
        self.job_repository.update_internal_job_fields(job_id, **kwargs)

    def start_non_internal_job(self, job_id: int) -> str:
        return self.job_repository.start_non_internal_job(job_id)

    def complete_non_internal_job(self, job_id: int) -> str:
        # Auto-populate the type-appropriate "actually done" timestamp
        # when it's still NULL. Goes through the service-layer update
        # methods so type validation (Change 4) still fires; the user
        # can pre-fill these via PATCH and we won't overwrite their
        # value.
        job = self.job_repository.get_job(job_id)
        if job is None:
            raise LookupError("Job not found")
        job_type = job.get("job_type")
        now = datetime.now(timezone.utc).isoformat()
        if job_type == "External" and not job.get("date_delivered"):
            self.update_external_job_fields(job_id, date_delivered=now)
        elif job_type == "Design" and not job.get("design_completed_at"):
            self.update_design_job_fields(job_id, design_completed_at=now)

        wo_id = self.job_repository.complete_non_internal_job(job_id)
        # Repo committed the job UPDATE on its own connection. Open a
        # fresh conn here so the WO rollup runs independently and stays
        # in the service layer (cross-table orchestration).
        conn = self.job_repository._get_conn()
        try:
            status_sync.sync_work_order_status(conn, wo_id)
            conn.commit()
        finally:
            conn.close()
        return wo_id
