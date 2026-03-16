"""
PRINT FARM MONITOR - Server Entrypoint
=========================================
Loads configuration, initializes all subsystems,
and starts the Flask web server.

Usage:
    python server.py

Then open http://localhost:5001 in your browser.

Configuration:
    Edit config.yaml with your printer IPs and API keys.
"""

import os
import re

import yaml
from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS

from database import PrintHistoryDB, FilamentInventoryDB, FilamentAssignmentDB
from production_db import ProductionDB
from farm_manager import PrintFarmManager
from drone import DroneController
from routes import register_routes
from production_routes import register_production_routes

# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _resolve_env_vars(obj):
    """Replace ${VAR} placeholders with environment variable values."""
    if isinstance(obj, str):
        return re.sub(
            r"\$\{(\w+)\}",
            lambda m: os.environ.get(m.group(1), m.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(i) for i in obj]
    return obj


def load_config():
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    config_path = os.path.join(BASE_DIR, "config.yaml")
    if not os.path.exists(config_path):
        print(f"ERROR: {config_path} not found.")
        exit(1)
    with open(config_path, "r") as f:
        return _resolve_env_vars(yaml.safe_load(f))


def ensure_data_dir():
    """Create the data/ directory if it doesn't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# MAIN
# ============================================================

def main():
    config = load_config()
    ensure_data_dir()

    # --- Database paths ---
    inventory_db_path = config.get(
        "db_path",
        os.path.join(DATA_DIR, "FilamentInventory.db")
    )
    # If db_path is relative, make it relative to BASE_DIR
    if not os.path.isabs(inventory_db_path):
        inventory_db_path = os.path.join(BASE_DIR, inventory_db_path)

    history_db_path = os.path.join(DATA_DIR, "print_history.db")
    production_db_path = os.path.join(DATA_DIR, "production_log.db")
    snapshots_dir = os.path.join(DATA_DIR, "snapshots")

    # --- Initialize databases ---
    filament_db = FilamentInventoryDB(inventory_db_path)
    history_db = PrintHistoryDB(history_db_path)
    assignment_db_path = os.path.join(DATA_DIR, "assignments.db")
    assignment_db = FilamentAssignmentDB(assignment_db_path)
    production_db = ProductionDB(production_db_path,
                                 snapshots_dir=snapshots_dir)

    # --- Initialize managers ---
    farm_manager = PrintFarmManager(config, history_db,
                                    filament_db=filament_db,
                                    assignment_db=assignment_db,
                                    production_db=production_db,
                                    snapshots_dir=snapshots_dir,
                                    data_dir=DATA_DIR)
    drone_controller = DroneController()

    # --- Flask app ---
    app = Flask(__name__, static_folder="static", template_folder="templates")
    CORS(app)
    max_upload_mb = int(config.get("max_upload_mb", 512))
    app.config["MAX_CONTENT_LENGTH"] = max_upload_mb * 1024 * 1024

    register_routes(
        app,
        farm_manager,
        filament_db,
        history_db,
        drone_controller,
        assignment_db=assignment_db,
        ui_config={
            "poll_interval_ms": max(
                1000, int(config.get("poll_interval_sec", 5) * 1000)
            ),
        },
    )
    register_production_routes(app, production_db, farm_manager,
                               snapshots_dir=snapshots_dir)

    # Start background polling
    farm_manager.start_polling()

    # Start web server
    port = config.get("server_port", 5001)
    print(f"\n{'='*50}")
    print(f"  Print Farm Monitor running!")
    print(f"  Dashboard:  http://localhost:{port}")
    print(f"  API:        http://localhost:{port}/api/printers")
    print(f"  Inventory:  {inventory_db_path}")
    print(f"  History:    {history_db_path}")
    print(f"  Production: {production_db_path}")
    print(f"  Snapshots:  {snapshots_dir}")
    print(f"{'='*50}\n")

    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
