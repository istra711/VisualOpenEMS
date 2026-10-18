"""Parse openEMS engine progress lines (app.simulation.progress)."""

from __future__ import annotations

from app.simulation.progress import EngineProgress

_MAX_LINE = "Max. number of timesteps: 200000 ( --> 19.8669 * Excitation signal length)"
_STEP_LINE = (
    "[@        4s] Timestep:          888 || Speed:   35.6 MC/s (4.618e-03 s/TS) "
    "|| Energy: ~6.74e-19 (- 0.00dB)"
)
_MID_LINE = (
    "[@     10m31s] Timestep:       173900 || Speed:   45.8 MC/s (3.593e-03 s/TS) "
    "|| Energy: ~5.36e-15 (-25.91dB)"
)
_WARN_LINE = (
    "RunFDTD: Warning: Max. number of timesteps was reached before the "
    "end-criteria of -50dB was reached... "
)
_OK_LINE = "RunFDTD: end-criteria of -50dB was reached after 1234 timesteps."


def test_ignores_unrelated_lines():
    p = EngineProgress()
    assert p.feed("FDTD timestep is: 5.69e-13 s; Nyquist rate: 297 timesteps") is False
    assert p.feed("") is False
    assert p.active is False
    assert p.percent == 0


def test_max_timesteps_and_step_line():
    p = EngineProgress()
    assert p.feed(_MAX_LINE) is True
    assert p.max_timesteps == 200000
    assert p.feed(_STEP_LINE) is True
    assert p.timestep == 888
    assert p.energy_db == 0.0
    assert p.elapsed == "4s"
    assert p.percent == 0  # 888 / 200000


def test_mid_run_percent_and_text():
    p = EngineProgress()
    p.feed(_MAX_LINE)
    p.feed(_MID_LINE)
    assert p.timestep == 173900
    assert p.energy_db == -25.91
    assert p.elapsed == "10m31s"
    assert p.percent == 87  # 173900 / 200000 = 86.95 -> 87
    text = p.text()
    assert "TS 173900/200000" in text
    assert "Energy -25.9 dB" in text
    assert "@10m31s" in text


def test_repeated_line_is_not_a_change():
    p = EngineProgress()
    p.feed(_MAX_LINE)
    assert p.feed(_MID_LINE) is True
    assert p.feed(_MID_LINE) is False


def test_multi_run_prefix_still_parses():
    p = EngineProgress()
    p.feed("[in1] " + _MAX_LINE)
    p.feed("[in1] " + _STEP_LINE)
    assert p.max_timesteps == 200000
    assert p.timestep == 888


def test_end_criteria_warning_is_not_converged():
    p = EngineProgress()
    p.feed(_MAX_LINE)
    p.feed(_MID_LINE)
    assert p.feed(_WARN_LINE) is True
    assert p.stopped_before_criteria is True
    assert p.end_criteria_hit is False
    assert "max TS reached" in p.text()


def test_end_criteria_reached_is_converged():
    p = EngineProgress()
    p.feed(_MAX_LINE)
    p.feed(_OK_LINE)
    assert p.end_criteria_hit is True
    assert p.stopped_before_criteria is False
    assert p.percent == 100
    assert "converged" in p.text()


def test_energy_percent_tracks_the_criterion():
    p = EngineProgress()
    p.target_db = 50.0  # -50 dB end criterion
    p.feed(_MAX_LINE)
    p.feed(_STEP_LINE)  # 0 dB
    assert p.energy_percent == 0
    p.feed(_MID_LINE)  # -25.91 dB -> ~52 %
    assert p.energy_percent == 52
    p.feed(_OK_LINE)  # end criteria reached
    assert p.energy_percent == 100


def test_energy_percent_without_target_is_zero():
    p = EngineProgress()
    p.feed(_MID_LINE)
    assert p.energy_db == -25.91
    assert p.target_db is None
    assert p.energy_percent == 0
    assert p.energy_text() == "Energy -26 dB"


def test_energy_text_with_target_is_clamped_example():
    p = EngineProgress()
    p.target_db = 50.0
    p.feed(_MID_LINE)
    assert p.energy_text() == "Energy -26 / -50 dB"


def test_reset_clears_state():
    p = EngineProgress()
    p.feed(_MAX_LINE)
    p.feed(_OK_LINE)
    p.reset()
    assert p.max_timesteps is None
    assert p.timestep == 0
    assert p.energy_db is None
    assert p.end_criteria_hit is False
    assert p.active is False
    assert p.percent == 0
