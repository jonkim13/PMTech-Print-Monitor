"""
Machine Log Repository
======================
SQLite-backed persistence for production machine events (ISO 9001 traceability).
Extracted from production_db.py — behavior preserved exactly.
"""

import json
import sqlite3
from datetime import datetime, timezone


class MachineLogRepository:
    """Read/write access to production machine_log table."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS machine_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                printer_id TEXT NOT NULL,
                printer_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_timestamp TEXT NOT NULL,
                details TEXT DEFAULT '{}',
                total_print_hours_at_event REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_machine_printer ON machine_log(printer_id);
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Machine Log
    # ------------------------------------------------------------------

    def log_machine_event(self, printer_id, printer_name, event_type,
                          details=None):
        """Log a machine event. Auto-calculates total print hours."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        # Calculate total print hours for this printer
        row = conn.execute("""
            SELECT COALESCE(SUM(print_duration_sec), 0) as total_sec
            FROM print_jobs
            WHERE printer_id = ? AND status = 'completed'
        """, (printer_id,)).fetchone()
        total_hours = round(row["total_sec"] / 3600.0, 2) if row else 0

        details_str = json.dumps(details or {})
        conn.execute("""
            INSERT INTO machine_log
                (printer_id, printer_name, event_type, event_timestamp,
                 details, total_print_hours_at_event)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (printer_id, printer_name, event_type, now,
              details_str, total_hours))
        conn.commit()
        conn.close()

    def get_machine_log(self, printer_id=None, event_type=None,
                        date_from=None, date_to=None,
                        limit=100, offset=0):
        """Get machine event log with filters."""
        conn = self._get_connection()
        query = "SELECT * FROM machine_log WHERE 1=1"
        params = []
        if printer_id:
            query += " AND printer_id = ?"
            params.append(printer_id)
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if date_from:
            query += " AND event_timestamp >= ?"
            params.append(date_from)
        if date_to:
            query += " AND event_timestamp <= ?"
            params.append(date_to)
        query += " ORDER BY log_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_machine_summary(self, printer_id):
        """Get summary stats for a single printer."""
        conn = self._get_connection()
        total_jobs = conn.execute(
            "SELECT COUNT(*) FROM print_jobs WHERE printer_id = ?",
            (printer_id,)
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM print_jobs WHERE printer_id = ? AND status = 'completed'",
            (printer_id,)
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM print_jobs WHERE printer_id = ? AND status = 'failed'",
            (printer_id,)
        ).fetchone()[0]
        total_sec = conn.execute(
            "SELECT COALESCE(SUM(print_duration_sec), 0) FROM print_jobs WHERE printer_id = ? AND status = 'completed'",
            (printer_id,)
        ).fetchone()[0]

        # Current streak (consecutive completed)
        rows = conn.execute("""
            SELECT status FROM print_jobs
            WHERE printer_id = ? AND status IN ('completed', 'failed')
            ORDER BY job_id DESC LIMIT 100
        """, (printer_id,)).fetchall()
        streak = 0
        for r in rows:
            if r["status"] == "completed":
                streak += 1
            else:
                break

        # Last maintenance
        maint = conn.execute("""
            SELECT event_timestamp FROM machine_log
            WHERE printer_id = ? AND event_type = 'maintenance'
            ORDER BY log_id DESC LIMIT 1
        """, (printer_id,)).fetchone()

        conn.close()

        success_rate = 0
        if completed + failed > 0:
            success_rate = round(completed / (completed + failed) * 100, 1)

        return {
            "printer_id": printer_id,
            "total_jobs": total_jobs,
            "completed": completed,
            "failed": failed,
            "success_rate": success_rate,
            "total_print_hours": round(total_sec / 3600.0, 1),
            "current_streak": streak,
            "last_maintenance": maint["event_timestamp"] if maint else None,
        }

    def get_all_machine_summaries(self, printer_ids):
        """Get machine summaries for all printers."""
        return {pid: self.get_machine_summary(pid) for pid in printer_ids}
