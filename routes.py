"""
Flask Routes
==============
All API endpoints and the dashboard route, organized as a Blueprint.
"""

import os

from flask import Blueprint, jsonify, render_template, request
from werkzeug.utils import secure_filename

api = Blueprint("api", __name__)

# These are set by register_routes() before the blueprint is used.
_farm_manager = None
_filament_db = None
_history_db = None
_drone_controller = None
_assignment_db = None
_ui_config = {}
_ALLOWED_UPLOAD_EXTENSIONS = {".gcode", ".gco", ".g", ".bgcode"}


def register_routes(app, farm_manager, filament_db, history_db,
                    drone_controller, assignment_db=None, ui_config=None):
    """Wire up the blueprint with the application's shared objects."""
    global _farm_manager, _filament_db, _history_db, _drone_controller
    global _assignment_db, _ui_config
    _farm_manager = farm_manager
    _filament_db = filament_db
    _history_db = history_db
    _drone_controller = drone_controller
    _assignment_db = assignment_db
    _ui_config = ui_config or {}
    app.register_blueprint(api)


def _validate_filament_material(material):
    material = str(material or "").strip()
    if not material:
        raise ValueError("material is required")
    if material in _filament_db.DEPRECATED_CREATION_MATERIALS:
        raise ValueError(
            f"Material '{material}' is deprecated and cannot be used for new or updated filament entries"
        )
    return material


