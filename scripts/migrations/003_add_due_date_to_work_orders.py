"""Add ``due_date`` column to ``work_orders``.

Phase 2.5a's Dashboard surfaces a "Late WOs" stat tile, which counts
work orders past their due date that aren't completed or cancelled.
The current ``work_orders`` schema has no due-date column. This adds
one as a nullable ``TEXT`` (ISO date string, consistent with the
table's other date columns: ``created_at``, ``completed_at``).

Pattern mirrors ``_template.py.example`` and migration 002.

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
from typing import Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.shared.migrations.runner import MigrationRunner  # noqa: E402


MIGRATION_ID = "003"
DESCRIPTION = "Add due_date column to work_orders"

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


def _column_exists(conn: sqlite3.Connection, table: str,
                   column: str) -> bool:
    cursor = conn.execute("PRAGMA table_info({})".format(table))
    return any(row[1] == column for row in cursor.fetchall())


def _describe_plan(conn: sqlite3.Connection) -> None:
    print("=" * 70)
    print("Migration {}: {}".format(MIGRATION_ID, DESCRIPTION))
    print("=" * 70)
    if _column_exists(conn, "work_orders", "due_date"):
        print("Column work_orders.due_date already exists.")
        print("Apply would only record the migration in schema_version.")
    else:
        total = conn.execute(
            "SELECT COUNT(*) FROM work_orders"
        ).fetchone()[0]
        print("Will add nullable TEXT column 'due_date' to work_orders.")
        print("Rows in work_orders: {} (all will have NULL due_date).".format(total))
    print()
    print("Dry run complete. No writes performed.")


def _perform_changes(conn: sqlite3.Connection) -> None:
    if not _column_exists(conn, "work_orders", "due_date"):
        conn.execute("ALTER TABLE work_orders ADD COLUMN due_date TEXT")


def apply_migration(db_path: str, runner: MigrationRunner) -> int:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        try:
            _perform_changes(conn)
            runner.record(MIGRATION_ID, DESCRIPTION, conn)
            conn.execute("COMMIT")
            return EXIT_OK
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
            print("Transaction rolled back. DB is unchanged from before "
                  "this run.", file=sys.stderr)
            return EXIT_SQLITE_ERROR
    finally:
        conn.close()


def _build_parser() -> _Parser:
    parser = _Parser(description=DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview the migration without touching the "
                           "DB (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Apply the migration after creating a "
                           "backup. Refuses to run if the print-farm "
                           "service is up.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH,
                        help="Path to the target DB "
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

    if runner.is_applied(MIGRATION_ID):
        print("Migration {} already applied. Nothing to do.".format(
            MIGRATION_ID))
        return EXIT_OK

    if not args.apply:
        conn = sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)
        conn.row_factory = sqlite3.Row
        try:
            _describe_plan(conn)
        finally:
            conn.close()
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

    return apply_migration(db_path, runner)


if __name__ == "__main__":
    sys.exit(main())
