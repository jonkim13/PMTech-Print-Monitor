"""Status transition classification helpers."""

PRINT_COMPLETE = "print_complete"
PRINT_STARTED = "print_started"
PRINTER_ERROR = "printer_error"


def is_print_complete_transition(previous_status, new_status):
    """Return whether a transition should be treated as print completion."""
    return new_status == "finished" or (
        previous_status == "printing" and new_status == "idle"
    )


def is_print_started_transition(previous_status, new_status):
    """Return whether a transition should be treated as print start."""
    return new_status == "printing" and previous_status != "printing"


def is_printer_error_transition(new_status):
    """Return whether a status should be treated as a printer error event."""
    return new_status in ("error",)


def detect_status_transition(previous_status, new_status):
    """Classify a status change into the existing event types."""
    if previous_status == new_status:
        return None
    if is_print_complete_transition(previous_status, new_status):
        return PRINT_COMPLETE
    if is_print_started_transition(previous_status, new_status):
        return PRINT_STARTED
    if is_printer_error_transition(new_status):
        return PRINTER_ERROR
    return None


def build_transition_event(printer_id, previous_status, state, timestamp):
    """Build the existing event payload shape for a status transition."""
    return {
        "timestamp": timestamp,
        "printer_id": printer_id,
        "printer_name": state["name"],
        "from_status": previous_status,
        "to_status": state["status"],
        "filename": state["job"]["filename"],
        "duration_sec": 0,
    }
