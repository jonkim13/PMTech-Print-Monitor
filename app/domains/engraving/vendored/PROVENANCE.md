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

> **Superseded as of 2026-07-23.** The mesh-integrity fixes below are the first
> changes to the algorithms themselves, and they move the golden counts to
> **436,990** / **436,974**. See "Post-vendoring fixes" for what changed and why
> the older output should not be reused.

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

## Post-vendoring fixes — 2026-07-23 (mesh integrity)

The vendored output was never watertight, and PrusaSlicer escalated this to an
"open mesh" error that blocked printing. Diagnosis found three independent
defects. These are the **first changes to the vendored algorithms** — everything
above this section is mechanical. Golden triangle counts moved from
**435,130 / 435,114** to **436,990 / 436,974** as a result (see MOD0009).

- **MOD0008 — `img2Mesh`: uint8 wraparound (a numpy 2.x regression).**
  `((i[y, x]*depth)/255)` multiplied a `uint8` pixel by an `int` depth. Under
  numpy < 2 this promoted to `int64`; under numpy >= 2 (NEP 50, and this repo
  pins `numpy==2.5.1`) it stays `uint8` and wraps, so every pixel >= 128 folded
  back onto the bottom of the range — `255*2 -> 254`. The `BORDER_COLOR=255`
  ring that exists to make the relief's perimeter sit flush with the template
  mapped to 0.996mm instead of 2.0mm, so the relief floated **1.003922mm** clear
  of the template and the engraving rendered at ~1mm with a discontinuity at
  mid-grey. Fixed by widening the image once (`i = i.astype(np.int32)`) at the
  top of `img2Mesh`. Triangle counts unaffected — which is why the golden-count
  tests never noticed. Dev and Pi were both on numpy 2.5.1, so both produced the
  same wrong geometry; there was no dev/Pi divergence to reconcile.

- **MOD0009 — `refan_border`: welding the relief into the template.**
  `remove_triangles` punches a hole bounded by four long edges; `img2Mesh`
  builds a bare heightfield sheet (no skirt, no cap) whose border is subdivided
  into 466 segments per side; `Mesh.__add__` only concatenates triangle lists.
  The result was **two entirely disconnected shells** with **1,868 boundary
  edges**. Even with MOD0008 closing the 1mm gap, the seam remained a T-junction.
  New `refan_border` replaces the single template face carrying each hole edge
  with a fan of coplanar triangles, one per relief segment, so every edge is
  shared by exactly two faces. `Mesh` gained a `border_sides` attribute
  (populated by `img2Mesh`) to carry the sheet's four edges in order. Adds
  `4 * 465 = 1,860` triangles per model. Matching is on coordinates rounded to
  `_WELD_ROUNDING = 4` decimals (1e-4mm) — far above float32 representation
  error at these coordinates (~4e-6mm) and far below the 0.2mm relief vertex
  spacing. It raises rather than silently emitting an open mesh if a template's
  hole and its relief do not line up; only `Coaster_100mm_Square` is exercised.

- **MOD0010 — `Mesh.translate`: normal corruption.**
  The method added the translation vector to every `normalVector` and
  renormalised against it. Translation cannot change a normal. The mold escaped
  because its `rot_array` is set, so `rotate()` -> `update_normal()` recomputed
  normals afterwards; the product's `rot_array` is `None`, so `rotate()` is a
  no-op and the corrupt normals reached the file — 434,312 of 436,974 non-unit,
  magnitudes up to 68. The offending loop was deleted.

- **MOD0011 — inverted winding on the mold.**
  Both `crossProd` in `img2Mesh` and `Triangle.update_normal` returned the
  *negation* of the winding normal (provable: `A x (A-B) == -(A x B)`), and
  `flip_normal` negated only the stored normal, never the vertex order that
  slicers actually read. Net effect: `flip_norms: True` made the mold's stored
  normals look right while its relief stayed geometrically **inside-out** — the
  mold enclosed 64,201mm3 against a 35,263mm3 template, where a 95x95x2mm relief
  can only add 0..18,050mm3. Fixed by ordering both cross products to yield the
  true winding normal and making `flip_normal` reverse the winding as well. The
  mold now encloses 36,407.51mm3: its relief **adds** 1,144.53mm3, exactly
  matching the 1,144.55mm3 the product's relief **removes** (both are the same
  integral of `depth - height` over the same 95x95mm square). This defect
  predates the vendoring; it was undetectable while the mesh was open.

Verified on `PM_Technologies_Vert.png` / `Coaster_100mm_Square`: both outputs are
watertight, 0 boundary edges, 0 non-manifold edges, 0 degenerate faces, a single
connected shell, consistent winding, and all normals unit and agreeing with that
winding. `tests/test_engraving_vendored.py` now pins those invariants directly;
triangle counts are demoted to drift detection.

### Superseded output

Any STL generated before 2026-07-23 is superseded and should be regenerated:
the historical smoke-test output (quotes 1001 / 1002 / 1003, and the prototype's
`RESULTS.md` counts) carries the MOD0008 aliasing — roughly 1mm of engraving
depth instead of the configured 2mm, with mid-tones folded — plus the open seam,
and for molds the inverted relief. The **triangle counts** quoted in `RESULTS.md`
remain the correct historical record of what the prototype produced; they are no
longer the expected output of this library.

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
