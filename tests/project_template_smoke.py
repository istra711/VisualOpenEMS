"""Smoke: user project template save dialog, manager and project install."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.project import PortDef, Project  # noqa: E402


def main() -> int:
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow
    from app.ui.project_template_dialogs import (
        ProjectTemplateDialog,
        SaveProjectTemplateDialog,
    )

    app = QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings.ai_provider = "offline"
    settings.work_dir = tempfile.mkdtemp(prefix="template_smoke_work_")
    settings.template_dir = tempfile.mkdtemp(prefix="template_smoke_store_")
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    project = Project.new_default("Smoke Template Project")
    project.primitives = [
        {
            "kind": "box",
            "name": "smoke_patch",
            "material": "copper",
            "start": [-4.0, -4.0, 1.6],
            "stop": [4.0, 4.0, 1.635],
        }
    ]
    project.ports = [
        PortDef(
            name="p1",
            start=[0.0, 0.0, 0.0],
            stop=[0.0, 0.0, 1.0],
            excite_port=True,
        )
    ]
    win.project = project
    win.primitives = list(project.primitives)
    original_id = project.project_id

    # --- save dialog ------------------------------------------------------
    save_dlg = SaveProjectTemplateDialog(win.project, win.primitives, settings)
    if "Smoke Template Project" not in save_dlg.ed_name.text():
        print("FAIL: save dialog did not prefill the project name")
        return 1
    save_dlg.ed_name.setText("Dialog Template")
    save_dlg.ed_description.setPlainText("Created by the template smoke test.")
    save_dlg._save()
    if save_dlg.template_info is None or not save_dlg.template_info.path.exists():
        print("FAIL: save dialog did not write a template")
        return 2
    print("  save dialog wrote", save_dlg.template_info.path.name)

    # --- manager: preview + selection ------------------------------------
    from app.project_templates import save_project_template

    save_project_template(
        settings.template_dir,
        win.project,
        win.primitives,
        "Smoke Template",
        "Reusable patch/port starting point.",
    )
    dlg = ProjectTemplateDialog(settings)
    if dlg.list.count() != 2:
        print("FAIL: manager expected 2 templates, got", dlg.list.count())
        return 3
    target = None
    for i in range(dlg.list.count()):
        item = dlg.list.item(i)
        if item.text() == "Smoke Template":
            target = item
            break
    if target is None:
        print("FAIL: Smoke Template missing from manager")
        return 4
    dlg.list.setCurrentItem(target)
    app.processEvents()
    if dlg.selected_template() is None:
        print("FAIL: manager did not select the template")
        return 5
    preview = dlg.preview.toPlainText()
    if "Reusable patch/port starting point" not in preview:
        print("FAIL: manager preview misses the description")
        return 6
    if not dlg.btn_new.isEnabled():
        print("FAIL: New project button is disabled")
        return 7
    loaded = dlg.load_selected()
    if loaded is None or loaded.info.name != "Smoke Template":
        print("FAIL: manager could not load the selection")
        return 8

    # --- install as a fresh unsaved project ------------------------------
    win.vtk_viewer._series = [object()]
    win.vtk_viewer._current_path = Path("old_field.vtr")
    win._install_template_project(loaded)
    app.processEvents()
    if win.project.name != "Smoke Template":
        print("FAIL: installed project name:", win.project.name)
        return 9
    if win.project.project_id == original_id:
        print("FAIL: template install reused the project id")
        return 10
    if win.project.results != {}:
        print("FAIL: template install kept results:", win.project.results)
        return 11
    if win._current_file_path is not None:
        print("FAIL: template install set a project file path")
        return 12
    if win._saved_project_sig != "__template_unsaved__":
        print("FAIL: template project is not marked unsaved")
        return 13
    if win.vtk_viewer._series:
        print("FAIL: old Field Viewer data survived the template install")
        return 14
    if "New unsaved project" not in win.geom_info.toPlainText():
        print("FAIL: template summary/hint missing")
        return 15
    if len(win.primitives) != 1 or win.primitives[0]["name"] != "smoke_patch":
        print("FAIL: template primitives missing")
        return 16

    save_dlg.close()
    dlg.close()
    win.close()
    print("PROJECT TEMPLATE SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
