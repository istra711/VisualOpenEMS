"""Interactive antenna pattern viewer: polar cuts, gain vs frequency, 3D pattern.

Reads ``nf2ff/nf2ff.json`` (written by :mod:`app.postproc.nf2ff`). Follows the
``SParamViewer`` pattern: file discovery under ``work/*/simulation``, a left
file/info panel, and a matplotlib QtAgg plot panel that can be popped out.
"""

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
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import matplotlib

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MultipleLocator


def half_power_beamwidth(
    ang_deg: np.ndarray,
    db: np.ndarray,
    level_db: float = -3.0,
) -> dict | None:
    """Half-power beamwidth (opening angle) of the main lobe in a 1D cut.

    ``ang_deg`` / ``db`` are one polar cut (degrees, dBi). Walks from the peak
    to the first ``level_db`` (default −3 dB) crossing on each side and
    interpolates linearly. Returns None if the peak never falls below the
    threshold (omnidirectional / full circle).
    """
    ang = np.asarray(ang_deg, dtype=float).reshape(-1)
    vals = np.asarray(db, dtype=float).reshape(-1)
    n = min(ang.size, vals.size)
    if n < 3:
        return None
    ang, vals = ang[:n], vals[:n]
    finite = np.isfinite(vals)
    if not np.any(finite):
        return None
    work = np.where(finite, vals, -np.inf)
    i_pk = int(np.nanargmax(work))
    peak = float(work[i_pk])
    thr = peak + float(level_db)

    def _cross(i0: int, step: int) -> float | None:
        i = i0
        for _ in range(n):
            j = i + step
            if j < 0 or j >= n:
                return None
            a, b = work[i], work[j]
            if a >= thr > b:
                t = (thr - a) / (b - a) if b != a else 0.0
                return float(ang[i] + t * (ang[j] - ang[i]))
            i = j
        return None

    left = _cross(i_pk, -1)
    right = _cross(i_pk, +1)
    if left is None or right is None:
        return None
    width = right - left
    if width < 0:
        width += 360.0
    return {
        "width_deg": float(width),
        "left_deg": float(left),
        "right_deg": float(right),
        "peak_deg": float(ang[i_pk]),
        "peak_dbi": peak,
        "level_db": float(level_db),
        "threshold_dbi": thr,
    }


def front_to_back_ratio(ang_deg: np.ndarray, db: np.ndarray) -> dict | None:
    """VRV (front-to-back ratio) from a 1D gain/directivity cut.

    Peak of the cut vs the sample at peak + 180°. Both values come from the
    antenna pattern only (no S-parameters). Result in dB and as linear ratio.
    """
    ang = np.asarray(ang_deg, dtype=float).reshape(-1)
    vals = np.asarray(db, dtype=float).reshape(-1)
    n = min(ang.size, vals.size)
    if n < 3:
        return None
    ang, vals = ang[:n], vals[:n]
    work = np.where(np.isfinite(vals), vals, -np.inf)
    if not np.any(np.isfinite(work)) or float(np.max(work)) == -np.inf:
        return None
    i_pk = int(np.nanargmax(work))
    peak = float(work[i_pk])
    peak_ang = float(ang[i_pk]) % 360.0
    back_ang = (peak_ang + 180.0) % 360.0
    wrap = (ang - back_ang + 180.0) % 360.0 - 180.0
    # interpolate between the two samples bracketing the antipodal angle
    order = np.argsort(ang)
    a_s, v_s = ang[order], work[order]
    # map back_ang into the sorted angle span (usually 0..360)
    a_ext = np.concatenate([a_s, [a_s[0] + 360.0]])
    v_ext = np.concatenate([v_s, [v_s[0]]])
    target = back_ang
    if target < a_ext[0]:
        target += 360.0
    j = int(np.searchsorted(a_ext, target))
    j = max(1, min(j, a_ext.size - 1))
    a0, a1 = a_ext[j - 1], a_ext[j]
    v0, v1 = v_ext[j - 1], v_ext[j]
    t = 0.0 if a1 == a0 else (target - a0) / (a1 - a0)
    back = float(v0 + t * (v1 - v0))
    if not np.isfinite(back):
        back = float(work[int(np.argmin(np.abs(wrap)))])
    vrv_db = peak - back
    ratio = float(10.0 ** (vrv_db / 10.0))
    return {
        "vrv_db": float(vrv_db),
        "ratio": ratio,
        "peak_deg": peak_ang,
        "peak_dbi": peak,
        "back_deg": float(back_ang),
        "back_dbi": back,
    }


