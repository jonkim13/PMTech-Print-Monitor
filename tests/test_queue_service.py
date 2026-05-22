"""Unit tests for ``QueueService.start_print_request``.

These tests substitute fakes for every collaborator (queue + execution
repositories, farm_manager, execution_service). The point is to pin
the orchestration sequence and the status-code mapping the route
relies on. There are no integration tests on
``/api/queue/<id>/print`` today, so this file is the safety net for
the Phase 5e refactor.
"""

import io
import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.queue.service import (
    InvalidPrintRequestError,
    QueueExecutionConflictError,
    QueueItemNotFoundError,
    QueueService,
)


class FakeQueueRepo:
    ACTIVE_QUEUE_STATUSES = {"printing", "starting", "uploading"}
    PRINTABLE_QUEUE_STATUSES = {"queued", "upload_failed", "start_failed"}

    def __init__(self, items):
        self._by_id = {item["queue_id"]: item for item in items}

    def get_queue_items(self, queue_ids):
        return [self._by_id[q] for q in queue_ids if q in self._by_id]

    def get_queue_item(self, queue_id):
        return self._by_id.get(queue_id)


class FakeExecutionRepo:
    def __init__(self, result=None, exc=None):
        self.calls = []
        self._result = result
        self._exc = exc

    def start_queue_job_execution(self, queue_ids, printer_id,
                                  printer_name, gcode_file,
                                  operator_initials=None, job_id=None):
        self.calls.append({
            "queue_ids": list(queue_ids),
            "printer_id": printer_id,
            "printer_name": printer_name,
            "gcode_file": gcode_file,
            "operator_initials": operator_initials,
            "job_id": job_id,
        })
        if self._exc:
            raise self._exc
        return self._result or {
            "queue_job_id": 99, "job_id": 7, "queue_ids": list(queue_ids),
            "wo_id": "WO-1", "auto_created_job": False,
        }


class FakeClient:
    pass


class FakeFarmManager:
    def __init__(self, printer_status="idle", client=None):
        self._status = printer_status
        self._client = client if client is not None else FakeClient()

    def get_printer_client(self, printer_id):
        if printer_id == "missing":
            return None
        return self._client

    def get_printer_status(self, printer_id):
        return {"status": self._status, "name": "Printer 1"}


