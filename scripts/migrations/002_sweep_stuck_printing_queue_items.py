"""Sweep accumulated stuck-in-printing queue_items (pre-Phase-0 drift).

Between roughly March and April 2026, queue_items accumulated in
``status='printing'`` on the Pi while the Phase 0 predicate-widening
fix sat unmerged. This one-shot script triages every remaining
``printing`` queue_item by its FK linkage + production-record state and
either rolls it forward to ``completed`` (production actually
succeeded) or cancels it (production never ran or stalled).

Sub-cases (classified structurally, not by hardcoded IDs)
--------------------------------------------------------
A. Pre-execution stuck:    queue_job_id IS NULL AND print_job_id IS NULL.
                           No production record exists. Cancel the
                           queue_item; nothing else to update.

B. Mid-execution stuck:    queue_job_id IS NOT NULL AND the linked
                           print_jobs row has status='started' (process
                           died or printer was power-cycled before
                           completion). Cancel queue_item + queue_job;
                           stop the print_job with outcome='cancelled'.

C. Production completed:   queue_job_id IS NOT NULL AND the linked
                           print_jobs row has status='completed'. The
                           Phase 0 fix targets this case for future
                           rows; sweep any historical occurrences by
                           rolling queue_item + queue_job to
                           ``completed`` using the print_job's
                           completed_at as the timestamp.

Unclassified:              any other shape (e.g. queue_job_id populated
                           but no matching print_jobs row, or print_jobs
                           status is neither 'started' nor 'completed').
                           The script REFUSES to act on these — the
                           operator must classify them manually and
                           extend the script before re-running.

Two databases, two transactions
-------------------------------
This migration touches ``data/work_orders.db`` (queue_items, queue_jobs,
jobs, work_orders, schema_version) AND ``data/production_log.db``
(print_jobs). SQLite cannot run atomic transactions across separate
database files, so each DB gets its own transaction. The script commits
``work_orders.db`` first (registry write lives in that DB and ties the
migration to the queue_item changes), then ``production_log.db``. If
the second commit fails the script prints both backup paths and the
restore command for ``production_log.db`` — ``work_orders.db`` is
already durable at that point.

Usage
-----
    # Read-only preview (default if neither flag is passed):
    python scripts/migrations/002_sweep_stuck_printing_queue_items.py
    python scripts/migrations/002_sweep_stuck_printing_queue_items.py --dry-run

    # Apply. Creates a backup of BOTH DBs first.
    sudo systemctl stop print-farm-monitor
    python scripts/migrations/002_sweep_stuck_printing_queue_items.py --apply
    sudo systemctl start print-farm-monitor

    # Custom paths:
    python scripts/migrations/002_sweep_stuck_printing_queue_items.py \\
        --apply \\
        --db-path /srv/print-farm-monitor/data/work_orders.db \\
        --production-db-path /srv/print-farm-monitor/data/production_log.db

Recovery
--------
``--apply`` writes timestamped backups next to both DBs before any
writes:

    data/work_orders.db.bak-YYYYMMDD-HHMMSS
    data/production_log.db.bak-YYYYMMDD-HHMMSS

To restore both:

    sudo systemctl stop print-farm-monitor
    cp data/work_orders.db.bak-YYYYMMDD-HHMMSS data/work_orders.db
    cp data/production_log.db.bak-YYYYMMDD-HHMMSS data/production_log.db
    sudo systemctl start print-farm-monitor

Idempotence
-----------
The script registers under MIGRATION_ID='002' via the schema_version
registry. After a successful apply, re-running the script (in either
mode) detects the registry row and exits 0 without touching either DB.

Exit codes
----------
    0  Success (or dry-run / already-applied).
    1  Bad arguments.
    2  print-farm-monitor service still running on port 5001.
    3  Backup creation failed (DB untouched).
    4  PRAGMA foreign_key_check found violations (transaction rolled
       back; backups intact).
    5  Any other SQLite error (transaction rolled back; backups intact).
    6  Unclassified ``printing`` queue_items present — script refuses
       to guess. Inspect the triage output and extend the script.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.shared.migrations.runner import MigrationRunner  # noqa: E402
from app.domains.work_orders.status_sync import (  # noqa: E402
    sync_job_status,
    sync_work_order_status,
)


MIGRATION_ID = "002"
DESCRIPTION = (
    "Sweep accumulated stuck-in-printing queue_items (pre-Phase-0 drift)"
)

DEFAULT_DB_PATH = "data/work_orders.db"
DEFAULT_PRODUCTION_DB_PATH = "data/production_log.db"

# Exit codes
EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_SERVICE_RUNNING = 2
EXIT_BACKUP_FAILED = 3
EXIT_FK_VIOLATION = 4
EXIT_SQLITE_ERROR = 5
EXIT_UNCLASSIFIED = 6

# Sub-case tags used in the triage table and the apply summary.
CASE_A = "A"  # pre-execution stuck (cancel queue_item)
CASE_B = "B"  # mid-execution stalled (cancel chain + stop print_job)
CASE_C = "C"  # production completed (roll forward)
CASE_UNCLASSIFIED = "?"


class _Parser(argparse.ArgumentParser):
    """argparse with our exit-code convention."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_BAD_ARGS,
                  "{}: error: {}\n".format(self.prog, message))


