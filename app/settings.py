"""Application settings and default paths."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.paths import app_root, settings_path

APP_ROOT = app_root()
DEFAULT_WORKDIR = Path(r"C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work")
DEFAULT_SETTINGS = settings_path()

FREECAD_CMD_DEFAULT = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")
# FreeCAD GUI binary (often freecad.exe next to FreeCADCmd); empty => auto-detect
FREECAD_GUI_DEFAULT = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCAD.exe")
OPENEMS_ROOT_DEFAULT = Path(r"c:\openEMS")
PYTHON_DEFAULT = Path(
    r"c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe"
)


@dataclass
class AppSettings:
    freecad_cmd: str = str(FREECAD_CMD_DEFAULT)
    freecad_gui: str = str(FREECAD_GUI_DEFAULT)
    openems_root: str = str(OPENEMS_ROOT_DEFAULT)
    simulation_python: str = str(PYTHON_DEFAULT)
    work_dir: str = str(DEFAULT_WORKDIR)
    default_unit: str = "mm"  # mm | um | m
    last_project: str = ""
    #: most-recently-opened project files, newest first (File → Recent projects)
    recent_projects: list[str] = field(default_factory=list)
    window_geometry: str = ""  # base64 QByteArray from QMainWindow.saveGeometry()
    log_panel_visible: bool = True  # simulator log panel under the tabs

    # --- Field Viewer (VTK) ---
    #: start playback automatically when a time series is loaded/exported
    field_viewer_autoplay: bool = False
    #: axes display options (see app.ui.vtk_viewer.DEFAULT_AXES)
    field_viewer_axes: dict = field(default_factory=dict)

    # --- Port defaults (Settings dialog + FreeCAD import fallbacks) ---
    default_port_type: str = "lumped"  # lumped | msl | rect_waveguide
    default_port_resistance: float = 50.0  # Ohm
    default_port_excite: bool = True  # first / only port typically excited
    default_port_mesh_dirs: str = "xyz"  # xyz | xy | xz | yz | x | y | z | ""
    default_port_direction: str = ""  # "" = auto from start/stop vector

    def port_defaults(self) -> dict:
        """Defaults applied when FreeCAD/export omits port fields."""
        return {
            "type": self.default_port_type or "lumped",
            "resistance": float(self.default_port_resistance or 50.0),
            "excite_port": bool(self.default_port_excite),
            "mesh_dirs": str(self.default_port_mesh_dirs or "xyz"),
            "direction": str(self.default_port_direction or ""),
        }

    def freecad_gui_path(self) -> Path | None:
        """Resolved FreeCAD.exe (explicit setting or derived from FreeCADCmd)."""
        from app.freecad_io.freecad_launcher import resolve_freecad_gui

        return resolve_freecad_gui(self.freecad_cmd, self.freecad_gui)

    def work_path(self) -> Path:
        p = Path(self.work_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    #: how many project paths the recent-projects list keeps
    MAX_RECENT_PROJECTS = 10

    @staticmethod
    def _norm_project_path(path: Path | str) -> str:
        try:
            return os.path.normcase(str(Path(path).resolve()))
        except Exception:  # noqa: BLE001 — odd/invalid paths: compare as given
            return os.path.normcase(str(path))

    def remember_project(self, path: Path | str, limit: int | None = None) -> None:
        """Push ``path`` to the front of the recent-projects list.

        Newest first, de-duplicated (case-insensitive on Windows), capped at
        ``limit`` (default :data:`MAX_RECENT_PROJECTS`); also keeps
        ``last_project`` in sync.
        """
        limit = int(limit or self.MAX_RECENT_PROJECTS)
        key = str(Path(path))
        norm = self._norm_project_path(key)
        items = [
            p for p in self.recent_projects if self._norm_project_path(p) != norm
        ]
        self.recent_projects = [key, *items][: max(1, limit)]
        self.last_project = key

    def recent_projects_existing(self) -> list[str]:
        """Recent entries that still exist on disk (newest first)."""
        return [p for p in self.recent_projects if Path(p).exists()]

    @classmethod
    def load(cls, path: Path | str | None = None) -> "AppSettings":
        target = Path(path) if path else DEFAULT_SETTINGS
        if not target.exists():
            s = cls()
            s._source_path = target
            s.save(target)
            return s
        data = json.loads(target.read_text(encoding="utf-8"))
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        s = cls(**{k: v for k, v in data.items() if k in known})
        # remember where they came from so save() writes back there (tests and
        # portable installs use a custom path)
        s._source_path = target
        return s

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path else getattr(self, "_source_path", None) or DEFAULT_SETTINGS
        target.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return target
