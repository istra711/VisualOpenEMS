"""Smoke: log panel toggle on/off — single instance, no dock, no Logs tab."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

from app.settings import AppSettings  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    settings = AppSettings.load()
    settings.log_panel_visible = True
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    assert not hasattr(win, "dock_log"), "dock_log must be gone"
    assert not hasattr(win, "log_view_tab"), "log_view_tab must be gone"
    assert hasattr(win, "log_panel") and hasattr(win, "log_view")
    assert isinstance(win.log_view, QPlainTextEdit)
    tabs = [win.sparam_tabs.tabText(i) for i in range(win.sparam_tabs.count())]
    assert "Logs" not in tabs, f"Logs tab must be gone, got {tabs}"
    assert win.log_panel.isVisible()
    assert win.btn_log_toggle.isChecked()

    win._log("hello toggle test")
    win._drain_log_queue()
    app.processEvents()
    assert "hello toggle test" in win.log_view.toPlainText()

    win.btn_log_toggle.setChecked(False)
    app.processEvents()
    assert not win.log_panel.isVisible(), "panel should hide"
    assert not win.btn_log_toggle.isChecked()

    win._log("still queued while hidden")
    win._drain_log_queue()
    app.processEvents()
    assert "still queued while hidden" in win.log_view.toPlainText()

    win.btn_log_toggle.setChecked(True)
    app.processEvents()
    assert win.log_panel.isVisible(), "panel should show again"
    assert win.btn_log_toggle.isChecked()

    win._clear_logs()
    app.processEvents()
    assert win.log_view.toPlainText() == "" or win.lbl_log_count.text().startswith("0")

    win.close()
    print("LOG TOGGLE OK", tabs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
