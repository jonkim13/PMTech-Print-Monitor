"""
Shared Upload Workflow
======================
Stages files locally, uploads them to the printer, verifies the transfer,
and optionally starts the print as a separate step.
"""

import os
import time
import uuid
from typing import Optional

from filament_usage import (
    FILAMENT_SOURCE_FILENAME,
    FILAMENT_SOURCE_NONE,
    extract_grams_from_filename,
)
from werkzeug.utils import secure_filename


class UploadWorkflowService:
    """Reusable upload/verify/start workflow shared by printer routes."""

    def __init__(self, uploads_dir: str, upload_session_db,
                 farm_manager=None, work_order_db=None):
        self.uploads_dir = uploads_dir
        self.upload_session_db = upload_session_db
        self.farm_manager = farm_manager
        self.work_order_db = work_order_db
        self.verify_timeout_sec = 45
        self.verify_poll_sec = 2
        self.start_confirm_timeout_sec = 30

    @staticmethod
    def _result(ok: bool, message: str,
                error_type: str = None,
                http_status: int = None,
                details: dict = None,
                **extra) -> dict:
        result = {
            "ok": ok,
            "success": ok,
            "message": message,
            "error_type": error_type,
            "http_status": http_status,
            "details": details or {},
        }
        result.update(extra)
        return result

    @staticmethod
    def _build_remote_filename(printer_id: str, upload_session_id: str,
                               original_filename: str) -> str:
        # Keep the original name visible while making the remote file unique.
        safe_name = secure_filename(os.path.basename(original_filename))
        if not safe_name:
            safe_name = "upload.gcode"
        token = upload_session_id[:12]
        safe_printer = secure_filename(printer_id) or "printer"
        return "{}__{}__{}".format(safe_printer, token, safe_name)

    def _stage_file(self, uploaded_file, printer_id: str,
                    original_filename: str,
                    upload_session_id: str) -> dict:
        safe_name = secure_filename(os.path.basename(original_filename))
        if not safe_name:
            raise ValueError("Invalid filename")

        staged_dir = os.path.join(
            self.uploads_dir,
            secure_filename(printer_id) or "printer",
            upload_session_id,
        )
        os.makedirs(staged_dir, exist_ok=True)
        staged_path = os.path.join(staged_dir, safe_name)
        uploaded_file.save(staged_path)
        return {
            "staged_dir": staged_dir,
            "staged_path": staged_path,
            "original_filename": safe_name,
            "file_size_bytes": os.path.getsize(staged_path),
        }

    def _serialize_session(self, session: Optional[dict]) -> dict:
        if not session:
            return {}
        return {
            "upload_session_id": session.get("upload_session_id"),
            "printer_id": session.get("printer_id"),
            "queue_job_id": session.get("queue_job_id"),
            "work_order_job_id": session.get("work_order_job_id"),
            "original_filename": session.get("original_filename"),
            "remote_filename": session.get("remote_filename"),
            "remote_storage": session.get("remote_storage"),
            "status": session.get("status"),
            "operator_initials": session.get("operator_initials"),
            "staged_path": session.get("staged_path"),
            "file_size_bytes": session.get("file_size_bytes"),
            "parsed_grams": session.get("parsed_grams"),
            "parsed_grams_source": session.get("parsed_grams_source"),
            "last_error": session.get("last_error"),
        }

    def _resolve_remote_storage(self, printer_id: str,
                                remote_storage: str = None) -> str:
        if remote_storage:
            return str(remote_storage).strip().lower()
        client = (self.farm_manager.get_printer_client(printer_id)
                  if self.farm_manager else None)
        configured = getattr(client, "default_storage", None) if client else None
        return str(configured or "usb").strip().lower() or "usb"

    @staticmethod
    def _transfer_active(details: dict) -> bool:
        details = details or {}
        flags = (
            details.get("active"),
            details.get("in_progress"),
            details.get("transferring"),
        )
        if any(flag is True for flag in flags):
            return True
        state = str(details.get("state") or details.get("status") or "")
        state = state.strip().lower()
        return state in {"active", "running", "transferring", "uploading"}

    @staticmethod
    def _verification_summary(result: dict) -> str:
        if not isinstance(result, dict):
            return "none"
        details = result.get("details") or {}
        summary = details.get("summary")
        if summary:
            return str(summary)
        parts = []
        for key in ("method", "storage", "remote_filename", "http_status",
                    "downstream_message", "reason"):
            value = details.get(key)
            if value not in (None, "", []):
                parts.append("{}={}".format(key, value))
        if result.get("error_type"):
            parts.append("error_type={}".format(result.get("error_type")))
        if result.get("message"):
            parts.append("message={}".format(result.get("message")))
        return ", ".join(parts) if parts else "none"

    def _sync_queue_job_status(self, queue_job_id: int, status: str,
                               upload_session_id: str = None) -> None:
        if not self.work_order_db or not queue_job_id:
            return
        if status == "uploading":
            self.work_order_db.mark_queue_job_uploading(
                queue_job_id, upload_session_id=upload_session_id
            )
        elif status == "uploaded":
            self.work_order_db.mark_queue_job_uploaded(queue_job_id)
        elif status == "starting":
            self.work_order_db.mark_queue_job_starting(queue_job_id)
        elif status == "printing":
            self.work_order_db.mark_queue_job_printing(queue_job_id)
        elif status == "upload_failed":
            self.work_order_db.mark_queue_job_upload_failed(queue_job_id)
        elif status == "start_failed":
            self.work_order_db.mark_queue_job_start_failed(queue_job_id)

    def _verify_remote_file(self, client, remote_filename: str,
                            storage: str) -> dict:
        printer_id = getattr(client, "printer_id", "unknown")
        verify_start = time.monotonic()
        deadline = time.monotonic() + self.verify_timeout_sec
        last_transfer = None
        last_exists = None
        saw_active_transfer = False
        attempts = 0

        print("[VERIFY] Starting post-upload verification: printer_id={} "
              "storage={} remote_file={} timeout={}s poll={}s".format(
                  printer_id, storage, remote_filename,
                  self.verify_timeout_sec, self.verify_poll_sec
              ))

        while time.monotonic() < deadline:
            attempts += 1
            elapsed = time.monotonic() - verify_start
            transfer = client.get_transfer_status()
            if transfer.get("ok"):
                last_transfer = transfer
                transfer_active = self._transfer_active(transfer.get("details"))
                saw_active_transfer = saw_active_transfer or transfer_active
                print("[VERIFY] {} attempt={} elapsed={:.1f}s "
                      "transfer_http_status={} active={} summary={}".format(
                          printer_id,
                          attempts,
                          elapsed,
                          transfer.get("http_status"),
                          transfer_active,
                          self._verification_summary(transfer),
                      ))
            else:
                transfer_active = False
                last_transfer = transfer
                print("[VERIFY] {} attempt={} elapsed={:.1f}s "
                      "transfer_check_failed error_type={} http_status={} "
                      "summary={}".format(
                          printer_id,
                          attempts,
                          elapsed,
                          transfer.get("error_type"),
                          transfer.get("http_status"),
                          self._verification_summary(transfer),
                      ))

            exists = client.file_exists(
                remote_filename,
                storage=storage,
                attempt=attempts,
                elapsed_sec=elapsed,
            )
            last_exists = exists
            if exists.get("ok"):
                if exists.get("details", {}).get("exists"):
                    total_elapsed = time.monotonic() - verify_start
                    print("[VERIFY] Success on {}: storage={} remote_file={} "
                          "attempts={} elapsed={:.1f}s summary={}".format(
                              printer_id,
                              storage,
                              remote_filename,
                              attempts,
                              total_elapsed,
                              self._verification_summary(exists),
                          ))
                    return self._result(
                        True,
                        "Uploaded file verified on printer storage",
                        http_status=200,
                        details={
                            "transfer": (last_transfer or {}).get("details"),
                            "file_check": exists.get("details"),
                            "attempts": attempts,
                            "elapsed_sec": round(total_elapsed, 2),
                        },
                    )
                print("[VERIFY] {} attempt={} elapsed={:.1f}s "
                      "file_check_found=False summary={}".format(
                          printer_id,
                          attempts,
                          elapsed,
                          self._verification_summary(exists),
                      ))
            else:
                print("[VERIFY] {} attempt={} elapsed={:.1f}s "
                      "file_check_failed error_type={} http_status={} "
                      "summary={}".format(
                          printer_id,
                          attempts,
                          elapsed,
                          exists.get("error_type"),
                          exists.get("http_status"),
                          self._verification_summary(exists),
                      ))

            if not transfer_active and saw_active_transfer and last_exists:
                # Give PrusaLink a few more polls to expose the file listing.
                pass
            time.sleep(self.verify_poll_sec)

        total_elapsed = time.monotonic() - verify_start
        last_summary = self._verification_summary(last_exists or last_transfer)
        print("[VERIFY] Failure on {}: expected_storage={} "
              "expected_remote_file={} attempts={} waited={:.1f}s "
              "last_summary={}".format(
                  printer_id,
                  storage,
                  remote_filename,
                  attempts,
                  total_elapsed,
                  last_summary,
              ))

        if last_exists and not last_exists.get("ok"):
            return self._result(
                False,
                last_exists.get("message") or "Remote file verification failed",
                error_type=(
                    last_exists.get("error_type") or "verification_failed"
                ),
                http_status=last_exists.get("http_status") or 502,
                details={
                    "transfer": (last_transfer or {}).get("details"),
                    "file_check": last_exists.get("details"),
                    "attempts": attempts,
                    "elapsed_sec": round(total_elapsed, 2),
                    "last_summary": last_summary,
                },
            )

        return self._result(
            False,
            "Upload completed but the file never became visible on the printer",
            error_type="verification_failed",
            http_status=409,
            details={
                "transfer": (last_transfer or {}).get("details"),
                "file_check": (last_exists or {}).get("details"),
                "attempts": attempts,
                "elapsed_sec": round(total_elapsed, 2),
                "last_summary": last_summary,
            },
        )

    def _upload_existing_session(self, session: dict) -> dict:
        client = self.farm_manager.get_printer_client(session["printer_id"])
        if not client:
            return self._result(
                False,
                "Unknown printer",
                error_type="printer_not_found",
                http_status=404,
                details={"printer_id": session["printer_id"]},
            )

        staged_path = session["staged_path"]
        if not os.path.exists(staged_path):
            self.upload_session_db.set_status(
                session["upload_session_id"],
                "upload_failed",
                last_error="Staged file missing from server",
            )
            self._sync_queue_job_status(
                session.get("queue_job_id"), "upload_failed"
            )
            return self._result(
                False,
                "Staged file is missing from the server",
                error_type="staged_file_missing",
                http_status=404,
                details=self._serialize_session(
                    self.upload_session_db.get_session(
                        session["upload_session_id"]
                    )
                ),
            )

        self.upload_session_db.set_status(
            session["upload_session_id"], "uploading",
            operator_initials=session.get("operator_initials"),
            completed=False,
        )
        self._sync_queue_job_status(
            session.get("queue_job_id"), "uploading",
            upload_session_id=session["upload_session_id"],
        )

        print("[UPLOAD][SESSION] printer_id={} upload_session_id={} "
              "original_filename={} staged_path={} remote_filename={} "
              "remote_storage={}".format(
                  session["printer_id"],
                  session["upload_session_id"],
                  session.get("original_filename"),
                  staged_path,
                  session.get("remote_filename"),
                  session.get("remote_storage") or "usb",
              ))

        upload_result = client.upload_file(
            staged_path,
            session["remote_filename"],
            storage=session.get("remote_storage") or "usb",
        )
        if not upload_result.get("ok"):
            session = self.upload_session_db.set_status(
                session["upload_session_id"],
                "upload_failed",
                last_error=upload_result.get("message"),
            )
            self._sync_queue_job_status(
                session.get("queue_job_id"), "upload_failed"
            )
            return self._result(
                False,
                upload_result.get("message") or "Upload failed",
                error_type=upload_result.get("error_type") or "upload_failed",
                http_status=upload_result.get("http_status") or 502,
                details={
                    "session": self._serialize_session(session),
                    "upload": upload_result.get("details"),
                },
                downstream_result=upload_result,
                upload_session_id=session["upload_session_id"],
            )

        print("[VERIFY] Upload succeeded; verifying same remote target: "
              "printer_id={} upload_session_id={} storage={} "
              "remote_filename={} staged_path={} original_filename={}"
              .format(
                  session["printer_id"],
                  session["upload_session_id"],
                  session.get("remote_storage") or "usb",
                  session["remote_filename"],
                  staged_path,
                  session.get("original_filename"),
              ))
        verify_result = self._verify_remote_file(
            client,
            session["remote_filename"],
            storage=session.get("remote_storage") or "usb",
        )
        if not verify_result.get("ok"):
            session = self.upload_session_db.set_status(
                session["upload_session_id"],
                "upload_failed",
                last_error=verify_result.get("message"),
            )
            self._sync_queue_job_status(
                session.get("queue_job_id"), "upload_failed"
            )
            return self._result(
                False,
                verify_result.get("message"),
                error_type=verify_result.get("error_type"),
                http_status=verify_result.get("http_status"),
                details={
                    "session": self._serialize_session(session),
                    "verification": verify_result.get("details"),
                },
                downstream_result=verify_result,
                upload_session_id=session["upload_session_id"],
            )

        session = self.upload_session_db.set_status(
            session["upload_session_id"], "uploaded",
            last_error=None,
            operator_initials=session.get("operator_initials"),
            completed=False,
        )
        self._sync_queue_job_status(session.get("queue_job_id"), "uploaded")
        return self._result(
            True,
            "File uploaded to printer storage and verified",
            http_status=200,
            details={
                "session": self._serialize_session(session),
                "upload": upload_result.get("details"),
                "verification": verify_result.get("details"),
            },
            upload_session_id=session["upload_session_id"],
            filename=session["original_filename"],
            remote_filename=session["remote_filename"],
        )

    def start_existing_session(self, upload_session_id: str,
                               operator_initials: str = None) -> dict:
        session = self.upload_session_db.get_session(upload_session_id)
        if not session:
            return self._result(
                False,
                "Upload session not found",
                error_type="session_not_found",
                http_status=404,
            )

        client = self.farm_manager.get_printer_client(session["printer_id"])
        if not client:
            return self._result(
                False,
                "Unknown printer",
                error_type="printer_not_found",
                http_status=404,
            )

        operator_initials = (
            str(operator_initials or session.get("operator_initials") or "")
            .strip() or None
        )
        if not operator_initials:
            return self._result(
                False,
                "operator_initials is required when starting a print",
                error_type="missing_operator_initials",
                http_status=400,
            )

        verify_result = self._verify_remote_file(
            client,
            session["remote_filename"],
            storage=session.get("remote_storage") or "usb",
        )
        if not verify_result.get("ok"):
            error_type = verify_result.get("error_type") or "start_failed"
            if error_type == "verification_failed":
                error_type = "remote_file_missing"
            session = self.upload_session_db.set_status(
                upload_session_id,
                "start_failed",
                last_error=verify_result.get("message"),
                operator_initials=operator_initials,
                completed=True,
            )
            self._sync_queue_job_status(session.get("queue_job_id"),
                                        "start_failed")
            return self._result(
                False,
                verify_result.get("message"),
                error_type=error_type,
                http_status=verify_result.get("http_status"),
                details={
                    "session": self._serialize_session(session),
                    "verification": verify_result.get("details"),
                },
                upload_session_id=upload_session_id,
            )

        session = self.upload_session_db.set_status(
            upload_session_id,
            "starting",
            last_error=None,
            operator_initials=operator_initials,
            completed=False,
        )
        self._sync_queue_job_status(session.get("queue_job_id"), "starting")

        self.farm_manager.record_pending_print_start(
            printer_id=session["printer_id"],
            upload_session_id=upload_session_id,
            remote_filename=session["remote_filename"],
            original_filename=session["original_filename"],
            operator_initials=operator_initials,
            queue_job_id=session.get("queue_job_id"),
            job_id=session.get("work_order_job_id"),
        )

        print("[START] Using verified remote target: printer_id={} "
              "upload_session_id={} storage={} remote_filename={} "
              "original_filename={}".format(
                  session["printer_id"],
                  upload_session_id,
                  session.get("remote_storage") or "usb",
                  session["remote_filename"],
                  session.get("original_filename"),
              ))
        start_result = client.start_file_print(
            session["remote_filename"],
            storage=session.get("remote_storage") or "usb",
        )
        if not start_result.get("ok"):
            self.farm_manager.clear_pending_print_start(
                session["printer_id"],
                upload_session_id=upload_session_id,
            )
            session = self.upload_session_db.set_status(
                upload_session_id,
                "start_failed",
                last_error=start_result.get("message"),
                operator_initials=operator_initials,
                completed=True,
            )
            self._sync_queue_job_status(session.get("queue_job_id"),
                                        "start_failed")
            return self._result(
                False,
                start_result.get("message") or "Failed to start print",
                error_type=start_result.get("error_type") or "start_failed",
                http_status=start_result.get("http_status") or 502,
                details={
                    "session": self._serialize_session(session),
                    "start": start_result.get("details"),
                },
                upload_session_id=upload_session_id,
            )

        confirmed = self.farm_manager.wait_for_print_confirmation(
            session["printer_id"],
            upload_session_id=upload_session_id,
            timeout_sec=self.start_confirm_timeout_sec,
        )
        if not confirmed.get("ok"):
            self.farm_manager.clear_pending_print_start(
                session["printer_id"],
                upload_session_id=upload_session_id,
            )
            session = self.upload_session_db.set_status(
                upload_session_id,
                "start_failed",
                last_error=confirmed.get("message"),
                operator_initials=operator_initials,
                completed=True,
            )
            self._sync_queue_job_status(session.get("queue_job_id"),
                                        "start_failed")
            return self._result(
                False,
                confirmed.get("message"),
                error_type=confirmed.get("error_type") or "start_timeout",
                http_status=409,
                details={
                    "session": self._serialize_session(session),
                    "start": start_result.get("details"),
                    "confirmation": confirmed.get("details"),
                },
                upload_session_id=upload_session_id,
            )

        session = self.upload_session_db.set_status(
            upload_session_id,
            "printing",
            last_error=None,
            operator_initials=operator_initials,
            completed=True,
        )
        self._sync_queue_job_status(session.get("queue_job_id"), "printing")
        return self._result(
            True,
            "Print started and printer state confirmed",
            http_status=200,
            details={
                "session": self._serialize_session(session),
                "start": start_result.get("details"),
                "confirmation": confirmed.get("details"),
            },
            upload_session_id=upload_session_id,
            filename=session["original_filename"],
            remote_filename=session["remote_filename"],
        )

    def create_and_upload(self, printer_id: str, uploaded_file,
                          original_filename: str,
                          start_print: bool = False,
                          operator_initials: str = None,
                          queue_job_id: int = None,
                          work_order_job_id: int = None,
                          remote_storage: str = None) -> dict:
        upload_session_id = uuid.uuid4().hex
        try:
            staged = self._stage_file(
                uploaded_file, printer_id, original_filename,
                upload_session_id
            )
        except Exception as exc:
            return self._result(
                False,
                "Failed to stage uploaded file on the server",
                error_type="stage_failed",
                http_status=500,
                details={
                    "printer_id": printer_id,
                    "original_filename": original_filename,
                    "error": str(exc),
                },
            )
        if staged["file_size_bytes"] <= 0:
            return self._result(
                False,
                "Uploaded file is empty",
                error_type="empty_file",
                http_status=400,
            )

        resolved_storage = self._resolve_remote_storage(
            printer_id, remote_storage=remote_storage
        )
        parsed_grams = extract_grams_from_filename(staged["original_filename"])
        session = self.upload_session_db.create_session(
            upload_session_id=upload_session_id,
            printer_id=printer_id,
            queue_job_id=queue_job_id,
            work_order_job_id=work_order_job_id,
            original_filename=staged["original_filename"],
            staged_path=staged["staged_path"],
            remote_filename=self._build_remote_filename(
                printer_id, upload_session_id, staged["original_filename"]
            ),
            remote_storage=resolved_storage,
            file_size_bytes=staged["file_size_bytes"],
            status="staged",
            operator_initials=operator_initials,
            parsed_grams=parsed_grams,
            parsed_grams_source=(
                FILAMENT_SOURCE_FILENAME
                if parsed_grams is not None else FILAMENT_SOURCE_NONE
            ),
        )
        if session.get("queue_job_id"):
            self._sync_queue_job_status(
                session["queue_job_id"],
                "uploading",
                upload_session_id=upload_session_id,
            )

        upload_result = self._upload_existing_session(session)
        if not upload_result.get("ok") or not start_print:
            return upload_result
        return self.start_existing_session(upload_session_id, operator_initials)

    def retry_session(self, upload_session_id: str, start_print: bool = False,
                      operator_initials: str = None) -> dict:
        session = self.upload_session_db.get_session(upload_session_id)
        if not session:
            return self._result(
                False,
                "Upload session not found",
                error_type="session_not_found",
                http_status=404,
            )

        status = session.get("status")
        if status in ("uploaded", "start_failed") and start_print:
            return self.start_existing_session(upload_session_id,
                                               operator_initials)
        if status == "printing":
            return self._result(
                True,
                "Print is already confirmed for this upload session",
                http_status=200,
                details={"session": self._serialize_session(session)},
                upload_session_id=upload_session_id,
            )
        if status in ("uploading", "starting"):
            return self._result(
                False,
                "Upload session is already in progress",
                error_type="session_in_progress",
                http_status=409,
                details={"session": self._serialize_session(session)},
                upload_session_id=upload_session_id,
            )
        if status == "uploaded":
            return self._result(
                True,
                "File is already uploaded and verified on the printer",
                http_status=200,
                details={"session": self._serialize_session(session)},
                upload_session_id=upload_session_id,
            )
        upload_result = self._upload_existing_session(session)
        if not upload_result.get("ok") or not start_print:
            return upload_result
        return self.start_existing_session(upload_session_id,
                                           operator_initials)

    def get_queue_retry_session(self, queue_job_id: int) -> Optional[dict]:
        if not queue_job_id:
            return None
        return self.upload_session_db.get_latest_session_for_queue_job(
            queue_job_id
        )
