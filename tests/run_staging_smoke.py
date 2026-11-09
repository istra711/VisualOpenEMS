"""Smoke: Run Simulation writes to staging and only Commit makes it active."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.project import Project  # noqa: E402
from app.postproc.sparams import export_sparams  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


class FakeRunner:
    def __init__(self) -> None:
        self.last_exit = None
        self.log_path = None

    def is_running(self) -> bool:
        return False

    def start(self, script_path, log_cb=None) -> None:
        root = Path(script_path).parent
        (root / "sparams").mkdir(parents=True, exist_ok=True)
        export_sparams(
            {"freq": [1e9, 2e9], "S11": [0.1 + 0.02j, 0.2 + 0.01j]},
            root / "sparams",
            basename="sparams",
        )
        (root / "csx.xml").write_text("<xml/>", encoding="utf-8")
        self.log_path = root / "sim_log.txt"
        self.log_path.write_text("fake run\n", encoding="utf-8")
        self.last_exit = 0
        if log_cb:
            log_cb("fake run done")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="run_staging_"))
    settings = AppSettings(work_dir=str(tmp))
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    project_path = tmp / "RunProj.fcsim.json"
    win.project = Project.new_default("RunProj")
    win.project.results = {}
    win.primitives = [
        {
            "kind": "box",
            "name": "trace",
            "material": "copper",
            "start": [0.0, 0.0, 0.0],
            "stop": [1.0, 1.0, 0.0],
        }
    ]
    win._current_file_path = project_path
    win.project.save(project_path)
    before_json = project_path.read_bytes()
    win._project_store_obj = None
    win.runner = FakeRunner()

    win._on_run()
    QTest.qWait(1400)
    app.processEvents()
    assert project_path.read_bytes() == before_json, (
        "starting a run must not modify the saved project JSON"
    )

    staging = win._staged_run_dir
    assert staging is not None and staging.is_dir(), "run did not use staging"
    assert (staging / "project_run.py").is_file()
    assert (staging / "sparams" / "sparams.json").is_file()
    assert not (win.project.results or {}).get("active_run"), (
        "run became active before an explicit commit"
    )
    assert win.sparam_viewer.network is not None
    assert win._staged_run_status == "complete", win._staged_run_status

    run = win._commit_staged_run()
    assert run is not None and run.is_dir()
    assert (win.project.results or {}).get("active_run")
    assert not staging.exists()

    win.close()
    print("RUN STAGING SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
