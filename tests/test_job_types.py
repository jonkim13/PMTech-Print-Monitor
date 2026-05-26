"""Phase C — Job Types (Internal / External / Design).

Covers the service + repository changes that introduce a
``job_type`` discriminator and the type-specific columns added by
Migration 005. Routes, frontend, and status_sync derivation changes
land in later Phase C changes — this test module only exercises the
service/repo seam.

Layout
------
- ``JobTypeTests`` — service/repository behavioral tests (10 cases).
- ``InitTablesMirrorTests`` — locks ``JobRepository._init_tables``
  against drift from Migration 005's ``NEW_COLUMNS`` list.
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService
from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository


# Load Migration 005 as a module so the test can read NEW_COLUMNS
# directly. The 005 file is not on the import path under a normal
# package name; load it from its absolute path.
_MIGRATION_005_PATH = os.path.join(
    ROOT_DIR, "scripts", "migrations", "005_add_job_type_columns.py"
)
_spec = importlib.util.spec_from_file_location(
    "_migration_005", _MIGRATION_005_PATH
)
_migration_005 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_migration_005)


def _build_stack(db_path):
    """Initialize sibling repos in the order needed for FKs to resolve."""
    job_repo = JobRepository(db_path)
    QueueExecutionRepository(db_path)
    QueueRepository(db_path)
    wo_repo = WorkOrderRepository(db_path)
    service = WorkOrderService(
        work_order_repository=wo_repo,
        job_repository=job_repo,
    )
    return job_repo, wo_repo, service


def _fetch_job_row(db_path, job_id):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _fetch_queue_ids_for_wo(db_path, wo_id):
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT queue_id FROM queue_items WHERE wo_id = ? "
            "ORDER BY queue_id ASC", (wo_id,)
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


class JobTypeTests(unittest.TestCase):
    """Behavioral tests for the Phase C job-type seam."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "work_orders.db")
        self.job_repo, self.wo_repo, self.service = _build_stack(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    # ------------------------------------------------------------------
    # 1. Regression: Internal create path unchanged
    # ------------------------------------------------------------------

    def test_create_internal_job_unchanged(self):
        """Pure regression: the one-click Internal create path is untouched.

        The job_type='Internal' default is enforced by Migration 005's
        column DEFAULT and locked against drift by the mirror test
        (test_init_tables_mirrors_migration_005_columns). This test
        verifies only what existing callers depend on today: queue_ids
        get assigned to the new job row.
        """
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 2}],
        )
        queue_ids = _fetch_queue_ids_for_wo(self.db_path, wo["wo_id"])
        self.assertEqual(len(queue_ids), 2)

        result = self.service.create_job(wo["wo_id"], queue_ids=queue_ids)
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.get("job_id"))

        # Queue items moved onto the job — existing behavior preserved.
        items = self.job_repo.get_job_queue_items(result["job_id"])
        self.assertEqual({i["queue_id"] for i in items}, set(queue_ids))

    # ------------------------------------------------------------------
    # 2. External requires vendor + external_process
    # ------------------------------------------------------------------

    def test_create_external_job_requires_vendor_and_process(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        wo_id = wo["wo_id"]

        # Both missing.
        with self.assertRaises(ValueError):
            self.service.create_job(wo_id, job_type="External")

        # Vendor only — process missing.
        with self.assertRaises(ValueError):
            self.service.create_job(
                wo_id, job_type="External", vendor="MachiningCo"
            )

        # Process only — vendor missing.
        with self.assertRaises(ValueError):
            self.service.create_job(
                wo_id, job_type="External", external_process="Anodize"
            )

    # ------------------------------------------------------------------
    # 3. External create succeeds and persists type-specific fields
    # ------------------------------------------------------------------

    def test_create_external_job_succeeds(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        result = self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        row = _fetch_job_row(self.db_path, result["job_id"])
        self.assertEqual(row["job_type"], "External")
        self.assertEqual(row["vendor"], "MachiningCo")
        self.assertEqual(row["external_process"], "CNC Mill")
        # Non-External fields stay NULL.
        for col in ("requirements", "designer", "design_completed_at",
                    "approved_by", "date_delivered", "inspection_report",
                    "inspector", "inspection_date"):
            self.assertIsNone(row[col], "External job left {} non-null"
                              .format(col))

    # ------------------------------------------------------------------
    # 4. Design requires designer
    # ------------------------------------------------------------------

    def test_create_design_job_requires_designer(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        with self.assertRaises(ValueError):
            self.service.create_job(wo["wo_id"], job_type="Design")
        with self.assertRaises(ValueError):
            self.service.create_job(
                wo["wo_id"], job_type="Design", designer="   "
            )

    # ------------------------------------------------------------------
    # 5. Design create succeeds and persists type-specific fields
    # ------------------------------------------------------------------

    def test_create_design_job_succeeds(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        result = self.service.create_job(
            wo["wo_id"], job_type="Design",
            designer="Jonathan",
            requirements="Redesign the latch bracket for steel.",
        )
        row = _fetch_job_row(self.db_path, result["job_id"])
        self.assertEqual(row["job_type"], "Design")
        self.assertEqual(row["designer"], "Jonathan")
        self.assertEqual(
            row["requirements"],
            "Redesign the latch bracket for steel.",
        )
        for col in ("vendor", "external_process", "date_delivered",
                    "design_completed_at", "approved_by",
                    "inspection_report", "inspector", "inspection_date"):
            self.assertIsNone(row[col], "Design job left {} non-null"
                              .format(col))

    # ------------------------------------------------------------------
    # 6. Partial update — External fields
    # ------------------------------------------------------------------

    def test_update_external_fields_partial(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        result = self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        job_id = result["job_id"]

        self.job_repo.update_external_job_fields(
            job_id, date_delivered="2026-05-26"
        )

        row = _fetch_job_row(self.db_path, job_id)
        self.assertEqual(row["date_delivered"], "2026-05-26")
        # Untouched fields preserve their original values.
        self.assertEqual(row["vendor"], "MachiningCo")
        self.assertEqual(row["external_process"], "CNC Mill")
        self.assertIsNone(row["inspection_report"])
        self.assertIsNone(row["inspector"])
        self.assertIsNone(row["inspection_date"])

    # ------------------------------------------------------------------
    # 7. Partial update — Design fields
    # ------------------------------------------------------------------

    def test_update_design_fields_partial(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        result = self.service.create_job(
            wo["wo_id"], job_type="Design", designer="Jonathan",
            requirements="Original brief.",
        )
        job_id = result["job_id"]

        self.job_repo.update_design_job_fields(
            job_id, design_completed_at="2026-05-26T12:00:00+00:00"
        )

        row = _fetch_job_row(self.db_path, job_id)
        self.assertEqual(
            row["design_completed_at"], "2026-05-26T12:00:00+00:00"
        )
        # Other design fields preserved.
        self.assertEqual(row["designer"], "Jonathan")
        self.assertEqual(row["requirements"], "Original brief.")
        self.assertIsNone(row["approved_by"])

    # ------------------------------------------------------------------
    # 8. start_non_internal_job rejects Internal jobs
    # ------------------------------------------------------------------

    def test_start_non_internal_job_internal_rejected(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        queue_ids = _fetch_queue_ids_for_wo(self.db_path, wo["wo_id"])
        result = self.service.create_job(wo["wo_id"], queue_ids=queue_ids)

        with self.assertRaises(ValueError):
            self.service.start_non_internal_job(result["job_id"])

    # ------------------------------------------------------------------
    # 9. start_non_internal_job advances External 'open' → 'in_progress'
    # ------------------------------------------------------------------

    def test_start_non_internal_job_external_succeeds(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        result = self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        job_id = result["job_id"]

        row_before = _fetch_job_row(self.db_path, job_id)
        self.assertEqual(row_before["status"], "open")
        self.assertIsNone(row_before["started_at"])

        self.service.start_non_internal_job(job_id)

        row_after = _fetch_job_row(self.db_path, job_id)
        self.assertEqual(row_after["status"], "in_progress")
        self.assertIsNotNone(row_after["started_at"])

    # ------------------------------------------------------------------
    # 10. complete_non_internal_job advances Design → 'completed' and
    #     triggers WO rollup.
    # ------------------------------------------------------------------

    def test_complete_non_internal_job_design_succeeds(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        wo_id = wo["wo_id"]
        result = self.service.create_job(
            wo_id, job_type="Design", designer="Jonathan",
        )
        job_id = result["job_id"]
        self.service.start_non_internal_job(job_id)

        # Mark the single queue_item completed so the WO rollup driven
        # off queue_items has something to roll up to 'completed'. This
        # asserts the service actually ran status_sync — if it didn't,
        # the WO would still be 'open'.
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE queue_items SET status = 'completed' WHERE wo_id = ?",
            (wo_id,),
        )
        conn.commit()
        conn.close()

        wo_before = self.wo_repo.get_work_order(wo_id)
        self.assertEqual(wo_before["status"], "open")

        self.service.complete_non_internal_job(job_id)

        row = _fetch_job_row(self.db_path, job_id)
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["completed_at"])

        wo_after = self.wo_repo.get_work_order(wo_id)
        self.assertEqual(wo_after["status"], "completed")


    # ------------------------------------------------------------------
    # Phase C Change 6 — complete_non_internal_job auto-populates the
    # type-appropriate "actually done" timestamp.
    # ------------------------------------------------------------------

    def test_complete_external_auto_sets_date_delivered(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        ext = self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        job_id = ext["job_id"]
        self.service.start_non_internal_job(job_id)

        row_before = _fetch_job_row(self.db_path, job_id)
        self.assertIsNone(row_before["date_delivered"])

        self.service.complete_non_internal_job(job_id)

        row = _fetch_job_row(self.db_path, job_id)
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(
            row["date_delivered"],
            "date_delivered should auto-populate on complete",
        )

    def test_complete_design_auto_sets_design_completed_at(self):
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        des = self.service.create_job(
            wo["wo_id"], job_type="Design", designer="Jonathan",
        )
        job_id = des["job_id"]
        self.service.start_non_internal_job(job_id)

        row_before = _fetch_job_row(self.db_path, job_id)
        self.assertIsNone(row_before["design_completed_at"])

        self.service.complete_non_internal_job(job_id)

        row = _fetch_job_row(self.db_path, job_id)
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(
            row["design_completed_at"],
            "design_completed_at should auto-populate on complete",
        )

    def test_complete_does_not_overwrite_existing_date_delivered(self):
        """User-set value must survive auto-population on complete."""
        wo = self.wo_repo.create_work_order(
            "Acme",
            [{"part_name": "widget", "material": "PLA", "quantity": 1}],
        )
        ext = self.service.create_job(
            wo["wo_id"], job_type="External",
            vendor="MachiningCo", external_process="CNC Mill",
        )
        job_id = ext["job_id"]
        self.service.start_non_internal_job(job_id)

        preset = "2026-05-20"
        self.service.update_external_job_fields(
            job_id, date_delivered=preset
        )
        self.service.complete_non_internal_job(job_id)

        row = _fetch_job_row(self.db_path, job_id)
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["date_delivered"], preset,
                         "Existing date_delivered must not be overwritten")


class InitTablesMirrorTests(unittest.TestCase):
    """Locks JobRepository._init_tables against drift from Migration 005."""

    def test_init_tables_mirrors_migration_005_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "work_orders.db")
            JobRepository(db_path)

            conn = sqlite3.connect(db_path)
            try:
                rows = conn.execute(
                    "PRAGMA table_info(jobs)"
                ).fetchall()
            finally:
                conn.close()

        # PRAGMA table_info columns:
        #   (cid, name, type, notnull, dflt_value, pk)
        cols_by_name = {row[1]: row for row in rows}

        for col_name, col_def in _migration_005.NEW_COLUMNS:
            self.assertIn(col_name, cols_by_name,
                          "Column {} missing from jobs table — _init_tables "
                          "mirror is out of sync with Migration 005".format(
                              col_name
                          ))
            row = cols_by_name[col_name]
            actual_type = (row[2] or "").upper()
            actual_notnull = bool(row[3])
            actual_default = row[4]

            # Type — first token of the column def.
            expected_type = col_def.split()[0].upper()
            self.assertEqual(
                actual_type, expected_type,
                "Column {} type mismatch (got {}, want {})".format(
                    col_name, actual_type, expected_type
                ),
            )

            # NOT NULL — only job_type carries it per Migration 005.
            expected_notnull = "NOT NULL" in col_def.upper()
            self.assertEqual(
                actual_notnull, expected_notnull,
                "Column {} NOT NULL mismatch (got {}, want {})".format(
                    col_name, actual_notnull, expected_notnull
                ),
            )

            # DEFAULT — only job_type carries one ('Internal'); others NULL.
            if "DEFAULT" in col_def.upper():
                # SQLite stores the literal incl. quotes for TEXT defaults.
                self.assertIsNotNone(
                    actual_default,
                    "Column {} missing DEFAULT".format(col_name),
                )
                self.assertIn(
                    "Internal", str(actual_default),
                    "Column {} default expected 'Internal' (got {!r})".format(
                        col_name, actual_default
                    ),
                )
            else:
                self.assertIsNone(
                    actual_default,
                    "Column {} should have no DEFAULT (got {!r})".format(
                        col_name, actual_default
                    ),
                )


if __name__ == "__main__":
    unittest.main()
