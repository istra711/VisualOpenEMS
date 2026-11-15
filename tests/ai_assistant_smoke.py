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

    knowledge_dir = tmp / "knowledge"
    knowledge_dir.mkdir()
    (knowledge_dir / "pml.md").write_text(
        "# PML boundary\n\n"
        "Use FDTD.SetBoundaryCond with PML_8 on all six faces. "
        "AddLumpedPort defines a lumped port for S-parameter extraction.\n",
        encoding="utf-8",
    )
    from app.ai.knowledge import invalidate_knowledge_cache

    win.settings.ai_knowledge_enabled = True
    win.settings.ai_knowledge_paths = str(knowledge_dir)
    win.settings.ai_knowledge_max_chars = 3000
    invalidate_knowledge_cache()

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

        # Local knowledge: retrieval, citation and the Sources label.
        prompt = win._ai_prompt(
            "How do I configure a PML boundary and an AddLumpedPort?"
        )
        if "[K" not in prompt or "LOCAL KNOWLEDGE" not in prompt:
            print("FAIL: local knowledge excerpts missing from the prompt")
            return 9
        bundle = win._ai_panel.knowledge_bundle()
        if bundle is None or not bundle.enabled or not bundle.hits:
            print("FAIL: knowledge bundle was not stored in the panel")
            return 10
        if bundle.hits[0].path not in prompt:
            print("FAIL: cited knowledge path missing from the prompt")
            return 15
        if not win._ai_panel.lbl_knowledge.text().startswith("Local knowledge:"):
            print("FAIL: knowledge status label not updated")
            return 11
        if not win._ai_panel.btn_sources.isEnabled():
            print("FAIL: Sources button is disabled")
            return 12

        win.settings.ai_knowledge_enabled = False
        invalidate_knowledge_cache()
        win._ai_knowledge = None
        prompt_off = win._ai_prompt("PML boundary")
        if "Local knowledge is disabled" not in prompt_off:
            print("FAIL: disabled knowledge note missing from the prompt")
            return 13
        disabled_bundle = win._ai_panel.knowledge_bundle()
        if disabled_bundle is None or disabled_bundle.enabled or disabled_bundle.hits:
            print("FAIL: disabled knowledge still contributed excerpts")
            return 16
        if "off" not in win._ai_panel.lbl_knowledge.text().lower():
            print("FAIL: disabled knowledge status not shown")
            return 14
        win.settings.ai_knowledge_enabled = True
        invalidate_knowledge_cache()

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
