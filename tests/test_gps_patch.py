"""GPS L1 patch preset: chamfer outline and project defaults.

The preset models the two corner chamfers as a staircase polygon (CSXCAD
rasterises a polygon with a point-in-polygon test at the cell centre, so a true
45 deg edge inside one 5 mm cell would be all-metal or all-air). These tests
pin the properties the circular polarisation relies on:

* the outline stays inside the square and removes the two opposite corners,
* it is invariant under a 180 deg rotation (the two chamfers must be a
  rotation of each other - a mirrored second staircase breaks the two-fold
  symmetry and costs axial ratio),
* every edge is axis-aligned (no 45 deg edge the mesh cannot resolve),
* the probe sits on the patch centre line.
"""

from __future__ import annotations

import pytest

from app.csxcad_builder.gps_patch import (
    chamfer_removed_area,
    chamfered_patch_points,
    gps_patch_primitives,
)

A = 44.10
CUT = 5.0
STEP = 1.0


def test_outline_is_inside_the_square() -> None:
    pts = chamfered_patch_points(A, CUT, STEP)
    assert len(pts) >= 6
    h = A / 2.0
    for x, y in pts:
        assert -h - 1e-9 <= x <= h + 1e-9
        assert -h - 1e-9 <= y <= h + 1e-9


def test_corners_are_cut_off() -> None:
    pts = chamfered_patch_points(A, CUT, STEP)
    h = A / 2.0
    for corner in ((-h, -h), (h, h)):
        assert not any(
            abs(x - corner[0]) < 1e-9 and abs(y - corner[1]) < 1e-9 for x, y in pts
        )
    for corner in ((-h, h), (h, -h)):
        assert any(
            abs(x - corner[0]) < 1e-9 and abs(y - corner[1]) < 1e-9 for x, y in pts
        )


def test_outline_has_two_fold_symmetry() -> None:
    pts = chamfered_patch_points(A, CUT, STEP)
    rotated = {(round(-x, 9), round(-y, 9)) for x, y in pts}
    original = {(round(x, 9), round(y, 9)) for x, y in pts}
    assert rotated == original


def test_every_edge_is_axis_aligned() -> None:
    pts = chamfered_patch_points(A, CUT, STEP)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        assert abs(x0 - x1) < 1e-9 or abs(y0 - y1) < 1e-9


def test_chamfer_area_is_close_to_the_triangle() -> None:
    removed = chamfer_removed_area(A, CUT, STEP)
    ideal = CUT * CUT / 2.0
    # a staircase cannot hit c^2/2 exactly; one step adds at most c*step/2
    assert ideal * 0.75 <= removed <= ideal * 1.30


def test_steps_are_not_finer_than_the_mesh_cell() -> None:
    pts = chamfered_patch_points(A, CUT, STEP)
    xs = sorted({round(x, 9) for x, _ in pts})
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 1e-9]
    assert gaps, "no chamfer steps"
    # easyMesh drops lines below min_cellsize (1 mm in the preset)
    assert min(gaps) >= STEP - 1e-9


def test_project_defaults_and_centre_line_feed() -> None:
    pytest.importorskip("openEMS")
    project, prims = gps_patch_primitives()
    poly = next(p for p in prims if p["kind"] == "polygon")
    assert poly["axis"] == "z"
    assert poly["length"] == pytest.approx(0.035)
    assert len(poly["points"]) >= 6
    port = project.ports[0]
    assert port.start[0] == pytest.approx(-0.5)  # x = 0 +- 0.5 -> centre line
    assert port.stop[0] == pytest.approx(0.5)
    y = 0.5 * (port.start[1] + port.stop[1])
    assert y == pytest.approx(-A / 2.0 + 10.9)
    assert project.mesh.min_cellsize == pytest.approx(1.0)
    assert project.nf2ff.enabled
    assert project.nf2ff.frequencies == pytest.approx([1.57542e9])
