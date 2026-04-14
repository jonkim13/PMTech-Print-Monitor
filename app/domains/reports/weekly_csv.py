"""Multi-section CSV renderer for the weekly operations log.

The output is a single text blob with section headers so a
non-technical auditor can open it in any spreadsheet tool and see the
whole week at a glance.
"""

import csv
import io
from datetime import datetime, timezone


def _writerow(writer, row):
    writer.writerow(["" if value is None else value for value in row])


def _section_header(writer, title):
    _writerow(writer, [])
    _writerow(writer, ["=== {} ===".format(title)])


def build_weekly_csv(window, summary, production, materials,
                     equipment, work_orders, timeline) -> str:
    """Render the whole report as one multi-section CSV string."""
    output = io.StringIO()
    writer = csv.writer(output)

    _writerow(writer, ["WEEKLY OPERATIONS LOG"])
    _writerow(writer, ["Week", "{} to {}".format(
        window.start_date.isoformat(), window.end_date.isoformat()
    )])
    _writerow(writer, ["Generated", datetime.now(timezone.utc).isoformat()])

    _render_summary(writer, summary)
    _render_production(writer, production)
    _render_materials(writer, materials)
    _render_equipment(writer, equipment)
    _render_work_orders(writer, work_orders)
    _render_timeline(writer, timeline)

    return output.getvalue()


def _render_summary(writer, summary):
    _section_header(writer, "PRODUCTION SUMMARY")
    _writerow(writer, ["Metric", "Value"])

    prod = summary.get("production", {})
    mat = summary.get("materials", {})
    wo = summary.get("work_orders", {})
    eq = summary.get("equipment", {})

    rows = [
        ("Prints Completed", prod.get("prints_completed", 0)),
        ("Prints Failed", prod.get("prints_failed", 0)),
        ("Prints Cancelled", prod.get("prints_cancelled", 0)),
        ("Total Prints", prod.get("total_prints", 0)),
        ("Success Rate %", prod.get("success_rate", 0)),
        ("Total Print Hours", prod.get("total_print_hours", 0)),
        ("Unique Files Printed", prod.get("unique_files_printed", 0)),
        ("Total Grams Consumed", mat.get("total_grams_consumed", 0)),
        ("Spools Used", mat.get("spools_used_count", 0)),
        ("Work Orders Created", wo.get("created", 0)),
        ("Work Orders Completed", wo.get("completed", 0)),
        ("Work Orders In Progress", wo.get("in_progress", 0)),
        ("Parts Completed", wo.get("parts_completed", 0)),
        ("Parts Failed", wo.get("parts_failed", 0)),
        ("Printers Active", eq.get("printers_active", 0)),
        ("Errors Logged", eq.get("errors_logged", 0)),
        ("Maintenance Events", eq.get("maintenance_events", 0)),
    ]
    for label, value in rows:
        _writerow(writer, [label, value])

    by_material = mat.get("by_material") or []
    if by_material:
        _writerow(writer, [])
        _writerow(writer, ["Material Breakdown"])
        _writerow(writer, ["Material", "Grams", "Prints"])
        for item in by_material:
            _writerow(writer, [
                item.get("material", ""),
                item.get("grams", 0),
                item.get("prints", 0),
            ])


def _render_production(writer, production):
    _section_header(writer, "PRODUCTION DETAIL")
    _writerow(writer, [
        "Job ID", "Printer", "File", "Status",
        "Started", "Completed", "Duration (h)",
        "Operator", "Material", "Spool ID",
        "Grams Used", "QC Outcome", "Notes",
    ])
    jobs = production.get("jobs") or []
    for job in jobs:
        _writerow(writer, [
            job.get("job_id", ""),
            job.get("printer_name", ""),
            job.get("file_name", ""),
            job.get("status", ""),
            job.get("started_at", ""),
            job.get("completed_at", ""),
            job.get("print_duration_hours", 0),
            job.get("operator_initials", ""),
            job.get("material", ""),
            job.get("spool_id", ""),
            job.get("filament_used_g", 0),
            job.get("outcome", ""),
            job.get("notes", ""),
        ])


