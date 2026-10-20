"""Coordinate quantization helpers for CSXCAD.

CSXCAD stores polyhedron vertices (FreeCAD triangle meshes) as float32 but
boxes, ports and dumps as float64. Mixing both on the same coordinate leaves
two mesh lines a few 1e-8 apart (1.6 vs 1.6000000238), which easyMesh reports
as grading_jump / very_small_cell warnings. Quantizing every CSX coordinate to
float32 keeps all geometry sources on one grid.
"""

from __future__ import annotations

import numpy as np


def f32(v) -> float:
    """Round a coordinate to the nearest float32 value (as float)."""
    return float(np.float32(float(v)))


def f32_list(values) -> list[float]:
    """Round a sequence of coordinates to float32 values."""
    return [f32(x) for x in values]


#: Half thickness (drawing units, normally mm) given to a zero-thickness metal
#: sheet *after* meshing. A PEC sheet must contain the grid plane exactly:
#: float32 quantization (1.6 -> 1.6000000238) plus double-precision mesh
#: arithmetic can leave the plane 1e-15 below the line, and CSXCAD then
#: silently drops the primitive ("Warning: Unused primitive" — the metal
#: disappears). 1 nm is far below any physical feature and still hits only the
#: one grid plane it models.
SHEET_HALF_THICKNESS = 1e-6


def zero_extent_axes(start, stop) -> list[int]:
    """Indices of the axes in which a (quantized) box has zero thickness."""
    return [i for i in range(3) if f32(start[i]) == f32(stop[i])]


def inflated_extents(start, stop, axes, delta: float = SHEET_HALF_THICKNESS):
    """Widen ``start``/``stop`` by ``delta`` along ``axes`` (other axes as-is)."""
    s = list(start)
    e = list(stop)
    for i in axes:
        s[i] -= delta
        e[i] += delta
    return s, e


def inflate_sheet_box(box, axes, delta: float = SHEET_HALF_THICKNESS) -> None:
    """Give a zero-thickness CSXCAD metal box a tiny thickness.

    Call this **after** the mesh has been generated: the mesher must see the
    authored (zero-thickness) geometry, otherwise the two new faces 2 nm apart
    show up as two metal interfaces and perturb the grid.
    """
    start, stop = inflated_extents(box.GetStart(), box.GetStop(), axes, delta)
    box.SetStart(start)
    box.SetStop(stop)
