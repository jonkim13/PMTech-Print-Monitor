"""Low-level SQL helpers for the weekly operations log.

Each function opens its own connection to exactly one SQLite file and
returns plain dicts/lists. Cross-database joining happens in Python.
This module is intentionally thin — the service layer composes these
helpers into report sections.
"""

import json
import sqlite3


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ----------------------------------------------------------------------
# production_log.db
# ----------------------------------------------------------------------

def print_jobs_in_week(db_path, start_iso, next_iso):
    """Return all jobs whose started_at falls in [start_iso, next_iso)."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT job_id, printer_id, printer_name, file_name,
               file_display_name, status, started_at, completed_at,
               print_duration_sec, filament_type, filament_used_g,
               filament_used_mm, filament_used_source, spool_id,
               spool_material, spool_brand, operator_initials,
               operator, notes, outcome, tool_spools
        FROM print_jobs
        WHERE started_at >= ? AND started_at < ?
        ORDER BY started_at ASC, job_id ASC
    """, (start_iso, next_iso)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def material_usage_in_week(db_path, start_iso, next_iso):
    """Return material_usage rows with their related job info."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT mu.usage_id, mu.spool_id, mu.job_id, mu.printer_id,
               mu.grams_used, mu.mm_used, mu.tool_index,
               mu.usage_source, mu.timestamp,
               pj.file_name, pj.file_display_name, pj.printer_name,
               pj.started_at
        FROM material_usage mu
        LEFT JOIN print_jobs pj ON mu.job_id = pj.job_id
        WHERE mu.timestamp >= ? AND mu.timestamp < ?
        ORDER BY mu.timestamp ASC
    """, (start_iso, next_iso)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def machine_log_in_week(db_path, start_iso, next_iso):
    """Return machine_log rows in the week with JSON details parsed."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT log_id, printer_id, printer_name, event_type,
               event_timestamp, details, total_print_hours_at_event
        FROM machine_log
        WHERE event_timestamp >= ? AND event_timestamp < ?
        ORDER BY event_timestamp ASC
    """, (start_iso, next_iso)).fetchall()
    conn.close()
    result = []
    for row in rows:
        data = dict(row)
        data["details_parsed"] = _parse_details(data.get("details"))
        result.append(data)
    return result


def _parse_details(raw):
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


# ----------------------------------------------------------------------
# print_history.db
# ----------------------------------------------------------------------

def print_history_in_week(db_path, start_iso, next_iso, limit=2000):
    """Return print_history rows whose timestamp falls within the week."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT id, timestamp, printer_id, printer_name, event_type,
               filename, from_status, to_status, duration_sec
        FROM print_history
        WHERE timestamp >= ? AND timestamp < ?
        ORDER BY timestamp ASC, id ASC
        LIMIT ?
    """, (start_iso, next_iso, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# FilamentInventory.db
# ----------------------------------------------------------------------

def filament_all(db_path):
    """Return every filament spool (for joining to usage/assignments)."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT id, material, brand, color, supplier, grams,
               diameter, batch, operator, date_ins
        FROM Filament
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def spools_added_in_week(db_path, week_start_date, week_end_date):
    """Return spools whose date_ins falls within the week.

    date_ins is stored as YYYY-MM-DD (no time), so we compare dates
    lexicographically instead of ISO timestamps.
    """
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT id, material, brand, color, supplier, grams,
               diameter, batch, operator, date_ins
        FROM Filament
        WHERE date_ins >= ? AND date_ins <= ?
        ORDER BY date_ins ASC, id ASC
    """, (week_start_date.isoformat(), week_end_date.isoformat())).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# assignments.db
# ----------------------------------------------------------------------

def assignments_changed_in_week(db_path, start_iso, next_iso):
    """Return assignment rows whose assigned_at falls in the week."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT printer_id, tool_index, spool_id, assigned_at
        FROM filament_assignments
        WHERE assigned_at >= ? AND assigned_at < ?
        ORDER BY assigned_at ASC
    """, (start_iso, next_iso)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ----------------------------------------------------------------------
