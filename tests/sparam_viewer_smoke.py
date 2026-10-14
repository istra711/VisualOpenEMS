"""Headless smoke for SParamViewer using offscreen Qt."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from PySide6.QtWidgets import QApplication

from app.ui.sparam_viewer import SParamViewer, find_latest_sparam_files, load_network_from_path


def main() -> int:
    work = APP_ROOT / "work"
    files = find_latest_sparam_files(work)
    print("discovered:", files)
    if not files:
        print("FAIL: no sparam files under work/")
        return 1
    touch = next((p for p in files if p.suffix.lower().startswith(".s")), files[0])
    print("loading", touch)
    ntwk = load_network_from_path(touch)
    print("network", ntwk.nports, "pts", len(ntwk.f))

    app = QApplication.instance() or QApplication(sys.argv)
    viewer = SParamViewer(work_dir=work)
    viewer.resize(1100, 800)
    # do not auto-load via file list; load explicitly
    viewer.load_path(touch)
    print("info:", viewer.lbl_info.text())
    print("ports combo:", [viewer.cmb_port.itemText(i) for i in range(viewer.cmb_port.count())])
    print("trace S11", None if viewer._trace("S11") is None else viewer._trace("S11").shape)

    for plot in viewer.PLOT_TYPES:
        viewer.cmb_plot.setCurrentText(plot)
        viewer._redraw()
        print("plot ok:", plot, "axes", len(viewer.figure.axes))
        if plot == "Smith":
            ax = viewer.figure.axes[0]
            n_patches = len(ax.patches)
            n_labels = len(ax.texts)
            print(f"  smith artists: lines {len(ax.lines)}, patches {n_patches}, labels {n_labels}")
            if n_patches < 20 or n_labels < 8:
                print("FAIL: Smith chart missing skrf grid/labels")
                return 4
            print("  smith: scikit-rf full grid + labels OK")

    viewer.cmb_plot.setCurrentText("Smith")
    viewer._redraw()
    smith_png = work / "sparam_smith_full.png"
    viewer.canvas.draw()
    viewer.canvas.print_png(str(smith_png))
    print("smith screenshot:", smith_png, smith_png.exists(), smith_png.stat().st_size if smith_png.exists() else 0)

    viewer.cmb_plot.setCurrentText("|S| dB")
    viewer._redraw()
    viewer._goto_min()
    print("markers after min:", viewer.markers)
    print("marker list rows:", viewer.marker_list.count())
    if viewer.marker_list.count() < 1:
        print("FAIL: no marker rows")
        return 2

    # click-like: add marker at midpoint
    import numpy as np

    f_mid = float(np.mean(viewer.network.f))
    viewer.markers.append(f_mid)
    viewer.markers.sort()
    viewer._refresh_markers()
    viewer._redraw()
    print("markers:", [round(f / 1e9, 4) for f in viewer.markers])

    # marker style selector: list rows follow the chosen format
    def _asc(s: str) -> str:
        return s.encode("ascii", "replace").decode()

    viewer.cmb_marker_style.setCurrentText("dB")
    row0 = viewer.marker_list.item(0).text()
    print("dB list row:", _asc(row0))
    if "dB" not in row0:
        print("FAIL: marker list does not follow the selected style")
        return 5
    viewer.cmb_marker_style.setCurrentText("Z")
    row0 = viewer.marker_list.item(0).text()
    print("Z list row:", _asc(row0))
    if "Z=" not in row0:
        print("FAIL: Z style list row")
        return 6
    viewer.cmb_marker_style.setCurrentText("All formats")
    row0 = viewer.marker_list.item(0).text()
    print("all-formats list row:", _asc(row0))
    if not ("dB" in row0 and "VSWR" in row0):
        print("FAIL: All formats list row")
        return 7
    print("marker styles OK:", len(SParamViewer.MARKER_STYLES), "styles")

    # right-click delete: custom context menu + _delete_marker_at
    from PySide6.QtCore import Qt as _Qt

    if viewer.marker_list.contextMenuPolicy() != _Qt.ContextMenuPolicy.CustomContextMenu:
        print("FAIL: marker list has no custom context menu")
        return 8
    n_before = len(viewer.markers)
    viewer._delete_marker_at(0)
    if len(viewer.markers) != n_before - 1:
        print("FAIL: marker not deleted", n_before, "->", len(viewer.markers))
        return 9
    if viewer.marker_list.count() != len(viewer.markers):
        print("FAIL: marker list out of sync after delete")
        return 10
    print("delete marker OK:", n_before, "->", len(viewer.markers))

    out = work / "sparam_viewer_smoke.png"
    viewer.canvas.draw()
    viewer.canvas.print_png(str(out))
    print("screenshot:", out, out.exists(), out.stat().st_size if out.exists() else 0)
    if not out.exists() or out.stat().st_size < 1000:
        print("FAIL: screenshot missing/too small")
        return 3
    print("SPARAM VIEWER SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
