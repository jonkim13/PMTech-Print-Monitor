"""Flask routes for monitoring events and historical print observations.

Both event queues and PrintHistoryDB are observability-side concerns —
they describe what's happened or is about to happen on the printers —
so they share a blueprint here in the monitoring domain.
"""

from flask import Blueprint, jsonify, request


monitoring_api = Blueprint("monitoring_api", __name__)

_event_service = None
_farm_manager = None
_history_db = None


def register_monitoring_routes(app, event_service, farm_manager, history_db):
    """Wire up the monitoring blueprint."""
    global _event_service, _farm_manager, _history_db
    _event_service = event_service
    _farm_manager = farm_manager
    _history_db = history_db
    app.register_blueprint(monitoring_api)


# --- Events ---

@monitoring_api.route("/api/events")
def api_events():
    """Get pending events (prints completed, errors, etc.).

    The drone system polls this endpoint; events are cleared after
    retrieval.
    """
    if _event_service:
        return jsonify(_event_service.consume_events())
    return jsonify(_farm_manager.get_pending_events())


@monitoring_api.route("/api/events/peek")
def api_events_peek():
    """Peek at pending events without clearing them."""
    if _event_service:
        return jsonify(_event_service.peek_events())
    return jsonify(_farm_manager.peek_pending_events())


# --- Print history ---

@monitoring_api.route("/api/history")
def api_history():
    """Get print history from the database."""
    limit = request.args.get("limit", 100, type=int)
    return jsonify(_history_db.get_history(limit))


@monitoring_api.route("/api/history/stats")
def api_history_stats():
    """Get aggregate print statistics."""
    return jsonify(_history_db.get_stats())
