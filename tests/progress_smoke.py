"""Smoke: FDTD engine progress bar in the status bar.

Feeds openEMS-style stdout lines through ``MainWindow._log`` and asserts the
progress bar + label appear and track the timestep budget. Standalone (offscreen).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_LINES = [
    "Max. number of timesteps: 200000 ( --> 19.8669 * Excitation signal length)",
    "[@        4s] Timestep:          888 || Speed:   35.6 MC/s (4.618e-03 s/TS) "
    "|| Energy: ~6.74e-19 (- 0.00dB)",
    "[@     10m31s] Timestep:       173900 || Speed:   45.8 MC/s (3.593e-03 s/TS) "
    "|| Energy: ~5.36e-15 (-25.91dB)",
]


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    settings = AppSettings.load()
    settings.work_dir = tempfile.mkdtemp(prefix="progress_smoke_")
    win = MainWindow(settings)

    # NB: isVisible() is False while the top-level window is not shown, so use
    # the explicit hidden flag (setVisible) instead.
    assert win.progress.isHidden(), "progress bar should start hidden"
    win._reset_engine_progress()
    assert win.progress.isHidden()

    for line in _LINES:
        win._log(line)
    win._drain_log_queue()

    assert not win.progress.isHidden(), "progress bar not shown after engine lines"
    assert win.progress.value() == 87, win.progress.value()
    assert not win.lbl_engine.isHidden()
    assert "TS 173900/200000" in win.lbl_engine.text(), win.lbl_engine.text()
    assert "Energy -25.9 dB" in win.lbl_engine.text(), win.lbl_engine.text()

    # second bar: residual-energy convergence toward the end criterion
    assert not win.progress_energy.isHidden(), "energy bar not shown after engine lines"
    assert win._engine_progress.target_db == 50.0, win._engine_progress.target_db
    assert win.progress_energy.value() == 52, win.progress_energy.value()
    assert win.progress_energy.format() == "E 52%", win.progress_energy.format()

    win._reset_engine_progress()
    assert win.progress.isHidden(), "reset must hide the bar"
    assert win.progress_energy.isHidden(), "reset must hide the energy bar"

    win.close()
    print("PROGRESS SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
