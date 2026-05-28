"""Quality persistence — NCRs and Corrective Actions in quality.db.

The canonical table DDL lives here as module constants so the
Migration 007 script and the fresh-install ``_init_tables`` mirror
share one source of truth (the migration imports these — no
byte-drift possible). The repository takes whatever it is handed;
validation lives in :class:`QualityService`.
"""

import sqlite3
from datetime import datetime, timezone
from typing import List, Optional


# Canonical schema. Imported verbatim by
# scripts/migrations/007_create_quality_tables.py so a migrated DB and
# a fresh install converge byte-for-byte. ``IF NOT EXISTS`` keeps every
# statement idempotent, so the migration can run them under its own
# table-existence guard without special-casing.
NON_CONFORMANCES_DDL = (
    "CREATE TABLE IF NOT EXISTS non_conformances (\n"
    "    ncr_id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    job_id INTEGER NOT NULL,\n"
    "    wo_id TEXT NOT NULL,\n"
    "    description TEXT NOT NULL,\n"
    "    remedial_action TEXT,\n"
    "    reported_by TEXT NOT NULL,\n"
    "    affected_parts TEXT,\n"
    "    corrective_action_needed TEXT NOT NULL DEFAULT 'N',\n"
    "    status TEXT NOT NULL DEFAULT 'open',\n"
    "    created_at TEXT NOT NULL,\n"
    "    closed_at TEXT\n"
    ")"
)

CORRECTIVE_ACTIONS_DDL = (
    "CREATE TABLE IF NOT EXISTS corrective_actions (\n"
    "    ca_id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "    ncr_id INTEGER NOT NULL REFERENCES non_conformances(ncr_id),\n"
    "    root_cause_actions TEXT NOT NULL,\n"
    "    responsible_persons TEXT,\n"
    "    resources_needed TEXT,\n"
    "    effectiveness_verification TEXT,\n"
    "    verifying_person TEXT,\n"
    "    status TEXT NOT NULL DEFAULT 'open',\n"
    "    created_at TEXT NOT NULL,\n"
    "    closed_at TEXT\n"
    ")"
)

# Ordered list of schema statements (tables + indexes). The migration
# replays this list under a transaction; _init_tables replays it on
# fresh installs. Every statement is IF NOT EXISTS, so order only needs
# to satisfy the FK reference (non_conformances before its index/CA).
QUALITY_SCHEMA_STATEMENTS = [
    NON_CONFORMANCES_DDL,
    "CREATE INDEX IF NOT EXISTS idx_ncr_wo ON non_conformances(wo_id)",
    "CREATE INDEX IF NOT EXISTS idx_ncr_job ON non_conformances(job_id)",
    CORRECTIVE_ACTIONS_DDL,
    "CREATE INDEX IF NOT EXISTS idx_ca_ncr ON corrective_actions(ncr_id)",
]

# The two tables this domain owns — the migration's layer-2 idempotency
# predicate checks for both before deciding it has work to do.
QUALITY_TABLES = ["non_conformances", "corrective_actions"]


class QualityRepository:
    """Read/write access to non_conformances + corrective_actions."""

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
        try:
            for stmt in QUALITY_SCHEMA_STATEMENTS:
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Non-Conformances
    # ------------------------------------------------------------------

    def create_ncr(self, job_id: int, wo_id: str, description: str,
                   reported_by: str, affected_parts: Optional[str] = None,
                   remedial_action: Optional[str] = None,
                   corrective_action_needed: str = "N") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO non_conformances "
                "(job_id, wo_id, description, remedial_action, reported_by, "
                " affected_parts, corrective_action_needed, status, "
                " created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
                (job_id, wo_id, description, remedial_action, reported_by,
                 affected_parts, corrective_action_needed, now),
            )
            ncr_id = cursor.lastrowid
            conn.commit()
            row = conn.execute(
                "SELECT * FROM non_conformances WHERE ncr_id = ?", (ncr_id,)
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def get_ncr(self, ncr_id: int) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM non_conformances WHERE ncr_id = ?", (ncr_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_ncrs_for_wo(self, wo_id: str) -> List[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM non_conformances WHERE wo_id = ? "
                "ORDER BY ncr_id ASC",
                (wo_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_ncrs_for_job(self, job_id: int) -> List[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM non_conformances WHERE job_id = ? "
                "ORDER BY ncr_id ASC",
                (job_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_open_ncrs(self) -> List[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM non_conformances WHERE status = 'open' "
                "ORDER BY ncr_id ASC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def close_ncr(self, ncr_id: int) -> Optional[dict]:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE non_conformances SET status = 'closed', "
                "closed_at = ? WHERE ncr_id = ?",
                (now, ncr_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM non_conformances WHERE ncr_id = ?", (ncr_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def count_open_ncrs_for_wo(self, wo_id: str) -> int:
        """The WO-rollup gate query — open NCR count for a work order."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM non_conformances "
                "WHERE wo_id = ? AND status = 'open'",
                (wo_id,),
            ).fetchone()
            return int(row["n"] or 0)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Corrective Actions
    # ------------------------------------------------------------------

    _CA_UPDATE_COLUMNS = (
        "root_cause_actions", "responsible_persons", "resources_needed",
        "effectiveness_verification", "verifying_person",
    )

    def create_ca(self, ncr_id: int, root_cause_actions: str,
                  responsible_persons: Optional[str] = None,
                  resources_needed: Optional[str] = None,
                  effectiveness_verification: Optional[str] = None,
                  verifying_person: Optional[str] = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._get_conn()
        try:
            cursor = conn.execute(
                "INSERT INTO corrective_actions "
                "(ncr_id, root_cause_actions, responsible_persons, "
                " resources_needed, effectiveness_verification, "
                " verifying_person, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
                (ncr_id, root_cause_actions, responsible_persons,
                 resources_needed, effectiveness_verification,
                 verifying_person, now),
            )
            ca_id = cursor.lastrowid
            conn.commit()
            row = conn.execute(
                "SELECT * FROM corrective_actions WHERE ca_id = ?", (ca_id,)
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    def get_ca(self, ca_id: int) -> Optional[dict]:
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM corrective_actions WHERE ca_id = ?", (ca_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_cas_for_ncr(self, ncr_id: int) -> List[dict]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM corrective_actions WHERE ncr_id = ? "
                "ORDER BY ca_id ASC",
                (ncr_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_ca(self, ca_id: int, **fields) -> Optional[dict]:
        updates = {k: v for k, v in fields.items()
                   if k in self._CA_UPDATE_COLUMNS and v is not None}
        if updates:
            assignments = ", ".join("{} = ?".format(c) for c in updates)
            params = list(updates.values()) + [ca_id]
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE corrective_actions SET {} WHERE ca_id = ?".format(
                        assignments),
                    params,
                )
                conn.commit()
            finally:
                conn.close()
        return self.get_ca(ca_id)

    def set_ca_status(self, ca_id: int, status: str,
                      verifying_person: Optional[str] = None) -> Optional[dict]:
        now = datetime.now(timezone.utc).isoformat()
        closed_at = now if status == "closed" else None
        conn = self._get_conn()
        try:
            if verifying_person is not None:
                conn.execute(
                    "UPDATE corrective_actions SET status = ?, "
                    "verifying_person = ?, closed_at = ? WHERE ca_id = ?",
                    (status, verifying_person, closed_at, ca_id),
                )
            else:
                conn.execute(
                    "UPDATE corrective_actions SET status = ?, closed_at = ? "
                    "WHERE ca_id = ?",
                    (status, closed_at, ca_id),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM corrective_actions WHERE ca_id = ?", (ca_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
