"""Configure the project-level variable sweep (Phase A)."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
from app.models.sweep import SweepSpec
from app.numfmt import parse_number
from app.ui.tab_help import show_tab_help

_MAX_POINTS = 200


def _number(text: str) -> float | None:
    value = parse_number(str(text or "").strip())
    return None if value is None else float(value)


def _split_values(text: str) -> list[str]:
    return [part for part in re.split(r"[,;\s]+", str(text or "").strip()) if part]


class SweepDialog(QDialog):
    """Edit one project-level sweep definition and validate it against usage."""

    def __init__(
        self,
        project: Project,
        primitives: list[dict] | None = None,
        *,
        usage: dict[str, int] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self._usage = {
            str(k).casefold(): int(v) for k, v in (usage or {}).items()
        }
        self._values: list[float] = []
        self._values_error = ""
        self.setWindowTitle("Variable sweep")
        # Start compact: the long explanations are word-wrapped and the input
        # fields stay at their natural width.  Users can still widen the
        # dialog if they prefer a wider warning block.
        self.setMinimumWidth(430)
        self.resize(460, 720)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Sweep one geometry variable or a port excitation phase and store "
            "all points in one staged run. Each point gets its own project "
            "snapshot; the project itself is not changed."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        self.chk_enabled = QCheckBox("Sweep enabled")
        self.chk_enabled.setChecked(project.sweep is None)
        self.chk_enabled.setToolTip(
            "Disabled sweeps stay in the project but cannot be run."
        )
        form.addRow(self.chk_enabled)
        self.cmb_target_kind = QComboBox()
        self.cmb_target_kind.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.cmb_target_kind.setMinimumContentsLength(16)
        self.cmb_target_kind.addItem("Geometry variable", "variable")
        self.cmb_target_kind.addItem("Port excitation phase", "port_phase")
        self.cmb_target_kind.setToolTip(
            "Geometry variable: changes the model and rebuilds the mesh.\n"
            "Port excitation phase: keeps geometry/mesh unchanged; use this "
            "to animate antenna patterns (NF2FF is required)."
        )
        self.cmb_target_kind.setMaximumWidth(230)
        form.addRow("Target", self.cmb_target_kind)
        self.cmb_variable = QComboBox()
        self.cmb_variable.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.cmb_variable.setMinimumContentsLength(14)
        for variable in project.variables:
            name = str(getattr(variable, "name", "") or "").strip()
            if name:
                self.cmb_variable.addItem(name)
        self.cmb_variable.setToolTip(
            "Named project variable. Dependent variables are re-evaluated for "
            "every point. Formula variables are overridden per point."
        )
        self.cmb_variable.setMaximumWidth(230)
        form.addRow("Variable", self.cmb_variable)
        self.cmb_port = QComboBox()
        self.cmb_port.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.cmb_port.setMinimumContentsLength(14)
        for port in project.ports:
            if bool(getattr(port, "active", True)):
                name = str(getattr(port, "name", "") or "").strip()
                if name:
                    self.cmb_port.addItem(name)
        self.cmb_port.setToolTip(
            "Active port whose excitation phase is swept. For a meaningful "
            "pattern animation, excite several ports simultaneously and vary "
            "their relative phase."
        )
        self.cmb_port.setMaximumWidth(230)
        form.addRow("Port", self.cmb_port)
        unit = str(getattr(project.mesh, "drawing_unit", "mm") or "mm")
        self.lbl_unit = QLabel(f"Drawing unit: {unit}")
        form.addRow("Unit", self.lbl_unit)
        root.addLayout(form)

        values_box = QGroupBox("Values")
        values_layout = QVBoxLayout(values_box)
        mode_row = QVBoxLayout()
        self.rad_range = QRadioButton("Start / stop / points")
        self.rad_list = QRadioButton("Explicit value list")
        self.rad_range.setChecked(True)
        mode_row.addWidget(self.rad_range)
        mode_row.addWidget(self.rad_list)
        values_layout.addLayout(mode_row)

        self.range_widget = QWidget()
        range_form = QFormLayout(self.range_widget)
        range_form.setContentsMargins(0, 0, 0, 0)
        range_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        self.ed_start = QLineEdit()
        self.ed_start.setPlaceholderText("e.g. 10")
        self.ed_stop = QLineEdit()
        self.ed_stop.setPlaceholderText("e.g. 14")
        self.ed_points = QLineEdit()
        self.ed_points.setPlaceholderText("e.g. 5")
        self.ed_points.setText("5")
        for edit in (self.ed_start, self.ed_stop, self.ed_points):
            edit.setAlignment(Qt.AlignmentFlag.AlignLeft)
            edit.setMaximumWidth(150)
        range_form.addRow("Start", self.ed_start)
        range_form.addRow("Stop", self.ed_stop)
        range_form.addRow("Points", self.ed_points)
        values_layout.addWidget(self.range_widget)

        self.list_widget = QWidget()
        list_form = QFormLayout(self.list_widget)
        list_form.setContentsMargins(0, 0, 0, 0)
        list_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint
        )
        self.ed_list = QLineEdit()
        self.ed_list.setPlaceholderText("e.g. 10, 12, 13.5, 15")
        self.ed_list.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.ed_list.setMaximumWidth(260)
        list_form.addRow("Values", self.ed_list)
        values_layout.addWidget(self.list_widget)
        self.list_widget.setVisible(False)
        root.addWidget(values_box)

        options = QGroupBox("Run options")
        options_layout = QVBoxLayout(options)
        nports = len([p for p in project.ports if bool(getattr(p, "active", True))])
        self.chk_full = QCheckBox("Full S-matrix per point")
        self.chk_full.setChecked(nports >= 2)
        self.chk_full.setEnabled(nports >= 2)
        self.chk_full.setToolTip(
            f"{nports} active port(s). Runs one excitation per active port "
            "for every sweep point and merges the S-parameter columns."
        )
        if nports < 2:
            self.chk_full.setToolTip(
                "The project has fewer than two active ports; one run per "
                "point is enough."
            )
        options_layout.addWidget(self.chk_full)
        self.chk_nf2ff = QCheckBox("Collect NF2FF per point")
        self.chk_nf2ff.setChecked(False)
        options_layout.addWidget(self.chk_nf2ff)
        self.chk_fields = QCheckBox("Collect field dumps per point")
        self.chk_fields.setChecked(False)
        self.chk_fields.setToolTip(
            "Field dumps can become very large over a sweep. Off by default."
        )
        options_layout.addWidget(self.chk_fields)
        self.chk_continue = QCheckBox("Continue after failed point")
        self.chk_continue.setChecked(True)
        options_layout.addWidget(self.chk_continue)
        root.addWidget(options)

        self.txt_warning = QPlainTextEdit()
        self.txt_warning.setReadOnly(True)
        self.txt_warning.setFont(self.font())
        self.txt_warning.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.txt_warning.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.txt_warning.setFixedHeight(160)
        self.txt_warning.setToolTip(
            "Validation result and workload estimate. Scroll for the full "
            "explanation; the Help button opens the complete topic."
        )
        self.txt_warning.setStyleSheet(
            "QPlainTextEdit { color: #8a4b00; background: #fff7e0; "
            "border: 1px solid #e0bd7a; padding: 6px; }"
        )
        root.addWidget(self.txt_warning)

        self.bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btn_help = self.bb.addButton(
            "Help", QDialogButtonBox.ButtonRole.HelpRole
        )
        btn_help.setToolTip(
            "Detailed explanation of targets, mesh impact, run counts and "
            "antenna-pattern animation"
        )
        btn_help.clicked.connect(
            lambda: show_tab_help(self, "Variable Sweep")
        )
        root.addWidget(self.bb)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)

        self.rad_range.toggled.connect(self._on_mode_changed)
        self.rad_list.toggled.connect(self._on_mode_changed)
        for edit in (self.ed_start, self.ed_stop, self.ed_points, self.ed_list):
            edit.textChanged.connect(self._refresh)
        self.cmb_variable.currentTextChanged.connect(self._refresh)
        self.cmb_port.currentTextChanged.connect(self._refresh)
        self.cmb_target_kind.currentIndexChanged.connect(
            self._on_target_kind_changed
        )
        self.chk_enabled.toggled.connect(self._refresh)
        self.chk_full.toggled.connect(self._refresh)
        self.chk_nf2ff.toggled.connect(self._refresh)
        self.chk_fields.toggled.connect(self._refresh)
        self.chk_continue.toggled.connect(self._refresh)

        self._load_spec(project.sweep)
        self._on_mode_changed()
        self._on_target_kind_changed()
        self._refresh()

    def _load_spec(self, spec: SweepSpec | None) -> None:
        if spec is None:
            return
        self.chk_enabled.setChecked(bool(spec.enabled))
        kind = str(spec.target_kind or "variable")
        kind_index = self.cmb_target_kind.findData(kind)
        if kind_index >= 0:
            self.cmb_target_kind.setCurrentIndex(kind_index)
        target = spec.target_name()
        if spec.is_phase():
            index = self.cmb_port.findText(target)
            if index >= 0:
                self.cmb_port.setCurrentIndex(index)
        else:
            index = self.cmb_variable.findText(target)
            if index >= 0:
                self.cmb_variable.setCurrentIndex(index)
        if spec.values:
            self.rad_list.setChecked(True)
            self.ed_list.setText(", ".join(f"{v:g}" for v in spec.values))
        self.chk_full.setChecked(bool(spec.full_smatrix) and self.chk_full.isEnabled())
        self.chk_nf2ff.setChecked(bool(spec.collect_nf2ff))
        self.chk_fields.setChecked(bool(spec.collect_fields))
        self.chk_continue.setChecked(not bool(spec.stop_on_error))

    def _on_mode_changed(self, *_args) -> None:
        use_range = self.rad_range.isChecked()
        self.range_widget.setVisible(use_range)
        self.list_widget.setVisible(not use_range)
        self._refresh()

    def _on_target_kind_changed(self, *_args) -> None:
        phase = self.cmb_target_kind.currentData() == "port_phase"
        self.cmb_variable.setEnabled(not phase)
        self.cmb_port.setEnabled(phase)
        if phase:
            self.chk_full.blockSignals(True)
            self.chk_full.setChecked(False)
            self.chk_full.blockSignals(False)
            self.chk_full.setEnabled(False)
            self.chk_nf2ff.blockSignals(True)
            self.chk_nf2ff.setChecked(True)
            self.chk_nf2ff.blockSignals(False)
            self.chk_nf2ff.setEnabled(False)
            self.lbl_unit.setText("Phase unit: degrees (0…360)")
        else:
            self.chk_full.setEnabled(self._active_nports() >= 2)
            self.chk_nf2ff.setEnabled(True)
            unit = str(getattr(self.project.mesh, "drawing_unit", "mm") or "mm")
            self.lbl_unit.setText(f"Drawing unit: {unit}")
        self._refresh()

    def _parse_values(self) -> list[float]:
        self._values_error = ""
        if self.rad_list.isChecked():
            parts = _split_values(self.ed_list.text())
            if not parts:
                self._values_error = "Enter at least one numeric value."
                return []
            values: list[float] = []
            for part in parts:
                value = _number(part)
                if value is None:
                    self._values_error = f"'{part}' is not a number."
                    return []
                values.append(value)
        else:
            start = _number(self.ed_start.text())
            stop = _number(self.ed_stop.text())
            points_value = _number(self.ed_points.text())
            if start is None or stop is None:
                self._values_error = "Start and stop must be numeric."
                return []
            if points_value is None or points_value < 1:
                self._values_error = "Points must be an integer >= 1."
                return []
            points = int(round(points_value))
            if abs(points_value - points) > 1e-9:
                self._values_error = "Points must be an integer."
                return []
            if points > _MAX_POINTS:
                self._values_error = f"At most {_MAX_POINTS} points are allowed."
                return []
            if points == 1:
                values = [start]
            else:
                step = (stop - start) / float(points - 1)
                values = [start + step * i for i in range(points)]
        # preserve order but remove exact duplicates
        unique: list[float] = []
        for value in values:
            if not any(abs(value - old) <= 1e-12 for old in unique):
                unique.append(value)
        if not unique:
            self._values_error = "The value list is empty."
        return unique

    def _active_nports(self) -> int:
        return len(
            [p for p in self.project.ports if bool(getattr(p, "active", True))]
        )

    def _refresh(self, *_args) -> None:
        self._values = self._parse_values()
        phase = self.cmb_target_kind.currentData() == "port_phase"
        target = (
            self.cmb_port.currentText().strip()
            if phase
            else self.cmb_variable.currentText().strip()
        )
        used = self._usage.get(target.casefold(), 0)
        enabled = self.chk_enabled.isChecked()
        nports = self._active_nports()
        uses_full = (
            not phase and bool(self.chk_full.isChecked()) and nports >= 2
        )
        runs = len(self._values) * (nports if uses_full else 1)
        lines: list[str] = []

        if phase:
            if not target:
                lines.append(
                    "Select an active port whose excitation phase is swept."
                )
            else:
                lines.append(
                    f"Port phase target: '{target}'. Geometry and mesh data do "
                    "not change; the current runner still launches a normal "
                    "per-phase script (mesh reuse is a future optimization)."
                )
                lines.append(
                    "S-parameters are not phase-dependent; NF2FF has been "
                    "enabled and is the useful result. Ensure the NF2FF box is "
                    "enabled and fitted inside the domain."
                )
                port = next(
                    (
                        p
                        for p in self.project.ports
                        if str(getattr(p, "name", "") or "").strip()
                        == target
                    ),
                    None,
                )
                if port is not None and not bool(
                    getattr(port, "excite_port", False)
                ):
                    lines.append(
                        "This port is not marked 'excite port'; enable it in "
                        "the Port dialog or the phase value has no effect."
                    )
                lines.append(
                    "For a visible pattern change, excite at least two ports "
                    "simultaneously and vary their relative phase. A global "
                    "phase of a single port only shifts the complex field."
                )
                lines.append(
                    "Estimated workload: one FDTD run per phase "
                    f"({runs} run(s))."
                )
        else:
            expression_variable = any(
                str(getattr(v, "name", "") or "").strip().casefold()
                == target.casefold()
                and bool(str(getattr(v, "expression", "") or "").strip())
                for v in self.project.variables
            )
            if not target:
                lines.append("Define a variable first (Geometry → Variables…).")
            elif used <= 0:
                lines.append(
                    f"Variable '{target}' is not used by any geometry, port, "
                    "dump or NF2FF field."
                )
            else:
                lines.append(
                    f"Variable '{target}' is used by {used} field"
                    f"{'s' if used != 1 else ''}."
                )
                if expression_variable:
                    lines.append(
                        "Formula variable: each sweep value overrides its formula "
                        "for that point."
                    )
                lines.append(
                    "Geometry/ports may change → the mesh is rebuilt for every "
                    "point/run."
                )
            if uses_full:
                lines.append(
                    f"Estimated workload: {len(self._values)} points × "
                    f"{nports} ports = {runs} FDTD runs."
                )
            else:
                lines.append(f"Estimated workload: {runs} FDTD run(s).")
        if enabled and not self._values:
            lines.append(self._values_error or "No valid sweep values.")
        self.txt_warning.setPlainText("\n".join(lines))

        ok = bool(target)
        if enabled:
            ok = ok and bool(self._values) and not self._values_error
            if not phase:
                ok = ok and used > 0
        self.bb.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def spec(self) -> SweepSpec:
        phase = self.cmb_target_kind.currentData() == "port_phase"
        target = (
            self.cmb_port.currentText().strip()
            if phase
            else self.cmb_variable.currentText().strip()
        )
        return SweepSpec(
            enabled=bool(self.chk_enabled.isChecked()),
            variable=target if not phase else "",
            target_kind="port_phase" if phase else "variable",
            target=target,
            values=list(self._values),
            full_smatrix=bool(
                not phase
                and self.chk_full.isChecked()
                and self._active_nports() >= 2
            ),
            collect_nf2ff=bool(phase or self.chk_nf2ff.isChecked()),
            collect_fields=bool(self.chk_fields.isChecked()),
            stop_on_error=not bool(self.chk_continue.isChecked()),
            mesh_policy="rebuild_per_point",
        )
