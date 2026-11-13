"""Dialogs for generic antenna/PCB projects: ports, materials, primitives, dumps."""

from __future__ import annotations

import math
import re
from typing import Any

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models.project import DumpDef, GeomAssignment, MaterialDef, PortDef
from app.models.variables import (
    VariableError,
    evaluate_expression,
    expression_entry,
    expression_text,
)
from app.numfmt import format_number, parse_number
from app.ui.input_alignment import double_spin_box, spin_box
from app.ui.object_display import DisplayOptionsWidget
from app.ui.primitive_preview import PolygonPreview
from app.ui.variable_field import (
    VariableField,
    variable_field_expressions,
)

MATERIAL_KINDS = ["pec", "dielectric", "conducting_sheet", "lumped_rlc", "ignore"]
PORT_TYPES = ["lumped", "msl", "rect_waveguide"]
DUMP_QTY = [
    "E", "H", "J", "Jtot", "D", "B",
    "SAR", "SAR1g", "SAR10g", "SARraw", "custom",
]


def _axis_suffixes(coord_system: str) -> tuple[str, str, str]:
    if coord_system == "cylindrical":
        return "ρ", "φ", "z"
    return "x", "y", "z"


def _fit_dialog(
    dlg: QDialog,
    want: tuple[int, int],
    minimum: tuple[int, int] = (520, 360),
    margin: int = 80,
) -> None:
    """Size a dialog for the screen it opens on.

    ``want`` is the comfortable size; the dialog is clamped to the available
    screen (minus ``margin`` px) so it never opens larger than the display, and
    it can be shrunk down to ``minimum``. A size remembered by the caller
    (``want`` = last size) simply wins as long as it still fits.
    """
    screen = dlg.screen() or QApplication.primaryScreen()
    avail = screen.availableGeometry() if screen is not None else None
    max_w = (avail.width() if avail else 1280) - margin
    max_h = (avail.height() if avail else 800) - margin
    min_w = max(320, min(int(minimum[0]), max_w))
    min_h = max(260, min(int(minimum[1]), max_h))
    dlg.setMinimumSize(min_w, min_h)
    dlg.resize(
        max(min_w, min(int(want[0]), max_w)),
        max(min_h, min(int(want[1]), max_h)),
    )


def _variables_for(
    parent: QWidget | None, variables: dict[str, float] | None
) -> dict[str, float]:
    """Explicit variables, else the parent MainWindow's project variables."""
    if variables is not None:
        return {str(k): float(v) for k, v in variables.items()}
    project = getattr(parent, "project", None)
    getter = getattr(project, "variable_values", None)
    if callable(getter):
        try:
            return dict(getter())
        except Exception:  # noqa: BLE001 - a dialog must still open
            return {}
    return {}


def _expression_for(expressions: dict | None, key: str) -> str:
    if not isinstance(expressions, dict):
        return ""
    return expression_text(expressions.get(key))


def _read_checked(parent: QWidget, title: str, reader):
    """Run a dialog reader and show one clear warning for an invalid field."""
    try:
        return reader()
    except VariableError as e:
        QMessageBox.warning(parent, title, str(e))
        return None


def _vec3_row(
    form: QFormLayout,
    label: str,
    values: list[float],
    lo: float = -1e4,
    hi: float = 1e4,
    coord_system: str = "cartesian",
    *,
    variables: dict[str, float] | None = None,
    expressions: dict | None = None,
    prefix: str = "",
):
    """Three variable-capable fields (one per axis).

    Cylindrical: suffixes ρ/φ/z and the phi field shows DEGREES (project values
    are always radians).  Saved expressions are shown verbatim so editing an
    object does not silently replace ``L/2`` with its current number.
    """
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    fields: list[VariableField] = []
    polar = coord_system == "cylindrical"
    axes = _axis_suffixes(coord_system)
    for i, axis in enumerate(axes):
        raw = float(values[i]) if i < len(values) else 0.0
        use_phi = polar and i == 1
        field = VariableField(
            math.degrees(raw) if use_phi else raw,
            variables=variables or {},
            scale=math.pi / 180.0 if use_phi else 1.0,
            label=f"{label} {axis}",
            minimum=-36000.0 if use_phi else lo,
            maximum=36000.0 if use_phi else hi,
        )
        key = f"{prefix}.{i}" if prefix else ""
        expr = _expression_for(expressions, key) if key else ""
        if expr:
            field.setExpression(expr)
        row = QWidget()
        row_lay = QHBoxLayout(row)
        row_lay.setContentsMargins(0, 0, 0, 0)
        row_lay.setSpacing(4)
        row_lay.addWidget(field, 1)
        suffix = QLabel("°" if use_phi else axis)
        suffix.setMinimumWidth(14)
        row_lay.addWidget(suffix)
        lay.addWidget(row)
        fields.append(field)
    form.addRow(label, w)
    return fields


def _axis_combo(current: str) -> QComboBox:
    """x/y/z combo with an explicit '(auto)' entry (stored as empty string)."""
    cmb = QComboBox()
    for label, value in (("(auto)", ""), ("x", "x"), ("y", "y"), ("z", "z")):
        cmb.addItem(label, value)
    idx = cmb.findData((current or "").strip().lower())
    cmb.setCurrentIndex(idx if idx >= 0 else 0)
    return cmb


def _vec3_read(fields: list[VariableField], coord_system: str = "cartesian") -> list[float]:
    """Fields -> project values (scaling already handled by VariableField)."""
    return [float(field.value()) for field in fields]


def _split_point_line(line: str) -> list[str]:
    """Split a point line on commas/semicolons first, then whitespace.

    Commas are the reliable separator when an expression contains spaces or
    operators; whitespace splitting keeps old numeric point lists working.
    """
    parts = [s.strip() for s in re.split(r"[,;]+", line) if s.strip()]
    if len(parts) >= 2:
        return parts
    return [s for s in re.split(r"\s+", line.strip()) if s]


class PortDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        port: PortDef | None = None,
        coord_system: str = "cartesian",
        variables: dict[str, float] | None = None,
        width_preview=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Port")
        self.resize(380, 500)
        self._coord_system = coord_system
        self._variables = _variables_for(parent, variables)
        self._width_preview = width_preview
        p = port or PortDef(name="in1")
        self._expressions = dict(getattr(p, "expressions", {}) or {})
        self.result_port: PortDef | None = None
        form = QFormLayout(self)

        self.ed_name = QLineEdit(p.name)
        self.cmb_type = QComboBox()
        self.cmb_type.addItems(PORT_TYPES)
        self.cmb_type.setCurrentText(p.type)
        self.spn_r = double_spin_box()
        self.spn_r.setRange(0.1, 1e4)
        self.spn_r.setDecimals(2)
        self.spn_r.setValue(float(p.resistance or 50.0))
        self.cmb_dir = QComboBox()
        self.cmb_dir.addItems(["x", "y", "z"])
        self.cmb_dir.setCurrentText(p.direction or "z")
        self.spn_wg_a = VariableField(
            float(getattr(p, "wg_a", 0.0) or 0.0),
            variables=self._variables,
            label="Waveguide a",
            minimum=0.0,
            maximum=1000.0,
        )
        self.spn_wg_b = VariableField(
            float(getattr(p, "wg_b", 0.0) or 0.0),
            variables=self._variables,
            label="Waveguide b",
            minimum=0.0,
            maximum=1000.0,
        )
        expr_wg_a = _expression_for(self._expressions, "wg_a")
        expr_wg_b = _expression_for(self._expressions, "wg_b")
        if expr_wg_a:
            self.spn_wg_a.setExpression(expr_wg_a)
        if expr_wg_b:
            self.spn_wg_b.setExpression(expr_wg_b)
        self.cmb_wg_mode = QComboBox()
        self.cmb_wg_mode.addItems(["TE10", "TE20", "TE01", "TE11", "TE21"])
        _mode = str(getattr(p, "wg_mode", "") or "TE10").strip().upper()
        self.cmb_wg_mode.setCurrentText(_mode if _mode in
                                        [self.cmb_wg_mode.itemText(i)
                                         for i in range(self.cmb_wg_mode.count())]
                                        else "TE10")
        self.spn_wg_a.setToolTip(
            "Rectangular waveguide only (drawing units).\n"
            "a = broad inner width, along the FIRST transverse axis:\n"
            "  direction x -> a along y, b along z\n"
            "  direction y -> a along z, b along x\n"
            "  direction z -> a along x, b along y\n"
            "WR-90 (X band, 8.2-12.4 GHz): a 22.86, b 10.16 mm."
        )
        self.spn_wg_b.setToolTip("Rectangular waveguide only: narrow inner height (see a).")
        self.cmb_wg_mode.setToolTip(
            "Rectangular waveguide only: TE mode. TE10 is the single-mode\n"
            "fundamental; the cutoff follows from a and b."
        )
        self.cmb_width = QComboBox()
        for label, value in (
            ("Line (no width)", "line"),
            ("Area - as wide as the metal (x)", "x"),
            ("Area - as wide as the metal (y)", "y"),
            ("Area - as wide as the metal (z)", "z"),
        ):
            self.cmb_width.addItem(label, value)
        _wmode = str(getattr(p, "lumped_width", "line") or "line").strip().lower()
        _widx = self.cmb_width.findData(_wmode)
        self.cmb_width.setCurrentIndex(_widx if _widx >= 0 else 0)
        self.cmb_width.setToolTip(
            "Lumped ports only:\n"
            "  Line (no width) - a 1-D port (Start/Stop differ only along Direction)\n"
            "  Area - as wide as the metal (axis) - an area/sheet port: the box is\n"
            "  widened to the metal the port was placed on, the other transverse\n"
            "  axis stays a zero-thickness sheet at the click.\n"
            "Changing it rebuilds the box (a line collapses to its middle); an\n"
            "unchanged mode leaves your Start/Stop edits alone."
        )
        self._seed_metal = str(getattr(p, "metal", "") or "")
        self.cmb_prop = _axis_combo(getattr(p, "prop_direction", ""))
        self.cmb_prop.setToolTip(
            "MSL only: propagation axis along the line\n(empty = longest port edge)"
        )
        self.cmb_exc = _axis_combo(getattr(p, "exc_direction", ""))
        self.cmb_exc.setToolTip(
            "MSL only: excitation axis through the substrate\n(empty = shortest port edge)"
        )
        self.chk_excite = QCheckBox("Excite port")
        self.chk_excite.setChecked(bool(p.excite_port))
        self.spn_exc_phase = double_spin_box()
        self.spn_exc_phase.setRange(-360.0, 360.0)
        self.spn_exc_phase.setDecimals(2)
        self.spn_exc_phase.setSuffix(" °")
        self.spn_exc_phase.setValue(float(getattr(p, "excite_phase_deg", 0.0) or 0.0))
        self.spn_exc_phase.setToolTip(
            "Excitation phase at the project's target frequency.\n"
            "Used when several ports are excited at once (e.g. two feeds of a\n"
            "CP patch with a 90° hybrid). openEMS gets an equivalent\n"
            "non-negative excitation delay; 0° means no delay."
        )
        self.chk_reverse = QCheckBox("Reverse (swap Start/Stop)")
        self.chk_reverse.setChecked(bool(getattr(p, "reverse", False)))
        self.chk_reverse.setToolTip(
            "Flip the port direction: Start and Stop are swapped when the run\n"
            "script is generated. openEMS takes the port polarity from\n"
            "sign(Stop - Start), so this inverts the excitation and current sign\n"
            "of a lumped port and the propagation sense of an MSL port.\n"
            "The box in the viewer does not change (no effect on rect waveguide\n"
            "ports - their direction comes from the Direction field)."
        )
        self.chk_active = QCheckBox("Active (use in the simulation)")
        self.chk_active.setChecked(bool(getattr(p, "active", True)))
        self.chk_active.setToolTip(
            "Untick to switch the port off: it stays in the project (greyed out)\n"
            "but is not written to the simulation — no port number, no\n"
            "S-parameter column, ignored by 'Run All Ports'."
        )
        self.cmb_mesh = QComboBox()
        self.cmb_mesh.addItems(["xyz", "xy", "xz", "yz", "x", "y", "z", ""])
        self.cmb_mesh.setCurrentText(p.mesh_dirs or "xy")

        form.addRow("Name", self.ed_name)
        form.addRow("Type", self.cmb_type)
        form.addRow("Resistance (Ω)", self.spn_r)
        form.addRow("Direction", self.cmb_dir)
        form.addRow("Waveguide a", self.spn_wg_a)
        form.addRow("Waveguide b", self.spn_wg_b)
        form.addRow("TE mode", self.cmb_wg_mode)
        form.addRow("Width (lumped)", self.cmb_width)
        form.addRow("Propagation dir (MSL)", self.cmb_prop)
        form.addRow("Excitation dir (MSL)", self.cmb_exc)
        form.addRow(self.chk_active)
        form.addRow(self.chk_excite)
        form.addRow("Excitation phase", self.spn_exc_phase)
        form.addRow(self.chk_reverse)
        form.addRow("Mesh dirs", self.cmb_mesh)
        self.spn_start = _vec3_row(
            form,
            "Start",
            p.start,
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="start",
        )
        self.spn_stop = _vec3_row(
            form,
            "Stop",
            p.stop,
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="stop",
        )
        if coord_system == "cylindrical":
            self.cmb_dir.setToolTip(
                "Excitation/normal axis letter: x=ρ, y=φ, z (openEMS always\n"
                "uses x/y/z letters, also on a cylindrical mesh)."
            )
            self.cmb_mesh.setToolTip("Mesh axis letters: x=ρ, y=φ, z")
        self.cmb_type.currentTextChanged.connect(self._on_type_changed)
        self._on_type_changed(self.cmb_type.currentText())
        self.cmb_width.currentIndexChanged.connect(self._on_width_mode_changed)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _on_width_mode_changed(self, _index: int = 0) -> None:
        """Show the rebuilt box immediately when the width mode changes."""
        if not callable(self._width_preview):
            return
        if str(self.cmb_type.currentText()) != "lumped":
            return
        mode = str(self.cmb_width.currentData() or "line").strip().lower()
        start = [field.safe_value() for field in self.spn_start]
        stop = [field.safe_value() for field in self.spn_stop]
        result = self._width_preview(
            mode, start, stop, self.cmb_dir.currentText(), self._seed_metal
        )
        if not result:
            return
        new_start, new_stop, metal = result
        for fields, old_values, new_values in (
            (self.spn_start, start, new_start),
            (self.spn_stop, stop, new_stop),
        ):
            for field, old, new in zip(fields, old_values, new_values):
                if abs(float(new) - float(old)) > 1e-9:
                    field.setValue(float(new))
        if metal:
            self._seed_metal = str(metal)

    def _on_type_changed(self, kind: str) -> None:
        """Only lumped ports have a (lumped) width - MSL uses its own axes."""
        is_wg = str(kind) == "rect_waveguide"
        self.cmb_width.setEnabled(str(kind) == "lumped")
        form = self.layout()
        if isinstance(form, QFormLayout):
            for w in (self.spn_wg_a, self.spn_wg_b, self.cmb_wg_mode):
                form.setRowVisible(w, is_wg)
            # a waveguide port uses the analytic wave impedance, not R
            form.setRowVisible(self.spn_r, not is_wg)

    def _accept(self) -> None:
        vals = _read_checked(
            self,
            "Port",
            lambda: (
                _vec3_read(self.spn_start, self._coord_system),
                _vec3_read(self.spn_stop, self._coord_system),
                float(self.spn_wg_a.value()),
                float(self.spn_wg_b.value()),
            ),
        )
        if vals is None:
            return
        start, stop, wg_a, wg_b = vals
        name = self.ed_name.text().strip() or "in1"
        ptype = self.cmb_type.currentText()
        expressions = variable_field_expressions(
            {
                **{f"start.{i}": f for i, f in enumerate(self.spn_start)},
                **{f"stop.{i}": f for i, f in enumerate(self.spn_stop)},
                "wg_a": self.spn_wg_a,
                "wg_b": self.spn_wg_b,
            }
        )
        self.result_port = PortDef(
            name=name,
            type=ptype,  # type: ignore[arg-type]
            start=start,
            stop=stop,
            resistance=float(self.spn_r.value()),
            direction=self.cmb_dir.currentText(),
            prop_direction=self.cmb_prop.currentData() or "",
            exc_direction=self.cmb_exc.currentData() or "",
            excite_port=self.chk_excite.isChecked(),
            excite_phase_deg=float(self.spn_exc_phase.value()),
            active=self.chk_active.isChecked(),
            reverse=self.chk_reverse.isChecked(),
            mesh_dirs=self.cmb_mesh.currentText(),
            lumped_width=(
                (self.cmb_width.currentData() or "line")
                if ptype == "lumped"
                else "line"
            ),
            metal=self._seed_metal,
            wg_a=wg_a,
            wg_b=wg_b,
            wg_mode=self.cmb_wg_mode.currentText() if ptype == "rect_waveguide" else "TE10",
            expressions=expressions,
        )
        self.accept()