class FakeExecutionService:
    def __init__(self, result=None, exc=None):
        self.calls = []
        self._result = result
        self._exc = exc

    def create_and_upload(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        if self._result is None:
            return {"ok": True, "success": True, "message": "uploaded",
                    "http_status": 200}
        return dict(self._result)


class FakeFile:
    """Stand-in for a Werkzeug FileStorage. Service only reads .filename."""
    def __init__(self, filename):
        self.filename = filename


def _printable_items():
    return [
        {"queue_id": 1, "wo_id": "WO-1", "status": "queued", "job_id": None},
    ]


def _build_service(*, queue_items=None, exec_repo_result=None,
                   exec_repo_exc=None, exec_service_result=None,
                   farm_status="idle", client=FakeClient()):
    queue_repo = FakeQueueRepo(queue_items or _printable_items())
    exec_repo = FakeExecutionRepo(result=exec_repo_result, exc=exec_repo_exc)
    exec_service = FakeExecutionService(result=exec_service_result)
    svc = QueueService(
        queue_repository=queue_repo,
        execution_repository=exec_repo,
        farm_manager=FakeFarmManager(printer_status=farm_status, client=client),
        execution_service=exec_service,
    )
    return svc, exec_repo, exec_service


class StartPrintRequestTests(unittest.TestCase):
    def test_happy_path_returns_result_with_workflow_fields(self):
        svc, exec_repo, exec_service = _build_service()

        result = svc.start_print_request(
            printer_id="printer-1",
            queue_ids=[1],
            requested_job_id=None,
            uploaded_file=FakeFile("part_A.gcode"),
            operator_initials="JK",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["queue_ids"], [1])
        self.assertEqual(result["queue_job_id"], 99)
        self.assertEqual(result["job_id"], 7)
        self.assertEqual(result["printer_id"], "printer-1")
        self.assertEqual(result["wo_id"], "WO-1")
        # Orchestration sequence: queue resolution -> execution session ->
        # create_and_upload, with the validated filename flowing through.
        self.assertEqual(len(exec_repo.calls), 1)
        self.assertEqual(exec_repo.calls[0]["gcode_file"], "part_A.gcode")
        self.assertEqual(len(exec_service.calls), 1)
        self.assertEqual(
            exec_service.calls[0]["original_filename"], "part_A.gcode"
        )
        self.assertEqual(exec_service.calls[0]["operator_initials"], "JK")

    def test_unknown_queue_id_raises_not_found(self):
        svc, _, _ = _build_service()
        with self.assertRaises(QueueItemNotFoundError):
            svc.start_print_request(
                printer_id="printer-1",
                queue_ids=[999],
                requested_job_id=None,
                uploaded_file=FakeFile("part.gcode"),
                operator_initials="JK",
            )

    def test_printer_busy_raises_invalid_request_preserving_400(self):
        """The pre-5e route returned 400 for non-idle printers (NOT 409).
        Status-code preservation: this must surface as
        InvalidPrintRequestError so the route still emits 400."""
        svc, _, _ = _build_service(farm_status="printing")
        with self.assertRaises(InvalidPrintRequestError) as ctx:
            svc.start_print_request(
                printer_id="printer-1",
                queue_ids=[1],
                requested_job_id=None,
                uploaded_file=FakeFile("part.gcode"),
                operator_initials="JK",
            )
        self.assertIn("Printer is not idle", str(ctx.exception))

    def test_unknown_printer_raises_not_found(self):
        svc, _, _ = _build_service()
        with self.assertRaises(QueueItemNotFoundError) as ctx:
            svc.start_print_request(
                printer_id="missing",
                queue_ids=[1],
                requested_job_id=None,
                uploaded_file=FakeFile("part.gcode"),
                operator_initials="JK",
            )
        self.assertIn("Unknown printer", str(ctx.exception))

    def test_missing_file_raises_invalid_request(self):
        svc, _, _ = _build_service()
        with self.assertRaises(InvalidPrintRequestError) as ctx:
            svc.start_print_request(
                printer_id="printer-1",
                queue_ids=[1],
                requested_job_id=None,
                uploaded_file=None,
                operator_initials="JK",
            )
        self.assertIn("No gcode file", str(ctx.exception))

    def test_missing_operator_initials_raises_invalid_request(self):
        svc, _, _ = _build_service()
        with self.assertRaises(InvalidPrintRequestError) as ctx:
            svc.start_print_request(
                printer_id="printer-1",
                queue_ids=[1],
                requested_job_id=None,
                uploaded_file=FakeFile("part.gcode"),
                operator_initials="   ",
            )
        self.assertIn("operator_initials", str(ctx.exception))

    def test_unsupported_extension_raises_invalid_request(self):
        svc, _, _ = _build_service()
        with self.assertRaises(InvalidPrintRequestError) as ctx:
            svc.start_print_request(
                printer_id="printer-1",
                queue_ids=[1],
                requested_job_id=None,
                uploaded_file=FakeFile("part.stl"),
                operator_initials="JK",
            )
        self.assertIn("Unsupported file type", str(ctx.exception))

    def test_items_already_in_progress_raises_conflict(self):
        items = [
            {"queue_id": 1, "wo_id": "WO-1", "status": "printing",
             "job_id": None},
        ]
        svc, _, _ = _build_service(queue_items=items)
        with self.assertRaises(QueueExecutionConflictError):
            svc.start_print_request(
                printer_id="printer-1",
                queue_ids=[1],
                requested_job_id=None,
                uploaded_file=FakeFile("part.gcode"),
                operator_initials="JK",
            )

    def test_execution_session_runtime_error_raises_conflict(self):
        svc, _, _ = _build_service(
            exec_repo_exc=RuntimeError("queue_job already running"),
        )
        with self.assertRaises(QueueExecutionConflictError):
            svc.start_print_request(
                printer_id="printer-1",
                queue_ids=[1],
                requested_job_id=None,
                uploaded_file=FakeFile("part.gcode"),
                operator_initials="JK",
            )

    def test_upload_failure_returns_result_with_ok_false(self):
        """Upload-side failures don't raise — the result dict carries
        ``ok=False`` and ``http_status`` so the route's existing
        ``_workflow_status_code`` path can emit the right HTTP code.
        This preserves the pre-5e two-tier model: validation gates use
        typed exceptions, upload failures use the result dict."""
        svc, _, _ = _build_service(exec_service_result={
            "ok": False, "success": False,
            "message": "PrusaLink rejected the upload",
            "error_type": "upload_failed",
            "http_status": 502,
        })

        result = svc.start_print_request(
            printer_id="printer-1",
            queue_ids=[1],
            requested_job_id=None,
            uploaded_file=FakeFile("part.gcode"),
            operator_initials="JK",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["http_status"], 502)
        # Result still carries the orchestration metadata.
        self.assertEqual(result["queue_job_id"], 99)
        self.assertEqual(result["printer_id"], "printer-1")


if __name__ == "__main__":
    unittest.main()
