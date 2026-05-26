"""FAT32 short-filename duplicate-row dedup (Phase 6).

USB-stick prints with long filenames trigger Prusa firmware to report
the file inconsistently between API paths: sometimes long-form
(``xl_2__...Tile_Holder_5in_0.4n_0.2mm_PLA_XL_2h22m.bgcode``),
sometimes the FAT32 8.3 truncated form (``XL_2__~2.BGC``). The existing
filename-keyed 120s dedup in
``PrintJobRepository.create_job`` misses on the long-vs-short flip and
two ``print_jobs`` rows are inserted ~10-255s apart. See audit #20.

This file specifies the contract for the fix (state-based dedup
layered after the existing branch), the specific FAT32 witnesses
observed on the Pi DB, the legit cases the fix must NOT collapse,
and one regression test locking in the existing filename branch so
the new layered branch can't bypass or break it.

Test layout
-----------
- ``ContractAssertions``   — invariants the fix MUST preserve
                             regardless of which code path it takes.
- ``WitnessAssertions``    — specific FAT32 manifestations observed in
                             the Pi DB; documentation of the bug timing.
                             A different fix path could legitimately
                             invalidate these without invalidating the
                             contract.
- ``NegativeAssertions``   — sequences the fix must NOT collapse.
- ``ExistingBranchRegressionTests`` — locks in the prior 120s
                             filename-keyed dedup so the new layered
                             branch can't bypass or break it. Passes
                             both before and after Change 2.

Clock control
-------------
``create_job`` calls ``datetime.now(timezone.utc).isoformat()`` once
per invocation to stamp ``started_at`` / ``created_at``. We patch
``app.domains.production.job_repository.datetime`` with a Mock whose
``now`` returns scripted timestamps anchored to real wall-clock
``base = datetime.now(timezone.utc)`` so SQLite's own
``datetime('now')`` (used by the existing branch's 120s window
predicate) stays in agreement with the inserted ``started_at`` values.
``complete_job`` / ``stop_job`` / ``fail_job`` also call
``datetime.now``; each call consumes one element from the scripted
list, so the offset tuple must be sized to match the call count.
"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.production.job_repository import PrintJobRepository


# Filenames from pair 65/66 inspected on the Pi DB.
LONG_NAME = (
    "xl_2__8444149a699d__Tile_Holder_5in_0.4n_0.2mm_PLA_XL_2h22m.bgcode"
)
SHORT_NAME = "XL_2__~2.BGC"

# Display names: Prusa firmware exposes the long form as
# file_display_name when it has it; the 8.3 flip exposes only the
# truncated form for both file_name and file_display_name.
LONG_DISPLAY = "Tile Holder 5in (Original Long Name)"


def _all_rows(db_path, printer_id=None):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if printer_id is None:
            cur = conn.execute(
                "SELECT * FROM print_jobs ORDER BY job_id"
            )
        else:
            cur = conn.execute(
                "SELECT * FROM print_jobs "
                "WHERE printer_id = ? ORDER BY job_id",
                (printer_id,),
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _one_row(db_path, printer_id):
    rows = _all_rows(db_path, printer_id=printer_id)
    assert len(rows) == 1, (
        "expected exactly one print_jobs row for {pid}, got {n}: {rows}"
        .format(pid=printer_id, n=len(rows), rows=rows)
    )
    return rows[0]


class _ClockBase(unittest.TestCase):
    """Common setUp + scripted-clock helper for create_job tests."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(
            self.tmpdir.name, "production_log.db"
        )
        self.repo = PrintJobRepository(self.db_path)
        # Anchor scripted timestamps to real wall-clock now so SQLite's
        # datetime('now', '-120 seconds') in the existing branch stays
        # in agreement with the inserted started_at values.
        self.base = datetime.now(timezone.utc)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _scripted_clock(self, *offsets_sec):
        """Patch datetime in job_repository to script .now() returns.

        Each offset is consumed by one ``datetime.now(timezone.utc)``
        call in the production code. ``create_job``, ``complete_job``,
        ``stop_job``, and ``fail_job`` each call ``now()`` once. The
        return value must be a real ``datetime`` so ``.isoformat()``
        works downstream.
        """
        values = [
            self.base + timedelta(seconds=s) for s in offsets_sec
        ]
        fake_dt = mock.Mock()
        fake_dt.now = mock.Mock(side_effect=values)
        return mock.patch(
            "app.domains.production.job_repository.datetime",
            fake_dt,
        )


# ----------------------------------------------------------------------
# Contract — the invariants the fix MUST preserve.
# ----------------------------------------------------------------------


