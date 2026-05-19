"""Queue business logic."""

from typing import Optional


class QueueService:
    """Orchestrates queue operations."""

    def __init__(self, queue_repository, execution_repository,
                 work_order_repository=None, job_repository=None,
                 farm_manager=None, production_job_repository=None):
        self.queue_repository = queue_repository
        self.execution_repository = execution_repository
        self.work_order_repository = work_order_repository
        self.job_repository = job_repository
        self.farm_manager = farm_manager
        self.production_job_repository = production_job_repository

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_queue_item(self, queue_id: int) -> Optional[dict]:
        return self.queue_repository.get_queue_item(queue_id)

    def get_queue_items(self, queue_ids) -> list:
        return self.queue_repository.get_queue_items(queue_ids)

    # ------------------------------------------------------------------
    # Status Changes
    # ------------------------------------------------------------------

    def requeue_item(self, queue_id: int) -> bool:
        return self.queue_repository.requeue_item(queue_id)

    def complete_queue_item(self, queue_id: int,
                            print_job_id: Optional[int] = None) -> bool:
        return self.queue_repository.complete_queue_item(
            queue_id, print_job_id=print_job_id
        )

    def fail_queue_item(self, queue_id: int) -> bool:
        return self.queue_repository.fail_queue_item(queue_id)

    # ------------------------------------------------------------------
    # Cancel / Retry (part level)
    # ------------------------------------------------------------------

    def cancel_queue_item(self, queue_id: int) -> dict:
        """Cancel a single queue item; stop the printer if it's printing."""
        item = self.queue_repository.get_queue_item(queue_id)
        if not item:
            return {"found": False, "cancelled_count": 0, "printing_count": 0}

        affected = self.queue_repository.cancel_queue_items([queue_id])
        if affected and affected[0].get("was_printing"):
            self._stop_printer_for(affected[0])
        return {
            "found": True,
            "cancelled_count": len(affected),
            "printing_count": sum(1 for a in affected if a["was_printing"]),
            "affected": affected,
        }

    def retry_queue_item(self, queue_id: int) -> dict:
        """Requeue a single cancelled/failed queue item."""
        item = self.queue_repository.get_queue_item(queue_id)
        if not item:
            return {"found": False, "requeued_count": 0}

        affected = self.queue_repository.requeue_queue_items([queue_id])
        return {
            "found": True,
            "requeued_count": len(affected),
            "affected": affected,
        }

    def _stop_printer_for(self, affected_item: dict) -> None:
        """Stop the printer + close production for a cancelled printing part.

        Mirror of WorkOrderService._stop_printer_and_close_production.
        The pending-stop flag is set BEFORE stop_job so the polling loop
        can't misread the printing->idle transition as a completion and
        deduct filament; see farm_manager.poll_printer.
        """
        printer_id = affected_item.get("assigned_printer_id")
        if not printer_id or not self.farm_manager:
            return
        try:
            self.farm_manager.mark_stop_pending(printer_id)
        except Exception as exc:
            print("[CANCEL] mark_stop_pending failed for {}: {}".format(
                printer_id, exc))
        client = self.farm_manager.get_printer_client(printer_id)
        if client:
            try:
                client.stop_job()
            except Exception as exc:
                print("[CANCEL] stop_job raised on {}: {}".format(
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
                print("[CANCEL] production close failed for job_id={}: "
                      "{}".format(active_job_id, exc))
        try:
            self.farm_manager.clear_active_job(printer_id)
        except Exception as exc:
            print("[CANCEL] clear_active_job failed for {}: {}".format(
                printer_id, exc))

    # ------------------------------------------------------------------
    # Print Validation
    # ------------------------------------------------------------------

    def validate_queue_print_items(self, queue_ids) -> list:
        """Validate a queue selection for printing."""
        items = self.queue_repository.get_queue_items(queue_ids)
        if len(items) != len(queue_ids):
            raise LookupError("One or more selected parts were not found")

        active = self.queue_repository.ACTIVE_QUEUE_STATUSES
        printable = self.queue_repository.PRINTABLE_QUEUE_STATUSES

        if any(item["status"] in active for item in items):
            raise RuntimeError("items already in progress")

        printable_items = [
            item for item in items if item["status"] in printable
        ]
        if not printable_items:
            raise ValueError("no items to print")
        if len(printable_items) != len(items):
            raise ValueError(
                "Selected parts must be queued or retryable before printing"
            )

        wo_ids = {item["wo_id"] for item in items}
        if len(wo_ids) != 1:
            raise ValueError(
                "Selected parts must belong to the same work order"
            )

        return items

    def resolve_print_request_items(self, queue_ids, requested_job_id=None):
        """Resolve the explicit queue items for a print request."""
        parsed_queue_ids = list(queue_ids) if queue_ids else []

        if requested_job_id is not None:
            job_items = self.job_repository.get_job_queue_items(
                requested_job_id
            )
            if job_items is None:
                raise LookupError("job not found")

            active = self.queue_repository.ACTIVE_QUEUE_STATUSES
            printable = self.queue_repository.PRINTABLE_QUEUE_STATUSES

            if parsed_queue_ids:
                job_item_ids = {item["queue_id"] for item in job_items}
                if any(qid not in job_item_ids for qid in parsed_queue_ids):
                    raise ValueError(
                        "selected parts must belong to the requested job"
                    )

            if any(item["status"] in active for item in job_items):
                raise RuntimeError("items already in progress")

            printable_items = [
                item for item in job_items if item["status"] in printable
            ]
            if not printable_items:
                raise ValueError("no items to print")

            return (
                [item["queue_id"] for item in printable_items],
                printable_items,
            )

        queue_items = self.validate_queue_print_items(parsed_queue_ids)
        self._validate_selected_job(queue_items)
        return parsed_queue_ids, queue_items

    @staticmethod
    def _validate_selected_job(queue_items, requested_job_id=None):
        """Ensure a print selection stays within one persisted job."""
        job_ids = {
            item.get("job_id") for item in queue_items if item.get("job_id")
        }
        if requested_job_id is not None:
            if any(item.get("job_id") not in (None, requested_job_id)
                   for item in queue_items):
                raise ValueError(
                    "Selected parts must belong to the requested job"
                )
            return
        if len(job_ids) > 1:
            raise ValueError(
                "Selected parts must belong to the same job before printing"
            )

    # ------------------------------------------------------------------
    # Start Execution
    # ------------------------------------------------------------------

    def start_queue_job_execution(self, queue_ids, printer_id, printer_name,
                                  gcode_file, operator_initials=None,
                                  job_id=None) -> dict:
        return self.execution_repository.start_queue_job_execution(
            queue_ids, printer_id, printer_name, gcode_file,
            operator_initials=operator_initials, job_id=job_id,
        )
