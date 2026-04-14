"""
Print Job Repository
====================
SQLite-backed persistence for production print jobs (ISO 9001 traceability).
Extracted from production_db.py — behavior preserved exactly.
"""

import json as _json
import sqlite3
from datetime import datetime, timezone


class PrintJobRepository:
    """Read/write access to production print_jobs table."""

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
            CREATE TABLE IF NOT EXISTS print_jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                printer_id TEXT NOT NULL,
                printer_name TEXT NOT NULL,
                file_name TEXT,
                file_display_name TEXT,
                status TEXT NOT NULL DEFAULT 'started',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                print_duration_sec INTEGER DEFAULT 0,
                filament_type TEXT,
                filament_used_g REAL DEFAULT 0,
                filament_used_mm REAL DEFAULT 0,
                filament_used_source TEXT DEFAULT 'none',
                spool_id TEXT,
                spool_material TEXT,
                spool_brand TEXT,
                layer_height REAL,
                nozzle_diameter REAL,
                fill_density REAL,
                nozzle_temp REAL,
                bed_temp REAL,
                operator_initials TEXT,
                operator TEXT DEFAULT 'unassigned',
                notes TEXT DEFAULT '',
                outcome TEXT DEFAULT 'unknown',
                snapshot_path TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_printer ON print_jobs(printer_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON print_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_started ON print_jobs(started_at);
        """)
        conn.commit()

        # Migrate: add tool_spools column to print_jobs if missing
        self._add_column_if_missing(conn, "print_jobs", "tool_spools",
                                    "TEXT DEFAULT '{}'")
        # Migrate: add operator_initials column to print_jobs if missing
        self._add_column_if_missing(conn, "print_jobs", "operator_initials",
                                    "TEXT")
        # Migrate: add filament_used_source to print_jobs if missing
        self._add_column_if_missing(
            conn, "print_jobs", "filament_used_source",
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
    # Print Jobs
    # ------------------------------------------------------------------

    def create_job(self, printer_id, printer_name, file_name,
                   file_display_name=None, filament_type=None,
                   filament_used_g=0, filament_used_mm=0,
                   spool_id=None, spool_material=None, spool_brand=None,
                   layer_height=None, nozzle_diameter=None,
                   fill_density=None, nozzle_temp=None, bed_temp=None,
                   tool_spools=None, operator_initials=None):
        """Create a new print job record when a print starts.
        Poll-loop dedup: if a 'started' job for this printer+file_name
        was created within the last 120 seconds, reuse it instead of
        inserting a duplicate. The window is intentionally short —
        the status poller only fires every few seconds, so genuine
        duplicates collapse instantly, while a legitimate re-print of
        the same gcode (minutes/hours later) gets its own production
        record. The prior 24-hour window collapsed same-day re-runs
        into one row and lost per-run traceability.

        tool_spools: dict mapping tool_index -> {spool_id, material,
        brand, color} for ISO 9001 traceability of multi-tool prints.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        operator_initials = str(operator_initials or "").strip() or None

        # Check for existing active job with same file on same printer
        # within the short poll-dedup window.
        existing = conn.execute("""
            SELECT job_id, operator_initials FROM print_jobs
            WHERE printer_id = ? AND file_name = ? AND status = 'started'
              AND started_at >= datetime('now', '-120 seconds')
            ORDER BY job_id DESC LIMIT 1
        """, (printer_id, file_name)).fetchone()
        if existing:
            if operator_initials and not existing["operator_initials"]:
                conn.execute("""
                    UPDATE print_jobs
                    SET operator_initials = ?
                    WHERE job_id = ?
                """, (operator_initials, existing["job_id"]))
                conn.commit()
            conn.close()
            return existing["job_id"]

        # Log when a new job is created despite a prior row existing
        # for the same printer+file — useful post-fix for diagnosing
        # re-print vs. polling-duplicate patterns.
        prior = conn.execute("""
            SELECT job_id, status, started_at FROM print_jobs
            WHERE printer_id = ? AND file_name = ?
            ORDER BY job_id DESC LIMIT 1
        """, (printer_id, file_name)).fetchone()
        if prior:
            print(f"[PRODUCTION] Creating new job for {printer_id} / "
                  f"{file_name} (prior job #{prior['job_id']} "
                  f"status={prior['status']}, "
                  f"started={prior['started_at']})")

        tool_spools_json = _json.dumps(tool_spools) if tool_spools else "{}"

        cursor = conn.execute("""
            INSERT INTO print_jobs
                (printer_id, printer_name, file_name, file_display_name,
                 status, started_at, filament_type, filament_used_g,
                 filament_used_mm, spool_id, spool_material, spool_brand,
                 layer_height, nozzle_diameter, fill_density,
                 nozzle_temp, bed_temp, tool_spools, operator_initials,
                 created_at)
            VALUES (?, ?, ?, ?, 'started', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (printer_id, printer_name, file_name,
              file_display_name or file_name,
              now, filament_type, filament_used_g, filament_used_mm,
              spool_id, spool_material, spool_brand,
              layer_height, nozzle_diameter, fill_density,
              nozzle_temp, bed_temp, tool_spools_json,
              operator_initials, now))
        job_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return job_id

    def complete_job(self, job_id, duration_sec=0, filament_used_g=0,
                     filament_used_mm=0, filament_used_source="none",
                     snapshot_path=None):
        """Mark a job as completed. Idempotent: skips if already completed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        # Only update if the job hasn't already been completed/failed
        conn.execute("""
            UPDATE print_jobs
            SET status = 'completed',
                completed_at = ?,
                print_duration_sec = ?,
                filament_used_g = CASE WHEN ? > 0 THEN ? ELSE filament_used_g END,
                filament_used_mm = CASE WHEN ? > 0 THEN ? ELSE filament_used_mm END,
                filament_used_source = ?,
                snapshot_path = COALESCE(?, snapshot_path)
            WHERE job_id = ? AND completed_at IS NULL
        """, (now, duration_sec,
              filament_used_g, filament_used_g,
              filament_used_mm, filament_used_mm,
              filament_used_source or "none",
              snapshot_path, job_id))
        conn.commit()
        conn.close()

    def fail_job(self, job_id, duration_sec=0):
        """Mark a job as failed. Idempotent: skips if already completed/failed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        conn.execute("""
            UPDATE print_jobs
            SET status = 'failed', completed_at = ?, print_duration_sec = ?
            WHERE job_id = ? AND completed_at IS NULL
        """, (now, duration_sec, job_id))
        conn.commit()
        conn.close()

    def stop_job(self, job_id, duration_sec=0):
        """Mark a job as stopped."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        conn.execute("""
            UPDATE print_jobs
            SET status = 'stopped', completed_at = ?, print_duration_sec = ?
            WHERE job_id = ?
        """, (now, duration_sec, job_id))
        conn.commit()
        conn.close()

    def update_job_qc(self, job_id, outcome=None, operator=None, notes=None):
        """Update QC fields on a job (outcome, operator, notes)."""
        conn = self._get_connection()
        fields = []
        params = []
        if outcome is not None:
            fields.append("outcome = ?")
            params.append(outcome)
        if operator is not None:
            fields.append("operator = ?")
            params.append(operator)
        if notes is not None:
            fields.append("notes = ?")
            params.append(notes)
        if not fields:
            conn.close()
            return False
        params.append(job_id)
        conn.execute(
            f"UPDATE print_jobs SET {', '.join(fields)} WHERE job_id = ?",
            params
        )
        conn.commit()
        conn.close()
        return True

    def get_job(self, job_id):
        """Get a single job by ID."""
        conn = self._get_connection()
        row = conn.execute(
            "SELECT * FROM print_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_active_job(self, printer_id):
        """Get the currently active (started) job for a printer."""
        conn = self._get_connection()
        row = conn.execute("""
            SELECT * FROM print_jobs
            WHERE printer_id = ? AND status = 'started'
            ORDER BY job_id DESC LIMIT 1
        """, (printer_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_jobs(self, printer_id=None, status=None, outcome=None,
                 material=None, date_from=None, date_to=None,
                 limit=100, offset=0):
        """Get jobs with optional filters."""
        conn = self._get_connection()
        query = "SELECT * FROM print_jobs WHERE 1=1"
        params = []
        if printer_id:
            query += " AND printer_id = ?"
            params.append(printer_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        if outcome:
            query += " AND outcome = ?"
            params.append(outcome)
        if material:
            query += " AND (filament_type = ? OR spool_material = ?)"
            params.extend([material, material])
        if date_from:
            query += " AND started_at >= ?"
            params.append(date_from)
        if date_to:
            query += " AND started_at <= ?"
            params.append(date_to)
        query += " ORDER BY job_id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