def _coerce_optional_bool(value, field_name: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    raise ValueError(f"'{field_name}' must be a boolean")


def _format_assignment_location(printer_id: str, tool_index: int) -> str:
    printer_data = (_farm_manager.printers or {}).get(printer_id, {})
    client = printer_data.get("client")
    printer_name = getattr(client, "name", "") if client else ""
    printer_label = printer_name if printer_name else printer_id
    if printer_name and printer_name != printer_id:
        printer_label = f"{printer_name} ({printer_id})"
    return f"{printer_label} T{tool_index + 1}"


# --- Dashboard ---

@api.route("/")
def dashboard():
    return render_template(
        "dashboard.html",
        allowed_suppliers=_filament_db.ALLOWED_SUPPLIERS,
        poll_interval_ms=int(_ui_config.get("poll_interval_ms", 3000)),
    )


# --- Printer API ---

@api.route("/api/printers")
def api_printers():
    """Get status of all printers."""
    return jsonify(_farm_manager.get_all_status())


@api.route("/api/printers/<printer_id>")
def api_printer(printer_id):
    """Get status of a specific printer."""
    status = _farm_manager.get_printer_status(printer_id)
    if status.get("error"):
        return jsonify(status), 404
    return jsonify(status)


@api.route("/api/printers/<printer_id>/files")
def api_printer_files(printer_id):
    """Get file listing from a printer."""
    client = _farm_manager.get_printer_client(printer_id)
    if not client:
        return jsonify({"error": "Unknown printer"}), 404
    storage = request.args.get("storage")
    return jsonify(client.get_files(storage=storage))


@api.route("/api/printers/<printer_id>/upload", methods=["POST"])
def api_printer_upload(printer_id):
    """
    Upload a gcode file to a printer.
    Expects multipart form data with 'file' field.
    Optional query param: ?print_after=1
    """
    client = _farm_manager.get_printer_client(printer_id)
    if not client:
        return jsonify({"error": "Unknown printer"}), 404

    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    uploaded = request.files["file"]
    if not uploaded.filename:
        return jsonify({"error": "Empty filename"}), 400

    print_after = request.args.get("print_after", "0") == "1"
    file_data = uploaded.read()
    filename = secure_filename(uploaded.filename)
    if not filename:
        return jsonify({"error": "Invalid filename"}), 400

    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_UPLOAD_EXTENSIONS:
        return jsonify({"error": f"Unsupported extension: {ext}"}), 400

    result = client.upload_gcode(file_data, filename,
                                 print_after=print_after)
    status_code = 200 if result.get("success") else 500
    return jsonify(result), status_code


@api.route("/api/printers/<printer_id>/stop", methods=["POST"])
def api_printer_stop(printer_id):
    """Stop the current print job on a printer."""
    client = _farm_manager.get_printer_client(printer_id)
    if not client:
        return jsonify({"error": "Unknown printer"}), 404
    result = client.stop_job()
    status_code = 200 if result.get("success") else 500
    return jsonify(result), status_code


# --- Events API ---

@api.route("/api/events")
def api_events():
    """
    Get pending events (prints completed, errors, etc.)
    The drone system will poll this endpoint.
    Events are cleared after retrieval.
    """
    return jsonify(_farm_manager.get_pending_events())


@api.route("/api/events/peek")
def api_events_peek():
    """
    Peek at pending events without clearing them.
    Useful for the dashboard.
    """
    return jsonify(_farm_manager.peek_pending_events())


# --- Print History API ---

@api.route("/api/history")
def api_history():
    """Get print history from the database."""
    limit = request.args.get("limit", 100, type=int)
    return jsonify(_history_db.get_history(limit))


@api.route("/api/history/stats")
def api_history_stats():
    """Get aggregate print statistics."""
    return jsonify(_history_db.get_stats())


# --- Filament Inventory API ---

@api.route("/api/inventory")
def api_inventory():
    """Get all filament inventory, with optional filters."""
    material = request.args.get("material")
    brand = request.args.get("brand")
    color = request.args.get("color")
    supplier = request.args.get("supplier")
    return jsonify(_filament_db.get_all(material, brand, color, supplier))


@api.route("/api/inventory/<spool_id>")
def api_inventory_spool(spool_id):
    """Get a specific spool by ID."""
    spool = _filament_db.get_by_id(spool_id)
    if not spool:
        return jsonify({"error": "Spool not found"}), 404
    return jsonify(spool)


@api.route("/api/inventory", methods=["POST"])
def api_inventory_add():
    """Add a new filament spool."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    required = ["material", "brand", "color", "supplier",
                 "grams", "diameter", "operator"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    try:
        grams = int(data["grams"])
        diameter = float(data["diameter"])
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid numeric values for grams/diameter"}), 400

    if grams <= 0:
        return jsonify({"error": "grams must be > 0"}), 400
    if diameter <= 0:
        return jsonify({"error": "diameter must be > 0"}), 400

    try:
        material = _validate_filament_material(data["material"])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    supplier = str(data["supplier"]).strip()
    if supplier not in _filament_db.ALLOWED_SUPPLIERS:
        allowed = ", ".join(_filament_db.ALLOWED_SUPPLIERS)
        return jsonify({
            "error": f"Invalid supplier '{supplier}'. Allowed suppliers: {allowed}"
        }), 400

    try:
        spool_id = _filament_db.add_filament(
            material=material,
            brand=data["brand"],
            color=data["color"],
            supplier=supplier,
            grams=grams,
            diameter=diameter,
            batch=data.get("batch", ""),
            operator=data["operator"],
        )
        return jsonify({
            "success": True,
            "id": spool_id,
            "spool_id": spool_id,
        }), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@api.route("/api/inventory/<spool_id>/weight", methods=["PUT"])
def api_inventory_update_weight(spool_id):
    """Update the weight of a filament spool."""
    data = request.get_json()
    if not data or "grams" not in data:
        return jsonify({"error": "Missing 'grams' field"}), 400

    try:
        grams = int(data["grams"])
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid grams value"}), 400

    if grams < 0:
        return jsonify({"error": "grams must be >= 0"}), 400

    success = _filament_db.update_weight(spool_id, grams)
    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Spool not found"}), 404


@api.route("/api/inventory/<spool_id>", methods=["DELETE"])
def api_inventory_delete(spool_id):
    """Delete a filament spool."""
    success = _filament_db.delete_spool(spool_id)
    if success:
        return jsonify({"success": True})
    return jsonify({"error": "Spool not found"}), 404


@api.route("/api/inventory/options")
def api_inventory_options():
    """Get available materials, brands, and suppliers for form dropdowns."""
    return jsonify({
        "materials": _filament_db.get_materials_list(),
        "filter_materials": _filament_db.get_filter_materials_list(),
        "form_materials": _filament_db.get_creation_materials_list(),
        "brands": _filament_db.get_brands_list(),
        "suppliers": _filament_db.get_suppliers_list(),
    })


# --- Filament Assignment API ---

@api.route("/api/assignments")
def api_assignments():
    """Get all printer-to-spool assignments."""
    return jsonify(_assignment_db.get_all_assignments())


@api.route("/api/assignments/<printer_id>", methods=["GET"])
def api_printer_assignments(printer_id):
    """Get all tool assignments for a specific printer."""
    if printer_id not in _farm_manager.printers:
        return jsonify({"error": "Unknown printer"}), 404
    assignments = _assignment_db.get_printer_assignments(printer_id)
    return jsonify(assignments)


@api.route("/api/assignments/<printer_id>", methods=["POST"])
def api_assign_spool(printer_id):
    """Assign a spool to a printer tool.

    Body: { "spool_id": "...", "tool_index": 0, "was_dried": true }
    tool_index defaults to 0 if not provided (backward compat).
    """
    if printer_id not in _farm_manager.printers:
        return jsonify({"error": "Unknown printer"}), 404

    data = request.get_json()
    if not data or not data.get("spool_id"):
        return jsonify({"error": "Missing 'spool_id'"}), 400

    spool = _filament_db.get_by_id(data["spool_id"])
    if not spool:
        return jsonify({"error": "Spool not found"}), 404

    try:
        was_dried = _coerce_optional_bool(data.get("was_dried"),
                                          "was_dried")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    tool_index = int(data.get("tool_index", 0))
    existing_assignments = _assignment_db.get_spool_assignments(
        data["spool_id"]
    )
    conflict = next(
        (
            assignment for assignment in existing_assignments
            if assignment["printer_id"] != printer_id
            or assignment["tool_index"] != tool_index
        ),
        None,
    )
    if conflict:
        location = _format_assignment_location(conflict["printer_id"],
                                               conflict["tool_index"])
        return jsonify({
            "error": f"Spool {data['spool_id']} is already assigned to {location}"
        }), 400

    current_assignment = _assignment_db.get_assignment(
        printer_id, tool_index=tool_index
    )
    if current_assignment and current_assignment.get("spool_id") == data["spool_id"]:
        if was_dried:
            _filament_db.update_last_dried(data["spool_id"])
        return jsonify({"success": True})

    _assignment_db.assign(printer_id, data["spool_id"],
                          tool_index=tool_index)
    if was_dried:
        _filament_db.update_last_dried(data["spool_id"])
    return jsonify({"success": True})


@api.route("/api/assignments/<printer_id>", methods=["DELETE"])
def api_unassign_spool(printer_id):
    """Remove spool assignment from a printer tool.

    Query param: ?tool_index=0 (defaults to 0).
    Use ?all=1 to remove all tool assignments.
    """
    if request.args.get("all") == "1":
        success = _assignment_db.unassign_all(printer_id)
    else:
        tool_index = request.args.get("tool_index", 0, type=int)
        success = _assignment_db.unassign(printer_id,
                                          tool_index=tool_index)
    if success:
        return jsonify({"success": True})
    return jsonify({"error": "No assignment found"}), 404


# --- Drone API ---

@api.route("/api/drone/status")
def api_drone_status():
    """Get drone status."""
    return jsonify(_drone_controller.get_status())


@api.route("/api/drone/mission", methods=["POST"])
def api_drone_mission():
    """
    Send a mission to the drone.
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


@api.route("/api/drone/missions")
def api_drone_missions():
    """Get drone mission log."""
    return jsonify(_drone_controller.get_mission_log())


# --- Health ---

@api.route("/api/health")
def api_health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "printers": len(_farm_manager.printers),
        "uptime": "running",
    })
