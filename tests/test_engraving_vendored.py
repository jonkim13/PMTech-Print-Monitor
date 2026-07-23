"""Vendored ImageEngraver library — API contract + golden-output tests.

Covers the public surface of ``app.domains.engraving.vendored``:

* importing the package/``api`` module has zero side effects (no files
  written, importable with no image present);
* ``generate_models`` raises ``ValueError`` for an unknown product and
  ``FileNotFoundError`` for a missing image;
* a full ``Coaster_100mm_Square`` generation reproduces the golden triangle
  counts (mold 436,990 / product 436,974) and writes well-formed binary STL
  (size == 84 + 50 * triangles);
* the generated meshes are closed manifold solids with correctly oriented
  faces -- see "mesh integrity" at the bottom of this file.

The full-generation test is unmarked (the suite has no ``slow`` marker
convention); it runs in ~5 s on the repo venv.
"""

import os
import struct
import subprocess
import sys

import numpy as np
import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

FIXTURE = os.path.join(
    ROOT_DIR, "tests", "fixtures", "engraving", "PM_Technologies_Vert.png"
)

# Golden triangle counts. Drift detection only - counts alone never noticed
# that the relief was a disconnected floating shell. The mesh-integrity tests
# at the bottom of this file are the ones that matter.
#
# Was {"mold": 435130, "prod": 435114} (the prototype's quote1001 smoke test)
# until refan_border started welding the relief into the template: that
# subdivides the four template faces bounding the hole into one triangle per
# relief border segment, +4*465 = +1860 triangles per model.
GOLDEN = {"mold": 436990, "prod": 436974}


