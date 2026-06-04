"""
Work Order Routes
==================
API endpoints for work orders, production queue,
and integrated print-from-queue functionality.
"""

from flask import Blueprint, jsonify, render_template, request

from app.domains.queue.service import (
    InvalidPrintRequestError,
    QueueExecutionConflictError,
    QueueItemNotFoundError,
)
from app.domains.work_orders.service import DeliveryStateError

work_order_api = Blueprint("work_order_api", __name__)

_work_order_service = None
_queue_service = None
_triage_service = None
_farm_manager = None
_gcode_uploads_dir = None
_execution_service = None


def register_work_order_routes(app, farm_manager,
                               gcode_uploads_dir=None,
                               upload_workflow=None,
                               execution_service=None,
                               work_order_service=None,
                               queue_service=None,
                               triage_service=None):
    """Wire up the work order blueprint."""
    global _work_order_service, _queue_service, _triage_service
    global _farm_manager, _gcode_uploads_dir, _execution_service
    _work_order_service = work_order_service
    _queue_service = queue_service
    _triage_service = triage_service
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


def _dispatch_print_request(queue_ids):
    """Translate Flask request context into a QueueService print call."""
    uploaded = request.files.get("file")
    try:
        result = _queue_service.start_print_request(
            printer_id=request.form.get("printer_id"),
            queue_ids=queue_ids,
            requested_job_id=request.form.get("job_id", type=int),
            uploaded_file=uploaded,
            operator_initials=request.form.get("operator_initials"),
        )
    except InvalidPrintRequestError as exc:
        return jsonify({"error": str(exc)}), 400
    except QueueItemNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except QueueExecutionConflictError as exc:
        return jsonify({"error": str(exc)}), 409

    status_code = _workflow_status_code(result)
    if status_code >= 500 and not result.get("ok"):
        _log_route_failure(
            "api_print_queue", result.get("printer_id"), result, status_code,
        )
    return jsonify(result), status_code


# ------------------------------------------------------------------
# Work Orders
# ------------------------------------------------------------------

