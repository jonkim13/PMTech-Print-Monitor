"""Remove the WO-001/002/003 dummy work orders and renumber the remainder.

This is a one-shot data migration meant to run on the Pi against the
production ``data/work_orders.db``. It:

1. Deletes WO-001, WO-002, WO-003 (and all dependent rows) from the
   five tables in ``work_orders.db``: ``queue_jobs``, ``queue_items``,
   ``jobs``, ``line_items``, ``work_orders``.
2. Renumbers every surviving work order down to fill the gap so the
   visible numbering starts at WO-001 again.

The renumber updates ``wo_id`` in five tables atomically inside a
single transaction. Foreign keys are temporarily disabled (NO ACTION
on every FK in this schema, so no automatic cascade), the rows are
rewritten in ascending-target order to avoid PK collisions, and
``PRAGMA foreign_key_check`` validates consistency before commit.

Usage
-----
    # Read-only preview (default if neither flag is passed):
    python scripts/migrations/001_remove_dummy_wos_and_renumber.py
    python scripts/migrations/001_remove_dummy_wos_and_renumber.py --dry-run

    # Apply the migration. Creates a backup first.
    sudo systemctl stop print-farm-monitor
    python scripts/migrations/001_remove_dummy_wos_and_renumber.py --apply
    sudo systemctl start print-farm-monitor

    # Custom DB path:
    python scripts/migrations/001_remove_dummy_wos_and_renumber.py \
        --apply --db-path /srv/print-farm-monitor/data/work_orders.db

Recovery
--------
``--apply`` writes a timestamped backup next to the DB before touching
anything:

    data/work_orders.db.bak-YYYYMMDD-HHMMSS

To restore:

    sudo systemctl stop print-farm-monitor
    cp data/work_orders.db.bak-YYYYMMDD-HHMMSS data/work_orders.db
    sudo systemctl start print-farm-monitor

Warning
-------
Stop the print-farm-monitor service before running ``--apply``. The
script refuses to run when something is bound to port 5001 to avoid
writing to a DB that the live polling thread is reading.

Exit codes
----------
    0  Success (or dry-run completed cleanly).
    1  Bad arguments / unknown flag.
    2  The print-farm-monitor service is still running on port 5001.
    3  Backup creation failed (DB untouched).
    4  PRAGMA foreign_key_check found violations (transaction rolled
       back; backup intact).
    5  Any other SQLite error (transaction rolled back; backup intact).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import sqlite3
import sys
from datetime import datetime
from typing import List, Optional, Sequence, Tuple


DUMMY_WO_IDS: Tuple[str, ...] = ("WO-001", "WO-002", "WO-003")
DEFAULT_DB_PATH = "data/work_orders.db"

# Tables that carry wo_id, ordered child-first for deletes. The same
# tables receive the renumber UPDATE; the order does not matter for
# UPDATE because foreign keys are disabled during the transaction.
WO_ID_TABLES: Tuple[str, ...] = (
    "queue_jobs",
    "queue_items",
    "jobs",
    "line_items",
    "work_orders",
)

# Exit codes
EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_SERVICE_RUNNING = 2
EXIT_BACKUP_FAILED = 3
EXIT_FK_VIOLATION = 4
EXIT_SQLITE_ERROR = 5

WO_ID_RE = re.compile(r"^WO-(\d+)$")


class _Parser(argparse.ArgumentParser):
    """argparse with our exit-code convention."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_BAD_ARGS,
                  "{}: error: {}\n".format(self.prog, message))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def parse_wo_number(wo_id: str) -> Optional[int]:
    """Return the numeric suffix of ``WO-NNN``, or None if malformed."""
    match = WO_ID_RE.match(wo_id)
    if not match:
        return None
    return int(match.group(1))


def format_wo_id(number: int) -> str:
    """Format an integer as ``WO-NNN`` with the project's zero padding."""
    return "WO-{:03d}".format(number)


