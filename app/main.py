"""Application shell and composition root."""

import os
import threading
import time

from flask import Flask, jsonify, render_template
from flask_cors import CORS

from .config.container import AppContainer, build_container
from .config.settings import AppSettings, load_settings
from .domains.assignments.routes import register_assignments_routes
from .domains.dashboard.routes import register_dashboard_routes
from .domains.drone.routes import register_drone_routes
from .domains.inventory.routes import register_inventory_routes
from .domains.monitoring.routes import register_monitoring_routes
from .domains.printers.routes import register_printers_routes
from .domains.production.routes import register_production_routes
from .domains.quality.routes import register_quality_routes
from .domains.reports.routes import register_reports_routes
from .domains.work_orders.routes import register_work_order_routes
from .shared.migrations.runner import MigrationRunner
from .shared.snapshots.runner import prune_snapshots, snapshot_all_dbs

_runtime_lock = threading.Lock()
_runtime_container = None
_poller_started = False

_GCODE_MAX_AGE_SEC = 24 * 60 * 60  # 24 hours


def cleanup_old_gcode_uploads(uploads_dir):
    """Delete old staged upload trees from the uploads directory."""
    if not uploads_dir or not os.path.isdir(uploads_dir):
        return
    cutoff = time.time() - _GCODE_MAX_AGE_SEC
    count = 0
    for root, dirs, files in os.walk(uploads_dir, topdown=False):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    count += 1
            except OSError:
                pass
        for dname in dirs:
            dpath = os.path.join(root, dname)
            try:
                if not os.listdir(dpath):
                    os.rmdir(dpath)
            except OSError:
                pass
    if count:
        print("[CLEANUP] Removed {} old staged gcode file(s)".format(count))


def _get_runtime_container(settings: AppSettings = None) -> AppContainer:
    """Build the shared runtime dependencies once per process."""
    global _runtime_container

    with _runtime_lock:
        if _runtime_container is None:
            active_settings = settings or load_settings()
            active_settings.ensure_runtime_dirs()
            _snapshot_and_prune(active_settings)
            cleanup_old_gcode_uploads(active_settings.gcode_uploads_dir)
            MigrationRunner(
                active_settings.work_order_db_path
            ).ensure_schema_version_table()
            _runtime_container = build_container(active_settings)
        return _runtime_container


def _snapshot_and_prune(settings: AppSettings) -> None:
    """Capture a startup snapshot and prune the recovery folder.

    Both steps are best-effort: a broken snapshot layer must not block
    the service from starting. A running app without a snapshot is
    better than a dead app — the snapshot exists *for* the live app.
    """
    try:
        snapshot_all_dbs(settings, reason="startup")
    except Exception as exc:
        print(f"[SNAPSHOT-FAILED] startup snapshot failed: {exc}")
    try:
        prune_snapshots(settings)
    except Exception as exc:
        print(f"[SNAPSHOT-FAILED] snapshot prune failed: {exc}")


def _start_poller_once(farm_manager) -> bool:
    """Start background polling once for the process runtime."""
    global _poller_started

    with _runtime_lock:
        if _poller_started:
            return False
        farm_manager.start_polling()
        _poller_started = True
        return True


def _register_blueprints(app: Flask, container: AppContainer) -> None:
    """Register every domain blueprint with the application."""
    register_dashboard_routes(app, container.dashboard_service)
    register_printers_routes(app, container.farm_manager)
    register_monitoring_routes(
        app,
        container.event_service,
        container.farm_manager,
        container.history_db,
    )
    register_inventory_routes(app, container.inventory_service)
    register_assignments_routes(
        app, container.assignment_service, container.farm_manager,
    )
    register_drone_routes(app, container.drone_controller)
    register_production_routes(
        app,
        container.production_service,
        container.export_service,
        container.farm_manager,
        work_order_service=container.work_order_service,
    )
    register_work_order_routes(
        app,
        container.farm_manager,
        gcode_uploads_dir=container.settings.gcode_uploads_dir,
        execution_service=container.execution_service,
        work_order_service=container.work_order_service,
        queue_service=container.queue_service,
        triage_service=container.triage_service,
    )
    register_quality_routes(app, container.quality_service)
    register_reports_routes(app, container.weekly_report_service)


def _register_core_routes(app: Flask, container: AppContainer) -> None:
    """Register framework-level routes that don't belong to any domain."""
    filament_db = container.filament_db
    farm_manager = container.farm_manager
    poll_interval_ms = int(container.settings.poll_interval_ms)

    @app.route("/")
    def dashboard():
        return render_template(
            "dashboard.html",
            allowed_suppliers=filament_db.ALLOWED_SUPPLIERS,
            poll_interval_ms=poll_interval_ms,
        )

    @app.route("/api/health")
    def api_health():
        return jsonify({
            "status": "ok",
            "printers": len(farm_manager.printers),
            "uptime": "running",
        })


def create_app(settings: AppSettings = None, start_poller: bool = True) -> Flask:
    """Create and configure the Flask application."""
    container = _get_runtime_container(settings)

    app = Flask(
        __name__,
        static_folder=container.settings.static_dir,
        template_folder=container.settings.template_dir,
    )
    CORS(app)
    app.config["MAX_CONTENT_LENGTH"] = container.settings.max_content_length
    app.extensions["print_farm_container"] = container

    _register_blueprints(app, container)
    _register_core_routes(app, container)

    if start_poller:
        _start_poller_once(container.farm_manager)

    return app


def _print_startup_banner(container: AppContainer) -> None:
    """Emit the existing startup summary."""
    settings = container.settings
    print(f"\n{'=' * 50}")
    print("  Print Farm Monitor running!")
    print(f"  Dashboard:  http://localhost:{settings.server_port}")
    print(f"  API:        http://localhost:{settings.server_port}/api/printers")
    print(f"  Inventory:  {settings.inventory_db_path}")
    print(f"  History:    {settings.history_db_path}")
    print(f"  Production: {settings.production_db_path}")
    print(f"  WorkOrders: {settings.work_order_db_path}")
    print(f"  Uploads:    {settings.upload_session_db_path}")
    print(f"  Snapshots:  {settings.snapshots_dir}")
    print(f"{'=' * 50}\n")


def main() -> None:
    """Run the current application entrypoint."""
    app = create_app()
    container = app.extensions["print_farm_container"]
    _print_startup_banner(container)
    app.run(host="0.0.0.0", port=container.settings.server_port, debug=False)
