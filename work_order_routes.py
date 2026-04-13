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

_work_order_service = None
_queue_service = None
_farm_manager = None
_gcode_uploads_dir = None
_execution_service = None
_ALLOWED_UPLOAD_EXTENSIONS = {".gcode", ".gco", ".g", ".bgcode"}


def register_work_order_routes(app, farm_manager,
                               gcode_uploads_dir=None,
                               upload_workflow=None,
                               execution_service=None,
                               work_order_service=None,
                               queue_service=None):
    """Wire up the work order blueprint."""
    global _work_order_service, _queue_service
    global _farm_manager, _gcode_uploads_dir, _execution_service
    _work_order_service = work_order_service
    _queue_service = queue_service
    _farm_manager = farm_manager
    _gcode_uploads_dir = gcode_uploads_dir
    _execution_service = execution_service or upload_workflow
    app.register_blueprint(work_order_api)


def _validate_operator_initials(value):
    initials = str(value or "").strip()
    if not initials:
        raise ValueError("operator_initials is required when starting a print")
    return initials


def _workflow_status_code(result):
    status_code = result.get("http_status") or result.get("status_code")
    if status_code is None:
        status_code = 200 if result.get("ok") or result.get("success") else 500
    return status_code


def _log_route_failure(route_name: str, printer_id: str,
                       result: dict, status_code: int) -> None:
    downstream = result.get("downstream_result") or result
    details = downstream.get("details") or {}
    downstream_message = (
        details.get("downstream_message")
        or result.get("message")
        or result.get("error")
    )
    print("[UPLOAD][ROUTE] {} failure for {}: status_code={} "
          "error_type={} http_status={} downstream_message={}".format(
              route_name, printer_id, status_code,
              result.get("error_type"),
              result.get("http_status"),
              downstream_message))
    print("[UPLOAD][ROUTE] {} structured_result={}".format(
        route_name, downstream
    ))


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


