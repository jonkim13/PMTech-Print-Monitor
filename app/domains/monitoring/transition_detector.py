"""Status transition classification helpers."""

from app.shared.constants import EventType, PrinterStatus


PRINT_COMPLETE = EventType.PRINT_COMPLETE
PRINT_STARTED = EventType.PRINT_STARTED
PRINTER_ERROR = EventType.PRINTER_ERROR


def is_print_complete_transition(previous_status, new_status):
    """Return whether a transition should be treated as print completion."""
    return new_status == PrinterStatus.FINISHED or (
        previous_status == PrinterStatus.PRINTING
        and new_status == PrinterStatus.IDLE
    )


def is_print_started_transition(previous_status, new_status):
    """Return whether a transition should be treated as print start."""
    return (
        new_status == PrinterStatus.PRINTING
        and previous_status != PrinterStatus.PRINTING
    )


def is_printer_error_transition(new_status):
    """Return whether a status should be treated as a printer error event."""
    return new_status in (PrinterStatus.ERROR,)


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