def fetch_existing_wo_ids(conn: sqlite3.Connection) -> List[str]:
    """All wo_id values, sorted naturally by numeric suffix when valid."""
    rows = conn.execute("SELECT wo_id FROM work_orders").fetchall()
    ids = [r[0] for r in rows]

    def sort_key(value: str):
        number = parse_wo_number(value)
        return (0, number) if number is not None else (1, value)

    return sorted(ids, key=sort_key)


def count_rows_for_wo_ids(conn: sqlite3.Connection,
                          wo_ids: Sequence[str]) -> dict:
    """Per-table count of rows referencing any of ``wo_ids``."""
    if not wo_ids:
        return {table: 0 for table in WO_ID_TABLES}
    placeholders = ",".join("?" for _ in wo_ids)
    counts = {}
    for table in WO_ID_TABLES:
        row = conn.execute(
            "SELECT COUNT(*) FROM {} WHERE wo_id IN ({})".format(
                table, placeholders),
            tuple(wo_ids),
        ).fetchone()
        counts[table] = row[0] if row else 0
    return counts


def build_renumber_mapping(surviving_wo_ids: Sequence[str]
                           ) -> List[Tuple[str, str]]:
    """Pair each surviving wo_id with its compacted new id.

    Surviving ids are renumbered to 1, 2, 3, ... in their original sort
    order. Only entries that actually need to change are returned, so
    the mapping is empty when nothing needs renumbering.

    Pairs are returned in ascending order of the new wo_id, which is
    the order writes must execute to avoid PK collisions while
    foreign_keys is disabled.
    """
    pairs: List[Tuple[str, str]] = []
    for index, old_id in enumerate(surviving_wo_ids, start=1):
        new_id = format_wo_id(index)
        if new_id != old_id:
            pairs.append((old_id, new_id))
    return pairs


# ----------------------------------------------------------------------
# Service safety
# ----------------------------------------------------------------------

def is_service_running(host: str = "127.0.0.1", port: int = 5001,
                       timeout_sec: float = 1.0) -> bool:
    """Return True when something is bound to ``host:port``.

    Uses ``socket.connect_ex`` — a 0 return means the connect handshake
    succeeded, which means the port is in use. Any non-zero return
    (refused / unreachable / timeout) means the port is free.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


# ----------------------------------------------------------------------
# Backup
# ----------------------------------------------------------------------

def create_backup(db_path: str) -> str:
    """Copy ``db_path`` to a timestamped ``.bak-...`` sibling.

    Returns the backup path on success. Raises on any I/O failure so
    the caller can map it to the exit code.
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = "{}.bak-{}".format(db_path, stamp)
    if os.path.exists(backup_path):
        # Extremely unlikely (same-second invocation) but make
        # filename collision an error rather than a silent overwrite.
        raise FileExistsError(
            "Backup path already exists: {}".format(backup_path)
        )
    shutil.copy2(db_path, backup_path)
    return backup_path


# ----------------------------------------------------------------------
# Dry-run output
# ----------------------------------------------------------------------

