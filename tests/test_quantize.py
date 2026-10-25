"""Tests for the CSXCAD coordinate quantization helpers."""

from __future__ import annotations

import numpy as np
import pytest

from app.csxcad_builder.quantize import (
    SHEET_HALF_THICKNESS,
    align_coordinates,
    f32,
    f32_array,
    inflate_sheet_box,
    inflated_extents,
    ulp_tolerance,
    zero_extent_axes,
)


class _Box:
    """Minimal stand-in for CSPrimBox (Get/SetStart, Get/SetStop)."""

    def __init__(self, start, stop):
        self.start = list(start)
        self.stop = list(stop)

    def GetStart(self):
        return list(self.start)

    def GetStop(self):
        return list(self.stop)

    def SetStart(self, start):
        self.start = list(start)

    def SetStop(self, stop):
        self.stop = list(stop)


def test_zero_extent_axes():
    assert zero_extent_axes([0, 0, 1.6], [1, 1, 1.6]) == [2]
    assert zero_extent_axes([0, 0, 0], [1, 1, 0.25]) == []
    assert zero_extent_axes([1, 2, 3], [1, 4, 5]) == [0]


def test_inflate_sheet_box_only_widens_the_sheet_axis():
    box = _Box([-32, -1.5, f32(1.6)], [32, 1.5, f32(1.6)])
    axes = zero_extent_axes(box.GetStart(), box.GetStop())
    inflate_sheet_box(box, axes)

    start, stop = box.GetStart(), box.GetStop()
    assert stop[2] - start[2] == pytest.approx(2 * SHEET_HALF_THICKNESS)
    # the mesh line (float32 of 1.6) must be inside the slab
    assert start[2] < f32(1.6) < stop[2]
    assert start[:2] == [-32, -1.5]
    assert stop[:2] == [32, 1.5]


def test_inflated_extents_do_not_touch_other_axes():
    start, stop = inflated_extents([0, 0, 0], [1, 2, 0], [2])
    assert start == [0, 0, -SHEET_HALF_THICKNESS]
    assert stop == [1, 2, SHEET_HALF_THICKNESS]


# --- coordinate alignment (P1: one grid for every source) ------------------- #


def test_aligned_triangle_vertex_equals_a_box_coordinate():
    """The regression that motivated P1: 1.6 is not a float32 value."""
    aligned = float(align_coordinates([1.6])[0])
    assert aligned == f32(1.6)  # bit-identical to a box/port coordinate
    assert aligned != 1.6


def test_legacy_mode_rounds_decimally_and_leaves_an_offset():
    """Off = pre-P1 behaviour: 0.1 um decimal rounding."""
    vertex = 20.00006  # a tessellation vertex next to the box face at 20.0
    legacy = float(align_coordinates([vertex], aligned=False)[0])
    aligned = float(align_coordinates([vertex])[0])
    assert legacy == pytest.approx(20.0001)  # decimal rounding
    assert aligned == f32(vertex)  # the float32 grid boxes/ports use
    # the decimal step moves the vertex up to half a step (0.05 um) off its own
    # value - the offset that produced the "second grid line" in the field
    assert abs(legacy - vertex) == pytest.approx(4.0e-5, rel=0.05)


def test_align_coordinates_is_idempotent_and_keeps_a_float64_container():
    raw = np.array([1.6, 20.00006, -3.25])
    once = align_coordinates(raw)
    twice = align_coordinates(once)
    assert once.dtype == np.float64
    assert np.array_equal(once, twice)
    legacy = align_coordinates(raw, aligned=False)
    assert np.array_equal(legacy, align_coordinates(legacy, aligned=False))


def test_f32_array_matches_f32_per_element():
    raw = [1.6, 20.00006, 0.0, -12.3456789]
    assert list(f32_array(raw)) == [f32(v) for v in raw]


def test_ulp_tolerance_spans_float32_arithmetic_noise():
    assert ulp_tolerance([]) == 0.0
    assert ulp_tolerance([0.0, 0.0]) == 0.0
    tol20 = ulp_tolerance([0.0, 20.0])
    assert tol20 == pytest.approx(8 * float(np.spacing(np.float32(20.0))))
    # grows with the largest coordinate (float32 has a relative resolution)
    assert ulp_tolerance([0.0, 300.0]) > 10 * tol20