def _render_materials(writer, materials):
    _section_header(writer, "MATERIAL USAGE")
    _writerow(writer, [
        "Spool ID", "Material", "Brand",
        "Total Grams Used", "Prints Count", "Printers",
    ])
    for row in materials.get("usage") or []:
        printers = ", ".join(row.get("printers_used") or [])
        _writerow(writer, [
            row.get("spool_id", ""),
            row.get("material", ""),
            row.get("brand", ""),
            row.get("total_grams_used", 0),
            row.get("prints_count", 0),
            printers,
        ])

    changes = materials.get("inventory_changes") or {}
    added = changes.get("spools_added") or []
    if added:
        _writerow(writer, [])
        _writerow(writer, ["Spools Added This Week"])
        _writerow(writer, [
            "Spool ID", "Material", "Brand", "Date Added", "Grams",
        ])
        for spool in added:
            _writerow(writer, [
                spool.get("spool_id", ""),
                spool.get("material", ""),
                spool.get("brand", ""),
                spool.get("date_added", ""),
                spool.get("grams", 0),
            ])
    assignments = changes.get("assignments_changed") or []
    if assignments:
        _writerow(writer, [])
        _writerow(writer, ["Spool Assignments Changed This Week"])
        _writerow(writer, [
            "Spool ID", "Printer", "Tool", "Action", "Date",
        ])
        for change in assignments:
            _writerow(writer, [
                change.get("spool_id", ""),
                change.get("printer", ""),
                change.get("tool", 0),
                change.get("action", ""),
                change.get("date", ""),
            ])


def _render_equipment(writer, equipment):
    _section_header(writer, "EQUIPMENT HEALTH")
    _writerow(writer, [
        "Printer", "Prints Completed", "Prints Failed",
        "Print Hours", "Utilization %",
        "Errors", "Maintenance Events",
    ])
    for printer in equipment.get("printers") or []:
        _writerow(writer, [
            printer.get("printer_name", printer.get("printer_id", "")),
            printer.get("prints_completed", 0),
            printer.get("prints_failed", 0),
            printer.get("print_hours", 0),
            printer.get("utilization_pct", 0),
            len(printer.get("errors") or []),
            len(printer.get("maintenance") or []),
        ])


def _render_work_orders(writer, work_orders):
    _section_header(writer, "WORK ORDER ACTIVITY")
    parts = work_orders.get("parts_summary") or {}
    _writerow(writer, [
        "Parts Completed", parts.get("completed_this_week", 0),
    ])
    _writerow(writer, [
        "Parts Failed", parts.get("failed_this_week", 0),
    ])
    _writerow(writer, [
        "Parts Cancelled", parts.get("cancelled_this_week", 0),
    ])
    _writerow(writer, [
        "Parts Started", parts.get("started_this_week", 0),
    ])
    _writerow(writer, [])
    _writerow(writer, [
        "WO ID", "Customer", "Created", "Completed", "Status",
        "Parts Total", "Parts Completed", "Parts Failed", "Bucket",
    ])
    for label, key in (
        ("created", "orders_created"),
        ("completed", "orders_completed"),
        ("active", "orders_active"),
    ):
        for wo in work_orders.get(key) or []:
            _writerow(writer, [
                wo.get("wo_id", ""),
                wo.get("customer_name", ""),
                wo.get("created_at", ""),
                wo.get("completed_at", ""),
                wo.get("status", ""),
                wo.get("total_parts", 0),
                wo.get("parts_completed", 0),
                wo.get("parts_failed", 0),
                label,
            ])


def _render_timeline(writer, timeline):
    _section_header(writer, "EVENT TIMELINE")
    _writerow(writer, [
        "Timestamp", "Category", "Description",
        "Printer", "Operator", "Customer",
    ])
    for event in timeline.get("events") or []:
        _writerow(writer, [
            event.get("timestamp", ""),
            event.get("category", ""),
            event.get("description", ""),
            event.get("printer", ""),
            event.get("operator", ""),
            event.get("customer", ""),
        ])
    if timeline.get("truncated"):
        _writerow(writer, [])
        _writerow(writer, [
            "Note",
            "Timeline truncated to {} events — some events are not shown.".format(
                timeline.get("cap", 500)
            ),
        ])
