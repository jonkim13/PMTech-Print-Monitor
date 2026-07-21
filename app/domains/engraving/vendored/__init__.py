"""Vendored ImageEngraver library.

Vendored from a client-provided prototype on 2026-07-21. The public
entry point is :func:`api.generate_models`; see ``PROVENANCE.md`` for
the vendoring history and modifications.

Preview rendering (:func:`render.render_preview`,
:func:`render.render_product_previews`) is re-exported lazily via PEP 562
``__getattr__`` so that importing this package does not pull in matplotlib
or trimesh — they load only when a render function is first accessed. This
keeps ``import`` side-effect-free and the ``generate_models`` path light.
"""
from .api import EngraveResult, EngravingError, generate_models

__all__ = [
    "EngraveResult",
    "EngravingError",
    "generate_models",
    "RenderError",
    "render_preview",
    "render_product_previews",
]

_LAZY = {"render_preview", "render_product_previews", "RenderError"}


def __getattr__(name):
    if name in _LAZY:
        from . import render

        return getattr(render, name)
    raise AttributeError("module {!r} has no attribute {!r}".format(__name__, name))
