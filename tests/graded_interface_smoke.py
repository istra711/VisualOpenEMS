"""Smoke: graded mesh refinement at metal interfaces (cartesian).

Checks app/meshing/interface_refine.py end-to-end on a small CSX with
  - a copper sheet on a dielectric substrate (metal<->dielectric), and
  - a copper box standing in air (metal<->air),
verifying that with the option ON
  * the cells adjacent to a metal face get fine and grow geometrically
    (ratio <= max_grading_ratio),
  * metal-in-air is refined too (the easyMesh-internal pass missed it),
  * nothing is inserted inside a solid metal,
  * the max cell size target is still met on every axis,
and that the pure helpers behave (ramp ratios, exact fit, no duplicates).

Run: python -u tests/graded_interface_smoke.py
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402


def _build_csx(flag: bool):
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS

    from app.meshing.easy_wrapper import (
        apply_boundary,
        enhance_csx,
        enhance_fdtd,
        generate_mesh,
    )
    from app.meshing.preview import mesh_lines_from_csx
    from app.meshing.rules import compute_targets
    from app.models.project import Project

    p = Project.new_default("graded_interface_smoke")
    p.mesh.add_graded_mesh_lines_at_material_transitions = flag
    p.mesh.start_frequency = 2.2e9
    p.mesh.stop_frequency = 2.7e9
    p.mesh.target_frequency = 2.45e9
    p.mesh.preset = "medium"

    FDTD = openEMS(NrTS=10, EndCriteria=1e-3)
    FDTD.SetGaussExcite(2.45e9, 0.5e9)
    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)
    setup: dict = {}
    CSX = enhance_csx(CSX, setup)
    FDTD = enhance_fdtd(FDTD, setup)
    targets = compute_targets(p.mesh, eps_max=1.0)
    apply_boundary(FDTD, p.boundary, p.mesh, targets)
    CSX.GetGrid().SetDeltaUnit(p.mesh.unit_to_meters())

    # dielectric substrate z 0..1.52
    sub = CSX.AddMaterial("substrate", epsilon=3.38)
    sub.AddBox(start=[-25.0, -25.0, 0.0], stop=[25.0, 25.0, 1.52], priority=5)
    # copper sheet on top of the substrate (zero thickness) @ z=1.52
    patch = CSX.AddMetal("copper")
    patch.AddBox(start=[-15.0, -15.0, 1.52], stop=[15.0, 15.0, 1.52], priority=20)
    # solid copper box in AIR (metal<->air, no dielectric primitive around)
    airbox = CSX.AddMetal("copper")
    airbox.AddBox(start=[-40.0, -40.0, 20.0], stop=[-20.0, -20.0, 20.5], priority=20)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        generate_mesh(CSX, p.mesh, p.boundary, targets, primitives_mesh_setup=setup)

    return CSX, mesh_lines_from_csx(CSX), p, targets


def _cells_around(lines, pos, n=4):
    """Cells (diffs) immediately left and right of `pos`."""
    arr = np.sort(np.asarray(lines, dtype=float))
    left = arr[arr < pos - 1e-12][-n:]
    right = arr[arr > pos + 1e-12][:n]
    return left, right


def _test_helpers() -> None:
    from app.meshing.interface_refine import graded_fill, interface_refine_lines

    # exact fit + ratios <= growth
    fill = graded_fill(0.0, 10.0, 0.5, 2.0)
    seq = [0.0] + fill + [10.0]
    ratios = [seq[i + 1] - seq[i] for i in range(len(seq) - 1)]
    assert all(r > 0 for r in ratios), ratios
    for a, b in zip(ratios, ratios[1:]):
        assert b / a <= 2.0 + 1e-9, (ratios,)
    assert fill and fill[0] > 0.0

    # descending direction works too
    fill = graded_fill(10.0, 0.0, 0.5, 2.0)
    assert all(0.0 < v < 10.0 for v in fill), fill

    # ramp: needs a coarse-enough local cell (span) vs the neighbouring cell
    lines = [-50.0, -30.0, 0.0, 60.0, 100.0]
    new = interface_refine_lines(lines, [(0.0, +1)], fine=None, growth=2.0)
    assert new, "expected ramp lines"
    assert new == sorted(new)
    for v in new:
        assert all(abs(v - l) > 1e-9 for l in lines), (v, lines)
    assert max(new) < 60.0
    # the FIRST inserted cell (caps!) must be >= neighbouring cell / growth
    seq = [0.0] + [v for v in new] + [60.0]
    first_cell = seq[1] - seq[0]
    assert first_cell >= 30.0 / 2.0 - 1e-9, first_cell
    # already smooth transition (ratio <= growth) -> nothing to repair
    smooth = [-50.0, -30.0, 0.0, 20.0, 45.0]
    assert interface_refine_lines(smooth, [(0.0, +1)], fine=None, growth=2.0) == []

    # --- medium (eps) jumps: detected on BOTH sides + ramped ----------------
    from app.meshing.interface_refine import material_jump_sides
    from app.meshing.preview import PrimitiveInfo

    infos = [
        PrimitiveInfo("d#0", "Material", (-15.0, -15.0, 5.0), (15.0, 15.0, 8.0), 5, epsilon=3.38),
        PrimitiveInfo("m#0", "Metal", (-5.0, -5.0, -0.5), (5.0, 5.0, 0.0), 10),
    ]
    assert material_jump_sides(infos, 2) == [
        (5.0, -1), (5.0, 1), (8.0, 1), (8.0, -1)
    ], material_jump_sides(infos, 2)
    assert material_jump_sides(infos, 0) == [
        (-15.0, -1), (-15.0, 1), (15.0, 1), (15.0, -1)
    ], material_jump_sides(infos, 0)
    assert material_jump_sides(infos, 1) == [
        (-15.0, -1), (-15.0, 1), (15.0, 1), (15.0, -1)
    ], material_jump_sides(infos, 1)

    lines = [0.0, 0.25, 0.4, 3.0, 9.0]
    new = interface_refine_lines(lines, [(0.4, +1)], fine=None, growth=2.0, continuity=True)
    assert new, "eps jump must be ramped"
    seq = [0.0, 0.25, 0.4] + sorted(new) + [3.0, 9.0]
    cells = [b - a for a, b in zip(seq, seq[1:])]
    cross = cells[2] / cells[1]  # first cell after the interface / cell before it
    assert cross <= 2.0 + 1e-6, (cross, cells)
    for a, b in zip(cells[2:5], cells[3:6]):
        assert b / a <= 2.0 + 1e-6, cells
    print(f"[helpers] OK (caps respected, smooth transitions untouched, eps ramp {cross:.2f}x)")


def _test_mesh() -> None:
    CSX_off, lines_off, project, targets = _build_csx(False)
    CSX_on, lines_on, _, _ = _build_csx(True)

    growth = float(project.mesh.max_grading_ratio)
    z_off = np.sort(lines_off["z"])
    z_on = np.sort(lines_on["z"])
    x_off = np.sort(lines_off["x"])
    x_on = np.sort(lines_on["x"])

    # --- the option adds lines, ends up finer --------------------------------
    assert z_on.size > z_off.size, (z_on.size, z_off.size)
    assert x_on.size > x_off.size, (x_on.size, x_off.size)
    print(f"[mesh] lines z: {z_off.size} -> {z_on.size}, x: {x_off.size} -> {x_on.size}")

    # --- metal sheet on substrate (z = 1.52) --------------------------------
    # The COARSE side (air above the metal) gets the graded ramp; the already
    # fine substrate side must not be made worse (the ramp never goes more
    # than one grading step below the smallest existing cell / the opposite
    # neighbour, so it cannot collapse the CFL timestep).
    iface = 1.52
    _, right_off = _cells_around(z_off, iface, 1)
    _, right_on = _cells_around(z_on, iface, 1)
    left_off = z_off[z_off < iface - 1e-12][-1:]
    left_on = z_on[z_on < iface - 1e-12][-1:]
    cell_below_off = iface - float(left_off[-1])
    cell_below_on = iface - float(left_on[-1])
    assert cell_below_on <= cell_below_off * 1.05, (cell_below_on, cell_below_off)
    assert float(right_on[0]) < float(right_off[0]) * 0.75, (right_on, right_off)
    print(f"[mesh] interface z=1.52 cells below {cell_below_off:.4g} -> {cell_below_on:.4g} "
          f"(not worse), air side {float(right_off[0]):.4g} -> {float(right_on[0]):.4g} (refined)")

    # grading: cells grow by <= max_grading_ratio when moving AWAY from the
    # metal, on the refined (coarse) side
    above = np.diff(z_on[z_on >= iface][:5])
    assert above.size >= 3, above
    for a, b in zip(above, above[1:]):
        assert b / a <= growth + 0.05, (growth, above)
    print(f"[mesh] grading above the metal sheet {above.round(4).tolist()}")

    # --- metal in AIR (x = -40 face): refined now (was missed before) --------
    band_lo, band_hi = -42.0, -40.0
    n_off = int(((x_off > band_lo) & (x_off < band_hi)).sum())
    n_on = int(((x_on > band_lo) & (x_on < band_hi)).sum())
    assert n_on > n_off, ("metal-in-air not refined", n_off, n_on)
    print(f"[mesh] air lines next to the x=-40 metal face: {n_off} -> {n_on}")

    # --- nothing inserted INSIDE the solid metal box -------------------------
    inside_lo, inside_hi = -39.9, -20.1
    i_off = int(((x_off > inside_lo) & (x_off < inside_hi)).sum())
    i_on = int(((x_on > inside_lo) & (x_on < inside_hi)).sum())
    assert i_on == i_off, ("lines inserted inside the metal", i_off, i_on)
    print(f"[mesh] metal interior lines unchanged ({i_on})")

    # --- max cell size target still met on every axis (fork bug regression) --
    for d in "xyz":
        sizes = np.diff(np.sort(lines_on[d]))
        assert sizes.size
        assert sizes.max() <= targets.max_cell_units * 1.0001, (
            d, sizes.max(), targets.max_cell_units,
        )
    print(f"[mesh] max cell <= {targets.max_cell_units:.4g} on x/y/z")

    # --- cylindrical is a no-op ---------------------------------------------
    from app.meshing.interface_refine import refine_at_metal_interfaces

    stats = refine_at_metal_interfaces(CSX_on, project, coord_system="cylindrical")
    assert stats == {}, stats
    print("[mesh] cylindrical no-op OK")


def main() -> int:
    _test_helpers()
    _test_mesh()
    print("GRADED INTERFACE SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
