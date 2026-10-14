"""Smoke: post-sim S-param path — export hardening + viewer auto-load."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.postproc.sparams import export_sparams  # noqa: E402
from app.settings import AppSettings  # noqa: E402


def main() -> int:
    # 1) export_sparams writes JSON even if touchstone fails
    tmp = ROOT / "work" / "sparam_export_smoke"
    tmp.mkdir(parents=True, exist_ok=True)
    freqs = np.linspace(2e9, 3e9, 11)
    s = 0.1 + 0.01j * np.ones_like(freqs)
    table = {"freq": freqs, "S11": s, "S_in1": s}
    written = export_sparams(table, tmp, basename="sparams")
    assert written.get("json") and written["json"].exists(), "json missing"
    assert written.get("touchstone") and written["touchstone"].exists(), "touchstone missing"
    assert written.get("png") and written["png"].exists(), "png missing"

    # 2) NaN-only table still writes json; touchstone may fail but json exists
    bad = {"freq": freqs, "S11": np.full(freqs.shape, np.nan, dtype=complex)}
    written_bad = export_sparams(bad, tmp, basename="sparams_bad")
    assert written_bad.get("json") and written_bad["json"].exists(), "bad json missing"

    # 3) GUI viewer auto-loads newest sparams under work/
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    settings = AppSettings.load()
    win = MainWindow(settings)
    win.show()
    app.processEvents()
    win._on_show_sparams()
    app.processEvents()
    # At least one known good file should be loaded (patch_antenna / patch_verify)
    assert win.sparam_viewer.network is not None, "viewer should auto-load newest sparams"
    assert win.sparam_viewer.network_path is not None
    info = win.sparam_viewer.lbl_info.text()
    assert "sparams" in info.lower() or "port" in info.lower() or "GHz" in info, info
    win.close()
    print("SPARAM POST-SIM OK", win.sparam_viewer.network_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
