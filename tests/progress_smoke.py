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
    tmp = Path(tempfile.mkdtemp(prefix="progress_smoke_"))
    settings = AppSettings.load(tmp / "settings.json")
    settings.work_dir = str(tmp / "work")
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
    assert win.progress_energy.peak == 52, win.progress_energy.peak

    # Energy can briefly rise again (percentage moves backwards).  The bar
    # follows it, but the peak-hold marker must stay at the maximum.
    win._log(
        "[@     11m00s] Timestep:       174000 || Speed:   45.8 MC/s "
        "(3.593e-03 s/TS) || Energy: ~1e-8 (-20.00dB)"
    )
    win._drain_log_queue()
    assert win.progress_energy.value() == 40, win.progress_energy.value()
    assert win.progress_energy.peak == 52, win.progress_energy.peak
    assert win.progress_energy.grab().size().width() == win.progress_energy.width()

    # Max timesteps without reaching the energy criterion: the TS bar is full
    # (all timesteps really ran) but turns red and is labelled "(max)"; the
    # energy bar shows the actual 4 % instead of a misleading 100 %.
    win._reset_engine_progress()
    win._log(_LINES[0])
    win._log(
        "[@   1h50m] Timestep:       200000 || Speed:  28.8 MC/s (3.5e-03 s/TS) "
        "|| Energy: ~1e-5 (-2.0dB)"
    )
    win._log(
        "RunFDTD: Warning: Max. number of timesteps was reached before the "
        "end-criteria of -50dB was reached..."
    )
    win._drain_log_queue()
    assert win.progress.value() == 100
    assert "max" in win.progress.format().lower(), win.progress.format()
    assert "converged" not in win.lbl_engine.text().lower(), win.lbl_engine.text()
    assert win.progress_energy.value() == 4, win.progress_energy.value()
    assert "c62828" in win.progress.styleSheet().lower()
    assert "c62828" in win.progress_energy.styleSheet().lower()

    win._reset_engine_progress()
    assert win.progress.isHidden(), "reset must hide the bar"
    assert win.progress_energy.isHidden(), "reset must hide the energy bar"
    assert win.progress_energy.peak is None, "peak hold must reset for a new run"
    assert win.progress.styleSheet() == ""

    # New Project / Open / geometry import must not leave the previous run's
    # progress widgets or message in the bottom status line.
    def _prime_status() -> str:
        win._reset_engine_progress()
        for line in _LINES:
            win._log(line)
        win._drain_log_queue()
        assert not win.progress.isHidden()
        return win.status.currentMessage()

    def _assert_status_cleared(label: str) -> None:
        assert win.progress.isHidden(), f"{label}: TS bar still visible"
        assert win.progress_energy.isHidden(), f"{label}: energy bar still visible"
        assert win.lbl_engine.isHidden(), f"{label}: engine label still visible"
        assert win.lbl_runtime.isHidden(), f"{label}: runtime label still visible"
        assert win.lbl_eta.isHidden(), f"{label}: ETA label still visible"
        assert win.progress_energy.peak is None, f"{label}: peak marker not reset"
        assert win.progress.styleSheet() == "", f"{label}: TS bar still red"
        assert win.progress_energy.styleSheet() == "", f"{label}: E bar still red"
        msg = win.status.currentMessage()
        assert "Timestep:" not in msg and "Energy:" not in msg, (label, msg)

    old = _prime_status()
    win._log(_LINES[-1])  # still queued when the project transition starts
    win._on_new()
    win._drain_log_queue()
    _assert_status_cleared("new project")
    assert win.status.currentMessage() != old

    stl = Path(settings.work_dir) / "status_triangle.stl"
    stl.parent.mkdir(parents=True, exist_ok=True)
    stl.write_text(
        "solid tri\n"
        "facet normal 0 0 1\n"
        "outer loop\n"
        "vertex 0 0 0\n"
        "vertex 1 0 0\n"
        "vertex 0 1 0\n"
        "endloop\n"
        "endfacet\n"
        "endsolid tri\n",
        encoding="ascii",
    )
    old = _prime_status()
    win._log(_LINES[-1])
    win._import_geometry_path(str(stl))
    win._drain_log_queue()
    _assert_status_cleared("geometry import")
    assert win.status.currentMessage() != old

    from app.models.project import Project

    project_file = Path(settings.work_dir) / "status_reset.fcsim.json"
    Project.new_default("status_reset").save(project_file)
    old = _prime_status()
    win._log(_LINES[-1])
    win._open_project_path(project_file)
    win._drain_log_queue()
    _assert_status_cleared("open project")
    assert win.status.currentMessage() != old

    # Windows styles reserve a text label to the right of the groove.  The
    # peak marker must follow the actual progress rect, not the full widget.
    from PySide6.QtWidgets import QStyle, QStyleOptionProgressBar

    old_style = app.style().name()
    app.setStyle("windowsvista")
    bar = win.progress_energy
    bar.resize(150, 22)
    bar.set_peak(100)
    geometry = bar._peak_marker_geometry()
    assert geometry is not None
    x, y0, y1, track = geometry
    opt = QStyleOptionProgressBar()
    bar.initStyleOption(opt)
    contents = bar.style().subElementRect(
        QStyle.SubElement.SE_ProgressBarContents, opt, bar
    )
    assert contents.left() <= x <= contents.right(), (x, contents)
    assert contents.top() <= y0 <= y1 <= contents.bottom(), (y0, y1, contents)
    if "windows" in bar.style().name().lower():
        assert contents.width() < bar.width(), (contents.width(), bar.width())
    app.setStyle(old_style)

    win.close()
    print("PROGRESS SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
