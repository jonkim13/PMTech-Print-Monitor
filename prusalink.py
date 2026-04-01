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
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import requests
from requests.auth import HTTPDigestAuth


class PrusaLinkClient:
    """Communicates with a single Prusa printer via the PrusaLink API."""

    def __init__(self, printer_id: str, name: str, host: str,
                 username: str = "maker", password: str = "",
                 model: str = "unknown"):
        self.printer_id = printer_id
        self.name = name
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.model = model
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

        # Current state (updated by polling)
        self.state = {
            "printer_id": printer_id,
            "name": name,
            "model": model,
            "online": False,
            "status": "unknown",      # idle, printing, paused, error, finished
            "temperatures": {
                "nozzle_current": 0.0,
                "nozzle_target": 0.0,
                "bed_current": 0.0,
                "bed_target": 0.0,
            },
            "job": {
                "filename": "",
                "progress": 0.0,
                "time_elapsed_sec": 0,
                "time_remaining_sec": 0,
            },
            "last_updated": None,
            "error": None,
        }

    def _get_auth(self):
        """Return the appropriate auth object."""
        if self.use_basic:
            return self.auth_basic
        return self.auth_digest

    def _request(self, endpoint: str, method: str = "GET",
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
            if hasattr(body, "seek"):
                try:
                    body.seek(0)
                except (AttributeError, OSError):
                    pass
            self.use_basic = True
            resp = req_method(url, auth=self._get_auth(),
                              timeout=timeout, **kwargs)

        resp.raise_for_status()
        return resp

    @staticmethod
    def _result(success, status_code=None, **kwargs):
        # type: (bool, Optional[int], ...) -> dict
        result = {"success": success}
        if status_code is not None:
            result["status_code"] = status_code
        result.update(kwargs)
        return result

    def _file_endpoint(self, filename):
        # type: (str) -> str
        """Build the PrusaLink file endpoint for local storage."""
        quoted = quote(filename, safe="")
        return "/api/v1/files/local/{}".format(quoted)

    def upload_gcode(self, local_path, filename, print_after=False):
        # type: (str, str, bool) -> dict
        """
        Upload a gcode file to the printer via PrusaLink HTTP API.

        PUT /api/v1/files/local/{filename}
        Uses HTTP Digest auth. Retries up to 3 times on failure.

        Args:
            local_path: Path to the gcode file on the Pi/server.
            filename: The filename to use on the printer.
            print_after: If True, include Print-After-Upload header.

        Returns:
            dict with success, status_code, error, etc.
        """
        file_size = os.path.getsize(local_path)
        endpoint = self._file_endpoint(filename)

        last_error = None
        for attempt in range(1, self.upload_retries + 1):
            start_time = time.monotonic()
            print("[UPLOAD] Attempt {}/{} to {}: file={} size={}B "
                  "print_after={}".format(
                      attempt, self.upload_retries, self.printer_id,
                      filename, file_size,
                      "yes" if print_after else "no"))
            try:
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(file_size),
                    "Overwrite": "?1",
                }
                if print_after:
                    headers["Print-After-Upload"] = "?1"

                with open(local_path, "rb") as fh:
                    resp = self._request(
                        endpoint,
                        method="PUT",
                        data=fh,
                        headers=headers,
                        timeout=self.upload_timeout,
                    )

                elapsed = time.monotonic() - start_time
                print("[UPLOAD] Success on {} (attempt {}): file={} "
                      "size={}B status={} elapsed={:.1f}s".format(
                          self.printer_id, attempt, filename,
                          file_size, resp.status_code, elapsed))
                return self._result(
                    True,
                    status_code=200,
                    upload_completed=True,
                    print_started=bool(print_after),
                    attempt=attempt,
                )

            except requests.exceptions.Timeout as exc:
                elapsed = time.monotonic() - start_time
                last_error = ("Upload timed out (attempt {}/{}, "
                              "{:.0f}s)".format(attempt, self.upload_retries,
                                                elapsed))
                print("[UPLOAD] Timeout on {} (attempt {}): file={} "
                      "elapsed={:.1f}s".format(
                          self.printer_id, attempt, filename, elapsed))

            except requests.exceptions.HTTPError as exc:
                elapsed = time.monotonic() - start_time
                sc = exc.response.status_code if exc.response else 502
                last_error = ("Printer rejected upload with HTTP {} "
                              "(attempt {}/{})".format(
                                  sc, attempt, self.upload_retries))
                print("[UPLOAD] HTTP {} on {} (attempt {}): file={} "
                      "elapsed={:.1f}s".format(
                          sc, self.printer_id, attempt, filename, elapsed))

            except requests.exceptions.RequestException as exc:
                elapsed = time.monotonic() - start_time
                last_error = ("Connection error: {} (attempt {}/{})".format(
                    exc, attempt, self.upload_retries))
                print("[UPLOAD] Connection error on {} (attempt {}): "
                      "file={} error={}".format(
                          self.printer_id, attempt, filename, exc))

            except Exception as exc:
                elapsed = time.monotonic() - start_time
                last_error = str(exc)
                print("[UPLOAD] Unexpected error on {} (attempt {}): "
                      "file={} error={}".format(
                          self.printer_id, attempt, filename, exc))

            # Wait before retry (except on last attempt)
            if attempt < self.upload_retries:
                print("[UPLOAD] Retrying in {}s...".format(
                    self.upload_retry_delay))
                time.sleep(self.upload_retry_delay)

        # All retries exhausted
        print("[UPLOAD] All {} attempts failed for {} on {}".format(
            self.upload_retries, filename, self.printer_id))
        return self._result(
            False,
            status_code=504,
            error=("Upload failed after {} attempts: {}".format(
                self.upload_retries, last_error)),
            error_type="upload_timeout",
            upload_completed=False,
            print_started=False,
        )

    def poll(self):
        # type: () -> dict
        """
        Fetch current status from the printer.
        Returns the updated state dict.
        """
        try:
            # --- GET /api/v1/status ---
            status_resp = self._request("/api/v1/status")
            status_data = status_resp.json()

            self.state["online"] = True
            self.state["error"] = None
            self.state["last_updated"] = datetime.now(timezone.utc).isoformat()

            # Parse printer state
            printer_info = status_data.get("printer", {})
            self.state["status"] = printer_info.get("state", "unknown").lower()

            # Temperatures
            self.state["temperatures"]["nozzle_current"] = (
                printer_info.get("temp_nozzle", 0.0)
            )
            self.state["temperatures"]["nozzle_target"] = (
                printer_info.get("target_nozzle", 0.0)
            )
            self.state["temperatures"]["bed_current"] = (
                printer_info.get("temp_bed", 0.0)
            )
            self.state["temperatures"]["bed_target"] = (
                printer_info.get("target_bed", 0.0)
            )

            # Job info
            job_info = status_data.get("job", {})
            if job_info:
                self.state["job"]["filename"] = job_info.get(
                    "file", {}).get("display_name",
                    job_info.get("file", {}).get("name", "")
                )
                self.state["job"]["progress"] = job_info.get(
                    "progress", 0.0
                )
                self.state["job"]["time_elapsed_sec"] = job_info.get(
                    "time_printing", 0
                )
                self.state["job"]["time_remaining_sec"] = job_info.get(
                    "time_remaining", 0
                )
            else:
                self.state["job"] = {
                    "filename": "",
                    "progress": 0.0,
                    "time_elapsed_sec": 0,
                    "time_remaining_sec": 0,
                }

        except requests.exceptions.ConnectionError:
            self.state["online"] = False
            self.state["status"] = "offline"
            self.state["error"] = "Connection failed"
            self.state["last_updated"] = datetime.now(timezone.utc).isoformat()

        except requests.exceptions.HTTPError as e:
            self.state["online"] = True
            self.state["error"] = "HTTP {}".format(e.response.status_code)
            self.state["last_updated"] = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            self.state["online"] = False
            self.state["error"] = str(e)
            self.state["last_updated"] = datetime.now(timezone.utc).isoformat()

        return self.state.copy()

    def get_files(self, storage=None):
        # type: (Optional[str]) -> dict
        """Get file listing from printer storage (all or one storage)."""
        try:
            endpoint = "/api/v1/storage"
            if storage:
                endpoint = "/api/v1/storage/{}".format(storage)
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