def print_plan(db_path: str,
               existing_wo_ids: List[str],
               present_dummies: List[str],
               dummy_counts: dict,
               surviving_wo_ids: List[str],
               renumber_mapping: List[Tuple[str, str]]) -> None:
    """Render the planned changes without performing them."""
    print("=" * 70)
    print("Migration 001: remove WO-001/002/003 + renumber survivors")
    print("=" * 70)
    print("DB path:            {}".format(db_path))
    print("Total work orders:  {}".format(len(existing_wo_ids)))
    print("Existing wo_ids:    {}".format(
        ", ".join(existing_wo_ids) if existing_wo_ids else "(none)"
    ))
    print()
    print("--- Phase A: delete dummies ---")
    if not present_dummies:
        print("No dummy WO-001/002/003 rows present. Nothing to delete.")
    else:
        print("Would delete: {}".format(", ".join(present_dummies)))
        for table in WO_ID_TABLES:
            print("  {:>14s}: {} row(s) referencing dummy wo_ids".format(
                table, dummy_counts.get(table, 0)))
    print()
    print("--- Phase B: renumber survivors ---")
    if not surviving_wo_ids:
        print("No surviving work orders. Nothing to renumber.")
    elif not renumber_mapping:
        print("Survivors already numbered consecutively from WO-001. "
              "Nothing to renumber.")
        print("Survivors:        {}".format(", ".join(surviving_wo_ids)))
    else:
        print("Renumber mapping ({} change(s), processed in ascending "
              "order of new wo_id):".format(len(renumber_mapping)))
        for old_id, new_id in renumber_mapping:
            print("  {} -> {}".format(old_id, new_id))
        unchanged = [
            wo for wo in surviving_wo_ids
            if wo not in {pair[0] for pair in renumber_mapping}
        ]
        if unchanged:
            print("Unchanged: {}".format(", ".join(unchanged)))
    print()
    print("--- Expected final state ---")
    final_count = len(surviving_wo_ids)
    if final_count == 0:
        print("Work orders after migration: 0")
    else:
        print("Work orders after migration: {}".format(final_count))
        print("Final wo_id range: WO-001 .. {}".format(
            format_wo_id(final_count)))
    print()
    print("Note: wo_id is generated via ORDER BY rowid DESC LIMIT 1 in "
          "app/domains/work_orders/repository.py::_next_wo_id (no "
          "counter column to reset).")
    print()
    print("Dry run complete. No writes performed.")


# ----------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------

def delete_dummy_rows(conn: sqlite3.Connection,
                      dummy_ids: Sequence[str]) -> dict:
    """Delete every row in ``WO_ID_TABLES`` referencing a dummy wo_id.

    Returns a per-table count of rows actually deleted.
    """
    if not dummy_ids:
        return {table: 0 for table in WO_ID_TABLES}
    placeholders = ",".join("?" for _ in dummy_ids)
    deleted = {}
    for table in WO_ID_TABLES:
        cursor = conn.execute(
            "DELETE FROM {} WHERE wo_id IN ({})".format(
                table, placeholders),
            tuple(dummy_ids),
        )
        deleted[table] = cursor.rowcount
    return deleted


def apply_renumber(conn: sqlite3.Connection,
                   mapping: Sequence[Tuple[str, str]]) -> None:
    """Rewrite wo_id in each WO_ID_TABLE per ``mapping``.

    The caller is responsible for disabling foreign_keys, opening the
    transaction, and running ``foreign_key_check`` afterwards.
    """
    for old_id, new_id in mapping:
        for table in WO_ID_TABLES:
            conn.execute(
                "UPDATE {} SET wo_id = ? WHERE wo_id = ?".format(table),
                (new_id, old_id),
            )


def apply_migration(db_path: str,
                    present_dummies: List[str],
                    mapping: List[Tuple[str, str]]) -> Tuple[int, dict]:
    """Run the destructive migration inside a single transaction.

    Returns ``(exit_code, summary_dict)``. The connection is opened in
    autocommit mode (``isolation_level=None``) so the script controls
    BEGIN / COMMIT / ROLLBACK explicitly — required because
    ``PRAGMA foreign_keys`` is a no-op inside an open transaction.
    """
    summary = {"deleted": {}, "renamed": 0, "fk_violations": []}
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        try:
            summary["deleted"] = delete_dummy_rows(conn, present_dummies)
            apply_renumber(conn, mapping)
            summary["renamed"] = len(mapping)

            violations = conn.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if violations:
                summary["fk_violations"] = [dict(v) for v in violations]
                conn.execute("ROLLBACK")
                return EXIT_FK_VIOLATION, summary

            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            summary["error"] = str(exc)
            return EXIT_SQLITE_ERROR, summary
        finally:
            # Re-enable FK enforcement for whatever happens next.
            # Safe to call even if rollback happened — there's no
            # active transaction at this point.
            try:
                conn.execute("PRAGMA foreign_keys = ON")
            except sqlite3.Error:
                pass
        return EXIT_OK, summary
    finally:
        conn.close()


