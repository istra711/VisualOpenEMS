"""Mesh timing / feasibility diagnostics (app.meshing.timing)."""

from __future__ import annotations

import numpy as np
import pytest

from app.meshing.timing import (
    CELLS_PER_LAMBDA_MIN,
    analyse_lines,
    courant_timestep,
    report_lines,
    summary_line,
)

C0 = 299792458.0

# Grid in mm: one 0.1 mm cell in x, 1 mm cells elsewhere.
X = np.array([0.0, 0.1, 1.1, 2.1])
Y = np.array([0.0, 1.0, 2.0])
Z = np.array([0.0, 1.0])
LINES = {"x": X, "y": Y, "z": Z}


def test_courant_timestep_is_the_cfl_formula() -> None:
    dt = courant_timestep(LINES, 0.001)
    expected = 0.001 / (C0 * np.sqrt(1 / 0.1**2 + 1 / 1.0**2 + 1 / 1.0**2))
    assert dt == pytest.approx(expected)


def test_cell_sizes_are_converted_to_metres() -> None:
    rep = analyse_lines(LINES, unit_m=0.001, f_max_hz=3e9)
    assert rep.axes["x"].min_cell_m == pytest.approx(0.1e-3)
    assert rep.axes["x"].max_cell_m == pytest.approx(1.0e-3)
    assert rep.axes["x"].median_cell_m == pytest.approx(1.0e-3)
    # position stays in project units (mm)
    assert rep.axes["x"].min_cell_at_units == pytest.approx(0.05)
    assert rep.cells_total == 3 * 2 * 1


def test_dielectric_reduces_the_timestep_bound() -> None:
    air = analyse_lines(LINES, unit_m=0.001, f_max_hz=3e9, eps_max=1.0)
    fr4 = analyse_lines(LINES, unit_m=0.001, f_max_hz=3e9, eps_max=10.2)
    assert fr4.dt_courant_s == pytest.approx(air.dt_courant_s)
    assert fr4.dt_dielectric_s == pytest.approx(air.dt_courant_s / np.sqrt(10.2))
    assert fr4.dt_dielectric_s < air.dt_dielectric_s


def test_tiny_cell_reports_a_runtime_penalty() -> None:
    rep = analyse_lines(LINES, unit_m=0.001, f_max_hz=3e9)
    assert rep.refinement_penalty == pytest.approx(10.0)
    assert rep.penalty_axis == "x"
    assert "tiny_cell_slows_run" in [i.code for i in rep.issues]


def test_under_resolved_mesh_is_an_error() -> None:
    rep = analyse_lines(LINES, unit_m=0.001, f_max_hz=100e9)  # 1 mm cells
    worst = min(rep.cells_per_lambda.values())
    assert worst < CELLS_PER_LAMBDA_MIN
    codes = [i.code for i in rep.issues]
    assert "cells_per_lambda_low" in codes
    assert rep.ok is False
    assert rep.errors


def test_window_limited_sweep_is_flagged() -> None:
    rep = analyse_lines(
        LINES,
        unit_m=0.001,
        f_max_hz=3e9,
        nr_ts=1000,
        sweep_points=201,
        sweep_span_hz=1.4e9,
    )
    assert rep.target_resolution_hz == pytest.approx(1.4e9 / 200)
    assert rep.resolution_best_hz > rep.target_resolution_hz
    codes = [i.code for i in rep.issues]
    assert "resolution_limited" in codes
    assert rep.nr_ts_for_resolution > 1000


def test_well_matched_mesh_has_no_findings() -> None:
    # 10 mm domain, 0.5 mm cells, modest timestep count, wide sweep steps
    grid = {
        "x": np.arange(0.0, 10.1, 0.5),
        "y": np.arange(0.0, 10.1, 0.5),
        "z": np.arange(0.0, 2.1, 0.5),
    }
    rep = analyse_lines(
        grid,
        unit_m=0.001,
        f_max_hz=3e9,
        nr_ts=400_000,
        sweep_points=201,
        sweep_span_hz=1.4e9,
    )
    assert rep.ok
    assert rep.warnings == []


def test_text_renderers() -> None:
    rep = analyse_lines(LINES, unit_m=0.001, f_max_hz=3e9, nr_ts=1000,
                        sweep_span_hz=1.4e9)
    line = summary_line(rep)
    assert line.startswith("Mesh timing:")
    assert "cells/lambda" in line
    body = "\n".join(report_lines(rep))
    assert "Courant limit dt" in body
    assert "cell size x" in body.lower()
    assert "Findings:" in body
