"""Custom Engraving routes (Phase E-2).

Thin blueprint: a page to submit a request, the POST that creates the WO
and kicks off generation, a decoupled per-WO status endpoint the WO-detail
page polls, and DB-record-resolved file serving for previews and STLs.

All logic lives in ``EngravingService``. File-serving routes never build a
path from a URL segment — ``which`` is validated against a fixed set and
the actual path comes from the DB record.
"""

import os

from flask import (
    Blueprint,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
)

from .service import EngravingValidationError

engraving_api = Blueprint("engraving_api", __name__)

_engraving_service = None
_settings = None

_VALID_WHICH = {"prod", "mold"}


def register_engraving_routes(app, engraving_service, settings):
    """Wire up the engraving blueprint."""
    global _engraving_service, _settings
    _engraving_service = engraving_service
    _settings = settings
    app.register_blueprint(engraving_api)


def _enabled_products():
    """[(product_key, display_name)] for the product dropdown."""
    products = _settings.engraving_products
    return [
        (key, cfg.get("display_name", key))
        for key, cfg in products.items()
    ]


def _render_form(*, error=None, form=None, status=200):
    form = form or {}
    html = render_template(
        "engraving_new.html",
        standalone_page=True,
        active_sidebar_page="engraving",
        poll_interval_ms=_settings.poll_interval_ms,
        products=_enabled_products(),
        min_quantity=_settings.engraving_min_quantity,
        max_quantity=_settings.engraving_max_quantity,
        error=error,
        form=form,
    )
    return (html, status) if status != 200 else html


@engraving_api.route("/engraving/new")
def page_engraving_new():
    return _render_form()


@engraving_api.route("/engraving", methods=["POST"])
def submit_engraving():
    form = {
        "customer_name": request.form.get("customer_name", ""),
        "quantity": request.form.get("quantity", ""),
        "product_key": request.form.get("product_key", ""),
    }
    try:
        result = _engraving_service.submit_request(
            uploaded_file=request.files.get("image"),
            product_key=form["product_key"],
            quantity=form["quantity"],
            customer_name=form["customer_name"],
        )
    except EngravingValidationError as exc:
        return _render_form(error=str(exc), form=form, status=400)
    return redirect("/work-orders/{}".format(result["wo_id"]))


@engraving_api.route("/api/work-orders/<wo_id>/engraving")
def api_wo_engraving(wo_id):
    view = _engraving_service.get_wo_engraving_view(wo_id)
    if view is None:
        return jsonify(None)
    return jsonify(view)


@engraving_api.route("/api/engraving/<int:engraving_id>/preview/<which>")
def api_engraving_preview(engraving_id, which):
    if which not in _VALID_WHICH:
        abort(404)
    path = _engraving_service.get_artifact_path(engraving_id, "preview", which)
    if not path or not os.path.isfile(path):
        abort(404)
    return send_file(path, mimetype="image/png")


@engraving_api.route("/api/engraving/<int:engraving_id>/stl/<which>")
def api_engraving_stl(engraving_id, which):
    if which not in _VALID_WHICH:
        abort(404)
    path = _engraving_service.get_artifact_path(engraving_id, "stl", which)
    if not path or not os.path.isfile(path):
        abort(404)
    return send_file(
        path,
        mimetype="model/stl",
        as_attachment=True,
        download_name="engraving_{}_{}.stl".format(engraving_id, which),
    )
