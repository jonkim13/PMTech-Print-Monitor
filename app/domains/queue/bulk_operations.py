"""Bulk cancel and requeue operations on queue_items.

Split from QueueRepository in Phase 5f so the single-item CRUD surface
stays focused. Both classes share the same work_orders.db file and the
same connection pattern; the schema (and migrations) remain owned by
QueueRepository._init_tables.
"""

import sqlite3
from datetime import datetime, timezone

from app.domains.work_orders import status_sync


class QueueBulkOperations:
    """Multi-item write operations (cancel/requeue) on queue_items."""

    # States that can be transitioned to 'cancelled'. Completed and
    # already-cancelled rows are protected.
    _CANCELLABLE_STATUSES = (
        "queued", "uploading", "uploaded", "starting", "printing",
        "failed", "upload_failed", "start_failed",
    )

    # States that can be requeued (back to 'queued').
    _RETRYABLE_STATUSES = (
        "cancelled", "failed", "upload_failed", "start_failed",
    )

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @staticmethod
    def _normalize_queue_ids(queue_ids) -> list:
        result = []
        seen = set()
        for raw_id in queue_ids or []:
            queue_id = int(raw_id)
            if queue_id in seen:
                continue
            seen.add(queue_id)
            result.append(queue_id)
        return result

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel_queue_items(self, queue_ids: list) -> list:
        """Mark the given queue items cancelled if they're non-terminal.

        Returns a list of dicts for every item that was actually
        cancelled, including fields the service layer needs to stop
        printers / close production records:

            {queue_id, wo_id, job_id, queue_job_id, assigned_printer_id,
             print_job_id, prior_status, was_printing}

        Completed and already-cancelled items are silently skipped.
        """
        ids = self._normalize_queue_ids(queue_ids)
        if not ids:
            return []

        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute("""
                SELECT queue_id, wo_id, job_id, queue_job_id,
                       assigned_printer_id, print_job_id, status
                FROM queue_items
                WHERE queue_id IN ({})
            """.format(placeholders), ids).fetchall()

            affected = []
            for row in rows:
                if row["status"] not in self._CANCELLABLE_STATUSES:
                    continue
                conn.execute("""
                    UPDATE queue_items
                    SET status = 'cancelled', completed_at = ?
                    WHERE queue_id = ?
                """, (now, row["queue_id"]))
                affected.append({
                    "queue_id": row["queue_id"],
                    "wo_id": row["wo_id"],
                    "job_id": row["job_id"],
                    "queue_job_id": row["queue_job_id"],
                    "assigned_printer_id": row["assigned_printer_id"],
                    "print_job_id": row["print_job_id"],
                    "prior_status": row["status"],
                    "was_printing": row["status"] == "printing",
                })

            if not affected:
                conn.rollback()
                return []

            # Mark any queue_jobs whose items were all cancelled as
            # cancelled too so the session reflects final state.
            queue_job_ids = sorted({
                a["queue_job_id"] for a in affected if a["queue_job_id"]
            })
            for qjid in queue_job_ids:
                remaining = conn.execute("""
                    SELECT status FROM queue_items WHERE queue_job_id = ?
                """, (qjid,)).fetchall()
                statuses = [r["status"] for r in remaining]
                if statuses and all(s in ("cancelled", "completed")
                                    for s in statuses):
                    terminal = ("cancelled"
                                if all(s == "cancelled" for s in statuses)
                                else "completed")
                    conn.execute("""
                        UPDATE queue_jobs
                        SET status = ?, completed_at = ?
                        WHERE queue_job_id = ?
                    """, (terminal, now, qjid))

            job_ids = sorted({a["job_id"] for a in affected if a["job_id"]})
            wo_ids = sorted({a["wo_id"] for a in affected if a["wo_id"]})
            for jid in job_ids:
                status_sync.sync_job_status(conn, jid)
            for wid in wo_ids:
                status_sync.sync_work_order_status(conn, wid)

            conn.commit()
            return affected
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def cancel_queue_items_for_wo(self, wo_id: str) -> list:
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in self._CANCELLABLE_STATUSES)
        rows = conn.execute("""
            SELECT queue_id FROM queue_items
            WHERE wo_id = ? AND status IN ({})
        """.format(placeholders),
                            [wo_id] + list(self._CANCELLABLE_STATUSES)
                            ).fetchall()
        conn.close()
        return self.cancel_queue_items([r["queue_id"] for r in rows])

    def cancel_queue_items_for_job(self, job_id: int) -> list:
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in self._CANCELLABLE_STATUSES)
        rows = conn.execute("""
            SELECT queue_id FROM queue_items
            WHERE job_id = ? AND status IN ({})
        """.format(placeholders),
                            [job_id] + list(self._CANCELLABLE_STATUSES)
                            ).fetchall()
        conn.close()
        return self.cancel_queue_items([r["queue_id"] for r in rows])

    # ------------------------------------------------------------------
    # Requeue
    # ------------------------------------------------------------------

    def requeue_queue_items(self, queue_ids: list) -> list:
        """Requeue the given queue items if they're cancelled/failed.

        Returns a list of dicts (queue_id, wo_id, job_id) for every
        item that was requeued. Clears assignment fields so the item
        can be re-scheduled cleanly.
        """
        ids = self._normalize_queue_ids(queue_ids)
        if not ids:
            return []

        conn = self._get_conn()
        try:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute("""
                SELECT queue_id, wo_id, job_id, status
                FROM queue_items
                WHERE queue_id IN ({})
            """.format(placeholders), ids).fetchall()

            affected = []
            for row in rows:
                if row["status"] not in self._RETRYABLE_STATUSES:
                    continue
                conn.execute("""
                    UPDATE queue_items
                    SET status = 'queued',
                        queue_job_id = NULL,
                        assigned_printer_id = NULL,
                        assigned_printer_name = NULL,
                        gcode_file = NULL,
                        upload_session_id = NULL,
                        print_job_id = NULL,
                        assigned_at = NULL,
                        started_at = NULL,
                        completed_at = NULL
                    WHERE queue_id = ?
                """, (row["queue_id"],))
                affected.append({
                    "queue_id": row["queue_id"],
                    "wo_id": row["wo_id"],
                    "job_id": row["job_id"],
                    "prior_status": row["status"],
                })

            if not affected:
                conn.rollback()
                return []

            job_ids = sorted({a["job_id"] for a in affected if a["job_id"]})
            wo_ids = sorted({a["wo_id"] for a in affected if a["wo_id"]})
            for jid in job_ids:
                status_sync.sync_job_status(conn, jid)
            for wid in wo_ids:
                status_sync.sync_work_order_status(conn, wid)

            conn.commit()
            return affected
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def requeue_queue_items_for_wo(self, wo_id: str) -> list:
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in self._RETRYABLE_STATUSES)
        rows = conn.execute("""
            SELECT queue_id FROM queue_items
            WHERE wo_id = ? AND status IN ({})
        """.format(placeholders),
                            [wo_id] + list(self._RETRYABLE_STATUSES)
                            ).fetchall()
        conn.close()
        return self.requeue_queue_items([r["queue_id"] for r in rows])

    def requeue_queue_items_for_job(self, job_id: int) -> list:
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in self._RETRYABLE_STATUSES)
        rows = conn.execute("""
            SELECT queue_id FROM queue_items
            WHERE job_id = ? AND status IN ({})
        """.format(placeholders),
                            [job_id] + list(self._RETRYABLE_STATUSES)
                            ).fetchall()
        conn.close()
        return self.requeue_queue_items([r["queue_id"] for r in rows])
