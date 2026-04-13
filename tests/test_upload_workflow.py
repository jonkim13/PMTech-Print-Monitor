import os
import tempfile
import unittest
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from upload_sessions_db import UploadSessionDB
from upload_workflow import UploadWorkflowService


class UploadedFileStub:
    def __init__(self, payload: bytes):
        self.payload = payload

    def save(self, path):
        with open(path, "wb") as handle:
            handle.write(self.payload)


class FakeClient:
    def __init__(self):
        self.upload_calls = 0
        self.start_calls = 0
        self.transfer_calls = 0
        self.file_checks = 0
        self.file_check_calls = []
        self.upload_args = []
        self.start_args = []
        self.exists_after = 1

    def upload_file(self, local_path, remote_filename, storage="usb"):
        self.upload_calls += 1
        self.upload_args.append((local_path, remote_filename, storage))
        return {
            "ok": True,
            "success": True,
            "message": "uploaded",
            "http_status": 200,
            "details": {
                "local_path": local_path,
                "remote_filename": remote_filename,
                "storage": storage,
            },
        }

    def get_transfer_status(self):
        self.transfer_calls += 1
        return {
            "ok": True,
            "success": True,
            "message": "transfer",
            "http_status": 200,
            "details": {"active": False},
        }

    def file_exists(self, remote_filename, storage="usb",
                    attempt=None, elapsed_sec=None):
        self.file_checks += 1
        self.file_check_calls.append(
            (remote_filename, storage, attempt, elapsed_sec)
        )
        exists = self.file_checks >= self.exists_after
        return {
            "ok": True,
            "success": True,
            "message": "exists",
            "http_status": 200,
            "details": {
                "exists": exists,
                "remote_filename": remote_filename,
                "storage": storage,
                "method": "head",
                "summary": "HEAD 200 file found" if exists else "HEAD 404 not found",
            },
        }

    def start_file_print(self, remote_filename, storage="usb"):
        self.start_calls += 1
        self.start_args.append((remote_filename, storage))
        return {
            "ok": True,
            "success": True,
            "message": "start requested",
            "http_status": 200,
            "details": {
                "remote_filename": remote_filename,
                "storage": storage,
            },
        }


class FakeFarmManager:
    def __init__(self, client):
        self.client = client
        self.pending_calls = []

    def get_printer_client(self, printer_id):
        return self.client

    def record_pending_print_start(self, **kwargs):
        self.pending_calls.append(kwargs)

    def clear_pending_print_start(self, printer_id, upload_session_id=None,
                                  remote_filename=None):
        self.pending_calls = [
            call for call in self.pending_calls
            if call.get("upload_session_id") != upload_session_id
        ]

    def wait_for_print_confirmation(self, printer_id, upload_session_id,
                                    timeout_sec=30):
        return {
            "ok": True,
            "success": True,
            "message": "confirmed",
            "details": {
                "printer_id": printer_id,
                "upload_session_id": upload_session_id,
            },
        }


class UploadWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.upload_db = UploadSessionDB(
            os.path.join(self.tempdir.name, "upload_sessions.db")
        )
        self.client = FakeClient()
        self.farm_manager = FakeFarmManager(self.client)
        self.service = UploadWorkflowService(
            os.path.join(self.tempdir.name, "gcode_uploads"),
            self.upload_db,
            farm_manager=self.farm_manager,
        )
        self.service.verify_timeout_sec = 1
        self.service.verify_poll_sec = 0

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_and_upload_stages_uniquely_and_marks_uploaded(self):
        result = self.service.create_and_upload(
            printer_id="mk4-01",
            uploaded_file=UploadedFileStub(b"G1 X1 Y1\n"),
            original_filename="widget.gcode",
            start_print=False,
        )

        self.assertTrue(result["ok"])
        session = self.upload_db.get_session(result["upload_session_id"])
        self.assertIsNotNone(session)
        self.assertEqual(session["status"], "uploaded")
        self.assertTrue(os.path.exists(session["staged_path"]))
        self.assertIn(os.path.join("gcode_uploads", "mk4-01"),
                      session["staged_path"])
        self.assertIn("widget.gcode", session["remote_filename"])
        self.assertEqual(self.client.upload_calls, 1)
        self.assertEqual(self.client.start_calls, 0)
        self.assertEqual(
            self.client.upload_args[0][2],
            session["remote_storage"],
        )
        self.assertEqual(
            self.client.file_check_calls[0][0],
            session["remote_filename"],
        )
        self.assertEqual(
            self.client.file_check_calls[0][1],
            session["remote_storage"],
        )

    def test_create_and_upload_persists_parsed_filename_grams(self):
        result = self.service.create_and_upload(
            printer_id="mk4-01",
            uploaded_file=UploadedFileStub(b"G1 X1 Y1\n"),
            original_filename="widget_8g_PLA.gcode",
            start_print=False,
        )

        self.assertTrue(result["ok"])
        session = self.upload_db.get_session(result["upload_session_id"])
        self.assertEqual(session["parsed_grams"], 8.0)
        self.assertEqual(session["parsed_grams_source"], "filename")

    def test_start_failed_retry_reuses_uploaded_file_without_reupload(self):
        staged_path = os.path.join(self.tempdir.name, "retry.gcode")
        with open(staged_path, "wb") as handle:
            handle.write(b"G1 X2 Y2\n")

        session = self.upload_db.create_session(
            upload_session_id="retry-session-1",
            printer_id="mk4-02",
            queue_job_id=11,
            work_order_job_id=22,
            original_filename="retry.gcode",
            staged_path=staged_path,
            remote_filename="mk4-02__retry__retry.gcode",
            remote_storage="usb",
            file_size_bytes=os.path.getsize(staged_path),
            status="start_failed",
            operator_initials="AB",
        )

        result = self.service.retry_session(
            session["upload_session_id"],
            start_print=True,
            operator_initials="AB",
        )

        self.assertTrue(result["ok"])
        refreshed = self.upload_db.get_session(session["upload_session_id"])
        self.assertEqual(refreshed["status"], "printing")
        self.assertEqual(self.client.upload_calls, 0)
        self.assertEqual(self.client.start_calls, 1)
        self.assertEqual(len(self.farm_manager.pending_calls), 1)
        self.assertEqual(
            self.client.start_args[0],
            (session["remote_filename"], session["remote_storage"]),
        )

    def test_create_and_upload_retries_visibility_until_file_appears(self):
        self.client.exists_after = 3

        result = self.service.create_and_upload(
            printer_id="mk4-01",
            uploaded_file=UploadedFileStub(b"G1 X1 Y1\n"),
            original_filename="delayed.gcode",
            start_print=False,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(self.client.file_checks, 3)
        session = self.upload_db.get_session(result["upload_session_id"])
        self.assertEqual(session["status"], "uploaded")
        self.assertTrue(all(
            call[0] == session["remote_filename"]
            and call[1] == session["remote_storage"]
            for call in self.client.file_check_calls
        ))


if __name__ == "__main__":
    unittest.main()
