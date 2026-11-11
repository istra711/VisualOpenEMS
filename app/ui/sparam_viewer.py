"""Interactive S-parameter viewer: Smith chart, traces, markers, cursor."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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

from app.postproc.sparams import (
    export_network_touchstone,
    write_sparam_ascii,
    write_sparam_csv,
)


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


_SPARAM_NAMES = ("sparams.s1p", "sparams.s2p", "sparams.s3p", "sparams.s4p", "sparams.json")


def find_latest_sparam_files(work_dir: Path) -> list[Path]:
    """Discover sparams.s*p / sparams.json, newest first.

    One result per directory (``sparams.s1p`` beats ``sparams.json``) at
    ``<dir>``, ``<dir>/sparams``, ``<dir>/simulation`` and up to two nested
    levels (``<dir>/<project>/...``) — so both the global work directory (all
    projects) and a single project directory can be passed.
    """
    out: list[Path] = []
    if not work_dir.exists():
        return out
    dirs: list[Path] = [work_dir]
    for pat in ("*", "*/*", "*/*/*"):
        dirs += sorted(p for p in work_dir.glob(pat) if p.is_dir())
    seen: set[Path] = set()
    for d in dirs:
        if d in seen:
            continue
        seen.add(d)
        for name in _SPARAM_NAMES:
            p = d / name
            if p.is_file():
                out.append(p)
                break
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


class SParamGraphWindow(QMainWindow):
    """Standalone pop-out window hosting the S-param plot panel."""

    def __init__(self, viewer: "SParamViewer") -> None:
        super().__init__(None)
        self.viewer: SParamViewer | None = viewer
        self.project_name = "Untitled"
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
        self.set_project_name(viewer.project_name if viewer is not None else "Untitled")
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

        self.lbl_trust = QLabel("")
        self.lbl_trust.setWordWrap(True)
        self.lbl_trust.setStyleSheet(
            "color: #c62828; font-weight: 600; background: #fff3f3; "
            "border: 1px solid #e57373; padding: 3px;"
        )
        self.lbl_trust.setVisible(False)
        layout.addWidget(self.lbl_trust)

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
            trust = getattr(viewer, "lbl_trust", None)
            if trust is not None:
                self.lbl_trust.setText(trust.text())
                self.lbl_trust.setVisible(trust.isVisible())
            viewer.btn_popout.setText("Graph popped out")

        layout.setContentsMargins(6, 6, 6, 6)
        self.setCentralWidget(container)

        self.cmb_plot.currentTextChanged.connect(self._on_plot_changed)
        self.cmb_port.currentTextChanged.connect(self._on_port_changed)

    def set_project_name(self, name: str) -> None:
        self.project_name = str(name or "").strip() or "Untitled"
        self.setWindowTitle(f"S-Parameter Graph — {self.project_name}")
        if hasattr(self, "lbl_title"):
            self.lbl_title.setText(f"Project: {self.project_name} — S-plot")

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
        # The pop-out's single Trace box means "show exactly this trace".
        if text and hasattr(v, "_select_only_trace"):
            v._select_only_trace(text, redraw=False)
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

    markers_changed = Signal()

    PLOT_TYPES = [
        "Smith",
        "|S| dB",
        "Return loss [dB]",
        "|S| mag",
        "Phase deg",
        "VSWR",
        "Complex Re/Im",
        "Re/Im + Phase",
        "Z Re/Im",
    ]

    TRACE_PRESETS = [
        "Single trace",
        "Return loss (Sii)",
        "Transmission (Sij)",
        "All traces",
        "Custom",
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
        self.project_name = "Untitled"
        # root used by "Scan work dir"; work_dir may be scoped to one project
        self.scan_root: Path | None = None
        self.network = None
        self.network_path: Path | None = None
        self.markers: list[float] = []  # frequencies (Hz)
        self._markers_visible = True
        self._marker_tags: dict[float, str] = {}  # optional "S21 max" labels
        self._delta_refs: dict[float, float] = {}  # delta marker -> reference marker
        self._selected_marker: float | None = None  # highlighted in list + plot
        self._cursor_line = None
        self._cursor_text = None
        self._marker_artists: list = []
        self._marker_z0 = 50.0
        self._graph_win: SParamGraphWindow | None = None
        self._syncing_traces = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # analysis dialog (right-click on plot)
        self._analysis_dlg = self._build_analysis_dialog()

        bar = QHBoxLayout()
        self.btn_open = QPushButton("Open…")
        self.btn_open.setToolTip(
            "Open S-parameters: a Touchstone file (.sNp), sparams.json or a\n"
            "results JSON of this app."
        )
        self.btn_reload = QPushButton("Reload")
        self.btn_scan = QPushButton("Scan work dir")
        self.btn_scan.setToolTip(
            "List all sparams.s*p / sparams.json files in the whole work\n"
            "directory (not only the current project)"
        )
        self.btn_export = QPushButton("Export…")
        self.btn_export.setToolTip(
            "Export the loaded S-parameters as Touchstone (.sNp), CSV or\n"
            "ASCII table. Touchstone is the standard for CST / VNA / skrf."
        )
        self.btn_export.setEnabled(False)
        self.cmb_plot = QComboBox()
        self.cmb_plot.addItems(self.PLOT_TYPES)
        self.cmb_port = QComboBox()
        self.cmb_port.setToolTip(
            "Primary trace for marker placement / cursor / analysis.\n"
            "The marker list shows every ticked trace at the marker frequency."
        )
        self.cmb_trace_set = QComboBox()
        self.cmb_trace_set.addItems(self.TRACE_PRESETS)
        self.cmb_trace_set.setToolTip(
            "Quick selection for the overlay:\n"
            "• Return loss (Sii): S11, S22, …\n"
            "• Transmission (Sij): S12, S13, … (i≠j)\n"
            "• All traces: the complete S-matrix"
        )
        self.lbl_info = QLabel("No data loaded")
        for w in (
            self.btn_open,
            self.btn_reload,
            self.btn_scan,
            self.btn_export,
        ):
            bar.addWidget(w)
        bar.addWidget(QLabel("Plot"))
        bar.addWidget(self.cmb_plot)
        bar.addWidget(QLabel("Preset"))
        bar.addWidget(self.cmb_trace_set)
        bar.addWidget(QLabel("Primary"))
        bar.addWidget(self.cmb_port)
        bar.addStretch(1)
        self.lbl_project = QLabel("Project: Untitled")
        self.lbl_project.setToolTip("Currently open VisualopenEMS project")
        _pf = self.lbl_project.font()
        _pf.setBold(True)
        self.lbl_project.setFont(_pf)
        bar.addWidget(self.lbl_project)
        root.addLayout(bar)

        self.lbl_trust = QLabel("")
        self.lbl_trust.setWordWrap(True)
        self.lbl_trust.setStyleSheet(
            "color: #c62828; font-weight: 600; background: #fff3f3; "
            "border: 1px solid #e57373; padding: 3px;"
        )
        self.lbl_trust.setVisible(False)
        root.addWidget(self.lbl_trust)

        self._plot_split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self._plot_split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Loaded files"))
        self.file_list = QListWidget()
        lv.addWidget(self.file_list)
        lv.addWidget(QLabel("Traces (check to overlay)"))
        self.trace_list = QListWidget()
        self.trace_list.setMaximumHeight(155)
        self.trace_list.setToolTip(
            "Tick several S-parameters to draw them in one plot.\n"
            "The bold/current entry (Primary) drives marker placement and\n"
            "cursor/analysis; the marker list shows values for every ticked trace."
        )
        lv.addWidget(self.trace_list)
        marker_bar = QHBoxLayout()
        marker_bar.addWidget(QLabel("Markers (click plot to add)"))
        self.chk_markers_visible = QCheckBox("show")
        self.chk_markers_visible.setChecked(True)
        self.chk_markers_visible.setToolTip(
            "Show/hide the marker cursors on the plot.\n"
            "Markers stay in the list (and in the project for S-parameters)."
        )
        marker_bar.addWidget(self.chk_markers_visible)
        marker_bar.addStretch(1)
        lv.addLayout(marker_bar)
        self.marker_list = QListWidget()
        self.marker_list.setToolTip(
            "One row per marker frequency and ticked trace.\n"
            "Ticking/unticking a trace updates these values automatically."
        )
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
        self.marker_list.currentItemChanged.connect(self._on_marker_list_selected)
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
        self.btn_scan.clicked.connect(self._on_scan_workdir)
        self.btn_export.clicked.connect(self._on_export)
        self.chk_markers_visible.toggled.connect(self._on_markers_visible_toggled)
        self.cmb_marker_style.currentTextChanged.connect(self._on_marker_style_changed)
        self.cmb_plot.currentTextChanged.connect(lambda *_: self._redraw())
        self.cmb_plot.currentIndexChanged.connect(self._redraw)
        self.cmb_port.currentIndexChanged.connect(self._on_primary_trace_changed)
        self.cmb_trace_set.currentTextChanged.connect(self._on_trace_preset_changed)
        self.trace_list.itemChanged.connect(self._on_trace_item_changed)
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        self.canvas.setToolTip(
            "Left-click: add a marker at the nearest data point.\n"
            "Right-click: smart markers (min/max, nearest peak/valley,\n"
            "-3 dB points, band center), clear markers, analysis dialog,\n"
            "and pop-out graph."
        )

        if self.work_dir:
            # no automatic listing: "Loaded files" fills only on
            # "Scan work dir" (or when a file is opened explicitly)
            self.file_list.clear()

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
    def set_project_name(self, name: str) -> None:
        """Show the current project name here and in an open pop-out."""
        self.project_name = str(name or "").strip() or "Untitled"
        if hasattr(self, "lbl_project"):
            self.lbl_project.setText(f"Project: {self.project_name}")
        win = getattr(self, "_graph_win", None)
        if win is not None and hasattr(win, "set_project_name"):
            win.set_project_name(self.project_name)

    def set_work_dir(self, work_dir: Path) -> None:
        """Scope the auto-load to ``work_dir`` (usually one project).

        Does NOT fill the "Loaded files" list — that only happens on
        "Scan work dir" (``scan_root`` = the global work directory) or when a
        file is opened explicitly.
        """
        self.work_dir = Path(work_dir)

    def set_untrusted(self, note: str | None) -> None:
        """Show/hide the red result-trust banner (failed/partial run)."""
        text = str(note or "").strip()
        if hasattr(self, "lbl_trust"):
            self.lbl_trust.setText(text)
            self.lbl_trust.setVisible(bool(text))
        gw = getattr(self, "_graph_win", None)
        if gw is not None and hasattr(gw, "lbl_trust"):
            gw.lbl_trust.setText(text)
            gw.lbl_trust.setVisible(bool(text))

    def clear(self) -> None:
        """Drop the loaded network (empty diagram) — e.g. project without results."""
        self.network = None
        self.network_path = None
        self.markers = []
        self._marker_tags.clear()
        self._delta_refs.clear()
        self._selected_marker = None
        if hasattr(self, "btn_export"):
            self.btn_export.setEnabled(False)
        self._populate_ports()
        self._refresh_markers()
        self.lbl_info.setText("")
        self.set_untrusted(None)
        self._redraw()
        gw = getattr(self, "_graph_win", None)
        if gw is not None and hasattr(gw, "lbl_info"):
            gw.lbl_info.setText("")

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
        self._marker_tags.clear()
        self._delta_refs.clear()
        self._selected_marker = None
        if hasattr(self, "btn_export"):
            self.btn_export.setEnabled(True)
        self.set_untrusted(None)
        self._populate_ports()
        self._refresh_markers()
        info = (
            f"{path.name}: {ntwk.nports} port(s), {len(ntwk.f)} pts, "
            f"{ntwk.f[0]/1e9:.4g}–{ntwk.f[-1]/1e9:.4g} GHz"
        )
        missing = self._missing_traces(ntwk)
        if missing:
            info += "  · no data: " + ", ".join(missing)
        self.lbl_info.setText(info)
        self._redraw()

    @staticmethod
    def _trace_has_data(s) -> bool:
        """False for placeholder traces: all NaN (or all exactly zero).

        ``sparams.json`` stores unmeasured S-matrix cells as NaN, Touchstone
        exports may write them as 0; both mean "this column was not
        simulated" and must not drive autoscaling.
        """
        arr = np.asarray(s, dtype=complex)
        if arr.size == 0 or not np.any(np.isfinite(arr)):
            return False
        return bool(np.any(arr != 0))

    @staticmethod
    def _missing_traces(ntwk) -> list[str]:
        """S-params of the matrix whose data is empty (never simulated)."""
        try:
            s = np.asarray(ntwk.s)
        except Exception:  # noqa: BLE001
            return []
        if s.ndim != 3:
            return []
        missing = []
        for i in range(s.shape[1]):
            for j in range(s.shape[2]):
                if not SParamViewer._trace_has_data(s[:, i, j]):
                    missing.append(f"S{i + 1}{j + 1}")
        return missing

    def _populate_ports(self) -> None:
        self._syncing_traces = True
        self.cmb_port.blockSignals(True)
        self.cmb_port.clear()
        names: list[str] = []
        if self.network is not None:
            n = self.network.nports
            if n == 1:
                names = ["S11"]
            else:
                for i in range(1, n + 1):
                    for j in range(1, n + 1):
                        names.append(f"S{i}{j}")
            self.cmb_port.addItems(names)
        self.cmb_port.blockSignals(False)
        self._syncing_traces = False
        self._populate_traces(names)

    def _populate_traces(self, names: list[str] | None = None) -> None:
        """Fill the checkable trace list and start with one primary trace."""
        if names is None:
            names = [self.cmb_port.itemText(i) for i in range(self.cmb_port.count())]
        self._syncing_traces = True
        self.trace_list.blockSignals(True)
        self.trace_list.clear()
        primary = self.cmb_port.currentText() or (names[0] if names else "")
        for name in names:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if name == primary else Qt.CheckState.Unchecked
            )
            self.trace_list.addItem(item)
        self.trace_list.blockSignals(False)
        self.cmb_trace_set.blockSignals(True)
        self.cmb_trace_set.setCurrentText("Single trace")
        self.cmb_trace_set.blockSignals(False)
        self._syncing_traces = False

    def _checked_traces(self) -> list[str]:
        """Currently ticked S-parameter names (in matrix order)."""
        keys: list[str] = []
        for i in range(self.trace_list.count()):
            item = self.trace_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                keys.append(item.text())
        if not keys:
            primary = self.cmb_port.currentText()
            if primary:
                keys = [primary]
        return keys

    def _set_checked_traces(self, keys: list[str], preset: str | None = None) -> None:
        """Check exactly ``keys`` and make the first one the primary trace."""
        wanted = set(keys)
        self._syncing_traces = True
        self.trace_list.blockSignals(True)
        first = ""
        for i in range(self.trace_list.count()):
            item = self.trace_list.item(i)
            checked = item.text() in wanted
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            if checked and not first:
                first = item.text()
        self.trace_list.blockSignals(False)
        if first:
            self.cmb_port.blockSignals(True)
            self.cmb_port.setCurrentText(first)
            self.cmb_port.blockSignals(False)
        if preset is not None:
            self.cmb_trace_set.blockSignals(True)
            self.cmb_trace_set.setCurrentText(preset)
            self.cmb_trace_set.blockSignals(False)
        self._syncing_traces = False

    def _select_only_trace(self, key: str, *, redraw: bool = True) -> None:
        self._set_checked_traces([key], "Single trace")
        if redraw:
            self._redraw()

    def _on_primary_trace_changed(self, _index: int = 0) -> None:
        if self._syncing_traces:
            return
        key = self.cmb_port.currentText()
        if key:
            self._set_checked_traces([key], "Single trace")
        self._redraw()

    def _on_trace_item_changed(self, _item: QListWidgetItem) -> None:
        if self._syncing_traces:
            return
        keys = self._checked_traces()
        if keys and self.cmb_port.currentText() not in keys:
            self.cmb_port.blockSignals(True)
            self.cmb_port.setCurrentText(keys[0])
            self.cmb_port.blockSignals(False)
        self.cmb_trace_set.blockSignals(True)
        self.cmb_trace_set.setCurrentText("Custom")
        self.cmb_trace_set.blockSignals(False)
        self._redraw()

    def _on_trace_preset_changed(self, text: str) -> None:
        if self._syncing_traces:
            return
        if text == "Single trace":
            key = self.cmb_port.currentText()
            self._set_checked_traces([key] if key else [], "Single trace")
        elif text == "Return loss (Sii)":
            keys = [
                f"S{i}{i}"
                for i in range(1, (self.network.nports if self.network else 0) + 1)
            ]
            self._set_checked_traces(keys, text)
        elif text == "Transmission (Sij)":
            n = self.network.nports if self.network else 0
            keys = [f"S{i}{j}" for i in range(1, n + 1) for j in range(1, n + 1) if i != j]
            self._set_checked_traces(keys, text)
        elif text == "All traces":
            keys = [self.trace_list.item(i).text() for i in range(self.trace_list.count())]
            self._set_checked_traces(keys, text)
        else:  # Custom — user ticks the list manually
            return
        # Pick the matching quantity: return loss is positive (−20log|Sii|),
        # transmission stays the usual signed |Sij| in dB.
        if text == "Return loss (Sii)":
            self.cmb_plot.setCurrentText("Return loss [dB]")
        elif text in ("Transmission (Sij)", "All traces") and self.cmb_plot.currentText() in (
            "Smith",
            "Return loss [dB]",
        ):
            self.cmb_plot.setCurrentText("|S| dB")
        self._redraw()

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

    def _on_scan_workdir(self) -> None:
        """The "Scan work dir" button: list every result of the whole work dir."""
        self._on_scan(root=self.scan_root or self.work_dir)

    def _on_scan(self, auto_load: bool = False, root: Path | None = None) -> None:
        """Fill the list with the results below ``root`` (default: ``work_dir``).

        ``work_dir`` is usually scoped to the current project; "Scan work dir"
        passes ``scan_root`` (the global work dir) instead. The loaded file
        stays selected without being reloaded.
        """
        root = root or self.work_dir
        self.file_list.blockSignals(True)
        self.file_list.clear()
        files: list[Path] = []
        if root is not None:
            files = find_latest_sparam_files(root)
            for f in files:
                # sub-path as shown text (like the Antenna tab), full path in
                # UserRole + tooltip for loading
                try:
                    shown = str(f.relative_to(root))
                except ValueError:
                    shown = f.name
                item = QListWidgetItem(shown)
                item.setData(Qt.ItemDataRole.UserRole, str(f))
                item.setToolTip(str(f))
                self.file_list.addItem(item)
        # keep the loaded file selected when the scan found it
        row = -1
        if self.network_path is not None:
            for i in range(self.file_list.count()):
                raw = self.file_list.item(i).data(Qt.ItemDataRole.UserRole)
                if str(raw) == str(self.network_path):
                    row = i
                    break
        if auto_load and row < 0 and files and self.network is None:
            row = 0
        if row >= 0:
            self.file_list.setCurrentRow(row)
        self.file_list.blockSignals(False)
        if auto_load and files and self.network is None:
            # Newest file first — load so the tab is never blank when data exists.
            self.load_path(files[0])

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

    # ----- export --------------------------------------------------------
    def _export_default_dir(self) -> Path:
        if self.network_path is not None:
            return self.network_path.parent
        if self.work_dir is not None:
            return self.work_dir
        return Path.cwd()

    def _export_basename(self) -> str:
        """Default export name: the currently loaded file's stem.

        ``work/.../reference_analytic.s2p`` -> ``reference_analytic`` for CSV /
        ASCII, and ``reference_analytic.sNp`` for Touchstone.  Falls back to
        ``sparams`` when no file is loaded.
        """
        if self.network_path is not None and self.network_path.stem:
            return self.network_path.stem
        return "sparams"

    def _on_export(self) -> None:
        if self.network is None:
            QMessageBox.information(
                self, "Export S-parameters", "No S-parameters are loaded."
            )
            return
        menu = QMenu(self)
        act_touch = menu.addAction("Touchstone (.sNp)…")
        act_csv = menu.addAction("CSV (dB / phase / real / imag)…")
        act_ascii = menu.addAction("ASCII table (.txt)…")
        chosen = menu.exec(
            self.btn_export.mapToGlobal(self.btn_export.rect().bottomLeft())
        )
        if chosen is act_touch:
            self._export_touchstone()
        elif chosen is act_csv:
            self._export_csv()
        elif chosen is act_ascii:
            self._export_ascii()

    def _export_done(self, path: Path) -> None:
        self.lbl_info.setText(f"Exported: {path}")
        self.lbl_info.setToolTip(str(path))

    def _export_missing_note(self) -> list[str]:
        if self.network is None:
            return []
        missing = self._missing_traces(self.network)
        if not missing:
            return []
        return [
            "missing S-matrix cells: "
            + ", ".join(missing)
            + " (written as 0 in Touchstone; run all ports and merge)"
        ]

    def _export_touchstone(self) -> None:
        if self.network is None:
            return
        n = max(1, int(self.network.nports))
        default = self._export_default_dir() / f"{self._export_basename()}.s{n}p"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Touchstone",
            str(default),
            f"Touchstone (*.s{n}p *.s*p);;All (*.*)",
        )
        if not path:
            return
        try:
            out = export_network_touchstone(
                self.network,
                path,
                comments=[
                    f"source={self.network_path or 'viewer'}",
                    *self._export_missing_note(),
                ],
            )
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Touchstone export failed", str(e))
            return
        self._export_done(out)

    def _export_csv(self) -> None:
        if self.network is None:
            return
        default = self._export_default_dir() / f"{self._export_basename()}.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export S-parameters (CSV)", str(default), "CSV (*.csv);;All (*.*)"
        )
        if not path:
            return
        try:
            out = write_sparam_csv(self.network, path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "CSV export failed", str(e))
            return
        self._export_done(out)

    def _export_ascii(self) -> None:
        if self.network is None:
            return
        default = self._export_default_dir() / f"{self._export_basename()}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export S-parameters (ASCII)",
            str(default),
            "ASCII table (*.txt *.dat *.asc);;All (*.*)",
        )
        if not path:
            return
        try:
            out = write_sparam_ascii(self.network, path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "ASCII export failed", str(e))
            return
        self._export_done(out)

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
        head = f"{f_ghz:.4f} GHz {key}"
        if not (math.isfinite(val.real) and math.isfinite(val.imag)):
            return f"{head}  no data"
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

    def marker_state(self) -> list[dict]:
        """JSON-serialisable marker list for the project file."""
        return [
            {
                "f": float(f),
                "tag": str(self._marker_tags.get(float(f), "")),
                "delta_ref": (
                    float(self._delta_refs[float(f)])
                    if float(f) in self._delta_refs
                    else None
                ),
            }
            for f in self.markers
        ]

    def set_marker_state(self, state) -> None:
        """Restore markers from a project JSON; snap to loaded data points."""
        self.markers = []
        self._marker_tags.clear()
        self._delta_refs.clear()
        self._selected_marker = None
        for entry in state or []:
            if not isinstance(entry, dict):
                continue
            try:
                f = float(entry.get("f"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(f):
                continue
            if self.network is not None and len(self.network.f):
                f = float(self.network.f[int(np.argmin(np.abs(self.network.f - f)))])
            if f not in self.markers:
                self.markers.append(f)
            tag = str(entry.get("tag") or "")
            if tag:
                self._marker_tags[f] = tag
            ref = entry.get("delta_ref")
            if ref is not None:
                try:
                    rf = float(ref)
                except (TypeError, ValueError):
                    rf = None
                if rf is not None and math.isfinite(rf):
                    if self.network is not None and len(self.network.f):
                        rf = float(
                            self.network.f[
                                int(np.argmin(np.abs(self.network.f - rf)))
                            ]
                        )
                    self._delta_refs[f] = rf
        self.markers.sort()
        self._refresh_markers()
        self._redraw()
        self.markers_changed.emit()

    def _on_markers_visible_toggled(self, checked: bool) -> None:
        """Hide/show marker cursors; the marker list/project state is kept."""
        self._markers_visible = bool(checked)
        self._redraw()

    def _clear_markers(self) -> None:
        self.markers = []
        self._marker_tags.clear()
        self._delta_refs.clear()
        self._selected_marker = None
        # _refresh_markers() clears the list with signals blocked; clearing the
        # QListWidget first would emit currentItemChanged and re-enter _redraw.
        self._refresh_markers()
        self._redraw()
        self.markers_changed.emit()

    def _add_marker_at_freq(self, f: float, tag: str = "") -> float:
        """Add/snap a marker to the nearest data point and optionally tag it."""
        if self.network is None:
            return float(f)
        idx = int(np.argmin(np.abs(self.network.f - float(f))))
        snapped = float(self.network.f[idx])
        if snapped not in self.markers:
            self.markers.append(snapped)
            self.markers.sort()
        if tag:
            self._marker_tags[snapped] = tag
        self._selected_marker = snapped
        self._refresh_markers()
        self._redraw()
        self.markers_changed.emit()
        return snapped

    def _add_delta_marker(self, f_target: float) -> None:
        """Add a delta marker relative to the currently selected marker."""
        if self.network is None:
            return
        if self._selected_marker is None:
            QMessageBox.information(
                self,
                "Delta marker",
                "Select a marker row first; it becomes the delta reference.",
            )
            return
        ref = float(self._selected_marker)
        idx_ref = int(np.argmin(np.abs(self.network.f - ref)))
        ref = float(self.network.f[idx_ref])
        idx_t = int(np.argmin(np.abs(self.network.f - float(f_target))))
        target = float(self.network.f[idx_t])
        if abs(target - ref) < 1.0:
            QMessageBox.information(
                self,
                "Delta marker",
                "The delta marker needs a different frequency than the reference.",
            )
            return
        if target not in self.markers:
            self.markers.append(target)
            self.markers.sort()
        ref_tag = self._marker_tags.get(ref)
        ref_label = ref_tag or f"{ref / 1e9:.4f} GHz"
        self._marker_tags[target] = f"Δ from {ref_label}"
        self._delta_refs[target] = ref
        self._selected_marker = target
        self._refresh_markers()
        self._redraw()
        self.markers_changed.emit()

    def _delta_marker_text(
        self,
        key: str,
        s: np.ndarray | None,
        f_target: float,
        f_ref: float,
        z0: float,
    ) -> str:
        """One list row with frequencies and the dB difference only."""
        idx_t = int(np.argmin(np.abs(self.network.f - f_target)))
        idx_r = int(np.argmin(np.abs(self.network.f - f_ref)))
        head = f"Δ {self.network.f[idx_t]/1e9:.4f} GHz {key}"
        if s is None or idx_t >= len(s) or idx_r >= len(s):
            return f"{head}  no data"
        val = complex(s[idx_t])
        ref = complex(s[idx_r])
        if not (
            math.isfinite(val.real)
            and math.isfinite(val.imag)
            and math.isfinite(ref.real)
            and math.isfinite(ref.imag)
        ):
            return f"{head}  no data"
        df = (float(self.network.f[idx_t]) - float(self.network.f[idx_r])) / 1e9
        mag_t, mag_r = abs(val), abs(ref)
        d_db = 20.0 * math.log10(max(mag_t, 1e-15) / max(mag_r, 1e-15))
        parts = [
            head,
            f"Δf {df * 1000.0:+.3g} MHz",
            f"Δ|S| {d_db:+.3f} dB",
            f"(ref {self.network.f[idx_r]/1e9:.4f} GHz)",
        ]
        return "  ".join(parts)

    def _goto_min(self) -> None:
        if self.network is None or self.cmb_port.count() == 0:
            return
        key = self.cmb_port.currentText()
        s = self._trace(key)
        if s is None or not self._trace_has_data(s):
            return
        db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
        if not np.any(np.isfinite(db)):
            return
        i = int(np.nanargmin(db))
        self._add_marker_at_freq(float(self.network.f[i]), f"{key} min")

    def _goto_max(self) -> None:
        if self.network is None or self.cmb_port.count() == 0:
            return
        key = self.cmb_port.currentText()
        s = self._trace(key)
        if s is None or not self._trace_has_data(s):
            return
        db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
        if not np.any(np.isfinite(db)):
            return
        i = int(np.nanargmax(db))
        self._add_marker_at_freq(float(self.network.f[i]), f"{key} max")

    def _goto_nearest_extremum(self, f_click: float | None) -> None:
        if self.network is None or self.cmb_port.count() == 0:
            return
        key = self.cmb_port.currentText()
        s = self._trace(key)
        if s is None or not self._trace_has_data(s):
            return
        db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
        if db.size < 3 or not np.any(np.isfinite(db)):
            return
        freqs = np.asarray(self.network.f, dtype=float)
        peaks = [
            i
            for i in range(1, len(db) - 1)
            if np.isfinite(db[i - 1])
            and np.isfinite(db[i])
            and np.isfinite(db[i + 1])
            and db[i] >= db[i - 1]
            and db[i] >= db[i + 1]
        ]
        valleys = [
            i
            for i in range(1, len(db) - 1)
            if np.isfinite(db[i - 1])
            and np.isfinite(db[i])
            and np.isfinite(db[i + 1])
            and db[i] <= db[i - 1]
            and db[i] <= db[i + 1]
        ]
        candidates = peaks + valleys
        if not candidates:
            return
        target = float(f_click) if f_click is not None else float(np.mean(freqs))
        best = min(candidates, key=lambda i: abs(freqs[i] - target))
        tag = f"{key} peak" if best in peaks else f"{key} valley"
        self._add_marker_at_freq(float(freqs[best]), tag)

    def _mark_bandwidth(self) -> None:
        """Add markers at the -3 dB points around the primary trace peak/null."""
        if self.network is None or self.cmb_port.count() == 0:
            return
        key = self.cmb_port.currentText()
        s = self._trace(key)
        if s is None or not self._trace_has_data(s):
            return
        db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
        if db.size < 3 or not np.any(np.isfinite(db)):
            return
        freqs = np.asarray(self.network.f, dtype=float)
        reflection = self._is_reflection_key(key)
        if reflection:
            center = int(np.nanargmin(db))
            level = float(db[center] + 3.0)
            inside = lambda value: value < level
        else:
            center = int(np.nanargmax(db))
            level = float(db[center] - 3.0)
            inside = lambda value: value > level

        def _interp(i_a: int, i_b: int) -> float:
            d1, d2 = float(db[i_a]), float(db[i_b])
            if not (math.isfinite(d1) and math.isfinite(d2)) or abs(d2 - d1) < 1e-15:
                return float(freqs[i_b])
            t = (level - d1) / (d2 - d1)
            t = max(0.0, min(1.0, t))
            return float(freqs[i_a] + t * (freqs[i_b] - freqs[i_a]))

        left_i = None
        for i in range(center - 1, -1, -1):
            if not math.isfinite(float(db[i])):
                continue
            if not inside(float(db[i])):
                left_i = i
                break
        right_i = None
        for i in range(center + 1, len(db)):
            if not math.isfinite(float(db[i])):
                continue
            if not inside(float(db[i])):
                right_i = i
                break

        added: list[float] = []
        if left_i is not None:
            f_left = _interp(left_i, left_i + 1)
            added.append(self._add_marker_at_freq(f_left, f"{key} -3dB low"))
        if right_i is not None:
            f_right = _interp(right_i, right_i - 1)
            added.append(self._add_marker_at_freq(f_right, f"{key} -3dB high"))
        if not added:
            QMessageBox.information(
                self,
                "Markers",
                "No -3 dB crossings were found inside the current frequency band.",
            )
        elif len(added) == 1:
            QMessageBox.information(
                self,
                "Markers",
                "Only one -3 dB crossing was found inside the current frequency band.",
            )

    def _goto_band_center(self) -> None:
        if self.network is None or len(self.network.f) == 0:
            return
        center = float(np.mean([self.network.f[0], self.network.f[-1]]))
        self._add_marker_at_freq(center, "band center")

    def _open_analysis_at(self, event) -> None:
        pos = self.canvas.mapToGlobal(
            QPoint(int(event.x), int(event.y))
        )
        self._analysis_dlg.move(pos - self._analysis_dlg.rect().center())
        self._analysis_dlg.show()

    def _show_plot_context_menu(self, event) -> None:
        """Right-click marker helpers for the primary S-parameter trace."""
        f_click = self._freq_from_event(event) if self.network is not None else None
        menu = QMenu(self)
        act_here = menu.addAction("Add marker here")
        act_here.setEnabled(f_click is not None)
        act_delta = menu.addAction("Delta marker here (from selected)")
        act_delta.setEnabled(
            f_click is not None and self._selected_marker is not None
        )
        menu.addSeparator()
        act_min = menu.addAction("Marker at trace minimum")
        act_max = menu.addAction("Marker at trace maximum")
        act_ext = menu.addAction("Marker at nearest peak/valley")
        act_ext.setEnabled(f_click is not None)
        act_bw = menu.addAction("Mark -3 dB points")
        act_center = menu.addAction("Marker at band center")
        menu.addSeparator()
        act_clear = menu.addAction("Clear markers")
        act_analysis = menu.addAction("Analysis dialog…")
        act_popout = menu.addAction("Pop out graph")
        export_menu = menu.addMenu("Export S-parameters")
        act_export_touch = export_menu.addAction("Touchstone (.sNp)…")
        act_export_csv = export_menu.addAction("CSV (dB / phase / re / im)…")
        act_export_ascii = export_menu.addAction("ASCII table (.txt)…")
        act_export_touch.triggered.connect(self._export_touchstone)
        act_export_csv.triggered.connect(self._export_csv)
        act_export_ascii.triggered.connect(self._export_ascii)
        enabled = bool(self.network is not None and self.cmb_port.count())
        for act in (act_min, act_max, act_ext, act_bw, act_center, act_clear):
            act.setEnabled(enabled)
        for act in (act_export_touch, act_export_csv, act_export_ascii):
            act.setEnabled(self.network is not None)
        chosen = menu.exec(
            self.canvas.mapToGlobal(QPoint(int(event.x), int(event.y)))
        )
        if chosen is act_here and f_click is not None:
            self._add_marker_at_freq(f_click)
        elif chosen is act_delta and f_click is not None:
            self._add_delta_marker(f_click)
        elif chosen is act_min:
            self._goto_min()
        elif chosen is act_max:
            self._goto_max()
        elif chosen is act_ext:
            self._goto_nearest_extremum(f_click)
        elif chosen is act_bw:
            self._mark_bandwidth()
        elif chosen is act_center:
            self._goto_band_center()
        elif chosen is act_clear:
            self._clear_markers()
        elif chosen is act_analysis:
            self._open_analysis_at(event)
        elif chosen is act_popout:
            self.popout_graph()

    def _on_click(self, event) -> None:
        if event.button == 3 and event.inaxes is not None:
            self._show_plot_context_menu(event)
            return
        if event.inaxes is None or event.button != 1 or self.network is None:
            return
        if event.xdata is None:
            return
        # Smith chart: xdata is complex-ish (Re); still pick nearest in freq via mouse xy
        f = self._freq_from_event(event)
        if f is None:
            return
        self._add_marker_at_freq(f)

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
        if not self._trace_has_data(s) or not (
            math.isfinite(val.real) and math.isfinite(val.imag)
        ):
            base = self.lbl_info.text().split(" | cursor")[0]
            self.lbl_info.setText(
                f"{base} | cursor {self.network.f[idx]/1e9:.4f} GHz {key} "
                "no data (not simulated)"
            )
            self._draw_cursor(idx)
            return
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
            if not np.any(np.isfinite(d)):
                return None
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

    def _on_marker_list_selected(self, current, _previous=None) -> None:
        """Selecting a marker row highlights the same marker in the plot."""
        if current is None:
            self._selected_marker = None
        else:
            raw = current.data(Qt.ItemDataRole.UserRole)
            self._selected_marker = float(raw) if raw is not None else None
        self._redraw()

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
        """Rebuild one row per marker * selected trace (same marker frequency).

        The marker is a frequency, not a trace: every ticked S-parameter gets a
        value row for it.  Selecting/deselecting a trace updates this list on
        the next redraw, including explicit ``no data`` rows for empty cells.
        """
        current = self.marker_list.currentItem()
        current_key = (
            current.data(Qt.ItemDataRole.UserRole + 1) if current is not None else None
        )
        self.marker_list.blockSignals(True)
        try:
            self.marker_list.clear()
            if self.network is None:
                self._selected_marker = None
                return
            keys = self._checked_traces()
            if not keys:
                primary = self.cmb_port.currentText() if self.cmb_port.count() else ""
                keys = [primary] if primary else ["S11"]
            z0 = self._z0_from_network()
            self._marker_z0 = z0
            for f in self.markers:
                idx = int(np.argmin(np.abs(self.network.f - f)))
                f_ghz = float(self.network.f[idx]) / 1e9
                ref_f = self._delta_refs.get(float(f))
                tag = self._marker_tags.get(float(f))
                for key in keys:
                    s = self._trace(key)
                    has_data = s is not None and self._trace_has_data(s)
                    if ref_f is not None:
                        text = self._delta_marker_text(
                            key, s if has_data else None, float(f), float(ref_f), z0
                        )
                    else:
                        val = (
                            complex(s[idx])
                            if has_data and idx < len(s)
                            else complex("nan")
                        )
                        text = self._marker_list_text(key, val, f_ghz, z0)
                    if tag:
                        text = f"{tag}  |  {text}"
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, float(self.network.f[idx]))
                    item.setData(Qt.ItemDataRole.UserRole + 1, key)
                    self.marker_list.addItem(item)
            # Keep the selected marker row visible after a rebuild.  All trace
            # rows of a marker share the same frequency, so the first match is
            # enough to keep the plot highlight.
            if self._selected_marker is not None and self.markers:
                candidates: list[int] = []
                for row in range(self.marker_list.count()):
                    item = self.marker_list.item(row)
                    raw = item.data(Qt.ItemDataRole.UserRole) if item else None
                    if raw is not None and abs(float(raw) - self._selected_marker) < 1.0:
                        candidates.append(row)
                selected_row = None
                if current_key:
                    for row in candidates:
                        item = self.marker_list.item(row)
                        if item.data(Qt.ItemDataRole.UserRole + 1) == current_key:
                            selected_row = row
                            break
                if selected_row is None and candidates:
                    selected_row = candidates[0]
                if selected_row is not None:
                    self.marker_list.setCurrentRow(selected_row)
                else:
                    self._selected_marker = None
        finally:
            self.marker_list.blockSignals(False)

    def _on_marker_context_menu(self, pos) -> None:
        """Right-click a marker row to delete it."""
        item = self.marker_list.itemAt(pos)
        if item is None:
            return
        self.marker_list.setCurrentItem(item)
        menu = QMenu(self.marker_list)
        act_copy = menu.addAction("Copy marker value")
        act_freq = menu.addAction("Copy frequency")
        menu.addSeparator()
        act_del = menu.addAction("Delete marker")
        chosen = menu.exec(self.marker_list.viewport().mapToGlobal(pos))
        row = self.marker_list.row(item)
        if chosen is act_copy:
            self._copy_marker_value(row)
        elif chosen is act_freq:
            self._copy_marker_frequency(row)
        elif chosen is act_del:
            self._delete_marker_at(row)

    def _copy_marker_value(self, row: int) -> None:
        item = self.marker_list.item(row)
        if item is not None:
            QApplication.clipboard().setText(item.text())

    def _copy_marker_frequency(self, row: int) -> None:
        item = self.marker_list.item(row)
        if item is None:
            return
        raw = item.data(Qt.ItemDataRole.UserRole)
        if raw is None:
            return
        QApplication.clipboard().setText(f"{float(raw) / 1e9:.9g} GHz")

    def _delete_marker_at(self, row: int) -> None:
        """Remove the marker shown by a marker-list row (all its trace rows)."""
        item = self.marker_list.item(row)
        if item is None:
            return
        raw = item.data(Qt.ItemDataRole.UserRole)
        if raw is None:
            return
        f_row = float(raw)
        matches = [mf for mf in self.markers if abs(mf - f_row) < 1.0]
        if not matches:
            return
        f = matches[0]
        # Remove delta markers that referenced this marker, and the delta
        # link when the deleted marker itself was a delta.
        dependents = [
            mf for mf, rf in self._delta_refs.items() if abs(rf - f) < 1.0
        ]
        for mf in dependents + [f]:
            self._marker_tags.pop(float(mf), None)
            self._delta_refs.pop(float(mf), None)
            if mf in self.markers:
                self.markers.remove(mf)
        if self._selected_marker is not None and (
            self._selected_marker in dependents
            or abs(f - self._selected_marker) < 1.0
        ):
            self._selected_marker = None
        self._refresh_markers()
        self._redraw()
        self.markers_changed.emit()

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
        keys = self._checked_traces()
        traces: list[tuple[str, np.ndarray]] = []
        for name in keys:
            data = self._trace(name)
            if data is not None and self._trace_has_data(data):
                traces.append((name, data))
        if not traces:
            # untouched S-matrix cell (NaN/0): do not autoscale to it, which
            # leaves a meaningless axis and looks like a broken scale
            key = keys[0] if keys else "S11"
            ports = "port" if self.network.nports == 1 else "ports"
            ax.text(
                0.5,
                0.5,
                f"{key}: no data in this file\n\n"
                f"This {self.network.nports}-{ports} network stores the cell, but it\n"
                "was never simulated (each FDTD run measures only the\n"
                "column of its excited port).\n\n"
                "Simulation ▸ Run All Ports (full S-matrix)\n"
                "runs one FDTD run per port and merges the columns.",
                ha="center",
                va="center",
                fontsize=10,
            )
            ax.set_xticks([])
            ax.set_yticks([])
            self.canvas.draw_idle()
            self._refresh_markers()
            return
        key, s = traces[0]

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
        elif plot == "Return loss [dB]":
            rl = -20 * np.log10(np.maximum(np.abs(s), 1e-15))
            ax.plot(f_ghz, rl, lw=1.4, label=key)
            ax.set_ylabel("Return loss [dB]")
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

        # --- overlay the other checked traces -----------------------------
        if len(traces) > 1 and plot == "Smith":
            for idx, (extra_key, extra_s) in enumerate(traces[1:], start=1):
                self._draw_smith_extra(ax, extra_s, extra_key, f"C{idx % 10}")
            ax.set_title(
                f"Smith chart — {len(traces)} traces  "
                f"[Z0={z0:g} Ω]"
            )
            ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
        elif len(traces) > 1 and plot in (
            "|S| dB",
            "Return loss [dB]",
            "|S| mag",
            "Phase deg",
            "VSWR",
            "Complex Re/Im",
            "Z Re/Im",
        ):
            for idx, (extra_key, extra_s) in enumerate(traces[1:], start=1):
                color = f"C{idx % 10}"
                if plot == "|S| dB":
                    extra_db = 20 * np.log10(np.maximum(np.abs(extra_s), 1e-15))
                    ax.plot(f_ghz, extra_db, lw=1.3, color=color, label=extra_key)
                elif plot == "Return loss [dB]":
                    extra_rl = -20 * np.log10(np.maximum(np.abs(extra_s), 1e-15))
                    ax.plot(f_ghz, extra_rl, lw=1.3, color=color, label=extra_key)
                elif plot == "|S| mag":
                    ax.plot(f_ghz, np.abs(extra_s), lw=1.3, color=color, label=extra_key)
                elif plot == "Phase deg":
                    ax.plot(
                        f_ghz,
                        np.degrees(np.angle(extra_s)),
                        lw=1.3,
                        color=color,
                        label=extra_key,
                    )
                elif plot == "VSWR":
                    mag = np.abs(extra_s)
                    vswr = (1 + mag) / np.maximum(1 - mag, 1e-12)
                    ax.plot(f_ghz, vswr, lw=1.3, color=color, label=extra_key)
                elif plot == "Complex Re/Im":
                    ax.plot(
                        f_ghz, np.real(extra_s), lw=1.2, color=color,
                        label=f"{extra_key} Re",
                    )
                    ax.plot(
                        f_ghz, np.imag(extra_s), lw=1.0, ls="--", color=color,
                        label=f"{extra_key} Im",
                    )
                elif plot == "Z Re/Im":
                    if self._is_reflection_key(extra_key):
                        z = np.array(
                            [self._s_to_z(complex(v), z0) for v in extra_s],
                            dtype=complex,
                        )
                        ax.plot(
                            f_ghz, np.real(z), lw=1.2, color=color,
                            label=f"{extra_key} R",
                        )
                        ax.plot(
                            f_ghz, np.imag(z), lw=1.0, ls="--", color=color,
                            label=f"{extra_key} X",
                        )
                    else:
                        ax.plot(
                            f_ghz, np.real(extra_s), lw=1.2, color=color,
                            label=f"{extra_key} Re",
                        )
                        ax.plot(
                            f_ghz, np.imag(extra_s), lw=1.0, ls="--", color=color,
                            label=f"{extra_key} Im",
                        )
            ax.legend(loc="best", fontsize=8)

        if plot != "Smith":
            ax.set_xlabel("Frequency [GHz]")
            # markers as vertical cursors; the selected row gets a bold line
            for f in (self.markers if self._markers_visible else []):
                x = f / 1e9
                selected = (
                    self._selected_marker is not None
                    and abs(f - self._selected_marker) < 1.0
                )
                is_delta = float(f) in self._delta_refs
                if selected:
                    color, ls, lw, alpha, zorder = "black", "-", 2.2, 0.95, 8
                elif is_delta:
                    color, ls, lw, alpha, zorder = "darkviolet", "-.", 1.6, 0.95, 5
                else:
                    color, ls, lw, alpha, zorder = "crimson", "--", 1.0, 0.85, 2
                artist = ax.axvline(
                    x,
                    color=color,
                    ls=ls,
                    lw=lw,
                    alpha=alpha,
                    zorder=zorder,
                )
                self._marker_artists.append(artist)
        else:
            self._draw_smith_markers(ax, s, key, z0)

        self.canvas.draw_idle()
        self._refresh_markers()

    def _draw_smith_markers(self, ax, s: np.ndarray, key: str, z0: float) -> None:
        """Marker dots + optional Z labels on the Smith chart."""
        if not self._markers_visible or not self.markers or self.network is None:
            return
        refl = self._is_reflection_key(key)
        for f in self.markers:
            idx = int(np.argmin(np.abs(self.network.f - f)))
            val = complex(s[idx])
            if not (math.isfinite(val.real) and math.isfinite(val.imag)):
                continue
            re, im = float(np.real(val)), float(np.imag(val))
            selected = (
                self._selected_marker is not None
                and abs(f - self._selected_marker) < 1.0
            )
            is_delta = float(f) in self._delta_refs
            if selected:
                color, marker, ms, edge, edge_w, zorder = (
                    "black", "o", 10, "gold", 1.5, 9
                )
            elif is_delta:
                color, marker, ms, edge, edge_w, zorder = (
                    "darkviolet", "D", 7, "black", 0.6, 7
                )
            else:
                color, marker, ms, edge, edge_w, zorder = (
                    "crimson", "o", 6, "black", 0.5, 6
                )
            artist = ax.plot(
                re,
                im,
                marker,
                color=color,
                ms=ms,
                markeredgecolor=edge,
                markeredgewidth=edge_w,
                zorder=zorder,
            )[0]
            self._marker_artists.append(artist)
            label = f"{self.network.f[idx]/1e9:.3f}"
            if is_delta:
                label = "Δ " + label
            if refl:
                z = self._s_to_z(val, z0)
                if math.isfinite(z.real) and math.isfinite(z.imag):
                    label += f"\n{z.real:.1f}+j{z.imag:.1f}"
                else:
                    label += "\n∞"
            else:
                label += f"\n{abs(val):.3g}"
            # Keep labels readable: only annotate first few + last
            n_markers = len(self.markers)
            if (
                selected
                or is_delta
                or idx == int(np.argmin(np.abs(self.network.f - self.markers[0])))
                or n_markers <= 4
            ):
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

        # Trace + start/end (skrf Network.plot_s_smith style); NaN samples
        # (unmeasured cells) are skipped and never become start/end points.
        ok = np.isfinite(s)
        re, im = np.real(s), np.imag(s)
        ax.plot(re, im, "-", color="tab:blue", lw=1.6, zorder=4, label=key)
        if ok.any():
            first, last = int(np.flatnonzero(ok)[0]), int(np.flatnonzero(ok)[-1])
            ax.plot(re[first], im[first], "g+", ms=10, mew=2, zorder=5, label="start")
            ax.plot(re[last], im[last], "ks", ms=6, zorder=5, label="end")
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

    def _draw_smith_extra(
        self, ax, s: np.ndarray, key: str, color: str
    ) -> None:
        """Overlay one more reflection trace on an existing Smith chart."""
        ok = np.isfinite(s)
        if not ok.any():
            return
        ax.plot(
            np.real(s),
            np.imag(s),
            "-",
            color=color,
            lw=1.3,
            zorder=4,
            label=key,
        )

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
        if not self._trace_has_data(s) or not (
            math.isfinite(val.real) and math.isfinite(val.imag)
        ):
            self.canvas.draw_idle()
            return
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
            elif plot == "Return loss [dB]":
                y = -db
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
