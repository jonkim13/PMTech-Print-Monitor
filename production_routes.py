"""
Production Log Routes (ISO 9001 Traceability)
===============================================
API endpoints for print jobs, machine log, material usage,
QC updates, and CSV export.
"""

import os

from flask import Blueprint, jsonify, request, Response, send_file

production_api = Blueprint("production_api", __name__)

_production_service = None
_export_service = None
_farm_manager = None


def register_production_routes(app, production_service, export_service,
                               farm_manager):
    """Wire up the production blueprint."""
    global _production_service, _export_service, _farm_manager
    _production_service = production_service
    _export_service = export_service
    _farm_manager = farm_manager
    app.register_blueprint(production_api)


def _printer_names():
    """Return a {printer_id: printer_name} lookup from the farm manager."""
    result = {}
    for pid, data in _farm_manager.printers.items():
        client = data.get("client")
        if client is not None:
            result[pid] = client.name
    return result


# ------------------------------------------------------------------
# Print Jobs
# ------------------------------------------------------------------

@production_api.route("/api/production/jobs")
def api_production_jobs():
    """Get print jobs with optional filters."""
    jobs = _production_service.list_jobs(
        printer_id=request.args.get("printer_id"),
        status=request.args.get("status"),
        outcome=request.args.get("outcome"),
        material=request.args.get("material"),
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
        limit=request.args.get("limit", 100, type=int),
        offset=request.args.get("offset", 0, type=int),
    )
    return jsonify(jobs)


@production_api.route("/api/production/jobs/<int:job_id>")
def api_production_job(job_id):
    """Get a single job with full details."""
    job = _production_service.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@production_api.route("/api/production/jobs/<int:job_id>", methods=["PATCH"])
def api_production_job_update(job_id):
    """Update QC fields: outcome, operator, notes."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    success, error = _production_service.update_job_qc(
        job_id,
        outcome=data.get("outcome"),
        operator=data.get("operator"),
        notes=data.get("notes"),
    )
    if success:
        return jsonify({"success": True})
    if error == "outcome must be pass, fail, or unknown":
        return jsonify({"error": error}), 400
    return jsonify({"error": error}), 404


# ------------------------------------------------------------------
# Job Snapshot
# ------------------------------------------------------------------

@production_api.route("/api/production/jobs/<int:job_id>/snapshot")
def api_production_snapshot(job_id):
    """Serve the snapshot image for a job."""
    path = _production_service.get_job_snapshot_path(job_id)
    if not path:
        return jsonify({"error": "No snapshot available"}), 404
    if not os.path.isfile(path):
        return jsonify({"error": "Snapshot file not found"}), 404
    return send_file(path, mimetype="image/png")


# ------------------------------------------------------------------
# Machine Log & Summary
# ------------------------------------------------------------------

@production_api.route("/api/production/machines")
def api_production_machines():
    """Get machine summaries for all printers."""
    printer_ids = list(_farm_manager.printers.keys())
    summaries = _production_service.list_machine_summaries(
        printer_ids, printer_name_by_id=_printer_names(),
    )
    return jsonify(summaries)


@production_api.route("/api/production/machines/<printer_id>/log")
def api_production_machine_log(printer_id):
    """Get machine event log for a printer."""
    logs = _production_service.get_machine_log(
        printer_id=printer_id,
        event_type=request.args.get("event_type"),
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
        limit=request.args.get("limit", 100, type=int),
    )
    return jsonify(logs)


@production_api.route("/api/production/machines/<printer_id>/maintenance",
                      methods=["POST"])
def api_production_maintenance(printer_id):
    """Log a maintenance or calibration event."""
    if printer_id not in _farm_manager.printers:
        return jsonify({"error": "Unknown printer"}), 404

    data = request.get_json() or {}
    printer_name = _farm_manager.printers[printer_id]["client"].name
    success, error = _production_service.log_maintenance_event(
        printer_id=printer_id,
        printer_name=printer_name,
        event_type=data.get("event_type", "maintenance"),
        notes=data.get("notes", ""),
    )
    if not success:
        return jsonify({"error": error}), 400
    return jsonify({"success": True}), 201


# ------------------------------------------------------------------
# Material Traceability
# ------------------------------------------------------------------

@production_api.route("/api/production/materials/<spool_id>/usage")
def api_production_spool_usage(spool_id):
    """Get all jobs and usage records for a spool."""
    return jsonify(_production_service.get_spool_usage(spool_id))


# ------------------------------------------------------------------
# CSV Exports
# ------------------------------------------------------------------

@production_api.route("/api/production/export/jobs")
def api_export_jobs():
    """Export print jobs as CSV."""
    csv_data = _export_service.export_jobs_csv(
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
    )
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=print_jobs.csv"},
    )


@production_api.route("/api/production/export/machines")
def api_export_machines():
    """Export machine log as CSV."""
    csv_data = _export_service.export_machines_csv(
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
    )
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=machine_log.csv"},
    )


@production_api.route("/api/production/export/materials")
def api_export_materials():
    """Export material usage as CSV."""
    csv_data = _export_service.export_materials_csv(
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
    )
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition":
                 "attachment; filename=material_usage.csv"},
    )
