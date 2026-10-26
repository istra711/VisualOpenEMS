"""Polygon primitives must be drawn with their real outline, not their AABB.

Regression: the Mesh Viewer drew every polygon as the bounding rectangle of
its points (a hexagon patch looked like a rectangular patch, a chamfered
corner like a full block). The cut is derived per slice instead:

* face-on cut  -> the polygon itself (sheet) / its cross-section (prism),
* edge-on cut  -> a segment (sheet) or a quad (extruded prism),
* projection   -> the outline (face-on) or the silhouette (edge-on).

The geometry lives in ``app.meshing.polygon_geometry`` (Qt-free, so it can be
tested here); the viewer-level render check is ``tests/polygon_viewer_smoke.py``
because importing PySide6 inside pytest crashes this environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: hexagon patch, points given in the polygon plane (x, y) for a z-normal
#: polygon; the AABB is 30 x 20 mm
HEX = [(-5.0, -10.0), (5.0, -10.0), (15.0, 0.0), (5.0, 10.0), (-5.0, 10.0), (-15.0, 0.0)]


def test_polygon_bbox_is_derived_from_the_points():
    from app.meshing.polygon_geometry import polygon_bbox

    lo, hi = polygon_bbox(HEX, "z", 1.6, 0.0)
    assert lo == [-15.0, -10.0, 1.6]
    assert hi == [15.0, 10.0, 1.6]

    # extruded prism: the length adds to the normal axis
    lo, hi = polygon_bbox(HEX, "z", 1.6, 2.0)
    assert lo[2] == pytest.approx(1.6)
    assert hi[2] == pytest.approx(3.6)

    # normal axis x -> points are (y, z)
    lo, hi = polygon_bbox([(0.0, 1.0), (2.0, 1.0), (2.0, 3.0)], "x", 0.5, 0.0)
    assert lo == [0.5, 0.0, 1.0]
    assert hi == [0.5, 2.0, 3.0]

    assert polygon_bbox([], "z", 0.0, 0.0) is None
    assert polygon_bbox([(0.0, 0.0)], "z", 0.0, 0.0) is not None


def test_face_on_cut_returns_the_hexagon_not_a_rectangle():
    from app.meshing.polygon_geometry import polygon_cut_paths

    paths = polygon_cut_paths(HEX, "z", 1.6, 0.0, 0, 1, 2, 1.6, 1e-6)
    assert len(paths) == 1
    assert len(paths[0]) == 6, f"expected 6 outline points, got {paths[0]}"
    assert [tuple(p) for p in paths[0]] == [tuple(p) for p in HEX]


def test_face_on_cut_ignores_other_planes():
    from app.meshing.polygon_geometry import polygon_cut_paths

    # a flat sheet exists only in its own plane
    assert polygon_cut_paths(HEX, "z", 1.6, 0.0, 0, 1, 2, 2.0, 1e-6) == []
    assert polygon_cut_paths(HEX, "z", 1.6, 0.0, 0, 1, 2, 1.6 + 1e-3, 1e-6) == []


def test_prism_is_cut_on_every_plane_of_its_extrusion():
    from app.meshing.polygon_geometry import polygon_cut_paths

    for plane in (1.6, 2.5, 3.6):
        paths = polygon_cut_paths(HEX, "z", 1.6, 2.0, 0, 1, 2, plane, 1e-6)
        assert len(paths) == 1 and len(paths[0]) == 6, (plane, paths)
    assert polygon_cut_paths(HEX, "z", 1.6, 2.0, 0, 1, 2, 3.7, 1e-6) == []


def test_edge_on_cut_is_a_segment():
    from app.meshing.polygon_geometry import polygon_cut_paths

    # XZ slice (x, z) at y = 0 cuts the hexagon across its full width
    paths = polygon_cut_paths(HEX, "z", 1.6, 0.0, 0, 2, 1, 0.0, 1e-6)
    assert len(paths) == 1 and len(paths[0]) == 2, paths
    (x0, z0), (x1, z1) = paths[0]
    assert z0 == pytest.approx(1.6) and z1 == pytest.approx(1.6)
    assert sorted((x0, x1)) == pytest.approx([-15.0, 15.0])

    # y = 5 cuts a narrower chord
    paths = polygon_cut_paths(HEX, "z", 1.6, 0.0, 0, 2, 1, 5.0, 1e-6)
    assert sorted(p[0] for p in paths[0]) == pytest.approx([-10.0, 10.0])

    # no intersection -> nothing to draw
    assert polygon_cut_paths(HEX, "z", 1.6, 0.0, 0, 2, 1, 50.0, 1e-6) == []


def test_edge_on_cut_through_a_prism_is_a_quad():
    from app.meshing.polygon_geometry import polygon_cut_paths

    paths = polygon_cut_paths(HEX, "z", 1.6, 2.0, 0, 2, 1, 0.0, 1e-6)
    assert len(paths) == 1 and len(paths[0]) == 4, paths
    zs = sorted({p[1] for p in paths[0]})
    assert zs == pytest.approx([1.6, 3.6])


def test_projection_draws_the_outline_or_the_silhouette():
    from app.meshing.polygon_geometry import polygon_cut_paths

    # overview XY: face-on -> hexagon
    paths = polygon_cut_paths(HEX, "z", 1.6, 0.0, 0, 1, 2, None, 0.0)
    assert len(paths) == 1 and len(paths[0]) == 6

    # overview XZ: edge-on sheet -> segment, prism -> rectangle
    paths = polygon_cut_paths(HEX, "z", 1.6, 0.0, 0, 2, 1, None, 0.0)
    assert len(paths) == 1 and len(paths[0]) == 2, paths
    paths = polygon_cut_paths(HEX, "z", 1.6, 2.0, 0, 2, 1, None, 0.0)
    assert len(paths) == 1 and len(paths[0]) == 4, paths


def test_axis_mapping_is_cyclic():
    """x-normal -> (y, z), y-normal -> (z, x): the outline must not rotate."""
    from app.meshing.polygon_geometry import polygon_cut_paths

    tri = [(0.0, 0.0), (2.0, 0.0), (0.0, 3.0)]

    # y-normal polygon: points are (z, x); the face-on slice is XZ (x, z)
    # -> mapped as (v, u)
    paths = polygon_cut_paths(tri, "y", 1.0, 0.0, 0, 2, 1, 1.0, 1e-6)
    assert paths == [[(0.0, 0.0), (0.0, 2.0), (3.0, 0.0)]], paths

    # x-normal polygon: points are (y, z); the face-on slice is YZ (y, z)
    # -> points used as given
    paths = polygon_cut_paths(tri, "x", 1.0, 0.0, 1, 2, 0, 1.0, 1e-6)
    assert paths == [[(0.0, 0.0), (2.0, 0.0), (0.0, 3.0)]], paths


def test_csx_polygons_expose_their_outline():
    """``iter_primitive_infos`` must carry the points, not only the AABB."""
    from CSXCAD import ContinuousStructure

    from app.meshing.preview import iter_primitive_infos

    csx = ContinuousStructure()
    metal = csx.AddMetal("patch")
    xs = [p[0] for p in HEX]
    ys = [p[1] for p in HEX]
    metal.AddPolygon((xs, ys), "z", 1.6)
    metal.AddLinPoly((xs, ys), "z", 1.6, 2.0)

    infos = iter_primitive_infos(csx)
    poly = [i for i in infos if i.poly_points]
    assert len(poly) == 2, [(i.name, i.prim_type) for i in infos]

    sheet, prism = sorted(poly, key=lambda i: i.poly_length)
    assert sheet.poly_axis == "z"
    assert sheet.poly_coord == pytest.approx(1.6)
    assert sheet.poly_length == pytest.approx(0.0)
    assert len(sheet.poly_points) == 6
    assert sheet.poly_points[0] == pytest.approx(HEX[0])
    # AABB still available (domain checks / highlight)
    assert sheet.start[2] == pytest.approx(1.6) == pytest.approx(sheet.stop[2])

    assert prism.poly_length == pytest.approx(2.0)
    assert prism.stop[2] == pytest.approx(3.6)

    # and the outline survives the slice helper
    from app.meshing.polygon_geometry import polygon_cut_paths

    paths = polygon_cut_paths(
        sheet.poly_points, sheet.poly_axis, sheet.poly_coord, sheet.poly_length,
        0, 1, 2, 1.6, 1e-6,
    )
    assert len(paths) == 1 and len(paths[0]) == 6
