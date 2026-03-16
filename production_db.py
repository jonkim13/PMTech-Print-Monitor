"""
Production Log Database (ISO 9001 Traceability)
=================================================
SQLite-backed tables for print jobs, machine events,
and material usage tracking.
"""

import os
import sqlite3
from datetime import datetime, timezone


class ProductionDB:
    """Full production traceability database."""

    def __init__(self, db_path: str, snapshots_dir: str = None):
        self.db_path = db_path
        self.snapshots_dir = snapshots_dir
        if self.snapshots_dir:
            os.makedirs(self.snapshots_dir, exist_ok=True)
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_conn()
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
                spool_id TEXT,
                spool_material TEXT,
                spool_brand TEXT,
                layer_height REAL,
                nozzle_diameter REAL,
                fill_density REAL,
                nozzle_temp REAL,
                bed_temp REAL,
                operator TEXT DEFAULT 'unassigned',
                notes TEXT DEFAULT '',
                outcome TEXT DEFAULT 'unknown',
                snapshot_path TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS machine_log (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                printer_id TEXT NOT NULL,
                printer_name TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_timestamp TEXT NOT NULL,
                details TEXT DEFAULT '{}',
                total_print_hours_at_event REAL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS material_usage (
                usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                spool_id TEXT,
                job_id INTEGER,
                printer_id TEXT NOT NULL,
                grams_used REAL DEFAULT 0,
                mm_used REAL DEFAULT 0,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES print_jobs(job_id)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_printer ON print_jobs(printer_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON print_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_started ON print_jobs(started_at);
            CREATE INDEX IF NOT EXISTS idx_machine_printer ON machine_log(printer_id);
            CREATE INDEX IF NOT EXISTS idx_material_spool ON material_usage(spool_id);
            CREATE INDEX IF NOT EXISTS idx_material_job ON material_usage(job_id);
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Print Jobs
    # ------------------------------------------------------------------

    def create_job(self, printer_id, printer_name, file_name,
                   file_display_name=None, filament_type=None,
                   filament_used_g=0, filament_used_mm=0,
                   spool_id=None, spool_material=None, spool_brand=None,
                   layer_height=None, nozzle_diameter=None,
                   fill_density=None, nozzle_temp=None, bed_temp=None):
        """Create a new print job record when a print starts."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute("""
            INSERT INTO print_jobs
                (printer_id, printer_name, file_name, file_display_name,
                 status, started_at, filament_type, filament_used_g,
                 filament_used_mm, spool_id, spool_material, spool_brand,
                 layer_height, nozzle_diameter, fill_density,
                 nozzle_temp, bed_temp, created_at)
            VALUES (?, ?, ?, ?, 'started', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (printer_id, printer_name, file_name,
              file_display_name or file_name,
              now, filament_type, filament_used_g, filament_used_mm,
              spool_id, spool_material, spool_brand,
              layer_height, nozzle_diameter, fill_density,
              nozzle_temp, bed_temp, now))
        job_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return job_id

    def complete_job(self, job_id, duration_sec=0, filament_used_g=0,
                     filament_used_mm=0, snapshot_path=None):
        """Mark a job as completed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE print_jobs
            SET status = 'completed',
                completed_at = ?,
                print_duration_sec = ?,
                filament_used_g = CASE WHEN ? > 0 THEN ? ELSE filament_used_g END,
                filament_used_mm = CASE WHEN ? > 0 THEN ? ELSE filament_used_mm END,
                snapshot_path = COALESCE(?, snapshot_path)
            WHERE job_id = ?
        """, (now, duration_sec,
              filament_used_g, filament_used_g,
              filament_used_mm, filament_used_mm,
              snapshot_path, job_id))
        conn.commit()
        conn.close()

    def fail_job(self, job_id, duration_sec=0):
        """Mark a job as failed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE print_jobs
            SET status = 'failed', completed_at = ?, print_duration_sec = ?
            WHERE job_id = ?
        """, (now, duration_sec, job_id))
        conn.commit()
        conn.close()

    def stop_job(self, job_id, duration_sec=0):
        """Mark a job as stopped."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            UPDATE print_jobs
            SET status = 'stopped', completed_at = ?, print_duration_sec = ?
            WHERE job_id = ?
        """, (now, duration_sec, job_id))
        conn.commit()
        conn.close()

    def update_job_qc(self, job_id, outcome=None, operator=None, notes=None):
        """Update QC fields on a job (outcome, operator, notes)."""
        conn = self._get_conn()
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
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM print_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_active_job(self, printer_id):
        """Get the currently active (started) job for a printer."""
        conn = self._get_conn()
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
        conn = self._get_conn()
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

    # ------------------------------------------------------------------
    # Machine Log
    # ------------------------------------------------------------------

    def log_machine_event(self, printer_id, printer_name, event_type,
                          details=None):
        """Log a machine event. Auto-calculates total print hours."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        # Calculate total print hours for this printer
        row = conn.execute("""
            SELECT COALESCE(SUM(print_duration_sec), 0) as total_sec
            FROM print_jobs
            WHERE printer_id = ? AND status = 'completed'
        """, (printer_id,)).fetchone()
        total_hours = round(row["total_sec"] / 3600.0, 2) if row else 0

        import json
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
        conn = self._get_conn()
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
        conn = self._get_conn()
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

    # ------------------------------------------------------------------
    # Material Usage
    # ------------------------------------------------------------------

    def log_material_usage(self, spool_id, job_id, printer_id,
                           grams_used=0, mm_used=0):
        """Log material usage for a job."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO material_usage
                (spool_id, job_id, printer_id, grams_used, mm_used, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (spool_id, job_id, printer_id, grams_used, mm_used, now))
        conn.commit()
        conn.close()

    def get_spool_usage(self, spool_id, limit=100):
        """Get all usage records for a specific spool."""
        conn = self._get_conn()
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
        conn = self._get_conn()
        row = conn.execute("""
            SELECT COALESCE(SUM(grams_used), 0) as total_grams,
                   COALESCE(SUM(mm_used), 0) as total_mm,
                   COUNT(*) as job_count
            FROM material_usage WHERE spool_id = ?
        """, (spool_id,)).fetchone()
        conn.close()
        return dict(row)

    # ------------------------------------------------------------------
    # CSV Export
    # ------------------------------------------------------------------

    def export_jobs_csv(self, date_from=None, date_to=None):
        """Export print jobs as CSV string."""
        jobs = self.get_jobs(date_from=date_from, date_to=date_to,
                             limit=100000, offset=0)
        return self._to_csv(jobs, [
            "job_id", "printer_id", "printer_name", "file_name",
            "file_display_name", "status", "started_at", "completed_at",
            "print_duration_sec", "filament_type", "filament_used_g",
            "filament_used_mm", "spool_id", "spool_material", "spool_brand",
            "layer_height", "nozzle_diameter", "fill_density",
            "nozzle_temp", "bed_temp", "operator", "notes", "outcome",
        ])

    def export_machine_csv(self, date_from=None, date_to=None):
        """Export machine log as CSV string."""
        logs = self.get_machine_log(date_from=date_from, date_to=date_to,
                                    limit=100000, offset=0)
        return self._to_csv(logs, [
            "log_id", "printer_id", "printer_name", "event_type",
            "event_timestamp", "details", "total_print_hours_at_event",
        ])

    def export_materials_csv(self, date_from=None, date_to=None):
        """Export material usage as CSV string."""
        conn = self._get_conn()
        query = """
            SELECT mu.*, pj.file_name, pj.printer_name
            FROM material_usage mu
            LEFT JOIN print_jobs pj ON mu.job_id = pj.job_id
            WHERE 1=1
        """
        params = []
        if date_from:
            query += " AND mu.timestamp >= ?"
            params.append(date_from)
        if date_to:
            query += " AND mu.timestamp <= ?"
            params.append(date_to)
        query += " ORDER BY mu.usage_id DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        data = [dict(r) for r in rows]
        return self._to_csv(data, [
            "usage_id", "spool_id", "job_id", "printer_id",
            "printer_name", "file_name", "grams_used", "mm_used",
            "timestamp",
        ])

    @staticmethod
    def _to_csv(rows, columns):
        """Convert a list of dicts to a CSV string."""
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(c, "") for c in columns])
        return output.getvalue()
