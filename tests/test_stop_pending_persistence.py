"""`_stop_pending` disk-persistence tests.

Validates that the stop-pending markers survive a process restart so a
cancel issued moments before a `systemctl restart` still routes the
next printing->idle transition through the cancel handler.
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from farm_manager import PrintFarmManager, STOP_PENDING_TTL_SEC
from app.shared.constants import PrinterStatus


def _make_manager(tmpdir):
    m = PrintFarmManager.__new__(PrintFarmManager)
    m.printers = {
        "printer-1": {"client": None, "previous_status": PrinterStatus.IDLE},
        "printer-2": {"client": None, "previous_status": PrinterStatus.IDLE},
    }
    m._stop_pending = {}
    m._lock = threading.Lock()
    m.data_dir = tmpdir
    return m


def _read_state(tmpdir):
    with open(os.path.join(tmpdir, "server_state.json"), "r") as f:
        return json.load(f)


def _write_state(tmpdir, payload):
    with open(os.path.join(tmpdir, "server_state.json"), "w") as f:
        json.dump(payload, f)


class StopPendingPersistenceTests(unittest.TestCase):
    def test_save_includes_stop_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_manager(tmpdir)
            now = time.time()
            m._stop_pending["printer-1"] = now
            m._save_state()

            saved = _read_state(tmpdir)
            self.assertIn("stop_pending", saved)
            self.assertIn("printer-1", saved["stop_pending"])
            self.assertAlmostEqual(saved["stop_pending"]["printer-1"], now, delta=1)
            self.assertIn("previous_status", saved)

    def test_load_restores_recent_stop_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            recent_ts = time.time() - 30
            _write_state(tmpdir, {
                "previous_status": {"printer-1": PrinterStatus.PRINTING},
                "stop_pending": {"printer-1": recent_ts},
            })

            m = _make_manager(tmpdir)
            m.history_db = None
            m.job_repository = None
            m.queue_execution_repository = None
            m.runtime_state = None
            m._restore_previous_state()

            self.assertIn("printer-1", m._stop_pending)
            self.assertAlmostEqual(m._stop_pending["printer-1"], recent_ts, delta=1)

    def test_load_drops_stale_stop_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stale_ts = time.time() - (STOP_PENDING_TTL_SEC + 80)
            _write_state(tmpdir, {
                "previous_status": {"printer-1": PrinterStatus.PRINTING},
                "stop_pending": {"printer-1": stale_ts},
            })

            m = _make_manager(tmpdir)
            m.history_db = None
            m.job_repository = None
            m.queue_execution_repository = None
            m.runtime_state = None
            m._restore_previous_state()

            self.assertNotIn("printer-1", m._stop_pending)

    def test_save_after_clear_removes_stop_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_manager(tmpdir)
            m._stop_pending["printer-1"] = time.time()
            m._save_state()
            m._stop_pending.pop("printer-1", None)
            m._save_state()

            saved = _read_state(tmpdir)
            self.assertEqual(saved["stop_pending"], {})


if __name__ == "__main__":
    unittest.main()
