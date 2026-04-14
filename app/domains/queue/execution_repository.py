"""Queue execution session (queue_jobs) persistence."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List

from app.domains.queue.execution_lifecycle import QueueExecutionLifecycleMixin
from app.domains.work_orders import status_sync


class QueueExecutionRepository(QueueExecutionLifecycleMixin):
    """Manages queue_jobs execution sessions in work_orders.db."""

    ACTIVE_QUEUE_STATUSES = status_sync.ACTIVE_QUEUE_STATUSES
    FAILURE_QUEUE_STATUSES = status_sync.FAILURE_QUEUE_STATUSES
    PRINTABLE_QUEUE_STATUSES = ("queued", "failed", "upload_failed",
                                "start_failed")

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
        if not QueueExecutionRepository._has_column(conn, table, column):
            conn.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(
                    table, column, col_def)
            )
            conn.commit()

    def _init_tables(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS queue_jobs (
                queue_job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                wo_id TEXT NOT NULL,
                job_id INTEGER,
                status TEXT NOT NULL DEFAULT 'uploading',
                assigned_printer_id TEXT,
                assigned_printer_name TEXT,
                gcode_file TEXT,
                upload_session_id TEXT,
                operator_initials TEXT,
                print_job_id INTEGER,
                created_at TEXT NOT NULL,
                assigned_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (wo_id) REFERENCES work_orders(wo_id),
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            );

            CREATE INDEX IF NOT EXISTS idx_queue_jobs_status
                ON queue_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_queue_jobs_printer
                ON queue_jobs(assigned_printer_id);
        """)
        self._add_column_if_missing(conn, "queue_jobs", "job_id", "INTEGER")
        self._add_column_if_missing(
            conn, "queue_jobs", "operator_initials", "TEXT"
        )
        self._add_column_if_missing(
            conn, "queue_jobs", "upload_session_id", "TEXT"
        )
        if self._has_column(conn, "queue_jobs", "job_id"):
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_queue_jobs_job
                ON queue_jobs(job_id)
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_jobs_upload_session
            ON queue_jobs(upload_session_id)
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
        items_by_id = {item["queue_id"]: item for item in items}
        return [items_by_id[qid] for qid in queue_ids if qid in items_by_id]

    def _get_queue_job_by_id(self, conn, queue_job_id: int) -> Optional[dict]:
        row = conn.execute("""
            SELECT * FROM queue_jobs WHERE queue_job_id = ?
        """, (queue_job_id,)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Status rollup helpers — delegate to status_sync module
    # ------------------------------------------------------------------

    def _sync_job_status(self, conn, job_id: int) -> None:
        status_sync.sync_job_status(conn, job_id)

    def _update_wo_status_from_items(self, conn, wo_id: str) -> None:
        status_sync.sync_work_order_status(conn, wo_id)

    # ------------------------------------------------------------------
    # Resolve or create work-order job for execution
    # ------------------------------------------------------------------

    def _resolve_work_order_job_id(self, conn, items: List[dict],
                                   requested_job_id: Optional[int]
                                   ) -> Optional[int]:
        result = self._resolve_work_order_job_id_detail(
            conn, items, requested_job_id
        )
        if result is None:
            return None
        return result[0]

    def _resolve_work_order_job_id_detail(
        self, conn, items: List[dict],
        requested_job_id: Optional[int],
    ):
        """Like _resolve_work_order_job_id but also reports whether the
        resolved job row was newly created by this call.

        Returns (job_id, was_newly_created) or None when the request
        can't be satisfied.
        """
        if not items:
            return None

        wo_id = items[0]["wo_id"]
        existing_job_ids = {
            item.get("job_id") for item in items if item.get("job_id")
        }

        if requested_job_id is not None:
            job = conn.execute("""
                SELECT job_id FROM jobs
                WHERE job_id = ? AND wo_id = ?
            """, (requested_job_id, wo_id)).fetchone()
            if not job:
                return None
            if any(item.get("job_id") not in (None, requested_job_id)
                   for item in items):
                return None
            self._move_queue_items_to_job(conn, requested_job_id, items)
            return requested_job_id, False

        if len(existing_job_ids) > 1:
            return None

        if existing_job_ids:
            job_id = next(iter(existing_job_ids))
            self._move_queue_items_to_job(conn, job_id, items)
            return job_id, False

        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO jobs (wo_id, status, created_at)
            VALUES (?, 'open', ?)
        """, (wo_id, now))
        job_id = cursor.lastrowid
        self._move_queue_items_to_job(conn, job_id, items)
        return job_id, True

    def _move_queue_items_to_job(self, conn, job_id: int,
                                 items: List[dict]) -> None:
        if not items:
            return
        queue_ids = [item["queue_id"] for item in items]
        placeholders = ",".join("?" for _ in queue_ids)
        prior_job_ids = {
            item.get("job_id") for item in items
            if item.get("job_id") and item.get("job_id") != job_id
        }
        conn.execute("""
            UPDATE queue_items SET job_id = ?
            WHERE queue_id IN ({})
        """.format(placeholders), [job_id] + queue_ids)
        for prior_job_id in prior_job_ids:
            self._sync_job_status(conn, prior_job_id)
        self._sync_job_status(conn, job_id)

    # ------------------------------------------------------------------
    # Start Execution
    # ------------------------------------------------------------------

    def start_queue_job_execution(self, queue_ids, printer_id: str,
                                  printer_name: str,
                                  gcode_file: str,
                                  operator_initials: Optional[str] = None,
                                  job_id: Optional[int] = None) -> dict:
        queue_ids = self._normalize_queue_ids(queue_ids)
        if not queue_ids:
            raise ValueError("no items to print")

        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()

        try:
            items = self._get_queue_items_by_ids(conn, queue_ids)
            if len(items) != len(queue_ids):
                raise LookupError("one or more selected parts were not found")

            wo_ids = {item["wo_id"] for item in items}
            if len(wo_ids) != 1:
                raise ValueError(
                    "selected parts must belong to the same work order"
                )

            if any(item["status"] in self.ACTIVE_QUEUE_STATUSES
                   for item in items):
                raise RuntimeError("items already in progress")

            printable_items = [
                item for item in items
                if item["status"] in self.PRINTABLE_QUEUE_STATUSES
            ]
            if not printable_items:
                raise ValueError("no items to print")
            if len(printable_items) != len(items):
                raise ValueError(
                    "selected parts must be queued or retryable before printing"
                )

            resolved = self._resolve_work_order_job_id_detail(
                conn, items, requested_job_id=job_id
            )
            if resolved is None:
                if job_id is not None:
                    raise LookupError("job not found")
                raise ValueError(
                    "selected parts must belong to the same job "
                    "before printing"
                )
            work_order_job_id, auto_created_job = resolved

            queue_job_id = self._create_queue_job_session(
                conn, items[0]["wo_id"], work_order_job_id,
                printer_id, printer_name, gcode_file,
                operator_initials, now,
            )

            placeholders = ",".join("?" for _ in queue_ids)
            cursor = conn.execute("""
                UPDATE queue_items
                SET status = 'uploading',
                    job_id = ?,
                    queue_job_id = ?,
                    assigned_printer_id = ?,
                    assigned_printer_name = ?,
                    gcode_file = ?,
                    upload_session_id = NULL,
                    print_job_id = NULL,
                    assigned_at = ?,
                    started_at = NULL,
                    completed_at = NULL
                WHERE queue_id IN ({})
                  AND status IN ('queued', 'failed', 'upload_failed',
                                 'start_failed')
            """.format(placeholders),
                                  [work_order_job_id, queue_job_id, printer_id,
                                   printer_name, gcode_file, now]
                                  + queue_ids)

            if cursor.rowcount != len(queue_ids):
                raise RuntimeError("items already in progress")

            conn.execute("""
                UPDATE jobs
                SET status = 'in_progress',
                    printer_id = ?,
                    printer_name = ?,
                    gcode_file = ?,
                    operator_initials = ?,
                    started_at = NULL,
                    completed_at = NULL
                WHERE job_id = ?
            """, (printer_id, printer_name, gcode_file, operator_initials,
                  work_order_job_id))
            self._sync_job_status(conn, work_order_job_id)
            self._update_wo_status_from_items(conn, items[0]["wo_id"])
            conn.commit()
            return {
                "queue_job_id": queue_job_id,
                "job_id": work_order_job_id,
                "wo_id": items[0]["wo_id"],
                "queue_ids": queue_ids,
                "auto_created_job": auto_created_job,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _create_queue_job_session(self, conn, wo_id, job_id, printer_id,
                                  printer_name, gcode_file,
                                  operator_initials, created_at) -> int:
        cursor = conn.execute("""
            INSERT INTO queue_jobs
                (wo_id, job_id, status, assigned_printer_id,
                 assigned_printer_name, gcode_file, upload_session_id,
                 operator_initials, created_at, assigned_at)
            VALUES (?, ?, 'uploading', ?, ?, ?, NULL, ?, ?, ?)
        """, (wo_id, job_id, printer_id, printer_name, gcode_file,
              operator_initials, created_at, created_at))
        return cursor.lastrowid
