"""Smoke: background mesh build (worker thread).

Builds a real mesh through ``MainWindow._on_build_mesh`` (which now runs
``app.meshing.build_task`` on a ``MeshBuildThread``) and asserts the GUI is
finished: report filled, csx.xml written, viewer loaded, button re-enabled.
Standalone (offscreen Qt).
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.models.project import Project
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="mesh_thread_smoke_"))

    settings = AppSettings.load()
    settings.work_dir = str(tmp)
    win = MainWindow(settings)
    win.project = Project.new_default("mesh_build")
    win.primitives = [
        {
            "kind": "box",
            "name": "board",
            "material": "copper",
            "priority": 10,
            "start": [0.0, 0.0, 0.0],
            "stop": [10.0, 5.0, 1.6],
        }
    ]
    win.project.primitives = list(win.primitives)

    win._on_build_mesh()
    assert win._mesh_building, "mesh build did not start"
    assert not win.btn_build_mesh.isEnabled(), "build button must be disabled while busy"

    deadline = time.time() + 90
    while win._mesh_building and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert not win._mesh_building, "mesh build did not finish in time"
    assert win.btn_build_mesh.isEnabled(), "build button not re-enabled"

    report = win.mesh_report.toPlainText()
    assert "Mesh setup" in report, report[:400]
    assert win.mesh_viewer._csx is not None, "viewer did not get the built CSX"
    csx = tmp / "mesh_build" / "csx.xml"
    assert csx.exists(), f"csx.xml missing under {tmp / 'mesh_build'}"

    # Regression: num_lines = 1 (old Mesh-Parameters minimum) made easyMesh die
    # with "zero-size array to reduction operation minimum which has no identity"
    # (it samples + diffs num_lines points per edge gap). Clamped to >= 2 now.
    # num_grading = 1.0 broke CSXCAD's cell cap -> the mesh failed validation
    # (cell_too_large) and the run aborted; clamped to >= 1.05 now.
    win.project.mesh.num_lines = 1
    win.project.mesh.max_grading_ratio = 1.0
    win._on_build_mesh()
    deadline = time.time() + 90
    while win._mesh_building and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert not win._mesh_building, "mesh build (num_lines=1) did not finish in time"
    report = win.mesh_report.toPlainText()
    assert "zero-size array" not in report, report[:400]
    assert "Mesh setup" in report, report[:400]
    assert "[ERROR]" not in report, report[:400]  # no cell_too_large either
    assert "cell_too_large" not in report, report[:400]
    print("  num_lines / grading clamped (no numpy or cell_too_large error) OK")

    win.close()
    print("MESH THREAD SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
