# Engraving mesh integrity — diagnosis and fix

**Date:** 2026-07-23
**Scope:** `app/domains/engraving/vendored/` (engraver.py)
**Trigger:** PrusaSlicer reported an "open mesh" error severe enough to block
printing. trimesh had reported `watertight=False` since the library was first
vendored, but it had never been chased down.

Package-level changelog lives in
[`PROVENANCE.md`](../../app/domains/engraving/vendored/PROVENANCE.md) (MOD0008–MOD0011).
This note is the investigation record: how the defects were found, the evidence,
and what was ruled out.

---

## Summary

Four independent defects, all in the graft between the template STL and the
image-derived relief. Three were latent from day one; one was a silent
regression introduced by the numpy 2.x upgrade.

| # | Defect | Symptom |
|---|---|---|
| MOD0008 | uint8 overflow in the pixel→height term | relief 1.004 mm short of the template; engraving ~1 mm deep instead of 2 mm, mid-tones folded |
| MOD0009 | graft seam never stitched | 1,868 boundary edges, two disconnected shells → "open mesh" |
| MOD0010 | `Mesh.translate` corrupted normals | 434,312 non-unit normals on the product, magnitudes to 68 |
| MOD0011 | cross products inverted throughout | mold geometrically inside-out |

Golden triangle counts moved **435,130 / 435,114 → 436,990 / 436,974**.

---

## How the mesh was open

The templates are fine — both are watertight with euler number 2 and zero
boundary edges. The damage is entirely in the graft:

```
                        mold        product
faces                   435,130     435,114
watertight              False       False
boundary edges          1,868       1,868
face-connected shells   2           2          <- 818 + 434,312
degenerate faces        0           0
non-manifold edges      0           0
```

The 1,868 boundary edges resolve into exactly **two closed loops** per file
(every boundary vertex has degree 2 — no branching):

| loop | verts | what it is |
|---|---|---|
| 0 | 1,864 | the relief sheet's perimeter, on a plane at y = ∓3.996078 |
| 1 | 4 | the hole `remove_triangles` punched, on the template surface at y = ∓5.0 |

1,864 = 2·(467+467) − 4, the perimeter of the 467×467 heightfield grid.
Both loops sit at |x|,|z| = 47.5 — the relief square. The parts' outer
perimeters are closed, so this is a graft defect, not a template defect.

All 1,864 relief boundary vertices sit *directly* above/below the hole's edges —
laterally perfect, vertically off by a constant **1.003922 mm**. The opening is
a 95×95 mm band, 380 mm of perimeter × 1.004 mm tall, right around the
engraving.

**Ruled out: float precision.** Re-welding at successively coarser tolerance
changed nothing (`digits=4/3/2` → still 1,868 boundary edges). The gap is four
orders of magnitude beyond any weld tolerance. This was never a
near-coincident-vertex problem — the bridging geometry was genuinely absent.

### Why

Three code facts, together:

- `remove_triangles` deletes the 2 triangles of the placeholder square, leaving
  a 4-vertex, 4-edge loop. Each hole edge is 95 mm and bounds one wall face.
- `img2Mesh` builds a **bare heightfield sheet** — no skirt, no walls, no cap.
  An open disk with a 1,864-edge boundary, by construction.
- `Mesh.__add__` is `self.triangles += other.triangles`. Pure list
  concatenation. No adjacency, no welding, no stitching.

## Why the 1.004 mm gap (the numpy 2.x regression)

`img2Mesh` computed vertex height as `((i[y, x]*depth)/255)` where `i` is a
**uint8** image and `depth` is an **int**. Under numpy < 2 this promoted to
int64. Under numpy ≥ 2 (NEP 50) it stays uint8 and wraps:

```
px= 127  ->  0.996078   correct 0.996078
px= 128  ->  0.000000   correct 1.003922   OVERFLOW
px= 255  ->  0.996078   correct 2.000000   OVERFLOW
```

