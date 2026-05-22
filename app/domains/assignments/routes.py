"""Flask routes for printer-to-spool assignments."""

from flask import Blueprint, jsonify, request


assignments_api = Blueprint("assignments_api", __name__)

_assignment_service = None
_farm_manager = None


def register_assignments_routes(app, assignment_service, farm_manager):
    """Wire up the assignments blueprint."""
    global _assignment_service, _farm_manager
    _assignment_service = assignment_service
    _farm_manager = farm_manager
    app.register_blueprint(assignments_api)


@assignments_api.route("/api/assignments")
def api_assignments():
    """Get all printer-to-spool assignments."""
    return jsonify(_assignment_service.get_all_assignments())


@assignments_api.route("/api/assignments/<printer_id>", methods=["GET"])
def api_printer_assignments(printer_id):
    """Get all tool assignments for a specific printer."""
    if printer_id not in _farm_manager.printers:
        return jsonify({"error": "Unknown printer"}), 404
    return jsonify(_assignment_service.get_printer_assignments(printer_id))


@assignments_api.route("/api/assignments/<printer_id>", methods=["POST"])
def api_assign_spool(printer_id):
    """Assign a spool to a printer tool.

    Body: { "spool_id": "...", "tool_index": 0, "was_dried": true }
    tool_index defaults to 0 if not provided (backward compat).
    """
    if printer_id not in _farm_manager.printers:
        return jsonify({"error": "Unknown printer"}), 404
    try:
        _assignment_service.assign(printer_id, request.get_json())
        return jsonify({"success": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except KeyError:
        return jsonify({"error": "Spool not found"}), 404


@assignments_api.route("/api/assignments/<printer_id>", methods=["DELETE"])
def api_unassign_spool(printer_id):
    """Remove spool assignment from a printer tool.

    Query param: ?tool_index=0 (defaults to 0).
    Use ?all=1 to remove all tool assignments.
    """
    try:
        _assignment_service.unassign(
            printer_id,
            tool_index=request.args.get("tool_index", 0, type=int),
            unassign_all=request.args.get("all") == "1",
        )
        return jsonify({"success": True})
    except KeyError:
        return jsonify({"error": "No assignment found"}), 404
