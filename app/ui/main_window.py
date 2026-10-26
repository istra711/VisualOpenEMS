"""Main window — English UI for FreeCAD → openEMS workflow."""

from __future__ import annotations

import time
import traceback
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

from PySide6 import QtCore
from PySide6.QtCore import QUrl, QObject, Qt, QTimer, Signal, QThread
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.freecad_io.importer import load_geometry_file, overall_bounds
from app.models.project import GeomAssignment, PortDef, Project
from app.runlog import format_run_end, format_run_start, format_run_step
from app.settings import AppSettings
from app.simulation.progress import EngineProgress
from app.simulation.runner import SimulationRunner
from app.simulation.script_gen import generate_run_script
from app.csxcad_builder.primitives import patch_antenna_primitives
from app.csxcad_builder.quantize import f32 as _f32
from app.freecad_io.freecad_mesh import tessellate_cad_to_meshes
from app.ui.object_display import DisplayOptionsWidget, normalize_display
from app.ui.tab_help import show_tab_help

# Tree kinds the context menu / geometry menu can duplicate (geom and nf2ff
# are singleton/read-only entries).
DUP_TREE_KINDS = frozenset({"prim", "assign", "mat", "port", "dump"})


def _read_text(path: Path) -> str | None:
    """File content, or None when the file is missing/unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


class _LogBridge(QObject):
    """Thread-safe log hop: worker threads emit, UI thread runs the slot."""

    line = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self.settings = settings
        self.project = Project.new_default("untitled")
        self.primitives: list[dict] = []
        self.runner = SimulationRunner(settings, app_root=APP_ROOT)
        self._current_file_path: Path | None = None
        # signature of the project state the Mesh Viewer's CSX was built from
        self._mesh_sig: str | None = None
        #: signature the viewer's current (geometry-only) scene was built from
        self._viewer_geometry_sig: str | None = None
        #: True while the tree / material list is rebuilt (programmatic selection)
        self._tree_rebuilding = False
        #: serial of the selection (a click that changed it must not toggle)
        self._tree_selection_serial = 0
        self._tree_last_click_serial = -1
        self._tree_last_click = 0.0
        # user deleted the mesh -> suppress the automatic rebuild until the next
        # explicit Build Mesh
        self._mesh_deleted = False
        # background mesh build (worker thread) state
        self._mesh_building = False
        self._mesh_thread = None
        #: MeshTiming report of the last mesh build (Mesh menu -> Mesh Timing)
        self._last_mesh_timing = None
        #: Phase-0 coordinate probe of the last mesh build (read-only)
        self._last_coord_diag = None
        #: text of the last post-run status block (Simulation menu -> Status)
        self._last_sim_report_text = ""
        # reserved: Mesh-Editor edits are applied to the existing mesh directly
        # (no full rebuild), so no auto-build timer is needed anymore
        self._auto_mesh_build = False
        # multi-excitation driver state (Simulation → Run All Ports)
        self._multi_plans: list | None = None
        self._multi_pos: int = 0
        self._multi_timer = None
        # run-log session: monotonic start time (None when no run is active)
        self._run_t0: float | None = None
        self._abort_requested = False
        self._build_ui()
        self._apply_window_geometry()
        self._update_window_title()
        self.statusBar().showMessage("Ready")

    def _update_window_title(self) -> None:
        """Title = app name + project name (+ file name) so windows differ."""
        try:
            name = str(getattr(self.project, "name", "") or "no project").strip()
        except Exception:  # noqa: BLE001
            name = "no project"
        path = getattr(self, "_current_file_path", None)
        suffix = f"  —  {Path(path).name}" if path else ""
        self.setWindowTitle(f"FreeCAD OpenEMS GUI — {name}{suffix}")

    def _screen_avail(self):
        screen = self.screen()
        if screen is None:
            from PySide6.QtWidgets import QApplication

            screen = QApplication.primaryScreen()
        if screen is None:
            return QtCore.QRect(0, 0, 1280, 800)
        return screen.availableGeometry()

    def _dialog_size(self, key: str) -> tuple[int, int] | None:
        """Remembered size of a resizable dialog (``None`` -> its default)."""
        try:
            w, h = (int(v) for v in (self.settings.dialog_sizes or {}).get(key, []))
        except Exception:  # noqa: BLE001 — corrupt/old entry: use the default
            return None
        return (w, h) if w >= 320 and h >= 260 else None

    def _remember_dialog_size(self, key: str, dlg) -> None:
        """Remember a resizable dialog's size for the next open (clamped)."""
        avail = self._screen_avail()
        size = dlg.size()
        w = max(320, min(int(size.width()), max(320, avail.width() - 40)))
        h = max(260, min(int(size.height()), max(260, avail.height() - 40)))
        sizes = dict(self.settings.dialog_sizes or {})
        if list(sizes.get(key) or []) == [w, h]:
            return
        sizes[key] = [w, h]
        self.settings.dialog_sizes = sizes
        try:
            self.settings.save()
        except Exception as e:  # noqa: BLE001 — never block a dialog close
            print("save dialog size failed:", e)

    def _apply_window_geometry(self) -> None:
        """Restore saved geometry, else fit on the primary available screen."""
        avail = self._screen_avail()
        # Adaptive minimum: never force a size larger than the screen
        min_w = max(700, min(900, avail.width() - 40))
        min_h = max(450, min(600, avail.height() - 40))
        self.setMinimumSize(min_w, min_h)

        geo = getattr(self.settings, "window_geometry", "") or ""
        if geo:
            try:
                import base64

                data = base64.b64decode(geo)
                if self.restoreGeometry(QtCore.QByteArray(data)):
                    self._clamp_to_screen(avail)
                    return
            except Exception as e:  # noqa: BLE001
                print("restore geometry failed:", e)

        self._fit_default(avail)

    def _fit_default(self, avail) -> None:
        """~92% of available space, clamped to screen, centered."""
        min_w = max(700, min(900, avail.width() - 40))
        min_h = max(450, min(600, avail.height() - 40))
        w = int(avail.width() * 0.92)
        h = int(avail.height() * 0.92)
        w = max(min_w, min(w, avail.width() - 16, 1600))
        h = max(min_h, min(h, avail.height() - 16, 900))
        self.resize(w, h)
        x = avail.x() + max(0, (avail.width() - w) // 2)
        y = avail.y() + max(0, (avail.height() - h) // 2)
        self.move(x, y)

    def _clamp_to_screen(self, avail) -> None:
        geo = self.frameGeometry()
        if geo.width() > avail.width() or geo.height() > avail.height():
            self._fit_default(avail)
            return
        # pull fully on-screen if needed
        x = self.x()
        y = self.y()
        if x < avail.x():
            x = avail.x()
        if y < avail.y():
            y = avail.y()
        if x + geo.width() > avail.x() + avail.width():
            x = max(avail.x(), avail.x() + avail.width() - geo.width())
        if y + geo.height() > avail.y() + avail.height():
            y = max(avail.y(), avail.y() + avail.height() - geo.height())
        self.move(x, y)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Final safety: if something (sizeHint/docks) blew past the screen, refit.
        avail = self._screen_avail()
        if self.width() > avail.width() or self.height() > avail.height():
            self._fit_default(avail)

    def closeEvent(self, event) -> None:  # noqa: N802
        # Let a running background mesh build finish before tearing down the
        # CSX/FDTD objects and the GUI thread.
        thread = getattr(self, "_mesh_thread", None)
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.wait(5000)
            except Exception as e:  # noqa: BLE001
                print("wait for mesh thread failed:", e)
        try:
            import base64

            data = bytes(self.saveGeometry())
            self.settings.window_geometry = base64.b64encode(data).decode("ascii")
            self.settings.log_panel_visible = self.btn_log_toggle.isChecked()
            self.settings.project_panel_visible = self.btn_project_toggle.isChecked()
            self.settings.save()
        except Exception as e:  # noqa: BLE001
            print("save geometry failed:", e)
        super().closeEvent(event)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Hidden off-screen action buttons (kept referenced — a bare local would
        # be GC'd and its C++ object deleted). All of them are reachable via the
        # menu bar; they only exist as the menu's click/state targets (esp.
        # btn_log_toggle for the View menu check state, btn_help for the label,
        # btn_build_mesh for run-time enable/disable).
        self.btn_new = QPushButton("New Project")
        self.btn_open = QPushButton("Open…")
        self.btn_save = QPushButton("Save…")
        self.btn_import = QPushButton("Import Geometry…")
        self.btn_freecad = QPushButton("FreeCAD…")
        self.btn_freecad.setToolTip(
            "Launch FreeCAD GUI or export FCStd/STEP → meshes for openEMS"
        )
        self.btn_patch = QPushButton("Preset: Patch Antenna…")
        self.btn_yagi = QPushButton("Preset: Yagi Antenna…")
        self.btn_yagi.setToolTip(
            "2.45 GHz Yagi-Uda + FreeCAD model (FCStd/STEP/STL) under work/yagi_antenna"
        )
        self.btn_pcb = QPushButton("Preset: PCB Filter…")
        self.btn_pcb.setToolTip(
            "2.45 GHz microstrip notch filter on FR4 (2 ports) under work/pcb_filter"
        )
        self.btn_pcb_bp = QPushButton("Preset: PCB Bandpass…")
        self.btn_pcb_bp.setToolTip(
            "5.6 GHz 5-pole Chebyshev band-pass, five open stubs on PTFE "
            "(2 ports) under work/pcb_bandpass"
        )
        self.btn_pcb_hairpin = QPushButton("Preset: PCB Hairpin…")
        self.btn_pcb_hairpin.setToolTip(
            "2.4 GHz compact 5-pole hairpin band-pass on RT/duroid 6010, "
            "10 % bandwidth, no vias (2 ports) under work/pcb_hairpin"
        )
        self.btn_run = QPushButton("Run Simulation")
        self.btn_abort = QPushButton("Abort")
        self.btn_script = QPushButton("Script…")
        self.btn_script.setToolTip(
            "View / edit the generated project_run.py (Run Simulation regenerates it)"
        )
        self.btn_settings = QPushButton("Settings…")
        self.btn_sparams = QPushButton("S-Params Viewer")
        self.btn_sparams_popout = QPushButton("Pop-out S-Graph")
        self.btn_sparams_popout.setToolTip("Open the S-param plot in a separate window")
        self.btn_antenna = QPushButton("Antenna Pattern Viewer")
        self.btn_antenna_popout = QPushButton("Pop-out Antenna Graph")
        self.btn_antenna_popout.setToolTip("Open the antenna pattern plot in a separate window")
        self.btn_log_toggle = QPushButton("Log")
        self.btn_log_toggle.setCheckable(True)
        self.btn_log_toggle.setChecked(bool(getattr(self.settings, "log_panel_visible", True)))
        # checkable state target for View → "Project / Geometry Panel" (the pane
        # itself has no button; show/hide also works from its right-click menu)
        self.btn_project_toggle = QPushButton("Project")
        self.btn_project_toggle.setCheckable(True)
        self.btn_project_toggle.setChecked(
            bool(getattr(self.settings, "project_panel_visible", True))
        )
        self.btn_help = QPushButton("Help")
        self.btn_help.setToolTip("Open help for the currently selected tab")
        self.btn_exit = QPushButton("Exit")
        self._hidden_toolbar_buttons: list[QPushButton] = [
            self.btn_new,
            self.btn_open,
            self.btn_save,
            self.btn_import,
            self.btn_freecad,
            self.btn_patch,
            self.btn_yagi,
            self.btn_pcb,
            self.btn_pcb_bp,
            self.btn_pcb_hairpin,
            self.btn_run,
            self.btn_abort,
            self.btn_script,
            self.btn_settings,
            self.btn_sparams,
            self.btn_sparams_popout,
            self.btn_antenna,
            self.btn_antenna_popout,
            self.btn_log_toggle,
            self.btn_project_toggle,
            self.btn_help,
            self.btn_exit,
        ]
        for b in self._hidden_toolbar_buttons:
            b.hide()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter = splitter  # holds [Project/Geometry pane, tabs]
        root.addWidget(splitter, 1)

        # Left: project tree + geometry
        left = QWidget()
        self.project_panel = left
        self._project_panel_sizes: list[int] | None = None
        lv = QVBoxLayout(left)
        self.lbl_project_panel = QLabel("Project / Geometry")
        lv.addWidget(self.lbl_project_panel)
        self.tree = QListWidget()
        # No buttons below the tree any more: everything is reachable via the
        # Geometry menu (Add/Edit/Duplicate/Delete/NF2FF) or right-click.
        self.tree.setToolTip(
            "Project structure.\n"
            "Double-click to edit · right-click for Edit/Duplicate/Remove · Delete key removes the selection.\n"
            "Geom entries are imported CAD — edit geometry in FreeCAD.\n"
            "Right-click on empty space: hide this panel (View → Project / Geometry Panel, Ctrl+B)."
        )
        lv.addWidget(self.tree)
        self.tree.itemDoubleClicked.connect(self._on_tree_double_clicked)
        # selecting an entry highlights the object in the Mesh Viewer; a second
        # click on the already selected row toggles only the viewer mark
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        self.tree.keyPressEvent = self._tree_key_press  # type: ignore[method-assign]
        # right-click on the panel background / its title, too
        for w in (left, self.lbl_project_panel):
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            w.customContextMenuRequested.connect(self._show_project_panel_context_menu)
        splitter.addWidget(left)

        # Right tabs
        tabs = QTabWidget()
        # Vertical split: workflow tabs on top, log panel below
        # (not a QDockWidget, not a tab — avoids duplicate log UI).
        self.right_split = QSplitter(Qt.Orientation.Vertical)
        self.right_split.addWidget(tabs)
        splitter.addWidget(self.right_split)

        # Geometry tab
        geom_tab = QWidget()
        gv = QVBoxLayout(geom_tab)
        self.geom_info = QPlainTextEdit()
        self.geom_info.setReadOnly(True)
        gv.addWidget(QLabel("Imported objects"))
        gv.addWidget(self.geom_info)
        self.mat_list = QListWidget()
        gv.addWidget(QLabel("Materials"))
        gv.addWidget(self.mat_list)
        # a material picked here highlights its objects in the Mesh Viewer too
        self.mat_list.itemSelectionChanged.connect(self._on_material_selection_changed)
        self._refresh_materials()
        tabs.addTab(geom_tab, "Geometry")

        # Mesh form (hidden off-screen — canonical sync/edit target for the
        # Mesh Parameters dialog + build). The Mesh tab page was removed in
        # favour of the Mesh Parameters window (Mesh menu → Mesh Parameters…).
        # Keep a strong ref: without a parent a hidden QWidget would be GC'd
        # and its child spins/combos would become deleted C++ objects.
        self._mesh_form = QWidget()
        self._mesh_form.hide()
        mv = QVBoxLayout(self._mesh_form)
        top = QHBoxLayout()
        form = QFormLayout()
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(["low", "medium", "high", "very_high"])
        self.cmb_preset.setCurrentText("high")
        from app.ui.freq_units import (
            FREQ_UNITS,
            configure_freq_spin,
            set_spin_hz,
            spin_hz,
            unit_combo_items,
        )

        self._freq_unit_name = "GHz"
        self._freq_unit_factor = float(FREQ_UNITS["GHz"])
        self.cmb_freq_unit = QComboBox()
        unit_combo_items(self.cmb_freq_unit)
        self.cmb_freq_unit.setCurrentText(self._freq_unit_name)
        self.cmb_freq_unit.setToolTip(
            "Display unit for frequencies (Hz / kHz / MHz / GHz).\n"
            "Values are always stored and simulated in Hz."
        )

        self.spn_f0 = QDoubleSpinBox()
        self.spn_f0.setKeyboardTracking(False)
        configure_freq_spin(self.spn_f0, self._freq_unit_name)
        self.spn_f0.setValue(2.45)  # GHz display default
        self.spn_f0.setToolTip(
            "Center/design frequency f0: mesh target frequency and the centre of the\n"
            "Gauss (and Sinus) excitation.\n"
            "Editing f0/fc updates Start/Stop as f0±fc."
        )
        self.spn_fc = QDoubleSpinBox()
        self.spn_fc.setKeyboardTracking(False)
        configure_freq_spin(self.spn_fc, self._freq_unit_name)
        self.spn_fc.setValue(0.5)
        self.spn_fc.setToolTip(
            "Gauss corner frequency (bandwidth/2) — used by the Gauss excitation only.\n"
            "Editing f0/fc updates Start/Stop as f0±fc."
        )
        from app.simulation.excitation import available_kinds, kind_label

        self.cmb_excite = QComboBox()
        for _kind in available_kinds():
            self.cmb_excite.addItem(kind_label(_kind), _kind)
        self.cmb_excite.setToolTip(
            "FDTD excitation waveform (Run / generated project_run.py):\n"
            "Gauss — broadband pulse defined by f0/fc (default, best for S-params).\n"
            "Sinus — single frequency f0; S-params are only meaningful near f0.\n"
            "Step — broadband step, normalised to the band's highest frequency.\n"
            "Dirac — broadband impulse.\n"
            "Custom — two-column CSV (time in seconds, amplitude); needs an\n"
            "openEMS build with SetCustomExcite."
        )
        self.edt_excite_file = QLineEdit()
        self.edt_excite_file.setReadOnly(True)
        self.edt_excite_file.setPlaceholderText("no file selected")
        self.edt_excite_file.setToolTip(
            "Custom excitation waveform: two columns (time [s], amplitude).\n"
            "Comma/semicolon/tab/space separated, optional header, # comments."
        )
        self.btn_excite_file = QPushButton("…")
        self.btn_excite_file.setToolTip("Choose the custom excitation CSV file")
        exc_file_row = QWidget()
        exc_file_layout = QHBoxLayout(exc_file_row)
        exc_file_layout.setContentsMargins(0, 0, 0, 0)
        exc_file_layout.addWidget(self.edt_excite_file, 1)
        exc_file_layout.addWidget(self.btn_excite_file)
        self.wdg_excite_file = exc_file_row
        self._excite_guard = False
        self._update_excitation_state()
        self.spn_start_f = QDoubleSpinBox()
        self.spn_start_f.setKeyboardTracking(False)
        configure_freq_spin(self.spn_start_f, self._freq_unit_name)
        self.spn_start_f.setValue(1.95)
        self.spn_start_f.setToolTip(
            "Simulation start frequency (mesh λ + S-param range).\n"
            "Edit this and Stop directly — independent of f0/fc."
        )
        self.spn_stop_f = QDoubleSpinBox()
        self.spn_stop_f.setKeyboardTracking(False)
        configure_freq_spin(self.spn_stop_f, self._freq_unit_name)
        self.spn_stop_f.setValue(2.95)
        self.spn_stop_f.setToolTip(
            "Simulation stop frequency (mesh λ + S-param range).\n"
            "Edit this and Start directly — independent of f0/fc."
        )
        self.cmb_unit = QComboBox()
        self.cmb_unit.addItems(["mm", "um", "m"])
        self.cmb_coords = QComboBox()
        self.cmb_coords.addItems(["cartesian", "cylindrical"])
        self.cmb_coords.setToolTip(
            "FDTD coordinate system (mirror of Mesh Parameters → Coordinate system)"
        )
        form.addRow("Mesh preset", self.cmb_preset)
        form.addRow("Frequency unit", self.cmb_freq_unit)
        form.addRow("Center frequency f0", self.spn_f0)
        form.addRow("Corner frequency fc", self.spn_fc)
        form.addRow("Excitation", self.cmb_excite)
        form.addRow("Custom waveform", exc_file_row)
        form.addRow("Start frequency", self.spn_start_f)
        form.addRow("Stop frequency", self.spn_stop_f)
        form.addRow("Drawing unit", self.cmb_unit)
        form.addRow("Coordinate system", self.cmb_coords)
        top.addLayout(form)
        top.addStretch(1)
        self.btn_build_mesh = QPushButton("Build Mesh")
        top.addWidget(self.btn_build_mesh)
        mv.addLayout(top)

        # Mesh check report (single source: _set_mesh_report fills the log
        # panel's Mesh Report tab; this widget is the legacy off-screen target)
        self.mesh_report = QPlainTextEdit()
        self.mesh_report.setReadOnly(True)
        self.mesh_report.hide()
        mv.addWidget(self.mesh_report)

        # Mesh Viewer tab — full-size interactive slice / cell-size plots
        from app.ui.mesh_viewer import MeshGeometryViewer

        viewer_tab = QWidget()
        viewer_layout = QVBoxLayout(viewer_tab)
        viewer_layout.setContentsMargins(0, 0, 0, 0)
        self.mesh_viewer = MeshGeometryViewer(work_dir=self.settings.work_path())
        self.mesh_viewer.mesh_edited.connect(self._on_mesh_edited)
        self.mesh_viewer.critical_changed.connect(self._on_critical_overlay_changed)
        self.mesh_viewer.critical_cells_changed.connect(self._sync_critical_menu)
        self.mesh_viewer.rebuild_requested.connect(self._on_rebuild_scratch)
        self.mesh_viewer.port_place_requested.connect(self._on_mesh_port_place)
        self.mesh_viewer.port_mode_changed.connect(self._on_mesh_port_mode_changed)
        self.mesh_viewer.port_type_changed.connect(self._on_mesh_port_type_changed)
        viewer_layout.addWidget(self.mesh_viewer)
        self.mesh_viewer_tab_index = tabs.addTab(viewer_tab, "Mesh Viewer")

        # Sim Params (hidden off-screen — canonical sync/edit target). The
        # Sim Params tab page was removed in favour of the Simulation
        # Parameters window (Simulation menu → Simulation Parameters…).
        # Keep a strong ref: without a parent a hidden QWidget would be GC'd
        # and its child spins/combos would become deleted C++ objects.
        from app.ui.sim_params import SimParamsPanel

        self._sim_form = QWidget()
        self._sim_form.hide()
        sim_form_layout = QVBoxLayout(self._sim_form)
        self.sim_params = SimParamsPanel()
        sim_form_layout.addWidget(self.sim_params)

        # Ports / Dumps (hidden off-screen — canonical sync/edit target). The
        # Ports / Dumps tab page was removed in favour of the Ports / Dumps
        # window (Geometry menu → Ports / Dumps…). Keep a strong ref: without
        # a parent a hidden QWidget would be GC'd and its children deleted.
        ports_tab = QWidget()
        ports_tab.hide()
        self._ports_form = ports_tab
        pv = QVBoxLayout(ports_tab)
        self.port_list = QListWidget()
        self.port_list.setToolTip(
            "Project ports.\nDouble-click a row to edit (type, R, direction, start/stop).\n"
            "'[off]' = inactive: kept in the project but not simulated.\n"
            "Defaults for FreeCAD import: Settings -> Port defaults."
        )
        port_btn_row = QHBoxLayout()
        self.btn_edit_port = QPushButton("Edit Port…")
        self.btn_del_port = QPushButton("Remove Port")
        self.btn_toggle_port = QPushButton("Active on/off")
        self.btn_toggle_port.setToolTip(
            "Switch the selected port on/off.\n"
            "Inactive ports stay in the project (shown as '[off]'), are drawn\n"
            "greyed out and are not written to the simulation: no port number,\n"
            "no S-parameter column, ignored by 'Run All Ports'."
        )
        self.btn_toggle_port.clicked.connect(self._on_toggle_port_active)
        port_btn_row.addWidget(self.btn_edit_port)
        port_btn_row.addWidget(self.btn_del_port)
        port_btn_row.addWidget(self.btn_toggle_port)
        port_btn_row.addStretch(1)
        pv.addWidget(QLabel("Ports (double-click to edit)"))
        pv.addWidget(self.port_list)
        pv.addLayout(port_btn_row)
        self.chk_nf2ff = QCheckBox("Enable NF2FF box")
        self.chk_nf2ff.setChecked(True)
        self.chk_dumps = QCheckBox("Enable field dumps")
        self.chk_dumps.setChecked(True)
        pv.addWidget(self.chk_nf2ff)
        pv.addWidget(self.chk_dumps)
        self.btn_add_dump = QPushButton("Add Field Dump…")
        pv.addWidget(self.btn_add_dump)
        self.btn_edit_nf2ff2 = QPushButton("Edit NF2FF Box…")
        pv.addWidget(self.btn_edit_nf2ff2)
        # ports_tab is hidden off-screen (no addTab) — see Ports/Dumps window

        # Simulator log — panel under the tabs (single instance, toggle via toolbar Log)
        log_font = QFont("Consolas")
        log_font.setStyleHint(QFont.StyleHint.Monospace)
        log_font.setPointSize(10)

        self.log_panel = QWidget()
        self.log_panel.setObjectName("log_panel")
        lp = QVBoxLayout(self.log_panel)
        lp.setContentsMargins(4, 4, 4, 4)
        log_bar = QHBoxLayout()
        self.btn_log_clear = QPushButton("Clear")
        self.btn_log_pop = QPushButton("Pop-out…")
        self.btn_log_save = QPushButton("Save log…")
        self.btn_log_autoscroll = QPushButton("Auto-scroll: ON")
        self.btn_log_autoscroll.setCheckable(True)
        self.btn_log_autoscroll.setChecked(True)
        log_bar.addWidget(self.btn_log_clear)
        log_bar.addWidget(self.btn_log_pop)
        log_bar.addWidget(self.btn_log_save)
        log_bar.addWidget(self.btn_log_autoscroll)
        log_bar.addStretch(1)
        self.lbl_log_count = QLabel("0 lines")
        log_bar.addWidget(self.lbl_log_count)
        lp.addLayout(log_bar)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFont(log_font)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_view.setMaximumBlockCount(200_000)
        self.log_view.setPlaceholderText("Simulator / app log output…")
        self.log_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Mesh report (same text as the Mesh tab's report; single source)
        self.log_mesh_report = QPlainTextEdit()
        self.log_mesh_report.setReadOnly(True)
        self.log_mesh_report.setFont(log_font)
        self.log_mesh_report.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.log_mesh_report.setPlaceholderText(
            "Mesh check report appears after Build… (also shown on the Mesh tab)"
        )

        self.log_tabs = QTabWidget()
        self.log_tabs.setObjectName("log_tabs")
        self.log_tabs.addTab(self.log_view, "Log")
        self.log_tabs.addTab(self.log_mesh_report, "Mesh Report")
        lp.addWidget(self.log_tabs, 1)
        self.right_split.addWidget(self.log_panel)

        # Mesh Viewer Scene Info (mirrors the viewer's info_view) — inside log panel
        self.mesh_viewer_scene_info = QPlainTextEdit()
        self.mesh_viewer_scene_info.setReadOnly(True)
        self.mesh_viewer_scene_info.setFont(log_font)
        self.mesh_viewer_scene_info.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.mesh_viewer_scene_info.setPlaceholderText(
            "Build mesh first, then the scene info appears here (same as the Mesh Viewer's info panel)"
        )
        self.log_tabs.addTab(self.mesh_viewer_scene_info, "Mesh Viewer Scene Info")
        self.log_mesh_viewer_scene_info_tab_index = self.log_tabs.indexOf(
            self.mesh_viewer_scene_info
        )

        # Right-click anywhere in the log area opens the log-area options. It used
        # to be only the thin status bar below, which was hard to hit: now the
        # panel background, the tab bar, the button row and the line counter
        # react too, and the text views keep Copy/Select All.
        for w in (
            self.log_panel,
            self.log_tabs.tabBar(),
            self.btn_log_clear,
            self.btn_log_pop,
            self.btn_log_save,
            self.btn_log_autoscroll,
            self.lbl_log_count,
        ):
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            w.customContextMenuRequested.connect(self._show_status_context_menu)
        for w in (self.log_panel, self.log_tabs.tabBar(), self.lbl_log_count):
            w.setToolTip(
                "Right-click: log-area options (show/hide the Log and the "
                "Project / Geometry panel, pick a log tab, auto-scroll, save)"
            )
        for view in (self.log_view, self.log_mesh_report, self.mesh_viewer_scene_info):
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda pos, v=view: self._show_log_view_context_menu(v, pos)
            )
        # the divider between the tabs and the log panel, too
        self.right_split.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.right_split.customContextMenuRequested.connect(self._show_status_context_menu)
        handle = self.right_split.handle(1)
        if handle is not None:
            handle.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            handle.customContextMenuRequested.connect(self._show_status_context_menu)
            handle.setToolTip(
                "Drag: resize the log panel · right-click: log-area options"
            )

        self.right_split.setSizes([520, 220])
        self._log_sizes: list[int] | None = None
        self._apply_log_panel_visibility(
            bool(getattr(self.settings, "log_panel_visible", True))
        )

        # S-parameters tab (skrf Smith chart + markers + cursor)
        from app.ui.sparam_viewer import SParamViewer

        self.sparam_viewer = SParamViewer(work_dir=self.settings.work_path())
        # "Scan work dir" always searches the global work dir; set_work_dir()
        # may scope the initial listing to a single project.
        self.sparam_viewer.scan_root = self.settings.work_path()
        tabs.addTab(self.sparam_viewer, "S-Params")
        self.sparam_tabs = tabs
        self.tabs = tabs  # main workflow tab widget (View menu)
        self.sparam_tab_index = tabs.indexOf(self.sparam_viewer)

        # Antenna pattern tab (NF2FF far-field: directivity + gain)
        from app.ui.antenna_viewer import AntennaPatternViewer

        self.antenna_viewer = AntennaPatternViewer(work_dir=self.settings.work_path())
        tabs.addTab(self.antenna_viewer, "Antenna")
        self.antenna_tab_index = tabs.indexOf(self.antenna_viewer)

        # Field Viewer tab: field dumps (.h5) -> VTK (.vtr) + embedded 3D view
        from app.ui.vtk_viewer import VtkFieldViewer

        self.vtk_viewer = VtkFieldViewer(work_dir=self.settings.work_path())
        self.vtk_viewer.set_autoplay_default(
            bool(getattr(self.settings, "field_viewer_autoplay", False))
        )
        self.vtk_viewer.chk_autoplay.toggled.connect(self._on_field_autoplay_toggled)
        self.vtk_viewer.set_axes_options(
            getattr(self.settings, "field_viewer_axes", None)
        )
        self.vtk_viewer.on_axes_changed = self._on_field_axes_changed
        # standard view (applied on every load; picked in the viewer or Settings)
        self.vtk_viewer.set_view_default(
            str(getattr(self.settings, "field_viewer_view", "keep") or "keep")
        )
        self.vtk_viewer.on_view_changed = self._on_field_view_changed
        # vector glyphs (ParaView-style arrows; remembered across sessions)
        self.vtk_viewer.set_vector_options(
            getattr(self.settings, "field_viewer_vectors", None)
        )
        self.vtk_viewer.on_vectors_changed = self._on_field_vectors_changed
        tabs.addTab(self.vtk_viewer, "Field Viewer")
        self.vtk_tab_index = tabs.indexOf(self.vtk_viewer)

        # Per-tab help: toolbar button (current tab) + Help on each page
        from app.ui.tab_help import inject_help_button, show_tab_help

        self._tab_title_map = {tabs.tabText(i): i for i in range(tabs.count())}
        tabs.currentChanged.connect(self._on_tab_changed_for_help)
        for i in range(tabs.count()):
            title = tabs.tabText(i)
            page = tabs.widget(i)
            if page is None:
                continue
            if page.layout() is not None:
                inject_help_button(page, title, self)
        self._on_tab_changed_for_help(tabs.currentIndex())

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        # FDTD engine progress (energy / end criteria) — updated from the
        # openEMS stdout lines as they arrive (see _append_log_on_gui).
        self._engine_progress = EngineProgress()
        # Two bars SIDE BY SIDE in the status bar (TS, then energy).
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedWidth(150)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        self.progress.setToolTip("Timestep progress (TS / max TS)")
        # Second bar: residual-energy convergence toward the end criteria — the
        # run's actual stopping condition, which the timestep count alone misses.
        # 0% = energy at start (0 dB), 100% = end criterion reached (min. energy).
        self.progress_energy = QProgressBar()
        self.progress_energy.setRange(0, 100)
        self.progress_energy.setValue(0)
        self.progress_energy.setFixedWidth(150)
        self.progress_energy.setTextVisible(True)
        self.progress_energy.setVisible(False)
        self.progress_energy.setToolTip(
            "Residual-energy convergence: 0% = energy at start (0 dB), "
            "100% = end criteria reached (minimum energy in dB)"
        )
        self.lbl_engine = QLabel("")
        self.lbl_engine.setVisible(False)
        self.status.addPermanentWidget(self.lbl_engine)
        self.status.addPermanentWidget(self.progress)
        self.status.addPermanentWidget(self.progress_energy)
        # Right-click on the bottom edge (status bar and its widgets) opens the
        # log-area options (show/hide the log panel, pick a log tab, …).
        for w in (self.status, self.lbl_engine, self.progress, self.progress_energy):
            w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            w.customContextMenuRequested.connect(self._show_status_context_menu)
        self.status.setToolTip(
            "Right-click here for log-area options (show/hide the Log and the "
            "Project / Geometry panel)"
        )
        # Larger default window so the mesh viewer is usable
        self.resize(1440, 900)
        # left pane state from the last session (View → Project / Geometry Panel)
        self._apply_project_panel_visibility(
            bool(getattr(self.settings, "project_panel_visible", True))
        )

        self.btn_log_clear.clicked.connect(self._clear_logs)
        self.btn_log_save.clicked.connect(self._save_log)
        self.btn_log_pop.clicked.connect(self._pop_out_log)
        self.btn_log_autoscroll.toggled.connect(self._toggle_autoscroll)
        self.btn_log_toggle.toggled.connect(self._toggle_log_panel)
        self.btn_project_toggle.toggled.connect(self._toggle_project_panel)
        self._log_autoscroll = True
        self._script_dialog: object | None = None  # ScriptEditorDialog (modeless)

        # Mesh form ↔ Sim Params computed readout
        for w in (self.spn_f0, self.spn_fc):
            w.valueChanged.connect(self._on_mesh_form_changed)
        for w in (self.spn_start_f, self.spn_stop_f):
            w.valueChanged.connect(self._on_freq_range_changed)
        self.cmb_excite.currentIndexChanged.connect(self._on_excitation_changed)
        self.btn_excite_file.clicked.connect(self._on_pick_excitation_file)
        self.cmb_freq_unit.currentTextChanged.connect(self._on_freq_unit_changed)
        self.cmb_preset.currentTextChanged.connect(self._on_mesh_form_changed)
        self.cmb_unit.currentTextChanged.connect(self._on_mesh_form_changed)
        self.cmb_coords.currentTextChanged.connect(self._on_mesh_form_changed)
        self.sim_params.computed_changed.connect(self._on_sim_params_changed)
        self.sim_params.btn_apply_patch.clicked.connect(self._on_apply_patch_params)
        self.sim_params.btn_fit_nf2ff.clicked.connect(self._on_fit_nf2ff)
        self.sim_params.btn_edit_nf2ff.clicked.connect(self._on_edit_nf2ff)

        # connections
        self.btn_new.clicked.connect(self._on_new)
        self.btn_open.clicked.connect(self._on_open)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_import.clicked.connect(self._on_import)
        self.btn_freecad.clicked.connect(self._on_freecad)
        self.btn_patch.clicked.connect(self._on_load_patch)
        self.btn_yagi.clicked.connect(self._on_load_yagi)
        self.btn_pcb.clicked.connect(self._on_load_pcb)
        self.btn_pcb_bp.clicked.connect(self._on_load_pcb_bandpass)
        self.btn_pcb_hairpin.clicked.connect(self._on_load_pcb_hairpin)
        self.btn_build_mesh.clicked.connect(self._on_build_mesh)
        self.btn_run.clicked.connect(self._on_run)
        self.btn_abort.clicked.connect(self._on_abort)
        self.btn_script.clicked.connect(self._on_edit_script)
        self.btn_settings.clicked.connect(self._on_settings)
        self.btn_sparams.clicked.connect(self._on_show_sparams)
        self.btn_sparams_popout.clicked.connect(self._on_popout_sparams)
        self.btn_antenna.clicked.connect(self._on_show_antenna)
        self.btn_antenna_popout.clicked.connect(self._on_popout_antenna)
        self.btn_edit_port.clicked.connect(self._on_edit_port)
        self.btn_del_port.clicked.connect(self._on_remove_port)
        self.port_list.itemDoubleClicked.connect(self._on_edit_port)
        self.btn_add_dump.clicked.connect(self._on_add_dump)
        self.btn_edit_nf2ff2.clicked.connect(self._on_edit_nf2ff)
        self.btn_exit.clicked.connect(self.close)
        self.btn_help.clicked.connect(self._show_current_tab_help)

        # Real menu bar over the same buttons (see _build_menu)
        self._sim_params_dlg: object | None = None  # SimParamsDialog (modeless)
        self._mesh_params_dlg: object | None = None  # MeshParamsDialog (modeless)
        self._ports_dlg: object | None = None  # PortsDumpsDialog (modeless)
        self._build_menu()

        # Cross-thread log: SimulationRunner may call from a worker thread.
        self._log_bridge = _LogBridge(self)
        self._log_bridge.line.connect(self._log, Qt.ConnectionType.QueuedConnection)
        self._log_queue: Queue[str] = Queue()
        self._log_poll = QTimer(self)
        self._log_poll.setInterval(200)
        self._log_poll.timeout.connect(self._drain_log_queue)
        self._log_poll.start()

    # ----------------------------------------------------------------- menu
    def _build_menu(self) -> None:
        """Menu bar over the existing toolbar buttons.

        Every entry is a QAction that either delegates to a button
        (``btn.click()`` — disabled button = no-op) or to a plain slot, so the
        buttons and their tests keep working unchanged.
        """
        self.menu_btn_pairs: list[tuple[QAction, QWidget]] = []
        self.menu_view_actions: dict[str, QAction] = {}
        bar = self.menuBar()

        def add_action(
            menu,
            text: str,
            *,
            btn: QPushButton | None = None,
            slot=None,
            shortcut: str | QKeySequence | None = None,
            tip: str = "",
        ) -> QAction:
            action = QAction(text, self)
            if shortcut is not None:
                action.setShortcut(shortcut)
            if tip:
                action.setToolTip(tip)
                action.setStatusTip(tip)
            if btn is not None:
                action.triggered.connect(btn.click)
                if btn.isCheckable():
                    action.setCheckable(True)
                    action.setChecked(btn.isChecked())
                    btn.toggled.connect(action.setChecked)
                self.menu_btn_pairs.append((action, btn))
            elif slot is not None:
                action.triggered.connect(slot)
            menu.addAction(action)
            return action

        fmenu = bar.addMenu("&File")
        add_action(fmenu, "New Project", btn=self.btn_new, shortcut="Ctrl+N")
        add_action(fmenu, "Open\u2026", btn=self.btn_open, shortcut="Ctrl+O")
        self._recent_menu = fmenu.addMenu("Recent projects")
        self._recent_menu.setStatusTip("Project files opened most recently")
        add_action(fmenu, "Save", btn=self.btn_save, shortcut="Ctrl+S")
        add_action(fmenu, "Save As\u2026", slot=self._on_save_as, shortcut="Ctrl+Shift+S")
        fmenu.addSeparator()
        add_action(fmenu, "Import Geometry\u2026", btn=self.btn_import)
        add_action(fmenu, "FreeCAD\u2026", btn=self.btn_freecad)
        fmenu.addSeparator()
        add_action(fmenu, "Open Work Folder", slot=self._open_work_folder)
        fmenu.addSeparator()
        add_action(fmenu, "Exit", btn=self.btn_exit, shortcut="Ctrl+Q")
        self._refresh_recent_projects()

        pmenu = bar.addMenu("&Presets")
        add_action(pmenu, "Preset: Patch Antenna\u2026", btn=self.btn_patch)
        add_action(pmenu, "Preset: Yagi Antenna\u2026", btn=self.btn_yagi)
        add_action(pmenu, "Preset: PCB Filter\u2026", btn=self.btn_pcb)
        add_action(pmenu, "Preset: PCB Bandpass (5.6 GHz)\u2026", btn=self.btn_pcb_bp)
        add_action(
            pmenu,
            "Preset: PCB Hairpin Bandpass (2.4 GHz)\u2026",
            btn=self.btn_pcb_hairpin,
        )

        gmenu = bar.addMenu("&Geometry")
        add_action(gmenu, "Add Box…", slot=self._on_add_prim_box)
        add_action(gmenu, "Add Cylinder…", slot=self._on_add_prim_cyl)
        add_action(gmenu, "Add Sphere…", slot=self._on_add_prim_sphere)
        add_action(gmenu, "Add Polygon…", slot=self._on_add_prim_polygon)
        gmenu.addSeparator()
        add_action(gmenu, "Add Assignment…", slot=self._on_add_assignment)
        add_action(gmenu, "Add Material…", slot=self._on_add_material)
        gmenu.addSeparator()
        self.act_add_port = add_action(
            gmenu,
            "Add Port…",
            slot=self._on_add_port,
            tip="Ports need the cartesian coordinate system (openEMS LumpedPort "
            "is not cylindrical-mesh aware)",
        )
        add_action(gmenu, "Add Field Dump…", btn=self.btn_add_dump)
        gmenu.addSeparator()
        add_action(gmenu, "Edit Selected…", slot=self._on_edit_selected_tree)
        add_action(
            gmenu,
            "Duplicate Selected",
            slot=self._on_duplicate_selected_tree,
            shortcut="Ctrl+D",
        )
        add_action(gmenu, "Delete Selected", slot=self._on_delete_selected_tree, shortcut="Del")
        gmenu.addSeparator()
        add_action(
            gmenu,
            "Ports / Dumps…",
            slot=self._on_open_ports_dumps_dialog,
            shortcut="Ctrl+Shift+D",
            tip="Ports, field dumps and NF2FF box in one window",
        )
        gmenu.addSeparator()
        add_action(gmenu, "Edit NF2FF Box…", slot=self._on_edit_nf2ff)
        add_action(gmenu, "Fit NF2FF to Geometry", slot=self._on_fit_nf2ff)

        mmenu = bar.addMenu("Mes&h")
        add_action(mmenu, "Build Mesh", btn=self.btn_build_mesh, shortcut="Ctrl+M")
        add_action(
            mmenu,
            "Rebuild from scratch\u2026",
            slot=self._on_rebuild_scratch,
            tip="Discard ALL manual mesh lines and rebuild with easyMesh (asks first)",
        )
        add_action(
            mmenu,
            "Delete Mesh\u2026",
            slot=self._on_delete_mesh,
            tip="Remove the current mesh (clears the Mesh Viewer and the stored csx.xml)",
        )
        add_action(
            mmenu,
            "New Scene (no mesh)\u2026",
            slot=self._on_prepare_scene,
            tip="Show geometry + domain box + NF2FF box only (builds NO mesh) to add the lines by hand",
        )
        mmenu.addSeparator()
        add_action(
            mmenu,
            "Mesh Parameters\u2026",
            slot=self._on_open_mesh_params_dialog,
            shortcut="Ctrl+Shift+M",
            tip="All mesh parameters in one window (Apply / Close)",
        )
        # V1-V3: one submenu that mirrors the Mesh Viewer's right-click
        # submenu. The menu has no cursor, so its target is the worst open
        # finding ("worst open region"); the enablement comes from the same
        # viewer helper the right-click menu uses.
        self.menu_critical = mmenu.addMenu("Critical cells")
        self.act_critical_overlay = add_action(
            self.menu_critical,
            "Mark critical cells",
            slot=self._on_toggle_critical_overlay,
            tip="Draw the cells that cost runtime in the Mesh Viewer (same as "
            "the viewer's right-click entry; default off)",
        )
        self.act_critical_overlay.setCheckable(True)
        self.menu_critical.addSeparator()
        self.act_critical_repair = add_action(
            self.menu_critical,
            "Repair the worst open region\u2026",
            slot=self._on_repair_critical_cells,
            tip="Open the Mesh Viewer and the repair preview for the open "
            "finding with the largest runtime penalty (needs a built mesh). "
            "The rule is stored in the project, so the preview and the solver "
            "script build the same grid.",
        )
        self.act_critical_remove = add_action(
            self.menu_critical,
            "Remove the offending line\u2026",
            slot=self._on_remove_worst_critical,
            tip="Delete one auto grid line of the worst open finding (stored as "
            "a Mesh Editor removal). The two cells merge; may leave a grading "
            "jump - 'Repair' re-spaces instead.",
        )
        self.act_critical_snap = add_action(
            self.menu_critical,
            "Snap onto the neighbour line\u2026",
            slot=self._on_snap_worst_critical,
            tip="Duplicate-scale slivers only (cell < 0.1 % of a normal cell): "
            "drop the auto line so only the authored coordinate remains.",
        )
        self.act_critical_accept = add_action(
            self.menu_critical,
            "Accept region (do not repair)",
            slot=self._on_accept_worst_critical,
            tip="Store the worst open region in MeshSpec.accepted_cells: the "
            "mark stays (grey) but is no longer an open finding.",
        )
        self.act_critical_explain = add_action(
            self.menu_critical,
            "Explain\u2026",
            slot=self._on_explain_worst_critical,
            tip="Cause, source, runtime penalty and what a repair would do.",
        )
        self.menu_critical.addSeparator()
        self.act_enforce_min = add_action(
            self.menu_critical,
            "Enforce min cell size (repair)",
            slot=self._on_toggle_enforce_min_cellsize,
            tip="Treat 'Min cell size' as a hard lower bound: after easyMesh a "
            "cell below it whose faces are not both authored coordinates is "
            "merged into its smaller neighbour and the grading is re-spaced. "
            "Genuine geometry gaps stay and are reported. Off = easyMesh decides.",
        )
        self.act_enforce_min.setCheckable(True)
        self.act_enforce_min.setChecked(
            bool(getattr(self.project.mesh, "enforce_min_cellsize", False))
        )
        mmenu.addSeparator()
        add_action(mmenu, "Mesh Report", slot=self._show_mesh_report_tab)
        add_action(
            mmenu,
            "Mesh Timing / Feasibility…",
            slot=self._on_show_mesh_timing,
            tip="Smallest cell, the resulting timestep (CFL), cells per wavelength "
            "and the frequency resolution the mesh/settings allow — decides whether "
            "the run can work at all",
        )
        add_action(mmenu, "Show Mesh Viewer", slot=self._show_mesh_viewer)
        mmenu.addSeparator()
        add_action(mmenu, "Export CSX XML\u2026", btn=self.mesh_viewer.btn_export_xml)
        add_action(mmenu, "Open in AppCSXCAD (3D)\u2026", btn=self.mesh_viewer.btn_appcsxcad)

        smenu = bar.addMenu("&Simulation")
        add_action(smenu, "Run Simulation", btn=self.btn_run, shortcut="Ctrl+R")
        self.act_run_all_ports = add_action(
            smenu,
            "Run All Ports (full S-matrix)",
            slot=self._on_run_all_ports,
            shortcut="Ctrl+Shift+R",
            tip="One FDTD run per port (one excitation each) then merge into the "
            "complete S-matrix — needs >= 2 ports",
        )
        add_action(smenu, "Abort", btn=self.btn_abort)
        smenu.addSeparator()
        add_action(
            smenu,
            "Simulation Status…",
            slot=self._on_show_sim_status,
            tip="Errors, warnings, convergence, passivity (|S11|^2+|S21|^2) and "
            "the reliability of the last run — the same block is appended to the log",
        )
        smenu.addSeparator()
        add_action(
            smenu,
            "Simulation Parameters\u2026",
            slot=self._on_open_sim_params_dialog,
            shortcut="Ctrl+Shift+P",
            tip="All simulation parameters in one window (Apply / Close)",
        )
        add_action(
            smenu,
            "Solver Parameters\u2026",
            slot=self._on_open_solver_params,
            tip="openEMS FDTD init + Run() options: engine, verbose, timestep, "
            "MaxTime, debug dumps, dump statistics, setup-only …",
        )
        add_action(smenu, "Script\u2026", btn=self.btn_script)
        smenu.addSeparator()
        add_action(smenu, "S-Params Viewer", btn=self.btn_sparams)
        add_action(smenu, "Pop-out S-Graph", btn=self.btn_sparams_popout)
        smenu.addSeparator()
        add_action(smenu, "Antenna Pattern Viewer", btn=self.btn_antenna)
        add_action(smenu, "Pop-out Antenna Graph", btn=self.btn_antenna_popout)
        smenu.addSeparator()
        add_action(smenu, "Field Viewer", slot=self._on_show_vtk)
        add_action(
            smenu,
            "Field Viewer (pop-out)\u2026",
            slot=self._on_popout_field_viewer,
            tip="Open the Field Viewer tab and move the 3D view to a separate window",
        )
        add_action(
            smenu,
            "Export field dumps to VTK\u2026",
            slot=self._on_export_vtk,
            tip="Convert the project's field dumps (simulation/*.h5) to VTK (.vtr) "
            "in <project>/vtk and open them in the Field Viewer",
        )
        add_action(
            smenu,
            "Export field video (MP4)\u2026",
            slot=self._on_export_field_video,
            tip="Render the Field Viewer's loaded time series to an MP4 using the "
            "current 3D settings and the fps value",
        )

        vmenu = bar.addMenu("&View")
        for title, idx in self._tab_title_map.items():
            # NOTE: QAction.triggered(bool checked) overwrites a single lambda/
            # def argument (i=False/True -> tab 0/1) -> build a 0-arg closure.
            def _make_switch(i: int):
                def _switch() -> None:
                    self.tabs.setCurrentIndex(i)

                return _switch

            action = add_action(vmenu, title, slot=_make_switch(idx))
            action.setCheckable(True)
            self.menu_view_actions[title] = action
        vmenu.addSeparator()
        add_action(vmenu, "Log Panel", btn=self.btn_log_toggle, shortcut="Ctrl+L")
        add_action(
            vmenu,
            "Project / Geometry Panel",
            btn=self.btn_project_toggle,
            shortcut="Ctrl+B",
            tip="Show/hide the left Project / Geometry pane "
            "(also on its right-click menu)",
        )
        vmenu.addSeparator()
        vmenu.addMenu(self.vtk_viewer.build_axes_menu(vmenu))
        vmenu.aboutToShow.connect(self._sync_view_menu)

        tmenu = bar.addMenu("&Settings")
        add_action(tmenu, "Settings\u2026", btn=self.btn_settings)

        hmenu = bar.addMenu("&Help")
        add_action(hmenu, "Help for Current Tab", btn=self.btn_help, shortcut="F1")
        add_action(
            hmenu,
            "Antenna Simulation (FDTD hints)…",
            slot=lambda: show_tab_help(self, "Antenna Simulation"),
        )
        add_action(
            hmenu,
            "Cylindrical (polar) mesh…",
            slot=lambda: show_tab_help(self, "Cylindrical Mesh"),
        )
        add_action(
            hmenu,
            "Mesh repair (critical cells)…",
            slot=lambda: show_tab_help(self, "Mesh Repair"),
        )
        add_action(
            hmenu,
            "Polarization / phase center…",
            slot=lambda: show_tab_help(self, "Polarization & Phase Center"),
        )
        hmenu.addSeparator()
        add_action(hmenu, "Open ANLEITUNG_DE.md", slot=lambda: self._open_doc("docs/ANLEITUNG_DE.md"))
        add_action(hmenu, "Open FreeCAD Workflow (Geometry → Import)", slot=lambda: self._open_doc("docs/FREECAD_WORKFLOW_DE.md"))
        add_action(hmenu, "Open README.md", slot=lambda: self._open_doc("README.md"))
        hmenu.addSeparator()
        add_action(hmenu, "About\u2026", slot=self._about)

        # grey out menu entries whose button is disabled (e.g. during a run)
        def _sync_enabled() -> None:
            for action, btn in self.menu_btn_pairs:
                action.setEnabled(btn.isEnabled())

        for m in (fmenu, pmenu, gmenu, mmenu, smenu, vmenu, tmenu, hmenu):
            m.aboutToShow.connect(_sync_enabled)

        # The Critical-cells submenu mirrors the viewer; refresh it whenever a
        # menu opens (marks/accept state can change without a project signal).
        mmenu.aboutToShow.connect(self._sync_mesh_menu_actions)
        self.menu_critical.aboutToShow.connect(self._sync_critical_menu)
        self._sync_mesh_menu_actions()

    def _sync_view_menu(self) -> None:
        """Check the View entry of the tab that is currently on top."""
        try:
            cur = self.tabs.currentIndex()
        except Exception:  # noqa: BLE001
            return
        for title, action in self.menu_view_actions.items():
            action.setChecked(self._tab_title_map.get(title, -1) == cur)

    def _show_mesh_viewer(self) -> None:
        idx = self._tab_title_map.get("Mesh Viewer", -1)
        if idx >= 0:
            self.tabs.setCurrentIndex(idx)
        self._ensure_mesh_viewer_content()

    def _on_mesh_edited(self) -> None:
        """Mesh Editor edit — applied to the existing mesh (no rebuild)."""
        mesh = getattr(self.project, "mesh", None)
        if mesh is None:
            return
        add = getattr(mesh, "manual_mesh_lines", None) or {}
        rem = getattr(mesh, "manual_mesh_lines_removed", None) or {}
        na = sum(len(v or []) for v in add.values())
        nr = sum(len(v or []) for v in rem.values())
        repairs = len(getattr(mesh, "mesh_repairs", None) or [])
        accepted = len(getattr(mesh, "accepted_cells", None) or [])
        extra = ""
        if repairs:
            extra += f", {repairs} repair rule(s)"
        if accepted:
            extra += f", {accepted} accepted region(s)"
        self._log(
            f"Mesh editor: {na} added, {nr} removed{extra} (applied to the "
            "current mesh; click Build Mesh to save csx.xml)"
        )
        self._sync_mesh_menu_actions()

    # ------------------------------------------------------------------ #
    # Mesh menu: repair options (V1-V3)
    # ------------------------------------------------------------------ #
    def _on_toggle_enforce_min_cellsize(self, checked: bool) -> None:
        """Mesh menu: treat Min cell size as a hard lower bound (V2)."""
        from app.meshing.coord_diag import requested_min_cellsize

        mesh = getattr(self.project, "mesh", None)
        if mesh is None:
            return
        mesh.enforce_min_cellsize = bool(checked)
        dlg = self._mesh_params_dlg
        if dlg is not None:
            box = getattr(dlg.panel, "chk_enforce_min", None)
            if box is not None:
                box.blockSignals(True)
                box.setChecked(bool(checked))
                box.blockSignals(False)
        unit = str(getattr(mesh, "drawing_unit", "mm") or "mm")
        if checked:
            floor = requested_min_cellsize(mesh)
            if floor:
                self._log(
                    f"Min cell enforcement: ON - {float(floor):.6g} {unit} is now "
                    "a hard floor (repair pass after easyMesh; build the mesh "
                    "to apply)"
                )
            else:
                self._log(
                    "Min cell enforcement: ON but no Min cell size is set - "
                    "set it in Mesh Parameters first"
                )
        else:
            self._log("Min cell enforcement: OFF - easyMesh decides the cells")
        self._refresh_run_script_if_generated()

    def _on_toggle_critical_overlay(self, checked: bool) -> None:
        """Mesh menu: draw/hide the critical-cell marks (like right-click)."""
        self.mesh_viewer.set_critical_enabled(bool(checked))
        if checked and not self.mesh_viewer.critical_count():
            self._log("Critical cells: no marks yet - build the mesh first")

    def _on_critical_overlay_changed(self, on: bool) -> None:
        """Keep the checkable menu entry in sync with the viewer."""
        act = getattr(self, "act_critical_overlay", None)
        if act is None:
            return
        act.blockSignals(True)
        act.setChecked(bool(on))
        act.blockSignals(False)

    def _sync_critical_menu(self) -> None:
        """Open count + per-action enablement for the Critical-cells submenu."""
        sub = getattr(self, "menu_critical", None)
        if sub is None:
            return
        try:
            n_open = self.mesh_viewer.critical_open_count()
        except Exception:  # noqa: BLE001
            n_open = 0
        sub.setTitle(
            f"Critical cells ({n_open} open)" if n_open else "Critical cells"
        )
        cell = self._worst_open_critical()
        try:
            state = self.mesh_viewer.critical_action_state(cell)
        except Exception:  # noqa: BLE001
            state = {}
        for name, key in (
            ("act_critical_repair", "repair"),
            ("act_critical_remove", "remove"),
            ("act_critical_snap", "snap"),
            ("act_critical_accept", "accept"),
            ("act_critical_explain", "explain"),
        ):
            act = getattr(self, name, None)
            if act is not None:
                act.setEnabled(bool(state.get(key)))

    def _sync_mesh_menu_actions(self) -> None:
        """Mirror MeshSpec + viewer state into the Mesh-menu check states."""
        act = getattr(self, "act_enforce_min", None)
        if act is not None:
            act.blockSignals(True)
            act.setChecked(
                bool(getattr(self.project.mesh, "enforce_min_cellsize", False))
            )
            act.blockSignals(False)
        self._sync_critical_menu()

    def _on_repair_critical_cells(self) -> None:
        """Mesh menu: preview/repair the worst open critical cell (V1/V3)."""
        self._show_mesh_viewer()
        if not self.mesh_viewer.critical_count():
            self._log("Critical cells: no marks yet - build the mesh first")
            return
        label = self.mesh_viewer.repair_worst_open_cell()
        if label is None:
            self._log(
                "Critical cells: nothing repairable - every open region is "
                "bounded by authored coordinates (accept it instead)"
            )
        else:
            self._log(f"Critical cells: repair preview for {label}")

    def _worst_open_critical(self):
        """The worst open finding (the Mesh menu has no cursor to aim with)."""
        try:
            return self.mesh_viewer.worst_open_cell()
        except Exception:  # noqa: BLE001
            return None

    def _on_remove_worst_critical(self) -> None:
        cell = self._worst_open_critical()
        if cell is None:
            self._log("Critical cells: no open region to remove")
            return
        self._show_mesh_viewer()
        self.mesh_viewer._critical_remove(cell)

    def _on_snap_worst_critical(self) -> None:
        cell = self._worst_open_critical()
        if cell is None:
            self._log("Critical cells: no open region to snap")
            return
        self._show_mesh_viewer()
        self.mesh_viewer._critical_snap(cell)

    def _on_accept_worst_critical(self) -> None:
        cell = self._worst_open_critical()
        if cell is None:
            self._log("Critical cells: no open region to accept")
            return
        self.mesh_viewer._critical_accept(cell)
        self._log(f"Critical cells: accepted {cell.label}")

    def _on_explain_worst_critical(self) -> None:
        cell = self._worst_open_critical()
        if cell is None:
            self._log("Critical cells: no open region to explain")
            return
        self._show_mesh_viewer()
        self.mesh_viewer._critical_explain(cell)

    def _on_rebuild_scratch(self) -> None:
        """Discard all manual lines and rebuild the mesh from scratch."""
        mesh = getattr(self.project, "mesh", None)
        if mesh is None:
            return
        from PySide6.QtWidgets import QMessageBox

        add = getattr(mesh, "manual_mesh_lines", None) or {}
        rem = getattr(mesh, "manual_mesh_lines_removed", None) or {}
        na = sum(len(v or []) for v in add.values())
        nr = sum(len(v or []) for v in rem.values())
        if na or nr:
            resp = QMessageBox.warning(
                self,
                "Rebuild from scratch",
                f"This discards ALL manual mesh lines ({na} added, {nr} removed) "
                "and rebuilds the mesh with easyMesh.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if resp != QMessageBox.StandardButton.Yes:
                return
        mesh.manual_mesh_lines = None
        mesh.manual_mesh_lines_removed = None
        self.mesh_viewer.set_auto_grid(None)
        self._log("Mesh editor: manual lines cleared — rebuilding from scratch…")
        self._on_build_mesh()

    def _on_delete_mesh(self) -> None:
        """Delete the current mesh (viewer + stored csx.xml), with confirmation."""
        from PySide6.QtWidgets import QMessageBox

        resp = QMessageBox.warning(
            self,
            "Delete mesh",
            "Delete the current mesh?\n\n"
            "• the Mesh Viewer is cleared\n"
            "• the stored csx.xml in the project folder is removed\n\n"
            "The project, geometry and manual mesh lines are NOT touched.\n"
            "Use Build Mesh to create a new mesh.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        try:
            self.mesh_viewer.clear_mesh()
        except Exception as e:  # noqa: BLE001
            self._log(f"Delete mesh: clearing the viewer failed: {e}")
        work = self._project_work_dir()
        for name in ("csx.xml", "mesh_preview.png"):
            p = work / name
            try:
                if p.exists():
                    p.unlink()
                    self._log(f"Deleted {p}")
            except Exception as e:  # noqa: BLE001
                self._log(f"Could not delete {p}: {e}")
        self._mesh_sig = None
        self._last_csx = None
        self._last_fdtd = None
        self._mesh_deleted = True
        self._log("Mesh deleted. Click 'Build Mesh' to recreate it.")

    def _on_prepare_scene(self) -> None:
        """Show geometry + domain box + NF2FF box WITHOUT building a mesh.

        Lets the user add every mesh line by hand (Mesh Editor) starting from the
        domain corners; nothing is written to disk.
        """
        self._sync_project_from_form()
        try:
            self._ensure_patch_primitives()
        except Exception as e:  # noqa: BLE001
            self._log(f"Prepare scene failed: {e}")
            return
        from app.meshing.build_task import build_scene_csx

        try:
            result = build_scene_csx(
                self.project,
                list(self.primitives),
                self._project_work_dir(),
                log=self._log,
            )
        except Exception as e:  # noqa: BLE001
            self._log(f"Prepare scene failed: {e}")
            return
        self._last_csx = result.csx
        self._last_fdtd = result.fdtd
        try:
            self.mesh_viewer.set_project_context(
                project=self.project,
                primitives=self.primitives,
                ports=self.project.ports,
                nf2ff=self.project.nf2ff,
            )
            self.mesh_viewer.set_auto_grid(result.auto_mesh_lines)
            self.mesh_viewer.set_csx(result.csx)
            self._mesh_sig = self._mesh_signature()
            self._mesh_deleted = False
            self._sync_scene_info()
            self._set_mesh_report("\n".join(result.lines))
            idx = getattr(self, "mesh_viewer_tab_index", None)
            if idx is not None and idx >= 0:
                self.tabs.setCurrentIndex(idx)
        except Exception as e:  # noqa: BLE001 — viewer must not block
            self._log(f"Prepare scene: viewer update failed: {e}")
        self._log(
            "Scene prepared: geometry + domain + NF2FF box (NO mesh). "
            "Add lines in the Mesh Editor, then Build Mesh."
        )

    def _open_work_folder(self) -> None:
        try:
            path = self.settings.work_path()
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            self._log(f"Opened work folder {path}")
        except Exception as e:  # noqa: BLE001
            self._log(f"Open work folder failed: {e}")

    def _open_doc(self, rel: str) -> None:
        path = APP_ROOT / rel
        if not path.is_file():
            self._log(f"Document not found: {path}")
            QMessageBox.information(self, "Document", f"Not found:\n{path}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _about(self) -> None:
        from app import APP_NAME, __version__

        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b><br>Version {__version__}<br><br>"
            "FreeCAD &rarr; openEMS workflow: geometry import, openEMS-conform "
            "meshing, S-parameters, NF2FF.<br>"
            "Docs: <tt>docs/ANLEITUNG_DE.md</tt>",
        )

    def _current_tab_title(self) -> str:
        try:
            idx = self.sparam_tabs.currentIndex()
            if idx < 0:
                return "Tab"
            return self.sparam_tabs.tabText(idx) or "Tab"
        except Exception:  # noqa: BLE001
            return "Tab"

    def _on_tab_changed_for_help(self, _index: int) -> None:
        title = self._current_tab_title()
        # opening the Mesh Viewer shows the last mesh (recorded or generated)
        if _index == getattr(self, "mesh_viewer_tab_index", -1):
            self._ensure_mesh_viewer_content()
        # opening the Antenna tab shows the current project's pattern
        if _index == getattr(self, "antenna_tab_index", -1):
            self._refresh_antenna_viewer()
        # opening the Field Viewer tab points it at the current project's work dir
        if _index == getattr(self, "vtk_tab_index", -1):
            try:
                self.vtk_viewer.set_work_dir(self._project_work_dir())
            except Exception:  # noqa: BLE001
                pass
        try:
            self.btn_help.setText(f"Help: {title}")
            self.btn_help.setToolTip(f"Open help for the {title} tab")
        except Exception:  # noqa: BLE001
            pass

    def _show_current_tab_help(self) -> None:
        from app.ui.tab_help import show_tab_help

        show_tab_help(self, self._current_tab_title())

    @QtCore.Slot(str)
    def _log(self, msg: str) -> None:
        """Safe log entry — always hop to GUI thread via queue."""
        try:
            if not isinstance(msg, str):
                msg = str(msg)
            # Always queue: worker threads may call this; QTimer drains on GUI thread.
            self._log_queue.put(msg)
        except Exception as e:  # noqa: BLE001 — never let log UI kill the app
            print("log enqueue error:", e)

    def _drain_log_queue(self) -> None:
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._append_log_on_gui(msg)
        except Empty:
            pass

    # ----- run-log session: START banner / step lines / END banner ---------
    def _begin_run_log(self, title: str = "simulation") -> None:
        """Open a run session in the log and start the elapsed-time base."""
        self._run_t0 = time.monotonic()
        self._abort_requested = False
        self._log(format_run_start())
        self._log(f"Run: {title}")

    def _run_step(self, name: str) -> None:
        """Log the step being worked on (marked line, blank line before).

        The delta is measured from the START of the run session.
        """
        t0 = self._run_t0
        elapsed = None if t0 is None else time.monotonic() - t0
        self._log("")  # Leerzeile vor jeder Step-Markierung
        self._log(format_run_step(name, elapsed))

    def _end_run_log(self, note: str | None = None) -> None:
        """Close the run session: END banner with end time and total duration."""
        t0 = self._run_t0
        if t0 is None:
            return  # no active session (e.g. late/duplicate poll)
        elapsed = time.monotonic() - t0
        self._run_t0 = None
        if note:
            self._log(note)
        self._log(format_run_end(elapsed))

    def _reset_engine_progress(self) -> None:
        """Clear the FDTD engine progress bars (called when a run starts)."""
        import math

        self._engine_progress.reset()
        ec = 0.0
        try:
            ec = float(getattr(self.project.sim, "end_criteria", 0.0) or 0.0)
        except Exception:  # noqa: BLE001
            ec = 0.0
        # end criterion as a positive dB drop to reach (energy ratio -> 10*log10)
        self._engine_progress.target_db = (-10.0 * math.log10(ec)) if ec > 0 else None
        for bar in (self.progress, self.progress_energy):
            bar.setValue(0)
            bar.setFormat("")
            bar.setVisible(False)
        self.lbl_engine.setText("")
        self.lbl_engine.setVisible(False)

    def _show_engine_progress(self) -> None:
        p = self._engine_progress
        percent = p.percent
        self.progress.setValue(percent)
        self.progress.setFormat(f"TS {percent}%")
        self.progress.setVisible(True)
        self.progress_energy.setValue(p.energy_percent)
        self.progress_energy.setFormat(f"E {p.energy_percent}%")
        self.progress_energy.setVisible(True)
        self.lbl_engine.setText(p.text())
        self.lbl_engine.setVisible(True)

    def _feed_engine_progress(self, msg: str) -> None:
        """Feed openEMS stdout lines into the engine progress bar.

        Cheap pre-filter so multi-line reports (mesh, S-params) don't run the
        regexes line by line.
        """
        low = msg.lower()
        if "timestep" not in low and "criteria" not in low and "energy" not in low:
            return
        for raw in msg.splitlines():
            line = raw.strip()
            if not line:
                continue
            if self._engine_progress.feed(line):
                self._show_engine_progress()

    def _append_log_on_gui(self, msg: str) -> None:
        try:
            self.log_view.appendPlainText(msg)
            if getattr(self, "_log_autoscroll", True):
                sb = self.log_view.verticalScrollBar()
                sb.setValue(sb.maximum())
            n = self.log_view.blockCount()
            self.lbl_log_count.setText(f"{n} lines")
            self.status.showMessage(msg.splitlines()[-1] if msg else "")
            self._feed_engine_progress(msg)
            low = msg.lower()
            # Refresh only AFTER export messages (not "simulation finished" — too early).
            if (
                "wrote touchstone" in low
                or "s-param outputs" in low
                or "wrote json" in low
                or "[exit code" in low
            ):
                try:
                    self.sparam_viewer.set_work_dir(self._project_work_dir())
                    self._auto_load_sparams_if_empty()
                    # remember the run's artifacts in the project JSON so a
                    # re-opened project restores the viewers directly
                    self._record_results()
                except Exception as e:  # noqa: BLE001
                    print("sparam viewer refresh failed:", e)
            # NF2FF summary → refresh the antenna pattern viewer
            if "nf2ff summary" in low or "wrote json:" in low and "nf2ff" in low:
                try:
                    self.antenna_viewer.set_work_dir(self.settings.work_path())
                except Exception as e:  # noqa: BLE001
                    print("antenna viewer refresh failed:", e)
        except Exception as e:  # noqa: BLE001
            print("log append error:", e)

    def _show_mesh_report_tab(self) -> None:
        """Show the log panel's Mesh Report tab (menu entry)."""
        self._ensure_log_visible("mesh report")
        self.log_tabs.setCurrentWidget(self.log_mesh_report)

    def _set_mesh_report(self, text: str) -> None:
        """Single source for the mesh check report (log Mesh Report tab)."""
        self.mesh_report.setPlainText(text)
        self.log_mesh_report.setPlainText(text)

    def _on_show_mesh_timing(self) -> None:
        """Mesh menu: timing / feasibility report of the last mesh build.

        Smallest cell -> Courant timestep, cells per wavelength, the frequency
        resolution the settings allow and the runtime penalty of an
        over-refined cell. Answers "can this simulation work at all?" before
        the FDTD engine is started.
        """
        from app.ui.report_dialog import show_report

        rep = getattr(self, "_last_mesh_timing", None)
        if rep is None:
            text = (
                "No mesh analysed yet.\n\n"
                "Use Mesh > Build Mesh first (Ctrl+M). The report is produced "
                "right after meshing and is also shown in the Mesh Report tab."
            )
        else:
            from app.meshing.timing import report_lines, summary_line

            parts = [summary_line(rep), *report_lines(rep)]
            coord = getattr(self, "_last_coord_diag", None)
            if coord is not None:
                from app.meshing.coord_diag import report_lines as coord_report
                from app.meshing.coord_diag import summary_line as coord_summary

                parts += [coord_summary(coord), *coord_report(coord)]
            text = "\n".join(parts)
        self._mesh_timing_dialog = show_report(
            self,
            "Mesh Timing / Feasibility",
            text,
            hint="Mesh-only estimates (no FDTD yet). The engine's exact timestep "
            "is reported in the Simulation Status after a run.",
            save_name="mesh_timing.txt",
        )

    def _sync_scene_info(self) -> None:
        """Mirror the Mesh Viewer's info text into the scene info tab."""
        text = self.mesh_viewer.info_text()
        self.mesh_viewer_scene_info.setPlainText(text)

    def _clear_logs(self) -> None:
        self.log_view.clear()
        self.lbl_log_count.setText("0 lines")

    def _toggle_log_panel(self, checked: bool) -> None:
        self._apply_log_panel_visibility(checked)
        self.settings.log_panel_visible = checked
        try:
            self.settings.save()
        except Exception as e:  # noqa: BLE001
            print("save log_panel_visible failed:", e)
        self.status.showMessage(
            "Log panel shown" if checked else "Log panel hidden (Pop-out available from the Log panel)"
        )

    def _ensure_log_visible(self, reason: str = "") -> None:
        """Show log panel if hidden (mesh build / simulation). Does not fight a user hide after."""
        if not self.btn_log_toggle.isChecked():
            self.btn_log_toggle.setChecked(True)
            self._log(f"Log panel shown ({reason})")

    def _apply_log_panel_visibility(self, visible: bool) -> None:
        if not hasattr(self, "log_panel") or not hasattr(self, "right_split"):
            return
        if visible:
            self.log_panel.show()
            sizes = self._log_sizes or [520, 220]
            # right_split: [tabs, log_panel]
            if len(sizes) >= 2:
                self.right_split.setSizes(sizes)
        else:
            sizes = self.right_split.sizes()
            if sizes and len(sizes) >= 2 and any(s > 0 for s in sizes):
                self._log_sizes = list(sizes)
            self.log_panel.hide()

    def _toggle_autoscroll(self, checked: bool) -> None:
        self._log_autoscroll = checked
        self.btn_log_autoscroll.setText("Auto-scroll: ON" if checked else "Auto-scroll: OFF")

    # ----- Project / Geometry pane (left) ---------------------------------
    def _toggle_project_panel(self, checked: bool) -> None:
        self._apply_project_panel_visibility(checked)
        self.settings.project_panel_visible = bool(checked)
        try:
            self.settings.save()
        except Exception as e:  # noqa: BLE001
            print("save project_panel_visible failed:", e)
        self.status.showMessage(
            "Project / Geometry panel shown"
            if checked
            else "Project / Geometry panel hidden (View → Project / Geometry Panel, Ctrl+B)"
        )

    def _apply_project_panel_visibility(self, visible: bool) -> None:
        if not hasattr(self, "project_panel") or not hasattr(self, "main_splitter"):
            return
        if visible:
            self.project_panel.show()
            sizes = self._project_panel_sizes or [280, 700]
            if len(sizes) >= 2:
                self.main_splitter.setSizes(sizes)
        else:
            sizes = self.main_splitter.sizes()
            if sizes and len(sizes) >= 2 and any(s > 0 for s in sizes):
                self._project_panel_sizes = list(sizes)
            self.project_panel.hide()

    def _set_project_panel_visible(self, visible: bool) -> None:
        """Show/hide the pane (keeps the View menu action and the state in sync)."""
        visible = bool(visible)
        if self.btn_project_toggle.isChecked() != visible:
            self.btn_project_toggle.setChecked(visible)  # triggers the toggle
        else:
            self._toggle_project_panel(visible)

    def _add_project_panel_actions(self, menu: QMenu) -> None:
        """Right-click entries of the Project / Geometry pane."""
        act_hide = menu.addAction("Hide Project / Geometry panel")
        act_hide.triggered.connect(lambda _checked=False: self._set_project_panel_visible(False))
        menu.addSeparator()
        head = menu.addAction("Show again: View → Project / Geometry Panel  (Ctrl+B)")
        head.setEnabled(False)

    def _build_project_panel_menu(self) -> QMenu:
        menu = QMenu(self)
        self._add_project_panel_actions(menu)
        return menu

    def _show_project_panel_context_menu(self, _pos=None) -> None:
        """Right-click on the Project / Geometry pane → panel options."""
        self._build_project_panel_menu().exec(QCursor.pos())

    # ----- log area (panel + bottom edge) menu ---------------------------
    def _add_log_area_actions(self, menu: QMenu) -> None:
        """Panel switches + log tabs + auto-scroll + tools (shared by the
        status-bar right-click, the log text and the log panel)."""
        act_panel = menu.addAction("Show log panel")
        act_panel.setCheckable(True)
        act_panel.setChecked(self.btn_log_toggle.isChecked())
        act_panel.toggled.connect(self._set_log_panel_visible)
        act_proj = menu.addAction("Show Project / Geometry panel")
        act_proj.setCheckable(True)
        act_proj.setChecked(self.btn_project_toggle.isChecked())
        act_proj.toggled.connect(self._set_project_panel_visible)

        menu.addSeparator()
        head = menu.addAction("Log area tab")
        head.setEnabled(False)
        for i in range(self.log_tabs.count()):
            title = self.log_tabs.tabText(i)
            act = menu.addAction(title)
            act.setCheckable(True)
            act.setChecked(self.log_tabs.currentIndex() == i)
            act.triggered.connect(lambda _checked=False, k=i: self._show_log_tab(k))

        menu.addSeparator()
        act_scroll = menu.addAction("Auto-scroll")
        act_scroll.setCheckable(True)
        act_scroll.setChecked(self.btn_log_autoscroll.isChecked())
        act_scroll.toggled.connect(self._set_log_autoscroll)

        menu.addSeparator()
        menu.addAction("Save log…", self._save_log)
        menu.addAction("Pop out log…", self._pop_out_log)

    def _build_status_menu(self) -> QMenu:
        """Options for the log area: show/hide, log tabs, auto-scroll, tools."""
        menu = QMenu(self)
        self._add_log_area_actions(menu)
        return menu

    def _log_view_menu(self, view) -> QMenu:
        """Log text: copy/select-all (Qt's standard menu) + log-area options."""
        menu = view.createStandardContextMenu()
        menu.addSeparator()
        self._add_log_area_actions(menu)
        return menu

    def _show_log_view_context_menu(self, view, pos=None) -> None:
        """Right-click in a log text view → standard entries + log-area options."""
        menu = self._log_view_menu(view)
        if pos is None:
            menu.exec(QCursor.pos())
        else:
            menu.exec(view.viewport().mapToGlobal(pos))

    def _show_status_context_menu(self, _pos=None) -> None:
        """Right-click in the log area / at the bottom edge → log-area menu."""
        menu = self._build_status_menu()
        menu.exec(QCursor.pos())

    def _set_log_panel_visible(self, visible: bool) -> None:
        """Show/hide the whole log panel (same state as the View → Log action)."""
        visible = bool(visible)
        if self.btn_log_toggle.isChecked() != visible:
            self.btn_log_toggle.setChecked(visible)  # triggers _toggle_log_panel
        else:
            self._toggle_log_panel(visible)

    def _set_log_autoscroll(self, on: bool) -> None:
        on = bool(on)
        if self.btn_log_autoscroll.isChecked() != on:
            self.btn_log_autoscroll.setChecked(on)
        else:
            self._toggle_autoscroll(on)

    def _show_log_tab(self, index: int) -> None:
        """Show the log panel and select one of its tabs."""
        if not (0 <= index < self.log_tabs.count()):
            return
        self._set_log_panel_visible(True)
        self.log_tabs.setCurrentIndex(index)

    def _save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save simulator log",
            str(self.settings.work_path() / "simulator_log.txt"),
            "Text (*.txt);;All (*.*)",
        )
        if not path:
            return
        Path(path).write_text(self.log_view.toPlainText(), encoding="utf-8")
        self._log(f"Log saved to {path}")

    def _pop_out_log(self) -> None:
        """Detached large log window with scroll (same content as the log panel)."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Simulator Log")
        dlg.resize(1100, 700)
        v = QVBoxLayout(dlg)
        bar = QHBoxLayout()
        b = QPushButton("Clear")
        b2 = QPushButton("Save…")
        b3 = QPushButton("Close")
        bar.addWidget(b)
        bar.addWidget(b2)
        bar.addStretch(1)
        bar.addWidget(b3)
        v.addLayout(bar)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(self.log_view.font())
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        view.setPlainText(self.log_view.toPlainText())
        v.addWidget(view, 1)

        def _sync_from_main() -> None:
            view.setPlainText(self.log_view.toPlainText())
            sb = view.verticalScrollBar()
            sb.setValue(sb.maximum())

        def _clear() -> None:
            self._clear_logs()

        def _save() -> None:
            self._save_log()
            _sync_from_main()

        b.clicked.connect(_clear)
        b2.clicked.connect(_save)
        b3.clicked.connect(dlg.reject)
        timer = QtCore.QTimer(dlg)
        timer.timeout.connect(_sync_from_main)
        timer.start(500)
        dlg.exec()

    def _on_show_sparams(self) -> None:
        # scope the browser/auto-load to the CURRENT project's results, and
        # prefer the file recorded in the project JSON
        self.sparam_viewer.set_work_dir(self._project_work_dir())
        recorded = self._existing_path((self.project.results or {}).get("sparams"))
        if recorded is not None and self.sparam_viewer.network is None:
            try:
                self.sparam_viewer.load_path(recorded)
            except Exception as e:  # noqa: BLE001
                self._log(f"Could not load recorded S-params: {e}")
        else:
            self._auto_load_sparams_if_empty()
        if self.sparam_tab_index >= 0:
            self.sparam_tabs.setCurrentIndex(self.sparam_tab_index)

    def _auto_load_sparams_if_empty(self) -> None:
        """Load the newest S-params of the current project (no file listing).

        The "Loaded files" list stays empty until "Scan work dir" is pressed.
        """
        if self.sparam_viewer.network is not None:
            return
        from app.ui.sparam_viewer import find_latest_sparam_files

        try:
            files = find_latest_sparam_files(self._project_work_dir())
        except Exception:  # noqa: BLE001
            files = []
        if not files:
            return
        try:
            self.sparam_viewer.load_path(files[0])
        except Exception as e:  # noqa: BLE001
            self._log(f"Could not auto-load S-params: {e}")

    def _on_popout_sparams(self) -> None:
        self.sparam_viewer.set_work_dir(self._project_work_dir())
        recorded = self._existing_path((self.project.results or {}).get("sparams"))
        if recorded is not None and self.sparam_viewer.network is None:
            try:
                self.sparam_viewer.load_path(recorded)
            except Exception as e:  # noqa: BLE001
                self._log(f"Could not load recorded S-params: {e}")
        else:
            self._auto_load_sparams_if_empty()
        self.sparam_viewer.popout_graph()
        self._log("S-Params viewer opened")

    def _refresh_antenna_viewer(self) -> None:
        """Show the current project's pattern when the Antenna tab is opened.

        Only this project's own result is auto-loaded (never another project's
        file); the "Loaded files" list stays empty until "Scan work dir".
        """
        self.antenna_viewer.set_work_dir(self.settings.work_path())
        if self.antenna_viewer.data is not None:
            return  # keep what the user has loaded
        proj_dir = self._project_work_dir()
        target = next(
            (
                c
                for c in (
                    self._existing_path((self.project.results or {}).get("nf2ff")),
                    self._existing_path(proj_dir / "nf2ff" / "nf2ff.json"),
                    self._existing_path(proj_dir / "simulation" / "nf2ff" / "nf2ff.json"),
                )
                if c is not None
            ),
            None,
        )
        if target is None:
            # never auto-load another project's pattern
            self._log(f"Antenna pattern: no NF2FF result for {proj_dir.name}")
            return
        try:
            self.antenna_viewer.load_path(target)
            self._log(f"Antenna pattern: loaded {Path(target).name} ({Path(target).parent.parent.name})")
        except Exception as e:  # noqa: BLE001
            self._log(f"Could not load NF2FF {target}: {e}")

    def _on_show_antenna(self) -> None:
        self._refresh_antenna_viewer()
        if self.antenna_tab_index >= 0:
            self.tabs.setCurrentIndex(self.antenna_tab_index)
        self._log("Antenna pattern viewer opened")

    def _on_popout_antenna(self) -> None:
        self._refresh_antenna_viewer()
        self.antenna_viewer.popout_graph()
        self._log("Antenna pattern viewer opened")

    def _on_show_vtk(self) -> None:
        """Switch to the Field Viewer tab (work dir follows the current project)."""
        try:
            self.vtk_viewer.set_work_dir(self._project_work_dir())
        except Exception:  # noqa: BLE001
            pass
        if getattr(self, "vtk_tab_index", -1) >= 0:
            self.tabs.setCurrentIndex(self.vtk_tab_index)
        self._log("Field Viewer opened")

    def _on_popout_field_viewer(self) -> None:
        """Open the Field Viewer tab and show the view in a separate window."""
        self._on_show_vtk()
        try:
            if self.vtk_viewer._popout_win is None:
                self.vtk_viewer.popout()
        except Exception as e:  # noqa: BLE001 — never crash the GUI
            self._log(f"Field Viewer pop-out failed: {e}")

    def _on_export_vtk(self) -> None:
        """Convert the project's field dumps to VTK and show them."""
        try:
            self.vtk_viewer.set_work_dir(self._project_work_dir())
        except Exception:  # noqa: BLE001
            pass
        if getattr(self, "vtk_tab_index", -1) >= 0:
            self.tabs.setCurrentIndex(self.vtk_tab_index)
        try:
            self.vtk_viewer.export_and_load()
        except Exception as e:  # noqa: BLE001 — never crash the GUI
            self._log(f"VTK export failed: {e}")

    def _on_export_field_video(self) -> None:
        """Export the Field Viewer's sequence to MP4 with the current view.

        The Field Viewer tab is raised first: its GL window must be current
        (and not zero-sized) for the frames to contain the rendered scene.
        """
        if getattr(self, "vtk_tab_index", -1) >= 0:
            self.tabs.setCurrentIndex(self.vtk_tab_index)
            QApplication.processEvents()
        try:
            self.vtk_viewer.export_video()
        except Exception as e:  # noqa: BLE001 — never crash the GUI
            self._log(f"Field video export failed: {e}")
            QMessageBox.warning(self, "Save video", f"Video export failed:\n{e}")

    def _refresh_materials(self) -> None:
        self._tree_rebuilding = True
        try:
            self.mat_list.clear()
            for m in self.project.materials:
                self.mat_list.addItem(f"{m.name} ({m.kind})")
        finally:
            self._tree_rebuilding = False
        if getattr(self, "tree", None) is not None:
            self._refresh_tree()

    def _on_material_selection_changed(self) -> None:
        """Materials are not scene objects — selecting one clears the highlight.

        Only *Prim* and *Port* entries select something in the Mesh Viewer.
        """
        if self._tree_rebuilding:
            return
        self._highlight_in_viewer([], [], "", focus=False)

    # ----- highlight the Project / Geometry selection in the Mesh Viewer ----
    def _tree_selection_highlight(self, kind: str, idx: int) -> tuple[list, list, str]:
        """(boxes, names, label) for the entry selected in the tree.

        Only real scene objects — **Prim** and **Port** — are highlighted.
        Materials, assignments, imported geometry files, dumps and the NF2FF
        box are not objects the user placed; selecting them leaves the viewer
        unhighlighted.
        """
        if kind == "prim" and 0 <= idx < len(self.primitives):
            pr = self.primitives[idx]
            name = str(pr.get("name") or "")
            return [(pr.get("start"), pr.get("stop"))], [name], f"Prim: {name}"
        if kind == "port" and 0 <= idx < len(self.project.ports):
            p = self.project.ports[idx]
            return [(p.start, p.stop)], [p.name], f"Port: {p.name}"
        return [], [], ""

    def _highlight_in_viewer(
        self, boxes, names, label: str, focus: bool = True
    ) -> None:
        viewer = getattr(self, "mesh_viewer", None)
        if viewer is None or not hasattr(viewer, "set_highlight"):
            return
        try:
            viewer.set_highlight(boxes=boxes, names=names, label=label, focus=focus)
        except Exception as e:  # noqa: BLE001 — never break the GUI on a highlight
            print("mesh viewer highlight failed:", e)

    def _on_tree_selection_changed(self) -> None:
        """Highlight the object selected in the Project / Geometry list.

        The slice plane jumps to the middle of the object (see
        ``MeshGeometryViewer.focus_highlight``) — but only for a real click: the
        tree is rebuilt after every edit and re-selects the same row, which must
        not pull the plane back while working.
        """
        self._tree_selection_serial += 1
        items = self.tree.selectedItems()
        if not items:
            self._highlight_in_viewer([], [], "", focus=False)  # no highlight
            return
        item = items[0]
        k_raw = item.data(Qt.UserRole)
        i_raw = item.data(Qt.UserRole + 1)
        kind = "" if k_raw is None else str(k_raw)
        idx = -1 if i_raw is None else int(i_raw)
        self._highlight_in_viewer(
            *self._tree_selection_highlight(kind, idx),
            focus=not self._tree_rebuilding,
        )

    def _on_tree_item_clicked(self, item) -> None:
        """A repeat click on the selected row toggles the viewer mark.

        The row itself stays selected (the tree selection is the user's
        working selection). Only the mark in the Mesh Viewer is cleared /
        restored, so a second click does not make the row disappear.
        """
        now = time.monotonic()
        last = getattr(self, "_tree_last_click", 0.0)
        self._tree_last_click = now
        changed = self._tree_selection_serial != self._tree_last_click_serial
        self._tree_last_click_serial = self._tree_selection_serial
        if changed:
            return  # this click changed the selection: the mark was just set
        if now - last < 0.30:
            return  # second click of a double-click (opens the editor)
        k = item.data(Qt.UserRole)
        i = item.data(Qt.UserRole + 1)
        kind = "" if k is None else str(k)
        idx = -1 if i is None else int(i)
        boxes, names, label = self._tree_selection_highlight(kind, idx)
        current = getattr(self.mesh_viewer, "highlight", lambda: {})()
        if label and current.get("label") == label:
            self._highlight_in_viewer([], [], "", focus=False)
        elif label:
            self._highlight_in_viewer(boxes, names, label, focus=False)

    def _refresh_ports(self) -> None:
        self.port_list.clear()
        for p in self.project.ports:
            off = "" if bool(getattr(p, "active", True)) else "  [off]"
            rev = "  [rev]" if bool(getattr(p, "reverse", False)) else ""
            self.port_list.addItem(
                f"{p.name}: {p.type}{self._port_width_text(p, short=True)} "
                f"{p.start} -> {p.stop} R={p.resistance}{rev}{off}"
            )
        if getattr(self, "tree", None) is not None:
            self._refresh_tree()

    def _on_toggle_port_active(self) -> None:
        """Switch the selected port on/off (it stays in the project)."""
        idx = self._selected_port_index()
        if idx < 0:
            self._log("Select a port to switch it on/off (double-click a row)")
            return
        p = self.project.ports[idx]
        p.active = not bool(getattr(p, "active", True))
        self._refresh_ports()
        if idx < self.port_list.count():
            self.port_list.setCurrentRow(idx)
        self.mesh_viewer.set_ports(self.project.ports)
        state = "active" if p.active else "inactive (not simulated)"
        self._log(f"Port {p.name}: {state}")

    def _freq_unit(self) -> str:
        return getattr(self, "_freq_unit_name", None) or getattr(
            self, "cmb_freq_unit", None
        ).currentText()

    def _freq_factor(self) -> float:
        from app.ui.freq_units import FREQ_UNITS

        return float(FREQ_UNITS.get(self._freq_unit(), 1.0))

    def _hz_from_spin(self, spin) -> float:
        return float(spin.value()) * self._freq_factor()

    def _set_spin_hz(self, spin, hz: float) -> None:
        from app.ui.freq_units import set_spin_hz

        set_spin_hz(spin, hz, self._freq_unit())

    def _on_freq_unit_changed(self, unit: str) -> None:
        """Re-display mesh frequencies in the newly selected unit (value stays Hz)."""
        from app.ui.freq_units import FREQ_UNITS, configure_freq_spin, set_spin_hz

        if unit not in FREQ_UNITS:
            return
        old_unit = getattr(self, "_freq_unit_name", None)
        if old_unit == unit:
            return
        old_factor = float(FREQ_UNITS.get(old_unit or "GHz", 1e9))
        f0_hz = float(self.spn_f0.value()) * old_factor
        fc_hz = float(self.spn_fc.value()) * old_factor
        start_hz = float(self.spn_start_f.value()) * old_factor
        stop_hz = float(self.spn_stop_f.value()) * old_factor
        self._freq_unit_name = unit
        self._freq_unit_factor = float(FREQ_UNITS[unit])

        guard = getattr(self, "_freq_guard", False)
        self._freq_guard = True
        try:
            for sp in (self.spn_f0, self.spn_fc, self.spn_start_f, self.spn_stop_f):
                configure_freq_spin(sp, unit)
            set_spin_hz(self.spn_f0, f0_hz, unit)
            set_spin_hz(self.spn_fc, fc_hz, unit)
            set_spin_hz(self.spn_start_f, start_hz, unit)
            set_spin_hz(self.spn_stop_f, stop_hz, unit)
        finally:
            self._freq_guard = guard
        self.sim_params.refresh_computed(self.project)
        self._log(f"Frequency display unit: {unit} (values stored in Hz)")

    def _sync_mesh_freqs_from_widgets(self, source: str = "f0fc") -> None:
        """Write frequency widgets → project.mesh (always stored in Hz).

        source="f0fc": start/stop derived as f0±fc (excitation-centric).
        source="range": start/stop are authoritative; f0/fc updated to stay consistent.
        """
        if getattr(self, "_freq_guard", False):
            return
        self._freq_guard = True
        try:
            self.project.mesh.preset = self.cmb_preset.currentText()  # type: ignore[assignment]
            self.project.mesh.drawing_unit = self.cmb_unit.currentText()  # type: ignore[assignment]
            if hasattr(self, "cmb_coords"):
                self.project.mesh.coord_system = self.cmb_coords.currentText()  # type: ignore[assignment]
            f0 = self._hz_from_spin(self.spn_f0)
            fc = self._hz_from_spin(self.spn_fc)
            start = self._hz_from_spin(self.spn_start_f)
            stop = self._hz_from_spin(self.spn_stop_f)
            if source == "f0fc":
                start = f0 - fc
                stop = f0 + fc
                if stop <= start:
                    stop = start + max(1e5, 0.05 * start)
                self._set_spin_hz(self.spn_start_f, start)
                self._set_spin_hz(self.spn_stop_f, stop)
                self.project.mesh.start_frequency = start
                self.project.mesh.stop_frequency = stop
                self.project.mesh.target_frequency = f0
            else:
                if stop <= start:
                    stop = start + max(1e5, 0.05 * start)
                    self._set_spin_hz(self.spn_stop_f, stop)
                self.project.mesh.start_frequency = start
                self.project.mesh.stop_frequency = stop
                if f0 < start or f0 > stop:
                    f0 = 0.5 * (start + stop)
                    self._set_spin_hz(self.spn_f0, f0)
                fc = 0.5 * (stop - start)
                self._set_spin_hz(self.spn_fc, fc)
                self.project.mesh.target_frequency = f0
        finally:
            self._freq_guard = False

    def _sync_project_from_form(self) -> None:
        # Prefer explicit start/stop widgets if they differ from f0±fc
        start_w = self._hz_from_spin(self.spn_start_f)
        stop_w = self._hz_from_spin(self.spn_stop_f)
        f0 = self._hz_from_spin(self.spn_f0)
        fc = self._hz_from_spin(self.spn_fc)
        if abs(start_w - (f0 - fc)) < 1.0 and abs(stop_w - (f0 + fc)) < 1.0:
            self._sync_mesh_freqs_from_widgets("f0fc")
        else:
            self._sync_mesh_freqs_from_widgets("range")
        self.sim_params.write_to_project(self.project)
        self.project.sim.nf2ff_enabled = self.chk_nf2ff.isChecked()
        self.project.nf2ff.enabled = self.chk_nf2ff.isChecked()
        self.project.sim.field_dumps_enabled = self.chk_dumps.isChecked()
        # Keep the hidden Sim Params panel's NF2FF toggle in step with the
        # Ports / Dumps checkbox (both edit project.sim.nf2ff_enabled).
        self.sim_params.chk_nf2ff_enabled.setChecked(bool(self.chk_nf2ff.isChecked()))

    def _sync_form_from_project(self) -> None:
        m = self.project.mesh
        f0 = m.target_frequency or (0.5 * (m.start_frequency + m.stop_frequency))
        fc = max(1e5, 0.5 * abs(m.stop_frequency - m.start_frequency))
        # Display in current unit (default GHz if unset)
        if not getattr(self, "_freq_unit_name", None):
            from app.ui.freq_units import suggest_freq_unit

            unit = suggest_freq_unit(f0)
            if hasattr(self, "cmb_freq_unit"):
                self.cmb_freq_unit.setCurrentText(unit)
            self._freq_unit_name = unit
        self._set_spin_hz(self.spn_start_f, float(m.start_frequency))
        self._set_spin_hz(self.spn_stop_f, float(m.stop_frequency))
        self._set_spin_hz(self.spn_f0, f0)
        self._set_spin_hz(self.spn_fc, fc)
        from app.simulation.excitation import normalize_kind

        self._sync_excite_combo(normalize_kind(getattr(m, "excitation", "gauss")))
        self.edt_excite_file.setText(str(getattr(m, "excitation_file", "") or ""))
        self._update_excitation_state()
        self.cmb_preset.setCurrentText(m.preset)
        self.cmb_unit.setCurrentText(m.drawing_unit)
        self._sync_mesh_menu_actions()
        if hasattr(self, "cmb_coords"):
            self.cmb_coords.setCurrentText(
                str(getattr(m, "coord_system", "cartesian") or "cartesian")
            )
        _polar = bool(getattr(m, "coord_system", "cartesian") == "cylindrical")
        self.chk_nf2ff.setEnabled(not _polar)
        self.chk_nf2ff.setToolTip(
            "NF2FF far-field is not supported in cylindrical mode."
            if _polar
            else ""
        )
        self.chk_nf2ff.setChecked(self.project.nf2ff.enabled and not _polar)
        self.chk_dumps.setChecked(self.project.sim.field_dumps_enabled)
        if hasattr(self, "act_add_port"):
            self.act_add_port.setEnabled(not _polar)
            self.act_add_port.setToolTip(
                "Ports are not supported in cylindrical mode (openEMS LumpedPort "
                "produces NaN fields there). Switch Mesh Parameters -> Coordinate "
                "system to cartesian to use ports."
                if _polar
                else "Ports need the cartesian coordinate system (openEMS "
                "LumpedPort is not cylindrical-mesh aware)"
            )
        self.sim_params.sync_from_project(self.project)

    def _on_mesh_form_changed(self, *_args: object) -> None:
        self._sync_mesh_freqs_from_widgets("f0fc")
        self.sim_params.refresh_computed(self.project)

    # --- excitation waveform (Mesh tab) --------------------------------
    def _on_excitation_changed(self, *_args: object) -> None:
        if getattr(self, "_excite_guard", False):
            return
        kind = self.cmb_excite.currentData() or "gauss"
        self.project.mesh.excitation = str(kind)
        self.project.mesh.excitation_file = self.edt_excite_file.text().strip()
        self._update_excitation_state()
        self.sim_params.refresh_computed(self.project)

    def _on_pick_excitation_file(self) -> None:
        from app.simulation.excitation import read_custom_waveform

        start = self.edt_excite_file.text().strip() or str(self._project_work_dir())
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Custom excitation waveform",
            start,
            "Waveform (*.csv *.txt *.dat);;All (*.*)",
        )
        if not path:
            return
        try:
            times, values = read_custom_waveform(path)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid waveform", str(e))
            return
        self.edt_excite_file.setText(path)
        self.project.mesh.excitation_file = path
        self.project.mesh.excitation = "custom"
        self._sync_excite_combo("custom")
        self._update_excitation_state()
        self._log(
            f"Custom excitation: {Path(path).name} "
            f"({len(times)} samples, {times[-1] * 1e9:.3g} ns)"
        )
        self.sim_params.refresh_computed(self.project)

    def _sync_excite_combo(self, kind: str) -> None:
        """Select ``kind`` in the combo without firing the change handler."""
        idx = self.cmb_excite.findData(str(kind or "gauss"))
        if idx < 0:
            idx = 0
        self._excite_guard = True
        try:
            self.cmb_excite.setCurrentIndex(idx)
        finally:
            self._excite_guard = False

    def _update_excitation_state(self) -> None:
        kind = self.cmb_excite.currentData() or "gauss"
        gauss = kind == "gauss"
        self.spn_fc.setEnabled(gauss)
        if gauss:
            self.spn_fc.setToolTip(
                "Gauss corner frequency (bandwidth/2) — used by the Gauss excitation only.\n"
                "Editing f0/fc updates Start/Stop as f0±fc."
            )
        else:
            self.spn_fc.setToolTip(
                f"Not used by the '{self.cmb_excite.currentText()}' excitation.\n"
                "Start/Stop still define the mesh and the S-param band."
            )
        custom = kind == "custom"
        self.wdg_excite_file.setEnabled(custom)
        self.btn_excite_file.setEnabled(custom)
        self.btn_excite_file.setToolTip(
            "Choose the custom excitation CSV file" if custom else
            "Only used by the 'Custom waveform' excitation"
        )

    def _on_freq_range_changed(self, *_args: object) -> None:
        self._sync_mesh_freqs_from_widgets("range")
        self.sim_params.refresh_computed(self.project)

    def _on_sim_params_changed(self) -> None:
        # Form values live in Sim Params widgets; mesh tab may already hold f0/fc.
        start_w = self._hz_from_spin(self.spn_start_f)
        stop_w = self._hz_from_spin(self.spn_stop_f)
        f0 = self._hz_from_spin(self.spn_f0)
        fc = self._hz_from_spin(self.spn_fc)
        self._sync_mesh_freqs_from_widgets(
            "range" if abs(start_w - (f0 - fc)) > 1.0 else "f0fc"
        )
        self.sim_params.refresh_computed(self.project)

    # --- Simulation menu: standalone parameter window -------------------
    def _on_open_sim_params_dialog(self) -> None:
        """Open (or re-use) the modeless Simulation Parameters window."""
        from app.ui.sim_params import SimParamsDialog

        if self._sim_params_dlg is None:
            dlg = SimParamsDialog(self)
            dlg.apply_requested.connect(self._apply_sim_params_dialog)
            dlg.panel.btn_fit_nf2ff.clicked.connect(self._on_fit_nf2ff)
            dlg.panel.btn_edit_nf2ff.clicked.connect(self._on_edit_nf2ff)
            dlg.panel.btn_apply_patch.clicked.connect(
                self._on_apply_patch_params_from_dialog
            )
            self._sim_params_dlg = dlg
        dlg = self._sim_params_dlg
        dlg.load(
            self.project,
            patch_visible=self.sim_params.patch_group_visible,
            patch_params=self.sim_params.patch_params,
        )
        dlg.show()

    def _on_open_solver_params(self) -> None:
        """Open the Solver Parameters dialog (openEMS init + Run() options)."""
        from app.ui.solver_params import SolverParamsDialog

        self._sync_project_from_form()
        dlg = SolverParamsDialog(self, self.project)
        if dlg.exec():
            self._log(
                f"Solver parameters: engine={self.project.sim.engine or 'default'}, "
                f"verbose={self.project.sim.verbose}, setup_only={self.project.sim.setup_only}, "
                f"cleanup={self.project.sim.cleanup}"
            )
            self._sync_form_from_project()
        dlg.raise_()
        dlg.activateWindow()

    def _apply_sim_params_dialog(self) -> None:
        """Push the dialog's values into project + hidden Sim Params panel."""
        dlg = self._sim_params_dlg
        if dlg is None:
            return
        dlg.panel.write_to_project(self.project)
        # mirror into the hidden Sim Params panel (run/save re-sync from it)
        self.sim_params.sync_from_project(self.project)
        # antenna mode may have changed the mesh preset — keep the Mesh-tab
        # combo in step, otherwise Build would write the old preset back.
        self.cmb_preset.setCurrentText(self.project.mesh.preset or "high")
        # keep the Ports / Dumps NF2FF checkbox in step with the dialog
        self.chk_nf2ff.setChecked(bool(self.project.nf2ff.enabled))
        self.sim_params.set_patch_group_visible(dlg.panel.patch_group_visible)
        if dlg.panel.patch_group_visible:
            self.sim_params.patch_params = dict(dlg.panel.patch_params)
        self._on_sim_params_changed()
        self._log(
            "Simulation parameters applied: "
            f"end_criteria={self.project.sim.end_criteria:g}, "
            f"max_timesteps={self.project.sim.max_timesteps}, "
            f"boundary={self.project.boundary.xmin}/{self.project.boundary.ymin}"
            f"/{self.project.boundary.zmin}"
            + (
                f", antenna mode ON (preset={self.project.mesh.preset}, "
                f"air_margin={self.project.boundary.air_margin[0]:.4g})"
                if getattr(self.project.sim, "antenna_mode", False)
                else ""
            )
        )
        self._refresh_run_script_if_generated()

    def _on_open_mesh_params_dialog(self) -> None:
        """Open (or re-use) the modeless Mesh Parameters window."""
        from app.ui.mesh_params import MeshParamsDialog

        if self._mesh_params_dlg is None:
            dlg = MeshParamsDialog(self)
            dlg.apply_requested.connect(self._apply_mesh_params_dialog)
            self._mesh_params_dlg = dlg
        dlg = self._mesh_params_dlg
        dlg.load(self.project)
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _apply_mesh_params_dialog(self) -> None:
        """Push the dialog's values into project + Mesh tab widgets."""
        dlg = self._mesh_params_dlg
        if dlg is None:
            return
        dlg.panel.write_to_project(self.project)
        self._sync_mesh_menu_actions()
        # mirror into the Mesh tab widgets (build re-syncs from that panel)
        m = self.project.mesh
        self._freq_guard = True
        try:
            self.cmb_preset.setCurrentText(m.preset or "high")
            self.cmb_unit.setCurrentText(m.drawing_unit or "mm")
            if hasattr(self, "cmb_coords"):
                self.cmb_coords.setCurrentText(
                    str(getattr(m, "coord_system", "cartesian") or "cartesian")
                )
            self._set_spin_hz(self.spn_start_f, float(m.start_frequency))
            self._set_spin_hz(self.spn_stop_f, float(m.stop_frequency))
            self._set_spin_hz(self.spn_f0, float(m.target_frequency))
            fc = max(1e5, 0.5 * abs(m.stop_frequency - m.start_frequency))
            self._set_spin_hz(self.spn_fc, fc)
            from app.simulation.excitation import normalize_kind

            self._sync_excite_combo(normalize_kind(getattr(m, "excitation", "gauss")))
            self.edt_excite_file.setText(str(getattr(m, "excitation_file", "") or ""))
        finally:
            self._freq_guard = False
        self._update_excitation_state()
        # re-sync the hidden panel too: antenna mode mirrors the mesh preset
        self.sim_params.sync_from_project(self.project)
        self._log(
            "Mesh parameters applied: "
            f"preset={m.preset}, unit={m.drawing_unit}, "
            f"start={m.start_frequency:.6g}, stop={m.stop_frequency:.6g}, "
            f"target={(m.target_frequency or 0.0):.6g}"
        )
        self._refresh_run_script_if_generated()

    def _refresh_run_script_if_generated(self) -> None:
        """Regenerate work/project_run.py after parameter changes (Apply).

        Only when a script already exists (project was saved/built/run before);
        the first generation still happens via Run Simulation or the Script
        editor. Hand-edits are backed up as usual (_generate_script /
        _note_script_backup). A visible Script editor reloads the file unless
        it holds unsaved edits.
        """
        work = self._project_work_dir()
        if not (work / "project_run.py").exists():
            return
        try:
            script = self._generate_script(work)
        except Exception as e:  # noqa: BLE001 — Apply must not fail on this
            self._log(f"WARNING: run script could not be refreshed: {e}")
            return
        self._log(f"Run script updated: {script}")
        dlg = self._script_dialog
        if dlg is not None and dlg.isVisible():
            dlg.show_path(script)  # reloads unless the editor has unsaved edits

    def _on_open_ports_dumps_dialog(self) -> None:
        """Open (or re-use) the modeless Ports / Dumps window (Geometry menu)."""
        from app.ui.ports_dumps import PortsDumpsDialog

        if self._ports_dlg is None:
            self._ports_dlg = PortsDumpsDialog(self._ports_form, self)
        self._refresh_ports()
        self._ports_dlg.show()
        self._ports_dlg.raise_()
        self._ports_dlg.activateWindow()

    def _on_apply_patch_params_from_dialog(self) -> None:
        """Patch rebuild using the dialog's values (then re-sync the dialog)."""
        dlg = self._sim_params_dlg
        if dlg is None:
            return
        self.sim_params.patch_params = dict(dlg.panel.patch_params)
        self._on_apply_patch_params()
        # the rebuild replaced self.project -> re-sync the dialog to it
        dlg.load(
            self.project,
            patch_visible=self.sim_params.patch_group_visible,
            patch_params=self.sim_params.patch_params,
        )

    def _on_apply_patch_params(self) -> None:
        from app.ui.sim_params import DEFAULT_PATCH_PARAMS

        pp = {**DEFAULT_PATCH_PARAMS, **self.sim_params.patch_params}
        kwargs = {k: float(pp[k]) for k in DEFAULT_PATCH_PARAMS}
        self.project, self.primitives = patch_antenna_primitives(**kwargs)
        self.project.patch_params = dict(pp)
        self.sim_params.patch_params = dict(pp)
        self._set_spin_hz(self.spn_f0, kwargs["f0"])
        self._set_spin_hz(self.spn_fc, kwargs["fc"])
        self._sync_form_from_project()
        self._refresh_tree()
        self._refresh_ports()
        self.geom_info.setPlainText(
            "Patch antenna rebuilt from Sim Params dialog values.\n"
            "Press 'Build Mesh' then 'Run Simulation'."
        )
        self._log(
            f"Patch geometry applied: W={kwargs['patch_width']} L={kwargs['patch_length']} "
            f"εr={kwargs['substrate_eps']} h={kwargs['substrate_thickness']} "
            f"ground={kwargs['ground_thickness']} feed={kwargs['feed_pos']}"
        )
        if hasattr(self.sim_params, "set_patch_group_visible"):
            self.sim_params.set_patch_group_visible(True)
        self._log_computed_summary()

    def _log_computed_summary(self) -> None:
        try:
            self._sync_project_from_form()
            from app.meshing.rules import air_margin_units, compute_targets

            t = compute_targets(self.project.mesh)
            air = air_margin_units(
                self.project.mesh, self.project.boundary.air_margin, t
            )
            self._log(
                f"Computed: λ_air={t.lambda_min_units:.4g} mm, "
                f"max_cell={t.max_cell_units:.4g} mm, "
                f"target_cell={t.target_cell_units:.4g} mm, "
                f"air_margin={air[0]:.4g} mm, "
                f"NrTS={self.project.sim.max_timesteps}, "
                f"end_criteria={self.project.sim.end_criteria:g}, "
                f"boundary={self.project.boundary.as_openems_list()}"
            )
        except Exception as e:  # noqa: BLE001
            self._log(f"Computed summary failed: {e}")

    def _clear_result_viewers(self, reason: str = "") -> None:
        """Empty the result viewers so no stale data from another project shows."""
        for name, viewer in (
            ("mesh", getattr(self, "mesh_viewer", None)),
            ("S-params", getattr(self, "sparam_viewer", None)),
            ("NF2FF", getattr(self, "antenna_viewer", None)),
            ("field", getattr(self, "vtk_viewer", None)),
        ):
            if viewer is None:
                continue
            try:
                viewer.clear()
            except Exception as e:  # noqa: BLE001 — never crash on a reset
                print(f"{name} viewer clear failed: {e}")
        if reason:
            self._log(f"Result viewers cleared ({reason})")

    def _on_new(self) -> None:
        self.project = Project.new_default("untitled")
        self.primitives = []
        self._current_file_path = None
        # forget the previous project's mesh signature: a new project starts
        # empty and must not reuse a leftover work/untitled/csx.xml
        self._mesh_sig = None
        self._viewer_geometry_sig = None
        self._mesh_deleted = False
        self._clear_result_viewers("new project")
        self.tree.clear()
        self.geom_info.clear()
        self._refresh_materials()
        self._refresh_ports()
        self._sync_form_from_project()
        if hasattr(self.sim_params, "set_patch_group_visible"):
            self.sim_params.set_patch_group_visible(False)
        self._refresh_tree()
        self._update_window_title()
        self._log("New project created (generic — not patch-specific)")

    def _on_open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open project", str(self.settings.work_path()), "Project (*.fcsim.json *.json)"
        )
        if not path:
            return
        self._open_project_path(path)

    def _open_project_path(self, path: Path | str) -> None:
        """Load a project file and restore the viewers from its last results."""
        try:
            self._current_file_path = Path(path)
            self._mesh_sig = None  # different project -> loaded CSX is unknown
            self.project = Project.load(path)
            # Rebuild primitives from saved project.primitives / mesh_path / patch preset
            self.primitives = []
            import numpy as np  # noqa: F401

            if self.project.primitives:
                self.primitives = [dict(p) for p in self.project.primitives]
            if not self.primitives:
                for gf in self.project.geometry_files:
                    mp = getattr(gf, "mesh_path", "") or ""
                    if mp and Path(mp).exists():
                        for name in gf.object_names or [Path(gf.path).stem]:
                            self.primitives.append(
                                {
                                    "kind": "stl",
                                    "name": name,
                                    "material": "copper",
                                    "priority": 10,
                                    "path": mp,
                                }
                            )
            if self.project.patch_params and not self.primitives:
                from app.ui.sim_params import DEFAULT_PATCH_PARAMS

                kwargs = {k: float(v) for k, v in {**DEFAULT_PATCH_PARAMS, **self.project.patch_params}.items()}
                self.project, self.primitives = patch_antenna_primitives(**kwargs)
                self.sim_params.patch_params = dict(self.project.patch_params)
            self._sync_form_from_project()
            if hasattr(self.sim_params, "set_patch_group_visible"):
                self.sim_params.set_patch_group_visible(bool(self.project.patch_params))
            self._refresh_tree()
            self.geom_info.setPlainText(
                f"Opened {path}\n"
                f"Primitives: {len(self.primitives)}, ports: {len(self.project.ports)}, "
                f"materials: {len(self.project.materials)}"
            )
            self._refresh_materials()
            self._refresh_ports()
            self._log(f"Opened {path}")
            self._update_window_title()
            self._log_computed_summary()
            # remember for File → Recent projects (persisted in the settings)
            try:
                self.settings.remember_project(self._current_file_path or path)
                self.settings.save()
                self._refresh_recent_projects()
            except Exception as e:  # noqa: BLE001
                print("recent projects update failed:", e)
            self._load_last_results()
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            self._log(f"ERROR opening {path}: {e}")
            self._log(tb)
            QMessageBox.warning(
                self,
                "Open failed",
                f"Could not open project:\n{path}\n\n{e}\n\nSee Log tab for details.",
            )

    def _refresh_recent_projects(self) -> None:
        """Rebuild File → Recent projects from the settings MRU list."""
        menu = getattr(self, "_recent_menu", None)
        if menu is None:
            return
        menu.clear()
        items = self.settings.recent_projects_existing()
        if not items:
            empty = menu.addAction("(no recent projects)")
            empty.setEnabled(False)
            return
        for p in items:
            path = Path(p)
            act = menu.addAction(path.name)
            act.setToolTip(p)
            act.setStatusTip(p)
            act.triggered.connect(
                lambda _checked=False, pp=path: self._open_recent_project(pp)
            )
        menu.addSeparator()
        menu.addAction("Clear list").triggered.connect(self._clear_recent_projects)

    def _open_recent_project(self, path: Path) -> None:
        if not Path(path).exists():
            self._log(f"Recent project no longer exists: {path}")
            self.settings.recent_projects = [
                p for p in self.settings.recent_projects if Path(p).exists()
            ]
            self.settings.save()
            self._refresh_recent_projects()
            return
        self._open_project_path(path)

    def _clear_recent_projects(self) -> None:
        self.settings.recent_projects = []
        self.settings.save()
        self._refresh_recent_projects()
        self._log("Recent projects list cleared")

    def _ensure_project_dir(self) -> Path:
        """Create the project folder (and the result subfolders) if needed.

        Called whenever the project is saved so the layout is in place before
        the first run/build: ``<work>/<project>/{sparams,fields,nf2ff}``.
        """
        d = self._project_work_dir()
        try:
            for sub in (d, d / "sparams", d / "fields", d / "nf2ff"):
                sub.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            self._log(f"Could not create project folder {d}: {e}")
        return d

    def _project_work_dir(self) -> Path:
        """Project folder ``<work>/<project file name>`` holding the results.

        The folder is named after the project JSON file (which lives in the
        work directory); until the project has been saved it falls back to the
        project name.
        """
        stem = Path(self._current_file_path).stem if self._current_file_path else ""
        if stem.endswith(".fcsim"):
            stem = stem[: -len(".fcsim")]
        name = (stem or str(self.project.name or "")).strip() or "project"
        return self.settings.work_path() / name

    # ------------------------------------------------------------------ results
    @staticmethod
    def _existing_path(value) -> Path | None:
        """Existing path from a recorded string, else None.

        NOTE: ``Path("")`` is the cwd (``.``) and *does* exist — an empty
        recorded string must never be turned into a path.
        """
        s = str(value or "").strip()
        if not s:
            return None
        p = Path(s)
        return p if p.exists() else None

    def _find_sparams_files(self, project_dir: Path, sim_dir: Path | None = None) -> list[Path]:
        """``sparams.*`` of a project — new ``<project>/sparams`` layout first,
        then ``<project>/simulation``.

        The old name-based ``<work>/<Project.name>/simulation`` folder is only
        used for projects that record no results at all: a stale/generic
        ``Project.name`` (e.g. "untitled") must never pull in another project's
        data when the real results were deleted.
        """
        dirs = [Path(project_dir) / "sparams"]
        if sim_dir is not None:
            dirs.append(Path(sim_dir))
        dirs.append(Path(project_dir) / "simulation")
        rec = dict(self.project.results or {})
        legacy_name = str(self.project.name or "").strip()
        if (
            not rec.get("project_dir")
            and not rec.get("sim_dir")
            and not rec.get("sparams")
            and legacy_name.lower() not in ("", "untitled", "project")
        ):
            dirs.append(self.settings.work_path() / legacy_name / "simulation")
        for d in dirs:
            for pat in ("sparams.s*p", "sparams.json"):
                hits = sorted(d.glob(pat))
                if hits:
                    return hits
        return []

    def _collect_result_paths(self, project_dir: Path | None = None) -> dict:
        """Paths of the project's artifacts (only those that exist).

        New layout: ``<work>/<project>/{csx.xml, sparams/, fields/, nf2ff/}``;
        the legacy ``<project>/simulation/`` location is still searched.
        """
        project_dir = Path(project_dir) if project_dir else self._project_work_dir()
        sim_dir = project_dir / "simulation"  # legacy layout
        out: dict[str, Any] = {
            "project_dir": str(project_dir),
            "sim_dir": str(sim_dir),
        }
        csx = project_dir / "csx.xml"
        if not csx.exists():
            csx = sim_dir / "csx.xml"
        if csx.exists():
            out["csx_xml"] = str(csx)
        sparams = self._find_sparams_files(project_dir, sim_dir)
        if sparams:
            out["sparams"] = str(sparams[-1])
        nf2ff = project_dir / "nf2ff" / "nf2ff.json"
        if not nf2ff.exists():
            nf2ff = sim_dir / "nf2ff" / "nf2ff.json"
        if nf2ff.exists():
            out["nf2ff"] = str(nf2ff)
        return out

    def _record_results(self, sim_dir: Path | None = None, save: bool = True) -> None:
        """Store the last run's artifact paths in the project JSON.

        ``save`` writes the project file (if it has one) so a later
        *File → Open* can restore the Mesh Viewer / S-param Viewer straight from
        the recorded paths.
        """
        try:
            self.project.results = self._collect_result_paths(sim_dir)
            self.project.results["time"] = datetime.now().isoformat(timespec="seconds")
            if save and self._current_file_path and self._current_file_path.exists():
                self.project.primitives = list(self.primitives)
                self.project.save(self._current_file_path)
            keys = [k for k in ("csx_xml", "sparams", "nf2ff") if k in self.project.results]
            self._log(f"Recorded results in project ({', '.join(keys) or 'none'})")
        except Exception as e:  # noqa: BLE001
            print("record results failed:", e)

    def _load_recorded_csx(self) -> bool:
        """Load the CSX recorded in the project JSON into the Mesh Viewer.

        Prefers the last simulation's mesh (``csx_xml``), falls back to the
        mesh built by *Build Mesh* (``mesh_xml``).
        """
        rec = self.project.results or {}
        # A never-saved "untitled" project must not inherit a mesh from
        # work/untitled/csx.xml left behind by an earlier session.
        if self._current_file_path is None and not rec:
            return False
        # An empty project has nothing to draw, even if a csx.xml exists.
        if not (self.primitives or self.project.geometry_files):
            return False
        # recorded mesh -> fixed locations (new project folder, legacy
        # simulation/ folder)
        candidates = [rec.get("csx_xml"), rec.get("mesh_xml")]
        rec_sim = self._existing_path(rec.get("sim_dir"))
        if rec_sim is not None:
            candidates.append(rec_sim / "csx.xml")
        candidates += [
            self._project_work_dir() / "csx.xml",
            self._project_work_dir() / "simulation" / "csx.xml",
            # old name-based project folder
            self.settings.work_path()
            / (str(self.project.name or "").strip() or "project")
            / "simulation"
            / "csx.xml",
        ]
        for cand in candidates:
            xml = self._existing_path(cand)
            if xml is None:
                continue
            try:
                from CSXCAD import ContinuousStructure

                csx = ContinuousStructure()
                csx.ReadFromXML(str(xml))
                self.mesh_viewer.set_project_context(
                    project=self.project,
                    primitives=self.primitives,
                    ports=self.project.ports,
                    nf2ff=self.project.nf2ff,
                )
                self.mesh_viewer.set_csx(csx)
                self._mesh_sig = self._mesh_signature()
                self._log(f"Mesh Viewer: loaded recorded mesh {xml}")
                return True
            except Exception as e:  # noqa: BLE001
                self._log(f"Could not load recorded mesh {xml}: {e}")
        return False

    def _mesh_signature(self) -> str:
        """Hash of everything that shapes the CSX/mesh of the current project.

        Used to detect that the Mesh Viewer's CSX is stale after geometry,
        port, dump or mesh-parameter changes.
        """
        import hashlib
        import json as _json
        from dataclasses import asdict

        def _plain(obj):
            try:
                return asdict(obj)
            except Exception:  # noqa: BLE001
                return repr(obj)

        payload = {
            "primitives": self.primitives,
            "materials": [_plain(m) for m in self.project.materials],
            "assignments": [_plain(a) for a in self.project.assignments],
            "ports": [_plain(p) for p in self.project.ports],
            "dumps": [_plain(d) for d in self.project.dumps],
            "nf2ff": _plain(self.project.nf2ff),
            "mesh": _plain(self.project.mesh),
            "boundary": _plain(self.project.boundary),
        }
        blob = _json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()

    def _ensure_mesh_viewer_content(self) -> None:
        """Show the last mesh when the Mesh Viewer is opened.

        Rebuilds when the loaded CSX no longer matches the project (geometry,
        ports, dumps or mesh parameters changed); otherwise keeps it. Prefers
        recorded results only when nothing is loaded at all. The view type, slice
        plane and zoom the user set are kept across such a rebuild.
        """
        try:
            current = self._mesh_signature()
        except Exception:  # noqa: BLE001
            current = None
        if self._mesh_deleted:
            self._log("Mesh Viewer: mesh deleted — click Build Mesh to recreate it")
            return
        if self.mesh_viewer._csx is not None and self._mesh_sig == current:
            return  # up to date
        if self.mesh_viewer._csx is None and self._load_recorded_csx():
            return
        if not (self.primitives or self.project.geometry_files):
            self._log("Mesh Viewer: nothing to show yet (no geometry/primitives)")
            return
        if self.mesh_viewer._csx is not None:
            self._log("Mesh Viewer: geometry changed — rebuilding the mesh…")
        else:
            self._log("Mesh Viewer: no recorded mesh — building the mesh now…")
        try:
            self._auto_mesh_build = True  # entering the tab: keep the current view
            self._build_and_validate()
        except Exception as e:  # noqa: BLE001
            self._log(f"Mesh Viewer: automatic meshing failed: {e}")
        finally:
            self._auto_mesh_build = False

    def _maybe_refresh_viewer_geometry(self) -> None:
        """Show geometry edits in the Mesh Viewer right away (no mesh rebuild).

        Called after the project list changed (add/edit/remove/import): the
        viewer gets a freshly built scene CSX (geometry + domain, no easyMesh)
        with the current view/plane/zoom and the grid of the last build. The mesh
        itself is stale afterwards, so the next tab entry or Build Mesh refreshes
        it.
        """
        viewer = getattr(self, "mesh_viewer", None)
        if viewer is None or getattr(viewer, "_csx", None) is None:
            return  # nothing shown yet — entering the tab prepares it
        if not (self.primitives or self.project.geometry_files):
            return
        try:
            sig = self._mesh_signature()
        except Exception:  # noqa: BLE001
            return
        if sig == getattr(self, "_viewer_geometry_sig", None):
            return  # the viewer already shows this state
        self._viewer_geometry_sig = sig
        self._update_viewer_scene()

    def _update_viewer_scene(self) -> None:
        """Swap a scene CSX (geometry only) into the Mesh Viewer, view kept."""
        from app.meshing.build_task import build_scene_csx
        from app.meshing.preview import mesh_lines_from_csx

        viewer = self.mesh_viewer
        try:
            result = build_scene_csx(
                self.project, list(self.primitives), self._project_work_dir()
            )
        except Exception as e:  # noqa: BLE001
            self._log(f"Mesh Viewer: geometry update failed: {e}")
            return
        # carry the grid lines of the last build over, so the picture does not
        # jump to a coarse default grid while the mesh is stale
        try:
            old = getattr(viewer, "_csx", None)
            if old is not None:
                lines = mesh_lines_from_csx(old)
                grid = result.csx.GetGrid()
                for d, axis in enumerate("xyz"):
                    arr = lines.get(axis)
                    if arr is not None and len(arr) >= 2:
                        grid.SetLines(d, [float(v) for v in arr])
        except Exception as e:  # noqa: BLE001
            print("mesh viewer grid carry-over failed:", e)
        try:
            viewer.set_project_context(
                project=self.project,
                primitives=self.primitives,
                ports=self.project.ports,
                nf2ff=self.project.nf2ff,
            )
            old_csx = getattr(viewer, "_csx", None)
            viewer.set_csx(result.csx, keep_view=True)
        except Exception as e:  # noqa: BLE001
            self._log(f"Mesh Viewer: geometry update failed: {e}")
            return
        # CSXCAD/openEMS objects free their C++ side on destruction; a viewer or
        # matplotlib reference into a freed object hard-crashes the GUI, so both
        # the replaced CSX and the new scene FDTD are parked (like a mesh build).
        self._park_retired(old_csx)
        self._park_retired(result.csx, getattr(result, "fdtd", None))
        # the mesh grid is from the previous build now -> rebuild on next entry
        self._mesh_sig = None
        self._log(
            "Mesh Viewer: geometry updated (mesh grid from the last build — "
            "Build Mesh (Ctrl+M) refreshes it)"
        )

    def _park_retired(self, csx=None, fdtd=None) -> None:
        """Keep CSXCAD/openEMS objects alive (their destructor can crash the GUI)."""
        for name, obj in (("_retired_csx", csx), ("_retired_fdtd", fdtd)):
            if obj is None:
                continue
            lst = getattr(self, name, None)
            if not isinstance(lst, list):
                lst = []
                setattr(self, name, lst)
            lst.append(obj)
            if len(lst) > 4:  # bound memory
                del lst[:-4]

    def _find_field_series(self, project_dir: Path) -> Path | None:
        """Newest exported field dump (``<project>/vtk/*.vtr``), or ``None``.

        Prefers the first step of the newest series (``<stem>_0000.vtr``) so the
        film starts at the beginning.
        """
        import re

        vtk_dir = Path(project_dir) / "vtk"
        if not vtk_dir.is_dir():
            return None
        vtrs = sorted(
            vtk_dir.glob("*.vtr"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not vtrs:
            return None
        m = re.match(r"(?P<base>.+)_(?P<idx>\d+)$", vtrs[0].stem)
        if m:
            first = vtk_dir / f"{m.group('base')}_0000.vtr"
            if first.exists():
                return first
        return vtrs[0]

    def _load_last_results(self) -> None:
        """Restore the viewers from the project's results — or empty them.

        Prefers the paths recorded in the project JSON (``Project.results``,
        written after every run) and falls back to the project folder
        (``<work>/<project>/{csx.xml, sparams/, vtk/}``) plus the legacy
        ``<project>/simulation/`` location for older projects.

        A viewer with *no* data for the opened project (e.g. the results were
        deleted) is cleared, so nothing from the previously opened project
        stays on screen.
        """
        rec = dict(self.project.results or {})
        project_dir = self._existing_path(rec.get("project_dir")) or self._project_work_dir()
        sim_dir = self._existing_path(rec.get("sim_dir")) or (project_dir / "simulation")
        loaded: list[str] = []
        cleared: list[str] = []

        # --- Mesh Viewer: the meshed CSX of that run (or the last build) ----
        if self._load_recorded_csx():
            loaded.append("mesh")
        else:
            try:
                self.mesh_viewer.clear()
            except Exception as e:  # noqa: BLE001
                print("mesh viewer clear failed:", e)
            cleared.append("mesh")

        # --- S-param Viewer: recorded file, else newest of THIS project -----
        self.sparam_viewer.set_work_dir(project_dir)
        recorded = self._existing_path(rec.get("sparams"))
        cands = [recorded] if recorded is not None else self._find_sparams_files(project_dir, sim_dir)
        ok = False
        if cands:
            try:
                self.sparam_viewer.load_path(cands[-1])
                loaded.append(f"S-params ({cands[-1]})")
                ok = True
            except Exception as e:  # noqa: BLE001
                self._log(f"Could not restore S-params {cands[-1].name}: {e}")
        if not ok:
            self.sparam_viewer.clear()
            cleared.append("S-params")

        # --- Antenna pattern (NF2FF): recorded, else this project's file ----
        self.antenna_viewer.set_work_dir(self.settings.work_path())
        nf2ff = next(
            (
                c
                for c in (
                    self._existing_path(rec.get("nf2ff")),
                    self._existing_path(project_dir / "nf2ff" / "nf2ff.json"),
                    self._existing_path(sim_dir / "nf2ff" / "nf2ff.json"),
                )
                if c is not None
            ),
            None,
        )
        ok = False
        if nf2ff is not None:
            try:
                self.antenna_viewer.load_path(nf2ff)
                loaded.append(f"NF2FF ({nf2ff})")
                ok = True
            except Exception as e:  # noqa: BLE001
                self._log(f"Could not restore NF2FF {nf2ff.name}: {e}")
        if not ok:
            self.antenna_viewer.clear()
            cleared.append("NF2FF")

        # --- Field Viewer (VTK): newest exported dump of THIS project -------
        self.vtk_viewer.set_work_dir(project_dir)
        series = self._find_field_series(project_dir)
        ok = False
        if series is not None:
            try:
                self.vtk_viewer.load(series)
                loaded.append(f"field ({series})")
                ok = True
            except Exception as e:  # noqa: BLE001
                self._log(f"Could not restore field dump {series.name}: {e}")
        if not ok:
            self.vtk_viewer.clear()
            cleared.append("field")

        if loaded:
            self._log(f"Restored last results from {project_dir}: " + ", ".join(loaded))
        if cleared:
            self._log(
                f"No results for {project_dir}: " + ", ".join(cleared)
                + " — viewer(s) cleared"
            )

    def _on_save(self) -> None:
        self._sync_project_from_form()
        self.project.primitives = list(self.primitives)
        if self._current_file_path and self._current_file_path.exists():
            saved = self.project.save(self._current_file_path)
            self._ensure_project_dir()
            self._update_window_title()
            self._log(f"Saved {saved}")
            return
        # No known file yet — behave like Save As
        self._on_save_as()

    def _on_save_as(self) -> None:
        self._sync_project_from_form()
        self.project.primitives = list(self.primitives)
        initial = str(self.settings.work_path() / f"{self.project.name}.fcsim.json")
        if self._current_file_path:
            initial = str(self._current_file_path)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save project as",
            initial,
            "Project (*.fcsim.json)",
        )
        if not path:
            return
        self._current_file_path = Path(path)
        saved = self.project.save(path)
        self._ensure_project_dir()
        self._update_window_title()
        self._log(f"Saved {saved}")
        try:
            self.settings.remember_project(saved)
            self.settings.save()
            self._refresh_recent_projects()
        except Exception as e:  # noqa: BLE001
            print("recent projects update failed:", e)

    def _object_names_for_dialog(self) -> list[str]:
        names: list[str] = []
        for gf in self.project.geometry_files:
            names.extend(gf.object_names or [])
            if gf.path and Path(gf.path).stem not in names:
                names.append(Path(gf.path).stem)
        for a in self.project.assignments:
            if a.object_name and a.object_name not in names:
                names.append(a.object_name)
        for pr in self.primitives:
            if pr.get("name") and pr["name"] not in names:
                names.append(str(pr["name"]))
        return names

    # ----- per-object Mesh Viewer display (edited in the object dialogs) ----
    def _object_display(self, name: str) -> dict:
        """Mesh Viewer appearance of one object (defaults when none are set)."""
        store = getattr(self.project, "object_display", None)
        raw = store.get(name) if isinstance(store, dict) else None
        return normalize_display(raw)

    def _store_object_display(self, name: str, values: dict | None) -> None:
        """Store the display options of one object and repaint the Mesh Viewer."""
        name = str(name or "").strip()
        if not name:
            return
        if not isinstance(getattr(self.project, "object_display", None), dict):
            self.project.object_display = {}
        self.project.object_display[name] = normalize_display(values)
        viewer = getattr(self, "mesh_viewer", None)
        if viewer is not None:
            try:
                viewer.refresh_view()  # colour / transparency / visibility
            except Exception as e:  # noqa: BLE001 - never block the edit
                print("mesh viewer display refresh failed:", e)

    def _rename_object_display(self, old: str, new: str) -> None:
        """Keep the display settings when an object is renamed."""
        store = getattr(self.project, "object_display", None)
        if not isinstance(store, dict) or not old or not new or old == new:
            return
        if old in store:
            store[new] = store.pop(old)

    def _material_names(self) -> list[str]:
        return [m.name for m in self.project.materials] or ["copper", "substrate", "vacuum"]

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Import FreeCAD geometry (STEP/STL/OBJ/FCStd/manifest)",
            "",
            "CAD / FreeCAD meshes (*.step *.stp *.stl *.obj *.fcstd *.json);;All files (*)",
        )
        if not path:
            return
        self._import_geometry_path(path)

    def _on_freecad(self) -> None:
        """Launch FreeCAD GUI or export FCStd/STEP → openEMS meshes."""
        from app.ui.freecad_tools import FreeCADToolsDialog

        work = self.settings.work_path()
        default_src = ""
        for gf in self.project.geometry_files:
            if gf.path and Path(gf.path).exists():
                default_src = gf.path
                break
        if not default_src:
            yagi = work / "yagi_antenna" / "yagi_2p45.step"
            if yagi.exists():
                default_src = str(yagi)

        dlg = FreeCADToolsDialog(
            self,
            freecad_cmd=self.settings.freecad_cmd,
            freecad_gui=getattr(self.settings, "freecad_gui", ""),
            work_dir=str(work),
            default_source=default_src,
        )
        if not dlg.exec():
            return
        if dlg.last_action == "launch":
            self._log(dlg.status.text() or "FreeCAD launched")
            self._log(
                "FreeCAD: draw solids (clear names) → File → Export → STEP "
                "(or save FCStd), then use FreeCAD… → Export CAD…"
            )
            return
        if dlg.last_action == "export":
            self._log(f"FreeCAD export done: {dlg.result_out_dir}")
            self._log(dlg.status.text())
            src = dlg.result_source
            if dlg.result_import and src:
                self._log(f"Importing FreeCAD export into project: {src}")
                self._import_geometry_path(src)
            elif src:
                self._log(
                    f"Export ready (import skipped). Use Import Geometry… for: {src}"
                )

    def _import_geometry_path(self, path: str) -> None:
        import json as _json
        import numpy as np

        from app.freecad_io.freecad_mesh import tessellate_cad_to_meshes
        from app.freecad_io.port_marks import find_manifest_ports, parse_ports_payload, ports_to_port_defs
        from app.models.project import GeometryFile

        fmt = Path(path).suffix.lower().lstrip(".")
        if not fmt and Path(path).is_dir():
            # FreeCAD export folder (stl/ with per-object meshes)
            fmt = "stl"
        obj_dir = self.settings.work_path() / "imported" / Path(path).stem
        obj_dir.mkdir(parents=True, exist_ok=True)
        object_names: list[str] = []
        mesh_paths: list[str] = []
        lines: list[str] = []
        warnings: list[str] = []
        freecad_ports: list = []

        imported_meshes: list = []
        if fmt in {"step", "stp", "fcstd"}:
            # FreeCAD BREP → triangle meshes for openEMS
            lines.append(
                f"Tessellating FreeCAD CAD ({fmt}) via FreeCADCmd → {obj_dir}"
            )
            mesh_result = tessellate_cad_to_meshes(
                path,
                freecad_cmd=self.settings.freecad_cmd,
                out_dir=obj_dir,
            )
            warnings.extend(mesh_result.warnings)
            imported_meshes = mesh_result.objects
            freecad_ports = list(getattr(mesh_result, "ports", None) or [])
            if not mesh_result.ok:
                lines.append("ERROR: FreeCAD tessellation failed — check FreeCADCmd in Settings.")
                for w in mesh_result.warnings:
                    lines.append(f"WARNING: {w}")
        else:
            # STL/OBJ/manifest/folder: read FreeCAD mesh objects directly
            from app.freecad_io.stl_import import load_stl_meshes

            mesh_res = load_stl_meshes(path, out_dir=obj_dir)
            warnings.extend(mesh_res.warnings)
            if mesh_res.objects:
                lines.append(
                    f"FreeCAD meshes ({len(mesh_res.objects)} objects) → {obj_dir}"
                )
                imported_meshes = list(mesh_res.objects)
            else:
                # fallback: single mesh file / CAD bounds via importer
                result = load_geometry_file(
                    path,
                    freecad_cmd=self.settings.freecad_cmd,
                    work_dir=obj_dir,
                )
                warnings.extend(result.warnings)
                for o in result.objects:
                    tris = getattr(o, "triangles", None)
                    npy = ""
                    stl = ""
                    if tris is not None and len(tris):
                        npy_path = obj_dir / f"{o.name}.npy"
                        stl_path = obj_dir / f"{o.name}.stl"
                        np.save(str(npy_path), tris)
                        npy = str(npy_path)
                        stl = str(stl_path)
                        # also write binary STL for external tools
                        try:
                            n = len(tris)
                            import struct

                            with open(stl_path, "wb") as f:
                                f.write(b"stl".ljust(80, b" ")[:80])
                                f.write(struct.pack("<I", n))
                                for t in tris:
                                    f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
                                    for p in t:
                                        f.write(struct.pack("<3f", float(p[0]), float(p[1]), float(p[2])))
                                    f.write(struct.pack("<H", 0))
                        except Exception:
                            pass

                    class _M:
                        pass

                    m = _M()
                    m.name = o.name
                    m.format = o.format
                    m.source_path = o.path
                    m.npy_path = npy
                    m.stl_path = stl
                    m.bounds_min = o.bounds_min
                    m.bounds_max = o.bounds_max
                    m.triangle_count = int(len(tris)) if tris is not None else 0
                    m.notes = o.notes
                    imported_meshes.append(m)

        for m in imported_meshes:
            name = m.name
            object_names.append(name)
            bmin = m.bounds_min
            bmax = m.bounds_max
            pd = getattr(m, "primitive", None)
            if pd is not None:
                lines.append(
                    f"{name} [{m.format}] analytic={pd.get('kind', '?')} "
                    f"bounds={bmin}..{bmax}"
                )
            else:
                lines.append(
                    f"{name} [{m.format}] triangles={getattr(m, 'triangle_count', 0)} "
                    f"bounds={bmin}..{bmax}"
                )

            # Re-import: refresh a primitive that came from this same source
            # file (legacy entries without source_file match on kind=stl).
            dup = next(
                (
                    i
                    for i, pr in enumerate(self.primitives)
                    if pr.get("name") == name
                    and (
                        pr.get("source_file") == path
                        or (not pr.get("source_file") and pr.get("kind") == "stl")
                    )
                ),
                None,
            )
            # Material/priority: existing assignment (preset/manual) wins, then
            # the material already on a re-imported primitive (persists across
            # re-imports), then the copper PEC default.
            assign = next(
                (a for a in self.project.assignments if a.object_name == name), None
            )
            if assign is not None and assign.role != "ignore":
                mat = assign.material or (
                    "copper" if assign.role in ("pec", "conducting_sheet") else ""
                )
                pri = int(assign.priority)
            elif dup is not None:
                mat = self.primitives[dup].get("material") or "copper"
                pri = int(self.primitives[dup].get("priority", 10))
            else:
                mat, pri = "copper", 10

            if pd is not None:
                # Native openEMS primitive (box/cylinder/sphere/polygon) —
                # exact geometry, no STL. No auto-assignment: assignments are
                # created from presets or manually, so a re-import keeps the
                # user's material choices instead of re-adding copper rows.
                prim = {"kind": str(pd.get("kind") or ""), "name": name}
                for key, val in pd.items():
                    if key != "kind":
                        prim[key] = val
                # bbox start/stop for the viewer slice rect (sphere/polygon
                # params themselves carry no bbox)
                prim.setdefault("start", list(bmin) if bmin else [0, 0, 0])
                prim.setdefault("stop", list(bmax) if bmax else [0, 0, 0])
                prim["material"] = mat
                prim["priority"] = pri
                prim["source_format"] = getattr(m, "format", "") or fmt
                prim["source_file"] = path
                if dup is None:
                    self.primitives.append(prim)
                else:
                    # re-import: replace in place (an old stl prim of this
                    # object gets upgraded to the analytic primitive)
                    self.primitives[dup] = prim
                    lines.append(f"updated existing mesh primitive: {name}")
                continue

            mesh_path = ""
            npy = getattr(m, "npy_path", "") or ""
            if npy and Path(npy).exists():
                mesh_path = npy
                mesh_paths.append(mesh_path)
            elif getattr(m, "stl_path", "") and Path(m.stl_path).exists():
                # load stl → npy for CSX.AddMesh
                tris = None
                try:
                    from app.freecad_io.freecad_mesh import load_stl_or_obj_triangles

                    tris = load_stl_or_obj_triangles(m.stl_path)
                except Exception:
                    tris = None
                if tris is not None and len(tris):
                    npy_path = obj_dir / f"{name}.npy"
                    np.save(str(npy_path), tris)
                    mesh_path = str(npy_path)
                    mesh_paths.append(mesh_path)

            if mesh_path:
                stl_out = ""
                if getattr(m, "stl_path", "") and Path(m.stl_path).exists():
                    stl_out = str(m.stl_path)
                else:
                    stl_candidate = obj_dir / f"{name}.stl"
                    if stl_candidate.exists():
                        stl_out = str(stl_candidate)
                prim = {
                    "kind": "stl",
                    "name": name,
                    "material": mat,
                    "priority": pri,
                    "path": mesh_path,
                    "mesh_path": mesh_path,
                    "stl_path": stl_out,
                    "start": list(bmin) if bmin else [0, 0, 0],
                    "stop": list(bmax) if bmax else [0, 0, 0],
                    "source_format": getattr(m, "format", "") or fmt,
                    "source_file": path,
                }
                if dup is None:
                    self.primitives.append(prim)
                else:
                    # re-import: refresh mesh in place
                    self.primitives[dup] = {**self.primitives[dup], **prim}
                    lines.append(f"updated existing mesh primitive: {name}")
            else:
                warnings.append(f"No simulation mesh for {name} (no triangles)")

        # --- FreeCAD port marks → PortDef (Settings → port defaults) ---
        port_defaults = self.settings.port_defaults()
        if not freecad_ports:
            # STEP import may sit next to openems_export.json
            for cand in (
                Path(path).parent / "openems_export.json",
                obj_dir / "openems_export.json",
            ):
                freecad_ports = find_manifest_ports(str(cand), port_defaults)
                if freecad_ports:
                    lines.append(f"Ports from manifest: {cand}")
                    break
        if freecad_ports:
            new_defs = ports_to_port_defs(
                parse_ports_payload(freecad_ports, port_defaults),
                port_defaults,
            )
            existing = {p.name.lower() for p in self.project.ports}
            added = 0
            for pd in new_defs:
                if pd.name.lower() in existing:
                    continue
                self.project.ports.append(pd)
                existing.add(pd.name.lower())
                added += 1
            lines.append(
                f"FreeCAD ports detected: {len(new_defs)} (added {added}); "
                f"defaults type={port_defaults.get('type')} "
                f"R={port_defaults.get('resistance')} "
                f"excite={port_defaults.get('excite_port')}"
            )
            self._log(
                f"FreeCAD ports detected: {len(new_defs)} (added {added}) "
                f"[defaults type={port_defaults.get('type')} "
                f"R={port_defaults.get('resistance')}]"
            )
            for pd in new_defs:
                self._log(
                    f"  Port {pd.name}: {pd.type} {pd.start}→{pd.stop} R={pd.resistance} "
                    f"dir={pd.direction} excite={pd.excite_port}"
                )
            if added:
                self._log(
                    "  Edit ports: double-click in Ports / Dumps "
                    "(defaults: Settings -> Port defaults)"
                )

        gf = GeometryFile(
            path=path,
            format=fmt,
            unit="mm",
            mesh_path=mesh_paths[0] if mesh_paths else "",
            object_names=object_names,
        )
        existing_gf = next(
            (i for i, g in enumerate(self.project.geometry_files) if g.path == path), None
        )
        if existing_gf is None:
            self.project.geometry_files.append(gf)
        else:
            self.project.geometry_files[existing_gf] = gf
        # Remember tessellated mesh directory for script_gen fallback
        note = f"import_mesh_dir={obj_dir}"
        if note not in (self.project.notes or ""):
            self.project.notes = (self.project.notes + "\n" if self.project.notes else "") + note
        self._sync_assignment_materials_to_primitives()

        for w in warnings:
            lines.append(f"WARNING: {w}")
        self.geom_info.setPlainText("\n".join(lines) or "No objects")
        self._log(f"Imported {path}")
        if mesh_paths:
            self._log(f"Simulation meshes (.npy): {len(mesh_paths)} object(s)")
        for w in warnings:
            self._log(f"WARNING: {w}")
        if fmt in {"step", "stp", "fcstd"}:
            self._log(
                "Tip: FreeCAD → mark ports (PORT_x_start/stop points or openems_port_* props) "
                "→ Export CAD… / Import; ports are auto-added."
            )
        elif fmt in {"stl", "obj", "json"} or Path(path).is_dir():
            self._log(
                "Tip: STL meshes are simulated as Polyhedron surfaces; "
                "change material/role via Assignment."
            )
        self._refresh_ports()
        self._refresh_materials()
        self._refresh_tree()

    def _sync_assignment_materials_to_primitives(self) -> None:
        """Keep primitive materials in sync with Geometry assignment dialogs."""
        for a in self.project.assignments:
            if a.role == "ignore":
                continue
            mat = (a.material or "").strip()
            if not mat:
                mat = "copper" if a.role == "pec" else a.role
            for pr in self.primitives:
                if pr.get("name") == a.object_name:
                    pr["material"] = mat
                    pr["priority"] = int(a.priority)

    def _on_load_patch(self) -> None:
        from app.ui.sim_params import DEFAULT_PATCH_PARAMS

        self._current_file_path = None
        if self.project.patch_params:
            self.sim_params.patch_params = {
                **DEFAULT_PATCH_PARAMS,
                **{k: float(v) for k, v in self.project.patch_params.items()},
            }
        else:
            self.sim_params.patch_params = dict(DEFAULT_PATCH_PARAMS)
        kwargs = {k: float(self.sim_params.patch_params[k]) for k in DEFAULT_PATCH_PARAMS}
        self.project, self.primitives = patch_antenna_primitives(**kwargs)
        self.project.patch_params = dict(self.sim_params.patch_params)
        self._sync_form_from_project()
        self.sim_params.set_patch_group_visible(True)
        self._refresh_tree()
        self._refresh_ports()
        self.geom_info.setPlainText(
            "Patch antenna PRESET loaded (not required for other antennas/PCBs).\n"
            "Use Import Geometry / Add Primitive / Add Port for custom structures.\n"
            "Simulation Parameters window holds patch overrides when this preset is active."
        )
        self._update_window_title()
        self._log("Patch antenna preset loaded")
        self._log_computed_summary()

    def _on_load_yagi(self) -> None:
        """Load 2.45 GHz Yagi-Uda preset + generate FreeCAD model if possible."""
        from app.csxcad_builder.primitives import yagi_antenna_primitives
        from app.models.project import GeometryFile

        self._current_file_path = None

        project, primitives = yagi_antenna_primitives()
        self.project = project
        self.primitives = primitives
        self.project.primitives = list(primitives)
        if hasattr(self.sim_params, "set_patch_group_visible"):
            self.sim_params.set_patch_group_visible(False)
        self._sync_form_from_project()
        self._refresh_tree()
        self._refresh_ports()
        self._refresh_materials()

        work = self.settings.work_path() / "yagi_antenna"
        work.mkdir(parents=True, exist_ok=True)
        lines = [
            "Yagi-Uda PRESET loaded (2.45 GHz, 5 elements + boom).",
            "X=elements, Y=boom, Z=up. Lumped 50 Ω port at driven dipole gap.",
            f"Work dir: {work}",
        ]
        model_info = {"ok": False}
        try:
            from examples.run_yagi_example import generate_freecad_model

            model_info = generate_freecad_model(work, freecad_cmd=Path(self.settings.freecad_cmd))
            if model_info.get("ok") and Path(model_info["step"]).exists():
                # Analytic primitives already define the full geometry. Do NOT
                # register the STEP in geometry_files — that would make script_gen
                # tessellate it and add 14 duplicate STL primitives next to the
                # analytic cylinders (boom, reflector, driven_*, director_*).
                lines += [
                    f"FreeCAD model (reference only): {model_info['fcstd']}",
                    f"STEP: {model_info['step']}",
                    f"STL: {model_info['stl']}",
                    f"Params: {model_info['params']}",
                    "Simulation uses the analytic primitives below (STEP is reference only).",
                ]
            else:
                lines.append(model_info.get("log") or "FreeCAD model not generated (check FreeCADCmd path).")
        except Exception as e:  # noqa: BLE001
            lines.append(f"FreeCAD model generation error: {e}")
            self._log(f"Yagi FreeCAD model error: {e}")

        self.geom_info.setPlainText("\n".join(lines))
        self._log("Yagi antenna preset loaded")
        for ln in lines:
            self._log(ln)
        self._log_computed_summary()
        self._update_window_title()

    def _on_load_pcb(self, use_freecad: bool = True) -> None:
        self._current_file_path = None
        """Load the 2-port microstrip notch filter preset.

        Default path: FreeCADCmd builds the board as named solids, the export
        turns them into per-object STL meshes and the simulation runs on those
        meshes (same as Import Geometry). Analytic boxes only as fallback when
        FreeCAD is unavailable.
        """
        from app.csxcad_builder.primitives import pcb_filter_primitives

        work = self.settings.work_path() / "pcb_filter"
        work.mkdir(parents=True, exist_ok=True)

        project = None
        prims: list[dict] = []
        source_lines: list[str] = []
        if use_freecad:
            try:
                from examples.run_pcb_filter_example import prepare_freecad_project

                self._log("PCB filter preset: building FreeCAD model (please wait) ...")
                project, prims, info = prepare_freecad_project(
                    work, freecad_cmd=Path(self.settings.freecad_cmd)
                )
                if project is None:
                    self._log(f"FreeCAD PCB model failed: {info.get('error')}")
                else:
                    port_txt = ", ".join(
                        f"{p.name}{' [exc]' if p.excite_port else ''}"
                        for p in project.ports
                    )
                    source_lines = [
                        f"FreeCAD model: {info.get('fcstd')}",
                        f"STEP: {info.get('step')}",
                        f"Mesh manifest: {info.get('manifest')}",
                        "FreeCAD solids -> STL meshes: "
                        + ", ".join(info.get("objects") or []),
                        f"Ports (FreeCAD marks): {port_txt}",
                    ]
            except Exception as e:  # noqa: BLE001
                self._log(f"FreeCAD PCB model error: {e}")
                project = None

        if project is None:
            project, prims = pcb_filter_primitives()
            source_lines = [
                "FreeCAD path unavailable - analytic boxes (fallback).",
                "Set FreeCADCmd in Settings to build the board in FreeCAD instead.",
            ]

        self.project = project
        self.primitives = prims
        self.project.primitives = list(prims)
        if hasattr(self.sim_params, "set_patch_group_visible"):
            self.sim_params.set_patch_group_visible(False)
        self._sync_form_from_project()
        self._refresh_tree()
        self._refresh_ports()
        self._refresh_materials()

        stub = next((p for p in prims if p.get("name") == "msl_stub"), None)
        stub_len = (stub["stop"][1] - stub["start"][1]) if stub else 0.0
        lines = [
            "PCB filter PRESET loaded: 2-port microstrip notch filter on FR4 1.6 mm.",
            f"50 Ω line + open λg/4 stub ({stub_len:.2f} mm) -> stop band at 2.45 GHz.",
            *source_lines,
            "Two lumped edge ports: one run measures one S-matrix column — "
            "examples/run_pcb_filter_example.py runs both and merges them to S2P.",
            f"Work dir: {work}",
        ]
        self.geom_info.setPlainText("\n".join(lines))
        self._update_window_title()
        self._log("PCB filter preset loaded")
        for ln in lines:
            self._log(ln)
        self._log_computed_summary()

    def _on_load_pcb_bandpass(self) -> None:
        """Load the 5.6 GHz 5-pole stub band-pass preset (PTFE, no vias)."""
        from app.csxcad_builder.primitives import pcb_bandpass_stub_primitives

        self._current_file_path = None
        work = self.settings.work_path() / "pcb_bandpass"
        work.mkdir(parents=True, exist_ok=True)

        project, prims = pcb_bandpass_stub_primitives()
        project.ports[0].excite_port = True
        project.ports[1].excite_port = False
        self.project = project
        self.primitives = prims
        self.project.primitives = list(prims)
        if hasattr(self.sim_params, "set_patch_group_visible"):
            self.sim_params.set_patch_group_visible(False)
        self._sync_form_from_project()
        self._refresh_tree()
        self._refresh_ports()
        self._refresh_materials()

        saved = project.save(work / "pcb_bandpass.fcsim.json")
        stubs = [p for p in prims if str(p.get("name", "")).startswith("stub_")]
        stub_txt = ", ".join(
            f"{(s['stop'][0] - s['start'][0]):.2f}x{(s['stop'][1] - s['start'][1]):.1f} mm"
            for s in stubs
        )
        lines = [
            "PCB band-pass PRESET loaded: 5-pole Chebyshev, 0.1 dB ripple, "
            "f0 = 5.6 GHz.",
            "PTFE (Teflon) 0.254 mm, eps_r 2.1, tan_d 0.0002 — five open "
            "lambda/2 stubs, no vias.",
            f"Stubs (width x length): {stub_txt}",
            "lambda/4 connecting lines act as impedance inverters; ~30 % "
            "bandwidth, |S11| < -10 dB from 4.9 to 6.45 GHz.",
            "Two lumped edge ports: one run measures one S-matrix column — "
            "examples/run_pcb_bandpass_example.py runs both and merges to S2P.",
            f"Project: {saved}",
        ]
        self.geom_info.setPlainText("\n".join(lines))
        self._update_window_title()
        self._log("PCB band-pass preset loaded")
        for ln in lines:
            self._log(ln)
        self._log_computed_summary()

    def _on_load_pcb_hairpin(self) -> None:
        """Load the compact 2.4 GHz 5-pole hairpin band-pass preset (RT/duroid)."""
        from app.csxcad_builder.hairpin import (
            HAIRPIN_BANDPASS_DESIGN,
            hairpin_bandpass_design,
            pcb_hairpin_primitives,
        )

        self._current_file_path = None
        work = self.settings.work_path() / "pcb_hairpin"
        work.mkdir(parents=True, exist_ok=True)

        project, prims = pcb_hairpin_primitives()
        project.ports[0].excite_port = True
        project.ports[1].excite_port = False
        self.project = project
        self.primitives = prims
        self.project.primitives = list(prims)
        if hasattr(self.sim_params, "set_patch_group_visible"):
            self.sim_params.set_patch_group_visible(False)
        self._sync_form_from_project()
        self._refresh_tree()
        self._refresh_ports()
        self._refresh_materials()

        saved = project.save(work / "pcb_hairpin.fcsim.json")
        des = hairpin_bandpass_design()
        mat = HAIRPIN_BANDPASS_DESIGN
        lines = [
            "PCB hairpin band-pass PRESET loaded: 5-pole Chebyshev, 0.1 dB ripple, "
            "f0 = 2.4 GHz.",
            f"RT/duroid 6010LM: eps_r {mat['eps_r']}, tan_d {mat['tand']}, "
            f"{mat['h']} mm, 35 um copper — five folded half-wave resonators, no vias.",
            f"50 ohm line {des['w50']:.2f} mm, resonator arm {des['arm']:.2f} mm, "
            f"coupling gaps "
            + ", ".join(f"{g:.3f}" for g in des["gaps"])
            + " mm.",
            f"~{mat['fbw'] * 100:.0f} % bandwidth around 2.4 GHz; the layout is only "
            f"{des['span']:.1f} mm long (folded resonators).",
            "Two lumped edge ports: one run measures one S-matrix column — "
            "examples/run_pcb_hairpin_example.py runs both and merges to S2P.",
            f"Project: {saved}",
        ]
        self.geom_info.setPlainText("\n".join(lines))
        self._update_window_title()
        self._log("PCB hairpin band-pass preset loaded")
        for ln in lines:
            self._log(ln)
        self._log_computed_summary()

    def _on_add_assignment(self) -> None:
        from app.ui.geometry_editors import AssignmentDialog

        dlg = AssignmentDialog(
            self._object_names_for_dialog(),
            self._material_names(),
            self,
            display_for=self._object_display,
        )
        if dlg.exec() and dlg.result:
            a = dlg.result
            # replace existing assignment for same object
            self.project.assignments = [
                x for x in self.project.assignments if x.object_name != a.object_name
            ]
            self.project.assignments.append(a)
            self._store_object_display(a.object_name, dlg.display_values())
            self._sync_assignment_materials_to_primitives()
            self._refresh_tree()
            self._log(f"Assignment: {a.object_name} -> {a.role}/{a.material}")

    def _on_add_material(self) -> None:
        from app.ui.geometry_editors import MaterialDialog

        dlg = MaterialDialog(self)
        if dlg.exec() and dlg.result:
            m = dlg.result
            self.project.materials = [x for x in self.project.materials if x.name != m.name]
            self.project.materials.append(m)
            self._refresh_materials()
            self._log(f"Material: {m.name} ({m.kind}, εr={m.epsilon})")

    def _on_add_prim_box(self) -> None:
        from app.ui.geometry_editors import PrimitiveBoxDialog

        dlg = PrimitiveBoxDialog(self, coord_system=self.project.mesh.coord_system)
        if dlg.exec() and dlg.result:
            pr = dlg.result
            self.primitives = [p for p in self.primitives if p.get("name") != pr["name"]]
            self.primitives.append(pr)
            self._store_object_display(pr["name"], dlg.display_values())
            self._log(f"Box primitive: {pr['name']} {pr['material']} {pr['start']}→{pr['stop']}")
            self._refresh_tree()

    def _on_add_prim_cyl(self) -> None:
        from app.ui.geometry_editors import PrimitiveCylinderDialog

        dlg = PrimitiveCylinderDialog(self, coord_system=self.project.mesh.coord_system)
        if dlg.exec() and dlg.result:
            pr = dlg.result
            self.primitives = [p for p in self.primitives if p.get("name") != pr["name"]]
            self.primitives.append(pr)
            self._store_object_display(pr["name"], dlg.display_values())
            self._log(
                f"Cylinder primitive: {pr['name']} r={pr['radius']} "
                f"{pr['start']}→{pr['stop']}"
            )
            self._refresh_tree()

    def _polar_prim_blocked(self, kind: str) -> bool:
        """Cylindrical mesh: refuse primitives that only exist on a cartesian grid.

        openEMS primitives other than box/cylinder (and STL meshes) cannot be
        placed on a polar grid — csxcad_builder.meshing.polar rejects them at
        run time; block the dialogs early with a clear message instead.
        """
        if self.project.mesh.coord_system == "cylindrical" and kind not in {
            "box",
            "cylinder",
        }:
            self._log(
                f"'{kind}' primitives are not supported in cylindrical mode. "
                "Switch Mesh Parameters -> Coordinate system to cartesian."
            )
            return True
        return False

    def _on_add_prim_sphere(self) -> None:
        from app.ui.geometry_editors import PrimitiveSphereDialog

        if self._polar_prim_blocked("sphere"):
            return
        dlg = PrimitiveSphereDialog(self, coord_system=self.project.mesh.coord_system)
        if dlg.exec() and dlg.result:
            pr = dlg.result
            self.primitives = [p for p in self.primitives if p.get("name") != pr["name"]]
            self.primitives.append(pr)
            self._store_object_display(pr["name"], dlg.display_values())
            self._log(
                f"Sphere primitive: {pr['name']} r={pr['radius']} center={pr['center']}"
            )
            self._refresh_tree()

    def _on_add_prim_polygon(self) -> None:
        from app.ui.geometry_editors import PrimitivePolygonDialog

        if self._polar_prim_blocked("polygon"):
            return
        dlg = PrimitivePolygonDialog(
            self,
            coord_system=self.project.mesh.coord_system,
            unit=self.project.mesh.drawing_unit,
            size=self._dialog_size("primitive_polygon"),
        )
        ok = dlg.exec()
        self._remember_dialog_size("primitive_polygon", dlg)
        if ok and dlg.result:
            pr = dlg.result
            self.primitives = [p for p in self.primitives if p.get("name") != pr["name"]]
            self.primitives.append(pr)
            self._store_object_display(pr["name"], dlg.display_values())
            self._log(
                f"Polygon primitive: {pr['name']} axis={pr['axis']} "
                f"coord={pr['coord']} length={pr['length']} "
                f"({len(pr['points'])} points)"
            )
            self._refresh_tree()

    def _polar_ports_blocked(self) -> bool:
        """Cylindrical mode: refuse port dialogs (openEMS LumpedPort is NaN).

        The installed openEMS LumpedPort is not cylindrical-mesh aware: its
        Capacity computation returns NaN on a polar grid, which poisons the
        excitation and the whole field. The cylindrical-aware CoaxialPort
        exists only in openEMS >= 0.37.
        """
        if getattr(self.project.mesh, "coord_system", "cartesian") == "cylindrical":
            self._log(
                "Ports are not supported in cylindrical mode (openEMS LumpedPort "
                "produces NaN fields there). Remove the port or switch Mesh "
                "Parameters -> Coordinate system to cartesian."
            )
            return True
        return False

    def _on_add_port(self) -> None:
        from app.ui.geometry_editors import PortDialog

        if self._polar_ports_blocked():
            return

        from app.models.project import PortDef as _PD

        # New port starts from Settings → port defaults (empty geometry)
        pd = self.settings.port_defaults()
        seed = _PD(
            name="in1",
            type=pd.get("type", "lumped"),  # type: ignore[arg-type]
            resistance=float(pd.get("resistance", 50.0)),
            excite_port=bool(pd.get("excite_port", True)),
            mesh_dirs=str(pd.get("mesh_dirs") or "xyz"),
            direction=str(pd.get("direction") or "z"),
        )
        dlg = PortDialog(self, port=seed, coord_system=self.project.mesh.coord_system)
        if dlg.exec() and dlg.result_port:
            self.project.ports.append(dlg.result_port)
            self._refresh_ports()
            self._log(
                f"Port {dlg.result_port.name}: {dlg.result_port.type} "
                f"{dlg.result_port.start}→{dlg.result_port.stop} R={dlg.result_port.resistance}"
            )

    def _on_mesh_port_mode_changed(self, on: bool) -> None:
        """Port mode toggled in the Mesh Viewer's right-click menu."""
        if on:
            self._log(
                "Port mode ON — left-click in a slice view (XY/XZ/YZ) to place a "
                "port at exactly those coordinates (no snapping); right-click → "
                "'Port mode' turns it off."
            )
        else:
            self._log("Port mode OFF")

    def _on_mesh_port_type_changed(self, kind: str, orientation: str) -> None:
        """Port type / width axis chosen in the Mesh Viewer's menu."""
        axis = str(orientation or "").upper()
        if str(kind) == "msl":
            self._log(
                f"Port type: MSL — width follows the clicked metal along "
                f"{axis or '?'}; the line runs along the other in-plane axis"
            )
        elif axis:
            self._log(
                f"Port type: Lumped, area — as wide as the clicked metal along "
                f"{axis}, zero thickness towards the other in-plane axis"
            )
        else:
            self._log("Port type: Lumped (line from the clicked metal to the next)")

    def _primitive_span(self, name: str, axis: str) -> tuple[float, float] | None:
        """Extent of the project primitive ``name`` along ``axis`` (None if unknown).

        Used by "width = metal" to re-derive a lumped area port's width after the
        mode was changed in the Port dialog.
        """
        letter = str(axis or "").strip().lower()
        if not name or letter not in ("x", "y", "z"):
            return None
        k = "xyz".index(letter)
        prims = self.primitives or (self.project.primitives or [])
        for pr in prims:
            if str(pr.get("name") or "") != str(name):
                continue
            try:
                lo = float(pr["start"][k])
                hi = float(pr["stop"][k])
            except (KeyError, TypeError, ValueError, IndexError):
                return None
            return (lo, hi) if hi >= lo else (hi, lo)
        return None

    def _apply_port_width(self, port, previous: str = "line") -> None:
        """Match a lumped port's box to its width mode (only if the mode changed).

        ``"line"`` collapses the two axes across the excitation direction to the
        middle of the box (a 1-D port); an axis letter widens it to the extent of
        the metal it was placed on and keeps the other transverse axis at the
        middle (a zero-thickness sheet = area port). Boxes the user edited by
        hand are left alone when the mode did not change.
        """
        mode = str(getattr(port, "lumped_width", "line") or "line").strip().lower()
        if mode in ("", "none", "-"):
            mode = "line"
        if str(port.type) != "lumped":
            port.lumped_width = "line"
            return
        direction = str(port.direction or "z").strip().lower()
        if direction not in ("x", "y", "z"):
            direction = "z"
        di = "xyz".index(direction)
        if mode in ("x", "y", "z") and mode == direction:
            self._log(
                f"  Port {port.name}: width axis {mode.upper()} is the excitation "
                "direction — using a line instead"
            )
            mode = "line"
        elif mode not in ("x", "y", "z", "line"):
            self._log(f"  Port {port.name}: unknown width '{mode}' — using a line")
            mode = "line"
        port.lumped_width = mode
        if mode == str(previous or "line").strip().lower():
            return  # unchanged: do not touch a hand-edited box
        if mode == "line":
            for k in range(3):
                if k == di:
                    continue
                mid = 0.5 * (float(port.start[k]) + float(port.stop[k]))
                port.start[k] = port.stop[k] = mid
            return
        wi = "xyz".index(mode)
        span = self._primitive_span(str(getattr(port, "metal", "") or ""), mode)
        if span is None:
            self._log(
                f"  Port {port.name}: metal '{getattr(port, 'metal', '') or '?'}' "
                f"not found — keep the box, widen it along {mode.upper()} by hand"
            )
        else:
            port.start[wi], port.stop[wi] = float(span[0]), float(span[1])
        for k in range(3):
            if k in (di, wi):
                continue
            mid = 0.5 * (float(port.start[k]) + float(port.stop[k]))
            port.start[k] = port.stop[k] = mid

    def _on_mesh_port_place(self, request) -> None:
        """A click in the Mesh Viewer's port mode: configure a port there.

        ``request`` comes from the viewer and already holds the computed box: the
        port runs along the slice normal from the bottom face of the clicked metal
        to the top face of the next metal surface (lumped area/MSL: as wide as
        the clicked metal along the chosen in-plane axis; MSL is also one mesh
        cell long along the line). The dialog opens prefilled and the port is
        appended on OK.
        """
        viewer = self.mesh_viewer
        if self._polar_ports_blocked():  # lumped ports need a cartesian mesh
            viewer.clear_port_marker()
            return

        from app.models.project import PortDef as _PD
        from app.ui.geometry_editors import PortDialog

        req = dict(request or {})
        start = [float(v) for v in (req.get("start") or [0.0, 0.0, 0.0])]
        stop = [float(v) for v in (req.get("stop") or start)]
        kind = str(req.get("type") or "lumped")
        width_axis = str(req.get("width_axis") or "").strip().lower()
        pd = self.settings.port_defaults()
        seed = _PD(
            name=f"in{len(self.project.ports) + 1}",
            type=kind if kind in ("lumped", "msl") else "lumped",  # type: ignore[arg-type]
            start=list(start),
            stop=list(stop),
            resistance=float(pd.get("resistance", 50.0)),
            excite_port=bool(pd.get("excite_port", True)),
            mesh_dirs=str(pd.get("mesh_dirs") or "xyz"),
            direction=str(req.get("direction") or pd.get("direction") or "z"),
            prop_direction=str(req.get("prop_direction") or ""),
            exc_direction=str(req.get("exc_direction") or ""),
            lumped_width=(width_axis if kind == "lumped" else "line") or "line",
            metal=str(req.get("metal") or ""),
        )
        where_extra = ""
        if req.get("metal") or req.get("partner"):
            side = "below" if req.get("side") == "below" else "above"
            where_extra = (
                f" [{req.get('metal') or '?'} → {req.get('partner') or '?'} ({side})]"
            )
        if kind == "msl" or (kind == "lumped" and width_axis):
            where_extra += f" width along {width_axis.upper()}"
        self._log(
            f"Placed {kind.upper()} port candidate at "
            f"x={start[0]:.4g}, y={start[1]:.4g}, z={start[2]:.4g}"
            f"{where_extra}"
        )
        if req.get("warning"):
            self._log(f"  {req['warning']}")
        dlg = PortDialog(self, port=seed, coord_system=self.project.mesh.coord_system)
        try:
            while dlg.exec():
                port = dlg.result_port
                if port is None or port.start != port.stop:
                    break
                QMessageBox.warning(
                    self,
                    "Port",
                    "Start and Stop are identical - the port has no length.\n"
                    "Give it a gap (e.g. change one end) so openEMS can excite it.",
                )
        finally:
            viewer.clear_port_marker()
        port = dlg.result_port
        if port is None or port.start == port.stop:
            return  # cancelled / still degenerate
        self._apply_port_width(port, previous=seed.lumped_width)
        self.project.ports.append(port)
        self._refresh_ports()
        self.mesh_viewer.set_ports(self.project.ports)
        self._log(
            f"Port {port.name}: {port.type}{self._port_width_text(port)} "
            f"{port.start}→{port.stop} R={port.resistance}"
            + ("  [reversed: Start/Stop swapped]" if getattr(port, "reverse", False) else "")
        )

    @staticmethod
    def _port_width_text(port, short: bool = False) -> str:
        """``", area along Y"`` for a lumped area port, ``", line"`` for a line.

        ``short`` drops the "line" marker (compact port list).
        """
        if str(getattr(port, "type", "")) != "lumped":
            return ""
        mode = str(getattr(port, "lumped_width", "line") or "line").lower()
        if mode in ("x", "y", "z"):
            return f", area along {mode.upper()}"
        return "" if short else ", line"

    def _selected_port_index(self) -> int:
        row = self.port_list.currentRow()
        if row < 0 or row >= len(self.project.ports):
            return -1
        return row

    def _on_edit_port(self, _item=None) -> None:
        from app.ui.geometry_editors import PortDialog

        if self._polar_ports_blocked():
            return

        idx = self._selected_port_index()
        if idx < 0:
            self._log("Select a port to edit (double-click a row)")
            return
        old = self.project.ports[idx]
        old_mode = str(getattr(old, "lumped_width", "line") or "line").lower()
        dlg = PortDialog(self, port=old, coord_system=self.project.mesh.coord_system)
        if dlg.exec() and dlg.result_port:
            new_port = dlg.result_port
            self._apply_port_width(new_port, previous=old_mode)
            self.project.ports[idx] = new_port
            self._refresh_ports()
            self.mesh_viewer.set_ports(self.project.ports)
            if idx < self.port_list.count():
                self.port_list.setCurrentRow(idx)
            self._log(
                f"Port updated: {new_port.name} {new_port.type}"
                f"{self._port_width_text(new_port)} "
                f"{new_port.start}→{new_port.stop} "
                f"R={new_port.resistance} dir={new_port.direction} "
                f"excite={new_port.excite_port} reverse={new_port.reverse}"
            )

    def _on_remove_port(self) -> None:
        idx = self._selected_port_index()
        if idx < 0:
            self._log("Select a port to remove")
            return
        p = self.project.ports.pop(idx)
        self._refresh_ports()
        self._log(f"Port removed: {p.name}")

    def _default_dump_box(self):
        """A non-degenerate default field-dump box: geometry bbox + ~10 % margin.

        A dump defaulting to [0,0,0]→[0,0,0] is a single point and produces an
        empty result — so seed the dialog with the structure's extent instead.
        """
        import math

        from app.meshing.bounds import cartesian_primitive_bbox

        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for pr in self.primitives or []:
            bb = cartesian_primitive_bbox(pr)
            if bb is None:
                continue
            for i in range(3):
                lo[i] = min(lo[i], float(bb[0][i]))
                hi[i] = max(hi[i], float(bb[1][i]))
        if not all(math.isfinite(v) for v in lo):
            return None
        span = max(hi[i] - lo[i] for i in range(3)) or 1.0
        pad = 0.1 * span
        return [lo[i] - pad for i in range(3)], [hi[i] + pad for i in range(3)]

    def _dump_fit_boxes(self):
        """(geometry bbox, NF2FF box) as ((lo,hi)|None, (lo,hi)|None) for the dump dialog."""
        fit = self._default_dump_box()
        nf = self.project.nf2ff
        nf_box = None
        try:
            lo = [float(v) for v in nf.start]
            hi = [float(v) for v in nf.stop]
            if any(abs(hi[i] - lo[i]) > 1e-9 for i in range(3)):
                nf_box = (lo, hi)
        except Exception:  # noqa: BLE001
            nf_box = None
        return fit, nf_box

    def _on_add_dump(self) -> None:
        from app.models.project import DumpDef
        from app.ui.geometry_editors import DumpDialog

        fit, nf_box = self._dump_fit_boxes()
        seed = None
        if fit is not None:
            f0 = float(getattr(self.project.mesh, "target_frequency", 0) or 2.45e9)
            seed = DumpDef(
                name="Et", quantity="E", start=fit[0], stop=fit[1], frequency=f0
            )
        dlg = DumpDialog(
            self,
            dump=seed,
            coord_system=self.project.mesh.coord_system,
            fit_box=fit,
            nf2ff_box=nf_box,
        )
        if dlg.exec() and dlg.result:
            d = dlg.result
            self.project.dumps = [x for x in self.project.dumps if x.name != d.name]
            self.project.dumps.append(d)
            self._log(f"Field dump: {d.name} ({d.quantity}) {d.start}→{d.stop}")
            self._refresh_tree()

    def _update_viewer_nf2ff(self) -> None:
        """Push a changed NF2FF box to the Mesh Viewer so the frame updates now."""
        try:
            if getattr(self.mesh_viewer, "_csx", None) is None:
                self._log("(Mesh Viewer: build mesh first — the NF2FF frame is drawn from the built mesh)")
                return
            self.mesh_viewer.set_nf2ff(self.project.nf2ff)
        except Exception as e:  # noqa: BLE001 — viewer must not block editing
            print(f"viewer NF2FF refresh failed: {e}")

    def _on_edit_nf2ff(self) -> None:
        from app.ui.geometry_editors import NF2FFBoxDialog

        if self.project.mesh.is_cylindrical:
            self._log(
                "NF2FF far-field is not supported in cylindrical mode — box editing disabled "
                "(S-params and field dumps still work)"
            )
            return
        n = self.project.nf2ff
        dlg = NF2FFBoxDialog(self, start=n.start, stop=n.stop, center=n.center)
        if dlg.exec() and dlg.result_start and dlg.result_stop:
            n.start = dlg.result_start
            n.stop = dlg.result_stop
            n.center = [
                0.5 * (dlg.result_start[i] + dlg.result_stop[i]) for i in range(3)
            ]
            n.enabled = True
            self.project.sim.nf2ff_enabled = True
            self.chk_nf2ff.setChecked(True)
            self._log(f"NF2FF box: {n.start} → {n.stop}")
            self._refresh_tree()
            self._update_viewer_nf2ff()

    def _on_fit_nf2ff(self) -> None:
        if self.project.mesh.is_cylindrical:
            self._log(
                "Fit NF2FF: far-field is not supported in cylindrical mode — skipped"
            )
            return
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        from app.meshing.bounds import cartesian_primitive_bbox

        for pr in self.primitives:
            bb = cartesian_primitive_bbox(pr)
            if bb is None:
                continue
            lo, hi = bb
            xs += [lo[0], hi[0]]
            ys += [lo[1], hi[1]]
            zs += [lo[2], hi[2]]
        for gf in self.project.geometry_files:
            # bounds not always stored on GeometryFile; skip
            del gf
        if not xs:
            self._log("Fit NF2FF: no primitives/geometry bounds found")
            return
        from app.meshing.bounds import nf2ff_fit_box, nf2ff_fit_margin_units
        from app.meshing.rules import compute_targets

        t = compute_targets(self.project.mesh)
        margin = nf2ff_fit_margin_units(
            self.project.mesh, self.project.sim, self.project.boundary, t
        )
        start, stop = nf2ff_fit_box(
            [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)], margin
        )
        n = self.project.nf2ff
        n.start = [float(v) for v in start]
        n.stop = [float(v) for v in stop]
        n.center = [0.5 * (start[i] + stop[i]) for i in range(3)]
        n.enabled = True
        self.project.sim.nf2ff_enabled = True
        self.chk_nf2ff.setChecked(True)
        self._log(f"NF2FF fitted to geometry + air margin≈{margin:.3g}: {n.start} → {n.stop}")
        self._refresh_tree()
        self._update_viewer_nf2ff()

    def _tree_item(self, text: str, kind: str, index: int) -> QListWidgetItem:
        it = QListWidgetItem(text)
        it.setData(Qt.UserRole, kind)
        it.setData(Qt.UserRole + 1, index)
        it.setToolTip(
            "Double-click to edit" if kind != "geom" else "Imported geometry (read-only) — edit in FreeCAD"
        )
        return it

    def _refresh_tree(self) -> None:
        # a rebuild re-selects the previous row: that must not move the slice
        # plane (only a real click in the list focuses the object)
        self._tree_rebuilding = True
        prev: tuple[str, int] = ("", -1)
        cur = self.tree.currentItem()
        if cur is not None:
            k_raw = cur.data(Qt.UserRole)
            i_raw = cur.data(Qt.UserRole + 1)
            prev = (
                "" if k_raw is None else str(k_raw),
                -1 if i_raw is None else int(i_raw),
            )
        self.tree.clear()
        restore_row = -1

        def _add(text: str, kind: str, index: int) -> None:
            nonlocal restore_row
            self.tree.addItem(self._tree_item(text, kind, index))
            if kind == prev[0] and index == prev[1]:
                restore_row = self.tree.count() - 1

        for i, gf in enumerate(self.project.geometry_files):
            _add(f"Geom: {Path(gf.path).name} [{gf.format}]", "geom", i)
        for i, pr in enumerate(self.primitives):
            kind = pr.get("kind", "box")
            _add(
                f"Prim: {pr.get('name')} ({kind}/{pr.get('material', '?')})",
                "prim",
                i,
            )
        for i, a in enumerate(self.project.assignments):
            _add(
                f"Assign: {a.object_name} -> {a.role}/{a.material}",
                "assign",
                i,
            )
        for i, m in enumerate(self.project.materials):
            _add(f"Mat: {m.name} ({m.kind})", "mat", i)
        for i, p in enumerate(self.project.ports):
            off = "" if bool(getattr(p, "active", True)) else " [off]"
            rev = " [rev]" if bool(getattr(p, "reverse", False)) else ""
            _add(
                f"Port: {p.name} {p.type}{self._port_width_text(p, short=True)} "
                f"{p.start}→{p.stop}{rev}{off}",
                "port",
                i,
            )
        for i, d in enumerate(self.project.dumps):
            _add(f"Dump: {d.name} ({d.quantity})", "dump", i)
        n = self.project.nf2ff
        _add(
            f"NF2FF: {'on' if n.enabled else 'off'} {n.start}→{n.stop}",
            "nf2ff",
            0,
        )
        if restore_row >= 0:
            self.tree.setCurrentRow(restore_row)
        self._tree_rebuilding = False
        # geometry edits (add / edit / remove / import) become visible in the
        # Mesh Viewer right away — the list refresh is the common end point of
        # all those actions
        self._maybe_refresh_viewer_geometry()

    def _tree_key_press(self, event) -> None:
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._on_delete_selected_tree()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_edit_selected_tree()
            event.accept()
            return
        QListWidget.keyPressEvent(self.tree, event)

    def _current_tree_entry(self) -> tuple[str, int]:
        item = self.tree.currentItem()
        if item is None:
            return "", -1
        kind_raw = item.data(Qt.UserRole)
        idx_raw = item.data(Qt.UserRole + 1)
        kind = "" if kind_raw is None else str(kind_raw)
        idx = -1 if idx_raw is None else int(idx_raw)
        return kind, idx

    def _on_edit_selected_tree(self) -> None:
        kind, idx = self._current_tree_entry()
        if not kind:
            self._log("Select a tree entry to edit (double-click also works)")
            return
        self._edit_tree_entry(kind, idx)

    def _on_delete_selected_tree(self) -> None:
        kind, idx = self._current_tree_entry()
        if not kind:
            self._log("Select a tree entry to remove (Delete key or Geometry menu)")
            return
        if kind == "geom":
            gf = (
                self.project.geometry_files[idx]
                if 0 <= idx < len(self.project.geometry_files)
                else None
            )
            name = Path(gf.path).name if gf else "?"
            self.project.geometry_files.pop(idx)
            self._log(f"Geometry file removed from project: {name}")
            self._refresh_tree()
            return
        if kind == "nf2ff":
            n = self.project.nf2ff
            n.enabled = False
            n.start = [-20, -20, -20]
            n.stop = [20, 20, 20]
            n.center = [0, 0, 0]
            self.project.sim.nf2ff_enabled = False
            self.chk_nf2ff.setChecked(False)
            self._log("NF2FF disabled and reset")
            self._refresh_tree()
            return
        self._remove_tree_entry(kind, idx)

    def _on_duplicate_selected_tree(self) -> None:
        """Insert a deep copy of the selected tree entry right after it.

        Copies get a unique ``… (copy)`` name (prim/mat/port/dump); the copy
        is selected afterwards, so repeated duplication chains naturally.
        """
        kind, idx = self._current_tree_entry()
        if not kind:
            self._log("Select a tree entry to duplicate (right-click → Duplicate)")
            return
        if kind not in DUP_TREE_KINDS:
            self._log(f"'{kind}' entries cannot be duplicated")
            return
        import copy as _copy
        import re as _re

        def _uniq(base: object, taken) -> str:
            base = _re.sub(r" \(copy( \d+)?\)$", "", str(base or "item"))
            names = {str(t) for t in taken if t}
            cand = f"{base} (copy)"
            n = 2
            while cand in names:
                cand = f"{base} (copy {n})"
                n += 1
            return cand

        if kind == "prim":
            if not (0 <= idx < len(self.primitives)):
                return
            pr = _copy.deepcopy(self.primitives[idx])
            pr["name"] = _uniq(pr.get("name"), [p.get("name") for p in self.primitives])
            self.primitives.insert(idx + 1, pr)
            label = f"Primitive duplicated: {pr['name']}"
            self._refresh_tree()
        elif kind == "assign":
            if not (0 <= idx < len(self.project.assignments)):
                return
            a = _copy.deepcopy(self.project.assignments[idx])
            self.project.assignments.insert(idx + 1, a)
            self._sync_assignment_materials_to_primitives()
            label = f"Assignment duplicated: {a.object_name} -> {a.role}/{a.material}"
            self._refresh_tree()
        elif kind == "mat":
            if not (0 <= idx < len(self.project.materials)):
                return
            m = _copy.deepcopy(self.project.materials[idx])
            m.name = _uniq(m.name, [x.name for x in self.project.materials])
            self.project.materials.insert(idx + 1, m)
            label = f"Material duplicated: {m.name}"
            self._refresh_materials()  # also refreshes the tree
        elif kind == "port":
            if not (0 <= idx < len(self.project.ports)):
                return
            p = _copy.deepcopy(self.project.ports[idx])
            p.name = _uniq(p.name, [x.name for x in self.project.ports])
            self.project.ports.insert(idx + 1, p)
            label = f"Port duplicated: {p.name}"
            self._refresh_ports()  # also refreshes the tree
        elif kind == "dump":
            if not (0 <= idx < len(self.project.dumps)):
                return
            d = _copy.deepcopy(self.project.dumps[idx])
            d.name = _uniq(d.name, [x.name for x in self.project.dumps])
            self.project.dumps.insert(idx + 1, d)
            label = f"Field dump duplicated: {d.name}"
            self._refresh_tree()
        else:  # pragma: no cover - guarded by DUP_TREE_KINDS
            return
        self._select_tree_entry(kind, idx + 1)
        self._log(label)

    def _select_tree_entry(self, kind: str, idx: int) -> None:
        """Move the current-row cursor to the (kind, idx) tree entry."""
        for i in range(self.tree.count()):
            it = self.tree.item(i)
            if it.data(Qt.UserRole) == kind and it.data(Qt.UserRole + 1) == idx:
                self.tree.setCurrentRow(i)
                return

    def _on_tree_double_clicked(self, item: QListWidgetItem) -> None:
        kind_raw = item.data(Qt.UserRole)
        idx_raw = item.data(Qt.UserRole + 1)
        kind = "" if kind_raw is None else str(kind_raw)
        idx = 0 if idx_raw is None else int(idx_raw)
        self._edit_tree_entry(kind, idx)

    def _on_tree_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu

        item = self.tree.itemAt(pos)
        if item is None:
            # empty space below the tree: panel options (hide the pane)
            self._build_project_panel_menu().exec(self.tree.viewport().mapToGlobal(pos))
            return
        kind_raw = item.data(Qt.UserRole)
        idx_raw = item.data(Qt.UserRole + 1)
        kind = "" if kind_raw is None else str(kind_raw)
        idx = 0 if idx_raw is None else int(idx_raw)
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        act_edit = menu.addAction("Edit…")
        act_edit.setEnabled(kind != "geom")
        act_dup = menu.addAction("Duplicate")
        act_dup.setEnabled(kind in DUP_TREE_KINDS)
        act_del = menu.addAction("Remove")
        act_del.setEnabled(kind in {"geom", "prim", "assign", "mat", "port", "dump", "nf2ff"})
        menu.addSeparator()
        self._add_project_panel_actions(menu)
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_edit:
            self._edit_tree_entry(kind, idx)
        elif chosen is act_dup:
            self._on_duplicate_selected_tree()
        elif chosen is act_del:
            self._on_delete_selected_tree()

    def _edit_tree_entry(self, kind: str, idx: int) -> None:
        if kind == "geom":
            gf = self.project.geometry_files[idx] if 0 <= idx < len(self.project.geometry_files) else None
            if gf:
                self._log(
                    f"Geometry file: {gf.path} [{gf.format}] — "
                    "edit in FreeCAD, then re-import (FreeCAD... / Import Geometry...)"
                )
            return
        if kind == "prim":
            if not (0 <= idx < len(self.primitives)):
                return
            pr = self.primitives[idx]
            ptype = str(pr.get("kind", "box"))
            if ptype in {"box", "cylinder", "sphere", "polygon"}:
                if ptype in {"sphere", "polygon"} and self._polar_prim_blocked(ptype):
                    return
                from app.ui.geometry_editors import (
                    PrimitiveBoxDialog,
                    PrimitiveCylinderDialog,
                    PrimitivePolygonDialog,
                    PrimitiveSphereDialog,
                )

                dialog_cls = {
                    "box": PrimitiveBoxDialog,
                    "cylinder": PrimitiveCylinderDialog,
                    "sphere": PrimitiveSphereDialog,
                    "polygon": PrimitivePolygonDialog,
                }[ptype]
                kwargs: dict[str, object] = {
                    "prim": pr,
                    "coord_system": self.project.mesh.coord_system,
                    "display": self._object_display(str(pr.get("name") or "")),
                }
                if ptype == "polygon":
                    # only the polygon dialog has a live preview that labels units
                    kwargs["unit"] = self.project.mesh.drawing_unit
                    kwargs["size"] = self._dialog_size("primitive_polygon")
                dlg = dialog_cls(self, **kwargs)
                ok = dlg.exec()
                if ptype == "polygon":
                    self._remember_dialog_size("primitive_polygon", dlg)
                if ok and dlg.result:
                    old_name = pr.get("name")
                    self.primitives[idx] = dlg.result
                    new_name = dlg.result.get("name")
                    if old_name and new_name and old_name != new_name:
                        self._rename_primitive(old_name, new_name)
                    self._store_object_display(new_name, dlg.display_values())
                    self._log(
                        f"Primitive updated: {new_name} {dlg.result.get('material')} "
                        f"{dlg.result.get('start')}→{dlg.result.get('stop')}"
                    )
                    self._refresh_tree()
                return
            # stl / imported mesh — material + priority only
            self._edit_stl_primitive(idx)
            return
        if kind == "assign":
            if not (0 <= idx < len(self.project.assignments)):
                return
            from app.ui.geometry_editors import AssignmentDialog

            a = self.project.assignments[idx]
            dlg = AssignmentDialog(
                self._object_names_for_dialog(),
                self._material_names(),
                self,
                assignment=a,
                display_for=self._object_display,
            )
            if dlg.exec() and dlg.result:
                old_obj = a.object_name
                new = dlg.result
                # keep stable identity: don't allow silent second row for same object
                self.project.assignments = [
                    x
                    for x in self.project.assignments
                    if x is not a and x.object_name != new.object_name
                ]
                self.project.assignments.append(new)
                if old_obj != new.object_name:
                    for pr in self.primitives:
                        if pr.get("name") == old_obj:
                            pr["name"] = new.object_name
                self._store_object_display(new.object_name, dlg.display_values())
                self._sync_assignment_materials_to_primitives()
                self._log(
                    f"Assignment updated: {new.object_name} -> {new.role}/{new.material}"
                )
                self._refresh_tree()
            return
        if kind == "mat":
            if not (0 <= idx < len(self.project.materials)):
                return
            from app.ui.geometry_editors import MaterialDialog

            m = self.project.materials[idx]
            dlg = MaterialDialog(self, material=m)
            if dlg.exec() and dlg.result:
                old = m.name
                new = dlg.result
                self.project.materials = [
                    x for x in self.project.materials if x is not m and x.name != new.name
                ]
                self.project.materials.append(new)
                if old != new.name:
                    for a in self.project.assignments:
                        if a.material == old:
                            a.material = new.name
                    for pr in self.primitives:
                        if pr.get("material") == old:
                            pr["material"] = new.name
                self._refresh_materials()
                self._log(f"Material updated: {new.name} ({new.kind}, εr={new.epsilon})")
                self._refresh_tree()
            return
        if kind == "port":
            if not (0 <= idx < len(self.project.ports)):
                return
            if self._polar_ports_blocked():
                return
            from app.ui.geometry_editors import PortDialog

            p = self.project.ports[idx]
            dlg = PortDialog(self, port=p, coord_system=self.project.mesh.coord_system)
            if dlg.exec() and dlg.result_port:
                self.project.ports[idx] = dlg.result_port
                self._refresh_ports()
                self._log(
                    f"Port updated: {dlg.result_port.name} {dlg.result_port.type} "
                    f"{dlg.result_port.start}→{dlg.result_port.stop} "
                    f"R={dlg.result_port.resistance}"
                )
                self._refresh_tree()
            return
        if kind == "dump":
            if not (0 <= idx < len(self.project.dumps)):
                return
            from app.ui.geometry_editors import DumpDialog

            d = self.project.dumps[idx]
            fit, nf_box = self._dump_fit_boxes()
            dlg = DumpDialog(
                self,
                dump=d,
                coord_system=self.project.mesh.coord_system,
                fit_box=fit,
                nf2ff_box=nf_box,
            )
            if dlg.exec() and dlg.result:
                self.project.dumps[idx] = dlg.result
                self._log(f"Field dump updated: {dlg.result.name} ({dlg.result.quantity})")
                self._refresh_tree()
            return
        if kind == "nf2ff":
            self._on_edit_nf2ff()
            self._refresh_tree()

    def _edit_stl_primitive(self, idx: int) -> None:
        from PySide6.QtWidgets import (
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QSpinBox,
        )

        pr = self.primitives[idx]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Mesh: {pr.get('name', '')}")
        form = QFormLayout(dlg)
        ed_name = QLineEdit(str(pr.get("name", "")))
        ed_name.setReadOnly(True)
        cmb_mat = QComboBox()
        cmb_mat.setEditable(True)
        names = self._material_names() or ["copper", "substrate", "vacuum"]
        cmb_mat.addItems(names)
        cmb_mat.setCurrentText(str(pr.get("material", "copper")))
        spn_pri = QSpinBox()
        spn_pri.setRange(0, 200)
        spn_pri.setValue(int(pr.get("priority", 10)))
        form.addRow("Name (imported)", ed_name)
        form.addRow("Material", cmb_mat)
        form.addRow("Priority", spn_pri)
        display = DisplayOptionsWidget(
            self._object_display(str(pr.get("name", ""))), dlg
        )
        form.addRow(display)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        if not dlg.exec():
            return
        pr["material"] = cmb_mat.currentText().strip() or "copper"
        pr["priority"] = spn_pri.value()
        # keep assignment in sync for this object
        name = pr.get("name")
        self._store_object_display(name, display.values())
        for a in self.project.assignments:
            if a.object_name == name and a.role != "ignore":
                a.material = pr["material"]
                a.priority = pr["priority"]
        self._sync_assignment_materials_to_primitives()
        self._log(f"Mesh material updated: {name} -> {pr['material']} pri={pr['priority']}")
        self._refresh_tree()

    def _rename_primitive(self, old: str, new: str) -> None:
        if not old or not new or old == new:
            return
        for a in self.project.assignments:
            if a.object_name == old:
                a.object_name = new
        for gf in self.project.geometry_files:
            if old in (gf.object_names or []):
                gf.object_names = [new if n == old else n for n in gf.object_names]
        self._rename_object_display(old, new)

    def _remove_tree_entry(self, kind: str, idx: int) -> None:
        if kind == "prim":
            if 0 <= idx < len(self.primitives):
                pr = self.primitives.pop(idx)
                self._log(f"Primitive removed: {pr.get('name')}")
        elif kind == "assign":
            if 0 <= idx < len(self.project.assignments):
                a = self.project.assignments.pop(idx)
                self._log(f"Assignment removed: {a.object_name}")
                self._sync_assignment_materials_to_primitives()
        elif kind == "mat":
            if 0 <= idx < len(self.project.materials):
                m = self.project.materials.pop(idx)
                self._log(f"Material removed: {m.name}")
                self._refresh_materials()
        elif kind == "port":
            if 0 <= idx < len(self.project.ports):
                p = self.project.ports.pop(idx)
                self._log(f"Port removed: {p.name}")
                self._refresh_ports()
        elif kind == "dump":
            if 0 <= idx < len(self.project.dumps):
                d = self.project.dumps.pop(idx)
                self._log(f"Field dump removed: {d.name}")
        else:
            return
        self._refresh_tree()

    def _on_build_mesh(self) -> None:
        if getattr(self, "_mesh_building", False) or (
            getattr(self, "_mesh_thread", None) is not None
            and self._mesh_thread.isRunning()
        ):
            self._log("Mesh build already running — ignored.")
            return
        if not getattr(self, "_auto_mesh_build", False):
            self._ensure_log_visible("mesh build")
        self._sync_project_from_form()
        try:
            self._ensure_patch_primitives()
        except Exception as e:  # noqa: BLE001
            self._set_mesh_report(f"ERROR: {e}")
            self._log(f"Mesh build failed: {e}")
            return
        self._log_computed_summary()
        self._mesh_building = True
        self.btn_build_mesh.setEnabled(False)
        self._log("Building mesh in the background (easyMesh + validators)…")
        from app.ui.mesh_worker import MeshBuildThread

        thread = MeshBuildThread(
            self.project, list(self.primitives), self._project_work_dir(), self
        )
        thread.log.connect(self._log)
        thread.done.connect(self._on_mesh_built)
        thread.failed.connect(self._on_mesh_build_failed)
        self._mesh_thread = thread
        thread.start()

    def _on_mesh_built(self, result) -> None:
        """GUI-thread slot: finish a background mesh build."""
        try:
            self._finish_mesh_build(result)
        except Exception as e:  # noqa: BLE001
            self._set_mesh_report(f"ERROR: {e}")
            self._log(f"Mesh build failed: {e}\n{traceback.format_exc()}")
        finally:
            self._mesh_building = False
            self.btn_build_mesh.setEnabled(True)
            self._mesh_thread = None

    def _on_mesh_build_failed(self, message: str) -> None:
        """GUI-thread slot: the background mesh build raised."""
        self._set_mesh_report(f"ERROR: {message}")
        self._log(f"Mesh build failed: {message}")
        self._mesh_building = False
        self.btn_build_mesh.setEnabled(True)
        self._mesh_thread = None

    def _ensure_patch_primitives(self) -> None:
        """Patch-preset fallback: derive primitives from patch_params if empty."""
        if not self.primitives and self.project.patch_params:
            self.project, self.primitives = patch_antenna_primitives(
                **{k: float(v) for k, v in self.project.patch_params.items()}
            )
            self.sim_params.patch_params = dict(self.project.patch_params)
            self._sync_form_from_project()

    def _build_and_validate(self) -> str:
        """Synchronous mesh build (Mesh-Viewer auto-build + tests).

        The Build action (:meth:`_on_build_mesh`) runs the same core on a worker
        thread; this blocking path stays for ``_ensure_mesh_viewer_content`` and
        for tests that stub it.
        """
        from app.meshing.build_task import build_mesh_and_validate

        self._ensure_patch_primitives()
        result = build_mesh_and_validate(
            self.project, self.primitives, self._project_work_dir(), log=self._log
        )
        return self._finish_mesh_build(result)

    def _finish_mesh_build(self, result) -> str:
        """GUI-thread finish: preview PNG, report, viewer swap, result record.

        Must run on the GUI thread — matplotlib preview, the Mesh Viewer CSX
        swap and the retired-object bookkeeping are not worker-safe.
        """
        auto = bool(getattr(self, "_auto_mesh_build", False))
        self._auto_mesh_build = False
        self._mesh_deleted = False  # a fresh build re-enables the auto-show
        lines = list(result.lines)

        # Backend-safe PNG export (does not switch matplotlib backend).
        try:
            from app.meshing.preview import save_mesh_preview

            preview = save_mesh_preview(
                result.csx, self.settings.work_path() / "mesh_preview.png", "Mesh cells"
            )
            lines.append(f"Preview: {preview}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"(preview export failed: {e})")

        CSX = result.csx
        FDTD = result.fdtd

        # Keep previous CSX/FDTD alive until the viewer has switched to the new
        # ones. Freeing FDTD while the viewer still holds its CSX causes an
        # access violation on the 2nd Build Mesh click (CSXCAD parent freed).
        old_csx = getattr(self, "_last_csx", None)
        old_fdtd = getattr(self, "_last_fdtd", None)
        self._last_csx = CSX
        self._last_fdtd = FDTD

        # Feed interactive mesh/geometry viewer (new CSX only)
        try:
            self.mesh_viewer.set_project_context(
                project=self.project,
                primitives=self.primitives,
                ports=self.project.ports,
                nf2ff=self.project.nf2ff,
            )
            self.mesh_viewer.set_auto_grid(result.auto_mesh_lines)
            # keep the view type, slice plane and zoom across a rebuild (the
            # user's settings must survive leaving/entering the tab or a build)
            self.mesh_viewer.set_csx(CSX, keep_view=True)
            self._mesh_sig = self._mesh_signature()
            self._viewer_geometry_sig = self._mesh_sig
            self._sync_scene_info()
            if not auto:
                # Switch to the viewer tab so the user sees the new mesh
                idx = getattr(self, "mesh_viewer_tab_index", None)
                if idx is not None and idx >= 0:
                    self.tabs.setCurrentIndex(idx)
        except Exception as e:  # noqa: BLE001 — viewer must not block mesh build
            lines.append(f"(viewer update failed: {e})")

        # Delay old-object destruction: park them until next build / shutdown.
        # Immediate del/GC of FDTD while Qt is painting can hard-crash.
        if not hasattr(self, "_retired_csx"):
            self._retired_csx = []
        if not hasattr(self, "_retired_fdtd"):
            self._retired_fdtd = []
        if old_csx is not None:
            self._retired_csx.append(old_csx)
        if old_fdtd is not None:
            self._retired_fdtd.append(old_fdtd)
        # Bound memory: keep only the last few retired objects
        if len(self._retired_csx) > 4:
            self._retired_csx = self._retired_csx[-4:]
        if len(self._retired_fdtd) > 4:
            self._retired_fdtd = self._retired_fdtd[-4:]

        text = "\n".join(lines)
        self._set_mesh_report(text)
        self._last_mesh_timing = getattr(result, "timing", None)
        self._last_coord_diag = getattr(result, "coord_diag", None)
        if self._last_mesh_timing is not None:
            from app.meshing.timing import summary_line

            self._log(summary_line(self._last_mesh_timing))
        if self._last_coord_diag is not None:
            from app.meshing.coord_diag import summary_line as coord_summary_line

            self._log(coord_summary_line(self._last_coord_diag))
        # Hand the flagged cells to the Mesh Viewer (right-click toggles them).
        try:
            crit = getattr(result, "critical_cells", None)
            self.mesh_viewer.set_critical_cells(crit)
            if crit:
                from app.meshing.critical_cells import summary_line as crit_summary

                self._log(crit_summary(crit) + " - marked in the Mesh Viewer")
        except Exception as e:  # noqa: BLE001 — a mark must not fail the build
            self._log(f"Mesh Viewer critical cells failed: {e}")
        self._record_results(save=False)
        return text

    def _prepare_run_state(self, work: Path) -> None:
        """Work dir + patch-preset fallback — shared by Run Simulation and Script…."""
        work.mkdir(parents=True, exist_ok=True)
        if not self.primitives and self.project.patch_params:
            from app.ui.sim_params import DEFAULT_PATCH_PARAMS

            pp = {**DEFAULT_PATCH_PARAMS, **(self.project.patch_params or {})}
            kwargs = {k: float(pp[k]) for k in DEFAULT_PATCH_PARAMS}
            self.project, self.primitives = patch_antenna_primitives(**kwargs)
            self.sim_params.patch_params = dict(pp)
            self._sync_form_from_project()
        if not self.primitives:
            self._log(
                "WARNING: no primitives/geometry — Import STEP/STL/OBJ from FreeCAD, "
                "or add boxes/cylinders first"
            )

    def _generate_script(self, work: Path) -> Path:
        """Save the project and (re)write work/project_run.py.

        Hand-edits of the previous script are detected via the pristine
        project_run.py.lastgen copy and backed up to project_run_edited.py.
        """
        project_path = self.project.save(work / "project.fcsim.json")
        previous = _read_text(work / "project_run.py")
        script = generate_run_script(self.project, project_path, work, primitives=self.primitives)
        self._note_script_backup(work, previous)
        return script

    def _note_script_backup(self, work: Path, previous: str | None) -> None:
        current = _read_text(work / "project_run.py")
        if current is None:
            return
        lastgen_path = work / "project_run.py.lastgen"
        lastgen = _read_text(lastgen_path)
        # lastgen is the pristine output of the previous generation: a difference
        # between the script we just overwrote and it means the user edited by hand.
        edited = previous is not None and lastgen is not None and previous != lastgen
        try:
            lastgen_path.write_text(current, encoding="utf-8", newline="\n")
        except OSError:
            return
        if not edited:
            return
        backup = work / "project_run_edited.py"
        try:
            backup.write_text(previous, encoding="utf-8", newline="\n")
        except OSError:
            return
        self._log(f"NOTE: your edited script was backed up to {backup} (Run regenerates it)")

    def _ensure_script_baseline(self, work: Path, script_path: Path) -> None:
        """Record the current script as pristine so later hand-edits are detected."""
        lastgen = work / "project_run.py.lastgen"
        if _read_text(lastgen) is not None:
            return
        text = _read_text(script_path)
        if text is None:
            return
        try:
            lastgen.write_text(text, encoding="utf-8", newline="\n")
        except OSError:
            pass

    def _on_edit_script(self) -> None:
        """View / edit work/<project>/project_run.py in a modeless editor."""
        from app.ui.script_editor import ScriptEditorDialog

        self._sync_project_from_form()
        work = self._project_work_dir()
        self._prepare_run_state(work)
        script_path = work / "project_run.py"
        error: str | None = None
        if not script_path.exists():
            try:
                script_path = self._generate_script(work)
            except Exception as e:  # noqa: BLE001 — editor must still open
                error = str(e)
                self._log(f"Script generation failed: {e}")
        self._ensure_script_baseline(work, script_path)

        def _regenerate() -> Path:
            # resolve the work dir at call time (project may have been renamed)
            return self._generate_script(
                self._project_work_dir()
            )

        dlg = self._script_dialog
        if dlg is None or not dlg.isVisible():
            dlg = ScriptEditorDialog(script_path, regenerate_cb=_regenerate, parent=self)
            self._script_dialog = dlg
            dlg.show()
        else:
            dlg.show_path(script_path)
        if error:
            dlg.lbl_status.setText(f"Generation failed: {error}")
        self._log(f"Script editor: {script_path}")

    def _script_has_edits(self, work: Path) -> bool:
        current = _read_text(work / "project_run.py")
        lastgen = _read_text(work / "project_run.py.lastgen")
        return current is not None and lastgen is not None and current != lastgen

    def _run_script_for(self, work: Path) -> Path:
        """Regenerate project_run.py, or keep hand-edits when the user wants that."""
        script_path = work / "project_run.py"
        if self._script_has_edits(work):
            answer = QMessageBox.question(
                self,
                "Edited script",
                "project_run.py was edited by hand.\n\n"
                "Yes — regenerate it from the project (your edits are backed up to "
                "project_run_edited.py; run them manually with the simulation Python).\n"
                "No — run your edited script as it is.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.No:
                self._log(f"Running your edited script as-is: {script_path}")
                return script_path
        script = self._generate_script(work)
        self._log(f"Generated script: {script}")
        return script

    def _on_run(self) -> None:
        self._ensure_log_visible("simulation")
        self._sync_project_from_form()
        if self.runner.is_running() or self._multi_plans is not None:
            QMessageBox.warning(self, "Busy", "A simulation is already running.")
            return
        self._begin_run_log("Run Simulation")
        self._run_step("Preparing run (project, work dir, geometry)")
        work = self._project_work_dir()
        self._prepare_run_state(work)
        f0 = self.project.mesh.target_frequency or 2.45e9
        fc = max(1e6, 0.5 * abs(self.project.mesh.stop_frequency - self.project.mesh.start_frequency))
        # User-controlled max_timesteps — warn below recommended floor, do not silently override.
        min_ts = 150_000
        user_ts = int(self.project.sim.max_timesteps or 0)
        if user_ts < min_ts:
            self._log(
                f"WARNING: max_timesteps={user_ts} < recommended {min_ts} "
                f"(f0={f0/1e9:.3f} GHz, fc={fc/1e9:.3f} GHz). Using your value — may not converge."
            )
        self._log_computed_summary()
        self._run_step("Generating run script")
        script = self._run_script_for(work)
        self._log(
            f"Sim settings: f0={f0/1e9:.3f} GHz, max_timesteps={self.project.sim.max_timesteps}, "
            f"end_criteria={self.project.sim.end_criteria}, "
            f"boundary={self.project.boundary.as_openems_list()}"
        )
        self._reset_engine_progress()
        self._run_step("Starting openEMS (FDTD)")
        try:
            # _log is thread-safe (queue only) — do not pass Signal.emit
            self.runner.start(script, log_cb=self._log)
            self._log("Simulation started")
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Run failed", str(e))
            self._log(f"Run failed: {e}")
            self._end_run_log("Run failed to start")

        # poll for finished sim and open S-param viewer (GUI thread only)
        def _check() -> None:
            try:
                if self.runner.is_running():
                    return
                timer.stop()
                exit_code = getattr(self.runner, "last_exit", None)
                log_path = getattr(self.runner, "log_path", None)
                self._run_step("openEMS finished — loading results")
                files = self._find_sparams_files(work)
                found = files[-1] if files else None
                # Antenna pattern (NF2FF) — load alongside the S-params result
                nf2ff_json = work / "nf2ff" / "nf2ff.json"
                if not nf2ff_json.exists():
                    legacy = work / "simulation" / "nf2ff" / "nf2ff.json"
                    nf2ff_json = legacy if legacy.exists() else nf2ff_json
                if nf2ff_json.exists():
                    try:
                        self.antenna_viewer.set_work_dir(self.settings.work_path())
                        self.antenna_viewer.load_path(nf2ff_json)
                        self._log(f"Antenna pattern ready: {nf2ff_json}")
                    except Exception as e:  # noqa: BLE001
                        self._log(f"Antenna pattern load failed: {e}")
                if found:
                    self.sparam_viewer.set_work_dir(self._project_work_dir())
                    self.sparam_viewer.load_path(found)
                    if self.sparam_tab_index >= 0:
                        self.sparam_tabs.setCurrentIndex(self.sparam_tab_index)
                    self._log(f"S-param results ready: {found}")
                    if exit_code not in (0, None):
                        self._log(f"WARNING: simulation exit code {exit_code} (S-params may be partial)")
                else:
                    self._log(f"S-param results MISSING under: {work}")
                    self._log(f"Simulation exit code: {exit_code}")
                    if log_path:
                        self._log(f"Full simulation log: {log_path}")
                    self._log(
                        "Look for: S-param postproc FAILED / CalcPort failed / mesh validation failed"
                    )
                    self.sparam_viewer.set_work_dir(self._project_work_dir())
                    self._auto_load_sparams_if_empty()
                    if self.sparam_tab_index >= 0:
                        self.sparam_tabs.setCurrentIndex(self.sparam_tab_index)
                    QMessageBox.warning(
                        self,
                        "S-params missing",
                        f"No sparams.s1p / sparams.json under:\n{work}\n\n"
                        f"Exit code: {exit_code}\n"
                        f"Log: {log_path}\n\n"
                        "Check the simulator log for CalcPort / export / mesh errors.",
                    )
                self._publish_sim_report(work, exit_code, log_path)
                self._end_run_log()
            except Exception as e:  # noqa: BLE001
                print("post-sim viewer error:", e)
                self._log(f"Post-sim viewer error: {e}")
                self._end_run_log(f"Run ended with a post-processing error: {e}")

        timer = QTimer(self)
        timer.timeout.connect(_check)
        timer.start(1000)

    def _on_run_all_ports(self) -> None:
        """Run one FDTD simulation per port and merge the full S-matrix.

        One run measures one S-matrix column; running every port excited once
        and merging (``app/simulation/multirun.py``) yields the complete matrix
        / ``.sNp``. Needs >= 2 ports — otherwise it falls back to a normal run.
        """
        self._ensure_log_visible("simulation")
        self._sync_project_from_form()
        if self.runner.is_running() or self._multi_plans is not None:
            QMessageBox.warning(self, "Busy", "A simulation is already running.")
            return
        nports = len(self.project.ports)
        if nports < 2:
            self._log("Run All Ports: fewer than 2 ports — running the normal simulation")
            self._on_run()
            return
        self._begin_run_log("Run All Ports")
        self._run_step("Preparing run (project, work dir, geometry)")
        work = self._project_work_dir()
        self._prepare_run_state(work)
        from app.simulation.multirun import plan_port_runs

        self._run_step(f"Planning {nports} port run(s)")
        try:
            plans = plan_port_runs(self.project, self.primitives, work)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Multi-run setup failed", str(e))
            self._log(f"Multi-run setup failed: {e}")
            self._end_run_log("Run All Ports failed to start")
            return
        self._multi_plans = plans
        self._multi_pos = 0
        self._log(
            f"Run All Ports: {nports} ports → {nports} FDTD runs (one excitation "
            f"each) under {work / 'multi'}"
        )
        self._start_next_multi_run()

    def _start_next_multi_run(self) -> None:
        plans = self._multi_plans or []
        if self._multi_pos >= len(plans):
            self._finish_multi_run()
            return
        plan = plans[self._multi_pos]
        self._run_step(
            f"Multi-run {self._multi_pos + 1}/{len(plans)}: excite "
            f"'{plan.port_name}' (port {plan.port_number})"
        )
        self._reset_engine_progress()
        try:
            self.runner.start(
                plan.script_path,
                log_cb=lambda line, _n=plan.port_name: self._log(f"[{_n}] {line}"),
            )
        except Exception as e:  # noqa: BLE001
            self._log(f"Multi-run start failed ({plan.port_name}): {e}")
            QMessageBox.critical(self, "Run failed", str(e))
            self._multi_plans = None
            self._end_run_log("Run All Ports failed to start")
            return
        if self._multi_timer is None:
            self._multi_timer = QTimer(self)
            self._multi_timer.timeout.connect(self._poll_multi_run)
        self._multi_timer.start(1000)

    def _poll_multi_run(self) -> None:
        if self.runner.is_running():
            return
        self._multi_timer.stop()
        plans = self._multi_plans or []
        plan = plans[self._multi_pos]
        exit_code = getattr(self.runner, "last_exit", None)
        self._log(
            f"Multi-run {self._multi_pos + 1}/{len(plans)} "
            f"('{plan.port_name}') exit code {exit_code}"
        )
        if getattr(self, "_abort_requested", False):
            self._log("Multi-run aborted by the user")
            self._multi_pos = len(plans)  # do not schedule the remaining runs
            self._finish_multi_run()
            return
        self._multi_pos += 1
        self._start_next_multi_run()

    def _finish_multi_run(self) -> None:
        from app.simulation.multirun import merge_port_runs, summarise_table

        plans = self._multi_plans or []
        self._multi_plans = None
        work = self._project_work_dir()
        self._run_step("Merging port runs")
        try:
            result = merge_port_runs(work, plans)
        except Exception as e:  # noqa: BLE001
            self._log(f"Multi-run merge failed: {e}")
            QMessageBox.warning(self, "Merge failed", str(e))
            self._end_run_log("Run All Ports failed while merging")
            return
        if result["missing"]:
            self._log(
                f"WARNING: no sparams.json for port(s) {result['missing']} — "
                "those columns stay empty"
            )
        self._log("Merged S-matrix:")
        for line in summarise_table(result["table"]):
            self._log(line)
        self._log(f"Merged outputs: {result['written']}")
        self._record_results()
        try:
            found = self._find_sparams_files(work)
            if found:
                self.sparam_viewer.set_work_dir(work)
                self.sparam_viewer.load_path(found[-1])
                if self.sparam_tab_index >= 0:
                    self.sparam_tabs.setCurrentIndex(self.sparam_tab_index)
                self._log(f"S-param results ready: {found[-1]}")
        except Exception as e:  # noqa: BLE001 — merge succeeded, viewer is best-effort
            self._log(f"S-param viewer update failed: {e}")
        self._publish_sim_report(
            work, getattr(self.runner, "last_exit", None),
            getattr(self.runner, "log_path", None),
        )
        self._end_run_log()

    def _publish_sim_report(self, work: Path, exit_code, log_path) -> str:
        """Post-run status report: append it to the log, keep it for the menu.

        Collects solver errors/warnings, convergence, the engine's timing facts
        and the passivity (``|S11|^2 + |S21|^2``) of the measured S-parameters,
        so a finished run can be judged without reading the whole log.
        """
        text = ""
        try:
            from app.simulation.report import build_report, render

            log_text = ""
            p = Path(log_path) if log_path else None
            if p is not None and p.is_file():
                log_text = p.read_text(encoding="utf-8", errors="replace")
            sparams = None
            sp_path = ""
            files = self._find_sparams_files(work)
            # _find_sparams_files prefers the merged Touchstone; the passivity
            # check needs the JSON, so look for that separately.
            json_cands = [f for f in files if f.suffix.lower() == ".json"]
            if not json_cands:
                for sub in ("sparams", "simulation"):
                    cand = Path(work) / sub / "sparams.json"
                    if cand.is_file():
                        json_cands.append(cand)
            if json_cands:
                from app.postproc.sparams import load_sparams_json

                sp_path = str(json_cands[-1])
                try:
                    sparams = load_sparams_json(json_cands[-1])
                except Exception as e:  # noqa: BLE001 — report without the S-params
                    self._log(f"Simulation status: S-params unreadable ({e})")
            elif files:
                sp_path = str(files[-1])
            rep = build_report(
                log_text=log_text,
                exit_code=exit_code,
                sparams=sparams,
                sparams_path=sp_path,
                log_path=str(p) if p is not None else "",
                band_hz=(
                    self.project.mesh.start_frequency,
                    self.project.mesh.stop_frequency,
                ),
                target_hz=self.project.mesh.target_frequency,
                end_criteria=self.project.sim.end_criteria,
                timing=getattr(self, "_last_mesh_timing", None),
                results_found=bool(files or json_cands),
            )
            text = render(rep)
        except Exception as e:  # noqa: BLE001 — a report must never break a run
            text = f"Simulation status report failed: {e}"
        self._last_sim_report_text = text
        for line in text.splitlines():
            self._log(line)
        # After a run the engine names the cell that sets the timestep: mark it
        # in the Mesh Viewer (the "cells the simulation flagged" part).
        if log_text:
            try:
                from app.meshing.critical_cells import parse_engine_cell
                from app.meshing.preview import mesh_lines_from_csx

                lines_by_axis = (
                    mesh_lines_from_csx(self._last_csx)
                    if getattr(self, "_last_csx", None) is not None
                    else {}
                )
                cell = parse_engine_cell(log_text, lines_by_axis)
                if cell is not None:
                    self.mesh_viewer.add_critical_cell(cell)
                    self._log(f"Mesh Viewer: {cell.label} marked")
            except Exception as e:  # noqa: BLE001 — never break the report
                self._log(f"Mesh Viewer engine-cell mark failed: {e}")
        return text

    def _on_show_sim_status(self) -> None:
        """Simulation menu: status / quality report of the last run."""
        from app.ui.report_dialog import show_report

        text = getattr(self, "_last_sim_report_text", "")
        if not text:
            text = (
                "No simulation finished in this session yet.\n\n"
                "Run Simulation (Ctrl+R): the status block (errors, warnings, "
                "convergence, engine timing, passivity) is written to the log "
                "when the run ends and is available here afterwards."
            )
        self._sim_status_dialog = show_report(
            self,
            "Simulation Status",
            text,
            hint="Quality check of the last run: solver errors/warnings, "
            "convergence, mesh-vs-engine timestep and the passivity of the "
            "measured S-parameters.",
            save_name="simulation_status.txt",
        )

    def _on_abort(self) -> None:
        self._abort_requested = True
        self.runner.abort()
        self._log("Abort requested")

    def _on_field_autoplay_toggled(self, on: bool) -> None:
        """Remember the Field Viewer Auto-play choice across sessions."""
        self.settings.field_viewer_autoplay = bool(on)
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001 — never block playback on a write error
            pass

    def _on_field_axes_changed(self, options: dict) -> None:
        """Persist the Field Viewer axes options and mirror them to the pop-out."""
        self.settings.field_viewer_axes = dict(options or {})
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001 - never block the viewer on a write error
            pass
        win = getattr(getattr(self, "vtk_viewer", None), "_popout_win", None)
        if win is not None and getattr(win, "viewer", None) is not None:
            try:
                win.viewer.set_axes_options(options)
            except Exception:  # noqa: BLE001
                pass

    def _on_field_view_changed(self, key: str) -> None:
        """Persist the Field Viewer standard view and mirror it to the pop-out."""
        key = str(key or "keep")
        self.settings.field_viewer_view = key
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001 - never block the viewer on a write error
            pass
        win = getattr(getattr(self, "vtk_viewer", None), "_popout_win", None)
        if win is not None and getattr(win, "viewer", None) is not None:
            try:
                win.viewer.set_view_default(key)
            except Exception:  # noqa: BLE001
                pass

    def _on_field_vectors_changed(self, options: dict) -> None:
        """Persist the Field Viewer vector glyphs and mirror them to the pop-out."""
        self.settings.field_viewer_vectors = dict(options or {})
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001 - never block the viewer on a write error
            pass
        win = getattr(getattr(self, "vtk_viewer", None), "_popout_win", None)
        if win is not None and getattr(win, "viewer", None) is not None:
            try:
                win.viewer.set_vector_options(options)
            except Exception:  # noqa: BLE001
                pass

    def _on_settings(self) -> None:
        # Settings dialog: paths + port defaults
        from PySide6.QtWidgets import (
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QDoubleSpinBox,
            QFormLayout as QFL,
            QGroupBox,
            QVBoxLayout,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.resize(480, 560)
        root = QVBoxLayout(dlg)

        form = QFL()
        e_fc = QLineEdit(self.settings.freecad_cmd)
        e_fgui = QLineEdit(getattr(self.settings, "freecad_gui", ""))
        e_py = QLineEdit(self.settings.simulation_python)
        e_oe = QLineEdit(self.settings.openems_root)
        e_wd = QLineEdit(self.settings.work_dir)
        form.addRow("FreeCADCmd", e_fc)
        form.addRow("FreeCAD GUI", e_fgui)
        form.addRow("Simulation Python", e_py)
        form.addRow("openEMS root", e_oe)
        form.addRow("Work dir", e_wd)
        root.addLayout(form)

        # --- Port defaults (FreeCAD import + Add Port seed) ---
        grp = QGroupBox("Port defaults")
        pform = QFL(grp)
        cmb_ptype = QComboBox()
        cmb_ptype.addItems(["lumped", "msl", "rect_waveguide"])
        cmb_ptype.setCurrentText(
            str(getattr(self.settings, "default_port_type", "lumped") or "lumped")
        )
        cmb_ptype.setToolTip(
            "Type used when FreeCAD/export does not specify openems_port_type."
        )
        spn_pr = QDoubleSpinBox()
        spn_pr.setRange(0.1, 1e4)
        spn_pr.setDecimals(2)
        spn_pr.setValue(float(getattr(self.settings, "default_port_resistance", 50.0)))
        spn_pr.setSuffix(" Ω")
        spn_pr.setToolTip("Resistance for imported/new ports without explicit R.")
        chk_excite = QCheckBox("Excite new/imported ports by default")
        chk_excite.setChecked(bool(getattr(self.settings, "default_port_excite", True)))
        cmb_mesh = QComboBox()
        cmb_mesh.addItems(["xyz", "xy", "xz", "yz", "x", "y", "z", ""])
        cmb_mesh.setCurrentText(
            str(getattr(self.settings, "default_port_mesh_dirs", "xyz") or "xyz")
        )
        cmb_mesh.setToolTip("Mesh dirs when the CAD mark has none (empty = auto).")
        cmb_dir = QComboBox()
        cmb_dir.addItems(["", "x", "y", "z"])  # "" = auto from start/stop
        cmb_dir.setCurrentText(
            str(getattr(self.settings, "default_port_direction", "") or "")
        )
        cmb_dir.setToolTip(
            "Direction: empty = infer from start/stop vector (recommended)."
        )
        pform.addRow("Default type", cmb_ptype)
        pform.addRow("Default resistance", spn_pr)
        pform.addRow("", chk_excite)
        pform.addRow("Default mesh dirs", cmb_mesh)
        pform.addRow("Default direction", cmb_dir)
        root.addWidget(grp)

        hint = QLabel(
            "Port defaults fill values FreeCAD omitted (PORT_* points have no R/type).\n"
            "Explicit openems_port_* properties always win. "
            "Edit single ports later in Ports / Dumps (double-click)."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        # --- Field Viewer ---
        grp_fv = QGroupBox("Field Viewer")
        fvform = QFL(grp_fv)
        chk_autoplay = QCheckBox("Auto-play time series after loading / exporting")
        chk_autoplay.setChecked(
            bool(getattr(self.settings, "field_viewer_autoplay", False))
        )
        chk_autoplay.setToolTip(
            "Default state of the Field Viewer 'Auto-play' checkbox.\n"
            "Toggle in the viewer is remembered as well."
        )
        fvform.addRow("", chk_autoplay)
        from app.ui.vtk_viewer import VIEW_PRESETS

        cmb_view = QComboBox()
        for key, label in VIEW_PRESETS:
            cmb_view.addItem(label, key)
        idx_view = cmb_view.findData(str(getattr(self.settings, "field_viewer_view", "keep")))
        cmb_view.setCurrentIndex(idx_view if idx_view >= 0 else 0)
        cmb_view.setToolTip(
            "Standard view of the Field Viewer's 3D pane.\n"
            "Applied to every newly loaded field; the viewer's 'View' box\n"
            "changes the same setting."
        )
        fvform.addRow("Default view", cmb_view)
        chk_vectors = QCheckBox("Show field vectors (glyph arrows) after loading")
        chk_vectors.setChecked(
            bool(
                (getattr(self.settings, "field_viewer_vectors", None) or {}).get(
                    "enabled", False
                )
            )
        )
        chk_vectors.setToolTip(
            "Default state of the Field Viewer 'Vectors' checkbox.\n"
            "The arrows show the field direction (ParaView Glyph) — slice\n"
            "them onto the cut plane to watch the field move during playback.\n"
            "Changing it in the viewer is remembered as well."
        )
        fvform.addRow("", chk_vectors)
        root.addWidget(grp_fv)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        root.addWidget(bb)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        if not dlg.exec():
            return

        self.settings.freecad_cmd = e_fc.text()
        self.settings.freecad_gui = e_fgui.text()
        self.settings.simulation_python = e_py.text()
        self.settings.openems_root = e_oe.text()
        self.settings.work_dir = e_wd.text()
        self.settings.default_port_type = cmb_ptype.currentText() or "lumped"
        self.settings.default_port_resistance = float(spn_pr.value())
        self.settings.default_port_excite = bool(chk_excite.isChecked())
        self.settings.default_port_mesh_dirs = cmb_mesh.currentText()
        self.settings.default_port_direction = cmb_dir.currentText()
        self.settings.field_viewer_autoplay = bool(chk_autoplay.isChecked())
        self.settings.field_viewer_view = str(cmb_view.currentData() or "keep")
        vec_opts = dict(getattr(self.settings, "field_viewer_vectors", None) or {})
        vec_opts["enabled"] = bool(chk_vectors.isChecked())
        self.settings.field_viewer_vectors = vec_opts
        self.settings.save()
        try:
            self.vtk_viewer.set_autoplay_default(self.settings.field_viewer_autoplay)
        except Exception:  # noqa: BLE001 — viewer may not exist in early tests
            pass
        try:
            self.vtk_viewer.set_view_default(self.settings.field_viewer_view)
        except Exception:  # noqa: BLE001 — viewer may not exist in early tests
            pass
        try:
            self.vtk_viewer.set_vector_options(self.settings.field_viewer_vectors)
        except Exception:  # noqa: BLE001 — viewer may not exist in early tests
            pass
        try:
            self.sparam_viewer.scan_root = self.settings.work_path()
            self.antenna_viewer.set_work_dir(self.settings.work_path())
        except Exception:  # noqa: BLE001 — viewers may not exist in early tests
            pass
        self.runner = SimulationRunner(self.settings, app_root=APP_ROOT)
        pd = self.settings.port_defaults()
        self._log(
            f"Settings saved (port defaults: type={pd['type']} "
            f"R={pd['resistance']} excite={pd['excite_port']} "
            f"mesh={pd['mesh_dirs']} dir={pd['direction'] or 'auto'})"
        )


from app.paths import app_root as _app_root

APP_ROOT = _app_root()
