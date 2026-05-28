"""Canonical status derivation + sync helpers.

One module owns the rules for rolling queue-item statuses up into
job and work-order statuses. All repositories call through these
helpers so the derivation never drifts between call sites.
"""

from datetime import datetime, timezone
from typing import List

ACTIVE_QUEUE_STATUSES = ("uploading", "uploaded", "starting", "printing")
FAILURE_QUEUE_STATUSES = ("upload_failed", "start_failed", "failed")

# Phase C — map job statuses into the queue-item status vocabulary so
# the existing five-state rollup can consume them unchanged. Phase D:
# this now covers Internal jobs too — sync_work_order_status pulls
# every job into the pool so the inspection gate is visible above the
# job level (see the rationale on that function).
_JOB_STATUS_TO_QUEUE_STATUS = {
    "open":        "queued",
    "in_progress": "printing",
    "completed":   "completed",
    "cancelled":   "cancelled",
    "attention":   "failed",
}


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


def derive_job_status_combined(
    queue_item_statuses: List[str],
    job_type: str,
    inspection_outcome: str,
) -> str:
    """Phase D sibling: queue rollup + inspector pass/fail gate.

    Design jobs skip the gate — derivation falls through to the
    base queue-only deriver.

    Internal and External jobs: if the base deriver lands on
    ``completed`` we map the gate state into the existing job-status
    enum. No new status values are introduced on ``jobs``:

        - pass    → 'completed'
        - fail    → 'attention'
        - pending → 'in_progress' (treat as still in flight pending QC)

    External jobs have no queue_items; the caller passes the stored
    job status as a single-element list so the same gate logic
    applies symmetrically.
    """
    base = derive_job_status(queue_item_statuses)
    if job_type == "Design":
        return base
    if base != "completed":
        return base
    if inspection_outcome == "pass":
        return "completed"
    if inspection_outcome == "fail":
        return "attention"
    return "in_progress"


def derive_work_order_status_combined(
    queue_item_statuses: List[str],
    non_internal_job_statuses: List[str],
    has_blocking_ncr: bool = False,
) -> str:
    """Phase C rollup: queue_items + jobs in one pool. Phase E NCR gate.

    Job statuses are projected into the queue-item status vocabulary
    via _JOB_STATUS_TO_QUEUE_STATUS so the existing
    derive_work_order_status logic stays the single source of truth
    for the five-state output (open / in_progress / attention /
    completed / cancelled). Unknown job statuses flow through
    unchanged — the migration constrains the inputs to the mapped
    set.

    Phase E — the open-NCR gate mirrors Phase D's inspection gate: it
    only ever touches the 'completed' branch. A work order whose work
    is otherwise done cannot be 'completed' while an open
    non-conformance is outstanding, so we hold it at 'attention'. Every
    non-'completed' branch is returned untouched, so an
    already-'attention'/'in_progress'/'open' WO is unaffected by the
    flag — the two gates never mask each other.
    """
    projected = [
        _JOB_STATUS_TO_QUEUE_STATUS.get(s, s)
        for s in non_internal_job_statuses
    ]
    base = derive_work_order_status(list(queue_item_statuses) + projected)
    if has_blocking_ncr and base == "completed":
        return "attention"
    return base


