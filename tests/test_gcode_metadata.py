"""Tests for the Phase 6 slicer-metadata parser + persistence path.

In-tree parser (gcode-metadata is not on PyPI). Covers:
- Parse happy path on .gcode (single + multi tool)
- Parse error paths: missing file, unsupported extension, garbage bgcode
- Per-tool list shape (JSON-encoded floats)
- ExecutionService.create_and_upload persists parsed metadata to
  the upload_session row, and proceeds when parse fails
- ProductionHandler.start copies parsed meta onto print_jobs with
  source='parsed' and stamps upload_session_id for completion linkage
- ProductionHandler.complete does NOT call get_job_details() once the
  job has a 'parsed' source set at start time
- filament_usage.FILAMENT_SOURCE_PARSED short-circuits
  ProductionMaterialUsage._resolve_total_usage
"""

import json
import os
import shutil
import sqlite3
import struct
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.shared.gcode_metadata import parse_print_metadata
from app.domains.execution.service import ExecutionService
from app.domains.execution.upload_session_repository import (
    UploadSessionRepository,
)
from app.domains.monitoring.filament_handler import FilamentHandler
from app.domains.monitoring.production_handler import ProductionHandler
from app.domains.monitoring.production_materials import (
    ProductionMaterialUsage,
)
from app.domains.monitoring.runtime_state import MonitoringRuntimeState
from app.domains.production.job_repository import PrintJobRepository
from app.domains.production.machine_repository import MachineLogRepository
from app.domains.production.material_repository import MaterialUsageRepository
from filament_usage import FILAMENT_SOURCE_PARSED


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

