"""Post-run status report (app.simulation.report)."""

from __future__ import annotations

import numpy as np
import pytest

from app.simulation.report import (
    build_report,
    compute_match_quality,
    compute_power_balance,
    parse_engine_log,
    render,
    scan_log,
)

LOG_NOT_CONVERGED = """\
Create FDTD operator (compressed SSE + multi-threading)
Size of Operator\t: 3737664 Byte (3.56451 MiB)
Size of Field-Data\t: 1868832 Byte (1.78226 MiB)
Timestep (s)\t\t: 2.29369e-13
Timestep method name\t: Rennings_2
Nyquist criteria (TS)\t: 682
Excitation Length (TS)\t: 15771
FDTD simulation size: 54x103x14 --> 77868 FDTD cells
FDTD timestep is: 2.29369e-13 s; Nyquist rate: 682 timesteps @3.19633e+09 Hz
Excitation signal length is: 15771 timesteps (3.61737e-09s)
Max. number of timesteps: 120000 ( --> 7.6089 * Excitation signal length)
Capacity: -nan(ind)
Inductance: -nan(ind)
[WARNING] grading_jump: Axis x: worst neighbor grading 5.58 > 2.0
[@        4s] Timestep:         2890 || Speed:   56.1 MC/s (1.389e-03 s/TS) || Energy: ~8.23e-18 (- 0.00dB)
[@       57s] Timestep:       119340 || Speed:   61.0 MC/s (1.000e-03 s/TS) || Energy: ~1.00e-15 (-16.3dB)
RunFDTD: Warning: Max. number of timesteps was reached before the end-criteria of -50dB was reached...
"""

LOG_CONVERGED = """\
Timestep (s)\t\t: 5.00e-13
FDTD timestep is: 5.00e-13 s; Nyquist rate: 313 timesteps @3.19633e+09 Hz
Excitation signal length is: 5000 timesteps (2.5e-09s)
Max. number of timesteps: 50000 ( --> 10.0 * Excitation signal length)
[@        9s] Timestep:        45000 || Speed:   70.0 MC/s (1.000e-03 s/TS) || Energy: ~1.00e-20 (- 62.0dB)
RunFDTD: end-criteria of -50dB was reached after 45000 timesteps.
"""


def _sparams(*, s11_db: list[float], s21_db: list[float], f0=1.6e9, f1=3.2e9):
    freq = np.linspace(f0, f1, len(s11_db))
    return {
        "freq": freq,
        "S11": {"real": [0.0] * len(freq), "imag": [0.0] * len(freq), "db": s11_db},
        "S21": {"real": [0.0] * len(freq), "imag": [0.0] * len(freq), "db": s21_db},
    }


def test_parse_engine_facts() -> None:
    f = parse_engine_log(LOG_NOT_CONVERGED)
    assert f.timestep_s == pytest.approx(2.29369e-13)
    assert f.timestep_method == "Rennings_2"
    assert f.nyquist_ts == 682
    assert f.nyquist_f_hz == pytest.approx(3.19633e9)
    assert f.excitation_ts == 15771
    assert f.nr_ts == 120000
    assert f.nr_ts_vs_excitation == pytest.approx(7.6089)
    assert f.cells_total == 77868
    assert f.operator_bytes == 3737664
    assert f.steps == 119340
    assert f.energy_db == pytest.approx(-16.3)
    assert f.speed_mcs == pytest.approx(61.0)
    assert f.elapsed_s == 57
    assert f.max_ts_reached is True
    assert f.end_criteria_hit is False


def test_converged_run_is_recognised() -> None:
    f = parse_engine_log(LOG_CONVERGED)
    assert f.end_criteria_hit is True
    assert f.end_criteria_db == pytest.approx(-50.0)
    assert f.max_ts_reached is False
    assert f.steps == 45000


def test_scan_log_filters_benign_nan_and_collects_warnings() -> None:
    findings = scan_log(LOG_NOT_CONVERGED)
    codes = [f.code for f in findings]
    messages = " ".join(f.message for f in findings)
    assert "Capacity" not in messages  # benign -nan(ind) port lines
    assert "not_converged" in codes
    assert "mesh_grading_jump" in codes
    assert "[pre-run mesh]" in messages
    assert all(f.severity != "error" for f in findings)