class ContractAssertions(_ClockBase):

    def test_contract_one_physical_print_one_row(self):
        """Two consecutive create_job calls for the same printer with
        different file_name, no intervening complete/fail/stop, result
        in exactly one row in print_jobs."""
        with self._scripted_clock(0, 30):
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=LONG_NAME,
            )
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=SHORT_NAME,
            )
        rows = _all_rows(self.db_path, printer_id="core_one_1")
        self.assertEqual(len(rows), 1)

    def test_contract_dedup_preserves_operator_initials(self):
        """First call recorded initials, second call empty: the
        surviving row keeps the first call's initials."""
        with self._scripted_clock(0, 30):
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=LONG_NAME, operator_initials="JM",
            )
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=SHORT_NAME, operator_initials="",
            )
        row = _one_row(self.db_path, printer_id="core_one_1")
        self.assertEqual(row["operator_initials"], "JM")

    def test_contract_dedup_does_not_overwrite_file_display_name(self):
        """First call recorded a long file_display_name, second call
        passes the truncated 8.3 name: surviving row keeps the long
        display name."""
        with self._scripted_clock(0, 30):
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=LONG_NAME, file_display_name=LONG_DISPLAY,
            )
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=SHORT_NAME, file_display_name=SHORT_NAME,
            )
        row = _one_row(self.db_path, printer_id="core_one_1")
        self.assertEqual(row["file_display_name"], LONG_DISPLAY)
        self.assertEqual(row["file_name"], LONG_NAME)

    def test_contract_dedup_returns_same_job_id(self):
        """Both create_job calls return the same job_id."""
        with self._scripted_clock(0, 30):
            j1 = self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=LONG_NAME,
            )
            j2 = self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=SHORT_NAME,
            )
        self.assertEqual(j1, j2)


# ----------------------------------------------------------------------
# Witness — specific FAT32 manifestations observed in the Pi DB.
# ----------------------------------------------------------------------


class WitnessAssertions(_ClockBase):

    def test_witness_long_to_short_78s_one_row(self):
        """Pair 65/66 timing. T=0 long name, T=78s 8.3 name."""
        with self._scripted_clock(0, 78):
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=LONG_NAME, file_display_name=LONG_DISPLAY,
                operator_initials="JM",
            )
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=SHORT_NAME, file_display_name=SHORT_NAME,
            )
        row = _one_row(self.db_path, printer_id="core_one_1")
        self.assertEqual(row["file_name"], LONG_NAME)
        self.assertEqual(row["file_display_name"], LONG_DISPLAY)
        self.assertEqual(row["operator_initials"], "JM")

    def test_witness_short_to_long_one_row(self):
        """Reversed order: 8.3 name first, then long name. First insert
        wins on metadata; only operator_initials is back-filled."""
        with self._scripted_clock(0, 78):
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=SHORT_NAME, file_display_name=SHORT_NAME,
            )
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name=LONG_NAME, file_display_name=LONG_DISPLAY,
                operator_initials="JM",
            )
        row = _one_row(self.db_path, printer_id="core_one_1")
        self.assertEqual(row["file_name"], SHORT_NAME)
        self.assertEqual(row["file_display_name"], SHORT_NAME)
        self.assertEqual(row["operator_initials"], "JM")

    def test_witness_long_run_flip_at_255s(self):
        """Pair 21/22 timing — 255s gap, beyond the old 120s window.
        State-based dedup must still collapse to one row."""
        with self._scripted_clock(0, 255):
            self.repo.create_job(
                printer_id="core_one_2", printer_name="Core One 2",
                file_name=LONG_NAME, file_display_name=LONG_DISPLAY,
            )
            self.repo.create_job(
                printer_id="core_one_2", printer_name="Core One 2",
                file_name=SHORT_NAME, file_display_name=SHORT_NAME,
            )
        row = _one_row(self.db_path, printer_id="core_one_2")
        self.assertEqual(row["file_name"], LONG_NAME)


# ----------------------------------------------------------------------
# Negative — legit sequences the fix must NOT collapse.
# ----------------------------------------------------------------------


