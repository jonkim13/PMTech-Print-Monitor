"""Smoke test for /api/dashboard JSON shape.

DashboardService is exercised against stub repositories so the test
runs with no DB files. Validates the top-level keys + stat structure
the dashboard poll JS relies on.
"""

import os
import sys
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.dashboard.service import DashboardService


class _FakeFarmManager:
    def get_all_status(self):
        return [
            {
                "printer_id": "core-1", "name": "Core One #1",
                "model": "core_one", "status": "printing",
                "tool_count": 1,
                "temperatures": {
                    "nozzle_current": 217, "nozzle_target": 215,
                    "bed_current": 60, "bed_target": 60,
                },
                "job": {
                    "filename": "rover-chassis-mount.gcode",
                    "progress": 47, "time_remaining_sec": 4920,
                },
                "assigned_spool": {
                    "id": "26PLA01", "material": "PLA", "color": "Black",
                    "brand": "Prusa", "grams": 620,
                },
                "assigned_spools": [],
            },
            {
                "printer_id": "core-2", "name": "Core One #2",
                "model": "core_one", "status": "idle",
                "tool_count": 1,
                "temperatures": {"nozzle_current": 27, "nozzle_target": 0,
                                 "bed_current": 25, "bed_target": 0},
                "job": {},
                "assigned_spool": {
                    "id": "26PETG01", "material": "PETG", "color": "Green",
                    "brand": "Prusa", "grams": 180,
                },
                "assigned_spools": [],
            },
        ]


class _FakeWorkOrderRepository:
    def __init__(self, late_count=0):
        self._late = late_count

    def count_late_work_orders(self, today_iso):
        return self._late


class _FakeQueueRepository:
    pass


class _FakeHistoryDB:
    def get_history(self, limit=6):
        return [
            {"timestamp": "2026-05-18T14:09:42+00:00",
             "printer_name": "Core One #1",
             "event_type": "print_started",
             "filename": "rover-chassis-mount.gcode",
             "from_status": "idle", "to_status": "printing"},
            {"timestamp": "2026-05-18T14:03:11+00:00",
             "printer_name": "Core One #1",
             "event_type": "print_complete",
             "filename": "sensor-bracket.gcode",
             "from_status": "printing", "to_status": "idle"},
        ]


class _FakeFilamentDB:
    pass


class _FakeProductionJobRepo:
    def __init__(self, jobs=None):
        self._jobs = jobs or []

    def get_jobs(self, **kwargs):
        # Crude filter: status + outcome filter applied.
        out = list(self._jobs)
        if "status" in kwargs and kwargs["status"]:
            out = [j for j in out if j.get("status") == kwargs["status"]]
        if "outcome" in kwargs and kwargs["outcome"]:
            out = [j for j in out if j.get("outcome") == kwargs["outcome"]]
        if "date_from" in kwargs and kwargs["date_from"]:
            df = kwargs["date_from"]
            out = [j for j in out if j.get("completed_at", "") >= df]
        return out


class DashboardPayloadShapeTests(unittest.TestCase):
    def _service(self, **kwargs):
        return DashboardService(
            farm_manager=kwargs.get("farm_manager", _FakeFarmManager()),
            work_order_repository=kwargs.get(
                "work_order_repository", _FakeWorkOrderRepository()),
            queue_repository=kwargs.get(
                "queue_repository", _FakeQueueRepository()),
            history_db=kwargs.get("history_db", _FakeHistoryDB()),
            filament_db=kwargs.get("filament_db", _FakeFilamentDB()),
            production_job_repository=kwargs.get(
                "production_job_repository", _FakeProductionJobRepo()),
            work_order_db_path=None,
        )

    def test_payload_top_level_keys(self):
        payload = self._service().get_dashboard_payload()
        for k in ("now", "printers", "fleet_stats", "stats",
                  "attention_items", "attention_total", "events"):
            self.assertIn(k, payload, "missing key: " + k)

    def test_stats_block_has_design_field_names(self):
        payload = self._service().get_dashboard_payload()
        stats = payload["stats"]
        for k in ("printers_printing", "printers_total", "done_today",
                  "awaiting_qc", "awaiting_qc_wo_count", "late_wos"):
            self.assertIn(k, stats, "stats missing: " + k)

    def test_printer_projection_field_shape(self):
        payload = self._service().get_dashboard_payload()
        self.assertEqual(len(payload["printers"]), 2)
        p1 = payload["printers"][0]
        for k in ("id", "name", "model", "status", "progress",
                  "nozzle", "bed", "spools"):
            self.assertIn(k, p1)
        self.assertEqual(p1["status"], "printing")
        self.assertAlmostEqual(p1["progress"], 0.47, places=2)
        self.assertEqual(p1["nozzle"]["cur"], 217)
        self.assertEqual(p1["nozzle"]["tgt"], 215)
        # Spool projection: 1000g notional, 620g -> 0.62.
        self.assertEqual(len(p1["spools"]), 1)
        self.assertEqual(p1["spools"][0]["slot"], "T1")
        self.assertEqual(p1["spools"][0]["material"], "PLA")
        self.assertAlmostEqual(p1["spools"][0]["percent"], 0.62, places=2)

    def test_fleet_stats_aggregate_correctly(self):
        payload = self._service().get_dashboard_payload()
        self.assertEqual(payload["fleet_stats"]["total"], 2)
        self.assertEqual(payload["fleet_stats"]["printing"], 1)
        self.assertEqual(payload["fleet_stats"]["idle"], 1)

    def test_late_wos_propagates_from_repo(self):
        svc = self._service(
            work_order_repository=_FakeWorkOrderRepository(late_count=3),
        )
        payload = svc.get_dashboard_payload()
        self.assertEqual(payload["stats"]["late_wos"], 3)

    def test_spool_low_surfaces_in_attention(self):
        # core-2 has 180g PETG → 18% → below 25% threshold.
        payload = self._service().get_dashboard_payload()
        kinds = [it["kind"] for it in payload["attention_items"]]
        self.assertIn("spool", kinds)
        spool_item = next(it for it in payload["attention_items"]
                          if it["kind"] == "spool")
        self.assertEqual(spool_item["count"], 1)
        self.assertGreaterEqual(payload["attention_total"], 1)

    def test_awaiting_qc_counts_unknown_completed_jobs(self):
        # Three jobs awaiting QC.
        repo = _FakeProductionJobRepo([
            {"job_id": 1, "status": "completed", "outcome": "unknown",
             "file_name": "a.gcode", "printer_name": "Core One #1"},
            {"job_id": 2, "status": "completed", "outcome": "unknown",
             "file_name": "b.gcode", "printer_name": "XL #1"},
            {"job_id": 3, "status": "completed", "outcome": "pass",
             "file_name": "c.gcode", "printer_name": "XL #2"},
        ])
        svc = self._service(production_job_repository=repo)
        payload = svc.get_dashboard_payload()
        self.assertEqual(payload["stats"]["awaiting_qc"], 2)
        kinds = [it["kind"] for it in payload["attention_items"]]
        self.assertIn("qc", kinds)

    def test_events_are_projected_with_design_fields(self):
        payload = self._service().get_dashboard_payload()
        self.assertEqual(len(payload["events"]), 2)
        e = payload["events"][0]
        for k in ("ts", "color", "what", "where"):
            self.assertIn(k, e)
        # Started event maps to info tone.
        self.assertEqual(e["color"], "info")

    def test_eta_text_formats_when_printing(self):
        payload = self._service().get_dashboard_payload()
        self.assertEqual(payload["printers"][0]["eta_text"], "1h 22m left")


if __name__ == "__main__":
    unittest.main()
