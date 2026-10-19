"""Embedded VTK viewer for openEMS field dumps (ParaView-style controls).

Mirrors the openEMS "Field Dumps Analysis via ParaView" workflow:
  * choose the displayed field array (point/cell),
  * represent it as Surface / Wireframe / Slice / Iso-surface,
  * rescale the colour range (data range / manual min-max / logarithmic),
  * animate the time steps (Play) when a per-step series exists.

Uses VTK's Qt interactor (``QVTKRenderWindowInteractor``) so the 3D view lives
inside the GUI. The GL window is not created on a headless/offscreen platform
(VTK would crash), but the data logic still runs.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.postproc.vtk_export import dump_to_vtr, export_dumps_to_vtk  # noqa: F401

_AXIS_NORMAL = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}

#: X red / Y green / Z blue — used by the triad, its labels and the ruler.
AXIS_COLORS = ((0.95, 0.25, 0.25), (0.30, 0.85, 0.35), (0.35, 0.55, 1.0))

#: Size presets for the orientation triad (factor on the auto length).
AXES_SCALES: tuple[tuple[str, float], ...] = (
    ("Small (0.5×)", 0.5),
    ("Medium (1×)", 1.0),
    ("Large (2×)", 2.0),
    ("XXL (4×)", 4.0),
    ("XXXL (8×)", 8.0),
)

#: Label font sizes offered in the axes menu.
AXES_FONT_SIZES: tuple[int, ...] = (8, 10, 12, 14, 18, 24)

#: Defaults for the axes display options (also persisted in AppSettings).
DEFAULT_AXES: dict = {
    "show": True,  # master switch for the triad / corner marker
    "corner": False,  # True = ParaView-style corner widget, False = in-scene triad
    "labels": True,  # X / Y / Z captions on the triad
    "font": 12,  # caption font size (pt)
    "scale": 1.0,  # triad size factor (see AXES_SCALES)
    "ruler": False,  # numeric coordinate ticks around the dataset bounds
}


def axes_total_length(bounds, scale: float = 1.0) -> float:
    """Edge length of the orientation triad, derived from the dataset bounds.

    A fixed length (the old ``10, 10, 10``) dwarfs the metre-sized (or smaller)
    openEMS domains and dominates ``ResetCamera()``, so the field looked
    invisible at startup until the user zoomed in deeply. ``scale`` multiplies
    the auto length (axes size preset).
    """
    dx = abs(float(bounds[1]) - float(bounds[0]))
    dy = abs(float(bounds[3]) - float(bounds[2]))
    dz = abs(float(bounds[5]) - float(bounds[4]))
    try:
        factor = float(scale)
    except (TypeError, ValueError):
        factor = 1.0
    if not math.isfinite(factor) or factor <= 0.0:
        factor = 1.0
    diag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if not math.isfinite(diag) or diag <= 0.0:
        return factor  # fallback: auto length 1.0 (times the size factor)
    return 0.18 * diag * factor


def style_axes_triad(
    axes, *, show_labels: bool = True, font_size: int = 12
) -> None:
    """Colour and label a ``vtkAxesActor`` (X red / Y green / Z blue).

    ``show_labels`` toggles the X/Y/Z captions (the colours stay either way),
    ``font_size`` sets their point size. Shared by the in-scene triad and the
    corner orientation marker.
    """
    try:
        size = max(1, int(font_size))
    except (TypeError, ValueError):
        size = 12
    axes.SetAxisLabels(1 if show_labels else 0)
    for axis, (r, g, b) in zip(("X", "Y", "Z"), AXIS_COLORS):
        shaft = getattr(axes, f"Get{axis}AxisShaftProperty")()
        tip = getattr(axes, f"Get{axis}AxisTipProperty")()
        shaft.SetColor(r, g, b)
        tip.SetColor(r, g, b)
        getattr(axes, f"Set{axis}AxisLabelText")(axis)
        text = getattr(axes, f"Get{axis}AxisCaptionActor2D")().GetCaptionTextProperty()
        text.SetColor(r, g, b)
        text.SetFontSize(size)


def _vtk():
    """Import vtk lazily (heavy) and make the render backend available."""
    import vtk
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 — register OpenGL factory
    import vtkmodules.vtkInteractionStyle  # noqa: F401
    import vtkmodules.vtkRenderingFreeType  # noqa: F401

    return vtk


def _make_reader(vtk, path: Path):
    """Pick a VTK reader from the file extension (not just our own .vtr)."""
    cls = {
        ".vtr": "vtkXMLRectilinearGridReader",
        ".vti": "vtkXMLImageDataReader",
        ".vtu": "vtkXMLUnstructuredGridReader",
        ".vtp": "vtkXMLPolyDataReader",
        ".vts": "vtkXMLStructuredGridReader",
        ".vtk": "vtkGenericDataObjectReader",  # legacy .vtk
    }.get(path.suffix.lower(), "vtkXMLGenericDataObjectReader")
    return getattr(vtk, cls)()


class FieldViewerWindow(QMainWindow):
    """Standalone window with its OWN field viewer.

    Re-parenting the embedded QVTKRenderWindowInteractor between top-level
    windows leaves a stale native window behind (a duplicated view), so the
    pop-out gets a second VtkFieldViewer that mirrors the current state instead.
    """

    def __init__(self, source: "VtkFieldViewer") -> None:
        super().__init__(None)
        self.source: VtkFieldViewer | None = source
        self.viewer = VtkFieldViewer(work_dir=source.work_dir)
        self.viewer.btn_popout.setVisible(False)  # no nested pop-outs
        self.viewer.on_axes_changed = self._on_viewer_axes_changed
        self.setWindowTitle("Field Viewer - pop-out")
        self.resize(1000, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCentralWidget(self.viewer)

        if source._current_path is not None:
            self.viewer.load(source._current_path)
        self.viewer._copy_state_from(source)

    def _on_viewer_axes_changed(self, options: dict) -> None:
        """Axes changed in the pop-out: mirror to the tab view + persist."""
        src = self.source
        if src is not None and src is not self.viewer:
            try:
                src.set_axes_options(options, notify=True)
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, event) -> None:  # noqa: N802
        src = self.source
        self.source = None
        if src is not None and src._popout_win is self:
            src._popout_win = None
            src.btn_popout.setText("Pop out")
        super().closeEvent(event)


class VtkFieldViewer(QWidget):
    """A VTK render window + ParaView-style controls for field dumps."""

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = Path(work_dir) if work_dir else None
        self._vtk = None
        self._iren = None
        self._renderer = None
        self._grid = None
        self._data_range = (0.0, 1.0)
        self._scalar_name = None  # kept for compatibility / status text
        self._scalar_range = (0.0, 1.0)
        self._series: list[Path] = []
        self._series_labels: list[str] = []
        self._series_times: list[float | None] = []  # seconds (TD dumps)
        self._series_freqs: list[float | None] = []  # Hz (FD dumps)
        self._current_path: Path | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._on_play_tick)
        self._series_range = None  # global (lo, hi) so the movie does not flicker
        self._series_range_cache: dict = {}
        self._popout_win: FieldViewerWindow | None = None
        # axes display options (menu + persisted settings)
        self._axes = dict(DEFAULT_AXES)
        self.on_axes_changed = None  # callback(options: dict) for persistence
        self._marker = None  # vtkOrientationMarkerWidget (corner axes)
        self._marker_axes = None  # its vtkAxesActor
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        self._root_layout = root  # holds the control rows + the 3D pane

        # Right-click menu on the controls/background only — never on the 3D
        # pane (VTK uses right-click there, see _context_menu_allowed).
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        bar = QHBoxLayout()
        self.btn_open = QPushButton("Open…")
        self.btn_open.clicked.connect(self._on_open)
        self.btn_export = QPushButton("Export project dumps → VTK")
        self.btn_export.setToolTip(
            "Convert every field dump (simulation/*.h5) of the current project to\n"
            "VTK in <project>/vtk (incl. one file per time step) and load the first."
        )
        self.btn_export.clicked.connect(self.export_and_load)
        self.cmb_rep = QComboBox()
        self.cmb_rep.addItems(["Surface", "Wireframe", "Slice", "Iso-surface"])
        self.cmb_rep.setCurrentText("Surface")  # ParaView-like default
        self.cmb_rep.currentIndexChanged.connect(self._refresh_display)
        self.cmb_array = QComboBox()
        self.cmb_array.setMinimumWidth(150)
        self.cmb_array.setToolTip("Field array to display (point or cell data).")
        self.cmb_array.currentIndexChanged.connect(self._on_array_changed)
        self.chk_outline = QCheckBox("Outline")
        self.chk_outline.setChecked(True)
        self.chk_outline.toggled.connect(self._refresh_display)
        self.btn_reset = QPushButton("Reset view")
        self.btn_reset.clicked.connect(self._reset_camera)
        self.btn_popout = QPushButton("Pop out")
        self.btn_popout.setToolTip("Show the 3D view in a separate window (click again to dock).")
        self.btn_popout.clicked.connect(self.popout)
        for w in (self.btn_open, self.btn_export, QLabel("Representation"), self.cmb_rep,
                  QLabel("Array"), self.cmb_array, self.chk_outline):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.btn_popout)
        bar.addWidget(self.btn_reset)
        root.addLayout(bar)

        # slice + iso level
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Slice axis"))
        self.cmb_slice_axis = QComboBox()
        self.cmb_slice_axis.addItems(["X", "Y", "Z"])
        self.cmb_slice_axis.currentIndexChanged.connect(self._refresh_display)
        self.slider_slice = QSlider(Qt.Orientation.Horizontal)
        self.slider_slice.setRange(0, 1000)
        self.slider_slice.setValue(500)
        self.slider_slice.valueChanged.connect(self._refresh_display)
        row2.addWidget(self.cmb_slice_axis)
        row2.addWidget(self.slider_slice, 1)
        row2.addWidget(QLabel("Iso-level"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.valueChanged.connect(self._on_slider)
        row2.addWidget(self.slider, 1)
        self.lbl_level = QLabel("—")
        self.lbl_level.setMinimumWidth(160)
        row2.addWidget(self.lbl_level)
        root.addLayout(row2)

        # colour range (ParaView "Rescale to ... / log scale")
        row3 = QHBoxLayout()
        self.chk_auto = QCheckBox("Auto (data range)")
        self.chk_auto.setChecked(True)
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        self.spn_min = QDoubleSpinBox()
        self.spn_max = QDoubleSpinBox()
        for s in (self.spn_min, self.spn_max):
            s.setDecimals(6)
            s.setRange(-1e30, 1e30)
            s.setEnabled(False)
            s.valueChanged.connect(self._refresh_display)
        self.chk_log = QCheckBox("Log scale")
        self.chk_log.toggled.connect(self._refresh_display)
        self.btn_rescale = QPushButton("Rescale to data range")
        self.btn_rescale.clicked.connect(self._rescale_to_data)
        row3.addWidget(QLabel("Colour"))
        row3.addWidget(self.chk_auto)
        row3.addWidget(QLabel("min"))
        row3.addWidget(self.spn_min)
        row3.addWidget(QLabel("max"))
        row3.addWidget(self.spn_max)
        row3.addWidget(self.chk_log)
        row3.addWidget(self.btn_rescale)
        row3.addStretch(1)
        root.addLayout(row3)

        # time series (animation)
        row4 = QHBoxLayout()
        self.lbl_time = QLabel("Time")
        self.slider_time = QSlider(Qt.Orientation.Horizontal)
        self.slider_time.setRange(0, 0)
        self.slider_time.valueChanged.connect(self._on_time_slider)
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.setCheckable(True)
        self.btn_play.toggled.connect(self._on_play_toggled)
        self.lbl_time_info = QLabel("")
        self.lbl_sim_time = QLabel("")
        self.lbl_sim_time.setToolTip(
            "Physical simulation time of this frame (ps/ns) — or frequency for "
            "frequency-domain dumps — taken from the openEMS HDF5 dump."
        )
        self.spn_fps = QSpinBox()
        self.spn_fps.setRange(1, 60)
        self.spn_fps.setValue(4)
        self.spn_fps.setSuffix(" fps")
        self.spn_fps.setToolTip("Playback speed of the time-series animation.")
        self.spn_fps.valueChanged.connect(self._on_fps_changed)
        self.chk_loop = QCheckBox("Loop")
        self.chk_loop.setChecked(True)
        self.chk_loop.setToolTip("Restart at the first step after the last one.")
        self.chk_autoplay = QCheckBox("Auto-play")
        self.chk_autoplay.setChecked(False)  # default off; enable in Settings
        self.chk_autoplay.setToolTip(
            "Start the animation automatically after loading / exporting a series.\n"
            "Default can be changed in Settings → Field Viewer."
        )
        row4.addWidget(self.lbl_time)
        row4.addWidget(self.slider_time, 1)
        row4.addWidget(self.btn_play)
        row4.addWidget(self.lbl_time_info)
        row4.addWidget(self.lbl_sim_time)
        row4.addWidget(self.spn_fps)
        row4.addWidget(self.chk_loop)
        row4.addWidget(self.chk_autoplay)
        root.addLayout(row4)
        self._set_time_visible(False)

        # VTK render window (created lazily so importing vtk is deferred)
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_hint = QLabel(
            "No VTK data. Use “Export project dumps → VTK” (field dumps must cover "
            "a volume/plane, not a single point) or “Open…”."
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#666;")
        self._host_layout.addWidget(self.lbl_hint)
        root.addWidget(self._host, 1)

    def _set_time_visible(self, on: bool) -> None:
        self._time_controls_on = on
        for w in (
            self.lbl_time,
            self.slider_time,
            self.btn_play,
            self.lbl_time_info,
            self.lbl_sim_time,
            self.spn_fps,
            self.chk_loop,
            self.chk_autoplay,
        ):
            w.setVisible(on)

    def _ensure_render_window(self) -> bool:
        """Create the VTK Qt interactor once; False if unavailable/cannot init."""
        if self._iren is not None:
            return True
        if getattr(self, "_vtk_failed", False):
            return False
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            self._vtk_failed = True
            self.lbl_hint.setText(
                "VTK 3D view unavailable on the offscreen platform. The converted "
                "files can be opened in ParaView."
            )
            self.lbl_hint.setVisible(True)
            return False
        try:
            vtk = _vtk()
            self._vtk = vtk
            from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

            self.lbl_hint.setVisible(False)
            self._iren = QVTKRenderWindowInteractor(self._host)
            self._host_layout.addWidget(self._iren, 1)
            self._renderer = vtk.vtkRenderer()
            self._renderer.SetBackground(0.15, 0.15, 0.18)
            self._iren.GetRenderWindow().AddRenderer(self._renderer)
            self._iren.Initialize()
            return True
        except Exception as e:  # noqa: BLE001 — e.g. no OpenGL in this session
            self._vtk_failed = True
            self.lbl_hint.setText(
                f"VTK 3D view unavailable (OpenGL init failed): {e}\n"
                "The converted files can still be opened in ParaView."
            )
            self.lbl_hint.setVisible(True)
            print(f"VTK viewer disabled: {e}")
            return False

    # --------------------------------------------------------------- actions
    def set_work_dir(self, work_dir: Path | None) -> None:
        self.work_dir = Path(work_dir) if work_dir else None

    def set_autoplay_default(self, on: bool) -> None:
        """Apply the Settings default for the Auto-play checkbox."""
        self.chk_autoplay.setChecked(bool(on))

    # ----------------------------------------------------------- axes options
    def axes_options(self) -> dict:
        """Current axes display options (JSON-serialisable)."""
        return dict(self._axes)

    def set_axes_options(self, options: dict | None, *, notify: bool = False) -> None:
        """Apply axes options; unknown keys are ignored, missing ones unchanged.

        ``notify=True`` calls :attr:`on_axes_changed` afterwards (used by the
        menus so the main window can persist the choice).
        """
        if isinstance(options, dict):
            for key, default in DEFAULT_AXES.items():
                if key not in options:
                    continue
                value = options[key]
                try:
                    if key in ("show", "corner", "labels", "ruler"):
                        value = bool(value)
                    elif key == "font":
                        value = max(6, min(72, int(value)))
                    elif key == "scale":
                        value = float(value)
                        if not math.isfinite(value) or value <= 0.0:
                            value = 1.0
                    else:
                        value = default
                except (TypeError, ValueError):
                    value = default
                self._axes[key] = value
        self._apply_axes()
        if notify and callable(self.on_axes_changed):
            self.on_axes_changed(self.axes_options())

    def set_axes_option(self, key: str, value) -> None:
        """Change one axes option and notify (menu slots)."""
        self.set_axes_options({key: value}, notify=True)

    def _apply_axes(self) -> None:
        """Re-render the triad / corner marker / ruler with the current options."""
        if self._renderer is not None and self._grid is not None:
            self._refresh_display()
        else:
            self._update_corner_marker()
            if self._iren is not None:
                try:
                    self._iren.GetRenderWindow().Render()
                except Exception:  # noqa: BLE001
                    pass

    def _update_corner_marker(self) -> None:
        """Small, fixed-size orientation axes in the lower-left corner."""
        if self._iren is None:
            return
        show = bool(
            self._axes["show"] and self._axes["corner"] and self._grid is not None
        )
        if show and self._marker is None:
            try:
                vtk = self._vtk or _vtk()
                self._vtk = vtk
                axes = vtk.vtkAxesActor()
                axes.SetTotalLength(1.0, 1.0, 1.0)
                widget = vtk.vtkOrientationMarkerWidget()
                widget.SetOrientationMarker(axes)
                widget.SetInteractor(self._iren)
                widget.SetViewport(0.0, 0.0, 0.16, 0.22)
                widget.InteractiveOff()
                widget.SetEnabled(1)
                self._marker_axes, self._marker = axes, widget
            except Exception as e:  # noqa: BLE001 — no GL: keep the scene usable
                print(f"Corner marker unavailable: {e}")
                self._marker = None
                self._marker_axes = None
                return
        if self._marker is None:
            return
        if show:
            style_axes_triad(
                self._marker_axes,
                show_labels=bool(self._axes["labels"]),
                font_size=int(self._axes["font"]),
            )
            self._marker.SetEnabled(1)
        else:
            self._marker.SetEnabled(0)

    def _add_coordinate_ruler(self, vtk) -> None:
        """Numeric x/y/z ticks around the dataset bounds (vtkCubeAxesActor)."""
        cube = vtk.vtkCubeAxesActor()
        cube.SetBounds(self._grid.GetBounds())
        cube.SetCamera(self._renderer.GetActiveCamera())
        cube.SetFlyModeToClosestTriad()
        cube.SetUseTextActor3D(False)
        cube.SetPickable(False)
        font = int(self._axes["font"])
        titles = ("x [m]", "y [m]", "z [m]")
        for axis, title, color in zip(("X", "Y", "Z"), titles, AXIS_COLORS):
            getattr(cube, f"Set{axis}Title")(title)
            getattr(cube, f"Set{axis}LabelFormat")("%.4g")
            label = getattr(cube, f"Get{axis}AxesLabelProperty")()
            label.SetColor(0.9, 0.9, 0.9)
            label.SetFontSize(font)
            caption = getattr(cube, f"Get{axis}AxesTitleProperty")()
            caption.SetColor(0.9, 0.9, 0.9)
            caption.SetFontSize(font)
            getattr(cube, f"Get{axis}AxesLinesProperty")().SetColor(*color)
        self._renderer.AddActor(cube)

    def build_axes_menu(self, parent=None) -> QMenu:
        """Menu with all axes options (View menu + Field Viewer context menu).

        The check states are refreshed in ``aboutToShow`` so several menus can
        show the same state and stay in sync.
        """
        menu = QMenu("Axes", parent)
        acts: dict = {}
        acts["show"] = menu.addAction("Show axes")
        acts["corner"] = menu.addAction("Corner marker (ParaView style)")
        acts["corner"].setToolTip(
            "Show a small orientation triad in the lower-left corner instead of "
            "the triad inside the 3D scene."
        )
        acts["labels"] = menu.addAction("Labels X/Y/Z")
        acts["labels"].setToolTip("Show the X/Y/Z captions on the axes (colours stay).")
        acts["ruler"] = menu.addAction("Coordinate ruler (numbers)")
        acts["ruler"].setToolTip(
            "Numeric coordinate ticks around the dataset bounds (in metres)."
        )
        menu.addSeparator()
        size_menu = menu.addMenu("Size")
        acts["size"] = {}
        size_group = QActionGroup(size_menu)
        size_group.setExclusive(True)
        for text, factor in AXES_SCALES:
            act = size_menu.addAction(text)
            act.setCheckable(True)
            size_group.addAction(act)
            acts["size"][factor] = act
        font_menu = menu.addMenu("Label font")
        acts["font"] = {}
        font_group = QActionGroup(font_menu)
        font_group.setExclusive(True)
        for size in AXES_FONT_SIZES:
            act = font_menu.addAction(f"{size} pt")
            act.setCheckable(True)
            font_group.addAction(act)
            acts["font"][size] = act

        for key in ("show", "corner", "labels", "ruler"):
            act = acts[key]
            act.setCheckable(True)
            act.toggled.connect(lambda on, k=key: self.set_axes_option(k, bool(on)))
        for factor, act in acts["size"].items():
            act.toggled.connect(
                lambda on, f=factor: on and self.set_axes_option("scale", f)
            )
        for size, act in acts["font"].items():
            act.toggled.connect(
                lambda on, s=size: on and self.set_axes_option("font", s)
            )

        menu._axes_actions = acts  # type: ignore[attr-defined]
        self._sync_axes_menu(menu)
        menu.aboutToShow.connect(lambda: self._sync_axes_menu(menu))
        return menu

    def _sync_axes_menu(self, menu) -> None:
        acts = getattr(menu, "_axes_actions", None)
        if not acts:
            return
        for key in ("show", "corner", "labels", "ruler"):
            act = acts[key]
            act.blockSignals(True)
            act.setChecked(bool(self._axes[key]))
            act.blockSignals(False)
        for factor, act in acts["size"].items():
            act.blockSignals(True)
            act.setChecked(abs(float(self._axes["scale"]) - factor) < 1e-9)
            act.blockSignals(False)
        for size, act in acts["font"].items():
            act.blockSignals(True)
            act.setChecked(int(self._axes["font"]) == size)
            act.blockSignals(False)

    def clear(self, message: str | None = None) -> None:
        """Drop the loaded field/series so no stale scene stays visible.

        Used when a project without field dumps is opened (nothing must be
        shown); the pop-out mirror (if any) is emptied as well.
        """
        self._timer.stop()
        if self.btn_play.isChecked():
            self.btn_play.blockSignals(True)
            self.btn_play.setChecked(False)
            self.btn_play.setText("▶ Play")
            self.btn_play.blockSignals(False)
        self._series, self._series_labels = [], []
        self._series_times, self._series_freqs = [], []
        self._current_path = None
        self._grid = None
        self._scalar_name = None
        self._data_range = (0.0, 1.0)
        self._scalar_range = (0.0, 1.0)
        self._series_range = None
        self._series_range_cache = {}
        self.lbl_level.setText("")
        self.lbl_time_info.setText("")
        self.lbl_sim_time.setText("")
        self._set_time_visible(False)
        self.cmb_array.blockSignals(True)
        self.cmb_array.clear()
        self.cmb_array.blockSignals(False)
        if self._renderer is not None:
            try:
                self._renderer.RemoveAllViewProps()
                self._update_corner_marker()  # no data -> hide the corner axes
                self._iren.GetRenderWindow().Render()
            except Exception:  # noqa: BLE001
                pass
        if not getattr(self, "_vtk_failed", False):
            if message:
                self.lbl_hint.setText(message)
            self.lbl_hint.setVisible(True)
        win = self._popout_win
        if win is not None and getattr(win, "viewer", None) is not self:
            try:
                win.viewer.clear()
            except Exception:  # noqa: BLE001
                pass

    def popout(self) -> None:
        """Open a separate window mirroring the view (toggle: click again to close)."""
        if self._popout_win is not None:
            self.restore_from_popout()
            return
        win = FieldViewerWindow(self)
        self._popout_win = win
        win.show()
        win.raise_()
        win.activateWindow()
        self.btn_popout.setText("Dock view")

    def restore_from_popout(self) -> None:
        """Close the pop-out window (the tab view is unaffected)."""
        win = self._popout_win
        if win is None:
            return
        self._popout_win = None
        win.source = None  # avoid a re-entrant closeEvent
        try:
            win.close()
        except Exception:  # noqa: BLE001
            pass
        self.btn_popout.setText("Pop out")

    def _context_menu_allowed(self, pos) -> bool:
        """True unless the click is inside the 3D pane (VTK right-click there)."""
        host = getattr(self, "_host", None)
        return host is None or not host.geometry().contains(pos)

    def _show_context_menu(self, pos) -> None:
        if not self._context_menu_allowed(pos):
            return
        menu = QMenu(self)
        menu.addAction("Open…", self._on_open)
        menu.addAction("Export project dumps → VTK", self.export_and_load)
        menu.addSeparator()
        if self._popout_win is not None:
            menu.addAction("Dock view (close pop-out)", self.restore_from_popout)
        elif self.btn_popout.isHidden():
            win = self.window()
            if isinstance(win, FieldViewerWindow):
                menu.addAction("Close pop-out window", win.close)
        else:
            menu.addAction("Pop out view", self.popout)
        menu.addAction("Reset view", self._reset_camera)
        menu.addSeparator()
        menu.addMenu(self.build_axes_menu(menu))
        menu.exec(self.mapToGlobal(pos))

    def _copy_state_from(self, other: "VtkFieldViewer") -> None:
        """Mirror representation / colour / playback settings (for the pop-out)."""
        for setter in (
            lambda: self.cmb_rep.setCurrentText(other.cmb_rep.currentText()),
            lambda: self.chk_outline.setChecked(other.chk_outline.isChecked()),
            lambda: self.cmb_slice_axis.setCurrentText(other.cmb_slice_axis.currentText()),
            lambda: self.slider_slice.setValue(other.slider_slice.value()),
            lambda: self.slider.setValue(other.slider.value()),
            lambda: self.chk_log.setChecked(other.chk_log.isChecked()),
            lambda: self.chk_auto.setChecked(other.chk_auto.isChecked()),
            lambda: self.spn_min.setValue(other.spn_min.value()),
            lambda: self.spn_max.setValue(other.spn_max.value()),
            lambda: self.spn_fps.setValue(other.spn_fps.value()),
            lambda: self.chk_loop.setChecked(other.chk_loop.isChecked()),
            lambda: self.chk_autoplay.setChecked(other.chk_autoplay.isChecked()),
            lambda: self.set_axes_options(other.axes_options()),
        ):
            try:
                setter()
            except Exception:  # noqa: BLE001
                pass
        # the array combo is rebuilt on load; select the same entry by name
        i = self.cmb_array.findText(other.cmb_array.currentText())
        if i >= 0:
            self.cmb_array.setCurrentIndex(i)
        if self._series and other._series:
            self.slider_time.setValue(min(other.slider_time.value(), len(self._series) - 1))

    def _on_open(self) -> None:
        start = ""
        if self.work_dir:
            vtk_dir = Path(self.work_dir) / "vtk"
            # the converted files live in <project>/vtk — start there
            start = str(vtk_dir if vtk_dir.is_dir() else self.work_dir)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open VTK file",
            start,
            "VTK files (*.vtr *.vti *.vtu *.vtp *.vts *.vtk);;All files (*)",
        )
        if path:
            self.load(path)

    def export_and_load(self) -> None:
        if self.work_dir is None:
            QMessageBox.information(self, "VTK", "No work directory set.")
            return
        sim = Path(self.work_dir) / "simulation"
        if not sim.is_dir():
            QMessageBox.information(
                self, "VTK", f"No simulation folder found:\n{sim}\n\nRun a simulation first."
            )
            return
        written = export_dumps_to_vtk(sim, per_step=True)
        if not written:
            QMessageBox.information(
                self,
                "VTK",
                "No usable field dump found. Check that the dumps cover a volume/plane "
                "(Edit Dump: start≠stop), then run again.",
            )
            return
        self.load(written[0])
        print(f"VTK: converted {len(written)} file(s) -> {written[0].parent}")

    def load(self, path: Path | str) -> None:
        path = Path(path)
        if not path.exists():
            return
        self._detect_series(path)
        if not self._read_into_grid(path, show_errors=True):
            return
        self._current_path = path
        if self._series:
            self.slider_time.blockSignals(True)
            self.slider_time.setRange(0, len(self._series) - 1)
            self.slider_time.setValue(self._series.index(path) if path in self._series else 0)
            self.slider_time.blockSignals(False)
            self._set_time_visible(True)
        else:
            self._set_time_visible(False)
        self._populate_arrays()
        if self._ensure_render_window():
            self._refresh_display()
            self._reset_camera()
        self._log_title(path)
        self._autoplay()

    def _read_into_grid(self, path: Path, show_errors: bool = False) -> bool:
        vtk = _vtk()
        try:
            reader = _make_reader(vtk, path)
            reader.SetFileName(str(path))
            reader.Update()
            grid = reader.GetOutput()
        except Exception as e:  # noqa: BLE001
            if show_errors:
                QMessageBox.warning(self, "VTK", f"Could not read {path.name}:\n{e}")
            return False
        if grid is None or grid.GetNumberOfPoints() == 0:
            if show_errors:
                QMessageBox.warning(
                    self, "VTK", f"Could not read {path.name} ({path.suffix} unsupported?)."
                )
            return False
        self._grid = grid
        self._vtk = vtk
        return True

    def _detect_series(self, path: Path) -> None:
        self._series, self._series_labels = [], []
        self._series_times, self._series_freqs = [], []
        m = re.match(r"(?P<base>.+)_(?P<idx>\d+)$", path.stem)
        if not m:
            return
        base = m.group("base")
        files = sorted(
            path.parent.glob(base + "_*.vtr"),
            key=lambda p: int(re.search(r"_(\d+)$", p.stem).group(1)),  # noqa: B023
        )
        if len(files) < 2:
            return
        labels = [p.stem.rsplit("_", 1)[1] for p in files]
        sidecar = path.parent / (base + ".series.json")
        times: list[float | None] = []
        freqs: list[float | None] = []
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                labels = list(data.get("labels", labels))
                times = list(data.get("times") or [])
                freqs = list(data.get("frequencies") or [])
            except Exception:  # noqa: BLE001
                pass
        self._series = files
        self._series_labels = labels
        n = len(files)
        # keep the lists aligned with the files (sidecars from older exports
        # have no times/frequencies)
        self._series_times = (times + [None] * n)[:n]
        self._series_freqs = (freqs + [None] * n)[:n]
        self._series_range = None  # computed lazily for the chosen array

    def _populate_arrays(self) -> None:
        self.cmb_array.blockSignals(True)
        self.cmb_array.clear()
        if self._grid is not None:
            pd = self._grid.GetPointData()
            cd = self._grid.GetCellData()
            for i in range(pd.GetNumberOfArrays()):
                self.cmb_array.addItem(f"pt:{pd.GetArrayName(i)}", ("point", pd.GetArrayName(i)))
            for i in range(cd.GetNumberOfArrays()):
                self.cmb_array.addItem(f"cell:{cd.GetArrayName(i)}", ("cell", cd.GetArrayName(i)))
            # default: our magnitude, else the first array
            idx = 0
            for i in range(self.cmb_array.count()):
                d = self.cmb_array.itemData(i)
                if d and d[1] == "magnitude":
                    idx = i
                    break
            self.cmb_array.setCurrentIndex(idx if self.cmb_array.count() else -1)
        self.cmb_array.blockSignals(False)
        self._on_array_changed()

    def _array(self):
        d = self.cmb_array.currentData()
        return d if d else (None, None)

    def _on_array_changed(self, *_a) -> None:
        loc, name = self._array()
        self._scalar_name = name
        if self._grid is None or not name:
            self._data_range = (0.0, 1.0)
        else:
            data = self._grid.GetPointData() if loc == "point" else self._grid.GetCellData()
            arr = data.GetArray(name)
            rng = arr.GetRange() if arr else (0.0, 1.0)
            self._data_range = (float(rng[0]), float(rng[1]))
        if self._series and name and self.chk_auto.isChecked():
            rng = self._series_range_for(name)
            if rng is not None:
                self._data_range = rng  # fixed range -> no flicker while playing
        self._scalar_range = self._data_range  # compatibility alias
        self._rescale_to_data()
        self._refresh_display()

    def _series_range_for(self, name: str):
        cache = getattr(self, "_series_range_cache", None)
        if cache is None:
            cache = self._series_range_cache = {}
        if name in cache:
            return cache[name]
        vtk = _vtk()
        lo, hi = float("inf"), float("-inf")
        for p in self._series:
            try:
                r = _make_reader(vtk, p)
                r.SetFileName(str(p))
                r.Update()
                g = r.GetOutput()
            except Exception:  # noqa: BLE001
                continue
            if g is None:
                continue
            arr = g.GetPointData().GetArray(name) or g.GetCellData().GetArray(name)
            if arr is not None:
                a, b = arr.GetRange()
                lo, hi = min(lo, a), max(hi, b)
        res = None if lo == float("inf") or hi <= lo else (lo, hi)
        cache[name] = res
        return res

    def _rescale_to_data(self, *_a) -> None:
        lo, hi = self._data_range
        if hi <= lo:
            hi = lo + 1.0
        self.spn_min.blockSignals(True)
        self.spn_max.blockSignals(True)
        self.spn_min.setValue(lo)
        self.spn_max.setValue(hi)
        self.spn_min.blockSignals(False)
        self.spn_max.blockSignals(False)
        self._update_color_widgets()
        self._refresh_display()

    def _on_auto_toggled(self, *_a) -> None:
        self._update_color_widgets()
        self._refresh_display()

    def _update_color_widgets(self) -> None:
        man = not self.chk_auto.isChecked()
        self.spn_min.setEnabled(man)
        self.spn_max.setEnabled(man)

    def _color_range(self) -> tuple[float, float]:
        if self.chk_auto.isChecked():
            lo, hi = self._data_range
        else:
            lo, hi = float(self.spn_min.value()), float(self.spn_max.value())
        if hi <= lo:
            hi = lo + 1.0
        return lo, hi

    def _effective_range(self) -> tuple[float, float]:
        """Colour range actually used by the LUT / mapper.

        log10 is undefined at 0, but field magnitudes normally reach exactly 0
        somewhere — the old ``lo > 0`` test silently fell back to linear, so the
        "Log scale" box did nothing. Substitute the smallest positive value.
        """
        lo, hi = self._color_range()
        if hi <= lo:
            hi = lo + 1.0
        if self.chk_log.isChecked() and hi > 0:
            if lo <= 0:
                lo = self._min_positive() or hi * 1e-9
            if lo >= hi:
                lo = hi * 1e-6
        return lo, hi

    def _min_positive(self) -> float | None:
        """Smallest positive value of the displayed array (None if none)."""
        loc, name = self._array()
        if self._grid is None or not name:
            return None
        data = self._grid.GetPointData() if loc == "point" else self._grid.GetCellData()
        arr = data.GetArray(name)
        if arr is None:
            return None
        import numpy as np
        from vtk.util import numpy_support as ns

        a = ns.vtk_to_numpy(arr).astype(float).ravel()
        a = a[np.isfinite(a) & (a > 0)]
        return float(a.min()) if a.size else None

    def _lookup_table(self):
        vtk = self._vtk
        lut = vtk.vtkLookupTable()
        lut.SetNumberOfTableValues(256)
        lut.SetHueRange(0.667, 0.0)  # blue -> red (ParaView "jet"-ish)
        lut.Build()
        lo, hi = self._effective_range()
        if self.chk_log.isChecked() and lo > 0 and hi > lo:
            lut.SetScaleToLog10()
        else:
            lut.SetScaleToLinear()
        lut.SetTableRange(lo, hi)
        lut.Build()
        return lut

    def _on_slider(self, _v: int) -> None:
        self._refresh_display()

    def _iso_value(self) -> float:
        lo, hi = self._color_range()
        return lo + (hi - lo) * (self.slider.value() / 1000.0)

    def _slice_origin(self):
        b = self._grid.GetBounds()  # x0,x1,y0,y1,z0,z1
        axis = self.cmb_slice_axis.currentText()
        frac = self.slider_slice.value() / 1000.0
        if axis == "X":
            return (b[0] + (b[1] - b[0]) * frac, 0.0, 0.0)
        if axis == "Y":
            return (0.0, b[2] + (b[3] - b[2]) * frac, 0.0)
        return (0.0, 0.0, b[4] + (b[5] - b[4]) * frac)

    # ------------------------------------------------------------- rendering
    @staticmethod
    def _make_scalar_bar(vtk, lut, name: str):
        """Compact colour bar: VTK otherwise scales title/labels with the bar
        height, which produced a huge "field" filling the pane."""
        bar = vtk.vtkScalarBarActor()
        bar.SetLookupTable(lut)
        bar.SetTitle(name)
        bar.SetNumberOfLabels(5)
        bar.UnconstrainedFontSizeOn()  # font size in points, not bar-relative
        # Pin the bar to the right edge in normalised viewport coordinates.
        # (MaximumWidth/HeightInPixels override the position and centre the bar,
        # which is why it drifted into the middle of the pane.)
        bar.SetPosition(0.84, 0.06)
        bar.SetPosition2(0.15, 0.88)
        bar.SetBarRatio(0.24)
        bar.GetTitleTextProperty().SetFontSize(12)
        bar.GetTitleTextProperty().SetColor(1, 1, 1)
        bar.GetLabelTextProperty().SetFontSize(10)
        bar.GetLabelTextProperty().SetColor(1, 1, 1)
        bar.SetLabelFormat("%.2e")
        bar.SetTextPad(3)
        bar.SetVerticalTitleSeparation(4)
        return bar

    def _make_mapper(self, vtk, lut, loc, name):
        """Return (mapper, lut) for the current representation (None if none)."""
        if self._grid is None:
            return None
        rep = self.cmb_rep.currentText()
        if rep == "Iso-surface" and name:
            flt = vtk.vtkContourFilter()
            flt.SetInputData(self._grid)
            flt.SetInputArrayToProcess(0, 0, 0, 0, name)
            flt.SetValue(0, self._iso_value())
            flt.Update()
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(flt.GetOutputPort())
        elif rep == "Slice":
            nx, ny, nz = _AXIS_NORMAL[self.cmb_slice_axis.currentText()]
            plane = vtk.vtkPlane()
            plane.SetNormal(nx, ny, nz)
            plane.SetOrigin(*self._slice_origin())
            cutter = vtk.vtkCutter()
            cutter.SetInputData(self._grid)
            cutter.SetCutFunction(plane)
            cutter.Update()
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(cutter.GetOutputPort())
        else:
            mapper = vtk.vtkDataSetMapper()
            mapper.SetInputData(self._grid)
            if rep == "Wireframe":
                mapper.SetRepresentationToWireframe()
        if name:
            mapper.SetLookupTable(lut)
            mapper.SetScalarModeToUsePointFieldData() if loc == "point" else mapper.SetScalarModeToUseCellFieldData()
            mapper.SelectColorArray(name)
            mapper.SetScalarRange(*self._effective_range())
            mapper.ScalarVisibilityOn()
        else:
            mapper.ScalarVisibilityOff()
        return mapper

    def _refresh_display(self, *_args) -> None:
        if self._renderer is None or self._grid is None:
            return
        vtk = self._vtk
        self._renderer.RemoveAllViewProps()

        rep = self.cmb_rep.currentText()
        loc, name = self._array()
        self.slider.setEnabled(rep == "Iso-surface")
        self.slider_slice.setEnabled(rep == "Slice")
        self.cmb_slice_axis.setEnabled(rep == "Slice")

        lut = self._lookup_table()
        mapper = self._make_mapper(vtk, lut, loc, name)
        if mapper is not None:
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            if name:
                actor.GetMapper().SetLookupTable(lut)
            self._renderer.AddActor(actor)
            if name:
                self._renderer.AddActor2D(self._make_scalar_bar(vtk, lut, name))

        if self.chk_outline.isChecked():
            outline = vtk.vtkOutlineFilter()
            outline.SetInputData(self._grid)
            om = vtk.vtkPolyDataMapper()
            om.SetInputConnection(outline.GetOutputPort())
            oa = vtk.vtkActor()
            oa.SetMapper(om)
            oa.GetProperty().SetColor(0.8, 0.8, 0.8)
            self._renderer.AddActor(oa)

        # Orientation triad, scaled to the dataset and placed at the lower
        # corner (a fixed-size triad used to make the field appear tiny).
        # Options: corner marker instead of the in-scene triad, labels on/off,
        # font size, size factor, numeric coordinate ruler.
        if self._axes["show"] and not self._axes["corner"]:
            bounds = self._grid.GetBounds()
            axes = vtk.vtkAxesActor()
            length = axes_total_length(bounds, self._axes["scale"])
            axes.SetTotalLength(length, length, length)
            axes.SetPosition(bounds[0], bounds[2], bounds[4])
            style_axes_triad(
                axes,
                show_labels=bool(self._axes["labels"]),
                font_size=int(self._axes["font"]),
            )
            self._renderer.AddActor(axes)
        if self._axes["ruler"]:
            self._add_coordinate_ruler(vtk)
        self._update_corner_marker()

        if self._iren is not None:
            self._iren.GetRenderWindow().Render()

    def _reset_camera(self) -> None:
        if self._renderer is not None:
            self._renderer.ResetCamera()
            # keep near/far planes tight around the fitted scene
            self._renderer.ResetCameraClippingRange()
            if self._iren is not None:
                self._iren.GetRenderWindow().Render()

    # ------------------------------------------------------------ time series
    def _on_time_slider(self, idx: int) -> None:
        if not self._series or idx < 0 or idx >= len(self._series):
            return
        if self._read_into_grid(self._series[idx]):
            self._populate_arrays()  # rebuilds arrays + rescales + refreshes
            self._log_title(self._series[idx])

    def _on_play_toggled(self, on: bool) -> None:
        self.btn_play.setText("■ Stop" if on else "▶ Play")
        if on and self._series:
            if self.slider_time.value() >= len(self._series) - 1:
                self.slider_time.setValue(0)  # restart from the beginning
            self._timer.start()
        else:
            self._timer.stop()

    def _on_fps_changed(self, fps: int) -> None:
        self._timer.setInterval(max(1, int(round(1000.0 / max(1, fps)))))

    def _on_play_tick(self) -> None:
        if not self._series:
            return
        cur = self.slider_time.value()
        if cur >= len(self._series) - 1:
            if not self.chk_loop.isChecked():
                self.btn_play.setChecked(False)
                return
            nxt = 0
        else:
            nxt = cur + 1
        self.slider_time.setValue(nxt)

    def _autoplay(self) -> None:
        if self._series and self.chk_autoplay.isChecked():
            self.btn_play.setChecked(True)

    def _log_title(self, path: Path) -> None:
        n = self._grid.GetNumberOfPoints() if self._grid else 0
        name = self._scalar_name
        lo, hi = self._data_range
        if name:
            self.lbl_level.setText(f"{name} {lo:.3g} … {hi:.3g}  ({n} pts)")
        else:
            self.lbl_level.setText(f"{n} points")
        if self._series:
            i = self.slider_time.value()
            self.lbl_time_info.setText(
                f"{i + 1}/{len(self._series)}  ({self._step_label(i)})"
            )
        self._update_sim_time_label()
        print(f"VTK viewer: loaded {path.name} ({n} points, scalar={name})")

    def _step_label(self, i: int) -> str:
        """``step 6324`` for openEMS TD keys, else the sidecar label as-is."""
        lab = self._series_labels[i] if i < len(self._series_labels) else str(i)
        m = re.fullmatch(r"t(\d+)", str(lab))
        return f"step {m.group(1)}" if m else str(lab)

    @staticmethod
    def format_sim_time(seconds: float | None) -> str:
        """``t = 339.9 ps`` / ``t = 1.268 ns`` (auto unit)."""
        if seconds is None:
            return ""
        a = abs(seconds)
        if a < 1e-9:
            return f"t = {seconds * 1e12:.4g} ps"
        if a < 1e-6:
            return f"t = {seconds * 1e9:.4g} ns"
        if a < 1e-3:
            return f"t = {seconds * 1e6:.4g} µs"
        return f"t = {seconds:.4g} s"

    @staticmethod
    def format_frequency(hz: float | None) -> str:
        if hz is None:
            return ""
        a = abs(hz)
        if a >= 1e9:
            return f"f = {hz / 1e9:.4g} GHz"
        if a >= 1e6:
            return f"f = {hz / 1e6:.4g} MHz"
        if a >= 1e3:
            return f"f = {hz / 1e3:.4g} kHz"
        return f"f = {hz:.4g} Hz"

    def _update_sim_time_label(self) -> None:
        text = ""
        if self._series:
            i = self.slider_time.value()
            if 0 <= i < len(self._series_times):
                t = self._series_times[i]
                f = self._series_freqs[i] if i < len(self._series_freqs) else None
                text = self.format_frequency(f) or self.format_sim_time(t)
        self.lbl_sim_time.setText(text)
        self.lbl_sim_time.setVisible(bool(text) and getattr(self, "_time_controls_on", False))