def find_latest_nf2ff_files(work_dir: Path) -> list[Path]:
    """Discover ``nf2ff/nf2ff.json`` (new project layout) or the legacy
    ``*/simulation/nf2ff/nf2ff.json``, newest first."""
    out: list[Path] = []
    if not work_dir or not Path(work_dir).exists():
        return out
    root = Path(work_dir)
    candidates = [root / "nf2ff" / "nf2ff.json"]
    # new layout: <work>/<project>/nf2ff/nf2ff.json
    candidates += [d / "nf2ff.json" for d in sorted(root.glob("*/nf2ff"))]
    # legacy layout: <work>/<project>/simulation/nf2ff/nf2ff.json
    candidates += [d / "nf2ff" / "nf2ff.json" for d in sorted(root.glob("*/simulation"))]
    for p in candidates:
        if p.exists():
            out.append(p)
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def load_nf2ff_json(path: Path | str) -> dict:
    """Load an nf2ff.json into numpy arrays. Returns a dict with keys:

    ``frequencies`` (Hz), ``theta_deg``, ``phi_deg`` (degrees),
    ``D_dBi``/``gain_dBi`` (``[n_freq, n_theta, n_phi]`` or ``None``),
    ``Prad``, ``Dmax_dBi``, ``efficiency``, and the loaded ``raw`` payload.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    data = json.loads(path.read_text(encoding="utf-8"))

    def _arr(key):
        val = data.get(key)
        if val is None:
            return None
        try:
            return np.asarray(val, dtype=float)
        except Exception:  # noqa: BLE001
            return None

    return {
        "frequencies": np.asarray(data.get("frequencies", []), dtype=float),
        "theta_deg": _arr("theta_deg"),
        "phi_deg": _arr("phi_deg"),
        "D_dBi": _arr("D_dBi"),
        "gain_dBi": _arr("gain_dBi"),
        "Prad": _arr("Prad"),
        "Dmax_dBi": _arr("Dmax_dBi"),
        "efficiency": np.asarray(
            [x if x is not None else np.nan for x in (data.get("efficiency") or [])],
            dtype=float,
        )
        if data.get("efficiency") is not None
        else None,
        "raw": data,
    }


class AntennaGraphWindow(QMainWindow):
    """Standalone pop-out window hosting the antenna plot panel."""

    def __init__(self, viewer: "AntennaPatternViewer") -> None:
        super().__init__(None)
        self.viewer: AntennaPatternViewer | None = viewer
        self.setWindowTitle("Antenna Pattern — pop-out")
        self.resize(900, 720)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)

        bar = QHBoxLayout()
        self.lbl_title = QLabel("Pop-out: antenna pattern")
        self.lbl_title.setStyleSheet("font-weight: bold;")
        bar.addWidget(self.lbl_title)
        bar.addStretch(1)
        bar.addWidget(QLabel("Plot"))
        self.cmb_plot = QComboBox()
        self.cmb_plot.addItems(AntennaPatternViewer.PLOT_TYPES)
        bar.addWidget(self.cmb_plot)
        self.lbl_cmap = QLabel("cmap")
        self.cmb_cmap = QComboBox()
        self.cmb_cmap.addItems(list(AntennaPatternViewer.CMAP_CHOICES))
        self.cmb_cmap.setToolTip("Colormap of the 3D pattern (colorbar follows)")
        bar.addWidget(self.lbl_cmap)
        bar.addWidget(self.cmb_cmap)
        layout.addLayout(bar)

        self.lbl_info = QLabel("")
        self.lbl_info.setWordWrap(True)
        layout.addWidget(self.lbl_info)

        if viewer is not None:
            right = viewer.right_panel
            right.setParent(None)
            layout.addWidget(right, 1)
            self.cmb_plot.blockSignals(True)
            self.cmb_plot.setCurrentText(viewer.cmb_plot.currentText())
            self.cmb_plot.blockSignals(False)
            self.cmb_cmap.blockSignals(True)
            self.cmb_cmap.setCurrentText(viewer.cmb_cmap.currentText())
            self.cmb_cmap.blockSignals(False)
            is_3d = viewer.cmb_plot.currentText() == "3D Pattern"
            self.lbl_cmap.setVisible(is_3d)
            self.cmb_cmap.setVisible(is_3d)
            self.lbl_info.setText(viewer.lbl_info.text())
            viewer.btn_popout.setText("Graph popped out")

        self.setCentralWidget(container)

        self.cmb_plot.currentTextChanged.connect(self._on_plot_changed)
        self.cmb_cmap.currentTextChanged.connect(self._on_cmap_changed)

    def _on_plot_changed(self, text: str) -> None:
        v = self.viewer
        if v is None:
            return
        v.cmb_plot.blockSignals(True)
        v.cmb_plot.setCurrentText(text)
        v.cmb_plot.blockSignals(False)
        v._redraw()

    def _on_cmap_changed(self, text: str) -> None:
        v = self.viewer
        if v is None:
            return
        v.cmb_cmap.blockSignals(True)
        v.cmb_cmap.setCurrentText(text)
        v.cmb_cmap.blockSignals(False)
        v._redraw()

    def closeEvent(self, event) -> None:  # noqa: N802
        v = self.viewer
        self.viewer = None
        if v is not None and v._graph_win is self:
            v._on_graph_popout_closed()
        super().closeEvent(event)


class AntennaPatternViewer(QWidget):
    """Browse NF2FF antenna patterns: polar cuts, gain vs frequency, 3D pattern."""

    PLOT_TYPES = [
        "E-Plane (phi=90)",
        "H-Plane (phi=0)",
        "Azimuth (theta=90)",
        "Gain vs Frequency",
        "Radiated Efficiency vs Frequency",
        "3D Pattern",
    ]

    THREE_D_FIELDS = ("Gain", "Dmax")

    CMAP_CHOICES = ("viridis", "plasma", "inferno", "turbo", "jet", "coolwarm", "gray")

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = Path(work_dir) if work_dir else None
        self.data: dict | None = None
        self.data_path: Path | None = None
        # click markers: {"plot","kind","x","y","label","xunit","yunit"}
        self.markers: list[dict] = []
        self._marker_cfg: dict | None = None
        self._graph_win: AntennaGraphWindow | None = None
        # last HPBW / VRV results (per cut name), drawn on polar plots
        self._hpbw: dict | None = None
        self._vrv: dict | None = None
        self._plot_rmin_eff = 0.0
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.btn_open = QPushButton("Open nf2ff.json…")
        self.btn_reload = QPushButton("Reload")
        self.btn_scan = QPushButton("Scan work dir")
        self.cmb_plot = QComboBox()
        self.cmb_plot.addItems(self.PLOT_TYPES)
        self.cmb_freq = QComboBox()
        # polar analysis dialog (right-click on polar plot)
        self._radial_dlg = self._build_radial_dialog()
        self.lbl_cmap = QLabel("cmap")
        self.cmb_cmap = QComboBox()
        self.cmb_cmap.addItems(list(self.CMAP_CHOICES))
        self.cmb_cmap.setToolTip("Colormap of the 3D pattern (colorbar follows)")
        self.lbl_info = QLabel("No data loaded")
        for w in (
            self.btn_open,
            self.btn_reload,
            self.btn_scan,
        ):
            bar.addWidget(w)
        bar.addWidget(QLabel("Plot"))
        bar.addWidget(self.cmb_plot)
        bar.addWidget(QLabel("Freq"))
        bar.addWidget(self.cmb_freq)
        bar.addWidget(self.lbl_cmap)
        bar.addWidget(self.cmb_cmap)
        bar.addStretch(1)
        root.addLayout(bar)

        self._plot_split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self._plot_split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Loaded files"))
        self.file_list = QListWidget()
        lv.addWidget(self.file_list)
        self.lbl_detail = QLabel("")
        self.lbl_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_detail.setWordWrap(True)
        self.lbl_detail_title = QLabel("Details")
        lv.addWidget(self.lbl_detail_title)
        lv.addWidget(self.lbl_detail)
        lv.addWidget(QLabel("Markers (click plot to add)"))
        self.marker_list = QListWidget()
        self.marker_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.marker_list.customContextMenuRequested.connect(self._on_marker_context_menu)
        lv.addWidget(self.marker_list)
        lv.addStretch(1)
        self.left_panel = left
        self._plot_split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        self.figure = Figure(figsize=(7, 6), tight_layout=False)
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        # 3D controls live inside the plot panel's toolbar (right of the save
        # icon) so the pop-out window — which reparents this panel — shows
        # them without any sync code. They sit in a single container widget
        # with layout spacing, otherwise the row is far too tight.
        self.lbl_3d_field = QLabel("3D field")
        self.cmb_3d_field = QComboBox()
        self.cmb_3d_field.addItems(list(self.THREE_D_FIELDS))
        # widest item ("Dmax") must fit incl. the drop-down arrow
        self.cmb_3d_field.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        self.cmb_3d_field.setMinimumWidth(80)
        self.lbl_dbi_win = QLabel("dBi min")
        self.slider_dbi_min = QSlider(Qt.Orientation.Horizontal)
        self.slider_dbi_min.setMinimumWidth(110)
        self.slider_dbi_min.setToolTip(
            "Lower dBi bound of the 3D radius/color window (max = pattern max)"
        )
        self.lbl_dbi_min = QLabel("-")
        self.lbl_dbi_min.setMinimumWidth(42)
        self.toolbar.addSeparator()
        self.dbi_controls = QWidget()
        dv = QHBoxLayout(self.dbi_controls)
        dv.setContentsMargins(6, 0, 6, 0)
        dv.setSpacing(10)
        for w in (
            self.lbl_3d_field,
            self.cmb_3d_field,
            self.lbl_dbi_win,
            self.slider_dbi_min,
            self.lbl_dbi_min,
        ):
            dv.addWidget(w)
        self.toolbar.addWidget(self.dbi_controls)
        rv.addWidget(self.toolbar)
        rv.addWidget(self.canvas)
        self.right_panel = right
        self._plot_split.addWidget(right)
        self._plot_split.setSizes([260, 740])

        self.btn_open.clicked.connect(self._on_open)
        self.btn_reload.clicked.connect(self._reload_current)
        self.btn_scan.clicked.connect(self._on_scan)
        self.btn_popout.clicked.connect(self.popout_graph)
        self.btn_clear_markers.clicked.connect(self._clear_markers)
        self.btn_hpbw.clicked.connect(self._on_hpbw)
        self.btn_vrv.clicked.connect(self._on_vrv)
        self.cmb_plot.currentTextChanged.connect(lambda *_: self._redraw())
        self.cmb_plot.currentIndexChanged.connect(self._redraw)
        self.cmb_freq.currentIndexChanged.connect(self._redraw)
        self.cmb_freq.currentIndexChanged.connect(lambda *_: self._update_details())
        self.spn_rmin.valueChanged.connect(self._redraw)
        self.spn_rmax.valueChanged.connect(self._redraw)
        self.spn_rstep.valueChanged.connect(self._redraw)
        self.spn_lblpos.valueChanged.connect(self._redraw)
        self._radial_dlg.accepted.connect(self._redraw)
        self.slider_dbi_min.valueChanged.connect(self._on_dbi_slider)
        self.cmb_3d_field.currentTextChanged.connect(self._on_3d_field_changed)
        self.cmb_cmap.currentTextChanged.connect(self._on_cmap_changed)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        self.canvas.mpl_connect("button_press_event", self._on_click)
        self.canvas.setToolTip(
            "Right-click on a polar plot to open the analysis menu\n"
            "(r min / r max / r step / label offset / HPBW / VRV)."
        )
        self._dbi_data_min = -1.0
        self._dbi_data_max = 1.0
        self._set_dbi_controls_visible(False)

        if self.work_dir:
            self._on_scan()

    # ----- polar analysis dialog (right-click) ------------------------------
    def _build_radial_dialog(self) -> QDialog:
        # parent=None: floats above both the tab and the pop-out window
        dlg = QDialog(None)
        dlg.setWindowTitle("Polar analysis")
        form = QFormLayout(dlg)
        self.lbl_rmin = QLabel("r min")
        self.spn_rmin = QDoubleSpinBox()
        self.spn_rmin.setRange(-200.0, 200.0)
        self.spn_rmin.setDecimals(1)
        self.spn_rmin.setSingleStep(1.0)
        self.spn_rmin.setValue(-25.0)
        self.spn_rmin.setSpecialValueText("auto")
        self.spn_rmin.setToolTip(
            "Inner ring (floor) of the polar plot in dBi.\n"
            "Negative values allowed; 0 = auto (floor at 0 dB)."
        )
        self.lbl_rmax = QLabel("r max")
        self.spn_rmax = QDoubleSpinBox()
        self.spn_rmax.setRange(0.0, 200.0)
        self.spn_rmax.setDecimals(1)
        self.spn_rmax.setSingleStep(0.5)
        self.spn_rmax.setValue(0.0)
        self.spn_rmax.setSpecialValueText("auto")
        self.spn_rmax.setToolTip(
            "Outer ring of the polar plot in dBi.\n"
            "0 = auto (rounded up to a nice value)."
        )
        self.lbl_rstep = QLabel("r step")
        self.spn_rstep = QDoubleSpinBox()
        self.spn_rstep.setRange(0.0, 50.0)
        self.spn_rstep.setDecimals(1)
        self.spn_rstep.setSingleStep(0.5)
        self.spn_rstep.setValue(0.0)
        self.spn_rstep.setSpecialValueText("auto")
        self.spn_rstep.setToolTip(
            "Spacing of the polar radial (dB) rings.\n"
            "0 = auto."
        )
        self.lbl_lblpos = QLabel("label offset")
        self.spn_lblpos = QDoubleSpinBox()
        self.spn_lblpos.setRange(0.0, 2.0)
        self.spn_lblpos.setDecimals(2)
        self.spn_lblpos.setSingleStep(0.05)
        self.spn_lblpos.setValue(0.42)
        self.spn_lblpos.setToolTip(
            "How far outside the outer ring the HPBW/VRV\n"
            "labels are placed (fraction of r max)."
        )
        self.btn_hpbw = QPushButton("Opening angle (HPBW)")
        self.btn_hpbw.setCheckable(True)
        self.btn_hpbw.setToolTip(
            "Half-power beamwidth of the current polar cut:\n"
            "angular width of the main lobe at −3 dB below the peak.\n"
            "Uses only the NF2FF gain/directivity pattern (no S-params)."
        )
        self.btn_vrv = QPushButton("VRV (F/B)")
        self.btn_vrv.setCheckable(True)
        self.btn_vrv.setToolTip(
            "Front-to-back ratio (Vorwärts-Rückwärts-Verhältnis):\n"
            "Gmax − gain at Gmax+180° of the current polar cut, in dB and linear.\n"
            "Uses only the NF2FF gain/directivity pattern (no S-params)."
        )
        form.addRow(self.lbl_rmin, self.spn_rmin)
        form.addRow(self.lbl_rmax, self.spn_rmax)
        form.addRow(self.lbl_rstep, self.spn_rstep)
        form.addRow(self.lbl_lblpos, self.spn_lblpos)
        form.addRow(self.btn_hpbw)
        form.addRow(self.btn_vrv)
        self.btn_clear_markers = QPushButton("Clear markers")
        form.addRow(self.btn_clear_markers)
        self.btn_popout = QPushButton("Pop out graph")
        self.btn_popout.setToolTip("Open the pattern plot in a separate window")
        form.addRow(self.btn_popout)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        form.addRow(buttons)
        dlg.setModal(False)
        dlg.setWindowModality(Qt.WindowModality.NonModal)
        # float above pop-out without stealing focus / hiding it
        dlg.setWindowFlag(Qt.WindowType.Tool, True)
        dlg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        return dlg

    # ----- 3D dBi window (slider) + field selection ----------------------
    def _set_dbi_controls_visible(self, visible: bool) -> None:
        self.dbi_controls.setVisible(visible)
        for w in (
            self.lbl_3d_field,
            self.cmb_3d_field,
            self.lbl_dbi_win,
            self.slider_dbi_min,
            self.lbl_dbi_min,
            self.lbl_cmap,
            self.cmb_cmap,
        ):
            w.setVisible(visible)
        # mirror the cmap visibility into an open pop-out window
        gw = getattr(self, "_graph_win", None)
        if gw is not None and hasattr(gw, "cmb_cmap"):
            gw.lbl_cmap.setVisible(visible)
            gw.cmb_cmap.setVisible(visible)

    def _update_dbi_labels(self) -> None:
        self.lbl_dbi_min.setText(f"{self.slider_dbi_min.value() / 10.0:.1f}")

    def _on_dbi_slider(self, _val: int) -> None:
        self._update_dbi_labels()
        plot = self.cmb_plot.currentText()
        if plot == "3D Pattern":
            self._redraw()

    def _on_3d_field_changed(self, *_args: object) -> None:
        self._init_dbi_sliders()
        plot = self.cmb_plot.currentText()
        if plot == "3D Pattern":
            self._redraw()

    def _on_cmap_changed(self, *_args: object) -> None:
        # mirror into an open pop-out window (always, independent of 3D state)
        gw = getattr(self, "_graph_win", None)
        if gw is not None and hasattr(gw, "cmb_cmap"):
            if gw.cmb_cmap.currentText() != self.cmb_cmap.currentText():
                gw.cmb_cmap.blockSignals(True)
                gw.cmb_cmap.setCurrentText(self.cmb_cmap.currentText())
                gw.cmb_cmap.blockSignals(False)
        plot = self.cmb_plot.currentText()
        if plot == "3D Pattern":
            self._redraw()

    def _selected_3d_field(self) -> str:
        """Key of the pattern shown in the 3D plot (combo selection + fallback)."""
        want_d = self.cmb_3d_field.currentText() == "Dmax"
        primary = "D_dBi" if want_d else "gain_dBi"
        secondary = "gain_dBi" if want_d else "D_dBi"
        if self.data and self.data.get(primary) is not None:
            return primary
        if self.data and self.data.get(secondary) is not None:
            return secondary
        return primary

    def _dbi_window(self) -> tuple[float, float]:
        """Absolute dBi window: slider min .. pattern max of the selected field."""
        lo = self.slider_dbi_min.value() / 10.0
        hi = self._dbi_data_max
        if not np.isfinite(lo) or not np.isfinite(hi) or hi - lo < 0.1:
            lo, hi = self._dbi_data_min, self._dbi_data_max
            if hi - lo < 0.1:
                lo, hi = lo - 0.5, hi + 0.5
        return lo, hi

    def _init_dbi_sliders(self) -> None:
        """Slider bounds from the selected field's data range (0.1 dBi steps)."""
        arr = None
        if self.data:
            key = self._selected_3d_field()
            v = self.data.get(key)
            if v is not None:
                arr = np.asarray(v, dtype=float)
        if arr is None or arr.size == 0 or not np.any(np.isfinite(arr)):
            self._dbi_data_min = -1.0
            self._dbi_data_max = 1.0
            self._set_dbi_controls_visible(False)
            return
        self._dbi_data_min = float(np.nanmin(arr))
        self._dbi_data_max = float(np.nanmax(arr))
        lo_i = int(np.floor(self._dbi_data_min * 10.0))
        # keep at least a 0.5 dBi window below the pattern max
        hi_i = max(lo_i + 1, int(np.floor((self._dbi_data_max - 0.5) * 10.0)))
        for s in (self.slider_dbi_min,):
            s.blockSignals(True)
        self.slider_dbi_min.setRange(lo_i, hi_i)
        self.slider_dbi_min.setValue(lo_i)
        self.slider_dbi_min.blockSignals(False)
        self._update_dbi_labels()

    # ----- data loading -------------------------------------------------
    def set_work_dir(self, work_dir: Path) -> None:
        self.work_dir = Path(work_dir)
        self._on_scan()

    def _on_open(self) -> None:
        # start in the folder of the currently loaded file (or the work dir)
        if self.data_path is not None:
            start = str(Path(self.data_path).parent)
        else:
            start = str(self.work_dir) if self.work_dir else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open nf2ff.json", start, "NF2FF JSON (nf2ff.json);;All files (*)"
        )
        if not path:
            return
        self.load_path(Path(path))
        if self.data is None:
            return
        # make sure the opened file shows up in the list and stays selected
        target = str(Path(path))
        self.file_list.blockSignals(True)
        row = next(
            (
                i
                for i in range(self.file_list.count())
                if str(self.file_list.item(i).data(Qt.ItemDataRole.UserRole)) == target
            ),
            -1,
        )
        if row < 0:
            item = QListWidgetItem(Path(path).name)
            item.setData(Qt.ItemDataRole.UserRole, target)
            item.setToolTip(target)
            self.file_list.insertItem(0, item)
            row = 0
        self.file_list.setCurrentRow(row)
        self.file_list.blockSignals(False)

    def _reload_current(self) -> None:
        if self.data_path:
            self.load_path(self.data_path)

    def _on_scan(self, auto_load: bool = False) -> None:
        """Refresh the loaded-files list — without replacing the current data.

        Keeps the currently loaded file selected; with ``auto_load`` and nothing
        loaded yet the newest entry is loaded (first fill).
        """
        current = str(self.data_path) if self.data_path else ""
        self.file_list.blockSignals(True)
        self.file_list.clear()
        row = -1
        if self.work_dir:
            for i, p in enumerate(find_latest_nf2ff_files(self.work_dir)):
                try:
                    shown = str(p.relative_to(self.work_dir))
                except ValueError:
                    shown = p.name
                item = QListWidgetItem(shown)
                item.setData(Qt.ItemDataRole.UserRole, str(p))
                item.setToolTip(str(p))
                self.file_list.addItem(item)
                if current and str(p) == current and row < 0:
                    row = i
        if row < 0 and self.file_list.count():
            row = 0
        if row >= 0:
            self.file_list.setCurrentRow(row)
        self.file_list.blockSignals(False)
        if auto_load and self.data is None and self.file_list.count():
            self._on_file_selected(self.file_list.currentItem())

    def _on_file_selected(self, current, _previous=None) -> None:
        if current is not None:
            raw = current.data(Qt.ItemDataRole.UserRole) or current.text()
            self.load_path(Path(raw))

    def load_path(self, path: Path) -> None:
        self.markers = []
        self._refresh_markers()
        self._hpbw = None
        self._vrv = None
        try:
            self.data = load_nf2ff_json(path)
            self.data_path = Path(path)
        except Exception as e:  # noqa: BLE001
            self.lbl_info.setText(f"Load failed: {e}")
            self.data = None
            self.data_path = None
            self._update_details()
            self._redraw()
            return

        freqs = self.data.get("frequencies")
        self.cmb_freq.blockSignals(True)
        self.cmb_freq.clear()
        if freqs is not None and np.asarray(freqs).size:
            for f in np.asarray(freqs).reshape(-1):
                self.cmb_freq.addItem(f"{f / 1e9:.6g} GHz" if f > 1e6 else f"{f:.6g} Hz")
        self.cmb_freq.blockSignals(False)

        prad = self.data.get("Prad")
        dmax = self.data.get("Dmax_dBi")
        eff = self.data.get("efficiency")
        parts = [f"file: {path.name}"]
        if prad is not None and np.asarray(prad).size:
            parts.append(f"Prad={float(np.asarray(prad).reshape(-1)[0]):.4g} W*s^2")
        if dmax is not None and np.asarray(dmax).size:
            parts.append(f"Dmax={float(np.asarray(dmax).reshape(-1)[0]):.2f} dBi")
        if eff is not None and np.asarray(eff).size and np.isfinite(np.asarray(eff).reshape(-1)[0]):
            parts.append(f"eta={float(np.asarray(eff).reshape(-1)[0]):.3f}")
        self.lbl_info.setText("  ".join(parts))
        self._update_details()
        self._init_dbi_sliders()
        self._redraw()

    def _update_details(self) -> None:
        """Details block: file name in the header + Dmax/Gmax/eta/Prad/theta/phi.

        All per-frequency values follow the Freq combo (selected index).
        """
        if self.data is None:
            self.lbl_detail_title.setText("Details")
            self.lbl_detail.setText("")
            return
        name = self.data_path.name if self.data_path else ""
        self.lbl_detail_title.setText(f"Details ({name})" if name else "Details")
        d = self.data
        i = self._freq_index()

        def _per_freq(key: str) -> float | None:
            v = d.get(key)
            if v is None:
                return None
            a = np.asarray(v, dtype=float).reshape(-1)
            if not a.size:
                return None
            x = float(a[min(i, a.size - 1)])
            return x if np.isfinite(x) else None

        def _peak(key: str) -> float | None:
            v = d.get(key)
            if v is None:
                return None
            a = np.asarray(v, dtype=float)
            if a.ndim >= 3:
                if not a.shape[0]:
                    return None
                x = float(np.nanmax(a[min(i, a.shape[0] - 1)]))
            else:
                r = a.reshape(-1)
                if not r.size:
                    return None
                x = float(r[min(i, r.size - 1)])
            return x if np.isfinite(x) else None

        dmax = _per_freq("Dmax_dBi")
        if dmax is None:
            dmax = _peak("D_dBi")
        eta = _per_freq("efficiency")
        gmax = _peak("gain_dBi")
        if gmax is None and dmax is not None and eta is not None and eta > 0:
            gmax = dmax + 10.0 * math.log10(eta)
        prad = _per_freq("Prad")
        n_th = 0 if d.get("theta_deg") is None else int(np.asarray(d["theta_deg"]).size)
        n_ph = 0 if d.get("phi_deg") is None else int(np.asarray(d["phi_deg"]).size)
        lines = [
            f"Dmax: {dmax:.2f} dBi" if dmax is not None else "Dmax: n/a",
            f"Gmax: {gmax:.2f} dBi" if gmax is not None else "Gmax: n/a",
            f"eta: {eta:.3f}" if eta is not None else "eta: n/a",
            f"Prad: {prad:.4g} W*s^2" if prad is not None else "Prad: n/a",
            f"theta: {n_th} pts",
            f"phi: {n_ph} pts",
        ]
        h = self._hpbw
        if h is not None:
            lines.append("")
            lines.append(f"HPBW ({h.get('plot', '?')}):")
            lines.append(f"  opening angle: {h['width_deg']:.1f}°")
            lines.append(f"  edges: {h['left_deg']:.1f}° … {h['right_deg']:.1f}°")
            lines.append(f"  peak: {h['peak_deg']:.1f}° @ {h['peak_dbi']:.2f} dBi")
            lines.append(f"  −3 dB level: {h['threshold_dbi']:.2f} dBi")
        v = self._vrv
        if v is not None:
            lines.append("")
            lines.append(f"VRV / F-B ({v.get('plot', '?')}):")
            lines.append(f"  ratio: {v['vrv_db']:.2f} dB  (linear {v['ratio']:.3g})")
            lines.append(f"  peak: {v['peak_deg']:.1f}° @ {v['peak_dbi']:.2f} dBi")
            lines.append(f"  back: {v['back_deg']:.1f}° @ {v['back_dbi']:.2f} dBi")
        self.lbl_detail.setText("\n".join(lines))

    # ----- pop-out ------------------------------------------------------
    @property
    def graph_is_popped_out(self) -> bool:
        return self._graph_win is not None

    def popout_graph(self) -> None:
        if self._graph_win is not None:
            # toggle: second click restores the graph to the tab
            self.restore_graph_to_tab()
            return
        win = AntennaGraphWindow(self)
        self._graph_win = win
        win.show()
        win.raise_()
        win.activateWindow()
        self._redraw()
        # close the analysis dialog once the graph is popped out
        self._radial_dlg.close()

    def restore_graph_to_tab(self) -> None:
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
        self._plot_split.setSizes([260, 740])
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
        self._plot_split.setSizes([260, 740])
        self.btn_popout.setText("Pop out graph")
        self._redraw()

    # ----- plotting -----------------------------------------------------
    def _freq_index(self) -> int:
        if self.cmb_freq.count() <= 1:
            return 0
        return max(0, self.cmb_freq.currentIndex())

    def _pattern_2d(self, field: str) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Return (cut2d[n_theta,n_phi], theta_deg, phi_deg) for the selected freq."""
        d = self.data
        if d is None:
            return None
        arr = d.get(field)
        th = d.get("theta_deg")
        ph = d.get("phi_deg")
        if arr is None or th is None or ph is None:
            return None
        arr = np.asarray(arr, dtype=float)
        th = np.asarray(th, dtype=float).reshape(-1)
        ph = np.asarray(ph, dtype=float).reshape(-1)
        if arr.ndim == 3:
            fi = min(self._freq_index(), arr.shape[0] - 1)
            arr = arr[fi]
        if arr.ndim != 2 or arr.shape[0] != th.size or arr.shape[1] != ph.size:
            return None
        return arr, th, ph

    def _downsample(self, arr2d: np.ndarray, th: np.ndarray, ph: np.ndarray, max_pts: int = 60):
        n_t, n_p = arr2d.shape
        step_t = max(1, n_t // max_pts)
        step_p = max(1, n_p // max_pts)
        return arr2d[::step_t, ::step_p], th[::step_t], ph[::step_p]

    def _redraw(self) -> None:
        self.figure.clear()
        self._marker_cfg = None
        if self.data is None:
            self._set_dbi_controls_visible(False)
            self.figure.text(0.5, 0.5, "No data loaded", ha="center", va="center")
            self.canvas.draw_idle()
            return

        plot = self.cmb_plot.currentText()
        polar = plot in (
            "E-Plane (phi=90)",
            "H-Plane (phi=0)",
            "Azimuth (theta=90)",
        )
        self._polar_active = polar
        self._set_dbi_controls_visible(plot == "3D Pattern")
        if plot == "Gain vs Frequency":
            self._plot_gain_vs_freq()
        elif plot == "Radiated Efficiency vs Frequency":
            self._plot_eff_vs_freq()
        elif plot == "3D Pattern":
            self._plot_3d()
        else:
            self._plot_polar_cut(plot)
        self._draw_markers(plot)
        self.canvas.draw_idle()

    # ----- markers (left-click a 2D plot) --------------------------------
    def _register_marker_ax(self, ax, kind: str, xunit: str, yunit: str) -> None:
        """Enable click markers on a freshly drawn 2D axes."""
        self._marker_cfg = {"ax": ax, "kind": kind, "xunit": xunit, "yunit": yunit}
        # polar plots use a radial offset (rmin_eff): click y is radius, not dBi
        self._marker_cfg["rmin_eff"] = float(getattr(self, "_plot_rmin_eff", 0.0))

    def _on_click(self, event) -> None:
        if (
            event.button == 3
            and getattr(self, "_polar_active", False)
            and event.inaxes is not None
        ):
            # position near the click; do NOT raise/activate — that would
            # steal focus from the pop-out window and hide it behind the main UI
            pos = self.canvas.mapToGlobal(self.canvas.rect().center())
            self._radial_dlg.move(pos - self._radial_dlg.rect().center())
            self._radial_dlg.show()
            return
        cfg = self._marker_cfg
        if (
            cfg is None
            or self.data is None
            or event.button != 1
            or event.inaxes is not cfg["ax"]
            or event.x is None
            or event.y is None
        ):
            return
        ax = cfg["ax"]
        curves = [ln for ln in ax.get_lines() if not (ln.get_label() or "").startswith("_")]
        multi = len(curves) > 1
        best: tuple[float, float, float, str] | None = None
        for line in curves or ax.get_lines():
            xs = np.asarray(line.get_xdata(), dtype=float)
            ys = np.asarray(line.get_ydata(), dtype=float)
            n = min(xs.size, ys.size)
            if n == 0:
                continue
            pts = ax.transData.transform(np.column_stack([xs[:n], ys[:n]]))
            d = np.hypot(pts[:, 0] - event.x, pts[:, 1] - event.y)
            if not np.isfinite(d).any():
                continue
            i = int(np.nanargmin(d))
            if best is None or float(d[i]) < best[0]:
                best = (float(d[i]), float(xs[i]), float(ys[i]), line.get_label() or "")
        if best is None:
            return
        self._add_marker(best[1], best[2], best[3] if multi else "", cfg)

    def _add_marker(self, x: float, y: float, label: str, cfg: dict) -> None:
        if not (math.isfinite(x) and math.isfinite(y)):
            return
        plot = self.cmb_plot.currentText()
        if cfg["kind"] == "polar":
            x = float(np.degrees(x)) % 360.0
            # plotted radius is offset by rmin_eff → convert back to dBi
            y = y + float(cfg.get("rmin_eff", 0.0))
        label = label.replace(" [dBi]", "").replace(" [W]", "").strip()
        if label.startswith("_"):
            label = ""
        for m in self.markers:
            if (
                m["plot"] == plot
                and abs(m["x"] - x) < 1e-6
                and abs(m["y"] - y) < 1e-6
            ):
                return  # identical point already marked
        self.markers.append(
            {
                "plot": plot,
                "kind": cfg["kind"],
                "x": x,
                "y": y,
                "label": label,
                "xunit": cfg["xunit"],
                "yunit": cfg["yunit"],
            }
        )
        self._refresh_markers()
        self._redraw()

    def _draw_markers(self, plot: str) -> None:
        """Draw the crimson dots of every marker belonging to `plot`."""
        if not self.markers or not self.figure.axes:
            return
        ax = self.figure.axes[0]
        if ax.name == "3d":
            return
        for m in self.markers:
            if m["plot"] != plot:
                continue
            x = math.radians(m["x"]) if m["kind"] == "polar" else m["x"]
            y = m["y"]
            if m["kind"] == "polar":
                # stored y is dBi; plot uses shifted radius
                y = y - float(getattr(self, "_plot_rmin_eff", 0.0))
            ax.plot(
                [x],
                [y],
                "o",
                color="crimson",
                ms=7,
                mec="white",
                mew=1.0,
                zorder=7,
                label="_marker",
            )

    def _refresh_markers(self) -> None:
        self.marker_list.clear()
        for m in self.markers:
            label = f"  ({m['label']})" if m.get("label") else ""
            self.marker_list.addItem(
                QListWidgetItem(
                    f"{m['plot']}: {m['x']:.4g}{m['xunit']}  {m['y']:.3g}{m['yunit']}{label}"
                )
            )

    def _clear_markers(self) -> None:
        self.markers = []
        self._refresh_markers()
        self._redraw()

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

    @staticmethod
    def _plane_cut(
        cut2d: np.ndarray, th: np.ndarray, ph: np.ndarray, phi0: float
    ) -> tuple[np.ndarray, np.ndarray]:
        """Theta cut at azimuth phi0, completed to a full 360° circle.

        A plane through the antenna consists of the phi0 half (theta 0..180,
        drawn clockwise from north) plus the phi0+180 half (theta 180..0,
        drawn continuing to 360). Without the mirror half only the right half
        of the plane would be shown.
        """
        ip = int(np.argmin(np.abs(ph - phi0)))
        target = (phi0 + 180.0) % 360.0
        wrap = (ph - target + 180.0) % 360.0 - 180.0
        ip_back = int(np.argmin(np.abs(wrap)))
        front = np.asarray(cut2d[:, ip], dtype=float)
        back = np.asarray(cut2d[:, ip_back], dtype=float)
        ang = np.concatenate([np.deg2rad(th), np.deg2rad(360.0 - th[::-1])])
        vals = np.concatenate([front, back[::-1]])
        return ang, vals

    @staticmethod
    def _radial_ticks(ax, vmin: float, vmax: float) -> None:
        """Nice radial (dB) tick steps: about five labels for any range."""
        if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
            return
        steps = (0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0)
        target = (vmax - vmin) / 5.0
        step = next((s for s in steps if s >= target), steps[-1])
        ax.yaxis.set_major_locator(MultipleLocator(step))

    def _current_polar_cut(self) -> tuple[np.ndarray, np.ndarray, str, str] | None:
        """(ang_rad, cut_dBi, field_key, plot_name) for the selected polar cut."""
        if self.data is None:
            return None
        plot = self.cmb_plot.currentText()
        if plot not in (
            "E-Plane (phi=90)",
            "H-Plane (phi=0)",
            "Azimuth (theta=90)",
        ):
            return None
        field = "gain_dBi" if self.data.get("gain_dBi") is not None else "D_dBi"
        got = self._pattern_2d(field)
        if got is None:
            return None
        cut2d, th, ph = got
        if plot in ("E-Plane (phi=90)", "H-Plane (phi=0)"):
            phi0 = 90.0 if plot.startswith("E-") else 0.0
            ang, cut = self._plane_cut(cut2d, th, ph, phi0)
        else:
            it = int(np.argmin(np.abs(th - 90.0)))
            cut = np.asarray(cut2d[it, :], dtype=float)
            ang = np.deg2rad(ph[: cut.size])
        return ang, cut, field, plot

    def _pattern_field_label(self, field: str) -> str:
        return "Gain" if field == "gain_dBi" else "Directivity"

    def _on_hpbw(self, checked: bool = False) -> None:
        if not checked:
            self._hpbw = None
            self._update_details()
            self._redraw()
            self.lbl_info.setText("HPBW off")
            return
        got = self._current_polar_cut()
        if got is None:
            self.btn_hpbw.setChecked(False)
            self.lbl_info.setText(
                "HPBW: select a polar cut (E-Plane / H-Plane / Azimuth) first."
            )
            return
        ang, cut, field, plot = got
        res = half_power_beamwidth(np.degrees(ang), cut, level_db=-3.0)
        if res is not None:
            res = {**res, "plot": plot, "field": field}
        self._hpbw = res
        self._update_details()
        self._redraw()
        if res is not None:
            self.lbl_info.setText(
                f"HPBW {res['width_deg']:.1f}° on {plot} "
                f"[{res['left_deg']:.1f}°..{res['right_deg']:.1f}°]"
            )

    def _on_vrv(self, checked: bool = False) -> None:
        if not checked:
            self._vrv = None
            self._update_details()
            self._redraw()
            self.lbl_info.setText("VRV off")
            return
        got = self._current_polar_cut()
        if got is None:
            self.btn_vrv.setChecked(False)
            self.lbl_info.setText(
                "VRV: select a polar cut (E-Plane / H-Plane / Azimuth) first."
            )
            return
        ang, cut, field, plot = got
        res = front_to_back_ratio(np.degrees(ang), cut)
        if res is not None:
            res = {**res, "plot": plot, "field": field}
        self._vrv = res
        self._update_details()
        self._redraw()
        if res is not None:
            self.lbl_info.setText(
                f"VRV {res['vrv_db']:.2f} dB (×{res['ratio']:.3g}) on {plot}"
            )

    def _plot_polar_cut(self, plot: str) -> None:
        field = "gain_dBi" if self.data.get("gain_dBi") is not None else "D_dBi"
        got = self._pattern_2d(field)
        if got is None:
            self.figure.text(0.5, 0.5, f"{field} not available", ha="center", va="center")
            return
        cut2d, th, ph = got
        ax = self.figure.add_subplot(111, projection="polar")

        if plot in ("E-Plane (phi=90)", "H-Plane (phi=0)"):
            phi0 = 90.0 if plot.startswith("E-") else 0.0
            ang, cut = self._plane_cut(cut2d, th, ph, phi0)
        elif plot.startswith("Azimuth"):
            it = int(np.argmin(np.abs(th - 90.0)))
            cut = np.asarray(cut2d[it, :], dtype=float)
            ang = np.deg2rad(ph[: cut.size])
        else:  # fallback: elevation cut at phi=0
            ip = int(np.argmin(np.abs(ph - 0.0)))
            cut = np.asarray(cut2d[:, ip], dtype=float)
            ang = np.deg2rad(th[: cut.size])

        rmin_data = float(np.nanmin(cut))
        rmax_data = float(np.nanmax(cut))
        # radial window: user overrides or auto (floor 0, nice ceiling)
        rmin_user = float(self.spn_rmin.value())
        rmax_user = float(self.spn_rmax.value())
        rmin_eff = rmin_user  # 0 = auto floor at 0 dB
        # polar r ≥ 0 only: shift data so rmin_eff → 0
        cut_plot = np.maximum(cut, rmin_eff) - rmin_eff
        self._plot_rmin_eff = rmin_eff
        self._register_marker_ax(ax, "polar", "°", "dBi")
        ax.plot(ang, cut_plot)
        if rmax_user > 0.0:
            # user rmax is absolute dBi → plot-space height
            rmax_eff = max(rmax_user - rmin_eff, 1e-6)
        else:
            peak_shift = max(rmax_data - rmin_eff, 0.0)
            rmax_eff = None  # auto below
            for nice in (1.0, 2.0, 2.5, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0, 80.0, 100.0):
                if peak_shift <= nice + 1e-9:
                    rmax_eff = nice
                    break
            else:
                rmax_eff = math.ceil(peak_shift)
        ax.set_rlim(0.0, max(rmax_eff, 1e-6))
        # ring labels show actual dBi (undo the shift)
        if rmin_eff != 0.0:
            def _dbi_label(r: float, _pos: int = 0) -> str:
                return f"{r + rmin_eff:g}"

            ax.yaxis.set_major_formatter(FuncFormatter(_dbi_label))
        field_name = "Gain" if field == "gain_dBi" else "Directivity"
        title = f"{plot}  {field_name} [dBi]"
        notes: list[str] = []
        # HPBW overlay: only for the cut it was computed on
        if self._hpbw is not None and self._hpbw.get("plot") == plot:
            h = self._hpbw
            notes.append(f"HPBW {h['width_deg']:.1f}°")
            thr = h["threshold_dbi"] - rmin_eff
            a_l = math.radians(h["left_deg"] % 360.0)
            a_r = math.radians(h["right_deg"] % 360.0)
            if a_r < a_l:
                a_r += 2.0 * math.pi
            r_arc = rmax_eff  # HPBW arc on the outer ring
            arc = np.linspace(a_l, a_r, 96)
            ax.plot(
                arc,
                np.full_like(arc, r_arc),
                color="crimson",
                lw=3.5,
                label="_hpbw_arc",
                zorder=6,
            )
            for a in (a_l, a_r):
                aa = a % (2.0 * math.pi)
                ax.plot(
                    [aa, aa],
                    [0.0, r_arc],
                    "--",
                    color="crimson",
                    lw=0.8,
                    label="_hpbw_edge",
                    zorder=6,
                )
            circ = np.linspace(0.0, 2.0 * math.pi, 181)
            ax.plot(
                circ,
                np.full_like(circ, thr),
                "--",
                color="0.5",
                lw=0.9,
                label="_hpbw_thr",
                zorder=5,
            )
            a_mid = 0.5 * (a_l + a_r)
            ax.text(
                a_mid,
                rmax_eff + float(self.spn_lblpos.value()) * rmax_eff,
                f"{h['width_deg']:.1f}°",
                color="crimson",
                fontsize=10,
                fontweight="bold",
                ha="center",
                va="bottom",
                zorder=7,
                clip_on=False,
            )
        # VRV overlay: only for the cut it was computed on
        if self._vrv is not None and self._vrv.get("plot") == plot:
            v = self._vrv
            notes.append(f"VRV {v['vrv_db']:.1f} dB")
            a = math.radians(v["back_deg"] % 360.0)
            ax.plot(
                [a],
                [max(v["back_dbi"] - rmin_eff, 0.0)],
                "D",
                color="tab:orange",
                ms=7,
                mec="white",
                mew=0.8,
                label="_vrv_back",
                zorder=7,
            )
            a_pk = math.radians(v["peak_deg"] % 360.0)
            ax.plot(
                [a_pk, a],
                [
                    max(v["peak_dbi"] - rmin_eff, 0.0),
                    max(v["back_dbi"] - rmin_eff, 0.0),
                ],
                color="tab:orange",
                lw=1.0,
                ls="--",
                alpha=0.7,
                label="_vrv_line",
                zorder=6,
            )
            ax.text(
                a,
                rmax_eff + float(self.spn_lblpos.value()) * rmax_eff,
                f"F/B {v['vrv_db']:.1f} dB",
                color="tab:orange",
                fontsize=9,
                ha="center",
                va="bottom",
                zorder=7,
                clip_on=False,
            )
        if notes:
            title += "   [" + ", ".join(notes) + "]"
        ax.set_title(title, fontsize=10)
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        # radial ticks: user step (spn_rstep > 0) or auto
        step_user = float(self.spn_rstep.value())
        if step_user > 0.0:
            ax.yaxis.set_major_locator(MultipleLocator(step_user))
        else:
            self._radial_ticks(ax, 0.0, ax.get_rmax())

    def _plot_gain_vs_freq(self) -> None:
        d = self.data
        freqs = np.asarray(d.get("frequencies", []), dtype=float).reshape(-1)
        dmax = d.get("Dmax_dBi")
        gain = d.get("gain_dBi")
        prad = d.get("Prad")
        eff = d.get("efficiency")

        ax = self.figure.add_subplot(111)
        fdisp = freqs / 1e9 if freqs.size and np.nanmax(freqs) > 1e6 else freqs
        xlabel = "Frequency [GHz]" if (freqs.size and np.nanmax(freqs) > 1e6) else "Frequency"

        if dmax is not None and np.asarray(dmax).size:
            dm = np.asarray(dmax, dtype=float).reshape(-1)
            ax.plot(fdisp[: dm.size], dm, "o-", label="Dmax [dBi]")
        if gain is not None and np.asarray(gain).size:
            g = np.asarray(gain, dtype=float)
            if g.ndim == 3:
                gm = np.nanmax(g.reshape(g.shape[0], -1), axis=1)
            else:
                gm = g.reshape(-1)
            ax.plot(fdisp[: gm.size], gm, "s-", label="Gain max [dBi]")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("dBi")
        ax.set_title("Gain / Directivity vs frequency")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)

        if prad is not None and np.asarray(prad).size:
            p = np.asarray(prad, dtype=float).reshape(-1)
            ax2 = ax.twinx()
            ax2.plot(fdisp[: p.size], p, "^--", color="tab:orange", alpha=0.7, label="Prad [W]")
            ax2.set_ylabel("Prad [W]")
            if eff is not None and np.asarray(eff).size and np.any(np.isfinite(np.asarray(eff))):
                e = np.asarray(eff, dtype=float).reshape(-1)
                ax2.plot(fdisp[: e.size], e, "--", color="tab:green", alpha=0.7, label="eta")
                ax2.set_ylim(0, 1.05)
        self._register_marker_ax(ax, "xy", "GHz" if "GHz" in xlabel else "", "dBi")

    def _plot_eff_vs_freq(self) -> None:
        """Radiated efficiency eta = P_rad/P_acc vs frequency (in %).

        Uses the exported ``efficiency`` array; falls back to the gain /
        directivity ratio (eta = G/D, same angle, so max values work).
        """
        d = self.data
        freqs = np.asarray(d.get("frequencies", []), dtype=float).reshape(-1)
        eff = d.get("efficiency")
        eff = np.asarray(eff, dtype=float).reshape(-1) if eff is not None else None
        source = "P_rad / P_acc"
        if eff is None or not eff.size or not np.any(np.isfinite(eff)):
            g = d.get("gain_dBi")
            dm = d.get("Dmax_dBi")
            if g is not None and dm is not None:
                g = np.asarray(g, dtype=float)
                if g.ndim == 3:
                    gm = np.nanmax(g.reshape(g.shape[0], -1), axis=1)
                else:
                    gm = g.reshape(-1)
                dm = np.asarray(dm, dtype=float).reshape(-1)
                n = min(gm.size, dm.size, freqs.size)
                if n:
                    eff = np.power(10.0, (gm[:n] - dm[:n]) / 10.0)
                    freqs = freqs[:n]
                    source = "gain / directivity"
        ax = self.figure.add_subplot(111)
        if eff is None or not eff.size or not np.any(np.isfinite(eff)):
            self.figure.text(
                0.5, 0.5, "efficiency not available", ha="center", va="center"
            )
            return
        n = min(eff.size, freqs.size)
        eff = eff[:n]
        fdisp = freqs / 1e9 if freqs.size and np.nanmax(freqs) > 1e6 else freqs
        xlabel = "Frequency [GHz]" if (freqs.size and np.nanmax(freqs) > 1e6) else "Frequency"
        finite = eff[np.isfinite(eff)]
        # efficiency is exported as a fraction (0..1); tolerate percent input
        scale = 100.0 if finite.size and float(np.nanmax(finite)) <= 1.5 else 1.0
        y = eff * scale
        ax.plot(fdisp[: y.size], y, "o-", color="tab:green", label=f"eta ({source})")
        ax.set_xlabel(xlabel)
        ax.set_ylabel("Radiated efficiency [%]")
        ax.set_title("Radiated efficiency vs frequency", fontsize=10)
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)
        yf = y[np.isfinite(y)]
        if yf.size and float(np.nanmax(yf)) <= 105.0:
            ax.set_ylim(0.0, 105.0)
        self._register_marker_ax(ax, "xy", "GHz" if "GHz" in xlabel else "", "%")

    def _plot_3d(self) -> None:
        field = self._selected_3d_field()
        got = self._pattern_2d(field)
        if got is None:
            self.figure.text(0.5, 0.5, f"{field} not available", ha="center", va="center")
            return
        cut2d, th, ph = self._downsample(*got)
        th_r = np.deg2rad(th)
        ph_r = np.deg2rad(ph)
        TH, PH = np.meshgrid(th_r, ph_r, indexing="ij")

        # Absolute dBi window (sliders): radius 0..1 and the color scale both
        # map the [dmin, dmax] dBi interval, so the surface carries the full
        # color gradient and the user can zoom the dBi range.
        data_min = float(np.nanmin(cut2d))
        data_max = float(np.nanmax(cut2d))
        dmin, dmax = self._dbi_window()
        if dmax <= dmin:
            dmin, dmax = data_min, data_max
            if dmax - dmin < 0.1:
                dmin, dmax = dmin - 0.5, dmax + 0.5
        cut2d = np.nan_to_num(cut2d, nan=dmin, posinf=dmax, neginf=dmin)
        span = dmax - dmin
        R = np.clip((cut2d - dmin) / span, 0.0, 1.0)
        X = R * np.sin(TH) * np.cos(PH)
        Y = R * np.sin(TH) * np.sin(PH)
        Z = R * np.cos(TH)

        field_name = "Dmax" if field == "D_dBi" else "Gain"
        ax = self.figure.add_subplot(111, projection="3d")
        import matplotlib.cm as cm
        from matplotlib import colormaps
        from matplotlib.colors import Normalize

        norm = Normalize(vmin=dmin, vmax=dmax, clip=True)
        try:
            cmap = colormaps[self.cmb_cmap.currentText()]
        except KeyError:
            cmap = colormaps["viridis"]
        try:
            from mpl_toolkits.mplot3d.art3d import Poly3DCollection

            polys = []
            colors = []
            for i in range(cut2d.shape[0] - 1):
                for j in range(cut2d.shape[1] - 1):
                    quad = [
                        [X[i, j], Y[i, j], Z[i, j]],
                        [X[i + 1, j], Y[i + 1, j], Z[i + 1, j]],
                        [X[i + 1, j + 1], Y[i + 1, j + 1], Z[i + 1, j + 1]],
                        [X[i, j + 1], Y[i, j + 1], Z[i, j + 1]],
                    ]
                    polys.append(quad)
                    colors.append(cmap(norm(cut2d[i, j])))
            pc = Poly3DCollection(polys, facecolors=colors, edgecolors="none", alpha=0.9)
            ax.add_collection3d(pc)
        except Exception:  # noqa: BLE001
            ax.plot_surface(
                X, Y, Z, facecolors=cmap(norm(cut2d)), edgecolor="none", shade=False
            )
        ax.set_xlim(-1, 1)
        ax.set_ylim(-1, 1)
        ax.set_zlim(-1, 1)
        ax.set_box_aspect((1, 1, 1))
        # no numbers on the 3D axes (the colorbar carries the dBi scale)
        ax.set_xticklabels([])
        ax.set_yticklabels([])
        ax.set_zticklabels([])
        ax.set_title(f"3D {field_name} [dBi]  ({dmin:.1f} .. {dmax:.1f})", fontsize=10)
        extend = "neither"
        if data_min < dmin and data_max > dmax:
            extend = "both"
        elif data_min < dmin:
            extend = "min"
        elif data_max > dmax:
            extend = "max"
        mappable = cm.ScalarMappable(norm=norm, cmap=cmap)
        mappable.set_array(cut2d)
        self.figure.colorbar(
            mappable,
            ax=ax,
            shrink=0.6,
            pad=0.12,
            label=f"{field_name} [dBi]",
            extend=extend,
        )
