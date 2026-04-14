"""Week-range helpers for the weekly operations log.

Week boundaries are Monday 00:00:00 UTC through Sunday 23:59:59 UTC.
Accepts strings like ``2026-04-06`` and snaps backward to the nearest
Monday when the caller passes a non-Monday date.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone


@dataclass(frozen=True)
class WeekWindow:
    """Resolved Monday/Sunday pair plus ISO timestamps for SQL filtering."""

    start_date: date   # Monday
    end_date: date     # Sunday

    @property
    def start_iso(self) -> str:
        """Monday 00:00:00 UTC as ISO string."""
        return datetime.combine(
            self.start_date, time.min, tzinfo=timezone.utc
        ).isoformat()

    @property
    def end_iso(self) -> str:
        """Sunday 23:59:59.999999 UTC as ISO string (inclusive upper bound)."""
        return datetime.combine(
            self.end_date, time.max, tzinfo=timezone.utc
        ).isoformat()

    @property
    def next_monday_iso(self) -> str:
        """The Monday 00:00:00 UTC *after* this week — exclusive upper bound."""
        return datetime.combine(
            self.end_date + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        ).isoformat()

    @property
    def total_hours(self) -> float:
        """Total hours in the week (168.0 unless clamped — always 168)."""
        return 7 * 24.0

    def to_dict(self) -> dict:
        return {
            "week_start": self.start_date.isoformat(),
            "week_end": self.end_date.isoformat(),
        }


def _parse_date(value) -> date:
    """Parse a date-like value as an ISO ``YYYY-MM-DD``.

    Accepts ``date`` and ``datetime`` directly, or a string in
    ``YYYY-MM-DD`` (preferred) / full ISO format.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        raise ValueError("empty date value")
    text = str(value).strip()
    # Accept both date and datetime shapes
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(
            "week_start must be ISO YYYY-MM-DD, got {}".format(text)
        ) from exc


def _monday_of(target: date) -> date:
    """Snap backward to the Monday of the week containing ``target``.

    Python's ``date.weekday()`` returns 0 for Monday and 6 for Sunday.
    """
    return target - timedelta(days=target.weekday())


def resolve_week(week_start=None) -> WeekWindow:
    """Resolve a week window from a user-supplied start date.

    - ``None`` / empty: use the current week (UTC).
    - Non-Monday dates: snap backward to the Monday of that week.
    """
    if week_start is None or (isinstance(week_start, str) and not week_start.strip()):
        today = datetime.now(timezone.utc).date()
        monday = _monday_of(today)
    else:
        parsed = _parse_date(week_start)
        monday = _monday_of(parsed)

    sunday = monday + timedelta(days=6)
    return WeekWindow(start_date=monday, end_date=sunday)


def is_future_week(window: WeekWindow) -> bool:
    """True when the window's Monday is strictly after the current Monday."""
    current_monday = _monday_of(datetime.now(timezone.utc).date())
    return window.start_date > current_monday
