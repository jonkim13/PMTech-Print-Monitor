"""
PrusaLink Client
=================
Communicates with Prusa printers via the PrusaLink HTTP API.

PrusaLink API docs:
https://github.com/prusa3d/Prusa-Link-Web/blob/master/spec/openapi.yaml

Key endpoints:
    GET  /api/v1/status    - printer state, temperatures, job progress
    GET  /api/v1/job       - current job details
    GET  /api/v1/storage   - available files
    PUT  /api/v1/files/{storage}/{path} - upload gcode
    POST /api/v1/files/{storage}/{path} - start print of uploaded file
    DELETE /api/v1/job     - stop current job

Authentication:
    PrusaLink uses HTTP Digest auth with username "maker"
    and the API key/password from the printer's settings.
"""

import os
import time
from typing import Optional
from urllib.parse import quote

import requests
from requests.auth import HTTPDigestAuth


class PrusaLinkClient:
    """Communicates with a single Prusa printer via the PrusaLink API."""

    def __init__(self, printer_id: str, name: str, host: str,
                 username: str = "maker", password: str = "",
                 model: str = "unknown",
                 upload_storage: str = "usb"):
        self.printer_id = printer_id
        self.name = name
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.model = model
        self.default_storage = self._sanitize_storage_name(upload_storage)
        self.base_url = "http://{}".format(self.host)
        self.timeout = 10
        self.upload_timeout = (10, 120)
        self.upload_retries = 3
        self.upload_retry_delay = 5
        self.start_print_wait_sec = 15

        # PrusaLink uses HTTP Digest auth, but some firmware versions
        # use Basic auth. We try Digest first, fall back to Basic.
        self.auth_digest = HTTPDigestAuth(self.username, self.password)
        self.auth_basic = (self.username, self.password)
        self.use_basic = False  # will flip to True if Digest fails

        print("[PRUSALINK] {} configured upload storage target: {}".format(
            self.printer_id, self.default_storage
        ))

        # Current state (updated by polling)
        from app.domains.printers.status_mapper import build_printer_state

        self.state = build_printer_state(printer_id, name, model)

    def _get_auth(self):
        """Return the appropriate auth object."""
        if self.use_basic:
            return self.auth_basic
        return self.auth_digest

    def _request_raw(self, endpoint: str, method: str = "GET",
                     **kwargs) -> requests.Response:
        """
        Make an authenticated request, handling auth method fallback.
        Tries Digest first, falls back to Basic if it gets a 401.
        """
        url = "{}{}".format(self.base_url, endpoint)
        req_method = getattr(requests, method.lower())
        timeout = kwargs.pop("timeout", self.timeout)
        body = kwargs.get("data")

        # Try primary auth method
        resp = req_method(url, auth=self._get_auth(),
                          timeout=timeout, **kwargs)

        # If Digest fails with 401, try Basic
        if resp.status_code == 401 and not self.use_basic:
            print("[PRUSALINK] {} auth fallback: digest received HTTP 401, "
                  "retrying with basic auth".format(self.printer_id))
            if hasattr(body, "seek"):
                try:
                    body.seek(0)
                except (AttributeError, OSError):
                    pass
            self.use_basic = True
            resp = req_method(url, auth=self._get_auth(),
                              timeout=timeout, **kwargs)

        return resp

    def _request(self, endpoint: str, method: str = "GET",
                 **kwargs) -> requests.Response:
        resp = self._request_raw(endpoint, method=method, **kwargs)
        resp.raise_for_status()
        return resp

    @staticmethod
    def _truncate_text(value, limit=500):
        # type: (object, int) -> str
        text = str(value or "")
        if len(text) <= limit:
            return text
        return text[:limit] + "...[truncated]"

    def _auth_mode_label(self):
        # type: () -> str
        return "basic" if self.use_basic else "digest"

    @classmethod
    def _response_debug_details(cls, response):
        # type: (Optional[requests.Response]) -> dict
        if response is None:
            return {
                "http_status": None,
                "reason": None,
                "response_text": "",
                "downstream_message": None,
            }
        try:
            response_text = response.text
        except Exception as exc:
            response_text = "<unavailable: {}>".format(exc)
        truncated = cls._truncate_text(response_text, 500)
        downstream = truncated or str(response.reason or "").strip() or None
        return {
            "http_status": response.status_code,
            "reason": response.reason,
            "response_text": truncated,
            "downstream_message": downstream,
        }

    @staticmethod
    def _exception_flags(exc):
        # type: (Exception) -> dict
        return {
            "is_connect_timeout": isinstance(
                exc, requests.exceptions.ConnectTimeout
            ),
            "is_read_timeout": isinstance(
                exc, requests.exceptions.ReadTimeout
            ),
            "is_timeout": isinstance(exc, requests.exceptions.Timeout),
            "is_connection_error": isinstance(
                exc, requests.exceptions.ConnectionError
            ),
            "is_http_error": isinstance(exc, requests.exceptions.HTTPError),
            "is_request_exception": isinstance(
                exc, requests.exceptions.RequestException
            ),
        }

    @staticmethod
    def _result(ok, message, http_status=None, error_type=None,
                details=None, **kwargs):
        # type: (bool, str, Optional[int], Optional[str], Optional[dict], ...) -> dict
        result = {
            "ok": ok,
            "success": ok,
            "message": message,
            "error_type": error_type,
            "http_status": http_status,
            "details": details or {},
        }
        if http_status is not None:
            result["status_code"] = http_status
        if not ok:
            result["error"] = message
        result.update(kwargs)
        return result

    @staticmethod
    def _sanitize_storage_name(storage):
        # type: (Optional[str]) -> str
        normalized = str(storage or "").strip().lower()
        return normalized or "usb"

    def _resolve_storage(self, storage=None):
        # type: (Optional[str]) -> str
        normalized = str(storage or "").strip().lower()
        if normalized:
            return self._sanitize_storage_name(normalized)
        return self.default_storage

    @staticmethod
    def _classify_http_status(status_code):
        # type: (int) -> str
        if status_code in (401, 403):
            return "auth_error"
        if status_code == 404:
            return "not_found"
        if status_code in (408, 504):
            return "timeout"
        if status_code in (409, 423):
            return "printer_busy"
        if status_code in (413, 507):
            return "storage_full"
        if status_code >= 500:
            return "printer_api_error"
        return "request_rejected"

    @classmethod
    def _http_error_result(cls, exc, action, details=None):
        # type: (requests.exceptions.HTTPError, str, Optional[dict]) -> dict
        status_code = exc.response.status_code if exc.response else 502
        error_type = cls._classify_http_status(status_code)
        response_info = cls._response_debug_details(exc.response)
        reason = response_info.get("reason")
        message = "{} failed with HTTP {}{}".format(
            action,
            status_code,
            " {}".format(reason) if reason else "",
        )
        merged = dict(details or {})
        merged.update({
            "exception_class": exc.__class__.__name__,
            "exception_message": str(exc),
            "http_status": response_info.get("http_status"),
            "reason": response_info.get("reason"),
            "response_text": response_info.get("response_text"),
            "downstream_message": response_info.get("downstream_message"),
        })
        merged.update(cls._exception_flags(exc))
        return cls._result(
            False,
            message,
            http_status=status_code,
            error_type=error_type,
            details=merged,
        )

    @classmethod
    def _http_response_result(cls, response, action, details=None):
        # type: (requests.Response, str, Optional[dict]) -> dict
        response_info = cls._response_debug_details(response)
        status_code = response_info.get("http_status") or 502
        reason = response_info.get("reason")
        message = "{} failed with HTTP {}{}".format(
            action,
            status_code,
            " {}".format(reason) if reason else "",
        )
        merged = dict(details or {})
        merged.update({
            "http_status": response_info.get("http_status"),
            "reason": response_info.get("reason"),
            "response_text": response_info.get("response_text"),
            "downstream_message": response_info.get("downstream_message"),
        })
        return cls._result(
            False,
            message,
            http_status=status_code,
            error_type=cls._classify_http_status(status_code),
            details=merged,
        )

    @classmethod
    def _request_error_result(cls, exc, action, details=None):
        # type: (Exception, str, Optional[dict]) -> dict
        merged = dict(details or {})
        merged.update({
            "exception_class": exc.__class__.__name__,
            "exception_message": str(exc),
            "downstream_message": cls._truncate_text(str(exc), 500),
        })
        merged.update(cls._exception_flags(exc))
        if isinstance(exc, requests.exceptions.Timeout):
            return cls._result(
                False,
                "{} timed out".format(action),
                http_status=504,
                error_type="timeout",
                details=merged,
            )
        if isinstance(exc, requests.exceptions.ConnectionError):
            return cls._result(
                False,
                "{} failed: printer connection error".format(action),
                http_status=502,
                error_type="connection_error",
                details=merged,
            )
        if isinstance(exc, requests.exceptions.HTTPError):
            return cls._http_error_result(exc, action, details=merged)
        if isinstance(exc, requests.exceptions.RequestException):
            return cls._result(
                False,
                "{} failed: {}".format(action, exc),
                http_status=502,
                error_type="request_error",
                details=merged,
            )
        return cls._result(
            False,
            "{} failed: {}".format(action, exc),
            http_status=500,
            error_type="unexpected_error",
            details=merged,
        )

    @staticmethod
    def _should_retry_result(result):
        # type: (dict) -> bool
        return result.get("error_type") in (
            "timeout",
            "connection_error",
            "printer_api_error",
            "request_error",
        )

    @staticmethod
    def _collect_storage_candidates(node):
        # type: (object) -> list
        candidates = []
        if isinstance(node, dict):
            for key in ("path", "name", "display_name", "display"):
                value = node.get(key)
                if isinstance(value, str) and value:
                    candidates.append(value)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    candidates.extend(
                        PrusaLinkClient._collect_storage_candidates(value)
                    )
        elif isinstance(node, list):
            for item in node:
                candidates.extend(
                    PrusaLinkClient._collect_storage_candidates(item)
                )
        return candidates

    def _file_endpoint(self, filename, storage="usb"):
        # type: (str, str) -> str
        """Build the PrusaLink file endpoint for printer storage."""
        quoted = quote(filename, safe="/")
        return "/api/v1/files/{}/{}".format(
            self._resolve_storage(storage), quoted
        )

    def _log_file_check(self, method_label, remote_filename, storage,
                        endpoint, response=None, exists=None,
                        attempt=None, elapsed_sec=None, note=None):
        # type: (str, str, str, str, Optional[requests.Response], Optional[bool], Optional[int], Optional[float], Optional[str]) -> None
        response_info = self._response_debug_details(response)
        parts = [
            "[VERIFY][CHECK] {}".format(self.printer_id),
            "storage={}".format(storage),
            "remote_file={}".format(remote_filename),
            "method={}".format(method_label),
            "url={}{}".format(self.base_url, endpoint),
            "status={}".format(response_info.get("http_status")),
            "found={}".format(exists),
        ]
        if attempt is not None:
            parts.append("attempt={}".format(attempt))
        if elapsed_sec is not None:
            parts.append("elapsed={:.1f}s".format(elapsed_sec))
        if response_info.get("reason"):
            parts.append("reason={}".format(response_info.get("reason")))
        if note:
            parts.append("note={}".format(note))
        if (response is not None and response.status_code >= 400
                and response_info.get("response_text")):
            parts.append("body={}".format(
                self._truncate_text(response_info.get("response_text"), 200)
            ))
        print(" ".join(parts))

    def upload_file(self, local_path, remote_filename, storage="usb"):
        # type: (str, str, str) -> dict
        """Upload a G-code file to printer storage without starting it."""
        if not os.path.exists(local_path):
            return self._result(
                False,
                "Local staged file not found",
                http_status=404,
                error_type="local_file_missing",
                details={"local_path": local_path},
            )

        try:
            file_size = os.path.getsize(local_path)
        except OSError as exc:
            return self._result(
                False,
                "Could not read local staged file",
                http_status=500,
                error_type="local_file_error",
                details={"local_path": local_path, "error": str(exc)},
            )

        endpoint = self._file_endpoint(remote_filename, storage=storage)
        upload_url = "{}{}".format(self.base_url, endpoint)
        normalized_storage = self._resolve_storage(storage)
        last_result = None

        for attempt in range(1, self.upload_retries + 1):
            start_time = time.monotonic()
            print("[UPLOAD] Attempt {}/{} to {}: url={} remote_file={} "
                  "local_path={} size={}B timeout={} auth_mode={} "
                  "storage={}".format(
                      attempt, self.upload_retries, self.printer_id,
                      upload_url, remote_filename, local_path, file_size,
                      self.upload_timeout, self._auth_mode_label(),
                      normalized_storage))
            try:
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(file_size),
                    "Overwrite": "?1",
                }
                with open(local_path, "rb") as fh:
                    resp = self._request(
                        endpoint,
                        method="PUT",
                        data=fh,
                        headers=headers,
                        timeout=self.upload_timeout,
                    )

                elapsed = time.monotonic() - start_time
                print("[UPLOAD] Success on {} (attempt {}): remote_file={} "
                      "size={}B status={} elapsed={:.1f}s".format(
                          self.printer_id, attempt, remote_filename,
                          file_size, resp.status_code, elapsed))
                return self._result(
                    True,
                    "File uploaded to printer storage",
                    http_status=resp.status_code,
                    details={
                        "attempt": attempt,
                        "elapsed_sec": round(elapsed, 2),
                        "remote_filename": remote_filename,
                        "storage": normalized_storage,
                        "upload_url": upload_url,
                        "local_path": local_path,
                        "timeout": self.upload_timeout,
                        "auth_mode": self._auth_mode_label(),
                        "file_size_bytes": file_size,
                    },
                )

            except Exception as exc:
                elapsed = time.monotonic() - start_time
                response = exc.response if hasattr(exc, "response") else None
                response_info = self._response_debug_details(response)
                last_result = self._request_error_result(
                    exc,
                    "upload",
                    details={
                        "attempt": attempt,
                        "elapsed_sec": round(elapsed, 2),
                        "upload_url": upload_url,
                        "remote_filename": remote_filename,
                        "local_path": local_path,
                        "storage": normalized_storage,
                        "timeout": self.upload_timeout,
                        "auth_mode": self._auth_mode_label(),
                        "file_size_bytes": file_size,
                    },
                )
                if response is not None:
                    print("[UPLOAD] HTTP response on {} (attempt {}): "
                          "status={} reason={} body={}".format(
                              self.printer_id, attempt,
                              response_info.get("http_status"),
                              response_info.get("reason"),
                              response_info.get("response_text")))
                flags = self._exception_flags(exc)
                print("[UPLOAD] Exception on {} (attempt {}): class={} "
                      "message={} connect_timeout={} read_timeout={} "
                      "timeout={} connection_error={} http_error={} "
                      "request_exception={}".format(
                          self.printer_id, attempt, exc.__class__.__name__,
                          str(exc), flags["is_connect_timeout"],
                          flags["is_read_timeout"], flags["is_timeout"],
                          flags["is_connection_error"],
                          flags["is_http_error"],
                          flags["is_request_exception"]))
                print("[UPLOAD] Failure on {} (attempt {}): remote_file={} "
                      "error_type={} http_status={} exception_class={} "
                      "downstream_message={} elapsed={:.1f}s".format(
                          self.printer_id, attempt, remote_filename,
                          last_result.get("error_type"),
                          last_result.get("http_status"),
                          last_result.get("details", {}).get(
                              "exception_class"),
                          self._truncate_text(
                              last_result.get("details", {}).get(
                                  "downstream_message"
                              ), 200
                          ),
                          elapsed))
                if (attempt >= self.upload_retries
                        or not self._should_retry_result(last_result)):
                    break
                print("[UPLOAD] Retrying in {}s...".format(
                    self.upload_retry_delay))
                time.sleep(self.upload_retry_delay)

        return last_result or self._result(
            False,
            "Upload failed",
            http_status=500,
            error_type="upload_failed",
            details={"remote_filename": remote_filename, "storage": storage},
        )

    def get_transfer_status(self):
        # type: () -> dict
        """Read the current upload/transfer status from PrusaLink."""
        try:
            resp = self._request("/api/v1/transfer")
            payload = {}
            if resp.content:
                payload = resp.json()
            return self._result(
                True,
                "Transfer status fetched",
                http_status=resp.status_code,
                details=payload,
            )
        except Exception as exc:
            return self._request_error_result(exc, "transfer status lookup")

    def file_exists(self, remote_filename, storage="usb",
                    attempt=None, elapsed_sec=None):
        # type: (str, str, Optional[int], Optional[float]) -> dict
        """Check whether a file is visible on printer storage."""
        normalized_storage = self._resolve_storage(storage)
        target = str(remote_filename or "").strip().lstrip("/")
        file_endpoint = self._file_endpoint(target, storage=normalized_storage)
        storage_endpoint = "/api/v1/storage/{}".format(normalized_storage)

        head_resp = None
        try:
            head_resp = self._request_raw(
                file_endpoint,
                method="HEAD",
            )
            if 200 <= head_resp.status_code < 300:
                self._log_file_check(
                    "HEAD", target, normalized_storage, file_endpoint,
                    response=head_resp, exists=True, attempt=attempt,
                    elapsed_sec=elapsed_sec,
                )
                return self._result(
                    True,
                    "File found on printer storage",
                    http_status=head_resp.status_code,
                    details={
                        "exists": True,
                        "method": "head",
                        "remote_filename": target,
                        "storage": normalized_storage,
                        "endpoint": file_endpoint,
                        "summary": "HEAD {} file found".format(
                            head_resp.status_code
                        ),
                    },
                )
            self._log_file_check(
                "HEAD", target, normalized_storage, file_endpoint,
                response=head_resp, exists=False, attempt=attempt,
                elapsed_sec=elapsed_sec,
                note=("falling back to storage listing"
                      if head_resp.status_code in (404, 405, 501)
                      else "non-success response"),
            )
        except Exception as exc:
            result = self._request_error_result(
                exc,
                "file existence HEAD check",
                details={
                    "remote_filename": target,
                    "storage": normalized_storage,
                    "endpoint": file_endpoint,
                },
            )
            print("[VERIFY][CHECK] {} storage={} remote_file={} method=HEAD "
                  "attempt={} elapsed={}s error_type={} http_status={} "
                  "exception_class={} downstream_message={}".format(
                      self.printer_id,
                      normalized_storage,
                      target,
                      attempt,
                      "{:.1f}".format(elapsed_sec)
                      if elapsed_sec is not None else "n/a",
                      result.get("error_type"),
                      result.get("http_status"),
                      result.get("details", {}).get("exception_class"),
                      self._truncate_text(
                          result.get("details", {}).get(
                              "downstream_message"
                          ), 200
                      ),
                  ))
            return result

        try:
            list_resp = self._request_raw(storage_endpoint)
            if list_resp.status_code >= 400:
                self._log_file_check(
                    "LIST_STORAGE", target, normalized_storage,
                    storage_endpoint, response=list_resp, exists=False,
                    attempt=attempt, elapsed_sec=elapsed_sec,
                )
                return self._http_response_result(
                    list_resp,
                    "file existence storage listing",
                    details={
                        "remote_filename": target,
                        "storage": normalized_storage,
                        "endpoint": storage_endpoint,
                    },
                )

            payload = list_resp.json() if list_resp.content else {}
            target_lower = target.lower()
            target_bare = os.path.basename(target_lower)
            matches = []
            for candidate in self._collect_storage_candidates(payload):
                normalized = str(candidate).strip().lower()
                if (normalized == target_lower
                        or normalized.endswith("/" + target_lower)
                        or normalized == target_bare
                        or normalized.endswith("/" + target_bare)):
                    matches.append(candidate)

            exists = bool(matches)
            self._log_file_check(
                "LIST_STORAGE", target, normalized_storage, storage_endpoint,
                response=list_resp, exists=exists, attempt=attempt,
                elapsed_sec=elapsed_sec,
                note=("matches={}".format(matches[:3]) if matches else None),
            )
            return self._result(
                True,
                "File {} on printer storage".format(
                    "found" if exists else "not found"
                ),
                http_status=list_resp.status_code,
                details={
                    "exists": exists,
                    "matches": matches[:5],
                    "method": "storage_listing",
                    "remote_filename": target,
                    "storage": normalized_storage,
                    "endpoint": storage_endpoint,
                    "summary": ("LIST {} matches={}".format(
                        list_resp.status_code, len(matches)
                    )),
                },
            )
        except Exception as exc:
            result = self._request_error_result(
                exc,
                "file existence storage listing",
                details={
                    "remote_filename": target,
                    "storage": normalized_storage,
                    "endpoint": storage_endpoint,
                },
            )
            print("[VERIFY][CHECK] {} storage={} remote_file={} "
                  "method=LIST_STORAGE attempt={} elapsed={}s error_type={} "
                  "http_status={} exception_class={} downstream_message={}"
                  .format(
                      self.printer_id,
                      normalized_storage,
                      target,
                      attempt,
                      "{:.1f}".format(elapsed_sec)
                      if elapsed_sec is not None else "n/a",
                      result.get("error_type"),
                      result.get("http_status"),
                      result.get("details", {}).get("exception_class"),
                      self._truncate_text(
                          result.get("details", {}).get(
                              "downstream_message"
                          ), 200
                      ),
                  ))
            return result

    def start_file_print(self, remote_filename, storage="usb"):
        # type: (str, str) -> dict
        """Start printing a file that already exists on printer storage."""
        try:
            endpoint = self._file_endpoint(remote_filename, storage=storage)
            print("[START] printer_id={} storage={} remote_file={} url={} "
                  "auth_mode={}".format(
                      self.printer_id,
                      self._resolve_storage(storage),
                      remote_filename,
                      "{}{}".format(self.base_url, endpoint),
                      self._auth_mode_label(),
                  ))
            resp = self._request(endpoint, method="POST")
            return self._result(
                True,
                "Print start requested",
                http_status=resp.status_code,
                details={
                    "remote_filename": remote_filename,
                    "storage": self._resolve_storage(storage),
                },
            )
        except Exception as exc:
            return self._request_error_result(
                exc,
                "print start",
                details={
                    "remote_filename": remote_filename,
                    "storage": self._resolve_storage(storage),
                },
            )

    def upload_gcode(self, local_path, filename, print_after=False):
        # type: (str, str, bool) -> dict
        """
        Backward-compatible wrapper. Active routes use upload/verify/start
        separately, but older call sites may still rely on this helper.
        """
        upload_result = self.upload_file(local_path, filename)
        if not upload_result.get("ok") or not print_after:
            upload_result["upload_completed"] = upload_result.get("ok")
            upload_result["print_started"] = False
            return upload_result

        start_result = self.start_file_print(filename)
        if not start_result.get("ok"):
            return self._result(
                False,
                start_result.get("message"),
                http_status=start_result.get("http_status"),
                error_type=start_result.get("error_type"),
                details={
                    "upload": upload_result.get("details"),
                    "start": start_result.get("details"),
                },
                upload_completed=True,
                print_started=False,
            )

        return self._result(
            True,
            "File uploaded and print start requested",
            http_status=start_result.get("http_status"),
            details={
                "upload": upload_result.get("details"),
                "start": start_result.get("details"),
            },
            upload_completed=True,
            print_started=True,
        )

    def poll(self):
        # type: () -> dict
        """
        Fetch current status from the printer.
        Returns the updated state dict.
        """
        from app.domains.printers.status_mapper import (
            apply_status_payload,
            mark_connection_failed,
            mark_http_error,
            mark_poll_error,
        )

        try:
            # --- GET /api/v1/status ---
            status_resp = self._request("/api/v1/status")
            status_data = status_resp.json()
            apply_status_payload(self.state, status_data)

        except requests.exceptions.ConnectionError:
            mark_connection_failed(self.state)

        except requests.exceptions.HTTPError as e:
            mark_http_error(self.state, e.response.status_code)

        except Exception as e:
            mark_poll_error(self.state, e)

        return self.state.copy()

    def get_files(self, storage=None):
        # type: (Optional[str]) -> dict
        """Get file listing from printer storage (all or one storage)."""
        try:
            endpoint = "/api/v1/storage"
            if storage:
                endpoint = "/api/v1/storage/{}".format(
                    self._sanitize_storage_name(storage)
                )
            resp = self._request(endpoint)
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def get_job_details(self):
        # type: () -> dict
        """
        Fetch detailed job information from GET /api/v1/job.
        Returns metadata including filament usage estimates,
        layer height, nozzle diameter, temperatures, etc.

        For multi-tool printers (XL), also returns per-tool arrays:
        - filament_used_g_per_tool: list of grams per nozzle
        - filament_used_mm_per_tool: list of mm per nozzle
        - filament_type_per_tool: list of filament type strings
        - nozzle_diameter_per_tool: list of diameters
        """
        try:
            resp = self._request("/api/v1/job")
            data = resp.json()
            # Normalize the response into a flat dict of useful fields
            file_info = data.get("file", {})
            meta = file_info.get("meta", {}) or {}
            # PrusaLink meta uses space-bracket names like
            # "filament used [g]" — try those first, fall back to
            # underscore variants for compatibility.
            raw_g = (meta.get("filament used [g]")
                     or meta.get("filament_used_g") or 0)
            raw_mm = (meta.get("filament used [mm]")
                      or meta.get("filament_used_mm") or 0)
            filament_g = float(raw_g) if raw_g else 0
            filament_mm = float(raw_mm) if raw_mm else 0
            estimated_time = (meta.get("estimated_print_time")
                              or meta.get("estimated print time")
                              or data.get("time_remaining") or 0)

            result = {
                "file_name": file_info.get("name", ""),
                "file_display_name": file_info.get("display_name",
                                                    file_info.get("name", "")),
                "filament_type": meta.get("filament_type", ""),
                "filament_used_g": filament_g,
                "filament_used_mm": filament_mm,
                "layer_height": meta.get("layer_height", None),
                "nozzle_diameter": meta.get("nozzle_diameter", None),
                "fill_density": meta.get("fill_density", None),
                "nozzle_temp": meta.get("nozzle_temp", None),
                "bed_temp": meta.get("bed_temp", None),
                "estimated_time_sec": estimated_time,
                # Per-tool arrays (XL multi-tool support)
                "filament_used_g_per_tool": meta.get(
                    "filament used [g] per tool", []),
                "filament_used_mm_per_tool": meta.get(
                    "filament used [mm] per tool", []),
                "filament_type_per_tool": meta.get(
                    "filament_type per tool", []),
                "nozzle_diameter_per_tool": meta.get(
                    "nozzle_diameter per tool", []),
                "temperature_per_tool": meta.get(
                    "temperature per tool", []),
            }
            return result
        except Exception as e:
            return {"error": str(e)}

    def get_camera_snapshot(self):
        # type: () -> Optional[bytes]
        """
        Grab a camera snapshot from the printer.
        GET /api/v1/cameras/snap returns PNG image data.
        Returns raw bytes on success, None on failure.
        """
        try:
            resp = self._request("/api/v1/cameras/snap")
            if resp.status_code == 200 and resp.content:
                return resp.content
            return None
        except Exception:
            return None

    def stop_job(self):
        # type: () -> dict
        """Stop the current print job via DELETE /api/v1/job."""
        try:
            resp = self._request("/api/v1/job", method="DELETE")
            return {"success": True, "status_code": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}
