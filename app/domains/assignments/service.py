"""
Assignment Service
===================
Business logic for printer-tool to spool assignment operations.
Validates input and delegates persistence to the repository.
"""


def _coerce_optional_bool(value, field_name: str) -> bool:
    """Coerce a value to bool, raising ValueError on bad input."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError(f"'{field_name}' must be a boolean")


class AssignmentService:
    """Encapsulates assignment business rules extracted from route handlers."""

    def __init__(self, assignment_repository, inventory_repository,
                 printer_name_resolver=None):
        """
        Args:
            assignment_repository: FilamentAssignmentDB instance.
            inventory_repository: FilamentInventoryDB instance (for spool
                existence checks and dried-date updates).
            printer_name_resolver: Optional callable(printer_id) -> str
                returning a human-readable label for conflict messages.
        """
        self.assignment_repo = assignment_repository
        self.inventory_repo = inventory_repository
        self._resolve_printer_name = printer_name_resolver or (lambda pid: pid)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_all_assignments(self):
        """Return all assignments (backward-compat flat + multi format)."""
        return self.assignment_repo.get_all_assignments()

    def get_printer_assignments(self, printer_id):
        """Return tool assignments for one printer."""
        return self.assignment_repo.get_printer_assignments(printer_id)

    def get_spool_assignments(self, spool_id):
        """Return all active assignments for a spool."""
        return self.assignment_repo.get_spool_assignments(spool_id)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def assign(self, printer_id, data):
        """Validate and assign a spool to a printer tool.

        Raises ValueError on validation failure (bad input, conflict).
        Raises KeyError if spool not found.
        Returns True on success.
        """
        if not data or not data.get("spool_id"):
            raise ValueError("Missing 'spool_id'")

        spool_id = data["spool_id"]

        spool = self.inventory_repo.get_by_id(spool_id)
        if not spool:
            raise KeyError("Spool not found")

        was_dried = _coerce_optional_bool(data.get("was_dried"), "was_dried")

        # TODO: validate tool_index against printer tool count
        tool_index = int(data.get("tool_index", 0))

        # Check for conflicts — spool already assigned elsewhere
        existing_assignments = self.assignment_repo.get_spool_assignments(
            spool_id
        )
        conflict = next(
            (
                a for a in existing_assignments
                if a["printer_id"] != printer_id
                or a["tool_index"] != tool_index
            ),
            None,
        )
        if conflict:
            location = self._format_assignment_location(
                conflict["printer_id"], conflict["tool_index"]
            )
            raise ValueError(
                f"Spool {spool_id} is already assigned to {location}"
            )

        # Idempotent: if same spool is already assigned to this tool,
        # just update dried date if requested
        current = self.assignment_repo.get_assignment(
            printer_id, tool_index=tool_index
        )
        if current and current.get("spool_id") == spool_id:
            if was_dried:
                self.inventory_repo.update_last_dried(spool_id)
            return True

        self.assignment_repo.assign(printer_id, spool_id,
                                    tool_index=tool_index)
        if was_dried:
            self.inventory_repo.update_last_dried(spool_id)
        return True

    def unassign(self, printer_id, tool_index=None, unassign_all=False):
        """Remove assignment(s). Returns True on success.

        Raises KeyError if no assignment found.
        """
        if unassign_all:
            success = self.assignment_repo.unassign_all(printer_id)
        else:
            success = self.assignment_repo.unassign(
                printer_id, tool_index=tool_index if tool_index is not None else 0
            )
        if not success:
            raise KeyError("No assignment found")
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _format_assignment_location(self, printer_id, tool_index):
        """Build a human-readable location string for conflict messages."""
        label = self._resolve_printer_name(printer_id)
        return f"{label} T{tool_index + 1}"
