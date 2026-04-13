"""Work order business logic."""

from typing import Optional


class WorkOrderService:
    """Orchestrates work-order and job operations."""

    def __init__(self, work_order_repository, job_repository):
        self.work_order_repository = work_order_repository
        self.job_repository = job_repository

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
        return self.work_order_repository.get_work_order(wo_id)

    def update_work_order_status(self, wo_id: str, status: str) -> bool:
        if status == "cancelled":
            return self.work_order_repository.cancel_work_order(wo_id)
        return self.work_order_repository.update_work_order_status(
            wo_id, status
        )

    def cancel_work_order(self, wo_id: str) -> bool:
        return self.work_order_repository.cancel_work_order(wo_id)

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
