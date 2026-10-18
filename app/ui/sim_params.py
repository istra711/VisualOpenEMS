"""Simulation parameters panel: show auto values + allow overrides."""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.meshing.rules import air_margin_units, compute_targets
from app.models.project import (
    CELLS_PER_WAVELENGTH_BY_PRESET,
    BoundaryKind,
    BoundarySpec,
    MeshSpec,
    Project,
)
from app.postproc.nf2ff import parse_freq_list
from app.ui.freq_units import fmt_hz
from app.ui.tab_help import show_tab_help

BOUNDARY_KINDS: list[BoundaryKind] = ["PEC", "PMC", "MUR", "PML"]

# NF2FF evaluation frequency modes (combo labels -> project freq_mode)
NF2FF_FREQ_MODES: list[tuple[str, str]] = [
    ("Auto (S11 minimum)", "auto"),
    ("N points (mesh range)", "count"),
    ("Frequency list", "list"),
]

DEFAULT_PATCH_PARAMS: dict[str, float] = {
    "patch_width": 31.7,
    "patch_length": 39.7,
    "substrate_eps": 3.38,
    "substrate_width": 60.0,
    "substrate_length": 60.0,
    "substrate_thickness": 1.524,
    "feed_pos": -6.0,
    "feed_r": 50.0,
    "f0": 2.45e9,
    "fc": 0.5e9,
    "ground_thickness": 0.25,
}


def _fmt_hz(v: float) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.4f} GHz"
    if v >= 1e6:
        return f"{v / 1e6:.3f} MHz"
    return f"{v:.3f} Hz"


def _fmt_len(v: float) -> str:
    if abs(v) >= 1.0:
        return f"{v:.4g} mm"
    return f"{v * 1000:.4g} um"


