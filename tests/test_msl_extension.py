"""MSL zero-length port extension, including the domain-edge float32 case."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.simulation.msl import extend_zero_propagation


def test_msl_port_at_lower_edge_extends_inward():
    start = [1.0, -5.08, 0.0]
    stop = [2.0, -5.08, 1.0]
    lines = [-5.08, -3.65, -0.5, 0.0, 2.0]
    s, p, widened = extend_zero_propagation(start, stop, "y", lines)
    assert widened
    assert s == start
    assert p[1] == -3.65


def test_msl_port_at_upper_edge_uses_previous_line_not_float32_duplicate():
    # The CST/port value is 19.284188 while the snapped grid line is 19.2842.
    # The latter must not count as a real extension (zero-length strip).
    start = [1.0, 19.284188, 0.0]
    stop = [2.0, 19.284188, 1.0]
    lines = [-5.08, -3.65, 18.014, 18.7727, 19.2842]
    s, p, widened = extend_zero_propagation(start, stop, "y", lines)
    assert widened
    assert s == start
    assert p[1] == 18.7727


def test_upper_port_with_air_margin_still_extends_inward():
    # With a margin the next grid line is OUTSIDE the PCB; the port must grow
    # toward the domain centre, not into the air region.
    start = [1.0, 19.284188, 0.0]
    stop = [2.0, 19.284188, 1.0]
    lines = [-6.51, 18.014, 18.7727, 19.2842, 19.5059, 20.5542]
    s, p, widened = extend_zero_propagation(start, stop, "y", lines)
    assert widened
    assert s == start
    assert p[1] == 18.7727


def test_reversed_upper_port_extends_start_so_swap_keeps_plane_at_edge():
    # PortDef.oriented() swaps start/stop afterwards.  For a reversed port the
    # cell must therefore be added to start; otherwise the excitation plane
    # ends up inside the line and the propagation sense points outwards.
    start = [1.0, 19.284188, 1.0]
    stop = [2.0, 19.284188, 0.0]
    lines = [-6.51, 18.014, 18.7727, 19.2842, 19.5059, 20.5542]
    s, p, widened = extend_zero_propagation(
        start, stop, "y", lines, reverse=True
    )
    assert widened
    assert s[1] == 18.7727
    assert p == stop


def test_msl_port_with_no_usable_line_is_left_alone():
    start = [1.0, 19.284188, 0.0]
    stop = [2.0, 19.284188, 1.0]
    assert extend_zero_propagation(start, stop, "y", [19.2842])[2] is False
