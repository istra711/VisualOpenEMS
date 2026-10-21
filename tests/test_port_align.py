"""Unit tests for port_align (port coordinates -> mesh lines, no GUI)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def test_required_coordinates_skips_inactive_and_dedupes():
    from app.meshing.port_align import required_coordinates
    from app.models.project import PortDef

    p1 = PortDef(name="a", start=[0.0, 1.0, 2.0], stop=[0.0, 1.0, 5.0])
    p2 = PortDef(name="b", start=[0.5, 1.0, 2.0], stop=[0.5, 1.0, 5.0])
    off = PortDef(name="c", active=False, start=[9.0, 9.0, 9.0], stop=[9.0, 9.0, 9.0])
    req = required_coordinates([p1, p2, off])
    assert req["x"] == [0.0, 0.5]
    assert req["y"] == [1.0]
    assert req["z"] == [2.0, 5.0]


def test_exact_coordinates_leave_the_grid_alone():
    from app.meshing.port_align import align_axis

    lines = np.array([0.0, 1.0, 2.0, 3.0])
    out, stats = align_axis(lines, [1.0, 2.0])
    assert np.allclose(out, lines)
    assert stats == {"moved": [], "added": [], "snapped": []}


def test_line_close_to_the_port_is_moved_instead_of_split():
    """The real pcb_notch_ports case: y=0.0987 next to an interpolated 0.0882."""
    from app.meshing.port_align import align_axis

    lines = np.array([-0.0882, 0.0882, 0.2647])
    out, stats = align_axis(lines, [0.0987])
    assert 0.0987 in list(out)
    assert 0.0882 not in list(out)
    # moving keeps cell sizes sane — no 10 µm cell next to a 0.18 mm cell
    assert np.allclose(np.diff(out), [0.1869, 0.166], atol=1e-9)
    assert stats["moved"] == [(0.0882, 0.0987)]
    assert stats["added"] == []


def test_far_coordinate_gets_an_own_line():
    from app.meshing.port_align import align_axis

    out, stats = align_axis(np.array([0.0, 1.0, 2.0]), [0.5])
    assert list(out) == [0.0, 0.5, 1.0, 2.0]
    assert stats["added"] == [0.5]
    assert stats["moved"] == []


def test_geometry_line_is_never_moved():
    from app.meshing.port_align import align_axis

    out, stats = align_axis(np.array([0.0, 1.0, 2.0]), [1.05], protected=[1.0])
    # a metal face must stay put; the port is only snapped (5 % of a cell)
    assert list(out) == [0.0, 1.0, 2.0]
    assert stats["snapped"] == [(1.05, 1.0)]


def test_line_of_another_port_is_not_moved():
    from app.meshing.port_align import align_axis

    out, stats = align_axis(np.array([0.0, 1.0, 2.0]), [1.0, 1.05])
    assert list(out) == [0.0, 1.0, 2.0]  # no tiny cell, exact port 1 kept
    assert stats["snapped"] == [(1.05, 1.0)]


def test_coordinate_outside_the_domain_is_ignored():
    from app.meshing.port_align import align_axis

    out, stats = align_axis(np.array([0.0, 1.0, 2.0]), [5.0, -3.0])
    assert list(out) == [0.0, 1.0, 2.0]
    assert stats == {"moved": [], "added": [], "snapped": []}


def test_describe_stats():
    from app.meshing.port_align import describe_stats

    text = describe_stats(
        {
            "x": {"moved": [(1.0, 1.2)], "added": [], "snapped": []},
            "y": {"moved": [], "added": [0.5], "snapped": []},
        }
    )
    assert "x 1 moved" in text
    assert "y +1 line(s)" in text
    assert describe_stats({}) == ""


def test_ensure_port_lines_updates_the_csx_grid():
    from CSXCAD import ContinuousStructure
    from app.meshing.port_align import ensure_port_lines
    from app.models.project import PortDef

    csx = ContinuousStructure()
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)
    grid.SetLines("x", [0.0, 1.0, 2.0])
    grid.SetLines("y", [0.0, 0.0882, 0.2647, 1.0])
    grid.SetLines("z", [0.0, 1.0])
    port = PortDef(
        name="p", start=[0.5, 0.0987, 0.0], stop=[0.5, 0.0987, 1.0], direction="z"
    )
    stats = ensure_port_lines(csx, [port])
    y_lines = [round(float(v), 6) for v in grid.GetLines(1)]
    assert 0.0987 in y_lines and 0.0882 not in y_lines
    assert 0.5 in [round(float(v), 6) for v in grid.GetLines(0)]
    assert stats["y"]["moved"] == [(0.0882, 0.0987)]


def test_ensure_port_lines_keeps_manual_lines_and_ignores_inactive_ports():
    from CSXCAD import ContinuousStructure
    from app.meshing.port_align import ensure_port_lines
    from app.models.project import PortDef

    csx = ContinuousStructure()
    grid = csx.GetGrid()
    grid.SetDeltaUnit(1e-3)
    grid.SetLines("x", [0.0, 1.0, 2.0])
    grid.SetLines("y", [0.0, 0.0882, 0.2647, 1.0])
    grid.SetLines("z", [0.0, 1.0])
    port = PortDef(
        name="p",
        start=[1.0, 0.0987, 0.0],
        stop=[1.0, 0.0987, 1.0],
        direction="z",
        active=False,
    )
    assert ensure_port_lines(csx, [port]) == {}
    assert [round(float(v), 6) for v in grid.GetLines(1)] == [0.0, 0.0882, 0.2647, 1.0]

    port.active = True
    stats = ensure_port_lines(csx, [port], extra_protected={"y": [0.0882]})
    assert [round(float(v), 6) for v in grid.GetLines(1)] == [0.0, 0.0882, 0.2647, 1.0]
    assert stats["y"]["snapped"] == [(0.0987, 0.0882)]


def main() -> int:
    tests = [
        test_required_coordinates_skips_inactive_and_dedupes,
        test_exact_coordinates_leave_the_grid_alone,
        test_line_close_to_the_port_is_moved_instead_of_split,
        test_far_coordinate_gets_an_own_line,
        test_geometry_line_is_never_moved,
        test_line_of_another_port_is_not_moved,
        test_coordinate_outside_the_domain_is_ignored,
        test_describe_stats,
        test_ensure_port_lines_updates_the_csx_grid,
        test_ensure_port_lines_keeps_manual_lines_and_ignores_inactive_ports,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print("Port-align tests:", "PASS" if failed == 0 else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
