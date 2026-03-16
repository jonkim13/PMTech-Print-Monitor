"""
Production Log Routes (ISO 9001 Traceability)
===============================================
API endpoints for print jobs, machine log, material usage,
QC updates, and CSV export.
"""

import os

from flask import Blueprint, jsonify, request, Response, send_file

production_api = Blueprint("production_api", __name__)

_production_db = None
_farm_manager = None
_snapshots_dir = None


def register_production_routes(app, production_db, farm_manager,
                               snapshots_dir=None):
    """Wire up the production blueprint."""
    global _production_db, _farm_manager, _snapshots_dir
    _production_db = production_db
    _farm_manager = farm_manager
    _snapshots_dir = snapshots_dir
    app.register_blueprint(production_api)


# ------------------------------------------------------------------
# Print Jobs
# ------------------------------------------------------------------

@production_api.route("/api/production/jobs")
def api_production_jobs():
    """Get print jobs with optional filters."""
    jobs = _production_db.get_jobs(
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
    job = _production_db.get_job(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    return jsonify(job)


@production_api.route("/api/production/jobs/<int:job_id>", methods=["PATCH"])
def api_production_job_update(job_id):
    """Update QC fields: outcome, operator, notes."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    outcome = data.get("outcome")
    if outcome and outcome not in ("pass", "fail", "unknown"):
        return jsonify({"error": "outcome must be pass, fail, or unknown"}), 400

    success = _production_db.update_job_qc(
        job_id,
        outcome=outcome,
        operator=data.get("operator"),
        notes=data.get("notes"),
    )
    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Job not found or no changes"}), 404


# ------------------------------------------------------------------
# Job Snapshot
# ------------------------------------------------------------------

@production_api.route("/api/production/jobs/<int:job_id>/snapshot")
def api_production_snapshot(job_id):
    """Serve the snapshot image for a job."""
    job = _production_db.get_job(job_id)
    if not job or not job.get("snapshot_path"):
        return jsonify({"error": "No snapshot available"}), 404
    path = job["snapshot_path"]
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
    summaries = _production_db.get_all_machine_summaries(printer_ids)
    # Include printer names
    result = []
    for pid, summary in summaries.items():
        p = _farm_manager.printers.get(pid)
        if p:
            summary["printer_name"] = p["client"].name
        result.append(summary)
    return jsonify(result)


@production_api.route("/api/production/machines/<printer_id>/log")
def api_production_machine_log(printer_id):
    """Get machine event log for a printer."""
    logs = _production_db.get_machine_log(
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
    event_type = data.get("event_type", "maintenance")
    if event_type not in ("maintenance", "calibration"):
        return jsonify({"error": "event_type must be maintenance or calibration"}), 400

    printer_name = _farm_manager.printers[printer_id]["client"].name
    _production_db.log_machine_event(
        printer_id, printer_name, event_type,
        details={"notes": data.get("notes", "")},
    )
    return jsonify({"success": True}), 201


# ------------------------------------------------------------------
# Material Traceability
# ------------------------------------------------------------------

@production_api.route("/api/production/materials/<spool_id>/usage")
def api_production_spool_usage(spool_id):
    """Get all jobs and usage records for a spool."""
    usage = _production_db.get_spool_usage(spool_id)
    totals = _production_db.get_spool_totals(spool_id)
    return jsonify({"usage": usage, "totals": totals})


# ------------------------------------------------------------------
# CSV Exports
# ------------------------------------------------------------------

@production_api.route("/api/production/export/jobs")
def api_export_jobs():
    """Export print jobs as CSV."""
    csv_data = _production_db.export_jobs_csv(
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
    csv_data = _production_db.export_machine_csv(
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
    csv_data = _production_db.export_materials_csv(
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
    )
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition":
                 "attachment; filename=material_usage.csv"},
    )
