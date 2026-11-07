"""Smoke: main-window close prompt for unsaved markers/settings."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.models.project import Project  # noqa: E402
from app.settings import AppSettings  # noqa: E402


def _network():
    import skrf as rf

    f = np.linspace(1e9, 3e9, 101)
    s = np.zeros((len(f), 1, 1), dtype=complex)
    s[:, 0, 0] = 0.5 * np.exp(1j * 0.1)
    return rf.Network(frequency=f, s=s, z0=50)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from app.ui.main_window import MainWindow

    tmp = Path(tempfile.mkdtemp(prefix="voems-close-"))
    win = MainWindow(AppSettings(work_dir=str(tmp)))
    assert not win._has_unsaved_project_changes(), "fresh window must be clean"
    print("  fresh window is clean OK")

    # A marker makes the project dirty.
    viewer = win.sparam_viewer
    viewer.network = _network()
    viewer._populate_ports()
    viewer.cmb_plot.setCurrentText("|S| dB")
    viewer._redraw()
    viewer._add_marker_at_freq(2e9)
    assert win.project.sparam_markers, "marker must reach the project"
    assert win._has_unsaved_project_changes(), "marker must mark project dirty"
    print("  marker change is dirty OK")

    original_warning = QMessageBox.warning
    try:
        QMessageBox.warning = lambda *a, **k: QMessageBox.StandardButton.Cancel
        assert not win._confirm_close_with_unsaved_changes()
        print("  Cancel keeps window open OK")

        QMessageBox.warning = lambda *a, **k: QMessageBox.StandardButton.Discard
        assert win._confirm_close_with_unsaved_changes()
        print("  Discard closes OK")

        # Save clears the dirty state and writes markers to the project JSON.
        proj_path = tmp / "close-prompt.fcsim.json"
        proj_path.write_text("{}", encoding="utf-8")
        win._current_file_path = proj_path
        QMessageBox.warning = lambda *a, **k: QMessageBox.StandardButton.Save
        assert win._confirm_close_with_unsaved_changes()
        data = json.loads(proj_path.read_text(encoding="utf-8"))
        assert data.get("sparam_markers")
        assert not win._has_unsaved_project_changes()
        print("  Save writes markers and clears dirty state OK")
    finally:
        QMessageBox.warning = original_warning

    # Form/settings changes are also detected.
    win.spn_f0.setValue(win.spn_f0.value() + 0.1)
    assert win._has_unsaved_project_changes(), "form change must be dirty"
    win._mark_project_saved()
    assert not win._has_unsaved_project_changes()
    print("  form/settings change is dirty OK")

    # Opening a project without loaded results keeps its saved markers.
    proj_path2 = tmp / "missing-results.fcsim.json"
    project = Project.new_default("missing-results")
    project.sparam_markers = [{"f": 2e9, "tag": "", "delta_ref": None}]
    project.save(proj_path2)
    win2 = MainWindow(AppSettings(work_dir=str(tmp)))
    win2._open_project_path(proj_path2)
    assert win2.project.sparam_markers == project.sparam_markers
    assert not win2._has_unsaved_project_changes()
    print("  markers survive open without results OK")

    print("MAIN-WINDOW CLOSE PROMPT SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
