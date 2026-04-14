"""Work-order job persistence and status derivation."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List

from app.domains.work_orders import status_sync


class JobRepository:
    """Manages persisted work-order jobs in the work_orders.db file."""

    ACTIVE_QUEUE_STATUSES = status_sync.ACTIVE_QUEUE_STATUSES
    FAILURE_QUEUE_STATUSES = status_sync.FAILURE_QUEUE_STATUSES

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
        if not JobRepository._has_column(conn, table, column):
            conn.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(
                    table, column, col_def)
            )
            conn.commit()

    def _init_tables(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                wo_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                printer_id TEXT,
                printer_name TEXT,
                gcode_file TEXT,
                operator_initials TEXT,
                print_job_id INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (wo_id) REFERENCES work_orders(wo_id)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_wo
                ON jobs(wo_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status);
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Status Derivation
    # ------------------------------------------------------------------

    _derive_job_status = staticmethod(status_sync.derive_job_status)

    def sync_job_status(self, conn, job_id: int) -> None:
        """Recalculate a persisted job status from its queue items."""
        status_sync.sync_job_status(conn, job_id)

    # ------------------------------------------------------------------
    # Job Summary
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_job_summary(row) -> dict:
        job = dict(row)
        for key in ("part_count", "completed_parts", "queued_parts",
                    "printing_parts", "failed_parts",
                    "print_session_count"):
            job[key] = int(job.get(key) or 0)
        return job

    def _get_job_summary(self, conn, job_id: int) -> Optional[dict]:
        row = conn.execute("""
            SELECT j.*,
                   (
                       SELECT qj.queue_job_id
                       FROM queue_jobs qj
                       WHERE qj.job_id = j.job_id
                       ORDER BY COALESCE(qj.assigned_at, qj.created_at) DESC,
                                qj.queue_job_id DESC
                       LIMIT 1
                   ) AS latest_queue_job_id,
                   (
                       SELECT qj.status
                       FROM queue_jobs qj
                       WHERE qj.job_id = j.job_id
                       ORDER BY COALESCE(qj.assigned_at, qj.created_at) DESC,
                                qj.queue_job_id DESC
                       LIMIT 1
                   ) AS latest_queue_job_status,
                   (
                       SELECT COUNT(*)
                       FROM queue_jobs qj
                       WHERE qj.job_id = j.job_id
                   ) AS print_session_count,
                   COUNT(qi.queue_id) AS part_count,
                   SUM(CASE WHEN qi.status = 'completed'
                            THEN 1 ELSE 0 END) AS completed_parts,
                   SUM(CASE WHEN qi.status = 'queued'
                            THEN 1 ELSE 0 END) AS queued_parts,
                   SUM(CASE WHEN qi.status IN ('uploading', 'uploaded',
                                               'starting', 'printing')
                            THEN 1 ELSE 0 END) AS printing_parts,
                   SUM(CASE WHEN qi.status IN ('upload_failed', 'start_failed',
                                               'failed')
                            THEN 1 ELSE 0 END) AS failed_parts
            FROM jobs j
            LEFT JOIN queue_items qi ON qi.job_id = j.job_id
            WHERE j.job_id = ?
            GROUP BY j.job_id
        """, (job_id,)).fetchone()
        if not row:
            return None
        return self._normalize_job_summary(row)

    def _get_work_order_jobs(self, conn, wo_id: str) -> list:
        rows = conn.execute("""
            SELECT j.*,
                   (
                       SELECT qj.queue_job_id
                       FROM queue_jobs qj
                       WHERE qj.job_id = j.job_id
                       ORDER BY COALESCE(qj.assigned_at, qj.created_at) DESC,
                                qj.queue_job_id DESC
                       LIMIT 1
                   ) AS latest_queue_job_id,
                   (
                       SELECT qj.status
                       FROM queue_jobs qj
                       WHERE qj.job_id = j.job_id
                       ORDER BY COALESCE(qj.assigned_at, qj.created_at) DESC,
                                qj.queue_job_id DESC
                       LIMIT 1
                   ) AS latest_queue_job_status,
                   (
                       SELECT COUNT(*)
                       FROM queue_jobs qj
                       WHERE qj.job_id = j.job_id
                   ) AS print_session_count,
                   COUNT(qi.queue_id) AS part_count,
                   SUM(CASE WHEN qi.status = 'completed'
                            THEN 1 ELSE 0 END) AS completed_parts,
                   SUM(CASE WHEN qi.status = 'queued'
                            THEN 1 ELSE 0 END) AS queued_parts,
                   SUM(CASE WHEN qi.status IN ('uploading', 'uploaded',
                                               'starting', 'printing')
                            THEN 1 ELSE 0 END) AS printing_parts,
                   SUM(CASE WHEN qi.status IN ('upload_failed', 'start_failed',
                                               'failed')
                            THEN 1 ELSE 0 END) AS failed_parts
            FROM jobs j
            LEFT JOIN queue_items qi ON qi.job_id = j.job_id
            WHERE j.wo_id = ?
            GROUP BY j.job_id
            ORDER BY j.created_at ASC, j.job_id ASC
        """, (wo_id,)).fetchall()
        return [self._normalize_job_summary(row) for row in rows]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_work_order_jobs(self, wo_id: str) -> Optional[list]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM work_orders WHERE wo_id = ?", (wo_id,)
        ).fetchone()
        if not row:
            conn.close()
            return None
        jobs = self._get_work_order_jobs(conn, wo_id)
        conn.close()
        return jobs

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

    def _create_job_row(self, conn, wo_id: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO jobs (wo_id, status, created_at)
            VALUES (?, 'open', ?)
        """, (wo_id, now))
        return cursor.lastrowid

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
            UPDATE queue_items
            SET job_id = ?
            WHERE queue_id IN ({})
        """.format(placeholders), [job_id] + queue_ids)

        for prior_job_id in prior_job_ids:
            self.sync_job_status(conn, prior_job_id)
        self.sync_job_status(conn, job_id)

    def _validate_job_assignment(self, conn, wo_id: str,
                                 queue_ids, job_id: int = None) -> list:
        queue_ids = self._normalize_queue_ids(queue_ids)
        if not queue_ids:
            raise ValueError("At least one part must be selected")

        items = self._get_queue_items_by_ids(conn, queue_ids)
        if len(items) != len(queue_ids):
            raise LookupError("One or more selected parts were not found")

        if any(item["wo_id"] != wo_id for item in items):
            raise ValueError(
                "Selected parts must belong to the same work order"
            )

        if any(item["status"] not in ("queued", "failed") for item in items):
            raise ValueError(
                "Only queued or failed parts can be assigned to a job"
            )

        if job_id is not None:
            job = conn.execute("""
                SELECT job_id
                FROM jobs
                WHERE job_id = ? AND wo_id = ?
            """, (job_id, wo_id)).fetchone()
            if not job:
                raise LookupError("Job not found")

        return items

    def create_job(self, wo_id: str, queue_ids=None) -> dict:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM work_orders WHERE wo_id = ?", (wo_id,)
            ).fetchone()
            if not row:
                raise LookupError("Work order not found")

            items = []
            if queue_ids:
                items = self._validate_job_assignment(conn, wo_id, queue_ids)

            job_id = self._create_job_row(conn, wo_id)
            if items:
                self._move_queue_items_to_job(conn, job_id, items)

            conn.commit()
            return self._get_job_summary(conn, job_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def assign_queue_items_to_job(self, wo_id: str,
                                  job_id: int,
                                  queue_ids) -> dict:
        conn = self._get_conn()
        try:
            items = self._validate_job_assignment(
                conn, wo_id, queue_ids, job_id=job_id
            )
            self._move_queue_items_to_job(conn, job_id, items)
            conn.commit()
            return self._get_job_summary(conn, job_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_job_queue_items(self, job_id: int) -> Optional[list]:
        conn = self._get_conn()
        try:
            job = conn.execute("""
                SELECT job_id FROM jobs WHERE job_id = ?
            """, (job_id,)).fetchone()
            if not job:
                return None

            rows = conn.execute("""
                SELECT qi.*, qj.status AS queue_job_status
                FROM queue_items qi
                LEFT JOIN queue_jobs qj ON qi.queue_job_id = qj.queue_job_id
                WHERE qi.job_id = ?
                ORDER BY qi.item_id ASC, qi.sequence_number ASC, qi.queue_id ASC
            """, (job_id,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
