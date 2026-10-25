"""Phase-0 coordinate probe (app.meshing.coord_diag) — measurement only."""

from __future__ import annotations

import numpy as np
import pytest

from app.meshing.coord_diag import (
    analyse_axis,
    analyse_coord_diag,
    min_cellsize_units,
    planned_quantum_units,
    report_lines,
    source_coordinates,
    summary_line,
)
from app.models.project import MeshSpec, PortDef


class _Grid:
    def __init__(self, lines: dict) -> None:
        self._lines = lines

    def GetLines(self, axis):  # noqa: N802 - CSXCAD API
        return self._lines[axis]


class _CSX:
    def __init__(self, lines: dict) -> None:
        self._grid = _Grid(lines)

    def GetGrid(self):  # noqa: N802
        return self._grid


# --------------------------------------------------------------------------- #
# planned lattice (the rule the plan states but its formula does not implement)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "c_min,decimals",
    [(1.0, 3), (0.1, 4), (0.01, 5), (0.05, 5), (0.5, 4), (10.0, 2), (0.001, 6)],
)
def test_planned_quantum_matches_the_documented_examples(c_min, decimals):
    mesh = MeshSpec(min_cellsize=c_min, drawing_unit="mm")
    assert planned_quantum_units(mesh) == pytest.approx(10.0 ** (-decimals))


def test_planned_quantum_never_exceeds_cmin_over_1000():
    for c in (10.0, 3.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.002, 0.001):
        q = planned_quantum_units(MeshSpec(min_cellsize=c))
        assert q is not None
        assert q <= c / 1000 * 1.0000001, (c, q)


def test_planned_quantum_units_are_decimal():
    q = planned_quantum_units(MeshSpec(min_cellsize=0.1))
    assert q == pytest.approx(1e-4)
    assert str(q).startswith("0.0001")


# --------------------------------------------------------------------------- #
# min cell detection
# --------------------------------------------------------------------------- #
def test_min_cellsize_prefers_the_explicit_value():
    mesh = MeshSpec(min_cellsize=0.05, min_cellsize_z=0.02, refined_cellsize=0.2)
    assert min_cellsize_units(mesh) == pytest.approx(0.05)
    mesh = MeshSpec(min_cellsize=None, min_cellsize_z=0.02, refined_cellsize=0.2)
    assert min_cellsize_units(mesh) == pytest.approx(0.02)


def test_min_cellsize_falls_back_to_refined_then_target():
    assert min_cellsize_units(MeshSpec(refined_cellsize=0.2)) == pytest.approx(0.2)

    class _Targets:
        target_cell_units = 0.3

    assert min_cellsize_units(MeshSpec(), _Targets()) == pytest.approx(0.3)
    assert min_cellsize_units(MeshSpec(), None) is None


# --------------------------------------------------------------------------- #
# authored coordinates
# --------------------------------------------------------------------------- #
def test_source_coordinates_tag_every_source():
    prims = [
        {"kind": "box", "name": "sub", "start": [0, 0, 0], "stop": [10, 10, 1.6]},
        {"kind": "polygon", "name": "poly", "points": [[1, 2], [3, 4]],
         "axis": "z", "coord": 1.6},
    ]

    class _P:
        ports = [PortDef(name="in1", start=[2, 5, 1.6], stop=[2, 6, 1.6])]
        dumps: list = []
        nf2ff = None

    coords = source_coordinates(_P(), prims, {"x": [7.5]})
    tags_x = {tag for _v, tag in coords["x"]}
    assert "box:sub" in tags_x and "polygon:poly" in tags_x
    assert "port:in1" in tags_x and "manual" in tags_x
    # z carries the box faces and the polygon plane
    assert [v for v, t in coords["z"] if t == "polygon:poly"] == [1.6]


# --------------------------------------------------------------------------- #
# axis analysis
# --------------------------------------------------------------------------- #
def test_near_duplicate_lines_are_bucketed():
    lines = np.array([0.0, 1.0, 1.0000001, 2.0])
    diag = analyse_axis("x", lines=lines, tol=0.0)
    assert diag.n_lines == 4
    assert len(diag.line_pairs) == 1
    pair = diag.line_pairs[0]
    assert pair.delta == pytest.approx(1e-7)
    assert pair.bucket == "<1e-6"
    assert diag.bucket_counts() == {"<1e-6": 1}
    assert diag.min_cell == pytest.approx(1e-7)
    assert diag.median_cell > 0.9


def test_axis_analysis_does_not_modify_the_input():
    lines = np.array([0.0, 1.0, 1.0000001, 2.0])
    keep = lines.copy()
    analyse_axis("x", lines=lines)
    assert np.array_equal(lines, keep)


