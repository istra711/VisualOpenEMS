"""Smoke: antenna-pattern overlay/film dialog for a sweep bundle."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402
import numpy as np  # noqa: E402


def _write_nf2ff(path: Path, level: float) -> None:
    theta = np.linspace(0.0, 180.0, 19)
    phi = np.linspace(0.0, 360.0, 37)
    th, ph = np.meshgrid(theta, phi, indexing="ij")
    pattern = level + 8.0 * np.cos(np.deg2rad(th)) + 0.3 * np.cos(np.deg2rad(ph))
    data = {
        "frequencies": [2.4e9],
        "theta_deg": theta.tolist(),
        "phi_deg": phi.tolist(),
        "D_dBi": [pattern.tolist()],
        "gain_dBi": [(pattern - 0.2).tolist()],
        "Dmax_dBi": [float(np.max(pattern))],
        "efficiency": [0.85],
        "Prad": [1.0],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def main() -> int:
    from app.ui.sweep_patterns import SweepPatternsDialog
    from app.ui.sweep_results import SweepResultsDialog

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="sweep_patterns_"))
    points = []
    for index, (value, level) in enumerate(((0.0, 5.0), (90.0, 6.0))):
        nf = f"sweep/point-{index:03d}/nf2ff/nf2ff.json"
        _write_nf2ff(tmp / nf, level)
        points.append(
            {
                "index": index,
                "values": {"p1": value},
                "label": f"phase p1={value:g}°",
                "work_dir": f"sweep/point-{index:03d}",
                "project_path": f"sweep/point-{index:03d}/point_project.json",
                "status": "complete",
                "error": "",
                "sparams": "",
                "nf2ff": nf,
            }
        )
    sweep_json = tmp / "sweep.json"
    sweep_json.write_text(
        json.dumps(
            {
                "version": 1,
                "status": "complete",
                "spec": {
                    "enabled": True,
                    "target_kind": "port_phase",
                    "target": "p1",
                    "variable": "",
                    "values": [0.0, 90.0],
                    "collect_nf2ff": True,
                },
                "points": points,
            }
        ),
        encoding="utf-8",
    )

    res = SweepResultsDialog(sweep_json)
    if not res.btn_patterns.isEnabled():
        print("FAIL: Antenna patterns button is disabled with NF2FF data")
        return 1
    res.close()

    dlg = SweepPatternsDialog(sweep_json)
    if dlg.point_list.count() != 2:
        print("FAIL: pattern dialog points:", dlg.point_list.count())
        return 2
    if dlg.cmb_freq.count() != 1:
        print("FAIL: pattern dialog frequency count:", dlg.cmb_freq.count())
        return 2
    dlg.cmb_plot.setCurrentText("E-Plane (phi=90)")
    dlg._redraw()
    if not dlg.figure.axes:
        print("FAIL: 2D pattern plot has no axes")
        return 3
    dlg.cmb_plot.setCurrentText("3D Pattern")
    dlg._redraw()
    if len(dlg.figure.axes) < 1:
        print("FAIL: 3D pattern plot has no axes")
        return 3
    from unittest.mock import patch

    gif = tmp / "pattern_film.gif"
    with patch(
        "app.ui.sweep_patterns.QFileDialog.getSaveFileName",
        return_value=(str(gif), ""),
    ), patch("app.ui.sweep_patterns.QMessageBox.information"):
        dlg._export_animation("gif")
    if not gif.is_file() or gif.stat().st_size < 100:
        print("FAIL: 3D pattern GIF export")
        return 4
    dlg.close()
    print("SWEEP PATTERNS SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