# ----------------------------------------------------------------------
# Service safety
# ----------------------------------------------------------------------

def is_service_running(host: str = "127.0.0.1", port: int = 5001,
                       timeout_sec: float = 1.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_sec)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


# ----------------------------------------------------------------------
# Backup
# ----------------------------------------------------------------------

def create_backup(db_path: str, stamp: str) -> str:
    backup_path = "{}.bak-{}".format(db_path, stamp)
    if os.path.exists(backup_path):
        raise FileExistsError(
            "Backup path already exists: {}".format(backup_path)
        )
    shutil.copy2(db_path, backup_path)
    return backup_path


# ----------------------------------------------------------------------
# Classification
# ----------------------------------------------------------------------

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def is_migration_applied(db_path: str, migration_id: str) -> bool:
    """Return True only if schema_version exists AND has the row.

    Read-only — opens the DB in URI ``mode=ro``. Treats a missing
    ``schema_version`` table as "not applied yet" so dry-run on a fresh
    DB doesn't crash trying to read a registry that hasn't been
    initialized.
    """
    conn = sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)
    try:
        if not _table_exists(conn, "schema_version"):
            return False
        row = conn.execute(
            "SELECT 1 FROM schema_version WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def fetch_applied_at(db_path: str, migration_id: str) -> Optional[str]:
    conn = sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)
    try:
        if not _table_exists(conn, "schema_version"):
            return None
        row = conn.execute(
            "SELECT applied_at FROM schema_version WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def classify_stuck_rows(wo_conn: sqlite3.Connection,
                        prod_conn: sqlite3.Connection) -> List[dict]:
    """Pull every ``printing`` queue_item and tag it by sub-case.

    Both connections must be open and read-capable; this function does
    not write. Returns a list of dicts with the queue_item fields plus
    ``sub_case``, ``print_job_status``, and ``print_job_completed_at``.
    """
    if not _table_exists(wo_conn, "queue_items"):
        return []

    rows = wo_conn.execute("""
        SELECT queue_id, wo_id, job_id, queue_job_id, print_job_id,
               part_name, sequence_number, total_quantity, status
        FROM queue_items
        WHERE status = 'printing'
        ORDER BY queue_id
    """).fetchall()

    classified: List[dict] = []
    has_print_jobs = _table_exists(prod_conn, "print_jobs")

    for row in rows:
        entry = dict(row)
        pj_status: Optional[str] = None
        pj_completed_at: Optional[str] = None

        if entry["print_job_id"] is not None and has_print_jobs:
            pj_row = prod_conn.execute(
                "SELECT status, completed_at FROM print_jobs "
                "WHERE job_id = ?",
                (entry["print_job_id"],),
            ).fetchone()
            if pj_row is not None:
                pj_status = pj_row["status"]
                pj_completed_at = pj_row["completed_at"]

        entry["print_job_status"] = pj_status
        entry["print_job_completed_at"] = pj_completed_at

        if entry["queue_job_id"] is None and entry["print_job_id"] is None:
            entry["sub_case"] = CASE_A
        elif (entry["queue_job_id"] is not None
                and entry["print_job_id"] is not None
                and pj_status == "completed"):
            entry["sub_case"] = CASE_C
        elif (entry["queue_job_id"] is not None
                and entry["print_job_id"] is not None
                and pj_status == "started"):
            entry["sub_case"] = CASE_B
        else:
            entry["sub_case"] = CASE_UNCLASSIFIED

        classified.append(entry)

    return classified


