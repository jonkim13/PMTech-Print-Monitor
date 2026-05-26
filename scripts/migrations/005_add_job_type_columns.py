"""Add job_type discriminator + External/Design/inspection columns to jobs.

Phase C introduces three Job variants — Internal (existing), External
(vendor-driven), and Design (designer-driven) — implemented as a
discriminator column plus nullable type-specific columns on the
existing ``jobs`` table. This migration is **additive only**:

- 11 new columns on ``jobs``.
- No UPDATE statements anywhere.
- No DROP, rename, or type-change statements.
- No data transformation logic.

Existing rows pick up ``job_type='Internal'`` via SQLite's column
DEFAULT applied at ALTER ADD time — no per-row UPDATE is needed.

Columns added
-------------
    job_type             TEXT DEFAULT 'Internal' NOT NULL
    vendor               TEXT                            -- External
    external_process     TEXT                            -- External
    date_delivered       TEXT                            -- External
    requirements         TEXT                            -- Design
    designer             TEXT                            -- Design
    design_completed_at  TEXT                            -- Design
    approved_by          TEXT                            -- Design
    inspection_report    TEXT   -- Internal + External shared
    inspector            TEXT   -- Internal + External shared
    inspection_date      TEXT   -- Internal + External shared

Two layers of idempotence
-------------------------
1. ``MigrationRunner.is_applied('005_add_job_type_columns')`` short-
   circuits the whole script before any DDL.
2. Each ALTER is guarded by ``add_column_if_missing`` (PRAGMA
   table_info check). If the registry row was lost but the columns
   already exist, the apply path is a no-op for the schema and only
   records the registry row.

Usage
-----
    # Read-only preview (default if neither flag is passed):
    python scripts/migrations/005_add_job_type_columns.py
    python scripts/migrations/005_add_job_type_columns.py --dry-run

    # Apply. Creates a backup of work_orders.db first.
    sudo systemctl stop print-farm-monitor
    python scripts/migrations/005_add_job_type_columns.py --apply
    sudo systemctl start print-farm-monitor

    # Custom DB path:
    python scripts/migrations/005_add_job_type_columns.py \\
        --apply --db /path/to/work_orders.db

Recovery
--------
``--apply`` writes a timestamped backup before any DDL:

    data/work_orders.db.bak-YYYYMMDD-HHMMSS

To restore:

    sudo systemctl stop print-farm-monitor
    cp data/work_orders.db.bak-YYYYMMDD-HHMMSS data/work_orders.db
    sudo systemctl start print-farm-monitor

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

from app.shared.migrations.runner import MigrationRunner  # noqa: E402
from app.shared.sqlite_migrations import has_column  # noqa: E402


MIGRATION_ID = "005_add_job_type_columns"
DESCRIPTION = (
    "Add job_type discriminator + External/Design/inspection columns to jobs"
)

DEFAULT_DB_PATH = "data/work_orders.db"

EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_SERVICE_RUNNING = 2
EXIT_BACKUP_FAILED = 3
EXIT_SQLITE_ERROR = 5

# Ordered: discriminator first, then External-only, Design-only, and the
# shared inspection columns. Order only affects PRAGMA output; SQLite
# applies each ALTER independently.
NEW_COLUMNS: List[Tuple[str, str]] = [
    ("job_type",             "TEXT DEFAULT 'Internal' NOT NULL"),
    ("vendor",               "TEXT"),
    ("external_process",     "TEXT"),
    ("date_delivered",       "TEXT"),
    ("requirements",         "TEXT"),
    ("designer",             "TEXT"),
    ("design_completed_at",  "TEXT"),
    ("approved_by",          "TEXT"),
    ("inspection_report",    "TEXT"),
    ("inspector",            "TEXT"),
    ("inspection_date",      "TEXT"),
]


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

def classify_columns(conn: sqlite3.Connection) -> Tuple[List[str], List[str]]:
    """Split NEW_COLUMNS into (missing, present) by current jobs schema."""
    missing: List[str] = []
    present: List[str] = []
    for col_name, _ in NEW_COLUMNS:
        if has_column(conn, "jobs", col_name):
            present.append(col_name)
        else:
            missing.append(col_name)
    return missing, present


def count_jobs(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
    return int(row[0] or 0)


def column_def_for(col_name: str) -> str:
    return dict(NEW_COLUMNS)[col_name]


# ----------------------------------------------------------------------
# Triage output
# ----------------------------------------------------------------------

def print_triage(db_path: str, missing: List[str], present: List[str],
                 job_count: int) -> None:
    print("=" * 70)
    print("Migration {}: {}".format(MIGRATION_ID, DESCRIPTION))
    print("=" * 70)
    print("DB: {}".format(db_path))
    print()
    print("Existing jobs row count: {}".format(job_count))
    print()
    print("Planned column additions ({} missing):".format(len(missing)))
    if not missing:
        print("  (none — all {} columns already present)".format(
            len(NEW_COLUMNS)
        ))
    else:
        for col in missing:
            print("  + jobs.{:<20} {}".format(col, column_def_for(col)))
    if present:
        print()
        print("Already present (would be skipped) — {}:".format(len(present)))
        for col in present:
            print("  - jobs.{}".format(col))
    print()
    print("Existing jobs would receive job_type='Internal' via "
          "SQLite DEFAULT.")
    print("No data UPDATEs would be issued.")
    print()
    print("Dry run complete. No writes performed.")


# ----------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------

def _apply_writes(conn: sqlite3.Connection,
                  runner: MigrationRunner) -> Tuple[int, int, int]:
    """Issue ALTER ADD COLUMN for each missing column and record.

    Caller has already issued BEGIN; this function does NOT commit.
    Returns (added, skipped, job_count).
    """
    added = 0
    skipped = 0
    for col_name, col_def in NEW_COLUMNS:
        if has_column(conn, "jobs", col_name):
            skipped += 1
            continue
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN {} {}".format(col_name, col_def)
        )
        added += 1

    job_count = count_jobs(conn)
    runner.record(MIGRATION_ID, DESCRIPTION, conn)
    return added, skipped, job_count


def apply_migration(db_path: str, runner: MigrationRunner,
                    backup_path: Optional[str]) -> int:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        try:
            added, skipped, job_count = _apply_writes(conn, runner)
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
            print("ERROR: SQLite error: {}".format(exc),
                  file=sys.stderr)
            if backup_path:
                print("Transaction rolled back. Backup intact: {}".format(
                    backup_path), file=sys.stderr)
            return EXIT_SQLITE_ERROR
    finally:
        conn.close()

    print_apply_summary(db_path, backup_path, added, skipped, job_count)
    return EXIT_OK


def print_apply_summary(db_path: str, backup_path: Optional[str],
                        added: int, skipped: int, job_count: int) -> None:
    print()
    print("Migration 005 — Job type discriminator + type-specific columns")
    print("  DB: {}".format(db_path))
    if backup_path:
        print("  Backup: {}".format(backup_path))
    print("  Columns added to jobs: {} (skipped {} already present)".format(
        added, skipped
    ))
    print("  Existing jobs receive job_type='Internal' via SQLite DEFAULT")
    print("  Total existing job rows: {} (unchanged, no data UPDATE)".format(
        job_count
    ))


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def _build_parser() -> _Parser:
    parser = _Parser(description=DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview the schema additions without "
                           "touching the DB (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Apply the migration after creating a backup. "
                           "Refuses to run if the print-farm service is up.")
    parser.add_argument("--db", "--db-path", dest="db_path",
                        default=DEFAULT_DB_PATH,
                        help="Path to work_orders.db (default: {}).".format(
                            DEFAULT_DB_PATH))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    db_path = args.db_path
    if not os.path.exists(db_path):
        print("ERROR: DB not found at {}".format(db_path),
              file=sys.stderr)
        return EXIT_BAD_ARGS

    runner = MigrationRunner(db_path)
    runner.ensure_schema_version_table()

    if runner.is_applied(MIGRATION_ID):
        print("Migration {} already applied. Nothing to do.".format(
            MIGRATION_ID))
        return EXIT_OK

    # Read-only classification first.
    ro_conn = sqlite3.connect("file:{}?mode=ro".format(db_path),
                              uri=True)
    ro_conn.row_factory = sqlite3.Row
    try:
        missing, present = classify_columns(ro_conn)
        job_count = count_jobs(ro_conn)
    finally:
        ro_conn.close()

    if not args.apply:
        print_triage(db_path, missing, present, job_count)
        return EXIT_OK

    if is_service_running():
        print("ERROR: print-farm-monitor appears to be running "
              "(port 5001 is bound).", file=sys.stderr)
        print("Stop the service first:", file=sys.stderr)
        print("  sudo systemctl stop print-farm-monitor",
              file=sys.stderr)
        return EXIT_SERVICE_RUNNING

    backup_path: Optional[str] = None
    try:
        backup_path = create_backup(db_path)
    except (OSError, FileExistsError) as exc:
        print("ERROR: backup failed: {}".format(exc),
              file=sys.stderr)
        print("DB has not been modified.", file=sys.stderr)
        return EXIT_BACKUP_FAILED
    print("Backup created: {}".format(backup_path))

    return apply_migration(db_path, runner, backup_path)


if __name__ == "__main__":
    sys.exit(main())
