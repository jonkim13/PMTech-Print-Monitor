"""Weekly operations log service.

Coordinates queries across the production, monitoring, inventory,
assignment, and work-order databases into a single read-only weekly
report for ISO 9001 clause 9.1 (monitoring & evaluation).

All cross-database joins happen in Python — each repository has its
own SQLite file and cannot be JOIN-ed directly.
"""

from collections import defaultdict

from app.shared.constants import (
    MachineEventType,
    ProductionJobStatus,
    QueueItemStatus,
)

from . import weekly_queries as queries
from .week_window import WeekWindow, is_future_week, resolve_week


COMPLETED_STATUSES = (ProductionJobStatus.COMPLETED,)
FAILED_STATUSES = (ProductionJobStatus.FAILED,)
CANCELLED_STATUSES = (ProductionJobStatus.STOPPED,)
ERROR_HISTORY_EVENTS = ("printer_error",)
MAINTENANCE_EVENTS = (
    MachineEventType.MAINTENANCE,
    MachineEventType.CALIBRATION,
)
QUEUE_FAILURE_STATUSES = (
    QueueItemStatus.FAILED,
    QueueItemStatus.UPLOAD_FAILED,
    QueueItemStatus.START_FAILED,
)


def _round_to(value, digits=1):
    try:
        return round(float(value or 0), digits)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class WeeklyReportService:
    """Read-only weekly operations log.

    The service is stateless; callers pass ``week_start`` on each call
    so the same instance can serve any historical week.
    """

    TIMELINE_EVENT_CAP = 500

    def __init__(self, settings, farm_manager=None):
        """Wire the service to the project's database paths.

        ``settings`` provides the concrete file paths so the service
        opens its own connections per repository without reaching into
        the existing repository objects (which keep their own
        connections scoped to writes).
        """
        self.production_db_path = settings.production_db_path
        self.history_db_path = settings.history_db_path
        self.inventory_db_path = settings.inventory_db_path
        self.assignment_db_path = settings.assignment_db_path
        self.work_order_db_path = settings.work_order_db_path
        self.farm_manager = farm_manager

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _window(self, week_start) -> WeekWindow:
        return resolve_week(week_start)

    def _printer_name_map(self):
        """Return {printer_id: display_name} via the farm manager."""
        if not self.farm_manager:
            return {}
        result = {}
        for pid, data in getattr(self.farm_manager, "printers", {}).items():
            client = data.get("client")
            if client is not None:
                result[pid] = getattr(client, "name", pid) or pid
        return result

    def _printer_ids(self):
        if not self.farm_manager:
            return []
        return list(getattr(self.farm_manager, "printers", {}).keys())

    def _display_printer(self, printer_id, fallback):
        return (
            self._printer_name_map().get(printer_id)
            or fallback
            or printer_id
        )

    def _spool_index(self):
        """Return {spool_id: spool_dict} for the whole inventory."""
        spools = queries.filament_all(self.inventory_db_path)
        return {s["id"]: s for s in spools}

    def window_dict(self, week_start) -> dict:
        """Return just the window info — useful for HEAD checks."""
        window = self._window(week_start)
        return {
            **window.to_dict(),
            "is_future": is_future_week(window),
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_summary(self, week_start=None) -> dict:
        """High-level weekly stats."""
        window = self._window(week_start)
        jobs = queries.print_jobs_in_week(
            self.production_db_path, window.start_iso, window.next_monday_iso
        )
        usage = queries.material_usage_in_week(
            self.production_db_path, window.start_iso, window.next_monday_iso
        )
        machine_logs = queries.machine_log_in_week(
            self.production_db_path, window.start_iso, window.next_monday_iso
        )
        history = queries.print_history_in_week(
            self.history_db_path, window.start_iso, window.next_monday_iso
        )
        wo_created = queries.work_orders_created_in_week(
            self.work_order_db_path, window.start_iso, window.next_monday_iso
        )
        wo_completed = queries.work_orders_completed_in_week(
            self.work_order_db_path, window.start_iso, window.next_monday_iso
        )
        wo_active = queries.work_orders_active_during_week(
            self.work_order_db_path, window.start_iso, window.next_monday_iso
        )
        queue_activity = queries.queue_items_activity_in_week(
            self.work_order_db_path, window.start_iso, window.next_monday_iso
        )

        # Production counts
        completed = sum(1 for j in jobs if j["status"] in COMPLETED_STATUSES)
        failed = sum(1 for j in jobs if j["status"] in FAILED_STATUSES)
        cancelled = sum(1 for j in jobs if j["status"] in CANCELLED_STATUSES)
        total = len(jobs)
        finished = completed + failed
        success_rate = (
            round(completed / finished * 100, 1) if finished else 0.0
        )
        print_seconds = sum(
            _safe_int(j.get("print_duration_sec")) for j in jobs
        )
        unique_files = len({j["file_name"] for j in jobs if j.get("file_name")})

        # Materials
        total_grams = sum(
            float(u.get("grams_used") or 0) for u in usage
        )
        spools_used = len({
            u["spool_id"] for u in usage if u.get("spool_id")
        })
        by_material = defaultdict(lambda: {"grams": 0.0, "prints": set()})
        spool_index = self._spool_index()
        for row in usage:
            spool = spool_index.get(row.get("spool_id")) or {}
            material = spool.get("material") or "Unknown"
            by_material[material]["grams"] += float(row.get("grams_used") or 0)
            if row.get("job_id"):
                by_material[material]["prints"].add(row["job_id"])
        by_material_list = sorted([
            {
                "material": material,
                "grams": _round_to(info["grams"], 1),
                "prints": len(info["prints"]),
            }
            for material, info in by_material.items()
        ], key=lambda m: m["grams"], reverse=True)

        # Work orders
        wo_active_only = [
            wo for wo in wo_active
            if wo["status"] not in (
                QueueItemStatus.COMPLETED, QueueItemStatus.CANCELLED,
            )
        ]
        parts_completed = sum(
            1 for q in queue_activity
            if q["status"] == QueueItemStatus.COMPLETED
            and self._in_window(q.get("completed_at"), window)
        )
        parts_failed = sum(
            1 for q in queue_activity
            if q["status"] in QUEUE_FAILURE_STATUSES
            and self._in_window(q.get("completed_at"), window)
        )

        # Equipment
        printers_active = len({
            j["printer_id"] for j in jobs if j.get("printer_id")
        })
        maintenance_events = sum(
            1 for m in machine_logs
            if m["event_type"] in MAINTENANCE_EVENTS
        )
        errors_logged = sum(
            1 for h in history
            if h["event_type"] in ERROR_HISTORY_EVENTS
        )

        return {
            **window.to_dict(),
            "is_future": is_future_week(window),
            "production": {
                "prints_completed": completed,
                "prints_failed": failed,
                "prints_cancelled": cancelled,
                "total_prints": total,
                "success_rate": success_rate,
                "total_print_hours": _round_to(print_seconds / 3600.0, 1),
                "unique_files_printed": unique_files,
            },
            "materials": {
                "total_grams_consumed": _round_to(total_grams, 1),
                "spools_used_count": spools_used,
                "by_material": by_material_list,
            },
            "work_orders": {
                "created": len(wo_created),
                "completed": len(wo_completed),
                "in_progress": len(wo_active_only),
                "parts_completed": parts_completed,
                "parts_failed": parts_failed,
            },
            "equipment": {
                "printers_active": printers_active,
                "total_uptime_hours": _round_to(print_seconds / 3600.0, 1),
                "errors_logged": errors_logged,
                "maintenance_events": maintenance_events,
            },
        }

    @staticmethod
    def _in_window(iso_value, window: WeekWindow) -> bool:
        if not iso_value:
            return False
        # String compare works for ISO timestamps when both have the
        # same timezone offset. Our schema uses UTC so this is safe.
        return window.start_iso <= str(iso_value) < window.next_monday_iso

    # ------------------------------------------------------------------
    # Production detail
    # ------------------------------------------------------------------

    def get_production(self, week_start=None) -> dict:
        """Detailed job log for the week with filament-usage enrichment."""
        window = self._window(week_start)
        jobs = queries.print_jobs_in_week(
            self.production_db_path, window.start_iso, window.next_monday_iso
        )
        # Build {job_id: [usage_rows]} so we can surface per-tool grams.
        usage = queries.material_usage_in_week(
            self.production_db_path, window.start_iso, window.next_monday_iso
        )
        usage_by_job = defaultdict(list)
        for row in usage:
            if row.get("job_id"):
                usage_by_job[row["job_id"]].append(row)

        spool_index = self._spool_index()
        name_map = self._printer_name_map()

        output = []
        for job in jobs:
            job_usage = usage_by_job.get(job["job_id"]) or []
            grams_used = float(job.get("filament_used_g") or 0)
            if not grams_used and job_usage:
                grams_used = sum(
                    float(u.get("grams_used") or 0) for u in job_usage
                )
            material = (
                job.get("spool_material")
                or job.get("filament_type")
                or (
                    (spool_index.get(job.get("spool_id")) or {}).get("material")
                    if job.get("spool_id") else None
                )
                or ""
            )
            output.append({
                "job_id": job["job_id"],
                "printer_id": job["printer_id"],
                "printer_name": name_map.get(
                    job["printer_id"], job.get("printer_name")
                ),
                "file_name": job.get("file_display_name") or job.get("file_name"),
                "status": job.get("status"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "print_duration_hours": _round_to(
                    _safe_int(job.get("print_duration_sec")) / 3600.0, 2
                ),
                "operator_initials": job.get("operator_initials") or "",
                "spool_id": job.get("spool_id") or "",
                "material": material,
                "filament_used_g": _round_to(grams_used, 1),
                "outcome": job.get("outcome") or "unknown",
                "notes": job.get("notes") or "",
            })

        return {
            **window.to_dict(),
            "jobs": output,
        }

    # ------------------------------------------------------------------
    # Materials
    # ------------------------------------------------------------------

    def get_materials(self, week_start=None) -> dict:
        """Spool-level usage + inventory changes for the week."""
        window = self._window(week_start)
        usage = queries.material_usage_in_week(
            self.production_db_path, window.start_iso, window.next_monday_iso
        )
        spool_index = self._spool_index()
        name_map = self._printer_name_map()

        by_spool = defaultdict(lambda: {
            "spool_id": "", "material": "", "brand": "",
            "grams": 0.0, "prints": set(), "printers": set(),
        })
        for row in usage:
            spool_id = row.get("spool_id") or ""
            bucket = by_spool[spool_id]
            spool = spool_index.get(spool_id) or {}
            bucket["spool_id"] = spool_id
            bucket["material"] = spool.get("material") or ""
            bucket["brand"] = spool.get("brand") or ""
            bucket["grams"] += float(row.get("grams_used") or 0)
            if row.get("job_id"):
                bucket["prints"].add(row["job_id"])
            printer_id = row.get("printer_id")
            if printer_id:
                bucket["printers"].add(
                    name_map.get(printer_id, row.get("printer_name") or printer_id)
                )

        usage_rows = [
            {
                "spool_id": info["spool_id"] or "(unknown)",
                "material": info["material"],
                "brand": info["brand"],
                "total_grams_used": _round_to(info["grams"], 1),
                "prints_count": len(info["prints"]),
                "printers_used": sorted(info["printers"]),
            }
            for info in by_spool.values()
        ]
        usage_rows.sort(key=lambda r: r["total_grams_used"], reverse=True)

        spools_added = [
            {
                "spool_id": spool["id"],
                "material": spool.get("material") or "",
                "brand": spool.get("brand") or "",
                "date_added": spool.get("date_ins") or "",
                "grams": _safe_int(spool.get("grams")),
            }
            for spool in queries.spools_added_in_week(
                self.inventory_db_path,
                window.start_date, window.end_date,
            )
        ]
        assignments = [
            {
                "spool_id": a["spool_id"],
                "printer": name_map.get(a["printer_id"], a["printer_id"]),
                "tool": int(a.get("tool_index") or 0),
                "action": "assigned",
                "date": a["assigned_at"],
            }
            for a in queries.assignments_changed_in_week(
                self.assignment_db_path,
                window.start_iso, window.next_monday_iso,
            )
        ]

        return {
            **window.to_dict(),
            "usage": usage_rows,
            "inventory_changes": {
                "spools_added": spools_added,
                "assignments_changed": assignments,
            },
        }

    # ------------------------------------------------------------------
    # Equipment
    # ------------------------------------------------------------------

    def get_equipment(self, week_start=None) -> dict:
        """Per-printer activity and health for the week."""
        window = self._window(week_start)
        jobs = queries.print_jobs_in_week(
            self.production_db_path, window.start_iso, window.next_monday_iso
        )
        machine_logs = queries.machine_log_in_week(
            self.production_db_path, window.start_iso, window.next_monday_iso
        )
        history = queries.print_history_in_week(
            self.history_db_path, window.start_iso, window.next_monday_iso
        )
        name_map = self._printer_name_map()
        known_ids = set(self._printer_ids())

        stats = defaultdict(lambda: {
            "prints_completed": 0, "prints_failed": 0,
            "print_seconds": 0,
            "errors": [], "maintenance": [],
        })
        for job in jobs:
            pid = job.get("printer_id")
            if not pid:
                continue
            known_ids.add(pid)
            bucket = stats[pid]
            if job["status"] in COMPLETED_STATUSES:
                bucket["prints_completed"] += 1
            elif job["status"] in FAILED_STATUSES:
                bucket["prints_failed"] += 1
            bucket["print_seconds"] += _safe_int(job.get("print_duration_sec"))

        for entry in history:
            if entry["event_type"] not in ERROR_HISTORY_EVENTS:
                continue
            pid = entry.get("printer_id") or ""
            if pid:
                known_ids.add(pid)
            stats[pid]["errors"].append({
                "timestamp": entry.get("timestamp"),
                "event_type": entry.get("event_type"),
                "filename": entry.get("filename") or "",
            })

        for entry in machine_logs:
            if entry["event_type"] not in MAINTENANCE_EVENTS:
                continue
            pid = entry.get("printer_id") or ""
            if pid:
                known_ids.add(pid)
            details = entry.get("details_parsed") or {}
            notes = details.get("notes") if isinstance(details, dict) else None
            stats[pid]["maintenance"].append({
                "timestamp": entry.get("event_timestamp"),
                "event_type": entry.get("event_type"),
                "details": notes or "",
            })

        total_hours = window.total_hours or 1.0
        printers = []
        for pid in sorted(known_ids):
            info = stats.get(pid, {})
            print_hours = _round_to(
                info.get("print_seconds", 0) / 3600.0, 1
            )
            printers.append({
                "printer_id": pid,
                "printer_name": name_map.get(pid, pid),
                "prints_completed": info.get("prints_completed", 0),
                "prints_failed": info.get("prints_failed", 0),
                "print_hours": print_hours,
                "utilization_pct": _round_to(
                    print_hours / total_hours * 100, 1
                ),
                "errors": info.get("errors", []),
                "maintenance": info.get("maintenance", []),
            })

        return {**window.to_dict(), "printers": printers}

    # ------------------------------------------------------------------
    # Work orders
    # ------------------------------------------------------------------

    def get_work_orders(self, week_start=None) -> dict:
        """Work-order and parts activity within the week."""
        window = self._window(week_start)
        wo_created = queries.work_orders_created_in_week(
            self.work_order_db_path, window.start_iso, window.next_monday_iso
        )
        wo_completed = queries.work_orders_completed_in_week(
            self.work_order_db_path, window.start_iso, window.next_monday_iso
        )
        wo_active = queries.work_orders_active_during_week(
            self.work_order_db_path, window.start_iso, window.next_monday_iso
        )
        queue_activity = queries.queue_items_activity_in_week(
            self.work_order_db_path, window.start_iso, window.next_monday_iso
        )

        completed_ids = {wo["wo_id"] for wo in wo_completed}
        created_ids = {wo["wo_id"] for wo in wo_created}
        active_only = [
            wo for wo in wo_active
            if wo["wo_id"] not in completed_ids
            and wo["wo_id"] not in created_ids
        ]

        def _wo_summary(wo):
            return {
                "wo_id": wo["wo_id"],
                "customer_name": wo.get("customer_name") or "",
                "created_at": wo.get("created_at"),
                "completed_at": wo.get("completed_at"),
                "status": wo.get("status"),
                "total_parts": _safe_int(wo.get("total_parts")),
                "parts_completed": _safe_int(wo.get("completed_parts")),
                "parts_failed": _safe_int(wo.get("failed_parts")),
            }

        parts_completed = 0
        parts_failed = 0
        parts_cancelled = 0
        parts_started = 0
        for item in queue_activity:
            status = item["status"]
            if self._in_window(item.get("completed_at"), window):
                if status == QueueItemStatus.COMPLETED:
                    parts_completed += 1
                elif status in QUEUE_FAILURE_STATUSES:
                    parts_failed += 1
                elif status == QueueItemStatus.CANCELLED:
                    parts_cancelled += 1
            if self._in_window(item.get("started_at"), window):
                parts_started += 1

        return {
            **window.to_dict(),
            "orders_created": [_wo_summary(w) for w in wo_created],
            "orders_completed": [_wo_summary(w) for w in wo_completed],
            "orders_active": [_wo_summary(w) for w in active_only],
            "parts_summary": {
                "completed_this_week": parts_completed,
                "failed_this_week": parts_failed,
                "cancelled_this_week": parts_cancelled,
                "started_this_week": parts_started,
            },
        }

    # ------------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------------

    def get_timeline(self, week_start=None) -> dict:
        """Merged chronological event log for the week (capped)."""
        from .weekly_timeline import build_timeline

        window = self._window(week_start)
        events, truncated = build_timeline(
            window, self, cap=self.TIMELINE_EVENT_CAP,
        )
        return {
            **window.to_dict(),
            "events": events,
            "truncated": truncated,
            "cap": self.TIMELINE_EVENT_CAP,
        }

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def export_csv(self, week_start=None) -> str:
        """Render the full multi-section CSV for the week."""
        from .weekly_csv import build_weekly_csv

        window = self._window(week_start)
        summary = self.get_summary(window.start_date.isoformat())
        production = self.get_production(window.start_date.isoformat())
        materials = self.get_materials(window.start_date.isoformat())
        equipment = self.get_equipment(window.start_date.isoformat())
        work_orders = self.get_work_orders(window.start_date.isoformat())
        timeline = self.get_timeline(window.start_date.isoformat())

        return build_weekly_csv(
            window=window,
            summary=summary,
            production=production,
            materials=materials,
            equipment=equipment,
            work_orders=work_orders,
            timeline=timeline,
        )