@work_order_api.route("/api/workorders", methods=["POST"])
def api_create_work_order():
    """Create a new work order with Internal line items and/or jobs.

    Phase G — the body may carry Internal ``line_items`` (Part/Material/
    Qty, expanded into queue_items exactly as before) and/or a ``jobs``
    list of non-Internal job specs (External: vendor + external_process;
    Design: designer + optional requirements). At least one of the two
    must be present. Everything is created in one transaction (see
    ``WorkOrderService.create_work_order``); an invalid job spec rejects
    the whole request and creates nothing. A body with only
    ``line_items`` behaves exactly as the pre-Phase-G endpoint did.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    customer = data.get("customer_name", "").strip()
    if not customer:
        return jsonify({"error": "Missing customer_name"}), 400

    items = data.get("line_items") or []
    raw_jobs = data.get("jobs") or []
    if not items and not raw_jobs:
        return jsonify({
            "error": "At least one line item or job is required"
        }), 400

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

    # Normalize job specs; authoritative per-type validation lives in
    # the service (reused from create_job) and runs before the
    # transaction opens, so a bad spec creates nothing.
    jobs = [{
        "job_type": (spec.get("job_type") or "").strip(),
        "vendor": _opt_str(spec.get("vendor")),
        "external_process": _opt_str(spec.get("external_process")),
        "designer": _opt_str(spec.get("designer")),
        "requirements": _opt_str(spec.get("requirements")),
    } for spec in raw_jobs]

    due_date = data.get("due_date")
    if due_date is not None:
        due_date = str(due_date).strip() or None

    try:
        result = _work_order_service.create_work_order(
            customer, items, due_date=due_date, jobs=jobs
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
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
    """Get work order detail with line items and queue items.

    Phase 2.5c extended the payload to include per-job inspection
    summaries, a normalized counts block (for the stacked progress
    bar), and a synthesized activity timeline. See WorkOrderService
    `_attach_*` helpers.
    """
    wo = _work_order_service.get_work_order(wo_id)
    if not wo:
        return jsonify({"error": "Work order not found"}), 404
    return jsonify(wo)


# Phase 2.5c — hyphenated alias matching the new HTML route convention.
# Kept alongside the legacy `/api/workorders/<wo_id>` endpoint so existing
# callers don't break. New JS modules should prefer this hyphenated name.
@work_order_api.route("/api/work-orders/<wo_id>")
def api_get_work_order_hyphenated(wo_id):
    return api_get_work_order(wo_id)


# ------------------------------------------------------------------
# Phase 2.5c — WO Detail HTML route.
# Server-renders the full WO Detail page; client-side JS polls
# /api/work-orders/<wo_id> every 2.5s for partial updates.
# ------------------------------------------------------------------
_BACK_TARGETS = {
    "triage": ("/?tab=workorders", "Triage"),
    "all": ("/?tab=workorders&subtab=orders", "All Orders"),
    "dashboard": ("/?tab=dashboard", "Dashboard"),
}


@work_order_api.route("/work-orders/<wo_id>")
def page_work_order_detail(wo_id):
    """Render the WO Detail page.

    Query params:
        ?from=triage|all|dashboard  — drives the breadcrumb back link.
        ?focus=JOB-x or P-xx        — pre-expands that job + adds the
                                       'DEEP-LINKED' pill.
    """
    wo = _work_order_service.get_work_order(wo_id)
    if not wo:
        return render_template(
            "wo_detail_404.html",
            wo_id=wo_id,
            poll_interval_ms=2500,
        ), 404

    from_key = (request.args.get("from") or "all").strip().lower()
    back_url, back_label = _BACK_TARGETS.get(
        from_key, _BACK_TARGETS["all"]
    )
    focus = (request.args.get("focus") or "").strip() or None
    focus_job_id = focus if focus and focus.startswith("JOB-") else None

    return render_template(
        "wo_detail.html",
        wo=wo,
        back_url=back_url,
        back_label=back_label,
        focus=focus,
        focus_job_id=focus_job_id,
        standalone_page=True,
        active_sidebar_page="workorders",
        poll_interval_ms=2500,
    )


@work_order_api.route("/api/workorders/<wo_id>/jobs")
def api_get_work_order_jobs(wo_id):
    """List persisted jobs for a work order."""
    jobs = _work_order_service.get_work_order_jobs(wo_id)
    if jobs is None:
        return jsonify({"error": "Work order not found"}), 404
    return jsonify(jobs)


def _opt_str(value):
    """Normalize an optional string field — strip + None on empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@work_order_api.route("/api/workorders/<wo_id>/jobs", methods=["POST"])
def api_create_work_order_job(wo_id):
    """Create a persisted job for a work order.

    Body fields (Phase C):
        job_type: 'Internal' (default) | 'External' | 'Design'
        queue_ids: list[int]                 (Internal only)
        vendor, external_process             (External required)
        designer, requirements               (Design — designer required)
    """
    data = request.get_json(silent=True) or {}
    job_type = (data.get("job_type") or "Internal")

    queue_ids = []
    if data.get("queue_ids") is not None:
        try:
            queue_ids = _parse_queue_ids(data.get("queue_ids"))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    try:
        job = _work_order_service.create_job(
            wo_id,
            job_type=job_type,
            queue_ids=queue_ids,
            vendor=_opt_str(data.get("vendor")),
            external_process=_opt_str(data.get("external_process")),
            designer=_opt_str(data.get("designer")),
            requirements=_opt_str(data.get("requirements")),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404

    return jsonify({
        "success": True,
        "job": job,
        "assigned_count": len(queue_ids),
    }), 201


# ------------------------------------------------------------------
# Phase C — Job lifecycle + per-type field updates
# ------------------------------------------------------------------

_EXTERNAL_PATCH_FIELDS = (
    "vendor", "external_process", "date_delivered",
    "inspection_report", "inspector", "inspection_date",
)

_DESIGN_PATCH_FIELDS = (
    "requirements", "designer", "design_completed_at", "approved_by",
)

_INSPECTION_PATCH_FIELDS = (
    "inspection_report", "inspector", "inspection_date",
)


def _collect_patch_fields(data: dict, allowed: tuple) -> dict:
    return {
        key: _opt_str(data.get(key))
        for key in allowed
        if data.get(key) is not None
    }


@work_order_api.route("/api/jobs/<int:job_id>/start", methods=["POST"])
def api_start_non_internal_job(job_id):
    """Transition a non-Internal job 'open' → 'in_progress'."""
    try:
        _work_order_service.start_non_internal_job(job_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "in_progress",
    })


