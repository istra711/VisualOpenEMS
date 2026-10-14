"""Menu bar smoke: structure, shortcuts, action -> button delegation.

Standalone (``python tests/menu_smoke.py``): under pytest the dash plugin
stack aborts on the PySide6 import, so this is not a pytest test.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

MENUS = ["File", "Project", "Geometry", "Mesh", "Simulation", "View", "Tools", "Help"]

_APP = None
_WIN = None


def _strip(text: str) -> str:
    return text.replace("&", "")


def _find(menu, label: str):
    for a in menu.actions():
        if _strip(a.text()) == label:
            return a
    raise AssertionError(
        f"{label!r} not in menu (got {[ _strip(a.text()) for a in menu.actions() ]})"
    )


def _menu(label: str):
    for a in _WIN.menuBar().actions():
        if _strip(a.text()) == label:
            m = a.menu()
            assert m is not None, label
            return m
    raise AssertionError(f"menu {label!r} missing")


def _win():
    """One shared QApplication + MainWindow (a second window aborts Qt)."""
    global _APP, _WIN
    if _WIN is None:
        from PySide6.QtWidgets import QApplication

        from app.settings import AppSettings
        from app.ui.main_window import MainWindow

        _APP = QApplication.instance() or QApplication(sys.argv)
        _WIN = MainWindow(AppSettings.load())
    return _WIN


def test_menu_structure_and_shortcuts() -> None:
    w = _win()
    got = [_strip(a.text()) for a in w.menuBar().actions()]
    assert got == MENUS, got

    assert _find(_menu("File"), "New Project").shortcut().toString() == "Ctrl+N"
    assert _find(_menu("File"), "Open\u2026").shortcut().toString() == "Ctrl+O"
    assert _find(_menu("File"), "Save\u2026").shortcut().toString() == "Ctrl+S"
    assert _find(_menu("File"), "Exit").shortcut().toString() == "Ctrl+Q"
    mesh_actions = [_strip(a.text()) for a in _menu("Mesh").actions()]
    assert "Build Mesh" not in mesh_actions, mesh_actions
    assert _find(_menu("Mesh"), "Build CSX + Mesh + Validate").shortcut().toString() == "Ctrl+M"
    assert _find(_menu("Mesh"), "Export CSX XML\u2026") is not None
    assert _find(_menu("Mesh"), "Open in AppCSXCAD (3D)\u2026") is not None
    assert _find(_menu("Simulation"), "Run Simulation").shortcut().toString() == "Ctrl+R"
    assert _find(_menu("View"), "Log Panel").shortcut().toString() == "Ctrl+L"
    assert _find(_menu("Help"), "Help for Current Tab").shortcut().toString() == "F1"
    assert _find(_menu("Geometry"), "Delete Selected").shortcut().toString() == "Del"
    assert _find(_menu("Geometry"), "Duplicate Selected").shortcut().toString() == "Ctrl+D"

    presets = [_strip(a.text()) for a in _menu("Project").actions()]
    assert presets == [
        "Preset: Patch Antenna\u2026",
        "Preset: Yagi Antenna\u2026",
        "Preset: PCB Filter\u2026",
    ], presets
    tabs = [_strip(a.text()) for a in _menu("View").actions()]
    for name in ("Geometry", "Mesh Viewer", "S-Params"):
        assert name in tabs, tabs
    assert "Mesh" not in tabs, f"Mesh tab should be removed (Mesh Parameters window): {tabs}"
    assert "Sim Params" not in tabs, (
        f"Sim Params tab should be removed (Simulation Parameters window): {tabs}"
    )
    assert "Ports / Dumps" not in tabs, (
        f"Ports / Dumps tab should be removed (Ports / Dumps window): {tabs}"
    )
    # the new window is reachable from the Geometry menu
    act = _find(_menu("Geometry"), "Ports / Dumps\u2026")
    assert act is not None, "Geometry menu is missing Ports / Dumps…"
    assert act.shortcut().toString() == "Ctrl+Shift+D"
    print("menus:", got)
    print("MENU STRUCTURE OK")


def test_menu_action_hits_button() -> None:
    w = _win()
    before = w.btn_log_toggle.isChecked()
    _find(_menu("View"), "Log Panel").trigger()
    assert w.btn_log_toggle.isChecked() != before, "Log Panel action did not toggle the button"
    _find(_menu("View"), "Log Panel").trigger()
    assert w.btn_log_toggle.isChecked() == before

    # checkable button state is mirrored on the action
    w.btn_log_toggle.setChecked(not before)
    assert _find(_menu("View"), "Log Panel").isChecked() == w.btn_log_toggle.isChecked()
    w.btn_log_toggle.setChecked(before)

    # menu entries follow the button's enabled state
    w.btn_run.setEnabled(False)
    for m in w.menuBar().actions():
        menu = m.menu()
        if menu is not None:
            menu.aboutToShow.emit()
    assert not _find(_menu("Simulation"), "Run Simulation").isEnabled()
    w.btn_run.setEnabled(True)
    print("MENU ACTION DELEGATION OK")


def test_view_menu_switches_tabs() -> None:
    w = _win()
    for title, idx in w._tab_title_map.items():
        w.tabs.setCurrentIndex(0)
        _find(_menu("View"), title).trigger()  # triggered(bool) must not clobber idx
        assert w.tabs.currentIndex() == idx, (title, w.tabs.currentIndex(), idx)
    # checked-state sync from the live tab
    _find(_menu("View"), "Mesh Viewer").trigger()
    _menu("View").aboutToShow.emit()
    assert _find(_menu("View"), "Mesh Viewer").isChecked()
    assert not _find(_menu("View"), "Geometry").isChecked()
    print("VIEW MENU TAB SWITCH OK")


def test_sim_params_dialog() -> None:
    w = _win()
    act = _find(_menu("Simulation"), "Simulation Parameters\u2026")
    assert act.shortcut().toString() == "Ctrl+Shift+P"
    act.trigger()
    dlg = w._sim_params_dlg
    assert dlg is not None, "dialog not created"
    from PySide6.QtWidgets import QPushButton

    from app.ui.tab_help import help_for

    assert any(
        b.text() == "Help" for b in dlg.findChildren(QPushButton)
    ), "Simulation Parameters dialog has no Help button"
    assert "SIMULATION PARAMETERS" in help_for("Simulation Parameters"), "help topic missing"
    # edit in the dialog, Apply -> project + Sim Params tab both updated
    before = int(w.project.sim.max_timesteps)
    dlg.panel.spn_max_ts.setValue(before + 7)
    dlg.btn_apply.click()
    assert w.project.sim.max_timesteps == before + 7
    assert int(w.sim_params.spn_max_ts.value()) == before + 7
    # second open reuses + re-syncs the same window
    act.trigger()
    assert w._sim_params_dlg is dlg
    # Close without Apply discards edits
    dlg.panel.spn_max_ts.setValue(1)
    dlg.close()
    assert w.project.sim.max_timesteps == before + 7
    print("SIM PARAMS DIALOG OK")


def test_mesh_params_dialog() -> None:
    w = _win()
    act = _find(_menu("Mesh"), "Mesh Parameters\u2026")
    assert act.shortcut().toString() == "Ctrl+Shift+M"
    act.trigger()
    dlg = w._mesh_params_dlg
    assert dlg is not None, "dialog not created"
    assert dlg.isVisible()
    # Help button must exist and the topic must be in TAB_HELP
    from PySide6.QtWidgets import QPushButton

    def _help_btns(widget) -> list:
        out = []
        for child in widget.findChildren(QPushButton):
            if child.text() == "Help":
                out.append(child)
        return out

    helps = _help_btns(dlg)
    assert len(helps) >= 1, "Mesh Parameters dialog has no Help button"
    from app.ui.tab_help import help_for

    assert "MESH PARAMETERS" in help_for("Mesh Parameters"), "help topic missing"
    # edit f0 in the dialog, Apply -> project updated + hidden form mirrored.
    # f0 is the excitation-centred source; start/stop are derived as f0 +/- fc,
    # so an unchanged start spin must NOT clobber f0.
    before_f0 = float(w.project.mesh.target_frequency or 2.45e9)
    dlg.panel.spn_f0.setValue(before_f0 / 1e9 + 0.1)
    dlg.btn_apply.click()
    want_f0 = before_f0 + 1e8
    assert abs(float(w.project.mesh.target_frequency) - want_f0) < 1.0, (
        want_f0,
        w.project.mesh.target_frequency,
    )
    assert abs(float(w.spn_f0.value()) * 1e9 - want_f0) < 1.0, w.spn_f0.value()
    # second open reuses the same window
    act.trigger()
    assert w._mesh_params_dlg is dlg
    # Close discards an unapplied edit (do NOT touch combos: cmb_preset's
    # currentTextChanged triggers the main-window sync and would clobber the
    # project on its own, which is unrelated to Close)
    dlg.panel.spn_f0.setValue(want_f0 / 1e9 + 0.2)  # unapplied edit
    dlg.close()
    assert abs(float(w.project.mesh.target_frequency) - want_f0) < 1.0
    print("MESH PARAMS DIALOG OK")


def test_log_mesh_report_tab() -> None:
    """The log panel has a second tab that shows the same mesh report."""
    w = _win()
    assert hasattr(w, "log_tabs")
    titles = [w.log_tabs.tabText(i) for i in range(w.log_tabs.count())]
    assert "Mesh Report" in titles, titles
    idx = titles.index("Mesh Report")
    # single source: setting the report fills BOTH the Mesh tab and the log tab
    w._set_mesh_report("hello mesh report")
    assert "hello mesh report" in w.mesh_report.toPlainText()
    assert "hello mesh report" in w.log_mesh_report.toPlainText()
    w.log_tabs.setCurrentIndex(idx)
    assert w.log_tabs.currentWidget() is w.log_mesh_report
    w._set_mesh_report("")  # reset
    print("LOG MESH REPORT TAB OK")


def test_mesh_viewer_scene_info_tab() -> None:
    """The 'Mesh Viewer Scene Info' tab mirrors the Mesh Viewer's info_view."""
    w = _win()
    assert hasattr(w, "mesh_viewer_scene_info")
    titles = [w.log_tabs.tabText(i) for i in range(w.log_tabs.count())]
    assert "Mesh Viewer Scene Info" in titles, titles
    idx = titles.index("Mesh Viewer Scene Info")
    assert w.log_tabs.indexOf(w.mesh_viewer_scene_info) == idx
    w.log_tabs.setCurrentIndex(idx)
    assert w.log_tabs.currentWidget() is w.mesh_viewer_scene_info
    # mirror fills the tab from the viewer's info_text()
    w.mesh_viewer._info_text = "hello scene info"
    w._sync_scene_info()
    assert "hello scene info" in w.mesh_viewer_scene_info.toPlainText()

    # viewer still holds its own copy
    assert "hello scene info" in w.mesh_viewer.info_text()
    print("MESH VIEWER SCENE INFO TAB OK")


def main() -> int:
    failed = 0
    for t in (
        test_menu_structure_and_shortcuts,
        test_menu_action_hits_button,
        test_view_menu_switches_tabs,
        test_sim_params_dialog,
        test_mesh_params_dialog,
        test_log_mesh_report_tab,
        test_mesh_viewer_scene_info_tab,
    ):
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {t.__name__}: {e!r}")
    print("Menu tests:", "PASS" if failed == 0 else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
