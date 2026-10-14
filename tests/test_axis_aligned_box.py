"""Axis-aligned box meshes from FreeCAD are emitted as openEMS boxes."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def box_tris(lo, hi):
    lo = np.asarray(lo, dtype=float)
    hi = np.asarray(hi, dtype=float)
    corners = np.array([
        [lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]],
        [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]], [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]],
    ], dtype=float)
    faces = [
        (0, 1, 2), (0, 2, 3),
        (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1),
        (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3),
        (3, 7, 4), (3, 4, 0),
    ]
    return np.array([[corners[i], corners[j], corners[k]] for i, j, k in faces], dtype=float)


def wedge_tris():
    """Triangular prism (ridge along x) — never an axis-aligned box."""
    p0 = [-5.0, -3.0, 0.0]
    p1 = [5.0, -3.0, 0.0]
    p2 = [5.0, 3.0, 0.0]
    p3 = [-5.0, 3.0, 0.0]
    q0 = [-5.0, 0.0, 2.0]
    q1 = [5.0, 0.0, 2.0]
    return np.array([
        [p0, p2, p1],
        [p0, p3, p2],
        [p0, p1, q1],
        [p0, q1, q0],
        [p3, p2, q1],
        [p3, q1, q0],
        [p0, p3, q0],
        [p1, q1, p2],
    ], dtype=float)


def test_bounds_of_box_mesh():
    from app.csxcad_builder.triangle_mesh import axis_aligned_box_bounds

    bounds = axis_aligned_box_bounds(box_tris([-32, -1.5, 1.6], [32, 1.5, 1.635]))
    assert bounds is not None
    lo, hi = bounds
    assert np.allclose(lo, [-32, -1.5, 1.6])
    assert np.allclose(hi, [32, 1.5, 1.635])


def test_non_box_mesh_is_rejected():
    from app.csxcad_builder.triangle_mesh import axis_aligned_box_bounds

    assert axis_aligned_box_bounds(wedge_tris()) is None
    assert axis_aligned_box_bounds(np.zeros((0, 3, 3))) is None
    # degenerate (zero thickness) mesh stays a mesh, never a zero-height box
    flat = box_tris([0, 0, 1.6], [10, 4, 1.6])
    assert axis_aligned_box_bounds(flat) is None


def test_add_triangle_mesh_emits_box_for_axis_aligned_mesh():
    from CSXCAD import ContinuousStructure

    from app.csxcad_builder.triangle_mesh import add_triangle_mesh

    csx = ContinuousStructure()
    prop = csx.AddMetal("copper")
    prim = add_triangle_mesh(prop, name="line", triangles=box_tris([-2, -1, 0], [2, 1, 0.5]),
                             priority=7)
    assert type(prim).__name__ == "CSPrimBox"
    box = prim.GetBoundBox()
    assert np.allclose(box[0], [-2, -1, 0], atol=1e-6)
    assert np.allclose(box[1], [2, 1, 0.5], atol=1e-6)


def test_add_triangle_mesh_keeps_stl_for_non_box(tmp_path):
    from CSXCAD import ContinuousStructure

    from app.csxcad_builder.triangle_mesh import add_triangle_mesh, write_binary_stl

    stl = tmp_path / "wedge.stl"
    write_binary_stl(stl, wedge_tris())
    csx = ContinuousStructure()
    prop = csx.AddMetal("copper")
    prim = add_triangle_mesh(prop, name="wedge", stl_path=stl, priority=10)
    assert "Polyhedron" in type(prim).__name__
    assert stl.exists()