def print_apply_summary(db_path: str, backup_path: str,
                        deleted: dict, renamed: int,
                        post_state: List[str]) -> None:
    print("=" * 70)
    print("Migration applied")
    print("=" * 70)
    print("DB path:    {}".format(db_path))
    print("Backup:     {}".format(backup_path))
    print()
    print("Rows deleted (dummies):")
    for table in WO_ID_TABLES:
        print("  {:>14s}: {}".format(table, deleted.get(table, 0)))
    print()
    print("Work orders renumbered: {}".format(renamed))
    print()
    print("Final state:")
    print("  total work_orders: {}".format(len(post_state)))
    if post_state:
        print("  wo_ids: {}".format(", ".join(post_state)))
    print()
    print("To restore: cp '{}' '{}'".format(backup_path, db_path))


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def _build_parser() -> _Parser:
    parser = _Parser(
        description="Remove WO-001/002/003 dummies and renumber the "
                    "remaining work orders to fill the gap."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview the migration without touching the "
                           "DB (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Apply the migration after creating a "
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

    # Plan the migration. Both dry-run and apply paths read the same
    # state to decide what to do.
    conn = sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)
    try:
        existing_wo_ids = fetch_existing_wo_ids(conn)
        present_dummies = [wo for wo in DUMMY_WO_IDS
                           if wo in existing_wo_ids]
        dummy_counts = count_rows_for_wo_ids(conn, present_dummies)
        surviving_wo_ids = [wo for wo in existing_wo_ids
                            if wo not in present_dummies]
        renumber_mapping = build_renumber_mapping(surviving_wo_ids)
    finally:
        conn.close()

    if not args.apply:
        # Default branch — dry-run.
        print_plan(db_path, existing_wo_ids, present_dummies,
                   dummy_counts, surviving_wo_ids, renumber_mapping)
        return EXIT_OK

    # --apply path
    if is_service_running():
        print("ERROR: print-farm-monitor appears to be running "
              "(port 5001 is bound).",
              file=sys.stderr)
        print("Stop the service first:", file=sys.stderr)
        print("  sudo systemctl stop print-farm-monitor", file=sys.stderr)
        print("Then re-run this script.", file=sys.stderr)
        return EXIT_SERVICE_RUNNING

    if not present_dummies and not renumber_mapping:
        print("Nothing to do — no dummies present and survivors are "
              "already numbered consecutively from WO-001.")
        return EXIT_OK

    try:
        backup_path = create_backup(db_path)
    except (OSError, FileExistsError) as exc:
        print("ERROR: backup failed: {}".format(exc), file=sys.stderr)
        print("DB has not been modified.", file=sys.stderr)
        return EXIT_BACKUP_FAILED
    print("Backup created: {}".format(backup_path))

    exit_code, summary = apply_migration(
        db_path, present_dummies, renumber_mapping
    )

    if exit_code == EXIT_FK_VIOLATION:
        print("ERROR: PRAGMA foreign_key_check reported violations:",
              file=sys.stderr)
        for v in summary.get("fk_violations", []):
            print("  {}".format(dict(v) if not isinstance(v, dict) else v),
                  file=sys.stderr)
        print("Transaction rolled back. DB is unchanged from before "
              "this run.", file=sys.stderr)
        print("Restore the backup if you want to verify: "
              "cp '{}' '{}'".format(backup_path, db_path),
              file=sys.stderr)
        return EXIT_FK_VIOLATION

    if exit_code == EXIT_SQLITE_ERROR:
        print("ERROR: SQLite error: {}".format(
            summary.get("error", "unknown")), file=sys.stderr)
        print("Transaction rolled back. DB is unchanged from before "
              "this run.", file=sys.stderr)
        print("Restore the backup if needed: "
              "cp '{}' '{}'".format(backup_path, db_path),
              file=sys.stderr)
        return EXIT_SQLITE_ERROR

    # Success — read the final state for the summary table.
    conn = sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)
    try:
        post_state = fetch_existing_wo_ids(conn)
    finally:
        conn.close()
    print_apply_summary(db_path, backup_path,
                        summary.get("deleted", {}),
                        summary.get("renamed", 0),
                        post_state)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