def test_import_has_no_side_effects(tmp_path):
    """Importing the package + api creates no files and needs no image."""
    code = (
        "import app.domains.engraving as eng\n"
        "from app.domains.engraving.vendored import api, generate_models, "
        "EngraveResult, EngravingError\n"
        "assert callable(generate_models)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT_DIR
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # keep the check pristine
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert os.listdir(tmp_path) == [], "import wrote files into the cwd"


def test_unknown_product_key_raises_value_error(tmp_path):
    from app.domains.engraving.vendored import generate_models

    with pytest.raises(ValueError):
        generate_models(FIXTURE, tmp_path, product_key="NotARealProduct")


def test_missing_image_raises_file_not_found(tmp_path):
    from app.domains.engraving.vendored import generate_models

    with pytest.raises(FileNotFoundError):
        generate_models(tmp_path / "does_not_exist.png", tmp_path)


def _assert_valid_binary_stl(path, triangles):
    size = os.path.getsize(path)
    assert size == 84 + 50 * triangles, (
        "{}: size {} != 84 + 50*{}".format(path, size, triangles)
    )
    with open(path, "rb") as fh:
        fh.read(80)  # header
        count = struct.unpack("<I", fh.read(4))[0]
    assert count == triangles, "{}: header count {} != {}".format(
        path, count, triangles
    )


def test_full_generation_matches_golden(tmp_path):
    from app.domains.engraving.vendored import EngraveResult, generate_models

    result = generate_models(
        FIXTURE, tmp_path, product_key="Coaster_100mm_Square", invert=False
    )

    assert isinstance(result, EngraveResult)
    assert os.path.isfile(result.mold_path)
    assert os.path.isfile(result.prod_path)

    # Exact match to the smoke-test golden values.
    assert result.triangle_counts == GOLDEN

    _assert_valid_binary_stl(result.mold_path, result.triangle_counts["mold"])
    _assert_valid_binary_stl(result.prod_path, result.triangle_counts["prod"])

    # Filenames derive from the image stem — no quoteNum, no _F/_T.
    assert os.path.basename(result.mold_path) == "mold_PM_Technologies_Vert.stl"
    assert os.path.basename(result.prod_path) == "prod_PM_Technologies_Vert.stl"
    assert result.duration_seconds > 0


# --- render.py preview rendering --------------------------------------------

# Generation is the expensive step (~5 s); do it ONCE for all render tests.
@pytest.fixture(scope="module")
def engrave_result(tmp_path_factory):
    from app.domains.engraving.vendored import generate_models

    out = tmp_path_factory.mktemp("engrave_gen")
    return generate_models(
        FIXTURE, out, product_key="Coaster_100mm_Square", invert=False
    )


def test_render_preview_produces_valid_png(engrave_result, tmp_path):
    from PIL import Image

    from app.domains.engraving.vendored import render_preview

    out = tmp_path / "prod_top.png"
    returned = render_preview(engrave_result.prod_path, out)

    assert returned == str(out)
    assert os.path.isfile(out)
    assert os.path.getsize(out) > 10 * 1024  # non-trivial, not a blank canvas
    with Image.open(out) as im:
        width, height = im.size
    assert width > 100 and height > 100


def test_render_missing_stl_raises_file_not_found(tmp_path):
    from app.domains.engraving.vendored import render_preview

    with pytest.raises(FileNotFoundError):
        render_preview(tmp_path / "does_not_exist.stl", tmp_path / "out.png")


def test_render_iso_view_raises_not_implemented(engrave_result, tmp_path):
    from app.domains.engraving.vendored import render_preview

    with pytest.raises(NotImplementedError):
        render_preview(engrave_result.prod_path, tmp_path / "out.png", view="iso")


def test_render_product_previews_returns_both(engrave_result, tmp_path):
    from app.domains.engraving.vendored import render_product_previews

    paths = render_product_previews(engrave_result, tmp_path)

    assert set(paths) == {"prod", "mold"}
    for key, png in paths.items():
        assert os.path.isfile(png), key
        assert os.path.getsize(png) > 10 * 1024, key


# --- mesh integrity ---------------------------------------------------------
#
# The vendored engraver grafts an image-derived relief into a hole punched in a
# watertight template. Triangle counts alone never noticed that the graft left
# the two shells completely disconnected, so these tests pin the properties
# that actually matter for slicing. Topology is computed here with numpy only:
# trimesh is a declared dependency but its graph helpers need networkx/scipy,
# which are not installed.

# Configured engraving depth for Coaster_100mm_Square (info_dict.py).
DEPTH_MM = 2.0
# Plane of the template surface the relief is grafted into, per role.
SURFACE_Y = {"mold": -5.0, "prod": 5.0}
TEMPLATE_DIR = os.path.join(
    ROOT_DIR, "app", "domains", "engraving", "vendored", "templates"
)
MOLD_TEMPLATE = "100mmSquare_Mold_RevA.STL"
PROD_TEMPLATE = "100mmSquare_Prod_RevA.STL"


def _read_stl(path):
    """Return (normals, triangles) as float32 arrays from a binary STL."""
    with open(path, "rb") as fh:
        data = fh.read()
    count = struct.unpack("<I", data[80:84])[0]
    rec = np.frombuffer(data[84 : 84 + 50 * count], dtype=np.uint8).reshape(count, 50)
    vals = rec[:, :48].copy().view("<f4").reshape(count, 4, 3)
    return vals[:, 0, :], vals[:, 1:, :]


# Coordinates are welded at this many decimals (1e-4 mm) before topology is
# computed, matching engraver._WELD_ROUNDING and what slicers do. Welding on
# exact float32 instead reports 76 boundary edges on every mold - but those
# come from 100mmSquare_Mold_RevA.STL itself, whose outer chamfer has vertex
# pairs disagreeing by ~1e-14mm from CAD export. The tolerance is ~1e10 times
# that noise and ~2000 times finer than the relief's 0.2mm vertex spacing.
_WELD_DECIMALS = 4


def _topology(triangles):
    """Weld coincident vertices and return topology counts."""
    count = len(triangles)
    welded = np.round(triangles.reshape(-1, 3).astype(np.float64), _WELD_DECIMALS)
    _, inv = np.unique(welded, axis=0, return_inverse=True)
    faces = inv.reshape(count, 3)

    edges = np.sort(
        np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1
    )
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_edges = int((counts == 1).sum())
    nonmanifold_edges = int((counts > 2).sum())

    # union-find over faces joined by a 2-face edge -> connected shells
    face_of_edge = np.tile(np.arange(count), 3)
    order = np.lexsort((edges[:, 1], edges[:, 0]))
    e_sorted, f_sorted = edges[order], face_of_edge[order]
    same = np.all(e_sorted[1:] == e_sorted[:-1], axis=1)
    parent = np.arange(count)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in np.nonzero(same)[0]:
        ra, rb = find(f_sorted[i]), find(f_sorted[i + 1])
        if ra != rb:
            parent[ra] = rb
    shells = len({find(i) for i in range(count)})

    return {
        "boundary_edges": boundary_edges,
        "nonmanifold_edges": nonmanifold_edges,
        "shells": shells,
        "watertight": boundary_edges == 0 and nonmanifold_edges == 0,
    }


def _relief_depths(stl_path, template_name, role):
    """Depth (mm) below the template surface of every vertex the relief added.

    Relief vertices are isolated by subtracting the template's own vertices,
    which is exact and stable: a bounding box around the grafted square would
    also catch the mold's floor and the product's underside.
    """
    _, tris = _read_stl(stl_path)
    _, template_tris = _read_stl(
        os.path.join(TEMPLATE_DIR, template_name)
    )
    template_pts = {tuple(p) for p in template_tris.reshape(-1, 3)}
    pts = np.unique(tris.reshape(-1, 3), axis=0)
    relief = np.array([tuple(p) not in template_pts for p in pts])
    return np.abs(pts[relief][:, 1] - SURFACE_Y[role])


def test_img2mesh_maps_pixels_to_height_without_wraparound():
    """Regression: uint8 wraparound in the pixel -> height term.

    ``i[y, x] * depth`` on a uint8 image stayed uint8 under numpy >= 2.0
    (NEP 50), so every pixel >= 128 wrapped: 255 * 2 -> 254, and the whole
    upper half of the depth range folded back onto the lower half. This pins
    the mapping across all 256 pixel values at once.

    ``depth`` is passed as an int, exactly as info_dict.py stores it - that
    matters, because a float depth would promote the product to float64 and
    silently hide the wraparound this test exists to catch.
    """
    from app.domains.engraving.vendored import engraver

    # every pixel value 0..255 exactly once
    img = np.arange(256, dtype=np.uint8).reshape(16, 16)
    mesh = engraver.img2Mesh(img, depth=2, xwidth=10, ywidth=10, yz_swap=True)

    got = sorted({round(v.y, 9) for v in mesh.vertexList})
    want = sorted({round(val * 2 / 255.0, 9) for val in range(256)})
    assert got == want, (
        "pixel -> height mapping is not linear over 0..255 "
        "(255 maps to {}, expected {})".format(max(got), DEPTH_MM)
    )


@pytest.mark.parametrize("role,template", [("mold", MOLD_TEMPLATE), ("prod", PROD_TEMPLATE)])
def test_relief_lands_flush_with_the_template_surface(engrave_result, role, template):
    """The relief must touch the surface it is grafted into, and span full depth.

    ``importImg`` wraps the image in a 10px BORDER_COLOR=255 ring precisely so
    the relief's perimeter maps to the full configured depth and sits flush
    with the template. The uint8 wraparound made that ring 0.996mm instead of
    2.0mm, leaving the relief floating 1.004mm clear of the template.
    """
    path = getattr(engrave_result, "{}_path".format(role))
    depth = _relief_depths(path, template, role)

    assert depth.min() < 1e-4, (
        "{}: relief never reaches the template surface - closest approach is "
        "{:.6f}mm short".format(role, depth.min())
    )
    assert abs(depth.max() - DEPTH_MM) < 1e-3, (
        "{}: relief spans only {:.6f}mm, expected {}mm".format(
            role, depth.max(), DEPTH_MM
        )
    )


def _signed_volume(triangles):
    """Enclosed volume via the divergence theorem; sign follows the winding."""
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def _winding_normals(triangles):
    n = np.cross(
        triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    )
    return n / np.maximum(np.linalg.norm(n, axis=1), 1e-30)[:, None]


@pytest.mark.parametrize("role", ["mold", "prod"])
def test_output_is_a_closed_manifold_solid(engrave_result, role):
    """The graft must leave a single closed shell, not two loose ones.

    remove_triangles punches a hole in the template and img2Mesh makes an open
    sheet to fill it; Mesh.__add__ only concatenates triangle lists. Without
    refan_border the result was two entirely disconnected shells with 1868
    boundary edges, which PrusaSlicer rejects as an open mesh.
    """
    _, tris = _read_stl(getattr(engrave_result, "{}_path".format(role)))
    topo = _topology(tris)

    assert topo["boundary_edges"] == 0, "{}: {} edges bound only one face".format(
        role, topo["boundary_edges"]
    )
    assert topo["nonmanifold_edges"] == 0, "{}: {} edges shared by >2 faces".format(
        role, topo["nonmanifold_edges"]
    )
    assert topo["shells"] == 1, (
        "{}: {} disconnected shells - the relief is not welded to the "
        "template".format(role, topo["shells"])
    )
    assert topo["watertight"]


@pytest.mark.parametrize("role", ["mold", "prod"])
def test_stored_normals_are_unit_and_follow_the_winding(engrave_result, role):
    """Stored normals must be unit vectors agreeing with vertex order.

    Two separate defects used to break this: Mesh.translate added the
    translation vector to every normal (magnitudes up to 68 on the product,
    whose rot_array is None so nothing recomputed them), and both img2Mesh and
    update_normal returned the negation of the winding normal.
    """
    _, tris = _read_stl(getattr(engrave_result, "{}_path".format(role)))
    stored, _ = _read_stl(getattr(engrave_result, "{}_path".format(role)))
    lengths = np.linalg.norm(stored, axis=1)

    assert np.all(np.abs(lengths - 1.0) < 1e-3), (
        "{}: {} of {} stored normals are not unit vectors (max |n| = "
        "{:.3f})".format(
            role, int((np.abs(lengths - 1.0) >= 1e-3).sum()), len(lengths),
            lengths.max(),
        )
    )

    agreement = (_winding_normals(tris) * stored).sum(axis=1)
    inverted = int((agreement < 0).sum())
    assert inverted == 0, "{}: {} of {} normals point opposite their winding".format(
        role, inverted, len(agreement)
    )


def test_relief_displaces_equal_and_opposite_volume(engrave_result):
    """The mold's relief must ADD exactly what the product's relief REMOVES.

    Both come from the same image at the same depth over the same 95x95 area,
    so both are the integral of (depth - height) over that square - the mold
    filling its recess up to the engraved face, the product cutting into its
    top face. This is the assertion that catches an inside-out relief: the
    mold used to enclose 64201mm3 against a 35263mm3 template, i.e. the relief
    "added" 28939mm3 when 18050mm3 is the geometric maximum.
    """
    volumes = {}
    for role, template in (("mold", MOLD_TEMPLATE), ("prod", PROD_TEMPLATE)):
        _, tris = _read_stl(getattr(engrave_result, "{}_path".format(role)))
        _, template_tris = _read_stl(os.path.join(TEMPLATE_DIR, template))
        volumes[role] = _signed_volume(tris) - _signed_volume(template_tris)

    # 95x95 square, 2mm deep -> the relief can move at most 18050mm3.
    max_displacement = 95.0 * 95.0 * DEPTH_MM
    assert 0 < volumes["mold"] < max_displacement, (
        "mold relief displaces {:.2f}mm3, outside (0, {:.0f})".format(
            volumes["mold"], max_displacement
        )
    )
    assert -max_displacement < volumes["prod"] < 0, (
        "product relief displaces {:.2f}mm3, outside (-{:.0f}, 0)".format(
            volumes["prod"], max_displacement
        )
    )
    assert abs(volumes["mold"] + volumes["prod"]) < 0.1, (
        "mold adds {:.2f}mm3 but product removes {:.2f}mm3 - these must "
        "match".format(volumes["mold"], -volumes["prod"])
    )
