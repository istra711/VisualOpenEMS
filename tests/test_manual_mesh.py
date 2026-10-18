"""Pure tests for the Mesh-Editor manual line overrides (app.meshing.manual_mesh)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
from CSXCAD import ContinuousStructure

from app.meshing.manual_mesh import apply_manual_mesh_lines


def _csx():
    csx = ContinuousStructure()
    g = csx.GetGrid()
    g.SetLines(0, [-30.0, -1.0, 0.0, 1.0, 30.0])
    g.SetLines(1, [-30.0, -1.0, 0.0, 1.0, 30.0])
    g.SetLines(2, [-30.0, -1.0, 0.0, 1.0, 30.0])
    return csx


def _lines(csx, d):
    return sorted(float(v) for v in np.asarray(csx.GetGrid().GetLines(d), float))


def test_no_overrides_is_noop():
    csx = _csx()
    before = _lines(csx, 0)
    assert apply_manual_mesh_lines(csx, SimpleNamespace()) == {}
    assert _lines(csx, 0) == before


def test_added_line_is_inserted():
    csx = _csx()
    mesh = SimpleNamespace(manual_mesh_lines={"x": [5.0]})
    stats = apply_manual_mesh_lines(csx, mesh)
    assert stats["x"]["added"] == 1
    assert 5.0 in _lines(csx, 0)


def test_removed_auto_line_is_dropped():
    csx = _csx()
    mesh = SimpleNamespace(manual_mesh_lines_removed={"z": [0.0]})
    stats = apply_manual_mesh_lines(csx, mesh)
    assert stats["z"]["removed"] == 1
    assert 0.0 not in _lines(csx, 2)


def test_duplicate_add_is_ignored():
    csx = _csx()
    mesh = SimpleNamespace(manual_mesh_lines={"y": [1.0, 1.0, 7.0]})
    stats = apply_manual_mesh_lines(csx, mesh)
    assert stats["y"]["added"] == 1  # 1.0 already on the grid, only 7.0 is new
    assert _lines(csx, 1).count(1.0) == 1


def test_removal_snaps_to_nearest_line_when_mesh_moved():
    # a slightly changed auto mesh shifts the line; the removal must still hit
    # the nearest grid line instead of becoming a silent no-op
    csx = _csx()
    g = csx.GetGrid()
    g.SetLines(0, [-30.0, -1.05, 0.05, 1.05, 30.0])  # shifted by ~0.05
    mesh = SimpleNamespace(manual_mesh_lines_removed={"x": [0.0]})
    stats = apply_manual_mesh_lines(csx, mesh)
    assert stats["x"]["removed"] == 1
    assert stats["x"]["missed"] == []
    assert stats["x"]["drift"] == pytest.approx(0.05, abs=1e-9)
    assert 0.05 not in _lines(csx, 0)  # the nearest line went away
    assert -1.05 in _lines(csx, 0)


def test_removal_reports_miss_when_no_line_near():
    csx = _csx()  # x lines only at -30,-1,0,1,30
    mesh = SimpleNamespace(manual_mesh_lines_removed={"x": [500.0]})
    stats = apply_manual_mesh_lines(csx, mesh)
    assert stats["x"]["removed"] == 0
    assert stats["x"]["missed"] == [500.0]


def test_remove_then_add_same_coord():
    csx = _csx()
    mesh = SimpleNamespace(
        manual_mesh_lines={"x": [2.0]},
        manual_mesh_lines_removed={"x": [2.0]},
    )
    apply_manual_mesh_lines(csx, mesh)
    # removed then re-added -> exactly one line at 2.0
    assert _lines(csx, 0).count(2.0) == 1


def test_cylindrical_is_skipped():
    csx = _csx()
    mesh = SimpleNamespace(manual_mesh_lines={"x": [5.0]})
    assert apply_manual_mesh_lines(csx, mesh, coord_system="cylindrical") == {}
    assert 5.0 not in _lines(csx, 0)
