"""Vendored ImageEngraver library — API contract + golden-output tests.

Covers the public surface of ``app.domains.engraving.vendored``:

* importing the package/``api`` module has zero side effects (no files
  written, importable with no image present);
* ``generate_models`` raises ``ValueError`` for an unknown product and
  ``FileNotFoundError`` for a missing image;
* a full ``Coaster_100mm_Square`` generation reproduces the smoke-test
  golden triangle counts (mold 435,130 / product 435,114) and writes
  well-formed binary STL (size == 84 + 50 * triangles).

The full-generation test is unmarked (the suite has no ``slow`` marker
convention); it runs in ~5 s on the repo venv.
"""

import os
import struct
import subprocess
import sys

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

FIXTURE = os.path.join(
    ROOT_DIR, "tests", "fixtures", "engraving", "PM_Technologies_Vert.png"
)

# Golden triangle counts from the prototype's quote1001 smoke test.
GOLDEN = {"mold": 435130, "prod": 435114}


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
