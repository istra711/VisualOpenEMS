"""Smoke: per-tab help content + MainWindow Help wiring."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from app.ui.tab_help import TAB_HELP, TAB_ORDER, help_for, show_tab_help

    for name in TAB_ORDER:
        text = help_for(name)
        assert text and len(text) > 80, name
        assert name in TAB_HELP, name
        if name == "Geometry":
            assert "GEOMETRY" in text
    print("content ok:", list(TAB_HELP))

    from PySide6.QtWidgets import QApplication

    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(AppSettings.load())
    tabs = win.sparam_tabs
    titles = [tabs.tabText(i) for i in range(tabs.count())]
    print("tabs:", titles)
    # Every main-window tab must have a help topic. TAB_ORDER may also hold
    # Help-menu-only topics (e.g. "Antenna Simulation") that are not tabs.
    for title in titles:
        assert title in TAB_ORDER, title
    for name in TAB_ORDER:
        if name in titles:
            assert name in TAB_HELP, name

    assert hasattr(win, "btn_help") and win.btn_help is not None
    # Help button title follows current tab
    idx = titles.index("Mesh Viewer")
    tabs.setCurrentIndex(idx)
    app.processEvents()
    print("btn_help:", win.btn_help.text())
    assert "Mesh Viewer" in win.btn_help.text()

    # Each tab page with a layout has a Help button somewhere in its widget tree
    from PySide6.QtWidgets import QPushButton

    def find_help(page) -> list[str]:
        found = []

        def walk(w):
            if isinstance(w, QPushButton) and w.text() == "Help":
                found.append(w.toolTip())
            for c in w.children():
                walk(c)

        walk(page)
        return found

    for i in range(tabs.count()):
        title = tabs.tabText(i)
        page = tabs.widget(i)
        tips = find_help(page)
        print(f"  {title}: help buttons={len(tips)}")
        assert tips, f"no Help button on tab {title}"

    # Dialog importable + content for a tab
    from app.ui.tab_help import TabHelpDialog

    dlg = TabHelpDialog(win, "S-Params")
    assert "S-PARAMS" in dlg.viewer.toPlainText()
    dlg.close()

    win.close()
    print("TAB HELP SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
