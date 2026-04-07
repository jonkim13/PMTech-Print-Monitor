"""Application configuration helpers.

Lazy imports to avoid circular dependency with farm_manager at
module-load time.
"""

__all__ = [
    "AppContainer",
    "AppSettings",
    "build_container",
    "load_settings",
]


def __getattr__(name):
    if name in ("AppContainer", "build_container"):
        from .container import AppContainer, build_container
        return {"AppContainer": AppContainer,
                "build_container": build_container}[name]
    if name in ("AppSettings", "load_settings"):
        from .settings import AppSettings, load_settings
        return {"AppSettings": AppSettings,
                "load_settings": load_settings}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
