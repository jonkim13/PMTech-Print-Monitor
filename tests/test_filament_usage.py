import os
import sqlite3
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from filament_usage import (
    FILAMENT_SOURCE_API,
    FILAMENT_SOURCE_FILENAME,
    FILAMENT_SOURCE_NONE,
    FILAMENT_SOURCE_MM_ESTIMATE,
    extract_grams_from_filename,
    resolve_total_filament_usage,
)
from app.domains.monitoring.filament_handler import FilamentHandler
from app.domains.monitoring.production_handler import ProductionHandler
from app.domains.monitoring.runtime_state import MonitoringRuntimeState
from app.domains.production.job_repository import PrintJobRepository
from app.domains.production.machine_repository import MachineLogRepository
from app.domains.production.material_repository import MaterialUsageRepository
from upload_sessions_db import UploadSessionDB


class FakeAssignmentDB:
    def __init__(self, assignments=None):
        self.assignments = assignments or {}

    def get_assignment(self, printer_id, tool_index=0):
        return self.assignments.get((printer_id, tool_index))

    def get_printer_assignments(self, printer_id):
        rows = []
        for (pid, tool_index), assignment in self.assignments.items():
            if pid == printer_id:
                row = dict(assignment)
                row["tool_index"] = tool_index
                rows.append(row)
        return rows


class FakeFilamentDB:
    def __init__(self):
        self.deductions = []

    def deduct_weight(self, spool_id, grams_used):
        self.deductions.append((spool_id, grams_used))
        return True

    def get_by_id(self, spool_id):
        return {"id": spool_id, "material": "PLA", "brand": "Prusa"}


class FakeClient:
    def __init__(self, name="Printer 1", model="coreone", details=None):
        self.name = name
        self.model = model
        self._details = details or {}

    def get_job_details(self):
        return dict(self._details)

    def get_camera_snapshot(self):
        return None


def build_filament_handler(assignment_db=None, filament_db=None,
                           job_repository=None):
    runtime_state = MonitoringRuntimeState()
    return FilamentHandler(
        assignment_db=assignment_db,
        filament_db=filament_db,
        job_repository=job_repository,
        runtime_state=runtime_state,
    ), runtime_state


def build_production_handler(assignment_db=None, filament_db=None,
                             job_repository=None,
                             machine_repository=None,
                             material_repository=None):
    runtime_state = MonitoringRuntimeState()
    return ProductionHandler(
        assignment_db=assignment_db,
        filament_db=filament_db,
        job_repository=job_repository,
        machine_repository=machine_repository,
        material_repository=material_repository,
        runtime_state=runtime_state,
    ), runtime_state


class FilenameGramsParserTests(unittest.TestCase):
    def test_extract_grams_examples(self):
        self.assertEqual(
            extract_grams_from_filename(
                "1_pieces_0.4n_12.7979g_PLA_XLIS_1h6m.bgcode"
            ),
            12.7979,
        )
        self.assertEqual(
            extract_grams_from_filename("widget_8g_PLA.gcode"),
            8.0,
        )
        self.assertEqual(
            extract_grams_from_filename("/tmp/part_125.25G_nylon.bgcode"),
            125.25,
        )

    def test_extract_grams_rejects_invalid_tokens(self):
        self.assertIsNone(extract_grams_from_filename("widget_12_PLA.gcode"))
        self.assertIsNone(extract_grams_from_filename("widget_12grams.gcode"))
        self.assertIsNone(extract_grams_from_filename("abc12g_widget.gcode"))

    def test_extract_grams_is_conservative_when_multiple_tokens_exist(self):
        self.assertIsNone(
            extract_grams_from_filename("widget_8g_revision_12g.gcode")
        )

    def test_resolve_total_filament_usage_prefers_api_grams(self):
        usage = resolve_total_filament_usage(
            filament_used_g=14.2,
            filament_used_mm=10000,
            filename_candidates=["widget_8g_PLA.gcode"],
        )
        self.assertEqual(usage["source"], FILAMENT_SOURCE_API)
        self.assertEqual(usage["grams"], 14.2)

    def test_resolve_total_filament_usage_prefers_filename_before_mm(self):
        usage = resolve_total_filament_usage(
            filament_used_g=0,
            filament_used_mm=10000,
            filename_candidates=["widget_8g_PLA.gcode"],
        )
        self.assertEqual(usage["source"], FILAMENT_SOURCE_FILENAME)
        self.assertEqual(usage["grams"], 8.0)
        self.assertEqual(usage["mm_used"], 10000.0)

    def test_resolve_total_filament_usage_returns_none_without_data(self):
        usage = resolve_total_filament_usage(
            filament_used_g=0,
            filament_used_mm=0,
            filename_candidates=["widget_plain.gcode"],
        )
        self.assertEqual(usage["source"], FILAMENT_SOURCE_NONE)
        self.assertEqual(usage["grams"], 0.0)