class AssignmentDialog(QDialog):
    def __init__(
        self,
        object_names: list[str],
        material_names: list[str],
        parent: QWidget | None = None,
        assignment: GeomAssignment | None = None,
        display: dict | None = None,
        display_for=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Material assignment")
        self.resize(380, 360)
        a = assignment or GeomAssignment(object_name="", role="dielectric")
        self._keep_notes = getattr(a, "notes", "") or ""
        self.result: GeomAssignment | None = None
        form = QFormLayout(self)
        self.cmb_obj = QComboBox()
        self.cmb_obj.setEditable(True)
        self.cmb_obj.addItems(object_names or ["(no objects)"])
        if a.object_name:
            self.cmb_obj.setCurrentText(a.object_name)
        self.cmb_role = QComboBox()
        self.cmb_role.addItems(MATERIAL_KINDS)
        self.cmb_role.setCurrentText(a.role)
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        self.cmb_mat.addItems(material_names or ["substrate", "copper", "vacuum"])
        if a.material:
            self.cmb_mat.setCurrentText(a.material)
        self.spn_pri = spin_box()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(a.priority))
        form.addRow("Object", self.cmb_obj)
        form.addRow("Role", self.cmb_role)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        self.display = DisplayOptionsWidget(
            display_for(self.cmb_obj.currentText()) if callable(display_for) else display,
            self,
        )
        if callable(display_for):
            # follow the object picked in the combo (new assignment / rename)
            self.cmb_obj.currentTextChanged.connect(
                lambda name: self.display.set_values(display_for(name))
            )
        form.addRow(self.display)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def display_values(self) -> dict:
        """Colour / transparency / visibility picked for this object."""
        return self.display.values()

    def _accept(self) -> None:
        obj = self.cmb_obj.currentText().strip()
        if not obj or obj == "(no objects)":
            obj = "object_1"
        notes = getattr(self, "_keep_notes", "") or ""
        self.result = GeomAssignment(
            object_name=obj,
            role=self.cmb_role.currentText(),  # type: ignore[arg-type]
            material=self.cmb_mat.currentText().strip(),
            priority=self.spn_pri.value(),
            notes=notes,
        )
        self.accept()


class MaterialDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, material: MaterialDef | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Material")
        self.resize(340, 280)
        m = material or MaterialDef(name="substrate", kind="dielectric", epsilon=3.38)
        self.result: MaterialDef | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(m.name)
        self.cmb_kind = QComboBox()
        self.cmb_kind.addItems(MATERIAL_KINDS)
        self.cmb_kind.setCurrentText(m.kind)
        self.spn_eps = double_spin_box()
        self.spn_eps.setRange(1.0, 100.0)
        self.spn_eps.setDecimals(4)
        self.spn_eps.setValue(float(m.epsilon or 1.0))
        self.spn_kappa = double_spin_box()
        self.spn_kappa.setRange(0.0, 1e9)
        self.spn_kappa.setDecimals(6)
        self.spn_kappa.setValue(float(m.kappa or 0.0))
        self.spn_tand = double_spin_box()
        self.spn_tand.setRange(0.0, 10.0)
        self.spn_tand.setDecimals(6)
        self.spn_tand.setValue(float(getattr(m, "tand", 0.0) or 0.0))
        self._keep_permeability = float(getattr(m, "permeability", 1.0) or 1.0)
        self._keep_color = tuple(getattr(m, "color", (0.8, 0.5, 0.2, 1.0)))
        form.addRow("Name", self.ed_name)
        form.addRow("Kind", self.cmb_kind)
        form.addRow("εr", self.spn_eps)
        form.addRow("κ (S/m)", self.spn_kappa)
        form.addRow("tan δ", self.spn_tand)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        name = self.ed_name.text().strip() or "material"
        self.result = MaterialDef(
            name=name,
            kind=self.cmb_kind.currentText(),  # type: ignore[arg-type]
            epsilon=float(self.spn_eps.value()),
            kappa=float(self.spn_kappa.value()),
            tand=float(self.spn_tand.value()),
            permeability=float(self._keep_permeability),
            color=self._keep_color,
        )
        self.accept()


class _DisplayMixin:
    """Per-object Mesh Viewer display options (colour / transparency / visibility)."""

    def display_values(self) -> dict:
        """Colour / transparency / visibility picked for this object."""
        return self.display.values()

    def _add_display(self, form: QFormLayout, display: dict | None) -> None:
        self.display = DisplayOptionsWidget(display, self)
        form.addRow(self.display)


