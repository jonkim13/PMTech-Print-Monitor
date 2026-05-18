"""One-shot retro-record for migration 001.

Migration 001 was applied on the Pi on 2026-05-15, before the
``schema_version`` registry existed. This script inserts the registry
row that 001 would have written if the runner had been in place at
the time. It performs no schema or data writes beyond that single row.

Run this once on the Pi after deploying the registry infrastructure.
After it succeeds, the registry reflects the true state of the DB and
no further retro-records are needed. The script is idempotent: a
second run detects the existing row and exits cleanly.

Usage
-----
    # Read-only preview (default if neither flag is passed):
    python scripts/migrations/000_record_migration_001.py
    python scripts/migrations/000_record_migration_001.py --dry-run

    # Record the migration. Creates a backup first.
    sudo systemctl stop print-farm-monitor
    python scripts/migrations/000_record_migration_001.py --apply
    sudo systemctl start print-farm-monitor

Exit codes
----------
    0  Success (or dry-run / already-recorded).
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
from typing import Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.shared.migrations.runner import MigrationRunner  # noqa: E402


MIGRATION_ID = "001"
DESCRIPTION = (
    "Remove WO-001/002/003 dummies and renumber survivors"
)
DEFAULT_DB_PATH = "data/work_orders.db"

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


def _record(db_path: str, runner: MigrationRunner) -> int:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        try:
            runner.record(MIGRATION_ID, DESCRIPTION, conn)
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            print("Migration {} already recorded. Nothing to do.".format(
                MIGRATION_ID))
            return EXIT_OK
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            print("ERROR: SQLite error: {}".format(exc), file=sys.stderr)
            print("Transaction rolled back. DB is unchanged from before "
                  "this run.", file=sys.stderr)
            return EXIT_SQLITE_ERROR
    finally:
        conn.close()

    return EXIT_OK


def _print_plan(runner: MigrationRunner) -> None:
    print("=" * 70)
    print("Retro-record migration {} into schema_version".format(MIGRATION_ID))
    print("=" * 70)
    print("Migration ID:  {}".format(MIGRATION_ID))
    print("Description:   {}".format(DESCRIPTION))
    print()
    if runner.is_applied(MIGRATION_ID):
        print("State: ALREADY RECORDED — --apply would no-op.")
    else:
        print("State: NOT RECORDED — --apply would insert this row.")
    print()
    print("Existing schema_version rows:")
    applied = runner.list_applied()
    if not applied:
        print("  (none)")
    else:
        for entry in applied:
            print("  {migration_id}  {applied_at}  {description}".format(
                **entry))
    print()
    print("Dry run complete. No writes performed.")


def _build_parser() -> _Parser:
    parser = _Parser(
        description=("Retro-record migration 001 in the schema_version "
                     "registry. One-shot.")
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview the registry insert (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Insert the registry row after creating a "
                           "backup. Refuses to run if the print-farm "
                           "service is up.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH,
                        help="Path to work_orders.db "
                             "(default: {}).".format(DEFAULT_DB_PATH))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    db_path = args.db_path
    if not os.path.exists(db_path):
        print("ERROR: DB not found at {}".format(db_path), file=sys.stderr)
        return EXIT_BAD_ARGS

    runner = MigrationRunner(db_path)
    runner.ensure_schema_version_table()

    if not args.apply:
        _print_plan(runner)
        return EXIT_OK

    if runner.is_applied(MIGRATION_ID):
        print("Migration {} already recorded. Nothing to do.".format(
            MIGRATION_ID))
        return EXIT_OK

    if is_service_running():
        print("ERROR: print-farm-monitor appears to be running "
              "(port 5001 is bound).", file=sys.stderr)
        print("Stop the service first:", file=sys.stderr)
        print("  sudo systemctl stop print-farm-monitor", file=sys.stderr)
        return EXIT_SERVICE_RUNNING

    try:
        backup_path = create_backup(db_path)
    except (OSError, FileExistsError) as exc:
        print("ERROR: backup failed: {}".format(exc), file=sys.stderr)
        print("DB has not been modified.", file=sys.stderr)
        return EXIT_BACKUP_FAILED
    print("Backup created: {}".format(backup_path))

    code = _record(db_path, runner)
    if code == EXIT_OK:
        print("Recorded migration {} in schema_version.".format(MIGRATION_ID))
    return code


if __name__ == "__main__":
    sys.exit(main())
