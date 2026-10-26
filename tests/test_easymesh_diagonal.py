"""easyMesh cannot mesh polygons with parallel 45 deg edges (bundled copy).

The bundled third-party easyMesh routes polygons with non-axis-aligned edges
through ``meshingUtils.handle_diagonal_edges``.  For a pair of *parallel*
diagonal edges that function calls
``geometryUtils.distance_between_segments(p1, p2, q1, q2)`` - a helper that
does not exist in the bundled ``geometryUtils.py`` (the name occurs exactly
once in the whole bundle: at that call site).  The mesh build therefore aborts
with ``AttributeError``.

Call chain (verified):

    easyMesher.GenMesh
      -> collect_mesh_data
        -> get_mesh_data
          -> meshingUtils.adjust_mesh_parameters   (line 176)
            -> meshingUtils.check_max_resolution   (line 742)
              -> meshingUtils.handle_diagonal_edges (line 360)

A single 45 deg edge does *not* trigger it: the pairwise loop only compares
edge pairs, and perpendicular pairs are skipped, so the crash needs two
parallel diagonals (e.g. two opposite corner chamfers - exactly the geometry of
a corner-truncated circularly polarized patch).

Consequence for this project: the textbook CP patch cannot be meshed, so
``app/csxcad_builder/gps_patch.py`` builds the same copper area from three
axis-aligned boxes (square notches instead of 45 deg chamfers).  The two tests
below pin both halves of that decision:

* an axis-aligned rectangle meshes fine (the workaround direction is sound),
* the same outline with 45 deg chamfers raises the AttributeError.

When a future easyMesh update provides ``distance_between_segments``, the
second test fails on purpose - that is the signal to drop the three-box
workaround and offer the textbook chamfered patch again.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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
# edges -> handle_diagonal_edges reaches the missing helper.
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


def test_parallel_45deg_edges_abort_the_mesh():
    """Documented limitation: two parallel 45 deg edges crash the mesher.

    When this starts failing, the bundled easyMesh gained
    ``geometryUtils.distance_between_segments`` - drop the three-box workaround
    in ``app/csxcad_builder/gps_patch.py`` and retry the chamfered patch.
    """
    with pytest.raises(AttributeError, match="distance_between_segments"):
        _mesh_polygon(CHAMFER_XS, CHAMFER_YS)
