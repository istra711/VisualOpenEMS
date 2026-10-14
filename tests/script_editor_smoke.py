"""Smoke: script editor dialog, MainWindow Script button, edited-script backup."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.settings import AppSettings  # noqa: E402


def _patch_modal_dialogs() -> list:
    """Record modal calls instead of blocking (offscreen test)."""
    seen: list[tuple] = []

    def _critical(parent, title, text, *a, **k):
        seen.append(("critical", title, text))
        return QMessageBox.StandardButton.Ok

    def _warning(parent, title, text, *a, **k):
        seen.append(("warning", title, text))
        return QMessageBox.StandardButton.Ok

    def _question(parent, title, text, *a, **k):
        seen.append(("question", title, text))
        return QMessageBox.StandardButton.Yes

    QMessageBox.critical = _critical
    QMessageBox.warning = _warning
    QMessageBox.question = _question
    return seen


def _wait_for_log(win, needle: str, timeout: float = 3.0) -> bool:
    """Log lines are queued and drained by a QTimer — wait for the text."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if needle in win.log_view.toPlainText():
            return True
        QApplication.processEvents()
        time.sleep(0.05)
    return needle in win.log_view.toPlainText()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    modal_calls = _patch_modal_dialogs()

    from app.ui.script_editor import ScriptEditorDialog

    work = ROOT / "work" / "script_editor_smoke"
    work.mkdir(parents=True, exist_ok=True)
    script = work / "project_run.py"
    script.write_text("# generated v1\nprint('run')\n", encoding="utf-8", newline="\n")

    dlg = ScriptEditorDialog(script)
    assert dlg.to_text().startswith("# generated v1"), "initial load failed"
    assert not dlg.is_modified()

    # edit + save
    dlg.editor.setPlainText("# edited by user\nprint('custom')\n")
    assert dlg.is_modified()
    assert "Edited" in dlg.lbl_status.text(), dlg.lbl_status.text()
    assert dlg.save()
    assert script.read_text(encoding="utf-8") == "# edited by user\nprint('custom')\n"
    assert not dlg.is_modified()

    # regenerate from a callback (what MainWindow passes)
    def regenerate() -> Path:
        script.write_text("# regenerated\n", encoding="utf-8", newline="\n")
        return script

    dlg.regenerate_cb = regenerate
    dlg._on_regenerate()
    assert dlg.to_text() == "# regenerated\n", dlg.to_text()
    assert not dlg.is_modified()
    assert not modal_calls, modal_calls

    # revert discards edits
    dlg.editor.setPlainText("scratch")
    assert dlg.is_modified()
    dlg._on_reload()
    assert dlg.to_text() == "# regenerated\n"
    dlg.close()

    # ---- MainWindow wiring -------------------------------------------------
    from app.ui.main_window import MainWindow

    win = MainWindow(AppSettings.load())
    win.show()
    app.processEvents()

    assert hasattr(win, "btn_script") and win.btn_script.text().startswith("Script")
    assert hasattr(win, "btn_pcb")

    win._on_edit_script()
    app.processEvents()
    ed = win._script_dialog
    assert ed is not None, "script dialog not opened"
    assert ed.isVisible(), "script dialog not visible"
    assert "def main" in ed.to_text(), "generated script content missing"
    # second click reuses the open dialog
    win._on_edit_script()
    app.processEvents()
    assert win._script_dialog is ed, "dialog should be reused"

    # edited script is backed up on the next generation
    work_dir = win.settings.work_path() / (win.project.name or "project")
    run_py = work_dir / "project_run.py"
    lastgen = work_dir / "project_run.py.lastgen"
    assert run_py.exists() and lastgen.exists(), "lastgen marker missing"
    run_py.write_text("# hand edited\n", encoding="utf-8", newline="\n")
    win._generate_script(work_dir)
    backup = work_dir / "project_run_edited.py"
    assert backup.exists(), "edited script not backed up"
    assert backup.read_text(encoding="utf-8") == "# hand edited\n"
    assert _wait_for_log(win, "backed up")

    # unedited regeneration does not create a new backup
    backup.unlink()
    win._generate_script(work_dir)
    assert not backup.exists(), "false-positive backup"

    # PCB filter preset (analytic fallback — the FreeCAD path has its own smoke)
    win._on_load_pcb(use_freecad=False)
    app.processEvents()
    assert win.project.name == "pcb_filter"
    assert len(win.project.ports) == 2
    assert {p.name for p in win.project.ports} == {"in1", "out1"}
    assert any(p.get("name") == "msl_stub" for p in win.primitives)
    assert "analytic" in win.geom_info.toPlainText().lower()

    ed.close()
    win.close()
    print("SCRIPT EDITOR SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