# ----------------------------------------------------------------------
# Triage output
# ----------------------------------------------------------------------

_CASE_LABEL = {
    CASE_A: "A pre-execution stuck -> cancel queue_item",
    CASE_B: "B mid-execution stalled -> cancel chain + stop print_job",
    CASE_C: "C production completed -> roll forward to completed",
    CASE_UNCLASSIFIED: "? unclassified -> refuses to act",
}


def _status_path(entry: dict) -> str:
    parts = ["qi=printing"]
    if entry["queue_job_id"] is None:
        parts.append("qj=null")
    else:
        parts.append("qj=#{}".format(entry["queue_job_id"]))
    if entry["print_job_id"] is None:
        parts.append("pj=null")
    else:
        parts.append("pj=#{}({})".format(
            entry["print_job_id"], entry["print_job_status"] or "missing"
        ))
    return ", ".join(parts)


def print_triage(db_path: str, production_db_path: str,
                 classified: List[dict]) -> None:
    print("=" * 70)
    print("Migration {}: {}".format(MIGRATION_ID, DESCRIPTION))
    print("=" * 70)
    print("work_orders DB:    {}".format(db_path))
    print("production_log DB: {}".format(production_db_path))
    print()

    if not classified:
        print("No queue_items currently stuck in 'printing'. "
              "Nothing to triage.")
        print()
        print("Dry run complete. No writes performed.")
        return

    print("Stuck queue_items: {}".format(len(classified)))
    print()
    print("{:>6}  {:<30}  {:<6}  {:<40}  {}".format(
        "qid", "part", "case", "status_path", "planned action"
    ))
    print("-" * 110)
    for entry in classified:
        part_label = "{}/{} {}".format(
            entry["sequence_number"], entry["total_quantity"],
            entry["part_name"] or "?",
        )
        if len(part_label) > 30:
            part_label = part_label[:27] + "..."
        action = _CASE_LABEL[entry["sub_case"]]
        print("{:>6}  {:<30}  {:<6}  {:<40}  {}".format(
            entry["queue_id"], part_label, entry["sub_case"],
            _status_path(entry), action,
        ))

    counts: Dict[str, int] = {
        CASE_A: 0, CASE_B: 0, CASE_C: 0, CASE_UNCLASSIFIED: 0,
    }
    for entry in classified:
        counts[entry["sub_case"]] += 1

    print()
    print("Counts by sub-case:")
    for case in (CASE_A, CASE_B, CASE_C, CASE_UNCLASSIFIED):
        print("  {:<22} {}".format(_CASE_LABEL[case], counts[case]))

    if counts[CASE_UNCLASSIFIED]:
        print()
        print("WARNING: --apply will refuse to proceed while any rows "
              "remain unclassified.")
        print("Inspect the rows above, classify them manually, and "
              "extend the script before re-running.")

    wo_ids = sorted({e["wo_id"] for e in classified if e["wo_id"]})
    job_ids = sorted({e["job_id"] for e in classified if e["job_id"]})
    print()
    print("Rollup recompute footprint after sweep:")
    print("  work_orders touched: {}".format(
        ", ".join(wo_ids) if wo_ids else "(none)"
    ))
    print("  jobs touched:        {}".format(
        ", ".join(str(j) for j in job_ids) if job_ids else "(none)"
    ))
    print()
    print("Dry run complete. No writes performed.")


# ----------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------

