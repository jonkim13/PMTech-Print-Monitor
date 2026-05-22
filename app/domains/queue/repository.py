"""Queue item persistence."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List

from app.domains.work_orders import status_sync
from app.shared.sqlite_migrations import add_column_if_missing, has_column


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
        add_column_if_missing(conn, "queue_items", "job_id", "INTEGER")
        add_column_if_missing(
            conn, "queue_items", "queue_job_id", "INTEGER"
        )
        add_column_if_missing(
            conn, "queue_items", "upload_session_id", "TEXT"
        )
        if has_column(conn, "queue_items", "job_id"):
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

    # Bulk cancel/requeue operations live in
    # app/domains/queue/bulk_operations.py (extracted in Phase 5f).

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
