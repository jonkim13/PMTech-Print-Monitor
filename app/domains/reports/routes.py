"""Flask routes for the weekly operations log (ISO 9001 clause 9.1).

Every endpoint accepts ``?week_start=YYYY-MM-DD``. Missing or empty
defaults to the current week (UTC).
"""

from flask import Blueprint, Response, jsonify, request


reports_api = Blueprint("reports_api", __name__)

_weekly_service = None


def register_reports_routes(app, weekly_service):
    """Wire up the reports blueprint."""
    global _weekly_service
    _weekly_service = weekly_service
    app.register_blueprint(reports_api)


def _week_start_arg():
    return request.args.get("week_start") or None


def _json_response(payload):
    """Wrap a payload in ``jsonify`` with 400 on week parsing errors."""
    return jsonify(payload)


def _handle(action):
    """Run a service action with consistent ValueError -> 400 handling."""
    try:
        return _json_response(action(_week_start_arg()))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------

@reports_api.route("/api/reports/weekly/summary")
def api_reports_weekly_summary():
    return _handle(_weekly_service.get_summary)


@reports_api.route("/api/reports/weekly/production")
def api_reports_weekly_production():
    return _handle(_weekly_service.get_production)


@reports_api.route("/api/reports/weekly/materials")
def api_reports_weekly_materials():
    return _handle(_weekly_service.get_materials)


@reports_api.route("/api/reports/weekly/equipment")
def api_reports_weekly_equipment():
    return _handle(_weekly_service.get_equipment)


@reports_api.route("/api/reports/weekly/work-orders")
def api_reports_weekly_work_orders():
    return _handle(_weekly_service.get_work_orders)


@reports_api.route("/api/reports/weekly/timeline")
def api_reports_weekly_timeline():
    return _handle(_weekly_service.get_timeline)


@reports_api.route("/api/reports/weekly/export")
def api_reports_weekly_export():
    """Download the whole week's report as a multi-section CSV."""
    try:
        window = _weekly_service.window_dict(_week_start_arg())
        csv_data = _weekly_service.export_csv(_week_start_arg())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    filename = "weekly_log_{}.csv".format(window["week_start"])
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=\"{}\"".format(filename),
        },
    )
