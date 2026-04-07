"""PrusaLink status payload mapping helpers."""

from datetime import datetime, timezone


def _iso_timestamp(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def build_printer_state(printer_id, name, model):
    """Build the frontend-facing printer state shape."""
    return {
        "printer_id": printer_id,
        "name": name,
        "model": model,
        "online": False,
        "status": "unknown",
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


def normalize_printer_status(status):
    """Normalize a PrusaLink status value using the existing behavior."""
    return status.lower()


def apply_status_payload(state, status_data, updated_at=None):
    """Apply a successful PrusaLink status response to an existing state."""
    state["online"] = True
    state["error"] = None
    state["last_updated"] = _iso_timestamp(updated_at)

    printer_info = status_data.get("printer", {})
    state["status"] = normalize_printer_status(
        printer_info.get("state", "unknown")
    )

    state["temperatures"]["nozzle_current"] = (
        printer_info.get("temp_nozzle", 0.0)
    )
    state["temperatures"]["nozzle_target"] = (
        printer_info.get("target_nozzle", 0.0)
    )
    state["temperatures"]["bed_current"] = (
        printer_info.get("temp_bed", 0.0)
    )
    state["temperatures"]["bed_target"] = (
        printer_info.get("target_bed", 0.0)
    )

    job_info = status_data.get("job", {})
    if job_info:
        state["job"]["filename"] = job_info.get(
            "file", {}
        ).get(
            "display_name",
            job_info.get("file", {}).get("name", ""),
        )
        state["job"]["progress"] = job_info.get("progress", 0.0)
        state["job"]["time_elapsed_sec"] = job_info.get("time_printing", 0)
        state["job"]["time_remaining_sec"] = job_info.get(
            "time_remaining", 0
        )
    else:
        state["job"] = {
            "filename": "",
            "progress": 0.0,
            "time_elapsed_sec": 0,
            "time_remaining_sec": 0,
        }
    return state


def mark_connection_failed(state, updated_at=None):
    """Apply the existing connection-failure state mapping."""
    state["online"] = False
    state["status"] = "offline"
    state["error"] = "Connection failed"
    state["last_updated"] = _iso_timestamp(updated_at)
    return state


def mark_http_error(state, status_code, updated_at=None):
    """Apply the existing HTTP-error state mapping."""
    state["online"] = True
    state["error"] = "HTTP {}".format(status_code)
    state["last_updated"] = _iso_timestamp(updated_at)
    return state


def mark_poll_error(state, error, updated_at=None):
    """Apply the existing generic poll-error state mapping."""
    state["online"] = False
    state["error"] = str(error)
    state["last_updated"] = _iso_timestamp(updated_at)
    return state
