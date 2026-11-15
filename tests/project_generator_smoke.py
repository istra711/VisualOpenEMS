"""Smoke: Phase-2 generator preview, project installation, patch apply/undo."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ai.preflight import preflight  # noqa: E402
from app.ai.project_patch import apply_project_patch, parse_project_patch  # noqa: E402
from app.ai.templates import build_template  # noqa: E402


def main() -> int:
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow
    from app.ui.project_generator import ProjectGeneratorDialog
    from app.ui.project_patch_dialog import ProjectPatchDialog

    app = QApplication.instance() or QApplication([])
    settings = AppSettings()
    settings.ai_provider = "offline"
    settings.work_dir = tempfile.mkdtemp(prefix="phase2_smoke_")
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    with patch("app.ui.main_window.QMessageBox.warning"), patch(
        "app.ui.main_window.QMessageBox.information"
    ):
        dialog = ProjectGeneratorDialog(settings, win)
        dialog.show()
        app.processEvents()
        if dialog.cmb_template.count() < 5:
            print("FAIL: template catalog is too small")
            return 1
        if not dialog._build_preview():
            print("FAIL: patch template preview failed")
            print(dialog.preview.toPlainText())
            return 2
        generated = dialog.result
        if generated is None or generated.template_id != "patch":
            print("FAIL: patch template result missing")
            return 3
        if not preflight(generated.project, generated.primitives).ok:
            print("FAIL: patch template preflight")
            return 4
        dialog.cmb_template.setCurrentIndex(dialog.cmb_template.findData("yagi"))
        if not dialog._build_preview():
            print("FAIL: yagi template preview failed")
            return 5

        from app.ai.backend import MockBackend

        def wait_for(condition, timeout=8.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                app.processEvents()
                if condition():
                    return True
                time.sleep(0.02)
            return False

        ai_answer = json.dumps(
            {
                "template": "patch",
                "title": "AI patch",
                "parameters": {"f0": 2.4, "eps_r": 4.4},
                "assumptions": ["test assumption"],
            }
        )
        dialog._backend = lambda: MockBackend(ai_answer)
        dialog.edt_description.setPlainText("2.4 GHz patch on FR4")
        dialog._on_ai_suggest()
        if not wait_for(lambda: dialog._worker is None):
            print("FAIL: generator AI suggestion did not finish")
            return 6
        if str(dialog.cmb_template.currentData()) != "patch":
            print("FAIL: AI suggestion did not select the patch template")
            return 7
        if not dialog._build_preview():
            print("FAIL: AI-filled preview failed")
            return 8
        dialog.close()

        win._install_generated_project(generated, preflight(generated.project, generated.primitives))
        app.processEvents()
        if win.project.name != "generated_patch":
            print("FAIL: generated project was not installed:", win.project.name)
            return 9
        if not win.primitives:
            print("FAIL: generated primitives missing")
            return 10

        before = int(win.project.sim.max_timesteps)
        proposal, errors = parse_project_patch(
            {
                "title": "Raise timesteps",
                "operations": [
                    {"op": "set", "path": "sim.max_timesteps", "value": before + 50000}
                ],
            }
        )
        if errors or proposal is None:
            print("FAIL: patch parse:", errors)
            return 8
        result, result_primitives, errors = apply_project_patch(
            win.project, win.primitives, proposal
        )
        if errors:
            print("FAIL: patch apply:", errors)
            return 9
        win._install_project_patch(proposal, result, result_primitives)
        app.processEvents()
        if int(win.project.sim.max_timesteps) != before + 50000:
            print("FAIL: patch was not installed")
            return 10
        if not win.act_ai_undo.isEnabled():
            print("FAIL: patch undo is disabled")
            return 11
        win._on_ai_undo()
        app.processEvents()
        if int(win.project.sim.max_timesteps) != before:
            print("FAIL: patch undo did not restore the project")
            return 12

        edit = ProjectPatchDialog(win.project, win.primitives, settings, win)
        edit.show()
        app.processEvents()
        edit_answer = json.dumps(
            {
                "title": "Mock edit",
                "operations": [
                    {"op": "set", "path": "sim.max_timesteps", "value": before + 12345}
                ],
            }
        )
        edit._backend = lambda: MockBackend(edit_answer)
        edit.edt_instruction.setPlainText("Raise max timesteps")
        edit._on_ask()
        if not wait_for(lambda: edit._worker is None):
            print("FAIL: patch dialog AI request did not finish")
            return 13
        if not edit.btn_apply.isEnabled() or edit.result_project is None:
            print("FAIL: patch dialog did not accept the mock proposal")
            print(edit.preview.toPlainText()[:1000])
            return 14
        edit.close()

    win.close()
    print("PROJECT GENERATOR SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
