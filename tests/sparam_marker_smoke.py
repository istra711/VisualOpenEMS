"""Smoke: smart S-parameter markers (min/max/peak/-3 dB/band center)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.project import Project  # noqa: E402


def _network():
    import skrf as rf

    f = np.linspace(1e9, 3e9, 401)
    x = (f - 2e9) / 0.5e9
    s21_db = -0.1 - 10.0 * x * x
    s11_db = -20.0 + 19.0 * x * x
    s21 = 10 ** (s21_db / 20.0) * np.exp(-1j * 2 * np.pi * f / 1e9)
    s11 = 10 ** (s11_db / 20.0) * np.exp(1j * np.pi * f / 1e9)
    s = np.zeros((len(f), 2, 2), dtype=complex)
    s[:, 0, 0] = s11
    s[:, 0, 1] = s21
    s[:, 1, 0] = s21
    s[:, 1, 1] = s11
    return rf.Network(frequency=f, s=s, z0=50)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from app.ui.sparam_viewer import SParamViewer

    viewer = SParamViewer()
    viewer.network = _network()
    viewer._populate_ports()
    viewer.cmb_port.setCurrentText("S21")
    viewer.cmb_plot.setCurrentText("|S| dB")
    viewer._redraw()
    viewer._clear_markers()

    viewer._goto_max()
    assert len(viewer.markers) == 1
    assert viewer._marker_tags[viewer.markers[0]] == "S21 max"
    print("  trace maximum marker OK")

    # Selecting a list row highlights the matching plot marker.
    viewer.marker_list.setCurrentRow(0)
    assert viewer._selected_marker == viewer.markers[0]
    viewer._redraw()
    assert viewer._marker_artists
    assert viewer._marker_artists[0].get_color() == "black"
    print("  marker-list selection highlights the plot marker OK")

    # Visibility toggle: hide the plot cursors but keep the marker/list row.
    viewer.chk_markers_visible.setChecked(False)
    viewer._redraw()
    assert viewer._marker_artists == []
    assert len(viewer.markers) == 1
    viewer.chk_markers_visible.setChecked(True)
    viewer._redraw()
    assert viewer._marker_artists
    print("  marker visibility toggle OK")

    # Delta marker from the selected reference.
    viewer._add_delta_marker(2.1e9)
    assert len(viewer.markers) == 2, viewer.markers
    delta_f = next(f for f in viewer.markers if f in viewer._delta_refs)
    assert abs(viewer._delta_refs[delta_f] - viewer.markers[0]) < 1.0
    viewer._refresh_markers()
    delta_row = next(
        viewer.marker_list.item(i).text()
        for i in range(viewer.marker_list.count())
        if "Δ" in viewer.marker_list.item(i).text()
    )
    assert "Δf" in delta_row and "Δ|S|" in delta_row, delta_row
    print("  delta marker row OK")

    # Deleting the reference also removes the dependent delta marker.
    viewer._delete_marker_at(0)
    assert viewer.markers == []
    assert viewer._delta_refs == {}
    print("  deleting the reference removes its delta marker OK")

    viewer._clear_markers()
    with patch("app.ui.sparam_viewer.QMessageBox.information"):
        viewer._mark_bandwidth()
    assert len(viewer.markers) == 2, viewer.markers
    tags = sorted(viewer._marker_tags.values())
    assert tags == ["S21 -3dB high", "S21 -3dB low"], tags
    low = min(viewer.markers) / 1e9
    high = max(viewer.markers) / 1e9
    expected = 0.5 * np.sqrt(2.9 / 10.0)
    assert abs(low - (2.0 - expected)) < 0.01, low
    assert abs(high - (2.0 + expected)) < 0.01, high
    print("  -3 dB point markers OK")

    viewer._clear_markers()
    viewer._goto_nearest_extremum(1.9e9)
    assert len(viewer.markers) == 1
    assert viewer._marker_tags[viewer.markers[0]] == "S21 peak"
    assert abs(viewer.markers[0] / 1e9 - 2.0) < 0.02
    print("  nearest peak/valley marker OK")

    viewer._clear_markers()
    viewer._goto_band_center()
    assert len(viewer.markers) == 1
    assert viewer._marker_tags[viewer.markers[0]] == "band center"
    assert abs(viewer.markers[0] / 1e9 - 2.0) < 1e-9
    viewer._refresh_markers()
    assert "band center" in viewer.marker_list.item(0).text()
    print("  band-center marker + tagged list row OK")

    viewer._delete_marker_at(0)
    assert viewer.markers == []
    assert viewer._marker_tags == {}
    print("  deleting a marker drops its tag OK")

    # Reflection trace: -3 dB points around the S11 null.
    viewer.cmb_port.setCurrentText("S11")
    viewer.cmb_plot.setCurrentText("|S| dB")
    viewer._redraw()
    viewer._clear_markers()
    with patch("app.ui.sparam_viewer.QMessageBox.information"):
        viewer._mark_bandwidth()
    assert len(viewer.markers) == 2, viewer.markers
    tags = sorted(viewer._marker_tags.values())
    assert tags == ["S11 -3dB high", "S11 -3dB low"], tags
    expected = 0.5 * np.sqrt(3.0 / 19.0)
    assert abs(min(viewer.markers) / 1e9 - (2.0 - expected)) < 0.01
    assert abs(max(viewer.markers) / 1e9 - (2.0 + expected)) < 0.01
    print("  S11 -3 dB null markers OK")

    # Clipboard: marker value and frequency.
    viewer._refresh_markers()
    row_text = viewer.marker_list.item(0).text()
    viewer._copy_marker_value(0)
    assert QApplication.clipboard().text() == row_text
    viewer._copy_marker_frequency(0)
    assert "GHz" in QApplication.clipboard().text()
    print("  marker clipboard copy OK")

    # Marker state survives a project JSON round-trip.
    state = viewer.marker_state()
    project = Project.new_default("marker-state")
    project.sparam_markers = state
    restored = Project.from_dict(project.to_dict())
    assert restored.sparam_markers == state
    viewer.set_marker_state(restored.sparam_markers)
    assert viewer.marker_state() == state
    print("  marker state project JSON round-trip OK")

    print("S-PARAM SMART MARKERS SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
