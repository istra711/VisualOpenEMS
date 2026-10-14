"""Smoke: frequency unit helpers + mesh form Hz/MHz/GHz entry."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.ui.freq_units import (
        FREQ_UNITS,
        FreqEdit,
        configure_freq_spin,
        fmt_hz,
        freq_to_hz,
        hz_to_freq,
        set_spin_hz,
        spin_hz,
        suggest_freq_unit,
    )
    from app.ui.sim_params import DEFAULT_PATCH_PARAMS

    app = QApplication.instance() or QApplication([])

    # conversion
    assert abs(freq_to_hz(2.45, "GHz") - 2.45e9) < 1e-3
    assert abs(hz_to_freq(2.45e9, "MHz") - 2450.0) < 1e-6
    assert suggest_freq_unit(2.45e9) == "GHz"
    assert suggest_freq_unit(100e6) == "MHz"
    assert abs(DEFAULT_PATCH_PARAMS["f0"] - 2.45e9) < 1e-3

    # FreqEdit
    fe = FreqEdit(2.45e9, unit="GHz")
    assert abs(fe.value_hz() - 2.45e9) < 1e-3
    fe.set_unit("MHz")
    assert fe.unit() == "MHz"
    assert abs(fe.value_hz() - 2.45e9) < 1e-3
    fe.set_hz(915e6)
    assert abs(fe.value_hz() - 915e6) < 1e-3
    fe.set_unit("Hz")
    assert abs(fe.value_hz() - 915e6) < 1e-3

    # spin helpers
    from PySide6.QtWidgets import QDoubleSpinBox

    sp = QDoubleSpinBox()
    configure_freq_spin(sp, "GHz")
    set_spin_hz(sp, 2.45e9, "GHz")
    assert abs(spin_hz(sp, "GHz") - 2.45e9) < 1e-3
    configure_freq_spin(sp, "MHz")
    set_spin_hz(sp, 2.45e9, "MHz")
    assert abs(spin_hz(sp, "MHz") - 2.45e9) < 1e-3

    # MainWindow mesh form (headless)
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    s = AppSettings.load()
    win = MainWindow(s)
    # default display GHz, values in Hz
    assert win._freq_unit() in FREQ_UNITS
    f0 = win._hz_from_spin(win.spn_f0)
    assert abs(f0 - 2.45e9) < 1e3, f0
    assert abs(win._hz_from_spin(win.spn_start_f) - 1.95e9) < 1e3
    assert abs(win._hz_from_spin(win.spn_stop_f) - 2.95e9) < 1e3
    # project may keep target_frequency=None until form sync
    win._sync_mesh_freqs_from_widgets("f0fc")
    assert win.project.mesh.target_frequency is not None
    assert abs(win.project.mesh.target_frequency - 2.45e9) < 1e3
    assert abs(win.project.mesh.start_frequency - 1.95e9) < 1e3

    # switch unit to MHz — values must stay the same in Hz
    win.cmb_freq_unit.setCurrentText("MHz")
    assert win._freq_unit() == "MHz"
    assert abs(win._hz_from_spin(win.spn_f0) - 2.45e9) < 1e3
    win.cmb_freq_unit.setCurrentText("GHz")
    assert abs(win._hz_from_spin(win.spn_f0) - 2.45e9) < 1e3

    # enter 915 MHz in MHz display → project storage in Hz
    win.cmb_freq_unit.setCurrentText("MHz")
    win.spn_f0.setValue(915.0)
    win.spn_fc.setValue(100.0)
    win._sync_mesh_freqs_from_widgets("f0fc")
    assert abs(win.project.mesh.target_frequency - 915e6) < 1e3
    assert abs(win.project.mesh.start_frequency - 815e6) < 1e3
    assert abs(win.project.mesh.stop_frequency - 1015e6) < 1e3

    # switch to Hz display: still 915e6
    win.cmb_freq_unit.setCurrentText("Hz")
    assert abs(win._hz_from_spin(win.spn_f0) - 915e6) < 1.0
    assert abs(win.spn_f0.value() - 915e6) < 1.0

    # GHz display: 0.915
    win.cmb_freq_unit.setCurrentText("GHz")
    assert abs(win._hz_from_spin(win.spn_f0) - 915e6) < 1.0
    assert abs(win.spn_f0.value() - 0.915) < 1e-6

    print("fmt:", fmt_hz(2.45e9), fmt_hz(915e6))
    print("FREQ UNIT SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
