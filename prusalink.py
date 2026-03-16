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
    DELETE /api/v1/job     - stop current job

Authentication:
    PrusaLink uses HTTP Digest auth with username "maker"
    and the API key/password from the printer's settings.
"""

from datetime import datetime, timezone

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
        self.base_url = f"http://{self.host}"
        self.timeout = 10

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
        url = f"{self.base_url}{endpoint}"
        req_method = getattr(requests, method.lower())

        # Try primary auth method
        resp = req_method(url, auth=self._get_auth(),
                          timeout=self.timeout, **kwargs)

        # If Digest fails with 401, try Basic
        if resp.status_code == 401 and not self.use_basic:
            self.use_basic = True
            resp = req_method(url, auth=self._get_auth(),
                              timeout=self.timeout, **kwargs)

        resp.raise_for_status()
        return resp

    def poll(self) -> dict:
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
            self.state["error"] = f"HTTP {e.response.status_code}"
            self.state["last_updated"] = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            self.state["online"] = False
            self.state["error"] = str(e)
            self.state["last_updated"] = datetime.now(timezone.utc).isoformat()

        return self.state.copy()

    def get_files(self, storage: str = None) -> dict:
        """Get file listing from printer storage (all or one storage)."""
        try:
            endpoint = "/api/v1/storage"
            if storage:
                endpoint = f"/api/v1/storage/{storage}"
            resp = self._request(endpoint)
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def upload_gcode(self, file_data, filename: str,
                     print_after: bool = False) -> dict:
        """
        Upload a gcode file to the printer.

        PrusaLink PUT /api/v1/files/{storage}/{path}
        Headers:
            Print-After-Upload: ?1 (to start printing immediately)
            Overwrite: ?1 (to overwrite existing files)
            Content-Type: application/octet-stream
        """
        try:
            endpoint = f"/api/v1/files/usb/{filename}"
            headers = {
                "Content-Type": "application/octet-stream",
                "Overwrite": "?1",
            }
            if print_after:
                headers["Print-After-Upload"] = "?1"

            resp = self._request(
                endpoint,
                method="PUT",
                data=file_data,
                headers=headers,
            )
            return {"success": True, "status_code": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_job_details(self) -> dict:
        """
        Fetch detailed job information from GET /api/v1/job.
        Returns metadata including filament usage estimates,
        layer height, nozzle diameter, temperatures, etc.
        """
        try:
            resp = self._request("/api/v1/job")
            data = resp.json()
            # Normalize the response into a flat dict of useful fields
            file_info = data.get("file", {})
            meta = file_info.get("meta", {}) or {}
            result = {
                "file_name": file_info.get("name", ""),
                "file_display_name": file_info.get("display_name",
                                                    file_info.get("name", "")),
                "filament_type": meta.get("filament_type", ""),
                "filament_used_g": meta.get("filament_used_g", 0),
                "filament_used_mm": meta.get("filament_used_mm", 0),
                "layer_height": meta.get("layer_height", None),
                "nozzle_diameter": meta.get("nozzle_diameter", None),
                "fill_density": meta.get("fill_density", None),
                "nozzle_temp": meta.get("nozzle_temp", None),
                "bed_temp": meta.get("bed_temp", None),
                "estimated_time_sec": meta.get("estimated_print_time",
                                               data.get("time_remaining", 0)),
            }
            return result
        except Exception as e:
            return {"error": str(e)}

    def get_camera_snapshot(self) -> bytes | None:
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

    def stop_job(self) -> dict:
        """Stop the current print job via DELETE /api/v1/job."""
        try:
            resp = self._request("/api/v1/job", method="DELETE")
            return {"success": True, "status_code": resp.status_code}
        except Exception as e:
            return {"success": False, "error": str(e)}