class SimParamsPanel(QWidget):
    """Edit + inspect simulation/mesh parameters that the tool auto-computes."""

    computed_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.patch_params: dict[str, float] = dict(DEFAULT_PATCH_PARAMS)
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        # two tabs: everything except NF2FF | NF2FF-specific parameters
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_sim_tab(), "Simulation-Parameter")
        self.tabs.addTab(self._build_nf2ff_tab(), "NF2FF-Parameter")

        # connections → recompute display
        for w in (
            self.spn_end_criteria,
            self.spn_max_ts,
            self.spn_num_threads,
            self.spn_pml,
            self.spn_air,
            self.spn_nf2ff_theta,
            self.spn_nf2ff_phi,
        ):
            w.valueChanged.connect(self._on_form_edited)
        self.chk_air_auto.toggled.connect(self._on_air_auto_toggled)
        self.chk_air_auto.toggled.connect(self._on_form_edited)
        self.chk_antenna.toggled.connect(self._on_antenna_toggled)
        self.chk_antenna.toggled.connect(self._on_form_edited)
        self.cmb_ant_density.currentIndexChanged.connect(self._on_form_edited)
        self.spn_ant_pml.valueChanged.connect(self._on_form_edited)
        self.chk_nf2ff_enabled.toggled.connect(self._on_form_edited)
        self.cmb_nf2ff_mode.currentTextChanged.connect(self._on_freq_mode_changed)
        self.spn_nf2ff_count.valueChanged.connect(self._on_form_edited)
        self.txt_nf2ff_freqs.editingFinished.connect(self._on_form_edited)
        for cb in self.cmb_b.values():
            cb.currentTextChanged.connect(self._on_form_edited)
        self.btn_edit_patch.clicked.connect(self._on_edit_patch)
        self.btn_apply_patch.clicked.connect(self._on_apply_patch)
        self.btn_fit_nf2ff.clicked.connect(self._on_fit_nf2ff)
        self.btn_edit_nf2ff.clicked.connect(self._on_edit_nf2ff)

        self._refresh_patch_label()
        self._update_freq_mode_widgets()
        self.refresh_computed()
        # Patch preset is optional — hidden until a patch project/preset is active
        self.set_patch_group_visible(False)

    @staticmethod
    def _mono_font(label: QLabel):
        """Monospace font for read-only data labels (returned for reuse)."""
        font = label.font()
        font.setFamily("Consolas")
        label.setFont(font)
        return font

    def _build_sim_tab(self) -> QWidget:
        """Tab 'Simulation-Parameter': computed values, sim/mesh/boundary."""
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 4, 4, 4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 1)
        body = QWidget()
        scroll.setWidget(body)
        inner = QVBoxLayout(body)
        inner.setContentsMargins(4, 4, 4, 4)

        # --- Computed (read-only) ---
        g_calc = QGroupBox("Computed automatically (read-only)")
        fc = QFormLayout(g_calc)
        self.lbl_computed = QLabel("—")
        self.lbl_computed.setWordWrap(True)
        self.lbl_computed.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._mono_font(self.lbl_computed)
        fc.addRow(self.lbl_computed)
        inner.addWidget(g_calc)

        # --- Excitation & convergence ---
        g_conv = QGroupBox("Excitation & convergence")
        fconv = QFormLayout(g_conv)
        self.spn_end_criteria = QDoubleSpinBox()
        # shown/stored in dB (relative to the start energy); the run stops when
        # the residual energy has dropped by this many dB. Internally openEMS
        # uses the linear energy ratio (10**(dB/10)).
        self.spn_end_criteria.setDecimals(1)
        self.spn_end_criteria.setRange(-200.0, -1.0)
        self.spn_end_criteria.setSingleStep(1.0)
        self.spn_end_criteria.setSuffix(" dB")
        self.spn_end_criteria.setValue(-50.0)
        self.spn_end_criteria.setToolTip(
            "End criteria: residual energy level relative to the start (0 dB),\n"
            "e.g. -50 dB (= linear 1e-5). The run stops when the energy has\n"
            "dropped by this much; 0 dB is the energy in the system at start."
        )
        self.spn_max_ts = QSpinBox()
        self.spn_max_ts.setRange(1_000, 20_000_000)
        self.spn_max_ts.setSingleStep(1_000)
        self.spn_max_ts.setValue(200_000)
        self.spn_num_threads = QSpinBox()
        self.spn_num_threads.setRange(0, 256)
        self.spn_num_threads.setSpecialValueText("Auto (all cores)")
        self.spn_num_threads.setValue(0)
        self.spn_num_threads.setToolTip(
            "Number of openEMS FDTD threads (numThreads).\n"
            "0 = Auto (use all available cores).\n"
            "Set > 0 to limit CPU load or reserve cores for other tasks."
        )
        fconv.addRow("End criteria (energy)", self.spn_end_criteria)
        # NOTE: the spin is in dB; write_to_project converts to the linear ratio.
        fconv.addRow("Max timesteps (NrTS)", self.spn_max_ts)
        fconv.addRow("Threads (numThreads)", self.spn_num_threads)
        inner.addWidget(g_conv)

        # --- Boundary ---
        g_b = QGroupBox("Boundary conditions")
        fb = QFormLayout(g_b)
        self.cmb_b: dict[str, QComboBox] = {}
        self.lbl_b: dict[str, QLabel] = {}
        for axis in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"):
            cb = QComboBox()
            cb.addItems(BOUNDARY_KINDS)
            cb.setCurrentText("PML")
            self.cmb_b[axis] = cb
            lbl = QLabel(axis)
            self.lbl_b[axis] = lbl
            fb.addRow(lbl, cb)
        self.spn_pml = QSpinBox()
        self.spn_pml.setRange(1, 32)
        self.spn_pml.setValue(8)
        fb.addRow("PML size", self.spn_pml)
        self.chk_air_auto = QCheckBox("Air margin auto (λ/4 free-space)")
        self.chk_air_auto.setChecked(True)
        fb.addRow(self.chk_air_auto)
        self.spn_air = QDoubleSpinBox()
        self.spn_air.setRange(0.0, 10_000.0)
        self.spn_air.setDecimals(3)
        self.spn_air.setSuffix(" mm")
        self.spn_air.setValue(20.0)
        self.spn_air.setEnabled(False)
        fb.addRow("Air margin (all faces)", self.spn_air)
        inner.addWidget(g_b)

        # --- Antenna simulation mode (FDTD antenna checklist) ---
        g_ant = QGroupBox("Antenna simulation")
        fant = QFormLayout(g_ant)
        self.chk_antenna = QCheckBox("Antenna simulation mode")
        self.chk_antenna.setToolTip(
            "Activates the antenna-oriented defaults of the FDTD checklist:\n"
            "• mesh density = the selection below, enforced at f_max (the highest\n"
            "  band frequency) and OVERRIDING the Mesh tab resolution while on\n"
            "• air margin = PML distance x λ(f_start) on all six faces\n"
            "  (overrides 'Air margin auto' while the mode is on)\n"
            "• NF2FF far-field enabled, box distance = NF2FF box x λ(f_start)\n"
            "  (only 'Fit to geometry' uses it; PCB runs keep the small box)\n"
            "See Help → Antenna Simulation for the full checklist."
        )
        fant.addRow(self.chk_antenna)
        self.cmb_ant_density = QComboBox()
        for _preset, _cells in (
            ("low", 10),
            ("medium", 15),
            ("high", 20),
            ("very_high", 25),
            ("extreme", 40),
        ):
            self.cmb_ant_density.addItem(f"{_preset} ({_cells} cells/λ)", _preset)
        self.cmb_ant_density.setCurrentIndex(2)  # high = 20 cells/λ
        self.cmb_ant_density.setToolTip(
            "Mesh preset used while antenna simulation mode is on.\n"
            "Rule of thumb: at least 15–20 cells per wavelength at f_max."
        )
        fant.addRow("Mesh density", self.cmb_ant_density)
        self.spn_ant_pml = QDoubleSpinBox()
        self.spn_ant_pml.setRange(0.25, 2.0)
        self.spn_ant_pml.setSingleStep(0.25)
        self.spn_ant_pml.setDecimals(2)
        self.spn_ant_pml.setValue(0.5)
        self.spn_ant_pml.setSuffix(" × λ")
        self.spn_ant_pml.setToolTip(
            "Distance between the structure and the PML, in wavelengths at\n"
            "f_start. Keep 0.50–1.00 (λ/2 … λ) — a smaller distance adds\n"
            "parasitic capacitance and pulls the resonance down."
        )
        fant.addRow("PML distance", self.spn_ant_pml)
        self.spn_ant_nf2ff = QDoubleSpinBox()
        self.spn_ant_nf2ff.setRange(0.25, 2.0)
        self.spn_ant_nf2ff.setSingleStep(0.25)
        self.spn_ant_nf2ff.setDecimals(2)
        self.spn_ant_nf2ff.setValue(0.5)
        self.spn_ant_nf2ff.setSuffix(" × λ")
        self.spn_ant_nf2ff.setToolTip(
            "Distance from the structure to the NF2FF far-field integration box,\n"
            "in wavelengths at f_start. Use ≥ 0.50 (λ/2) so the surface sits in\n"
            "the far field; it is clamped to stay inside the PML. Applied by\n"
            "'Fit to geometry + air margin' while antenna mode is on. PCB runs\n"
            "keep the small λ/4-at-f_stop box."
        )
        fant.addRow("NF2FF box", self.spn_ant_nf2ff)
        self.lbl_antenna_hint = QLabel(
            "ON: mesh preset ← selection above, air margin ← PML distance × λ(f_start) "
            "(all faces), NF2FF on with the box distance above. OFF: the normal "
            "air-margin controls above apply and the NF2FF box stays small "
            "(λ/4 at f_stop) for PCB-style runs. "
            "Also check substrate εr/tand for your target frequency — Help → Antenna Simulation."
        )
        self.lbl_antenna_hint.setWordWrap(True)
        fant.addRow(self.lbl_antenna_hint)
        inner.addWidget(g_ant)
        self._antenna_group = g_ant

        # --- Patch geometry ---
        g_p = QGroupBox("Patch antenna geometry")
        fp = QFormLayout(g_p)
        self.lbl_patch = QLabel("—")
        self.lbl_patch.setWordWrap(True)
        self._mono_font(self.lbl_patch)
        fp.addRow(self.lbl_patch)
        btn_row = QHBoxLayout()
        self.btn_edit_patch = QPushButton("Edit patch geometry…")
        self.btn_apply_patch = QPushButton("Apply patch (rebuild primitives)")
        btn_row.addWidget(self.btn_edit_patch)
        btn_row.addWidget(self.btn_apply_patch)
        btn_row.addStretch(1)
        fp.addRow(btn_row)
        inner.addWidget(g_p)
        self._patch_group = g_p

        # --- Effective settings (what will be applied) ---
        g_eff = QGroupBox("Effective settings for next Build / Run")
        fe = QFormLayout(g_eff)
        self.lbl_effective = QLabel("—")
        self.lbl_effective.setWordWrap(True)
        self._mono_font(self.lbl_effective)
        fe.addRow(self.lbl_effective)
        inner.addWidget(g_eff)
        inner.addStretch(1)
        return page

    def _build_nf2ff_tab(self) -> QWidget:
        """Tab 'NF2FF-Parameter': enable, evaluation frequencies, box + angles."""
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(4, 4, 4, 4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll, 1)
        body = QWidget()
        scroll.setWidget(body)
        inner = QVBoxLayout(body)
        inner.setContentsMargins(4, 4, 4, 4)

        # --- Enable ---
        g_en = QGroupBox("Far-field (NF2FF)")
        fen = QVBoxLayout(g_en)
        self.chk_nf2ff_enabled = QCheckBox("Enable far-field (NF2FF)")
        self.chk_nf2ff_enabled.setToolTip(
            "Create the NF2FF recording box and run the far-field post-processing "
            "(antenna pattern: directivity + gain)."
        )
        fen.addWidget(self.chk_nf2ff_enabled)
        inner.addWidget(g_en)

        # --- Evaluation frequencies ---
        g_fq = QGroupBox("Evaluation frequencies")
        fqf = QFormLayout(g_fq)
        self.cmb_nf2ff_mode = QComboBox()
        for label, _mode in NF2FF_FREQ_MODES:
            self.cmb_nf2ff_mode.addItem(label)
        self.cmb_nf2ff_mode.setToolTip(
            "Auto: one frequency at the S11 minimum.\n"
            "N points: equally spaced over the mesh frequency range "
            "(start/stop frequency from the Mesh tab).\n"
            "Frequency list: explicit frequencies entered below."
        )
        fqf.addRow("Mode", self.cmb_nf2ff_mode)
        self.spn_nf2ff_count = QSpinBox()
        self.spn_nf2ff_count.setRange(1, 500)
        self.spn_nf2ff_count.setValue(11)
        self.spn_nf2ff_count.setSuffix(" points")
        self.spn_nf2ff_count.setToolTip(
            "Number of evaluation frequencies, equally spaced over the mesh "
            "frequency range (start/stop frequency from the Mesh tab)."
        )
        fqf.addRow("Points", self.spn_nf2ff_count)
        self.txt_nf2ff_freqs = QLineEdit()
        self.txt_nf2ff_freqs.setPlaceholderText("e.g. 2.4 GHz, 2.45 GHz, 2500 MHz")
        self.txt_nf2ff_freqs.setToolTip(
            "Comma-separated frequencies; each entry may use Hz, kHz, MHz or GHz "
            "(bare number = Hz). Example: 2.4 GHz, 2.45e9, 2500 MHz"
        )
        fqf.addRow("Frequencies", self.txt_nf2ff_freqs)
        inner.addWidget(g_fq)

        # --- Box + angular resolution ---
        g_box = QGroupBox("NF2FF box (far-field)")
        fnf = QFormLayout(g_box)
        self.spn_nf2ff_theta = QSpinBox()
        self.spn_nf2ff_theta.setRange(2, 721)
        self.spn_nf2ff_theta.setValue(181)
        self.spn_nf2ff_phi = QSpinBox()
        self.spn_nf2ff_phi.setRange(2, 1441)
        self.spn_nf2ff_phi.setValue(361)
        fnf.addRow("NF2FF θ steps", self.spn_nf2ff_theta)
        fnf.addRow("NF2FF φ steps", self.spn_nf2ff_phi)
        self.btn_fit_nf2ff = QPushButton("Fit to geometry + air margin")
        self.btn_edit_nf2ff = QPushButton("Edit box…")
        nf_row = QHBoxLayout()
        nf_row.addWidget(self.btn_fit_nf2ff)
        nf_row.addWidget(self.btn_edit_nf2ff)
        nf_row.addStretch(1)
        fnf.addRow(nf_row)
        self.lbl_nf2ff = QLabel("—")
        self.lbl_nf2ff.setWordWrap(True)
        self._mono_font(self.lbl_nf2ff)
        fnf.addRow(self.lbl_nf2ff)
        inner.addWidget(g_box)
        inner.addStretch(1)
        return page

    def _current_freq_mode(self) -> str:
        label = self.cmb_nf2ff_mode.currentText()
        for lbl, mode in NF2FF_FREQ_MODES:
            if lbl == label:
                return mode
        return "auto"

    def _on_freq_mode_changed(self, *_args: Any) -> None:
        self._update_freq_mode_widgets()
        self._on_form_edited()

    def _update_freq_mode_widgets(self) -> None:
        """Enable count/list widgets according to the selected frequency mode."""
        mode = self._current_freq_mode()
        self.spn_nf2ff_count.setEnabled(mode == "count")
        self.txt_nf2ff_freqs.setEnabled(mode == "list")

    @staticmethod
    def _cell_spin() -> QDoubleSpinBox:
        sp = QDoubleSpinBox()
        sp.setRange(0.0, 1e6)
        sp.setDecimals(4)
        sp.setSpecialValueText("auto")
        sp.setValue(0.0)
        sp.setSuffix(" mm")
        return sp

    def _on_air_auto_toggled(self, checked: bool) -> None:
        # antenna mode owns the air margin while it is on
        self.spn_air.setEnabled(not checked and not self.chk_antenna.isChecked())

    def _on_antenna_toggled(self, checked: bool) -> None:
        """Antenna mode drives mesh preset, air margin and NF2FF."""
        self.cmb_ant_density.setEnabled(checked)
        self.spn_ant_pml.setEnabled(checked)
        self.spn_ant_nf2ff.setEnabled(checked)
        self.chk_air_auto.setEnabled(not checked)
        self.spn_air.setEnabled(not checked and not self.chk_air_auto.isChecked())
        if checked and not getattr(self, "_syncing", False):
            # programmatic (project) sync must not override a deliberate
            # "NF2FF off" — only a user click turns the far-field on.
            self.chk_nf2ff_enabled.setChecked(True)
        self.computed_changed.emit()

    def _sync_antenna_density(self, preset: str, density: float | None = None) -> None:
        """Mirror a mesh preset (or explicit density) into the density combo."""
        target = str(preset)
        if density:
            for name, cells in CELLS_PER_WAVELENGTH_BY_PRESET.items():
                if cells == float(density):
                    target = name
                    break
        for i in range(self.cmb_ant_density.count()):
            if str(self.cmb_ant_density.itemData(i) or "") == target:
                if self.cmb_ant_density.currentIndex() != i:
                    self.cmb_ant_density.blockSignals(True)
                    self.cmb_ant_density.setCurrentIndex(i)
                    self.cmb_ant_density.blockSignals(False)
                break

    def set_patch_group_visible(self, visible: bool) -> None:
        self._patch_visible = bool(visible)
        if hasattr(self, "_patch_group"):
            self._patch_group.setVisible(self._patch_visible)

    @property
    def patch_group_visible(self) -> bool:
        return bool(getattr(self, "_patch_visible", False))

    def _on_fit_nf2ff(self) -> None:
        self.computed_changed.emit()
        # MainWindow handles fit using primitives + mesh air margin

    def _on_edit_nf2ff(self) -> None:
        self.computed_changed.emit()

    def _on_form_edited(self, *_args: Any) -> None:
        # Parent refreshes computed readout (mesh tab f0/fc must stay in sync).
        self.computed_changed.emit()

    # --- project <-> form ---

    def write_to_project(self, project: Project) -> None:
        m: MeshSpec = project.mesh
        # the spin is in dB (e.g. -50) -> store the linear energy ratio (1e-5)
        project.sim.end_criteria = float(10.0 ** (float(self.spn_end_criteria.value()) / 10.0))
        project.sim.max_timesteps = int(self.spn_max_ts.value())
        project.sim.num_threads = int(self.spn_num_threads.value())
        b: BoundarySpec = project.boundary
        for axis, cb in self.cmb_b.items():
            setattr(b, axis, cb.currentText())  # type: ignore[arg-type]
        b.pml_size = int(self.spn_pml.value())
        if self.chk_antenna.isChecked():
            # Antenna mode owns the air margin: PML distance x λ(f_start),
            # all six faces. Mirror it into the (disabled) auto/fixed
            # controls so the form always shows what will be stored.
            margin = self._antenna_air_units(project)
            with QSignalBlocker(self.chk_air_auto), QSignalBlocker(self.spn_air):
                self.chk_air_auto.setChecked(False)
                self.spn_air.setValue(margin)
            b.air_margin = [margin] * 6
        elif self.chk_air_auto.isChecked():
            b.air_margin = ["auto"] * 6
        else:
            val = float(self.spn_air.value())
            b.air_margin = [val] * 6
        # Antenna simulation mode: persist the flag + its two knobs.
        project.sim.antenna_mode = bool(self.chk_antenna.isChecked())
        project.sim.antenna_pml_lambda = float(self.spn_ant_pml.value())
        project.sim.antenna_nf2ff_lambda = float(self.spn_ant_nf2ff.value())
        if project.sim.antenna_mode:
            preset = self.cmb_ant_density.currentData()
            if preset:
                m.preset = preset  # type: ignore[assignment]
                # Persist the density too: Project.effective_mesh() uses it to
                # override the mesh resolution (reference wavelength = f_max).
                project.sim.antenna_cells_per_wavelength = float(
                    CELLS_PER_WAVELENGTH_BY_PRESET.get(str(preset), 15.0)
                )
        project.nf2ff.theta_steps = int(self.spn_nf2ff_theta.value())
        project.nf2ff.phi_steps = int(self.spn_nf2ff_phi.value())
        nf_on = self.chk_nf2ff_enabled.isChecked()
        project.sim.nf2ff_enabled = nf_on
        project.nf2ff.enabled = nf_on
        project.nf2ff.freq_mode = self._current_freq_mode()
        project.nf2ff.freq_count = int(self.spn_nf2ff_count.value())
        project.nf2ff.frequencies = parse_freq_list(self.txt_nf2ff_freqs.text())
        # Only persist patch params when the patch preset group is active
        if getattr(self, "_patch_visible", False):
            project.patch_params = dict(self.patch_params)

    def _antenna_air_units(self, project: Project) -> float:
        """Air margin for antenna mode: PML distance x λ(f_start), drawing units.

        The FDTD hints require λ/2 … λ at the LOWEST band frequency between the
        structure and the PML (the λ/4-at-f_stop auto rule is smaller).
        """
        from app.meshing.rules import wavelength_m

        m = project.mesh
        f_start = min(float(m.start_frequency or 0.0), float(m.stop_frequency or 0.0))
        if f_start <= 0.0:
            f_start = 1e9
        frac = max(0.0, float(self.spn_ant_pml.value()))
        return wavelength_m(f_start, eps_r=1.0) / m.unit_to_meters() * frac

    def sync_from_project(self, project: Project, mesh_preset: str | None = None) -> None:
        m = project.mesh
        del mesh_preset  # reserved for future mesh-tab coupling
        self._syncing = True
        try:
            self._sync_from_project_impl(project, m)
        finally:
            self._syncing = False

    def _apply_coord_system_ui(self, coord_system: str) -> None:
        """Cylindrical mode: ρ/φ/z boundary labels, disabled φ faces, NF2FF gate."""
        polar = coord_system == "cylindrical"
        axes = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
        labels = ("ρ−", "ρ+", "φ−", "φ+", "z−", "z+") if polar else axes
        for axis, text in zip(axes, labels):
            lbl = getattr(self, "lbl_b", {}).get(axis)
            if lbl is not None:
                lbl.setText(text)
            cb = self.cmb_b.get(axis)
            if cb is not None:
                phi_face = axis in ("ymin", "ymax")
                cb.setEnabled(not (polar and phi_face))
                cb.setToolTip(
                    "φ faces do not exist in cylindrical mode "
                    "(closed full-turn cylinder)"
                    if polar and phi_face
                    else ""
                )
        nf_tip = (
            "NF2FF far-field post-processing is not supported in cylindrical mode "
            "(S-params and field dumps still work)."
            if polar
            else ""
        )
        self.chk_nf2ff_enabled.setEnabled(not polar)
        self.btn_fit_nf2ff.setEnabled(not polar)
        self.btn_edit_nf2ff.setEnabled(not polar)
        self.chk_nf2ff_enabled.setToolTip(nf_tip)
        self.btn_fit_nf2ff.setToolTip(nf_tip)
        self.btn_edit_nf2ff.setToolTip(nf_tip)
        if polar:
            with QSignalBlocker(self.chk_nf2ff_enabled):
                self.chk_nf2ff_enabled.setChecked(False)
        self.spn_air.setToolTip(
            "Cylindrical mode: only ρ+ and z−/z+ take air margin "
            "(ρ− is the r=0 axis, φ± are unused)."
            if polar
            else "Air margin in drawing units on all six faces (auto = λ/4)."
        )

    def _sync_from_project_impl(self, project: Project, m: MeshSpec) -> None:
        # spin is in dB: linear energy ratio -> dB
        _ec = float(project.sim.end_criteria or 1e-5)
        self.spn_end_criteria.setValue(
            10.0 * math.log10(_ec) if _ec > 0 else -50.0
        )
        self.spn_max_ts.setValue(int(project.sim.max_timesteps or 200_000))
        self.spn_num_threads.setValue(max(0, int(getattr(project.sim, "num_threads", 0) or 0)))
        b = project.boundary
        for axis, cb in self.cmb_b.items():
            kind = str(getattr(b, axis, "PML"))
            if kind not in BOUNDARY_KINDS:
                kind = "PML"
            cb.setCurrentText(kind)
        self.spn_pml.setValue(int(b.pml_size or 8))
        air = list(b.air_margin or ["auto"] * 6)
        is_auto = all(a == "auto" or a is None for a in air)
        self.chk_air_auto.setChecked(is_auto)
        if not is_auto:
            try:
                self.spn_air.setValue(float(air[0]))
            except (TypeError, ValueError):
                self.spn_air.setValue(20.0)
        self.spn_nf2ff_theta.setValue(int(project.nf2ff.theta_steps or 181))
        self.spn_nf2ff_phi.setValue(int(project.nf2ff.phi_steps or 361))
        self.chk_nf2ff_enabled.setChecked(
            bool(project.sim.nf2ff_enabled and project.nf2ff.enabled)
        )
        mode = str(getattr(project.nf2ff, "freq_mode", "auto") or "auto").lower()
        label = next(
            (lbl for lbl, m in NF2FF_FREQ_MODES if m == mode),
            NF2FF_FREQ_MODES[0][0],
        )
        self.cmb_nf2ff_mode.setCurrentText(label)
        self.spn_nf2ff_count.setValue(
            int(getattr(project.nf2ff, "freq_count", 11) or 11)
        )
        freqs = list(project.nf2ff.frequencies or [])
        self.txt_nf2ff_freqs.setText(", ".join(fmt_hz(f) for f in freqs))
        self._update_freq_mode_widgets()
        if project.patch_params:
            self.patch_params = dict(DEFAULT_PATCH_PARAMS)
            self.patch_params.update({k: float(v) for k, v in project.patch_params.items()})
        self._refresh_patch_label()
        # --- antenna simulation mode (last: it gates the air-margin controls) ---
        self.spn_ant_pml.setValue(float(getattr(project.sim, "antenna_pml_lambda", 0.5) or 0.5))
        self.spn_ant_nf2ff.setValue(
            float(getattr(project.sim, "antenna_nf2ff_lambda", 0.5) or 0.5)
        )
        self._sync_antenna_density(
            m.preset, getattr(project.sim, "antenna_cells_per_wavelength", None)
        )
        self.chk_antenna.setChecked(bool(getattr(project.sim, "antenna_mode", False)))
        self._on_antenna_toggled(self.chk_antenna.isChecked())
        self._apply_coord_system_ui(
            str(getattr(m, "coord_system", "cartesian") or "cartesian")
        )
        self.refresh_computed()

    def build_temp_project(self, base: Project | None = None) -> Project:
        """Project clone with form values applied (for computed readout)."""
        import copy

        p = copy.deepcopy(base) if base is not None else Project.new_default("tmp")
        self.write_to_project(p)
        return p

    def refresh_computed(self, project: Project | None = None) -> None:
        p = self.build_temp_project(project)
        try:
            targets = compute_targets(p.mesh)
            air = air_margin_units(p.mesh, p.boundary.air_margin, targets)
            auto_air = air_margin_units(p.mesh, ["auto"] * 6, targets)[0]
            f0 = p.mesh.target_frequency or 0.5 * (
                p.mesh.start_frequency + p.mesh.stop_frequency
            )
            fc = max(1e6, 0.5 * abs(p.mesh.stop_frequency - p.mesh.start_frequency))
            lines = [
                f"f_max          = {_fmt_hz(targets.f_max_hz)}",
                f"λ_air(f_max)   = {_fmt_len(targets.lambda_min_units)}",
                f"max cell λ/10  = {_fmt_len(targets.max_cell_units)}",
                f"target cell    = {_fmt_len(targets.target_cell_units)} (preset={p.mesh.preset})",
                f"air margin auto= {_fmt_len(auto_air)}  (λ/4, εr=1)",
                f"air margin used= {', '.join(_fmt_len(a) if not isinstance(a, str) else a for a in air)}",
                f"start f        = {_fmt_hz(p.mesh.start_frequency)}  (user-settable on Mesh tab)",
                f"stop f         = {_fmt_hz(p.mesh.stop_frequency)}  (user-settable on Mesh tab)",
                f"f0 / fc        = {_fmt_hz(p.mesh.target_frequency or 0)} / {_fmt_hz(fc)}  (excitation)",
                f"min/max cell   = {_fmt_len(p.mesh.min_cellsize) if p.mesh.min_cellsize else 'auto'} / "
                f"{_fmt_len(p.mesh.max_cellsize) if p.mesh.max_cellsize else 'auto'}",
            ]
            self.lbl_computed.setText("\n".join(lines))

            eff = [
                f"Gauss excite   = f0 {_fmt_hz(f0)}, fc {_fmt_hz(fc)}",
                f"NrTS / EndCrit = {p.sim.max_timesteps} / "
                f"{10.0 * math.log10(p.sim.end_criteria):.1f} dB",
                f"Threads        = {int(getattr(p.sim, 'num_threads', 0) or 0) or 'auto (all cores)'}",
                f"Boundary       = {p.boundary.as_openems_list()}",
                f"Air margin     = {', '.join(_fmt_len(a) for a in air)}",
                f"Smooth edges   = {p.mesh.smooth_metal_edge}, grading≤{p.mesh.max_grading_ratio}",
                f"NF2FF          = {'on' if p.nf2ff.enabled and p.sim.nf2ff_enabled else 'off'} "
                f"box {p.nf2ff.start} → {p.nf2ff.stop}, θ={p.nf2ff.theta_steps} φ={p.nf2ff.phi_steps}, "
                f"eval: {self._fmt_eval_freqs(p)}",
                f"Field dumps    = {'on' if p.sim.field_dumps_enabled else 'off'}",
            ]
            if p.patch_params:
                eff.append(
                    f"Patch preset   = W×L={p.patch_params.get('patch_width')}×"
                    f"{p.patch_params.get('patch_length')} mm, εr={p.patch_params.get('substrate_eps')}, "
                    f"feed={p.patch_params.get('feed_pos')} mm"
                )
            else:
                eff.append("Patch preset   = inactive (generic geometry workflow)")
            self.lbl_effective.setText("\n".join(eff))
            nf = p.nf2ff
            self.lbl_nf2ff.setText(
                f"{'on' if nf.enabled and p.sim.nf2ff_enabled else 'off'}  "
                f"{nf.start} → {nf.stop}\ncenter={nf.center}  "
                f"θ={nf.theta_steps} φ={nf.phi_steps}\n"
                f"eval freqs: {self._fmt_eval_freqs(p)}"
            )
        except Exception as e:  # noqa: BLE001 — display only
            self.lbl_computed.setText(f"Computed values unavailable: {e}")
            self.lbl_effective.setText(f"Effective settings unavailable: {e}")

    @staticmethod
    def _fmt_eval_freqs(p: Project) -> str:
        """Human-readable evaluation frequency description for readouts."""
        nf = p.nf2ff
        mode = str(getattr(nf, "freq_mode", "auto") or "auto").lower()
        if mode == "count":
            n = int(getattr(nf, "freq_count", 0) or 0)
            return f"{n} x {fmt_hz(p.mesh.start_frequency)}…{fmt_hz(p.mesh.stop_frequency)}"
        if mode == "list" and nf.frequencies:
            return ", ".join(fmt_hz(f) for f in nf.frequencies)
        return "auto (S11 minimum)"

    def _refresh_patch_label(self) -> None:
        d = self.patch_params
        self.lbl_patch.setText(
            f"W={d['patch_width']} mm, L={d['patch_length']} mm, "
            f"εr={d['substrate_eps']}, h={d['substrate_thickness']} mm, "
            f"ground={d['ground_thickness']} mm, feed={d['feed_pos']} mm, R={d['feed_r']} Ω, "
            f"f0={_fmt_hz(d['f0'])}, fc={_fmt_hz(d['fc'])}"
        )

    def _on_edit_patch(self) -> None:
        dlg = PatchParamsDialog(self.patch_params, self)
        if dlg.exec():
            self.patch_params = dlg.values
            self._refresh_patch_label()
            self.computed_changed.emit()

    def _on_apply_patch(self) -> None:
        self.computed_changed.emit()
        # MainWindow listens and rebuilds primitives from patch_params