def _apply_writes(wo_conn: sqlite3.Connection,
                  prod_conn: sqlite3.Connection,
                  classified: List[dict],
                  runner: MigrationRunner) -> Dict[str, object]:
    """Perform the actual updates inside the open transactions.

    Caller has already issued BEGIN on both connections. This function
    does NOT commit — it leaves both transactions open so the caller
    can run foreign_key_check and decide whether to commit.

    Returns a summary dict for the apply-summary printer.
    """
    now = datetime.now(timezone.utc).isoformat()
    summary: Dict[str, object] = {
        "case_a": 0,
        "case_b": 0,
        "case_c": 0,
        "queue_jobs_updated": 0,
        "print_jobs_updated": 0,
        "jobs_synced": [],
        "work_orders_synced": [],
    }

    queue_job_ids_touched: Dict[int, str] = {}  # qj_id -> target status

    for entry in classified:
        qid = entry["queue_id"]
        case = entry["sub_case"]
        qjid = entry["queue_job_id"]
        pjid = entry["print_job_id"]

        if case == CASE_A:
            wo_conn.execute(
                "UPDATE queue_items "
                "SET status = 'cancelled', completed_at = ? "
                "WHERE queue_id = ?",
                (now, qid),
            )
            summary["case_a"] += 1

        elif case == CASE_B:
            wo_conn.execute(
                "UPDATE queue_items "
                "SET status = 'cancelled', completed_at = ? "
                "WHERE queue_id = ?",
                (now, qid),
            )
            if qjid is not None:
                queue_job_ids_touched[qjid] = "cancelled"
            if pjid is not None:
                prod_conn.execute(
                    "UPDATE print_jobs "
                    "SET status = 'stopped', completed_at = ?, "
                    "    outcome = 'cancelled' "
                    "WHERE job_id = ?",
                    (now, pjid),
                )
                summary["print_jobs_updated"] += 1
            summary["case_b"] += 1

        elif case == CASE_C:
            roll_forward_ts = (
                entry["print_job_completed_at"] or now
            )
            wo_conn.execute(
                "UPDATE queue_items "
                "SET status = 'completed', completed_at = ? "
                "WHERE queue_id = ?",
                (roll_forward_ts, qid),
            )
            if qjid is not None:
                queue_job_ids_touched[qjid] = "completed"
            summary["case_c"] += 1

    for qjid, target_status in queue_job_ids_touched.items():
        remaining = wo_conn.execute(
            "SELECT status FROM queue_items WHERE queue_job_id = ?",
            (qjid,),
        ).fetchall()
        statuses = [r["status"] for r in remaining]
        if not statuses:
            continue
        if target_status == "cancelled" and all(
                s in ("cancelled", "completed") for s in statuses):
            terminal = ("cancelled"
                        if all(s == "cancelled" for s in statuses)
                        else "completed")
            wo_conn.execute(
                "UPDATE queue_jobs "
                "SET status = ?, completed_at = ? "
                "WHERE queue_job_id = ?",
                (terminal, now, qjid),
            )
            summary["queue_jobs_updated"] += 1
        elif target_status == "completed" and all(
                s in ("completed", "cancelled") for s in statuses):
            terminal = ("completed"
                        if any(s == "completed" for s in statuses)
                        else "cancelled")
            wo_conn.execute(
                "UPDATE queue_jobs "
                "SET status = ?, completed_at = ? "
                "WHERE queue_job_id = ?",
                (terminal, now, qjid),
            )
            summary["queue_jobs_updated"] += 1

    job_ids = sorted({e["job_id"] for e in classified if e["job_id"]})
    wo_ids = sorted({e["wo_id"] for e in classified if e["wo_id"]})

    synced_jobs: List[Tuple[int, str]] = []
    for jid in job_ids:
        new_status = sync_job_status(wo_conn, jid)
        synced_jobs.append((jid, new_status))
    summary["jobs_synced"] = synced_jobs

    synced_wos: List[Tuple[str, str]] = []
    for wid in wo_ids:
        new_status = sync_work_order_status(wo_conn, wid)
        synced_wos.append((wid, new_status))
    summary["work_orders_synced"] = synced_wos

    runner.record(MIGRATION_ID, DESCRIPTION, wo_conn)

    return summary


