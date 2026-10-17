"""Interactive S-parameter viewer: Smith chart, traces, markers, cursor."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure


def load_network_from_path(path: Path | str):
    """Load skrf Network from Touchstone or sparams.json (app export format)."""
    import skrf as rf

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        freqs = np.asarray(data.get("freq", []), dtype=float)
        if freqs.size == 0:
            raise ValueError(f"No freq data in {path}")
        keys = [
            k
            for k in data
            if k.startswith("S") and len(k) == 3 and k[1].isdigit() and k[2].isdigit()
        ]
        if not keys and "S11" in data:
            keys = ["S11"]
        n = 1
        for k in keys:
            n = max(n, int(k[1]), int(k[2]))
        s = np.zeros((freqs.size, n, n), dtype=complex)
        for k in keys:
            i, j = int(k[1]) - 1, int(k[2]) - 1
            block = data[k]
            if isinstance(block, dict) and "real" in block:
                s[:, i, j] = np.asarray(block["real"]) + 1j * np.asarray(block["imag"])
            else:
                s[:, i, j] = np.asarray(block, dtype=complex)
        return rf.Network(f=freqs, s=s, z0=50.0)
    return rf.Network(str(path))


def find_latest_sparam_files(work_dir: Path) -> list[Path]:
    """Discover sparams.s*p / sparams.json, newest first.

    Searches the new project layout (``<dir>/sparams``), a single project
    folder itself and the legacy ``<dir>/simulation`` / ``<dir>/*/simulation``
    locations — so both the global work directory (all projects) and one
    project directory can be passed.
    """
    out: list[Path] = []
    if not work_dir.exists():
        return out
    dirs: list[Path] = [work_dir / "sparams"]
    dirs += list(work_dir.glob("*/sparams"))
    direct = work_dir / "simulation"
    if direct.is_dir():
        dirs.append(direct)
    dirs += list(work_dir.glob("*/simulation"))
    for sim in dirs:
        for name in ("sparams.s1p", "sparams.s2p", "sparams.s3p", "sparams.s4p", "sparams.json"):
            p = sim / name
            if p.exists():
                out.append(p)
                break
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


