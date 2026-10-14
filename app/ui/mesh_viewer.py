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
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
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
from matplotlib.patches import FancyBboxPatch, Rectangle

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
    """Matplotlib-based mesh / geometry inspector."""

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = Path(work_dir) if work_dir else None
        self._csx = None
        self._project = None  # app.models.project.Project | None
        self._primitives: list[dict] = []
        self._ports: list = []
        self._nf2ff = None
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
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.chk_mesh = QCheckBox("Show mesh grid")
        self.chk_mesh.setChecked(True)
        self.chk_geom = QCheckBox("Show geometry")
        self.chk_geom.setChecked(True)
        self.chk_ports = QCheckBox("Show ports")
        self.chk_ports.setChecked(True)
        self.chk_nf2ff = QCheckBox("Show NF2FF")
        self.chk_nf2ff.setChecked(True)
        self.btn_appcsxcad = QPushButton("Open in AppCSXCAD (3D)…")
        self.btn_export_xml = QPushButton("Export CSX XML…")
        self.btn_popout = QPushButton("Pop-out viewer…")

        for w in (
            self.cmb_view,
            QLabel("Plane"),
            self.spn_plane,
            self.slider,
            self.chk_mesh,
            self.chk_geom,
            self.chk_ports,
            self.chk_nf2ff,
        ):
            bar.addWidget(w)
        bar.addStretch(1)
        bar.addWidget(self.btn_popout)
        root.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
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

        self.fig_slice = None
        self.canvas_slice = None
        self.toolbar_slice = None
        self.fig_cells = None
        self.canvas_cells = None
        self.toolbar_cells = None
        self._recreate_canvases()

        split.setSizes([1000, 0])
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)

        # connections
        self.cmb_view.currentIndexChanged.connect(self._on_view_changed)
        self.spn_plane.valueChanged.connect(self._on_plane_changed)
        self.slider.valueChanged.connect(self._on_slider)
        self.chk_mesh.toggled.connect(self._redraw)
        self.chk_geom.toggled.connect(self._redraw)
        self.chk_ports.toggled.connect(self._redraw)
        self.chk_nf2ff.toggled.connect(self._redraw)
        self.btn_appcsxcad.clicked.connect(self._on_open_appcsxcad)
        self.btn_export_xml.clicked.connect(self._on_export_xml)
        self.btn_popout.clicked.connect(self._on_popout)

        self._on_view_changed(0)

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
    def set_csx(self, csx) -> None:
        """Store CSX, recreate canvases, and refresh all plots."""
        self._csx = csx
        try:
            self._recreate_canvases()
            self._refresh()
        except Exception as e:  # noqa: BLE001 — never crash the GUI on redraw
            print(f"mesh viewer set_csx refresh error: {e}")

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
        view = self.cmb_view.currentText()
        if view in _SLICE_AXES:
            self.spn_plane.setEnabled(True)
            self.slider.setEnabled(True)
        else:
            self.spn_plane.setEnabled(False)
            self.slider.setEnabled(False)
        self._redraw()

    def _on_plane_changed(self, value: float) -> None:
        # keep slider roughly in sync (0..100 maps to plane range)
        if self._csx is None:
            return
        view = self.cmb_view.currentText()
        if view not in _SLICE_AXES:
            return
        normal = _SLICE_AXES[view][2]
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
        view = self.cmb_view.currentText()
        if view not in _SLICE_AXES:
            return
        normal = _SLICE_AXES[view][2]
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
        self._update_plane_range()
        self._update_info()
        self._redraw()

    def _update_plane_range(self) -> None:
        view = self.cmb_view.currentText()
        if view not in _SLICE_AXES:
            return
        normal = _SLICE_AXES[view][2]
        area = get_sim_area(self._csx)
        if area is None:
            return
        lo, hi = float(area[0][_AXIS[normal]]), float(area[1][_AXIS[normal]])
        self.spn_plane.setRange(lo, hi)
        # Map the slider onto the real domain so the plane actually lands on
        # mesh lines instead of the fixed 0..100 range.
        self.slider.blockSignals(True)
        self.slider.setRange(0, 1000)
        mid = 0.5 * (lo + hi)
        if abs(self.spn_plane.value()) < 1e-12 or self.spn_plane.value() == 0.0:
            self.spn_plane.setValue(mid)
        self.spn_plane.setValue(self.spn_plane.value())
        frac = (self.spn_plane.value() - lo) / (hi - lo) if hi > lo else 0.5
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

    def _update_info(self) -> None:
        lines: list[str] = []
        lines.append("=== Mesh ===")
        mesh = mesh_lines_from_csx(self._csx)
        for d in "xyz":
            arr = mesh[d]
            if arr.size == 0:
                lines.append(f"  {d}: (empty)")
                continue
            mids, sizes = mesh_cells_from_lines(arr)
            lines.append(
                f"  {d}: {arr.size} lines, "
                f"cells {sizes.size}, "
                f"min {sizes.min():.4g}, max {sizes.max():.4g}, "
                f"range [{arr.min():.4g}, {arr.max():.4g}]"
            )
        area = get_sim_area(self._csx)
        if area is not None:
            lines.append(
                f"  Domain: [{area[0][0]:.3g}, {area[0][1]:.3g}, {area[0][2]:.3g}] → "
                f"[{area[1][0]:.3g}, {area[1][1]:.3g}, {area[1][2]:.3g}]"
            )
        lines.append("")
        lines.append("=== Primitives (CSX) ===")
        infos = iter_primitive_infos(self._csx)
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
            if view in _SLICE_AXES:
                self.plot_tabs.setCurrentIndex(0)
                self._draw_slice(view)
            elif view == "Overview":
                self.plot_tabs.setCurrentIndex(0)
                self._draw_overview()
            else:  # Cell sizes
                self.plot_tabs.setCurrentIndex(1)
                self._draw_cells()
        except Exception as e:  # noqa: BLE001 — never crash the GUI on redraw
            print(f"mesh viewer redraw error: {e}")

    def _collect_scene(self):
        """Return (mesh_lines, prim_infos, ports, nf2ff, area)."""
        mesh = mesh_lines_from_csx(self._csx)
        infos = iter_primitive_infos(self._csx)
        area = get_sim_area(self._csx)
        return mesh, infos, self._ports, self._nf2ff, area

    @staticmethod
    def _outside_names(infos, area) -> set[str]:
        """Names of primitives that stick out of the FDTD domain."""
        try:
            return {info.name for info, _j, _a, _s in primitives_outside_domain(infos, area)}
        except Exception:  # noqa: BLE001
            return set()

    def _draw_slice(self, view: str) -> None:
        ax_a, ax_b, normal = _SLICE_AXES[view]
        ia, ib, inorm = _AXIS[ax_a], _AXIS[ax_b], _AXIS[normal]
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
            for lines, idx, color in ((mesh[ax_a], ia, (0.4, 0.4, 0.4, 0.35)),
                                       (mesh[ax_b], ib, (0.4, 0.4, 0.4, 0.35))):
                arr = np.sort(np.asarray(lines, dtype=float))
                if arr.size == 0:
                    continue
                if idx == ia:
                    ax.vlines(arr, *self._axis_limits(area, ib), colors=color, linewidths=0.4)
                else:
                    ax.hlines(arr, *self._axis_limits(area, ia), colors=color, linewidths=0.4)

        # Geometry primitives: draw 2D footprint when the box intersects the slice plane
        if self.chk_geom.isChecked():
            eps = 1e-9
            for info in infos:
                lo_n, hi_n = info.start[inorm], info.stop[inorm]
                if plane < lo_n - eps or plane > hi_n + eps:
                    continue
                x0, x1 = info.start[ia], info.stop[ia]
                y0, y1 = info.start[ib], info.stop[ib]
                color = default_color_for(info.kind, info.name)
                bad = info.name in outside
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
                if plane < min(st[inorm], sp[inorm]) - eps or plane > max(st[inorm], sp[inorm]) + eps:
                    continue
                x0, x1 = min(st[ia], sp[ia]), max(st[ia], sp[ia])
                y0, y1 = min(st[ib], sp[ib]), max(st[ib], sp[ib])
                # degenerate (line/point) ports → draw marker at center
                if abs(x1 - x0) < eps and abs(y1 - y0) < eps:
                    ax.plot(
                        [(x0 + x1) / 2], [(y0 + y1) / 2],
                        marker="o", markersize=8, color="red",
                        markeredgecolor="white", markeredgewidth=0.8, zorder=5,
                    )
                else:
                    ax.add_patch(
                        Rectangle(
                            (x0, y0), x1 - x0, y1 - y0,
                            facecolor=(1.0, 0.2, 0.2, 0.55),
                            edgecolor="darkred", linewidth=1.2, zorder=5,
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
