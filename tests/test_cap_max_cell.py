"""Regression: cap_max_cell_size keeps every grid cell within lambda_air/10.

easyMesh sizes the air cells from target_frequency and then expands the PML
region, which can push absorbing-region cells just past the openEMS hard limit
and abort the run via validate_mesh. The cap must split those cells.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("CSXCAD")

import CSXCAD  # noqa: E402

from app.meshing.easy_wrapper import cap_max_cell_size  # noqa: E402
from app.meshing.rules import MeshTargets  # noqa: E402
from app.models.project import MeshSpec  # noqa: E402


def _targets():
    return MeshTargets(
        f_max_hz=2.95e9,
        eps_max=1.0,
        unit_m=1e-3,
        lambda_min_m=0.10162,
        lambda_min_units=101.62,
        max_cell_units=10.162,
        target_cell_units=8.157,
    )


def test_cap_splits_oversized_cells():
    csx = CSXCAD.ContinuousStructure()
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)
    for x in (0.0, 10.324, 20.648, 30.972):
        grid.AddLine("x", x)
    cap_max_cell_size(csx, MeshSpec(drawing_unit="mm"), _targets())
    lines = np.sort(np.asarray(grid.GetLines("x"), dtype=float))
    cells = np.diff(lines)
    assert cells.size > 0
    assert cells.max() <= 10.162 + 1e-6


def test_cap_leaves_small_cells_untouched():
    csx = CSXCAD.ContinuousStructure()
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)
    coords = [0.0, 1.0, 2.0, 4.0, 8.0]
    for x in coords:
        grid.AddLine("x", x)
    cap_max_cell_size(csx, MeshSpec(drawing_unit="mm"), _targets())
    lines = np.sort(np.asarray(grid.GetLines("x"), dtype=float))
    assert np.allclose(lines, coords)
