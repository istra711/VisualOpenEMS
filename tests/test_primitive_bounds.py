"""Pure tests for the cartesian primitive AABB helper (app.meshing.bounds).

Regression guard for the NF2FF fit bug: cylinders only store their axis
endpoints, so the radius must be expanded onto the two perpendicular axes —
reading start/stop alone made a thin dipole look zero-width.
"""

from __future__ import annotations

import pytest

from app.meshing.bounds import cartesian_primitive_bbox


def test_box_uses_corners():
    lo, hi = cartesian_primitive_bbox(
        {"kind": "box", "start": [-1, -2, -3], "stop": [4, 5, 6]}
    )
    assert lo == [-1.0, -2.0, -3.0]
    assert hi == [4.0, 5.0, 6.0]


def test_box_order_independent():
    lo, hi = cartesian_primitive_bbox(
        {"kind": "box", "start": [4, 5, 6], "stop": [-1, -2, -3]}
    )
    assert lo == [-1.0, -2.0, -3.0]
    assert hi == [4.0, 5.0, 6.0]


def test_z_cylinder_radius_on_perpendicular_axes():
    # the dipole arms: axis z, radius 1 -> x/y must grow by the radius
    lo, hi = cartesian_primitive_bbox(
        {"kind": "cylinder", "start": [0, 0, -1], "stop": [0, 0, 29], "radius": 1.0}
    )
    assert lo == [-1.0, -1.0, -1.0]
    assert hi == [1.0, 1.0, 29.0]


def test_x_cylinder_radius_on_yz():
    lo, hi = cartesian_primitive_bbox(
        {"kind": "cylinder", "start": [-5, 0, 0], "stop": [5, 0, 0], "radius": 2.0}
    )
    assert lo == [-5.0, -2.0, -2.0]
    assert hi == [5.0, 2.0, 2.0]


def test_degenerate_cylinder_radius_on_all_axes():
    lo, hi = cartesian_primitive_bbox(
        {"kind": "cylinder", "start": [1, 1, 1], "stop": [1, 1, 1], "radius": 3.0}
    )
    assert lo == [-2.0, -2.0, -2.0]
    assert hi == [4.0, 4.0, 4.0]


def test_stl_passthrough():
    lo, hi = cartesian_primitive_bbox(
        {"kind": "stl", "start": [0, 1, 2], "stop": [3, 4, 5]}
    )
    assert lo == [0.0, 1.0, 2.0]
    assert hi == [3.0, 4.0, 5.0]


def test_polygon_passthrough():
    lo, hi = cartesian_primitive_bbox(
        {"kind": "polygon", "start": [-4, 0, -2], "stop": [-4, 7, 3]}
    )
    assert lo == [-4.0, 0.0, -2.0]
    assert hi == [-4.0, 7.0, 3.0]


def test_sphere_from_center_when_no_bbox():
    lo, hi = cartesian_primitive_bbox(
        {"kind": "sphere", "center": [1, 2, 3], "radius": 0.5}
    )
    assert lo == [0.5, 1.5, 2.5]
    assert hi == [1.5, 2.5, 3.5]


def test_sphere_prefers_stored_bbox():
    lo, hi = cartesian_primitive_bbox(
        {
            "kind": "sphere",
            "center": [0, 0, 0],
            "radius": 1.0,
            "start": [-1, -1, -1],
            "stop": [1, 1, 1],
        }
    )
    assert lo == [-1.0, -1.0, -1.0]
    assert hi == [1.0, 1.0, 1.0]


def test_unknown_kind_is_none():
    assert cartesian_primitive_bbox({"kind": "wormhole"}) is None


def test_missing_bounds_is_none():
    assert cartesian_primitive_bbox({"kind": "box"}) is None
    assert cartesian_primitive_bbox({"kind": "cylinder", "start": [0, 0, 0]}) is None
