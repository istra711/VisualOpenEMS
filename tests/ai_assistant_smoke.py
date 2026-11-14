"""Smoke: AI assistant panel, deterministic diagnosis, apply and undo."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.project import PortDef  # noqa: E402
from app.simulation.report import EngineFacts, Finding, RunReport  # noqa: E402


def main() -> int:
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings.ai_provider = "offline"
    settings.work_dir = tempfile.mkdtemp(prefix="ai_smoke_")
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    tmp = Path(settings.work_dir)
    log = tmp / "sim_log.txt"
    log.write_text(
        "[ERROR] mesh: critical cell found\n"
        "CalcPort failed: port 1 outside metal\n",
        encoding="utf-8",
    )
    win.project.sim.max_timesteps = 100_000
    win.project.ports = [
        PortDef(
            name="p1",
            start=[0.0, 0.0, 0.0],
            stop=[0.0, 0.0, 1.0],
            excite_port=False,
        )
    ]
    win._last_run_report = RunReport(
        verdict="warning",
        headline="not converged (max timesteps)",
        trust="use with care",
        exit_code=0,
        converged=False,
        facts=EngineFacts(steps=100_000, nr_ts=100_000, max_ts_reached=True),
        findings=[
            Finding("warning", "max_ts_reached", "max timesteps reached"),
        ],
    )
    win._last_run_work = tmp
    win._last_run_log_path = log

    with patch("app.ui.main_window.QMessageBox.warning"), patch(
        "app.ui.main_window.QMessageBox.information"
    ):
        win._on_ai_explain_last_run()
        app.processEvents()
        if not win._ai_dock.isVisible():
            print("FAIL: AI dock did not open")
            return 1
        if win._ai_panel.fix_list.count() < 1:
            print("FAIL: no safe fix offered")
            return 2
        fixes = [
            win._ai_panel.fix_list.item(i).data(0x0100)  # Qt.UserRole
            for i in range(win._ai_panel.fix_list.count())
        ]
        fix = next((f for f in fixes if getattr(f, "id", "") == "max_timesteps_2x"), None)
        if fix is None:
            print("FAIL: max_timesteps_2x missing")
            return 3
        old = int(win.project.sim.max_timesteps)
        win._on_ai_apply_fix(fix)
        app.processEvents()
        if int(win.project.sim.max_timesteps) <= old:
            print("FAIL: fix was not applied")
            return 4
        if not win.act_ai_undo.isEnabled():
            print("FAIL: undo action not enabled")
            return 5
        win._on_ai_undo()
        app.processEvents()
        if int(win.project.sim.max_timesteps) != old:
            print("FAIL: undo did not restore the snapshot")
            return 6

        # End-to-end worker path with a mock backend (no network).
        import time

        from app.ai.backend import MockBackend

        win.settings.ai_provider = "opencode"
        win._ai_backend = lambda: MockBackend("MOCK_ASSISTANT_ANSWER")
        win._on_ai_explain_last_run()
        deadline = time.time() + 8.0
        while time.time() < deadline:
            app.processEvents()
            if "MOCK_ASSISTANT_ANSWER" in win._ai_panel.chat_view.toPlainText():
                break
            time.sleep(0.02)
        if "MOCK_ASSISTANT_ANSWER" not in win._ai_panel.chat_view.toPlainText():
            print("FAIL: mock AI answer did not reach the chat panel")
            return 7
        if win._ai_worker is not None:
            print("FAIL: AI worker was not released")
            return 8

    win.close()
    print("AI ASSISTANT SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
