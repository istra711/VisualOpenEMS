"""Pure tests for the STEP export spec builder (app.freecad_io.step_export).

``build_shape_specs`` is the geometry half of the STEP export: it turns project
primitives (drawing units) into millimetre shape specs for the FreeCADCmd
runner. These tests do not need FreeCAD.

The runner is verified end-to-end by ``tests/step_export_smoke.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.freecad_io.step_export import build_shape_specs, mm_per_unit


def _kinds(specs):
    return [(s["name"], s["kind"]) for s in specs]


# --- units -------------------------------------------------------------------


@pytest.mark.parametrize(
    "unit, expected", [("m", 1000.0), ("cm", 10.0), ("mm", 1.0), ("um", 0.001)]
)
def test_mm_per_unit(unit, expected):
    assert mm_per_unit(unit) == pytest.approx(expected)


def test_coordinates_are_converted_to_mm():
    specs, warns = build_shape_specs(
        [{"kind": "box", "name": "b", "start": [0, 0, 0], "stop": [0.02, 0.01, 0.001]}],
        unit="m",
    )
    assert warns == []
    assert specs[0]["stop"] == pytest.approx([20.0, 10.0, 1.0])


def test_mm_is_a_no_op():
    specs, _ = build_shape_specs(
        [{"kind": "box", "name": "b", "start": [-1, -2, -3], "stop": [4, 5, 6]}]
    )
    assert specs[0]["start"] == pytest.approx([-1.0, -2.0, -3.0])
    assert specs[0]["stop"] == pytest.approx([4.0, 5.0, 6.0])


# --- boxes -------------------------------------------------------------------


def test_box_is_order_independent():
    specs, _ = build_shape_specs(
        [{"kind": "box", "name": "b", "start": [4, 5, 6], "stop": [-1, -2, -3]}]
    )
    assert specs[0]["start"] == pytest.approx([-1.0, -2.0, -3.0])
    assert specs[0]["stop"] == pytest.approx([4.0, 5.0, 6.0])


def test_zero_thickness_box_becomes_sheet():
    # the patch preset's metal plane: start.z == stop.z
    specs, warns = build_shape_specs(
        [
            {
                "kind": "box",
                "name": "patch",
                "start": [-15, -10, 1.6],
                "stop": [15, 10, 1.6],
            }
        ]
    )
    assert warns == []
    spec = specs[0]
    assert spec["kind"] == "sheet"
    assert spec["axis"] == "z"
    assert spec["coord"] == pytest.approx(1.6)
    # rectangle in the cyclic (x, y) plane
    assert spec["points"][0] == pytest.approx([-15.0, -10.0])
    assert spec["points"][2] == pytest.approx([15.0, 10.0])


def test_zero_thickness_box_along_x_uses_yz_plane():
    specs, _ = build_shape_specs(
        [{"kind": "box", "name": "s", "start": [2, 0, 0], "stop": [2, 5, 3]}]
    )
    assert specs[0]["axis"] == "x"
    # x normal -> cyclic (y, z)
    assert specs[0]["points"][0] == pytest.approx([0.0, 0.0])
    assert specs[0]["points"][2] == pytest.approx([5.0, 3.0])


def test_line_and_point_boxes_are_skipped():
    specs, warns = build_shape_specs(
        [
            {"kind": "box", "name": "line", "start": [0, 0, 0], "stop": [5, 0, 0]},
            {"kind": "box", "name": "point", "start": [1, 1, 1], "stop": [1, 1, 1]},
        ]
    )
    assert specs == []
    assert any("line" in w and "line" in w.lower() for w in warns)
    assert any("point" in w for w in warns)


# --- cylinders / spheres -----------------------------------------------------


def test_cylinder_spec():
    specs, _ = build_shape_specs(
        [
            {
                "kind": "cylinder",
                "name": "feed",
                "start": [0, 0, 0],
                "stop": [0, 0, 1.6],
                "radius": 0.5,
            }
        ]
    )
    assert specs[0] == {
        "name": "feed",
        "kind": "cylinder",
        "start": pytest.approx([0.0, 0.0, 0.0]),
        "stop": pytest.approx([0.0, 0.0, 1.6]),
        "radius": pytest.approx(0.5),
    }


@pytest.mark.parametrize(
    "prim",
    [
        {"kind": "cylinder", "name": "c", "start": [0, 0, 0], "stop": [0, 0, 1], "radius": 0},
        {"kind": "cylinder", "name": "c", "start": [0, 0, 1], "stop": [0, 0, 1], "radius": 1},
        {"kind": "sphere", "name": "s", "center": [0, 0, 0], "radius": 0},
    ],
)
def test_degenerate_round_primitives_are_skipped(prim):
    specs, warns = build_shape_specs([prim])
    assert specs == []
    assert warns and "skipped" in warns[0]


def test_sphere_spec():
    specs, _ = build_shape_specs(
        [{"kind": "sphere", "name": "ball", "center": [1, 2, 3], "radius": 2}]
    )
    assert specs[0]["kind"] == "sphere"
    assert specs[0]["center"] == pytest.approx([1.0, 2.0, 3.0])
    assert specs[0]["radius"] == pytest.approx(2.0)


# --- polygons ----------------------------------------------------------------


def test_polygon_with_length_becomes_prism():
    specs, _ = build_shape_specs(
        [
            {
                "kind": "polygon",
                "name": "p",
                "axis": "z",
                "coord": 0.5,
                "length": 1.5,
                "points": [[0, 0], [10, 0], [5, 10]],
            }
        ]
    )
    spec = specs[0]
    assert spec["kind"] == "prism"
    assert spec["length"] == pytest.approx(1.5)
    assert spec["coord"] == pytest.approx(0.5)
    flat = [c for pt in spec["points"] for c in pt]
    assert flat == pytest.approx([0.0, 0.0, 10.0, 0.0, 5.0, 10.0])


def test_polygon_without_length_becomes_sheet():
    specs, _ = build_shape_specs(
        [
            {
                "kind": "polygon",
                "name": "p",
                "axis": "y",
                "coord": 2.0,
                "length": 0,
                "points": [[0, 0], [4, 0], [4, 4]],
            }
        ]
    )
    assert specs[0]["kind"] == "sheet"
    assert specs[0]["axis"] == "y"


def test_polygon_needs_three_points():
    specs, warns = build_shape_specs(
        [
            {
                "kind": "polygon",
                "name": "p",
                "axis": "z",
                "coord": 0,
                "length": 0,
                "points": [[0, 0], [1, 1]],
            }
        ]
    )
    assert specs == []
    assert any("3 points" in w for w in warns)


def test_unknown_polygon_axis_is_skipped():
    specs, warns = build_shape_specs(
        [
            {
                "kind": "polygon",
                "name": "p",
                "axis": "q",
                "coord": 0,
                "length": 0,
                "points": [[0, 0], [1, 0], [0, 1]],
            }
        ]
    )
    assert specs == []
    assert any("axis" in w for w in warns)


# --- meshes ------------------------------------------------------------------


def _write_cube_stl(path):
    v = [
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
    ]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    tris = []
    for a, b, c, d in quads:
        tris.append((v[a], v[b], v[c]))
        tris.append((v[a], v[c], v[d]))
    from app.csxcad_builder.triangle_mesh import write_binary_stl

    write_binary_stl(path, np.asarray(tris, dtype=float))
    return path


def test_mesh_primitive_writes_scaled_stl(tmp_path):
    stl = _write_cube_stl(tmp_path / "cube.stl")
    specs, warns = build_shape_specs(
        [{"kind": "stl", "name": "m", "path": str(stl), "stl_path": str(stl)}],
        unit="cm",
        mesh_dir=tmp_path / "out",
    )
    assert warns == []
    assert specs[0]["kind"] == "mesh"
    from app.freecad_io.freecad_mesh import load_stl_or_obj_triangles

    written = load_stl_or_obj_triangles(specs[0]["stl"])
    # 1 drawing unit = 10 mm -> the unit cube is now 10 mm across
    assert written.reshape(-1, 3).max(axis=0) == pytest.approx([10.0, 10.0, 10.0])


def test_missing_mesh_is_skipped(tmp_path):
    specs, warns = build_shape_specs(
        [{"kind": "stl", "name": "gone", "path": str(tmp_path / "nope.stl")}],
        mesh_dir=tmp_path,
    )
    assert specs == []
    assert any("gone" in w for w in warns)


def test_mesh_without_dir_is_skipped(tmp_path):
    stl = _write_cube_stl(tmp_path / "cube.stl")
    specs, warns = build_shape_specs(
        [{"kind": "stl", "name": "m", "path": str(stl), "stl_path": str(stl)}],
        mesh_dir=None,
    )
    assert specs == []
    assert any("mesh directory" in w for w in warns)


# --- misc --------------------------------------------------------------------


def test_unknown_kind_is_skipped():
    specs, warns = build_shape_specs([{"kind": "torus", "name": "t"}])
    assert specs == []
    assert any("torus" in w for w in warns)


def test_empty_input():
    assert build_shape_specs(None) == ([], [])
    assert build_shape_specs([]) == ([], [])


def test_names_fall_back_to_index():
    specs, _ = build_shape_specs([{"kind": "sphere", "center": [0, 0, 0], "radius": 1}])
    assert specs[0]["name"] == "shape1"