def test_scan_log_reports_errors_and_tracebacks() -> None:
    text = "Traceback (most recent call last):\nValueError: bad\nFATAL: access violation\n"
    findings = scan_log(text)
    codes = {f.code for f in findings}
    assert "traceback" in codes
    assert "crash" in codes
    assert any(f.severity == "error" for f in findings)


def test_power_balance_flags_core_violation() -> None:
    s11 = [-3.0] * 11
    s21 = [-60.0] * 11
    s21[5] = -2.0  # middle of the sweep -> passivity violation in the core
    pb = compute_power_balance(_sparams(s11_db=s11, s21_db=s21))
    assert pb is not None
    assert pb.max_core > 1.0
    assert pb.violations_core == 1
    assert pb.max_core_f_hz == pytest.approx(1.6e9 + 5 * (3.2e9 - 1.6e9) / 10)


def test_power_balance_edge_violation_is_separated() -> None:
    s11 = [-3.0] * 11
    s21 = [-60.0] * 11
    s21[0] = 0.0  # first point only -> edge artefact (|S11|^2+|S21|^2 = 1.5)
    pb = compute_power_balance(_sparams(s11_db=s11, s21_db=s21))
    assert pb is not None
    assert pb.max_all > 1.0
    assert pb.violations_core == 0
    assert pb.violations_edge == 1


def test_report_verdict_warns_when_not_converged() -> None:
    sp = _sparams(s11_db=[-20.0] * 11, s21_db=[-3.0] * 11)
    rep = build_report(
        log_text=LOG_NOT_CONVERGED,
        exit_code=0,
        sparams=sp,
        band_hz=(1.6e9, 3.2e9),
        target_hz=2.4e9,
        end_criteria=1e-5,
    )
    assert rep.verdict == "warning"
    assert rep.converged is False
    assert "not converged" in rep.headline
    assert rep.dft_window_s > 0
    text = render(rep)
    assert "SIMULATION STATUS" in text
    assert "not_converged" in text
    assert "passivity" in text


def test_report_ok_for_converged_passive_run() -> None:
    sp = _sparams(s11_db=[-25.0] * 11, s21_db=[-2.0] * 11)
    rep = build_report(
        log_text=LOG_CONVERGED,
        exit_code=0,
        sparams=sp,
        band_hz=(1.6e9, 3.2e9),
        target_hz=2.4e9,
        end_criteria=1e-5,
    )
    assert rep.converged is True
    assert rep.verdict == "ok"
    assert rep.trust == "reliable"
    assert rep.balance is not None and rep.balance.violations_core == 0


def test_report_failed_run_without_results() -> None:
    rep = build_report(log_text="", exit_code=1, sparams=None)
    assert rep.verdict == "error"
    assert rep.trust == "unusable"
    text = render(rep)
    assert "exit" in text and "none written" in text


def test_complex_array_input_is_converted_to_db() -> None:
    """The app's loader returns complex arrays, not dB dicts."""
    freq = np.linspace(1.6e9, 3.2e9, 5)
    s11 = np.full(5, 10 ** (-20 / 20), dtype=complex)
    s21 = np.full(5, 10 ** (-3 / 20), dtype=complex)
    sp = {"freq": freq, "S11": s11, "S21": s21}
    q = compute_match_quality(sp, target_hz=2.4e9)
    assert q is not None and q.s11_min_db == pytest.approx(-20.0)
    pb = compute_power_balance(sp)
    assert pb is not None
    assert pb.max_core == pytest.approx(10 ** -2.0 + 10 ** -0.3, rel=1e-6)


def test_report_mentions_mesh_timing_cross_check() -> None:
    class _Timing:
        dt_courant_s = 3.24669e-13

    sp = _sparams(s11_db=[-25.0] * 11, s21_db=[-2.0] * 11)
    rep = build_report(
        log_text=LOG_CONVERGED,
        exit_code=0,
        sparams=sp,
        timing=_Timing(),
    )
    codes = [f.code for f in rep.findings]
    assert "timestep_check" in codes
    assert "71%" in render(rep) or "%" in render(rep)
