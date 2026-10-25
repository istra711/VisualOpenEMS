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


def f32_array(values) -> np.ndarray:
    """float32-aligned copy of an array (float64 container, float32 values)."""
    return np.asarray(values, dtype=float).astype(np.float32).astype(float)


#: Decimal step of the *legacy* triangle-mesh rounding: 0.1 um in mm drawings.
LEGACY_TRIANGLE_DECIMALS = 4


def align_coordinates(
    values, *, aligned: bool = True, decimals: int = LEGACY_TRIANGLE_DECIMALS
):
    """Put triangle-mesh coordinates on the grid every other source uses.

    ``aligned=True`` (default) casts to float32 — exactly what :func:`f32` does
    for boxes, ports, dumps and the NF2FF box — so a polyhedron vertex and a box
    face that are meant to be equal end up **bit-identical** (no second mesh
    line, no 0.1 um offset).

    ``aligned=False`` restores the legacy behaviour: a decimal round to
    ``10**-decimals`` (0.1 um for mm drawings). That was the fix for the
    original sliver-cell problem ("STL float32 vertex 1.6000000238 next to the
    float64 box 1.6") *before* the boxes went through ``f32()`` as well; it is
    now redundant and leaves that 0.1 um offset between the sources.
    """
    arr = np.asarray(values, dtype=float)
    if aligned:
        return arr.astype(np.float32).astype(float)
    return np.round(arr, decimals)


def ulp_tolerance(values, factor: float = 8.0) -> float:
    """Tolerance that spans float32 arithmetic noise at the largest |value|.

    Floor for the grid-line dedupe: two lines that differ only by float32
    rounding/arithmetic must merge even where the ``frac * cell`` rule stays
    below that noise (i.e. when the following cell is smaller than the float32
    resolution — exactly the case that collapses the Courant timestep).
    """
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return 0.0
    biggest = float(np.max(np.abs(arr)))
    if biggest <= 0.0:
        return 0.0
    return float(factor) * float(np.spacing(np.float32(biggest)))


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
