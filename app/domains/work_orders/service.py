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

    def update_work_order_status(self, wo_id: str, status: str) -> bool:
        if status == "cancelled":
            return self.cancel_work_order(wo_id)
        return self.work_order_repository.update_work_order_status(
            wo_id, status
        )

    def cancel_work_order(self, wo_id: str) -> bool:
        """Cancel a work order; stop any printer actively running its parts.

        Printer-stop operations are best-effort — a failed stop_job
        should not block the DB cancellation, only be logged.
        """
        self._stop_active_prints_for_wo(wo_id)
        return self.work_order_repository.cancel_work_order(wo_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _stop_active_prints_for_wo(self, wo_id: str) -> None:
        """Stop any printer currently running a queue item of this WO."""
        if not self.farm_manager or not self.queue_repository:
            return

        try:
            wo = self.work_order_repository.get_work_order(wo_id)
        except Exception as exc:
            print("[WO CANCEL] Failed to load WO {} before stop: {}".format(
                wo_id, exc))
            return
        if not wo:
            return

        printing_items = [
            qi for qi in (wo.get("queue_items") or [])
            if qi.get("status") == "printing"
            and qi.get("assigned_printer_id")
        ]
        stopped_printers = set()
        for item in printing_items:
            printer_id = item["assigned_printer_id"]
            if printer_id in stopped_printers:
                continue
            stopped_printers.add(printer_id)
            client = self.farm_manager.get_printer_client(printer_id)
            if client:
                try:
                    client.stop_job()
                except Exception as exc:
                    print("[WO CANCEL] stop_job raised on {}: {}".format(
                        printer_id, exc))
            try:
                self.farm_manager.mark_stop_pending(printer_id)
            except Exception as exc:
                print("[WO CANCEL] mark_stop_pending failed for {}: "
                      "{}".format(printer_id, exc))
            try:
                active_job_id = self.farm_manager.get_active_job_id(
                    printer_id
                )
            except Exception:
                active_job_id = None
            if active_job_id is not None and self.production_job_repository:
                try:
                    self.production_job_repository.stop_job(active_job_id)
                except Exception as exc:
                    print("[WO CANCEL] production stop_job failed for "
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

    def assign_queue_items_to_job(self, wo_id: str,
                                  job_id: int, queue_ids) -> dict:
        return self.job_repository.assign_queue_items_to_job(
            wo_id, job_id, queue_ids
        )
