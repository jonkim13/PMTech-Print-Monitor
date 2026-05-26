"""
Print Job Repository
====================
SQLite-backed persistence for production print jobs (ISO 9001 traceability).
Extracted from production_db.py — behavior preserved exactly.
"""

import json as _json
import sqlite3
from datetime import datetime, timezone

from app.shared.sqlite_migrations import add_column_if_missing


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
        add_column_if_missing(conn, "print_jobs", "tool_spools",
                              "TEXT DEFAULT '{}'")
        # Migrate: add operator_initials column to print_jobs if missing
        add_column_if_missing(conn, "print_jobs", "operator_initials",
                              "TEXT")
        # Migrate: add filament_used_source to print_jobs if missing
        add_column_if_missing(
            conn, "print_jobs", "filament_used_source",
            "TEXT DEFAULT 'none'"
        )
        # Phase 6 — link a production job back to the upload_session that
        # carried the slicer-parsed metadata. Lets the completion path
        # (which skips the post-FINISHED /api/v1/job blank-payload call)
        # look up parsed per-tool data when writing material_usage rows.
        add_column_if_missing(
            conn, "print_jobs", "upload_session_id", "TEXT"
        )
        conn.close()

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

        # Phase 6 — state-based dedup for the FAT32 long↔short filename
        # flip (audit #20). USB-stick prints with long filenames trigger
        # Prusa firmware to report the file under the 8.3 truncated form
        # on one API path and the long form on another, so the
        # (printer_id, file_name) filter above misses. Catch the
        # duplicate via the invariant "at most one status='started'
        # row per printer within the last 24h", which is the same
        # invariant complete_job/fail_job/stop_job already enforce on
        # close. The 24h upper bound prevents accidental absorption
        # onto a stale orphan from some other (non-FAT32) source —
        # Migration 004 reconciles pre-existing orphans separately,
        # and any new orphan source emits the [WARN] line below.
        # julianday() is used because started_at is Python ISO format
        # ('YYYY-MM-DDTHH:MM:SS+00:00') while SQLite's datetime()
        # returns space-separated text — a lex comparison would treat
        # the 'T' at position 10 as greater than the space and always
        # return TRUE, defeating the 24h bound.
        started = conn.execute("""
            SELECT job_id, operator_initials FROM print_jobs
            WHERE printer_id = ? AND status = 'started'
              AND julianday('now') - julianday(started_at) < 1
            ORDER BY job_id DESC
        """, (printer_id,)).fetchall()
        if len(started) == 1:
            existing_job_id = started[0]["job_id"]
            existing_initials = started[0]["operator_initials"]
            if operator_initials and not existing_initials:
                conn.execute("""
                    UPDATE print_jobs
                    SET operator_initials = ?
                    WHERE job_id = ?
                """, (operator_initials, existing_job_id))
                conn.commit()
            conn.close()
            return existing_job_id
        if len(started) > 1:
            # Invariant violation — should not happen after Migration
            # 004. Log clearly and fall through to insert; do not
            # silently pick one. This is the smoking gun for a
            # recurring non-FAT32 orphan source (see audit #22).
            print(
                "[WARN] create_job: {n} status='started' rows within "
                "24h for printer_id={pid}; expected at most 1. "
                "Existing job_ids: {ids}, incoming file_name={fn}. "
                "Falling through to insert.".format(
                    n=len(started), pid=printer_id,
                    ids=[r["job_id"] for r in started],
                    fn=file_name,
                )
            )

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

    def set_parsed_meta(self, job_id, upload_session_id=None, parsed=None):
        """Stamp the slicer-parsed meta + upload_session linkage onto a job.

        Called from ``ProductionHandler.start`` after ``create_job`` once
        the upload_session has been looked up. ``parsed`` is the dict
        shape from ``app.shared.gcode_metadata.parse_print_metadata`` —
        when ``parsed_filament_used_g`` is non-null we treat that as the
        authoritative source and stamp ``filament_used_source='parsed'``
        so the completion path skips the doomed API re-read.

        ``upload_session_id`` is always written when provided (it's the
        completion-time linkage even for prints where parsing failed —
        it lets us recover partial metadata or per-tool arrays later).
        """
        if not job_id:
            return
        fields = []
        params = []
        if upload_session_id is not None:
            fields.append("upload_session_id = ?")
            params.append(upload_session_id)
        if parsed and parsed.get("parsed_filament_used_g") is not None:
            grams = parsed.get("parsed_filament_used_g")
            mm = parsed.get("parsed_filament_used_mm") or 0
            fields.extend([
                "filament_used_g = ?",
                "filament_used_mm = ?",
                "filament_type = COALESCE(?, filament_type)",
                "layer_height = COALESCE(?, layer_height)",
                "nozzle_diameter = COALESCE(?, nozzle_diameter)",
                "fill_density = COALESCE(?, fill_density)",
                "nozzle_temp = COALESCE(?, nozzle_temp)",
                "bed_temp = COALESCE(?, bed_temp)",
                "filament_used_source = 'parsed'",
            ])
            params.extend([
                float(grams),
                float(mm) if mm else 0.0,
                parsed.get("parsed_filament_type"),
                parsed.get("parsed_layer_height"),
                parsed.get("parsed_nozzle_diameter"),
                parsed.get("parsed_fill_density"),
                parsed.get("parsed_nozzle_temp"),
                parsed.get("parsed_bed_temp"),
            ])
        if not fields:
            return
        params.append(job_id)
        conn = self._get_connection()
        conn.execute(
            "UPDATE print_jobs SET {} WHERE job_id = ?".format(
                ", ".join(fields)
            ),
            params,
        )
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