class SParamGraphWindow(QMainWindow):
    """Standalone pop-out window hosting the S-param plot panel."""

    def __init__(self, viewer: "SParamViewer") -> None:
        super().__init__(None)
        self.viewer: SParamViewer | None = viewer
        self.setWindowTitle("S-Parameter Graph — pop-out")
        self.resize(900, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        self.lbl_title = QLabel("Pop-out: Smith / S-plot")
        self.lbl_title.setStyleSheet("font-weight: bold;")
        bar.addWidget(self.lbl_title)
        bar.addStretch(1)
        bar.addWidget(QLabel("Plot"))
        self.cmb_plot = QComboBox()
        self.cmb_plot.addItems(SParamViewer.PLOT_TYPES)
        bar.addWidget(self.cmb_plot)
        bar.addWidget(QLabel("Trace"))
        self.cmb_port = QComboBox()
        bar.addWidget(self.cmb_port)
        layout.addLayout(bar)

        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        layout.addWidget(self.lbl_info)

        # Move plot panel into this window
        if viewer is not None:
            right = viewer.right_panel
            right.setParent(None)
            layout.addWidget(right, 1)
            # Seed local controls from embedded viewer
            self.cmb_plot.blockSignals(True)
            self.cmb_plot.setCurrentText(viewer.cmb_plot.currentText())
            self.cmb_plot.blockSignals(False)
            self.cmb_port.blockSignals(True)
            self.cmb_port.addItems([viewer.cmb_port.itemText(i) for i in range(viewer.cmb_port.count())])
            if viewer.cmb_port.count():
                self.cmb_port.setCurrentText(viewer.cmb_port.currentText())
            self.cmb_port.blockSignals(False)
            self.lbl_info.setText(viewer.lbl_info.text())
            viewer.btn_popout.setText("Graph popped out")

        layout.setContentsMargins(6, 6, 6, 6)
        self.setCentralWidget(container)

        self.cmb_plot.currentTextChanged.connect(self._on_plot_changed)
        self.cmb_port.currentTextChanged.connect(self._on_port_changed)

    def _on_plot_changed(self, text: str) -> None:
        v = self.viewer
        if v is None:
            return
        v.cmb_plot.blockSignals(True)
        v.cmb_plot.setCurrentText(text)
        v.cmb_plot.blockSignals(False)
        v._redraw()

    def _on_port_changed(self, text: str) -> None:
        v = self.viewer
        if v is None:
            return
        if text and text != v.cmb_port.currentText():
            v.cmb_port.blockSignals(True)
            v.cmb_port.setCurrentText(text)
            v.cmb_port.blockSignals(False)
        v._redraw()
        self.lbl_info.setText(v.lbl_info.text())

    def closeEvent(self, event) -> None:  # noqa: N802
        v = self.viewer
        self.viewer = None
        if v is not None and v._graph_win is self:
            v._on_graph_popout_closed()
        super().closeEvent(event)


class SParamViewer(QWidget):
    """Browse S-parameters with Smith chart, Cartesian plots, markers, cursor."""

    PLOT_TYPES = [
        "Smith",
        "|S| dB",
        "|S| mag",
        "Phase deg",
        "VSWR",
        "Complex Re/Im",
        "Re/Im + Phase",
        "Z Re/Im",
    ]

    # Marker-list row styles (combo order; drives _marker_list_text).
    MARKER_STYLES: tuple[str, ...] = (
        "All formats",
        "dB",
        "|S|",
        "Phase",
        "S complex",
        "Γ polar",
        "Γ rect",
        "Z",
        "Y",
        "VSWR",
        "RL",
    )

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = Path(work_dir) if work_dir else None
        self.network = None
        self.network_path: Path | None = None
        self.markers: list[float] = []  # frequencies (Hz)
        self._cursor_line = None
        self._cursor_text = None
        self._marker_artists: list = []
        self._marker_z0 = 50.0
        self._graph_win: SParamGraphWindow | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # analysis dialog (right-click on plot)
        self._analysis_dlg = self._build_analysis_dialog()

        bar = QHBoxLayout()
        self.btn_open = QPushButton("Open Touchstone / JSON…")
        self.btn_reload = QPushButton("Reload")
        self.btn_scan = QPushButton("Scan work dir")
        self.cmb_plot = QComboBox()
        self.cmb_plot.addItems(self.PLOT_TYPES)
        self.cmb_port = QComboBox()
        self.lbl_info = QLabel("No data loaded")
        for w in (
            self.btn_open,
            self.btn_reload,
            self.btn_scan,
        ):
            bar.addWidget(w)
        bar.addWidget(QLabel("Plot"))
        bar.addWidget(self.cmb_plot)
        bar.addWidget(QLabel("Trace"))
        bar.addWidget(self.cmb_port)
        bar.addStretch(1)
        root.addLayout(bar)

        self._plot_split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self._plot_split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Loaded files"))
        self.file_list = QListWidget()
        lv.addWidget(self.file_list)
        lv.addWidget(QLabel("Markers (click plot to add)"))
        self.marker_list = QListWidget()
        lv.addWidget(self.marker_list)
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Marker style"))
        self.cmb_marker_style = QComboBox()
        self.cmb_marker_style.addItems(list(self.MARKER_STYLES))
        self.cmb_marker_style.setToolTip("Format shown in the marker list")
        fmt_row.addWidget(self.cmb_marker_style, 1)
        lv.addLayout(fmt_row)
        self.marker_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.marker_list.customContextMenuRequested.connect(self._on_marker_context_menu)
        self.left_panel = left
        self._plot_split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.figure = Figure(figsize=(7, 6), tight_layout=True)
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        rv.addWidget(self.toolbar)
        rv.addWidget(self.canvas)
        self.right_panel = right
        self._plot_split.addWidget(right)
        self._plot_split.setSizes([280, 720])

        self.canvas.mpl_connect("button_press_event", self._on_click)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("figure_leave_event", self._on_leave)
        self.canvas.mpl_connect("axes_leave_event", self._on_leave)

        self.btn_open.clicked.connect(self._on_open)
        self.btn_reload.clicked.connect(self._reload_current)
        self.btn_scan.clicked.connect(self._on_scan)
        self.cmb_marker_style.currentTextChanged.connect(self._on_marker_style_changed)
        self.cmb_plot.currentTextChanged.connect(lambda *_: self._redraw())
        self.cmb_plot.currentIndexChanged.connect(self._redraw)
        self.cmb_port.currentIndexChanged.connect(self._redraw)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        self.canvas.setToolTip(
            "Right-click to open the analysis menu\n"
            "(Go to S-min / Clear markers / Pop out graph)."
        )

        if self.work_dir:
            # list files only; load on user selection / explicit open
            self._on_scan()

    # ----- analysis dialog (right-click) ---------------------------------
    def _build_analysis_dialog(self) -> QDialog:
        # parent=None: floats above both the tab and the pop-out window
        dlg = QDialog(None)
        dlg.setWindowTitle("S-Param analysis")
        form = QFormLayout(dlg)
        self.btn_min = QPushButton("Go to S-min")
        self.btn_min.setToolTip(
            "Jump to the frequency of the deepest S-parameter minimum\n"
            "of the currently selected trace."
        )
        self.btn_clear_markers = QPushButton("Clear markers")
        self.btn_popout = QPushButton("Pop out graph")
        self.btn_popout.setToolTip(
            "Open the Smith/S-plot in a separate window.\n"
            "Click again to restore it to the tab."
        )
        form.addRow(self.btn_min)
        form.addRow(self.btn_clear_markers)
        form.addRow(self.btn_popout)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        form.addRow(buttons)
        dlg.setModal(False)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        dlg.setWindowFlag(Qt.WindowType.Tool, True)
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.btn_min.clicked.connect(self._goto_min)
        self.btn_clear_markers.clicked.connect(self._clear_markers)
        self.btn_popout.clicked.connect(self.popout_graph)
        return dlg

    # ----- data loading -------------------------------------------------
    def set_work_dir(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir)
        self._on_scan()

    def load_path(self, path: Path | str) -> None:
        path = Path(path)
        try:
            ntwk = load_network_from_path(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Load failed", str(e))
            return
        self.network = ntwk
        self.network_path = path
        self.markers = []
        self._populate_ports()
        self._refresh_markers()
        self.lbl_info.setText(
            f"{path.name}: {ntwk.nports} port(s), {len(ntwk.f)} pts, "
            f"{ntwk.f[0]/1e9:.4g}–{ntwk.f[-1]/1e9:.4g} GHz"
        )
        self._redraw()

    def _populate_ports(self) -> None:
        self.cmb_port.blockSignals(True)
        self.cmb_port.clear()
        if self.network is None:
            self.cmb_port.blockSignals(False)
            return
        n = self.network.nports
        names = []
        if n == 1:
            names = ["S11"]
        else:
            for i in range(1, n + 1):
                for j in range(1, n + 1):
                    names.append(f"S{i}{j}")
        self.cmb_port.addItems(names)
        self.cmb_port.blockSignals(False)

    def _on_open(self) -> None:
        start = str(self.network_path.parent if self.network_path else (self.work_dir or Path.cwd()))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open S-parameters",
            start,
            "S-params (*.s1p *.s2p *.s3p *.s4p *.s*p *.json);;All (*.*)",
        )
        if path:
            self.load_path(path)

    def _on_scan(self, auto_load: bool = False) -> None:
        self.file_list.clear()
        if not self.work_dir:
            return
        files = find_latest_sparam_files(self.work_dir)
        for f in files:
            # sub-path as shown text (like the Antenna tab), full path in
            # UserRole + tooltip for loading
            try:
                shown = str(f.relative_to(self.work_dir))
            except ValueError:
                shown = f.name
            item = QListWidgetItem(shown)
            item.setData(Qt.ItemDataRole.UserRole, str(f))
            item.setToolTip(str(f))
            self.file_list.addItem(item)
        if auto_load and files:
            # Newest file first — load so the tab is never blank when data exists.
            self.load_path(files[0])
            if self.file_list.count():
                self.file_list.setCurrentRow(0)

    def _on_file_selected(self, current: QListWidgetItem | None, _prev=None) -> None:
        if current is None:
            return
        raw = current.data(Qt.ItemDataRole.UserRole) or current.text()
        self.load_path(Path(raw))

    def _reload_current(self) -> None:
        if self.network_path:
            self.load_path(self.network_path)
        else:
            self._on_scan()

    # ----- markers / cursor ---------------------------------------------
    @staticmethod
    def _fmt_ohm(z: complex) -> str:
        """Format impedance as 12.3+j45.6 Ω (or −j for capacitive)."""
        re, im = z.real, z.imag
        if abs(im) < 1e-12:
            return f"{re:.3f} Ω"
        sign = "+" if im >= 0 else "−"
        return f"{re:.3f}{sign}j{abs(im):.3f} Ω"

    @staticmethod
    def _fmt_siemens(y: complex) -> str:
        """Format admittance in mS."""
        re, im = y.real * 1e3, y.imag * 1e3
        if abs(im) < 1e-9:
            return f"{re:.3f} mS"
        sign = "+" if im >= 0 else "−"
        return f"{re:.3f}{sign}j{abs(im):.3f} mS"

    @staticmethod
    def _fmt_gamma_polar(g: complex) -> str:
        mag = abs(g)
        ang = math.degrees(math.atan2(g.imag, g.real))
        return f"{mag:.5g} ∠{ang:.2f}°"

    @staticmethod
    def _fmt_s_complex(s: complex) -> str:
        re, im = s.real, s.imag
        if abs(im) < 1e-15:
            return f"{re:.6g}"
        sign = "+" if im >= 0 else "−"
        return f"{re:.5g}{sign}j{abs(im):.5g}"

    @staticmethod
    def _is_reflection_key(key: str) -> bool:
        """True for S11/S22/… diagonal reflection ports."""
        k = (key or "").upper().replace(" ", "")
        return len(k) == 3 and k[0] == "S" and k[1].isdigit() and k[2].isdigit() and k[1] == k[2]

    @staticmethod
    def _s_to_z(s: complex, z0: float = 50.0) -> complex:
        """S11 → complex load impedance Z = Z0*(1+Γ)/(1-Γ)."""
        if abs(1.0 - s) < 1e-12:
            return complex(float("inf"), 0.0)
        return z0 * (1.0 + s) / (1.0 - s)

    @staticmethod
    def _s_to_y(s: complex, z0: float = 50.0) -> complex:
        """S11 → complex load admittance Y = 1/Z."""
        z = SParamViewer._s_to_z(s, z0)
        if z == 0 or not np.isfinite(z.real) or not np.isfinite(z.imag):
            return complex(float("inf"), 0.0)
        return 1.0 / z

    @staticmethod
    def _s_to_vswr(s: complex) -> float:
        mag = abs(s)
        return float((1.0 + mag) / max(1.0 - mag, 1e-12))

    @staticmethod
    def _s_to_rl_db(s: complex) -> float:
        mag = max(abs(s), 1e-15)
        return float(-20.0 * math.log10(mag))

    def _marker_list_text(self, key: str, val: complex, f_ghz: float, z0: float) -> str:
        """List row: all formats combined, or the selected marker style only."""
        mag = abs(val)
        db = 20 * math.log10(max(mag, 1e-15))
        vswr = self._s_to_vswr(val)
        rl = self._s_to_rl_db(val)
        ang = math.degrees(math.atan2(val.imag, val.real))
        refl = self._is_reflection_key(key)
        head = f"{f_ghz:.4f} GHz {key}"
        style = getattr(self, "cmb_marker_style", None)
        style = style.currentText() if style is not None else "All formats"
        if style == "All formats":
            parts = [head, f"{db:.2f} dB", f"VSWR {vswr:.3g}", f"∠{ang:.1f}°"]
            if refl:
                parts.append(f"Z={self._fmt_ohm(self._s_to_z(val, z0))}")
            else:
                parts.append(f"S={self._fmt_s_complex(val)}")
            return "  ".join(parts)
        if style == "dB":
            detail = f"{db:.2f} dB"
        elif style == "|S|":
            detail = f"|S| {mag:.5g}"
        elif style == "Phase":
            detail = f"∠{ang:.1f}°"
        elif style == "S complex":
            detail = self._fmt_s_complex(val)
        elif style == "Γ polar":
            detail = f"Γ={self._fmt_gamma_polar(val)}"
        elif style == "Γ rect":
            detail = f"Γ={self._fmt_s_complex(val)}"
        elif style == "Z":
            detail = f"Z={self._fmt_ohm(self._s_to_z(val, z0))}" if refl else "Z=—"
        elif style == "Y":
            y = self._s_to_y(val, z0)
            detail = f"Y={self._fmt_siemens(y)}" if refl else "Y=—"
        elif style == "VSWR":
            detail = f"VSWR {vswr:.3g}"
        elif style == "RL":
            detail = f"RL {rl:.3f} dB"
        else:
            detail = f"{db:.2f} dB"
        return f"{head}  {detail}"

    def _clear_markers(self) -> None:
        self.markers = []
        self.marker_list.clear()
        self._refresh_markers()
        self._redraw()

    def _goto_min(self) -> None:
        if self.network is None or self.cmb_port.count() == 0:
            return
        key = self.cmb_port.currentText()
        s = self._trace(key)
        if s is None:
            return
        db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
        i = int(np.nanargmin(db))
        f = float(self.network.f[i])
        if f not in self.markers:
            self.markers.append(f)
            self.markers.sort()
        self._refresh_markers()
        self._redraw()
        self._scroll_marker_to(f)

    def _on_click(self, event) -> None:
        if event.button == 3 and event.inaxes is not None:
            pos = self.canvas.mapToGlobal(self.canvas.rect().center())
            self._analysis_dlg.move(pos - self._analysis_dlg.rect().center())
            self._analysis_dlg.show()
            return
        if event.inaxes is None or event.button != 1 or self.network is None:
            return
        if event.xdata is None:
            return
        # Smith chart: xdata is complex-ish (Re); still pick nearest in freq via mouse xy
        f = self._freq_from_event(event)
        if f is None:
            return
        if f not in self.markers:
            self.markers.append(f)
            self.markers.sort()
        self._refresh_markers()
        self._redraw()
        self._scroll_marker_to(f)

    def _on_motion(self, event) -> None:
        if event.inaxes is None or self.network is None:
            self.lbl_info.setText(self.lbl_info.text().split(" | cursor")[0])
            return
        f = self._freq_from_event(event)
        if f is None:
            return
        key = self.cmb_port.currentText()
        s = self._trace(key)
        if s is None:
            return
        idx = int(np.argmin(np.abs(self.network.f - f)))
        val = complex(s[idx])
        mag = abs(val)
        db = 20 * np.log10(max(mag, 1e-15))
        ang = float(np.degrees(np.angle(val)))
        vswr = float((1 + mag) / max(1 - mag, 1e-12))
        z0 = 50.0
        extra = f"Γ={self._fmt_gamma_polar(val)}"
        if self._is_reflection_key(key):
            z = self._s_to_z(val, z0)
            extra += f"  Z={self._fmt_ohm(z)}  VSWR={vswr:.3g}"
        else:
            extra += f"  VSWR={vswr:.3g}"
        base = self.lbl_info.text().split(" | cursor")[0]
        self.lbl_info.setText(
            f"{base} | cursor {self.network.f[idx]/1e9:.4f} GHz {key} "
            f"|S|={mag:.4g} {db:.2f} dB ∠{ang:.1f}° {extra}"
        )
        self._draw_cursor(idx)

    def _on_leave(self, _event) -> None:
        base = self.lbl_info.text().split(" | cursor")[0]
        self.lbl_info.setText(base)
        if self._cursor_line is not None:
            try:
                self._cursor_line.remove()
            except Exception:  # noqa: BLE001
                pass
            self._cursor_line = None
        if self._cursor_text is not None:
            try:
                self._cursor_text.remove()
            except Exception:  # noqa: BLE001
                pass
            self._cursor_text = None
        self.canvas.draw_idle()

    def _freq_from_event(self, event) -> float | None:
        if self.network is None:
            return None
        plot = self.cmb_plot.currentText()
        f = self.network.f
        if plot == "Smith":
            # nearest frequency by complex plane distance to click
            # event.xdata/ydata are Re/Im of Gamma when using smith axes
            if event.xdata is None or event.ydata is None:
                return None
            key = self.cmb_port.currentText()
            s = self._trace(key)
            if s is None:
                return None
            d = np.hypot(np.real(s) - event.xdata, np.imag(s) - event.ydata)
            return float(f[int(np.nanargmin(d))])
        if event.xdata is None:
            return None
        # cartesian: x in GHz
        f_click = event.xdata * 1e9
        return float(f[int(np.argmin(np.abs(f - f_click)))])

    def _scroll_marker_to(self, f: float) -> None:
        for i in range(self.marker_list.count()):
            item = self.marker_list.item(i)
            if item and abs(float(item.data(Qt.ItemDataRole.UserRole)) - f) < 1.0:
                self.marker_list.setCurrentRow(i)
                return

    def _trace(self, key: str) -> np.ndarray | None:
        if self.network is None:
            return None
        try:
            if len(key) == 3 and key[0] == "S" and key[1].isdigit() and key[2].isdigit():
                i = int(key[1]) - 1
                j = int(key[2]) - 1
                s = self.network.s
                if s.ndim == 3 and s.shape[1] > i and s.shape[2] > j:
                    return np.asarray(s[:, i, j], dtype=complex)
            return np.asarray(self.network[key].s[:, 0, 0], dtype=complex)
        except Exception:  # noqa: BLE001
            return None

    def _z0_from_network(self) -> float:
        try:
            if self.network is not None and getattr(self.network, "z0", None) is not None:
                raw = np.asarray(self.network.z0)
                if np.iscomplexobj(raw):
                    raw = raw.real
                z0 = raw.astype(float)
                if z0.size:
                    val = float(np.ravel(z0)[0])
                    if math.isfinite(val) and val > 0:
                        return val
        except Exception:  # noqa: BLE001
            pass
        return 50.0

    def _on_marker_style_changed(self, *_args) -> None:
        self._refresh_markers()  # list rows follow the selected style

    def _refresh_markers(self) -> None:
        self.marker_list.clear()
        if self.network is None:
            return
        key = self.cmb_port.currentText() if self.cmb_port.count() else "S11"
        s = self._trace(key)
        if s is None:
            return
        z0 = self._z0_from_network()
        self._marker_z0 = z0
        for f in self.markers:
            idx = int(np.argmin(np.abs(self.network.f - f)))
            val = complex(s[idx]) if idx < len(s) else complex("nan")
            f_ghz = float(self.network.f[idx]) / 1e9
            self.marker_list.addItem(
                QListWidgetItem(self._marker_list_text(key, val, f_ghz, z0))
            )
            self.marker_list.item(self.marker_list.count() - 1).setData(
                Qt.ItemDataRole.UserRole, float(self.network.f[idx])
            )

    def _on_marker_context_menu(self, pos) -> None:
        """Right-click a marker row to delete it."""
        item = self.marker_list.itemAt(pos)
        if item is None:
            return
        self.marker_list.setCurrentItem(item)
        menu = QMenu(self.marker_list)
        act_del = menu.addAction("Delete marker")
        chosen = menu.exec(self.marker_list.viewport().mapToGlobal(pos))
        if chosen is act_del:
            self._delete_marker_at(self.marker_list.row(item))

    def _delete_marker_at(self, row: int) -> None:
        """Remove marker `row` (index in markers == row in marker_list)."""
        if 0 <= row < len(self.markers):
            del self.markers[row]
            self._refresh_markers()
            self._redraw()

    # ----- pop-out graph window ------------------------------------------
    @property
    def graph_is_popped_out(self) -> bool:
        return self._graph_win is not None

    def popout_graph(self) -> None:
        """Open the plot panel (figure/toolbar) in a separate window."""
        if self._graph_win is not None:
            # toggle: second click restores the graph to the tab
            self.restore_graph_to_tab()
            return
        win = SParamGraphWindow(self)
        self._graph_win = win
        win.show()
        win.raise_()
        win.activateWindow()
        self._redraw()
        # close the analysis dialog once the graph is popped out
        self._analysis_dlg.close()

    def restore_graph_to_tab(self) -> None:
        """Move the plot panel back into the S-Params tab splitter."""
        win = self._graph_win
        if win is None:
            return
        self._graph_win = None
        win.viewer = None  # type: ignore[assignment]
        try:
            win.close()
        except Exception:  # noqa: BLE001
            pass
        right = self.right_panel
        right.setParent(None)
        self._plot_split.addWidget(right)
        self._plot_split.setSizes([280, 720])
        self.btn_popout.setText("Pop out graph")
        self._redraw()

    def _on_graph_popout_closed(self) -> None:
        if self._graph_win is None:
            return
        win = self._graph_win
        self._graph_win = None
        win.viewer = None  # type: ignore[assignment]
        right = self.right_panel
        right.setParent(None)
        self._plot_split.addWidget(right)
        self._plot_split.setSizes([280, 720])
        self.btn_popout.setText("Pop out graph")
        self._redraw()

    # ----- plotting -----------------------------------------------------
    def _redraw(self) -> None:
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        self._cursor_line = None
        self._cursor_text = None
        self._marker_artists = []
        if self.network is None:
            ax.text(0.5, 0.5, "Load Touchstone or JSON", ha="center", va="center")
            self.canvas.draw_idle()
            return

        plot = self.cmb_plot.currentText()
        key = self.cmb_port.currentText() if self.cmb_port.count() else "S11"
        s = self._trace(key)
        if s is None:
            ax.text(0.5, 0.5, f"No data for {key}", ha="center", va="center")
            self.canvas.draw_idle()
            return

        f_ghz = self.network.f / 1e9
        z0 = self._z0_from_network()
        self._marker_z0 = z0
        if plot == "Smith":
            self._draw_smith(ax, s, key)
        elif plot == "|S| dB":
            db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
            ax.plot(f_ghz, db, lw=1.4, label=key)
            ax.set_ylabel("|S| [dB]")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best")
        elif plot == "|S| mag":
            ax.plot(f_ghz, np.abs(s), lw=1.4, label=key)
            ax.set_ylabel("|S|")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best")
        elif plot == "Phase deg":
            ax.plot(f_ghz, np.degrees(np.angle(s)), lw=1.4, label=key)
            ax.set_ylabel("Phase [deg]")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best")
        elif plot == "VSWR":
            mag = np.abs(s)
            vswr = (1 + mag) / np.maximum(1 - mag, 1e-12)
            ax.plot(f_ghz, vswr, lw=1.4, label=key)
            ax.set_ylabel("VSWR")
            ax.set_ylim(bottom=1.0)
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best")
        elif plot == "Complex Re/Im":
            ax.plot(f_ghz, np.real(s), lw=1.3, label=f"{key} Re")
            ax.plot(f_ghz, np.imag(s), lw=1.3, label=f"{key} Im")
            ax.set_ylabel("S (linear)")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best")
        elif plot == "Re/Im + Phase":
            # Real + imaginary parts on the left axis, phase on the right one
            # (two different scales, so a twin axis instead of a second plot).
            ax.plot(f_ghz, np.real(s), lw=1.3, label=f"{key} Re")
            ax.plot(f_ghz, np.imag(s), lw=1.3, label=f"{key} Im")
            ax.set_ylabel("S Re/Im (linear)")
            ax.grid(True, alpha=0.3)
            ax_ph = ax.twinx()
            ax_ph.plot(
                f_ghz,
                np.degrees(np.angle(s)),
                lw=1.2,
                ls="--",
                color="tab:green",
                label=f"{key} Phase",
            )
            ax_ph.set_ylabel("Phase [deg]", color="tab:green")
            ax_ph.tick_params(axis="y", colors="tab:green")
            handles, labels = ax.get_legend_handles_labels()
            h2, l2 = ax_ph.get_legend_handles_labels()
            ax.legend(handles + h2, labels + l2, loc="best")
        elif plot == "Z Re/Im":
            if self._is_reflection_key(key):
                z = np.array([self._s_to_z(complex(v), z0) for v in s], dtype=complex)
                ax.plot(f_ghz, np.real(z), lw=1.3, label="R")
                ax.plot(f_ghz, np.imag(z), lw=1.3, label="X")
                ax.set_ylabel("Z [Ω]")
            else:
                ax.plot(f_ghz, np.real(s), lw=1.3, label="S Re")
                ax.plot(f_ghz, np.imag(s), lw=1.3, label="S Im")
                ax.set_ylabel("S (Z needs Sii)")
            ax.grid(True, alpha=0.3)
            ax.legend(loc="best")

        if plot != "Smith":
            ax.set_xlabel("Frequency [GHz]")
            # markers as vertical cursors
            for f in self.markers:
                x = f / 1e9
                artist = ax.axvline(x, color="crimson", ls="--", lw=1.0, alpha=0.85)
                self._marker_artists.append(artist)
        else:
            self._draw_smith_markers(ax, s, key, z0)

        self.canvas.draw_idle()
        self._refresh_markers()

    def _draw_smith_markers(self, ax, s: np.ndarray, key: str, z0: float) -> None:
        """Marker dots + optional Z labels on the Smith chart."""
        if not self.markers or self.network is None:
            return
        refl = self._is_reflection_key(key)
        for f in self.markers:
            idx = int(np.argmin(np.abs(self.network.f - f)))
            val = complex(s[idx])
            re, im = float(np.real(val)), float(np.imag(val))
            artist = ax.plot(
                re,
                im,
                "o",
                color="crimson",
                ms=6,
                zorder=6,
            )[0]
            self._marker_artists.append(artist)
            label = f"{self.network.f[idx]/1e9:.3f}"
            if refl:
                z = self._s_to_z(val, z0)
                if math.isfinite(z.real) and math.isfinite(z.imag):
                    label += f"\\n{z.real:.1f}+j{z.imag:.1f}"
                else:
                    label += "\\n∞"
            else:
                label += f"\\n{abs(val):.3g}"
            # Keep labels readable: only annotate first few + last
            n_markers = len(self.markers)
            if idx == int(np.argmin(np.abs(self.network.f - self.markers[0]))) or n_markers <= 4:
                txt = ax.text(
                    re + 0.04,
                    im + 0.04,
                    label,
                    fontsize=7,
                    color="darkred",
                    zorder=7,
                    bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="crimson", alpha=0.85),
                )
                self._marker_artists.append(txt)

    def _draw_smith(self, ax, s: np.ndarray, key: str) -> None:
        """Full scikit-rf Smith chart (impedance + admittance grid, labels, VSWR)."""
        drawn = False
        try:
            import skrf.plotting as rfplot

            # Signature (skrf 1.4): smithR, chart_type, draw_labels, border, ax, ref_imm, draw_vswr
            rfplot.smith(
                ax=ax,
                smithR=1.0,
                chart_type="zy",
                draw_labels=True,
                border=True,
                ref_imm=1.0,
                draw_vswr=[1.2, 1.5, 2.0, 3.0, 5.0],
            )
            drawn = True
        except Exception as e:  # noqa: BLE001
            print("skrf.plotting.smith failed:", e)

        if not drawn:
            # last-resort geometric fallback (should not happen with skrf installed)
            th = np.linspace(0, 2 * np.pi, 361)
            ax.plot(np.cos(th), np.sin(th), "k-", lw=0.8)
            for r in (0.5, 0.25):
                ax.plot(np.cos(th) * r, np.sin(th) * r, ":", color="gray", lw=0.6)
            ax.axhline(0, color="gray", lw=0.5)
            ax.axvline(0, color="gray", lw=0.5)
            ax.set_xlim(-1.05, 1.05)
            ax.set_ylim(-1.05, 1.05)
            ax.set_aspect("equal")

        # Trace + start/end (skrf Network.plot_s_smith style)
        re, im = np.real(s), np.imag(s)
        ax.plot(re, im, "-", color="tab:blue", lw=1.6, zorder=4, label=key)
        ax.plot(re[0], im[0], "g+", ms=10, mew=2, zorder=5, label="start")
        ax.plot(re[-1], im[-1], "ks", ms=6, zorder=5, label="end")
        # frequency-colored dots every N points for readability
        n = len(s)
        step = max(1, n // 25)
        ax.scatter(re[::step], im[::step], c=self.network.f[::step] / 1e9, cmap="viridis", s=12, zorder=4)
        z0 = getattr(self, "_marker_z0", self._z0_from_network())
        ax.set_title(f"Smith chart (scikit-rf) — {key}  [Z0={z0:g} Ω]")
        ax.set_xlabel("Re(Γ)")
        ax.set_ylabel("Im(Γ)")
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(-1.05, 1.05)
        ax.set_ylim(-1.05, 1.05)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
        ax.grid(False)

    def _draw_cursor(self, idx: int) -> None:
        if self.network is None:
            return
        plot = self.cmb_plot.currentText()
        key = self.cmb_port.currentText()
        s = self._trace(key)
        if s is None:
            return
        ax = self.figure.axes[0] if self.figure.axes else None
        if ax is None:
            return
        # remove previous
        if self._cursor_line is not None:
            try:
                self._cursor_line.remove()
            except Exception:  # noqa: BLE001
                pass
            self._cursor_line = None
        if self._cursor_text is not None:
            try:
                self._cursor_text.remove()
            except Exception:  # noqa: BLE001
                pass
            self._cursor_text = None

        val = complex(s[idx])
        mag = abs(val)
        db = 20 * np.log10(max(mag, 1e-15))
        ang = float(np.degrees(np.angle(val)))
        if plot == "Smith":
            self._cursor_line = ax.plot(
                np.real(val), np.imag(val), "o", color="darkorange", ms=7, zorder=6
            )[0]
        else:
            x = self.network.f[idx] / 1e9
            if plot == "|S| dB":
                y = db
            elif plot == "|S| mag":
                y = mag
            elif plot == "Phase deg":
                y = ang
            elif plot == "VSWR":
                y = (1 + mag) / max(1 - mag, 1e-12)
            else:
                y = np.real(val)
            self._cursor_line = ax.axvline(x, color="darkorange", lw=1.0, alpha=0.9)
            self._cursor_text = ax.annotate(
                f"{self.network.f[idx]/1e9:.4f} GHz\n{db:.2f} dB",
                xy=(x, y),
                xytext=(8, 8),
                textcoords="offset points",
                fontsize=8,
                bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="darkorange", alpha=0.9),
            )
        self.canvas.draw_idle()
