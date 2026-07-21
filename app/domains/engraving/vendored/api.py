"""Public API for the vendored ImageEngraver library.

Exposes exactly one function, :func:`generate_models`, wrapping the
proven-but-CWD-bound prototype (``engraver.py``) into a headless,
path-independent, exception-raising library. See ``PROVENANCE.md`` for
the full vendoring history and the smoke tests this preserves.

Importing this module has no side effects: no GUI, no file I/O, no
config evaluation.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from . import engraver
from . import info_dict as ind


class EngravingError(Exception):
    """Base class for errors raised by the vendored engraving library."""


@dataclass
class EngraveResult:
    """Outcome of a single :func:`generate_models` run."""

    mold_path: str
    prod_path: str
    triangle_counts: dict  # {"mold": int, "prod": int}
    duration_seconds: float


def generate_models(
    image_path,
    output_dir,
    product_key: str = "Coaster_100mm_Square",
    invert: bool = False,
) -> EngraveResult:
    """Generate the mold and product STLs for ``image_path``.

    Paths may be absolute or relative; nothing here depends on the
    current working directory. Template STLs are resolved inside the
    vendored package (``templates/``), not from ``./inputSTL/``.

    Output filenames derive from the image filename stem:
    ``mold_{stem}.stl`` and ``prod_{stem}.stl`` (with ``_inverted``
    appended to each stem when ``invert=True``, e.g.
    ``mold_{stem}_inverted.stl``).

    Raises:
        ValueError: ``product_key`` is not a known product.
        FileNotFoundError: the image or a required template STL is missing.
        RuntimeError: mesh generation or STL writing failed.
    """
    if product_key not in ind.info:
        raise ValueError(
            "unknown product_key {!r}; available: {}".format(
                product_key, sorted(ind.info)
            )
        )

    image_path = Path(image_path)
    if not image_path.is_file():
        raise FileNotFoundError("image not found: {}".format(image_path))

    # Fail early and clearly if a template STL is missing, rather than
    # letting the raw open() deep inside create_models surface it.
    for role in ("Mold", "Product"):
        template = engraver._TEMPLATE_DIR / ind.info[product_key][role]["location"]
        if not template.is_file():
            raise FileNotFoundError(
                "template STL not found for {} {}: {}".format(
                    product_key, role, template
                )
            )

    stem = image_path.stem
    name_suffix = "_inverted" if invert else ""
    mold_name = "mold_{}{}.stl".format(stem, name_suffix)
    prod_name = "prod_{}{}.stl".format(stem, name_suffix)

    output_dir = Path(output_dir)

    start = time.perf_counter()
    try:
        mold_mesh, prod_mesh = engraver.create_models(
            str(image_path), product_key, invert=invert
        )
    except Exception as exc:  # noqa: BLE001 - wrap with context per API contract
        raise RuntimeError(
            "mesh generation failed for {} (product_key={!r}): {}".format(
                image_path, product_key, exc
            )
        ) from exc

    # save_stl builds its path as ``p + n`` and makedirs(p), so p needs a
    # trailing separator.
    save_prefix = os.path.join(str(output_dir), "")
    try:
        engraver.save_stl(mold_mesh, save_prefix, mold_name)
        engraver.save_stl(prod_mesh, save_prefix, prod_name)
    except Exception as exc:  # noqa: BLE001 - wrap with context per API contract
        raise RuntimeError(
            "STL writing failed for {} (product_key={!r}): {}".format(
                image_path, product_key, exc
            )
        ) from exc

    duration = time.perf_counter() - start

    return EngraveResult(
        mold_path=str(output_dir / mold_name),
        prod_path=str(output_dir / prod_name),
        triangle_counts={"mold": len(mold_mesh), "prod": len(prod_mesh)},
        duration_seconds=duration,
    )
