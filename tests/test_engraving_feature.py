"""Phase E-2 — Custom Engraving feature: repository, service, routes.

Covers the engraving_requests repository CRUD, the service submit/validate/
generation flow (with the vendored generator faked for speed), the boot-time
stale sweep, and the Flask routes (upload -> WO creation, decoupled status
endpoint, and DB-record-resolved file serving with path-safety).

One test (``test_e2e_real_generation``) runs the *real* vendored generator
against the fixture image; its runtime is reported by ``--durations``.

Run under the repo venv (has cv2/numpy/matplotlib/trimesh):
    ./venv/bin/python -m pytest tests/test_engraving_feature.py
"""

import io
import os
import sys
import time

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.config.settings import AppSettings
from app.domains.engraving.repository import EngravingRepository
from app.domains.engraving.routes import register_engraving_routes
from app.domains.engraving.service import (
    EngravingService,
    EngravingValidationError,
)
from app.domains.queue.bulk_operations import QueueBulkOperations
from app.domains.queue.execution_repository import QueueExecutionRepository
from app.domains.queue.repository import QueueRepository
from app.domains.work_orders.job_repository import JobRepository
from app.domains.work_orders.repository import WorkOrderRepository
from app.domains.work_orders.service import WorkOrderService

FIXTURE = os.path.join(
    ROOT_DIR, "tests", "fixtures", "engraving", "PM_Technologies_Vert.png"
)


# ----------------------------------------------------------------------
# Helpers / fakes
# ----------------------------------------------------------------------

def _settings(tmp_path, timeout=5):
    return AppSettings(
        base_dir=ROOT_DIR, config_path="", env_path="",
        data_dir=str(tmp_path),
        config={"engraving": {
            "generation_timeout_sec": timeout,
            "min_quantity": 1, "max_quantity": 50,
            "min_dimension_px": 64, "max_dimension_px": 5000,
            "products": {"Coaster_100mm_Square": {
                "display_name": "Custom Engraved Coaster (100mm Square)",
                "material": "PLA",
            }},
        }},
    )


class _FakeResult:
    def __init__(self, tmp):
        self.mold_path = os.path.join(tmp, "mold.stl")
        self.prod_path = os.path.join(tmp, "prod.stl")
        open(self.mold_path, "wb").write(b"m" * 100)
        open(self.prod_path, "wb").write(b"p" * 100)
        self.triangle_counts = {"mold": 435130, "prod": 435114}
        self.duration_seconds = 1.23


def _fake_generate(image_path, out_dir, product_key=None):
    return _FakeResult(out_dir)


def _fake_render(result, out_dir):
    mold = os.path.join(out_dir, "mold_top.png")
    prod = os.path.join(out_dir, "prod_top.png")
    open(mold, "wb").write(b"\x89PNG" + b"m" * 200)
    open(prod, "wb").write(b"\x89PNG" + b"p" * 200)
    return {"mold": mold, "prod": prod}


def _valid_png_bytes(w=100, h=100):
    import cv2
    import numpy as np
    return cv2.imencode(".png", np.full((h, w, 3), 128, np.uint8))[1].tobytes()


class _FileStorageStub:
    def __init__(self, data, filename="logo.png", mimetype="image/png"):
        self._data = data
        self.filename = filename
        self.mimetype = mimetype

    def read(self):
        return self._data


def _make_service(tmp_path, *, generate=_fake_generate, render=_fake_render,
                  spawn="inline", timeout=5):
    settings = _settings(tmp_path, timeout=timeout)
    db = settings.work_order_db_path
    wo_service = WorkOrderService(
        work_order_repository=WorkOrderRepository(db),
        job_repository=JobRepository(db),
        queue_repository=QueueRepository(db),
        queue_bulk_operations=QueueBulkOperations(db),
        queue_execution_repository=QueueExecutionRepository(db),
    )
    repo = EngravingRepository(db)
    spawn_fn = None
    if spawn == "inline":
        def spawn_fn(eid, up, pk, od):
            svc._run_generation(eid, up, pk, od)
    svc = EngravingService(
        repository=repo, work_order_service=wo_service, settings=settings,
        generate_models=generate, render_previews=render, spawn=spawn_fn,
    )
    return svc, repo, wo_service, settings, db


# ----------------------------------------------------------------------
# Repository CRUD
# ----------------------------------------------------------------------

def test_repository_crud(tmp_path):
    svc, repo, wo_service, settings, db = _make_service(tmp_path)
    wo = wo_service.create_work_order("Acme", [], jobs=[{
        "job_type": "Internal", "parts": [
            {"part_name": "X", "material": "PLA", "quantity": 1}]}])
    eid = repo.create(wo_id=wo["wo_id"], product_key="Coaster_100mm_Square",
                      customer_name="Acme", quantity=2,
                      original_filename="logo.png")
    rec = repo.get(eid)
    assert rec["status"] == "generating"
    assert rec["wo_id"] == wo["wo_id"]
    assert repo.get_by_wo(wo["wo_id"])["engraving_id"] == eid

    assert repo.mark_ready(
        eid, mold_stl_path="m", prod_stl_path="p",
        mold_preview_path="mp", prod_preview_path="pp",
        mold_triangles=1, prod_triangles=2, duration_seconds=3.0) is True
    assert repo.get(eid)["status"] == "ready"

    # Compare-and-set: a second terminal write no longer applies.
    assert repo.mark_failed(eid, "late") is False
    assert repo.get(eid)["status"] == "ready"


