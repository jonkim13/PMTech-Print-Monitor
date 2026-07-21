# PROVENANCE — vendored ImageEngraver

This package was vendored on **2026-07-21** from a client-provided prototype
(`~/Downloads/ImageEngraver/`, delivered as `engraver.py` + `info_dict.py` +
`inputSTL/`). The prototype is proven working: three structural smoke tests
(quotes 1001 / 1002 / 1003, documented in the prototype's `RESULTS.md`) produced
valid binary STL output, with the logo run (quote1001, `PM_Technologies_Vert.png`,
`Coaster_100mm_Square`) yielding golden triangle counts of **435,130** (mold) and
**435,114** (product). This vendoring is a library-ization, not a rewrite: the
mesh/image algorithms are unchanged. The Downloads folder remains the untouched
reference copy of the client's original delivery.

## Modifications made during vendoring

All changes are mechanical (import safety, path handling, exception surface, dead
GUI removal). No function/class was renamed, no formatting reflowed, no algorithm
altered.

- **`engraver.py:10`** — `import info_dict as ind` → `from . import info_dict as ind`
  (package-relative import).
- **`engraver.py` (top)** — removed the `HEADLESS = True` module flag; added
  `from pathlib import Path` and `_TEMPLATE_DIR = Path(__file__).resolve().parent /
  "templates"` so template STLs resolve relative to this package, never the CWD.
- **`engraver.py` `importImg`** — permanently removed the only live GUI code, the
  `if not HEADLESS: cv2.imshow('new', gray); cv2.waitKey(0)` pair. The library is
  headless by construction; the preview code's job is done. (Every other
  imshow/waitKey in the original was already commented out.)
- **`engraver.py` `create_models`** — the two `open_stl_binary(...)` template loads
  now resolve their path via `_TEMPLATE_DIR / location` instead of the bare
  `location` string.
- **`engraver.py` (bottom)** — removed the entire `if __name__ == '__main__':`
  block (the CLI harness). This eliminates the hardcoded `quoteNum`, `imgFN`, and
  the `./images/` / `./output/` CWD-relative paths. The trailing `#MOD00xx` history
  comments were kept.
- **`info_dict.py`** — every template `location` had its `./inputSTL/` prefix
  stripped, leaving a bare filename resolved against `_TEMPLATE_DIR` (6 active
  entries; 2 more inside a commented-out block were also stripped, inert).
- **New file `api.py`** — the sole public entry point, `generate_models(image_path,
  output_dir, product_key='Coaster_100mm_Square', invert=False) -> EngraveResult`.
  It validates inputs and raises `ValueError` (unknown product), `FileNotFoundError`
  (missing image or template), or `RuntimeError` (mesh/STL failure) instead of the
  prototype's print-and-continue behavior. Output filenames derive from the image
  stem: `mold_{stem}.stl` / `prod_{stem}.stl`, with `_inverted` appended to each
  stem when `invert=True`.
- **New `__init__.py` files** — make `engraving` and `engraving.vendored` proper
  packages; the vendored `__init__` re-exports the public API.
- **New file `render.py`** (added 2026-07-21, not part of the client delivery) —
  headless matplotlib Agg PNG preview rendering (`render_preview`,
  `render_product_previews`, `RenderError`). Derived from the client's disposable
  preview script `~/Downloads/ImageEngraver/render_previews.py` (trimesh decimation
  + `Poly3DCollection` shade-by-normal, Y-up→Z-up rotation, axis-aligned top view).
  Re-exported lazily from `__init__` (PEP 562 `__getattr__`) so importing the
  package never pulls in matplotlib/trimesh.

## Retained dead code

`img2Mesh2` (the Delaunay/`circumcircle` experiment) is **not** on the
`create_models` execution path and is retained verbatim as dead code. It references
`quotePath` / `moldName`, module globals that only existed in the now-removed
`__main__` block; because the function is never called, this is harmless (names
resolve at call time). Left in place deliberately rather than deleted.

## Templates shipped

Only the 6 STLs referenced by the three usable product configs were copied into
`templates/`. `Lithopane.STL` from the prototype's `inputSTL/` is unreferenced and
was excluded.

## Tested surface

Only **`Coaster_100mm_Square`** is exercised by the smoke tests and by the repo test
suite. `Ice_Cube` and `Silicone_Sample` ship in `info_dict.py` with their templates
and are complete configs, but are **untested** here — carried forward from the
prototype as-is.
