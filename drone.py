"""
Drone Controller (mock/placeholder)
=====================================
Provides mock status and accepts mission commands.
When the real drone system is ready, replace these methods.
"""

import time
from datetime import datetime, timezone


class DroneController:
    """
    Placeholder drone controller.
    Provides mock status and accepts mission commands.
    """

    def __init__(self):
        self.status = {
            "connected": False,
            "battery_percent": 0,
            "position": {"x": 0, "y": 0, "z": 0},
            "state": "docked",  # docked, flying, returning, charging
            "current_mission": None,
        }
        self.mission_log = []

    def get_status(self) -> dict:
        return self.status.copy()

    def send_mission(self, mission_type: str,
                     target: str = None) -> dict:
        """
        Queue a mission for the drone.
        mission_type: 'patrol_all', 'inspect_printer', 'return_to_dock'
        target: printer_id (for inspect_printer)
        """
        mission = {
            "id": f"mission_{int(time.time())}",
            "type": mission_type,
            "target": target,
            "status": "queued",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.mission_log.insert(0, mission)
        # Keep last 50 missions
        self.mission_log = self.mission_log[:50]
        return mission

    def get_mission_log(self) -> list:
        return self.mission_log.copy()
