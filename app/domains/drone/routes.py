"""Flask routes for the drone subsystem."""

from flask import Blueprint, jsonify, request


drone_api = Blueprint("drone_api", __name__)

_drone_controller = None


def register_drone_routes(app, drone_controller):
    """Wire up the drone blueprint."""
    global _drone_controller
    _drone_controller = drone_controller
    app.register_blueprint(drone_api)


@drone_api.route("/api/drone/status")
def api_drone_status():
    """Get drone status."""
    return jsonify(_drone_controller.get_status())


@drone_api.route("/api/drone/mission", methods=["POST"])
def api_drone_mission():
    """Send a mission to the drone.

    Body: { "type": "patrol_all" | "inspect_printer" | "return_to_dock",
            "target": "printer_id" (optional) }
    """
    data = request.get_json()
    if not data or "type" not in data:
        return jsonify({"error": "Missing 'type' field"}), 400

    mission = _drone_controller.send_mission(
        mission_type=data["type"],
        target=data.get("target"),
    )
    return jsonify(mission), 201


@drone_api.route("/api/drone/missions")
def api_drone_missions():
    """Get drone mission log."""
    return jsonify(_drone_controller.get_mission_log())
