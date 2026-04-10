"""
Material Usage Repository
=========================
SQLite-backed persistence for production material usage (ISO 9001 traceability).
Extracted from production_db.py — behavior preserved exactly.
"""

import sqlite3
from datetime import datetime, timezone


class MaterialUsageRepository:
    """Read/write access to production material_usage table."""

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
            CREATE TABLE IF NOT EXISTS material_usage (
                usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                spool_id TEXT,
                job_id INTEGER,
                printer_id TEXT NOT NULL,
                grams_used REAL DEFAULT 0,
                mm_used REAL DEFAULT 0,
                usage_source TEXT DEFAULT 'none',
                timestamp TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES print_jobs(job_id)
            );

            CREATE INDEX IF NOT EXISTS idx_material_spool ON material_usage(spool_id);
            CREATE INDEX IF NOT EXISTS idx_material_job ON material_usage(job_id);
        """)
        conn.commit()

        # Migrate: add tool_index column to material_usage if missing
        self._add_column_if_missing(conn, "material_usage", "tool_index",
                                    "INTEGER DEFAULT 0")
        # Migrate: add usage_source column to material_usage if missing
        self._add_column_if_missing(
            conn, "material_usage", "usage_source",
            "TEXT DEFAULT 'none'"
        )
        conn.close()

    @staticmethod
    def _add_column_if_missing(conn, table, column, col_def):
        """Add a column to a table if it doesn't already exist."""
        cursor = conn.execute(f"PRAGMA table_info({table})")
        columns = [row[1] for row in cursor.fetchall()]
        if column not in columns:
            conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
            conn.commit()

    # ------------------------------------------------------------------
    # Material Usage
    # ------------------------------------------------------------------

    def log_material_usage(self, spool_id, job_id, printer_id,
                           grams_used=0, mm_used=0, tool_index=0,
                           usage_source="none"):
        """Log material usage for a job, optionally per tool."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        conn.execute("""
            INSERT INTO material_usage
                (spool_id, job_id, printer_id, grams_used, mm_used,
                 tool_index, usage_source, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (spool_id, job_id, printer_id, grams_used, mm_used,
              tool_index, usage_source or "none", now))
        conn.commit()
        conn.close()

    def get_spool_usage(self, spool_id, limit=100):
        """Get all usage records for a specific spool."""
        conn = self._get_connection()
        rows = conn.execute("""
            SELECT mu.*, pj.file_name, pj.file_display_name,
                   pj.printer_name, pj.started_at
            FROM material_usage mu
            LEFT JOIN print_jobs pj ON mu.job_id = pj.job_id
            WHERE mu.spool_id = ?
            ORDER BY mu.usage_id DESC LIMIT ?
        """, (spool_id, limit)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_spool_totals(self, spool_id):
        """Get total material consumed from a spool."""
        conn = self._get_connection()
        row = conn.execute("""
            SELECT COALESCE(SUM(grams_used), 0) as total_grams,
                   COALESCE(SUM(mm_used), 0) as total_mm,
                   COUNT(*) as job_count
            FROM material_usage WHERE spool_id = ?
        """, (spool_id,)).fetchone()
        conn.close()
        return dict(row)
