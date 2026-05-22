"""Flask routes for the aggregated dashboard payload."""

from flask import Blueprint, jsonify


dashboard_api = Blueprint("dashboard_api", __name__)

_dashboard_service = None


def register_dashboard_routes(app, dashboard_service):
    """Wire up the dashboard blueprint."""
    global _dashboard_service
    _dashboard_service = dashboard_service
    app.register_blueprint(dashboard_api)


@dashboard_api.route("/api/dashboard")
def api_dashboard():
    """Aggregated payload for the Phase 2.5a dashboard.

    Composes printer state, fleet stats, attention items, and recent
    events into the field shape the dashboard poll JS expects.
    """
    if _dashboard_service is None:
        return jsonify({"error": "Dashboard service not configured"}), 500
    return jsonify(_dashboard_service.get_dashboard_payload())