def apply_migration(db_path: str, production_db_path: str,
                    classified: List[dict],
                    runner: MigrationRunner,
                    wo_backup: str, prod_backup: str) -> int:
    wo_conn = sqlite3.connect(db_path, isolation_level=None)
    wo_conn.row_factory = sqlite3.Row
    prod_conn = sqlite3.connect(production_db_path, isolation_level=None)
    prod_conn.row_factory = sqlite3.Row

    summary: Dict[str, object] = {}
    try:
        wo_conn.execute("BEGIN")
        prod_conn.execute("BEGIN")
        try:
            summary = _apply_writes(wo_conn, prod_conn, classified, runner)

            violations = wo_conn.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if violations:
                print("ERROR: foreign_key_check on work_orders.db "
                      "reported violations:", file=sys.stderr)
                for v in violations:
                    print("  {}".format(dict(v)), file=sys.stderr)
                wo_conn.execute("ROLLBACK")
                prod_conn.execute("ROLLBACK")
                print("Both transactions rolled back. Backups intact:",
                      file=sys.stderr)
                print("  {}".format(wo_backup), file=sys.stderr)
                print("  {}".format(prod_backup), file=sys.stderr)
                return EXIT_FK_VIOLATION

            wo_conn.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            try:
                wo_conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            try:
                prod_conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            print("Migration {} already recorded — nothing to do "
                  "({}).".format(MIGRATION_ID, exc), file=sys.stderr)
            return EXIT_OK
        except sqlite3.Error as exc:
            try:
                wo_conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            try:
                prod_conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            print("ERROR: SQLite error on work_orders.db transaction: "
                  "{}".format(exc), file=sys.stderr)
            print("Both transactions rolled back. Backups intact:",
                  file=sys.stderr)
            print("  {}".format(wo_backup), file=sys.stderr)
            print("  {}".format(prod_backup), file=sys.stderr)
            return EXIT_SQLITE_ERROR

        try:
            prod_conn.execute("COMMIT")
        except sqlite3.Error as exc:
            print("ERROR: work_orders.db commit succeeded but "
                  "production_log.db commit FAILED: {}".format(exc),
                  file=sys.stderr)
            print("work_orders.db is already durable (schema_version row "
                  "and queue_item/queue_job updates are committed).",
                  file=sys.stderr)
            print("production_log.db transaction was rolled back, so any "
                  "print_jobs updates were discarded.", file=sys.stderr)
            print("To restore production_log.db to its pre-migration "
                  "state:", file=sys.stderr)
            print("  cp '{}' '{}'".format(prod_backup, production_db_path),
                  file=sys.stderr)
            print("Then re-apply only the print_jobs updates manually "
                  "(see sub-case B in the script docstring).",
                  file=sys.stderr)
            print("work_orders.db backup: {}".format(wo_backup),
                  file=sys.stderr)
            return EXIT_SQLITE_ERROR

        print_apply_summary(db_path, production_db_path, wo_backup,
                            prod_backup, summary)
        return EXIT_OK
    finally:
        wo_conn.close()
        prod_conn.close()


