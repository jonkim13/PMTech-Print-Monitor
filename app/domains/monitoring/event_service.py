"""Centralized in-memory event buffer for printer transition events."""

import threading
from collections import deque
from datetime import datetime


class EventService:
    """Owns pending_events and job_history lists previously on PrintFarmManager.

    Both buffers are bounded to prevent unbounded memory growth.
    """

    PENDING_EVENTS_MAX = 1000
    JOB_HISTORY_MAX = 500

    def __init__(self):
        self._lock = threading.Lock()
        self._pending_events = deque(maxlen=self.PENDING_EVENTS_MAX)
        self._job_history = deque(maxlen=self.JOB_HISTORY_MAX)

    # -- pending events ----------------------------------------------------

    def add_event(self, event: dict) -> None:
        """Append an event to the pending buffer (bounded, drops oldest)."""
        with self._lock:
            self._pending_events.append(event)

    def is_duplicate_pending_event(self, event: dict) -> bool:
        """Check if a similar event already exists in pending_events."""
        for existing in self._pending_events:
            if (existing.get("printer_id") == event.get("printer_id")
                    and existing.get("type") == event.get("type")):
                try:
                    existing_time = datetime.fromisoformat(
                        existing["timestamp"])
                    event_time = datetime.fromisoformat(
                        event["timestamp"])
                    if abs((event_time - existing_time
                            ).total_seconds()) < 60:
                        return True
                except (ValueError, KeyError):
                    pass
        return False

    def peek_events(self) -> list:
        """Return a copy of pending events without clearing them."""
        with self._lock:
            return list(self._pending_events)

    def consume_events(self) -> list:
        """Return and clear all pending events."""
        with self._lock:
            events = list(self._pending_events)
            self._pending_events.clear()
        return events

    # -- job history -------------------------------------------------------

    def add_job_history(self, entry: dict) -> None:
        """Append an entry to the job history buffer (bounded, drops oldest)."""
        with self._lock:
            self._job_history.append(entry)

    def get_job_history(self) -> list:
        """Return a copy of the in-memory job history."""
        with self._lock:
            return list(self._job_history)
