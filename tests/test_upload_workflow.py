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

    def upload_file(self, local_path, remote_filename, storage="usb"):
        self.upload_calls += 1
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

    def file_exists(self, remote_filename, storage="usb"):
        self.file_checks += 1
        return {
            "ok": True,
            "success": True,
            "message": "exists",
            "http_status": 200,
            "details": {
                "exists": True,
                "remote_filename": remote_filename,
                "storage": storage,
            },
        }

    def start_file_print(self, remote_filename, storage="usb"):
        self.start_calls += 1
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
            work_order_db=None,
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


if __name__ == "__main__":
    unittest.main()
