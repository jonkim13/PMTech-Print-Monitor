"""DashboardService — composes the aggregated /api/dashboard payload.

Reads from inventory, work_orders, production, queue, and printers.
Cross-DB aggregation happens here in Python — no SQL joins across DB
files. The service depends only on existing repos + the farm manager;
no new persistence.

Output shape mirrors the field names in
design_handoff_print_monitor/prototype/mock-data.jsx so the dashboard
poll JS can interpolate directly.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional


# Spool baseline: no `full_grams` is recorded; the prompt's spool-low
# threshold (`grams * 100 / 1000 < 25`) sets the notional max at 1000g.
SPOOL_NOTIONAL_MAX_G = 1000.0
SPOOL_LOW_FRACTION = 0.25

# Queue statuses that count as "failed" for the dashboard attention rail.
# Combines auto-failures and operator cancellations per the v2 spec's
# "Stopped & Failed" lane.
FAILED_QUEUE_STATUSES = (
    "failed", "upload_failed", "start_failed", "cancelled",
)


class DashboardService:
    def __init__(self, farm_manager, work_order_repository,
                 queue_repository, history_db, filament_db,
                 production_job_repository,
                 work_order_db_path: Optional[str] = None):
        self.farm_manager = farm_manager
        self.work_order_repository = work_order_repository
        self.queue_repository = queue_repository
        self.history_db = history_db
        self.filament_db = filament_db
        self.production_job_repository = production_job_repository
        self.work_order_db_path = work_order_db_path

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def get_dashboard_payload(self) -> Dict[str, Any]:
        printers_raw = self.farm_manager.get_all_status() or []
        active_parts_by_pid = self._active_queue_items_by_printer()

        printers = [
            self._project_printer(p, active_parts_by_pid.get(p.get("printer_id")))
            for p in printers_raw
        ]
        fleet_stats = self._fleet_stats(printers)
        stats = self._stats(printers, fleet_stats)
        attention_items, attention_total = self._attention(printers)
        events = self._recent_events(limit=6)

        return {
            "now": datetime.now().strftime("%I:%M %p").lstrip("0"),
            "printers": printers,
            "fleet_stats": fleet_stats,
            "stats": stats,
            "attention_items": attention_items,
            "attention_total": attention_total,
            "events": events,
        }

    # ------------------------------------------------------------------
    # Printer projection
    # ------------------------------------------------------------------

    def _project_printer(self, raw: Dict[str, Any],
                         active_part: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Project the runtime status onto the design field shape."""
        status = str(raw.get("status") or "idle")
        temps = raw.get("temperatures") or {}
        job = raw.get("job") or {}

        progress = job.get("progress")
        try:
            progress_fraction = float(progress) / 100.0 if progress is not None else 0.0
        except (TypeError, ValueError):
            progress_fraction = 0.0

        eta_text = None
        remaining = job.get("time_remaining_sec")
        if status == "printing" and remaining and remaining > 0:
            eta_text = self._format_eta(remaining)

        spools = self._project_spools(raw)

        out = {
            "id": raw.get("printer_id"),
            "name": raw.get("name") or raw.get("printer_id"),
            "model": raw.get("model"),
            "status": status,
            "progress": progress_fraction,
            "eta_text": eta_text,
            "nozzle": {
                "cur": float(temps.get("nozzle_current") or 0),
                "tgt": float(temps.get("nozzle_target") or 0),
            },
            "bed": {
                "cur": float(temps.get("bed_current") or 0),
                "tgt": float(temps.get("bed_target") or 0),
            },
            "spools": spools,
            "part": None,
            "wo": None,
            "part_seq": None,
            "attention": None,
            "last_print_relative": None,
        }

        if active_part:
            out["part"] = active_part.get("part_name")
            out["wo"] = active_part.get("wo_id")
            seq = active_part.get("sequence_number")
            total = active_part.get("total_quantity")
            if seq and total:
                out["part_seq"] = "{}/{}".format(seq, total)

        if status == "error":
            out["error_title"] = "Printer error"
            out["error_sub"] = job.get("filename") or None

        # Mark filament-runout / spool-low attention on the printer card.
        if any(s.get("percent") is not None and s["percent"] <= 0.05 for s in spools if s.get("material")):
            out["attention"] = "filament-runout"
        elif any(s.get("percent") is not None and s["percent"] < SPOOL_LOW_FRACTION for s in spools if s.get("material")):
            out["attention"] = "spool-low"

        return out

    def _project_spools(self, raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Map raw assigned_spools to the design's spool field shape."""
        spools = []
        for entry in raw.get("assigned_spools") or []:
            spool = entry.get("spool") if isinstance(entry, dict) else None
            slot_idx = entry.get("tool_index") if isinstance(entry, dict) else None
            slot = "T{}".format((slot_idx or 0) + 1)
            if not spool:
                spools.append({
                    "slot": slot,
                    "material": None,
                    "color": None,
                    "color_name": None,
                    "percent": 0.0,
                    "grams_left": 0,
                })
                continue
            grams = float(spool.get("grams") or 0)
            percent = max(0.0, min(1.0, grams / SPOOL_NOTIONAL_MAX_G))
            spools.append({
                "slot": slot,
                "material": spool.get("material"),
                "color": self._normalize_color_for_swatch(spool.get("color")),
                "color_name": spool.get("color"),
                "percent": percent,
                "grams_left": int(grams),
                "spool_id": spool.get("id"),
                "printer_id": raw.get("printer_id"),
                "tool_index": slot_idx,
            })
        # Single-tool printers may only have `assigned_spool` populated.
        if not spools and raw.get("assigned_spool"):
            spool = raw["assigned_spool"]
            grams = float(spool.get("grams") or 0)
            percent = max(0.0, min(1.0, grams / SPOOL_NOTIONAL_MAX_G))
            spools.append({
                "slot": "T1",
                "material": spool.get("material"),
                "color": self._normalize_color_for_swatch(spool.get("color")),
                "color_name": spool.get("color"),
                "percent": percent,
                "grams_left": int(grams),
                "spool_id": spool.get("id"),
                "printer_id": raw.get("printer_id"),
                "tool_index": 0,
            })
        return spools

    @staticmethod
    def _normalize_color_for_swatch(color_value: Optional[str]) -> Optional[str]:
        """Best-effort CSS color from a free-text color name.

        The inventory stores `color` as a label like "Black" or "Orange".
        For the spool swatch we need a real CSS color. Map common names;
        leave anything else as None so the swatch falls back to the
        striped 'empty' pattern.
        """
        if not color_value:
            return None
        c = color_value.strip().lower()
        named = {
            "black": "#222",
            "white": "#eee",
            "red": "#e63946",
            "orange": "#e9722a",
            "yellow": "#f5c800",
            "green": "#34d399",
            "blue": "#3b82f6",
            "cyan": "#22d3ee",
            "purple": "#a855f7",
            "violet": "#8b5cf6",
            "pink": "#ec4899",
            "gray": "#6b7280",
            "grey": "#6b7280",
            "silver": "#c0c0c0",
            "gold": "#d4af37",
            "brown": "#8b4513",
            "natural": "#e8d6b3",
            "clear": "#dfe6ec",
            "carbon": "#444",
        }
        if c in named:
            return named[c]
        # Already a hex/CSS color?
        if c.startswith("#") and len(c) in (4, 7):
            return c
        return None

    # ------------------------------------------------------------------
    # Active queue item per printer (for part/wo/seq projection)
    # ------------------------------------------------------------------

    def _active_queue_items_by_printer(self) -> Dict[str, Dict[str, Any]]:
        if not self.work_order_db_path:
            return {}
        try:
            conn = sqlite3.connect(self.work_order_db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM queue_items "
                "WHERE assigned_printer_id IS NOT NULL "
                "AND status IN ('uploading', 'uploaded', 'starting', 'printing') "
                "ORDER BY started_at DESC, queue_id DESC"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return {}
        by_pid: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            pid = row["assigned_printer_id"]
            if pid and pid not in by_pid:
                by_pid[pid] = dict(row)
        return by_pid

    # ------------------------------------------------------------------
    # Fleet stats
    # ------------------------------------------------------------------

    def _fleet_stats(self, printers: List[Dict[str, Any]]) -> Dict[str, int]:
        total = len(printers)
        printing = sum(1 for p in printers if p["status"] == "printing")
        idle = sum(1 for p in printers if p["status"] == "idle")
        error = sum(1 for p in printers if p["status"] == "error")
        return {
            "total": total,
            "printing": printing,
            "idle": idle,
            "error": error,
            "done_today": self._count_done_today(),
        }

    def _stats(self, printers: List[Dict[str, Any]],
               fleet_stats: Dict[str, int]) -> Dict[str, int]:
        awaiting_qc, qc_wo_count = self._count_awaiting_qc()
        today_iso = date.today().isoformat()
        return {
            "printers_printing": fleet_stats["printing"],
            "printers_total": fleet_stats["total"],
            "done_today": fleet_stats["done_today"],
            "awaiting_qc": awaiting_qc,
            "awaiting_qc_wo_count": qc_wo_count,
            "late_wos": self.work_order_repository.count_late_work_orders(today_iso),
        }

    def _count_done_today(self) -> int:
        if not self.production_job_repository:
            return 0
        today_midnight = datetime.combine(
            date.today(), datetime.min.time()
        ).replace(tzinfo=timezone.utc).isoformat()
        # Use the public list_jobs filter — caps at limit=100 by default.
        # Bump the limit so a busy day doesn't get truncated.
        try:
            jobs = self.production_job_repository.get_jobs(
                status="completed",
                date_from=today_midnight,
                limit=10000,
            )
        except Exception:
            return 0
        return len(jobs)

    def _count_awaiting_qc(self) -> "tuple[int, int]":
        if not self.production_job_repository:
            return 0, 0
        try:
            jobs = self.production_job_repository.get_jobs(
                status="completed", outcome="unknown", limit=10000,
            )
        except Exception:
            return 0, 0
        wo_set = set()
        for j in jobs:
            # production_log jobs aren't FK'd to a wo_id; instead, the
            # link is via queue_items.print_job_id. Compute WO count via
            # a join in the work_orders DB.
            wo = self._wo_for_print_job(j.get("job_id"))
            if wo:
                wo_set.add(wo)
        return len(jobs), len(wo_set)

    def _wo_for_print_job(self, print_job_id) -> Optional[str]:
        if not print_job_id or not self.work_order_db_path:
            return None
        try:
            conn = sqlite3.connect(self.work_order_db_path)
            row = conn.execute(
                "SELECT wo_id FROM queue_items "
                "WHERE print_job_id = ? LIMIT 1",
                (print_job_id,),
            ).fetchone()
            conn.close()
            return row[0] if row else None
        except sqlite3.Error:
            return None

    # ------------------------------------------------------------------
    # Attention items (rail)
    # ------------------------------------------------------------------

    def _attention(self, printers: List[Dict[str, Any]]) -> "tuple[List[Dict[str, Any]], int]":
        items: List[Dict[str, Any]] = []
        total = 0

        failed = self._attention_failed()
        if failed["count"]:
            items.append(failed)
            total += failed["count"]

        qc = self._attention_qc()
        if qc["count"]:
            items.append(qc)
            total += qc["count"]

        spool = self._attention_spool(printers)
        if spool["count"]:
            items.append(spool)
            total += spool["count"]

        return items, total

    def _attention_failed(self) -> Dict[str, Any]:
        if not self.work_order_db_path:
            return {"kind": "failed", "count": 0, "items": [], "label": "STOPPED & FAILED"}
        placeholders = ",".join("?" * len(FAILED_QUEUE_STATUSES))
        try:
            conn = sqlite3.connect(self.work_order_db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM queue_items "
                "WHERE status IN ({}) "
                "ORDER BY COALESCE(completed_at, started_at, queued_at) DESC "
                "LIMIT 50".format(placeholders),
                FAILED_QUEUE_STATUSES,
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return {"kind": "failed", "count": 0, "items": [], "label": "STOPPED & FAILED"}

        if not rows:
            return {"kind": "failed", "count": 0, "items": [], "label": "STOPPED & FAILED"}

        first = dict(rows[0])
        title_part = first.get("part_name") or "part"
        seq = first.get("sequence_number")
        total = first.get("total_quantity")
        seq_label = " · {}/{}".format(seq, total) if seq and total else ""
        return {
            "kind": "failed",
            "count": len(rows),
            "label": "STOPPED & FAILED",
            "timestamp_label": "NOW",
            "title": "{}{}".format(title_part, seq_label),
            "sub": "{} · {}".format(
                first.get("assigned_printer_name") or "printer",
                first.get("status") or "failed",
            ),
            "items": [
                {
                    "queue_id": r["queue_id"],
                    "wo_id": r["wo_id"],
                    "part_name": r["part_name"],
                    "status": r["status"],
                    "assigned_printer_id": r["assigned_printer_id"],
                    "assigned_printer_name": r["assigned_printer_name"],
                }
                for r in [dict(x) for x in rows]
            ],
            "actions": [
                {"label": "Open Triage", "variant": "primary",
                 "onclick": "switchPage('workorders')"},
            ],
        }

    def _attention_qc(self) -> Dict[str, Any]:
        if not self.production_job_repository:
            return {"kind": "qc", "count": 0, "items": [], "label": "AWAITING QC"}
        try:
            jobs = self.production_job_repository.get_jobs(
                status="completed", outcome="unknown", limit=10000,
            )
        except Exception:
            return {"kind": "qc", "count": 0, "items": [], "label": "AWAITING QC"}
        if not jobs:
            return {"kind": "qc", "count": 0, "items": [], "label": "AWAITING QC"}

        first = jobs[0]
        wo = self._wo_for_print_job(first.get("job_id"))
        sub_bits = []
        if wo:
            sub_bits.append(wo)
        if first.get("printer_name"):
            sub_bits.append(first["printer_name"])
        return {
            "kind": "qc",
            "count": len(jobs),
            "label": "AWAITING PER-JOB INSPECTION · {}".format(len(jobs)),
            "title": "{}".format(first.get("file_display_name") or first.get("file_name") or "Part"),
            "sub": " · ".join(sub_bits) if sub_bits else None,
            "items": [
                {"job_id": j.get("job_id"),
                 "file_name": j.get("file_name"),
                 "printer_name": j.get("printer_name")}
                for j in jobs[:50]
            ],
            "actions": [
                {"label": "Open Production", "variant": "primary",
                 "onclick": "switchPage('production')"},
            ],
        }

    def _attention_spool(self, printers: List[Dict[str, Any]]) -> Dict[str, Any]:
        low = []
        for p in printers:
            for s in p.get("spools") or []:
                if not s.get("material"):
                    continue
                pct = s.get("percent") or 0
                if pct < SPOOL_LOW_FRACTION:
                    low.append({
                        "printer_id": p.get("id"),
                        "printer_name": p.get("name"),
                        "slot": s.get("slot"),
                        "material": s.get("material"),
                        "color_name": s.get("color_name"),
                        "percent": pct,
                        "grams_left": s.get("grams_left"),
                    })
        if not low:
            return {"kind": "spool", "count": 0, "items": [], "label": "SPOOL LOW"}

        first = low[0]
        pct = int(round(first["percent"] * 100))
        return {
            "kind": "spool",
            "count": len(low),
            "label": "SPOOL LOW",
            "title": "{} · {}%".format(first["printer_name"], pct),
            "sub": "{} · {}".format(
                first["slot"],
                "{} {}".format(first["material"], first.get("color_name") or "").strip(),
            ),
            "items": low,
            "actions": [
                {"label": "Swap spool",
                 "onclick": "showAssignSpoolModal('{}', '{}')".format(
                     first["printer_id"], first["printer_name"])},
            ],
        }

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _recent_events(self, limit: int = 6) -> List[Dict[str, Any]]:
        if not self.history_db:
            return []
        try:
            raw = self.history_db.get_history(limit=limit)
        except Exception:
            return []
        out = []
        for e in raw:
            ts = e.get("timestamp") or ""
            ts_short = self._format_timestamp(ts)
            ev_type = (e.get("event_type") or "").lower()
            color = "neutral"
            if ev_type == "print_complete":
                color = "ok"
            elif ev_type == "print_started":
                color = "info"
            elif ev_type == "printer_error":
                color = "err"
            elif ev_type == "print_cancelled":
                color = "warn"
            what = self._event_text(ev_type, e)
            out.append({
                "ts": ts_short,
                "color": color,
                "what": what,
                "who": None,
                "where": e.get("printer_name") or "",
            })
        return out

    @staticmethod
    def _event_text(ev_type: str, e: Dict[str, Any]) -> str:
        filename = e.get("filename") or "a job"
        if ev_type == "print_complete":
            return "Completed {}".format(filename)
        if ev_type == "print_started":
            return "Started {}".format(filename)
        if ev_type == "print_cancelled":
            return "Cancelled {}".format(filename)
        if ev_type == "printer_error":
            return "Printer error"
        return "{} → {}".format(
            e.get("from_status") or "?", e.get("to_status") or "?"
        )

    @staticmethod
    def _format_timestamp(ts: str) -> str:
        if not ts:
            return ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            return ts[:8] if len(ts) >= 8 else ts

    @staticmethod
    def _format_eta(remaining_sec: int) -> str:
        h = int(remaining_sec) // 3600
        m = (int(remaining_sec) % 3600) // 60
        if h > 0:
            return "{}h {}m left".format(h, m)
        return "{}m left".format(m)