class FilamentFallbackIntegrationTests(unittest.TestCase):
    def test_auto_deduct_uses_filename_before_mm_estimate(self):
        client = FakeClient(details={
            "file_name": "widget_8g_PLA.gcode",
            "filament_used_g": 0,
            "filament_used_mm": 10000,
        })
        filament_db = FakeFilamentDB()
        assignment_db = FakeAssignmentDB({
            ("printer-1", 0): {"spool_id": "SP001"},
        })
        handler, _runtime_state = build_filament_handler(
            assignment_db=assignment_db,
            filament_db=filament_db,
        )

        handler.auto_deduct_filament("printer-1", {
            "name": "Printer 1",
            "job": {"filename": "widget_8g_PLA.gcode"},
        }, client)

        self.assertEqual(len(filament_db.deductions), 1)
        spool_id, grams_used = filament_db.deductions[0]
        self.assertEqual(spool_id, "SP001")
        self.assertAlmostEqual(grams_used, 8.0)

    def test_auto_deduct_skips_when_no_usage_source_exists(self):
        client = FakeClient(details={
            "file_name": "widget_plain.gcode",
            "filament_used_g": 0,
            "filament_used_mm": 0,
        })
        filament_db = FakeFilamentDB()
        assignment_db = FakeAssignmentDB({
            ("printer-1", 0): {"spool_id": "SP001"},
        })
        handler, _runtime_state = build_filament_handler(
            assignment_db=assignment_db,
            filament_db=filament_db,
        )

        handler.auto_deduct_filament("printer-1", {
            "name": "Printer 1",
            "job": {"filename": "widget_plain.gcode"},
        }, client)

        self.assertEqual(filament_db.deductions, [])

    def test_production_complete_persists_filename_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "production.db")
            job_repository = PrintJobRepository(db_path)
            machine_repository = MachineLogRepository(db_path)
            material_repository = MaterialUsageRepository(db_path)
            job_id = job_repository.create_job(
                printer_id="printer-1",
                printer_name="Printer 1",
                file_name="printer-1__token__widget_8g_PLA.gcode",
                file_display_name="widget_8g_PLA.gcode",
                spool_id="SP001",
            )
            client = FakeClient(details={
                "file_name": "printer-1__token__widget_8g_PLA.gcode",
                "filament_used_g": 0,
                "filament_used_mm": 10000,
            })
            handler, runtime_state = build_production_handler(
                assignment_db=FakeAssignmentDB({
                    ("printer-1", 0): {"spool_id": "SP001"},
                }),
                filament_db=FakeFilamentDB(),
                job_repository=job_repository,
                machine_repository=machine_repository,
                material_repository=material_repository,
            )
            runtime_state.active_job_ids["printer-1"] = job_id

            handler.complete(
                "printer-1",
                client,
                {
                    "name": "Printer 1",
                    "job": {
                        "filename": "printer-1__token__widget_8g_PLA.gcode",
                    },
                },
                duration_sec=321,
            )

            job = job_repository.get_job(job_id)
            usage = material_repository.get_spool_usage("SP001")

        self.assertAlmostEqual(job["filament_used_g"], 8.0)
        self.assertEqual(job["filament_used_source"], FILAMENT_SOURCE_FILENAME)
        self.assertEqual(len(usage), 1)
        self.assertAlmostEqual(usage[0]["grams_used"], 8.0)
        self.assertEqual(usage[0]["usage_source"], FILAMENT_SOURCE_FILENAME)


