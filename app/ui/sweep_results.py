"""Sweep results browser: overlay all points, export CSV/PNG."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import matplotlib
import matplotlib.cm as cm

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from app.ui.tab_help import show_tab_help


class SweepResultsDialog(QDialog):
    """Browse a ``sweep.json`` result bundle without changing the project."""

    PLOT_TYPES = ("|S| dB", "Phase deg", "|S| mag", "VSWR")

    def __init__(self, sweep_json: Path | str, *, viewer=None, parent=None) -> None:
        super().__init__(parent)
        self.sweep_json = Path(sweep_json)
        self.viewer = viewer
        self.payload = json.loads(self.sweep_json.read_text(encoding="utf-8"))
        self.root = self.sweep_json.parent
        self.points: list[dict] = []
        self.networks: dict[int, object] = {}
        self._load_networks()

        self.setWindowTitle(f"Variable sweep — {self.sweep_json.parent.name}")
        self.resize(1050, 720)
        root = QVBoxLayout(self)

        info = QLabel(self._summary_text())
        info.setWordWrap(True)
        root.addWidget(info)
        explanation = QLabel(
            "S-Parameters: compare the selected trace across points. "
            "Antenna patterns: 2D cuts and gain/efficiency overlays, or a "
            "3D film through all points. For a phase sweep, vary the relative "
            "phase of at least two excited ports; a single port's global "
            "phase does not change the gain pattern. Load a point as "
            "current/reference below. Full details: Help."
        )
        explanation.setWordWrap(True)
        explanation.setStyleSheet("color: #455a64;")
        root.addWidget(explanation)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Sweep points (check to overlay)"))
        self.point_list = QListWidget()
        self.point_list.itemChanged.connect(self._redraw)
        lv.addWidget(self.point_list, 1)

        row = QHBoxLayout()
        self.btn_current = QPushButton("Load as current")
        self.btn_reference = QPushButton("Use as reference")
        self.btn_current.setEnabled(viewer is not None)
        self.btn_reference.setEnabled(viewer is not None)
        row.addWidget(self.btn_current)
        row.addWidget(self.btn_reference)
        lv.addLayout(row)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Trace"))
        self.cmb_trace = QComboBox()
        bar.addWidget(self.cmb_trace)
        bar.addWidget(QLabel("Plot"))
        self.cmb_plot = QComboBox()
        self.cmb_plot.addItems(self.PLOT_TYPES)
        bar.addWidget(self.cmb_plot)
        bar.addStretch(1)
        rv.addLayout(bar)
        self.figure = Figure(figsize=(7, 5), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        rv.addWidget(self.canvas, 1)
        split.addWidget(right)
        split.setSizes([300, 750])

        bottom = QHBoxLayout()
        self.btn_patterns = QPushButton("Antenna patterns…")
        self.btn_patterns.setToolTip(
            "Browse per-point NF2FF results: overlay 2D cuts, gain/efficiency "
            "curves, or play the 3D pattern as a film."
        )
        self.btn_csv = QPushButton("Export CSV…")
        self.btn_png = QPushButton("Export PNG…")
        self.btn_help = QPushButton("Help")
        self.btn_close = QPushButton("Close")
        bottom.addWidget(self.btn_patterns)
        bottom.addWidget(self.btn_csv)
        bottom.addWidget(self.btn_png)
        bottom.addStretch(1)
        bottom.addWidget(self.btn_help)
        bottom.addWidget(self.btn_close)
        root.addLayout(bottom)

        self.cmb_trace.currentTextChanged.connect(self._redraw)
        self.cmb_plot.currentTextChanged.connect(self._redraw)
        self.btn_current.clicked.connect(self._load_current)
        self.btn_reference.clicked.connect(self._load_reference)
        self.btn_patterns.clicked.connect(self._open_patterns)
        self.btn_csv.clicked.connect(self._export_csv)
        self.btn_png.clicked.connect(self._export_png)
        self.btn_help.clicked.connect(
            lambda: show_tab_help(self, "Variable Sweep")
        )
        self.btn_close.clicked.connect(self.accept)
        has_patterns = any(
            str(p.get("nf2ff") or "").strip()
            for p in (self.payload.get("points") or [])
        )
        self.btn_patterns.setEnabled(has_patterns)
        if not has_patterns:
            self.btn_patterns.setToolTip(
                "No NF2FF data in this sweep. Re-run with 'Collect NF2FF per "
                "point' enabled."
            )

        self._populate_points()
        self._populate_traces()
        self._redraw()

    # -- loading -----------------------------------------------------------
    def _summary_text(self) -> str:
        spec = self.payload.get("spec") or {}
        status = str(self.payload.get("status") or "?")
        points = self.payload.get("points") or []
        variable = str(spec.get("target") or spec.get("variable") or "?")
        if str(spec.get("target_kind") or "variable") == "port_phase":
            variable = f"phase {variable} [deg]"
        values = spec.get("values") or []
        return (
            f"Sweep {variable}: {len(points)} point(s), status {status}. "
            f"Values: {', '.join(f'{float(v):g}' for v in values[:12])}"
            + (" …" if len(values) > 12 else "")
        )

    def _load_networks(self) -> None:
        from app.ui.sparam_viewer import load_network_from_path

        for point in self.payload.get("points") or []:
            self.points.append(dict(point))
            rel = str(point.get("sparams") or "").strip()
            if not rel:
                continue
            path = self.root / rel
            if not path.is_file():
                continue
            try:
                self.networks[int(point.get("index", -1))] = load_network_from_path(
                    path
                )
            except Exception:  # noqa: BLE001
                pass

    def _populate_points(self) -> None:
        self.point_list.blockSignals(True)
        for point in self.points:
            index = int(point.get("index", -1))
            label = str(point.get("label") or f"point {index + 1}")
            status = str(point.get("status") or "?")
            item = QListWidgetItem(f"{label}   [{status}]")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if index in self.networks else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(
                str(self.root / str(point.get("work_dir") or ""))
                + (f"\n{point.get('error')}" if point.get("error") else "")
            )
            self.point_list.addItem(item)
        self.point_list.blockSignals(False)
        if self.point_list.count():
            self.point_list.setCurrentRow(0)

    def _populate_traces(self) -> None:
        keys: list[str] = []
        for network in self.networks.values():
            try:
                n = int(network.nports)
            except Exception:  # noqa: BLE001
                continue
            keys = [f"S{i + 1}{j + 1}" for i in range(n) for j in range(n)]
            break
        self.cmb_trace.blockSignals(True)
        self.cmb_trace.clear()
        self.cmb_trace.addItems(keys)
        if "S11" in keys:
            self.cmb_trace.setCurrentText("S11")
        elif keys:
            self.cmb_trace.setCurrentIndex(0)
        self.cmb_trace.blockSignals(False)

    # -- plotting ----------------------------------------------------------
    def _checked_points(self) -> list[dict]:
        out: list[dict] = []
        for i in range(self.point_list.count()):
            item = self.point_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                index = int(item.data(Qt.ItemDataRole.UserRole))
                if index in self.networks:
                    out.append(self.points[index])
        return out

    def _trace_values(self, network, key: str, plot: str) -> np.ndarray:
        try:
            if len(key) == 3 and key[0] == "S" and key[1].isdigit() and key[2].isdigit():
                i = int(key[1]) - 1
                j = int(key[2]) - 1
                s = np.asarray(network.s[:, i, j], dtype=complex)
            else:
                s = np.asarray(network[key].s[:, 0, 0], dtype=complex)
        except Exception:  # noqa: BLE001
            return np.array([])
        if plot == "|S| dB":
            return 20.0 * np.log10(np.maximum(np.abs(s), 1e-15))
        if plot == "Phase deg":
            return np.degrees(np.angle(s))
        if plot == "|S| mag":
            return np.abs(s)
        mag = np.abs(s)
        return (1.0 + mag) / np.maximum(1.0 - mag, 1e-12)

    def _redraw(self, *_args) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        points = self._checked_points()
        trace = self.cmb_trace.currentText()
        plot = self.cmb_plot.currentText()
        if not points or not trace:
            ax.text(0.5, 0.5, "No sweep point selected", ha="center", va="center")
            self.canvas.draw_idle()
            return
        spec = self.payload.get("spec") or {}
        target = str(spec.get("target") or spec.get("variable") or "")
        is_phase = str(spec.get("target_kind") or "variable") == "port_phase"
        values = [
            float((p.get("values") or {}).get(target, 0.0))
            for p in points
        ]
        vmin = 0.0 if is_phase else (min(values) if values else 0.0)
        vmax = 360.0 if is_phase else (max(values) if values else 1.0)
        span = max(abs(vmax - vmin), 1e-12)
        import matplotlib.colors as mcolors

        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
        try:
            cmap = matplotlib.colormaps["hsv" if is_phase else "viridis"]
        except Exception:  # noqa: BLE001 - older Matplotlib fallback
            cmap = cm.get_cmap("hsv" if is_phase else "viridis")
        legend_lines = []
        for point, value in zip(points, values):
            network = self.networks[int(point.get("index", -1))]
            y = self._trace_values(network, trace, plot)
            x = np.asarray(network.f, dtype=float) / 1e9
            if y.size != x.size:
                continue
            color = cmap((value - vmin) / span)
            line = ax.plot(x, y, lw=1.3, color=color, label=f"{value:g}")
            legend_lines.append(line[0])
        ax.set_xlabel("Frequency [GHz]")
        ax.set_ylabel(plot)
        ax.grid(True, alpha=0.3)
        if legend_lines and len(legend_lines) <= 12:
            ax.legend(loc="best", fontsize=8)
        sm = cm.ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        cbar = self.figure.colorbar(sm, ax=ax, pad=0.02)
        cbar.set_label("phase [deg]" if is_phase else (target or "value"))
        self.canvas.draw_idle()

    # -- actions -----------------------------------------------------------
    def _selected_point(self) -> tuple[dict | None, Path | None]:
        item = self.point_list.currentItem()
        if item is None:
            return None, None
        index = int(item.data(Qt.ItemDataRole.UserRole))
        point = next((p for p in self.points if int(p.get("index", -1)) == index), None)
        if point is None:
            return None, None
        rel = str(point.get("sparams") or "").strip()
        path = self.root / rel if rel else None
        return point, path if path is not None and path.is_file() else None

    def _open_patterns(self) -> None:
        from app.ui.sweep_patterns import SweepPatternsDialog

        dlg = SweepPatternsDialog(self.sweep_json, parent=self)
        dlg.exec()

    def _load_current(self) -> None:
        if self.viewer is None:
            return
        _point, path = self._selected_point()
        if path is None:
            QMessageBox.information(self, "Load point", "This point has no S-parameters.")
            return
        self.viewer.load_path(path)

    def _load_reference(self) -> None:
        if self.viewer is None:
            return
        point, path = self._selected_point()
        if path is None or point is None:
            QMessageBox.information(self, "Reference point", "This point has no S-parameters.")
            return
        label = str(point.get("label") or path.stem)
        self.viewer.load_reference_path(
            path,
            label=f"sweep · {label}",
            meta={"kind": "sweep", "label": label, "path": str(path)},
        )

    def _export_png(self) -> None:
        default = self.sweep_json.parent / "sweep_results.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export sweep plot", str(default), "PNG (*.png);;All (*.*)"
        )
        if not path:
            return
        self.canvas.draw()
        self.canvas.print_png(path)

    def _export_csv(self) -> None:
        default = self.sweep_json.parent / "sweep_results.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export sweep values", str(default), "CSV (*.csv);;All (*.*)"
        )
        if not path:
            return
        trace = self.cmb_trace.currentText()
        plot = self.cmb_plot.currentText()
        spec = self.payload.get("spec") or {}
        variable = str(spec.get("target") or spec.get("variable") or "value")
        lines = [
            "point,index,status,"
            + variable
            + ",freq_Hz,freq_GHz,Sij,value"
        ]
        for point in self.points:
            index = int(point.get("index", -1))
            network = self.networks.get(index)
            if network is None:
                continue
            value = float((point.get("values") or {}).get(variable, 0.0))
            y = self._trace_values(network, trace, plot)
            x = np.asarray(network.f, dtype=float)
            if y.size != x.size:
                continue
            for f_hz, val in zip(x, y):
                lines.append(
                    f"{point.get('label', '')},{index},{point.get('status', '')},"
                    f"{value:.12g},{f_hz:.12g},{f_hz / 1e9:.12g},{trace},"
                    f"{val:.12g}"
                )
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
