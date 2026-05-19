"""TriageService — composes the aggregated /api/triage payload.

5 lanes per the v2 design handoff (README §3):
    1. Stopped & Failed       — queue_items in failure/cancelled states
    2. Per-Job Inspection     — print_jobs awaiting QC (outcome='unknown')
    3. Ready to Ship          — Phase B (returns empty)
    4. Design · Awaiting Customer — Phase B (returns empty)
    5. External & Spools      — spool-low (vendor-past-due is Phase B)

Plus an active_parts table for cross-WO situational awareness.

Cross-DB aggregation in Python (mirror of DashboardService). No SQL
joins across DB files.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional


# Spool baseline matches 2.5a's DashboardService — see SPOOL_NOTIONAL_MAX_G.
SPOOL_NOTIONAL_MAX_G = 1000.0
SPOOL_LOW_FRACTION = 0.25

# Failure statuses that surface in the Stopped & Failed lane (auto-fail
# subset). 'cancelled' is its own kind in the same lane.
AUTO_FAIL_STATUSES = ("failed", "upload_failed", "start_failed")
CANCELLED_STATUS = "cancelled"
STOPPED_AND_FAILED_STATUSES = AUTO_FAIL_STATUSES + (CANCELLED_STATUS,)

# Active queue-item statuses that surface in the active_parts table.
# Excludes terminal states (completed/failed/upload_failed/start_failed/
# cancelled).
ACTIVE_QUEUE_STATUSES = (
    "queued", "uploading", "uploaded", "starting", "printing",
)

# Ordering priority for the active-parts table.
ACTIVE_STATUS_ORDER = {
    "printing": 0,
    "starting": 1,
    "uploading": 2,
    "uploaded": 3,
    "queued": 4,
}


class TriageService:
    def __init__(self, queue_repository, work_order_repository,
                 print_job_repository, inventory_repository,
                 farm_manager,
                 work_order_db_path: Optional[str] = None):
        self.queue_repository = queue_repository
        self.work_order_repository = work_order_repository
        self.print_job_repository = print_job_repository
        self.inventory_repository = inventory_repository
        self.farm_manager = farm_manager
        self.work_order_db_path = work_order_db_path

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def get_triage_payload(self) -> Dict[str, Any]:
        lane_failed = self._lane_failed()
        lane_qc = self._lane_qc()
        lane_ready = self._lane_ready_ship()       # Phase B stub
        lane_design = self._lane_design_await()    # Phase B stub
        lane_external_spool = self._lane_external_spool()

        lanes = [lane_failed, lane_qc, lane_ready, lane_design,
                 lane_external_spool]
        lanes_total = sum(lane["count"] for lane in lanes)

        return {
            "now": datetime.now().strftime("%I:%M %p").lstrip("0"),
            "lanes": lanes,
            "active_parts": self._active_parts(),
            "lanes_total": lanes_total,
        }

    # ------------------------------------------------------------------
    # Lane 1 — Stopped & Failed
    # ------------------------------------------------------------------

    def _lane_failed(self) -> Dict[str, Any]:
        rows = self._select_queue_items_by_status(STOPPED_AND_FAILED_STATUSES)
        customer_by_wo = self._customer_lookup({r["wo_id"] for r in rows})

        items: List[Dict[str, Any]] = []
        for r in rows:
            status = r.get("status")
            kind = "cancelled" if status == CANCELLED_STATUS else "auto-fail"
            seq = r.get("sequence_number")
            total = r.get("total_quantity")
            seq_label = "{}/{}".format(seq, total) if seq and total else ""
            title = "{} {}".format(r.get("part_name") or "", seq_label).strip()

            printer = r.get("assigned_printer_name") or r.get("assigned_printer_id") or ""
            sub_parts = []
            if status in AUTO_FAIL_STATUSES:
                sub_parts.append(self._failure_reason_label(status))
            if printer:
                sub_parts.append(printer)
            sub = " · ".join(sub_parts) if sub_parts else None

            items.append({
                "kind": kind,
                "wo_id": r.get("wo_id"),
                "queue_id": r.get("queue_id"),
                "title": title,
                "customer": customer_by_wo.get(r.get("wo_id")),
                "sub": sub,
                "failed_at": self._format_time(
                    r.get("completed_at") or r.get("started_at")
                ),
                "printer_id": r.get("assigned_printer_id"),
                "printer_name": r.get("assigned_printer_name"),
                "part_name": r.get("part_name"),
                "material": r.get("material"),
                "status": status,
            })

        return {
            "kind": "failed",
            "label": "Stopped & Failed",
            "tone": "err",
            "count": len(items),
            "items": items,
        }

    @staticmethod
    def _failure_reason_label(status: str) -> str:
        return {
            "failed": "Print failed",
            "upload_failed": "Upload failed",
            "start_failed": "Start failed",
        }.get(status, "Failed")

    # ------------------------------------------------------------------
    # Lane 2 — Per-Job Inspection (Internal subset only — Phase B adds
    # external-incoming)
    # ------------------------------------------------------------------

    def _lane_qc(self) -> Dict[str, Any]:
        if not self.print_job_repository:
            return self._empty_lane("qc", "Per-Job Inspection", "info")
        try:
            jobs = self.print_job_repository.get_jobs(
                status="completed", outcome="unknown", limit=10000,
            )
        except Exception:
            return self._empty_lane("qc", "Per-Job Inspection", "info")

        # Cross-DB linkage: print_jobs.job_id → queue_items.print_job_id →
        # wo_id + part_name. Batch the lookup so we don't open the WO DB
        # once per job.
        job_id_to_wo = self._wo_for_print_jobs(
            [j.get("job_id") for j in jobs if j.get("job_id")]
        )

        wo_set = {info[0] for info in job_id_to_wo.values()}
        customer_by_wo = self._customer_lookup(wo_set)

        items: List[Dict[str, Any]] = []
        for j in jobs:
            jid = j.get("job_id")
            wo_id, part_name = job_id_to_wo.get(jid, (None, None))
            title = part_name or j.get("file_display_name") or j.get("file_name") or "Untitled part"
            sub_parts = []
            if j.get("completed_at"):
                sub_parts.append(
                    "Completed " + self._format_time(j["completed_at"])
                )
            if j.get("printer_name"):
                sub_parts.append("Printed on " + j["printer_name"])
            sub = " · ".join(sub_parts) if sub_parts else None

            items.append({
                "kind": "internal-qc",
                "wo_id": wo_id,
                "job_id": jid,
                "title": title,
                "customer": customer_by_wo.get(wo_id),
                "sub": sub,
                "qty": 1,
                "printer_name": j.get("printer_name"),
                "completed_at": j.get("completed_at"),
            })

        # TODO Phase B: external-incoming inspection (received vendor
        # shipments) goes here too.
        return {
            "kind": "qc",
            "label": "Per-Job Inspection",
            "tone": "info",
            "count": len(items),
            "items": items,
        }

    # ------------------------------------------------------------------
    # Lane 3 — Ready to Ship (Phase B)
    # ------------------------------------------------------------------

    def _lane_ready_ship(self) -> Dict[str, Any]:
        # TODO Phase B: surface WOs where every job has passed inspection,
        # awaiting WO Sign-off + delivery. Requires the per-job inspection
        # state machine + WO Sign-off action — neither exists yet.
        return self._empty_lane("ready_ship", "Ready to Ship", "ok")

    # ------------------------------------------------------------------
    # Lane 4 — Design · Awaiting Customer (Phase B)
    # ------------------------------------------------------------------

    def _lane_design_await(self) -> Dict[str, Any]:
        # TODO Phase B: Design jobs with status='awaiting-customer' and
        # last feedback older than the configured threshold (default 5d).
        # Requires the Design job model + customer-feedback log — neither
        # exists yet.
        return self._empty_lane(
            "design_await", "Design · Awaiting Customer", "busy"
        )

    # ------------------------------------------------------------------
    # Lane 5 — External & Spools (spool-low subset only — Phase B adds
    # vendor-past-due)
    # ------------------------------------------------------------------

    def _lane_external_spool(self) -> Dict[str, Any]:
        items: List[Dict[str, Any]] = []
        if not self.farm_manager:
            return {
                "kind": "external_spool",
                "label": "External & Spools",
                "tone": "warn",
                "count": 0,
                "items": [],
            }

        try:
            printers = self.farm_manager.get_all_status() or []
        except Exception:
            printers = []

        for p in printers:
            for entry in (p.get("assigned_spools") or []):
                spool = entry.get("spool") if isinstance(entry, dict) else None
                tool_idx = entry.get("tool_index") if isinstance(entry, dict) else None
                if not spool:
                    continue
                grams = float(spool.get("grams") or 0)
                percent = max(0.0, min(1.0, grams / SPOOL_NOTIONAL_MAX_G))
                if percent >= SPOOL_LOW_FRACTION:
                    continue
                items.append({
                    "kind": "spool-low",
                    "printer_id": p.get("printer_id"),
                    "printer_name": p.get("name") or p.get("printer_id"),
                    "tool_index": tool_idx or 0,
                    "spool_id": spool.get("id"),
                    "material": "{} {}".format(
                        spool.get("material") or "",
                        spool.get("color") or "",
                    ).strip(),
                    "percent": percent,
                    "grams_left": int(grams),
                    "sub": "Below 25% threshold",
                })
            # Single-tool printers may only have assigned_spool populated.
            if not (p.get("assigned_spools") or []) and p.get("assigned_spool"):
                spool = p["assigned_spool"]
                grams = float(spool.get("grams") or 0)
                percent = max(0.0, min(1.0, grams / SPOOL_NOTIONAL_MAX_G))
                if percent < SPOOL_LOW_FRACTION:
                    items.append({
                        "kind": "spool-low",
                        "printer_id": p.get("printer_id"),
                        "printer_name": p.get("name") or p.get("printer_id"),
                        "tool_index": 0,
                        "spool_id": spool.get("id"),
                        "material": "{} {}".format(
                            spool.get("material") or "",
                            spool.get("color") or "",
                        ).strip(),
                        "percent": percent,
                        "grams_left": int(grams),
                        "sub": "Below 25% threshold",
                    })

        # TODO Phase B: vendor-past-due rows go in this lane too.
        return {
            "kind": "external_spool",
            "label": "External & Spools",
            "tone": "warn",
            "count": len(items),
            "items": items,
        }

    # ------------------------------------------------------------------
    # Active parts table (situational awareness — non-terminal items
    # across all WOs)
    # ------------------------------------------------------------------

    def _active_parts(self) -> List[Dict[str, Any]]:
        rows = self._select_queue_items_by_status(ACTIVE_QUEUE_STATUSES)
        customer_by_wo = self._customer_lookup({r["wo_id"] for r in rows})

        # Pull current job/eta from runtime so the printing row can show
        # a real countdown.
        printer_runtime = self._printer_runtime_by_id()

        rows.sort(key=lambda r: (
            ACTIVE_STATUS_ORDER.get(r.get("status"), 99),
            r.get("queue_id") or 0,
        ))

        out: List[Dict[str, Any]] = []
        for r in rows:
            pid = r.get("assigned_printer_id")
            runtime = printer_runtime.get(pid) if pid else None
            eta = ""
            if r.get("status") == "printing" and runtime:
                job = runtime.get("job") or {}
                remaining = job.get("time_remaining_sec")
                if remaining and remaining > 0:
                    eta = self._format_eta(remaining)

            seq = r.get("sequence_number")
            total = r.get("total_quantity")
            seq_label = "{}/{}".format(seq, total) if seq and total else ""

            out.append({
                "queue_id": r.get("queue_id"),
                "seq": seq_label,
                "wo_id": r.get("wo_id"),
                "part_name": r.get("part_name"),
                # Phase B will set this from the persisted job record
                # (job.type ∈ internal | external | design).
                "job_type": "internal",
                "material": r.get("material"),
                "status": r.get("status"),
                "printer": r.get("assigned_printer_name") or "-",
                "eta": eta,
                "customer": customer_by_wo.get(r.get("wo_id")),
            })
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _empty_lane(self, kind: str, label: str, tone: str) -> Dict[str, Any]:
        return {"kind": kind, "label": label, "tone": tone,
                "count": 0, "items": []}

    def _select_queue_items_by_status(
        self, statuses
    ) -> List[Dict[str, Any]]:
        if not self.work_order_db_path:
            return []
        placeholders = ",".join("?" * len(statuses))
        try:
            conn = sqlite3.connect(self.work_order_db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM queue_items WHERE status IN ({}) "
                "ORDER BY COALESCE(completed_at, started_at, queued_at) DESC, "
                "queue_id DESC".format(placeholders),
                list(statuses),
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return []
        return [dict(r) for r in rows]

    def _customer_lookup(self, wo_ids) -> Dict[str, str]:
        wo_ids = [w for w in wo_ids if w]
        if not wo_ids or not self.work_order_db_path:
            return {}
        placeholders = ",".join("?" * len(wo_ids))
        try:
            conn = sqlite3.connect(self.work_order_db_path)
            rows = conn.execute(
                "SELECT wo_id, customer_name FROM work_orders "
                "WHERE wo_id IN ({})".format(placeholders),
                list(wo_ids),
            ).fetchall()
            conn.close()
            return {row[0]: row[1] for row in rows}
        except sqlite3.Error:
            return {}

    def _wo_for_print_jobs(
        self, print_job_ids
    ) -> Dict[int, "tuple[Optional[str], Optional[str]]"]:
        """Map print_job_id → (wo_id, part_name) via queue_items linkage.

        Batched lookup to keep this one DB hit regardless of how many
        awaiting-QC jobs there are.
        """
        print_job_ids = [j for j in print_job_ids if j]
        if not print_job_ids or not self.work_order_db_path:
            return {}
        placeholders = ",".join("?" * len(print_job_ids))
        try:
            conn = sqlite3.connect(self.work_order_db_path)
            rows = conn.execute(
                "SELECT print_job_id, wo_id, part_name FROM queue_items "
                "WHERE print_job_id IN ({})".format(placeholders),
                list(print_job_ids),
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            return {}
        out = {}
        for row in rows:
            # If multiple queue_items share a print_job (multi-part job),
            # keep the first — they share the same wo_id by design.
            if row[0] not in out:
                out[row[0]] = (row[1], row[2])
        return out

    def _printer_runtime_by_id(self) -> Dict[str, Dict[str, Any]]:
        if not self.farm_manager:
            return {}
        try:
            printers = self.farm_manager.get_all_status() or []
        except Exception:
            return {}
        return {p.get("printer_id"): p for p in printers if p.get("printer_id")}

    @staticmethod
    def _format_time(ts: Optional[str]) -> str:
        if not ts:
            return ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%I:%M %p").lstrip("0")
        except (ValueError, TypeError):
            return ts[:16] if len(ts) >= 16 else ts

    @staticmethod
    def _format_eta(remaining_sec: int) -> str:
        h = int(remaining_sec) // 3600
        m = (int(remaining_sec) % 3600) // 60
        if h > 0:
            return "{}h {}m".format(h, m)
        return "{}m".format(m)
