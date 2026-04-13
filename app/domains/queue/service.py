"""Queue business logic."""

from typing import Optional


class QueueService:
    """Orchestrates queue operations."""

    def __init__(self, queue_repository, execution_repository,
                 work_order_repository=None, job_repository=None):
        self.queue_repository = queue_repository
        self.execution_repository = execution_repository
        self.work_order_repository = work_order_repository
        self.job_repository = job_repository

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_queue(self, status: Optional[str] = None,
                  limit: int = 200, offset: int = 0) -> list:
        return self.queue_repository.get_queue(
            status=status, limit=limit, offset=offset
        )

    def get_queue_stats(self) -> dict:
        return self.queue_repository.get_queue_stats()

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
