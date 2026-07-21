"""Persistence for custom-engraving requests (Phase E-2).

The ``engraving_requests`` table lives in ``work_orders.db`` — the same
file as the ``work_orders`` row it references, so the ``wo_id`` foreign
key is genuinely enforced (SQLite FKs cannot cross files). This mirrors
how the queue domain owns ``queue_items`` inside ``work_orders.db``.

The DDL is a single source of truth: the constants below are imported
verbatim by ``scripts/migrations/009_create_engraving_requests_table.py``
so a migrated DB and a fresh ``_init_tables`` install converge
byte-for-byte. ``IF NOT EXISTS`` keeps every statement idempotent.
"""

import sqlite3
from datetime import datetime, timezone

# Status values (free-text column, house style — no CHECK constraint).
STATUS_GENERATING = "generating"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


ENGRAVING_REQUESTS_DDL = (
    "CREATE TABLE IF NOT EXISTS engraving_requests (\n"
    "    engraving_id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    wo_id TEXT NOT NULL REFERENCES work_orders(wo_id),\n"
    "    product_key TEXT NOT NULL,\n"
    "    customer_name TEXT,\n"
    "    quantity INTEGER NOT NULL,\n"
    "    original_filename TEXT NOT NULL,\n"
    "    upload_path TEXT,\n"
    "    status TEXT NOT NULL DEFAULT 'generating',\n"
    "    error_message TEXT,\n"
    "    mold_stl_path TEXT,\n"
    "    prod_stl_path TEXT,\n"
    "    mold_preview_path TEXT,\n"
    "    prod_preview_path TEXT,\n"
    "    mold_triangles INTEGER,\n"
    "    prod_triangles INTEGER,\n"
    "    duration_seconds REAL,\n"
    "    created_at TEXT NOT NULL,\n"
    "    completed_at TEXT\n"
    ")"
)

ENGRAVING_REQUESTS_SCHEMA_STATEMENTS = [
    ENGRAVING_REQUESTS_DDL,
    "CREATE INDEX IF NOT EXISTS idx_engraving_requests_wo "
    "ON engraving_requests(wo_id)",
]

# The table this migration owns — layer-2 idempotency checks for it.
ENGRAVING_REQUESTS_TABLES = ["engraving_requests"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EngravingRepository:
    """CRUD for engraving_requests in the work_orders.db file."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_tables()

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_tables(self):
        conn = self._get_conn()
        for stmt in ENGRAVING_REQUESTS_SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, engraving_id: int) -> dict:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM engraving_requests WHERE engraving_id = ?",
                (engraving_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_by_wo(self, wo_id: str) -> dict:
        """Return the most recent engraving request for a work order."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM engraving_requests WHERE wo_id = ? "
                "ORDER BY engraving_id DESC LIMIT 1",
                (wo_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(self, wo_id: str, product_key: str, customer_name: str,
               quantity: int, original_filename: str) -> int:
        """Insert a new request in the ``generating`` state; return its id."""
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "INSERT INTO engraving_requests "
                "(wo_id, product_key, customer_name, quantity, "
                " original_filename, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (wo_id, product_key, customer_name, quantity,
                 original_filename, STATUS_GENERATING, _now()),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def set_upload_path(self, engraving_id: int, upload_path: str) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE engraving_requests SET upload_path = ? "
                "WHERE engraving_id = ?",
                (upload_path, engraving_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_ready(self, engraving_id: int, *, mold_stl_path: str,
                   prod_stl_path: str, mold_preview_path: str,
                   prod_preview_path: str, mold_triangles: int,
                   prod_triangles: int, duration_seconds: float) -> bool:
        """Mark a request ready — only if still ``generating``.

        The ``WHERE status = 'generating'`` guard is a compare-and-set: a
        worker that finishes after the request was already timed-out (and
        marked failed) cannot clobber the terminal state. Returns whether
        the update took effect.
        """
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "UPDATE engraving_requests SET "
                "  status = ?, mold_stl_path = ?, prod_stl_path = ?, "
                "  mold_preview_path = ?, prod_preview_path = ?, "
                "  mold_triangles = ?, prod_triangles = ?, "
                "  duration_seconds = ?, error_message = NULL, "
                "  completed_at = ? "
                "WHERE engraving_id = ? AND status = ?",
                (STATUS_READY, mold_stl_path, prod_stl_path,
                 mold_preview_path, prod_preview_path, mold_triangles,
                 prod_triangles, duration_seconds, _now(),
                 engraving_id, STATUS_GENERATING),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def mark_failed(self, engraving_id: int, error_message: str,
                    *, only_if_generating: bool = True) -> bool:
        """Mark a request failed with a message. Returns whether it applied.

        With ``only_if_generating`` (the default) this is a compare-and-set
        so a late-finishing worker cannot overwrite an already-terminal
        record, and vice versa.
        """
        conn = self._get_conn()
        try:
            if only_if_generating:
                cur = conn.execute(
                    "UPDATE engraving_requests SET "
                    "  status = ?, error_message = ?, completed_at = ? "
                    "WHERE engraving_id = ? AND status = ?",
                    (STATUS_FAILED, error_message, _now(),
                     engraving_id, STATUS_GENERATING),
                )
            else:
                cur = conn.execute(
                    "UPDATE engraving_requests SET "
                    "  status = ?, error_message = ?, completed_at = ? "
                    "WHERE engraving_id = ?",
                    (STATUS_FAILED, error_message, _now(), engraving_id),
                )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def sweep_stale_generating(self, message: str) -> int:
        """Fail every request stuck in ``generating`` (called at boot).

        Generation state is purely in-memory (a daemon thread), so any row
        still ``generating`` at process start is definitively stranded by a
        restart. Returns how many were swept.
        """
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "UPDATE engraving_requests SET "
                "  status = ?, error_message = ?, completed_at = ? "
                "WHERE status = ?",
                (STATUS_FAILED, message, _now(), STATUS_GENERATING),
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
