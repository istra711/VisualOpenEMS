"""Interactive mesh + geometry viewer for the openEMS workflow.

Shows:
  - 2D slices (XY / XZ / YZ) with mesh grid + primitive AABBs + ports + NF2FF
  - Per-axis cell-size plots
  - Domain overview
  - One-click export to AppCSXCAD for true 3D review
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle

from app.meshing.preview import (
    default_color_for,
    export_csx_xml,
    get_sim_area,
    iter_primitive_infos,
    mesh_cells_from_lines,
    mesh_lines_from_csx,
    primitives_outside_domain,
)

# Axis index helpers
_AXIS = {"x": 0, "y": 1, "z": 2}
#: What a click in "Port mode" places: lumped or MSL (see the right-click menu).
PORT_KINDS: tuple[tuple[str, str], ...] = (("lumped", "Lumped"), ("msl", "MSL"))
#: CSX property kinds that act as the port's electrodes (PEC / conducting sheet)
_METAL_KINDS = ("metal", "conducting")


def _is_metal(info) -> bool:
    """True for a PEC / conducting-sheet primitive (a port electrode)."""
    kind = str(getattr(info, "kind", "") or "").lower()
    return any(tok in kind for tok in _METAL_KINDS)


def _axis_letter(index: int) -> str:
    return ("x", "y", "z")[int(index)] if 0 <= int(index) < 3 else ""


def _close(a, b, rel: float = 1e-6) -> bool:
    """True when two coordinates are equal up to a relative tolerance.

    The CSX AABBs come back float32-rounded, the project boxes are doubles.
    """
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return abs(fa - fb) <= rel * max(1.0, abs(fa), abs(fb))


#: Plane slider travel (internal steps): base resolution = /SLIDER_BASE_STEP
#: (= 2000 steps, twice the old 0..1000) and Ctrl gives the full 0..20000
#: (10x finer than the base resolution).
SLIDER_RANGE = 20000
SLIDER_BASE_STEP = 10
# For a 2D slice: which two axes are in-plane, which is the slice normal
_SLICE_AXES = {
    "XY": ("x", "y", "z"),
    "XZ": ("x", "z", "y"),
    "YZ": ("y", "z", "x"),
}


def _slice_key(view: str) -> str | None:
    """Combo label ('XY slice') -> _SLICE_AXES key ('XY'), else None.

    Regression guard: the combo items carry a ' slice' suffix, so comparing
    the raw label against _SLICE_AXES never matched — slices silently drew
    the cell-sizes plot and the plane slider/spinbox did nothing.
    """
    key = str(view).split(" ", 1)[0]
    return key if key in _SLICE_AXES else None


def _circle_in_slice(
    start, stop, radius, ia: int, ib: int, inorm: int
) -> tuple[float, float, float] | None:
    """``(cx, cy, r)`` when an axis-aligned cylinder is cut by the slice.

    Returns None for non-cylinders and for tilted cylinders (their AABB is not
    a circle in the slice) — the caller then draws the bounding box.
    """
    try:
        r = float(radius or 0.0)
    except Exception:  # noqa: BLE001
        return None
    if r <= 0:
        return None
    ext = [abs(float(stop[k]) - float(start[k])) for k in range(3)]
    tol = 1e-6 * max(1.0, r)
    # the two in-plane extents must equal the diameter, the normal one must be
    # at least that (the normal axis is the cylinder axis)
    if abs(ext[ia] - 2 * r) > tol or abs(ext[ib] - 2 * r) > tol:
        return None
    if ext[inorm] < ext[ia] - tol:
        return None
    cx = 0.5 * (float(start[ia]) + float(stop[ia]))
    cy = 0.5 * (float(start[ib]) + float(stop[ib]))
    return cx, cy, r


class ResponsiveCanvas(FigureCanvas):
    """FigureCanvas that tracks the Qt widget size (no tiny fixed figsize)."""

    def __init__(self, figure: Figure, parent: QWidget | None = None) -> None:
        super().__init__(figure)
        if parent is not None:
            self.setParent(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(360, 240)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        try:
            dpi = float(self.figure.dpi) or 100.0
            w = self.width() / dpi
            h = self.height() / dpi
            if w > 0.5 and h > 0.5:
                self.figure.set_size_inches(w, h, forward=False)
        except Exception:  # noqa: BLE001
            pass


def bind_wheel_zoom(canvas: FigureCanvas) -> None:
    """Mouse wheel zoom on all axes + double-click home.

    NavigationToolbar does not zoom with the wheel — this fills that gap.
    """
    state = {"active": True}

    def _on_scroll(event) -> None:
        if not state["active"] or event.inaxes is None:
            return
        if event.button == "up":
            factor = 1 / 1.15
        elif event.button == "down":
            factor = 1.15
        else:
            return
        ax = event.inaxes
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        x0, x1 = xlim
        y0, y1 = ylim
        # data coords under cursor
        try:
            xdata = ax.transData.inverted().transform((event.x, event.y))[0]
            ydata = ax.transData.inverted().transform((event.x, event.y))[1]
        except Exception:  # noqa: BLE001
            xdata = 0.5 * (x0 + x1)
            ydata = 0.5 * (y0 + y1)
        # relative position of cursor in current view
        rx = (xdata - x0) / (x1 - x0) if x1 != x0 else 0.5
        ry = (ydata - y0) / (y1 - y0) if y1 != y0 else 0.5
        new_w = (x1 - x0) * factor
        new_h = (y1 - y0) * factor
        # keep absolute zoom sane
        if new_w <= 0 or new_h <= 0:
            return
        new_x0 = xdata - rx * new_w
        new_x1 = new_x0 + new_w
        new_y0 = ydata - ry * new_h
        new_y1 = new_y0 + new_h
        ax.set_xlim(new_x0, new_x1)
        ax.set_ylim(new_y0, new_y1)
        canvas.draw_idle()

    def _on_dblclick(event) -> None:
        if not getattr(event, "dblclick", False):
            return
        for ax in canvas.figure.axes:
            ax.relim()
            ax.autoscale()
            ax.autoscale_view()
        canvas.draw_idle()

    def _toggle(active: bool) -> None:
        state["active"] = bool(active)

    canvas.mpl_connect("scroll_event", _on_scroll)
    canvas.mpl_connect("button_press_event", _on_dblclick)
    canvas._wheel_zoom_toggle = _toggle  # type: ignore[attr-defined]
    canvas.setToolTip(
        "Scroll wheel: zoom at cursor\n"
        "Double-click: reset view\n"
        "Toolbar: pan / zoom / home"
    )


class MeshGeometryViewer(QWidget):
    """Matplotlib-based mesh / geometry inspector + Mesh Editor."""

    #: emitted when the user changes the manual mesh-line overrides (the caller
    #: may refresh the scene info / mark the mesh stale for a rebuild).
    mesh_edited = Signal()
    #: emitted by "Rebuild from scratch" — the caller clears the overrides and
    #: runs a full (easyMesh) build, discarding the manual lines.
    rebuild_requested = Signal()
    #: Port mode: a left click in a slice placed a port there. The payload is a
    #: dict with the computed box (``start``/``stop``/``type``/``direction``,
    #: ``prop_direction``/``exc_direction`` for MSL, ``metal``/``partner`` and an
    #: optional ``warning``). The caller opens the port dialog and then calls
    #: :meth:`clear_port_marker`.
    port_place_requested = Signal(object)
    #: Port mode was switched on/off (right-click menu) — for a log hint.
    port_mode_changed = Signal(bool)
    #: Port type (lumped / MSL) + MSL width axis chosen in the right-click menu.
    port_type_changed = Signal(str, str)

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = Path(work_dir) if work_dir else None
        self._csx = None
        self._project = None  # app.models.project.Project | None
        self._primitives: list[dict] = []
        self._ports: list = []
        self._nf2ff = None
        self._sel_line: tuple[str, float] | None = None  # highlighted editor row
        self._selected_lines: list[tuple[str, float]] = []  # (axis, coord) highlighted
        # auto grid (per axis) before manual overrides — lets editor edits be
        # re-applied to the EXISTING mesh without re-running easyMesh
        self._auto_mesh: dict | None = None
        # scene caches from the last built CSX so geometry / ports / NF2FF / the
        # domain stay visible after the mesh grid is deleted
        self._scene_infos: list = []
        self._scene_area = None
        self._editor_panel = None
        self._split = None
        # port mode (right-click menu): click in a slice -> red dot + dialog
        self._port_mode = False
        #: what a click places: "lumped" or "msl" (+ the MSL width axis letter)
        self._port_type = "lumped"
        self._msl_orientation = ""
        #: object selected in the Project / Geometry list (boxes + names)
        self._highlight: dict = {"boxes": [], "names": [], "label": ""}
        #: modifier state at the last slider mouse event (Ctrl = fine/free)
        self._slider_mods = Qt.KeyboardModifier.NoModifier
        #: (slice key "XY"/"XZ"/"YZ", x, y) of the unconfirmed placement
        self._port_marker: tuple[str, float, float] | None = None
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.cmb_view = QComboBox()
        self.cmb_view.addItems(["XY slice", "XZ slice", "YZ slice", "Cell sizes", "Overview"])
        self.cmb_view.setCurrentIndex(0)
        self.spn_plane = QDoubleSpinBox()
        self.spn_plane.setDecimals(4)
        self.spn_plane.setRange(-1e6, 1e6)
        self.spn_plane.setSuffix(" mm")
        self.spn_plane.setValue(0.0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        # handlers map the 0..SLIDER_RANGE axis onto the domain (see _on_slider /
        # _update_plane_range): 2000 steps by default (2x the old 0..1000) and
        # 10x finer while Ctrl is held.
        self.slider.setRange(0, SLIDER_RANGE)
        self.slider.setValue(0)
        self.slider.setMinimumWidth(320)  # ~2x the old default travel
        self.slider.setToolTip(
            "Slice plane along the view normal.\n"
            f"Normal: snaps to the nearest mesh line, {SLIDER_RANGE // SLIDER_BASE_STEP} steps.\n"
            "Ctrl while dragging: 10x finer AND free (no mesh snapping) — "
            "lets you put the plane between mesh lines.\n"
            "Enabled once a mesh is built (Build Mesh)."
        )
        # mode hint next to the plane spinbox (the position itself is in the box)
        self.lbl_plane = QLabel("")
        self.lbl_plane.setToolTip(
            "Shows '· fine' while Ctrl is held:\n"
            "10x finer steps and no snapping to mesh lines.\n"
            "The plane position itself is in the spinbox next to this hint."
        )
        self.slider.installEventFilter(self)
        self.chk_mesh = QCheckBox("Mesh grid")
        self.chk_mesh.setChecked(True)
        self.chk_mesh.setToolTip(
            "Draw the mesh grid in the slice plots.\n"
            "Opacity / line width: the slider next to this box."
        )
        self.slider_mesh = QSlider(Qt.Orientation.Horizontal)
        # opacity + line width of the mesh grid lines in the slice plot
        self.slider_mesh.setRange(0, 100)
        self.slider_mesh.setValue(35)
        self.slider_mesh.setFixedWidth(90)
        self.slider_mesh.setToolTip(
            "Mesh grid lines in the slice plot — opacity and line width.\n"
            "Left = faint and thin (less clutter next to geometry/ports),\n"
            "right = opaque and thick (easier to read the cells).\n"
            "Only drawn while 'Mesh grid' is ticked."
        )
        # live readout next to the grid slider (value in %)
        self.lbl_mesh = QLabel("")
        self.lbl_mesh.setMinimumWidth(40)
        self.lbl_mesh.setToolTip(self.slider_mesh.toolTip())
        self.chk_geom = QCheckBox("Geometry")
        self.chk_geom.setChecked(True)
        self.chk_geom.setToolTip("Draw the geometry (primitives, imported CAD) in the slice plots.")
        self.chk_ports = QCheckBox("Ports")
        self.chk_ports.setChecked(True)
        self.chk_ports.setToolTip("Draw the ports in the slice plots.")
        self.chk_nf2ff = QCheckBox("NF2FF")
        self.chk_nf2ff.setChecked(True)
        self.chk_nf2ff.setToolTip("Draw the NF2FF box in the slice plots.")
        self.chk_editor = QCheckBox("Mesh editor")
        self.chk_editor.setToolTip(
            "Show the manual mesh-line editor panel (add / edit / delete grid\n"
            "lines per axis). Hidden by default."
        )
        self.btn_appcsxcad = QPushButton("Open in AppCSXCAD (3D)…")
        self.btn_export_xml = QPushButton("Export CSX XML…")
        self.btn_popout = QPushButton("Pop-out…")
        self.btn_popout.setToolTip("Show the mesh viewer in a separate window.")

        # Two rows: as one line this bar was ~2040 px wide and thereby set the
        # minimum width of the whole main window - the Project/Geometry splitter
        # could not be dragged any more.
        bar = QHBoxLayout()
        for w in (
            self.cmb_view,
            QLabel("Plane"),
            self.spn_plane,
            self.slider,
            self.lbl_plane,
        ):
            bar.addWidget(w)
        bar.addStretch(1)
        root.addLayout(bar)

        bar2 = QHBoxLayout()
        for w in (
            self.chk_mesh,
            QLabel("Grid"),
            self.slider_mesh,
            self.lbl_mesh,
            self.chk_geom,
            self.chk_ports,
            self.chk_nf2ff,
            self.chk_editor,
        ):
            bar2.addWidget(w)
        bar2.addStretch(1)
        bar2.addWidget(self.btn_popout)
        root.addLayout(bar2)

        split = QSplitter(Qt.Orientation.Horizontal)
        self._split = split
        root.addWidget(split, 1)

        # Left: plot area with tabs (slice + cell sizes always available)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.plot_tabs = QTabWidget()
        # Permanent host widgets — canvases/toolbars are recreated on each set_csx
        # to avoid stale matplotlib/Qt state after openEMS runs in-process.
        self.slice_host = QWidget()
        self.slice_layout = QVBoxLayout(self.slice_host)
        self.slice_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_tabs.addTab(self.slice_host, "Slice / Overview")

        self.cells_host = QWidget()
        self.cells_layout = QVBoxLayout(self.cells_host)
        self.cells_layout.setContentsMargins(0, 0, 0, 0)
        self.plot_tabs.addTab(self.cells_host, "Cell sizes")
        lv.addWidget(self.plot_tabs)
        left.setMinimumSize(480, 360)
        split.addWidget(left)
        # Editor panel: hidden until the "Mesh editor" box is ticked.
        self._editor_panel = self._build_editor_panel()
        self._editor_panel.setVisible(False)
        split.addWidget(self._editor_panel)

        self.fig_slice = None
        self.canvas_slice = None
        self.toolbar_slice = None
        self.fig_cells = None
        self.canvas_cells = None
        self.toolbar_cells = None
        self._recreate_canvases()

        split.setSizes([1000, 330])
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)

        # connections
        self.cmb_view.currentIndexChanged.connect(self._on_view_changed)
        self.spn_plane.valueChanged.connect(self._on_plane_changed)
        self.slider.valueChanged.connect(self._on_slider)
        self.chk_mesh.toggled.connect(self._on_mesh_toggled)
        self.slider_mesh.valueChanged.connect(self._on_mesh_style_changed)
        self.chk_geom.toggled.connect(self._redraw)
        self.chk_ports.toggled.connect(self._redraw)
        self.chk_nf2ff.toggled.connect(self._redraw)
        self.btn_appcsxcad.clicked.connect(self._on_open_appcsxcad)
        self.btn_export_xml.clicked.connect(self._on_export_xml)
        self.btn_popout.clicked.connect(self._on_popout)

        # initial readouts (slider values are set before the signals hook up)
        self._update_plane_info()
        self._update_mesh_info()

        # Mesh editor
        self.chk_editor.toggled.connect(self._on_editor_toggled)
        self.lst_edit_lines.itemSelectionChanged.connect(self._on_editor_selection)
        self.cmb_edit_axis.currentIndexChanged.connect(self._refresh_editor)
        self.btn_edit_add.clicked.connect(self._editor_add)
        self.btn_edit_remove.clicked.connect(self._editor_remove_selected)
        self.btn_edit_restore.clicked.connect(self._editor_restore_selected)
        self.btn_edit_reset_axis.clicked.connect(lambda: self._editor_reset(None))
        self.btn_edit_reset_all.clicked.connect(self._editor_reset_all)
        self.btn_edit_rebuild.clicked.connect(self.rebuild_requested.emit)

        self._on_view_changed(0)
        self._refresh_editor()

    # ------------------------------------------------------- mesh editor
    def _build_editor_panel(self) -> QWidget:
        box = QGroupBox("Mesh editor — manual lines")
        v = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("Axis"))
        self.cmb_edit_axis = QComboBox()
        self.cmb_edit_axis.addItems(["x", "y", "z"])
        row.addWidget(self.cmb_edit_axis)
        row.addStretch(1)
        v.addLayout(row)

        row2 = QHBoxLayout()
        self.spn_edit_line = QDoubleSpinBox()
        self.spn_edit_line.setDecimals(4)
        self.spn_edit_line.setRange(-1e6, 1e6)
        self.spn_edit_line.setSuffix(" mm")
        self.btn_edit_add = QPushButton("Add line")
        row2.addWidget(self.spn_edit_line)
        row2.addWidget(self.btn_edit_add)
        v.addLayout(row2)

        self.lst_edit_lines = QListWidget()
        self.lst_edit_lines.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.lst_edit_lines.setToolTip(
            "Current lines on the chosen axis.  '+' = added by you, '-' = removed\n"
            "by you (auto line), blank = from easyMesh. Select rows (Shift/Ctrl for\n"
            "several) to remove / restore; Del removes the selection."
        )
        v.addWidget(self.lst_edit_lines, 1)
        # Del / Backspace removes the selected line(s) — via an event filter so
        # it works regardless of which child has the focus.
        self.lst_edit_lines.installEventFilter(self)

        row3 = QHBoxLayout()
        self.btn_edit_remove = QPushButton("Remove selected")
        self.btn_edit_restore = QPushButton("Restore selected")
        row3.addWidget(self.btn_edit_remove)
        row3.addWidget(self.btn_edit_restore)
        v.addLayout(row3)

        self.btn_edit_reset_axis = QPushButton("Reset axis")
        self.btn_edit_reset_all = QPushButton("Reset all")
        row5 = QHBoxLayout()
        row5.addWidget(self.btn_edit_reset_axis)
        row5.addWidget(self.btn_edit_reset_all)
        v.addLayout(row5)

        self.btn_edit_rebuild = QPushButton("Rebuild from scratch…")
        self.btn_edit_rebuild.setToolTip(
            "Discard ALL manual lines and rebuild the mesh with easyMesh from\n"
            "scratch (asks for confirmation)."
        )
        v.addWidget(self.btn_edit_rebuild)

        lbl = QLabel(
            "Edit lines here (add / select / remove) — or click in the slice:\n"
            "a click in free space adds a line, a click on a line selects it\n"
            "(Shift+click always adds). Edits are saved in the project and\n"
            "applied after automatic meshing; Build Mesh applies them to the grid."
        )
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#666;")
        v.addWidget(lbl)
        return box

    def _on_editor_toggled(self, checked: bool) -> None:
        """Show / hide the editor panel; give it width when shown."""
        if self._editor_panel is not None:
            self._editor_panel.setVisible(bool(checked))
        if checked and self._split is not None:
            self._split.setSizes([1000, 330])
        if checked:
            self._refresh_editor()

    def _on_editor_selection(self, *_args) -> None:
        """Remember the highlighted line(s) so the slice can emphasise them."""
        ax = self.cmb_edit_axis.currentText()
        self._selected_lines = [
            (ax, float(it.data(Qt.ItemDataRole.UserRole)))
            for it in self.lst_edit_lines.selectedItems()
        ]
        self._sel_line = self._selected_lines[0] if self._selected_lines else None
        try:
            self._redraw()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _lines_dict(mesh, attr: str, create: bool = True) -> dict:
        d = getattr(mesh, attr, None)
        if isinstance(d, dict):
            return d
        d = {}
        if create:
            setattr(mesh, attr, d)
        return d

    def _mesh_spec(self):
        proj = getattr(self, "_project", None)
        return getattr(proj, "mesh", None)

    @staticmethod
    def _has(lst, c: float) -> bool:
        return any(abs(float(v) - float(c)) < 1e-6 for v in (lst or []))

    def set_auto_grid(self, auto: dict | None) -> None:
        """Cache the auto grid (per axis, before manual overrides) from a build.

        With it, an editor edit is applied to the EXISTING mesh by re-deriving
        the grid from ``auto`` + the overrides — no easyMesh re-run.
        """
        self._auto_mesh = dict(auto) if auto else None

    def _apply_editor_change(self) -> None:
        """Apply the override set to the CURRENT mesh (no full rebuild)."""
        mesh = self._mesh_spec()
        if mesh is not None and self._csx is not None and self._auto_mesh:
            try:
                from app.meshing.manual_mesh import apply_lines_axis

                g = self._csx.GetGrid()
                adds = self._lines_dict(mesh, "manual_mesh_lines", create=False)
                rems = self._lines_dict(mesh, "manual_mesh_lines_removed", create=False)
                for d, name in enumerate(("x", "y", "z")):
                    base = self._auto_mesh.get(name)
                    if not base:
                        continue
                    new_lines, _ = apply_lines_axis(
                        base,
                        [float(v) for v in adds.get(name, []) or []],
                        [float(v) for v in rems.get(name, []) or []],
                    )
                    g.SetLines(d, [float(v) for v in new_lines])
            except Exception as e:  # noqa: BLE001
                print(f"mesh editor incremental apply failed: {e}")
        self._refresh_editor()
        try:
            self._redraw()
        except Exception:  # noqa: BLE001
            pass
        self.mesh_edited.emit()

    def _refresh_editor(self, *_args) -> None:
        if not hasattr(self, "lst_edit_lines"):
            return
        self.lst_edit_lines.blockSignals(True)
        self.lst_edit_lines.clear()
        self._sel_line = None
        self._selected_lines = []
        mesh = self._mesh_spec()
        ax = self.cmb_edit_axis.currentText()
        # constrain the coordinate spin to the domain (no lines outside it)
        _rng = self._domain_range(ax)
        if _rng is not None:
            self.spn_edit_line.setRange(_rng[0], _rng[1])
        else:
            self.spn_edit_line.setRange(-1e6, 1e6)
        built: list[float] = []
        if self._csx is not None:
            try:
                built = sorted(
                    float(v) for v in np.asarray(mesh_lines_from_csx(self._csx)[ax], float)
                )
            except Exception:  # noqa: BLE001
                built = []
        adds = self._lines_dict(mesh, "manual_mesh_lines", create=False).get(ax, []) if mesh else []
        rems = (
            self._lines_dict(mesh, "manual_mesh_lines_removed", create=False).get(ax, [])
            if mesh
            else []
        )
        # Show the *effective* grid: current built lines (from easyMesh) plus
        # pending additions; a removal disappears once the rebuild dropped it.
        vals = sorted(set(built) | {float(v) for v in adds})
        for v in vals:
            if self._has(adds, v):
                prefix, color = "+", "#2e7d32"
            elif self._has(built, v) and self._has(rems, v):
                prefix, color = "−", "#c62828"  # pending removal (still on grid)
            else:
                prefix, color = " ", "#333333"
            item = QListWidgetItem(f"{prefix} {v:.4f}")
            item.setData(Qt.ItemDataRole.UserRole, float(v))
            item.setForeground(QBrush(QColor(color)))
            self.lst_edit_lines.addItem(item)
        self.lst_edit_lines.blockSignals(False)

    def _domain_range(self, axis_name: str) -> tuple[float, float] | None:
        """FDTD domain extent [lo, hi] on one axis, or None if unknown."""
        if self._csx is None:
            return None
        try:
            area = get_sim_area(self._csx)
        except Exception:  # noqa: BLE001
            return None
        if area is None:
            return None
        idx = _AXIS[axis_name]
        lo, hi = float(area[0][idx]), float(area[1][idx])
        return (lo, hi) if hi > lo else None

    def _clamp_to_domain(self, axis_name: str, c: float) -> float:
        """Keep a new line inside the FDTD domain (no lines outside it)."""
        rng = self._domain_range(axis_name)
        if rng is None:
            return c
        cc = min(max(c, rng[0]), rng[1])
        if abs(cc - c) > 1e-9:
            print(
                f"mesh editor: {c:.4g} is outside the domain "
                f"[{rng[0]:.4g}, {rng[1]:.4g}] on {axis_name} — clamped to {cc:.4g}"
            )
        return cc

    def _editor_add(self, coord: float | None = None) -> None:
        mesh = self._mesh_spec()
        if mesh is None:
            return
        ax = self.cmb_edit_axis.currentText()
        c = float(self.spn_edit_line.value() if coord is None else coord)
        c = self._clamp_to_domain(ax, c)
        self.spn_edit_line.setValue(c)
        adds = self._lines_dict(mesh, "manual_mesh_lines")
        rems = self._lines_dict(mesh, "manual_mesh_lines_removed")
        rems[ax] = [v for v in rems.get(ax, []) if not self._has([c], v)]
        if not self._has(adds.get(ax, []), c):
            adds.setdefault(ax, []).append(c)
        self._refresh_editor()
        self._apply_editor_change()

    def _editor_remove_selected(self) -> None:
        mesh = self._mesh_spec()
        items = self.lst_edit_lines.selectedItems()
        if mesh is None or not items:
            return
        ax = self.cmb_edit_axis.currentText()
        adds = self._lines_dict(mesh, "manual_mesh_lines")
        rems = self._lines_dict(mesh, "manual_mesh_lines_removed")
        for item in items:
            c = float(item.data(Qt.ItemDataRole.UserRole))
            if self._has(adds.get(ax, []), c):
                adds[ax] = [v for v in adds.get(ax, []) if not self._has([c], v)]
            elif not self._has(rems.get(ax, []), c):
                rems.setdefault(ax, []).append(c)
        self._refresh_editor()
        self._apply_editor_change()

    def _editor_restore_selected(self) -> None:
        mesh = self._mesh_spec()
        items = self.lst_edit_lines.selectedItems()
        if mesh is None or not items:
            return
        ax = self.cmb_edit_axis.currentText()
        rems = self._lines_dict(mesh, "manual_mesh_lines_removed")
        for item in items:
            c = float(item.data(Qt.ItemDataRole.UserRole))
            rems[ax] = [v for v in rems.get(ax, []) if not self._has([c], v)]
        self._refresh_editor()
        self._apply_editor_change()

    def _editor_reset(self, axis: str | None) -> None:
        mesh = self._mesh_spec()
        if mesh is None:
            return
        if axis is None:
            axis = self.cmb_edit_axis.currentText()
        for attr in ("manual_mesh_lines", "manual_mesh_lines_removed"):
            d = self._lines_dict(mesh, attr, create=False)
            if axis in d:
                d.pop(axis, None)
        self._refresh_editor()
        self._apply_editor_change()

    def _editor_reset_all(self) -> None:
        mesh = self._mesh_spec()
        if mesh is None:
            return
        mesh.manual_mesh_lines = None
        mesh.manual_mesh_lines_removed = None
        self._refresh_editor()
        self._apply_editor_change()

    def _effective_lines(self, axis_name: str) -> list[float]:
        """Grid lines as shown in the editor list: built (easyMesh) + pending adds."""
        built: list[float] = []
        if self._csx is not None:
            try:
                built = [float(v) for v in np.asarray(mesh_lines_from_csx(self._csx)[axis_name], float)]
            except Exception:  # noqa: BLE001
                built = []
        mesh = self._mesh_spec()
        adds = self._lines_dict(mesh, "manual_mesh_lines", create=False).get(axis_name, []) if mesh else []
        return sorted(set(built) | {float(v) for v in adds})

    def _edge_coords(self, axis_name: str) -> list[float]:
        """Geometry-edge coordinates on one axis (primitive AABB faces + ports).

        Used to snap a newly clicked line exactly onto a geometry edge.
        """
        idx = _AXIS[axis_name]
        vals: list[float] = []
        if self._csx is not None:
            try:
                for info in iter_primitive_infos(
                    self._csx, coord_system=self._coord_system()
                ):
                    try:
                        vals.append(float(info.start[idx]))
                        vals.append(float(info.stop[idx]))
                    except Exception:  # noqa: BLE001
                        continue
            except Exception:  # noqa: BLE001
                pass
        for p in self._ports:
            st = getattr(p, "start", None) or (p.get("start") if isinstance(p, dict) else None)
            sp = getattr(p, "stop", None) or (p.get("stop") if isinstance(p, dict) else None)
            for v in (st, sp):
                if v:
                    try:
                        vals.append(float(v[idx]))
                    except Exception:  # noqa: BLE001
                        continue
        return sorted({round(v, 9) for v in vals})

    def _snap_to_edge(self, axis_name: str, val: float, band_frac: float = 0.01) -> float:
        """Snap ``val`` to the nearest geometry edge within a narrow band."""
        edges = self._edge_coords(axis_name)
        if not edges:
            return val
        span = self._axis_span(axis_name)
        band = band_frac * span if span > 0 else 0.0
        e = min(edges, key=lambda c: abs(c - val))
        return e if (band > 0.0 and abs(e - val) <= band) else val

    def _select_row_for(self, coord: float) -> None:
        for i in range(self.lst_edit_lines.count()):
            v = float(self.lst_edit_lines.item(i).data(Qt.ItemDataRole.UserRole))
            if abs(v - coord) < 1e-6:
                self.lst_edit_lines.setCurrentRow(i)
                return

    def _is_line_selected(self, axis: str, coord: float) -> bool:
        """True when this (axis, coordinate) is one of the highlighted lines."""
        return any(
            str(a) == str(axis) and abs(float(c) - float(coord)) < 1e-6
            for a, c in (self._selected_lines or [])
        )

    def clear_line_selection(self) -> None:
        """Drop the mesh-line selection (editor rows + slice highlight).

        The editor list owns the selection: clearing it triggers
        :meth:`_on_editor_selection`, which empties ``_selected_lines`` and
        redraws the slice without the highlight.
        """
        if hasattr(self, "lst_edit_lines"):
            self.lst_edit_lines.clearSelection()
        self._selected_lines = []
        self._sel_line = None

    def refresh_view(self) -> None:
        """Redraw the current view from the current mesh data, same zoom/pan.

        The mesh-line selection is undone (see :meth:`clear_line_selection`) and
        the editor list is rebuilt, so externally changed lines show up.
        """
        try:
            self._refresh_editor()  # also clears _selected_lines / _sel_line
        except Exception as e:  # noqa: BLE001
            print(f"mesh viewer refresh (editor) error: {e}")
        self._selected_lines = []
        self._sel_line = None
        try:
            self._redraw()  # keeps the zoom while the view type is unchanged
        except Exception as e:  # noqa: BLE001
            print(f"mesh viewer refresh error: {e}")

    def _axis_span(self, axis_name: str) -> float:
        area = get_sim_area(self._csx) if self._csx is not None else None
        idx = {"x": 0, "y": 1, "z": 2}[axis_name]
        if area is not None:
            lo, hi = self._axis_limits(area, idx)
            if hi > lo:
                return float(hi - lo)
        lines = self._effective_lines(axis_name)
        return float(max(lines) - min(lines)) if len(lines) >= 2 else 0.0

    def _handle_slice_click(self, event, shift: bool) -> None:
        """A left click on the slice: select a line in a narrow band, else add."""
        view = _slice_key(self.cmb_view.currentText())
        if view is None:
            return
        ax_a, ax_b, _normal = _SLICE_AXES[view]
        axis = self.cmb_edit_axis.currentText()
        if axis not in (ax_a, ax_b):
            axis = ax_a  # the editor axis is the slice normal -> use an in-plane one
            self.cmb_edit_axis.setCurrentText(axis)
        val = event.xdata if axis == ax_a else event.ydata
        if val is None:
            return
        val = float(val)
        # Snap to an existing line only within a NARROW band (0.6 % of the axis
        # span) -> selects it; anywhere else adds a new line. Shift bypasses it.
        if not shift:
            lines = self._effective_lines(axis)
            if lines:
                arr = np.asarray(lines, float)
                j = int(np.argmin(np.abs(arr - val)))
                band = 0.006 * self._axis_span(axis)
                if band > 0.0 and abs(float(arr[j]) - val) <= band:
                    coord = float(arr[j])
                    if self._is_line_selected(axis, coord):
                        self.clear_line_selection()  # clicking it again deselects
                    else:
                        self._select_row_for(coord)
                    return
        # Snap a newly added line onto a geometry edge when the click is near one.
        val = self._snap_to_edge(axis, val)
        self._editor_add(val)

    def _show_context_menu(self) -> None:
        """Right-click menu on the plot area (editor toggle + pop-out)."""
        menu = QMenu(self)
        act_editor = menu.addAction("Mesh editor")
        act_editor.setCheckable(True)
        act_editor.setChecked(bool(self.chk_editor.isChecked()))
        act_port = menu.addAction("Port mode (click in a slice to place)")
        act_port.setCheckable(True)
        act_port.setChecked(bool(self._port_mode))
        act_port.setToolTip(
            "Left-click anywhere in a slice view (XY/XZ/YZ) to place a port at\n"
            "exactly those coordinates (no snapping): a red dot marks the spot\n"
            "and the port dialog opens with the computed box prefilled.\n"
            "The port runs into the screen (slice normal) from the bottom face of\n"
            "the clicked metal to the top face of the next metal surface — that\n"
            "length is found automatically."
        )
        # What the click places. MSL needs the width direction, so it is part of
        # the entry ("width along X/Y/Z" = the in-plane axis the port is wide in).
        self._add_port_type_menu(menu)
        act_pop = menu.addAction("Pop-out viewer…")
        menu.addSeparator()
        n_sel = len(getattr(self, "_selected_lines", []) or [])
        del_label = "Delete selected line(s)"
        if n_sel:
            del_label += f" ({n_sel})"
        act_del = menu.addAction(del_label)
        act_del.setEnabled(n_sel > 0)
        act_del.setToolTip("Remove the selected line(s) from the mesh (like Del)")
        menu.addSeparator()
        act_refresh = menu.addAction("Refresh view (keep zoom)")
        act_refresh.setToolTip(
            "Redraw the current view from the current mesh data.\n"
            "View, zoom and pan stay as they are; the mesh-line selection is\n"
            "dropped (like clicking the selected line again)."
        )
        act_home = menu.addAction("Reset / Home view")
        chosen = menu.exec(QCursor.pos())
        if chosen is act_editor:
            self.chk_editor.setChecked(not self.chk_editor.isChecked())
        elif chosen is act_port:
            self.set_port_mode(not self._port_mode)
        elif chosen is act_pop:
            self._on_popout()
        elif chosen is act_del:
            self._editor_remove_selected()
        elif chosen is act_refresh:
            self.refresh_view()
        elif chosen is act_home:
            try:
                self.toolbar_slice.home()
            except Exception:  # noqa: BLE001
                pass

    def _on_slice_click(self, event) -> None:
        """Slice click: right = menu; left = add a line / select one on a line."""
        try:
            if event.button == 3:
                self._show_context_menu()
                return
            if event.button != 1 or event.inaxes is None:
                return
            if getattr(self.toolbar_slice, "mode", ""):
                return  # pan/zoom active — leave the click to the toolbar
            # A double-click resets the view; count it as one click, not two adds.
            now = time.monotonic()
            if now - getattr(self, "_last_click_t", 0.0) < 0.25:
                self._last_click_t = now
                return
            self._last_click_t = now
            if self._port_mode:
                self._place_port_at(event)  # port mode: no line snapping
                return
            self._handle_slice_click(event, shift=(event.key == "shift"))
        except Exception as e:  # noqa: BLE001 — never crash on a stray click
            print(f"mesh editor click error: {e}")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        """Del / Backspace on the editor list removes the selected line(s)."""
        if obj is getattr(self, "lst_edit_lines", None) and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._editor_remove_selected()
                return True
        if obj is self.slider and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonRelease,
        ):
            # remember Ctrl for the valueChanged() that follows this event
            mods = event.modifiers()
            if mods != self._slider_mods:
                self._slider_mods = mods
                self._update_plane_info()
        return super().eventFilter(obj, event)

    def _on_popout(self) -> None:
        """Open a large detached window with the current scene."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Mesh Viewer — pop-out")
        dlg.resize(1200, 800)
        lay = QVBoxLayout(dlg)
        host = MeshGeometryViewer(work_dir=self.work_dir, parent=dlg)
        host.set_project_context(
            project=self._project,
            primitives=self._primitives,
            ports=self._ports,
            nf2ff=self._nf2ff,
        )
        lay.addWidget(host)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        # copy live CSX into the pop-out viewer
        if self._csx is not None:
            host.set_csx(self._csx)
        dlg.exec()

    def _recreate_canvases(self) -> None:
        """Drop old Figure/Canvas/Toolbar widgets and build fresh ones.

        NavigationToolbar keeps artist references; openEMS in the same process
        can invalidate them. Recreating avoids hard crashes on the 2nd mesh build.
        """
        # --- slice canvas ---
        if self.canvas_slice is not None:
            try:
                self.slice_layout.removeWidget(self.toolbar_slice)
                self.slice_layout.removeWidget(self.canvas_slice)
                self.toolbar_slice.deleteLater()
                self.canvas_slice.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self.fig_slice = Figure(figsize=(10, 7), tight_layout=True)
        self.canvas_slice = ResponsiveCanvas(self.fig_slice, self.slice_host)
        self.toolbar_slice = NavigationToolbar(self.canvas_slice, self)
        self.slice_layout.addWidget(self.toolbar_slice)
        self.slice_layout.addWidget(self.canvas_slice, 1)
        bind_wheel_zoom(self.canvas_slice)
        try:
            self.canvas_slice.mpl_connect("button_press_event", self._on_slice_click)
        except Exception:  # noqa: BLE001
            pass
        # fresh canvases -> no zoom to preserve for the next redraw
        self._drawn_view = None

        # --- cells canvas ---
        if self.canvas_cells is not None:
            try:
                self.cells_layout.removeWidget(self.toolbar_cells)
                self.cells_layout.removeWidget(self.canvas_cells)
                self.toolbar_cells.deleteLater()
                self.canvas_cells.deleteLater()
            except Exception:  # noqa: BLE001
                pass
        self.fig_cells = Figure(figsize=(10, 5), tight_layout=True)
        self.canvas_cells = ResponsiveCanvas(self.fig_cells, self.cells_host)
        self.toolbar_cells = NavigationToolbar(self.canvas_cells, self)
        self.cells_layout.addWidget(self.toolbar_cells)
        self.cells_layout.addWidget(self.canvas_cells, 1)
        bind_wheel_zoom(self.canvas_cells)

    # ----------------------------------------------------------- data input
    def set_csx(self, csx, keep_view: bool = False) -> None:
        """Store CSX, recreate canvases, and refresh all plots.

        ``keep_view`` preserves the current view type, slice plane and zoom
        across the canvas rebuild (used by the automatic background mesh
        rebuild so an edit does not reset what the user is looking at).
        """
        state = self._capture_view_state() if keep_view else None
        self._csx = csx
        try:
            self._recreate_canvases()
            if state is not None:
                self._restore_view_state(state)
            else:
                self._refresh()
            self._update_controls_enabled()
            self._refresh_editor()
        except Exception as e:  # noqa: BLE001 — never crash the GUI on redraw
            print(f"mesh viewer set_csx refresh error: {e}")

    def _capture_view_state(self) -> dict:
        """Snapshot the visible view (type, plane, zoom) across a rebuild."""
        return {
            "view": self.cmb_view.currentText(),
            "plane": float(self.spn_plane.value()),
            "slice": self._figure_limits(self.fig_slice),
            "cells": self._figure_limits(self.fig_cells),
        }

    def _restore_view_state(self, state: dict) -> None:
        """Re-apply a :meth:`_capture_view_state` snapshot after set_csx()."""
        self.cmb_view.blockSignals(True)
        self.cmb_view.setCurrentText(state.get("view", "XY slice"))
        self.cmb_view.blockSignals(False)
        self.spn_plane.blockSignals(True)
        self.spn_plane.setValue(float(state.get("plane", 0.0)))
        self.spn_plane.blockSignals(False)
        self._update_plane_range(reset=False)
        self._update_info()
        view = state.get("view", "XY slice")
        key = _slice_key(view)
        try:
            if key is not None:
                self.plot_tabs.setCurrentIndex(0)
                self._draw_slice(key)
            elif view == "Overview":
                self.plot_tabs.setCurrentIndex(0)
                self._draw_overview()
            else:
                self.plot_tabs.setCurrentIndex(1)
                self._draw_cells()
            self._drawn_view = view
        except Exception as e:  # noqa: BLE001
            print(f"mesh viewer restore view error: {e}")
        self._apply_figure_limits(self.fig_slice, state.get("slice"))
        self._apply_figure_limits(self.fig_cells, state.get("cells"))
        for c in (self.canvas_slice, self.canvas_cells):
            if c is not None:
                c.draw_idle()

    def set_project_context(
        self,
        project=None,
        primitives: list[dict] | None = None,
        ports: list | None = None,
        nf2ff=None,
    ) -> None:
        """Optional: Project model + primitive dicts for richer overlay."""
        self._project = project
        self._primitives = list(primitives or [])
        self._ports = list(ports or [])
        self._nf2ff = nf2ff
        # Do NOT redraw here — caller should follow with set_csx() for a new CSX.
        # Redrawing with a stale CSX while the caller is replacing it can crash.

    def set_nf2ff(self, nf2ff=None) -> None:
        """Update the NF2FF overlay in place (after Fit/Edit NF2FF) and redraw.

        Only the box changed — the CSX/mesh stay valid, so a plain redraw is safe.
        """
        self._nf2ff = nf2ff
        try:
            self._redraw()
        except Exception as e:  # noqa: BLE001 — never crash the GUI on redraw
            print(f"mesh viewer nf2ff update error: {e}")

    def set_ports(self, ports: list | None = None) -> None:
        """Update the port overlay in place (after Add/Edit port) and redraw.

        Like :meth:`set_nf2ff` only the overlay changed, so a plain redraw is safe
        (no CSX swap, which would risk the retired-object crash).
        """
        self._ports = list(ports or [])
        try:
            self._redraw()
        except Exception as e:  # noqa: BLE001 — never crash the GUI on redraw
            print(f"mesh viewer ports update error: {e}")

    # ------------------------------------------------- object highlight (tree)
    def set_highlight(self, boxes=None, names=None, label: str = "") -> None:
        """Highlight the object selected in the Project / Geometry list.

        ``boxes`` are ``(start, stop)`` pairs in drawing units, ``names`` are
        object / material / port names — a drawn element is highlighted when its
        box or its name matches. Passing nothing clears the highlight.
        """
        clean_boxes: list = []
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
        self._highlight = {
            "boxes": clean_boxes,
            "names": [str(n) for n in (names or []) if str(n)],
            "label": str(label or ""),
        }
        try:
            self._redraw()  # keeps the zoom while the view type is unchanged
        except Exception as e:  # noqa: BLE001
            print(f"mesh viewer highlight redraw error: {e}")

    def highlight(self) -> dict:
        """Current highlight (``boxes``/``names``/``label``)."""
        return dict(self._highlight)

    def clear_highlight(self) -> None:
        """Drop the highlight of the Project / Geometry selection."""
        self.set_highlight()

    def _is_highlighted(self, start=None, stop=None, name: str = "") -> bool:
        """True when this element is the one selected in the geometry list."""
        hl = getattr(self, "_highlight", None) or {}
        boxes = hl.get("boxes") or []
        names = hl.get("names") or []
        if not boxes and not names:
            return False
        if name:
            mine = str(name).lower()
            base = mine.split("#")[0]  # CSX property names look like "copper#1"
            wanted = {str(n).lower() for n in names}
            if mine in wanted or base in wanted:
                return True
        if start is None or stop is None:
            return False
        for b0, b1 in boxes:
            try:
                if all(
                    _close(start[k], b0[k]) and _close(stop[k], b1[k])
                    for k in range(3)
                ):
                    return True
            except (TypeError, ValueError, IndexError):
                continue
        return False

    def _draw_highlight(
        self, ax, ia: int, ib: int, inorm: int, plane, infos, ports, nf2ff
    ) -> bool:
        """Thick yellow outline around the highlighted object (slice/overview).

        ``plane`` = the slice coordinate along the normal axis, or None to draw
        every footprint (overview projections).
        """
        hl = getattr(self, "_highlight", None) or {}
        if not (hl.get("boxes") or hl.get("names")):
            return False
        eps = 1e-9
        style = dict(
            fill=False, edgecolor="#ffd400", linewidth=3.0, zorder=8,
            label="selected",
        )
        drawn = False
        for info in infos or []:
            try:
                st, sp = info.start, info.stop
            except AttributeError:
                continue
            if not self._is_highlighted(st, sp, getattr(info, "name", "")):
                continue
            if plane is not None and (
                plane < st[inorm] - eps or plane > sp[inorm] + eps
            ):
                continue
            ax.add_patch(
                Rectangle(
                    (st[ia], st[ib]),
                    sp[ia] - st[ia],
                    sp[ib] - st[ib],
                    **style,
                )
            )
            drawn = True
        for p in ports or []:
            st = getattr(p, "start", None) or (
                p.get("start") if isinstance(p, dict) else None
            )
            sp = getattr(p, "stop", None) or (
                p.get("stop") if isinstance(p, dict) else None
            )
            if not st or not sp:
                continue
            if not self._is_highlighted(st, sp, getattr(p, "name", "")):
                continue
            if plane is not None:
                tol = self._cell_span(
                    self._grid_mesh(), _axis_letter(inorm), (st[inorm] + sp[inorm]) / 2.0
                )
                if plane < min(st[inorm], sp[inorm]) - tol or plane > max(
                    st[inorm], sp[inorm]
                ) + tol:
                    continue
            x0, x1 = min(st[ia], sp[ia]), max(st[ia], sp[ia])
            y0, y1 = min(st[ib], sp[ib]), max(st[ib], sp[ib])
            if abs(x1 - x0) < eps and abs(y1 - y0) < eps:
                ax.plot(
                    [(x0 + x1) / 2], [(y0 + y1) / 2], marker="o", markersize=12,
                    markerfacecolor="none", markeredgecolor="#ffd400",
                    markeredgewidth=3.0, zorder=8, label="selected",
                )
            else:
                ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, **style))
            drawn = True
        if nf2ff is not None:
            st = getattr(nf2ff, "start", None)
            sp = getattr(nf2ff, "stop", None)
            if st and sp and self._is_highlighted(st, sp, "nf2ff"):
                if plane is None or not (
                    plane < min(st[inorm], sp[inorm]) or plane > max(st[inorm], sp[inorm])
                ):
                    ax.add_patch(
                        Rectangle(
                            (min(st[ia], sp[ia]), min(st[ib], sp[ib])),
                            abs(sp[ia] - st[ia]),
                            abs(sp[ib] - st[ib]),
                            **style,
                        )
                    )
                    drawn = True
        return drawn

    # --------------------------------------------------------------- port mode
    def set_port_mode(self, on: bool) -> None:
        """Arm / disarm "click in a slice to place a port" (right-click menu)."""
        self._port_mode = bool(on)
        if not self._port_mode:
            self._port_marker = None
        cursor = (
            Qt.CursorShape.CrossCursor
            if self._port_mode
            else Qt.CursorShape.ArrowCursor
        )
        for canvas in (self.canvas_slice, self.canvas_cells):
            if canvas is not None:
                canvas.setCursor(cursor)
        self.port_mode_changed.emit(self._port_mode)
        try:
            self._redraw()
        except Exception as e:  # noqa: BLE001 — never crash on a toggle
            print(f"mesh viewer port mode redraw error: {e}")

    def clear_port_marker(self) -> None:
        """Drop the red 'placed port' dot (the dialog was handled)."""
        if self._port_marker is None:
            return
        self._port_marker = None
        try:
            self._redraw()
        except Exception as e:  # noqa: BLE001
            print(f"mesh viewer port marker redraw error: {e}")

    # --------------------------------------------------- port type (lumped/MSL)
    def set_port_type(self, kind: str, orientation: str = "") -> None:
        """What a click places: ``"lumped"`` or ``"msl"`` (with the width axis).

        For MSL ``orientation`` is the in-plane axis the port is as wide as the
        metal in; the line then runs along the other in-plane axis.
        """
        self._port_type = "msl" if str(kind or "").lower() == "msl" else "lumped"
        if orientation:
            self._msl_orientation = str(orientation).lower()
        self.port_type_changed.emit(self._port_type, self._msl_orientation)

    def port_type(self) -> tuple[str, str]:
        """``(kind, msl_orientation)`` as chosen in the right-click menu."""
        return self._port_type, self._msl_orientation

    def _add_port_type_menu(self, menu) -> None:
        """Add the "Port type" submenu: Lumped / MSL with the width axis."""
        sub = menu.addMenu("Port type")
        sub.setToolTip(
            "Lumped: a line from the clicked metal to the next metal surface.\n"
            "MSL: the same box, but as wide as the clicked metal along the chosen\n"
            "in-plane axis (the line then runs along the other in-plane axis)."
        )
        ax_a, ax_b, _normal = self._in_plane_axes()
        act = sub.addAction("Lumped")
        act.setCheckable(True)
        act.setChecked(self._port_type != "msl")
        act.triggered.connect(lambda _checked=False: self._on_port_type_action("lumped", ""))
        for axis in (ax_a, ax_b):
            other = ax_b if axis == ax_a else ax_a
            act = sub.addAction(f"MSL, width along {axis.upper()}")
            act.setCheckable(True)
            act.setChecked(
                self._port_type == "msl"
                and str(self._msl_orientation or "").lower() == axis
            )
            act.setToolTip(
                f"Microstrip port: as wide as the clicked metal along {axis.upper()},\n"
                f"propagating along the other in-plane axis ({other.upper()})."
            )
            act.triggered.connect(
                lambda _checked=False, a=axis: self._on_port_type_action("msl", a)
            )

    def _on_port_type_action(self, kind: str, axis: str) -> None:
        """Menu slot: switch the port type (MSL also arms port mode)."""
        self.set_port_type(kind, axis)
        if kind == "msl" and not self._port_mode:
            self.set_port_mode(True)  # MSL only makes sense with port mode

    def _in_plane_axes(self) -> tuple[str, str, str]:
        """(axis_a, axis_b, normal) of the shown slice (XY as fallback)."""
        key = _slice_key(self.cmb_view.currentText())
        return _SLICE_AXES[key] if key else _SLICE_AXES["XY"]

    def _metal_layers(self, key: str, a: float, b: float) -> list:
        """Metal layers covering the clicked in-plane point, bottom first.

        Each entry is ``(lo, hi, info)``: the layer's extent along the slice
        normal plus its :class:`~app.meshing.preview.PrimitiveInfo`.
        """
        ax_a, ax_b, normal = _SLICE_AXES[key]
        ia, ib, inorm = _AXIS[ax_a], _AXIS[ax_b], _AXIS[normal]
        eps = 1e-9
        layers: list = []
        for info in self._grid_infos():
            if not _is_metal(info):
                continue
            try:
                st = [float(v) for v in info.start]
                sp = [float(v) for v in info.stop]
            except (TypeError, ValueError, IndexError):
                continue
            if not (min(st[ia], sp[ia]) - eps <= a <= max(st[ia], sp[ia]) + eps):
                continue
            if not (min(st[ib], sp[ib]) - eps <= b <= max(st[ib], sp[ib]) + eps):
                continue
            lo, hi = sorted((st[inorm], sp[inorm]))
            layers.append((lo, hi, info))
        layers.sort(key=lambda t: t[0])
        return layers

    def _primitive_name(self, info) -> str:
        """Project primitive name for a CSX primitive (else its CSX name).

        The CSX properties are named after the material ("copper#1"), so the
        project primitive with the same box is looked up for a readable log.
        """
        fallback = str(getattr(info, "name", "") or "")
        try:
            st = [float(v) for v in info.start]
            sp = [float(v) for v in info.stop]
        except (TypeError, ValueError, IndexError):
            return fallback
        for pr in self._primitives:
            pst, psp = pr.get("start"), pr.get("stop")
            if not pst or not psp:
                continue
            try:
                if all(
                    abs(float(pst[k]) - st[k]) < 1e-5
                    and abs(float(psp[k]) - sp[k]) < 1e-5
                    for k in range(3)
                ):
                    return str(pr.get("name") or fallback)
            except (TypeError, ValueError, IndexError):
                continue
        return fallback

    @staticmethod
    def _layer_at(layers: list, plane: float):
        """The layer containing ``plane``, else the nearest one (None if empty)."""
        if not layers:
            return None
        eps = 1e-9
        for lo, hi, info in layers:
            if lo - eps <= plane <= hi + eps:
                return (lo, hi, info)

        def _dist(layer) -> float:
            lo, hi, _info = layer
            return 0.0 if lo <= plane <= hi else min(abs(plane - lo), abs(plane - hi))

        return min(layers, key=_dist)

    def _cell_around(self, axis: str, value: float) -> tuple[float, float]:
        """Mesh cell around ``value`` (a thin band when the mesh is unknown)."""
        arr = np.asarray(self._grid_mesh().get(axis, []), dtype=float)
        if arr.size >= 2:
            j = int(np.searchsorted(arr, float(value)))
            j = min(max(j, 1), int(arr.size) - 1)
            return float(arr[j - 1]), float(arr[j])
        span = abs(float(self._axis_span(axis) or 0.0))
        half = max(1e-9, span * 5e-4)
        return float(value) - half, float(value) + half

    def port_box_at(
        self,
        key: str,
        a: float,
        b: float,
        plane: float,
        kind: str = "",
        orientation: str = "",
    ) -> dict | None:
        """Compute the port box for a click at ``(a, b)`` in the slice ``key``.

        The port runs along the slice normal (into the screen): from the bottom
        face of the clicked metal down to the top face of the next metal surface
        (if there is none below, the next one above is used instead — its bottom
        face). ``kind``/``orientation`` default to the menu selection; MSL ports
        additionally span the clicked metal's width along the chosen in-plane
        axis and one mesh cell along the other in-plane axis (the line).

        Returns a dict with ``start``/``stop`` (drawing units), ``type``,
        ``direction``, ``prop_direction``/``exc_direction``, ``metal``,
        ``partner`` and ``side`` — or ``None`` when no second metal is found.
        """
        ax_a, ax_b, normal = _SLICE_AXES[key]
        ia, ib, inorm = _AXIS[ax_a], _AXIS[ax_b], _AXIS[normal]
        layers = self._metal_layers(key, a, b)
        cur = self._layer_at(layers, plane)
        if cur is None:
            return None
        cur_lo, cur_hi, cur_info = cur
        eps = 1e-9
        below = [t for t in layers if t is not cur and t[1] <= cur_lo + eps]
        above = [t for t in layers if t is not cur and t[0] >= cur_hi - eps]
        side = ""
        partner = None
        if below:  # next metal surface below: bottom of the click -> its top
            partner = max(below, key=lambda t: t[1])
            start_n, stop_n, side = cur_lo, partner[1], "below"
        elif above:  # nothing below -> top of the click -> bottom of the next
            partner = min(above, key=lambda t: t[0])
            start_n, stop_n, side = cur_hi, partner[0], "above"
        if partner is None or abs(stop_n - start_n) <= eps:
            return None

        start = [0.0, 0.0, 0.0]
        stop = [0.0, 0.0, 0.0]
        start[ia], start[ib] = float(a), float(b)
        stop[ia], stop[ib] = float(a), float(b)
        start[inorm], stop[inorm] = float(start_n), float(stop_n)
        port_kind, msl_axis = self._port_type, self._msl_orientation
        if kind:
            port_kind = "msl" if str(kind).lower() == "msl" else "lumped"
        if orientation:
            msl_axis = str(orientation).lower()
        box = {
            "type": port_kind,
            "start": start,
            "stop": stop,
            "direction": normal,
            "prop_direction": "",
            "exc_direction": "",
            "metal": self._primitive_name(cur_info),
            "partner": self._primitive_name(partner[2]),
            "side": side,
            "warning": "",
        }
        if port_kind != "msl":
            return box
        # MSL: as wide as the clicked metal, one mesh cell along the line
        iw = _AXIS.get(str(msl_axis).lower())
        if iw is None or iw == inorm:
            iw = ia  # no/!in-plane orientation -> first in-plane axis
        ip = ib if iw == ia else ia
        w_lo, w_hi = sorted((float(cur_info.start[iw]), float(cur_info.stop[iw])))
        if w_hi - w_lo <= eps:  # degenerate metal -> use the click
            w_lo = w_hi = float(a) if iw == ia else float(b)
        p_lo, p_hi = self._cell_around(_axis_letter(ip), float(a) if ip == ia else float(b))
        vals = {ia: [float(a), float(a)], ib: [float(b), float(b)]}
        vals[iw] = [w_lo, w_hi]
        vals[ip] = [p_lo, p_hi]
        for k in (ia, ib):
            start[k], stop[k] = vals[k][0], vals[k][1]
        box.update(
            prop_direction=_axis_letter(ip),
            exc_direction=normal,
            width_axis=_axis_letter(iw),
            width=w_hi - w_lo,
        )
        return box

    def _place_port_at(self, event) -> None:
        """Port mode: compute the port box at the clicked point and mark it red.

        The two in-plane coordinates come straight from the click, the third one
        from the current slice plane (the plane slider snaps to mesh lines, the
        click itself does not). The box (and, for MSL, the width) is derived from
        the metal surfaces around the click — see :meth:`port_box_at`.
        """
        key = _slice_key(self.cmb_view.currentText())
        if key is None:
            return  # Overview / Cell sizes: no 2D coordinate system
        ax_a, ax_b, normal = _SLICE_AXES[key]
        ia, ib, inorm = _AXIS[ax_a], _AXIS[ax_b], _AXIS[normal]
        if event.xdata is None or event.ydata is None:
            return
        a, b = float(event.xdata), float(event.ydata)
        plane = float(self.spn_plane.value())
        self._port_marker = (key, a, b)
        try:
            self._redraw()
        except Exception as e:  # noqa: BLE001
            print(f"mesh viewer port marker redraw error: {e}")
        box = self.port_box_at(key, a, b, plane)
        if box is None:
            # no second metal surface: keep the old behaviour (click = both ends)
            pt = [0.0, 0.0, 0.0]
            pt[ia], pt[ib], pt[inorm] = a, b, plane
            prop = ib if self._msl_orientation == ax_a else ia
            box = {
                "type": self._port_type,
                "start": list(pt),
                "stop": list(pt),
                "direction": normal,
                "prop_direction": _axis_letter(prop) if self._port_type == "msl" else "",
                "exc_direction": normal if self._port_type == "msl" else "",
                "metal": "",
                "partner": "",
                "side": "",
                "warning": (
                    "no second metal surface found at the click — start/stop are "
                    "the clicked point; give the port a length by hand"
                ),
            }
        self.port_place_requested.emit(box)

    def clear(self) -> None:
        """Drop scene data only — do NOT touch matplotlib figures here.

        Calling clf()/draw_idle() on the live QtAgg canvas and then
        set_csx() (which recreates canvases) can hard-crash after openEMS
        has run in-process. set_csx() owns figure lifecycle.
        """
        self._csx = None
        self._project = None
        self._primitives = []
        self._ports = []
        self._nf2ff = None
        self._info_text = ""
        self._auto_mesh = None
        self._selected_lines = []
        self._sel_line = None
        self._update_controls_enabled()

    def clear_mesh(self) -> None:
        """Remove the mesh GRID but keep geometry / ports / NF2FF / domain.

        Geometry and the domain are cached from the last build (_collect_scene),
        so after the mesh is deleted the viewer still draws the structure, the
        ports, the NF2FF box and the domain outline — only the mesh grid goes.
        """
        self._csx = None
        self._auto_mesh = None
        self._selected_lines = []
        self._sel_line = None
        self._update_controls_enabled()
        self._update_info()
        try:
            self._refresh_editor()
        except Exception:  # noqa: BLE001
            pass
        self._redraw()

    @staticmethod
    def _safe_clear_fig(fig, canvas) -> None:
        try:
            fig.clf()
        except Exception:  # noqa: BLE001
            pass
        try:
            canvas.draw_idle()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- actions
    def _on_view_changed(self, _idx: int) -> None:
        if _slice_key(self.cmb_view.currentText()) and self._csx is not None:
            # recalibrate plane spinbox + slider for the NEW slice normal and
            # pick a plane that shows geometry (the old value belongs to a
            # different axis and may cut nothing)
            self._update_plane_range(reset=True)
        self._update_controls_enabled()
        self._update_plane_info()
        self._redraw()

    def _update_controls_enabled(self) -> None:
        """Plane spinbox + slider: only meaningful with a CSX and a slice view."""
        has_scene = self._csx is not None or self._scene_area is not None
        active = _slice_key(self.cmb_view.currentText()) is not None and has_scene
        self.spn_plane.setEnabled(active)
        self.slider.setEnabled(active)
        self.slider_mesh.setEnabled(active and self.chk_mesh.isChecked())
        self._update_plane_info()
        self._update_mesh_info()

    def _mesh_alpha(self) -> float:
        """Mesh grid line alpha (0..1) from the visibility slider."""
        return float(self.slider_mesh.value()) / 100.0

    def _on_mesh_toggled(self, _checked: bool = False) -> None:
        self._update_controls_enabled()
        self._redraw()

    def _on_mesh_style_changed(self, _value: int = 0) -> None:
        self._update_mesh_info()
        self._redraw()

    def _update_mesh_info(self) -> None:
        """Readout next to the grid slider (alpha / line-width strength in %)."""
        txt = f"{int(self.slider_mesh.value())}%"
        if not self.chk_mesh.isChecked():
            txt += " (grid off)"
        self.lbl_mesh.setText(txt)

    @staticmethod
    def _ctrl_down_static() -> bool:
        try:
            from PySide6.QtWidgets import QApplication

            return bool(
                QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier
            )
        except Exception:  # noqa: BLE001 — headless: no Ctrl
            return False

    def _slider_fine(self) -> bool:
        """Ctrl held -> 10x finer steps and no snapping to mesh lines."""
        if self._slider_mods & Qt.KeyboardModifier.ControlModifier:
            return True
        return self._ctrl_down_static()

    def _slider_step(self) -> int:
        """Internal steps per slider position (10 by default, 1 with Ctrl)."""
        return 1 if self._slider_fine() else SLIDER_BASE_STEP

    def _set_slider_from_frac(self, frac: float) -> None:
        """Move the plane slider to ``frac`` (0..1 of the domain), no signals."""
        step = self._slider_step()
        pos = int(round(np.clip(frac, 0.0, 1.0) * SLIDER_RANGE / step)) * step
        self.slider.blockSignals(True)
        self.slider.setValue(int(np.clip(pos, 0, SLIDER_RANGE)))
        self.slider.blockSignals(False)

    def _update_plane_info(self) -> None:
        """Ctrl-mode hint next to the plane spinbox (the value is in the box)."""
        key = _slice_key(self.cmb_view.currentText())
        fine = key is not None and self._slider_fine()
        self.lbl_plane.setText("· fine" if fine else "")

    def _on_plane_changed(self, value: float) -> None:
        # keep the slider in sync (0..SLIDER_RANGE maps to the domain range)
        key = _slice_key(self.cmb_view.currentText())
        if key is None:
            return
        normal = _SLICE_AXES[key][2]
        area = self._grid_area()
        if area is not None:
            lo, hi = area[0][_AXIS[normal]], area[1][_AXIS[normal]]
            if hi > lo:
                self._set_slider_from_frac((value - lo) / (hi - lo))
        self._update_plane_info()
        self._redraw()

    def _on_slider(self, pos: int) -> None:
        key = _slice_key(self.cmb_view.currentText())
        if key is None:
            return
        normal = _SLICE_AXES[key][2]
        area = self._grid_area()
        if area is None:
            return
        # Base resolution: quantise to every SLIDER_BASE_STEP-th position (Ctrl
        # gives the raw 1-step resolution -> 10x finer).
        step = self._slider_step()
        if step > 1 and pos % step:
            pos = int(round(pos / step)) * step
            self.slider.blockSignals(True)
            self.slider.setValue(int(np.clip(pos, 0, SLIDER_RANGE)))
            self.slider.blockSignals(False)
        lo, hi = float(area[0][_AXIS[normal]]), float(area[1][_AXIS[normal]])
        value = lo + (hi - lo) * (pos / SLIDER_RANGE)
        # Normal mode snaps onto the nearest mesh line so the section visibly
        # jumps; Ctrl (fine) keeps the exact position so the finer steps (and
        # planes between mesh lines) are actually reachable.
        if not self._slider_fine():
            value = self._nearest_mesh_line(normal, value)
        self.spn_plane.blockSignals(True)
        self.spn_plane.setValue(value)
        self.spn_plane.blockSignals(False)
        self._update_plane_info()
        self._redraw()

    def _on_export_xml(self) -> None:
        if self._csx is None:
            QMessageBox.information(self, "Export", "No CSX loaded — build mesh first.")
            return
        from PySide6.QtWidgets import QFileDialog

        default = str((self.work_dir or Path(".")) / "csx_export.xml")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export CSX XML", default, "XML files (*.xml)"
        )
        if not path:
            return
        try:
            out = export_csx_xml(self._csx, path)
            QMessageBox.information(self, "Export", f"CSX written to:\n{out}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Export failed", str(e))

    def _on_open_appcsxcad(self) -> None:
        """Export temporary XML and launch AppCSXCAD.exe for true 3D review."""
        if self._csx is None:
            QMessageBox.information(self, "AppCSXCAD", "No CSX loaded — build mesh first.")
            return
        appcsxcad = self._find_appcsxcad()
        if not appcsxcad:
            QMessageBox.warning(
                self,
                "AppCSXCAD not found",
                "Could not locate AppCSXCAD.exe.\n"
                "Set openEMS root in Settings, or export XML manually.",
            )
            return
        work = self.work_dir or Path(".")
        xml_path = work / "appcsxcad_view.xml"
        try:
            export_csx_xml(self._csx, xml_path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Export failed", str(e))
            return
        try:
            subprocess.Popen(
                [str(appcsxcad), str(xml_path)],
                cwd=str(appcsxcad.parent),
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Launch failed", str(e))

    def _find_appcsxcad(self) -> Path | None:
        candidates: list[Path] = []
        # From settings (openEMS root)
        try:
            from app.settings import AppSettings

            s = AppSettings.load()
            candidates.append(Path(s.openems_root) / "AppCSXCAD.exe")
        except Exception:  # noqa: BLE001
            pass
        # CSXCAD package constant
        try:
            from CSXCAD import AppCSXCAD_BIN

            if AppCSXCAD_BIN:
                candidates.append(Path(AppCSXCAD_BIN))
        except Exception:  # noqa: BLE001
            pass
        # Known install path
        candidates.append(Path(r"c:\openEMS\AppCSXCAD.exe"))
        for c in candidates:
            if c and c.exists():
                return c
        return None

    # ------------------------------------------------------------- drawing
    def _refresh(self) -> None:
        if self._csx is None:
            self.clear()
            return
        self._update_plane_range(reset=True)
        if _slice_key(self.cmb_view.currentText()) is None:
            # non-slice view active while meshing (Cell sizes / Overview):
            # _update_plane_range returns early, so put the plane on the origin
            # explicitly — a slice view must start at 0 mm after a fresh mesh
            self.spn_plane.setValue(0.0)
        self._update_info()
        self._redraw()

    def _update_plane_range(self, reset: bool = False) -> None:
        key = _slice_key(self.cmb_view.currentText())
        if key is None:
            return
        normal = _SLICE_AXES[key][2]
        area = self._grid_area()
        if area is None:
            return
        lo, hi = float(area[0][_AXIS[normal]]), float(area[1][_AXIS[normal]])
        self.spn_plane.setRange(lo, hi)
        # Map the slider onto the real domain so the plane actually lands on
        # mesh lines instead of the fixed 0..100 range.
        self.slider.blockSignals(True)
        self.slider.setRange(0, SLIDER_RANGE)
        if reset:
            # a freshly built / loaded mesh starts on the model origin
            # (clamped by the spinbox range when 0 lies outside the domain)
            self.spn_plane.setValue(0.0)
        self.spn_plane.setValue(self.spn_plane.value())
        self.slider.blockSignals(False)
        frac = (self.spn_plane.value() - lo) / (hi - lo) if hi > lo else 0.0
        self._set_slider_from_frac(frac)
        self._update_plane_info()

    def _nearest_mesh_line(self, normal: str, value: float) -> float:
        """Snap the plane value to the closest mesh line on the slice normal."""
        try:
            lines = np.asarray(self._grid_mesh()[normal], dtype=float)
            if lines.size == 0:
                return value
            return float(lines[np.argmin(np.abs(lines - value))])
        except Exception:  # noqa: BLE001
            return value

    def _coord_system(self) -> str:
        """'cylindrical' when the project context says so (else cartesian)."""
        proj = getattr(self, "_project", None)
        if proj is not None:
            return str(getattr(proj.mesh, "coord_system", "cartesian") or "cartesian")
        return "cartesian"

    def _update_info(self) -> None:
        lines: list[str] = []
        lines.append("=== Mesh ===")
        mesh = self._grid_mesh()
        from app.meshing.polar import axis_labels

        _labels = dict(zip("xyz", axis_labels(self._coord_system())))
        for d in "xyz":
            arr = mesh[d]
            if arr.size == 0:
                lines.append(f"  {_labels[d]}: (empty)")
                continue
            mids, sizes = mesh_cells_from_lines(arr)
            lines.append(
                f"  {_labels[d]}: {arr.size} lines, "
                f"cells {sizes.size}, "
                f"min {sizes.min():.4g}, max {sizes.max():.4g}, "
                f"range [{arr.min():.4g}, {arr.max():.4g}]"
            )
        if self._coord_system() == "cylindrical":
            lines.append("  (cylindrical: ρ/φ/z — φ stored in radians 0..2π)")
        area = self._grid_area()
        if area is not None:
            lines.append(
                f"  Domain: [{area[0][0]:.3g}, {area[0][1]:.3g}, {area[0][2]:.3g}] → "
                f"[{area[1][0]:.3g}, {area[1][1]:.3g}, {area[1][2]:.3g}]"
            )
        lines.append("")
        lines.append("=== Primitives (CSX) ===")
        infos = self._grid_infos()
        if not infos:
            lines.append("  (none)")
        for info in infos:
            lines.append(
                f"  {info.name} [{info.kind}] "
                f"({info.start[0]:.3g},{info.start[1]:.3g},{info.start[2]:.3g}) → "
                f"({info.stop[0]:.3g},{info.stop[1]:.3g},{info.stop[2]:.3g})"
            )
        if self._primitives:
            lines.append("")
            lines.append("=== Project primitives (dict) ===")
            for pr in self._primitives:
                st = pr.get("start", [0, 0, 0])
                sp = pr.get("stop", [0, 0, 0])
                lines.append(
                    f"  {pr.get('name', '?')} mat={pr.get('material', '?')} "
                    f"({st[0]:.3g},{st[1]:.3g},{st[2]:.3g}) → ({sp[0]:.3g},{sp[1]:.3g},{sp[2]:.3g})"
                )
        if self._ports:
            lines.append("")
            lines.append("=== Ports ===")
            for p in self._ports:
                st = getattr(p, "start", None) or p.get("start", [0, 0, 0])
                sp = getattr(p, "stop", None) or p.get("stop", [0, 0, 0])
                name = getattr(p, "name", None) or p.get("name", "port")
                lines.append(f"  {name}: ({st[0]:.3g},{st[1]:.3g},{st[2]:.3g}) → ({sp[0]:.3g},{sp[1]:.3g},{sp[2]:.3g})")
        if self._nf2ff is not None:
            lines.append("")
            lines.append("=== NF2FF ===")
            nf = self._nf2ff
            st = getattr(nf, "start", None)
            sp = getattr(nf, "stop", None)
            en = getattr(nf, "enabled", False)
            lines.append(f"  enabled={en}")
            if st and sp:
                lines.append(
                    f"  box: ({st[0]:.3g},{st[1]:.3g},{st[2]:.3g}) → ({sp[0]:.3g},{sp[1]:.3g},{sp[2]:.3g})"
                )
        self._info_text = "\n".join(lines)

    def info_text(self) -> str:
        return getattr(self, "_info_text", "")

    def _redraw(self) -> None:
        if self._csx is None and self._scene_area is None and not self._scene_infos:
            return  # nothing to show at all
        try:
            view = self.cmb_view.currentText()
            key = _slice_key(view)
            # Keep the current zoom when only a visibility toggle / style
            # changed (same view): the plot is rebuilt from scratch, so the
            # axes limits must be captured before and re-applied after.
            same_view = view == self._drawn_view
            keep_slice = self._figure_limits(self.fig_slice) if same_view else None
            keep_cells = self._figure_limits(self.fig_cells) if same_view else None
            if key is not None:
                self.plot_tabs.setCurrentIndex(0)
                self._draw_slice(key)
            elif view == "Overview":
                self.plot_tabs.setCurrentIndex(0)
                self._draw_overview()
            else:  # Cell sizes
                self.plot_tabs.setCurrentIndex(1)
                self._draw_cells()
            self._drawn_view = view
            if same_view:
                self._apply_figure_limits(self.fig_slice, keep_slice)
                self._apply_figure_limits(self.fig_cells, keep_cells)
                for canvas in (self.canvas_slice, self.canvas_cells):
                    if canvas is not None:
                        canvas.draw_idle()
        except Exception as e:  # noqa: BLE001 — never crash the GUI on redraw
            print(f"mesh viewer redraw error: {e}")

    @staticmethod
    def _figure_limits(fig) -> list[tuple[tuple[float, float], tuple[float, float]]] | None:
        """Axes limits of a figure (for zoom preservation across redraws)."""
        if fig is None or not fig.axes:
            return None
        out = []
        for ax in fig.axes:
            try:
                out.append((tuple(ax.get_xlim()), tuple(ax.get_ylim())))
            except Exception:  # noqa: BLE001
                return None
        return out

    @staticmethod
    def _apply_figure_limits(fig, keep) -> None:
        if not keep or fig is None or len(fig.axes) != len(keep):
            return
        for ax, (xlim, ylim) in zip(fig.axes, keep):
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)

    def _collect_scene(self):
        """Return (mesh_lines, prim_infos, ports, nf2ff, area).

        Without a built CSX (mesh deleted) the cached geometry / domain from the
        last build is used, so geometry, ports, NF2FF and the domain stay visible
        while the mesh grid is gone.
        """
        if self._csx is not None:
            mesh = mesh_lines_from_csx(self._csx)
            infos = iter_primitive_infos(self._csx, coord_system=self._coord_system())
            area = get_sim_area(self._csx)
            self._scene_infos = list(infos)
            self._scene_area = area
            return mesh, infos, self._ports, self._nf2ff, area
        empty = {"x": np.asarray([]), "y": np.asarray([]), "z": np.asarray([])}
        return empty, list(self._scene_infos), self._ports, self._nf2ff, self._scene_area

    # Cached-scene accessors: fall back to the last build when the mesh grid has
    # been deleted (_csx is None), so geometry / ports / NF2FF / domain survive.
    def _grid_mesh(self) -> dict:
        if self._csx is not None:
            try:
                return mesh_lines_from_csx(self._csx)
            except Exception:  # noqa: BLE001
                pass
        return {"x": np.asarray([]), "y": np.asarray([]), "z": np.asarray([])}

    def _grid_area(self):
        if self._csx is not None:
            try:
                return get_sim_area(self._csx)
            except Exception:  # noqa: BLE001
                return None
        return self._scene_area

    def _grid_infos(self) -> list:
        if self._csx is not None:
            try:
                return list(
                    iter_primitive_infos(self._csx, coord_system=self._coord_system())
                )
            except Exception:  # noqa: BLE001
                return list(self._scene_infos)
        return list(self._scene_infos)

    @staticmethod
    def _outside_names(infos, area) -> set[str]:
        """Names of primitives that stick out of the FDTD domain."""
        try:
            return {info.name for info, _j, _a, _s in primitives_outside_domain(infos, area)}
        except Exception:  # noqa: BLE001
            return set()

    @staticmethod
    def _cell_span(mesh: dict, axis_name: str, coord: float) -> float:
        """Largest cell that touches ``coord`` on ``axis_name`` (0 if unknown).

        Used so degenerate features (ports perpendicular to the slice) show up
        for the cells they belong to instead of requiring an exact plane hit.
        """
        try:
            lines = np.sort(np.asarray(mesh.get(axis_name, []), dtype=float))
        except Exception:  # noqa: BLE001
            return 0.0
        if lines.size < 2:
            return 0.0
        below = lines[lines <= coord]
        above = lines[lines >= coord]
        cells: list[float] = []
        if below.size >= 2:
            cells.append(float(below[-1] - below[-2]))
        if above.size >= 2:
            cells.append(float(above[1] - above[0]))
        if not cells:
            cells = [float(np.max(np.diff(lines)))]
        return max(cells)

    @staticmethod
    def _is_port_artifact(kind: str) -> bool:
        """True for CSX properties that belong to a port, not to the structure.

        openEMS stores every port as a set of CSX primitives (LumpedElement,
        Excitation, ProbeBox/ResBox) whose sliding-plane footprints used to be
        drawn by the *structure* block — so they ignored "Show ports" and
        vanished with "Show structure".
        """
        k = (kind or "").lower()
        return any(
            tok in k for tok in ("lumped", "excitation", "probe", "resbox", "dump")
        )

    def _draw_slice(self, view: str) -> None:
        ax_a, ax_b, normal = _SLICE_AXES[view]
        ia, ib, inorm = _AXIS[ax_a], _AXIS[ax_b], _AXIS[normal]
        eps = 1e-9  # shared tolerance: also used by the ports block below,
        #             so it must exist even when the geometry is hidden
        plane = float(self.spn_plane.value())
        mesh, infos, ports, nf2ff, area = self._collect_scene()
        outside = self._outside_names(infos, area)

        if self.fig_slice is None or self.canvas_slice is None:
            self._recreate_canvases()
        self.fig_slice.clf()
        ax = self.fig_slice.add_subplot(111)

        # Domain outline
        if area is not None:
            x0, y0 = area[0][ia], area[0][ib]
            x1, y1 = area[1][ia], area[1][ib]
            ax.add_patch(
                Rectangle(
                    (x0, y0), x1 - x0, y1 - y0,
                    fill=False, edgecolor="black", linewidth=1.2, linestyle="--",
                    label="Domain",
                )
            )

        # Mesh grid lines
        if self.chk_mesh.isChecked():
            alpha = self._mesh_alpha()
            lw = 0.3 + 1.4 * alpha
            for lines, idx in ((mesh[ax_a], ia), (mesh[ax_b], ib)):
                arr = np.sort(np.asarray(lines, dtype=float))
                if arr.size == 0:
                    continue
                if idx == ia:
                    ax.vlines(
                        arr, *self._axis_limits(area, ib),
                        colors=(0.4, 0.4, 0.4), linewidths=lw, alpha=alpha,
                    )
                else:
                    ax.hlines(
                        arr, *self._axis_limits(area, ia),
                        colors=(0.4, 0.4, 0.4), linewidths=lw, alpha=alpha,
                    )

        # Pending Mesh-Editor overrides on the in-plane axes — shown before a
        # rebuild so the user sees exactly what will be added / removed.
        mesh_spec = self._mesh_spec()
        if mesh_spec is not None:
            adds = self._lines_dict(mesh_spec, "manual_mesh_lines", create=False)
            rems = self._lines_dict(mesh_spec, "manual_mesh_lines_removed", create=False)
            for axn, idx in ((ax_a, ia), (ax_b, ib)):
                g = np.asarray(mesh.get(axn, []), dtype=float)

                def _on_grid(c, g=g):
                    return g.size > 0 and float(np.min(np.abs(g - c))) <= 1e-6

                for c in adds.get(axn, []) or []:
                    c = float(c)
                    if _on_grid(c):
                        continue  # already applied -> a normal grid line by now
                    if idx == ia:
                        ax.axvline(c, color="#2e7d32", linestyle="--", linewidth=1.2,
                                   alpha=0.9, label="manual line (pending)")
                    else:
                        ax.axhline(c, color="#2e7d32", linestyle="--", linewidth=1.2,
                                   alpha=0.9, label="manual line (pending)")
                for c in rems.get(axn, []) or []:
                    c = float(c)
                    if not _on_grid(c):
                        continue  # applied -> the line is already gone
                    if idx == ia:
                        ax.axvline(c, color="#c62828", linestyle=":", linewidth=1.2,
                                   alpha=0.9, label="removed line (pending)")
                    else:
                        ax.axhline(c, color="#c62828", linestyle=":", linewidth=1.2,
                                   alpha=0.9, label="removed line (pending)")

        # Emphasise the line selected in the editor list (only when it lies in
        # this slice plane).
        # Highlight ONLY the selected line(s) (no full-height/width band: a span
        # changes the data limits under aspect='equal' -> the view jumps).
        for saxis, scoord in getattr(self, "_selected_lines", []) or []:
            for axn, idx in ((ax_a, ia), (ax_b, ib)):
                if axn != saxis:
                    continue
                if idx == ia:
                    ax.axvline(scoord, color="#ff9800", linewidth=2.8, zorder=7,
                               label="selected line")
                else:
                    ax.axhline(scoord, color="#ff9800", linewidth=2.8, zorder=7,
                               label="selected line")

        # Geometry primitives: draw 2D footprint when the box intersects the slice plane
        if self.chk_geom.isChecked():
            eps = 1e-9
            for info in infos:
                if self._is_port_artifact(info.kind):
                    continue  # ports are drawn by the "Show ports" block below
                lo_n, hi_n = info.start[inorm], info.stop[inorm]
                if plane < lo_n - eps or plane > hi_n + eps:
                    continue
                x0, x1 = info.start[ia], info.stop[ia]
                y0, y1 = info.start[ib], info.stop[ib]
                color = default_color_for(info.kind, info.name)
                bad = info.name in outside
                circle = _circle_in_slice(
                    info.start, info.stop, getattr(info, "radius", 0.0), ia, ib, inorm
                )
                if circle is not None:  # cylinder: draw the true cross-section
                    cx, cy, r = circle
                    ax.add_patch(
                        Circle(
                            (cx, cy), r,
                            facecolor=color,
                            edgecolor=(1.0, 0.0, 0.0, 1.0) if bad else color[:3] + (0.9,),
                            linewidth=2.0 if bad else 1.0,
                            label=info.name,
                        )
                    )
                    continue
                ax.add_patch(
                    Rectangle(
                        (x0, y0), x1 - x0, y1 - y0,
                        facecolor=color,
                        edgecolor=(1.0, 0.0, 0.0, 1.0) if bad else color[:3] + (0.9,),
                        linewidth=2.0 if bad else 1.0,
                        label=info.name,
                    )
                )

        # Project primitives (dicts) as extra outlines
        if self.chk_geom.isChecked():
            eps = 1e-9
            for pr in self._primitives:
                st = pr.get("start", [0, 0, 0])
                sp = pr.get("stop", [0, 0, 0])
                if plane < st[inorm] - eps or plane > sp[inorm] + eps:
                    continue
                x0, x1 = st[ia], sp[ia]
                y0, y1 = st[ib], sp[ib]
                mat = pr.get("material", "")
                color = default_color_for("Metal" if "copper" in mat or "ground" in mat else "Material", pr.get("name", mat))
                circle = None
                if str(pr.get("kind")) == "cylinder":
                    # dict start/stop are the AXIS endpoints -> build the AABB
                    r = float(pr.get("radius") or 0.0)
                    bb0 = [min(st[k], sp[k]) - r for k in range(3)]
                    bb1 = [max(st[k], sp[k]) + r for k in range(3)]
                    circle = _circle_in_slice(bb0, bb1, r, ia, ib, inorm)
                    if circle is None:  # tilted cylinder: bounding box
                        x0, x1 = bb0[ia], bb1[ia]
                        y0, y1 = bb0[ib], bb1[ib]
                if circle is not None:
                    cx, cy, r = circle
                    ax.add_patch(
                        Circle(
                            (cx, cy), r,
                            fill=False, edgecolor=color[:3] + (1.0,),
                            linewidth=1.5, linestyle=":",
                        )
                    )
                    continue
                ax.add_patch(
                    Rectangle(
                        (x0, y0), x1 - x0, y1 - y0,
                        fill=False, edgecolor=color[:3] + (1.0,),
                        linewidth=1.5, linestyle=":",
                    )
                )

        # Ports
        if self.chk_ports.isChecked():
            for p in ports:
                st = getattr(p, "start", None) or (p.get("start") if isinstance(p, dict) else None)
                sp = getattr(p, "stop", None) or (p.get("stop") if isinstance(p, dict) else None)
                if not st or not sp:
                    continue
                lo_n, hi_n = min(st[inorm], sp[inorm]), max(st[inorm], sp[inorm])
                # A port that lies perpendicular to the slice has (almost) no
                # extent along the normal — show it for the cells touching it
                # instead of only when the plane hits its exact coordinate.
                tol = self._cell_span(mesh, normal, (lo_n + hi_n) / 2.0)
                if plane < lo_n - tol or plane > hi_n + tol:
                    continue
                x0, x1 = min(st[ia], sp[ia]), max(st[ia], sp[ia])
                y0, y1 = min(st[ib], sp[ib]), max(st[ib], sp[ib])
                active = bool(getattr(p, "active", True))
                # inactive ports (PortDef.active=False) are greyed out
                face = (1.0, 0.2, 0.2, 0.55) if active else (0.55, 0.55, 0.55, 0.35)
                edge = "darkred" if active else "grey"
                linestyle = "-" if active else ":"
                label = "Ports" if active else "Ports (off)"
                # degenerate (line/point) ports → draw marker at center
                if abs(x1 - x0) < eps and abs(y1 - y0) < eps:
                    ax.plot(
                        [(x0 + x1) / 2], [(y0 + y1) / 2],
                        marker="o", markersize=8, color="red" if active else "grey",
                        markeredgecolor="white", markeredgewidth=0.8, zorder=5,
                        label=label,
                    )
                else:
                    ax.add_patch(
                        Rectangle(
                            (x0, y0), x1 - x0, y1 - y0,
                            facecolor=face,
                            edgecolor=edge, linewidth=1.2, linestyle=linestyle,
                            zorder=5,
                            label=label,
                        )
                    )

        # Port mode: the unconfirmed click is marked with a red dot (+ coords)
        if self._port_marker is not None and self._port_marker[0] == view:
            _mk, mx, my = self._port_marker
            ax.plot(
                [mx], [my], marker="o", markersize=11, color="red",
                markeredgecolor="white", markeredgewidth=1.2, zorder=9,
                label="New port",
            )
            ax.annotate(
                f"({mx:.4g}, {my:.4g})", (mx, my), textcoords="offset points",
                xytext=(8, 8), color="red", fontsize=8, zorder=9,
            )

        # NF2FF box footprint
        if self.chk_nf2ff.isChecked() and nf2ff is not None:
            st = getattr(nf2ff, "start", None)
            sp = getattr(nf2ff, "stop", None)
            if st and sp:
                if not (plane < min(st[inorm], sp[inorm]) or plane > max(st[inorm], sp[inorm])):
                    x0, x1 = min(st[ia], sp[ia]), max(st[ia], sp[ia])
                    y0, y1 = min(st[ib], sp[ib]), max(st[ib], sp[ib])
                    ax.add_patch(
                        Rectangle(
                            (x0, y0), x1 - x0, y1 - y0,
                            fill=False, edgecolor="purple", linewidth=1.4,
                            linestyle="-.", label="NF2FF",
                        )
                    )

        # Object selected in the Project / Geometry list -> thick outline
        sel_drawn = self._draw_highlight(ax, ia, ib, inorm, plane, infos, ports, nf2ff)

        ax.set_xlabel(ax_a + " [mm]")
        ax.set_ylabel(ax_b + " [mm]")
        note = "  [OUTSIDE DOMAIN]" if outside else ""
        sel = ""
        hl = getattr(self, "_highlight", None) or {}
        if hl.get("boxes") or hl.get("names"):
            label = hl.get("label") or "selection"
            sel = (
                f"  [selected: {label}]"
                if sel_drawn
                else f"  [selected: {label} - not in this plane]"
            )
        ax.set_title(
            f"{view} slice @ {normal}={plane:.4g} mm{note}{sel}",
            color="red" if outside else "black",
        )
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(True, alpha=0.25)
        # unique legend entries
        handles, labels = ax.get_legend_handles_labels()
        seen: dict[str, object] = {}
        for h, l in zip(handles, labels):
            if l not in seen:
                seen[l] = h
        if seen:
            ax.legend(seen.values(), seen.keys(), loc="upper right", fontsize=8)
        self.fig_slice.tight_layout()
        self.canvas_slice.draw_idle()

    def _draw_overview(self) -> None:
        mesh, infos, ports, nf2ff, area = self._collect_scene()
        outside = self._outside_names(infos, area)
        if self.fig_slice is None or self.canvas_slice is None:
            self._recreate_canvases()
        self.fig_slice.clf()
        # 2x2: XY footprint, XZ, YZ, plus domain summary text
        axes_defs = [("XY", 0, 1, 2), ("XZ", 0, 2, 1), ("YZ", 1, 2, 0)]
        for k, (name, ia, ib, inorm) in enumerate(axes_defs):
            ax = self.fig_slice.add_subplot(2, 2, k + 1)
            if area is not None:
                ax.add_patch(
                    Rectangle(
                        (area[0][ia], area[0][ib]),
                        area[1][ia] - area[0][ia],
                        area[1][ib] - area[0][ib],
                        fill=False, edgecolor="black", linestyle="--", linewidth=1.0,
                    )
                )
            for info in infos:
                color = default_color_for(info.kind, info.name)
                bad = info.name in outside
                circle = _circle_in_slice(
                    info.start, info.stop, getattr(info, "radius", 0.0), ia, ib, inorm
                )
                if circle is not None:  # cylinder: true cross-section
                    cx, cy, r = circle
                    ax.add_patch(
                        Circle(
                            (cx, cy), r,
                            facecolor=color,
                            edgecolor=(1.0, 0.0, 0.0, 1.0) if bad else color[:3] + (0.8,),
                            linewidth=2.0 if bad else 0.8,
                        )
                    )
                    continue
                ax.add_patch(
                    Rectangle(
                        (info.start[ia], info.start[ib]),
                        info.stop[ia] - info.start[ia],
                        info.stop[ib] - info.start[ib],
                        facecolor=color,
                        edgecolor=(1.0, 0.0, 0.0, 1.0) if bad else color[:3] + (0.8,),
                        linewidth=2.0 if bad else 0.8,
                    )
                )
            if self.chk_ports.isChecked():
                for p in ports:
                    st = getattr(p, "start", None) or (p.get("start") if isinstance(p, dict) else None)
                    sp = getattr(p, "stop", None) or (p.get("stop") if isinstance(p, dict) else None)
                    if not st or not sp:
                        continue
                    cx = 0.5 * (st[ia] + sp[ia])
                    cy = 0.5 * (st[ib] + sp[ib])
                    active = bool(getattr(p, "active", True))
                    ax.plot(
                        [cx], [cy], marker="o", markersize=5,
                        color="red" if active else "grey", zorder=5,
                    )
            if self.chk_nf2ff.isChecked() and nf2ff is not None:
                st = getattr(nf2ff, "start", None)
                sp = getattr(nf2ff, "stop", None)
                if st and sp:
                    ax.add_patch(
                        Rectangle(
                            (min(st[ia], sp[ia]), min(st[ib], sp[ib])),
                            abs(sp[ia] - st[ia]), abs(sp[ib] - st[ib]),
                            fill=False, edgecolor="purple", linestyle="-.", linewidth=1.2,
                        )
                    )
            # object selected in the Project / Geometry list -> thick outline
            self._draw_highlight(ax, ia, ib, inorm, None, infos, ports, nf2ff)
            ax.set_xlabel("xyz"[ia] + " [mm]")
            ax.set_ylabel("xyz"[ib] + " [mm]")
            ax.set_title(f"{name} projection")
            ax.set_aspect("equal", adjustable="datalim")
            ax.grid(True, alpha=0.25)

        # 4th subplot: mesh stats text
        ax = self.fig_slice.add_subplot(2, 2, 4)
        ax.axis("off")
        txt = ["Mesh cells per axis:"]
        for d in "xyz":
            _, sizes = mesh_cells_from_lines(mesh[d])
            if sizes.size:
                txt.append(
                    f"  {d}: n={sizes.size}  min={sizes.min():.4g}  "
                    f"max={sizes.max():.4g}  mean={sizes.mean():.4g}"
                )
            else:
                txt.append(f"  {d}: empty")
        if area is not None:
            txt.append("")
            txt.append(
                f"Domain size:\n  "
                f"{area[1][0]-area[0][0]:.4g} × {area[1][1]-area[0][1]:.4g} × {area[1][2]-area[0][2]:.4g} mm"
            )
        txt.append("")
        txt.append(f"Primitives: {len(infos)}")
        txt.append(f"Ports: {len(ports)}")
        if outside:
            txt.append("")
            txt.append(f"OUTSIDE DOMAIN: {len(outside)}")
            for name in sorted(outside)[:4]:
                txt.append(f"  ! {name}")
            if len(outside) > 4:
                txt.append(f"  ... +{len(outside) - 4} more")
        ax.text(0.05, 0.95, "\n".join(txt), transform=ax.transAxes,
                va="top", ha="left", family="monospace", fontsize=9,
                color="red" if outside else "black")
        self.fig_slice.tight_layout()
        self.canvas_slice.draw_idle()

    def _draw_cells(self) -> None:
        mesh = self._grid_mesh()
        if self.fig_cells is None or self.canvas_cells is None:
            self._recreate_canvases()
        self.fig_cells.clf()
        axes = self.fig_cells.subplots(1, 3)
        for ax, d in zip(axes, "xyz"):
            arr = mesh[d]
            if arr.size < 2:
                ax.set_title(f"{d}: empty")
                continue
            mids, sizes = mesh_cells_from_lines(arr)
            ax.plot(mids, sizes, ".", ms=3, color="steelblue")
            ax.set_xlabel(f"{d} position [mm]")
            ax.set_ylabel("cell size [mm]")
            ax.set_title(f"{d}: {sizes.size} cells")
            ax.grid(True, alpha=0.3)
        self.fig_cells.tight_layout()
        self.canvas_cells.draw_idle()

    @staticmethod
    def _axis_limits(area, idx: int) -> tuple[float, float]:
        if area is None:
            return (-1.0, 1.0)
        lo, hi = float(area[0][idx]), float(area[1][idx])
        if hi <= lo:
            return (lo - 1.0, lo + 1.0)
        return (lo, hi)