def _print_queue_items(queue_ids):
    """Assign one or more queue items, upload, verify, and start printing."""
    requested_job_id = request.form.get("job_id", type=int)
    try:
        queue_ids, queue_items = _queue_service.resolve_print_request_items(
            queue_ids, requested_job_id=requested_job_id
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409

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
    if not _execution_service:
        return jsonify({"error": "Upload workflow unavailable"}), 500

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

    printer_name = status.get("name", printer_id)
    try:
        execution = _queue_service.start_queue_job_execution(
            queue_ids,
            printer_id,
            printer_name,
            filename,
            operator_initials=operator_initials,
            job_id=requested_job_id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 409

    queue_job_id = execution["queue_job_id"]
    work_order_job_id = execution["job_id"]
    queue_ids = execution["queue_ids"]

    result = _execution_service.create_and_upload(
        printer_id=printer_id,
        uploaded_file=uploaded,
        original_filename=filename,
        start_print=True,
        operator_initials=operator_initials,
        queue_job_id=queue_job_id,
        work_order_job_id=work_order_job_id,
    )
    result.update({
        "queue_ids": queue_ids,
        "queue_job_id": queue_job_id,
        "job_id": work_order_job_id,
        "printer_id": printer_id,
        "wo_id": execution["wo_id"],
    })

    if not result.get("ok"):
        status_code = _workflow_status_code(result)
        if status_code >= 500:
            _log_route_failure("_print_queue_items", printer_id, result,
                               status_code)
        print("[WORKORDER] Queue job #{} did not reach printing for job #{} "
              "on {}: {} ({})".format(
                  queue_job_id, work_order_job_id, printer_name,
                  result.get("message"), result.get("error_type")))
        return jsonify(result), status_code

    print("[WORKORDER] Queue job #{} confirmed printing for job #{} on {} "
          "with {} part{}".format(
              queue_job_id, work_order_job_id, printer_name, len(queue_ids),
              "" if len(queue_ids) == 1 else "s"))
    return jsonify(result), _workflow_status_code(result)


# ------------------------------------------------------------------
# Work Orders
# ------------------------------------------------------------------

@work_order_api.route("/api/workorders", methods=["POST"])
def api_create_work_order():
    """Create a new work order with line items."""
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

    result = _work_order_service.create_work_order(customer, items)
    return jsonify(result), 201


@work_order_api.route("/api/workorders")
def api_list_work_orders():
    """List all work orders with summary counts."""
    status = request.args.get("status")
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    orders = _work_order_service.get_work_orders(
        status=status, limit=limit, offset=offset
    )
    return jsonify(orders)


@work_order_api.route("/api/workorders/<wo_id>")
def api_get_work_order(wo_id):
    """Get work order detail with line items and queue items."""
    wo = _work_order_service.get_work_order(wo_id)
    if not wo:
        return jsonify({"error": "Work order not found"}), 404
    return jsonify(wo)


@work_order_api.route("/api/workorders/<wo_id>/jobs")
def api_get_work_order_jobs(wo_id):
    """List persisted jobs for a work order."""
    jobs = _work_order_service.get_work_order_jobs(wo_id)
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
        job = _work_order_service.create_job(wo_id, queue_ids=queue_ids)
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
        job = _work_order_service.assign_queue_items_to_job(
            wo_id, job_id, queue_ids
        )
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

    success = _work_order_service.update_work_order_status(wo_id, new_status)

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Work order not found"}), 404


@work_order_api.route("/api/workorders/<wo_id>", methods=["DELETE"])
def api_delete_work_order(wo_id):
    """Cancel a work order."""
    success = _work_order_service.cancel_work_order(wo_id)
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
    items = _queue_service.get_queue(status=status, limit=limit)
    return jsonify(items)


@work_order_api.route("/api/queue/stats")
def api_queue_stats():
    """Get queue summary counts."""
    return jsonify(_queue_service.get_queue_stats())


@work_order_api.route("/api/queue/<int:queue_id>", methods=["PATCH"])
def api_update_queue_item(queue_id):
    """Update a queue item status."""
    data = request.get_json()
    if not data or "status" not in data:
        return jsonify({"error": "Missing status"}), 400

    new_status = data["status"]
    if new_status == "queued":
        success = _queue_service.requeue_item(queue_id)
    elif new_status == "completed":
        success = _queue_service.complete_queue_item(queue_id)
    elif new_status == "failed":
        success = _queue_service.fail_queue_item(queue_id)
    else:
        return jsonify({"error": "Invalid status"}), 400

    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Queue item not found or wrong state"}), 404


@work_order_api.route("/api/queue/<int:queue_id>/print", methods=["POST"])
def api_print_queue_item(queue_id):
    """Assign a printer, upload gcode, and start printing."""
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


@work_order_api.route("/api/queue/<int:queue_id>/retry", methods=["POST"])
def api_retry_queue_item(queue_id):
    """Retry a failed upload/start attempt using the stored upload session."""
    if not _execution_service:
        return jsonify({"error": "Upload workflow unavailable"}), 500

    item = _queue_service.get_queue_item(queue_id)
    if not item:
        return jsonify({"error": "Queue item not found"}), 404
    if item.get("status") not in ("upload_failed", "start_failed"):
        return jsonify({"error": "Queue item is not retryable"}), 409
    if not item.get("upload_session_id"):
        return jsonify({"error": "No upload session is linked to this item"}), 409
    if not item.get("assigned_printer_id"):
        return jsonify({"error": "No printer is assigned to this item"}), 409

    printer_status = _farm_manager.get_printer_status(item["assigned_printer_id"])
    if printer_status.get("status") not in ("idle", "finished"):
        return jsonify({
            "error": "Printer is not idle (status: {})".format(
                printer_status.get("status", "unknown")
            )
        }), 409

    data = request.get_json() or {}
    operator_initials = data.get("operator_initials")
    if operator_initials:
        try:
            operator_initials = _validate_operator_initials(operator_initials)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    result = _execution_service.retry_session(
        item["upload_session_id"],
        start_print=True,
        operator_initials=operator_initials,
    )
    result.update({
        "queue_id": queue_id,
        "queue_job_id": item.get("queue_job_id"),
        "job_id": item.get("job_id"),
        "printer_id": item.get("assigned_printer_id"),
        "wo_id": item.get("wo_id"),
    })
    status_code = _workflow_status_code(result)
    if status_code >= 500:
        _log_route_failure("api_retry_queue_item",
                           item.get("assigned_printer_id"), result,
                           status_code)
    return jsonify(result), status_code
