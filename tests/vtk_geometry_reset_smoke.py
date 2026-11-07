"""Smoke: the Field Viewer must not keep geometry ghosts across projects."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.project import Project  # noqa: E402


OLD_BOX = {
    "kind": "box",
    "name": "old_geometry",
    "material": "copper",
    "start": [-5.0, -5.0, 0.0],
    "stop": [5.0, 5.0, 0.1],
}


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from app.ui.vtk_viewer import VtkFieldViewer

    viewer = VtkFieldViewer()
    viewer.set_geometry([OLD_BOX], unit_m=1e-3)
    assert viewer._geom_prims, "old geometry fixture not set"
    viewer.clear()
    assert viewer._geom_prims == [], "clear() left old geometry ghosts"
    assert viewer._geom_actors == [], "clear() left old geometry actors"
    print("  clear() drops geometry ghosts OK")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for name in ("A", "B"):
            work = root / name
            work.mkdir(parents=True)
            project = Project.new_default(name)
            project.primitives = [
                {
                    "kind": "box",
                    "name": f"box_{name}",
                    "material": "copper",
                    "start": [0.0, 0.0, 0.0],
                    "stop": [1.0, 1.0, 0.1],
                }
            ]
            project.save(work / "project.fcsim.json")

        viewer.set_work_dir(root / "A")
        assert [p.get("name") for p in viewer._geom_prims] == ["box_A"]
        viewer.set_work_dir(root / "B")
        assert [p.get("name") for p in viewer._geom_prims] == ["box_B"]
        print("  set_work_dir swaps geometry OK")

        empty_dir = root / "empty"
        empty_dir.mkdir(parents=True)
        Project.new_default("empty").save(empty_dir / "empty.fcsim.json")

        from app.settings import AppSettings
        from app.ui.main_window import MainWindow

        win = MainWindow(AppSettings.load())
        win._on_new()
        win.vtk_viewer.set_geometry([OLD_BOX], unit_m=1e-3)
        assert win.vtk_viewer._geom_prims
        win._open_project_path(empty_dir / "empty.fcsim.json")
        assert win.vtk_viewer._geom_prims == [], (
            "opening a project without results kept old Field Viewer geometry"
        )
        assert win.vtk_viewer._geom_actors == [], (
            "opening a project without results kept old Field Viewer actors"
        )
        print("  opening a project clears old Field Viewer geometry OK")

    print("VTK GEOMETRY RESET SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
