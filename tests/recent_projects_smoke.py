"""Smoke: recent-projects list (settings MRU + File → Recent projects menu).

Checks the pure MRU bookkeeping (order, de-duplication, cap, existing-only)
and the GUI wiring: opening a project pushes it to the list, refreshes the
File menu, missing files are pruned and the list can be cleared.

Run: python -u tests/recent_projects_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _test_mru_logic(tmp: Path) -> None:
    from app.settings import AppSettings

    settings_file = tmp / "settings.json"
    s = AppSettings()
    s.save(settings_file)
    s = AppSettings.load(settings_file)

    a = tmp / "a.fcsim.json"
    b = tmp / "b.fcsim.json"
    c = tmp / "c.fcsim.json"
    for p in (a, b, c):
        p.write_text("{}", encoding="utf-8")

    s.remember_project(a)
    s.remember_project(b)
    s.remember_project(a)  # re-open A -> moves to the front (no duplicate)
    assert [Path(p).name for p in s.recent_projects] == ["a.fcsim.json", "b.fcsim.json"], s.recent_projects
    assert s.last_project.endswith("a.fcsim.json"), s.last_project

    # case-insensitive de-duplication (Windows)
    s.remember_project(str(a).upper())
    assert len(s.recent_projects) == 2, s.recent_projects

    # cap
    for i in range(20):
        p = tmp / f"p{i}.fcsim.json"
        p.write_text("{}", encoding="utf-8")
        s.remember_project(p, limit=5)
    assert len(s.recent_projects) == 5, s.recent_projects

    # persistence round trip + existing-only filter
    s.remember_project(b)
    c.unlink()
    s.remember_project(c)
    s.save()
    reloaded = AppSettings.load(settings_file)
    assert reloaded.recent_projects[0].endswith("c.fcsim.json")
    assert all(
        "c.fcsim.json" not in p for p in reloaded.recent_projects_existing()
    ), "missing file must be filtered"
    print("[logic] MRU order/dedupe/cap/persistence OK")


def _test_gui(tmp: Path) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    from app.models.project import Project
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    settings = AppSettings.load(tmp / "gui_settings.json")
    proj_a = tmp / "proj_a.fcsim.json"
    proj_b = tmp / "proj_b.fcsim.json"
    Project.new_default("proj_a").save(proj_a)
    Project.new_default("proj_b").save(proj_b)
    settings.remember_project(proj_b)
    settings.save()

    win = MainWindow(settings)
    win._on_new()
    texts = [a.text() for a in win._recent_menu.actions()]
    assert "proj_b.fcsim.json" in texts, texts

    # opening a project pushes it to the front and refreshes the menu
    win._open_project_path(proj_a)
    assert settings.recent_projects[0].endswith("proj_a.fcsim.json"), settings.recent_projects
    texts = [a.text() for a in win._recent_menu.actions()]
    assert texts[0] == "proj_a.fcsim.json", texts
    assert "proj_b.fcsim.json" in texts, texts
    assert any("Clear list" in t for t in texts), texts

    # a vanished file is pruned instead of opened
    proj_b.unlink()
    win._open_recent_project(proj_b)
    assert all("proj_b" not in p for p in settings.recent_projects), settings.recent_projects
    assert all("proj_b" not in a.text() for a in win._recent_menu.actions())

    # clear list
    win._clear_recent_projects()
    assert settings.recent_projects == []
    assert [a.text() for a in win._recent_menu.actions()] == ["(no recent projects)"]
    win.close()
    print("[gui] menu + open/remember/prune/clear OK")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="recent_projects_smoke_"))
    try:
        _test_mru_logic(tmp)
        _test_gui(tmp)
        print("RECENT PROJECTS SMOKE PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
