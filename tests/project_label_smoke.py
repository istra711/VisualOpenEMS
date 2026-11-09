"""Smoke: current project name is visible in every viewer tab/pop-out."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.settings import AppSettings  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="project_label_"))
    win = MainWindow(AppSettings(work_dir=str(tmp)))
    win.project.name = "DemoProject"
    win._update_window_title()
    app.processEvents()

    for attr in (
        "mesh_viewer",
        "design_viewer",
        "sparam_viewer",
        "antenna_viewer",
        "vtk_viewer",
    ):
        viewer = getattr(win, attr)
        assert viewer.project_name == "DemoProject", (attr, viewer.project_name)
        assert "DemoProject" in viewer.lbl_project.text(), (
            attr,
            viewer.lbl_project.text(),
        )
        viewer.set_project_name("RenamedProject")
        assert "RenamedProject" in viewer.lbl_project.text()

    # Docked tab labels are not renamed (help/tab persistence relies on the
    # stable base names); the project name is shown in the viewer header.
    assert win.tabs.tabText(win.mesh_viewer_tab_index) == "Mesh Viewer"

    # Pop-outs carry the project name in their window titles too.
    win.project.name = "DemoProject"
    win._update_window_title()
    win.sparam_viewer.popout_graph()
    app.processEvents()
    graph = win.sparam_viewer._graph_win
    assert graph is not None and "DemoProject" in graph.windowTitle()
    win.sparam_viewer.restore_graph_to_tab()
    win.antenna_viewer.popout_graph()
    app.processEvents()
    graph = win.antenna_viewer._graph_win
    assert graph is not None and "DemoProject" in graph.windowTitle()
    win.antenna_viewer.restore_graph_to_tab()
    win.vtk_viewer.popout()
    app.processEvents()
    pop = win.vtk_viewer._popout_win
    assert pop is not None and "DemoProject" in pop.windowTitle()
    win.vtk_viewer.restore_from_popout()
    win.design_viewer.popout()
    app.processEvents()
    pop = win.design_viewer._popout_win
    assert pop is not None and "DemoProject" in pop.windowTitle()
    win.design_viewer.restore_from_popout()

    win.close()
    print("PROJECT LABEL SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