@work_order_api.route("/api/jobs/<int:job_id>/complete", methods=["POST"])
def api_complete_non_internal_job(job_id):
    """Transition a non-Internal job → 'completed' and roll up the WO."""
    try:
        _work_order_service.complete_non_internal_job(job_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({
        "success": True,
        "job_id": job_id,
        "status": "completed",
    })


@work_order_api.route("/api/jobs/<int:job_id>/external", methods=["PATCH"])
def api_patch_external_job(job_id):
    """Partially update External-job fields."""
    data = request.get_json(silent=True) or {}
    fields = _collect_patch_fields(data, _EXTERNAL_PATCH_FIELDS)
    try:
        _work_order_service.update_external_job_fields(job_id, **fields)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"success": True, "job_id": job_id})


@work_order_api.route("/api/jobs/<int:job_id>/design", methods=["PATCH"])
def api_patch_design_job(job_id):
    """Partially update Design-job fields."""
    data = request.get_json(silent=True) or {}
    fields = _collect_patch_fields(data, _DESIGN_PATCH_FIELDS)
    try:
        _work_order_service.update_design_job_fields(job_id, **fields)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"success": True, "job_id": job_id})


@work_order_api.route("/api/jobs/<int:job_id>/inspection", methods=["PATCH"])
def api_patch_job_inspection(job_id):
    """Partially update inspection fields. Internal + External only."""
    data = request.get_json(silent=True) or {}
    fields = _collect_patch_fields(data, _INSPECTION_PATCH_FIELDS)

    job = _work_order_service.job_repository.get_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    job_type = job.get("job_type")
    if job_type == "Design":
        return jsonify({
            "error": "Inspection not applicable to Design jobs"
        }), 400

    try:
        if job_type == "Internal":
            _work_order_service.update_internal_job_fields(job_id, **fields)
        elif job_type == "External":
            _work_order_service.update_external_job_fields(job_id, **fields)
        else:
            return jsonify({
                "error": "Unknown job_type: {!r}".format(job_type)
            }), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"success": True, "job_id": job_id})


@work_order_api.route("/api/jobs/<int:job_id>/inspection", methods=["POST"])
def api_record_inspection(job_id):
    """Record an inspection pass/fail outcome (state transition).

    POST is the state transition: it writes the four inspection
    columns and re-rolls job + WO status through the inspection gate.
    The sibling PATCH endpoint edits inspection fields without
    changing the outcome.

    Body: {outcome, inspector, report?, date?}.
    """
    data = request.get_json(silent=True) or {}
    try:
        job = _work_order_service.record_inspection(
            job_id,
            outcome=data.get("outcome"),
            inspector=data.get("inspector"),
            report=_opt_str(data.get("report")),
            date=_opt_str(data.get("date")),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({"success": True, "job": job})


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
    """Cancel every non-terminal item in a work order."""
    result = _work_order_service.cancel_work_order(wo_id)
    if not result.get("found"):
        return jsonify({"error": "Work order not found"}), 404
    return jsonify({
        "success": True,
        "cancelled_count": result["cancelled_count"],
        "printing_count": result["printing_count"],
    })


@work_order_api.route("/api/workorders/<wo_id>/retry", methods=["POST"])
def api_retry_work_order(wo_id):
    """Requeue every cancelled/failed item in a work order."""
    result = _work_order_service.retry_work_order(wo_id)
    if not result.get("found"):
        return jsonify({"error": "Work order not found"}), 404
    return jsonify({
        "success": True,
        "requeued_count": result["requeued_count"],
    })


@work_order_api.route("/api/workorders/<wo_id>/deliver", methods=["POST"])
def api_deliver_work_order(wo_id):
    """Phase F — record delivery and stamp the WO 'delivered'.

    Body: {delivered_at?, received_by?, notes?, recorded_by?}.
    404 if the WO is missing; 409 if it isn't 'completed' or is
    already 'delivered'.
    """
    data = request.get_json(silent=True) or {}
    try:
        wo = _work_order_service.mark_delivered(
            wo_id,
            delivered_at=_opt_str(data.get("delivered_at")),
            received_by=_opt_str(data.get("received_by")),
            notes=_opt_str(data.get("notes")),
            recorded_by=_opt_str(data.get("recorded_by")),
        )
    except DeliveryStateError as exc:
        return jsonify({"error": str(exc)}), 409
    except LookupError as exc:
        return jsonify({"error": str(exc)}), 404
    return jsonify({
        "success": True,
        "work_order": wo,
        "delivery": wo.get("delivery"),
    })


@work_order_api.route("/api/workorders/<wo_id>/jobs/<int:job_id>",
                      methods=["DELETE"])
def api_cancel_work_order_job(wo_id, job_id):
    """Cancel every non-terminal item belonging to one job."""
    result = _work_order_service.cancel_job(job_id)
    if not result.get("found"):
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "success": True,
        "cancelled_count": result["cancelled_count"],
        "printing_count": result["printing_count"],
    })


