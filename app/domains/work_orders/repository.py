"""Work-order and line-item persistence."""

import sqlite3
from datetime import datetime, timezone
from typing import Optional, List


class WorkOrderRepository:
    """Manages work orders and line items in the work_orders.db file."""

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
        if not WorkOrderRepository._has_column(conn, table, column):
            conn.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(
                    table, column, col_def)
            )
            conn.commit()

    def _init_tables(self):
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

            CREATE INDEX IF NOT EXISTS idx_wo_status
                ON work_orders(status);
        """)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # WO Number Generation
    # ------------------------------------------------------------------

    def _next_wo_id(self, conn) -> str:
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
    # Status Derivation
    # ------------------------------------------------------------------

    ACTIVE_QUEUE_STATUSES = ("uploading", "uploaded", "starting", "printing")
    FAILURE_QUEUE_STATUSES = ("upload_failed", "start_failed", "failed")

    @staticmethod
    def _derive_work_order_status(statuses: List[str]) -> str:
        active_statuses = [s for s in statuses if s != "cancelled"]

        if not active_statuses and statuses:
            return "cancelled"
        if active_statuses and all(s == "completed" for s in active_statuses):
            return "completed"
        if any(s in (WorkOrderRepository.ACTIVE_QUEUE_STATUSES
                     + WorkOrderRepository.FAILURE_QUEUE_STATUSES
                     + ("completed",))
               for s in active_statuses):
            return "in_progress"
        return "open"

    def _update_wo_status_from_items(self, conn, wo_id: str):
        rows = conn.execute(
            "SELECT status FROM queue_items WHERE wo_id = ?",
            (wo_id,)
        ).fetchall()
        if not rows:
            return

        statuses = [r["status"] for r in rows]
        now = datetime.now(timezone.utc).isoformat()
        new_status = self._derive_work_order_status(statuses)
        completed_at = now if new_status in ("completed", "cancelled") else None
        conn.execute("""
            UPDATE work_orders
            SET status = ?,
                completed_at = ?
            WHERE wo_id = ?
        """, (new_status, completed_at, wo_id))

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_work_order(self, customer_name: str,
                          line_items: List[dict]) -> dict:
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
        conn = self._get_conn()
        wo = conn.execute(
            "SELECT * FROM work_orders WHERE wo_id = ?", (wo_id,)
        ).fetchone()
        if not wo:
            conn.close()
            return None

        result = dict(wo)

        li_rows = conn.execute(
            "SELECT * FROM line_items WHERE wo_id = ? ORDER BY item_id",
            (wo_id,)
        ).fetchall()
        result["line_items"] = [dict(r) for r in li_rows]

        qi_rows = conn.execute(
            "SELECT qi.*, qj.status AS queue_job_status "
            "FROM queue_items qi "
            "LEFT JOIN queue_jobs qj ON qi.queue_job_id = qj.queue_job_id "
            "WHERE qi.wo_id = ? "
            "ORDER BY qi.item_id, qi.sequence_number",
            (wo_id,)
        ).fetchall()
        result["queue_items"] = [dict(r) for r in qi_rows]
        self._attach_queue_job_metadata(conn, result["queue_items"])

        result["jobs"] = self._get_work_order_jobs(conn, wo_id)
        result["job_count"] = len(result["jobs"])

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
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute("""
            UPDATE queue_items
            SET status = 'cancelled', completed_at = ?
            WHERE wo_id = ?
              AND status IN ('queued', 'uploading', 'uploaded', 'starting')
        """, (now, wo_id))

        cursor = conn.execute("""
            UPDATE work_orders
            SET status = 'cancelled', completed_at = ?
            WHERE wo_id = ?
        """, (now, wo_id))
        conn.execute("""
            UPDATE jobs
            SET status = 'cancelled',
                completed_at = ?
            WHERE wo_id = ?
        """, (now, wo_id))
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def work_order_exists(self, conn, wo_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM work_orders WHERE wo_id = ?",
            (wo_id,)
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Job summary helpers (used by get_work_order)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_job_summary(row) -> dict:
        job = dict(row)
        for key in ("part_count", "completed_parts", "queued_parts",
                    "printing_parts", "failed_parts",
                    "print_session_count"):
            job[key] = int(job.get(key) or 0)
        return job

    @staticmethod
    def _derive_job_status(statuses: List[str]) -> str:
        active_statuses = [s for s in statuses if s != "cancelled"]

        if not active_statuses and statuses:
            return "cancelled"
        if active_statuses and all(s == "completed" for s in active_statuses):
            return "completed"
        if any(s in WorkOrderRepository.ACTIVE_QUEUE_STATUSES
               for s in active_statuses):
            return "in_progress"
        if any(s in WorkOrderRepository.FAILURE_QUEUE_STATUSES
               for s in active_statuses):
            return "attention"
        if any(s == "completed" for s in active_statuses):
            return "in_progress"
        return "open"

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

    def _attach_queue_job_metadata(self, conn,
                                   queue_items: List[dict]) -> None:
        queue_job_ids = sorted({
            item.get("queue_job_id") for item in queue_items
            if item.get("queue_job_id")
        })
        if not queue_job_ids:
            return

        placeholders = ",".join("?" for _ in queue_job_ids)
        rows = conn.execute("""
            SELECT queue_job_id,
                   COUNT(*) AS queue_job_part_count,
                   GROUP_CONCAT(part_name, ', ') AS queue_job_part_names
            FROM queue_items
            WHERE queue_job_id IN ({})
            GROUP BY queue_job_id
        """.format(placeholders), queue_job_ids).fetchall()
        summaries = {row["queue_job_id"]: dict(row) for row in rows}

        for item in queue_items:
            summary = summaries.get(item.get("queue_job_id"))
            if summary:
                item["queue_job_part_count"] = summary["queue_job_part_count"]
                item["queue_job_part_names"] = summary["queue_job_part_names"]
                item["job_part_count"] = summary["queue_job_part_count"]
                item["job_part_names"] = summary["queue_job_part_names"]
