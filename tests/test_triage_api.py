"""TriageService — payload shape + lane composition.

The service is exercised against stub repos and a fake farm_manager
so the tests run with no DB files. Validates the 5-lane structure +
active-parts projection that the dashboard JS / triage JS consume.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.domains.triage.service import TriageService


class _FakePrintJobRepo:
    def __init__(self, jobs=None):
        self._jobs = jobs or []

    def get_jobs(self, **kwargs):
        out = list(self._jobs)
        if kwargs.get("status"):
            out = [j for j in out if j.get("status") == kwargs["status"]]
        if kwargs.get("outcome"):
            out = [j for j in out if j.get("outcome") == kwargs["outcome"]]
        return out


class _FakeFarmManager:
    def __init__(self, printers=None):
        self._printers = printers or []

    def get_all_status(self):
        return list(self._printers)


def _make_wo_db(tmpdir, queue_items=None, work_orders=None):
    """Build a minimal work_orders.db with just the columns TriageService
    reads (queue_items + work_orders)."""
    path = os.path.join(tmpdir, "wo.db")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE work_orders (
            wo_id TEXT PRIMARY KEY,
            customer_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE TABLE queue_items (
            queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            wo_id TEXT NOT NULL,
            job_id INTEGER,
            queue_job_id INTEGER,
            part_name TEXT NOT NULL,
            material TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            sequence_number INTEGER NOT NULL,
            total_quantity INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            assigned_printer_id TEXT,
            assigned_printer_name TEXT,
            print_job_id INTEGER,
            queued_at TEXT NOT NULL,
            assigned_at TEXT,
            started_at TEXT,
            completed_at TEXT
        );
        """
    )
    for wo in (work_orders or []):
        conn.execute(
            "INSERT INTO work_orders (wo_id, customer_name, created_at) "
            "VALUES (?, ?, ?)",
            (wo["wo_id"], wo["customer_name"], wo.get("created_at", "2026-05-01T00:00:00")),
        )
    for q in (queue_items or []):
        conn.execute(
            "INSERT INTO queue_items ("
            "item_id, wo_id, job_id, part_name, material, customer_name, "
            "sequence_number, total_quantity, status, "
            "assigned_printer_id, assigned_printer_name, "
            "print_job_id, queued_at, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                q.get("item_id", 1), q["wo_id"], q.get("job_id"),
                q["part_name"], q.get("material", "PLA"),
                q.get("customer_name", "Acme"),
                q.get("sequence_number", 1), q.get("total_quantity", 1),
                q.get("status", "queued"),
                q.get("assigned_printer_id"), q.get("assigned_printer_name"),
                q.get("print_job_id"),
                q.get("queued_at", "2026-05-01T00:00:00"),
                q.get("started_at"), q.get("completed_at"),
            ),
        )
    conn.commit()
    conn.close()
    return path


class TriagePayloadShapeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _service(self, queue_items=None, work_orders=None,
                 print_jobs=None, printers=None):
        db_path = _make_wo_db(self.tmpdir.name,
                              queue_items=queue_items,
                              work_orders=work_orders)
        return TriageService(
            queue_repository=None,
            work_order_repository=None,
            print_job_repository=_FakePrintJobRepo(jobs=print_jobs or []),
            inventory_repository=None,
            farm_manager=_FakeFarmManager(printers=printers or []),
            work_order_db_path=db_path,
        )

    def test_empty_state_all_lanes_present_with_zero_count(self):
        payload = self._service().get_triage_payload()
        self.assertIn("lanes", payload)
        self.assertEqual(len(payload["lanes"]), 5)
        kinds = [l["kind"] for l in payload["lanes"]]
        self.assertEqual(
            kinds,
            ["failed", "qc", "ready_ship", "design_await", "external_spool"],
        )
        for lane in payload["lanes"]:
            self.assertEqual(lane["count"], 0)
            self.assertEqual(lane["items"], [])
        self.assertEqual(payload["lanes_total"], 0)
        self.assertEqual(payload["active_parts"], [])

    def test_top_level_keys_match_spec(self):
        payload = self._service().get_triage_payload()
        for k in ("now", "lanes", "active_parts", "lanes_total"):
            self.assertIn(k, payload)

    def test_failed_lane_distinguishes_auto_fail_and_cancelled(self):
        items = [
            {"item_id": 1, "wo_id": "WO-001", "part_name": "drone-arm",
             "sequence_number": 4, "total_quantity": 6,
             "status": "failed", "assigned_printer_name": "XL #2",
             "queued_at": "2026-05-18T13:00:00"},
            {"item_id": 2, "wo_id": "WO-001", "part_name": "drone-arm",
             "sequence_number": 5, "total_quantity": 6,
             "status": "cancelled", "assigned_printer_name": "XL #2",
             "queued_at": "2026-05-18T13:01:00"},
            {"item_id": 3, "wo_id": "WO-002", "part_name": "rover-mount",
             "sequence_number": 1, "total_quantity": 1,
             "status": "upload_failed", "assigned_printer_name": "Core #1",
             "queued_at": "2026-05-18T13:02:00"},
        ]
        wos = [{"wo_id": "WO-001", "customer_name": "Hyperion"},
               {"wo_id": "WO-002", "customer_name": "Acme"}]
        svc = self._service(queue_items=items, work_orders=wos)
        payload = svc.get_triage_payload()

        failed_lane = payload["lanes"][0]
        self.assertEqual(failed_lane["kind"], "failed")
        self.assertEqual(failed_lane["count"], 3)
        item_kinds = {it["kind"] for it in failed_lane["items"]}
        self.assertEqual(item_kinds, {"auto-fail", "cancelled"})

        # Customer name joined from work_orders
        customers = {it["customer"] for it in failed_lane["items"]}
        self.assertIn("Hyperion", customers)
        self.assertIn("Acme", customers)

    def test_qc_lane_pulls_completed_unknown_outcome_jobs(self):
        items = [
            {"item_id": 1, "wo_id": "WO-007", "part_name": "rover-mount",
             "print_job_id": 99, "status": "completed",
             "queued_at": "2026-05-18T10:00:00"},
        ]
        wos = [{"wo_id": "WO-007", "customer_name": "Acme"}]
        jobs = [
            {"job_id": 99, "status": "completed", "outcome": "unknown",
             "file_name": "rover.gcode", "printer_name": "Core #1"},
            {"job_id": 100, "status": "completed", "outcome": "pass",
             "file_name": "done.gcode", "printer_name": "Core #1"},
        ]
        svc = self._service(queue_items=items, work_orders=wos,
                            print_jobs=jobs)
        payload = svc.get_triage_payload()

        qc_lane = payload["lanes"][1]
        self.assertEqual(qc_lane["kind"], "qc")
        self.assertEqual(qc_lane["count"], 1)
        self.assertEqual(qc_lane["items"][0]["kind"], "internal-qc")
        # Cross-DB linkage resolves part_name from queue_items
        self.assertEqual(qc_lane["items"][0]["title"], "rover-mount")
        self.assertEqual(qc_lane["items"][0]["wo_id"], "WO-007")
        self.assertEqual(qc_lane["items"][0]["customer"], "Acme")

    def test_qc_lane_falls_back_to_file_name_when_no_queue_linkage(self):
        # No queue_items row links to this print_job — should fall back
        # to file_display_name / file_name for the title.
        jobs = [
            {"job_id": 200, "status": "completed", "outcome": "unknown",
             "file_name": "legacy.gcode", "file_display_name": "Legacy print",
             "printer_name": "XL #1"},
        ]
        svc = self._service(print_jobs=jobs)
        payload = svc.get_triage_payload()
        qc = payload["lanes"][1]
        self.assertEqual(qc["count"], 1)
        self.assertEqual(qc["items"][0]["title"], "Legacy print")
        self.assertIsNone(qc["items"][0]["wo_id"])

    def test_ready_ship_and_design_await_are_phase_b_stubs(self):
        payload = self._service().get_triage_payload()
        ready = payload["lanes"][2]
        design = payload["lanes"][3]
        self.assertEqual(ready["kind"], "ready_ship")
        self.assertEqual(ready["count"], 0)
        self.assertEqual(design["kind"], "design_await")
        self.assertEqual(design["count"], 0)

    def test_spool_low_populates_external_spool_lane(self):
        printers = [
            {"printer_id": "core-1", "name": "Core One #1",
             "assigned_spools": [
                 {"tool_index": 0, "spool": {
                     "id": "S001", "material": "PLA", "color": "Black",
                     "grams": 600,
                 }},
             ]},
            {"printer_id": "core-2", "name": "Core One #2",
             "assigned_spools": [
                 {"tool_index": 0, "spool": {
                     "id": "S002", "material": "PETG", "color": "Green",
                     "grams": 180,  # 18% → below threshold
                 }},
             ]},
        ]
        svc = self._service(printers=printers)
        payload = svc.get_triage_payload()
        ext = payload["lanes"][4]
        self.assertEqual(ext["kind"], "external_spool")
        self.assertEqual(ext["count"], 1)
        item = ext["items"][0]
        self.assertEqual(item["kind"], "spool-low")
        self.assertEqual(item["printer_id"], "core-2")
        self.assertEqual(item["grams_left"], 180)
        self.assertAlmostEqual(item["percent"], 0.18, places=2)

    def test_spool_low_single_tool_assigned_spool_fallback(self):
        # Single-tool printer with only `assigned_spool` populated.
        printers = [
            {"printer_id": "core-3", "name": "Core One #3",
             "assigned_spool": {
                 "id": "S003", "material": "PLA", "color": "Red",
                 "grams": 120,
             },
             "assigned_spools": []},
        ]
        svc = self._service(printers=printers)
        payload = svc.get_triage_payload()
        ext = payload["lanes"][4]
        self.assertEqual(ext["count"], 1)
        self.assertEqual(ext["items"][0]["printer_id"], "core-3")

    def test_lanes_total_sums_across_all_lanes(self):
        items = [
            {"item_id": 1, "wo_id": "WO-001", "part_name": "p1",
             "status": "failed",
             "queued_at": "2026-05-18T13:00:00"},
            {"item_id": 2, "wo_id": "WO-001", "part_name": "p2",
             "status": "cancelled",
             "queued_at": "2026-05-18T13:01:00"},
        ]
        wos = [{"wo_id": "WO-001", "customer_name": "Hyperion"}]
        jobs = [
            {"job_id": 99, "status": "completed", "outcome": "unknown",
             "file_name": "j.gcode"},
        ]
        printers = [
            {"printer_id": "core-2", "name": "Core One #2",
             "assigned_spools": [
                 {"tool_index": 0, "spool": {
                     "id": "S002", "material": "PETG", "color": "Green",
                     "grams": 180,
                 }},
             ]},
        ]
        svc = self._service(queue_items=items, work_orders=wos,
                            print_jobs=jobs, printers=printers)
        payload = svc.get_triage_payload()
        self.assertEqual(payload["lanes"][0]["count"], 2)  # failed
        self.assertEqual(payload["lanes"][1]["count"], 1)  # qc
        self.assertEqual(payload["lanes"][4]["count"], 1)  # spool
        self.assertEqual(payload["lanes_total"], 4)

    def test_active_parts_excludes_terminal_states(self):
        items = [
            {"item_id": 1, "wo_id": "WO-001", "part_name": "active-1",
             "status": "printing", "sequence_number": 1, "total_quantity": 2,
             "queued_at": "2026-05-18T13:00:00"},
            {"item_id": 2, "wo_id": "WO-001", "part_name": "active-2",
             "status": "queued", "sequence_number": 2, "total_quantity": 2,
             "queued_at": "2026-05-18T13:01:00"},
            # Terminal — should NOT appear in active_parts.
            {"item_id": 3, "wo_id": "WO-001", "part_name": "done",
             "status": "completed",
             "queued_at": "2026-05-18T13:02:00"},
            {"item_id": 4, "wo_id": "WO-001", "part_name": "failed-p",
             "status": "failed",
             "queued_at": "2026-05-18T13:03:00"},
            {"item_id": 5, "wo_id": "WO-001", "part_name": "cancelled-p",
             "status": "cancelled",
             "queued_at": "2026-05-18T13:04:00"},
        ]
        wos = [{"wo_id": "WO-001", "customer_name": "Acme"}]
        svc = self._service(queue_items=items, work_orders=wos)
        payload = svc.get_triage_payload()

        active = payload["active_parts"]
        self.assertEqual(len(active), 2)
        names = {p["part_name"] for p in active}
        self.assertEqual(names, {"active-1", "active-2"})

    def test_active_parts_orders_printing_before_queued(self):
        items = [
            {"item_id": 1, "wo_id": "WO-001", "part_name": "q-first",
             "status": "queued",
             "queued_at": "2026-05-18T13:00:00"},
            {"item_id": 2, "wo_id": "WO-001", "part_name": "printing-now",
             "status": "printing",
             "queued_at": "2026-05-18T13:01:00"},
        ]
        wos = [{"wo_id": "WO-001", "customer_name": "Acme"}]
        svc = self._service(queue_items=items, work_orders=wos)
        payload = svc.get_triage_payload()
        self.assertEqual(payload["active_parts"][0]["part_name"],
                         "printing-now")
        self.assertEqual(payload["active_parts"][1]["part_name"], "q-first")


if __name__ == "__main__":
    unittest.main()