# work_orders.db
# ----------------------------------------------------------------------

def work_orders_created_in_week(db_path, start_iso, next_iso):
    """Return work orders whose created_at falls in the week."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT wo.wo_id, wo.customer_name, wo.created_at, wo.status,
               wo.completed_at,
               COUNT(qi.queue_id) AS total_parts,
               SUM(CASE WHEN qi.status = 'completed'
                        THEN 1 ELSE 0 END) AS completed_parts,
               SUM(CASE WHEN qi.status IN ('failed', 'upload_failed',
                                           'start_failed')
                        THEN 1 ELSE 0 END) AS failed_parts
        FROM work_orders wo
        LEFT JOIN queue_items qi ON wo.wo_id = qi.wo_id
        WHERE wo.created_at >= ? AND wo.created_at < ?
        GROUP BY wo.wo_id
        ORDER BY wo.created_at ASC
    """, (start_iso, next_iso)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def work_orders_completed_in_week(db_path, start_iso, next_iso):
    """Return work orders whose completed_at falls in the week."""
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT wo.wo_id, wo.customer_name, wo.created_at, wo.status,
               wo.completed_at,
               COUNT(qi.queue_id) AS total_parts,
               SUM(CASE WHEN qi.status = 'completed'
                        THEN 1 ELSE 0 END) AS completed_parts,
               SUM(CASE WHEN qi.status IN ('failed', 'upload_failed',
                                           'start_failed')
                        THEN 1 ELSE 0 END) AS failed_parts
        FROM work_orders wo
        LEFT JOIN queue_items qi ON wo.wo_id = qi.wo_id
        WHERE wo.completed_at IS NOT NULL
          AND wo.completed_at >= ?
          AND wo.completed_at < ?
        GROUP BY wo.wo_id
        ORDER BY wo.completed_at ASC
    """, (start_iso, next_iso)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def work_orders_active_during_week(db_path, start_iso, next_iso):
    """Return WOs that had any queue_items activity during the week.

    Excludes already-returned "created this week" and "completed this
    week" from callers at the service layer so each section is distinct.
    """
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT wo.wo_id, wo.customer_name, wo.created_at, wo.status,
               wo.completed_at,
               COUNT(qi.queue_id) AS total_parts,
               SUM(CASE WHEN qi.status = 'completed'
                        THEN 1 ELSE 0 END) AS completed_parts,
               SUM(CASE WHEN qi.status IN ('failed', 'upload_failed',
                                           'start_failed')
                        THEN 1 ELSE 0 END) AS failed_parts
        FROM work_orders wo
        LEFT JOIN queue_items qi ON wo.wo_id = qi.wo_id
        WHERE EXISTS (
            SELECT 1 FROM queue_items qi2
            WHERE qi2.wo_id = wo.wo_id
              AND (
                (qi2.queued_at >= ? AND qi2.queued_at < ?)
                OR (qi2.started_at IS NOT NULL
                    AND qi2.started_at >= ? AND qi2.started_at < ?)
                OR (qi2.completed_at IS NOT NULL
                    AND qi2.completed_at >= ? AND qi2.completed_at < ?)
              )
        )
        GROUP BY wo.wo_id
        ORDER BY wo.created_at ASC
    """, (start_iso, next_iso, start_iso, next_iso,
          start_iso, next_iso)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def queue_items_activity_in_week(db_path, start_iso, next_iso):
    """Return queue_items with any timestamp falling in the week.

    Used for parts-summary counts and timeline events.
    """
    conn = _connect(db_path)
    rows = conn.execute("""
        SELECT queue_id, wo_id, part_name, material, customer_name,
               status, assigned_printer_id, assigned_printer_name,
               queued_at, started_at, completed_at
        FROM queue_items
        WHERE (queued_at >= ? AND queued_at < ?)
           OR (started_at IS NOT NULL
               AND started_at >= ? AND started_at < ?)
           OR (completed_at IS NOT NULL
               AND completed_at >= ? AND completed_at < ?)
    """, (start_iso, next_iso, start_iso, next_iso,
          start_iso, next_iso)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
