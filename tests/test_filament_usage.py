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
from production_db import ProductionDB
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
                           production_db=None):
    runtime_state = MonitoringRuntimeState()
    return FilamentHandler(
        assignment_db=assignment_db,
        filament_db=filament_db,
        production_db=production_db,
        runtime_state=runtime_state,
    ), runtime_state


def build_production_handler(assignment_db=None, filament_db=None,
                             production_db=None):
    runtime_state = MonitoringRuntimeState()
    return ProductionHandler(
        assignment_db=assignment_db,
        filament_db=filament_db,
        production_db=production_db,
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
            db = ProductionDB(os.path.join(tmpdir, "production.db"))
            job_id = db.create_job(
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
                production_db=db,
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

            job = db.get_job(job_id)
            usage = db.get_spool_usage("SP001")

        self.assertAlmostEqual(job["filament_used_g"], 8.0)
        self.assertEqual(job["filament_used_source"], FILAMENT_SOURCE_FILENAME)
        self.assertEqual(len(usage), 1)
        self.assertAlmostEqual(usage[0]["grams_used"], 8.0)
        self.assertEqual(usage[0]["usage_source"], FILAMENT_SOURCE_FILENAME)


class MigrationSafetyTests(unittest.TestCase):
    def test_production_db_adds_source_columns_to_legacy_schema(self):
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

            ProductionDB(db_path)
            ProductionDB(db_path)

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


if __name__ == "__main__":
    unittest.main()
