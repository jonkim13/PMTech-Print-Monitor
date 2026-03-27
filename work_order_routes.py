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


def _parse_queue_ids(values, default_queue_id=None):
    """Parse queue ids from form values or a route parameter."""
    raw_ids = list(values or [])
    if not raw_ids and default_queue_id is not None:
        raw_ids = [default_queue_id]

    queue_ids = []
    seen = set()
    for raw_id in raw_ids:
        parts = str(raw_id).split(",")
        for part in parts:
            value = part.strip()
            if not value:
                continue
            try:
                queue_id = int(value)
            except (TypeError, ValueError):
                raise ValueError("Invalid queue_id: {}".format(value))
            if queue_id in seen:
                continue
            seen.add(queue_id)
            queue_ids.append(queue_id)

    if not queue_ids:
        raise ValueError("At least one part must be selected")
    return queue_ids


def _validate_queue_print_items(queue_ids):
    """Validate a queue selection for single- or multi-part printing."""
    items = _wo_db.get_queue_items(queue_ids)
    if len(items) != len(queue_ids):
        raise LookupError("One or more selected parts were not found")

    if any(item["status"] not in ("queued", "failed") for item in items):
        raise ValueError(
            "Selected parts must be queued or failed before printing"
        )

    wo_ids = {item["wo_id"] for item in items}
    if len(wo_ids) != 1:
        raise ValueError(
            "Selected parts must belong to the same work order"
        )

    return items


def _validate_selected_job(queue_items, requested_job_id=None):
    """Ensure a print selection stays within one persisted work-order job."""
    job_ids = {
        item.get("job_id") for item in queue_items if item.get("job_id")
    }

    if requested_job_id is not None:
        if any(item.get("job_id") not in (None, requested_job_id)
               for item in queue_items):
            raise ValueError(
                "Selected parts must belong to the requested job"
            )
        return

    if len(job_ids) > 1:
        raise ValueError(
            "Selected parts must belong to the same job before printing"
        )


def _print_queue_items(queue_ids):
    """Assign one or more queue items, upload gcode, and start printing."""
    try:
        queue_ids = _parse_queue_ids(queue_ids)
        queue_items = _validate_queue_print_items(queue_ids)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404

    printer_id = request.form.get("printer_id")
    if not printer_id:
        return jsonify({"error": "Missing printer_id"}), 400

    try:
        operator_initials = _validate_operator_initials(
            request.form.get("operator_initials")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    requested_job_id = request.form.get("job_id", type=int)
    try:
        _validate_selected_job(queue_items, requested_job_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    client = _farm_manager.get_printer_client(printer_id)
    if not client:
        return jsonify({"error": "Unknown printer"}), 404

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

    printer_name = status.get("name", printer_id)
    queue_job_id = _wo_db.assign_queue_items(
        queue_ids, printer_id, printer_name, filename,
        operator_initials=operator_initials,
        job_id=requested_job_id,
    )
    if queue_job_id is None:
        return jsonify({
            "error": "Selected parts could not be assigned to a print job"
        }), 409

    updated_items = _wo_db.get_queue_items(queue_ids)
    work_order_job_id = (updated_items[0].get("job_id")
                         if updated_items else requested_job_id)

    return jsonify({
        "success": True,
        "message": "Uploaded {} to {} and started printing {} part{}".format(
            filename, printer_name, len(queue_items),
            "" if len(queue_items) == 1 else "s"
        ),
        "queue_ids": queue_ids,
        "queue_job_id": queue_job_id,
        "job_id": work_order_job_id,
        "printer_id": printer_id,
        "wo_id": queue_items[0]["wo_id"],
    })


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


@work_order_api.route("/api/workorders/<wo_id>/jobs")
def api_get_work_order_jobs(wo_id):
    """List persisted jobs for a work order."""
    jobs = _wo_db.get_work_order_jobs(wo_id)
    if jobs is None:
        return jsonify({"error": "Work order not found"}), 404
    return jsonify(jobs)


@work_order_api.route("/api/workorders/<wo_id>/jobs", methods=["POST"])
def api_create_work_order_job(wo_id):
    """Create a persisted job for a work order."""
    data = request.get_json(silent=True) or {}

    queue_ids = []
    if data.get("queue_ids") is not None:
        try:
            queue_ids = _parse_queue_ids(data.get("queue_ids"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    try:
        job = _wo_db.create_job(wo_id, queue_ids=queue_ids)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404

    return jsonify({
        "success": True,
        "job": job,
        "assigned_count": len(queue_ids),
    }), 201


@work_order_api.route("/api/workorders/<wo_id>/jobs/<int:job_id>/assign",
                      methods=["POST"])
def api_assign_work_order_job_items(wo_id, job_id):
    """Assign selected queue items to an existing persisted job."""
    data = request.get_json()
    if not data or data.get("queue_ids") is None:
        return jsonify({"error": "Missing queue_ids"}), 400

    try:
        queue_ids = _parse_queue_ids(data.get("queue_ids"))
        job = _wo_db.assign_queue_items_to_job(wo_id, job_id, queue_ids)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404

    return jsonify({
        "success": True,
        "job": job,
        "assigned_count": len(queue_ids),
    })


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
    return _print_queue_items([queue_id])


@work_order_api.route("/api/queue/print", methods=["POST"])
def api_print_queue_items():
    """Assign one or more selected queue items to a single print job."""
    queue_ids = request.form.getlist("queue_ids")
    if not queue_ids:
        single = request.form.get("queue_id")
        if single:
            queue_ids = [single]
    return _print_queue_items(queue_ids)
