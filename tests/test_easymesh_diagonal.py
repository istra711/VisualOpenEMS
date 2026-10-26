"""Polygons with 45 deg edges mesh correctly (restored easyMesh helper).

The bundled third-party easyMesh routes polygons with non-axis-aligned edges
through ``meshingUtils.handle_diagonal_edges``. For a pair of *parallel*
diagonal edges that function calls
``geometryUtils.distance_between_segments(p1, p2, q1, q2)`` — a helper the
bundled copy had lost during its refactor (the name occurred exactly once in
the whole bundle: at that call site), so any polygon with two parallel 45 deg
edges aborted the build with ``AttributeError``.

The helper is now restored verbatim from the upstream package
(github.com/MustafaAlchalabi/easyMesh4openEMS, ``easyMesh/geometryUtils.py``).
These tests pin both the helper contract (the caller indexes the records) and
the end-to-end build of a chamfered hexagon, so the three-box workaround in
``app/csxcad_builder/gps_patch.py`` is no longer required for meshing.

Known limitation of the upstream heuristic (not this fix): a hexagon still
gets a pair of ~0.2 mm sliver cells next to its flat y edges, because
``handle_diagonal_edges`` grades the lines between the diagonals. It is a
quality warning, not a build error.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_EASY = ROOT / "third_party" / "easyMesh4openEMS"
if _EASY.is_dir() and str(_EASY) not in sys.path:
    sys.path.insert(0, str(_EASY))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Axis-aligned rectangle: edges are parallel to x/y -> no diagonal path.
RECT_XS, RECT_YS = [0.0, 20.0, 20.0, 0.0], [0.0, 0.0, 20.0, 20.0]

# Same size hexagon: (10,0)-(20,10) and (0,20)-(-10,10) are parallel 45 deg
# edges -> handle_diagonal_edges reaches distance_between_segments.
CHAMFER_XS = [0.0, 10.0, 20.0, 10.0, 0.0, -10.0]
CHAMFER_YS = [0.0, 0.0, 10.0, 20.0, 20.0, 10.0]


def _mesh_polygon(xs: list[float], ys: list[float]) -> tuple[dict, object]:
    """Mesh a single copper polygon on z = 0.

    Returns the easyMesh global setup and the CSX (for grid checks).
    """
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS as OE

    from app.meshing.easy_wrapper import (
        apply_boundary,
        enhance_csx,
        enhance_fdtd,
        generate_mesh,
    )
    from app.meshing.rules import compute_targets
    from app.models.project import BoundarySpec, MeshSpec

    mesh = MeshSpec(
        preset="low",
        drawing_unit="mm",
        start_frequency=2.0e9,
        stop_frequency=3.0e9,
        target_frequency=2.45e9,
        max_grading_ratio=2.0,
        cells_per_wavelength=8.0,
    )
    boundary = BoundarySpec()
    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(mesh.unit_to_meters())

    setup: dict = {}
    csx_w = enhance_csx(csx, setup)
    prop = csx_w.AddMetal("copper")
    prop.AddPolygon((xs, ys), 2, 0.0, priority=10)  # normal z, elevation 0

    fdtd = OE(NrTS=10, EndCriteria=1e-3)
    enhance_fdtd(fdtd, setup)
    targets = compute_targets(mesh)
    apply_boundary(fdtd, boundary, mesh, targets)
    result = generate_mesh(csx, mesh, boundary, targets, primitives_mesh_setup=setup)
    return result, csx


def test_distance_between_segments_returns_the_caller_contract():
    """``handle_diagonal_edges`` indexes ``(dist, point, p1, p2, q1, q2)``."""
    from easyMesh import geometryUtils

    p1, p2 = np.array([0.0, 0.0]), np.array([10.0, 0.0])
    q1, q2 = np.array([0.0, 2.0]), np.array([10.0, 2.0])  # parallel, 2 mm apart
    records = geometryUtils.distance_between_segments(p1, p2, q1, q2)

    assert len(records) == 10, "one record per sample point on p1-p2"
    for rec in records:
        assert len(rec) == 6, rec
        dist, point, rp1, rp2, rq1, rq2 = rec
        assert dist == pytest.approx(2.0, abs=1e-9)
        assert np.asarray(point).shape == (2,)
        assert np.allclose(rp1, p1) and np.allclose(rp2, p2)
        assert np.allclose(rq1, q1) and np.allclose(rq2, q2)
    # the samples span the first segment (the caller uses them as a range)
    xs = [float(rec[1][0]) for rec in records]
    assert min(xs) == pytest.approx(0.0) and max(xs) == pytest.approx(10.0)


def test_axis_aligned_polygon_meshes():
    """Control: the workaround direction (no diagonal edges) builds a mesh."""
    result, csx = _mesh_polygon(RECT_XS, RECT_YS)
    assert "auto_mesh_lines" in result
    # the polygon corners must be snapped into the grid
    grid = csx.GetGrid()
    x_lines = grid.GetLines(0)
    y_lines = grid.GetLines(1)
    assert min(x_lines) <= 0.0 and max(x_lines) >= 20.0, (min(x_lines), max(x_lines))
    assert min(y_lines) <= 0.0 and max(y_lines) >= 20.0, (min(y_lines), max(y_lines))


def test_parallel_45deg_edges_mesh():
    """Regression: two parallel 45 deg edges used to abort the build.

    A hexagon (three pairs of parallel edges, two of them diagonal) is the
    geometry of a chamfered CP patch - it must build and keep every vertex
    coordinate on the grid, otherwise the staircase would cut the corners.
    """
    result, csx = _mesh_polygon(CHAMFER_XS, CHAMFER_YS)
    assert "auto_mesh_lines" in result

    grid = csx.GetGrid()
    x_lines = np.asarray(grid.GetLines(0), dtype=float)
    y_lines = np.asarray(grid.GetLines(1), dtype=float)
    for v in sorted(set(CHAMFER_XS)):
        assert np.isclose(x_lines, v, atol=1e-6).any(), (v, x_lines.tolist())
    for v in sorted(set(CHAMFER_YS)):
        assert np.isclose(y_lines, v, atol=1e-6).any(), (v, y_lines.tolist())
    # the mesh must cover the whole outline
    assert x_lines.min() <= min(CHAMFER_XS) and x_lines.max() >= max(CHAMFER_XS)
    assert y_lines.min() <= min(CHAMFER_YS) and y_lines.max() >= max(CHAMFER_YS)
