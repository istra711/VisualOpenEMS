"""Mesh repair (V1/V2): merge runtime-only cells, keep protected lines.

The numbers in the docstrings are the measured ones from
``Filter_new_test1`` (24 um artefact, dt 0.0790 ps) and the dipole cylinder
(51 um arc band, dt 0.1186 ps) - see docs/DIAGNOSTICS_CHANGES_EN.md.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.meshing.critical_cells import CriticalCell, find_critical_cells, summary_line
from app.meshing.coord_diag import requested_min_cellsize
from app.meshing.repair import (
    apply_mesh_repairs,
    apply_repairs,
    grid_lines,
    min_cell,
    preview,
    protected_coordinates,
    repair_axis,
    repair_report,
)
from app.models.project import MeshSpec

# measured z lines of Filter_new_test1 around the 24 um cell
FILTER_Z = [1.6, 1.635, 1.670672, 1.718235, 1.742016, 1.884704, 2.3]
FILTER_LO = 1.718235
FILTER_HI = 1.742016


# --------------------------------------------------------------------------- #
# fakes: the repair core only needs GetGrid().GetLines/SetLines
# --------------------------------------------------------------------------- #
class _Grid:
    def __init__(self, lines: dict[str, list[float]]) -> None:
        self._lines = {i: [float(v) for v in lines[ax]] for i, ax in enumerate("xyz")}

    def GetLines(self, i):  # noqa: N802 - CSXCAD API
        return list(self._lines[int(i)])

    def SetLines(self, i, values):  # noqa: N802 - CSXCAD API
        self._lines[int(i)] = [float(v) for v in values]


class _CSX:
    def __init__(self, lines: dict[str, list[float]]) -> None:
        self._grid = _Grid(lines)

    def GetGrid(self):  # noqa: N802 - CSXCAD API
        return self._grid


def _lines(z=None) -> dict[str, list[float]]:
    return {
        "x": [-2.0, -1.0, 0.0, 1.0, 2.0],
        "y": [-2.0, -1.0, 0.0, 1.0, 2.0],
        "z": list(z or FILTER_Z),
    }


def _targets(max_cell: float = 10.16):
    class _T:
        max_cell_units = max_cell

    return _T()


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def test_dip_merge_removes_the_inner_line():
    rep = repair_axis(FILTER_Z, "z", FILTER_LO, FILTER_HI)
    assert rep is not None and rep.changed
    assert rep.removed == [pytest.approx(FILTER_LO)]
    assert rep.min_before == pytest.approx(0.023781, rel=1e-3)
    # merged into the smaller neighbour: the 0.0476 cell becomes 0.0714
    assert rep.min_after >= 0.023781 * 1.4
    # sorted, endpoints kept
    assert rep.after == sorted(rep.after)
    assert rep.after[0] == pytest.approx(FILTER_Z[0])
    assert rep.after[-1] == pytest.approx(FILTER_Z[-1])
    assert rep.cells_after == rep.cells_before - 1


def test_plateau_is_coarsened_to_the_next_neighbour():
    band = [-1.0 + 0.051282 * i for i in range(0, 21)]
    x = [-2.0, -1.5, -1.051282] + band + [0.1282, 0.5, 1.0, 1.5, 2.0]
    rep = repair_axis(x, "x", band[0], band[-1])
    assert rep is not None and rep.changed
    # the floor is the next larger neighbour (0.1026), not the band itself
    assert rep.floor == pytest.approx(0.10256, rel=1e-3)
    assert rep.min_after == pytest.approx(0.10256, rel=1e-3)
    assert rep.min_after > rep.min_before
    assert rep.cells_after < rep.cells_before


def test_uniform_band_is_not_repaired():
    """A uniformly fine band is a resolution decision, not an artefact."""
    y = [0.0, 0.5] + [0.5 + 0.0449 * i for i in range(1, 11)] + [1.0, 1.5]
    assert repair_axis(y, "y", 0.5, 0.5 + 10 * 0.0449, floor=0.0449) is None


def test_a_genuine_gap_is_not_touched_by_the_dip_rule():
    # one 0.1304 cell between two coarse cells (a hairpin feed gap)
    z = [0.0, 0.5, 1.0, 1.1304, 1.2608, 1.5, 2.0]
    assert repair_axis(z, "z", 1.1304, 1.2608) is None


def test_protected_line_is_never_removed():
    z = [0.0, 0.5, 0.52, 1.0, 1.5, 2.0]
    rep = repair_axis(z, "z", 0.52, 1.0, floor=0.1, protected=[1.0])
    assert rep is not None and rep.changed
    assert all(abs(v - 1.0) > 1e-12 for v in rep.removed)
    assert 1.0 in rep.after
    assert 0.52 not in rep.after


def test_protected_line_blocks_the_whole_repair():
    z = [0.0, 0.5, 0.52, 0.53, 1.0, 1.5]
    # both interior lines are protected -> no cell may be merged
    assert repair_axis(z, "z", 0.52, 0.53, floor=0.1, protected=[0.52, 0.53]) is None


def test_protected_line_matches_the_float32_grid():
    """CSXCAD stores lines as float32; the authored coordinate is float64."""
    z32 = [np.float32(v) for v in (0.0, 0.5, 1.6, 1.635, 1.7, 2.0)]
    rep = repair_axis(z32, "z", 1.6, 1.635, floor=0.1, protected=[1.635])
    assert rep is not None and rep.changed
    # 1.635 must survive (float32 vs float64 is the same line)
    assert any(abs(float(v) - 1.635) < 1e-6 for v in rep.after)
    assert all(abs(float(v) - 1.635) > 1e-6 for v in rep.removed)


def test_grading_is_relaxed_after_the_merge():
    z = [0.0, 0.048, 0.166, 0.5, 1.0]
    rep = repair_axis(z, "z", 0.048, 0.166, floor=0.1, ratio=2.0)
    assert rep is not None and rep.changed
    cells = np.diff(rep.after)
    ratios = np.maximum(cells[1:], cells[:-1]) / np.minimum(cells[1:], cells[:-1])
    assert float(np.max(ratios)) <= 2.0 * 1.001


def test_cells_above_max_cell_are_split():
    """A merge can never exceed max_cell (floor <= max_cell), but a pre-existing
    oversized cell is split so the repair cannot ship a cell the cap rejects."""
    z = [0.0, 0.1, 0.2, 1.0]
    rep = repair_axis(z, "z", 0.1, 0.2, floor=0.05, max_cell=0.15)
    assert rep is not None and rep.changed
    cells = np.diff(rep.after)
    assert float(np.max(cells)) <= 0.15 * 1.0001
    assert float(np.min(cells)) >= 0.05 * 0.999


def test_merge_cannot_exceed_max_cell():
    # floor <= max_cell / 2: a merge of two cells below the floor stays below
    # max_cell, so a repair can never ship a cell the cap rejects.
    z = [0.0, 0.05, 0.1, 0.15, 1.0]
    rep = repair_axis(z, "z", 0.05, 0.1, floor=0.06, max_cell=0.15)
    assert rep is not None and rep.changed
    seg = [v for v in rep.after if rep.lo - 1e-12 <= v <= rep.hi + 1e-12]
    assert float(np.max(np.diff(seg))) <= 0.15 * 1.0001
    assert float(np.min(np.diff(seg))) >= 0.06 * 0.999


def test_unknown_region_returns_none():
    assert repair_axis(FILTER_Z, "z", 1.70, 1.76) is None  # no line at the faces


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #
def test_preview_reports_timestep_and_cells():
    rep = repair_axis(FILTER_Z, "z", FILTER_LO, FILTER_HI)
    pv = preview([rep], _lines(), 0.001)
    assert pv.dt_before_s > 0.0 and pv.dt_after_s > 0.0
    assert pv.dt_after_s > pv.dt_before_s  # longer timestep = faster run
    assert pv.cells_after < pv.cells_before
    assert pv.speedup > 1.0
    text = pv.text()
    assert "ps" in text and "cells" in text
    assert "shorter" in text


def test_preview_of_no_repair_is_neutral():
    pv = preview([], _lines(), 0.001)
    assert pv.speedup == pytest.approx(1.0)
    assert "unchanged" in pv.text()


# --------------------------------------------------------------------------- #
# apply / storage
# --------------------------------------------------------------------------- #
def test_apply_repairs_writes_the_grid():
    csx = _CSX(_lines())
    rep = repair_axis(FILTER_Z, "z", FILTER_LO, FILTER_HI)
    apply_repairs(csx, [rep])
    assert csx.GetGrid().GetLines(2) == [pytest.approx(v) for v in rep.after]


def test_stored_rule_is_reproducible():
    mesh = MeshSpec(
        mesh_repairs=[{"axis": "z", "lo": FILTER_LO, "hi": FILTER_HI, "expand": 1}]
    )
    a, b = _CSX(_lines()), _CSX(_lines())
    ra = apply_mesh_repairs(a, mesh, _targets())
    rb = apply_mesh_repairs(b, mesh, _targets())
    assert len(ra) == 1 and len(rb) == 1
    assert a.GetGrid().GetLines(2) == pytest.approx(b.GetGrid().GetLines(2))
    assert ra[0].removed == rb[0].removed


def test_stored_rule_keeps_protected_lines():
    mesh = MeshSpec(
        mesh_repairs=[{"axis": "z", "lo": 0.5, "hi": 1.0, "floor": 0.1, "expand": 0}]
    )
    csx = _CSX(_lines([0.0, 0.5, 0.52, 0.54, 1.0, 1.5]))
    applied = apply_mesh_repairs(csx, mesh, _targets(), protected={"z": [0.52]})
    assert applied
    lines = csx.GetGrid().GetLines(2)
    assert 0.52 in lines  # the protected line survived
    assert 1.0 in lines
    assert 0.54 not in lines  # the auto line next to it was merged


def test_enforce_min_cellsize_is_off_by_default():
    mesh = MeshSpec(min_cellsize=0.1)
    assert mesh.enforce_min_cellsize is False
    csx = _CSX(_lines())
    assert apply_mesh_repairs(csx, mesh, _targets()) == []
    assert csx.GetGrid().GetLines(2) == pytest.approx(FILTER_Z)


def test_enforce_min_cellsize_merges_below_the_requested_min():
    mesh = MeshSpec(min_cellsize=0.03, enforce_min_cellsize=True)
    csx = _CSX(_lines())
    applied = apply_mesh_repairs(csx, mesh, _targets())
    assert applied
    assert min_cell(csx.GetGrid().GetLines(2)) >= 0.03 * 0.999


def test_enforce_applies_the_global_minimum_to_every_axis():
    """'Min cell size' is a global floor, 'Min cell size Z' overrides z."""
    mesh = MeshSpec(min_cellsize=0.03, enforce_min_cellsize=True)
    csx = _CSX(_lines())
    assert [r.axis for r in apply_mesh_repairs(csx, mesh, _targets())] == ["z"]

    mesh = MeshSpec(min_cellsize=0.03, min_cellsize_z=0.5, enforce_min_cellsize=True)
    csx = _CSX(_lines())
    applied = apply_mesh_repairs(csx, mesh, _targets())
    assert [r.axis for r in applied] == ["z"]
    assert min_cell(csx.GetGrid().GetLines(2)) >= 0.5 * 0.999


def test_enforce_works_with_only_the_z_minimum():
    mesh = MeshSpec(min_cellsize_z=0.03, enforce_min_cellsize=True)
    csx = _CSX(_lines())
    applied = apply_mesh_repairs(csx, mesh, _targets())
    assert [r.axis for r in applied] == ["z"]
    assert min_cell(csx.GetGrid().GetLines(2)) >= 0.03 * 0.999


def test_easy_mesh_setup_drops_the_floor_only_when_enforcing():
    """easyMesh must not chase the floor the repair pass enforces afterwards."""
    from app.meshing.easy_wrapper import easy_mesh_setup
    from app.meshing.rules import compute_targets
    from app.models.project import BoundarySpec

    mesh = MeshSpec(min_cellsize=0.1, min_cellsize_z=0.05)
    boundary = BoundarySpec()
    targets = compute_targets(mesh)
    setup = easy_mesh_setup(mesh, boundary, targets)
    assert setup["min_cellsize"] == pytest.approx(0.1)
    assert setup["min_cellsize_z"] == pytest.approx(0.05)

    mesh.enforce_min_cellsize = True
    setup = easy_mesh_setup(mesh, boundary, targets)
    assert "min_cellsize" not in setup
    assert "min_cellsize_z" not in setup
    # everything else is untouched
    assert setup["max_grading_ratio"] == pytest.approx(
        mesh.effective_max_grading_ratio()
    )


def test_enforce_min_cellsize_needs_a_user_value():
    """Without a user min there is nothing to enforce (V4)."""
    mesh = MeshSpec(refined_cellsize=0.005, enforce_min_cellsize=True)
    assert requested_min_cellsize(mesh) is None
    csx = _CSX(_lines())
    assert apply_mesh_repairs(csx, mesh, _targets()) == []
    assert csx.GetGrid().GetLines(2) == pytest.approx(FILTER_Z)


def test_repair_report_accepts_stored_dicts():
    rows = repair_report(
        [{"axis": "z", "lo": 1.7, "hi": 1.8, "floor": 0.03, "removed": [1.75]}],
        0.001,
    )
    assert any("z: 1.7..1.8" in r for r in rows)
    assert any("1.75" in r for r in rows)


def test_protected_coordinates_drops_tags():
    src = {"x": [(0.0, "box:a"), (1.0, "port:p1")], "y": [], "z": [(0.5, "dump:d")]}
    out = protected_coordinates(src)
    assert out["x"] == [0.0, 1.0]
    assert out["z"] == [0.5]
    assert out["y"] == []


def test_grid_lines_roundtrip():
    csx = _CSX(_lines())
    out = grid_lines(csx)
    assert out["z"].size == len(FILTER_Z)
    assert float(out["z"][0]) == pytest.approx(FILTER_Z[0])


# --------------------------------------------------------------------------- #
# diagnostics honesty (V4)
# --------------------------------------------------------------------------- #
def _mark(axis=2, lo=(0.0, 0.0, 1.718235), hi=(1.0, 1.0, 1.742016), **kw):
    return CriticalCell("timestep_cell", axis, lo, hi, **kw)


def test_requested_min_cellsize_ignores_derived_values():
    assert requested_min_cellsize(MeshSpec()) is None
    assert requested_min_cellsize(MeshSpec(min_cellsize=0.1)) == pytest.approx(0.1)
    assert requested_min_cellsize(MeshSpec(min_cellsize_z=0.05)) == pytest.approx(0.05)
    assert requested_min_cellsize(MeshSpec(min_cellsize=0.1), axis="z") is None


def test_cell_below_min_needs_the_user_value():
    grid = _lines()
    # derived fallback would flag many cells; the user value does not exist
    marks = find_critical_cells(grid, _targets(), min_cellsize=None)
    assert all(m.kind != "cell_below_min" for m in marks)
    marks = find_critical_cells(grid, _targets(), min_cellsize=0.1)
    assert any(m.kind == "cell_below_min" for m in marks)


def test_cause_geometry_when_both_faces_are_authored():
    sources = {"z": [(FILTER_LO, "box:a"), (FILTER_HI, "box:b")]}
    marks = find_critical_cells(_lines(), _targets(), sources=sources)
    cell = [m for m in marks if m.axis == 2 and m.kind == "timestep_cell"][0]
    assert cell.faces == 2
    assert cell.cause == "geometry"
    assert cell.repairable is False


def test_cause_artifact_when_a_face_is_generated():
    sources = {"z": [(FILTER_HI, "box:b")]}
    marks = find_critical_cells(_lines(), _targets(), sources=sources)
    cell = [m for m in marks if m.axis == 2 and m.kind == "timestep_cell"][0]
    assert cell.faces == 1
    assert cell.cause == "artifact"
    assert cell.repairable is True


def test_cause_requested_below_the_min():
    # only one face authored + below the requested min -> requested
    sources = {"z": [(FILTER_HI, "box:b")]}
    marks = find_critical_cells(
        _lines(), _targets(), min_cellsize=0.1, sources=sources
    )
    cell = marks[0]
    assert cell.kind == "cell_below_min"
    assert cell.cause == "requested"
    assert cell.repairable is True


def test_cause_geometry_wins_over_wunschwert():
    # the merged cell_below_min region spans 1.6 .. 1.742016, both authored
    sources = {"z": [(1.6, "box:a"), (FILTER_HI, "box:b")]}
    marks = find_critical_cells(
        _lines(), _targets(), min_cellsize=0.1, sources=sources
    )
    assert marks[0].faces == 2
    assert marks[0].cause == "geometry"


def test_penalty_and_label():
    marks = find_critical_cells(_lines(), _targets())
    cell = [m for m in marks if m.axis == 2 and m.kind == "timestep_cell"][0]
    assert cell.penalty > 1.0
    assert "x" in cell.label


def test_accepted_region_is_counted_separately():
    accepted = [{"axis": "z", "lo": FILTER_LO, "hi": FILTER_HI}]
    marks = find_critical_cells(_lines(), _targets(), accepted=accepted)
    cell = [m for m in marks if m.axis == 2 and m.kind == "timestep_cell"][0]
    assert cell.accepted is True
    line = summary_line(marks)
    assert "accepted" in line
    assert "0 open" in line or "1 open" in line


def test_engine_cell_is_engine_cause():
    from app.meshing.critical_cells import parse_engine_cell

    cell = parse_engine_cell(
        "Operator::CalcTimestep_Var3: Smallest timestep (2.29e-13s) found at "
        "position: 1 : 1;2;3",
        _lines(),
    )
    assert cell is not None
    assert cell.cause == "engine"
