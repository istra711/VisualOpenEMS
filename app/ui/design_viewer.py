"""Embedded 3D design viewer built on the Field Viewer's VTK pane.

The Field Viewer already owns the expensive parts of an embedded VTK view
(render window, camera navigation, standard views and the primitive -> actor
conversion for boxes / cylinders / spheres / polygons / STL).  This module
reuses that machinery for a read-only *design* view and keeps the field-specific
widgets out of the way.

The viewer is deliberately split into a small, stable API so later features
(measurement, clipping planes, picking, mesh overlays) can be added without
touching the scene construction:

* :meth:`set_design` replaces the displayed project snapshot.
* :meth:`add_overlay_actor` / :meth:`remove_overlay_actors` let tools such as a
  ruler or a section-plane outline add temporary actors.
* :meth:`set_clip_plane` applies (or removes) one VTK clipping plane on every
  scene actor.  A future section-plane widget only has to call this method.
* :meth:`set_design` also accepts the built CSX mesh lines; the side panel can
  overlay them as a movable XY/XZ/YZ section grid.
* :meth:`pick_at` is a small cell-picking hook for object selection and
  measurement start/end points.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ui.object_display import display_alpha, display_rgb, normalize_display
from app.ui.vtk_viewer import (
    VIEW_PRESETS,
    VtkFieldViewer,
    _vtk,
    configure_marker_widget,
    preset_camera,
    style_axes_triad,
)


def _close(a: float, b: float) -> bool:
    """Tolerant equality for CSX/primitive coordinates (drawing units)."""
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)), abs(float(b)))


def _normalise_box(box: Any) -> tuple[list[float], list[float]] | None:
    """Accept ``(lo, hi)``, ``[[lo...], [hi...]]`` or a numpy-style area."""
    try:
        if box is None or len(box) != 2:
            return None
        lo = [float(v) for v in box[0]]
        hi = [float(v) for v in box[1]]
        if len(lo) != 3 or len(hi) != 3:
            return None
        if not all(math.isfinite(v) for v in lo + hi):
            return None
        return lo, hi
    except (TypeError, ValueError, IndexError):
        return None


class Design3DViewer(VtkFieldViewer):
    """Interactive 3D view of the project design (geometry + ports + NF2FF)."""

    #: The AppCSXCAD button in the design tool bar was clicked.
    open_appcsxcad_requested = Signal()

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        self._design_project = None
        self._design_prims: list[dict] = []
        self._design_ports: list = []
        self._design_nf2ff = None
        self._design_domain: tuple[list[float], list[float]] | None = None
        self._design_actors: list = []  # built-in domain / NF2FF / port actors
        self._design_ruler_actor = None  # current vtkCubeAxesActor
        self._ruler_view_observer = False
        self._updating_ruler_labels = False
        self._overlay_actors: list[tuple[str, str, Any]] = []  # (category, name, actor)
        self._design_needs_fit = True
        self._design_clip_plane = None
        #: built-mesh line coordinates in drawing units ({x,y,z} -> ndarray);
        #: the 3D mesh-plane overlay converts them with ``_geom_unit``.
        self._mesh_lines: dict[str, Any] = {
            axis: np.empty(0, dtype=float) for axis in "xyz"
        }
        #: transparent layer-1 renderer so the mesh grid is never hidden by
        #: the opaque design geometry (depth buffer is cleared per layer).
        self._mesh_overlay_renderer = None
        self._mesh_overlay_window = None
        self._mesh_plane_actor = None
        #: mesh-plane grid colour; white by default (Viewer settings)
        self._mesh_color = (1.0, 1.0, 1.0)
        self._highlight_names: list[str] = []
        self._highlight_boxes: list[tuple[tuple, tuple]] = []
        self._highlight_label = ""
        self._measure_mode = False
        self._measure_points: list[tuple[float, float, float]] = []
        self._measure_actors: list = []
        self._measure_preview_actors: list = []
        self._measure_text = ""
        self._vtk_right_pos = None
        self._vtk_menu_installed = False
        super().__init__(work_dir=work_dir, parent=parent)
        # Same axes defaults as the Field Viewer (DEFAULT_AXES): corner triad,
        # axis labels, numeric coordinate ruler, font 24 and 2x size. The
        # in-scene arrows are only drawn when the corner triad is off, so both
        # never appear at once.
        self._axes["show"] = True
        self._axes["labels"] = True
        self._axes["ruler"] = True
        self._axes["corner"] = True
        #: optional tick tape drawn directly on the three axis arrows
        self._axes["axis_tape"] = False
        self._axis_tape_axes: set[int] = {0, 1, 2}
        #: numeric cube ruler: placement and tick side
        self._axes["ruler_fly"] = "closest"
        self._axes["ruler_ticks"] = "outside"
        #: where the in-scene X/Y/Z triad is anchored: min|max|origin|center
        self._axis_anchor = "min"

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        """Design-specific UI; no field-dump / slice / playback controls."""
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(4, 4, 4, 4)
        self._root_layout.setSpacing(4)

        # Top action bar -------------------------------------------------
        # The tab already carries the "3D Design" title; keep this row compact.
        top = QHBoxLayout()
        top.addWidget(QLabel("View"))
        self.cmb_view_preset = QComboBox()
        self.cmb_view_preset.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.cmb_view_preset.setMinimumContentsLength(14)
        for key, label in VIEW_PRESETS:
            self.cmb_view_preset.addItem(label, key)
        # isometric is the useful starting point for a 3D design (the Field
        # Viewer's "keep" is a camera-fit only).
        idx = self.cmb_view_preset.findData("iso")
        if idx >= 0:
            self.cmb_view_preset.setCurrentIndex(idx)
        self.cmb_view_preset.currentIndexChanged.connect(self._on_view_preset_changed)
        top.addWidget(self.cmb_view_preset)
        self.btn_reset = QPushButton("Fit")
        self.btn_reset.setToolTip("Fit the camera to the visible design (direction is kept).")
        self.btn_reset.clicked.connect(self._reset_camera)
        top.addWidget(self.btn_reset)
        self.btn_popout = QPushButton("Pop out")
        self.btn_popout.setToolTip("Show the 3D design in a separate window.")
        self.btn_popout.clicked.connect(self.popout)
        top.addWidget(self.btn_popout)
        self.btn_appcsxcad = QPushButton("AppCSXCAD…")
        self.btn_appcsxcad.setToolTip(
            "Open the built CSX in AppCSXCAD for the full external 3D/CSX editor."
        )
        self.btn_appcsxcad.clicked.connect(
            lambda _checked=False: self.open_appcsxcad_requested.emit()
        )
        top.addWidget(self.btn_appcsxcad)
        top.addStretch(1)
        self._root_layout.addLayout(top)

        # Side panel -----------------------------------------------------
        panel = QWidget()
        self._side_panel = panel
        panel.setMinimumWidth(145)
        panel.setMaximumWidth(165)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(2)

        scene_box = QGroupBox("Scene")
        scene_form = QFormLayout(scene_box)
        scene_form.setContentsMargins(3, 3, 3, 3)
        scene_form.setSpacing(2)
        self.chk_geom = QCheckBox("Geometry")
        self.chk_geom.setChecked(True)
        self.chk_geom.setToolTip(
            "Show the project primitives (boxes, cylinders, polygons and imported STL).\n"
            "Per-object colour/transparency/visibility from the project is honoured."
        )
        self.chk_geom.toggled.connect(self._draw_geometry)
        scene_form.addRow(self.chk_geom)
        self.sld_geom = QSpinBox()
        self.sld_geom.setRange(0, 100)
        self.sld_geom.setValue(88)
        self.sld_geom.setSuffix(" %")
        self.sld_geom.setMaximumWidth(78)
        self.sld_geom.setToolTip("Global geometry opacity (100 % = solid).")
        self.sld_geom.valueChanged.connect(self._draw_geometry)
        scene_form.addRow("Opacity", self.sld_geom)
        self.chk_domain = QCheckBox("Domain box")
        self.chk_domain.setChecked(True)
        self.chk_domain.setToolTip(
            "Show the FDTD domain approximation around the structure.\n"
            "When the Mesh Viewer has a built CSX its exact simulation area is used; "
            "otherwise the project's air-margin settings are applied to the geometry bounds.\n"
            "When switched off, ruler, triad and camera fit follow the visible geometry/port bounds."
        )
        self.chk_domain.toggled.connect(self._draw_design_overlays)
        scene_form.addRow(self.chk_domain)
        self.chk_ports = QCheckBox("Ports")
        self.chk_ports.setChecked(True)
        self.chk_ports.setToolTip("Show port boxes; inactive ports are grey.")
        self.chk_ports.toggled.connect(self._draw_design_overlays)
        scene_form.addRow(self.chk_ports)
        self.chk_nf2ff = QCheckBox("NF2FF box")
        self.chk_nf2ff.setChecked(True)
        self.chk_nf2ff.setToolTip("Show the NF2FF integration box when NF2FF is enabled.")
        self.chk_nf2ff.toggled.connect(self._draw_design_overlays)
        scene_form.addRow(self.chk_nf2ff)

        # Built-mesh section plane: XY / XZ / YZ + one slider step per mesh line.
        mesh_box = QGroupBox("Mesh grid")
        mesh_layout = QVBoxLayout(mesh_box)
        mesh_layout.setContentsMargins(3, 3, 3, 3)
        mesh_layout.setSpacing(2)
        self.chk_mesh_plane = QCheckBox("Show plane")
        self.chk_mesh_plane.setChecked(False)
        self.chk_mesh_plane.setToolTip(
            "Overlay the built simulation mesh as a grid on a section plane.\n"
            "Build the mesh in the Mesh Viewer first; the plane snaps to a mesh line."
        )
        self.chk_mesh_plane.toggled.connect(self._draw_mesh_plane)
        mesh_layout.addWidget(self.chk_mesh_plane)
        mesh_axis_row = QHBoxLayout()
        mesh_axis_row.addWidget(QLabel("Plane"))
        self.cmb_mesh_axis = QComboBox()
        self.cmb_mesh_axis.addItems(["XY", "XZ", "YZ"])
        self.cmb_mesh_axis.setToolTip(
            "Section plane: XY (normal z), XZ (normal y), YZ (normal x)."
        )
        self.cmb_mesh_axis.currentIndexChanged.connect(self._on_mesh_axis_changed)
        mesh_axis_row.addWidget(self.cmb_mesh_axis, 1)
        mesh_layout.addLayout(mesh_axis_row)
        self.slider_mesh_plane = QSlider(Qt.Orientation.Horizontal)
        self.slider_mesh_plane.setRange(0, 1000)
        self.slider_mesh_plane.setValue(500)
        self.slider_mesh_plane.setToolTip(
            "Mesh line of the selected plane.\n"
            "Every slider step is one line of the built mesh."
        )
        self.slider_mesh_plane.valueChanged.connect(self._on_mesh_plane_slider)
        mesh_layout.addWidget(self.slider_mesh_plane)
        self.lbl_mesh_plane = QLabel("")
        self.lbl_mesh_plane.setStyleSheet("color:#555;")
        self.lbl_mesh_plane.setWordWrap(True)
        mesh_layout.addWidget(self.lbl_mesh_plane)
        panel_layout.addWidget(mesh_box)

        info_box = QGroupBox("Information")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(3, 3, 3, 3)
        self.lbl_design_info = QLabel("")
        self.lbl_design_info.setWordWrap(True)
        self.lbl_design_info.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.lbl_design_info.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        info_font = self.lbl_design_info.font()
        info_font.setPointSize(max(7, info_font.pointSize() - 1))
        self.lbl_design_info.setFont(info_font)
        self.lbl_design_info.setStyleSheet("color:#555;")
        info_layout.addWidget(self.lbl_design_info)

        panel_layout.addWidget(scene_box)
        panel_layout.addWidget(info_box)
        panel_layout.addStretch(1)

        # 3D host --------------------------------------------------------
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_hint = QLabel(
            "3D view will be created when this tab is shown.\n"
            "Use the mouse to rotate (left), pan (middle) and zoom (wheel/right)."
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#666;")
        self._host_layout.addWidget(self.lbl_hint)
        self._host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.addWidget(panel)
        main_row.addWidget(self._host, 1)
        self._main_row = main_row
        self._root_layout.addLayout(main_row, 1)

        # Right-click menu on the panel, the 3D host and the bottom strip.
        # The VTK child gets its own right-button observer (see
        # _install_vtk_context_menu), because VTK consumes mouse events there.
        for widget in (self, panel, self._host):
            widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            widget.customContextMenuRequested.connect(self._show_context_menu)

        self._root_layout.addWidget(self._hline())
        self._status = QLabel(
            "Geometry, ports and the NF2FF box are read-only here. "
            "Use Geometry / Ports / Simulation to edit them."
        )
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color:#666;")
        self._status.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._status.customContextMenuRequested.connect(self._show_context_menu)
        self._root_layout.addWidget(self._status)

        self._update_design_info()
        self._update_mesh_plane_controls()

    @staticmethod
    def _hline() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # The VTK GL window is created only once the tab is really visible.
        self._render_design()

    def _configure_render_window(self, render_window) -> None:
        """Enable the transparent layer used by the mesh-plane overlay."""
        try:
            render_window.SetAlphaBitPlanes(1)
            render_window.SetNumberOfLayers(2)
        except Exception:  # noqa: BLE001 — no alpha framebuffer
            pass

    def _ensure_render_window(self) -> bool:
        ok = super()._ensure_render_window()
        if ok:
            self._install_vtk_context_menu()
            self._install_ruler_view_observer()
            self._ensure_mesh_overlay_renderer()
        return ok

    def _install_ruler_view_observer(self) -> None:
        """Re-check Z-label space after camera interaction / window resize."""
        if self._iren is None or self._ruler_view_observer:
            return
        try:
            self._iren.AddObserver("EndInteractionEvent", self._on_ruler_view_changed)
            self._iren.AddObserver("ConfigureEvent", self._on_ruler_view_changed)
            self._ruler_view_observer = True
        except Exception:  # noqa: BLE001 - viewer still works without it
            pass

    def _on_ruler_view_changed(self, *_args) -> None:
        if self._updating_ruler_labels:
            return
        self._updating_ruler_labels = True
        try:
            if self._update_design_ruler_labels():
                self._render()
        finally:
            self._updating_ruler_labels = False

    def _install_vtk_context_menu(self) -> None:
        """Add a right-click menu to the VTK pane.

        VTK uses the right button for zooming, so the menu is only shown when
        the button is released without a drag (movement <= 5 pixels).
        """
        if self._vtk_menu_installed or self._iren is None:
            return
        try:
            self._iren.AddObserver(
                "RightButtonPressEvent", self._on_vtk_right_press
            )
            self._iren.AddObserver(
                "RightButtonReleaseEvent", self._on_vtk_right_release
            )
            self._iren.AddObserver(
                "LeftButtonPressEvent", self._on_measure_pick, 1.0
            )
            self._vtk_menu_installed = True
        except Exception:  # noqa: BLE001 — the toolbar/panel menu still works
            pass

    def _on_vtk_right_press(self, _obj, _event) -> None:
        try:
            self._vtk_right_pos = tuple(self._iren.GetEventPosition())
        except Exception:  # noqa: BLE001
            self._vtk_right_pos = None

    def _on_vtk_right_release(self, _obj, _event) -> None:
        start = self._vtk_right_pos
        self._vtk_right_pos = None
        if start is None:
            return
        try:
            end = tuple(self._iren.GetEventPosition())
        except Exception:  # noqa: BLE001
            return
        if (end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2 > 25:
            return  # right-drag was a zoom gesture, not a menu click
        try:
            point = self._iren.mapToGlobal(QPoint(int(end[0]), int(end[1])))
        except Exception:  # noqa: BLE001
            return
        QTimer.singleShot(0, lambda: self._show_context_menu_global(point))

    # ----------------------------------------------------------- context menu
    def _show_context_menu(self, pos) -> None:
        sender = self.sender()
        try:
            point = sender.mapToGlobal(pos) if sender is not None else self.mapToGlobal(pos)
        except Exception:  # noqa: BLE001
            point = self.mapToGlobal(pos)
        self._show_context_menu_global(point)

    def _show_context_menu_global(self, point) -> None:
        self._build_context_menu().exec(point)

    def _build_context_menu(self) -> QMenu:
        menu = QMenu(self)
        act_contrast = menu.addAction("High contrast objects")
        act_contrast.setCheckable(True)
        act_contrast.setChecked(bool(self._high_contrast))
        act_contrast.setToolTip(
            "Draw the geometry opaque with a bright outline and flatter\n"
            "lighting so it stands out against the dark background.\n"
            "Optional; off by default."
        )
        act_contrast.toggled.connect(self.set_high_contrast)
        menu.addSeparator()
        coord = menu.addMenu("Coordinate system")
        act_axes = coord.addAction("Show axes (X/Y/Z)")
        act_axes.setCheckable(True)
        act_axes.setChecked(bool(self._axes.get("show", True)))
        act_axes.toggled.connect(lambda on: self._set_design_axes("show", on))
        act_corner = coord.addAction("Corner axes (bottom-left)")
        act_corner.setCheckable(True)
        act_corner.setChecked(bool(self._axes.get("corner", False)))
        act_corner.setToolTip(
            "Show a small fixed X/Y/Z orientation triad in the lower-left\n"
            "corner of the 3D view, independent of the scene size."
        )
        act_corner.toggled.connect(lambda on: self._set_design_axes("corner", on))
        act_labels = coord.addAction("Axis labels")
        act_labels.setCheckable(True)
        act_labels.setChecked(bool(self._axes.get("labels", True)))
        act_labels.toggled.connect(lambda on: self._set_design_axes("labels", on))

        size_menu = coord.addMenu("Axes size")
        for label, factor in (
            ("0.5× (small)", 0.5),
            ("1× (default)", 1.0),
            ("1.5×", 1.5),
            ("2×", 2.0),
            ("3× (large)", 3.0),
        ):
            act = size_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(abs(float(self._axes.get("scale", 1.0)) - factor) < 1e-6)
            act.triggered.connect(
                lambda _checked=False, f=factor: self._set_design_axis_scale(f)
            )

        loc_menu = coord.addMenu("Axes location")
        for label, key in (
            ("Scene lower corner", "min"),
            ("Scene upper corner", "max"),
            ("World origin (0, 0, 0)", "origin"),
            ("Scene center", "center"),
        ):
            act = loc_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._axis_anchor == key)
            act.triggered.connect(
                lambda _checked=False, k=key: self._set_design_axis_anchor(k)
            )

        font_menu = coord.addMenu("Label size")
        for label, size in (
            ("10 pt", 10),
            ("12 pt (default)", 12),
            ("14 pt", 14),
            ("18 pt", 18),
            ("24 pt", 24),
        ):
            act = font_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(int(self._axes.get("font", 12)) == size)
            act.triggered.connect(
                lambda _checked=False, s=size: self._set_design_axis_font(s)
            )

        act_ruler = coord.addAction("Coordinate ruler / scale (numbers)")
        act_ruler.setCheckable(True)
        act_ruler.setChecked(bool(self._axes.get("ruler", False)))
        act_ruler.setToolTip(
            "Draw numeric coordinate axes around the scene bounds.\n"
            "Labels use the project drawing unit (mm by default)."
        )
        act_ruler.toggled.connect(lambda on: self._set_design_axes("ruler", on))

        place_menu = coord.addMenu("Ruler placement")
        for label, key in (
            ("Closest to camera", "closest"),
            ("Farthest from camera", "furthest"),
            ("Outer box edges", "outer"),
            ("Static edges", "static_edges"),
            ("Static triad", "static_triad"),
        ):
            act = place_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(str(self._axes.get("ruler_fly", "closest")) == key)
            act.triggered.connect(
                lambda _checked=False, k=key: self._set_ruler_fly_mode(k)
            )

        tick_menu = coord.addMenu("Ruler tick side")
        for label, key in (
            ("Outside", "outside"),
            ("Inside", "inside"),
            ("Both sides", "both"),
        ):
            act = tick_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(str(self._axes.get("ruler_ticks", "outside")) == key)
            act.triggered.connect(
                lambda _checked=False, k=key: self._set_ruler_tick_side(k)
            )

        act_tape = coord.addAction("Axis tape (ticks on X/Y/Z arrows)")
        act_tape.setCheckable(True)
        act_tape.setChecked(bool(self._axes.get("axis_tape", False)))
        act_tape.setToolTip(
            "Draw tick marks and numeric values directly on the coloured axis\n"
            "arrows (project drawing unit). Optional; off by default."
        )
        act_tape.toggled.connect(lambda on: self._set_design_axes("axis_tape", on))
        tape_axes = coord.addMenu("Axis tape uses")
        for idx, name in enumerate(("X axis", "Y axis", "Z axis")):
            act = tape_axes.addAction(name)
            act.setCheckable(True)
            act.setChecked(idx in self._axis_tape_axes)
            act.triggered.connect(
                lambda checked=False, i=idx: self._set_axis_tape_axis(i, checked)
            )
        coord.addSeparator()
        coord.addAction("Reset coordinate options", self._reset_design_axes)

        menu.addSeparator()
        act_measure = menu.addAction("Measure distance (tape, 2 clicks)")
        act_measure.setCheckable(True)
        act_measure.setChecked(bool(self._measure_mode))
        act_measure.setToolTip(
            "Click two points on the geometry; the distance is shown in the\n"
            "project drawing unit. Right-click again to open this menu."
        )
        act_measure.toggled.connect(self.set_measure_mode)
        act_clear = menu.addAction("Clear measurement")
        act_clear.setEnabled(bool(self._measure_actors or self._measure_text))
        act_clear.triggered.connect(self.clear_measurement)
        menu.addSeparator()
        menu.addMenu(self.build_views_menu(menu))
        menu.addAction("Fit view", self._reset_camera)
        menu.addSeparator()
        if self._popout_win is not None:
            menu.addAction("Dock view", self.restore_from_popout)
        else:
            menu.addAction("Pop out view", self.popout)
        return menu

    def _set_design_axes(self, key: str, on: bool) -> None:
        self._axes[key] = bool(on)
        self._draw_design_overlays()
        if key in ("corner", "labels"):
            self._update_corner_marker()

    def _set_design_axis_scale(self, factor: float) -> None:
        self._axes["scale"] = max(0.2, min(4.0, float(factor)))
        self._draw_design_overlays()

    def _set_design_axis_anchor(self, anchor: str) -> None:
        self._axis_anchor = anchor if anchor in ("min", "max", "origin", "center") else "min"
        self._draw_design_overlays()

    def _set_design_axis_font(self, size: int) -> None:
        self._axes["font"] = max(6, min(72, int(size)))
        self._draw_design_overlays()

    def _set_axis_tape_axis(self, axis: int, on: bool) -> None:
        if axis not in (0, 1, 2):
            return
        if on:
            self._axis_tape_axes.add(axis)
        else:
            self._axis_tape_axes.discard(axis)
        self._axes["axis_tape"] = bool(self._axis_tape_axes)
        self._draw_design_overlays()

    def _set_ruler_fly_mode(self, key: str) -> None:
        allowed = {"closest", "furthest", "outer", "static_edges", "static_triad"}
        self._axes["ruler_fly"] = key if key in allowed else "closest"
        self._draw_design_overlays()

    def _set_ruler_tick_side(self, key: str) -> None:
        self._axes["ruler_ticks"] = key if key in ("outside", "inside", "both") else "outside"
        self._draw_design_overlays()

    def _reset_design_axes(self) -> None:
        self._axes["show"] = True
        self._axes["corner"] = True
        self._axes["labels"] = True
        self._axes["ruler"] = True
        self._axes["ruler_fly"] = "closest"
        self._axes["ruler_ticks"] = "outside"
        self._axes["axis_tape"] = False
        self._axis_tape_axes = {0, 1, 2}
        self._axes["scale"] = 2.0
        self._axes["font"] = 24
        self._axis_anchor = "min"
        self._draw_design_overlays()
        self._update_corner_marker()

    # --------------------------------------------------------------- measuring
    def set_measure_mode(self, on: bool) -> None:
        """Start/stop the two-click tape measure."""
        on = bool(on)
        if on:
            if not self._ensure_render_window():
                return
            self._measure_mode = True
            self._measure_points = []
            self._clear_measure_preview()
            self._measure_text = "Measure: click first point"
            self._set_interactor_enabled(False)
        else:
            self._measure_mode = False
            self._measure_points = []
            self._clear_measure_preview()
            self._set_interactor_enabled(True)
            if not self._measure_actors:
                self._measure_text = ""
        self._update_design_info()
        self._render()

    def clear_measurement(self) -> None:
        """Remove all tape measurements and leave measure mode."""
        self._measure_mode = False
        self._measure_points = []
        self._clear_measure_preview()
        self._remove_actors(self._measure_actors)
        self._measure_actors = []
        self._measure_text = ""
        self._set_interactor_enabled(True)
        self._update_design_info()
        self._render()

    def _set_interactor_enabled(self, on: bool) -> None:
        if self._iren is None:
            return
        try:
            style = self._iren.GetInteractorStyle()
            if style is not None:
                style.SetEnabled(1 if on else 0)
        except Exception:  # noqa: BLE001
            pass

    def _on_measure_pick(self, _obj, _event) -> None:
        if not self._measure_mode or self._iren is None:
            return
        try:
            x, y = self._iren.GetEventPosition()
        except Exception:  # noqa: BLE001
            return
        hit = self.pick_at(int(x), int(y))
        if hit is None:
            self._measure_text = "Measure: no surface hit — try the geometry"
            self._update_design_info()
            return
        _actor, pos = hit
        if not self._measure_points:
            self._measure_points = [pos]
            self._draw_measure_preview(pos)
            self._measure_text = "Measure: click second point"
        else:
            p0 = self._measure_points[0]
            self._measure_points = []
            self._clear_measure_preview()
            self._add_measurement(p0, pos)
            self._measure_mode = False
            self._set_interactor_enabled(True)
        self._update_design_info()
        self._render()

    def _draw_measure_preview(self, pos) -> None:
        if self._renderer is None:
            return
        self._clear_measure_preview()
        vtk = self._vtk or _vtk()
        diag = self._scene_diagonal(
            float(getattr(self, "_geom_unit", 1e-3) or 1e-3),
            include_domain=self._domain_box_visible(),
        )
        src = vtk.vtkSphereSource()
        src.SetCenter(*pos)
        src.SetRadius(max(diag * 0.006, 1e-12))
        src.SetThetaResolution(16)
        src.SetPhiResolution(10)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(src.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(1.0, 0.82, 0.0)
        self._renderer.AddActor(actor)
        self._measure_preview_actors.append(actor)

    def _clear_measure_preview(self) -> None:
        self._remove_actors(self._measure_preview_actors)
        self._measure_preview_actors = []

    def _add_measurement(self, p0, p1) -> None:
        if self._renderer is None:
            return
        vtk = self._vtk or _vtk()
        scale = float(getattr(self, "_geom_unit", 1e-3) or 1e-3)
        diag = self._scene_diagonal(
            scale, include_domain=self._domain_box_visible()
        )
        try:
            line = vtk.vtkLineSource()
            line.SetPoint1(*p0)
            line.SetPoint2(*p1)
            tube = vtk.vtkTubeFilter()
            tube.SetInputConnection(line.GetOutputPort())
            tube.SetRadius(max(diag * 0.0018, 1e-12))
            tube.SetNumberOfSides(10)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(tube.GetOutputPort())
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(1.0, 0.82, 0.0)
            self._renderer.AddActor(actor)
            self._measure_actors.append(actor)
            for point in (p0, p1):
                dot = vtk.vtkSphereSource()
                dot.SetCenter(*point)
                dot.SetRadius(max(diag * 0.005, 1e-12))
                dot.SetThetaResolution(14)
                dot.SetPhiResolution(8)
                dm = vtk.vtkPolyDataMapper()
                dm.SetInputConnection(dot.GetOutputPort())
                da = vtk.vtkActor()
                da.SetMapper(dm)
                da.GetProperty().SetColor(1.0, 0.65, 0.0)
                self._renderer.AddActor(da)
                self._measure_actors.append(da)
            dist_m = math.dist(p0, p1)
            unit = self._unit_label_for_m(scale)
            value = dist_m / scale
            self._measure_text = f"Distance: {value:.5g} {unit}"
            label = vtk.vtkBillboardTextActor3D()
            label.SetInput(f"{value:.5g} {unit}")
            label.SetPosition(
                0.5 * (p0[0] + p1[0]),
                0.5 * (p0[1] + p1[1]),
                0.5 * (p0[2] + p1[2]),
            )
            prop = label.GetTextProperty()
            prop.SetFontSize(16)
            prop.SetBold(True)
            prop.SetColor(1.0, 0.85, 0.1)
            try:
                prop.SetBackgroundColor(0.1, 0.1, 0.1)
                prop.SetBackgroundOpacity(0.65)
            except Exception:  # noqa: BLE001 — optional on older VTK
                pass
            label.SetPickable(False)
            self._renderer.AddActor(label)
            self._measure_actors.append(label)
        except Exception as e:  # noqa: BLE001 — never break the viewer
            self._measure_text = f"Measure failed: {e}"
            print(f"3D measurement failed: {e}")

    @staticmethod
    def _unit_label_for_m(unit_m: float) -> str:
        """Axis caption for a metres-per-unit factor (matches the ranges)."""
        for factor, label in (
            (1.0, "m"),
            (1e-2, "cm"),
            (1e-3, "mm"),
            (1e-6, "µm"),
            (1e-9, "nm"),
        ):
            if abs(float(unit_m) - factor) <= max(abs(factor) * 1e-9, 1e-18):
                return label
        return "mm"

    # -------------------------------------------------------------- design API
    def set_design(
        self,
        project=None,
        primitives=None,
        ports=None,
        nf2ff=None,
        unit_m: float = 1e-3,
        domain=None,
        mesh_lines=None,
        *,
        fit: bool = False,
    ) -> None:
        """Replace the displayed project snapshot.

        ``primitives`` are the same dicts the Mesh Viewer / script generator
        use.  ``domain`` is an optional ``(lo, hi)`` box in drawing units; when
        omitted it is derived from the primitives and the project's boundary
        air margins.
        """
        self.clear_measurement()
        self._design_project = project
        self._design_prims = [dict(p) for p in (primitives or [])]
        self._design_ports = list(ports or [])
        self._design_nf2ff = nf2ff
        self._geom_prims = list(self._design_prims)
        self._geom_unit = float(unit_m or 1e-3)
        box = _normalise_box(domain)
        self._design_domain = box or self._fallback_domain(
            project, self._design_prims
        )
        self._design_needs_fit = bool(fit)
        self._set_mesh_lines(mesh_lines)
        self._draw_geometry()
        self._draw_design_overlays()
        self._draw_mesh_plane()
        self._update_design_info()
        if self.isVisible():
            self._render_design()

    def clear(self, message: str | None = None) -> None:
        """Drop the displayed design (used when a new/empty project is opened)."""
        self.clear_measurement()
        self._design_project = None
        self._design_prims = []
        self._design_ports = []
        self._design_nf2ff = None
        self._design_domain = None
        self._geom_prims = []
        self._remove_actors(self._geom_actors)
        self._geom_actors = []
        self._remove_actors(self._design_actors)
        self._design_actors = []
        self._remove_actors([a for _cat, _name, a in self._overlay_actors])
        self._overlay_actors = []
        self._clear_mesh_plane_actor()
        self._set_mesh_lines(None)
        self._update_mesh_plane_controls()
        self._update_design_info()
        if message:
            self.lbl_hint.setText(message)
            self.lbl_hint.setVisible(True)
        self._render()

    def set_highlight(
        self,
        boxes=None,
        names=None,
        label: str = "",
        focus: bool = False,
    ) -> None:
        """Highlight the object selected in the Project / Geometry list.

        ``boxes`` and ``names`` use the same convention as the Mesh Viewer;
        ``names`` is the reliable key for primitives and ports.  The highlight
        is a thick yellow outline around the matching object, so it also works
        for imported triangle meshes.  ``focus`` is accepted for API symmetry
        with the Mesh Viewer and currently does not move the 3D camera.
        """
        del focus  # keep the camera stable while clicking through objects
        if isinstance(boxes, str) and names is None:
            boxes, names = None, [boxes]
        clean_boxes: list[tuple[tuple, tuple]] = []
        for box in boxes or []:
            try:
                b0, b1 = box
                clean_boxes.append(
                    (
                        tuple(float(v) for v in b0),
                        tuple(float(v) for v in b1),
                    )
                )
            except (TypeError, ValueError):
                continue
        self._highlight_boxes = clean_boxes
        self._highlight_names = [str(n) for n in (names or []) if str(n)]
        self._highlight_label = str(label or "")
        self._update_design_info()
        self._draw_design_overlays()

    # ----------------------------------------------------------- extensibility
    def add_overlay_actor(
        self, actor, *, category: str = "user", name: str = ""
    ) -> None:
        """Add a temporary tool actor (ruler, section outline, selection marker)."""
        self._overlay_actors.append((str(category), str(name), actor))
        if self._renderer is not None:
            self._apply_clipping_to_actor(actor)
            self._renderer.AddActor(actor)
            self._render()

    def remove_overlay_actors(self, *, category: str | None = None) -> None:
        """Remove temporary actors, optionally only one category."""
        keep: list[tuple[str, str, Any]] = []
        for cat, name, actor in self._overlay_actors:
            if category is None or cat == category:
                if self._renderer is not None:
                    self._renderer.RemoveActor(actor)
            else:
                keep.append((cat, name, actor))
        self._overlay_actors = keep
        self._render()

    def set_clip_plane(self, origin=None, normal=None) -> None:
        """Apply/remove a clipping plane on the whole design scene.

        ``origin`` and ``normal`` use the VTK scene coordinates (metres after
        the project unit conversion); pass ``None`` for both to remove the
        clip.  This is the hook for a later section-plane widget.
        """
        plane = None
        if origin is not None and normal is not None:
            try:
                vtk = self._vtk or _vtk()
                plane = vtk.vtkPlane()
                plane.SetOrigin(*[float(v) for v in origin])
                plane.SetNormal(*[float(v) for v in normal])
            except (TypeError, ValueError):
                plane = None
        self._design_clip_plane = plane
        self._apply_clipping_to_all()
        self._render()

    # ---------------------------------------------------------- mesh plane
    def _set_mesh_lines(self, mesh_lines) -> None:
        """Normalise built-mesh line coordinates (drawing units)."""
        out: dict[str, np.ndarray] = {}
        for axis in "xyz":
            try:
                arr = np.asarray(
                    (mesh_lines or {}).get(axis, []), dtype=float
                ).reshape(-1)
                arr = np.unique(arr[np.isfinite(arr)])
            except Exception:  # noqa: BLE001 — malformed/old caller
                arr = np.empty(0, dtype=float)
            out[axis] = arr
        self._mesh_lines = out

    def set_mesh_color(self, color: str = "#ffffff") -> None:
        """Set the mesh-plane grid colour (live update, no rebuild)."""
        c = QColor(str(color or "#ffffff"))
        if not c.isValid():
            c = QColor("#ffffff")
        self._mesh_color = (c.redF(), c.greenF(), c.blueF())
        actor = self._mesh_plane_actor
        if actor is not None:
            try:
                actor.GetProperty().SetColor(*self._mesh_color)
            except Exception:  # noqa: BLE001
                pass
            self._render()

    def mesh_color(self) -> str:
        """Current mesh-plane grid colour as ``#rrggbb``."""
        r, g, b = self._mesh_color
        return (
            f"#{int(round(r * 255)):02x}"
            f"{int(round(g * 255)):02x}"
            f"{int(round(b * 255)):02x}"
        )

    def _mesh_axis_key(self) -> str:
        """Normal axis of the selected section plane (XY->z, XZ->y, YZ->x)."""
        return {"XY": "z", "XZ": "y", "YZ": "x"}.get(
            self.cmb_mesh_axis.currentText(), "z"
        )

    def _mesh_axis_values_m(self) -> np.ndarray:
        """Mesh line coordinates along the current plane normal, in metres."""
        scale = float(getattr(self, "_geom_unit", 1e-3) or 1e-3)
        return np.asarray(
            self._mesh_lines.get(self._mesh_axis_key(), []), dtype=float
        ).reshape(-1) * scale

    def _mesh_plane_pos(self) -> float | None:
        """Selected mesh line of the plane normal, in metres."""
        vals = self._mesh_axis_values_m()
        if vals.size == 0:
            return None
        idx = max(0, min(vals.size - 1, int(self.slider_mesh_plane.value())))
        return float(vals[idx])

    def _update_mesh_plane_controls(self) -> None:
        """Enable the plane controls only when a built mesh is available."""
        n = int(self._mesh_axis_values_m().size)
        has = n > 0
        for w in (self.chk_mesh_plane, self.cmb_mesh_axis, self.slider_mesh_plane):
            w.setEnabled(has)
        self.slider_mesh_plane.blockSignals(True)
        if not has:
            self.slider_mesh_plane.setRange(0, 1000)
            self.slider_mesh_plane.setValue(500)
            self.lbl_mesh_plane.setText("build mesh first (Mesh Viewer)")
            self.slider_mesh_plane.blockSignals(False)
            return
        self.slider_mesh_plane.setRange(0, max(0, n - 1))
        if not (0 <= self.slider_mesh_plane.value() < n):
            self.slider_mesh_plane.setValue(n // 2)
        self.slider_mesh_plane.blockSignals(False)
        scale = float(getattr(self, "_geom_unit", 1e-3) or 1e-3)
        pos = self._mesh_plane_pos()
        unit = self._unit_label_for_m(scale)
        self.lbl_mesh_plane.setText(
            f"{self._mesh_axis_key().upper()} = {pos / scale:.3f} {unit}"
            if pos is not None
            else ""
        )

    def _on_mesh_axis_changed(self, *_args) -> None:
        n = int(self._mesh_axis_values_m().size)
        self.slider_mesh_plane.blockSignals(True)
        self.slider_mesh_plane.setRange(0, max(0, n - 1))
        self.slider_mesh_plane.setValue(n // 2)
        self.slider_mesh_plane.blockSignals(False)
        self._draw_mesh_plane()

    def _on_mesh_plane_slider(self, _value: int) -> None:
        self._draw_mesh_plane()

    def _make_mesh_plane_actor(self):
        """Grid actor of the built mesh at the current section plane."""
        if self._renderer is None:
            return None
        try:
            vtk = self._vtk or _vtk()
            self._vtk = vtk
            scale = float(getattr(self, "_geom_unit", 1e-3) or 1e-3)
            lines_m = {
                axis: np.asarray(self._mesh_lines.get(axis, []), dtype=float)
                * scale
                for axis in "xyz"
            }
            axis = self._mesh_axis_key()
            normal = {"x": 0, "y": 1, "z": 2}[axis]
            in_plane = [i for i in range(3) if i != normal]
            a_vals = lines_m["xyz"[in_plane[0]]]
            b_vals = lines_m["xyz"[in_plane[1]]]
            pos = self._mesh_plane_pos()
            if pos is None or a_vals.size < 2 or b_vals.size < 2:
                return None
            a0, a1 = float(a_vals.min()), float(a_vals.max())
            b0, b1 = float(b_vals.min()), float(b_vals.max())
            points = vtk.vtkPoints()
            cells = vtk.vtkCellArray()

            def add_line(p0, p1) -> None:
                i0 = points.InsertNextPoint(*p0)
                i1 = points.InsertNextPoint(*p1)
                cells.InsertNextCell(2)
                cells.InsertCellPoint(i0)
                cells.InsertCellPoint(i1)

            for a in a_vals:
                p0 = [0.0, 0.0, 0.0]
                p1 = [0.0, 0.0, 0.0]
                p0[in_plane[0]] = p1[in_plane[0]] = float(a)
                p0[in_plane[1]] = b0
                p1[in_plane[1]] = b1
                p0[normal] = p1[normal] = pos
                add_line(p0, p1)
            for b in b_vals:
                p0 = [0.0, 0.0, 0.0]
                p1 = [0.0, 0.0, 0.0]
                p0[in_plane[1]] = p1[in_plane[1]] = float(b)
                p0[in_plane[0]] = a0
                p1[in_plane[0]] = a1
                p0[normal] = p1[normal] = pos
                add_line(p0, p1)

            poly = vtk.vtkPolyData()
            poly.SetPoints(points)
            poly.SetLines(cells)
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(poly)
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetColor(*self._mesh_color)
            prop.SetLineWidth(1.0)
            prop.SetOpacity(0.9)
            prop.SetLighting(False)
            return actor
        except Exception:  # noqa: BLE001 — never block the design view
            return None

    def _ensure_mesh_overlay_renderer(self) -> bool:
        """Create a transparent layer-1 renderer for the mesh grid.

        A separate renderer clears the depth buffer for its layer, so the mesh
        section grid stays visible even when it lies inside the opaque design
        geometry.  It shares the main camera, so it follows every rotation /
        zoom / fit without extra synchronization.
        """
        if self._iren is None or self._renderer is None:
            return False
        try:
            win = self._iren.GetRenderWindow()
        except Exception:  # noqa: BLE001
            return False
        if win is None:
            return False
        if (
            self._mesh_overlay_renderer is not None
            and self._mesh_overlay_window is win
        ):
            try:
                self._mesh_overlay_renderer.SetActiveCamera(
                    self._renderer.GetActiveCamera()
                )
            except Exception:  # noqa: BLE001
                pass
            return True
        if (
            self._mesh_overlay_renderer is not None
            and self._mesh_overlay_window is not None
        ):
            try:
                self._mesh_overlay_window.RemoveRenderer(self._mesh_overlay_renderer)
            except Exception:  # noqa: BLE001
                pass
        try:
            vtk = self._vtk or _vtk()
            self._vtk = vtk
            overlay = vtk.vtkRenderer()
            overlay.SetLayer(1)
            overlay.SetBackground(0.15, 0.15, 0.18)
            try:
                overlay.SetBackgroundAlpha(0.0)
            except Exception:  # noqa: BLE001 — older VTK
                pass
            overlay.SetPreserveDepthBuffer(False)
            try:
                overlay.SetInteractive(0)
            except Exception:  # noqa: BLE001
                pass
            overlay.SetActiveCamera(self._renderer.GetActiveCamera())
            win.SetNumberOfLayers(2)
            try:
                win.SetAlphaBitPlanes(1)
            except Exception:  # noqa: BLE001 — no alpha framebuffer
                pass
            win.AddRenderer(overlay)
            self._mesh_overlay_renderer = overlay
            self._mesh_overlay_window = win
            return True
        except Exception:  # noqa: BLE001 — fall back to the main renderer
            self._mesh_overlay_renderer = None
            self._mesh_overlay_window = None
            return False

    def _clear_mesh_plane_actor(self) -> None:
        """Remove the mesh grid from whichever renderer currently holds it."""
        actor = self._mesh_plane_actor
        if actor is None:
            return
        for renderer in (self._mesh_overlay_renderer, self._renderer):
            if renderer is None:
                continue
            try:
                renderer.RemoveActor(actor)
            except Exception:  # noqa: BLE001
                pass
        self._mesh_plane_actor = None

    def _draw_mesh_plane(self, *_args) -> None:
        """Rebuild the mesh-plane overlay (kept separate from scene actors)."""
        self._update_mesh_plane_controls()
        if self._renderer is None:
            return
        self._clear_mesh_plane_actor()
        if not self.chk_mesh_plane.isChecked():
            self._render()
            return
        actor = self._make_mesh_plane_actor()
        if actor is None:
            self._render()
            return
        target = (
            self._mesh_overlay_renderer
            if self._ensure_mesh_overlay_renderer()
            else self._renderer
        )
        try:
            target.AddActor(actor)
            self._mesh_plane_actor = actor
        except Exception:  # noqa: BLE001 — keep the viewer usable
            self._mesh_plane_actor = None
        self._render()

    def pick_at(self, x: int, y: int):
        """Return ``(actor, world_position)`` under a viewport pixel, or None.

        Intended for object selection and measurement tools.  The call is safe
        when VTK is unavailable (returns ``None``).
        """
        if self._renderer is None or self._iren is None:
            return None
        try:
            vtk = self._vtk or _vtk()
            picker = vtk.vtkCellPicker()
            picker.SetTolerance(0.005)
            picker.Pick(float(x), float(y), 0.0, self._renderer)
            actor = picker.GetActor()
            if actor is None:
                return None
            return actor, tuple(float(v) for v in picker.GetPickPosition())
        except Exception:  # noqa: BLE001 — picking must never break the view
            return None

    # -------------------------------------------------------------- rendering
    def _update_corner_marker(self) -> None:
        """Small fixed-size orientation triad in the lower-left corner."""
        if self._iren is None:
            return
        show = bool(self._axes.get("corner", False))
        if show and self._marker is None:
            try:
                vtk = self._vtk or _vtk()
                self._vtk = vtk
                axes = vtk.vtkAxesActor()
                axes.SetTotalLength(1.0, 1.0, 1.0)
                widget = vtk.vtkOrientationMarkerWidget()
                widget.SetOrientationMarker(axes)
                widget.SetInteractor(self._iren)
                configure_marker_widget(
                    widget, viewport=(0.0, 0.0, 0.13, 0.18)
                )
                self._marker_axes, self._marker = axes, widget
            except Exception as e:  # noqa: BLE001 — keep the view usable
                print(f"3D corner marker unavailable: {e}")
                self._marker = None
                self._marker_axes = None
                return
        if self._marker is None:
            return
        if show:
            try:
                self._marker.SetEnabled(1)
                style_axes_triad(
                    self._marker_axes,
                    show_labels=bool(self._axes.get("labels", True)),
                    font_size=max(7, min(14, int(self._axes.get("font", 12)) - 2)),
                )
                self._marker.SetViewport(0.0, 0.0, 0.13, 0.18)
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                self._marker.SetEnabled(0)
            except Exception:  # noqa: BLE001
                pass

    def _render_design(self) -> None:
        first_render = self._renderer is None
        if not self._ensure_render_window():
            return
        self._draw_geometry()
        self._draw_design_overlays()
        self._draw_mesh_plane()
        self._update_corner_marker()
        # A scene built while the tab was hidden has no camera yet, so fit on
        # the first real render even if set_design(fit=False) was used. The
        # selected standard view is applied as a *mode*: "Isometric" keeps the
        # scene isometric when the tab is opened, "Fit (keep direction)" only
        # fits. On the very first render "keep" falls back to "iso" because
        # there is no previous direction - that stale top/rolled camera from an
        # earlier tab was the reported "view wrong" symptom.
        if first_render or self._design_needs_fit:
            key = self.view_preset()
            if key in ("", "keep"):
                key = "iso" if first_render else "keep"
            try:
                self.apply_view_preset(key)
            except Exception:  # noqa: BLE001
                pass
            self._design_needs_fit = False
        self._render()

    def _render(self) -> None:
        if self._iren is None:
            return
        try:
            self._iren.GetRenderWindow().Render()
        except Exception:  # noqa: BLE001 — never block on a redraw
            pass

    def apply_view_preset(self, key: str | None = None) -> None:
        """Fit/standard view using the design bounds, not axes/label bounds.

        ``vtkBillboardTextActor3D`` (axis captions, measurement labels) can
        report unusable world bounds and would otherwise zoom the scene far
        out. The camera is therefore fitted to the actual geometry/domain box.
        """
        if self._renderer is None:
            return
        key = str(key if key is not None else self.view_preset())
        scale = float(getattr(self, "_geom_unit", 1e-3) or 1e-3)
        bounds = self._scene_bounds_m(
            scale, include_domain=self._domain_box_visible()
        )
        if bounds is None:
            try:
                bounds = self._renderer.ComputeVisiblePropBounds()
            except Exception:  # noqa: BLE001
                return
        camera = preset_camera(bounds, key)
        if camera is not None:
            position, focal, up = camera
            cam = self._renderer.GetActiveCamera()
            cam.SetPosition(*position)
            cam.SetFocalPoint(*focal)
            cam.SetViewUp(*up)
            cam.OrthogonalizeViewUp()
        self._renderer.ResetCamera(*bounds)
        self._renderer.ResetCameraClippingRange()
        self._update_design_ruler_labels()
        self._render()

    def _draw_geometry(self, *_args) -> None:
        """Draw primitives with per-object display colours/visibility."""
        if self._renderer is None:
            return
        vtk = self._vtk or _vtk()
        self._vtk = vtk
        self._remove_actors(self._geom_actors)
        self._geom_actors = []
        if not getattr(self, "chk_geom", None) or not self.chk_geom.isChecked():
            self._render()
            return
        opacity = max(0.0, min(1.0, self.sld_geom.value() / 100.0))
        scale = float(getattr(self, "_geom_unit", 1e-3) or 1e-3)
        for pr in getattr(self, "_geom_prims", []):
            disp = self._object_display(pr)
            if not disp.get("visible", True):
                continue
            actor = self._make_geometry_actor(vtk, pr, scale)
            if actor is None:
                continue
            prop = actor.GetProperty()
            rgb = display_rgb(disp) or self._default_material_color(pr)
            alpha = opacity * display_alpha(disp)
            prop.SetColor(*rgb)
            prop.SetEdgeVisibility(True)
            if self._high_contrast:
                # opaque fill + a bright outline: the object stays readable
                # even when its colour is close to the dark background
                prop.SetOpacity(1.0)
                prop.SetEdgeColor(1.0, 1.0, 1.0)
                prop.SetLineWidth(2.0)
                prop.SetAmbient(0.35)
                prop.SetDiffuse(0.9)
            else:
                prop.SetOpacity(alpha)
                prop.SetEdgeColor(
                    max(0.0, rgb[0] * 0.45),
                    max(0.0, rgb[1] * 0.45),
                    max(0.0, rgb[2] * 0.45),
                )
                prop.SetLineWidth(1.0)
            self._apply_clipping_to_actor(actor)
            self._renderer.AddActor(actor)
            self._geom_actors.append(actor)
        self._render()

    def _apply_axes(self) -> None:
        """Design scene owns the ruler/triad; only sync the corner marker.

        The inherited Field-Viewer ``_apply_axes()`` draws its own cube ruler
        and in-scene triad.  The 3D Design view draws the scene-aware ruler in
        ``_draw_coordinate_system()``, so running both produced two rulers (one
        at the geometry, one at the domain/field bounds).
        """
        self._update_corner_marker()
        if self._iren is not None:
            try:
                self._iren.GetRenderWindow().Render()
            except Exception:  # noqa: BLE001 - never block a redraw
                pass

    def _draw_design_overlays(self, *_args) -> None:
        """Redraw domain / NF2FF / port boxes (not user tool overlays)."""
        if self._renderer is None:
            return
        vtk = self._vtk or _vtk()
        self._vtk = vtk
        # Defensive cleanup: older builds / inherited showEvent paths may have
        # created the Field-Viewer ruler/triad actors. They are not part of the
        # design actor list, so remove them before drawing the design ruler.
        for attr in ("_ruler_actor", "_z_ruler_actor", "_triad_actor"):
            actor = getattr(self, attr, None)
            if actor is not None:
                self._renderer.RemoveActor(actor)
                setattr(self, attr, None)
        self._remove_actors(self._design_actors)
        self._design_actors = []
        self._design_ruler_actor = None
        scale = float(getattr(self, "_geom_unit", 1e-3) or 1e-3)
        diag = self._scene_diagonal(
            scale, include_domain=self._domain_box_visible()
        )

        if getattr(self, "chk_domain", None) and self.chk_domain.isChecked():
            if self._design_domain is not None:
                lo, hi = self._design_domain
                actor = self._wire_box(vtk, lo, hi, scale, (0.78, 0.80, 0.86), 1.2)
                if actor is not None:
                    self._renderer.AddActor(actor)
                    self._design_actors.append(actor)

        nf = self._design_nf2ff
        if (
            getattr(self, "chk_nf2ff", None)
            and self.chk_nf2ff.isChecked()
            and nf is not None
            and bool(getattr(nf, "enabled", False))
        ):
            try:
                lo = [float(v) for v in nf.start]
                hi = [float(v) for v in nf.stop]
                if all(abs(hi[i] - lo[i]) > 1e-12 for i in range(3)):
                    actor = self._wire_box(vtk, lo, hi, scale, (0.62, 0.18, 0.70), 2.0)
                    if actor is not None:
                        self._renderer.AddActor(actor)
                        self._design_actors.append(actor)
            except (TypeError, ValueError, IndexError):
                pass

        if getattr(self, "chk_ports", None) and self.chk_ports.isChecked():
            for port in self._design_ports:
                try:
                    st = [float(v) for v in port.start]
                    sp = [float(v) for v in port.stop]
                except (TypeError, ValueError, AttributeError):
                    continue
                actor = self._port_actor(vtk, st, sp, scale, diag, port)
                if actor is not None:
                    self._renderer.AddActor(actor)
                    self._design_actors.append(actor)

        # Selection from the Project / Geometry list: a yellow outline that is
        # slightly larger than the object so it stays visible on coincident
        # geometry edges.
        pad = max(diag * 0.004, 1e-12)
        for lo, hi in self._highlighted_boxes():
            actor = self._wire_box(
                vtk,
                [lo[i] - pad / scale for i in range(3)],
                [hi[i] + pad / scale for i in range(3)],
                scale,
                (1.0, 0.82, 0.0),
                3.0,
            )
            if actor is not None:
                self._renderer.AddActor(actor)
                self._design_actors.append(actor)

        self._draw_coordinate_system(vtk, scale, diag)
        self._apply_clipping_to_all()
        self._render()

    # --------------------------------------------------------------- actors
    def _wire_box(self, vtk, lo, hi, scale: float, rgb, line_width: float):
        try:
            bounds = (
                float(lo[0]) * scale,
                float(hi[0]) * scale,
                float(lo[1]) * scale,
                float(hi[1]) * scale,
                float(lo[2]) * scale,
                float(hi[2]) * scale,
            )
            src = vtk.vtkCubeSource()
            src.SetBounds(
                min(bounds[0], bounds[1]), max(bounds[0], bounds[1]),
                min(bounds[2], bounds[3]), max(bounds[2], bounds[3]),
                min(bounds[4], bounds[5]), max(bounds[4], bounds[5]),
            )
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(src.GetOutputPort())
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetRepresentationToWireframe()
            prop.SetColor(*rgb)
            prop.SetLineWidth(float(line_width))
            prop.SetOpacity(1.0)
            return actor
        except (TypeError, ValueError, IndexError):
            return None

    def _port_actor(self, vtk, start, stop, scale: float, diag: float, port):
        lo = [min(start[i], stop[i]) * scale for i in range(3)]
        hi = [max(start[i], stop[i]) * scale for i in range(3)]
        active = bool(getattr(port, "active", True))
        rgb = (0.95, 0.24, 0.20) if active else (0.58, 0.58, 0.58)
        try:
            if all(hi[i] - lo[i] <= max(diag * 1e-5, 1e-15) for i in range(3)):
                # Degenerate line/point port: a small sphere is the only
                # unambiguous marker at 3D scale.
                src = vtk.vtkSphereSource()
                src.SetCenter(*[(lo[i] + hi[i]) * 0.5 for i in range(3)])
                src.SetRadius(max(diag * 0.012, 1e-12))
                src.SetThetaResolution(16)
                src.SetPhiResolution(10)
            else:
                src = vtk.vtkCubeSource()
                src.SetBounds(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(src.GetOutputPort())
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            prop = actor.GetProperty()
            prop.SetColor(*rgb)
            prop.SetOpacity(0.58 if active else 0.32)
            prop.SetEdgeVisibility(True)
            prop.SetEdgeColor(*[max(0.0, c * 0.55) for c in rgb])
            prop.SetLineWidth(1.0)
            return actor
        except (TypeError, ValueError):
            return None

    def _scene_diagonal(self, scale: float, *, include_domain: bool = True) -> float:
        """Approximate visible design diagonal in metres (for marker sizes)."""
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        boxes = []
        if include_domain and self._design_domain is not None:
            boxes.append(self._design_domain)
        for pr in getattr(self, "_design_prims", []):
            try:
                from app.meshing.bounds import cartesian_primitive_bbox

                bb = cartesian_primitive_bbox(pr)
            except Exception:  # noqa: BLE001
                bb = None
            if bb is not None:
                boxes.append(bb)
        for port in self._design_ports:
            try:
                boxes.append((port.start, port.stop))
            except Exception:  # noqa: BLE001
                pass
        if not boxes and self._design_domain is not None:
            boxes.append(self._design_domain)
        for box_lo, box_hi in boxes:
            for i in range(3):
                lo[i] = min(lo[i], float(box_lo[i]) * scale)
                hi[i] = max(hi[i], float(box_hi[i]) * scale)
        if not all(math.isfinite(v) for v in lo + hi):
            return 1e-3
        return max(math.dist(lo, hi), 1e-9)

    def _domain_box_visible(self) -> bool:
        """True when the domain-box overlay is currently switched on."""
        chk = getattr(self, "chk_domain", None)
        if chk is None:
            return False
        try:
            return bool(chk.isChecked())
        except RuntimeError:  # Qt object already deleted during shutdown
            return False

    def _scene_bounds_m(
        self, scale: float, *, include_domain: bool = True
    ) -> tuple[float, float, float, float, float, float] | None:
        """Scene bounds in VTK metres, used by the axes/ruler.

        ``include_domain=False`` excludes the FDTD domain box so the ruler can
        snap to the visible geometry/port boxes when the domain overlay is
        hidden.  If there is no visible geometry at all, the domain bounds are
        still used as a safe fallback (otherwise the ruler would disappear in
        an empty scene).
        """
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        boxes = []
        if include_domain and self._design_domain is not None:
            boxes.append(self._design_domain)
        try:
            from app.meshing.bounds import cartesian_primitive_bbox
        except Exception:  # noqa: BLE001
            cartesian_primitive_bbox = None
        for pr in getattr(self, "_design_prims", []):
            bb = cartesian_primitive_bbox(pr) if cartesian_primitive_bbox else None
            if bb is None:
                st, sp = pr.get("start"), pr.get("stop")
                bb = (st, sp) if st is not None and sp is not None else None
            if bb is not None:
                boxes.append(bb)
        for port in getattr(self, "_design_ports", []):
            try:
                boxes.append((port.start, port.stop))
            except Exception:  # noqa: BLE001
                pass
        if not boxes and self._design_domain is not None:
            # Empty scene with the domain overlay hidden: keep a usable ruler
            # instead of falling through to the renderer's axes/label bounds.
            boxes.append(self._design_domain)
        for box_lo, box_hi in boxes:
            try:
                for i in range(3):
                    lo[i] = min(lo[i], float(box_lo[i]) * scale)
                    hi[i] = max(hi[i], float(box_hi[i]) * scale)
            except (TypeError, ValueError, IndexError):
                continue
        if not all(math.isfinite(v) for v in lo + hi):
            if self._renderer is not None:
                try:
                    b = self._renderer.ComputeVisiblePropBounds()
                    if all(math.isfinite(v) for v in b) and b[1] > b[0]:
                        return (b[0], b[1], b[2], b[3], b[4], b[5])
                except Exception:  # noqa: BLE001
                    pass
            return None
        span = max(hi[i] - lo[i] for i in range(3))
        pad = max(span * 0.002, 1e-9)
        for i in range(3):
            if hi[i] <= lo[i]:
                lo[i] -= pad
                hi[i] += pad
        return (lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])

    def _update_design_ruler_labels(self) -> bool:
        """Hide axis numbers/minor ticks when the projected axis is too short.

        ``vtkCubeAxesActor`` draws all labels even when an axis is only a few
        pixels long (e.g. a flat PCB or a small viewer window).  Measure every
        axis in display pixels after the camera has been set; below a
        font-dependent threshold, hide that axis' numeric labels and minor
        ticks but keep the axis line and title.  The check is repeated after
        camera interaction / window resize, so zooming in brings the numbers
        back.
        """
        cube = self._design_ruler_actor
        if cube is None or self._renderer is None:
            return False
        try:
            b = cube.GetBounds()
            renderer = self._renderer

            def _project(point: tuple[float, float, float]):
                renderer.SetWorldPoint(*point, 1.0)
                renderer.WorldToDisplay()
                return renderer.GetDisplayPoint()

            font = int(self._axes.get("font", 12))
            # Each numeric tick needs roughly 8 glyph heights along the axis.
            # Below that, labels overlap even when the axis itself is visible.
            min_pixels = max(72.0, font * 8.0)
            label_vis = dict(self._axes.get("ruler_labels", {}) or {})
            axes = (
                ("X", (b[0], b[2], b[4]), (b[1], b[2], b[4]), "x"),
                ("Y", (b[0], b[2], b[4]), (b[0], b[3], b[4]), "y"),
                ("Z", (b[0], b[2], b[4]), (b[0], b[2], b[5]), "z"),
            )
            changed = False
            for axis, p0w, p1w, key in axes:
                if not getattr(cube, f"Get{axis}AxisVisibility")():
                    continue
                p0 = _project(p0w)
                p1 = _project(p1w)
                pixels = math.hypot(
                    float(p1[0]) - float(p0[0]), float(p1[1]) - float(p0[1])
                )
                if pixels <= 0.0:
                    # No render window / zero-size viewport yet: do not hide
                    # labels based on a meaningless projection.
                    continue
                want = bool(label_vis.get(key, True)) and pixels >= min_pixels
                if bool(getattr(cube, f"Get{axis}AxisLabelVisibility")()) != want:
                    getattr(cube, f"Set{axis}AxisLabelVisibility")(bool(want))
                    changed = True
                if bool(getattr(cube, f"Get{axis}AxisMinorTickVisibility")()) != want:
                    getattr(cube, f"Set{axis}AxisMinorTickVisibility")(bool(want))
                    changed = True
            return changed
        except Exception:  # noqa: BLE001 - camera/VTK variants
            return False

    def _draw_coordinate_system(self, vtk, scale: float, diag: float) -> None:
        """In-scene X/Y/Z triad and optional numeric cube ruler."""
        if not self._axes.get("show", True) and not self._axes.get("ruler", False):
            return
        triad_bounds = self._scene_bounds_m(
            scale, include_domain=self._domain_box_visible()
        )
        if triad_bounds is None:
            return
        if self._axes.get("show", True) and not self._axes.get("corner", False):
            try:
                try:
                    factor = max(0.2, min(4.0, float(self._axes.get("scale", 1.0))))
                except (TypeError, ValueError):
                    factor = 1.0
                length = max(diag * 0.22 * factor, 1e-9)
                anchor = self._axis_anchor
                if anchor == "max":
                    pos = (triad_bounds[1], triad_bounds[3], triad_bounds[5])
                elif anchor == "origin":
                    pos = (0.0, 0.0, 0.0)
                elif anchor == "center":
                    pos = (
                        0.5 * (triad_bounds[0] + triad_bounds[1]),
                        0.5 * (triad_bounds[2] + triad_bounds[3]),
                        0.5 * (triad_bounds[4] + triad_bounds[5]),
                    )
                else:
                    pos = (triad_bounds[0], triad_bounds[2], triad_bounds[4])
                labels = bool(self._axes.get("labels", True))
                font = int(self._axes.get("font", 12))
                # Custom arrows instead of vtkAxesActor: its 2D captions do not
                # reliably follow SetPosition, which made "axes location" look
                # broken. This version anchors shaft, tip and label together.
                origin_ball = vtk.vtkSphereSource()
                origin_ball.SetCenter(*pos)
                origin_ball.SetRadius(max(diag * 0.006, 1e-12))
                origin_ball.SetThetaResolution(12)
                origin_ball.SetPhiResolution(8)
                om = vtk.vtkPolyDataMapper()
                om.SetInputConnection(origin_ball.GetOutputPort())
                oa = vtk.vtkActor()
                oa.SetMapper(om)
                oa.GetProperty().SetColor(0.85, 0.85, 0.85)
                self._renderer.AddActor(oa)
                self._design_actors.append(oa)
                self._add_axis_arrow(
                    vtk, pos, (1.0, 0.0, 0.0), length, (0.95, 0.28, 0.28), "X",
                    diag, labels, font,
                )
                self._add_axis_arrow(
                    vtk, pos, (0.0, 1.0, 0.0), length, (0.28, 0.88, 0.38), "Y",
                    diag, labels, font,
                )
                self._add_axis_arrow(
                    vtk, pos, (0.0, 0.0, 1.0), length, (0.40, 0.55, 0.98), "Z",
                    diag, labels, font,
                )
                if self._axes.get("axis_tape", False):
                    unit = self._unit_label_for_m(scale)
                    for axis, direction, rgb in (
                        (0, (1.0, 0.0, 0.0), (0.95, 0.28, 0.28)),
                        (1, (0.0, 1.0, 0.0), (0.28, 0.88, 0.38)),
                        (2, (0.0, 0.0, 1.0), (0.40, 0.55, 0.98)),
                    ):
                        if axis not in self._axis_tape_axes:
                            continue
                        self._add_axis_tape(
                            vtk,
                            pos,
                            axis,
                            direction,
                            length,
                            scale,
                            unit,
                            rgb,
                            diag,
                            labels,
                            font,
                        )
            except Exception:  # noqa: BLE001
                pass
        if not self._axes.get("ruler", False):
            return
        ruler_bounds = self._scene_bounds_m(
            scale, include_domain=self._domain_box_visible()
        )
        if ruler_bounds is None:
            ruler_bounds = triad_bounds
        try:
            cube = vtk.vtkCubeAxesActor()
            cube.SetBounds(ruler_bounds)
            cube.SetCamera(self._renderer.GetActiveCamera())
            fly = str(self._axes.get("ruler_fly", "closest"))
            if fly == "furthest":
                cube.SetFlyModeToFurthestTriad()
            elif fly == "outer":
                cube.SetFlyModeToOuterEdges()
            elif fly == "static_edges":
                cube.SetFlyModeToStaticEdges()
            elif fly == "static_triad":
                cube.SetFlyModeToStaticTriad()
            else:
                cube.SetFlyModeToClosestTriad()
            tick_side = str(self._axes.get("ruler_ticks", "outside"))
            if tick_side == "inside":
                cube.SetTickLocationToInside()
            elif tick_side == "both":
                cube.SetTickLocationToBoth()
            else:
                cube.SetTickLocationToOutside()
            cube.SetUseTextActor3D(False)
            cube.SetPickable(False)
            # Bounds live in VTK metres, but labels/ticks can represent a
            # separate range. This keeps the ruler aligned with the scene and
            # still prints the project drawing unit (mm by default).
            unit = self._unit_label_for_m(scale)
            for axis, lo_i, hi_i in zip("XYZ", (0, 2, 4), (1, 3, 5)):
                getattr(cube, f"Set{axis}AxisRange")(
                    ruler_bounds[lo_i] / scale, ruler_bounds[hi_i] / scale
                )
            font = int(self._axes.get("font", 12))
            colors = ((0.92, 0.30, 0.30), (0.30, 0.82, 0.35), (0.35, 0.50, 0.95))
            titles = (f"x [{unit}]", f"y [{unit}]", f"z [{unit}]")
            for axis, title, color in zip("XYZ", titles, colors):
                getattr(cube, f"Set{axis}Title")(title)
                getattr(cube, f"Set{axis}LabelFormat")("%.4g")
                getattr(cube, f"Get{axis}AxesLabelProperty")().SetColor(0.9, 0.9, 0.9)
                getattr(cube, f"Get{axis}AxesLabelProperty")().SetFontSize(font)
                getattr(cube, f"Get{axis}AxesTitleProperty")().SetColor(0.9, 0.9, 0.9)
                getattr(cube, f"Get{axis}AxesTitleProperty")().SetFontSize(font)
                getattr(cube, f"Get{axis}AxesLinesProperty")().SetColor(*color)
            # A planar scene (zero thickness) must not show a z ruler; VTK would
            # auto-scale numerical noise to "(x10^-6)".  Hide it after all the
            # title/label settings, because setting them re-enables the axis.
            dz = ruler_bounds[5] - ruler_bounds[4]
            ruler_diag = max(
                ruler_bounds[1] - ruler_bounds[0],
                ruler_bounds[3] - ruler_bounds[2],
                abs(dz),
                1e-30,
            )
            if abs(dz) <= max(ruler_diag * 1e-6, 1e-12):
                for suffix in (
                    "AxisTitleVisibility",
                    "AxisLabelVisibility",
                    "AxisTickVisibility",
                    "AxisMinorTickVisibility",
                ):
                    try:
                        getattr(cube, f"SetZ{suffix}")(False)
                    except Exception:  # noqa: BLE001 — older VTK
                        pass
                cube.SetZTitle("")
                cube.SetZAxisVisibility(False)
            self._renderer.AddActor(cube)
            self._design_actors.append(cube)
            self._design_ruler_actor = cube
            self._update_design_ruler_labels()
        except Exception:  # noqa: BLE001
            pass

    def _add_axis_arrow(
        self,
        vtk,
        origin,
        direction,
        length: float,
        rgb,
        label: str,
        diag: float,
        show_label: bool,
        font_size: int,
    ) -> None:
        """One coloured axis shaft + cone + optional billboard caption."""
        dx, dy, dz = direction
        tip = (
            origin[0] + dx * length,
            origin[1] + dy * length,
            origin[2] + dz * length,
        )
        cone_h = max(length * 0.16, 1e-12)
        shaft_end = (
            origin[0] + dx * (length - cone_h),
            origin[1] + dy * (length - cone_h),
            origin[2] + dz * (length - cone_h),
        )
        line = vtk.vtkLineSource()
        line.SetPoint1(*origin)
        line.SetPoint2(*shaft_end)
        tube = vtk.vtkTubeFilter()
        tube.SetInputConnection(line.GetOutputPort())
        tube.SetRadius(max(diag * 0.0018, 1e-12))
        tube.SetNumberOfSides(10)
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(tube.GetOutputPort())
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*rgb)
        self._renderer.AddActor(actor)
        self._design_actors.append(actor)

        cone = vtk.vtkConeSource()
        cone.SetDirection(dx, dy, dz)
        cone.SetHeight(cone_h)
        cone.SetRadius(max(length * 0.045, 1e-12))
        cone.SetCenter(
            origin[0] + dx * (length - 0.5 * cone_h),
            origin[1] + dy * (length - 0.5 * cone_h),
            origin[2] + dz * (length - 0.5 * cone_h),
        )
        cone.SetResolution(14)
        cm = vtk.vtkPolyDataMapper()
        cm.SetInputConnection(cone.GetOutputPort())
        ca = vtk.vtkActor()
        ca.SetMapper(cm)
        ca.GetProperty().SetColor(*rgb)
        self._renderer.AddActor(ca)
        self._design_actors.append(ca)

        if show_label:
            text = vtk.vtkBillboardTextActor3D()
            text.SetInput(label)
            text.SetPosition(
                tip[0] + dx * length * 0.06,
                tip[1] + dy * length * 0.06,
                tip[2] + dz * length * 0.06,
            )
            prop = text.GetTextProperty()
            prop.SetFontSize(max(8, min(28, int(font_size))))
            prop.SetBold(True)
            prop.SetColor(*rgb)
            text.SetPickable(False)
            self._renderer.AddActor(text)
            self._design_actors.append(text)

    def _add_axis_tape(
        self,
        vtk,
        origin,
        axis: int,
        direction,
        length: float,
        unit_m: float,
        unit: str,
        rgb,
        diag: float,
        show_labels: bool,
        font_size: int,
    ) -> None:
        """Tick marks + numbers along one coloured coordinate axis."""
        perp = [i for i in range(3) if i != axis]
        tick_len = max(diag * 0.006, length * 0.035)
        offset = max(diag * 0.02, tick_len * 2.5)
        # Offset labels outward at the lower corner; at the upper corner the
        # positive perpendicular direction is already outside the scene.
        sign = -1 if self._axis_anchor == "min" else 1
        # 3 readable ticks along the arrow; the tip keeps the X/Y/Z caption.
        for n in range(1, 4):
            t = n / 4.0
            center = [
                origin[i] + direction[i] * length * t for i in range(3)
            ]
            for p_axis in perp:
                p0 = list(center)
                p1 = list(center)
                p0[p_axis] -= 0.5 * tick_len
                p1[p_axis] += 0.5 * tick_len
                line = vtk.vtkLineSource()
                line.SetPoint1(*p0)
                line.SetPoint2(*p1)
                mapper = vtk.vtkPolyDataMapper()
                mapper.SetInputConnection(line.GetOutputPort())
                actor = vtk.vtkActor()
                actor.SetMapper(mapper)
                prop = actor.GetProperty()
                prop.SetColor(*rgb)
                prop.SetLineWidth(1.5)
                self._renderer.AddActor(actor)
                self._design_actors.append(actor)
            if not show_labels:
                continue
            # Offset the number away from the shaft so it does not sit on it.
            # X labels go down/up, Y labels left/right, Z labels to the side.
            if axis == 0:
                off_axis, label_sign = 2, sign
            elif axis == 1:
                off_axis, label_sign = 0, sign
            else:
                off_axis, label_sign = 0, 1
            label_pos = list(center)
            label_pos[off_axis] += label_sign * offset
            value = center[axis] / max(unit_m, 1e-30)
            suffix = f" {unit}" if n == 1 else ""
            text = vtk.vtkBillboardTextActor3D()
            text.SetInput(f"{value:.4g}{suffix}")
            text.SetPosition(*label_pos)
            prop = text.GetTextProperty()
            prop.SetFontSize(max(7, min(20, int(font_size) - 2)))
            prop.SetColor(*rgb)
            text.SetPickable(False)
            self._renderer.AddActor(text)
            self._design_actors.append(text)

    def _highlighted_boxes(self) -> list[tuple[list[float], list[float]]]:
        """Scene boxes of the objects selected in the Project / Geometry list."""
        wanted = {str(n).lower() for n in self._highlight_names if str(n)}
        refs = list(self._highlight_boxes)
        out: list[tuple[list[float], list[float]]] = []

        def _add(lo, hi) -> None:
            try:
                lo_f = [float(v) for v in lo]
                hi_f = [float(v) for v in hi]
            except (TypeError, ValueError):
                return
            if len(lo_f) != 3 or len(hi_f) != 3:
                return
            for old_lo, old_hi in out:
                if all(
                    _close(lo_f[i], old_lo[i]) and _close(hi_f[i], old_hi[i])
                    for i in range(3)
                ):
                    return
            out.append((lo_f, hi_f))

        def _matches_ref(lo, hi) -> bool:
            for b0, b1 in refs:
                if all(
                    _close(float(lo[i]), b0[i]) and _close(float(hi[i]), b1[i])
                    for i in range(3)
                ):
                    return True
            return False

        try:
            from app.meshing.bounds import cartesian_primitive_bbox
        except Exception:  # noqa: BLE001
            cartesian_primitive_bbox = None
        for pr in getattr(self, "_design_prims", []):
            name = str(pr.get("name") or "")
            bb = cartesian_primitive_bbox(pr) if cartesian_primitive_bbox else None
            if bb is None:
                st, sp = pr.get("start"), pr.get("stop")
                if st is not None and sp is not None:
                    bb = (st, sp)
            if bb is None:
                continue
            if name.lower() in wanted or _matches_ref(bb[0], bb[1]):
                _add(bb[0], bb[1])
        for port in getattr(self, "_design_ports", []):
            name = str(getattr(port, "name", "") or "")
            st = getattr(port, "start", None)
            sp = getattr(port, "stop", None)
            if st is None or sp is None:
                continue
            if name.lower() in wanted or _matches_ref(st, sp):
                _add(st, sp)
        return out

    def _remove_actors(self, actors) -> None:
        if self._renderer is None:
            return
        for actor in list(actors or []):
            try:
                self._renderer.RemoveActor(actor)
            except Exception:  # noqa: BLE001
                pass

    # --------------------------------------------------------------- display
    def _object_display(self, pr: dict) -> dict:
        name = str(pr.get("name") or "")
        store = getattr(self._design_project, "object_display", None) if self._design_project else None
        raw = store.get(name) if isinstance(store, dict) else None
        return normalize_display(raw)

    @staticmethod
    def _default_material_color(pr: dict) -> tuple[float, float, float]:
        mat = str(pr.get("material") or "").lower()
        if any(token in mat for token in ("copper", "pec", "metal", "gold", "silver")):
            return (0.86, 0.58, 0.22)
        if any(token in mat for token in ("substrate", "fr4", "dielectric", "solder")):
            return (0.24, 0.47, 0.82)
        return (0.56, 0.66, 0.84)

    # ------------------------------------------------------------- fallback box
    def _fallback_domain(self, project, primitives):
        try:
            from app.meshing.bounds import cartesian_primitive_bbox
        except Exception:  # noqa: BLE001
            return None
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        found = False
        for pr in primitives:
            bb = cartesian_primitive_bbox(pr)
            if bb is None:
                continue
            found = True
            for i in range(3):
                lo[i] = min(lo[i], float(bb[0][i]))
                hi[i] = max(hi[i], float(bb[1][i]))
        if not found or not all(math.isfinite(v) for v in lo + hi):
            return None
        air = [0.0] * 6
        try:
            from app.meshing.rules import air_margin_units, compute_targets

            if project is not None:
                targets = compute_targets(project.mesh)
                air = air_margin_units(
                    project.mesh, list(project.boundary.air_margin), targets
                )
                air = [float(v) for v in air]
        except Exception:  # noqa: BLE001 — a design view must not depend on meshing
            air = [0.0] * 6
        if len(air) != 6:
            air = [0.0] * 6
        return (
            [lo[i] - air[2 * i] for i in range(3)],
            [hi[i] + air[2 * i + 1] for i in range(3)],
        )

    # --------------------------------------------------------------- clipping
    def _all_scene_actors(self):
        actors = list(getattr(self, "_geom_actors", []) or [])
        actors += list(getattr(self, "_design_actors", []) or [])
        actors += [a for _cat, _name, a in getattr(self, "_overlay_actors", []) or []]
        return actors

    def _apply_clipping_to_actor(self, actor) -> None:
        if actor is None:
            return
        try:
            mapper = actor.GetMapper()
            plane = getattr(self, "_design_clip_plane", None)
            if mapper is None:
                return
            if plane is None:
                mapper.SetClippingPlanes(None)
                return
            vtk = self._vtk or _vtk()
            planes = vtk.vtkPlaneCollection()
            planes.AddItem(plane)
            mapper.SetClippingPlanes(planes)
        except Exception:  # noqa: BLE001
            pass

    def _apply_clipping_to_all(self) -> None:
        for actor in self._all_scene_actors():
            self._apply_clipping_to_actor(actor)

    # ----------------------------------------------------------------- status
    def _update_design_info(self) -> None:
        if not hasattr(self, "lbl_design_info"):
            return
        n_ports = len(self._design_ports)
        n_active = sum(1 for p in self._design_ports if bool(getattr(p, "active", True)))
        nf = self._design_nf2ff
        nf_text = "on" if nf is not None and bool(getattr(nf, "enabled", False)) else "off"
        dom = "—"
        if self._design_domain is not None:
            lo, hi = self._design_domain
            dom = (
                f"  {lo[0]:.3g}, {lo[1]:.3g}, {lo[2]:.3g}\n"
                f"  → {hi[0]:.3g}, {hi[1]:.3g}, {hi[2]:.3g}"
            )
        text = (
            f"Primitives: {len(self._design_prims)}\n"
            f"Ports: {n_active}/{n_ports} active\n"
            f"NF2FF: {nf_text}\n"
            f"Domain:\n{dom}"
        )
        if self._highlight_label:
            text += f"\nSelected: {self._highlight_label}"
        if self._measure_text:
            text += f"\n{self._measure_text}"
        self.lbl_design_info.setText(text)

    # ------------------------------------------------------------- pop-out
    def popout(self) -> None:
        if self._popout_win is not None:
            self.restore_from_popout()
            return
        win = DesignViewerWindow(self)
        self._popout_win = win
        win.show()
        win.raise_()
        win.activateWindow()
        self.btn_popout.setText("Dock view")


class DesignViewerWindow(QMainWindow):
    """Standalone window mirroring the design view (VTK must not be reparented)."""

    def __init__(self, source: Design3DViewer) -> None:
        super().__init__(None)
        self.source: Design3DViewer | None = source
        self.viewer = Design3DViewer(work_dir=source.work_dir)
        self.viewer.btn_popout.setVisible(False)
        self.viewer.open_appcsxcad_requested.connect(
            lambda: self.source.open_appcsxcad_requested.emit()
            if self.source is not None
            else None
        )
        self.setWindowTitle("3D Design - pop-out")
        self.resize(1120, 820)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCentralWidget(self.viewer)
        self._copy_from(source)

    def _copy_from(self, source: Design3DViewer) -> None:
        self.viewer.chk_geom.setChecked(source.chk_geom.isChecked())
        self.viewer.sld_geom.setValue(source.sld_geom.value())
        self.viewer.chk_domain.setChecked(source.chk_domain.isChecked())
        self.viewer.chk_ports.setChecked(source.chk_ports.isChecked())
        self.viewer.chk_nf2ff.setChecked(source.chk_nf2ff.isChecked())
        for key in (
            "show",
            "corner",
            "labels",
            "font",
            "scale",
            "ruler",
            "ruler_fly",
            "ruler_ticks",
            "axis_tape",
        ):
            self.viewer._axes[key] = source._axes.get(key, self.viewer._axes.get(key))
        self.viewer._axis_anchor = source._axis_anchor
        self.viewer._axis_tape_axes = set(source._axis_tape_axes)
        self.viewer.set_view_default(source.view_preset())
        self.viewer.set_design(
            project=source._design_project,
            primitives=source._design_prims,
            ports=source._design_ports,
            nf2ff=source._design_nf2ff,
            unit_m=source._geom_unit,
            domain=source._design_domain,
            mesh_lines=source._mesh_lines,
            fit=True,
        )
        self.viewer.set_mesh_color(source.mesh_color())
        self.viewer.set_high_contrast(source._high_contrast)
        self.viewer.chk_mesh_plane.setChecked(source.chk_mesh_plane.isChecked())
        idx = self.viewer.cmb_mesh_axis.findText(source.cmb_mesh_axis.currentText())
        if idx >= 0:
            self.viewer.cmb_mesh_axis.setCurrentIndex(idx)
        self.viewer.slider_mesh_plane.setValue(source.slider_mesh_plane.value())
        self.viewer._draw_mesh_plane()

    def closeEvent(self, event) -> None:  # noqa: N802
        src = self.source
        self.source = None
        if src is not None and src._popout_win is self:
            src._popout_win = None
            src.btn_popout.setText("Pop out")
        super().closeEvent(event)