def sync_job_status(conn, job_id: int) -> str:
    """Recompute jobs.status for a single job and persist it.

    Returns the new status. `conn` is expected to be an open SQLite
    connection on work_orders.db — the caller owns commit/rollback.
    Phase D: routes through the combined deriver so the inspection
    gate applies wherever the queue rollup runs. External jobs have
    no queue_items — for those we feed the stored job status as a
    single-element list so the gate still fires on 'completed'.
    """
    rows = conn.execute(
        "SELECT status FROM queue_items WHERE job_id = ?",
        (job_id,),
    ).fetchall()
    now = datetime.now(timezone.utc).isoformat()

    job_row = conn.execute(
        "SELECT job_type, status, inspection_outcome "
        "FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    job_type = (job_row["job_type"] if job_row else None) or "Internal"
    inspection_outcome = (
        (job_row["inspection_outcome"] if job_row else None) or "pending"
    )

    if rows:
        statuses = [row["status"] for row in rows]
    elif job_type == "External" and job_row is not None:
        # External jobs have no queue_items; let the gate operate on
        # the job's own stored status. The deriver only flips to
        # 'completed' when fed ['completed'] — every other stored
        # value either passes through or falls back to 'open'.
        statuses = [job_row["status"]]
    else:
        conn.execute(
            "UPDATE jobs SET status = 'open', completed_at = NULL "
            "WHERE job_id = ?",
            (job_id,),
        )
        return "open"

    new_status = derive_job_status_combined(
        statuses, job_type, inspection_outcome
    )
    completed_at = now if new_status in ("completed", "cancelled") else None
    conn.execute(
        "UPDATE jobs SET status = ?, completed_at = ? WHERE job_id = ?",
        (new_status, completed_at, job_id),
    )
    return new_status


def sync_work_order_status(conn, wo_id: str, quality_repository=None) -> str:
    """Recompute work_orders.status for a work order and persist it.

    Phase C: the rollup pool spans queue_items AND non-Internal jobs
    (External, Design).

    Phase D: Internal jobs are now ALSO pulled into the pool. The
    inspection gate (derive_job_status_combined) can make an Internal
    job's status diverge from its queue_items — a queue-completed job
    awaiting inspection is 'in_progress', and a failed inspection is
    'attention' — yet that signal lives only on the job row, never on
    the queue_items. So the WO can only reflect the gate by reading
    the job status. The earlier Phase C "double-count" concern is
    moot: when a job is NOT gated its status equals
    derive_job_status(its queue_items), and adding that consistent
    summary to the pool never changes derive_work_order_status's
    output (every branch is idempotent under a redundant element).
    The redundancy only ever introduces a *new* signal in exactly the
    gated cases we want surfaced.

    Phase E: when ``quality_repository`` is supplied, an open
    non-conformance for this WO gates the 'completed' branch down to
    'attention'. The open-NCR count is read from quality.db inside the
    repository — a cross-DB read at this sync layer, never a SQL join
    across files (same category as the production-QC lookups in the
    service). Callers that don't pass a repository (most queue-side
    syncs, and every pre-Phase-E call site) behave exactly as before.

    Phase F: ``delivered`` is a manual terminal status set by
    ``set_work_order_status_terminal`` — never produced by the
    derivers. A delivered WO is the end of the lifecycle, so this
    function early-returns for it: no queue write, inspection, or NCR
    mutation may re-derive a delivered WO back to ``completed`` /
    ``attention``. This guard is the crux of Phase F.

    Returns the new status (or empty string if the WO has neither
    queue_items nor jobs — in which case the row is left untouched).
    `conn` is an open SQLite connection; commit/rollback is the
    caller's responsibility.
    """
    current = conn.execute(
        "SELECT status FROM work_orders WHERE wo_id = ?",
        (wo_id,),
    ).fetchone()
    if current is not None and current["status"] == "delivered":
        # Terminal manual status — never re-derive it.
        return "delivered"

    qi_rows = conn.execute(
        "SELECT status FROM queue_items WHERE wo_id = ?",
        (wo_id,),
    ).fetchall()
    job_rows = conn.execute(
        "SELECT status FROM jobs WHERE wo_id = ?",
        (wo_id,),
    ).fetchall()
    if not qi_rows and not job_rows:
        return ""

    qi_statuses = [row["status"] for row in qi_rows]
    job_statuses = [row["status"] for row in job_rows]
    has_blocking_ncr = False
    if quality_repository is not None:
        has_blocking_ncr = (
            quality_repository.count_open_ncrs_for_wo(wo_id) > 0
        )
    new_status = derive_work_order_status_combined(
        qi_statuses, job_statuses, has_blocking_ncr=has_blocking_ncr
    )
    now = datetime.now(timezone.utc).isoformat()
    completed_at = now if new_status in ("completed", "cancelled") else None
    conn.execute(
        "UPDATE work_orders SET status = ?, completed_at = ? "
        "WHERE wo_id = ?",
        (new_status, completed_at, wo_id),
    )
    return new_status


def set_work_order_status_terminal(conn, wo_id: str, status: str) -> str:
    """Phase F — write a manual terminal WO status, bypassing derivation.

    The status derivers top out at ``completed``; ``delivered`` is a
    human-driven terminal transition with its own record, so it is set
    here directly rather than derived from children. This is the single
    write path for manual terminal WO status — callers must not issue an
    ad-hoc UPDATE. ``completed_at`` is preserved (COALESCE): a WO only
    reaches a manual terminal state from ``completed``, where it was
    already stamped. `conn` is open; commit/rollback is the caller's.
    """
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE work_orders SET status = ?, "
        "completed_at = COALESCE(completed_at, ?) WHERE wo_id = ?",
        (status, now, wo_id),
    )
    return status
