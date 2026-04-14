"""Timeline builder — merges events from every data source.

Events come from six sources and are returned as a single
time-ordered list. Each event has a ``category`` the UI uses to pick
an icon/color, plus whatever contextual fields the row had.
"""

from app.shared.constants import (
    MachineEventType,
    QueueItemStatus,
)

from . import weekly_queries as queries


# Event category constants (kept in this module to avoid coupling the
# whole app to report-specific strings).
CATEGORY_PRODUCTION = "production"
CATEGORY_ERROR = "error"
CATEGORY_MAINTENANCE = "maintenance"
CATEGORY_WORK_ORDER = "work_order"
CATEGORY_ASSIGNMENT = "assignment"
CATEGORY_INVENTORY = "inventory"


def _fmt_hours(duration_sec):
    try:
        hours = (float(duration_sec) / 3600.0) if duration_sec else 0
    except (TypeError, ValueError):
        hours = 0
    if hours <= 0:
        return ""
    return " ({:.1f}h)".format(hours)


def _printer_display(name_map, printer_id, fallback):
    if not printer_id:
        return fallback or ""
    return name_map.get(printer_id, fallback or printer_id)


def _event(timestamp, category, description, **extra):
    base = {
        "timestamp": timestamp,
        "category": category,
        "description": description,
    }
    base.update({k: v for k, v in extra.items() if v not in (None, "")})
    return base


def _history_events(rows, name_map):
    events = []
    for row in rows:
        printer = _printer_display(
            name_map, row.get("printer_id"), row.get("printer_name")
        )
        filename = row.get("filename") or ""
        event_type = row.get("event_type") or ""
        duration_label = _fmt_hours(row.get("duration_sec"))

        if event_type == "print_started":
            description = "Print started: {}".format(filename or "(unknown)")
            category = CATEGORY_PRODUCTION
        elif event_type == "print_complete":
            description = "Print completed: {}{}".format(
                filename or "(unknown)", duration_label
            )
            category = CATEGORY_PRODUCTION
        elif event_type == "print_stopped":
            description = "Print stopped: {}".format(filename or "(unknown)")
            category = CATEGORY_PRODUCTION
        elif event_type == "printer_error":
            description = "Printer error{}".format(
                " during " + filename if filename else ""
            )
            category = CATEGORY_ERROR
        else:
            description = event_type.replace("_", " ").title() or "History event"
            category = CATEGORY_PRODUCTION

        events.append(_event(
            row.get("timestamp"),
            category,
            description,
            printer=printer,
        ))
    return events


def _machine_events(rows, name_map):
    events = []
    for row in rows:
        event_type = row.get("event_type") or ""
        if event_type not in (
            MachineEventType.MAINTENANCE, MachineEventType.CALIBRATION,
        ):
            continue
        details = row.get("details_parsed") or {}
        notes = details.get("notes") if isinstance(details, dict) else None
        description_prefix = (
            "Maintenance" if event_type == MachineEventType.MAINTENANCE
            else "Calibration"
        )
        description = (
            "{}: {}".format(description_prefix, notes)
            if notes else description_prefix
        )
        events.append(_event(
            row.get("event_timestamp"),
            CATEGORY_MAINTENANCE,
            description,
            printer=_printer_display(
                name_map, row.get("printer_id"), row.get("printer_name")
            ),
        ))
    return events


def _work_order_events(created_rows, queue_activity_rows, window):
    events = []
    for wo in created_rows:
        total_parts = wo.get("total_parts") or 0
        customer = wo.get("customer_name") or ""
        description = (
            "Work order {} created for {} ({} parts)".format(
                wo["wo_id"], customer or "(no customer)", total_parts
            )
        )
        events.append(_event(
            wo.get("created_at"),
            CATEGORY_WORK_ORDER,
            description,
            customer=customer,
        ))

    for item in queue_activity_rows:
        completed_at = item.get("completed_at")
        status = item.get("status")
        if not completed_at or completed_at < window.start_iso:
            continue
        if completed_at >= window.next_monday_iso:
            continue
        part = item.get("part_name") or "part"
        wo_id = item.get("wo_id") or ""
        if status == QueueItemStatus.COMPLETED:
            description = "Part completed: {} ({})".format(part, wo_id)
            category = CATEGORY_WORK_ORDER
        elif status in (
            QueueItemStatus.FAILED, QueueItemStatus.UPLOAD_FAILED,
            QueueItemStatus.START_FAILED,
        ):
            description = "Part failed: {} ({})".format(part, wo_id)
            category = CATEGORY_ERROR
        elif status == QueueItemStatus.CANCELLED:
            description = "Part cancelled: {} ({})".format(part, wo_id)
            category = CATEGORY_WORK_ORDER
        else:
            continue
        events.append(_event(
            completed_at,
            category,
            description,
            customer=item.get("customer_name") or "",
            printer=item.get("assigned_printer_name") or "",
        ))
    return events


def _assignment_events(rows, name_map):
    events = []
    for row in rows:
        printer = _printer_display(name_map, row.get("printer_id"), None)
        tool = int(row.get("tool_index") or 0)
        description = (
            "Spool {} assigned to {} tool {}".format(
                row.get("spool_id") or "(unknown)", printer, tool
            )
        )
        events.append(_event(
            row.get("assigned_at"),
            CATEGORY_ASSIGNMENT,
            description,
            printer=printer,
        ))
    return events


def _inventory_events(rows):
    events = []
    for spool in rows:
        description = (
            "Spool {} added: {} {} ({}g)".format(
                spool["id"],
                spool.get("material") or "",
                spool.get("brand") or "",
                spool.get("grams") or 0,
            )
        ).strip()
        # Use midnight UTC on date_ins so string-sort of timeline is stable.
        date_ins = spool.get("date_ins") or ""
        timestamp = "{}T00:00:00+00:00".format(date_ins) if date_ins else ""
        events.append(_event(
            timestamp or "",
            CATEGORY_INVENTORY,
            description,
        ))
    return events


def build_timeline(window, service, cap=500):
    """Return (events, truncated) for the given week.

    ``service`` is a ``WeeklyReportService`` — we use its settings to
    open connections.
    """
    name_map = service._printer_name_map()

    history = queries.print_history_in_week(
        service.history_db_path, window.start_iso, window.next_monday_iso,
        limit=cap * 2,
    )
    machine_logs = queries.machine_log_in_week(
        service.production_db_path, window.start_iso, window.next_monday_iso,
    )
    wo_created = queries.work_orders_created_in_week(
        service.work_order_db_path, window.start_iso, window.next_monday_iso,
    )
    queue_activity = queries.queue_items_activity_in_week(
        service.work_order_db_path, window.start_iso, window.next_monday_iso,
    )
    assignments = queries.assignments_changed_in_week(
        service.assignment_db_path, window.start_iso, window.next_monday_iso,
    )
    spools_added = queries.spools_added_in_week(
        service.inventory_db_path, window.start_date, window.end_date,
    )

    merged = []
    merged.extend(_history_events(history, name_map))
    merged.extend(_machine_events(machine_logs, name_map))
    merged.extend(_work_order_events(wo_created, queue_activity, window))
    merged.extend(_assignment_events(assignments, name_map))
    merged.extend(_inventory_events(spools_added))

    # Stable sort by timestamp; empty strings sort first — push to the end.
    merged.sort(key=lambda e: (not e.get("timestamp"), e.get("timestamp") or ""))

    truncated = len(merged) > cap
    if truncated:
        merged = merged[:cap]
    return merged, truncated
