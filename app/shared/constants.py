"""Shared status constants used across the application.

These string values must match the raw strings already stored in the
database and returned by the PrusaLink polling layer.  Changing a value
here would be a data-level migration, not just a rename.
"""


class PrinterStatus:
    """Printer states reported by the polling layer."""
    IDLE = "idle"
    PRINTING = "printing"
    FINISHED = "finished"
    ERROR = "error"
    UNKNOWN = "unknown"


class QueueItemStatus:
    """Queue item / queue job lifecycle states."""
    QUEUED = "queued"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    STARTING = "starting"
    PRINTING = "printing"
    COMPLETED = "completed"
    FAILED = "failed"
    UPLOAD_FAILED = "upload_failed"
    START_FAILED = "start_failed"
    CANCELLED = "cancelled"


class ProductionJobStatus:
    """Production print_jobs.status values."""
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class EventType:
    """In-memory / history event types (transition_detector labels)."""
    PRINT_STARTED = "print_started"
    PRINT_COMPLETE = "print_complete"
    PRINTER_ERROR = "printer_error"
    PRINT_STOPPED = "print_stopped"


class MachineEventType:
    """Production machine_log event_type values."""
    PRINT_START = "print_start"
    PRINT_COMPLETE = "print_complete"
    PRINT_FAIL = "print_fail"
    PRINT_STOP = "print_stop"
    MAINTENANCE = "maintenance"
    CALIBRATION = "calibration"
