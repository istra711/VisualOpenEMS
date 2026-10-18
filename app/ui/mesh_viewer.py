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
        self._editor_panel = None
        self._split = None
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.cmb_view = QComboBox()
        self.cmb_view.addItems(["XY slice", "XZ slice", "YZ slice", "Cell sizes", "Overview"])
        self.cmb_view.setCurrentIndex(0)
        self.spn_plane = QDoubleSpinBox()
        self.spn_plane.setDecimals(4)
        self.spn_plane.setRange(-1e6, 1e6)
        self.spn_plane.setSuffix(" mm")
        self.spn_plane.setValue(0.0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        # handlers map 0..1000 onto the domain (see _on_slider /
        # _update_plane_range) — keep the same scale at creation and start at
        # the left end (0)
        self.slider.setRange(0, 1000)
        self.slider.setValue(0)
        self.slider.setToolTip(
            "Slice plane along the view normal — snaps to the nearest mesh line.\n"
            "Enabled once a mesh is built (Build Mesh)."
        )
        self.chk_mesh = QCheckBox("Show mesh grid")
        self.chk_mesh.setChecked(True)
        self.slider_mesh = QSlider(Qt.Orientation.Horizontal)
        # opacity + line width of the mesh grid lines in the slice plot
        self.slider_mesh.setRange(0, 100)
        self.slider_mesh.setValue(35)
        self.slider_mesh.setFixedWidth(90)
        self.slider_mesh.setToolTip(
            "Mesh grid lines: visibility (opacity and line width).\n"
            "Left = fainter (less clutter next to geometry/ports), "
            "right = stronger."
        )
        self.chk_geom = QCheckBox("Show geometry")
        self.chk_geom.setChecked(True)
        self.chk_ports = QCheckBox("Show ports")
        self.chk_ports.setChecked(True)
        self.chk_nf2ff = QCheckBox("Show NF2FF")
        self.chk_nf2ff.setChecked(True)
        self.chk_editor = QCheckBox("Mesh editor")
        self.chk_editor.setToolTip(
            "Show the manual mesh-line editor panel (add / edit / delete grid\n"
            "lines per axis). Hidden by default."
        )
        self.btn_appcsxcad = QPushButton("Open in AppCSXCAD (3D)…")
        self.btn_export_xml = QPushButton("Export CSX XML…")
        self.btn_popout = QPushButton("Pop-out viewer…")

        for w in (
            self.cmb_view,
            QLabel("Plane"),
            self.spn_plane,
            self.slider,
            self.chk_mesh,
            self.slider_mesh,
            self.chk_geom,
            self.chk_ports,
            self.chk_nf2ff,
            self.chk_editor,
        ):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.btn_popout)
        root.addLayout(bar)

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

    def _editor_add(self, coord: float | None = None) -> None:
        mesh = self._mesh_spec()
        if mesh is None:
            return
        ax = self.cmb_edit_axis.currentText()
        c = float(self.spn_edit_line.value() if coord is None else coord)
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
                    self._select_row_for(float(arr[j]))
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
        act_home = menu.addAction("Reset / Home view")
        chosen = menu.exec(QCursor.pos())
        if chosen is act_editor:
            self.chk_editor.setChecked(not self.chk_editor.isChecked())
        elif chosen is act_pop:
            self._on_popout()
        elif chosen is act_del:
            self._editor_remove_selected()
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
            self._handle_slice_click(event, shift=(event.key == "shift"))
        except Exception as e:  # noqa: BLE001 — never crash on a stray click
            print(f"mesh editor click error: {e}")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt override)
        """Del / Backspace on the editor list removes the selected line(s)."""
        if obj is self.lst_edit_lines and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
                self._editor_remove_selected()
                return True
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
        self._update_controls_enabled()

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
        self._redraw()

    def _update_controls_enabled(self) -> None:
        """Plane spinbox + slider: only meaningful with a CSX and a slice view."""
        active = _slice_key(self.cmb_view.currentText()) is not None and self._csx is not None
        self.spn_plane.setEnabled(active)
        self.slider.setEnabled(active)
        self.slider_mesh.setEnabled(active and self.chk_mesh.isChecked())

    def _mesh_alpha(self) -> float:
        """Mesh grid line alpha (0..1) from the visibility slider."""
        return float(self.slider_mesh.value()) / 100.0

    def _on_mesh_toggled(self, _checked: bool = False) -> None:
        self._update_controls_enabled()
        self._redraw()

    def _on_mesh_style_changed(self, _value: int = 0) -> None:
        self._redraw()

    def _on_plane_changed(self, value: float) -> None:
        # keep slider roughly in sync (0..1000 maps to the domain range)
        if self._csx is None:
            return
        key = _slice_key(self.cmb_view.currentText())
        if key is None:
            return
        normal = _SLICE_AXES[key][2]
        area = get_sim_area(self._csx)
        if area is not None:
            lo, hi = area[0][_AXIS[normal]], area[1][_AXIS[normal]]
            if hi > lo:
                frac = (value - lo) / (hi - lo)
                self.slider.blockSignals(True)
                self.slider.setValue(int(np.clip(frac * 1000, 0, 1000)))
                self.slider.blockSignals(False)
        self._redraw()

    def _on_slider(self, pos: int) -> None:
        if self._csx is None:
            return
        key = _slice_key(self.cmb_view.currentText())
        if key is None:
            return
        normal = _SLICE_AXES[key][2]
        area = get_sim_area(self._csx)
        if area is None:
            return
        lo, hi = float(area[0][_AXIS[normal]]), float(area[1][_AXIS[normal]])
        value = lo + (hi - lo) * (pos / 1000.0)
        # Snap onto the nearest mesh line so the section visibly jumps.
        value = self._nearest_mesh_line(normal, value)
        self.spn_plane.blockSignals(True)
        self.spn_plane.setValue(value)
        self.spn_plane.blockSignals(False)
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
        area = get_sim_area(self._csx)
        if area is None:
            return
        lo, hi = float(area[0][_AXIS[normal]]), float(area[1][_AXIS[normal]])
        self.spn_plane.setRange(lo, hi)
        # Map the slider onto the real domain so the plane actually lands on
        # mesh lines instead of the fixed 0..100 range.
        self.slider.blockSignals(True)
        self.slider.setRange(0, 1000)
        if reset:
            # a freshly built / loaded mesh starts on the model origin
            # (clamped by the spinbox range when 0 lies outside the domain)
            self.spn_plane.setValue(0.0)
        self.spn_plane.setValue(self.spn_plane.value())
        frac = (self.spn_plane.value() - lo) / (hi - lo) if hi > lo else 0.0
        self.slider.setValue(int(np.clip(frac * 1000, 0, 1000)))
        self.slider.blockSignals(False)

    def _nearest_mesh_line(self, normal: str, value: float) -> float:
        """Snap the plane value to the closest mesh line on the slice normal."""
        try:
            lines = np.asarray(mesh_lines_from_csx(self._csx)[normal], dtype=float)
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
        mesh = mesh_lines_from_csx(self._csx)
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
        area = get_sim_area(self._csx)
        if area is not None:
            lines.append(
                f"  Domain: [{area[0][0]:.3g}, {area[0][1]:.3g}, {area[0][2]:.3g}] → "
                f"[{area[1][0]:.3g}, {area[1][1]:.3g}, {area[1][2]:.3g}]"
            )
        lines.append("")
        lines.append("=== Primitives (CSX) ===")
        infos = iter_primitive_infos(self._csx, coord_system=self._coord_system())
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
        if self._csx is None:
            return
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
        """Return (mesh_lines, prim_infos, ports, nf2ff, area)."""
        mesh = mesh_lines_from_csx(self._csx)
        infos = iter_primitive_infos(self._csx, coord_system=self._coord_system())
        area = get_sim_area(self._csx)
        return mesh, infos, self._ports, self._nf2ff, area

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
                # degenerate (line/point) ports → draw marker at center
                if abs(x1 - x0) < eps and abs(y1 - y0) < eps:
                    ax.plot(
                        [(x0 + x1) / 2], [(y0 + y1) / 2],
                        marker="o", markersize=8, color="red",
                        markeredgecolor="white", markeredgewidth=0.8, zorder=5,
                        label="Ports",
                    )
                else:
                    ax.add_patch(
                        Rectangle(
                            (x0, y0), x1 - x0, y1 - y0,
                            facecolor=(1.0, 0.2, 0.2, 0.55),
                            edgecolor="darkred", linewidth=1.2, zorder=5,
                            label="Ports",
                        )
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

        ax.set_xlabel(ax_a + " [mm]")
        ax.set_ylabel(ax_b + " [mm]")
        note = "  [OUTSIDE DOMAIN]" if outside else ""
        ax.set_title(f"{view} slice @ {normal}={plane:.4g} mm{note}", color="red" if outside else "black")
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
                    ax.plot([cx], [cy], marker="o", markersize=5, color="red", zorder=5)
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
        mesh = mesh_lines_from_csx(self._csx)
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
