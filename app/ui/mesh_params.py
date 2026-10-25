"""Mesh parameters panel + standalone window (Mesh menu).

The panel mirrors every editable field on the Mesh tab (preset, frequency
unit, f0/fc/start/stop, drawing unit) plus an auto-computed readout (lambda at
target freq, max/target cell, effective boundary notes). Values are stored in
the project exactly like the Mesh tab widgets (frequencies always in Hz).

:class:`MeshParamsDialog` is the modeless window opened from
Mesh -> Mesh Parameters... (Ctrl+Shift+M). ``Apply`` emits
:attr:`MeshParamsDialog.apply_requested`; MainWindow writes the values into the
project and mirrors them into the Mesh tab. ``Close`` discards unapplied edits.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.meshing.rules import air_margin_units, compute_targets
from app.models.project import Project
from app.simulation.excitation import (
    available_kinds,
    describe_excitation,
    kind_label,
    normalize_kind,
    read_custom_waveform,
)
from app.ui.freq_units import (
    FREQ_UNITS,
    configure_freq_spin,
    hz_to_freq,
    set_spin_hz,
    spin_hz,
    suggest_freq_unit,
    unit_combo_items,
)
from app.ui.tab_help import show_tab_help

MESH_PRESETS: list[str] = ["low", "medium", "high", "very_high"]
DRAWING_UNITS: list[str] = ["mm", "um", "m"]
COORD_SYSTEMS: list[str] = ["cartesian", "cylindrical"]


class MeshParamsPanel(QWidget):
    """Form for every Mesh-tab parameter plus a live computed readout."""

    # any editable value changed (frequencies are already written to `project`)
    computed_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._freq_unit_name = "GHz"
        self._freq_unit_factor = float(FREQ_UNITS["GHz"])
        self._freq_guard = False

        root = QVBoxLayout(self)

        freq_box = QGroupBox("Frequencies")
        form = QFormLayout(freq_box)
        self.cmb_preset = QComboBox()
        self.cmb_preset.addItems(MESH_PRESETS)
        self.cmb_preset.setCurrentText("high")
        self.cmb_freq_unit = QComboBox()
        unit_combo_items(self.cmb_freq_unit)
        self.cmb_freq_unit.setCurrentText(self._freq_unit_name)

        self.spn_f0 = QDoubleSpinBox()
        self.spn_f0.setKeyboardTracking(False)
        configure_freq_spin(self.spn_f0, self._freq_unit_name)
        self.spn_f0.setValue(hz_to_freq(2.45e9, self._freq_unit_name))
        self.spn_fc = QDoubleSpinBox()
        self.spn_fc.setKeyboardTracking(False)
        configure_freq_spin(self.spn_fc, self._freq_unit_name)
        self.spn_fc.setValue(hz_to_freq(0.5e9, self._freq_unit_name))
        self.spn_start_f = QDoubleSpinBox()
        self.spn_start_f.setKeyboardTracking(False)
        configure_freq_spin(self.spn_start_f, self._freq_unit_name)
        self.spn_start_f.setValue(hz_to_freq(1.95e9, self._freq_unit_name))
        self.spn_stop_f = QDoubleSpinBox()
        self.spn_stop_f.setKeyboardTracking(False)
        configure_freq_spin(self.spn_stop_f, self._freq_unit_name)
        self.spn_stop_f.setValue(hz_to_freq(2.95e9, self._freq_unit_name))
        self.cmb_unit = QComboBox()
        self.cmb_unit.addItems(DRAWING_UNITS)

        # excitation waveform (same choice as the Mesh tab)
        self.cmb_excite = QComboBox()
        for _kind in available_kinds():
            self.cmb_excite.addItem(kind_label(_kind), _kind)
        self.cmb_excite.setToolTip(
            "FDTD excitation waveform:\n"
            "Gauss — broadband pulse defined by f0/fc (default, best for S-params).\n"
            "Sinus — single frequency f0; S-params are only meaningful near f0.\n"
            "Step — broadband step, normalised to the band's highest frequency.\n"
            "Dirac — broadband impulse.\n"
            "Custom — two-column CSV (time in seconds, amplitude)."
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
        self.btn_excite_file.clicked.connect(self._on_pick_excitation_file)
        exc_file_row = QWidget()
        exc_file_layout = QHBoxLayout(exc_file_row)
        exc_file_layout.setContentsMargins(0, 0, 0, 0)
        exc_file_layout.addWidget(self.edt_excite_file, 1)
        exc_file_layout.addWidget(self.btn_excite_file)
        self.wdg_excite_file = exc_file_row

        form.addRow("Mesh preset", self.cmb_preset)
        form.addRow("Frequency unit", self.cmb_freq_unit)
        form.addRow("Center frequency f0", self.spn_f0)
        form.addRow("Corner frequency fc", self.spn_fc)
        form.addRow("Excitation", self.cmb_excite)
        form.addRow("Custom waveform", exc_file_row)
        form.addRow("Start frequency", self.spn_start_f)
        form.addRow("Stop frequency", self.spn_stop_f)
        form.addRow("Drawing unit", self.cmb_unit)
        root.addWidget(freq_box)

        # --- Mesh overrides (moved from Simulation Parameters) ---
        g_m = QGroupBox("Mesh overrides (blank/0 = auto)")
        fm = QFormLayout(g_m)
        self.spn_refined = QDoubleSpinBox()
        self.spn_refined.setRange(0.0, 1_000_000.0)
        self.spn_refined.setDecimals(4)
        self.spn_refined.setSpecialValueText("auto")
        self.spn_min_cell = QDoubleSpinBox()
        self.spn_min_cell.setRange(0.0, 1_000_000.0)
        self.spn_min_cell.setDecimals(4)
        self.spn_min_cell.setSpecialValueText("auto")
        self.spn_min_cell_z = QDoubleSpinBox()
        self.spn_min_cell_z.setRange(0.0, 1_000_000.0)
        self.spn_min_cell_z.setDecimals(4)
        self.spn_min_cell_z.setSpecialValueText("auto")
        self.spn_max_cell = QDoubleSpinBox()
        self.spn_max_cell.setRange(0.0, 1_000_000.0)
        self.spn_max_cell.setDecimals(4)
        self.spn_max_cell.setSpecialValueText("auto")
        self.spn_grading = QDoubleSpinBox()
        self.spn_grading.setRange(1.05, 10.0)  # 1.0 breaks the lambda/10 cell cap
        self.spn_grading.setDecimals(2)
        self.spn_grading.setSingleStep(0.05)
        self.spn_grading.setValue(2.0)
        self.spn_grading.setToolTip(
            "Max grading ratio between neighbouring cells (1.05 … 10).\n"
            "Must be > 1 — 1.0 disables grading and makes the lambda/10 cell\n"
            "cap fail (oversized PML cells → the run aborts)."
        )
        self.spn_num_lines = QSpinBox()
        self.spn_num_lines.setRange(2, 50)  # >= 2: easyMesh samples + diffs per gap
        self.spn_num_lines.setValue(5)
        self.spn_num_lines.setToolTip(
            "Extra mesh lines per edge gap (easyMesh num_lines), at least 2.\n"
            "Higher = denser at metal edges."
        )
        self.cmb_dirs = QComboBox()
        self.cmb_dirs.addItems(["xyz", "xy", "xz", "yz", "x", "y", "z"])
        self.cmb_dirs.setToolTip("Mesh only these axes")
        self.cmb_coords = QComboBox()
        self.cmb_coords.addItems(COORD_SYSTEMS)
        self.cmb_coords.setToolTip(
            "FDTD coordinate system:\n"
            "cartesian — x/y/z grid (easyMesh), full geometry support.\n"
            "cylindrical — openEMS CoordSystem=1 (ρ/φ/z) for cylinders and "
            "rotationally symmetric structures.\n"
            "Cylindrical mode: box = wedge in (ρ, φ, z), φ in degrees in the "
            "editors (stored as radians), no STL/polygon/polyhedron, no NF2FF."
        )
        self.cmb_smooth = QComboBox()
        self.cmb_smooth.addItems(["one_third_two_thirds", "extra_lines", "False"])
        self.chk_circle = QCheckBox("Use circle detection")
        self.chk_arc = QCheckBox("Use arc detection")
        self.chk_edges = QCheckBox("Handle closely placed edges")
        self.chk_edges.setChecked(True)
        self.chk_graded_transitions = QCheckBox(
            "Refine mesh at metal interfaces (graded)"
        )
        self.chk_graded_transitions.setToolTip(
            "Insert graded mesh lines next to every metal (PEC) surface, on the\n"
            "vacuum/dielectric side: finest cell at the interface (Min cell\n"
            "size, fallback local cell / 4), coarsening by the Max grading\n"
            "ratio with distance. Cartesian only."
        )
        self.chk_align_coords = QCheckBox(
            "Align coordinates (float32 grid)"
        )
        self.chk_align_coords.setChecked(True)
        self.chk_align_coords.setToolTip(
            "Put every geometry coordinate on the same float32 grid: CSXCAD\n"
            "stores boxes/ports/dumps/NF2FF as float32, and triangle meshes\n"
            "(STEP/STL/FCStd) are aligned to it as well, so coordinates that\n"
            "should be equal are bit-identical - no second grid line, no sliver\n"
            "cell that collapses the timestep.\n"
            "Off = legacy: triangle meshes are rounded to 0.1 um instead, which\n"
            "leaves that small offset between the coordinate sources."
        )
        self.chk_diag_sources = QCheckBox("Attribute fine cells to their source")
        self.chk_diag_sources.setChecked(True)
        self.chk_diag_sources.setToolTip(
            "P2: name the primitive / port / manual line that forced every cell\n"
            "the mesh report flags (too small, grading jump, ...). The name shows\n"
            "up in the Mesh Viewer marks and in the mesh report section\n"
            "'Fine-cell sources'. Diagnostic only - the mesh is not changed."
        )
        self.chk_enforce_min = QCheckBox("Enforce min cell size (repair)")
        self.chk_enforce_min.setChecked(False)
        self.chk_enforce_min.setToolTip(
            "V2: treat 'Min cell size' as a hard lower bound. After easyMesh, a\n"
            "cell below it whose faces are NOT both authored coordinates is\n"
            "merged into its smaller neighbour (grading is re-spaced); a genuine\n"
            "gap whose both faces come from the geometry is kept and reported.\n"
            "Off (default) = easyMesh decides; the report still lists the cells\n"
            "that set the timestep."
        )
        fm.addRow("Refined cell size", self.spn_refined)
        fm.addRow("Min cell size", self.spn_min_cell)
        fm.addRow("Min cell size (Z)", self.spn_min_cell_z)
        fm.addRow("Max cell size", self.spn_max_cell)
        fm.addRow("Max grading ratio", self.spn_grading)
        fm.addRow("Num lines (edge gaps)", self.spn_num_lines)
        fm.addRow("Coordinate system", self.cmb_coords)
        fm.addRow("Mesh directions", self.cmb_dirs)
        fm.addRow("Smooth metal edge", self.cmb_smooth)
        fm.addRow(self.chk_circle)
        fm.addRow(self.chk_arc)
        fm.addRow(self.chk_edges)
        fm.addRow(self.chk_graded_transitions)
        fm.addRow(self.chk_align_coords)
        fm.addRow(self.chk_diag_sources)
        fm.addRow(self.chk_enforce_min)
        root.addWidget(g_m)

        self.readout = QPlainTextEdit()
        self.readout.setReadOnly(True)
        self.readout.setMaximumHeight(200)
        self.readout.setPlaceholderText("Computed mesh targets appear here.")
        root.addWidget(self.readout)
        root.addStretch(1)

        for w in (self.cmb_preset, self.cmb_unit):
            w.currentTextChanged.connect(self._on_changed)
        for w in (self.spn_f0, self.spn_fc):
            w.valueChanged.connect(self._on_f0fc_changed)
        for w in (self.spn_start_f, self.spn_stop_f):
            w.valueChanged.connect(self._on_range_changed)
        self.cmb_freq_unit.currentTextChanged.connect(self._on_freq_unit_changed)
        # mesh overrides
        for w in (self.spn_refined, self.spn_min_cell, self.spn_min_cell_z,
                   self.spn_max_cell, self.spn_grading, self.spn_num_lines):
            w.valueChanged.connect(self._on_changed)
        self.cmb_smooth.currentTextChanged.connect(self._on_changed)
        self.cmb_dirs.currentTextChanged.connect(self._on_changed)
        self.cmb_coords.currentTextChanged.connect(self._on_changed)
        self.cmb_excite.currentIndexChanged.connect(self._on_excitation_changed)
        self.chk_circle.toggled.connect(self._on_changed)
        self.chk_arc.toggled.connect(self._on_changed)
        self.chk_edges.toggled.connect(self._on_changed)
        self.chk_graded_transitions.toggled.connect(self._on_changed)
        self.chk_align_coords.toggled.connect(self._on_changed)
        self.chk_diag_sources.toggled.connect(self._on_changed)

        # Lazy form: edits never touch the project. `write_to_project` is the
        # only place the project is written (on Apply). The readout is served
        # from a buffer built from the form itself.
        self._draft = Project.new_default("draft")
        self._update_excitation_state()

    # --- frequency helpers (always Hz in the project) -------------------
    def _unit(self) -> str:
        return self._freq_unit_name or "GHz"

    def _factor(self) -> float:
        return float(FREQ_UNITS.get(self._unit(), 1.0))

    def _hz(self, spin: QDoubleSpinBox) -> float:
        return spin_hz(spin, self._unit())

    def _set_hz(self, spin: QDoubleSpinBox, hz: float) -> None:
        set_spin_hz(spin, hz, self._unit())

    # --- signal handlers (lazy: widgets only, project untouched) ---------
    def _on_changed(self, *_a: object) -> None:
        self._refresh_readout_from_draft()

    def _on_f0fc_changed(self, *_a: object) -> None:
        self._refresh_readout_from_draft()

    def _on_range_changed(self, *_a: object) -> None:
        self._refresh_readout_from_draft()

    def _on_freq_unit_changed(self, unit: str) -> None:
        if unit not in FREQ_UNITS or unit == self._freq_unit_name:
            return
        vals = {k: spin_hz(w, self._freq_unit_name) for k, w in self._spins().items()}
        self._freq_unit_name = unit
        self._freq_unit_factor = float(FREQ_UNITS[unit])
        for name, w in self._spins().items():
            configure_freq_spin(w, unit)
            set_spin_hz(w, vals[name], unit)
        self._refresh_readout_from_draft()

    def _spins(self) -> dict[str, QDoubleSpinBox]:
        return {
            "f0": self.spn_f0,
            "fc": self.spn_fc,
            "start": self.spn_start_f,
            "stop": self.spn_stop_f,
        }

    # --- excitation waveform --------------------------------------------
    def _on_excitation_changed(self, *_a: object) -> None:
        self._update_excitation_state()
        self._refresh_readout_from_draft()

    def _on_pick_excitation_file(self) -> None:
        start = self.edt_excite_file.text().strip() or ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Custom excitation waveform",
            start,
            "Waveform (*.csv *.txt *.dat);;All (*.*)",
        )
        if not path:
            return
        try:
            times, _values = read_custom_waveform(path)
        except ValueError as e:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.warning(self, "Invalid waveform", str(e))
            return
        self.edt_excite_file.setText(path)
        idx = self.cmb_excite.findData("custom")
        if idx >= 0:
            self.cmb_excite.setCurrentIndex(idx)
        self._update_excitation_state()
        self._refresh_readout_from_draft()
        print(f"Custom excitation: {path} ({len(times)} samples)")

    def _update_excitation_state(self) -> None:
        kind = self.cmb_excite.currentData() or "gauss"
        self.spn_fc.setEnabled(kind == "gauss")
        custom = kind == "custom"
        self.wdg_excite_file.setEnabled(custom)
        self.btn_excite_file.setEnabled(custom)

    # --- data flow -------------------------------------------------------
    def project(self) -> Project | None:
        """Project supplied by MainWindow via sync_from_project, else None."""
        return getattr(self, "_project", None)

    def sync_mesh_freqs(self, project: Project | None, source: str = "f0fc") -> None:
        """Write widgets -> project.mesh (mirror of MainWindow._sync_mesh_freqs_from_widgets)."""
        if self._freq_guard:
            return
        if project is None:
            return
        self._freq_guard = True
        try:
            m = project.mesh
            m.preset = self.cmb_preset.currentText()
            m.drawing_unit = self.cmb_unit.currentText()
            m.excitation = str(self.cmb_excite.currentData() or "gauss")
            m.excitation_file = self.edt_excite_file.text().strip()
            f0 = self._hz(self.spn_f0)
            fc = self._hz(self.spn_fc)
            start = self._hz(self.spn_start_f)
            stop = self._hz(self.spn_stop_f)
            if source == "f0fc":
                start = f0 - fc
                stop = f0 + fc
                if stop <= start:
                    stop = start + max(1e5, 0.05 * start)
                self._set_hz(self.spn_start_f, start)
                self._set_hz(self.spn_stop_f, stop)
                m.start_frequency = start
                m.stop_frequency = stop
                m.target_frequency = f0
            else:
                if stop <= start:
                    stop = start + max(1e5, 0.05 * start)
                    self._set_hz(self.spn_stop_f, stop)
                m.start_frequency = start
                m.stop_frequency = stop
                if f0 < start or f0 > stop:
                    f0 = 0.5 * (start + stop)
                    self._set_hz(self.spn_f0, f0)
                fc = 0.5 * (stop - start)
                self._set_hz(self.spn_fc, fc)
                m.target_frequency = f0
        finally:
            self._freq_guard = False

    def sync_from_project(self, project: Project, mesh_preset: str | None = None) -> None:
        """Load every field from project.mesh (called on open and on re-sync).

        ``mesh_preset`` (optional) overrides the preset read from the project,
        for API parity with ``SimParamsPanel.sync_from_project``.
        """
        self._project = project
        m = project.mesh
        f0 = m.target_frequency or 0.5 * (m.start_frequency + m.stop_frequency)
        fc = max(1e5, 0.5 * abs(m.stop_frequency - m.start_frequency))
        unit = suggest_freq_unit(f0) if self._freq_unit_name is None else self._freq_unit_name
        if unit != self._freq_unit_name:
            self._freq_unit_name = unit
            self._freq_unit_factor = float(FREQ_UNITS[unit])
            for w in self._spins().values():
                configure_freq_spin(w, unit)
            self.cmb_freq_unit.setCurrentText(unit)
        self._freq_guard = True
        try:
            self.cmb_preset.setCurrentText(mesh_preset or m.preset or "high")
            self.cmb_unit.setCurrentText(m.drawing_unit or "mm")
            self._set_hz(self.spn_start_f, float(m.start_frequency))
            self._set_hz(self.spn_stop_f, float(m.stop_frequency))
            self._set_hz(self.spn_f0, f0)
            self._set_hz(self.spn_fc, fc)
            idx = self.cmb_excite.findData(normalize_kind(getattr(m, "excitation", "gauss")))
            self.cmb_excite.setCurrentIndex(idx if idx >= 0 else 0)
            self.edt_excite_file.setText(str(getattr(m, "excitation_file", "") or ""))
        finally:
            self._freq_guard = False
        self._update_excitation_state()
        # mesh overrides
        self.spn_refined.setValue(float(m.refined_cellsize or 0.0))
        self.spn_min_cell.setValue(float(m.min_cellsize or 0.0))
        self.spn_min_cell_z.setValue(float(getattr(m, "min_cellsize_z", None) or 0.0))
        self.spn_max_cell.setValue(float(m.max_cellsize or 0.0))
        self.spn_grading.setValue(float(m.max_grading_ratio or 2.0))
        self.spn_num_lines.setValue(int(getattr(m, "num_lines", 5) or 5))
        dirs = str(getattr(m, "dirs", "xyz") or "xyz")
        idx = self.cmb_dirs.findText(dirs)
        self.cmb_dirs.setCurrentIndex(idx if idx >= 0 else 0)
        coord = str(getattr(m, "coord_system", "cartesian") or "cartesian")
        idx = self.cmb_coords.findText(coord)
        self.cmb_coords.setCurrentIndex(idx if idx >= 0 else 0)
        smooth = str(m.smooth_metal_edge)
        idx = self.cmb_smooth.findText(smooth)
        self.cmb_smooth.setCurrentIndex(idx if idx >= 0 else 0)
        self.chk_circle.setChecked(bool(m.use_circle_detection))
        self.chk_arc.setChecked(bool(getattr(m, "use_arc_detection", False)))
        self.chk_edges.setChecked(bool(m.handle_closely_placed_edges))
        self.chk_graded_transitions.setChecked(
            bool(getattr(m, "add_graded_mesh_lines_at_material_transitions", False))
        )
        self.chk_align_coords.setChecked(bool(getattr(m, "align_coordinates", True)))
        self.chk_diag_sources.setChecked(bool(getattr(m, "diagnose_cell_sources", True)))
        self.chk_enforce_min.setChecked(
            bool(getattr(m, "enforce_min_cellsize", False))
        )
        # mirror the loaded values into the draft buffer (readout source)
        self._draft = Project.new_default("draft")
        self.sync_mesh_freqs(self._draft, "range")
        self._refresh_readout_from_draft()

    def write_to_project(self, project: Project) -> None:
        """Write every widget into project.mesh (the only project write)."""
        self._project = project
        # start/stop vs f0/fc: keep the project self-consistent the same way the
        # Mesh tab does (start/stop = f0 -/+ fc when they still match).
        start = self._hz(self.spn_start_f)
        stop = self._hz(self.spn_stop_f)
        f0 = self._hz(self.spn_f0)
        fc = self._hz(self.spn_fc)
        if abs(start - (f0 - fc)) < 1.0 and abs(stop - (f0 + fc)) < 1.0:
            self.sync_mesh_freqs(project, "f0fc")
        else:
            self.sync_mesh_freqs(project, "range")
        # mesh overrides
        m = project.mesh
        m.refined_cellsize = float(self.spn_refined.value()) or None
        m.min_cellsize = float(self.spn_min_cell.value()) or None
        m.min_cellsize_z = float(self.spn_min_cell_z.value()) or None
        m.max_cellsize = float(self.spn_max_cell.value()) or None
        m.max_grading_ratio = float(self.spn_grading.value())
        m.num_lines = int(self.spn_num_lines.value())
        m.dirs = self.cmb_dirs.currentText()
        m.coord_system = self.cmb_coords.currentText()  # type: ignore[assignment]
        m.smooth_metal_edge = self.cmb_smooth.currentText()  # type: ignore[assignment]
        m.use_circle_detection = self.chk_circle.isChecked()
        m.use_arc_detection = self.chk_arc.isChecked()
        m.handle_closely_placed_edges = self.chk_edges.isChecked()
        m.add_graded_mesh_lines_at_material_transitions = self.chk_graded_transitions.isChecked()
        m.align_coordinates = self.chk_align_coords.isChecked()
        m.diagnose_cell_sources = self.chk_diag_sources.isChecked()
        m.enforce_min_cellsize = self.chk_enforce_min.isChecked()
        self.refresh_readout(project)

    # --- live readout ----------------------------------------------------
    def _refresh_readout_from_draft(self) -> None:
        self.sync_mesh_freqs(self._draft, "range")
        self.refresh_readout(self._draft)
        self.computed_changed.emit()

    def refresh_readout(self, project: Project | None = None) -> None:
        p = project or self.project()
        if p is None:
            return
        m = p.mesh
        f0 = m.target_frequency or 0.5 * (m.start_frequency + m.stop_frequency)
        info: list[str] = []
        try:
            t = compute_targets(m, eps_max=1.0)
            unit = m.drawing_unit or "mm"
            info.append(
                f"lambda_air  = {t.lambda_min_units:.6g} {unit}   (at f_max)"
            )
            info.append(f"max_cell    = {t.max_cell_units:.6g} {unit}")
            info.append(f"target_cell = {t.target_cell_units:.6g} {unit}")
        except Exception as e:  # noqa: BLE001
            info.append(f"targets: {e!r}")
        info.append("")
        info.append(
            f"preset={m.preset}, drawing_unit={m.drawing_unit}, "
            f"f0={f0:.6g} Hz"
        )
        info.append(
            describe_excitation(
                m, f0, max(1e5, 0.5 * abs(m.stop_frequency - m.start_frequency))
            )
        )
        info.append(
            f"coord_system={getattr(m, 'coord_system', 'cartesian')}"
            + (
                "  (box=wedge in rho/phi/z, phi in editors shown in degrees, "
                "no STL, no NF2FF)"
                if getattr(m, "coord_system", "cartesian") == "cylindrical"
                else ""
            )
        )
        info.append(
            f"start={m.start_frequency:.6g} Hz, stop={m.stop_frequency:.6g} Hz"
        )
        self.readout.setPlainText("\n".join(info))


class MeshParamsDialog(QDialog):
    """Standalone window with all mesh parameters (Mesh menu)."""

    apply_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mesh Parameters")
        self.setModal(False)
        self.resize(520, 560)
        self._project: Project | None = None

        root = QVBoxLayout(self)
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Mesh Parameters")
        title.setStyleSheet("font-weight: bold;")
        hl.addWidget(title)
        hl.addStretch(1)
        help_btn = QPushButton("Help")
        help_btn.setToolTip("Help for the Mesh Parameters window")
        help_btn.clicked.connect(
            lambda _=False, p=self: show_tab_help(p, "Mesh Parameters")
        )
        hl.addWidget(help_btn)
        root.addWidget(header)
        hint = QLabel(
            "All mesh parameters in one place. "
            "<b>Apply</b> writes them into the project (and mirrors them to the "
            "mesh form); <b>Close</b> discards unapplied changes."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.panel = MeshParamsPanel()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.panel)
        root.addWidget(scroll, 1)

        bb = QDialogButtonBox(self)
        self.btn_apply = bb.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        self.btn_close = bb.addButton(QDialogButtonBox.StandardButton.Close)
        root.addWidget(bb)
        bb.clicked.connect(self._on_button_clicked)
        self.panel.computed_changed.connect(self._refresh_readout)

    # --- data flow -------------------------------------------------------
    def load(self, project: Project) -> None:
        """Sync the form from the project (called on every open)."""
        self._project = project
        self.panel.sync_from_project(project)
        self.panel._refresh_readout_from_draft()

    def _refresh_readout(self) -> None:
        self.panel.refresh_readout(self._project)

    def _on_button_clicked(self, btn) -> None:  # noqa: ANN001
        if btn is self.btn_apply:
            self.apply_requested.emit()
        else:
            self.close()