class PatchParamsDialog(QDialog):
    def __init__(self, params: dict[str, float], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Patch antenna geometry")
        self.resize(420, 480)
        self.values = dict(params)
        form = QFormLayout(self)
        self._spins: dict[str, QDoubleSpinBox] = {}

        def row(key: str, label: str, lo: float, hi: float, dec: int = 3, step: float = 0.1) -> None:
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setDecimals(dec)
            sp.setSingleStep(step)
            sp.setValue(float(self.values.get(key, 0.0)))
            self._spins[key] = sp
            form.addRow(label, sp)

        row("patch_width", "Patch width (mm)", 0.1, 500.0)
        row("patch_length", "Patch length (mm)", 0.1, 500.0)
        row("substrate_eps", "Substrate εr", 1.0, 50.0, 4, 0.01)
        row("substrate_width", "Substrate width (mm)", 1.0, 500.0)
        row("substrate_length", "Substrate length (mm)", 1.0, 500.0)
        row("substrate_thickness", "Substrate thickness (mm)", 0.01, 50.0, 4, 0.01)
        row("ground_thickness", "Ground thickness (mm)", 0.001, 5.0, 4, 0.001)
        row("feed_pos", "Feed position x (mm)", -200.0, 200.0, 3, 0.1)
        row("feed_r", "Port resistance (Ω)", 1.0, 500.0, 2, 1.0)

        # Frequencies: display in Hz/kHz/MHz/GHz; store Hz
        from app.ui.freq_units import (
            FREQ_UNITS,
            configure_freq_spin,
            hz_to_freq,
            spin_hz,
            unit_combo_items,
        )

        self.cmb_freq_unit = QComboBox()
        unit_combo_items(self.cmb_freq_unit)
        self.cmb_freq_unit.setCurrentText("GHz")
        form.addRow("Frequency unit", self.cmb_freq_unit)

        self.spn_f0 = QDoubleSpinBox()
        configure_freq_spin(self.spn_f0, "GHz")
        self.spn_f0.setValue(hz_to_freq(float(self.values.get("f0", 2.45e9)), "GHz"))
        form.addRow("Center frequency f0", self.spn_f0)
        self.spn_fc = QDoubleSpinBox()
        configure_freq_spin(self.spn_fc, "GHz")
        self.spn_fc.setValue(hz_to_freq(float(self.values.get("fc", 0.5e9)), "GHz"))
        form.addRow("Corner frequency fc", self.spn_fc)

        def _on_unit(unit: str) -> None:
            if unit not in FREQ_UNITS:
                return
            f0_hz = spin_hz(self.spn_f0, self._unit_prev)
            fc_hz = spin_hz(self.spn_fc, self._unit_prev)
            self._unit_prev = unit
            configure_freq_spin(self.spn_f0, unit)
            configure_freq_spin(self.spn_fc, unit)
            self.spn_f0.setValue(hz_to_freq(f0_hz, unit))
            self.spn_fc.setValue(hz_to_freq(fc_hz, unit))

        self._unit_prev = "GHz"
        self.cmb_freq_unit.currentTextChanged.connect(_on_unit)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        from app.ui.freq_units import spin_hz

        for key, sp in self._spins.items():
            self.values[key] = float(sp.value())
        unit = getattr(self, "_unit_prev", "GHz") or "GHz"
        self.values["f0"] = spin_hz(self.spn_f0, unit)
        self.values["fc"] = spin_hz(self.spn_fc, unit)
        self.accept()


class SimParamsDialog(QDialog):
    """Standalone window with all simulation parameters (Simulation menu).

    Hosts its own :class:`SimParamsPanel`. ``Apply`` emits
    :attr:`apply_requested` — MainWindow writes the values into the project
    and mirrors them into the hidden Sim Params panel (run/save re-sync from
    that panel). ``Close`` discards everything not applied.
    """

    apply_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Simulation Parameters")
        self.setModal(False)
        self.resize(620, 760)
        self._project: Project | None = None

        root = QVBoxLayout(self)
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Simulation Parameters")
        title.setStyleSheet("font-weight: bold;")
        hl.addWidget(title)
        hl.addStretch(1)
        help_btn = QPushButton("Help")
        help_btn.setToolTip("Help for the Simulation Parameters window")
        help_btn.clicked.connect(
            lambda _=False, p=self: show_tab_help(p, "Simulation Parameters")
        )
        hl.addWidget(help_btn)
        root.addWidget(header)
        hint = QLabel(
            "Two tabs: <b>Simulation-Parameter</b> (FDTD / mesh / boundary) and "
            "<b>NF2FF-Parameter</b> (far-field box + evaluation frequencies). "
            "<b>Apply</b> writes them into the project (and mirrors them to the "
            "hidden Sim Params panel) and refreshes work/project_run.py when it "
            "already exists; <b>Close</b> discards unapplied changes."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.panel = SimParamsPanel()
        root.addWidget(self.panel, 1)

        bb = QDialogButtonBox(self)
        self.btn_apply = bb.addButton(
            "Apply", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.btn_close = bb.addButton(QDialogButtonBox.StandardButton.Close)
        root.addWidget(bb)
        bb.clicked.connect(self._on_button_clicked)
        self.panel.computed_changed.connect(self._refresh_computed)

    # --- data flow -----------------------------------------------------
    def load(
        self,
        project: Project,
        *,
        patch_visible: bool = False,
        patch_params: dict[str, float] | None = None,
    ) -> None:
        """Sync the form from the project (called on every open)."""
        self._project = project
        self.panel.sync_from_project(project)
        self.panel.set_patch_group_visible(patch_visible)
        if patch_params:
            self.panel.patch_params = dict(patch_params)
            self.panel._refresh_patch_label()
        self.panel.refresh_computed(project)

    def _refresh_computed(self) -> None:
        if self._project is not None:
            self.panel.refresh_computed(self._project)

    def _on_button_clicked(self, btn) -> None:  # noqa: ANN001
        if btn is self.btn_apply:
            self.apply_requested.emit()
        else:
            self.close()
