"""Application settings and default paths."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.paths import app_root, default_work_dir, settings_path
from app.platform_tools import (
    default_freecad_cmd,
    default_freecad_gui,
    default_openems_root,
    default_simulation_python,
)

APP_ROOT = app_root()
DEFAULT_WORKDIR = default_work_dir()
DEFAULT_SETTINGS = settings_path()

FREECAD_CMD_DEFAULT = default_freecad_cmd()
# FreeCAD GUI binary (often next to FreeCADCmd); empty => auto-detect
FREECAD_GUI_DEFAULT = default_freecad_gui(FREECAD_CMD_DEFAULT)
OPENEMS_ROOT_DEFAULT = default_openems_root()
PYTHON_DEFAULT = default_simulation_python()


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
    #: remembered sizes of resizable dialogs {key: [width, height]}
    dialog_sizes: dict = field(default_factory=dict)
    #: remembered geometry (base64 QByteArray) of dialogs {key: string}
    dialog_geometry: dict = field(default_factory=dict)
    #: main window layout: [left pane, tabs] and [tabs, log panel] widths/heights
    main_splitter_sizes: list[int] = field(default_factory=list)
    log_splitter_sizes: list[int] = field(default_factory=list)
    #: last active main tab (title, survives tab order changes)
    current_tab: str = ""
    #: last Viewer-settings tab (0 Viewer / 1 Colour / 2 Bar / 3 Mesh / 4 Mouse)
    viewer_settings_tab: int = 0
    log_panel_visible: bool = True  # simulator log panel under the tabs
    #: prefix each new simulator-log block with [HH:MM:SS.mmm] (optional)
    log_timestamps: bool = False
    #: left "Project / Geometry" pane (View menu / its right-click menu)
    project_panel_visible: bool = True
    #: Mesh Viewer overlay colours: grid/domain/nf2ff/port/port_inactive/critical
    mesh_viewer_colors: dict = field(default_factory=dict)
    #: 3D Design mesh section-plane colour (Viewer settings → Mesh colours)
    design_mesh_color: str = "#ffffff"
    #: critical-cell visibility threshold (runtime penalty 1/10/100x)
    mesh_critical_min_penalty: float = 1.0

    # --- Field Viewer (VTK) ---
    #: start playback automatically when a time series is loaded/exported
    field_viewer_autoplay: bool = False
    #: axes display options (see app.ui.vtk_viewer.DEFAULT_AXES)
    field_viewer_axes: dict = field(default_factory=dict)
    #: standard view of the 3D pane (see app.ui.vtk_viewer.VIEW_PRESETS);
    #: "keep" only fits the camera and keeps the current direction
    field_viewer_view: str = "iso"
    #: 3D mouse navigation (see app.ui.vtk_viewer.DEFAULT_MOUSE):
    #: style / motion_factor / wheel_factor / invert_wheel / swap_buttons
    field_viewer_mouse: dict = field(default_factory=dict)
    #: vector glyph options (see app.ui.vtk_viewer.DEFAULT_VECTORS):
    #: enabled / size / max_arrows / normalize
    field_viewer_vectors: dict = field(default_factory=dict)
    #: streamline appearance/seeding (see app.ui.vtk_viewer.DEFAULT_STREAMLINES):
    #: representation / radius_pct / sides / opacity / colour / seeds
    field_viewer_streamlines: dict = field(default_factory=dict)
    #: colour scale of the field plot (see app.ui.vtk_viewer.DEFAULT_COLOR_SCALE):
    #: cmap / reverse / steps
    field_viewer_color_scale: dict = field(default_factory=dict)
    #: colour-bar title (see app.ui.vtk_viewer.DEFAULT_BAR_TITLE):
    #: left / x / y / size / rotation
    field_viewer_title: dict = field(default_factory=dict)
    #: colour-bar geometry/font (see app.ui.vtk_viewer.DEFAULT_COLOR_BAR):
    #: x / y / width / height / font / font_size
    field_viewer_color_bar: dict = field(default_factory=dict)
    #: Field Viewer layout: "panel" (B) or "minimal" (C, Display… popup)
    field_viewer_layout: str = "panel"
    #: playback defaults (Viewer settings): frames per second and loop mode
    field_viewer_fps: int = 4
    field_viewer_loop: bool = True
    #: remembered geometry (base64 QByteArray) of the Display popup
    field_viewer_display_geometry: str = ""

    # --- Antenna pattern viewer (matplotlib 3D) ---
    #: 3D surface/rotation preset: "fast" (few polygons, no AA),
    #: "balanced" (default, smooth shaded) or "high" (fine grid, slower)
    antenna_3d_quality: str = "balanced"

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
        """Resolved FreeCAD GUI binary (explicit setting or derived from FreeCADCmd)."""
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