def test_foreign_key_enforced(tmp_path):
    """wo_id FK is real — inserting against a missing WO raises."""
    import sqlite3
    svc, repo, wo_service, settings, db = _make_service(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create(wo_id="WO-999", product_key="Coaster_100mm_Square",
                    customer_name="Nobody", quantity=1,
                    original_filename="x.png")


# ----------------------------------------------------------------------
# Service — happy path
# ----------------------------------------------------------------------

def test_submit_creates_wo_and_generates(tmp_path):
    svc, repo, wo_service, settings, db = _make_service(tmp_path)
    res = svc.submit_request(
        uploaded_file=_FileStorageStub(_valid_png_bytes()),
        product_key="Coaster_100mm_Square", quantity=3, customer_name="Acme")

    assert res["wo_id"].startswith("WO-")
    view = svc.get_wo_engraving_view(res["wo_id"])
    assert view["status"] == "ready"
    assert view["triangle_counts"] == {"mold": 435130, "prod": 435114}
    assert view["stl_prod_url"].endswith("/stl/prod")

    # Normal Internal WO with quantity-many queue_items.
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    n = conn.execute("SELECT COUNT(*) c FROM queue_items WHERE wo_id=?",
                     (res["wo_id"],)).fetchone()["c"]
    job = conn.execute("SELECT job_type, part_name, material FROM jobs "
                       "JOIN queue_items USING(job_id) WHERE jobs.wo_id=?",
                       (res["wo_id"],)).fetchone()
    conn.close()
    assert n == 3
    assert job["job_type"] == "Internal"
    assert job["part_name"] == "Custom Engraved Coaster (100mm Square)"
    assert job["material"] == "PLA"


# ----------------------------------------------------------------------
# Service — validation rejections (no WO created)
# ----------------------------------------------------------------------

@pytest.mark.parametrize("kwargs,frag", [
    (dict(product_key="Nope"), "Unsupported product"),
    (dict(customer_name="  "), "Customer name"),
    (dict(quantity=0), "between"),
    (dict(quantity=51), "between"),
    (dict(quantity="abc"), "whole number"),
])
def test_validation_field_rejections(tmp_path, kwargs, frag):
    svc, repo, wo_service, settings, db = _make_service(tmp_path)
    base = dict(uploaded_file=_FileStorageStub(_valid_png_bytes()),
                product_key="Coaster_100mm_Square", quantity=3,
                customer_name="Acme")
    base.update(kwargs)
    with pytest.raises(EngravingValidationError) as exc:
        svc.submit_request(**base)
    assert frag.lower() in str(exc.value).lower()
    # No WO was created — validation fails before create_work_order.
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM work_orders").fetchone()[0] == 0
    finally:
        conn.close()


@pytest.mark.parametrize("fs,frag", [
    (None, "image file is required"),
    (_FileStorageStub(b"", "e.png"), "empty"),
    (_FileStorageStub(b"hello", "e.txt"), "Unsupported image type"),
    (_FileStorageStub(b"not an image", "e.png"), "not a valid image"),
])
def test_validation_image_rejections(tmp_path, fs, frag):
    svc, repo, wo_service, settings, db = _make_service(tmp_path)
    with pytest.raises(EngravingValidationError) as exc:
        svc.submit_request(uploaded_file=fs,
                           product_key="Coaster_100mm_Square",
                           quantity=1, customer_name="Acme")
    assert frag.lower() in str(exc.value).lower()


def test_validation_min_dimensions(tmp_path):
    svc, repo, wo_service, settings, db = _make_service(tmp_path)
    with pytest.raises(EngravingValidationError) as exc:
        svc.submit_request(
            uploaded_file=_FileStorageStub(_valid_png_bytes(10, 10)),
            product_key="Coaster_100mm_Square", quantity=1,
            customer_name="Acme")
    assert "too small" in str(exc.value).lower()


# ----------------------------------------------------------------------
# Service — background failure & timeout
# ----------------------------------------------------------------------

def test_generation_failure_marks_failed(tmp_path):
    def boom(image_path, out_dir, product_key=None):
        raise RuntimeError("mesh exploded")
    svc, repo, wo_service, settings, db = _make_service(
        tmp_path, generate=boom)
    res = svc.submit_request(
        uploaded_file=_FileStorageStub(_valid_png_bytes()),
        product_key="Coaster_100mm_Square", quantity=1, customer_name="Acme")
    view = svc.get_wo_engraving_view(res["wo_id"])
    assert view["status"] == "failed"
    assert "mesh exploded" in view["error_message"]


def test_generation_timeout_marks_failed(tmp_path):
    def slow(image_path, out_dir, product_key=None):
        time.sleep(2.0)
        return _FakeResult(out_dir)
    svc, repo, wo_service, settings, db = _make_service(
        tmp_path, generate=slow, timeout=0.3)
    res = svc.submit_request(
        uploaded_file=_FileStorageStub(_valid_png_bytes()),
        product_key="Coaster_100mm_Square", quantity=1, customer_name="Acme")
    view = svc.get_wo_engraving_view(res["wo_id"])
    assert view["status"] == "failed"
    assert "timed out" in view["error_message"]


def test_stale_generating_sweep(tmp_path):
    svc, repo, wo_service, settings, db = _make_service(tmp_path)
    wo = wo_service.create_work_order("Acme", [], jobs=[{
        "job_type": "Internal", "parts": [
            {"part_name": "X", "material": "PLA", "quantity": 1}]}])
    eid = repo.create(wo_id=wo["wo_id"], product_key="Coaster_100mm_Square",
                      customer_name="Acme", quantity=1,
                      original_filename="logo.png")
    assert repo.get(eid)["status"] == "generating"
    assert svc.sweep_stale_generating() == 1
    rec = repo.get(eid)
    assert rec["status"] == "failed"
    assert "restart" in rec["error_message"]


# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    from flask import Flask
    svc, repo, wo_service, settings, db = _make_service(tmp_path)
    app = Flask(__name__, template_folder=os.path.join(ROOT_DIR, "templates"),
                static_folder=os.path.join(ROOT_DIR, "static"))
    register_engraving_routes(app, svc, settings)
    app.config["_svc"] = svc
    return app.test_client()


def test_route_get_form(client):
    resp = client.get("/engraving/new")
    assert resp.status_code == 200
    assert b"Custom Engraving" in resp.data


def test_route_post_creates_wo_and_redirects(client):
    data = {
        "product_key": "Coaster_100mm_Square",
        "quantity": "2",
        "customer_name": "Acme",
        "image": (io.BytesIO(_valid_png_bytes()), "logo.png"),
    }
    resp = client.post("/engraving", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "/work-orders/WO-" in resp.headers["Location"]


def test_route_post_validation_rerenders_form_no_wo(client):
    data = {  # no image
        "product_key": "Coaster_100mm_Square",
        "quantity": "2", "customer_name": "Acme",
    }
    resp = client.post("/engraving", data=data,
                       content_type="multipart/form-data")
    assert resp.status_code == 400
    assert b"image file is required" in resp.data


def test_route_wo_engraving_null_for_plain_wo(client):
    resp = client.get("/api/work-orders/WO-404/engraving")
    assert resp.status_code == 200
    assert resp.get_json() is None


def test_route_file_serving_and_path_safety(client):
    svc = client.application.config["_svc"]
    res = svc.submit_request(
        uploaded_file=_FileStorageStub(_valid_png_bytes()),
        product_key="Coaster_100mm_Square", quantity=1, customer_name="Acme")
    eid = svc.get_wo_engraving_view(res["wo_id"])["engraving_id"]

    # Valid preview + STL serve.
    assert client.get("/api/engraving/%d/preview/prod" % eid).status_code == 200
    stl = client.get("/api/engraving/%d/stl/mold" % eid)
    assert stl.status_code == 200
    assert "attachment" in stl.headers.get("Content-Disposition", "")

    # Invalid 'which' is rejected before any path construction.
    assert client.get("/api/engraving/%d/preview/passwd" % eid).status_code == 404
    assert client.get("/api/engraving/%d/stl/etc" % eid).status_code == 404
    # Unknown id -> 404.
    assert client.get("/api/engraving/999999/preview/prod").status_code == 404


# ----------------------------------------------------------------------
# End-to-end with the REAL vendored generator (slow — reports runtime)
# ----------------------------------------------------------------------

def test_e2e_real_generation(tmp_path):
    # Real generate_models + render_product_previews (no fakes).
    svc, repo, wo_service, settings, db = _make_service(
        tmp_path, generate=None, render=None, timeout=120)
    with open(FIXTURE, "rb") as fh:
        png = fh.read()
    res = svc.submit_request(
        uploaded_file=_FileStorageStub(png, "PM_Technologies_Vert.png"),
        product_key="Coaster_100mm_Square", quantity=1, customer_name="Acme")
    view = svc.get_wo_engraving_view(res["wo_id"])
    assert view["status"] == "ready", view.get("error_message")
    # Golden triangle counts from the E-1 smoke test.
    assert view["triangle_counts"] == {"mold": 435130, "prod": 435114}
    for key in ("prod_stl_path", "mold_stl_path",
                "prod_preview_path", "mold_preview_path"):
        assert os.path.isfile(repo.get(view["engraving_id"])[key])
