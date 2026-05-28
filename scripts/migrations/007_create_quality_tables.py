"""Create the quality.db tables (Phase E1).

Phase E adds Non-Conformance Reports (NCR) and Corrective Actions
(CA). They live in their own DB file, ``data/quality.db`` — a new
file this migration creates if absent. NCR↔job/WO links are logical
references resolved at the service layer; there are no cross-file SQL
joins, so this DB stands alone.

The migration is **create-only**:

- 2 new tables (``non_conformances``, ``corrective_actions``) plus
  their indexes.
- No data writes, no ALTER, no DROP.

The table DDL is imported verbatim from
``app/domains/quality/repository.py`` (QUALITY_SCHEMA_STATEMENTS) so a
migrated DB and a fresh ``_init_tables`` install converge byte-for-byte
— the strings cannot drift because there is only one copy.

Two layers of idempotence
-------------------------
1. ``MigrationRunner.is_applied('007_create_quality_tables')``
   short-circuits before any DDL (only consulted when the DB file
   already exists).
2. Each statement is ``CREATE ... IF NOT EXISTS`` and the apply path
   counts work via a table-existence check (sqlite_master), not a
   column check — this migration creates tables. If the registry row
   was lost but both tables already exist, the apply is a schema no-op
   and only re-records the registry row.

Usage
-----
    # Read-only preview (default if neither flag is passed):
    python scripts/migrations/007_create_quality_tables.py
    python scripts/migrations/007_create_quality_tables.py --dry-run

    # Apply. Backs up quality.db first ONLY if it already exists.
    sudo systemctl stop print-farm-monitor
    python scripts/migrations/007_create_quality_tables.py --apply
    sudo systemctl start print-farm-monitor

    # Custom DB path:
    python scripts/migrations/007_create_quality_tables.py \\
        --apply --db /path/to/quality.db

Exit codes
----------
    0  Success (or dry-run / already-applied).
    1  Bad arguments.
    2  print-farm-monitor service still running on port 5001.
    3  Backup creation failed (DB untouched).
    5  Any other SQLite error (transaction rolled back).
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.domains.quality.repository import (  # noqa: E402
    QUALITY_SCHEMA_STATEMENTS,
    QUALITY_TABLES,
)
from app.shared.migrations.runner import MigrationRunner  # noqa: E402


MIGRATION_ID = "007_create_quality_tables"
DESCRIPTION = (
    "Create quality.db tables: non_conformances + corrective_actions "
    "(Phase E1 NCR/CA)"
)

DEFAULT_DB_PATH = "data/quality.db"

EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_SERVICE_RUNNING = 2
EXIT_BACKUP_FAILED = 3
EXIT_SQLITE_ERROR = 5


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_BAD_ARGS,
                  "{}: error: {}\n".format(self.prog, message))


# ----------------------------------------------------------------------
# Service safety / backup
# ----------------------------------------------------------------------

def is_service_running(host: str = "127.0.0.1", port: int = 5001,
                       timeout_sec: float = 1.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def create_backup(db_path: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = "{}.bak-{}".format(db_path, stamp)
    if os.path.exists(backup_path):
        raise FileExistsError(
            "Backup path already exists: {}".format(backup_path)
        )
    shutil.copy2(db_path, backup_path)
    return backup_path


# ----------------------------------------------------------------------
# Classification (read-only)
# ----------------------------------------------------------------------

def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def classify_tables(conn: sqlite3.Connection) -> Tuple[List[str], List[str]]:
    """Split QUALITY_TABLES into (missing, present) by current schema."""
    missing: List[str] = []
    present: List[str] = []
    for table in QUALITY_TABLES:
        if table_exists(conn, table):
            present.append(table)
        else:
            missing.append(table)
    return missing, present


# ----------------------------------------------------------------------
# Triage output
# ----------------------------------------------------------------------

def print_triage(db_path: str, file_existed: bool,
                 missing: List[str], present: List[str]) -> None:
    print("=" * 70)
    print("Migration {}: {}".format(MIGRATION_ID, DESCRIPTION))
    print("=" * 70)
    print("DB: {}".format(db_path))
    if not file_existed:
        print("(DB file does not exist yet — it would be created.)")
    print()
    print("Planned table additions ({} missing):".format(len(missing)))
    if not missing:
        print("  (none — both tables already present)")
    else:
        for table in missing:
            print("  + {}".format(table))
    if present:
        print()
        print("Already present (would be skipped) — {}:".format(len(present)))
        for table in present:
            print("  - {}".format(table))
    print()
    print("No data is written. Tables are created empty.")
    print()
    print("Dry run complete. No writes performed.")


# ----------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------

def _apply_writes(conn: sqlite3.Connection,
                  runner: MigrationRunner) -> Tuple[int, int]:
    """Replay the schema statements and record the migration.

    Caller has issued BEGIN; this function does NOT commit. Returns
    (created, skipped) counted by table-existence before the writes.
    """
    before_missing = [t for t in QUALITY_TABLES if not table_exists(conn, t)]
    for stmt in QUALITY_SCHEMA_STATEMENTS:
        conn.execute(stmt)
    runner.record(MIGRATION_ID, DESCRIPTION, conn)
    created = len(before_missing)
    skipped = len(QUALITY_TABLES) - created
    return created, skipped


def apply_migration(db_path: str, runner: MigrationRunner,
                    backup_path: Optional[str]) -> int:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN")
        try:
            created, skipped = _apply_writes(conn, runner)
            conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            print("Migration {} already recorded — nothing to do "
                  "({}).".format(MIGRATION_ID, exc), file=sys.stderr)
            return EXIT_OK
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            print("ERROR: SQLite error: {}".format(exc), file=sys.stderr)
            if backup_path:
                print("Transaction rolled back. Backup intact: {}".format(
                    backup_path), file=sys.stderr)
            return EXIT_SQLITE_ERROR
    finally:
        conn.close()

    print_apply_summary(db_path, backup_path, created, skipped)
    return EXIT_OK


def print_apply_summary(db_path: str, backup_path: Optional[str],
                        created: int, skipped: int) -> None:
    print()
    print("Migration 007 — Quality tables")
    print("  DB: {}".format(db_path))
    if backup_path:
        print("  Backup: {}".format(backup_path))
    else:
        print("  Backup: (none — DB file was newly created)")
    print("  Tables created: {} (skipped {} already present)".format(
        created, skipped
    ))


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def _build_parser() -> _Parser:
    parser = _Parser(description=DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview the table additions without touching "
                           "the DB (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Apply the migration. Backs up quality.db first "
                           "only if it already exists. Refuses to run if "
                           "the print-farm service is up.")
    parser.add_argument("--db", "--db-path", dest="db_path",
                        default=DEFAULT_DB_PATH,
                        help="Path to quality.db (default: {}).".format(
                            DEFAULT_DB_PATH))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    db_path = args.db_path
    # Capture existence BEFORE anything could create the file — a dry run
    # against a brand-new path must not write quality.db to disk, and the
    # backup step only applies to a pre-existing file.
    file_existed = os.path.exists(db_path)

    # Layer-1 registry short-circuit is only meaningful when the DB
    # already exists (a brand-new file can't have a recorded migration).
    if file_existed:
        runner = MigrationRunner(db_path)
        runner.ensure_schema_version_table()
        if runner.is_applied(MIGRATION_ID):
            print("Migration {} already applied. Nothing to do.".format(
                MIGRATION_ID))
            return EXIT_OK

    # Read-only classification. Missing file → both tables missing,
    # without creating anything.
    if file_existed:
        ro_conn = sqlite3.connect("file:{}?mode=ro".format(db_path),
                                  uri=True)
        ro_conn.row_factory = sqlite3.Row
        try:
            missing, present = classify_tables(ro_conn)
        finally:
            ro_conn.close()
    else:
        missing, present = list(QUALITY_TABLES), []

    if not args.apply:
        print_triage(db_path, file_existed, missing, present)
        return EXIT_OK

    if is_service_running():
        print("ERROR: print-farm-monitor appears to be running "
              "(port 5001 is bound).", file=sys.stderr)
        print("Stop the service first:", file=sys.stderr)
        print("  sudo systemctl stop print-farm-monitor", file=sys.stderr)
        return EXIT_SERVICE_RUNNING

    backup_path: Optional[str] = None
    if file_existed:
        try:
            backup_path = create_backup(db_path)
        except (OSError, FileExistsError) as exc:
            print("ERROR: backup failed: {}".format(exc), file=sys.stderr)
            print("DB has not been modified.", file=sys.stderr)
            return EXIT_BACKUP_FAILED
        print("Backup created: {}".format(backup_path))
    else:
        print("New DB file — no backup needed.")

    # Now seed the registry (creates the file if new) and apply.
    runner = MigrationRunner(db_path)
    runner.ensure_schema_version_table()
    return apply_migration(db_path, runner, backup_path)


if __name__ == "__main__":
    sys.exit(main())
