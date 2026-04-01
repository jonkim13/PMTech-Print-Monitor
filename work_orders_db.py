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

    @staticmethod
    def _has_column(conn, table: str, column: str) -> bool:
        """Return True when a table already has a given column."""
        cursor = conn.execute("PRAGMA table_info({})".format(table))
        columns = [row[1] for row in cursor.fetchall()]
        return column in columns

    @staticmethod
    def _add_column_if_missing(conn, table: str,
                               column: str, col_def: str) -> None:
        """Add a column if it does not already exist."""
        if not WorkOrderDB._has_column(conn, table, column):
            conn.execute(
                "ALTER TABLE {} ADD COLUMN {} {}".format(
                    table, column, col_def)
            )
            conn.commit()

    @staticmethod
    def _normalize_queue_ids(queue_ids) -> list:
        """Normalize a sequence of queue ids into unique ints."""
        result = []
        seen = set()
        for raw_id in queue_ids or []:
            queue_id = int(raw_id)
            if queue_id in seen:
                continue
            seen.add(queue_id)
            result.append(queue_id)
        return result

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

            CREATE TABLE IF NOT EXISTS jobs (
                job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                wo_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                printer_id TEXT,
                printer_name TEXT,
                gcode_file TEXT,
                operator_initials TEXT,
                print_job_id INTEGER,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (wo_id) REFERENCES work_orders(wo_id)
            );

            CREATE TABLE IF NOT EXISTS queue_jobs (
                queue_job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                wo_id TEXT NOT NULL,
                job_id INTEGER,
                status TEXT NOT NULL DEFAULT 'printing',
                assigned_printer_id TEXT,
                assigned_printer_name TEXT,
                gcode_file TEXT,
                operator_initials TEXT,
                print_job_id INTEGER,
                created_at TEXT NOT NULL,
                assigned_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (wo_id) REFERENCES work_orders(wo_id),
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            );

            CREATE TABLE IF NOT EXISTS queue_items (
                queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                wo_id TEXT NOT NULL,
                job_id INTEGER,
                queue_job_id INTEGER,
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
                FOREIGN KEY (wo_id) REFERENCES work_orders(wo_id),
                FOREIGN KEY (job_id) REFERENCES jobs(job_id),
                FOREIGN KEY (queue_job_id) REFERENCES queue_jobs(queue_job_id)
            );

            CREATE INDEX IF NOT EXISTS idx_jobs_wo
                ON jobs(wo_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_queue_status
                ON queue_items(status);
            CREATE INDEX IF NOT EXISTS idx_queue_wo
                ON queue_items(wo_id);
            CREATE INDEX IF NOT EXISTS idx_queue_printer
                ON queue_items(assigned_printer_id);
            CREATE INDEX IF NOT EXISTS idx_queue_jobs_status
                ON queue_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_queue_jobs_printer
                ON queue_jobs(assigned_printer_id);
            CREATE INDEX IF NOT EXISTS idx_wo_status
                ON work_orders(status);
        """)
        self._add_column_if_missing(
            conn, "queue_items", "job_id",
            "INTEGER"
        )
        self._add_column_if_missing(
            conn, "queue_items", "queue_job_id",
            "INTEGER"
        )
        self._add_column_if_missing(
            conn, "queue_jobs", "job_id",
            "INTEGER"
        )
        self._add_column_if_missing(
            conn, "queue_jobs", "operator_initials",
            "TEXT"
        )
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_job
            ON queue_items(queue_job_id)
        """)
        if self._has_column(conn, "queue_items", "job_id"):
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_queue_items_job
                ON queue_items(job_id)
            """)
        if self._has_column(conn, "queue_jobs", "job_id"):
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_queue_jobs_job
                ON queue_jobs(job_id)
            """)
        conn.execute("""
            UPDATE queue_items
            SET job_id = (
                SELECT qj.job_id
                FROM queue_jobs qj
                WHERE qj.queue_job_id = queue_items.queue_job_id
            )
            WHERE job_id IS NULL
              AND queue_job_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM queue_jobs qj
                  WHERE qj.queue_job_id = queue_items.queue_job_id
                    AND qj.job_id IS NOT NULL
              )
        """)
        conn.execute("""
            UPDATE queue_jobs
            SET job_id = (
                SELECT qi.job_id
                FROM queue_items qi
                WHERE qi.queue_job_id = queue_jobs.queue_job_id
                  AND qi.job_id IS NOT NULL
                ORDER BY qi.queue_id ASC
                LIMIT 1
            )
            WHERE job_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM queue_items qi
                  WHERE qi.queue_job_id = queue_jobs.queue_job_id
                    AND qi.job_id IS NOT NULL
              )
        """)
        conn.execute("""
            UPDATE queue_jobs
            SET operator_initials = COALESCE(
                operator_initials,
                (
                    SELECT j.operator_initials
                    FROM jobs j
                    WHERE j.job_id = queue_jobs.job_id
                )
            )
            WHERE operator_initials IS NULL
              AND job_id IS NOT NULL
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

    @staticmethod
    def _normalize_job_summary(row) -> dict:
        """Normalize nullable aggregate fields on a job summary row."""
        job = dict(row)
        for key in ("part_count", "completed_parts", "queued_parts",
                    "printing_parts", "failed_parts",
                    "print_session_count"):
            job[key] = int(job.get(key) or 0)
        return job

    @staticmethod
    def _derive_job_status(statuses: List[str]) -> str:
        """Derive a stable work-order job status from child queue items."""
        active_statuses = [status for status in statuses
                           if status != "cancelled"]

        if not active_statuses and statuses:
            return "cancelled"
        if active_statuses and all(status == "completed"
                                   for status in active_statuses):
            return "completed"
        if any(status in ("printing", "assigned")
               for status in active_statuses):
            return "in_progress"
        if any(status == "failed" for status in active_statuses):
            return "attention"
        if any(status == "completed" for status in active_statuses):
            return "in_progress"
        return "open"

    @staticmethod
    def _derive_work_order_status(statuses: List[str]) -> str:
        """Derive the work-order status from its child queue items."""
        active_statuses = [status for status in statuses
                           if status != "cancelled"]

        if not active_statuses and statuses:
            return "cancelled"
        if active_statuses and all(status == "completed"
                                   for status in active_statuses):
            return "completed"
        if any(status in ("printing", "assigned", "completed", "failed")
               for status in active_statuses):
            return "in_progress"
        return "open"

    def _work_order_exists(self, conn, wo_id: str) -> bool:
        """Check whether a work order exists."""
        row = conn.execute(
            "SELECT 1 FROM work_orders WHERE wo_id = ?",
            (wo_id,)
        ).fetchone()
        return row is not None

    def _get_job_summary(self, conn, job_id: int) -> Optional[dict]:
        """Fetch a single persisted work-order job summary."""
        row = conn.execute("""
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
                   SUM(CASE WHEN qi.status = 'printing'
                            THEN 1 ELSE 0 END) AS printing_parts,
                   SUM(CASE WHEN qi.status = 'failed'
                            THEN 1 ELSE 0 END) AS failed_parts
            FROM jobs j
            LEFT JOIN queue_items qi ON qi.job_id = j.job_id
            WHERE j.job_id = ?
            GROUP BY j.job_id
        """, (job_id,)).fetchone()
        if not row:
            return None
        return self._normalize_job_summary(row)

    def _get_work_order_jobs(self, conn, wo_id: str) -> list:
        """Fetch persisted job summaries for a work order."""
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
                   SUM(CASE WHEN qi.status = 'printing'
                            THEN 1 ELSE 0 END) AS printing_parts,
                   SUM(CASE WHEN qi.status = 'failed'
                            THEN 1 ELSE 0 END) AS failed_parts
            FROM jobs j
            LEFT JOIN queue_items qi ON qi.job_id = j.job_id
            WHERE j.wo_id = ?
            GROUP BY j.job_id
            ORDER BY j.created_at ASC, j.job_id ASC
        """, (wo_id,)).fetchall()
        return [self._normalize_job_summary(row) for row in rows]

    def get_work_order_jobs(self, wo_id: str) -> Optional[list]:
        """List persisted jobs for a work order."""
        conn = self._get_conn()
        if not self._work_order_exists(conn, wo_id):
            conn.close()
            return None
        jobs = self._get_work_order_jobs(conn, wo_id)
        conn.close()
        return jobs

    def _create_job_row(self, conn, wo_id: str) -> int:
        """Create an empty persisted job for a work order."""
        now = datetime.now(timezone.utc).isoformat()
        cursor = conn.execute("""
            INSERT INTO jobs (wo_id, status, created_at)
            VALUES (?, 'open', ?)
        """, (wo_id, now))
        return cursor.lastrowid

    def _sync_job_status(self, conn, job_id: int) -> None:
        """Recalculate a persisted job status from its queue items."""
        rows = conn.execute("""
            SELECT status
            FROM queue_items
            WHERE job_id = ?
        """, (job_id,)).fetchall()
        now = datetime.now(timezone.utc).isoformat()

        if not rows:
            conn.execute("""
                UPDATE jobs
                SET status = 'open',
                    completed_at = NULL
                WHERE job_id = ?
            """, (job_id,))
            return

        statuses = [row["status"] for row in rows]
        new_status = self._derive_job_status(statuses)
        completed_at = now if new_status in ("completed", "cancelled") else None
        conn.execute("""
            UPDATE jobs
            SET status = ?,
                completed_at = ?
            WHERE job_id = ?
        """, (new_status, completed_at, job_id))

    def _move_queue_items_to_job(self, conn, job_id: int, items: List[dict]) -> None:
        """Assign queued/failed queue items to a persisted work-order job."""
        if not items:
            return

        queue_ids = [item["queue_id"] for item in items]
        placeholders = ",".join("?" for _ in queue_ids)
        prior_job_ids = {
            item.get("job_id") for item in items
            if item.get("job_id") and item.get("job_id") != job_id
        }

        conn.execute("""
            UPDATE queue_items
            SET job_id = ?
            WHERE queue_id IN ({})
        """.format(placeholders), [job_id] + queue_ids)

        for prior_job_id in prior_job_ids:
            self._sync_job_status(conn, prior_job_id)
        self._sync_job_status(conn, job_id)

    def _validate_job_assignment(self, conn, wo_id: str,
                                 queue_ids, job_id: int = None) -> list:
        """Validate queue items before assigning them to a work-order job."""
        queue_ids = self._normalize_queue_ids(queue_ids)
        if not queue_ids:
            raise ValueError("At least one part must be selected")

        items = self._get_queue_items_by_ids(conn, queue_ids)
        if len(items) != len(queue_ids):
            raise LookupError("One or more selected parts were not found")

        if any(item["wo_id"] != wo_id for item in items):
            raise ValueError(
                "Selected parts must belong to the same work order"
            )

        if any(item["status"] not in ("queued", "failed") for item in items):
            raise ValueError(
                "Only queued or failed parts can be assigned to a job"
            )

        if job_id is not None:
            job = conn.execute("""
                SELECT job_id
                FROM jobs
                WHERE job_id = ? AND wo_id = ?
            """, (job_id, wo_id)).fetchone()
            if not job:
                raise LookupError("Job not found")

        return items

    def create_job(self, wo_id: str, queue_ids=None) -> dict:
        """Create a persisted job under a work order."""
        conn = self._get_conn()
        try:
            if not self._work_order_exists(conn, wo_id):
                raise LookupError("Work order not found")

            items = []
            if queue_ids:
                items = self._validate_job_assignment(conn, wo_id, queue_ids)

            job_id = self._create_job_row(conn, wo_id)
            if items:
                self._move_queue_items_to_job(conn, job_id, items)

            conn.commit()
            return self._get_job_summary(conn, job_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def assign_queue_items_to_job(self, wo_id: str,
                                  job_id: int,
                                  queue_ids) -> dict:
        """Assign selected queue items to an existing persisted job."""
        conn = self._get_conn()
        try:
            items = self._validate_job_assignment(
                conn, wo_id, queue_ids, job_id=job_id
            )
            self._move_queue_items_to_job(conn, job_id, items)
            conn.commit()
            return self._get_job_summary(conn, job_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _resolve_work_order_job_id(self, conn, items: List[dict],
                                   requested_job_id: Optional[int]) -> Optional[int]:
        """Resolve or create the persisted job to use for a print start."""
        if not items:
            return None

        wo_id = items[0]["wo_id"]
        existing_job_ids = {
            item.get("job_id") for item in items if item.get("job_id")
        }

        if requested_job_id is not None:
            job = conn.execute("""
                SELECT job_id
                FROM jobs
                WHERE job_id = ? AND wo_id = ?
            """, (requested_job_id, wo_id)).fetchone()
            if not job:
                return None
            if any(item.get("job_id") not in (None, requested_job_id)
                   for item in items):
                return None
            self._move_queue_items_to_job(conn, requested_job_id, items)
            return requested_job_id

        if len(existing_job_ids) > 1:
            return None

        if existing_job_ids:
            job_id = next(iter(existing_job_ids))
            self._move_queue_items_to_job(conn, job_id, items)
            return job_id

        job_id = self._create_job_row(conn, wo_id)
        self._move_queue_items_to_job(conn, job_id, items)
        return job_id

    def _create_queue_job_session(self, conn, wo_id: str, job_id: int,
                                  printer_id: str, printer_name: str,
                                  gcode_file: str,
                                  operator_initials: Optional[str],
                                  created_at: str) -> int:
        """Create a printer execution session linked to one stable job."""
        cursor = conn.execute("""
            INSERT INTO queue_jobs
                (wo_id, job_id, status, assigned_printer_id,
                 assigned_printer_name, gcode_file, operator_initials,
                 created_at, assigned_at)
            VALUES (?, ?, 'printing', ?, ?, ?, ?, ?, ?)
        """, (wo_id, job_id, printer_id, printer_name, gcode_file,
              operator_initials, created_at, created_at))
        return cursor.lastrowid

    def _get_queue_job_by_id(self, conn, queue_job_id: int) -> Optional[dict]:
        """Fetch one queue-job execution session by id."""
        row = conn.execute("""
            SELECT *
            FROM queue_jobs
            WHERE queue_job_id = ?
        """, (queue_job_id,)).fetchone()
        return dict(row) if row else None

    def get_queue_job(self, queue_job_id: int) -> Optional[dict]:
        """Fetch one queue-job execution session by id."""
        conn = self._get_conn()
        try:
            return self._get_queue_job_by_id(conn, queue_job_id)
        finally:
            conn.close()

    def get_active_queue_job_for_printer(self, printer_id: str) -> Optional[dict]:
        """Fetch the current printing queue-job session for a printer."""
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT *
                FROM queue_jobs
                WHERE assigned_printer_id = ?
                  AND status = 'printing'
                ORDER BY queue_job_id DESC
                LIMIT 1
            """, (printer_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

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

    # ------------------------------------------------------------------
    # Queue Items
    # ------------------------------------------------------------------

    def get_queue(self, status: Optional[str] = None,
                  limit: int = 200, offset: int = 0) -> list:
        """Get the production queue, ordered FIFO by queued_at."""
        conn = self._get_conn()
        query = (
            "SELECT qi.*, qj.status AS queue_job_status "
            "FROM queue_items qi "
            "LEFT JOIN queue_jobs qj ON qi.queue_job_id = qj.queue_job_id "
            "WHERE 1=1"
        )
        params = []  # type: list
        if status:
            query += " AND qi.status = ?"
            params.append(status)
        else:
            # Exclude cancelled by default
            query += " AND qi.status != 'cancelled'"
        query += " ORDER BY qi.queued_at ASC, qi.queue_id ASC"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        items = [dict(r) for r in rows]
        self._attach_queue_job_metadata(conn, items)
        conn.close()
        return items

    def get_queue_item(self, queue_id: int) -> Optional[dict]:
        """Get a single queue item."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM queue_items WHERE queue_id = ?",
            (queue_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_queue_items(self, queue_ids) -> list:
        """Get multiple queue items in the same order as requested."""
        conn = self._get_conn()
        items = self._get_queue_items_by_ids(conn, queue_ids)
        conn.close()
        return items

    def get_job_queue_items(self, job_id: int) -> Optional[list]:
        """Get queue items assigned to one persisted work-order job."""
        conn = self._get_conn()
        try:
            job = conn.execute("""
                SELECT job_id
                FROM jobs
                WHERE job_id = ?
            """, (job_id,)).fetchone()
            if not job:
                return None

            rows = conn.execute("""
                SELECT qi.*, qj.status AS queue_job_status
                FROM queue_items qi
                LEFT JOIN queue_jobs qj ON qi.queue_job_id = qj.queue_job_id
                WHERE qi.job_id = ?
                ORDER BY qi.item_id ASC, qi.sequence_number ASC, qi.queue_id ASC
            """, (job_id,)).fetchall()
            items = [dict(row) for row in rows]
            self._attach_queue_job_metadata(conn, items)
            return items
        finally:
            conn.close()

    def _get_queue_items_by_ids(self, conn, queue_ids) -> list:
        """Fetch queue items by id using an existing connection."""
        queue_ids = self._normalize_queue_ids(queue_ids)
        if not queue_ids:
            return []

        placeholders = ",".join("?" for _ in queue_ids)
        rows = conn.execute("""
            SELECT qi.*, qj.status AS queue_job_status
            FROM queue_items qi
            LEFT JOIN queue_jobs qj ON qi.queue_job_id = qj.queue_job_id
            WHERE qi.queue_id IN ({})
        """.format(placeholders), queue_ids).fetchall()

        items = [dict(r) for r in rows]
        self._attach_queue_job_metadata(conn, items)
        items_by_id = {item["queue_id"]: item for item in items}
        return [items_by_id[qid] for qid in queue_ids if qid in items_by_id]

    def _attach_queue_job_metadata(self, conn, queue_items: List[dict]) -> None:
        """Attach grouped queue-job session data to queue items."""
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
                # Backward-compatible aliases for older frontend code.
                item["job_part_count"] = summary["queue_job_part_count"]
                item["job_part_names"] = summary["queue_job_part_names"]

    def assign_queue_item(self, queue_id: int, printer_id: str,
                          printer_name: str,
                          gcode_file: str,
                          operator_initials: Optional[str] = None,
                          job_id: Optional[int] = None) -> bool:
        """Mark a queue item as assigned to a printer."""
        queue_job_id = self.assign_queue_items(
            [queue_id], printer_id, printer_name, gcode_file,
            operator_initials=operator_initials, job_id=job_id)
        return queue_job_id is not None

    def start_queue_job_execution(self, queue_ids, printer_id: str,
                                  printer_name: str,
                                  gcode_file: str,
                                  operator_initials: Optional[str] = None,
                                  job_id: Optional[int] = None) -> dict:
        """Create and lock a new queue-job execution attempt."""
        queue_ids = self._normalize_queue_ids(queue_ids)
        if not queue_ids:
            raise ValueError("no items to print")

        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()

        try:
            items = self._get_queue_items_by_ids(conn, queue_ids)
            if len(items) != len(queue_ids):
                raise LookupError("one or more selected parts were not found")

            wo_ids = {item["wo_id"] for item in items}
            if len(wo_ids) != 1:
                raise ValueError(
                    "selected parts must belong to the same work order"
                )

            if any(item["status"] in ("printing", "assigned")
                   for item in items):
                raise RuntimeError("items already in progress")

            printable_items = [
                item for item in items
                if item["status"] in ("queued", "failed")
            ]
            if not printable_items:
                raise ValueError("no items to print")
            if len(printable_items) != len(items):
                raise ValueError(
                    "selected parts must be queued or failed before printing"
                )

            work_order_job_id = self._resolve_work_order_job_id(
                conn, items, requested_job_id=job_id
            )
            if work_order_job_id is None:
                if job_id is not None:
                    raise LookupError("job not found")
                raise ValueError(
                    "selected parts must belong to the same job before printing"
                )

            placeholders = ",".join("?" for _ in queue_ids)
            queue_job_id = self._create_queue_job_session(
                conn,
                items[0]["wo_id"],
                work_order_job_id,
                printer_id,
                printer_name,
                gcode_file,
                operator_initials,
                now,
            )

            cursor = conn.execute("""
                UPDATE queue_items
                SET status = 'printing',
                    job_id = ?,
                    queue_job_id = ?,
                    assigned_printer_id = ?,
                    assigned_printer_name = ?,
                    gcode_file = ?,
                    print_job_id = NULL,
                    assigned_at = ?,
                    started_at = ?,
                    completed_at = NULL
                WHERE queue_id IN ({}) AND status IN ('queued', 'failed')
            """.format(placeholders),
                                  [work_order_job_id, queue_job_id, printer_id,
                                   printer_name, gcode_file, now, now]
                                  + queue_ids)

            if cursor.rowcount != len(queue_ids):
                raise RuntimeError("items already in progress")

            conn.execute("""
                UPDATE jobs
                SET status = 'in_progress',
                    printer_id = ?,
                    printer_name = ?,
                    gcode_file = ?,
                    operator_initials = ?,
                    started_at = ?,
                    completed_at = NULL
                WHERE job_id = ?
            """, (printer_id, printer_name, gcode_file, operator_initials,
                  now, work_order_job_id))
            self._sync_job_status(conn, work_order_job_id)
            self._update_wo_status_from_items(conn, items[0]["wo_id"])
            conn.commit()
            return {
                "queue_job_id": queue_job_id,
                "job_id": work_order_job_id,
                "wo_id": items[0]["wo_id"],
                "queue_ids": queue_ids,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def assign_queue_items(self, queue_ids, printer_id: str,
                           printer_name: str,
                           gcode_file: str,
                           operator_initials: Optional[str] = None,
                           job_id: Optional[int] = None) -> Optional[int]:
        """Assign one or more queue items to the same print job."""
        try:
            result = self.start_queue_job_execution(
                queue_ids,
                printer_id,
                printer_name,
                gcode_file,
                operator_initials=operator_initials,
                job_id=job_id,
            )
            return result["queue_job_id"]
        except (LookupError, RuntimeError, ValueError):
            return None

    def complete_queue_item(self, queue_id: int,
                            print_job_id: Optional[int] = None) -> bool:
        """Mark a queue item as completed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        row = conn.execute("""
            SELECT wo_id, job_id
            FROM queue_items
            WHERE queue_id = ?
        """, (queue_id,)).fetchone()
        if not row:
            conn.close()
            return False

        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'completed',
                completed_at = ?,
                print_job_id = COALESCE(?, print_job_id)
            WHERE queue_id = ? AND status = 'printing'
        """, (now, print_job_id, queue_id))
        changed = cursor.rowcount > 0

        if changed:
            if row["job_id"]:
                conn.execute("""
                    UPDATE jobs
                    SET print_job_id = COALESCE(?, print_job_id)
                    WHERE job_id = ?
                """, (print_job_id, row["job_id"]))
                self._sync_job_status(conn, row["job_id"])
            self._update_wo_status_from_items(conn, row["wo_id"])
            conn.commit()
        else:
            conn.rollback()

        conn.close()
        return changed

    def fail_queue_item(self, queue_id: int) -> bool:
        """Mark a queue item as failed (can be re-queued)."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        row = conn.execute("""
            SELECT wo_id, job_id
            FROM queue_items
            WHERE queue_id = ?
        """, (queue_id,)).fetchone()
        if not row:
            conn.close()
            return False

        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'failed', completed_at = ?
            WHERE queue_id = ? AND status = 'printing'
        """, (now, queue_id))
        changed = cursor.rowcount > 0
        if changed:
            if row["job_id"]:
                self._sync_job_status(conn, row["job_id"])
            self._update_wo_status_from_items(conn, row["wo_id"])
            conn.commit()
        else:
            conn.rollback()
        conn.close()
        return changed

    def requeue_item(self, queue_id: int) -> bool:
        """Re-queue a failed item back to queued status."""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT wo_id, job_id
            FROM queue_items
            WHERE queue_id = ?
        """, (queue_id,)).fetchone()
        if not row:
            conn.close()
            return False
        cursor = conn.execute("""
            UPDATE queue_items
            SET status = 'queued',
                queue_job_id = NULL,
                assigned_printer_id = NULL,
                assigned_printer_name = NULL,
                gcode_file = NULL,
                print_job_id = NULL,
                assigned_at = NULL,
                started_at = NULL,
                completed_at = NULL
            WHERE queue_id = ? AND status = 'failed'
        """, (queue_id,))
        changed = cursor.rowcount > 0
        if changed:
            if row["job_id"]:
                self._sync_job_status(conn, row["job_id"])
            self._update_wo_status_from_items(conn, row["wo_id"])
            conn.commit()
        else:
            conn.rollback()
        conn.close()
        return changed

    def find_printing_queue_job_by_filename(self, printer_id: str,
                                            filename: str) -> Optional[dict]:
        """Fallback lookup for an active queue-job session by filename."""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT * FROM queue_jobs
            WHERE assigned_printer_id = ?
              AND gcode_file = ?
              AND status = 'printing'
            ORDER BY queue_job_id DESC LIMIT 1
        """, (printer_id, filename)).fetchone()

        if not row and filename:
            bare = filename.rsplit("/", 1)[-1] if "/" in filename else filename
            row = conn.execute("""
                SELECT * FROM queue_jobs
                WHERE assigned_printer_id = ?
                  AND (gcode_file = ? OR gcode_file LIKE ?)
                  AND status = 'printing'
                ORDER BY queue_job_id DESC LIMIT 1
            """, (printer_id, bare, "%" + bare)).fetchone()

        conn.close()
        return dict(row) if row else None

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

    def link_print_job_to_queue_job(self, queue_job_id: int,
                                    print_job_id: int) -> None:
        """Link a production log job id to all items in a grouped queue job."""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT job_id
            FROM queue_jobs
            WHERE queue_job_id = ?
        """, (queue_job_id,)).fetchone()
        conn.execute("""
            UPDATE queue_jobs
            SET print_job_id = ?
            WHERE queue_job_id = ?
        """, (print_job_id, queue_job_id))
        conn.execute("""
            UPDATE queue_items
            SET print_job_id = ?
            WHERE queue_job_id = ?
        """, (print_job_id, queue_job_id))
        if row and row["job_id"]:
            conn.execute("""
                UPDATE jobs
                SET print_job_id = ?
                WHERE job_id = ?
            """, (print_job_id, row["job_id"]))
        conn.commit()
        conn.close()

    def complete_queue_job(self, queue_job_id: int,
                           print_job_id: Optional[int] = None) -> bool:
        """Mark all queue items in a grouped job as completed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        job = conn.execute(
            "SELECT wo_id, job_id FROM queue_jobs WHERE queue_job_id = ?",
            (queue_job_id,)
        ).fetchone()
        if not job:
            conn.close()
            return False

        conn.execute("""
            UPDATE queue_items
            SET status = 'completed',
                completed_at = ?,
                print_job_id = COALESCE(?, print_job_id)
            WHERE queue_job_id = ? AND status = 'printing'
        """, (now, print_job_id, queue_job_id))

        conn.execute("""
            UPDATE queue_jobs
            SET status = 'completed',
                completed_at = ?,
                print_job_id = COALESCE(?, print_job_id)
            WHERE queue_job_id = ?
        """, (now, print_job_id, queue_job_id))

        if job["job_id"]:
            conn.execute("""
                UPDATE jobs
                SET print_job_id = COALESCE(?, print_job_id)
                WHERE job_id = ?
            """, (print_job_id, job["job_id"]))
            self._sync_job_status(conn, job["job_id"])
        self._update_wo_status_from_items(conn, job["wo_id"])
        conn.commit()

        conn.close()
        return True

    def fail_queue_job(self, queue_job_id: int,
                       requeue_items: bool = False) -> bool:
        """Mark all queue items in a grouped job as failed."""
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        job = conn.execute("""
            SELECT wo_id, job_id
            FROM queue_jobs
            WHERE queue_job_id = ?
        """, (queue_job_id,)).fetchone()
        if not job:
            conn.close()
            return False

        if requeue_items:
            conn.execute("""
                UPDATE queue_items
                SET status = 'queued',
                    queue_job_id = NULL,
                    assigned_printer_id = NULL,
                    assigned_printer_name = NULL,
                    gcode_file = NULL,
                    print_job_id = NULL,
                    assigned_at = NULL,
                    started_at = NULL,
                    completed_at = NULL
                WHERE queue_job_id = ? AND status IN ('printing', 'assigned')
            """, (queue_job_id,))
        else:
            conn.execute("""
                UPDATE queue_items
                SET status = 'failed',
                    completed_at = ?
                WHERE queue_job_id = ? AND status IN ('printing', 'assigned')
            """, (now, queue_job_id))

        conn.execute("""
            UPDATE queue_jobs
            SET status = 'failed',
                completed_at = ?
            WHERE queue_job_id = ?
        """, (now, queue_job_id))

        if job["job_id"]:
            self._sync_job_status(conn, job["job_id"])
        self._update_wo_status_from_items(conn, job["wo_id"])
        conn.commit()
        conn.close()
        return True

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
        new_status = self._derive_work_order_status(statuses)
        completed_at = now if new_status in ("completed", "cancelled") else None
        conn.execute("""
            UPDATE work_orders
            SET status = ?,
                completed_at = ?
            WHERE wo_id = ?
        """, (new_status, completed_at, wo_id))

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
