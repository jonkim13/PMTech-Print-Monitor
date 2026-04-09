"""
Filament Assignment Repository
================================
SQLite-backed persistence for printer-tool to spool assignments.
Extracted from database.py — behavior preserved exactly.
"""
import sqlite3
from datetime import datetime, timezone
from typing import Optional


class FilamentAssignmentDB:
    """Tracks which filament spool is loaded on which printer tool.

    Each printer can have multiple tools (nozzles). The XL has up to 5
    (tool_index 0-4), while the Core One has 1 (tool_index 0).
    """

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
        # Check if old schema (printer_id as sole PRIMARY KEY) exists
        # and migrate to new composite key schema
        cursor = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='filament_assignments'"
        )
        row = cursor.fetchone()
        if row and "tool_index" not in row["sql"]:
            # Migrate: add tool_index column to existing data
            conn.executescript("""
                ALTER TABLE filament_assignments
                    RENAME TO filament_assignments_old;
                CREATE TABLE filament_assignments (
                    printer_id TEXT NOT NULL,
                    tool_index INTEGER NOT NULL DEFAULT 0,
                    spool_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY (printer_id, tool_index)
                );
                INSERT INTO filament_assignments
                    (printer_id, tool_index, spool_id, assigned_at)
                SELECT printer_id, 0, spool_id, assigned_at
                FROM filament_assignments_old;
                DROP TABLE filament_assignments_old;
            """)
            conn.commit()
        elif not row:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS filament_assignments (
                    printer_id TEXT NOT NULL,
                    tool_index INTEGER NOT NULL DEFAULT 0,
                    spool_id TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY (printer_id, tool_index)
                )
            """)
            conn.commit()
        conn.close()

    def assign(self, printer_id: str, spool_id: str,
               tool_index: int = 0) -> None:
        """Assign a spool to a specific tool on a printer."""
        conn = self._get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO filament_assignments
                (printer_id, tool_index, spool_id, assigned_at)
            VALUES (?, ?, ?, ?)
        """, (printer_id, tool_index, spool_id,
              datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

    def unassign(self, printer_id: str, tool_index: int = 0) -> bool:
        """Remove the spool assignment for a specific tool."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM filament_assignments "
            "WHERE printer_id = ? AND tool_index = ?",
            (printer_id, tool_index)
        )
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def unassign_all(self, printer_id: str) -> bool:
        """Remove all spool assignments for a printer."""
        conn = self._get_conn()
        cursor = conn.execute(
            "DELETE FROM filament_assignments WHERE printer_id = ?",
            (printer_id,)
        )
        conn.commit()
        changed = cursor.rowcount > 0
        conn.close()
        return changed

    def get_assignment(self, printer_id: str,
                       tool_index: int = 0) -> Optional[dict]:
        """Get the assignment for a specific tool on a printer."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM filament_assignments "
            "WHERE printer_id = ? AND tool_index = ?",
            (printer_id, tool_index)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_printer_assignments(self, printer_id: str) -> list:
        """Get all tool assignments for a printer, ordered by tool_index."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM filament_assignments "
            "WHERE printer_id = ? ORDER BY tool_index",
            (printer_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_spool_assignments(self, spool_id: str) -> list:
        """Get all active assignments for a spool.

        Legacy data may contain duplicate active rows for the same spool_id,
        so callers should tolerate multiple results.
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM filament_assignments "
            "WHERE spool_id = ? ORDER BY printer_id, tool_index",
            (spool_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_all_assignments(self) -> dict:
        """Return {printer_id: spool_id} for tool 0 (backward compat).

        Also includes a '_multi' key with full per-tool data:
        {printer_id: [{tool_index, spool_id}, ...]}
        """
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT printer_id, tool_index, spool_id "
            "FROM filament_assignments ORDER BY printer_id, tool_index"
        ).fetchall()
        conn.close()
        # Backward-compatible flat dict (tool 0 only)
        flat = {}
        # Full multi-tool dict
        multi = {}  # type: dict
        for r in rows:
            pid = r["printer_id"]
            if r["tool_index"] == 0:
                flat[pid] = r["spool_id"]
            if pid not in multi:
                multi[pid] = []
            multi[pid].append({
                "tool_index": r["tool_index"],
                "spool_id": r["spool_id"],
            })
        flat["_multi"] = multi
        return flat