@work_order_api.route("/api/workorders/<wo_id>/jobs/<int:job_id>/retry",
                      methods=["POST"])
def api_retry_work_order_job(wo_id, job_id):
    """Requeue every cancelled/failed item belonging to one job."""
    result = _work_order_service.retry_job(job_id)
    if not result.get("found"):
        return jsonify({"error": "Job not found"}), 404
    return jsonify({
        "success": True,
        "requeued_count": result["requeued_count"],
    })


# ------------------------------------------------------------------
# Triage (Phase 2.5b — replaces the legacy Queue list / stats endpoints)
# ------------------------------------------------------------------

@work_order_api.route("/api/triage")
def api_triage():
    """Aggregated 5-lane triage payload + active-parts table.

    Composes from queue_items, print_jobs, and printer runtime state
    in Python — see app/domains/triage/service.py.
    """
    if _triage_service is None:
        return jsonify({"error": "Triage service not configured"}), 500
    return jsonify(_triage_service.get_triage_payload())


# ------------------------------------------------------------------
# Production Queue — action endpoints only.
# The legacy GET /api/queue and GET /api/queue/stats were retired in
# Phase 2.5b along with the Queue sub-tab; Triage is the read surface.
# Print / cancel / retry endpoints remain because the Print modal,
# Triage actions, WO Detail, and the polling completion handler all
# depend on them.
# ------------------------------------------------------------------

@work_order_api.route("/api/queue/<int:queue_id>", methods=["PATCH"])
def api_update_queue_item(queue_id):
    """Update a queue item status (queued/completed/failed/cancelled)."""
    data = request.get_json()
    if not data or "status" not in data:
        return jsonify({"error": "Missing status"}), 400

    new_status = data["status"]
    if new_status == "queued":
        result = _queue_service.retry_queue_item(queue_id)
        if not result.get("found"):
            return jsonify({"error": "Queue item not found"}), 404
        return jsonify({
            "success": True,
            "requeued_count": result["requeued_count"],
        })
    if new_status == "cancelled":
        result = _queue_service.cancel_queue_item(queue_id)
        if not result.get("found"):
            return jsonify({"error": "Queue item not found"}), 404
        return jsonify({
            "success": True,
            "cancelled_count": result["cancelled_count"],
            "printing_count": result["printing_count"],
        })
    if new_status == "completed":
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
    return _dispatch_print_request([queue_id])


@work_order_api.route("/api/queue/print", methods=["POST"])
def api_print_queue_items():
    """Assign one or more selected queue items to a single print job."""
    queue_ids = request.form.getlist("queue_ids")
    if not queue_ids:
        single = request.form.get("queue_id")
        if single:
            queue_ids = [single]
    return _dispatch_print_request(queue_ids)


@work_order_api.route("/api/queue/<int:queue_id>/cancel", methods=["POST"])
def api_cancel_queue_item(queue_id):
    """Cancel a single queue item; stop the printer if it's currently printing.

    Service layer owns the state transition, printer stop, and
    production-record close so every cancel path behaves the same.
    """
    result = _queue_service.cancel_queue_item(queue_id)
    if not result.get("found"):
        return jsonify({"error": "Queue item not found"}), 404
    return jsonify({
        "success": True,
        "cancelled_count": result["cancelled_count"],
        "printing_count": result["printing_count"],
        "queue_id": queue_id,
    })


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
