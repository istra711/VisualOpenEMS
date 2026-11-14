"""Antenna-pattern browser for a variable sweep.

2D cuts and gain/efficiency vs frequency are overlaid for all points.  The 3D
pattern is shown one frame at a time with a slider and play/pause controls; it
can be exported as GIF or MP4.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
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
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import matplotlib
import matplotlib.cm as cm
import matplotlib.colors as mcolors

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from app.postproc.video_timing import gif_frame_duration_ms
from app.ui.antenna_viewer import load_nf2ff_json
from app.ui.tab_help import show_tab_help


class SweepPatternsDialog(QDialog):
    """Overlay 2D cuts and play 3D patterns from a sweep result bundle."""

    PLOT_TYPES = (
        "E-Plane (phi=90)",
        "H-Plane (phi=0)",
        "Azimuth (theta=90)",
        "Gain vs Frequency",
        "Radiated Efficiency vs Frequency",
        "3D Pattern",
    )

    def __init__(self, sweep_json: Path | str, parent=None) -> None:
        super().__init__(parent)
        self.sweep_json = Path(sweep_json)
        self.root = self.sweep_json.parent
        self.payload = json.loads(self.sweep_json.read_text(encoding="utf-8"))
        self.spec = dict(self.payload.get("spec") or {})
        self.points = [
            dict(p)
            for p in (self.payload.get("points") or [])
            if str(p.get("nf2ff") or "").strip()
        ]
        self._cache: dict[int, dict] = {}
        self._frame_index = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_frame)
        self._playing = False

        self.setWindowTitle(f"Sweep antenna patterns â€” {self.root.name}")
        self.resize(1180, 760)
        self._build_ui()
        self._populate()
        self._redraw()

    # -- UI ---------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        info = QLabel(
            "Each point is a complete NF2FF far-field result. 2D cuts and "
            "gain/efficiency curves overlay all checked points; the 3D pattern "
            "plays as a film (one frame per sweep point). For a phase sweep, "
            "vary the relative phase of simultaneously excited ports to see a "
            "pattern change; changing the global phase of a single excited "
            "port does not change the gain magnitude."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #37474f;")
        root.addWidget(info)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Plot"))
        self.cmb_plot = QComboBox()
        self.cmb_plot.addItems(self.PLOT_TYPES)
        self.cmb_plot.setToolTip(
            "2D cuts and gain/efficiency curves overlay all checked points.\n"
            "3D Pattern uses one frame per point with the slider/play controls."
        )
        bar.addWidget(self.cmb_plot)
        bar.addWidget(QLabel("Frequency"))
        self.cmb_freq = QComboBox()
        self.cmb_freq.setToolTip(
            "NF2FF evaluation frequency. The 3D film and 2D cuts use this "
            "frequency; Gain/Efficiency vs Frequency use all frequencies."
        )
        bar.addWidget(self.cmb_freq)
        bar.addWidget(QLabel("Field"))
        self.cmb_field = QComboBox()
        self.cmb_field.addItem("Gain", "gain_dBi")
        self.cmb_field.addItem("Directivity", "D_dBi")
        self.cmb_field.setToolTip(
            "Gain includes radiation efficiency; Directivity is the pattern "
            "shape without losses. The 3D surface is normalised to the "
            "selected field's min/max; the colorbar is in dBi."
        )
        bar.addWidget(self.cmb_field)
        bar.addStretch(1)
        root.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Sweep points (check to overlay)"))
        self.point_list = QListWidget()
        self.point_list.itemChanged.connect(self._redraw)
        lv.addWidget(self.point_list, 1)
        lv.addWidget(
            QLabel(
                "The 3D player uses the point selected here as the first "
                "frame; the slider below steps through all checked points."
            )
        )
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.frame_bar = QWidget()
        fb = QHBoxLayout(self.frame_bar)
        fb.setContentsMargins(0, 0, 0, 0)
        self.btn_play = QPushButton("Play")
        self.btn_play.setCheckable(True)
        self.btn_play.setToolTip(
            "Animate the 3D pattern through the checked sweep points. "
            "For a 0â€¦360Â° phase sweep this loops seamlessly."
        )
        fb.addWidget(self.btn_play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setToolTip("Current 3D frame (sweep point)")
        fb.addWidget(self.slider, 1)
        fb.addWidget(QLabel("fps"))
        self.spn_fps = QSpinBox()
        self.spn_fps.setRange(1, 30)
        self.spn_fps.setValue(8)
        fb.addWidget(self.spn_fps)
        self.lbl_frame = QLabel("")
        fb.addWidget(self.lbl_frame)
        rv.addWidget(self.frame_bar)
        self.figure = Figure(figsize=(7, 5.5), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        rv.addWidget(self.canvas, 1)
        split.addWidget(right)
        split.setSizes([285, 850])

        bottom = QHBoxLayout()
        self.btn_csv = QPushButton("Export CSVâ€¦")
        self.btn_png = QPushButton("Export PNGâ€¦")
        self.btn_gif = QPushButton("Export GIFâ€¦")
        self.btn_mp4 = QPushButton("Export MP4â€¦")
        self.btn_help = QPushButton("Help")
        self.btn_close = QPushButton("Close")
        for btn in (
            self.btn_csv,
            self.btn_png,
            self.btn_gif,
            self.btn_mp4,
            self.btn_help,
            self.btn_close,
        ):
            bottom.addWidget(btn)
        bottom.addStretch(1)
        root.addLayout(bottom)

        self.cmb_plot.currentTextChanged.connect(self._on_plot_changed)
        self.cmb_freq.currentIndexChanged.connect(self._redraw)
        self.cmb_field.currentTextChanged.connect(self._redraw)
        self.btn_play.toggled.connect(self._set_playing)
        self.slider.valueChanged.connect(self._on_frame)
        self.spn_fps.valueChanged.connect(self._refresh_timer)
        self.btn_csv.clicked.connect(self._export_csv)
        self.btn_png.clicked.connect(self._export_png)
        self.btn_gif.clicked.connect(lambda: self._export_animation("gif"))
        self.btn_mp4.clicked.connect(lambda: self._export_animation("mp4"))
        self.btn_help.clicked.connect(
            lambda: show_tab_help(self, "Sweep Antenna Patterns")
        )
        self.btn_close.clicked.connect(self.accept)

    def _populate(self) -> None:
        self.point_list.blockSignals(True)
        for point in self.points:
            index = int(point.get("index", -1))
            label = str(point.get("label") or f"point {index + 1}")
            status = str(point.get("status") or "?")
            item = QListWidgetItem(f"{label}   [{status}]")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(str(self.root / str(point.get("nf2ff") or "")))
            self.point_list.addItem(item)
        self.point_list.blockSignals(False)
        self.slider.setRange(0, max(0, self.point_list.count() - 1))

        # Frequency combo from the first readable pattern.
        freqs: list[float] = []
        for point in self.points:
            data = self._pattern(point)
            if data is not None:
                freqs = [
                    float(f)
                    for f in np.asarray(data.get("frequencies", []), dtype=float)
                ]
                break
        self.cmb_freq.blockSignals(True)
        self.cmb_freq.clear()
        for f in freqs:
            self.cmb_freq.addItem(f"{f / 1e9:.6g} GHz" if f > 1e6 else f"{f:.6g} Hz")
        self.cmb_freq.blockSignals(False)
        self._on_plot_changed()

    def _pattern(self, point: dict) -> dict | None:
        index = int(point.get("index", -1))
        if index in self._cache:
            return self._cache[index]
        rel = str(point.get("nf2ff") or "").strip()
        if not rel:
            return None
        path = self.root / rel
        if not path.is_file():
            return None
        try:
            data = load_nf2ff_json(path)
        except Exception:  # noqa: BLE001
            return None
        self._cache[index] = data
        return data

    def _checked_points(self) -> list[dict]:
        out: list[dict] = []
        for i in range(self.point_list.count()):
            item = self.point_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            index = int(item.data(Qt.ItemDataRole.UserRole))
            point = next(
                (p for p in self.points if int(p.get("index", -1)) == index),
                None,
            )
            if point is not None and self._pattern(point) is not None:
                out.append(point)
        return out

    def _target_name(self) -> str:
        return str(self.spec.get("target") or self.spec.get("variable") or "")

    def _is_phase(self) -> bool:
        return str(self.spec.get("target_kind") or "variable") == "port_phase"

    def _value(self, point: dict) -> float:
        return float((point.get("values") or {}).get(self._target_name(), 0.0))

    def _frequency_index(self) -> int:
        return max(0, int(self.cmb_freq.currentIndex()))

    def _field_key(self) -> str:
        return str(self.cmb_field.currentData() or "gain_dBi")

    def _cut(
        self, data: dict, plot: str, field: str
    ) -> tuple[np.ndarray, np.ndarray] | None:
        arr = data.get(field)
        th = data.get("theta_deg")
        ph = data.get("phi_deg")
        if arr is None or th is None or ph is None:
            return None
        arr = np.asarray(arr, dtype=float)
        th = np.asarray(th, dtype=float).reshape(-1)
        ph = np.asarray(ph, dtype=float).reshape(-1)
        if arr.ndim == 3:
            fi = min(self._frequency_index(), arr.shape[0] - 1)
            arr = arr[fi]
        if arr.ndim != 2 or arr.shape[0] != th.size or arr.shape[1] != ph.size:
            return None
        if plot in ("E-Plane (phi=90)", "H-Plane (phi=0)"):
            phi0 = 90.0 if plot.startswith("E-") else 0.0
            ip = int(np.argmin(np.abs(ph - phi0)))
            target = (phi0 + 180.0) % 360.0
            wrap = (ph - target + 180.0) % 360.0 - 180.0
            ip_back = int(np.argmin(np.abs(wrap)))
            front = arr[:, ip]
            back = arr[:, ip_back]
            ang = np.concatenate([th, 360.0 - th[::-1]])
            vals = np.concatenate([front, back[::-1]])
            return ang, vals
        if plot.startswith("Azimuth"):
            it = int(np.argmin(np.abs(th - 90.0)))
            return ph, arr[it, :]
        ip = int(np.argmin(np.abs(ph - 0.0)))
        return th, arr[:, ip]

    # -- plotting ---------------------------------------------------------
    def _on_plot_changed(self, *_args) -> None:
        is_3d = self.cmb_plot.currentText() == "3D Pattern"
        self.frame_bar.setVisible(is_3d)
        self.btn_csv.setEnabled(not is_3d)
        if is_3d:
            self.btn_csv.setToolTip(
                "CSV is for the 2D cuts and gain/efficiency curves; use PNG or "
                "GIF/MP4 for the 3D pattern."
            )
        else:
            self.btn_csv.setToolTip("Export the current plotted curves as CSV")
        if not is_3d:
            self._set_playing(False)
        self._redraw()

    def _redraw(self, *_args) -> None:
        self.figure.clear()
        plot = self.cmb_plot.currentText()
        if plot == "3D Pattern":
            checked = self._checked_points()
            self.slider.blockSignals(True)
            self.slider.setRange(0, max(0, len(checked) - 1))
            self.slider.blockSignals(False)
            self._draw_3d()
            self._refresh_timer()
        elif plot == "Gain vs Frequency":
            self._plot_vs_frequency(gain=self._field_key() == "gain_dBi")
        elif plot == "Radiated Efficiency vs Frequency":
            self._plot_efficiency()
        else:
            self._plot_cuts(plot)
        self.canvas.draw_idle()

    def _colors(self, points: list[dict]):
        values = [self._value(p) for p in points]
        if self._is_phase():
            norm = mcolors.Normalize(vmin=0.0, vmax=360.0)
            cmap = matplotlib.colormaps["hsv"]
        else:
            vmin = min(values) if values else 0.0
            vmax = max(values) if values else 1.0
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
            cmap = matplotlib.colormaps["viridis"]
        return values, norm, cmap

    def _plot_cuts(self, plot: str) -> None:
        points = self._checked_points()
        ax = self.figure.add_subplot(111)
        if not points:
            ax.text(0.5, 0.5, "No NF2FF point selected", ha="center", va="center")
            return
        values, norm, cmap = self._colors(points)
        field = self._field_key()
        for point, value in zip(points, values):
            data = self._pattern(point)
            cut = self._cut(data, plot, field) if data else None
            if cut is None:
                continue
            ang, vals = cut
            ax.plot(ang, vals, lw=1.3, color=cmap(norm(value)))
        ax.set_xlabel("Angle [deg]")
        ax.set_ylabel("Gain [dBi]" if field == "gain_dBi" else "Directivity [dBi]")
        ax.set_title(plot)
        ax.grid(True, alpha=0.3)
        sm = cm.ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        cb = self.figure.colorbar(sm, ax=ax, pad=0.02)
        cb.set_label(
            ("phase [deg]" if self._is_phase() else self._target_name() or "value")
        )

    def _plot_vs_frequency(self, *, gain: bool) -> None:
        points = self._checked_points()
        ax = self.figure.add_subplot(111)
        if not points:
            ax.text(0.5, 0.5, "No NF2FF point selected", ha="center", va="center")
            return
        values, norm, cmap = self._colors(points)
        for point, value in zip(points, values):
            data = self._pattern(point)
            if not data:
                continue
            freqs = np.asarray(data.get("frequencies", []), dtype=float)
            arr = data.get("gain_dBi" if gain else "Dmax_dBi")
            if arr is None:
                continue
            arr = np.asarray(arr, dtype=float)
            if arr.ndim == 3:
                arr = np.nanmax(arr.reshape(arr.shape[0], -1), axis=1)
            else:
                arr = arr.reshape(-1)
            n = min(freqs.size, arr.size)
            ax.plot(freqs[:n] / 1e9, arr[:n], lw=1.3, color=cmap(norm(value)))
        ax.set_xlabel("Frequency [GHz]")
        ax.set_ylabel("dBi")
        ax.set_title("Gain max vs frequency" if gain else "Directivity max vs frequency")
        ax.grid(True, alpha=0.3)
        sm = cm.ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        cb = self.figure.colorbar(sm, ax=ax, pad=0.02)
        cb.set_label("phase [deg]" if self._is_phase() else self._target_name())

    def _plot_efficiency(self) -> None:
        points = self._checked_points()
        ax = self.figure.add_subplot(111)
        if not points:
            ax.text(0.5, 0.5, "No NF2FF point selected", ha="center", va="center")
            return
        values, norm, cmap = self._colors(points)
        for point, value in zip(points, values):
            data = self._pattern(point)
            if not data:
                continue
            freqs = np.asarray(data.get("frequencies", []), dtype=float)
            eff = data.get("efficiency")
            if eff is None:
                continue
            eff = np.asarray(eff, dtype=float).reshape(-1)
            n = min(freqs.size, eff.size)
            ax.plot(freqs[:n] / 1e9, eff[:n] * 100.0, lw=1.3, color=cmap(norm(value)))
        ax.set_xlabel("Frequency [GHz]")
        ax.set_ylabel("eta [%]")
        ax.set_title("Radiated efficiency vs frequency")
        ax.grid(True, alpha=0.3)
        sm = cm.ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        cb = self.figure.colorbar(sm, ax=ax, pad=0.02)
        cb.set_label("phase [deg]" if self._is_phase() else self._target_name())

    def _draw_3d(self) -> None:
        points = self._checked_points()
        if not points:
            self.figure.text(0.5, 0.5, "No NF2FF point selected", ha="center", va="center")
            return
        index = min(max(0, self.slider.value()), len(points) - 1)
        point = points[index]
        data = self._pattern(point)
        field = self._field_key()
        # For 3D we need theta/phi, not the 2D cut; build a coarse surface.
        arr = data.get(field) if data else None
        th = data.get("theta_deg") if data else None
        ph = data.get("phi_deg") if data else None
        if arr is None or th is None or ph is None:
            self.figure.text(0.5, 0.5, "pattern data not available", ha="center", va="center")
            return
        arr = np.asarray(arr, dtype=float)
        if arr.ndim == 3:
            fi = min(self._frequency_index(), arr.shape[0] - 1)
            arr = arr[fi]
        th = np.asarray(th, dtype=float).reshape(-1)
        ph = np.asarray(ph, dtype=float).reshape(-1)
        if arr.shape != (th.size, ph.size):
            self.figure.text(0.5, 0.5, "pattern grid mismatch", ha="center", va="center")
            return
        step_t = max(1, th.size // 60)
        step_p = max(1, ph.size // 60)
        arr = arr[::step_t, ::step_p]
        th = th[::step_t]
        ph = ph[::step_p]
        TH, PH = np.meshgrid(np.deg2rad(th), np.deg2rad(ph), indexing="ij")
        data_max = float(np.nanmax(arr))
        data_min = float(np.nanmin(arr))
        if not np.isfinite(data_max):
            self.figure.text(0.5, 0.5, "pattern data invalid", ha="center", va="center")
            return
        span = max(data_max - data_min, 1e-6)
        R = np.clip((arr - data_min) / span, 0.0, 1.0)
        X = R * np.sin(TH) * np.cos(PH)
        Y = R * np.sin(TH) * np.sin(PH)
        Z = R * np.cos(TH)
        ax = self.figure.add_subplot(111, projection="3d")
        cmap = matplotlib.colormaps["viridis"]
        ax.plot_surface(
            X, Y, Z, facecolors=cmap((arr - data_min) / span), linewidth=0, shade=False
        )
        ax.set_xlim(-1.05, 1.05)
        ax.set_ylim(-1.05, 1.05)
        ax.set_zlim(-1.05, 1.05)
        ax.set_box_aspect((1, 1, 1))
        ax.set_axis_off()
        ax.view_init(elev=24, azim=-60)
        self.lbl_frame.setText(
            f"{index + 1}/{len(points)} Â· {point.get('label', '')}"
        )
        ax.set_title(
            f"3D pattern Â· {self.cmb_freq.currentText()} Â· max {data_max:.1f} dBi"
        )
        sm = cm.ScalarMappable(
            norm=mcolors.Normalize(vmin=data_min, vmax=data_max), cmap=cmap
        )
        sm.set_array([])
        self.figure.colorbar(sm, ax=ax, shrink=0.7, pad=0.02, label="dBi")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._timer.stop()
        super().closeEvent(event)

    def _on_frame(self, value: int) -> None:
        if self.cmb_plot.currentText() != "3D Pattern":
            return
        self._frame_index = int(value)
        self._redraw()

    def _set_playing(self, playing: bool) -> None:
        self._playing = bool(playing)
        self.btn_play.blockSignals(True)
        self.btn_play.setChecked(self._playing)
        self.btn_play.setText("Pause" if self._playing else "Play")
        self.btn_play.blockSignals(False)
        self._refresh_timer()

    def _refresh_timer(self, *_args) -> None:
        if self._playing and self.cmb_plot.currentText() == "3D Pattern":
            self._timer.start(max(50, int(1000 / max(1, self.spn_fps.value()))))
        else:
            self._timer.stop()

    def _advance_frame(self) -> None:
        n = max(1, self.slider.maximum() + 1)
        self.slider.setValue((self.slider.value() + 1) % n)

    # -- export ------------------------------------------------------------
    def _export_csv(self) -> None:
        default = self.root / "sweep_patterns.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export sweep pattern data", str(default), "CSV (*.csv);;All (*.*)"
        )
        if not path:
            return
        plot = self.cmb_plot.currentText()
        field = self._field_key()
        lines = ["point,target,value,frequency_Hz,angle_deg,field,value_dB"]
        for point in self._checked_points():
            data = self._pattern(point)
            if not data:
                continue
            value = self._value(point)
            if plot in self.PLOT_TYPES[:3]:
                cut = self._cut(data, plot, field)
                if cut is None:
                    continue
                ang, vals = cut
                f = float(np.asarray(data.get("frequencies", [0.0]))[self._frequency_index()])
                for a, v in zip(ang, vals):
                    lines.append(
                        f"{point.get('label','')},{self._target_name()},{value:.12g},"
                        f"{f:.12g},{a:.12g},{field},{v:.12g}"
                    )
            elif plot == "Gain vs Frequency":
                freqs = np.asarray(data.get("frequencies", []), dtype=float)
                arr = np.asarray(data.get("gain_dBi", []), dtype=float)
                if arr.ndim == 3:
                    arr = np.nanmax(arr.reshape(arr.shape[0], -1), axis=1)
                for f, v in zip(freqs, arr):
                    lines.append(
                        f"{point.get('label','')},{self._target_name()},{value:.12g},"
                        f"{f:.12g},,gain_dBi,{v:.12g}"
                    )
            elif plot == "Radiated Efficiency vs Frequency":
                freqs = np.asarray(data.get("frequencies", []), dtype=float)
                arr = np.asarray(data.get("efficiency", []), dtype=float).reshape(-1)
                for f, v in zip(freqs, arr):
                    lines.append(
                        f"{point.get('label','')},{self._target_name()},{value:.12g},"
                        f"{f:.12g},,efficiency,{float(v) * 100.0:.12g}"
                    )
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _export_png(self) -> None:
        default = self.root / "sweep_patterns.png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export sweep pattern plot", str(default), "PNG (*.png);;All (*.*)"
        )
        if not path:
            return
        self.canvas.draw()
        self.canvas.print_png(path)

    def _export_animation(self, fmt: str) -> None:
        if not self._checked_points():
            QMessageBox.information(self, "Export animation", "No NF2FF point selected.")
            return
        if self.cmb_plot.currentText() != "3D Pattern":
            QMessageBox.information(
                self, "Export animation", "Select the 3D Pattern plot first."
            )
            return
        self._set_playing(False)
        suffix = ".gif" if fmt == "gif" else ".mp4"
        default = self.root / f"sweep_patterns{suffix}"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export 3D pattern animation",
            str(default),
            "Animated GIF (*.gif)" if fmt == "gif" else "MP4 video (*.mp4)",
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != suffix:
            target = target.with_suffix(suffix)
        try:
            import imageio
        except ImportError:
            QMessageBox.warning(self, "Export animation", "imageio is required.")
            return
        if fmt == "gif":
            try:
                from PIL import Image  # noqa: F401
            except ImportError:
                QMessageBox.warning(self, "Export animation", "Pillow is required for GIF.")
                return
            writer = imageio.get_writer(
                str(target),
                mode="I",
                duration=gif_frame_duration_ms(max(1, self.spn_fps.value())),
                loop=0,
            )
        else:
            try:
                import imageio_ffmpeg  # noqa: F401
            except ImportError:
                QMessageBox.warning(
                    self,
                    "Export animation",
                    "MP4 needs imageio-ffmpeg (pip install imageio-ffmpeg).",
                )
                return
            writer = imageio.get_writer(
                str(target), fps=max(1, self.spn_fps.value()), codec="libx264", quality=8
            )
        selected = self._checked_points()
        old = self.slider.value()
        try:
            for i in range(len(selected)):
                self.slider.setValue(i)
                self.canvas.draw()
                frame = np.asarray(self.canvas.buffer_rgba())
                writer.append_data(frame[..., :3])
        finally:
            writer.close()
            self.slider.setValue(old)
        QMessageBox.information(self, "Export animation", f"Written:\n{target}")
