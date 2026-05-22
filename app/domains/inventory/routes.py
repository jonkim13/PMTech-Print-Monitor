"""Flask routes for filament inventory."""

from flask import Blueprint, jsonify, request


inventory_api = Blueprint("inventory_api", __name__)

_inventory_service = None


def register_inventory_routes(app, inventory_service):
    """Wire up the inventory blueprint."""
    global _inventory_service
    _inventory_service = inventory_service
    app.register_blueprint(inventory_api)


@inventory_api.route("/api/inventory")
def api_inventory():
    """Get all filament inventory, with optional filters."""
    return jsonify(_inventory_service.get_inventory(
        material=request.args.get("material"),
        brand=request.args.get("brand"),
        color=request.args.get("color"),
        supplier=request.args.get("supplier"),
    ))


@inventory_api.route("/api/inventory/<spool_id>")
def api_inventory_spool(spool_id):
    """Get a specific spool by ID."""
    spool = _inventory_service.get_spool(spool_id)
    if not spool:
        return jsonify({"error": "Spool not found"}), 404
    return jsonify(spool)


@inventory_api.route("/api/inventory", methods=["POST"])
def api_inventory_add():
    """Add a new filament spool."""
    try:
        result = _inventory_service.add_spool(request.get_json())
        return jsonify(result), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@inventory_api.route("/api/inventory/<spool_id>", methods=["PUT"])
def api_inventory_update_spool(spool_id):
    """Update weight, brand, color, supplier, and batch on a spool."""
    try:
        _inventory_service.update_spool(spool_id, request.get_json())
        return jsonify({"success": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except KeyError:
        return jsonify({"error": "Spool not found"}), 404


@inventory_api.route("/api/inventory/<spool_id>", methods=["DELETE"])
def api_inventory_delete(spool_id):
    """Delete a filament spool."""
    try:
        _inventory_service.delete_spool(spool_id)
        return jsonify({"success": True})
    except KeyError:
        return jsonify({"error": "Spool not found"}), 404


@inventory_api.route("/api/inventory/options")
def api_inventory_options():
    """Get available materials, brands, and suppliers for form dropdowns."""
    return jsonify(_inventory_service.get_options())
