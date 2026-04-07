"""
Upload Session Database
=======================
Dedicated SQLite tracking for staged G-code uploads.

This lives in its own database because both direct printer uploads and
work-order queue executions need the same durable retry state without
coupling upload mechanics to production logging or work-order ownership.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional


class UploadSessionRepository:
    """Durable upload-session tracking for upload/verify/start workflows."""

    TERMINAL_STATUSES = {
        "uploaded",
        "printing",
        "upload_failed",
        "start_failed",
        "cancelled",
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _has_column(conn, table: str, column: str) -> bool:
        rows = conn.execute("PRAGMA table_info({})".format(table)).fetchall()
        return any(row[1] == column for row in rows)

    @classmethod
    def _add_column_if_missing(cls, conn, table: str,
                               column: str, col_def: str) -> None:
        if cls._has_column(conn, table, column):
            return
        conn.execute(
            "ALTER TABLE {} ADD COLUMN {} {}".format(table, column, col_def)
        )
        conn.commit()

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS upload_sessions (
                upload_session_id TEXT PRIMARY KEY,
                printer_id TEXT NOT NULL,
                queue_job_id INTEGER,
                work_order_job_id INTEGER,
                original_filename TEXT NOT NULL,
                staged_path TEXT NOT NULL,
                remote_filename TEXT NOT NULL,
                remote_storage TEXT NOT NULL DEFAULT 'usb',
                file_size_bytes INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                operator_initials TEXT,
                parsed_grams REAL,
                parsed_grams_source TEXT DEFAULT 'none',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                last_error TEXT
            )
        """)
        self._add_column_if_missing(
            conn, "upload_sessions", "queue_job_id", "INTEGER"
        )
        self._add_column_if_missing(
            conn, "upload_sessions", "work_order_job_id", "INTEGER"
        )
        self._add_column_if_missing(
            conn, "upload_sessions", "operator_initials", "TEXT"
        )
        self._add_column_if_missing(
            conn, "upload_sessions", "parsed_grams", "REAL"
        )
        self._add_column_if_missing(
            conn, "upload_sessions", "parsed_grams_source",
            "TEXT DEFAULT 'none'"
        )
        self._add_column_if_missing(
            conn, "upload_sessions", "completed_at", "TEXT"
        )
        self._add_column_if_missing(
            conn, "upload_sessions", "last_error", "TEXT"
        )
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_upload_sessions_printer
            ON upload_sessions(printer_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_upload_sessions_queue_job
            ON upload_sessions(queue_job_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_upload_sessions_status
            ON upload_sessions(status)
        """)
        conn.commit()
        conn.close()

    def create_session(self, upload_session_id: str, printer_id: str,
                       original_filename: str, staged_path: str,
                       remote_filename: str, remote_storage: str,
                       file_size_bytes: int, status: str = "staged",
                       queue_job_id: int = None,
                       work_order_job_id: int = None,
                       operator_initials: str = None,
                       parsed_grams: float = None,
                       parsed_grams_source: str = "none") -> dict:
        now = self._now()
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO upload_sessions (
                upload_session_id,
                printer_id,
                queue_job_id,
                work_order_job_id,
                original_filename,
                staged_path,
                remote_filename,
                remote_storage,
                file_size_bytes,
                status,
                operator_initials,
                parsed_grams,
                parsed_grams_source,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            upload_session_id,
            printer_id,
            queue_job_id,
            work_order_job_id,
            original_filename,
            staged_path,
            remote_filename,
            remote_storage,
            int(file_size_bytes or 0),
            status,
            operator_initials,
            parsed_grams,
            parsed_grams_source or "none",
            now,
            now,
        ))
        conn.commit()
        conn.close()
        return self.get_session(upload_session_id)

    def get_session(self, upload_session_id: str) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT *
            FROM upload_sessions
            WHERE upload_session_id = ?
        """, (upload_session_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_latest_session_for_queue_job(self,
                                         queue_job_id: int) -> Optional[dict]:
        conn = self._get_conn()
        row = conn.execute("""
            SELECT *
            FROM upload_sessions
            WHERE queue_job_id = ?
            ORDER BY created_at DESC, upload_session_id DESC
            LIMIT 1
        """, (queue_job_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_session(self, upload_session_id: str, **fields) -> Optional[dict]:
        if not fields:
            return self.get_session(upload_session_id)

        updates = []
        params = []
        for key, value in fields.items():
            updates.append("{} = ?".format(key))
            params.append(value)
        updates.append("updated_at = ?")
        params.append(self._now())
        params.append(upload_session_id)

        conn = self._get_conn()
        conn.execute("""
            UPDATE upload_sessions
            SET {}
            WHERE upload_session_id = ?
        """.format(", ".join(updates)), params)
        conn.commit()
        conn.close()
        return self.get_session(upload_session_id)

    def set_status(self, upload_session_id: str, status: str,
                   last_error: str = None,
                   operator_initials: str = None,
                   completed: bool = None) -> Optional[dict]:
        fields = {
            "status": status,
            "last_error": last_error,
        }
        if operator_initials is not None:
            fields["operator_initials"] = operator_initials

        if completed is None:
            completed = status in self.TERMINAL_STATUSES
        fields["completed_at"] = self._now() if completed else None
        return self.update_session(upload_session_id, **fields)


UploadSessionDB = UploadSessionRepository

