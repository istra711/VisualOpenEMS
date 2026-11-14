"""Smoke: previous-run S-param reference through the MainWindow.

Creates a temporary project with two committed runs, uses the older run as the
S-param reference, saves the project, and verifies that reopening restores the
reference and the provenance descriptor.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.project import Project  # noqa: E402
from app.settings import AppSettings  # noqa: E402


def _touchstone(path: Path, level_db: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["! test", "# Hz S RI R 50"]
    for freq in (1e9, 2e9, 3e9):
        lines.append(f"{freq:.0f} {level_db} 0 0.5 0 0.5 0 {level_db} 0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run(root: Path, run_id: str, status: str = "complete") -> Path:
    run = root / "runs" / run_id
    (run / "sparams").mkdir(parents=True, exist_ok=True)
    _touchstone(run / "sparams" / "sparams.s2p", -10.0)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": status,
                "created": "2026-01-01T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    return run


def main() -> int:
    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="sparam_run_ref_"))
    project_dir = tmp / "RefProj"
    old_run = _run(project_dir, "20260101-110000-aaaaaa")
    active_run = _run(project_dir, "20260102-120000-bbbbbb")
    project_json = tmp / "RefProj.fcsim.json"

    project = Project.new_default("RefProj")
    project.results = {
        "project_dir": str(project_dir),
        "sim_dir": str(active_run),
        "active_run": str(active_run),
        "run_id": active_run.name,
        "sparams": str(active_run / "sparams" / "sparams.s2p"),
        "time": "2026-01-02T12:00:00",
    }
    project.save(project_json)

    from app.ui.main_window import MainWindow

    win = MainWindow(AppSettings.load())
    win._open_project_path(project_json)
    win._on_show_sparams()
    app.processEvents()
    if win.sparam_viewer.network is None:
        print("FAIL: active-run S-parameters did not load")
        return 1

    win._on_compare_previous_run()
    app.processEvents()
    desc = win.sparam_viewer.reference_descriptor()
    if not desc or desc.get("run_id") != old_run.name:
        print("FAIL: previous run not used as reference:", desc)
        return 2
    if not win.project.sparam_reference or win.project.sparam_reference.get("run_id") != old_run.name:
        print("FAIL: reference descriptor not mirrored into the project")
        return 3
    if win.sparam_viewer.reference_path != old_run / "sparams" / "sparams.s2p":
        print("FAIL: wrong reference path:", win.sparam_viewer.reference_path)
        return 4

    win.sparam_viewer.clear_reference()
    if win.project.sparam_reference is not None:
        print("FAIL: clearing the viewer reference did not clear the project")
        return 4
    win._on_compare_previous_run()
    win.sparam_viewer.set_port_map([2, 1])
    if win.project.sparam_reference.get("port_map") != [2, 1]:
        print("FAIL: mapped ports not mirrored into the project")
        return 4

    win._on_save()
    win.close()
    app.processEvents()

    win2 = MainWindow(AppSettings.load())
    win2._open_project_path(project_json)
    app.processEvents()
    if win2.sparam_viewer.network is None:
        print("FAIL: reloaded project has no current S-parameters")
        return 5
    desc2 = win2.sparam_viewer.reference_descriptor()
    if not desc2 or desc2.get("run_id") != old_run.name:
        print("FAIL: saved reference not restored:", desc2)
        return 6
    if desc2.get("port_map") != [2, 1]:
        print("FAIL: saved port map not restored:", desc2)
        return 6
    if win2.sparam_viewer._mapped_reference_key("S21") != "S12":
        print("FAIL: restored port map is not active")
        return 6
    win2.close()

    print("SPARAM RUN REFERENCE SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
