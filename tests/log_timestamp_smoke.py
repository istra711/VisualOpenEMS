"""Smoke: every line of a run gets an elapsed-time timestamp."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> int:
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    settings = AppSettings.load()
    settings.work_dir = tempfile.mkdtemp(prefix="log_timestamp_")
    win = MainWindow(settings)

    win._begin_run_log("timestamp smoke")
    win._log(
        "[@  1h50m02s] Timestep: 11985085 || Speed: 50.6 MC/s "
        "(5.383e-04 s/TS) || Energy: ~1.46e-11 (- 2.07dB)"
    )
    win._log(
        "RunFDTD: Warning: Max. number of timesteps was reached before the "
        "end-criteria of -50dB was reached..."
    )
    win._log("\tYou may want to choose a higher number of max. timesteps...")
    win._log("Post-processing started - the solver has exited")
    win._log("Port port_1: |uf_inc|max=4.6e-13 |uf_ref|max=3.6e-13")
    win._drain_log_queue()

    lines = win.log_view.toPlainText().splitlines()
    checked = [
        line
        for line in lines
        if "RunFDTD: Warning" in line
        or "higher number" in line
        or "Post-processing" in line
        or "Port port_1" in line
    ]
    assert len(checked) == 4, checked
    for line in checked:
        assert line.lstrip().startswith("[@ "), line
    # An engine line that already came with [@ ...] must not be double-stamped.
    engine = [line for line in lines if "Timestep: 11985085" in line]
    assert engine and engine[0].lstrip().startswith("[@ "), engine
    assert engine[0].count("[@") == 1, engine[0]

    win._end_run_log()
    win.close()
    print("LOG TIMESTAMP SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
