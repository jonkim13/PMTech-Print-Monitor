"""
Work Order Database
====================
SQLite-backed tables for work orders, line items, and
production queue management.
"""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List


class WorkOrderDB:
    """Manages work orders, line items, and production queue."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS work_orders (
                wo_id TEXT PRIMARY KEY,
                customer_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS line_items (
                item_id INTEGER PRIMARY KEY AUTOINCREMENT,
                wo_id TEXT NOT NULL,
                part_name TEXT NOT NULL,
                material TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (wo_id) REFERENCES work_orders(wo_id)
            );

            CREATE TABLE IF NOT EXISTS queue_items (
                queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                wo_id TEXT NOT NULL,
                part_name TEXT NOT NULL,
                material TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                total_quantity INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                assigned_printer_id TEXT,
                assigned_printer_name TEXT,
                gcode_file TEXT,
                print_job_id INTEGER,
                queued_at TEXT NOT NULL,
                assigned_at TEXT,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (item_id) REFERENCES line_items(item_id),
                FOREIGN KEY (wo_id) REFERENCES work_orders(wo_id)
            );

            CREATE INDEX IF NOT EXISTS idx_queue_status
                ON queue_items(status);
            CREATE INDEX IF NOT EXISTS idx_queue_wo
                ON queue_items(wo_id);
            CREATE INDEX IF NOT EXISTS idx_queue_printer
                ON queue_items(assigned_printer_id);
            CREATE INDEX IF NOT EXISTS idx_wo_status
                ON work_orders(status);
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # WO Number Generation
    # ------------------------------------------------------------------

    def _next_wo_id(self, conn) -> str:
        """Generate next WO-NNN id."""
        row = conn.execute(
            "SELECT wo_id FROM work_orders ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            try:
                num = int(row["wo_id"].split("-")[1]) + 1
            except (IndexError, ValueError):
                num = 1
        else:
            num = 1
        return "WO-{:03d}".format(num)

    # ------------------------------------------------------------------
    # Work Orders
    # ------------------------------------------------------------------

    def create_work_order(self, customer_name: str,
                          line_items: List[dict]) -> dict:
        """Create a work order with line items and queue items.

        line_items: [{"part_name": str, "material": str, "quantity": int}]
        Returns the created work order dict with wo_id.
        """
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()

        wo_id = self._next_wo_id(conn)

        conn.execute("""
            INSERT INTO work_orders (wo_id, customer_name, created_at, status)
            VALUES (?, ?, ?, 'open')
        """, (wo_id, customer_name, now))

        for li in line_items:
            part_name = li["part_name"]
            material = li["material"]
            quantity = max(1, int(li.get("quantity", 1)))

            cursor = conn.execute("""
                INSERT INTO line_items
                    (wo_id, part_name, material, quantity, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (wo_id, part_name, material, quantity, now))
            item_id = cursor.lastrowid

            # Create individual queue items for each unit
            for seq in range(1, quantity + 1):
                conn.execute("""
                    INSERT INTO queue_items
                        (item_id, wo_id, part_name, material,
                         customer_name, sequence_number, total_quantity,
                         status, queued_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """, (item_id, wo_id, part_name, material,
                      customer_name, seq, quantity, now))

        conn.commit()
        conn.close()
        return {"wo_id": wo_id, "customer_name": customer_name,
                "status": "open", "created_at": now}

    def get_work_order(self, wo_id: str) -> Optional[dict]:
        """Get a work order with its line items and queue items."""
        conn = self._get_conn()
        wo = conn.execute(
            "SELECT * FROM work_orders WHERE wo_id = ?", (wo_id,)
        ).fetchone()
        if not wo:
            conn.close()
            return None

        result = dict(wo)

        # Line items
        li_rows = conn.execute(
            "SELECT * FROM line_items WHERE wo_id = ? ORDER BY item_id",
            (wo_id,)
        ).fetchall()
        result["line_items"] = [dict(r) for r in li_rows]

        # Queue items
        qi_rows = conn.execute(
            "SELECT * FROM queue_items WHERE wo_id = ? "
            "ORDER BY item_id, sequence_number",
            (wo_id,)
        ).fetchall()
        result["queue_items"] = [dict(r) for r in qi_rows]

        # Counts
        total = len(result["queue_items"])
        completed = sum(1 for q in result["queue_items"]
                        if q["status"] == "completed")
        result["total_parts"] = total
        result["completed_parts"] = completed

        conn.close()
        return result

    def get_all_work_orders(self, status: Optional[str] = None,
                            limit: int = 100,
                            offset: int = 0) -> list:
        """Get all work orders with summary counts."""
        conn = self._get_conn()
        query = """
            SELECT wo.*,
                COUNT(qi.queue_id) as total_parts,
                SUM(CASE WHEN qi.status = 'completed' THEN 1 ELSE 0 END)
                    as completed_parts
            FROM work_orders wo
            LEFT JOIN queue_items qi ON wo.wo_id = qi.wo_id
        """
        params = []  # type: list
        if status:
            query += " WHERE wo.status = ?"
            params.append(status)
        query += " GROUP BY wo.wo_id ORDER BY wo.created_at DESC"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def update_work_order_status(self, wo_id: str,
                                 status: str) -> bool:
        """Update work order status."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        completed_at = now if status in ("completed", "cancelled") else None

        cursor = conn.execute("""
            UPDATE work_orders
            SET status = ?, completed_at = COALESCE(?, completed_at)
            WHERE wo_id = ?
        """, (status, completed_at, wo_id))
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def cancel_work_order(self, wo_id: str) -> bool:
        """Cancel a work order and all its queued/assigned items."""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        # Only cancel items that aren't already completed
        conn.execute("""
            UPDATE queue_items
            SET status = 'cancelled', completed_at = ?
            WHERE wo_id = ? AND status IN ('queued', 'assigned')
        """, (now, wo_id))

        cursor = conn.execute("""
            UPDATE work_orders
            SET status = 'cancelled', completed_at = ?
            WHERE wo_id = ?
        """, (now, wo_id))
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    # ------------------------------------------------------------------
    # Queue Items
    # ------------------------------------------------------------------

    def get_queue(self, status: Optional[str] = None,
                  limit: int = 200, offset: int = 0) -> list:
        """Get the production queue, ordered FIFO by queued_at."""
        conn = self._get_conn()
        query = "SELECT * FROM queue_items WHERE 1=1"
        params = []  # type: list
        if status:
            query += " AND status = ?"
            params.append(status)
        else:
            # Exclude cancelled by default
            query += " AND status != 'cancelled'"
        query += " ORDER BY queued_at ASC, queue_id ASC"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_queue_item(self, queue_id: int) -> Optional[dict]:
        """Get a single queue item."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM queue_items WHERE queue_id = ?",
            (queue_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def assign_queue_item(self, queue_id: int, printer_id: str,
                          printer_name: str,
                          gcode_file: str) -> bool:
        """Mark a queue item as assigned to a printer."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'printing',
                assigned_printer_id = ?,
                assigned_printer_name = ?,
                gcode_file = ?,
                assigned_at = ?,
                started_at = ?
            WHERE queue_id = ? AND status IN ('queued', 'assigned')
        """, (printer_id, printer_name, gcode_file, now, now, queue_id))
        conn.commit()
        changed = cursor.rowcount > 0

        if changed:
            # Update work order to in_progress
            qi = self.get_queue_item(queue_id)
            if qi:
                self._update_wo_status_from_items(conn, qi["wo_id"])

        conn.close()
        return changed

    def complete_queue_item(self, queue_id: int,
                            print_job_id: Optional[int] = None) -> bool:
        """Mark a queue item as completed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'completed',
                completed_at = ?,
                print_job_id = COALESCE(?, print_job_id)
            WHERE queue_id = ? AND status = 'printing'
        """, (now, print_job_id, queue_id))
        conn.commit()
        changed = cursor.rowcount > 0

        if changed:
            qi = self.get_queue_item(queue_id)
            if qi:
                self._update_wo_status_from_items(conn, qi["wo_id"])

        conn.close()
        return changed

    def fail_queue_item(self, queue_id: int) -> bool:
        """Mark a queue item as failed (can be re-queued)."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'failed', completed_at = ?
            WHERE queue_id = ? AND status = 'printing'
        """, (now, queue_id))
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def requeue_item(self, queue_id: int) -> bool:
        """Re-queue a failed item back to queued status."""
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'queued',
                assigned_printer_id = NULL,
                assigned_printer_name = NULL,
                gcode_file = NULL,
                print_job_id = NULL,
                assigned_at = NULL,
                started_at = NULL,
                completed_at = NULL
            WHERE queue_id = ? AND status = 'failed'
        """, (queue_id,))
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def find_printing_item_by_filename(self, printer_id: str,
                                       filename: str) -> Optional[dict]:
        """Find an active queue item matching a printer and filename.

        Used by farm_manager to auto-update queue items when prints
        complete. Matches against the gcode_file field.
        """
        conn = self._get_conn()
        # Try exact match first
        row = conn.execute("""
            SELECT * FROM queue_items
            WHERE assigned_printer_id = ?
              AND gcode_file = ?
              AND status = 'printing'
            ORDER BY queue_id DESC LIMIT 1
        """, (printer_id, filename)).fetchone()

        if not row and filename:
            # Try matching just the filename without path
            bare = filename.rsplit("/", 1)[-1] if "/" in filename else filename
            row = conn.execute("""
                SELECT * FROM queue_items
                WHERE assigned_printer_id = ?
                  AND (gcode_file = ? OR gcode_file LIKE ?)
                  AND status = 'printing'
                ORDER BY queue_id DESC LIMIT 1
            """, (printer_id, bare, "%" + bare)).fetchone()

        conn.close()
        return dict(row) if row else None

    def link_print_job(self, queue_id: int,
                       print_job_id: int) -> None:
        """Link a production log job_id to a queue item."""
        conn = self._get_conn()
        conn.execute("""
            UPDATE queue_items SET print_job_id = ?
            WHERE queue_id = ?
        """, (print_job_id, queue_id))
        conn.commit()
        conn.close()

    def _update_wo_status_from_items(self, conn, wo_id: str):
        """Auto-update work order status based on queue items."""
        rows = conn.execute(
            "SELECT status FROM queue_items WHERE wo_id = ?",
            (wo_id,)
        ).fetchall()
        if not rows:
            return

        statuses = [r["status"] for r in rows]
        now = datetime.now(timezone.utc).isoformat()

        # All completed or cancelled → completed
        active_statuses = [s for s in statuses
                           if s not in ("cancelled",)]
        if all(s == "completed" for s in active_statuses):
            conn.execute("""
                UPDATE work_orders
                SET status = 'completed', completed_at = ?
                WHERE wo_id = ? AND status != 'completed'
            """, (now, wo_id))
            conn.commit()
        elif any(s in ("printing", "assigned") for s in statuses):
            conn.execute("""
                UPDATE work_orders
                SET status = 'in_progress'
                WHERE wo_id = ? AND status = 'open'
            """, (wo_id,))
            conn.commit()

    def get_queue_stats(self) -> dict:
        """Get summary counts for the queue."""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END) as queued,
                SUM(CASE WHEN status = 'printing' THEN 1 ELSE 0 END)
                    as printing,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END)
                    as completed,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)
                    as failed
            FROM queue_items
            WHERE status != 'cancelled'
        """).fetchone()
        conn.close()
        return dict(row) if row else {
            "total": 0, "queued": 0, "printing": 0,
            "completed": 0, "failed": 0,
        }
