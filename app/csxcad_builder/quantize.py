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