class NegativeAssertions(_ClockBase):

    def test_legit_consecutive_prints_two_rows(self):
        """print A → complete_job → print B. Two rows."""
        # 3 datetime.now() calls: create_job, complete_job, create_job.
        with self._scripted_clock(0, 300, 600):
            j1 = self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name="A.gcode",
            )
            self.repo.complete_job(j1, duration_sec=300)
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name="B.gcode",
            )
        rows = _all_rows(self.db_path, printer_id="core_one_1")
        self.assertEqual(len(rows), 2)
        statuses = sorted(r["status"] for r in rows)
        self.assertEqual(statuses, ["completed", "started"])

    def test_legit_stop_then_reprint_two_rows(self):
        """print A → stop_job → print B (different filename). Two rows."""
        with self._scripted_clock(0, 120, 600):
            j1 = self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name="A.gcode",
            )
            self.repo.stop_job(j1, duration_sec=120)
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name="B.gcode",
            )
        rows = _all_rows(self.db_path, printer_id="core_one_1")
        self.assertEqual(len(rows), 2)
        statuses = sorted(r["status"] for r in rows)
        self.assertEqual(statuses, ["started", "stopped"])

    def test_legit_failed_then_reprint_two_rows(self):
        """print A → fail_job → print B. Two rows."""
        with self._scripted_clock(0, 90, 600):
            j1 = self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name="A.gcode",
            )
            self.repo.fail_job(j1, duration_sec=90)
            self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name="B.gcode",
            )
        rows = _all_rows(self.db_path, printer_id="core_one_1")
        self.assertEqual(len(rows), 2)
        statuses = sorted(r["status"] for r in rows)
        self.assertEqual(statuses, ["failed", "started"])

    def test_legit_stale_orphan_does_not_absorb_new_print(self):
        """A status='started' row from >24h ago must NOT be deduped
        against. Regression guard for the 24h upper bound on the new
        state-based dedup. Without the bound, a ghost orphan from
        some other (non-FAT32) source would silently swallow a new
        print's metadata.
        """
        stale_iso = (
            datetime.now(timezone.utc) - timedelta(hours=25)
        ).isoformat()
        # Seed directly via INSERT, bypassing create_job, so we can
        # control started_at independently of the wall clock.
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO print_jobs "
                "(printer_id, printer_name, file_name, "
                " file_display_name, status, started_at, created_at, "
                " operator_initials) "
                "VALUES (?, ?, ?, ?, 'started', ?, ?, ?)",
                (
                    "core_one_1", "Core One 1",
                    "stale_orphan.gcode", "stale_orphan.gcode",
                    stale_iso, stale_iso, "ZZ",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # No clock mocking — created_at on the new row is just
        # real-now; doesn't affect the dedup decision.
        self.repo.create_job(
            printer_id="core_one_1", printer_name="Core One 1",
            file_name="fresh_print.gcode",
            file_display_name="fresh_print.gcode",
            operator_initials="JM",
        )

        rows = _all_rows(self.db_path, printer_id="core_one_1")
        self.assertEqual(len(rows), 2)
        # 25h-old row preserved verbatim — file_name, status, initials.
        stale_row = next(
            r for r in rows if r["file_name"] == "stale_orphan.gcode"
        )
        self.assertEqual(stale_row["status"], "started")
        self.assertEqual(stale_row["operator_initials"], "ZZ")
        self.assertEqual(stale_row["started_at"], stale_iso)
        # New row inserted with its own identity.
        fresh_row = next(
            r for r in rows if r["file_name"] == "fresh_print.gcode"
        )
        self.assertEqual(fresh_row["status"], "started")
        self.assertEqual(fresh_row["operator_initials"], "JM")

    def test_dedup_applies_to_started_row_within_24h(self):
        """Symmetric positive case for the 24h bound. A status='started'
        row from 23h ago must be deduped against — the new state-based
        branch picks it up because it's inside the 24h window.
        Surviving row is the original 23h-old one.
        """
        recent_iso = (
            datetime.now(timezone.utc) - timedelta(hours=23)
        ).isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "INSERT INTO print_jobs "
                "(printer_id, printer_name, file_name, "
                " file_display_name, status, started_at, created_at, "
                " operator_initials) "
                "VALUES (?, ?, ?, ?, 'started', ?, ?, ?)",
                (
                    "core_one_1", "Core One 1",
                    "in_flight_long.gcode", "in_flight_long.gcode",
                    recent_iso, recent_iso, None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        self.repo.create_job(
            printer_id="core_one_1", printer_name="Core One 1",
            file_name="IN_FLIG~1.GCO",
            file_display_name="IN_FLIG~1.GCO",
            operator_initials="JM",
        )

        rows = _all_rows(self.db_path, printer_id="core_one_1")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Surviving row IS the seeded 23h-old row.
        self.assertEqual(row["file_name"], "in_flight_long.gcode")
        self.assertEqual(row["started_at"], recent_iso)
        # Initials back-filled from the new call (seed had NULL).
        self.assertEqual(row["operator_initials"], "JM")


# ----------------------------------------------------------------------
# Existing-branch regression — lock in the prior 120s filename dedup.
# ----------------------------------------------------------------------


class ExistingBranchRegressionTests(_ClockBase):

    def test_existing_filename_dedup_within_120s_preserved(self):
        """Two create_job calls with the same file_name and same
        printer_id, 30s apart, no intervening close. Existing
        filename-keyed branch must still collapse them to one row
        and back-fill ``operator_initials`` (NULL on first →
        populated on second).

        Locks the existing branch in place so the new layered branch
        in Change 2 can't accidentally bypass or break it.
        """
        with self._scripted_clock(0, 30):
            j1 = self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name="same.gcode", operator_initials=None,
            )
            j2 = self.repo.create_job(
                printer_id="core_one_1", printer_name="Core One 1",
                file_name="same.gcode", operator_initials="JM",
            )
        self.assertEqual(j1, j2)
        row = _one_row(self.db_path, printer_id="core_one_1")
        self.assertEqual(row["operator_initials"], "JM")


if __name__ == "__main__":
    unittest.main()
