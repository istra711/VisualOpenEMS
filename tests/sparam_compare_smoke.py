"""Headless smoke: S-parameter reference overlay, delta plot and marker delta."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# PySide6 must be imported before scikit-rf in this environment (shiboken /
# NumPy init order); the other GUI smoke scripts do the same.
from unittest.mock import patch  # noqa: E402

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import numpy as np  # noqa: E402
import skrf as rf  # noqa: E402

from app.postproc.sparams import (  # noqa: E402
    align_network_to_frequencies,
    export_network_touchstone,
    export_sparams,
    network_z0,
)
from app.ui.sparam_viewer import SParamViewer, load_network_from_path  # noqa: E402


def _network(freqs, *, z0=50.0, level_db=-1.0, phase=0.3):
    x = (freqs - freqs.mean()) / max(np.ptp(freqs), 1.0)
    s21 = 10 ** ((level_db - 2.0 * x * x) / 20.0) * np.exp(
        1j * (phase + 2 * np.pi * freqs / 1e9)
    )
    s11 = 10 ** ((-18.0 + 4.0 * x * x) / 20.0) * np.exp(
        1j * (0.1 + np.pi * freqs / 1e9)
    )
    s = np.zeros((len(freqs), 2, 2), dtype=complex)
    s[:, 0, 0] = s11
    s[:, 0, 1] = s21
    s[:, 1, 0] = s21
    s[:, 1, 1] = s11
    return rf.Network(f=freqs, s=s, z0=z0)


def _line(ax, label):
    for line in ax.lines:
        if line.get_label() == label:
            return line
    return None


def main() -> int:
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="sparam_compare_"))

    current = _network(np.linspace(1e9, 3e9, 201), level_db=-1.0)
    reference = _network(np.linspace(1.5e9, 2.5e9, 101), z0=75.0, level_db=-2.0)
    current_path = export_network_touchstone(current, tmp / "current.s2p")
    reference_path = export_network_touchstone(reference, tmp / "reference.s2p")

    viewer = SParamViewer()
    viewer.resize(1100, 800)
    reference_events: list[int] = []
    viewer.reference_changed.connect(lambda: reference_events.append(1))
    viewer.load_path(current_path)
    if not viewer.btn_compare.isEnabled():
        print("FAIL: compare button must be enabled with a current network")
        return 1
    previous_requests: list[int] = []
    viewer.previous_run_requested.connect(lambda: previous_requests.append(1))
    viewer.set_previous_run_available(True)
    viewer.act_previous_run.trigger()
    if previous_requests != [1] or not viewer.act_previous_run.isEnabled():
        print("FAIL: Previous-run quick action is not wired")
        return 1
    if not viewer.load_reference_path(
        reference_path,
        label="demo / run-1",
        meta={
            "kind": "run",
            "run_id": "run-1",
            "status": "complete",
            "created": "2026-01-01T12:00:00",
        },
    ):
        print("FAIL: reference file did not load")
        return 2
    if viewer.reference_network is None or viewer.reference_path != reference_path:
        print("FAIL: reference state not stored")
        return 2
    desc = viewer.reference_descriptor()
    if not desc or desc.get("run_id") != "run-1" or desc.get("kind") != "run":
        print("FAIL: reference descriptor lost provenance:", desc)
        return 2
    if reference_events != [1]:
        print("FAIL: reference_changed not emitted exactly once")
        return 2
    viewer.set_port_map([2, 1])
    if viewer._mapped_reference_key("S21") != "S12":
        print("FAIL: port map did not remap S21 -> S12")
        return 2
    if viewer.reference_descriptor().get("port_map") != [2, 1]:
        print("FAIL: port map missing from descriptor")
        return 2
    viewer.set_port_map(None)
    if viewer._mapped_reference_key("S21") != "S21":
        print("FAIL: reset port map is not identity")
        return 2
    from app.ui.port_map_dialog import SParamPortMapDialog

    dlg = SParamPortMapDialog(2, 4, [2, 4])
    if dlg.selected_map() != [2, 4]:
        print("FAIL: port-map dialog did not preserve its map")
        return 2
    dlg.close()
    cmp_csv = tmp / "viewer_compare.csv"
    cmp_report = tmp / "viewer_compare.txt"
    with patch(
        "app.ui.sparam_viewer.QFileDialog.getSaveFileName",
        return_value=(str(cmp_csv), ""),
    ):
        viewer._export_comparison_csv()
    with patch(
        "app.ui.sparam_viewer.QFileDialog.getSaveFileName",
        return_value=(str(cmp_report), ""),
    ):
        viewer._export_comparison_report()
    if not cmp_csv.exists() or "S21" not in cmp_csv.read_text(encoding="utf-8"):
        print("FAIL: viewer comparison CSV export")
        return 2
    if not cmp_report.exists() or "Port map:" not in cmp_report.read_text(
        encoding="utf-8"
    ):
        print("FAIL: viewer comparison report export")
        return 2
    if network_z0(viewer.reference_network) != 75.0:
        print("FAIL: reference Z0 not read from Touchstone")
        return 3
    json_table = {
        "freq": np.linspace(1e9, 2e9, 5),
        "S11": np.full(5, 0.2 + 0.1j),
    }
    json_written = export_sparams(json_table, tmp / "json_z0", z0=73.0)
    json_net = load_network_from_path(json_written["json"])
    if network_z0(json_net) != 73.0:
        print("FAIL: sparams.json Z0 not restored")
        return 3
    if "Ref:" not in viewer.lbl_reference.text():
        print("FAIL: reference chip not visible:", viewer.lbl_reference.text())
        return 3

    viewer.cmb_port.setCurrentText("S21")
    viewer.cmb_plot.setCurrentText("|S| dB")
    viewer._redraw()
    ax = viewer.figure.axes[0]
    labels = [line.get_label() for line in ax.lines]
    if "S21" not in labels or "S21 (ref)" not in labels:
        print("FAIL: reference overlay missing:", labels)
        return 4
    ref_line = _line(ax, "S21 (ref)")
    y_ref = np.asarray(ref_line.get_ydata())
    if not (np.isnan(y_ref[0]) and np.isnan(y_ref[-1])):
        print("FAIL: reference must not be extrapolated outside its overlap")
        return 5
    if not np.isfinite(y_ref[len(y_ref) // 2]):
        print("FAIL: reference missing in the overlap")
        return 5
    viewer.act_show_reference.setChecked(False)
    viewer._redraw()
    hidden_labels = [line.get_label() for line in viewer.figure.axes[0].lines]
    if "S21 (ref)" in hidden_labels:
        print("FAIL: Show reference did not hide the reference trace")
        return 5
    viewer.act_show_reference.setChecked(True)
    viewer._redraw()

    # Delta = current dB - reference dB, only in the common frequency range.
    viewer.chk_delta.setChecked(True)
    viewer._redraw()
    ax = viewer.figure.axes[0]
    delta_line = _line(ax, "ΔS21")
    if delta_line is None:
        print("FAIL: delta trace missing:", [ln.get_label() for ln in ax.lines])
        return 6
    aligned = align_network_to_frequencies(reference, current.f, z0=50.0)
    current_db = 20 * np.log10(np.maximum(np.abs(current.s[:, 1, 0]), 1e-15))
    ref_db = 20 * np.log10(np.maximum(np.abs(aligned[:, 1, 0]), 1e-15))
    expected = current_db - ref_db
    got = np.asarray(delta_line.get_ydata())
    mask = np.isfinite(got) & np.isfinite(expected)
    if mask.sum() < 50:
        print("FAIL: delta overlap too small:", mask.sum())
        return 6
    if not np.allclose(got[mask], expected[mask], atol=1e-9):
        print("FAIL: delta values differ from current - reference")
        return 6

    # Every plot type gets the reference overlay; every scalar type also
    # gets a delta series when the Delta box is checked.
    for plot in viewer.PLOT_TYPES:
        viewer.cmb_plot.setCurrentText(plot)
        viewer.chk_delta.setChecked(False)
        viewer._redraw()
        labels = [line.get_label() for line in viewer.figure.axes[0].lines]
        if plot == "Smith":
            if "S21 (ref)" not in labels:
                print("FAIL: Smith reference overlay missing:", ascii(labels))
                return 7
            continue
        if not any("(ref)" in label for label in labels):
            print("FAIL:", ascii(plot), "reference overlay missing:", ascii(labels))
            return 7
        viewer.chk_delta.setChecked(True)
        viewer._redraw()
        labels = [line.get_label() for line in viewer.figure.axes[0].lines]
        if not any(label.startswith("\u0394") for label in labels):
            print("FAIL:", ascii(plot), "delta trace missing:", ascii(labels))
            return 7

    # Smith stays an overlay; delta is not offered there.
    viewer.cmb_plot.setCurrentText("Smith")
    if viewer.chk_delta.isEnabled():
        print("FAIL: delta must be disabled on the Smith chart")
        return 7
    viewer._redraw()
    smith_labels = [line.get_label() for line in viewer.figure.axes[0].lines]
    if "S21 (ref)" not in smith_labels:
        print("FAIL: Smith reference overlay missing:", smith_labels)
        return 7
    ref_smith = _line(viewer.figure.axes[0], "S21 (ref)")
    if ref_smith.get_linestyle() != "--":
        print("FAIL: Smith reference is not dashed:", ref_smith.get_linestyle())
        return 7

    # Marker list: reference value and delta, plus a dedicated Delta style.
    viewer.cmb_plot.setCurrentText("|S| dB")
    viewer.chk_delta.setChecked(False)
    viewer._clear_markers()
    viewer._add_marker_at_freq(2.0e9)
    row = viewer.marker_list.item(0).text()
    if "ref" not in row or "Δ" not in row:
        print("FAIL: marker row misses reference/delta:", row)
        return 8
    viewer.cmb_marker_style.setCurrentText("Delta")
    delta_row = viewer.marker_list.item(0).text()
    if "Δ" not in delta_row or "ref" not in delta_row:
        print("FAIL: Delta marker style:", delta_row)
        return 8

    # Clearing the reference leaves the current network intact.
    events_before_clear = len(reference_events)
    viewer.clear_reference()
    if viewer.reference_network is not None:
        print("FAIL: reference not cleared")
        return 9
    if len(reference_events) != events_before_clear + 1:
        print("FAIL: reference_changed not emitted on clear:", len(reference_events))
        return 9
    cleared_row = viewer.marker_list.item(0).text().lower()
    if "ref " in cleared_row or "Δ" in cleared_row:
        print("FAIL: marker row still mentions a reference:", cleared_row)
        return 9
    if "(ref)" in [line.get_label() for line in viewer.figure.axes[0].lines]:
        print("FAIL: reference lines remain after clear")
        return 9
    if viewer.lbl_reference.isVisible():
        print("FAIL: reference chip remains visible after clear")
        return 9

    # Selecting a row no longer replaces the current result; the right-click
    # context menu is the explicit "Load as current" / "Use as reference".
    scan_dir = tmp / "scan" / "other_project" / "sparams"
    scan_dir.mkdir(parents=True)
    scan_path = export_network_touchstone(reference, scan_dir / "sparams.s2p")
    viewer._on_scan(root=tmp / "scan")
    ref_row = -1
    for i in range(viewer.file_list.count()):
        raw = viewer.file_list.item(i).data(Qt.ItemDataRole.UserRole)
        if raw is not None and Path(str(raw)).resolve() == scan_path.resolve():
            ref_row = i
            break
    if ref_row < 0:
        print("FAIL: reference file missing from the scan list")
        return 10
    before_path = viewer.network_path
    viewer.file_list.setCurrentRow(ref_row)
    if viewer.network_path != before_path:
        print("FAIL: selecting a scan row must not replace the current result")
        return 10
    viewer._on_file_selected(viewer.file_list.item(ref_row))
    if viewer.network_path != scan_path:
        print("FAIL: explicit Load as current did not load the selected file")
        return 10
    viewer.load_path(current_path)

    # Right-click -> Use as reference must not replace the current result.
    viewer.show()
    app.processEvents()
    scan_item = viewer.file_list.item(ref_row)
    pos = viewer.file_list.visualItemRect(scan_item).center()
    class _FakeAction:
        def setEnabled(self, _value):  # noqa: N802
            pass

    class _FakeMenu:
        def __init__(self, _parent=None):
            self._actions = []

        def addAction(self, _text):  # noqa: N802
            action = _FakeAction()
            self._actions.append(action)
            return action

        def exec(self, *_args, **_kwargs):  # noqa: N802
            return self._actions[1]

    with patch("app.ui.sparam_viewer.QMenu", _FakeMenu):
        viewer._on_file_context_menu(pos)
    if viewer.network_path != current_path:
        print("FAIL: Use as reference replaced the current result")
        return 10
    if viewer.reference_path != scan_path:
        print("FAIL: context-menu reference was not loaded:", viewer.reference_path)
        return 10
    viewer.clear_reference()

    # Port-count mismatch is allowed (common Sij only) and called out.
    one_port = rf.Network(
        f=np.linspace(1.5e9, 2.5e9, 11),
        s=np.full((11, 1, 1), 0.2 + 0.1j),
        z0=50,
    )
    one_path = export_network_touchstone(one_port, tmp / "one.s1p")
    viewer.load_reference_path(one_path)
    if "\u26a0" not in viewer.lbl_reference.text():
        print("FAIL: port-count mismatch not reported:", viewer.lbl_reference.text())
        return 10
    if "Port count:" not in viewer.lbl_reference.toolTip():
        print("FAIL: port-count mismatch missing from the tooltip")
        return 10
    viewer.clear_reference()

    # Pop-out keeps the reference chip in sync.
    viewer.set_reference(reference, reference_path)
    viewer.popout_graph()
    win = viewer._graph_win
    if win is None or not win.lbl_reference.isVisible():
        print("FAIL: pop-out reference label missing")
        return 11
    viewer.restore_graph_to_tab()

    viewer.clear()
    if viewer.reference_network is not None:
        print("FAIL: clear() must drop the reference too")
        return 12

    print("SPARAM COMPARE SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
