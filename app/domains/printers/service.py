"""Printer-facing read helpers.

This module intentionally stays thin. It centralizes read-only access to the
current printer client registry while the legacy farm manager remains the
orchestrator for polling, events, and side effects.
"""

import copy
from contextlib import nullcontext


class PrinterStatusService:
    """Read current printer status from the existing client registry."""

    def __init__(self, printers, lock=None):
        self.printers = printers
        self.lock = lock

    def _locked(self):
        return self.lock if self.lock else nullcontext()

    def get_client(self, printer_id):
        """Return the configured printer client, if it exists."""
        printer_data = self.printers.get(printer_id)
        if printer_data:
            return printer_data["client"]
        return None

    def get_model(self, printer_id):
        """Return the printer model from its configured client."""
        client = self.get_client(printer_id)
        if client:
            return client.model
        return "unknown"

    def get_tool_count(self, printer_id):
        """Return the number of tool heads for the known printer model."""
        model = self.get_model(printer_id)
        if model == "xl":
            return 5
        return 1

    def get_all_status(self, enrich_status=None):
        """Return deep-copied status for every printer."""
        with self._locked():
            result = []
            for printer_id, printer_data in self.printers.items():
                status = copy.deepcopy(printer_data["client"].state)
                if enrich_status:
                    status = enrich_status(printer_id, status)
                result.append(status)
            return result

    def get_status(self, printer_id, enrich_status=None):
        """Return a deep-copied status dict for one printer."""
        printer_data = self.printers.get(printer_id)
        if printer_data:
            with self._locked():
                status = copy.deepcopy(printer_data["client"].state)
                if enrich_status:
                    status = enrich_status(printer_id, status)
                return status
        return {"error": "Unknown printer: {}".format(printer_id)}
