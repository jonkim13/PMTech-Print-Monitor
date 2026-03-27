"""
Work Order Routes
==================
API endpoints for work orders, production queue,
and integrated print-from-queue functionality.
"""

import os

from flask import Blueprint, jsonify, request
from werkzeug.utils import secure_filename

work_order_api = Blueprint("work_order_api", __name__)

_wo_db = None
_farm_manager = None
_ALLOWED_UPLOAD_EXTENSIONS = {".gcode", ".gco", ".g", ".bgcode"}


def register_work_order_routes(app, wo_db, farm_manager):
    """Wire up the work order blueprint."""
    global _wo_db, _farm_manager
    _wo_db = wo_db
    _farm_manager = farm_manager
    app.register_blueprint(work_order_api)


def _validate_operator_initials(value):
    initials = str(value or "").strip()
    if not initials:
        raise ValueError("operator_initials is required when starting a print")
    return initials


# ------------------------------------------------------------------
# Work Orders
# ------------------------------------------------------------------

@work_order_api.route("/api/workorders", methods=["POST"])
def api_create_work_order():
    """Create a new work order with line items.

    Body: {
        "customer_name": "Acme Corp",
        "line_items": [
            {"part_name": "Widget", "material": "PLA", "quantity": 10},
            {"part_name": "Bracket", "material": "PETG", "quantity": 5}
        ]
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    customer = data.get("customer_name", "").strip()
    if not customer:
        return jsonify({"error": "Missing customer_name"}), 400

    items = data.get("line_items", [])
    if not items:
        return jsonify({"error": "At least one line item required"}), 400

    for i, li in enumerate(items):
        if not li.get("part_name", "").strip():
            return jsonify({
                "error": "Line item {} missing part_name".format(i + 1)
            }), 400
        if not li.get("material", "").strip():
            return jsonify({
                "error": "Line item {} missing material".format(i + 1)
            }), 400
        try:
            qty = int(li.get("quantity", 1))
            if qty < 1:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({
                "error": "Line item {} has invalid quantity".format(i + 1)
            }), 400

    result = _wo_db.create_work_order(customer, items)
    return jsonify(result), 201


@work_order_api.route("/api/workorders")
def api_list_work_orders():
    """List all work orders with summary counts."""
    status = request.args.get("status")
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    orders = _wo_db.get_all_work_orders(status=status,
                                         limit=limit, offset=offset)
    return jsonify(orders)


@work_order_api.route("/api/workorders/<wo_id>")
def api_get_work_order(wo_id):
    """Get work order detail with line items and queue items."""
    wo = _wo_db.get_work_order(wo_id)
    if not wo:
        return jsonify({"error": "Work order not found"}), 404
    return jsonify(wo)


@work_order_api.route("/api/workorders/<wo_id>", methods=["PATCH"])
def api_update_work_order(wo_id):
    """Update work order status. Body: {"status": "cancelled"}"""
    data = request.get_json()
    if not data or "status" not in data:
        return jsonify({"error": "Missing status"}), 400

    new_status = data["status"]
    if new_status not in ("open", "in_progress", "completed", "cancelled"):
        return jsonify({"error": "Invalid status"}), 400

    if new_status == "cancelled":
        success = _wo_db.cancel_work_order(wo_id)
    else:
        success = _wo_db.update_work_order_status(wo_id, new_status)

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Work order not found"}), 404


@work_order_api.route("/api/workorders/<wo_id>", methods=["DELETE"])
def api_delete_work_order(wo_id):
    """Cancel a work order."""
    success = _wo_db.cancel_work_order(wo_id)
    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Work order not found"}), 404


# ------------------------------------------------------------------
# Production Queue
# ------------------------------------------------------------------

@work_order_api.route("/api/queue")
def api_queue():
    """Get the production queue, FIFO ordered."""
    status = request.args.get("status")
    limit = request.args.get("limit", 200, type=int)
    items = _wo_db.get_queue(status=status, limit=limit)
    return jsonify(items)


@work_order_api.route("/api/queue/stats")
def api_queue_stats():
    """Get queue summary counts."""
    return jsonify(_wo_db.get_queue_stats())


@work_order_api.route("/api/queue/<int:queue_id>", methods=["PATCH"])
def api_update_queue_item(queue_id):
    """Update a queue item status.

    Body: {"status": "queued"} to re-queue a failed item.
    """
    data = request.get_json()
    if not data or "status" not in data:
        return jsonify({"error": "Missing status"}), 400

    new_status = data["status"]
    if new_status == "queued":
        success = _wo_db.requeue_item(queue_id)
    elif new_status == "completed":
        success = _wo_db.complete_queue_item(queue_id)
    elif new_status == "failed":
        success = _wo_db.fail_queue_item(queue_id)
    else:
        return jsonify({"error": "Invalid status"}), 400

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Queue item not found or wrong state"}), 404


@work_order_api.route("/api/queue/<int:queue_id>/print", methods=["POST"])
def api_print_queue_item(queue_id):
    """Assign a printer, upload gcode, and start printing.

    Expects multipart form data:
    - printer_id: which printer to use
    - file: the gcode file
    - operator_initials: required traceability field
    """
    qi = _wo_db.get_queue_item(queue_id)
    if not qi:
        return jsonify({"error": "Queue item not found"}), 404
    if qi["status"] not in ("queued", "failed"):
        return jsonify({
            "error": "Item is not in a printable state (status: {})"
                     .format(qi["status"])
        }), 400

    printer_id = request.form.get("printer_id")
    if not printer_id:
        return jsonify({"error": "Missing printer_id"}), 400

    try:
        operator_initials = _validate_operator_initials(
            request.form.get("operator_initials")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    client = _farm_manager.get_printer_client(printer_id)
    if not client:
        return jsonify({"error": "Unknown printer"}), 404

    # Check printer is idle
    status = _farm_manager.get_printer_status(printer_id)
    if status.get("status") not in ("idle", "finished"):
        return jsonify({
            "error": "Printer is not idle (status: {})"
                     .format(status.get("status", "unknown"))
        }), 400

    if "file" not in request.files:
        return jsonify({"error": "No gcode file provided"}), 400

    uploaded = request.files["file"]
    if not uploaded.filename:
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename(uploaded.filename)
    if not filename:
        return jsonify({"error": "Invalid filename"}), 400

    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({"error": "Unsupported file type: {}".format(ext)}), 400

    file_data = uploaded.read()

    # Upload to printer with print-after-upload
    _farm_manager.record_pending_print_start(
        printer_id, filename, operator_initials
    )

    try:
        result = client.upload_gcode(file_data, filename, print_after=True)
    except Exception:
        _farm_manager.clear_pending_print_start(
            printer_id, filename, operator_initials
        )
        raise

    if not result.get("success"):
        _farm_manager.clear_pending_print_start(
            printer_id, filename, operator_initials
        )
        return jsonify({
            "error": "Upload failed: {}".format(result.get("error", "unknown"))
        }), 500

    # Re-queue failed items first so assign works
    if qi["status"] == "failed":
        _wo_db.requeue_item(queue_id)

    # Update queue item
    printer_name = status.get("name", printer_id)
    _wo_db.assign_queue_item(queue_id, printer_id, printer_name, filename)

    return jsonify({
        "success": True,
        "message": "Uploaded {} to {} and started printing".format(
            filename, printer_name),
        "queue_id": queue_id,
        "printer_id": printer_id,
    })
