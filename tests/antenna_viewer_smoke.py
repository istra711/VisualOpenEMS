"""Headless smoke for AntennaPatternViewer using offscreen Qt.

Generates a synthetic nf2ff.json (directive pattern with a known peak),
then exercises discovery, loading, every plot type, the 3D toggle and the
pop-out / restore round-trip.
"""

from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import numpy as np

from PySide6.QtWidgets import QApplication

from app.ui.antenna_viewer import (
    AntennaPatternViewer,
    find_latest_nf2ff_files,
    front_to_back_ratio,
    half_power_beamwidth,
    load_nf2ff_json,
)


def write_synthetic_nf2ff(path: Path) -> dict:
    """Directive pattern: theta=90 ring with a phi=0 lobe -> Dmax ~ 10 dBi."""
    theta = np.linspace(0.0, 180.0, 91)
    phi = np.linspace(0.0, 360.0, 181)
    TH = np.deg2rad(theta)[:, None]
    PH = np.deg2rad(phi)[None, :]
    # normalized Poynting-like pattern (peak 1)
    pattern = (np.sin(TH) ** 2) * (0.5 + 0.5 * np.cos(PH))
    pattern = pattern / np.max(pattern)
    prad = 0.5
    d_lin = 4.0 * np.pi * pattern  # Prad folded in already (normalized)
    d_dbi = 10.0 * np.log10(np.maximum(d_lin, 1e-6))
    eta = 0.85
    gain_dbi = d_dbi + 10.0 * np.log10(eta)
    payload = {
        "frequencies": [2.45e9],
        "theta_deg": theta.tolist(),
        "phi_deg": phi.tolist(),
        "Prad": [prad],
        "Dmax_dBi": [float(np.nanmax(d_dbi))],
        "efficiency": [eta],
        "D_dBi": [d_dbi.tolist()],
        "gain_dBi": [gain_dbi.tolist()],
        "P_rad": [(pattern * prad / (4 * np.pi)).tolist()],
        "out_file": str(path),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def main() -> int:
    work = APP_ROOT / "work"
    synth_dir = work / "antenna_smoke" / "simulation" / "nf2ff"
    synth_json = synth_dir / "nf2ff.json"
    write_synthetic_nf2ff(synth_json)

    files = find_latest_nf2ff_files(work)
    print("discovered:", files)
    if not files:
        print("FAIL: discovery missed the synthetic nf2ff.json")
        return 1

    data = load_nf2ff_json(synth_json)
    print(
        "loaded:",
        "freqs", data["frequencies"].shape,
        "theta", None if data["theta_deg"] is None else data["theta_deg"].shape,
        "D_dBi", None if data["D_dBi"] is None else data["D_dBi"].shape,
        "gain", None if data["gain_dBi"] is None else data["gain_dBi"].shape,
    )
    if data["D_dBi"] is None or data["gain_dBi"] is None:
        print("FAIL: pattern arrays missing")
        return 2
    if data["theta_deg"].max() != 180.0 or data["phi_deg"].max() != 360.0:
        print("FAIL: angular grid must span 0..180 / 0..360 deg")
        return 3

    app = QApplication.instance() or QApplication(sys.argv)
    viewer = AntennaPatternViewer(work_dir=work)
    viewer.resize(1100, 800)
    viewer.load_path(synth_json)
    print("info:", viewer.lbl_info.text())
    if "Prad" not in viewer.lbl_info.text():
        print("FAIL: info line missing Prad")
        return 4
    # Details header carries the file name; block lists Dmax/Gmax/eta/Prad/theta/phi
    if "Details (nf2ff.json)" not in viewer.lbl_detail_title.text():
        print("FAIL: details title:", viewer.lbl_detail_title.text())
        return 14
    det = viewer.lbl_detail.text()
    order = ["Dmax:", "Gmax:", "eta:", "Prad:", "theta:", "phi:"]
    pos = [det.find(t) for t in order]
    if any(p < 0 for p in pos) or pos != sorted(pos):
        print("FAIL: details block:", det.encode("ascii", "replace").decode())
        return 14
    print("details:", det.encode("ascii", "replace").decode().replace("\n", " | "))
    print("freq combo:", [viewer.cmb_freq.itemText(i) for i in range(viewer.cmb_freq.count())])

    for plot in viewer.PLOT_TYPES:
        viewer.cmb_plot.setCurrentText(plot)
        viewer._redraw()
        n_axes = len(viewer.figure.axes)
        print("plot ok:", plot, "axes", n_axes)
        if n_axes < 1:
            print("FAIL: no axes for", plot)
            return 5

    # radiated efficiency plot: fixture eta = 0.85 -> 85 %
    def _line_ys():
        ax = viewer.figure.axes[0]
        if not ax.lines:
            return np.array([])
        return np.concatenate([np.asarray(l.get_ydata(), dtype=float) for l in ax.lines])

    viewer.cmb_plot.setCurrentText("Radiated Efficiency vs Frequency")
    viewer._redraw()
    ys = _line_ys()
    if ys.size == 0 or not np.allclose(ys[np.isfinite(ys)], 85.0, atol=1e-6):
        print("FAIL: efficiency plot values", ys)
        return 13
    if "%" not in viewer.figure.axes[0].get_ylabel():
        print("FAIL: efficiency y-label:", viewer.figure.axes[0].get_ylabel())
        return 13
    # fallback: without the efficiency key, eta derives from gain/directivity
    viewer.data["efficiency"] = None
    viewer._redraw()
    ys = _line_ys()
    if ys.size == 0 or not np.allclose(ys[np.isfinite(ys)], 85.0, atol=1e-6):
        print("FAIL: derived efficiency values", ys)
        return 13
    viewer.data["efficiency"] = np.array([0.85])
    print("efficiency plot OK (direct + derived):", ys)

    # pop-out round-trip
    viewer.cmb_plot.setCurrentText("Gain vs Frequency")
    viewer._redraw()
    viewer.popout_graph()
    if not viewer.graph_is_popped_out or viewer._graph_win is None:
        print("FAIL: pop-out did not open")
        return 6
    print("pop-out open OK, title:", viewer._graph_win.windowTitle().encode("ascii", "replace").decode())
    gw = viewer._graph_win
    if not hasattr(gw, "cmb_cmap"):
        print("FAIL: pop-out missing cmap combo")
        return 12
    viewer.cmb_cmap.setCurrentText(viewer.CMAP_CHOICES[-1])
    if gw.cmb_cmap.currentText() != viewer.cmb_cmap.currentText():
        print("FAIL: cmap not mirrored into pop-out")
        return 12
    print("pop-out cmap mirror OK:", gw.cmb_cmap.currentText())
    viewer.restore_graph_to_tab()
    if viewer.graph_is_popped_out:
        print("FAIL: restore did not close the pop-out")
        return 7
    print("restore to tab OK")

    # 3D controls: dBi-min slider + field combo; visible in 3D, hidden on cuts
    viewer.cmb_plot.setCurrentText("3D Pattern")
    viewer._redraw()
    if viewer.slider_dbi_min.isHidden() or viewer.cmb_3d_field.isHidden():
        print("FAIL: 3D controls hidden in 3D plot")
        return 9
    lo0, hi0 = viewer._dbi_window()
    viewer.slider_dbi_min.setValue(viewer.slider_dbi_min.maximum())
    lo1, hi1 = viewer._dbi_window()
    print(f"dBi window: {lo0}..{hi0} -> raised min {lo1}..{hi1}")
    if not hi1 > lo1 or (hi1 - lo1) > (hi0 - lo0) or len(viewer.figure.axes) < 1:
        print("FAIL: invalid window/axes after dBi slider move")
        return 10
    for fld in AntennaPatternViewer.THREE_D_FIELDS:
        viewer.cmb_3d_field.setCurrentText(fld)
        viewer._redraw()
        if len(viewer.figure.axes) < 1:
            print("FAIL: no axes for 3D field", fld)
            return 10
    print("3D fields OK:", viewer.cmb_3d_field.currentText(), "window", viewer._dbi_window())
    for cm_name in AntennaPatternViewer.CMAP_CHOICES:
        viewer.cmb_cmap.setCurrentText(cm_name)
        viewer._redraw()
        if len(viewer.figure.axes) < 1:
            print("FAIL: no axes for cmap", cm_name)
            return 10
    print("cmaps OK:", viewer.cmb_cmap.currentText())
    viewer.cmb_plot.setCurrentText("E-Plane (phi=90)")
    viewer._redraw()
    if not viewer.slider_dbi_min.isHidden() or not viewer.cmb_cmap.isHidden():
        print("FAIL: 3D controls visible on polar plot")
        return 11

    # HPBW + VRV buttons (pattern-only, no S-params)
    if not hasattr(viewer, "btn_hpbw") or not hasattr(viewer, "btn_vrv"):
        print("FAIL: HPBW/VRV buttons missing")
        return 18
    viewer.cmb_plot.setCurrentText("E-Plane (phi=90)")
    viewer._redraw()
    viewer.btn_hpbw.click()
    det = viewer.lbl_detail.text()
    print("HPBW detail:", det.encode("ascii", "replace").decode().replace("\n", " | "))
    if "HPBW" not in det or viewer._hpbw is None:
        print("FAIL: HPBW not computed")
        return 18
    if not (0.0 < viewer._hpbw["width_deg"] <= 360.0):
        print("FAIL: HPBW width out of range", viewer._hpbw)
        return 18
    # side panel keeps Dmax + HPBW block
    if "Dmax:" not in det or "opening angle:" not in det:
        print("FAIL: HPBW not in side details block")
        return 18
    # overlay drawn: arc (crimson) + angle text on polar axes
    title = viewer.figure.axes[0].get_title()
    print("title after HPBW:", title.encode("ascii", "replace").decode())
    if "HPBW" not in title:
        print("FAIL: HPBW not in plot title")
        return 18
    labels = [ln.get_label() for ln in viewer.figure.axes[0].lines]
    if "_hpbw_arc" not in labels or "_hpbw_edge" not in labels:
        print("FAIL: HPBW arc/edges not drawn", labels)
        return 18
    texts = [t.get_text() for t in viewer.figure.axes[0].texts]
    if not any("°" in t for t in texts):
        print("FAIL: HPBW angle text missing", texts)
        return 18
    viewer.btn_vrv.click()
    det = viewer.lbl_detail.text()
    print("VRV detail:", det.encode("ascii", "replace").decode().replace("\n", " | "))
    if "VRV" not in det or viewer._vrv is None:
        print("FAIL: VRV not computed")
        return 18
    if "ratio:" not in det or "back:" not in det:
        print("FAIL: VRV not in side details block")
        return 18
    title = viewer.figure.axes[0].get_title()
    if "VRV" not in title:
        print("FAIL: VRV not in plot title")
        return 18
    labels = [ln.get_label() for ln in viewer.figure.axes[0].lines]
    if "_vrv_back" not in labels:
        print("FAIL: VRV back marker not drawn", labels)
        return 18
    print("HPBW/VRV UI OK")

    # markers: synthetic click -> dot + list row, dedupe, delete, clear
    from PySide6.QtCore import Qt as _Qt

    if viewer.marker_list.contextMenuPolicy() != _Qt.ContextMenuPolicy.CustomContextMenu:
        print("FAIL: marker list has no custom context menu")
        return 15
    ax = viewer.figure.axes[0]
    xs = np.asarray(ax.lines[0].get_xdata(), dtype=float)
    ys = np.asarray(ax.lines[0].get_ydata(), dtype=float)
    px = ax.transData.transform(np.column_stack([xs, ys]))[len(xs) // 3]
    ev = SimpleNamespace(button=1, inaxes=ax, x=float(px[0]), y=float(px[1]))
    viewer._on_click(ev)
    if len(viewer.markers) != 1 or viewer.marker_list.count() != 1:
        print("FAIL: click did not add a marker", len(viewer.markers))
        return 15
    m0 = viewer.markers[0]
    if m0["kind"] != "polar" or m0["xunit"] != "°" or m0["yunit"] != "dBi":
        print("FAIL: polar marker fields", m0)
        return 15
    n_dots = sum(1 for ln in viewer.figure.axes[0].lines if ln.get_label() == "_marker")
    if n_dots != 1:
        print("FAIL: marker dot not drawn", n_dots)
        return 15
    viewer._on_click(ev)  # same point again -> dedupe
    if len(viewer.markers) != 1:
        print("FAIL: duplicate marker not deduped")
        return 15
    print("marker polar OK:", viewer.marker_list.item(0).text().encode("ascii", "replace").decode())
    viewer._delete_marker_at(0)
    if viewer.markers or viewer.marker_list.count():
        print("FAIL: delete marker")
        return 16
    print("delete marker OK")

    # xy plot marker (Gain max curve) + Clear markers button
    viewer.cmb_plot.setCurrentText("Gain vs Frequency")
    viewer._redraw()
    ax = viewer.figure.axes[0]
    curve = next((ln for ln in ax.lines if ln.get_label() == "Gain max [dBi]"), ax.lines[0])
    xs = np.asarray(curve.get_xdata(), dtype=float)
    ys = np.asarray(curve.get_ydata(), dtype=float)
    px = ax.transData.transform(np.column_stack([xs, ys]))[0]
    viewer._on_click(SimpleNamespace(button=1, inaxes=ax, x=float(px[0]), y=float(px[1])))
    if len(viewer.markers) != 1 or viewer.markers[0]["yunit"] != "dBi":
        print("FAIL: xy marker", viewer.markers)
        return 17
    if viewer.markers[0]["plot"] != "Gain vs Frequency":
        print("FAIL: marker plot name", viewer.markers[0]["plot"])
        return 17
    print("marker xy OK:", viewer.marker_list.item(0).text().encode("ascii", "replace").decode())
    viewer.btn_clear_markers.click()
    if viewer.markers or viewer.marker_list.count():
        print("FAIL: Clear markers button")
        return 17
    print("clear markers OK")

    viewer.cmb_plot.setCurrentText("Gain vs Frequency")
    viewer._redraw()
    out = work / "antenna_viewer_smoke.png"
    viewer.canvas.draw()
    viewer.canvas.print_png(str(out))
    print("screenshot:", out, out.exists(), out.stat().st_size if out.exists() else 0)
    if not out.exists() or out.stat().st_size < 1000:
        print("FAIL: screenshot missing/too small")
        return 8

    print("ANTENNA VIEWER SMOKE PASS")
    return 0


def pure_fn_checks() -> int:
    """Unit checks for HPBW / VRV on synthetic cuts (no Qt)."""
    # Gaussian lobe at 0°, FWHM = 2*sqrt(2*ln2)*sigma ≈ 2.355*sigma
    ang = np.linspace(-180.0, 180.0, 3601)
    sigma = 20.0
    db = -((ang / sigma) ** 2) * (10.0 / np.log(10.0)) / 2.0  # peak 0 dBi
    # make it a clean dB Gaussian: -10*log10(exp(-(t/s)^2/2)) wait simpler:
    db = -((ang**2) / (2.0 * sigma**2)) * (10.0 / np.log(10.0))
    h = half_power_beamwidth(ang, db, level_db=-3.0)
    assert h is not None, "HPBW returned None"
    expected = 2.0 * math.sqrt(2.0 * math.log(2.0)) * sigma
    print(f"HPBW: {h['width_deg']:.3f} deg (expected ~{expected:.3f})")
    if abs(h["width_deg"] - expected) > 0.5:
        print("FAIL: HPBW width", h["width_deg"], expected)
        return 20
    if abs(h["peak_deg"]) > 0.2:
        print("FAIL: HPBW peak angle", h["peak_deg"])
        return 20

    # Front-to-back: peak at 0° @ 10 dBi, back at 180° @ 0 dBi -> VRV 10 dB
    ang2 = np.linspace(0.0, 360.0, 361)
    db2 = 10.0 * np.cos(np.deg2rad(ang2)) ** 2  # 10 at 0/180? no: max at 0 and 180
    # use asymmetric: max at 0, min at 180
    db2 = 10.0 * np.cos(np.deg2rad(ang2) / 2.0) ** 2
    # at 0: 10, at 180: 0 -> VRV = 10 dB
    v = front_to_back_ratio(ang2, db2)
    assert v is not None, "VRV returned None"
    print(f"VRV: {v['vrv_db']:.3f} dB ratio={v['ratio']:.3f} peak={v['peak_deg']}")
    if abs(v["vrv_db"] - 10.0) > 0.3:
        print("FAIL: VRV", v)
        return 21
    if abs(v["ratio"] - 10.0) > 0.3:
        print("FAIL: VRV linear", v)
        return 21
    print("PURE HPBW/VRV OK")
    return 0


if __name__ == "__main__":
    rc = pure_fn_checks()
    if rc:
        raise SystemExit(rc)
    raise SystemExit(main())
