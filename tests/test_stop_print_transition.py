import os
import sys
import threading
import unittest

from flask import Flask

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from farm_manager import PrintFarmManager
from app.domains.monitoring.runtime_state import MonitoringRuntimeState
from app.shared.constants import PrinterStatus
from routes import register_routes


class FakePollingClient:
    def __init__(self, state):
        self.state = state

    def poll(self):
        return dict(self.state)


class RecordingTransitionHandler:
    def __init__(self):
        self.calls = []

    def handle_print_completed(self, printer_id, printer_name, state, client,
                               event):
        self.calls.append(("completed", printer_id, event))

    def handle_print_stopped(self, printer_id, printer_name, state, client,
                             event):
        self.calls.append(("stopped", printer_id, event))

    def handle_print_started(self, printer_id, printer_name, state, client,
                             event):
        self.calls.append(("started", printer_id, event))

    def handle_print_failed(self, printer_id, printer_name, state, client,
                            event):
        self.calls.append(("failed", printer_id, event))


class FakeStopClient:
    def __init__(self, result):
        self.result = result

    def stop_job(self):
        return dict(self.result)


class RecordingFarmManager:
    def __init__(self, client):
        self.client = client
        self.recorded_stops = []
        self.stop_pending_marks = []

    def get_printer_client(self, printer_id):
        return self.client if printer_id == "printer-1" else None

    def record_stopped_printer(self, printer_id):
        self.recorded_stops.append(printer_id)

    def mark_stop_pending(self, printer_id):
        self.stop_pending_marks.append(printer_id)


def build_manager(client, handler):
    runtime_state = MonitoringRuntimeState()
    manager = PrintFarmManager.__new__(PrintFarmManager)
    manager.printers = {
        "printer-1": {
            "client": client,
            "previous_status": PrinterStatus.PRINTING,
        }
    }
    manager.runtime_state = runtime_state
    manager._print_start_times = runtime_state.print_start_times
    manager._active_job_ids = runtime_state.active_job_ids
    manager._active_queue_job_ids = runtime_state.active_queue_job_ids
    manager._pending_print_starts = runtime_state.pending_print_starts
    manager._stopped_printers = runtime_state.stopped_printers
    manager._stop_pending = {}
    manager.history_db = None
    manager._lock = threading.Lock()
    manager.transition_handler = handler
    return manager


class StopPrintTransitionTests(unittest.TestCase):
    def test_stop_marker_dispatches_stopped_instead_of_completed(self):
        handler = RecordingTransitionHandler()
        client = FakePollingClient({
            "name": "Printer 1",
            "status": PrinterStatus.IDLE,
            "job": {"filename": "part.gcode"},
        })
        manager = build_manager(client, handler)

        manager.record_stopped_printer("printer-1")
        manager.poll_printer("printer-1")

        self.assertEqual([call[0] for call in handler.calls], ["stopped"])
        self.assertEqual(manager._stopped_printers, set())

    def test_unmarked_printing_to_idle_dispatches_completed(self):
        handler = RecordingTransitionHandler()
        client = FakePollingClient({
            "name": "Printer 1",
            "status": PrinterStatus.IDLE,
            "job": {"filename": "part.gcode"},
        })
        manager = build_manager(client, handler)

        manager.poll_printer("printer-1")

        self.assertEqual([call[0] for call in handler.calls], ["completed"])

    def test_stop_endpoint_records_successful_stop_without_shape_change(self):
        manager = RecordingFarmManager(
            FakeStopClient({"success": True, "message": "stopped"})
        )
        app = Flask(__name__)
        register_routes(app, manager, None, None, None)

        response = app.test_client().post("/api/printers/printer-1/stop")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"success": True, "message": "stopped"},
        )
        self.assertEqual(manager.recorded_stops, ["printer-1"])


if __name__ == "__main__":
    unittest.main()