class PrimitiveBoxDialog(QDialog, _DisplayMixin):
    """Add/edit a box primitive (any antenna/PCB metal or dielectric volume)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        coord_system: str = "cartesian",
        display: dict | None = None,
        variables: dict[str, float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Box primitive")
        self.resize(400, 520)
        self._coord_system = coord_system
        if coord_system == "cylindrical":
            # In cylindrical mode a "box" is a wedge in (rho, phi, z).
            self.setWindowTitle("Wedge primitive (cylindrical)")
        p = prim or {"name": "box1", "material": "copper", "start": [0, 0, 0], "stop": [10, 10, 0.5]}
        self._variables = _variables_for(parent, variables)
        self._expressions = dict(p.get("expressions") or {}) if prim else {}
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "box1")))
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        mats = ["copper", "substrate", "vacuum", "pec", "dielectric"]
        self.cmb_mat.addItems(mats)
        self.cmb_mat.setCurrentText(str(p.get("material", "copper")))
        self.spn_pri = spin_box()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 10)))
        self.chk_edges = QCheckBox("Snap edges to mesh (edges2grid)")
        self.chk_edges.setChecked(bool(p.get("edges2grid")))
        form.addRow("Name", self.ed_name)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        form.addRow(self.chk_edges)
        self.spn_start = _vec3_row(
            form,
            "Start",
            list(p.get("start", [0, 0, 0])),
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="start",
        )
        self.spn_stop = _vec3_row(
            form,
            "Stop",
            list(p.get("stop", [10, 10, 0.5])),
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="stop",
        )
        self._add_display(form, display)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        vals = _read_checked(
            self,
            "Box primitive",
            lambda: (
                _vec3_read(self.spn_start, self._coord_system),
                _vec3_read(self.spn_stop, self._coord_system),
            ),
        )
        if vals is None:
            return
        start, stop = vals
        name = self.ed_name.text().strip() or "box1"
        prim: dict[str, Any] = {
            "kind": "box",
            "name": name,
            "material": self.cmb_mat.currentText().strip() or "copper",
            "priority": self.spn_pri.value(),
            "start": start,
            "stop": stop,
        }
        if self.chk_edges.isChecked():
            prim["edges2grid"] = "xy"
        expressions = variable_field_expressions(
            {
                **{f"start.{i}": f for i, f in enumerate(self.spn_start)},
                **{f"stop.{i}": f for i, f in enumerate(self.spn_stop)},
            }
        )
        if expressions:
            prim["expressions"] = expressions
        # keep imported STL fields (path, mesh_path, …) when editing
        for k, v in (self._keep_extra or {}).items():
            if k not in prim and k != "expressions":
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitiveCylinderDialog(QDialog, _DisplayMixin):
    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        coord_system: str = "cartesian",
        display: dict | None = None,
        variables: dict[str, float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cylinder primitive")
        self.resize(400, 480)
        self._coord_system = coord_system
        p = prim or {"name": "cyl1", "material": "copper", "start": [0, 0, 0], "stop": [0, 0, 5], "radius": 0.5}
        self._variables = _variables_for(parent, variables)
        self._expressions = dict(p.get("expressions") or {}) if prim else {}
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "cyl1")))
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        self.cmb_mat.addItems(["copper", "substrate", "vacuum"])
        self.cmb_mat.setCurrentText(str(p.get("material", "copper")))
        self.spn_pri = spin_box()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 10)))
        self.spn_radius = VariableField(
            float(p.get("radius", 0.5)),
            variables=self._variables,
            label="Radius",
            minimum=1e-4,
            maximum=1e3,
        )
        expr_radius = _expression_for(self._expressions, "radius")
        if expr_radius:
            self.spn_radius.setExpression(expr_radius)
        form.addRow("Name", self.ed_name)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        form.addRow("Radius", self.spn_radius)
        self.spn_start = _vec3_row(
            form,
            "Start (axis A)",
            list(p.get("start", [0, 0, 0])),
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="start",
        )
        self.spn_stop = _vec3_row(
            form,
            "Stop (axis B)",
            list(p.get("stop", [0, 0, 5])),
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="stop",
        )
        self._add_display(form, display)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        vals = _read_checked(
            self,
            "Cylinder primitive",
            lambda: (
                _vec3_read(self.spn_start, self._coord_system),
                _vec3_read(self.spn_stop, self._coord_system),
                float(self.spn_radius.value()),
            ),
        )
        if vals is None:
            return
        start, stop, radius = vals
        prim: dict[str, Any] = {
            "kind": "cylinder",
            "name": self.ed_name.text().strip() or "cyl1",
            "material": self.cmb_mat.currentText().strip() or "copper",
            "priority": self.spn_pri.value(),
            "start": start,
            "stop": stop,
            "radius": radius,
        }
        expressions = variable_field_expressions(
            {
                **{f"start.{i}": f for i, f in enumerate(self.spn_start)},
                **{f"stop.{i}": f for i, f in enumerate(self.spn_stop)},
                "radius": self.spn_radius,
            }
        )
        if expressions:
            prim["expressions"] = expressions
        for k, v in (self._keep_extra or {}).items():
            if k not in prim and k != "expressions":
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitiveSphereDialog(QDialog, _DisplayMixin):
    """Add/edit a sphere primitive (exact openEMS AddSphere)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        coord_system: str = "cartesian",
        display: dict | None = None,
        variables: dict[str, float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sphere primitive")
        self.resize(400, 500)
        self._coord_system = coord_system
        p = prim or {
            "name": "sphere1",
            "material": "copper",
            "center": [0, 0, 0],
            "radius": 5.0,
        }
        self._variables = _variables_for(parent, variables)
        self._expressions = dict(p.get("expressions") or {}) if prim else {}
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "sphere1")))
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        self.cmb_mat.addItems(["copper", "substrate", "vacuum"])
        self.cmb_mat.setCurrentText(str(p.get("material", "copper")))
        self.spn_pri = spin_box()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 10)))
        self.spn_radius = VariableField(
            float(p.get("radius", 5.0)),
            variables=self._variables,
            label="Radius",
            minimum=1e-4,
            maximum=1e3,
        )
        expr_radius = _expression_for(self._expressions, "radius")
        if expr_radius:
            self.spn_radius.setExpression(expr_radius)
        form.addRow("Name", self.ed_name)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        form.addRow("Radius", self.spn_radius)
        self.spn_center = _vec3_row(
            form,
            "Center",
            list(p.get("center", [0, 0, 0])),
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="center",
        )
        self._add_display(form, display)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        vals = _read_checked(
            self,
            "Sphere primitive",
            lambda: (
                _vec3_read(self.spn_center, self._coord_system),
                float(self.spn_radius.value()),
            ),
        )
        if vals is None:
            return
        center, radius = vals
        prim: dict[str, Any] = {
            "kind": "sphere",
            "name": self.ed_name.text().strip() or "sphere1",
            "material": self.cmb_mat.currentText().strip() or "copper",
            "priority": self.spn_pri.value(),
            "center": center,
            "radius": radius,
            # bbox (used by the viewer slice rect)
            "start": [center[0] - radius, center[1] - radius, center[2] - radius],
            "stop": [center[0] + radius, center[1] + radius, center[2] + radius],
        }
        expressions = variable_field_expressions(
            {
                **{f"center.{i}": f for i, f in enumerate(self.spn_center)},
                "radius": self.spn_radius,
            }
        )
        if expressions:
            prim["expressions"] = expressions
        for k, v in (self._keep_extra or {}).items():
            if k not in prim and k != "expressions":
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitiveLumpedElementDialog(QDialog, _DisplayMixin):
    """Add/edit a lumped R/L/C element (openEMS ``AddLumpedElement``)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        coord_system: str = "cartesian",
        display: dict | None = None,
        variables: dict[str, float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lumped element (R/L/C)")
        self.resize(430, 610)
        self._coord_system = coord_system
        p = prim or {
            "name": "R1",
            "direction": "z",
            "start": [0, 0, 0],
            "stop": [0, 0, 1],
            "resistance": 50.0,
            "inductance": 0.0,
            "capacitance": 0.0,
            "caps": True,
            "priority": 15,
        }
        self._variables = _variables_for(parent, variables)
        self._expressions = dict(p.get("expressions") or {}) if prim else {}
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None

        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "R1")))
        self.spn_pri = spin_box()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 15)))
        self.cmb_dir = QComboBox()
        self.cmb_dir.addItems(["x", "y", "z"])
        self.cmb_dir.setCurrentText(str(p.get("direction", "z")).lower())
        self.cmb_dir.setToolTip(
            "Component direction: the axis along which the lumped element acts.\n"
            "The Start/Stop box should span this axis."
        )
        self.spn_r = double_spin_box()
        self.spn_r.setRange(0.0, 1e9)
        self.spn_r.setDecimals(3)
        self.spn_r.setValue(float(p.get("resistance", 50.0) or 0.0))
        self.spn_r.setSuffix(" Ω")
        self.spn_l = double_spin_box()
        self.spn_l.setRange(0.0, 1e9)
        self.spn_l.setDecimals(4)
        self.spn_l.setValue(float(p.get("inductance", 0.0) or 0.0) * 1e9)
        self.spn_l.setSuffix(" nH")
        self.spn_c = double_spin_box()
        self.spn_c.setRange(0.0, 1e9)
        self.spn_c.setDecimals(4)
        self.spn_c.setValue(float(p.get("capacitance", 0.0) or 0.0) * 1e12)
        self.spn_c.setSuffix(" pF")
        self.chk_caps = QCheckBox("PEC end caps (ensure contact)")
        self.chk_caps.setChecked(bool(p.get("caps", True)))

        form.addRow("Name", self.ed_name)
        form.addRow("Priority", self.spn_pri)
        form.addRow("Direction", self.cmb_dir)
        self.spn_start = _vec3_row(
            form,
            "Start",
            list(p.get("start", [0, 0, 0])),
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="start",
        )
        self.spn_stop = _vec3_row(
            form,
            "Stop / other end",
            list(p.get("stop", [0, 0, 1])),
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="stop",
        )
        form.addRow("Resistance", self.spn_r)
        form.addRow("Inductance", self.spn_l)
        form.addRow("Capacitance", self.spn_c)
        form.addRow(self.chk_caps)
        hint = QLabel(
            "R, L and C are applied at the element; use 0 for values that are\n"
            "not present. Start/Stop define the component box/line (drawing unit)."
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        self._add_display(form, display)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        vals = _read_checked(
            self,
            "Lumped element (R/L/C)",
            lambda: (
                _vec3_read(self.spn_start, self._coord_system),
                _vec3_read(self.spn_stop, self._coord_system),
            ),
        )
        if vals is None:
            return
        start, stop = vals
        prim: dict[str, Any] = {
            "kind": "lumped",
            "name": self.ed_name.text().strip() or "R1",
            "direction": self.cmb_dir.currentText() or "z",
            "priority": self.spn_pri.value(),
            "start": start,
            "stop": stop,
            "resistance": float(self.spn_r.value()),
            "inductance": float(self.spn_l.value()) * 1e-9,
            "capacitance": float(self.spn_c.value()) * 1e-12,
            "caps": bool(self.chk_caps.isChecked()),
        }
        expressions = variable_field_expressions(
            {
                **{f"start.{i}": f for i, f in enumerate(self.spn_start)},
                **{f"stop.{i}": f for i, f in enumerate(self.spn_stop)},
            }
        )
        if expressions:
            prim["expressions"] = expressions
        for k, v in (self._keep_extra or {}).items():
            if k not in prim and k != "expressions":
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitivePolygonDialog(QDialog, _DisplayMixin):
    """Add/edit a polygon primitive (flat sheet or extruded prism).

    Points are given in the plane perpendicular to the axis: axis z ->
    (x, y), axis x -> (y, z), axis y -> (z, x) — the same cyclic mapping
    CSXCAD uses for AddPolygon/AddLinPoly.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        coord_system: str = "cartesian",
        display: dict | None = None,
        unit: str = "mm",
        size: tuple[int, int] | None = None,
        extruded: bool = False,
        variables: dict[str, float] | None = None,
    ) -> None:
        super().__init__(parent)
        # compact default; a size the user picked before (``size``) wins
        _fit_dialog(self, tuple(size) if size else (760, 520))
        self._coord_system = coord_system
        self._unit = unit or "mm"
        p = prim or {
            "name": "extruded_poly1" if extruded else "poly1",
            "material": "copper",
            "axis": "z",
            "coord": 0.0,
            "length": 1.0 if extruded else 0.0,
            "points": [[0, 0], [20, 0], [20, 10], [0, 10]],
        }
        is_extruded = bool(extruded) or float(p.get("length") or 0.0) > 0.0
        self.setWindowTitle(
            "Extruded polygon primitive"
            if is_extruded
            else "Polygon primitive"
        )
        self._variables = _variables_for(parent, variables)
        self._expressions = dict(p.get("expressions") or {}) if prim else {}
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "poly1")))
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        self.cmb_mat.addItems(["copper", "substrate", "vacuum"])
        self.cmb_mat.setCurrentText(str(p.get("material", "copper")))
        self.spn_pri = spin_box()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 10)))
        self.cmb_axis = QComboBox()
        self.cmb_axis.addItems(["x", "y", "z"])
        self.cmb_axis.setCurrentText(str(p.get("axis", "z")))
        self.spn_coord = VariableField(
            float(p.get("coord", 0.0)),
            variables=self._variables,
            label="Coordinate",
            minimum=-1e4,
            maximum=1e4,
        )
        self.spn_length = VariableField(
            float(p.get("length") or 0.0),
            variables=self._variables,
            label="Length",
            minimum=0.0,
            maximum=1e4,
        )
        expr_coord = _expression_for(self._expressions, "coord")
        expr_length = _expression_for(self._expressions, "length")
        if expr_coord:
            self.spn_coord.setExpression(expr_coord)
        if expr_length:
            self.spn_length.setExpression(expr_length)
        self.spn_length.setToolTip(
            "0 = flat surface (AddPolygon); > 0 = extruded solid prism\n"
            "(AddLinPoly) with this thickness along the axis."
        )
        form.addRow("Name", self.ed_name)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        form.addRow("Axis (normal)", self.cmb_axis)
        form.addRow("Coordinate (plane)", self.spn_coord)
        form.addRow("Length (0 = sheet)", self.spn_length)
        self.ed_points = QPlainTextEdit()
        self.ed_points.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        pts = p.get("points") or []
        point_lines: list[str] = []
        for point_index, pt in enumerate(pts):
            if len(pt) < 2:
                continue
            values: list[str] = []
            for axis_index in range(2):
                expr = _expression_for(
                    self._expressions, f"points.{point_index}.{axis_index}"
                )
                values.append(
                    expr if expr else format_number(float(pt[axis_index]))
                )
            point_lines.append(", ".join(values))
        self.ed_points.setPlainText("\n".join(point_lines))
        self.ed_points.setPlaceholderText("x, y  (one point per line, >= 3 points)")
        self.ed_points.setMinimumHeight(110)
        # live preview: polygon in its plane, repainted while typing
        self.preview = PolygonPreview(self)
        self.preview.setMinimumWidth(240)
        pts_row = QWidget(self)
        pts_lay = QHBoxLayout(pts_row)
        pts_lay.setContentsMargins(0, 0, 0, 0)
        pts_lay.addWidget(self.ed_points, 3)
        pts_lay.addWidget(self.preview, 2)
        form.addRow("Points", pts_row)
        self._add_display(form, display)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.ed_points.textChanged.connect(self._refresh_preview)
        self.cmb_axis.currentTextChanged.connect(self._refresh_preview)
        self.spn_coord.valueChanged.connect(self._refresh_preview)
        self.spn_length.valueChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """Feed the current dialog state to the live polygon preview."""
        points, _expressions, _errors = self._parse_points_variables(
            self.ed_points.toPlainText(), strict=False
        )
        self.preview.set_polygon(
            points,
            axis=self.cmb_axis.currentText(),
            coord=self.spn_coord.safe_value(),
            length=max(0.0, self.spn_length.safe_value()),
            unit=self._unit,
        )

    def _parse_points_variables(
        self, text: str, *, strict: bool
    ) -> tuple[list[list[float]], dict[str, Any], list[str]]:
        """Parse 2-D points; evaluate expressions and keep their source text."""
        points: list[list[float]] = []
        expressions: dict[str, Any] = {}
        errors: list[str] = []
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = _split_point_line(line)
            if len(parts) < 2:
                if strict:
                    errors.append(
                        f"Line {line_number}: expected 'x, y' (one point per line)."
                    )
                continue
            values: list[float] = []
            source: list[str] = []
            failed = False
            for axis_index, axis_name in enumerate(("x", "y")):
                token = parts[axis_index]
                try:
                    value = evaluate_expression(
                        token,
                        self._variables,
                        context=f"Point {len(points) + 1} {axis_name}",
                    )
                except VariableError as e:
                    if strict:
                        errors.append(str(e))
                    failed = True
                    break
                values.append(value)
                source.append(token)
            if failed:
                continue
            point_index = len(points)
            for axis_index, token in enumerate(source):
                if parse_number(token) is None:
                    expressions[f"points.{point_index}.{axis_index}"] = expression_entry(
                        token
                    )
            points.append(values)
        return points, expressions, errors

    @staticmethod
    def _parse_points(text: str) -> list[list[float]]:
        """Numeric-only compatibility parser (no variables)."""
        pts: list[list[float]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = _split_point_line(line)
            if len(parts) < 2:
                continue
            try:
                pts.append([float(parts[0]), float(parts[1])])
            except ValueError:
                continue
        return pts

    def _accept(self) -> None:
        vals = _read_checked(
            self,
            "Polygon primitive",
            lambda: (float(self.spn_coord.value()), float(self.spn_length.value())),
        )
        if vals is None:
            return
        coord, length = vals
        length = max(0.0, length)
        pts, point_expressions, point_errors = self._parse_points_variables(
            self.ed_points.toPlainText(), strict=True
        )
        if point_errors:
            QMessageBox.warning(
                self,
                "Polygon primitive",
                "\n".join(point_errors[:8]),
            )
            return
        if len(pts) < 3:
            QMessageBox.warning(
                self,
                "Polygon primitive",
                "A polygon needs at least 3 points (one \"x, y\" per line).",
            )
            return
        axis = self.cmb_axis.currentText()
        axis_idx = "xyz".index(axis) if axis in "xyz" else 2
        # bbox for the viewer slice rect (cyclic point mapping, see class doc)
        c0 = (axis_idx + 1) % 3
        c1 = (axis_idx + 2) % 3
        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]
        start = [0.0, 0.0, 0.0]
        stop = [0.0, 0.0, 0.0]
        start[axis_idx] = coord
        stop[axis_idx] = coord + length
        start[c0] = min(xs)
        stop[c0] = max(xs)
        start[c1] = min(ys)
        stop[c1] = max(ys)
        prim: dict[str, Any] = {
            "kind": "polygon",
            "name": self.ed_name.text().strip() or "poly1",
            "material": self.cmb_mat.currentText().strip() or "copper",
            "priority": self.spn_pri.value(),
            "axis": axis,
            "coord": coord,
            "length": length,
            "points": pts,
            "start": start,
            "stop": stop,
        }
        expressions = variable_field_expressions(
            {"coord": self.spn_coord, "length": self.spn_length}
        )
        expressions.update(point_expressions)
        if expressions:
            prim["expressions"] = expressions
        for k, v in (self._keep_extra or {}).items():
            if k not in prim and k != "expressions":
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitivePolylineDialog(QDialog, _DisplayMixin):
    """Add/edit a 3-D ``wire`` or ``curve`` primitive.

    Both kinds store a polyline of arbitrary 3-D points.  A wire additionally
    has a radius (CSXCAD ``AddWire``); a curve is a zero-thickness PEC curve
    (``AddCurve``).
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        kind: str = "wire",
        coord_system: str = "cartesian",
        display: dict | None = None,
        unit: str = "mm",
        size: tuple[int, int] | None = None,
        variables: dict[str, float] | None = None,
    ) -> None:
        super().__init__(parent)
        kind = str(kind or "wire").lower()
        if kind not in ("wire", "curve"):
            kind = "wire"
        self._kind = kind
        self._coord_system = coord_system
        self._unit = unit or "mm"
        self.setWindowTitle(
            "Wire primitive" if kind == "wire" else "Curve primitive"
        )
        _fit_dialog(self, tuple(size) if size else (620, 500))
        p = prim or {
            "name": "wire1" if kind == "wire" else "curve1",
            "material": "copper",
            "priority": 10,
            "radius": 0.5,
            "points": [[0.0, 0.0, 0.0], [20.0, 0.0, 0.0]],
        }
        self._variables = _variables_for(parent, variables)
        self._expressions = dict(p.get("expressions") or {}) if prim else {}
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None

        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", p.get("name", kind + "1"))))
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        self.cmb_mat.addItems(["copper", "substrate", "vacuum"])
        self.cmb_mat.setCurrentText(str(p.get("material", "copper")))
        self.spn_pri = spin_box()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 10)))
        form.addRow("Name", self.ed_name)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)

        self.spn_radius: VariableField | None = None
        if kind == "wire":
            self.spn_radius = VariableField(
                float(p.get("radius", 0.5) or 0.5),
                variables=self._variables,
                label="Wire radius",
                minimum=1e-3,
                maximum=1_000.0,
            )
            expr_radius = _expression_for(self._expressions, "radius")
            if expr_radius:
                self.spn_radius.setExpression(expr_radius)
            self.spn_radius.setToolTip(
                "Wire radius in the current drawing unit. CSXCAD AddWire\n"
                "sweeps a circular cross-section around the polyline."
            )
            form.addRow("Wire radius", self.spn_radius)

        self.ed_points = QPlainTextEdit()
        self.ed_points.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        pts = p.get("points") or []
        point_lines: list[str] = []
        for point_index, pt in enumerate(pts):
            if len(pt) < 3:
                continue
            values: list[str] = []
            for axis_index in range(3):
                expr = _expression_for(
                    self._expressions, f"points.{point_index}.{axis_index}"
                )
                values.append(
                    expr if expr else format_number(float(pt[axis_index]))
                )
            point_lines.append(", ".join(values))
        self.ed_points.setPlainText("\n".join(point_lines))
        self.ed_points.setPlaceholderText(
            "x, y, z  (one 3-D point per line, >= 2 points)"
        )
        self.ed_points.setMinimumHeight(180)
        self.ed_points.setToolTip(
            "One point per line: x, y, z in the current drawing unit.\n"
            "The points are connected in order; at least two are required."
        )
        form.addRow("Points (3-D)", self.ed_points)
        self._add_display(form, display)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _parse_points_variables(
        self, text: str, *, strict: bool
    ) -> tuple[list[list[float]], dict[str, Any], list[str]]:
        points: list[list[float]] = []
        expressions: dict[str, Any] = {}
        errors: list[str] = []
        for line_number, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = _split_point_line(line)
            if len(parts) < 3:
                if strict:
                    errors.append(
                        f"Line {line_number}: expected 'x, y, z' (one point per line)."
                    )
                continue
            values: list[float] = []
            source: list[str] = []
            failed = False
            for axis_index, axis_name in enumerate(("x", "y", "z")):
                token = parts[axis_index]
                try:
                    value = evaluate_expression(
                        token,
                        self._variables,
                        context=f"Point {len(points) + 1} {axis_name}",
                    )
                except VariableError as e:
                    if strict:
                        errors.append(str(e))
                    failed = True
                    break
                if not math.isfinite(value):
                    if strict:
                        errors.append(
                            f"Point {len(points) + 1} {axis_name}: value is not finite."
                        )
                    failed = True
                    break
                values.append(value)
                source.append(token)
            if failed:
                continue
            point_index = len(points)
            for axis_index, token in enumerate(source):
                if parse_number(token) is None:
                    expressions[f"points.{point_index}.{axis_index}"] = expression_entry(
                        token
                    )
            points.append(values)
        return points, expressions, errors

    @staticmethod
    def _parse_points(text: str) -> list[list[float]]:
        """Numeric-only compatibility parser (no variables)."""
        pts: list[list[float]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = _split_point_line(line)
            if len(parts) < 3:
                continue
            try:
                vals = [float(parts[0]), float(parts[1]), float(parts[2])]
            except ValueError:
                continue
            if all(math.isfinite(v) for v in vals):
                pts.append(vals)
        return pts

    def _accept(self) -> None:
        vals = _read_checked(
            self,
            "Wire/Curve primitive",
            lambda: (
                float(self.spn_radius.value())
                if self.spn_radius is not None
                else 0.0
            ),
        )
        if vals is None:
            return
        radius = max(0.0, vals)
        pts, point_expressions, point_errors = self._parse_points_variables(
            self.ed_points.toPlainText(), strict=True
        )
        if point_errors:
            QMessageBox.warning(
                self,
                "Wire/Curve primitive",
                "\n".join(point_errors[:8]),
            )
            return
        if len(pts) < 2:
            QMessageBox.warning(
                self,
                "Wire/Curve primitive",
                "A wire or curve needs at least 2 points "
                "(one \"x, y, z\" per line).",
            )
            return
        xs = [pt[0] for pt in pts]
        ys = [pt[1] for pt in pts]
        zs = [pt[2] for pt in pts]
        start = [min(xs), min(ys), min(zs)]
        stop = [max(xs), max(ys), max(zs)]
        if self._kind == "wire":
            start = [v - radius for v in start]
            stop = [v + radius for v in stop]
        prim: dict[str, Any] = {
            "kind": self._kind,
            "name": self.ed_name.text().strip()
            or ("wire1" if self._kind == "wire" else "curve1"),
            "material": self.cmb_mat.currentText().strip() or "copper",
            "priority": self.spn_pri.value(),
            "points": pts,
            "start": start,
            "stop": stop,
        }
        expressions = dict(point_expressions)
        if self._kind == "wire":
            prim["radius"] = radius
            if self.spn_radius is not None:
                expressions.update(
                    variable_field_expressions({"radius": self.spn_radius})
                )
        if expressions:
            prim["expressions"] = expressions
        for k, v in (self._keep_extra or {}).items():
            if k not in prim and k != "expressions":
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitiveWireDialog(PrimitivePolylineDialog):
    def __init__(self, parent=None, **kwargs) -> None:  # noqa: ANN001
        kwargs["kind"] = "wire"
        super().__init__(parent, **kwargs)


class PrimitiveCurveDialog(PrimitivePolylineDialog):
    def __init__(self, parent=None, **kwargs) -> None:  # noqa: ANN001
        kwargs["kind"] = "curve"
        super().__init__(parent, **kwargs)


class DumpDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        dump: DumpDef | None = None,
        coord_system: str = "cartesian",
        fit_box: tuple[list[float], list[float]] | None = None,
        nf2ff_box: tuple[list[float], list[float]] | None = None,
        variables: dict[str, float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Field dump")
        self.resize(440, 620)
        self._coord_system = coord_system
        self._fit_box = fit_box
        self._nf2ff_box = nf2ff_box
        self._variables = _variables_for(parent, variables)
        d = dump or DumpDef(name="Et", quantity="E", frequency=2.45e9)
        self._expressions = dict(getattr(d, "expressions", {}) or {})
        self.result: DumpDef | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(d.name)
        self.cmb_qty = QComboBox()
        self.cmb_qty.addItems(DUMP_QTY)
        self.cmb_qty.setCurrentText(d.quantity)
        self.cmb_qty.setToolTip(
            "Field type. J = electric current density (CSXCAD 12), "
            "Jtot = total current density rot(H) (13).\n"
            "SAR = local SAR (20), SAR1g/SAR10g = averaged SAR (21/22), "
            "SARraw = raw SAR data (29).\n"
            "SAR dumps are frequency-domain only; custom exposes the numeric "
            "CSXCAD DumpType."
        )
        self.spn_custom_type = spin_box()
        self.spn_custom_type.setRange(0, 99)
        self.spn_custom_type.setValue(int(getattr(d, "custom_dump_type", 10) or 10))
        self.spn_custom_type.setToolTip(
            "Numeric CSXCAD DumpType when Quantity = custom.\n"
            "Examples: 12 = J, 21 = 1 g SAR, 29 = raw SAR."
        )
        self.chk_fd = QCheckBox("Frequency domain")
        self.chk_fd.setChecked(bool(d.frequency_domain))
        self.chk_en = QCheckBox("Enabled")
        self.chk_en.setChecked(bool(d.enabled))
        self.spn_freq = double_spin_box()
        self.spn_freq.setKeyboardTracking(False)
        from app.ui.freq_units import configure_freq_spin, hz_to_freq, unit_combo_items

        self.cmb_freq_unit = QComboBox()
        unit_combo_items(self.cmb_freq_unit)
        self.cmb_freq_unit.setCurrentText("GHz")
        self._freq_unit_prev = "GHz"
        configure_freq_spin(self.spn_freq, "GHz")
        self.spn_freq.setValue(hz_to_freq(float(d.frequency or 2.45e9), "GHz"))
        self.cmb_file_type = QComboBox()
        for label, val in (("HDF5 (default)", 1), ("VTK (.vtr)", 0)):
            self.cmb_file_type.addItem(label, val)
        self.cmb_file_type.setCurrentIndex(0 if int(getattr(d, "file_type", 1)) else 1)
        self.cmb_file_type.setToolTip(
            "CSXCAD 'FileType': HDF5 (.h5, default) or VTK (.vtr written by openEMS)."
        )
        self.cmb_mode = QComboBox()
        for label, val in (("Node (default)", 1), ("None", 0), ("Cell", 2)):
            self.cmb_mode.addItem(label, val)
        self.cmb_mode.setCurrentIndex({1: 0, 0: 1, 2: 2}.get(int(getattr(d, "dump_mode", 1)), 0))
        self.cmb_mode.setToolTip(
            "CSXCAD 'DumpMode': no / node (default) / cell interpolation."
        )
        form.addRow("Name", self.ed_name)
        form.addRow("Quantity", self.cmb_qty)
        form.addRow("Custom DumpType", self.spn_custom_type)
        form.addRow("File type", self.cmb_file_type)
        form.addRow("Interpolation", self.cmb_mode)
        form.addRow("Frequency unit", self.cmb_freq_unit)
        form.addRow("Frequency", self.spn_freq)
        self.ed_freqs = QLineEdit()
        self.ed_freqs.setPlaceholderText("e.g. 2.4 GHz, 2.45 GHz — blank = use Frequency")
        self.ed_freqs.setToolTip(
            "Optional list of frequencies for a frequency-domain dump (overrides\n"
            "'Frequency'). Comma-separated, each with Hz/kHz/MHz/GHz."
        )
        if getattr(d, "frequencies", None):
            from app.ui.freq_units import fmt_hz

            self.ed_freqs.setText(", ".join(fmt_hz(f) for f in d.frequencies))
        form.addRow("Frequencies", self.ed_freqs)
        sub = list(getattr(d, "sub_sampling", [1, 1, 1]) or [1, 1, 1])
        self.spn_sub = []
        sub_row = QHBoxLayout()
        for i, ax in enumerate("xyz"):
            sp = spin_box()
            sp.setRange(1, 100000)
            sp.setValue(int(sub[i]) if i < len(sub) else 1)
            sp.setToolTip(
                f"SPATIAL sub-sampling on axis {ax} (CSXCAD 'sub_sampling'): "
                "1 = every mesh line (full resolution), N = every Nth mesh line\n"
                "(coarser, smaller dump). This does NOT set the time step; a\n"
                "time-domain dump stores every solver step automatically."
            )
            self.spn_sub.append(sp)
            sub_row.addWidget(QLabel(ax))
            sub_row.addWidget(sp)
        sub_row.addStretch(1)
        form.addRow("Spatial sub-sampling", sub_row)
        opt = list(getattr(d, "opt_resolution", None) or [0.0, 0.0, 0.0])
        self.spn_opt = []
        opt_row = QHBoxLayout()
        for i, ax in enumerate("xyz"):
            sp = double_spin_box()
            sp.setRange(0.0, 1_000_000.0)
            sp.setDecimals(4)
            sp.setValue(float(opt[i]) if i < len(opt) else 0.0)
            sp.setToolTip(
                f"Optional spatial dump resolution on {ax} in drawing units.\n"
                "0 = use the mesh resolution. Values > 0 resample the dump "
                "box with CSXCAD OptResolution."
            )
            self.spn_opt.append(sp)
            opt_row.addWidget(QLabel(ax))
            opt_row.addWidget(sp)
        opt_row.addStretch(1)
        form.addRow("Opt. resolution", opt_row)
        form.addRow(self.chk_fd)
        form.addRow(self.chk_en)
        self.spn_start = _vec3_row(
            form,
            "Start",
            d.start,
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="start",
        )
        self.spn_stop = _vec3_row(
            form,
            "Stop",
            d.stop,
            coord_system=coord_system,
            variables=self._variables,
            expressions=self._expressions,
            prefix="stop",
        )

        def _write(spins, vals) -> None:
            for sp, v in zip(spins, vals):
                sp.setValue(float(v))

        def _use(box) -> None:
            if box:
                _write(self.spn_start, box[0])
                _write(self.spn_stop, box[1])

        box_row = QHBoxLayout()
        self.btn_fit = QPushButton("Fit to geometry")
        self.btn_fit.setToolTip("Cover the whole structure (+ margin) — a volume.")
        self.btn_fit.setEnabled(fit_box is not None)
        self.btn_fit.clicked.connect(lambda: _use(self._fit_box))
        self.btn_nf2ff = QPushButton("Use NF2FF box")
        self.btn_nf2ff.setToolTip("Use the NF2FF integration box as the dump region.")
        self.btn_nf2ff.setEnabled(nf2ff_box is not None)
        self.btn_nf2ff.clicked.connect(lambda: _use(self._nf2ff_box))
        box_row.addWidget(self.btn_fit)
        box_row.addWidget(self.btn_nf2ff)
        box_row.addStretch(1)
        form.addRow(box_row)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        def _on_unit(unit: str) -> None:
            from app.ui.freq_units import configure_freq_spin, hz_to_freq, spin_hz

            if unit not in {"Hz", "kHz", "MHz", "GHz"}:
                return
            hz = spin_hz(self.spn_freq, self._freq_unit_prev)
            self._freq_unit_prev = unit
            configure_freq_spin(self.spn_freq, unit)
            self.spn_freq.setValue(hz_to_freq(hz, unit))

        self.cmb_freq_unit.currentTextChanged.connect(_on_unit)

        def _sync_freq_enabled() -> None:
            on = self.chk_fd.isChecked()
            for w in (self.spn_freq, self.cmb_freq_unit, self.ed_freqs):
                w.setEnabled(on)

        self.chk_fd.toggled.connect(lambda _on: _sync_freq_enabled())

        def _sync_qty() -> None:
            qty = self.cmb_qty.currentText()
            self.spn_custom_type.setEnabled(qty == "custom")
            # SAR dumps only exist in the frequency domain (types 20..22, 29).
            is_sar = qty in {"SAR", "SAR1g", "SAR10g", "SARraw"}
            if is_sar:
                self.chk_fd.setChecked(True)
                self.chk_fd.setEnabled(False)
            else:
                self.chk_fd.setEnabled(True)
            _sync_freq_enabled()

        self.cmb_qty.currentTextChanged.connect(lambda *_: _sync_qty())
        _sync_qty()

    def _accept(self) -> None:
        from app.postproc.nf2ff import parse_freq_list
        from app.ui.freq_units import spin_hz

        vals = _read_checked(
            self,
            "Field dump",
            lambda: (
                _vec3_read(self.spn_start, self._coord_system),
                _vec3_read(self.spn_stop, self._coord_system),
            ),
        )
        if vals is None:
            return
        start, stop = vals
        unit = getattr(self, "_freq_unit_prev", "GHz") or "GHz"
        freqs = parse_freq_list(self.ed_freqs.text()) or None
        opt = [float(s.value()) for s in self.spn_opt]
        opt_res = opt if all(v > 0.0 for v in opt) else None
        expressions = variable_field_expressions(
            {
                **{f"start.{i}": f for i, f in enumerate(self.spn_start)},
                **{f"stop.{i}": f for i, f in enumerate(self.spn_stop)},
            }
        )
        self.result = DumpDef(
            name=self.ed_name.text().strip() or "dump",
            quantity=self.cmb_qty.currentText(),  # type: ignore[arg-type]
            start=start,
            stop=stop,
            frequency_domain=self.chk_fd.isChecked(),
            frequency=spin_hz(self.spn_freq, unit),
            enabled=self.chk_en.isChecked(),
            file_type=int(self.cmb_file_type.currentData()),
            dump_mode=int(self.cmb_mode.currentData()),
            sub_sampling=[int(s.value()) for s in self.spn_sub],
            frequencies=freqs,
            custom_dump_type=int(self.spn_custom_type.value()),
            opt_resolution=opt_res,
            expressions=expressions,
        )
        self.accept()


class NF2FFBoxDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        start=None,
        stop=None,
        center=None,
        *,
        expressions: dict | None = None,
        variables: dict[str, float] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("NF2FF box")
        self.resize(360, 340)
        start = list(start or [-40, -40, 0])
        stop = list(stop or [40, 40, 40])
        self._variables = _variables_for(parent, variables)
        self._expressions = dict(expressions or {})
        self.result_start: list[float] | None = None
        self.result_stop: list[float] | None = None
        self.result_expressions: dict[str, Any] = {}
        form = QFormLayout(self)
        self.spn_start = _vec3_row(
            form,
            "Start",
            start,
            variables=self._variables,
            expressions=self._expressions,
            prefix="start",
        )
        self.spn_stop = _vec3_row(
            form,
            "Stop",
            stop,
            variables=self._variables,
            expressions=self._expressions,
            prefix="stop",
        )
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        vals = _read_checked(
            self,
            "NF2FF box",
            lambda: (
                _vec3_read(self.spn_start),
                _vec3_read(self.spn_stop),
            ),
        )
        if vals is None:
            return
        self.result_start, self.result_stop = vals
        self.result_expressions = variable_field_expressions(
            {
                **{f"start.{i}": f for i, f in enumerate(self.spn_start)},
                **{f"stop.{i}": f for i, f in enumerate(self.spn_stop)},
            }
        )
        self.accept()