`importImg` wraps the image in a 10 px ring of `BORDER_COLOR = 255` precisely so
the relief's perimeter maps to full depth and lands flush with the template.
That ring evaluated to 0.996078 mm instead of 2.0 mm — and 5 − 3.996078 =
**1.003922**, the measured gap exactly.

The second consequence is worse than the hole: **every pixel ≥128 wrapped**, so
the engraving rendered at ~1 mm with a discontinuity at mid-grey. It looked
plausible in previews only because the fixture is a near-binary logo.

The repo pins `numpy==2.5.1` and the Pi runs the same, so dev and Pi produced
identical wrong geometry — there was no divergence to reconcile. Triangle counts
are unaffected by the bug, which is why the golden-count tests never noticed.

> **Gotcha:** the overflow only fires when `depth` is an `int`. `info_dict.py`
> stores `"depth":2`. A test that passes `2.0` promotes to float64 and passes
> against the bug. The regression test deliberately passes an int.

## Why the mold was inside-out

Fixing the overflow closed the gap but not the mesh — the seam became a
zero-width T-junction (relief corners welded to hole corners, 1,864 edges still
boundary). Re-fanning the four hole-adjacent faces closed it. That made the mold
watertight, which in turn made a *fourth* defect measurable for the first time:

```
mold template alone      35,262.98 mm3
mold as generated        64,201.76 mm3   -> relief "added" 28,938.78
```

A 95×95 mm relief 2 mm deep can add at most **18,050 mm³**. The value is
impossible. Reversing the relief's winding gives **36,407.51 mm³**, i.e. the
relief adds **1,144.53 mm³** — matching to within 0.02 mm³ the **1,144.55 mm³**
the product's relief *removes*. Both are the same integral of (depth − height)
over the same square, so they must be equal and opposite. That identity is now a
test.

Root cause: both `crossProd` in `img2Mesh` and `Triangle.update_normal` returned
the **negation** of the winding normal — provable, since `A × (A−B) == −(A × B)`
— and `flip_normal` negated the stored normal without touching vertex order.
Slicers read winding, not the stored normal. So `flip_norms: True` made the
mold's normals *look* correct while the geometry stayed inverted.

Visible in the previews: the mold's recess floor rendered as a near-black plate
(lit as if facing away). It now renders correctly lit with the mirrored logo
clearly raised.

---

## Verification

Both outputs, after the fix:

```
watertight          True        boundary edges       0
shells              1           non-manifold edges   0
winding consistent  True        degenerate faces     0
normals             436,990 / 436,974 unit, all agreeing with winding
relief depth        0.000000 .. 2.000000 mm (was 1.003922 .. 2.000000)
volume balance      mold +1,144.53 / prod -1,144.55  (|sum| = 0.02)
```

Every one of those invariants fails against pre-fix output and passes after.
They are pinned in `tests/test_engraving_vendored.py`; triangle counts are
demoted to drift detection.

## Notes for whoever touches this next

- **Topology checks are written in plain numpy on purpose.** `trimesh` (4.12.2)
  is a declared dependency, but its graph backends are not installed — no
  `networkx`, no `scipy` — so `trimesh.repair.fill_holes` and
  `trimesh.graph.connected_components` raise `ImportError`. Loading,
  `edges_sorted`, `grouping.group_rows`, `is_watertight`,
  `is_winding_consistent` and volume all work fine.
- **`100mmSquare_Mold_RevA.STL` has 76 near-degenerate edges** of its own, in
  the outer chamfer, where vertex pairs disagree by ~1e-14 mm from CAD export.
  Welding on exact float32 reports them as boundary edges on every mold. Every
  real tool welds with tolerance, so this is harmless — but it is why the test
  helper welds at 1e-4 mm rather than exactly. Not introduced by these changes;
  present in the raw template.
- **`refan_border` raises rather than silently emitting an open mesh** if a
  template's hole and its relief do not line up. Only `Coaster_100mm_Square` is
  exercised; `Ice_Cube` and `Silicone_Sample` ship untested and would need
  checking before use.
- **All output generated before 2026-07-23 is superseded** — ~1 mm aliased
  depth, open seam, and for molds an inverted relief. Regenerate rather than
  reuse.
