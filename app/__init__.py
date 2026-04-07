"""Application shell exports.

Lazy imports: create_app and main are available via app.main or by
importing them explicitly.  We avoid eagerly importing .main here so
that lightweight subpackages (app.shared, app.domains) can be imported
without triggering the full container/farm_manager import chain.
"""


def __getattr__(name):
    if name in ("create_app", "main"):
        from .main import create_app, main  # noqa: F811
        return create_app if name == "create_app" else main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["create_app", "main"]