def test_targets_off_the_grid_are_reported_with_distance():
    lines = np.array([0.0, 1.0, 2.0])
    diag = analyse_axis(
        "x", lines=lines, targets=[(1.0, "port:a"), (1.5, "box:b")], tol=0.01
    )
    assert [(h.tag, h.cls) for h in diag.hits] == [
        ("port:a", "on_line"),
        ("box:b", "off_grid"),
    ]
    hit = diag.hits[1]
    assert hit.dist == pytest.approx(0.5)
    assert hit.ratio == pytest.approx(0.5)  # 1 mm cells next to it


def test_off_grid_uses_the_local_cell_not_an_absolute_tolerance():
    """The meaningful measure is a cell fraction, like ``port_align`` uses."""
    lines = np.array([0.0, 1.0, 2.0])
    snapped = analyse_axis("x", lines=lines, targets=[(1.1, "port:p")], tol=1e-9)
    assert snapped.hits[0].cls == "snapped"  # 10 % of a cell -> openEMS snaps
    assert snapped.hits[0].ratio == pytest.approx(0.1)
    off = analyse_axis("x", lines=lines, targets=[(1.3, "port:p")], tol=1e-9)
    assert off.hits[0].cls == "off_grid"  # 30 % of a cell -> real misalignment
    assert off.hits[0].ratio == pytest.approx(0.3)


def test_target_pairs_below_tolerance():
    diag = analyse_axis(
        "x",
        lines=np.array([0.0, 1.0]),
        targets=[(1.0, "box:a"), (1.0001, "stl:b"), (2.0, "port:c")],
        tol=0.001,
    )
    assert len(diag.target_pairs) == 1
    pair = diag.target_pairs[0]
    assert {pair.tag_a, pair.tag_b} == {"box:a", "stl:b"}


# --------------------------------------------------------------------------- #
# whole probe
# --------------------------------------------------------------------------- #
def _probe(**kw):
    lines = {
        "x": np.array([0.0, 1.0, 1.0000001, 2.0]),
        "y": np.array([0.0, 1.0, 2.0]),
        "z": np.array([0.0, 1.6]),
    }
    prims = [
        {"kind": "box", "name": "m", "start": [1.0, 0.5, 1.6], "stop": [2.0, 1.5, 1.6]},
    ]
    mesh = MeshSpec(min_cellsize=0.05, drawing_unit="mm")

    class _P:
        ports: list = []
        dumps: list = []
        nf2ff = None

    return analyse_coord_diag(
        _CSX(lines), _P(), prims, mesh, None, unit="mm", unit_m=1e-3, **kw
    )


def test_probe_reports_double_line_and_off_line():
    diag = _probe()
    codes = {i.code for i in diag.issues}
    assert "double_line" in codes  # the 1e-7 pair in x
    assert diag.tol_units == pytest.approx(0.5e-5)  # q = 1e-5 for c_min = 0.05
    assert diag.quantum_units == pytest.approx(1e-5)  # corrected auto rule
    # box faces 1.0/2.0 sit on lines; nothing to merge here
    assert diag.line_pairs[0].delta == pytest.approx(1e-7)
    # 0.05 mm requested, the grid's smallest cell is 1e-7 -> flagged
    assert "cell_below_min" in codes
    assert diag.warnings


def test_probe_without_a_mesh_still_analyses_sources():
    class _P:
        ports: list = []
        dumps: list = []
        nf2ff = None

    diag = analyse_coord_diag(None, _P(), [], MeshSpec(min_cellsize=1.0))
    assert all(a.n_lines == 0 for a in diag.axes.values())
    assert diag.line_pairs == []
    assert "no mesh" in "\n".join(report_lines(diag))


def test_probe_finds_authored_pairs_below_tolerance():
    prims = [
        {"kind": "box", "name": "a", "start": [1.0, 0, 0], "stop": [2.0, 1, 1]},
        # the STL/round(...,4) style twin: 1e-4 units away from the box face
        {"kind": "box", "name": "stl_like", "start": [1.0001, 0, 0],
         "stop": [2.0, 1, 1]},
    ]
    mesh = MeshSpec(min_cellsize=1.0)  # q = 1e-3, tol = 5e-4

    class _P:
        ports: list = []
        dumps: list = []
        nf2ff = None

    diag = analyse_coord_diag(None, _P(), prims, mesh, None)
    assert len(diag.target_pairs) >= 1
    assert any(i.code == "coord_pair" for i in diag.issues)
    assert "stl_like" in "\n".join(report_lines(diag))


def test_summary_and_report_text():
    diag = _probe()
    line = summary_line(diag)
    assert line.startswith("Coord probe:")
    assert "near-duplicate" in line
    body = "\n".join(report_lines(diag))
    assert "Phase 0" in body
    assert "Planned lattice" in body
    assert "Findings:" in body
