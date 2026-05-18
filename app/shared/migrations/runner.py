"""Schema-version registry for one-shot data migrations.

The runner owns a single ``schema_version`` table that records which
migrations have been applied. Migration scripts call into it to check
whether they need to run, and to record their completion as part of
the same transaction as their actual writes — so the registry entry
commits with the migration, or rolls back with it.

The runner is *not* an orchestrator. It does not discover migration
scripts, does not run them in sequence, and does not own the migration
lifecycle. Manual application of each script remains the operator's
job. The runner is a passive registry that migration scripts and the
app factory consult.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import List


SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    migration_id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
""".strip()


class MigrationRunner:
    """Registry of applied schema migrations."""

    def __init__(self, db_path: str):
        """
        Args:
            db_path: SQLite file where the ``schema_version`` table
                lives. The app factory passes
                ``settings.work_order_db_path``; migration scripts pass
                whatever DB they're operating on (typically the same).
        """
        self.db_path = db_path

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema_version_table(self) -> None:
        """Create the ``schema_version`` table if missing.

        Idempotent. Safe to call on every process start.
        """
        conn = self._open()
        try:
            conn.execute(SCHEMA_VERSION_DDL)
            conn.commit()
        finally:
            conn.close()

    def is_applied(self, migration_id: str) -> bool:
        """Return True if the migration has been recorded as applied."""
        conn = self._open()
        try:
            row = conn.execute(
                "SELECT 1 FROM schema_version WHERE migration_id = ?",
                (migration_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def record(self, migration_id: str, description: str,
               conn: sqlite3.Connection) -> None:
        """Insert a registry row using the caller's connection.

        The caller is responsible for the surrounding transaction —
        ``record`` does not commit. This is the whole point: the
        registry write lives in the same transaction as the migration's
        actual changes, so both succeed or both roll back.

        Raises:
            sqlite3.IntegrityError: ``migration_id`` is already
                recorded. Treat as "do not re-apply."
        """
        applied_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO schema_version "
            "(migration_id, description, applied_at) "
            "VALUES (?, ?, ?)",
            (migration_id, description, applied_at),
        )

    def list_applied(self) -> List[dict]:
        """All applied migrations ordered by ``applied_at`` ASC.

        Returns:
            list of ``{migration_id, description, applied_at}`` dicts.
            Empty list if the table exists but is empty.
        """
        conn = self._open()
        try:
            rows = conn.execute(
                "SELECT migration_id, description, applied_at "
                "FROM schema_version ORDER BY applied_at ASC"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
