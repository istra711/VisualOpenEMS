"""Pure tests for the lumped-port width (line / metal-wide sheet) helpers."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def test_line_collapses_transverse_axes_but_keeps_direction():
    from app.models.port_width import rebuild_box

    start, stop = rebuild_box(
        [-50.4025, 2.7629, 1.651], [-50.4025, 2.7629, 0.0], "z", "line"
    )
    assert start == [-50.4025, 2.7629, 1.651]
    assert stop == [-50.4025, 2.7629, 0.0]

    start, stop = rebuild_box([1.0, -3.0, 2.0], [5.0, 3.0, 9.0], "z", "line")
    assert start == [3.0, 0.0, 2.0]
    assert stop == [3.0, 0.0, 9.0]


def test_area_mode_widens_the_axis_and_keeps_a_zero_thickness_sheet():
    from app.models.port_width import rebuild_box

    start, stop = rebuild_box(
        [-50.4025, 2.7629, 1.651],
        [-50.4025, 2.7629, 0.0],
        "z",
        "y",
        (-1.0, 7.0),
    )
    assert start == [-50.4025, -1.0, 1.651]
    assert stop == [-50.4025, 7.0, 0.0]

    # reversed span input and a width along x work the same
    start, stop = rebuild_box(
        [2.0, 5.0, 1.0], [2.0, 5.0, 0.0], "z", "x", (10.0, -10.0)
    )
    assert start == [-10.0, 5.0, 1.0]
    assert stop == [10.0, 5.0, 0.0]


def test_degenerate_or_missing_metal_span_leaves_the_box():
    from app.models.port_width import rebuild_box

    assert rebuild_box([0, 2, 1], [0, 2, 0], "z", "y") is None
    assert rebuild_box([0, 2, 1], [0, 2, 0], "z", "y", (2.0, 2.0)) is None
    assert rebuild_box([0, 2, 1], [0, 2, 0], "z", "y", (float("nan"), 1.0)) is None


def test_width_along_the_excitation_direction_falls_back_to_a_line():
    from app.models.port_width import normalize_mode, rebuild_box

    assert normalize_mode("z", "z") == "line"
    assert normalize_mode("Y", "z") == "y"
    assert normalize_mode("", "z") == "line"
    assert normalize_mode("sideways", "z") == "line"

    start, stop = rebuild_box([1, -3, 2], [1, 3, 9], "z", "z")
    assert start == [1, 0, 2] and stop == [1, 0, 9]


def test_metal_name_at_port_prefers_the_trace_over_the_ground_plane():
    from app.models.port_width import metal_name_at_port

    primitives = [
        {
            "name": "gnd",
            "material": "copper",
            "start": [-60.0, -10.0, 0.0],
            "stop": [10.0, 10.0, 0.035],
        },
        {
            "name": "strip",
            "material": "copper",
            "start": [-60.0, -1.0, 1.651],
            "stop": [10.0, 7.0, 1.686],
        },
        {
            "name": "substrate",
            "material": "substrate",
            "start": [-60.0, -10.0, 0.0],
            "stop": [10.0, 10.0, 1.651],
        },
    ]
    metal_names = {"gnd", "strip"}
    assert (
        metal_name_at_port(
            primitives,
            metal_names,
            [-50.4025, 2.7629, 1.651],
            [-50.4025, 2.7629, 0.0],
            "z",
            "y",
        )
        == "strip"
    )

    # A port whose Start is off the metal end still falls back to the
    # x/y footprint (the trace's footprint is smaller than the ground's).
    assert (
        metal_name_at_port(
            primitives,
            metal_names,
            [0.0, 2.0, 2.0],
            [0.0, 2.0, 1.9],
            "z",
            "y",
        )
        == "strip"
    )

    # Midpoint fallback: the port starts above a metal volume.
    midpoint_case = [
        {"name": "pad", "material": "copper", "start": [0.0, 4.0, 0.0], "stop": [2.0, 8.0, 2.0]}
    ]
    assert (
        metal_name_at_port(
            midpoint_case,
            {"pad"},
            [1.0, 6.0, 2.5],
            [1.0, 6.0, 1.0],
            "z",
            "y",
        )
        == "pad"
    )
    # No metal at all -> empty, the caller keeps the hand-entered box.
    assert (
        metal_name_at_port(
            primitives, set(), [0.0, 0.0, 0.0], [0.0, 0.0, 1.0], "z", "y"
        )
        == ""
    )


def test_metal_name_at_port_uses_object_attributes_too():
    from types import SimpleNamespace

    from app.models.port_width import metal_name_at_port

    prim = SimpleNamespace(
        name="pad", start=(0.0, 4.0, 0.0), stop=(2.0, 8.0, 0.2), material="pec"
    )
    assert (
        metal_name_at_port(
            [prim], ["pad"], [1.0, 6.0, 0.0], [1.0, 6.0, 0.2], "z", "y"
        )
        == "pad"
    )
