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
    window_geometry: str = ""  # base64 QByteArray from QMainWindow.saveGeometry()
    log_panel_visible: bool = True  # simulator log panel under the tabs

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

    @classmethod
    def load(cls, path: Path | str | None = None) -> "AppSettings":
        path = Path(path) if path else DEFAULT_SETTINGS
        if not path.exists():
            s = cls()
            s.save(path)
            return s
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | str | None = None) -> Path:
        path = Path(path) if path else DEFAULT_SETTINGS
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path