class MigrationSafetyTests(unittest.TestCase):
    def test_production_repositories_add_source_columns_to_legacy_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "production_legacy.db")
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE print_jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    printer_id TEXT NOT NULL,
                    printer_name TEXT NOT NULL,
                    file_name TEXT,
                    file_display_name TEXT,
                    status TEXT NOT NULL DEFAULT 'started',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    print_duration_sec INTEGER DEFAULT 0,
                    filament_type TEXT,
                    filament_used_g REAL DEFAULT 0,
                    filament_used_mm REAL DEFAULT 0,
                    spool_id TEXT,
                    spool_material TEXT,
                    spool_brand TEXT,
                    layer_height REAL,
                    nozzle_diameter REAL,
                    fill_density REAL,
                    nozzle_temp REAL,
                    bed_temp REAL,
                    operator_initials TEXT,
                    operator TEXT DEFAULT 'unassigned',
                    notes TEXT DEFAULT '',
                    outcome TEXT DEFAULT 'unknown',
                    snapshot_path TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE material_usage (
                    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    spool_id TEXT,
                    job_id INTEGER,
                    printer_id TEXT NOT NULL,
                    grams_used REAL DEFAULT 0,
                    mm_used REAL DEFAULT 0,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES print_jobs(job_id)
                );
                INSERT INTO print_jobs (
                    printer_id, printer_name, file_name, file_display_name,
                    status, started_at, created_at
                ) VALUES (
                    'printer-1', 'Printer 1', 'legacy.gcode',
                    'legacy.gcode', 'started', '2026-04-01T00:00:00',
                    '2026-04-01T00:00:00'
                );
            """)
            conn.commit()
            conn.close()

            PrintJobRepository(db_path)
            MaterialUsageRepository(db_path)
            PrintJobRepository(db_path)
            MaterialUsageRepository(db_path)

            conn = sqlite3.connect(db_path)
            job_cols = [row[1] for row in conn.execute(
                "PRAGMA table_info(print_jobs)"
            ).fetchall()]
            usage_cols = [row[1] for row in conn.execute(
                "PRAGMA table_info(material_usage)"
            ).fetchall()]
            row = conn.execute(
                "SELECT file_name, filament_used_source FROM print_jobs"
            ).fetchone()
            conn.close()

        self.assertIn("filament_used_source", job_cols)
        self.assertIn("usage_source", usage_cols)
        self.assertEqual(row[0], "legacy.gcode")
        self.assertEqual(row[1], "none")

    def test_upload_session_db_adds_parsed_grams_columns_to_legacy_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "upload_legacy.db")
            conn = sqlite3.connect(db_path)
            conn.executescript("""
                CREATE TABLE upload_sessions (
                    upload_session_id TEXT PRIMARY KEY,
                    printer_id TEXT NOT NULL,
                    queue_job_id INTEGER,
                    work_order_job_id INTEGER,
                    original_filename TEXT NOT NULL,
                    staged_path TEXT NOT NULL,
                    remote_filename TEXT NOT NULL,
                    remote_storage TEXT NOT NULL DEFAULT 'usb',
                    file_size_bytes INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    operator_initials TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    last_error TEXT
                );
                INSERT INTO upload_sessions (
                    upload_session_id, printer_id, original_filename,
                    staged_path, remote_filename, remote_storage,
                    file_size_bytes, status, created_at, updated_at
                ) VALUES (
                    'session-1', 'printer-1', 'legacy.gcode',
                    '/tmp/legacy.gcode', 'legacy.gcode', 'usb',
                    123, 'staged', '2026-04-01T00:00:00',
                    '2026-04-01T00:00:00'
                );
            """)
            conn.commit()
            conn.close()

            UploadSessionDB(db_path)
            UploadSessionDB(db_path)

            conn = sqlite3.connect(db_path)
            session_cols = [row[1] for row in conn.execute(
                "PRAGMA table_info(upload_sessions)"
            ).fetchall()]
            row = conn.execute(
                "SELECT original_filename, parsed_grams_source "
                "FROM upload_sessions WHERE upload_session_id = 'session-1'"
            ).fetchone()
            conn.close()

        self.assertIn("parsed_grams", session_cols)
        self.assertIn("parsed_grams_source", session_cols)
        self.assertEqual(row[0], "legacy.gcode")
        self.assertEqual(row[1], "none")


class XLToolAttributionTests(unittest.TestCase):
    """Pre-Phase B Fix 1: XL prints on non-zero extruders must attribute
    filament usage to the correct tool's spool, not hardcoded tool 0."""

    def _build_xl_fixture(self, tmpdir, per_tool_grams, assignments_map):
        db_path = os.path.join(tmpdir, "production.db")
        job_repository = PrintJobRepository(db_path)
        machine_repository = MachineLogRepository(db_path)
        material_repository = MaterialUsageRepository(db_path)
        assignment_db = FakeAssignmentDB(assignments_map)
        filament_db = FakeFilamentDB()

        client = FakeClient(model="xl", details={
            "file_name": "part.bgcode",
            "file_display_name": "part.bgcode",
            "filament_used_g": sum(v for v in per_tool_grams if v > 0) or 0,
            "filament_used_mm": 0,
            "filament_used_g_per_tool": list(per_tool_grams),
            "filament_used_mm_per_tool": [0] * len(per_tool_grams),
        })
        handler, runtime_state = build_production_handler(
            assignment_db=assignment_db,
            filament_db=filament_db,
            job_repository=job_repository,
            machine_repository=machine_repository,
            material_repository=material_repository,
        )
        state = {"name": "XL Printer",
                 "job": {"filename": "part.bgcode"}}
        return (handler, runtime_state, client, state, job_repository,
                material_repository)

    def test_xl_print_on_tool_2_attributes_to_tool_2_spool(self):
        """Tool-2-only XL print must set primary spool to tool 2's spool,
        and material_usage rows must carry tool_index=2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (handler, _rs, client, state, job_repo,
             material_repo) = self._build_xl_fixture(
                tmpdir,
                per_tool_grams=[0, 0, 12.5],  # only tool 2 used
                assignments_map={
                    ("xl-01", 0): {"spool_id": "T0_SPOOL"},
                    ("xl-01", 1): {"spool_id": "T1_SPOOL"},
                    ("xl-01", 2): {"spool_id": "T2_SPOOL"},
                },
            )
            handler.start("xl-01", client, state)
            active_jobs = handler.runtime_state.active_job_ids
            job_id = active_jobs["xl-01"]
            started_job = job_repo.get_job(job_id)

        self.assertEqual(started_job["spool_id"], "T2_SPOOL",
                         "Primary spool should be tool 2's spool, not tool 0's")

    def test_xl_multi_tool_print_defers_primary_spool(self):
        """A true multi-tool print should leave the primary spool unset."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (handler, _rs, client, state, job_repo,
             _mrepo) = self._build_xl_fixture(
                tmpdir,
                per_tool_grams=[5.0, 0, 8.2],  # tools 0 AND 2 active
                assignments_map={
                    ("xl-01", 0): {"spool_id": "T0_SPOOL"},
                    ("xl-01", 2): {"spool_id": "T2_SPOOL"},
                },
            )
            handler.start("xl-01", client, state)
            job_id = handler.runtime_state.active_job_ids["xl-01"]
            started_job = job_repo.get_job(job_id)

        self.assertIsNone(started_job["spool_id"],
                          "Multi-tool print should leave spool_id NULL")

    def test_xl_print_completion_records_correct_tool_index_in_material_usage(self):
        """The per-tool material_usage rows must carry the tool index
        that actually consumed filament (not hardcoded 0)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            (handler, runtime_state, client, state, job_repo,
             material_repo) = self._build_xl_fixture(
                tmpdir,
                per_tool_grams=[0, 0, 12.5],
                assignments_map={
                    ("xl-01", 0): {"spool_id": "T0_SPOOL"},
                    ("xl-01", 2): {"spool_id": "T2_SPOOL"},
                },
            )
            handler.start("xl-01", client, state)
            job_id = runtime_state.active_job_ids["xl-01"]
            handler.complete("xl-01", client, state, duration_sec=100)

            usage = material_repo.get_spool_usage("T2_SPOOL")
            t0_usage = material_repo.get_spool_usage("T0_SPOOL")

        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["tool_index"], 2,
                         "material_usage.tool_index must match actual tool")
        self.assertAlmostEqual(usage[0]["grams_used"], 12.5)
        self.assertEqual(len(t0_usage), 0,
                         "Tool 0 spool should not be charged for a tool-2 print")

    def test_xl_filament_deduction_uses_production_job_spool_on_fallback(self):
        """When per-tool grams are missing, the XL fallback path should
        deduct from the production job's primary spool (set at start to
        the active-tool spool), not hardcoded tool 0."""
        filament_db = FakeFilamentDB()
        assignment_db = FakeAssignmentDB({
            ("xl-01", 0): {"spool_id": "T0_SPOOL"},
            ("xl-01", 2): {"spool_id": "T2_SPOOL"},
        })
        handler, runtime_state = build_filament_handler(
            assignment_db=assignment_db,
            filament_db=filament_db,
        )
        # Simulate an active production job where the active tool is T2
        handler.job_repository = _FakeJobRepo({
            "xl-01": {"spool_id": "T2_SPOOL", "file_name": "part.bgcode"}
        })
        # Per-tool grams empty — only total filename-derived grams known
        client = FakeClient(model="xl", details={
            "file_name": "widget_8g_PLA.gcode",
            "file_display_name": "widget_8g_PLA.gcode",
            "filament_used_g": 0,
            "filament_used_mm": 0,
            "filament_used_g_per_tool": [],
            "filament_used_mm_per_tool": [],
        })
        handler.auto_deduct_filament("xl-01", {
            "name": "XL Printer",
            "job": {"filename": "widget_8g_PLA.gcode"},
        }, client)

        self.assertEqual(len(filament_db.deductions), 1)
        spool_id, grams = filament_db.deductions[0]
        self.assertEqual(spool_id, "T2_SPOOL",
                         "XL fallback must deduct from active-tool spool")
        self.assertAlmostEqual(grams, 8.0)


class _FakeJobRepo:
    """Minimal job repository stub for filament handler fallback tests."""

    def __init__(self, by_printer):
        self._by_printer = by_printer

    def get_active_job(self, printer_id):
        return self._by_printer.get(printer_id)

    def get_job(self, job_id):
        return None


class ProductionJobDedupTests(unittest.TestCase):
    """Pre-Phase B Fix 2: create_job dedup window must be short enough
    to allow legitimate same-file re-prints to create new rows."""

    def test_create_job_reuses_row_within_poll_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "production.db")
            repo = PrintJobRepository(db_path)
            first = repo.create_job(
                printer_id="mk4-01", printer_name="MK4 1",
                file_name="widget.gcode",
            )
            second = repo.create_job(
                printer_id="mk4-01", printer_name="MK4 1",
                file_name="widget.gcode",
            )
            self.assertEqual(first, second,
                             "Back-to-back calls within 120s must collapse")

    def test_create_job_allows_reprint_after_window(self):
        """If the prior 'started' row is older than 120s, a new insert
        must happen — same-day re-prints should no longer be collapsed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "production.db")
            repo = PrintJobRepository(db_path)
            first = repo.create_job(
                printer_id="mk4-01", printer_name="MK4 1",
                file_name="widget.gcode",
            )
            # Rewrite started_at to simulate a row from 5 minutes ago.
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE print_jobs SET started_at = "
                "datetime('now', '-5 minutes') WHERE job_id = ?",
                (first,),
            )
            conn.commit()
            conn.close()

            second = repo.create_job(
                printer_id="mk4-01", printer_name="MK4 1",
                file_name="widget.gcode",
            )
            self.assertNotEqual(first, second,
                                "Re-print after 120s must create a new row")

    def test_create_job_does_not_reuse_completed_job(self):
        """A completed/failed row must never be reused as an 'active' match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "production.db")
            repo = PrintJobRepository(db_path)
            first = repo.create_job(
                printer_id="mk4-01", printer_name="MK4 1",
                file_name="widget.gcode",
            )
            repo.complete_job(first, duration_sec=100, filament_used_g=8)

            second = repo.create_job(
                printer_id="mk4-01", printer_name="MK4 1",
                file_name="widget.gcode",
            )
            self.assertNotEqual(first, second,
                                "Completed row must not block a new print")


if __name__ == "__main__":
    unittest.main()
