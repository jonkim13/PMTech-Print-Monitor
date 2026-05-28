"""Work order business logic."""

from datetime import datetime, timezone
from typing import Optional

from app.domains.work_orders import status_sync


_VALID_JOB_TYPES = ("Internal", "External", "Design")
_VALID_INSPECTION_OUTCOMES = ("pass", "fail")
_INSPECTABLE_JOB_TYPES = ("Internal", "External")


class DeliveryStateError(Exception):
    """Phase F — illegal delivery transition (not completed / already
    delivered). Maps to HTTP 409."""


class WorkOrderService:
    """Orchestrates work-order and job operations."""

    def __init__(self, *,
                 work_order_repository,
                 job_repository,
                 queue_repository=None,
                 queue_bulk_operations=None,
                 queue_execution_repository=None,
                 farm_manager=None,
                 production_job_repository=None,
                 quality_repository=None):
        self.work_order_repository = work_order_repository
        self.job_repository = job_repository
        self.queue_repository = queue_repository
        self.queue_bulk_operations = queue_bulk_operations
        self.queue_execution_repository = queue_execution_repository
        self.farm_manager = farm_manager
        self.production_job_repository = production_job_repository
        # Phase E — read-only handle so the WO rollup can apply the
        # open-NCR gate and the detail payload can carry an ncr_summary.
        self.quality_repository = quality_repository

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
            self._attach_ncr_summary(wo)
            self._attach_delivery(wo)
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
            # Phase D — the recorded inspector pass/fail outcome backing
            # the gate. Defaults to 'pending' for rows predating the
            # mirror. Lets the frontend pick the right button/pill
            # without a second fetch.
            outcome = (job.get("inspection_outcome") or "pending")
            job["inspection"] = {
                "passed": bucket["passed"],
                "failed": bucket["failed"],
                "pending": bucket["pending"],
                "total": bucket["total"],
                "inspector": inspector or job.get("inspector"),
                "state": state,
                "outcome": outcome,
            }
            # Phase C — surface the discriminator as a lowercase
            # template-friendly `type`. `or 'Internal'` defends
            # against rows that somehow lack the column (legacy DBs
            # that haven't picked up the mirror yet).
            job["type"] = (job.get("job_type") or "Internal").lower()

    def _attach_ncr_summary(self, wo: dict) -> None:
        """Phase E — attach this WO's open-NCR count + a lightweight list.

        Service-layer cross-DB read into quality.db (no SQL join). This
        is the payload E2's UI will render; E1 only exposes it. No-op
        when the quality repository isn't wired (e.g. minimal test
        services), mirroring _attach_production_outcome's None guard.
        """
        if not self.quality_repository:
            return
        ncrs = self.quality_repository.list_ncrs_for_wo(wo["wo_id"])
        open_count = sum(1 for n in ncrs if n.get("status") == "open")
        wo["ncr_summary"] = {
            "open_count": open_count,
            "total": len(ncrs),
            "ncrs": [
                {
                    "ncr_id": n.get("ncr_id"),
                    "job_id": n.get("job_id"),
                    "status": n.get("status"),
                    "corrective_action_needed": n.get(
                        "corrective_action_needed"
                    ),
                }
                for n in ncrs
            ],
        }

    def _attach_delivery(self, wo: dict) -> None:
        """Phase F — attach the delivery record (if any) to the payload.

        Lets the UI show "Delivered on <date>" instead of the Mark
        Delivered action once the WO is delivered. Same-DB read; no
        guard needed (the work_order_repository is always wired).
        """
        delivery = self.work_order_repository.get_delivery_for_wo(
            wo["wo_id"]
        )
        if delivery:
            wo["delivery"] = {
                "delivery_id": delivery.get("delivery_id"),
                "delivered_at": delivery.get("delivered_at"),
                "received_by": delivery.get("received_by"),
                "recorded_by": delivery.get("recorded_by"),
                "notes": delivery.get("notes"),
            }

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
            status_sync.sync_work_order_status(
                conn, wo_id, quality_repository=self.quality_repository
            )
            conn.commit()
        finally:
            conn.close()
        return wo_id

    def record_inspection(self, job_id: int, outcome: str,
                          inspector: str, report: Optional[str] = None,
                          date: Optional[str] = None) -> dict:
        """Phase D — write inspector pass/fail and re-roll statuses.

        Only Internal and External jobs are inspectable; Design jobs
        are rejected with ValueError per Philip's process diagram.
        ``outcome`` must be 'pass' or 'fail' — 'pending' is the
        default state, not a recordable outcome. The inspector name
        must be non-empty after stripping.

        On a successful write the call also re-runs
        ``sync_job_status`` (so the inspection gate translates the
        outcome into the job-status enum) and
        ``sync_work_order_status`` (so the WO rollup picks up the
        new job status) inside one transaction.

        Returns the updated job row.
        """
        if outcome not in _VALID_INSPECTION_OUTCOMES:
            raise ValueError(
                "outcome must be one of {}; got {!r}".format(
                    _VALID_INSPECTION_OUTCOMES, outcome
                )
            )
        inspector_clean = (inspector or "").strip()
        if not inspector_clean:
            raise ValueError("inspector is required")

        job = self.job_repository.get_job(job_id)
        if job is None:
            raise LookupError("Job not found")
        job_type = job.get("job_type")
        if job_type not in _INSPECTABLE_JOB_TYPES:
            raise ValueError(
                "Inspection not applicable to {} jobs".format(job_type)
            )

        date_value = date or datetime.now(timezone.utc).date().isoformat()
        report_value = report  # repository accepts None

        # Single transaction for the write + both rollups so the
        # inspection outcome and the derived statuses commit together.
        conn = self.job_repository._get_conn()
        try:
            conn.execute(
                "UPDATE jobs SET inspection_outcome = ?, inspector = ?, "
                "inspection_report = ?, inspection_date = ? "
                "WHERE job_id = ?",
                (outcome, inspector_clean, report_value, date_value, job_id),
            )
            # External jobs have no queue_items, so the inspection gate
            # in sync_job_status reads the job's OWN stored status. The
            # repurposed UI flow (Complete → inspect) submits while the
            # job is still 'in_progress', so mark it 'completed' first;
            # the gate then maps the outcome (pass → completed,
            # fail → attention). Internal jobs derive 'completed' from
            # their queue_items, so they need no nudge here.
            if job_type == "External":
                conn.execute(
                    "UPDATE jobs SET status = 'completed' WHERE job_id = ?",
                    (job_id,),
                )
            status_sync.sync_job_status(conn, job_id)
            status_sync.sync_work_order_status(
                conn, job["wo_id"],
                quality_repository=self.quality_repository,
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            return dict(row) if row else {}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def mark_delivered(self, wo_id: str, delivered_at: Optional[str] = None,
                       received_by: Optional[str] = None,
                       notes: Optional[str] = None,
                       recorded_by: Optional[str] = None) -> dict:
        """Phase F — record delivery and stamp the WO ``delivered``.

        Only reachable from ``completed``: a WO that is still open /
        in_progress / attention isn't finished, and a ``delivered`` WO
        is terminal (no re-delivery). Both rejections raise
        DeliveryStateError (HTTP 409); a missing WO raises LookupError
        (404).

        In one transaction we insert the delivery row and set the WO
        status via ``set_work_order_status_terminal`` — the direct
        terminal write that bypasses derivation. We deliberately do NOT
        call ``sync_work_order_status`` here: that would re-derive the
        WO back to ``completed``. The sync guard then keeps it
        ``delivered`` through any later queue/inspection/NCR write.

        Returns the updated WO (with the delivery record attached).
        """
        wo = self.work_order_repository.get_work_order(wo_id)
        if wo is None:
            raise LookupError("Work order not found")
        status = wo.get("status")
        if status == "delivered":
            raise DeliveryStateError(
                "Work order {} is already delivered".format(wo_id)
            )
        if status != "completed":
            raise DeliveryStateError(
                "Work order {} must be completed before delivery "
                "(current status: {})".format(wo_id, status)
            )

        delivered_at = (
            delivered_at or datetime.now(timezone.utc).date().isoformat()
        )
        now = datetime.now(timezone.utc).isoformat()

        conn = self.work_order_repository._get_conn()
        try:
            self.work_order_repository.insert_delivery(
                conn, wo_id, delivered_at, received_by, notes,
                recorded_by, now,
            )
            status_sync.set_work_order_status_terminal(
                conn, wo_id, "delivered"
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return self.get_work_order(wo_id)
