"""
Database Models
================
SQLite-backed database for print history.
Filament inventory and assignments have been extracted to domain modules.
"""
import sqlite3
from datetime import datetime, timezone

# ============================================================
# PRINT HISTORY DATABASE
# ============================================================
class PrintHistoryDB:
    """SQLite-backed print history log."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS print_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                printer_id TEXT NOT NULL,
                printer_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                filename TEXT,
                from_status TEXT,
                to_status TEXT,
                duration_sec INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def log_event(self, event: dict):
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO print_history
                (timestamp, printer_id, printer_name, event_type,
                 filename, from_status, to_status, duration_sec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event.get("timestamp", datetime.now(timezone.utc).isoformat()),
            event.get("printer_id", ""),
            event.get("printer_name", ""),
            event.get("type", "unknown"),
            event.get("filename", ""),
            event.get("from_status", ""),
            event.get("to_status", ""),
            event.get("duration_sec", 0),
        ))
        conn.commit()
        conn.close()

    def get_history(self, limit: int = 100) -> list:
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT * FROM print_history
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        conn = self._get_conn()
        total = conn.execute(
            "SELECT COUNT(*) FROM print_history"
        ).fetchone()[0]
        completed = conn.execute(
            "SELECT COUNT(*) FROM print_history WHERE event_type='print_complete'"
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM print_history WHERE event_type='printer_error'"
        ).fetchone()[0]
        started = conn.execute(
            "SELECT COUNT(*) FROM print_history WHERE event_type='print_started'"
        ).fetchone()[0]

        # Per-printer stats
        per_printer_rows = conn.execute("""
            SELECT printer_name, COUNT(*) as count
            FROM print_history
            WHERE event_type = 'print_complete'
            GROUP BY printer_name
        """).fetchall()
        per_printer = {r["printer_name"]: r["count"] for r in per_printer_rows}

        # Average duration of completed prints
        avg_row = conn.execute("""
            SELECT AVG(duration_sec) as avg_dur
            FROM print_history
            WHERE event_type = 'print_complete' AND duration_sec > 0
        """).fetchone()
        avg_duration = avg_row["avg_dur"] if avg_row["avg_dur"] else 0

        conn.close()

        success_rate = 0
        if completed + failed > 0:
            success_rate = round(completed / (completed + failed) * 100, 1)

        return {
            "total_events": total,
            "completed": completed,
            "failed": failed,
            "started": started,
            "success_rate": success_rate,
            "per_printer": per_printer,
            "avg_duration_sec": round(avg_duration),
        }
