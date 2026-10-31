"""VisualopenEMS — English desktop application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
# Prefer project easyMesh4openEMS (Polyhedron/AABB patches) over site-packages
_EASY = APP_ROOT / "third_party" / "easyMesh4openEMS"
if _EASY.is_dir() and str(_EASY) not in sys.path:
    sys.path.insert(0, str(_EASY))


def project_arg(argv: list[str] | None = None) -> Path | None:
    """Project file passed on the command line (``python main.py <project.json>``).

    Qt strips its own options from ``sys.argv``, so only a plain positional
    argument is looked at; the first existing file wins and anything else (e.g.
    a typo in the path) is ignored so the GUI still starts normally.
    """
    for raw in (sys.argv[1:] if argv is None else argv):
        if str(raw).startswith("-"):
            continue
        try:
            candidate = Path(raw)
        except (TypeError, ValueError):
            continue
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from app.paths import app_root
    from app.ui.main_window import MainWindow
    from app.settings import AppSettings

    app = QApplication(sys.argv)
    app.setApplicationName("VisualopenEMS")
    # window/taskbar icon (same asset PyInstaller embeds as the EXE icon)
    icon_path = app_root() / "assets" / "app_icon.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    settings = AppSettings.load()
    win = MainWindow(settings)
    win.show()
    # optional: "python main.py <project.json>" (or the EXE with a project file)
    start = project_arg()
    if start is not None:
        win._open_project_path(start)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
