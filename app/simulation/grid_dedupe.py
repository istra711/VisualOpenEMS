"""Grid-line dedupe + FDTD-domain helper for the generated run script.

Kept as a real module (instead of a function inside ``SCRIPT_TEMPLATE``) so the
exact code that runs during a simulation is unit-testable
(``tests/test_dedupe_grid_lines.py``). The generated ``project_run.py`` imports
it like the other ``app.*`` modules (it already puts ``APP_ROOT`` on
``sys.path``).
"""

from __future__ import annotations

import numpy as np

from app.csxcad_builder.quantize import ulp_tolerance


def get_domain_box(CSX):
    """Return (lo, hi) of the FDTD domain as floats, or None if no mesh lines yet."""
    from app.meshing.preview import get_sim_area

    area = get_sim_area(CSX)
    if area is None:
        return None
    return [float(v) for v in area[0]], [float(v) for v in area[1]]


def dedupe_grid_lines(
    CSX, tol: float = 1e-12, frac: float = 0.1, *, ulp_align: bool = True
) -> None:
    """Remove duplicate / near-duplicate grid lines that create tiny FDTD cells.

    ``tol`` catches exact duplicates: port ``edges2grid`` inserts the arm-axis
    line (x=0 / y=0) which collides with the mesh line already on that
    coordinate, leaving a zero-width cell that collapses the Courant timestep.

    ``frac`` additionally merges lines that sit closer than ``frac`` times the
    following cell: easyMesh's own edge refinement can place a line a few
    microns next to a geometry/manual line (e.g. a float32-rounded metal edge
    next to the exact manual line), which again collapses the timestep.

    ``ulp_align`` (default True) raises ``tol`` to the float32 resolution at the
    largest coordinate of the axis (8 ULP, see ``quantize.ulp_tolerance``): two
    lines that differ only by float32 arithmetic then merge even where the
    following cell is *smaller* than that noise. It never merges less than
    before, and is a no-op wherever ``frac * cell`` already dominates (all
    measured projects). ``False`` restores the strict legacy behaviour
    (``MeshSpec.align_coordinates``).

    Domain-boundary lines are never touched — they define the FDTD box edges.
    """
    grid = CSX.GetGrid()
    dom = get_domain_box(CSX)
    removed_total = 0
    for axis in range(3):
        raw = grid.GetLines(axis)
        if raw is None:
            continue
        vals = np.sort(np.array(raw, dtype=float))
        if vals.size < 2:
            continue
        axis_tol = max(tol, ulp_tolerance(vals)) if ulp_align else tol
        keep: list[float] = []
        removed = 0
        # domain edges are structural: never drop them
        dom_lo = dom[0][axis] if dom is not None else None
        dom_hi = dom[1][axis] if dom is not None else None
        for i, v in enumerate(vals):
            # The outermost lines define the FDTD box: never drop them. (The
            # old code protected a *tolerance band* around the domain edges,
            # which with the ULP floor would keep a whole sub-resolution cluster
            # next to the edge alive - the cell that collapses the timestep.)
            if i == 0 or i == vals.size - 1:
                keep.append(float(v))
                continue
            if dom_lo is not None and (abs(v - dom_lo) < tol or abs(v - dom_hi) < tol):
                keep.append(float(v))
                continue
            if keep:
                gap = abs(float(v) - keep[-1])
                nxt = float(vals[i + 1]) - float(v) if i + 1 < vals.size else gap
                if gap < max(axis_tol, frac * max(nxt, 0.0)):
                    removed += 1
                    continue
            keep.append(float(v))
        if len(keep) < 2:
            continue
        if removed:
            grid.SetLines(axis, np.array(keep, dtype=float))
            removed_total += removed
            print(
                f"  dedupe axis {axis}: removed {removed} zero-size cell(s), "
                f"{len(keep)} lines left"
            )
    if removed_total == 0:
        print("  dedupe: no zero-size cells found")
