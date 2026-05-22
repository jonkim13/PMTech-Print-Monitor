"""Flask routes for printer status and actions."""

from flask import Blueprint, jsonify, request


printers_api = Blueprint("printers_api", __name__)

_farm_manager = None


def register_printers_routes(app, farm_manager):
    """Wire up the printers blueprint."""
    global _farm_manager
    _farm_manager = farm_manager
    app.register_blueprint(printers_api)


@printers_api.route("/api/printers")
def api_printers():
    """Get status of all printers."""
    return jsonify(_farm_manager.get_all_status())


@printers_api.route("/api/printers/<printer_id>")
def api_printer(printer_id):
    """Get status of a specific printer."""
    status = _farm_manager.get_printer_status(printer_id)
    if status.get("error"):
        return jsonify(status), 404
    return jsonify(status)


@printers_api.route("/api/printers/<printer_id>/files")
def api_printer_files(printer_id):
    """Get file listing from a printer."""
    client = _farm_manager.get_printer_client(printer_id)
    if not client:
        return jsonify({"error": "Unknown printer"}), 404
    storage = request.args.get("storage")
    return jsonify(client.get_files(storage=storage))


@printers_api.route("/api/printers/<printer_id>/stop", methods=["POST"])
def api_printer_stop(printer_id):
    """Stop the current print job on a printer."""
    client = _farm_manager.get_printer_client(printer_id)
    if not client:
        return jsonify({"error": "Unknown printer"}), 404
    # Flag stop-pending BEFORE sending the DELETE so the poller can't
    # observe printing->idle and classify it as a completion while we
    # are still waiting on the HTTP response. The marker expires after
    # STOP_PENDING_TTL_SEC so a stale set can't mask a later legitimate
    # completion.
    _farm_manager.mark_stop_pending(printer_id)
    result = client.stop_job()
    if result.get("success"):
        _farm_manager.record_stopped_printer(printer_id)
    status_code = 200 if result.get("success") else 500
    return jsonify(result), status_code