def print_apply_summary(db_path: str, production_db_path: str,
                        wo_backup: str, prod_backup: str,
                        summary: Dict[str, object]) -> None:
    print("=" * 70)
    print("Migration {} applied".format(MIGRATION_ID))
    print("=" * 70)
    print("work_orders DB:    {}".format(db_path))
    print("production_log DB: {}".format(production_db_path))
    print("Backups:")
    print("  {}".format(wo_backup))
    print("  {}".format(prod_backup))
    print()
    print("queue_items updated:")
    print("  sub-case A (cancelled, pre-execution): {}".format(
        summary.get("case_a", 0)))
    print("  sub-case B (cancelled, mid-execution): {}".format(
        summary.get("case_b", 0)))
    print("  sub-case C (rolled forward to completed): {}".format(
        summary.get("case_c", 0)))
    print("queue_jobs updated: {}".format(
        summary.get("queue_jobs_updated", 0)))
    print("print_jobs stopped (sub-case B): {}".format(
        summary.get("print_jobs_updated", 0)))
    print()
    synced_jobs = summary.get("jobs_synced") or []
    print("jobs.status recomputed:")
    if not synced_jobs:
        print("  (none)")
    else:
        for jid, status in synced_jobs:
            print("  job_id={}: {}".format(jid, status))
    synced_wos = summary.get("work_orders_synced") or []
    print("work_orders.status recomputed:")
    if not synced_wos:
        print("  (none)")
    else:
        for wid, status in synced_wos:
            print("  {}: {}".format(wid, status))


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def _build_parser() -> _Parser:
    parser = _Parser(description=DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview the sweep without touching either "
                           "DB (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Apply the sweep after creating backups. "
                           "Refuses to run if the print-farm service "
                           "is up or if any rows are unclassified.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH,
                        help="Path to work_orders.db "
                             "(default: {}).".format(DEFAULT_DB_PATH))
    parser.add_argument("--production-db-path",
                        default=DEFAULT_PRODUCTION_DB_PATH,
                        help="Path to production_log.db (default: {}).".format(
                            DEFAULT_PRODUCTION_DB_PATH))
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    db_path = args.db_path
    production_db_path = args.production_db_path
    for label, path in (("work_orders.db", db_path),
                        ("production_log.db", production_db_path)):
        if not os.path.exists(path):
            print("ERROR: {} not found at {}".format(label, path),
                  file=sys.stderr)
            return EXIT_BAD_ARGS

    if is_migration_applied(db_path, MIGRATION_ID):
        applied_at = fetch_applied_at(db_path, MIGRATION_ID)
        print("Migration {} already applied at {}. Nothing to do.".format(
            MIGRATION_ID, applied_at or "<unknown>"))
        return EXIT_OK

    wo_conn = sqlite3.connect("file:{}?mode=ro".format(db_path), uri=True)
    wo_conn.row_factory = sqlite3.Row
    prod_conn = sqlite3.connect(
        "file:{}?mode=ro".format(production_db_path), uri=True
    )
    prod_conn.row_factory = sqlite3.Row
    try:
        classified = classify_stuck_rows(wo_conn, prod_conn)
    finally:
        wo_conn.close()
        prod_conn.close()

    if not args.apply:
        print_triage(db_path, production_db_path, classified)
        return EXIT_OK

    if is_service_running():
        print("ERROR: print-farm-monitor appears to be running "
              "(port 5001 is bound).", file=sys.stderr)
        print("Stop the service first:", file=sys.stderr)
        print("  sudo systemctl stop print-farm-monitor", file=sys.stderr)
        return EXIT_SERVICE_RUNNING

    unclassified = [e for e in classified
                    if e["sub_case"] == CASE_UNCLASSIFIED]
    if unclassified:
        print("ERROR: {} unclassified queue_item(s) present. Refusing to "
              "act.".format(len(unclassified)), file=sys.stderr)
        for entry in unclassified:
            print("  queue_id={}: {}".format(
                entry["queue_id"], _status_path(entry)),
                file=sys.stderr)
        print("Re-run with --dry-run for the full triage, classify "
              "these rows manually, and extend the script.",
              file=sys.stderr)
        return EXIT_UNCLASSIFIED

    if not classified:
        runner = MigrationRunner(db_path)
        runner.ensure_schema_version_table()
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
                print("ERROR: SQLite error: {}".format(exc),
                      file=sys.stderr)
                return EXIT_SQLITE_ERROR
        finally:
            conn.close()
        print("No stuck queue_items present. Recorded migration {} so "
              "future runs no-op.".format(MIGRATION_ID))
        return EXIT_OK

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        wo_backup = create_backup(db_path, stamp)
    except (OSError, FileExistsError) as exc:
        print("ERROR: backup of {} failed: {}".format(db_path, exc),
              file=sys.stderr)
        return EXIT_BACKUP_FAILED
    print("Backup created: {}".format(wo_backup))

    try:
        prod_backup = create_backup(production_db_path, stamp)
    except (OSError, FileExistsError) as exc:
        print("ERROR: backup of {} failed: {}".format(
            production_db_path, exc), file=sys.stderr)
        print("work_orders.db backup at {} can be discarded.".format(
            wo_backup), file=sys.stderr)
        return EXIT_BACKUP_FAILED
    print("Backup created: {}".format(prod_backup))

    runner = MigrationRunner(db_path)
    runner.ensure_schema_version_table()

    return apply_migration(db_path, production_db_path, classified,
                           runner, wo_backup, prod_backup)


if __name__ == "__main__":
    sys.exit(main())
