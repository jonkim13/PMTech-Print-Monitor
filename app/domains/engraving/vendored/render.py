"""Headless PNG preview rendering for engraved meshes.

New code (not part of the client delivery). Derived from the disposable
prototype preview script ``~/Downloads/ImageEngraver/render_previews.py``
(matplotlib ``plot_trisurf``-style ``Poly3DCollection`` render of a mesh
decimated to ~50k faces via trimesh's ``simplify_quadric_decimation``,
whose backend is ``fast-simplification``).

Two facts carried over from that script:

* The engraver builds parts **Y-up** (thickness in Y), so the mesh is
  rotated Y->Z for rendering only; matplotlib's Z-up ``elev``/``azim``
  then mean what they say.
* matplotlib's painter's-algorithm depth sort produces black-wedge
  artifacts on isometric views but renders cleanly on axis-aligned
  views, so only the axis-aligned ``top`` view is supported here.

Rendering uses the object-oriented Agg canvas (``Figure`` +
``FigureCanvasAgg``) rather than ``matplotlib.use('Agg')``: Agg is forced
for our figures without mutating the process-global backend, so this is
safe under systemd with no display and cannot clash with any other code
that imports matplotlib.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import trimesh
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import mpl_toolkits.mplot3d  # noqa: F401 - registers the '3d' projection

from .api import EngravingError

logger = logging.getLogger(__name__)

# (elev, azim), Z-up. Only the axis-aligned top view is supported; the
# iso view from the prototype is intentionally omitted (depth-sort artifacts).
_VIEWS = {"top": (90, -90)}

# Rotate Y-up geometry to Z-up for rendering only (thickness Y -> Z).
_Y_UP_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0])

# Above this multiple of max_faces, refuse to render undecimated rather than
# grind for minutes through Poly3DCollection (435k faces on a Pi ~= minutes).
_DECIMATION_HARD_LIMIT_FACTOR = 4


class RenderError(EngravingError):
    """A preview render failed (mesh load, decimation limit, or draw)."""


def render_preview(stl_path, out_png_path, view: str = "top", max_faces: int = 50000) -> str:
    """Render a top-down PNG preview of an STL mesh.

    Args:
        stl_path: path to a binary STL (absolute or relative; no CWD
            dependence beyond the path you pass).
        out_png_path: where to write the PNG. Parent dirs are created.
        view: only ``'top'`` is supported; anything else raises
            ``NotImplementedError``.
        max_faces: decimation target. Meshes above this are decimated via
            trimesh/fast-simplification for render speed; the on-disk STL is
            untouched.

    Returns:
        ``out_png_path`` (as a string).

    Raises:
        FileNotFoundError: ``stl_path`` does not exist.
        NotImplementedError: ``view`` is not ``'top'``.
        RenderError: mesh load/draw failed, or the mesh is too large to
            render and decimation is unavailable.
    """
    if view not in _VIEWS:
        raise NotImplementedError(
            "view={!r} not supported; only {} is available".format(
                view, sorted(_VIEWS)
            )
        )

    stl_path = Path(stl_path)
    if not stl_path.is_file():
        raise FileNotFoundError("STL not found: {}".format(stl_path))

    try:
        mesh = trimesh.load(str(stl_path), force="mesh")
    except Exception as exc:  # noqa: BLE001 - wrap with context
        raise RenderError("failed to load mesh {}: {}".format(stl_path, exc)) from exc

    face_count = len(mesh.faces)
    if face_count > max_faces:
        try:
            mesh = mesh.simplify_quadric_decimation(face_count=max_faces)
        except Exception as exc:  # noqa: BLE001 - fast-simplification missing/failed
            if face_count > _DECIMATION_HARD_LIMIT_FACTOR * max_faces:
                raise RenderError(
                    "mesh {} has {} faces (> {}x max_faces={}) and decimation "
                    "is unavailable ({}); refusing full-mesh render".format(
                        stl_path,
                        face_count,
                        _DECIMATION_HARD_LIMIT_FACTOR,
                        max_faces,
                        exc,
                    )
                ) from exc
            logger.warning(
                "decimation unavailable for %s (%s); rendering full mesh "
                "(%d faces, under %dx max_faces=%d)",
                stl_path,
                exc,
                face_count,
                _DECIMATION_HARD_LIMIT_FACTOR,
                max_faces,
            )

    mesh.apply_transform(_Y_UP_TO_Z_UP)

    out_png_path = Path(out_png_path)
    out_png_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _draw(mesh, out_png_path, view)
    except Exception as exc:  # noqa: BLE001 - wrap with context
        raise RenderError(
            "failed to render {} -> {}: {}".format(stl_path, out_png_path, exc)
        ) from exc

    return str(out_png_path)


def render_product_previews(engrave_result, out_dir) -> dict:
    """Render top-view previews of the product and mold from a result.

    Args:
        engrave_result: an ``EngraveResult`` (uses ``.prod_path`` and
            ``.mold_path``).
        out_dir: directory to write the PNGs into (created if needed).

    Returns:
        ``{'prod': <png path>, 'mold': <png path>}``.
    """
    out_dir = Path(out_dir)
    paths = {}
    for key, stl_path in (("prod", engrave_result.prod_path), ("mold", engrave_result.mold_path)):
        stl_path = Path(stl_path)
        out_png = out_dir / "{}_top.png".format(stl_path.stem)
        paths[key] = render_preview(stl_path, out_png, view="top")
    return paths


def _draw(mesh, out_png_path, view: str) -> None:
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    elev, azim = _VIEWS[view]

    fig = Figure(figsize=(6, 6), dpi=110)
    FigureCanvasAgg(fig)  # force the Agg canvas without touching the global backend
    ax = fig.add_subplot(111, projection="3d")

    tris = mesh.vertices[mesh.faces]
    coll = Poly3DCollection(tris, linewidths=0, edgecolors="none")
    # Shade by face-normal z so the relief reads (post Y->Z rotation, z is up).
    shade = 0.25 + 0.75 * ((mesh.face_normals[:, 2] + 1.0) / 2.0)
    coll.set_facecolor([(s * 0.55, s * 0.65, s * 0.8) for s in shade])
    ax.add_collection3d(coll)

    lo, hi = mesh.bounds
    ctr = (lo + hi) / 2.0
    r = float((hi - lo).max()) / 2.0
    ax.set_xlim(ctr[0] - r, ctr[0] + r)
    ax.set_ylim(ctr[1] - r, ctr[1] + r)
    ax.set_zlim(ctr[2] - r, ctr[2] + r)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()

    fig.savefig(str(out_png_path), bbox_inches="tight", pad_inches=0.1)
