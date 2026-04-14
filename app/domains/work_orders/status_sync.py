"""Canonical status derivation + sync helpers.

One module owns the rules for rolling queue-item statuses up into
job and work-order statuses. All repositories call through these
helpers so the derivation never drifts between call sites.
"""

from datetime import datetime, timezone
from typing import List

ACTIVE_QUEUE_STATUSES = ("uploading", "uploaded", "starting", "printing")
FAILURE_QUEUE_STATUSES = ("upload_failed", "start_failed", "failed")


def derive_job_status(statuses: List[str]) -> str:
    """Derive a jobs.status from its queue_items' statuses."""
    active_statuses = [s for s in statuses if s != "cancelled"]

    if not active_statuses and statuses:
        return "cancelled"
    if active_statuses and all(s == "completed" for s in active_statuses):
        return "completed"
    if any(s in ACTIVE_QUEUE_STATUSES for s in active_statuses):
        return "in_progress"
    if any(s in FAILURE_QUEUE_STATUSES for s in active_statuses):
        return "attention"
    if any(s == "completed" for s in active_statuses):
        return "in_progress"
    return "open"


def derive_work_order_status(statuses: List[str]) -> str:
    """Derive a work_orders.status from all its queue_items' statuses.

    `attention` surfaces when any queue_item is in a failure state and
    at least one other item is either still active or queued; if every
    non-completed, non-cancelled item is a failure we also raise
    `attention` so the work order doesn't silently linger as
    `in_progress` with nothing to push it forward.
    """
    active_statuses = [s for s in statuses if s != "cancelled"]

    if not active_statuses and statuses:
        return "cancelled"
    if active_statuses and all(s == "completed" for s in active_statuses):
        return "completed"

    has_failure = any(s in FAILURE_QUEUE_STATUSES for s in active_statuses)
    has_non_terminal = any(
        s in ACTIVE_QUEUE_STATUSES or s == "queued"
        for s in active_statuses
    )
    if has_failure and has_non_terminal:
        return "attention"
    if has_failure and not any(s == "completed" for s in active_statuses):
        return "attention"
    if has_failure:
        # All non-completed items are failed and there are no fresh
        # items queued or running — still needs attention.
        non_completed_active = [
            s for s in active_statuses if s != "completed"
        ]
        if non_completed_active and all(
            s in FAILURE_QUEUE_STATUSES for s in non_completed_active
        ):
            return "attention"

    if any(s in (ACTIVE_QUEUE_STATUSES + FAILURE_QUEUE_STATUSES
                 + ("completed",))
           for s in active_statuses):
        return "in_progress"
    return "open"


def sync_job_status(conn, job_id: int) -> str:
    """Recompute jobs.status for a single job and persist it.

    Returns the new status. `conn` is expected to be an open SQLite
    connection on work_orders.db — the caller owns commit/rollback.
    """
    rows = conn.execute(
        "SELECT status FROM queue_items WHERE job_id = ?",
        (job_id,),
    ).fetchall()
    now = datetime.now(timezone.utc).isoformat()

    if not rows:
        conn.execute(
            "UPDATE jobs SET status = 'open', completed_at = NULL "
            "WHERE job_id = ?",
            (job_id,),
        )
        return "open"

    statuses = [row["status"] for row in rows]
    new_status = derive_job_status(statuses)
    completed_at = now if new_status in ("completed", "cancelled") else None
    conn.execute(
        "UPDATE jobs SET status = ?, completed_at = ? WHERE job_id = ?",
        (new_status, completed_at, job_id),
    )
    return new_status


def sync_work_order_status(conn, wo_id: str) -> str:
    """Recompute work_orders.status for a work order and persist it.

    Returns the new status (or empty string if the WO has no queue
    items at all — in which case the row is left untouched).
    `conn` is an open SQLite connection; commit/rollback is the
    caller's responsibility.
    """
    rows = conn.execute(
        "SELECT status FROM queue_items WHERE wo_id = ?",
        (wo_id,),
    ).fetchall()
    if not rows:
        return ""

    statuses = [row["status"] for row in rows]
    new_status = derive_work_order_status(statuses)
    now = datetime.now(timezone.utc).isoformat()
    completed_at = now if new_status in ("completed", "cancelled") else None
    conn.execute(
        "UPDATE work_orders SET status = ?, completed_at = ? "
        "WHERE wo_id = ?",
        (new_status, completed_at, wo_id),
    )
    return new_status
