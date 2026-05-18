"""Work order business logic."""

from typing import Optional


class WorkOrderService:
    """Orchestrates work-order and job operations."""

    def __init__(self, work_order_repository, job_repository,
                 queue_repository=None, queue_execution_repository=None,
                 farm_manager=None, production_job_repository=None):
        self.work_order_repository = work_order_repository
        self.job_repository = job_repository
        self.queue_repository = queue_repository
        self.queue_execution_repository = queue_execution_repository
        self.farm_manager = farm_manager
        self.production_job_repository = production_job_repository

    # ------------------------------------------------------------------
    # Work Orders
    # ------------------------------------------------------------------

    def create_work_order(self, customer_name: str, line_items: list) -> dict:
        return self.work_order_repository.create_work_order(
            customer_name, line_items
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
        return wo

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

        affected = self.queue_repository.cancel_queue_items_for_wo(wo_id)
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

        affected = self.queue_repository.cancel_queue_items_for_job(job_id)
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

        affected = self.queue_repository.requeue_queue_items_for_wo(wo_id)
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

        affected = self.queue_repository.requeue_queue_items_for_job(job_id)
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

    def create_job(self, wo_id: str, queue_ids=None) -> dict:
        return self.job_repository.create_job(wo_id, queue_ids=queue_ids)
