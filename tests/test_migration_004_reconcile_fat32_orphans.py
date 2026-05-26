"""Migration 004 — FAT32 orphan reconciliation.

Tests against a tempdir fixture DB. The schema is bootstrapped by
``PrintJobRepository._init_db``; rows are seeded via direct INSERT so
``started_at`` can be controlled independently of wall-clock now.

Cases
-----
- ``test_paired_orphan_reconciled``        orphan + completion within
                                            300s → ``'stopped'`` with
                                            paired note pointing at the
                                            canonical job_id.
- ``test_solo_orphan_reconciled``          orphan, no pair → ``'stopped'``
                                            with solo (audit #22) note.
- ``test_recent_orphan_not_touched``       1h-old orphan → still
                                            ``'started'`` (24h guard).
- ``test_anomaly_two_pairs_not_touched``   orphan + two completions
                                            within 300s → orphan still
                                            ``'started'``; both
                                            completions untouched.
- ``test_idempotent_no_op_on_rerun``       second run hits the
                                            ``MigrationRunner.is_applied``
                                            short-circuit and exits 0.
- ``test_idempotent_even_if_record_missing``
                                            simulates the data predicate
                                            as the lone idempotency
                                            layer: drop the
                                            ``schema_version`` row after
                                            a successful apply, re-run,
                                            assert no further mutation
                                            (orphans are now ``'stopped'``
                                            so the orphan SELECT returns
                                            empty).
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.production.job_repository import PrintJobRepository


MIGRATION_SCRIPT = os.path.join(
    ROOT_DIR, "scripts", "migrations",
    "004_reconcile_fat32_orphans.py",
)
MIGRATION_ID = "004_reconcile_fat32_orphans"


def _hours_ago(hours):
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).isoformat()


def _seconds_offset(iso_ts, delta_seconds):
    base = datetime.fromisoformat(iso_ts)
    return (base + timedelta(seconds=delta_seconds)).isoformat()


def _insert_job(conn, *, printer_id="core_one_1",
                printer_name="Core One 1", file_name="x.gcode",
                file_display_name=None, status="started",
                started_at=None, completed_at=None,
                operator_initials=None, notes=""):
    conn.execute(
        "INSERT INTO print_jobs "
        "(printer_id, printer_name, file_name, file_display_name, "
        " status, started_at, completed_at, created_at, "
        " operator_initials, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            printer_id, printer_name, file_name,
            file_display_name or file_name, status,
            started_at, completed_at, started_at,
            operator_initials, notes,
        ),
    )
    return conn.execute(
        "SELECT job_id FROM print_jobs ORDER BY job_id DESC LIMIT 1"
    ).fetchone()[0]


def _row(db_path, job_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM print_jobs WHERE job_id = ?", (job_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _service_port_bound():
    """Skip --apply tests if port 5001 is bound (the migration refuses
    to run while the service is up). Mirrors Migration003Tests."""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        return sock.connect_ex(("127.0.0.1", 5001)) == 0
    finally:
        sock.close()


class Migration004Tests(unittest.TestCase):

    def setUp(self):
        if _service_port_bound():
            self.skipTest(
                "Port 5001 is bound; migration --apply refuses to run."
            )
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(
            self.tmpdir.name, "production_log.db"
        )
        # Bootstrap the schema via the real repository so columns +
        # indices match what the migration sees in production.
        PrintJobRepository(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _run_migration(self, *extra):
        return subprocess.run(
            [sys.executable, MIGRATION_SCRIPT, "--apply",
             "--db", self.db_path, *extra],
            capture_output=True, text=True,
        )

    # ------------------------------------------------------------------
    # Required cases
    # ------------------------------------------------------------------

    def test_paired_orphan_reconciled(self):
        """One status='started' >24h old + matching completion <300s
        away. Orphan → 'stopped' with paired note pointing at the
        canonical pair. Completion row untouched.
        """
        orphan_started = _hours_ago(48)
        # Canonical pair started 80s after the orphan, completed
        # shortly thereafter — simulates the FAT32 long/short flip
        # pair 65/66 timing.
        canonical_started = _seconds_offset(orphan_started, 80)
        canonical_completed_at = _seconds_offset(
            canonical_started, 8696
        )

        conn = self._conn()
        try:
            orphan_id = _insert_job(
                conn, printer_id="core_one_1",
                file_name="long_form.gcode",
                file_display_name="Long Form Name",
                status="started", started_at=orphan_started,
                operator_initials="JM",
            )
            canonical_id = _insert_job(
                conn, printer_id="core_one_1",
                file_name="LONG_F~1.GCO",
                status="completed",
                started_at=canonical_started,
                completed_at=canonical_completed_at,
                operator_initials="JM",
                notes="(canonical, pre-migration)",
            )
            conn.commit()
        finally:
            conn.close()

        result = self._run_migration()
        self.assertEqual(
            result.returncode, 0,
            "stderr:\n{}\nstdout:\n{}".format(
                result.stderr, result.stdout
            ),
        )
        self.assertIn("Paired orphans reconciled: 1", result.stdout)
        self.assertIn("Solo orphans reconciled:   0", result.stdout)
        self.assertIn("Anomalies skipped:         0", result.stdout)

        orphan = _row(self.db_path, orphan_id)
        self.assertEqual(orphan["status"], "stopped")
        self.assertIsNotNone(orphan["completed_at"])
        self.assertIn(
            "Canonical job_id={}".format(canonical_id),
            orphan["notes"],
        )
        self.assertIn("FAT32 duplicate", orphan["notes"])
        # Long-form metadata preserved on the orphan row (the migration
        # only flips status / completed_at / notes).
        self.assertEqual(orphan["file_name"], "long_form.gcode")
        self.assertEqual(orphan["operator_initials"], "JM")

        canonical = _row(self.db_path, canonical_id)
        self.assertEqual(canonical["status"], "completed")
        self.assertEqual(
            canonical["completed_at"], canonical_completed_at
        )
        self.assertEqual(
            canonical["notes"], "(canonical, pre-migration)"
        )

    def test_solo_orphan_reconciled(self):
        """status='started' >24h old, no completion within 300s.
        Orphan → 'stopped' with the audit-#22 solo note.
        """
        orphan_started = _hours_ago(72)
        conn = self._conn()
        try:
            orphan_id = _insert_job(
                conn, printer_id="core_one_2",
                file_name="solo_orphan.gcode",
                status="started", started_at=orphan_started,
                operator_initials=None,
            )
            conn.commit()
        finally:
            conn.close()

        result = self._run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Paired orphans reconciled: 0", result.stdout)
        self.assertIn("Solo orphans reconciled:   1", result.stdout)

        orphan = _row(self.db_path, orphan_id)
        self.assertEqual(orphan["status"], "stopped")
        self.assertIsNotNone(orphan["completed_at"])
        self.assertIn("Stale orphan reconciled", orphan["notes"])
        self.assertIn("audit #22", orphan["notes"])
        # File name + operator preserved.
        self.assertEqual(orphan["file_name"], "solo_orphan.gcode")

    def test_recent_orphan_not_touched(self):
        """status='started' from 1h ago is INSIDE the 24h guard and
        must not be reconciled — could be an in-flight print.
        """
        in_flight_started = _hours_ago(1)
        conn = self._conn()
        try:
            recent_id = _insert_job(
                conn, printer_id="core_one_3",
                file_name="in_flight.gcode",
                status="started", started_at=in_flight_started,
            )
            conn.commit()
        finally:
            conn.close()

        result = self._run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Total touched:             0", result.stdout)

        recent = _row(self.db_path, recent_id)
        self.assertEqual(recent["status"], "started")
        self.assertIsNone(recent["completed_at"])

    def test_anomaly_two_pairs_not_touched(self):
        """One orphan + two completion rows within 300s. Ambiguous
        canonical pair → migration refuses to act on the orphan.
        Both completion rows must also be untouched.
        """
        orphan_started = _hours_ago(30)
        first_pair_started = _seconds_offset(orphan_started, 60)
        second_pair_started = _seconds_offset(orphan_started, 200)

        conn = self._conn()
        try:
            orphan_id = _insert_job(
                conn, printer_id="core_one_4",
                file_name="ambiguous.gcode",
                status="started", started_at=orphan_started,
            )
            pair_a_id = _insert_job(
                conn, printer_id="core_one_4",
                file_name="AMBIGU~1.GCO",
                status="completed",
                started_at=first_pair_started,
                completed_at=_seconds_offset(first_pair_started, 600),
                notes="pair A",
            )
            pair_b_id = _insert_job(
                conn, printer_id="core_one_4",
                file_name="AMBIGU~2.GCO",
                status="completed",
                started_at=second_pair_started,
                completed_at=_seconds_offset(
                    second_pair_started, 600
                ),
                notes="pair B",
            )
            conn.commit()
        finally:
            conn.close()

        result = self._run_migration()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Anomalies skipped:         1", result.stdout)
        self.assertIn(
            "Paired orphans reconciled: 0", result.stdout
        )
        self.assertIn("Solo orphans reconciled:   0", result.stdout)

        orphan = _row(self.db_path, orphan_id)
        self.assertEqual(orphan["status"], "started")
        self.assertIsNone(orphan["completed_at"])

        pair_a = _row(self.db_path, pair_a_id)
        self.assertEqual(pair_a["status"], "completed")
        self.assertEqual(pair_a["notes"], "pair A")
        pair_b = _row(self.db_path, pair_b_id)
        self.assertEqual(pair_b["status"], "completed")
        self.assertEqual(pair_b["notes"], "pair B")

    def test_idempotent_no_op_on_rerun(self):
        """Layer 1 of idempotence: MigrationRunner.is_applied() should
        short-circuit on the second run.
        """
        # Seed something so the first run actually does work.
        orphan_started = _hours_ago(48)
        conn = self._conn()
        try:
            _insert_job(
                conn, printer_id="core_one_5",
                file_name="solo.gcode",
                status="started", started_at=orphan_started,
            )
            conn.commit()
        finally:
            conn.close()

        first = self._run_migration()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Solo orphans reconciled:   1", first.stdout)

        second = self._run_migration()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already applied", second.stdout.lower())
        # Second run did not print a fresh summary block.
        self.assertNotIn(
            "Solo orphans reconciled:", second.stdout
        )

    def test_idempotent_even_if_record_missing(self):
        """Layer 2 of idempotence: the data predicate (status='started')
        keeps the re-run safe even if the schema_version row is
        manually purged. After a successful first apply, every
        previously-reconciled row is now status='stopped', so the
        orphan SELECT returns empty and the second apply touches
        zero rows.
        """
        orphan_started = _hours_ago(48)
        canonical_started = _seconds_offset(orphan_started, 100)
        conn = self._conn()
        try:
            orphan_id = _insert_job(
                conn, printer_id="core_one_6",
                file_name="paired_then_purged.gcode",
                status="started", started_at=orphan_started,
            )
            canonical_id = _insert_job(
                conn, printer_id="core_one_6",
                file_name="PAIRED~1.GCO",
                status="completed",
                started_at=canonical_started,
                completed_at=_seconds_offset(canonical_started, 500),
            )
            conn.commit()
        finally:
            conn.close()

        first = self._run_migration()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("Paired orphans reconciled: 1", first.stdout)

        # Capture the reconciled state to compare against later.
        orphan_after_first = _row(self.db_path, orphan_id)
        canonical_after_first = _row(self.db_path, canonical_id)
        self.assertEqual(orphan_after_first["status"], "stopped")

        # Surgically remove the schema_version row to simulate the
        # registry layer failing/being bypassed.
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM schema_version WHERE migration_id = ?",
                (MIGRATION_ID,),
            )
            conn.commit()
        finally:
            conn.close()

        # Second apply: the data predicate alone must keep this safe.
        second = self._run_migration()
        self.assertEqual(second.returncode, 0, second.stderr)
        # Apply path did run (registry was missing), but found no
        # orphan rows to touch.
        self.assertIn("Total touched:             0", second.stdout)

        # Orphan + canonical rows unchanged from the post-first state.
        self.assertEqual(
            _row(self.db_path, orphan_id), orphan_after_first
        )
        self.assertEqual(
            _row(self.db_path, canonical_id), canonical_after_first
        )

        # Registry row reinstated by the second successful apply, so a
        # third run can still rely on layer 1.
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM schema_version WHERE migration_id = ?",
                (MIGRATION_ID,),
            ).fetchone()
            self.assertIsNotNone(
                row, "Second apply must re-record the migration row "
                     "so future runs short-circuit on layer 1 again."
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
