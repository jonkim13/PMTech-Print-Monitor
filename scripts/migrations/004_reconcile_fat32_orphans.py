"""Reconcile FAT32 duplicate-row orphans accumulated before Phase 6.

Phase 6 introduced a state-based dedup branch in
``PrintJobRepository.create_job`` keyed on the invariant "at most one
status='started' row per printer within the last 24h." That fix
prevents new duplicate rows but does not reach the rows that already
exist on the Pi. This one-shot script sweeps the production database
and reconciles each row currently in ``status='started'`` whose
``started_at`` is more than 24h old.

Sub-cases (classified structurally, not by hardcoded IDs)
---------------------------------------------------------
**Paired**     Another row exists for the same ``printer_id`` with
               ``status IN ('completed', 'failed', 'stopped')`` whose
               ``started_at`` is within +/-300s of the orphan's
               ``started_at``. The orphan is the doomed first half of
               a FAT32 long/short filename flip — the canonical
               completion landed on the paired row. UPDATE the orphan
               to ``status='stopped'`` and stamp a note that points
               at the canonical ``job_id``.

**Solo**      No paired completion within 300s. The orphan has an
               unknown root cause (predates Phase 0 in most cases,
               but at least one post-Phase-0 instance exists in the
               wild — see audit item #22). UPDATE to
               ``status='stopped'`` with a distinguishing note so
               the row is preserved for future analysis but no
               longer pollutes the active-job recovery path.

**Anomaly**    More than one completion candidate matches within
               300s. The script REFUSES to act and lists the
               anomaly job_ids — an operator must triage manually.

The 24h orphan cutoff is non-negotiable: it matches the new
``create_job`` branch's bound and ensures today's in-flight prints
(like job 68 ``FEMALE~1.BGC`` started 2026-05-22T15:39 UTC) are
never touched.

Two layers of idempotence
-------------------------
1. ``MigrationRunner.is_applied('004_reconcile_fat32_orphans')``
   short-circuits before any DB work.
2. The data predicate ``status='started'`` excludes already-
   reconciled rows (which are now ``'stopped'``), so even if the
   registry row is somehow lost, a re-run is a no-op.

julianday() in the SQL
----------------------
``started_at`` is Python ISO format (``YYYY-MM-DDTHH:MM:SS+00:00``)
while SQLite's ``datetime()`` returns space-separated text. A lex
comparison would treat the 'T' at position 10 as greater than the
space and always return TRUE, defeating both the 24h orphan filter
and the 300s pair-window filter. ``julianday()`` parses both
formats correctly and returns a numeric (fractional-day) value.

Usage
-----
    # Read-only preview (default if neither flag is passed):
    python scripts/migrations/004_reconcile_fat32_orphans.py
    python scripts/migrations/004_reconcile_fat32_orphans.py --dry-run

    # Apply. Creates a backup of production_log.db first.
    sudo systemctl stop print-farm-monitor
    python scripts/migrations/004_reconcile_fat32_orphans.py --apply
    sudo systemctl start print-farm-monitor

    # Custom DB path:
    python scripts/migrations/004_reconcile_fat32_orphans.py \\
        --apply --db /path/to/production_log.db

Recovery
--------
``--apply`` writes a timestamped backup before any writes:

    data/production_log.db.bak-YYYYMMDD-HHMMSS

To restore:

    sudo systemctl stop print-farm-monitor
    cp data/production_log.db.bak-YYYYMMDD-HHMMSS data/production_log.db
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
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.shared.migrations.runner import MigrationRunner  # noqa: E402


MIGRATION_ID = "004_reconcile_fat32_orphans"
DESCRIPTION = (
    "Reconcile FAT32 duplicate-row orphans accumulated before Phase 6"
)

DEFAULT_DB_PATH = "data/production_log.db"

EXIT_OK = 0
EXIT_BAD_ARGS = 1
EXIT_SERVICE_RUNNING = 2
EXIT_BACKUP_FAILED = 3
EXIT_SQLITE_ERROR = 5

CASE_PAIRED = "paired"
CASE_SOLO = "solo"
CASE_ANOMALY = "anomaly"

PAIR_WINDOW_SECONDS = 300

PAIRED_NOTE_FMT = (
    "Reconciled FAT32 duplicate. Canonical job_id={canonical_id}."
)
SOLO_NOTE = (
    "Stale orphan reconciled, original cause not yet diagnosed. "
    "See audit #22."
)


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

def fetch_stale_orphans(conn: sqlite3.Connection) -> List[dict]:
    """Return every status='started' row older than 24h.

    Uses ``julianday('now') - julianday(started_at) >= 1`` so the
    text-format mismatch (Python's 'T' separator vs SQLite's space)
    doesn't poison the comparison. Today's in-flight prints — those
    with ``started_at`` within the last 24h — are NOT returned.
    """
    rows = conn.execute("""
        SELECT job_id, printer_id, printer_name, file_name,
               file_display_name, status, started_at,
               operator_initials, notes
        FROM print_jobs
        WHERE status = 'started'
          AND julianday('now') - julianday(started_at) >= 1
        ORDER BY printer_id, started_at
    """).fetchall()
    return [dict(r) for r in rows]


def find_pair_candidates(conn: sqlite3.Connection,
                         orphan: dict) -> List[dict]:
    """Return completion rows within +/-300s for the same printer.

    Status filter is ``IN ('completed', 'failed', 'stopped')`` — any
    terminal state. Excludes the orphan itself by ``job_id != ?``.
    Distance is computed in seconds via julianday(); ordered by
    proximity so callers can pick the closest as the canonical pair.
    """
    cur = conn.execute("""
        SELECT job_id, printer_id, status, started_at, completed_at,
               file_name,
               ABS(julianday(?) - julianday(started_at)) * 86400.0
                   AS delta_sec
        FROM print_jobs
        WHERE printer_id = ?
          AND status IN ('completed', 'failed', 'stopped')
          AND job_id != ?
          AND ABS(julianday(?) - julianday(started_at)) * 86400.0
              <= ?
        ORDER BY delta_sec
    """, (
        orphan["started_at"], orphan["printer_id"], orphan["job_id"],
        orphan["started_at"], PAIR_WINDOW_SECONDS,
    ))
    return [dict(r) for r in cur.fetchall()]


def classify(conn: sqlite3.Connection,
             orphans: List[dict]) -> List[dict]:
    """Annotate each orphan with a sub-case + canonical pair (if any)."""
    classified: List[dict] = []
    for orphan in orphans:
        candidates = find_pair_candidates(conn, orphan)
        entry = dict(orphan)
        entry["candidates"] = candidates
        if not candidates:
            entry["sub_case"] = CASE_SOLO
            entry["canonical_pair"] = None
        elif len(candidates) == 1:
            entry["sub_case"] = CASE_PAIRED
            entry["canonical_pair"] = candidates[0]
        else:
            entry["sub_case"] = CASE_ANOMALY
            entry["canonical_pair"] = None
        classified.append(entry)
    return classified


# ----------------------------------------------------------------------
# Triage output
# ----------------------------------------------------------------------

_CASE_LABEL = {
    CASE_PAIRED: "paired   -> stop + canonical pair note",
    CASE_SOLO: "solo     -> stop + audit-#22 note",
    CASE_ANOMALY: "anomaly  -> REFUSES to act (multi-candidate pair)",
}


def print_triage(db_path: str, classified: List[dict]) -> None:
    print("=" * 70)
    print("Migration {}: {}".format(MIGRATION_ID, DESCRIPTION))
    print("=" * 70)
    print("DB: {}".format(db_path))
    print()

    if not classified:
        print("No stale orphan rows found "
              "(status='started' AND >24h old). Nothing to triage.")
        print()
        print("Dry run complete. No writes performed.")
        return

    print("Stale orphan rows: {}".format(len(classified)))
    print()
    print("{:>6}  {:<14}  {:<8}  {:<25}  {}".format(
        "jid", "printer", "case", "file_name", "planned action"
    ))
    print("-" * 110)
    for entry in classified:
        fn = (entry.get("file_name") or "")[:25]
        action = _CASE_LABEL[entry["sub_case"]]
        if entry["sub_case"] == CASE_PAIRED:
            action += " (canonical job_id={})".format(
                entry["canonical_pair"]["job_id"]
            )
        elif entry["sub_case"] == CASE_ANOMALY:
            action += " (candidates: {})".format(
                [c["job_id"] for c in entry["candidates"]]
            )
        print("{:>6}  {:<14}  {:<8}  {:<25}  {}".format(
            entry["job_id"], entry["printer_id"][:14],
            entry["sub_case"], fn, action,
        ))

    counts: Dict[str, int] = {
        CASE_PAIRED: 0, CASE_SOLO: 0, CASE_ANOMALY: 0,
    }
    for entry in classified:
        counts[entry["sub_case"]] += 1

    print()
    print("Counts by sub-case:")
    for case in (CASE_PAIRED, CASE_SOLO, CASE_ANOMALY):
        print("  {:<46} {}".format(_CASE_LABEL[case], counts[case]))

    if counts[CASE_ANOMALY]:
        print()
        print("NOTE: anomaly rows will be left untouched. Inspect "
              "them manually after the apply.")

    print()
    print("Dry run complete. No writes performed.")


# ----------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------

def _apply_writes(conn: sqlite3.Connection, classified: List[dict],
                  runner: MigrationRunner) -> Dict[str, object]:
    """Perform the orphan updates inside an open transaction.

    Caller has already issued BEGIN; this function does NOT commit.
    Returns a summary dict for the apply-summary printer.
    """
    now = datetime.now(timezone.utc).isoformat()
    summary: Dict[str, object] = {
        "paired_job_ids": [],
        "solo_job_ids": [],
        "anomaly_job_ids": [],
    }

    for entry in classified:
        case = entry["sub_case"]
        jid = entry["job_id"]
        if case == CASE_PAIRED:
            canonical_id = entry["canonical_pair"]["job_id"]
            note = PAIRED_NOTE_FMT.format(canonical_id=canonical_id)
            conn.execute(
                "UPDATE print_jobs "
                "SET status = 'stopped', completed_at = ?, notes = ? "
                "WHERE job_id = ?",
                (now, note, jid),
            )
            summary["paired_job_ids"].append(jid)
        elif case == CASE_SOLO:
            conn.execute(
                "UPDATE print_jobs "
                "SET status = 'stopped', completed_at = ?, notes = ? "
                "WHERE job_id = ?",
                (now, SOLO_NOTE, jid),
            )
            summary["solo_job_ids"].append(jid)
        else:  # CASE_ANOMALY
            summary["anomaly_job_ids"].append(jid)

    runner.record(MIGRATION_ID, DESCRIPTION, conn)
    return summary


def apply_migration(db_path: str, classified: List[dict],
                    runner: MigrationRunner,
                    backup_path: Optional[str]) -> int:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN")
        try:
            summary = _apply_writes(conn, classified, runner)
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

    print_apply_summary(db_path, backup_path, summary)
    return EXIT_OK


def print_apply_summary(db_path: str, backup_path: Optional[str],
                        summary: Dict[str, object]) -> None:
    paired = summary.get("paired_job_ids") or []
    solo = summary.get("solo_job_ids") or []
    anomaly = summary.get("anomaly_job_ids") or []
    total = len(paired) + len(solo)

    print()
    print("Migration 004 — FAT32 orphan reconciliation")
    print("  DB: {}".format(db_path))
    if backup_path:
        print("  Backup: {}".format(backup_path))
    print("  Paired orphans reconciled: {} (job_ids: {})".format(
        len(paired), paired or "[]"
    ))
    print("  Solo orphans reconciled:   {} (job_ids: {})".format(
        len(solo), solo or "[]"
    ))
    print("  Anomalies skipped:         {} (job_ids: {})".format(
        len(anomaly), anomaly or "[]"
    ))
    print("  Total touched:             {}".format(total))


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------

def _build_parser() -> _Parser:
    parser = _Parser(description=DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Preview the reconciliation without touching "
                           "the DB (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Apply the reconciliation after creating a "
                           "backup. Refuses to run if the print-farm "
                           "service is up.")
    parser.add_argument("--db", "--db-path", dest="db_path",
                        default=DEFAULT_DB_PATH,
                        help="Path to production_log.db "
                             "(default: {}).".format(DEFAULT_DB_PATH))
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

    # Read-only classification first (works for both dry-run and
    # apply; apply re-reads inside the transaction is unnecessary
    # because we're the only writer once the service is stopped).
    ro_conn = sqlite3.connect("file:{}?mode=ro".format(db_path),
                              uri=True)
    ro_conn.row_factory = sqlite3.Row
    try:
        orphans = fetch_stale_orphans(ro_conn)
        classified = classify(ro_conn, orphans)
    finally:
        ro_conn.close()

    if not args.apply:
        print_triage(db_path, classified)
        return EXIT_OK

    if is_service_running():
        print("ERROR: print-farm-monitor appears to be running "
              "(port 5001 is bound).", file=sys.stderr)
        print("Stop the service first:", file=sys.stderr)
        print("  sudo systemctl stop print-farm-monitor",
              file=sys.stderr)
        return EXIT_SERVICE_RUNNING

    backup_path: Optional[str] = None
    if classified:
        try:
            backup_path = create_backup(db_path)
        except (OSError, FileExistsError) as exc:
            print("ERROR: backup failed: {}".format(exc),
                  file=sys.stderr)
            print("DB has not been modified.", file=sys.stderr)
            return EXIT_BACKUP_FAILED
        print("Backup created: {}".format(backup_path))

    return apply_migration(db_path, classified, runner, backup_path)


if __name__ == "__main__":
    sys.exit(main())
