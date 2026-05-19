"""Queue item persistence."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List

from app.domains.work_orders import status_sync


class QueueRepository:
    """Manages queue items in the work_orders.db file."""

    ACTIVE_QUEUE_STATUSES = status_sync.ACTIVE_QUEUE_STATUSES
    FAILURE_QUEUE_STATUSES = status_sync.FAILURE_QUEUE_STATUSES
    # A cancelled item is re-printable directly — the cancel UI commits
    # to "fix the physical issue then hit Print again" and the Print
    # button must work without a prior Re-queue step. See the Row-4 UX
    # audit.
    PRINTABLE_QUEUE_STATUSES = ("queued", "failed", "upload_failed",
                                "start_failed", "cancelled")

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    @staticmethod
    def _has_column(conn, table: str, column: str) -> bool:
        cursor = conn.execute("PRAGMA table_info({})".format(table))
        columns = [row[1] for row in cursor.fetchall()]
        return column in columns

    @staticmethod
    def _add_column_if_missing(conn, table: str,
                               column: str, col_def: str) -> None:
        if not QueueRepository._has_column(conn, table, column):
            conn.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(
                    table, column, col_def)
            )
            conn.commit()

    def _init_tables(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS queue_items (
                queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                wo_id TEXT NOT NULL,
                job_id INTEGER,
                queue_job_id INTEGER,
                part_name TEXT NOT NULL,
                material TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                total_quantity INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                assigned_printer_id TEXT,
                assigned_printer_name TEXT,
                gcode_file TEXT,
                upload_session_id TEXT,
                print_job_id INTEGER,
                queued_at TEXT NOT NULL,
                assigned_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (item_id) REFERENCES line_items(item_id),
                FOREIGN KEY (wo_id) REFERENCES work_orders(wo_id),
                FOREIGN KEY (job_id) REFERENCES jobs(job_id),
                FOREIGN KEY (queue_job_id) REFERENCES queue_jobs(queue_job_id)
            );

            CREATE INDEX IF NOT EXISTS idx_queue_status
                ON queue_items(status);
            CREATE INDEX IF NOT EXISTS idx_queue_wo
                ON queue_items(wo_id);
            CREATE INDEX IF NOT EXISTS idx_queue_printer
                ON queue_items(assigned_printer_id);
        """)
        self._add_column_if_missing(conn, "queue_items", "job_id", "INTEGER")
        self._add_column_if_missing(
            conn, "queue_items", "queue_job_id", "INTEGER"
        )
        self._add_column_if_missing(
            conn, "queue_items", "upload_session_id", "TEXT"
        )
        if self._has_column(conn, "queue_items", "job_id"):
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_queue_items_job
                ON queue_items(job_id)
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_job
            ON queue_items(queue_job_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_items_upload_session
            ON queue_items(upload_session_id)
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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

    def _attach_queue_job_metadata(self, conn,
                                   queue_items: List[dict]) -> None:
        queue_job_ids = sorted({
            item.get("queue_job_id") for item in queue_items
            if item.get("queue_job_id")
        })
        if not queue_job_ids:
            return

        placeholders = ",".join("?" for _ in queue_job_ids)
        rows = conn.execute("""
            SELECT queue_job_id,
                   COUNT(*) AS queue_job_part_count,
                   GROUP_CONCAT(part_name, ', ') AS queue_job_part_names
            FROM queue_items
            WHERE queue_job_id IN ({})
            GROUP BY queue_job_id
        """.format(placeholders), queue_job_ids).fetchall()
        summaries = {row["queue_job_id"]: dict(row) for row in rows}

        for item in queue_items:
            summary = summaries.get(item.get("queue_job_id"))
            if summary:
                item["queue_job_part_count"] = summary["queue_job_part_count"]
                item["queue_job_part_names"] = summary["queue_job_part_names"]
                item["job_part_count"] = summary["queue_job_part_count"]
                item["job_part_names"] = summary["queue_job_part_names"]

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_queue_item(self, queue_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT qi.*, qj.status AS queue_job_status
            FROM queue_items qi
            LEFT JOIN queue_jobs qj ON qi.queue_job_id = qj.queue_job_id
            WHERE qi.queue_id = ?
        """, (queue_id,)).fetchone()
        item = dict(row) if row else None
        if item:
            self._attach_queue_job_metadata(conn, [item])
        conn.close()
        return item

    def get_queue_items(self, queue_ids) -> list:
        conn = self._get_conn()
        items = self._get_queue_items_by_ids(conn, queue_ids)
        conn.close()
        return items

    def _get_queue_items_by_ids(self, conn, queue_ids) -> list:
        queue_ids = self._normalize_queue_ids(queue_ids)
        if not queue_ids:
            return []

        placeholders = ",".join("?" for _ in queue_ids)
        rows = conn.execute("""
            SELECT qi.*, qj.status AS queue_job_status
            FROM queue_items qi
            LEFT JOIN queue_jobs qj ON qi.queue_job_id = qj.queue_job_id
            WHERE qi.queue_id IN ({})
        """.format(placeholders), queue_ids).fetchall()

        items = [dict(r) for r in rows]
        self._attach_queue_job_metadata(conn, items)
        items_by_id = {item["queue_id"]: item for item in items}
        return [items_by_id[qid] for qid in queue_ids if qid in items_by_id]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def complete_queue_item(self, queue_id: int,
                            print_job_id: Optional[int] = None) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        row = conn.execute("""
            SELECT wo_id, job_id
            FROM queue_items WHERE queue_id = ?
        """, (queue_id,)).fetchone()
        if not row:
            conn.close()
            return False

        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'completed',
                completed_at = ?,
                print_job_id = COALESCE(?, print_job_id)
            WHERE queue_id = ? AND status = 'printing'
        """, (now, print_job_id, queue_id))
        changed = cursor.rowcount > 0

        if changed:
            if row["job_id"]:
                conn.execute("""
                    UPDATE jobs
                    SET print_job_id = COALESCE(?, print_job_id)
                    WHERE job_id = ?
                """, (print_job_id, row["job_id"]))
                self._sync_job_status(conn, row["job_id"])
            self._update_wo_status_from_items(conn, row["wo_id"])
            conn.commit()
        else:
            conn.rollback()

        conn.close()
        return changed

    def fail_queue_item(self, queue_id: int) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        row = conn.execute("""
            SELECT wo_id, job_id
            FROM queue_items WHERE queue_id = ?
        """, (queue_id,)).fetchone()
        if not row:
            conn.close()
            return False

        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'failed', completed_at = ?
            WHERE queue_id = ? AND status = 'printing'
        """, (now, queue_id))
        changed = cursor.rowcount > 0
        if changed:
            if row["job_id"]:
                self._sync_job_status(conn, row["job_id"])
            self._update_wo_status_from_items(conn, row["wo_id"])
            conn.commit()
        else:
            conn.rollback()
        conn.close()
        return changed

    def requeue_item(self, queue_id: int) -> bool:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT wo_id, job_id, queue_job_id
            FROM queue_items WHERE queue_id = ?
        """, (queue_id,)).fetchone()
        if not row:
            conn.close()
            return False

        params = []
        if row["queue_job_id"]:
            where_clause = "queue_job_id = ?"
            params.append(row["queue_job_id"])
        else:
            where_clause = "queue_id = ?"
            params.append(queue_id)

        cursor = conn.execute("""
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
            WHERE {}
              AND status IN ('failed', 'upload_failed', 'start_failed')
        """.format(where_clause), params)
        changed = cursor.rowcount > 0
        if changed:
            if row["job_id"]:
                self._sync_job_status(conn, row["job_id"])
            self._update_wo_status_from_items(conn, row["wo_id"])
            conn.commit()
        else:
            conn.rollback()
        conn.close()
        return changed

    # ------------------------------------------------------------------
    # Cancel / Retry (bulk)
    # ------------------------------------------------------------------

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
                self._sync_job_status(conn, jid)
            for wid in wo_ids:
                self._update_wo_status_from_items(conn, wid)

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
                self._sync_job_status(conn, jid)
            for wid in wo_ids:
                self._update_wo_status_from_items(conn, wid)

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

    def find_printing_item_by_filename(self, printer_id: str,
                                       filename: str) -> Optional[dict]:
        """Find an in-flight queue_item on ``printer_id`` matching ``filename``.

        Widened from ``status='printing'`` to any ACTIVE_QUEUE_STATUSES
        state so completion routing can resolve queue_items that never
        reached 'printing' (the stuck-in-'starting' bug). Kept the
        historical name; the docstring records the widened semantics.
        """
        placeholders = ",".join("?" for _ in self.ACTIVE_QUEUE_STATUSES)
        active_params = tuple(self.ACTIVE_QUEUE_STATUSES)
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM queue_items
            WHERE assigned_printer_id = ?
              AND gcode_file = ?
              AND status IN ({})
            ORDER BY queue_id DESC LIMIT 1
        """.format(placeholders),
                           (printer_id, filename) + active_params
                           ).fetchone()

        if not row and filename:
            bare = filename.rsplit("/", 1)[-1] if "/" in filename else filename
            row = conn.execute("""
                SELECT * FROM queue_items
                WHERE assigned_printer_id = ?
                  AND (gcode_file = ? OR gcode_file LIKE ?)
                  AND status IN ({})
                ORDER BY queue_id DESC LIMIT 1
            """.format(placeholders),
                              (printer_id, bare, "%" + bare) + active_params
                              ).fetchone()

        conn.close()
        return dict(row) if row else None

    def link_print_job(self, queue_id: int, print_job_id: int) -> None:
        conn = self._get_conn()
        conn.execute("""
            UPDATE queue_items SET print_job_id = ?
            WHERE queue_id = ?
        """, (print_job_id, queue_id))
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Status rollup helpers — delegate to app.domains.work_orders.status_sync
    # ------------------------------------------------------------------

    def _sync_job_status(self, conn, job_id: int) -> None:
        status_sync.sync_job_status(conn, job_id)

    def _update_wo_status_from_items(self, conn, wo_id: str) -> None:
        status_sync.sync_work_order_status(conn, wo_id)
