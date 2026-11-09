"""Smoke: staging -> explicit commit -> immutable runs -> Save As copy."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from app.models.project import Project  # noqa: E402
from app.postproc.sparams import export_sparams  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.storage.run_store import ProjectStore  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def _write_run_outputs(staging: Path, value: float) -> None:
    table = {
        "freq": [1e9, 2e9],
        "S11": [complex(value, 0.05), complex(value + 0.1, -0.02)],
    }
    export_sparams(table, staging / "sparams", basename="sparams")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="staging_flow_"))
    settings = AppSettings(work_dir=str(tmp))
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    project_path = tmp / "StageProj.fcsim.json"
    win.project = Project.new_default("StageProj")
    win.project.results = {}
    win.primitives = []
    win._current_file_path = project_path
    win.project.save(project_path)
    win._project_store_obj = None

    # First run: staged, not visible as active result until commit.
    staging1 = win._fresh_staged_run()
    _write_run_outputs(staging1, 0.1)
    win._staged_run_status = "complete"
    assert win.project.results == {}
    run1 = win._commit_staged_run()
    assert run1 is not None and run1.is_dir()
    assert not staging1.exists(), "staging should be moved on commit"
    assert (run1 / "manifest.json").is_file()
    active1 = (win.project.results or {}).get("active_run")
    assert active1, win.project.results
    assert Path(active1).parent.name == "runs", active1

    # Second run: a new immutable run, the first one must survive.
    staging2 = win._fresh_staged_run()
    _write_run_outputs(staging2, 0.2)
    win._staged_run_status = "complete"
    run2 = win._commit_staged_run()
    assert run2 is not None and run2 != run1
    assert run1.is_dir(), "old committed run was deleted"
    assert (run1 / "sparams" / "sparams.json").is_file()
    active2 = (win.project.results or {}).get("active_run")
    assert active2 and active2 != active1

    # Re-opening the project restores exactly the committed active run.
    win2 = MainWindow(settings)
    win2._open_project_path(project_path)
    app.processEvents()
    assert win2._active_run_dir() == run2, win2._active_run_dir()
    assert win2.sparam_viewer.network is not None, "reopened S-params missing"
    win2.close()

    # Save As with a pending third run: old project/data untouched, new project
    # gets a copy of the staged run.
    old_json = project_path.read_bytes()
    staging3 = win._fresh_staged_run()
    _write_run_outputs(staging3, 0.3)
    win._staged_run_status = "complete"
    new_path = tmp / "CopyProj.fcsim.json"
    with patch.object(
        QFileDialog, "getSaveFileName", return_value=(str(new_path), "")
    ):
        win._on_save_as()
    assert project_path.read_bytes() == old_json, "Save As modified the old project JSON"
    assert run1.is_dir() and run2.is_dir(), "Save As deleted old runs"
    assert not staging3.exists(), "staged run should be copied and consumed"
    assert new_path.is_file()
    new_project = json.loads(new_path.read_text(encoding="utf-8"))
    new_active = (new_project.get("results") or {}).get("active_run")
    assert new_active, new_project.get("results")
    new_run = ProjectStore(new_path.parent / "CopyProj").active_run_dir(
        new_project.get("results")
    )
    assert new_run is not None and new_run.is_dir(), new_run
    assert (new_run / "sparams" / "sparams.json").is_file()
    assert win.sparam_viewer.network is not None

    win.close()
    print("STAGING FLOW SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
