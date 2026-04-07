"""Application settings and path helpers."""

import os
import re
from dataclasses import dataclass

import yaml
from dotenv import load_dotenv


def _resolve_env_vars(obj):
    """Replace ${VAR} placeholders with environment variable values."""
    if isinstance(obj, str):
        return re.sub(
            r"\$\{(\w+)\}",
            lambda match: os.environ.get(match.group(1), match.group(0)),
            obj,
        )
    if isinstance(obj, dict):
        return {key: _resolve_env_vars(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(item) for item in obj]
    return obj


@dataclass(frozen=True)
class AppSettings:
    """Resolved application configuration and runtime paths."""

    base_dir: str
    config_path: str
    env_path: str
    data_dir: str
    config: dict

    def normalize_path(self, path_value: str) -> str:
        """Resolve relative paths from the project root."""
        if os.path.isabs(path_value):
            return path_value
        return os.path.join(self.base_dir, path_value)

    def ensure_runtime_dirs(self) -> None:
        """Create the runtime directories expected during startup."""
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.gcode_uploads_dir, exist_ok=True)

    @property
    def static_dir(self) -> str:
        return os.path.join(self.base_dir, "static")

    @property
    def template_dir(self) -> str:
        return os.path.join(self.base_dir, "templates")

    @property
    def inventory_db_path(self) -> str:
        inventory_db_path = self.config.get(
            "db_path",
            os.path.join(self.data_dir, "FilamentInventory.db"),
        )
        return self.normalize_path(inventory_db_path)

    @property
    def history_db_path(self) -> str:
        return os.path.join(self.data_dir, "print_history.db")

    @property
    def assignment_db_path(self) -> str:
        return os.path.join(self.data_dir, "assignments.db")

    @property
    def production_db_path(self) -> str:
        return os.path.join(self.data_dir, "production_log.db")

    @property
    def work_order_db_path(self) -> str:
        return os.path.join(self.data_dir, "work_orders.db")

    @property
    def upload_session_db_path(self) -> str:
        return os.path.join(self.data_dir, "upload_sessions.db")

    @property
    def snapshots_dir(self) -> str:
        return os.path.join(self.data_dir, "snapshots")

    @property
    def gcode_uploads_dir(self) -> str:
        return os.path.join(self.data_dir, "gcode_uploads")

    @property
    def server_port(self) -> int:
        return self.config.get("server_port", 5001)

    @property
    def poll_interval_ms(self) -> int:
        return max(1000, int(self.config.get("poll_interval_sec", 5) * 1000))

    @property
    def max_content_length(self) -> int:
        return 500 * 1024 * 1024


def load_settings(base_dir: str = None) -> AppSettings:
    """Load `.env`, parse `config.yaml`, and resolve runtime settings."""
    project_root = base_dir or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    env_path = os.path.join(project_root, ".env")
    config_path = os.path.join(project_root, "config.yaml")

    load_dotenv(env_path)
    if not os.path.exists(config_path):
        print(f"ERROR: {config_path} not found.")
        raise SystemExit(1)

    with open(config_path, "r", encoding="utf-8") as handle:
        config = _resolve_env_vars(yaml.safe_load(handle))

    return AppSettings(
        base_dir=project_root,
        config_path=config_path,
        env_path=env_path,
        data_dir=os.path.join(project_root, "data"),
        config=config,
    )
