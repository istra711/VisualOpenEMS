"""Solver Parameters dialog — openEMS init + Run() options.

Mirrors the options described in the openEMS "Simulation" concepts page:
the FDTD initialization (EndCriteria, NrTS, MaxTime, OverSampling,
TimeStepFactor/Method, CellConstantMaterial) and the solver ``Run()`` keywords
(numThreads, engine, verbose, disable-dumps, the debug dumps, dump-statistics,
showProbeDiscretization, legacyHDF5Dumps, setup_only, cleanup).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project

ENGINES = ["", "fastest", "basic", "sse", "sse-compressed", "multithreaded"]
#: Run() keyword -> SimSpec attribute for the boolean debug/option flags
_BOOL_FLAGS = [
    ("cleanup", "cleanup", "Wipe old output files before running"),
    ("setup_only", "setup_only", "Pre-process only (no FDTD run, no results)"),
    ("disable_dumps", "disable_dumps", "Ignore all field dumps (faster)"),
    ("dump_statistics", "dump_statistics", "Write openEMS_*_stats.txt"),
    ("debug_material", "debug_material", "Write the discretized material distribution (VTK)"),
    ("debug_pec", "debug_pec", "Write the discretized metal distribution (VTK)"),
    ("debug_boxes", "debug_boxes", "Write probe/dump boxes to file"),
    ("debug_operator", "debug_operator", "Write the FDTD operator to file"),
    ("debug_csx", "debug_csx", "Write the parsed geometry (debugCSX.xml)"),
    ("show_probe_discretization", "show_probe_discretization", "Report where probes snapped to the grid"),
    ("legacy_hdf5_dumps", "legacy_hdf5_dumps", "HDF5 dumps with reversed axis order"),
    ("cell_constant_material", "cell_constant_material", "Assume material constant inside a cell"),
]


class SolverParamsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, project: Project | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Solver Parameters")
        self.resize(460, 620)
        self._project = project
        sim = project.sim if project is not None else None

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll, 1)
        body = QWidget()
        scroll.setWidget(body)
        root = QVBoxLayout(body)

        # --- Initialization ---
        g_init = QGroupBox("FDTD initialization")
        fi = QFormLayout(g_init)
        self.spn_over = QSpinBox()
        self.spn_over.setRange(0, 100)
        self.spn_over.setSpecialValueText("auto")
        self.spn_over.setToolTip("openEMS(OverSampling=…): Nyquist oversampling of time dumps.")
        self.spn_tsf = QDoubleSpinBox()
        self.spn_tsf.setRange(0.0, 1.0)
        self.spn_tsf.setDecimals(4)
        self.spn_tsf.setSingleStep(0.05)
        self.spn_tsf.setSpecialValueText("auto")
        self.spn_tsf.setToolTip(
            "openEMS(TimeStepFactor=…): reduce the (CFL/Rennings) timestep by this\n"
            "factor (0<x<=1). Only ever reduces it — for marginally stable runs."
        )
        self.cmb_tsm = QComboBox()
        self.cmb_tsm.addItems(["auto", "1 (CFL)", "3 (Rennings2)"])
        self.cmb_tsm.setToolTip("openEMS(TimeStepMethod=…).")
        self.spn_max_time = QDoubleSpinBox()
        self.spn_max_time.setRange(0.0, 1e6)
        self.spn_max_time.setDecimals(3)
        self.spn_max_time.setSpecialValueText("none")
        self.spn_max_time.setSuffix(" ns")
        self.spn_max_time.setToolTip(
            "SetMaxTime(): cap the *simulated* time (not wall-clock). Sets the\n"
            "frequency resolution df = 1/T."
        )
        fi.addRow("OverSampling", self.spn_over)
        fi.addRow("TimeStepFactor", self.spn_tsf)
        fi.addRow("TimeStepMethod", self.cmb_tsm)
        fi.addRow("Max simulated time", self.spn_max_time)
        root.addWidget(g_init)

        # --- Run options ---
        g_run = QGroupBox("Solver Run() options")
        fr = QFormLayout(g_run)
        self.spn_verbose = QSpinBox()
        self.spn_verbose.setRange(0, 3)
        self.cmb_engine = QComboBox()
        self.cmb_engine.addItems([e or "(default: fastest)" for e in ENGINES])
        self.cmb_engine.setToolTip("Run(engine=…): mainly for benchmarking / verification.")
        fr.addRow("Verbose (0-3)", self.spn_verbose)
        fr.addRow("Engine", self.cmb_engine)
        note = QLabel("Threads (numThreads) are set in Simulation Parameters.")
        note.setStyleSheet("color:#666;")
        fr.addRow(note)
        root.addWidget(g_run)

        # --- Boolean flags ---
        g_flags = QGroupBox("Options / debug")
        vf = QVBoxLayout(g_flags)
        self._flags: dict[str, QCheckBox] = {}
        for _key, attr, tip in _BOOL_FLAGS:
            cb = QCheckBox(attr.replace("_", "-"))
            cb.setToolTip(f"Run({_key}=…)\n{tip}")
            self._flags[attr] = cb
            vf.addWidget(cb)
        root.addWidget(g_flags)

        lbl = QLabel(
            "Defaults reproduce the normal behaviour. 'Cleanup' wipes old outputs;\n"
            "the debug-* options write extra VTK/XML files for troubleshooting."
        )
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#666;")
        root.addWidget(lbl)
        root.addStretch(1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        from PySide6.QtWidgets import QPushButton

        btn_help = QPushButton("Help")
        btn_help.setToolTip("Help for the Solver Parameters")
        from app.ui.tab_help import show_tab_help

        btn_help.clicked.connect(
            lambda _=False, p=self: show_tab_help(p, "Solver Parameters")
        )
        bb.addButton(btn_help, QDialogButtonBox.ButtonRole.HelpRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self._load(sim)

    def _load(self, sim) -> None:
        g = lambda k, d: getattr(sim, k, d)  # noqa: E731
        self.spn_over.setValue(int(g("over_sampling", 0) or 0))
        self.spn_tsf.setValue(float(g("time_step_factor", 0.0) or 0.0))
        tsm = g("time_step_method", None)
        self.cmb_tsm.setCurrentText({1: "1 (CFL)", 3: "3 (Rennings2)"}.get(int(tsm) if tsm else 0, "auto"))
        self.spn_max_time.setValue(float(g("max_time", 0.0) or 0.0) * 1e9)
        self.spn_verbose.setValue(int(g("verbose", 3) or 0))
        eng = str(g("engine", "") or "")
        self.cmb_engine.setCurrentText(eng or "(default: fastest)")
        for _key, attr, _tip in _BOOL_FLAGS:
            self._flags[attr].setChecked(bool(g(attr, _key == "cleanup")))

    def _accept(self) -> None:
        s = self._project.sim if self._project is not None else None
        if s is not None:
            s.over_sampling = int(self.spn_over.value()) or None
            s.time_step_factor = float(self.spn_tsf.value()) or None
            tsm = self.cmb_tsm.currentText()
            s.time_step_method = 1 if tsm.startswith("1") else (3 if tsm.startswith("3") else None)
            s.max_time = (float(self.spn_max_time.value()) * 1e-9) or None
            s.verbose = int(self.spn_verbose.value())
            eng = self.cmb_engine.currentText()
            s.engine = "" if eng.startswith("(") else eng
            for _key, attr, _tip in _BOOL_FLAGS:
                setattr(s, attr, bool(self._flags[attr].isChecked()))
        self.accept()
