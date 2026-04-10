"""Application shell and composition root."""

import threading

from flask import Flask
from flask_cors import CORS

from production_routes import register_production_routes
from routes import cleanup_old_gcode_uploads, register_routes
from work_order_routes import register_work_order_routes

from .config.container import AppContainer, build_container
from .config.settings import AppSettings, load_settings

_runtime_lock = threading.Lock()
_runtime_container = None
_poller_started = False


def _get_runtime_container(settings: AppSettings = None) -> AppContainer:
    """Build the shared runtime dependencies once per process."""
    global _runtime_container

    with _runtime_lock:
        if _runtime_container is None:
            active_settings = settings or load_settings()
            active_settings.ensure_runtime_dirs()
            cleanup_old_gcode_uploads(active_settings.gcode_uploads_dir)
            _runtime_container = build_container(active_settings)
        return _runtime_container


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
    """Register the existing route modules with unchanged dependencies."""
    register_routes(
        app,
        container.farm_manager,
        container.filament_db,
        container.history_db,
        container.drone_controller,
        assignment_db=container.assignment_db,
        ui_config={"poll_interval_ms": container.settings.poll_interval_ms},
        gcode_uploads_dir=container.settings.gcode_uploads_dir,
        execution_service=container.execution_service,
        event_service=container.event_service,
        inventory_service=container.inventory_service,
        assignment_service=container.assignment_service,
    )
    register_production_routes(
        app,
        container.production_service,
        container.export_service,
        container.farm_manager,
    )
    register_work_order_routes(
        app,
        container.work_order_db,
        container.farm_manager,
        gcode_uploads_dir=container.settings.gcode_uploads_dir,
        execution_service=container.execution_service,
    )


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