class ParsePrintMetadataGcodeTests(unittest.TestCase):
    def test_parses_single_tool_gcode_fixture(self):
        path = os.path.join(FIXTURES_DIR, "sample_single_tool.gcode")
        result = parse_print_metadata(path)

        self.assertIsNone(result["parse_error"])
        self.assertAlmostEqual(result["parsed_filament_used_g"], 4.5)
        self.assertAlmostEqual(result["parsed_filament_used_mm"], 1234.56)
        self.assertEqual(result["parsed_filament_type"], "PLA")
        self.assertAlmostEqual(result["parsed_layer_height"], 0.2)
        self.assertAlmostEqual(result["parsed_nozzle_diameter"], 0.4)
        self.assertAlmostEqual(result["parsed_fill_density"], 15.0)
        self.assertAlmostEqual(result["parsed_nozzle_temp"], 215.0)
        self.assertAlmostEqual(result["parsed_bed_temp"], 60.0)
        self.assertIsNotNone(result["parsed_at"])
        self.assertIsNone(result["parsed_filament_used_g_per_tool"])

    def test_parses_multi_tool_per_tool_arrays(self):
        path = os.path.join(FIXTURES_DIR, "sample_multi_tool.gcode")
        result = parse_print_metadata(path)

        self.assertIsNone(result["parse_error"])
        self.assertEqual(
            json.loads(result["parsed_filament_used_g_per_tool"]),
            [5.0, 0.0, 1.5],
        )
        self.assertEqual(
            json.loads(result["parsed_filament_used_mm_per_tool"]),
            [650.0, 0.0, 150.0],
        )

    def test_missing_file_returns_parse_error(self):
        result = parse_print_metadata("/does/not/exist.gcode")
        self.assertEqual(result["parse_error"], "file not found")
        self.assertIsNone(result["parsed_filament_used_g"])

    def test_unsupported_extension_returns_parse_error(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
            fh.write(b"hello")
            path = fh.name
        try:
            result = parse_print_metadata(path)
            self.assertIn("unsupported extension", result["parse_error"])
        finally:
            os.unlink(path)

    def test_gcode_without_metadata_block_sets_parse_error(self):
        with tempfile.NamedTemporaryFile(suffix=".gcode", delete=False) as fh:
            fh.write(b"G28\nG1 X10 Y10\nM84\n")
            path = fh.name
        try:
            result = parse_print_metadata(path)
            self.assertIsNone(result["parsed_filament_used_g"])
            self.assertEqual(
                result["parse_error"],
                "filament_used_g not found in slicer block",
            )
        finally:
            os.unlink(path)


class ParsePrintMetadataBgcodeTests(unittest.TestCase):
    def test_garbage_bgcode_returns_parse_error(self):
        with tempfile.NamedTemporaryFile(suffix=".bgcode", delete=False) as fh:
            fh.write(b"NOTGCDE\x00\x00\x00")
            path = fh.name
        try:
            result = parse_print_metadata(path)
            self.assertIsNotNone(result["parse_error"])
        finally:
            os.unlink(path)

    def test_bgcode_with_uncompressed_slicer_metadata_block(self):
        # Construct a minimal valid bgcode file with a single
        # SlicerMetadata block (type=2, compression=0) carrying our
        # canonical ASCII key=value text.
        with tempfile.NamedTemporaryFile(suffix=".bgcode", delete=False) as fh:
            header = b"GCDE" + struct.pack("<I", 1) + struct.pack("<H", 0)
            payload = (
                b"; filament used [g] = 7.25\n"
                b"; filament_type = PETG\n"
                b"; layer_height = 0.15\n"
            )
            block_header = struct.pack("<HHI", 2, 0, len(payload))
            fh.write(header + block_header + payload)
            path = fh.name
        try:
            result = parse_print_metadata(path)
            self.assertIsNone(result["parse_error"])
            self.assertAlmostEqual(result["parsed_filament_used_g"], 7.25)
            self.assertEqual(result["parsed_filament_type"], "PETG")
            self.assertAlmostEqual(result["parsed_layer_height"], 0.15)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# ExecutionService — parse + persist integration
# ---------------------------------------------------------------------------

class _StagedFileStub:
    """A `.save(path)`-compatible stand-in that drops a real .gcode."""

    def __init__(self, source_path):
        self.source_path = source_path

    def save(self, dest_path):
        shutil.copyfile(self.source_path, dest_path)


class _FakeClient:
    def __init__(self, ok=True):
        self._ok = ok
        self.default_storage = "usb"

    def upload_file(self, *args, **kwargs):
        return {"ok": self._ok, "success": self._ok, "message": "uploaded",
                "http_status": 200, "details": {}}

    def get_transfer_status(self):
        return {"ok": True, "details": {"active": False}, "http_status": 200}

    def file_exists(self, *args, **kwargs):
        return {"ok": True, "details": {"exists": True}, "http_status": 200}


class _FakeFarmManager:
    def __init__(self, client):
        self.client = client

    def get_printer_client(self, printer_id):
        return self.client


class ExecutionServiceParseTests(unittest.TestCase):
    """Phase 6 — parse_print_metadata fires from create_and_upload and
    persists onto the upload_session row."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.uploads_dir = os.path.join(self.tmp, "uploads")
        self.session_db_path = os.path.join(self.tmp, "sessions.db")
        self.session_db = UploadSessionRepository(self.session_db_path)
        self.client = _FakeClient()
        self.service = ExecutionService(
            uploads_dir=self.uploads_dir,
            upload_session_repository=self.session_db,
            farm_manager=_FakeFarmManager(self.client),
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_create_and_upload_persists_parsed_metadata(self):
        fixture = os.path.join(FIXTURES_DIR, "sample_single_tool.gcode")
        uploaded = _StagedFileStub(fixture)

        result = self.service.create_and_upload(
            printer_id="printer-1",
            uploaded_file=uploaded,
            original_filename="sample_single_tool.gcode",
        )
        self.assertTrue(result["ok"])
        upload_session_id = result["upload_session_id"]
        session = self.session_db.get_session(upload_session_id)

        self.assertAlmostEqual(session["parsed_filament_used_g"], 4.5)
        self.assertAlmostEqual(session["parsed_filament_used_mm"], 1234.56)
        self.assertEqual(session["parsed_filament_type"], "PLA")
        self.assertAlmostEqual(session["parsed_layer_height"], 0.2)
        self.assertIsNone(session["parse_error"])
        self.assertIsNotNone(session["parsed_at"])

    def test_create_and_upload_records_parse_error_but_proceeds(self):
        with tempfile.NamedTemporaryFile(
            suffix=".gcode", delete=False, dir=self.tmp
        ) as fh:
            fh.write(b"G28\nG1 X10 Y10\nM84\n")
            unparseable = fh.name
        uploaded = _StagedFileStub(unparseable)

        result = self.service.create_and_upload(
            printer_id="printer-1",
            uploaded_file=uploaded,
            original_filename="plain.gcode",
        )
        self.assertTrue(result["ok"])
        session = self.session_db.get_session(result["upload_session_id"])

        # Upload still succeeded; parse_error recorded for diagnostics.
        self.assertIsNone(session["parsed_filament_used_g"])
        self.assertEqual(
            session["parse_error"],
            "filament_used_g not found in slicer block",
        )


# ---------------------------------------------------------------------------
# ProductionHandler — set_parsed_meta wiring at start, skip API at complete
# ---------------------------------------------------------------------------

class _FakeApiClient:
    """Mimics the PrusaLink client. Tracks whether get_job_details was
    called — Phase 6 fix is to skip it at completion."""

    def __init__(self, name="Printer 1", model="coreone", details=None):
        self.name = name
        self.model = model
        self._details = details or {}
        self.get_job_details_calls = 0

    def get_job_details(self):
        self.get_job_details_calls += 1
        return dict(self._details)

    def get_camera_snapshot(self):
        return None


class _FakeAssignmentDB:
    def __init__(self, assignments=None):
        self.assignments = assignments or {}

    def get_assignment(self, printer_id, tool_index=0):
        return self.assignments.get((printer_id, tool_index))

    def get_printer_assignments(self, printer_id):
        return [
            dict(value, tool_index=tidx)
            for (pid, tidx), value in self.assignments.items()
            if pid == printer_id
        ]


class _FakeFilamentDBStub:
    def __init__(self):
        self.deductions = []

    def deduct_weight(self, spool_id, grams):
        self.deductions.append((spool_id, grams))

    def get_by_id(self, spool_id):
        return {"id": spool_id, "material": "PLA", "brand": "Prusa"}


class ProductionHandlerParsedMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.session_db = UploadSessionRepository(
            os.path.join(self.tmp, "sessions.db")
        )
        self.job_repo = PrintJobRepository(
            os.path.join(self.tmp, "production.db")
        )
        self.machine_repo = MachineLogRepository(
            os.path.join(self.tmp, "production.db")
        )
        self.material_repo = MaterialUsageRepository(
            os.path.join(self.tmp, "production.db")
        )
        self.assignment_db = _FakeAssignmentDB({
            ("printer-1", 0): {"spool_id": "SP001"},
        })
        self.runtime = MonitoringRuntimeState()
        self.handler = ProductionHandler(
            job_repository=self.job_repo,
            machine_repository=self.machine_repo,
            material_repository=self.material_repo,
            filament_db=_FakeFilamentDBStub(),
            assignment_db=self.assignment_db,
            upload_session_db=self.session_db,
            runtime_state=self.runtime,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _seed_upload_session(self, **overrides):
        defaults = {
            "upload_session_id": "sess-abc",
            "printer_id": "printer-1",
            "original_filename": "widget.gcode",
            "staged_path": "/tmp/staged.gcode",
            "remote_filename": "printer-1__abc__widget.gcode",
            "remote_storage": "usb",
            "file_size_bytes": 1024,
            "status": "printing",
        }
        defaults.update(overrides)
        session = self.session_db.create_session(**defaults)
        self.session_db.update_parsed_metadata(
            session["upload_session_id"],
            {
                "parsed_filament_used_g": 9.75,
                "parsed_filament_used_mm": 1000.0,
                "parsed_filament_type": "PETG",
                "parsed_layer_height": 0.2,
                "parsed_nozzle_diameter": 0.4,
                "parsed_at": "2026-05-22T00:00:00+00:00",
                "parse_error": None,
            },
        )
        return defaults["upload_session_id"]

    def _pending_start(self, printer_id, upload_session_id, filename):
        self.runtime.record_pending_print_start(
            printer_id=printer_id,
            upload_session_id=upload_session_id,
            remote_filename=filename,
            original_filename=filename,
            operator_initials="JK",
        )

    def test_start_copies_parsed_meta_onto_print_jobs(self):
        upload_session_id = self._seed_upload_session()
        self._pending_start(
            "printer-1", upload_session_id,
            "printer-1__abc__widget.gcode",
        )
        client = _FakeApiClient(details={
            "filament_used_g": 0,
            "filament_used_mm": 0,
        })
        state = {
            "name": "Printer 1",
            "job": {"filename": "printer-1__abc__widget.gcode"},
        }

        self.handler.start("printer-1", client, state)

        job_id = self.runtime.active_job_ids["printer-1"]
        job = self.job_repo.get_job(job_id)
        self.assertEqual(job["upload_session_id"], upload_session_id)
        self.assertAlmostEqual(job["filament_used_g"], 9.75)
        self.assertAlmostEqual(job["filament_used_mm"], 1000.0)
        self.assertEqual(job["filament_used_source"], FILAMENT_SOURCE_PARSED)
        self.assertEqual(job["filament_type"], "PETG")
        self.assertAlmostEqual(job["layer_height"], 0.2)

    def test_start_skips_when_no_upload_session(self):
        # No pending_start → no upload_session linkage. Job is created
        # from API details only and source stays 'none'.
        client = _FakeApiClient(details={
            "filament_used_g": 0, "filament_used_mm": 0,
        })
        state = {
            "name": "Printer 1",
            "job": {"filename": "usb-job.gcode"},
        }
        self.handler.start("printer-1", client, state)

        job_id = self.runtime.active_job_ids["printer-1"]
        job = self.job_repo.get_job(job_id)
        self.assertIsNone(job["upload_session_id"])
        self.assertEqual(job["filament_used_source"], "none")

    def test_complete_does_not_call_get_job_details_when_parsed(self):
        upload_session_id = self._seed_upload_session()
        self._pending_start(
            "printer-1", upload_session_id,
            "printer-1__abc__widget.gcode",
        )
        client = _FakeApiClient(details={
            "filament_used_g": 0, "filament_used_mm": 0,
        })
        state = {
            "name": "Printer 1",
            "job": {"filename": "printer-1__abc__widget.gcode"},
        }
        self.handler.start("printer-1", client, state)
        start_calls = client.get_job_details_calls

        self.handler.complete("printer-1", client, state, duration_sec=120)

        # Start may legitimately call the API once; complete should not.
        self.assertEqual(client.get_job_details_calls, start_calls,
                         "complete() must not re-read /api/v1/job")
        # And the row reflects the parsed values from start.
        # (job_id captured before complete() popped active_jobs)
        rows = self.job_repo.get_jobs(printer_id="printer-1", status="completed")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["filament_used_g"], 9.75)
        self.assertEqual(rows[0]["filament_used_source"],
                         FILAMENT_SOURCE_PARSED)


# ---------------------------------------------------------------------------
# Resolution chain — 'parsed' wins over everything else
# ---------------------------------------------------------------------------

class ParsedSourcePrecedenceTests(unittest.TestCase):
    """ProductionMaterialUsage._resolve_total_usage short-circuits to
    'parsed' when the production_job row carries that source."""

    def test_parsed_source_short_circuits_resolution(self):
        production_job = {
            "filament_used_g": 9.75,
            "filament_used_mm": 1000.0,
            "filament_used_source": FILAMENT_SOURCE_PARSED,
            "file_name": "widget_8g_PLA.gcode",   # would beat to filename
            "file_display_name": "widget_8g_PLA.gcode",
        }
        usage = ProductionMaterialUsage._resolve_total_usage(
            state_job={"filename": "widget_8g_PLA.gcode"},
            details={},  # post-FINISHED blank
            production_job=production_job,
        )

        self.assertEqual(usage["source"], FILAMENT_SOURCE_PARSED)
        self.assertAlmostEqual(usage["grams"], 9.75)
        self.assertAlmostEqual(usage["mm_used"], 1000.0)

    def test_resolution_uses_job_row_when_api_blank(self):
        # source != 'parsed' but the start-time API stamped grams onto
        # the job row. Completion-time API returns blank — falls back
        # to the row value, labeled 'api'.
        production_job = {
            "filament_used_g": 4.2,
            "filament_used_mm": 800,
            "filament_used_source": "none",
            "file_name": "widget_8g_PLA.gcode",
            "file_display_name": "widget_8g_PLA.gcode",
        }
        usage = ProductionMaterialUsage._resolve_total_usage(
            state_job={"filename": "widget_8g_PLA.gcode"},
            details={},
            production_job=production_job,
        )
        self.assertEqual(usage["source"], "api")
        self.assertAlmostEqual(usage["grams"], 4.2)


class FilamentHandlerParsedSourceTests(unittest.TestCase):
    """auto_deduct_filament reads parsed values off the production_job
    row when source='parsed' — no API call needed."""

    def test_auto_deduct_uses_parsed_source_without_api(self):
        filament_db = _FakeFilamentDBStub()
        assignment_db = _FakeAssignmentDB({
            ("printer-1", 0): {"spool_id": "SP001"},
        })
        runtime = MonitoringRuntimeState()
        runtime.active_job_ids["printer-1"] = 42

        class _StubJobRepo:
            def get_job(self, _job_id):
                return {
                    "job_id": 42,
                    "filament_used_g": 6.6,
                    "filament_used_mm": 750.0,
                    "filament_used_source": FILAMENT_SOURCE_PARSED,
                    "file_name": "x.gcode",
                    "file_display_name": "x.gcode",
                    "upload_session_id": None,
                    "spool_id": "SP001",
                }

            def get_active_job(self, _printer_id):
                return None

        handler = FilamentHandler(
            filament_db=filament_db,
            assignment_db=assignment_db,
            job_repository=_StubJobRepo(),
            runtime_state=runtime,
        )
        client = _FakeApiClient(details={
            # Even if API somehow returned grams here, parsed source
            # on the production_job row wins.
            "filament_used_g": 99.0,
        })

        handler.auto_deduct_filament("printer-1", {
            "name": "Printer 1",
            "job": {"filename": "x.gcode"},
        }, client)

        self.assertEqual(len(filament_db.deductions), 1)
        spool_id, grams = filament_db.deductions[0]
        self.assertEqual(spool_id, "SP001")
        self.assertAlmostEqual(grams, 6.6)


# ---------------------------------------------------------------------------
# Schema migration safety — new columns appear on a legacy upload_sessions
# table without losing data.
# ---------------------------------------------------------------------------

class UploadSessionParsedColumnsMigrationTests(unittest.TestCase):
    def test_new_parsed_columns_added_to_legacy_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "upload_legacy.db")
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE upload_sessions (
                    upload_session_id TEXT PRIMARY KEY,
                    printer_id TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    staged_path TEXT NOT NULL,
                    remote_filename TEXT NOT NULL,
                    remote_storage TEXT NOT NULL DEFAULT 'usb',
                    file_size_bytes INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT INTO upload_sessions (
                    upload_session_id, printer_id, original_filename,
                    staged_path, remote_filename, remote_storage,
                    file_size_bytes, status, created_at, updated_at
                ) VALUES (
                    'legacy-1', 'p1', 'old.gcode',
                    '/tmp/old.gcode', 'old.gcode', 'usb',
                    100, 'staged',
                    '2026-04-01T00:00:00', '2026-04-01T00:00:00'
                );
            """)
            conn.commit()
            conn.close()

            UploadSessionRepository(path)
            UploadSessionRepository(path)  # idempotent

            conn = sqlite3.connect(path)
            cols = [row[1] for row in conn.execute(
                "PRAGMA table_info(upload_sessions)"
            ).fetchall()]
            row = conn.execute(
                "SELECT original_filename, parsed_filament_used_g, "
                "parsed_filament_type, parse_error "
                "FROM upload_sessions WHERE upload_session_id = 'legacy-1'"
            ).fetchone()
            conn.close()

        for col in ("parsed_filament_used_g", "parsed_filament_used_mm",
                    "parsed_filament_used_g_per_tool",
                    "parsed_filament_type", "parsed_at", "parse_error"):
            self.assertIn(col, cols)
        # Legacy row's original data preserved; new columns default NULL.
        self.assertEqual(row[0], "old.gcode")
        self.assertIsNone(row[1])
        self.assertIsNone(row[2])
        self.assertIsNone(row[3])


class PrintJobsUploadSessionLinkMigrationTests(unittest.TestCase):
    def test_upload_session_id_column_added_to_legacy_print_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "production_legacy.db")
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE print_jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    printer_id TEXT NOT NULL,
                    printer_name TEXT NOT NULL,
                    file_name TEXT,
                    status TEXT NOT NULL DEFAULT 'started',
                    started_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            conn.commit()
            conn.close()

            PrintJobRepository(path)

            conn = sqlite3.connect(path)
            cols = [row[1] for row in conn.execute(
                "PRAGMA table_info(print_jobs)"
            ).fetchall()]
            conn.close()
        self.assertIn("upload_session_id", cols)


if __name__ == "__main__":
    unittest.main()
