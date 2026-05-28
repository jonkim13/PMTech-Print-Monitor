"""Quality routes — NCR + Corrective Action HTTP surface (Phase E1).

Thin parse-and-call wrappers over QualityService. Error mapping:

    QualityStateError       -> 409  (illegal transition / business rule)
    ValueError (validation) -> 400
    LookupError             -> 404
"""

from functools import wraps

from flask import Blueprint, jsonify, request

from app.domains.quality.service import QualityStateError

quality_api = Blueprint("quality_api", __name__)

_quality_service = None


def register_quality_routes(app, quality_service):
    """Wire up the quality blueprint."""
    global _quality_service
    _quality_service = quality_service
    app.register_blueprint(quality_api)


def _map_quality_errors(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except QualityStateError as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            # QualityValidationError subclasses ValueError.
            return jsonify({"error": str(exc)}), 400
        except LookupError as exc:
            return jsonify({"error": str(exc)}), 404
    return wrapper


def _opt_str(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ----------------------------------------------------------------------
# Non-Conformances
# ----------------------------------------------------------------------

@quality_api.route("/api/ncrs", methods=["POST"])
@_map_quality_errors
def api_create_ncr():
    data = request.get_json(silent=True) or {}
    ncr = _quality_service.create_ncr(
        job_id=data.get("job_id"),
        wo_id=data.get("wo_id"),
        description=data.get("description"),
        reported_by=data.get("reported_by"),
        affected_parts=_opt_str(data.get("affected_parts")),
        remedial_action=_opt_str(data.get("remedial_action")),
        corrective_action_needed=data.get("corrective_action_needed", "N"),
    )
    return jsonify({"success": True, "ncr": ncr}), 201


@quality_api.route("/api/ncrs", methods=["GET"])
@_map_quality_errors
def api_list_ncrs():
    wo_id = request.args.get("wo_id")
    job_id = request.args.get("job_id", type=int)
    ncrs = _quality_service.list_ncrs(wo_id=wo_id, job_id=job_id)
    return jsonify({"ncrs": ncrs})


@quality_api.route("/api/ncrs/<int:ncr_id>", methods=["GET"])
@_map_quality_errors
def api_get_ncr(ncr_id):
    ncr = _quality_service.get_ncr_with_cas(ncr_id)
    return jsonify({"ncr": ncr})


@quality_api.route("/api/ncrs/<int:ncr_id>/close", methods=["POST"])
@_map_quality_errors
def api_close_ncr(ncr_id):
    ncr = _quality_service.close_ncr(ncr_id)
    return jsonify({"success": True, "ncr": ncr})


# ----------------------------------------------------------------------
# Corrective Actions
# ----------------------------------------------------------------------

@quality_api.route("/api/ncrs/<int:ncr_id>/corrective-actions",
                   methods=["POST"])
@_map_quality_errors
def api_create_ca(ncr_id):
    data = request.get_json(silent=True) or {}
    ca = _quality_service.create_ca(
        ncr_id,
        root_cause_actions=data.get("root_cause_actions"),
        responsible_persons=_opt_str(data.get("responsible_persons")),
        resources_needed=_opt_str(data.get("resources_needed")),
        effectiveness_verification=_opt_str(
            data.get("effectiveness_verification")
        ),
        verifying_person=_opt_str(data.get("verifying_person")),
    )
    return jsonify({"success": True, "corrective_action": ca}), 201


@quality_api.route("/api/corrective-actions/<int:ca_id>", methods=["PATCH"])
@_map_quality_errors
def api_update_ca(ca_id):
    data = request.get_json(silent=True) or {}
    fields = {
        key: _opt_str(data.get(key))
        for key in ("root_cause_actions", "responsible_persons",
                    "resources_needed", "effectiveness_verification",
                    "verifying_person")
        if data.get(key) is not None
    }
    ca = _quality_service.update_ca(ca_id, **fields)
    return jsonify({"success": True, "corrective_action": ca})


@quality_api.route("/api/corrective-actions/<int:ca_id>/verify",
                   methods=["POST"])
@_map_quality_errors
def api_verify_ca(ca_id):
    data = request.get_json(silent=True) or {}
    ca = _quality_service.verify_ca(
        ca_id, verifying_person=data.get("verifying_person")
    )
    return jsonify({"success": True, "corrective_action": ca})
