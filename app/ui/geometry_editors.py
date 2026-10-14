"""Dialogs for generic antenna/PCB projects: ports, materials, primitives, dumps."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models.project import DumpDef, GeomAssignment, MaterialDef, PortDef

MATERIAL_KINDS = ["pec", "dielectric", "conducting_sheet", "lumped_rlc", "ignore"]
PORT_TYPES = ["lumped", "msl", "rect_waveguide"]
DUMP_QTY = ["E", "H", "J", "Jtot", "D", "B", "SAR", "custom"]


def _vec3_row(form: QFormLayout, label: str, values: list[float], lo: float = -1e4, hi: float = 1e4):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    spins: list[QDoubleSpinBox] = []
    for i, axis in enumerate("xyz"):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(4)
        sp.setSuffix(f" {axis}")
        sp.setValue(float(values[i]) if i < len(values) else 0.0)
        spins.append(sp)
        lay.addWidget(sp)
    form.addRow(label, w)
    return spins


class PortDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, port: PortDef | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Port")
        self.resize(380, 420)
        p = port or PortDef(name="in1")
        self.result_port: PortDef | None = None
        form = QFormLayout(self)

        self.ed_name = QLineEdit(p.name)
        self.cmb_type = QComboBox()
        self.cmb_type.addItems(PORT_TYPES)
        self.cmb_type.setCurrentText(p.type)
        self.spn_r = QDoubleSpinBox()
        self.spn_r.setRange(0.1, 1e4)
        self.spn_r.setDecimals(2)
        self.spn_r.setValue(float(p.resistance or 50.0))
        self.cmb_dir = QComboBox()
        self.cmb_dir.addItems(["x", "y", "z"])
        self.cmb_dir.setCurrentText(p.direction or "z")
        self.chk_excite = QCheckBox("Excite port")
        self.chk_excite.setChecked(bool(p.excite_port))
        self.cmb_mesh = QComboBox()
        self.cmb_mesh.addItems(["xyz", "xy", "xz", "yz", "x", "y", "z", ""])
        self.cmb_mesh.setCurrentText(p.mesh_dirs or "xy")

        form.addRow("Name", self.ed_name)
        form.addRow("Type", self.cmb_type)
        form.addRow("Resistance (Ω)", self.spn_r)
        form.addRow("Direction", self.cmb_dir)
        form.addRow(self.chk_excite)
        form.addRow("Mesh dirs", self.cmb_mesh)
        self.spn_start = _vec3_row(form, "Start", p.start)
        self.spn_stop = _vec3_row(form, "Stop", p.stop)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        name = self.ed_name.text().strip() or "in1"
        self.result_port = PortDef(
            name=name,
            type=self.cmb_type.currentText(),  # type: ignore[arg-type]
            start=[float(s.value()) for s in self.spn_start],
            stop=[float(s.value()) for s in self.spn_stop],
            resistance=float(self.spn_r.value()),
            direction=self.cmb_dir.currentText(),
            excite_port=self.chk_excite.isChecked(),
            mesh_dirs=self.cmb_mesh.currentText(),
        )
        self.accept()


class AssignmentDialog(QDialog):
    def __init__(
        self,
        object_names: list[str],
        material_names: list[str],
        parent: QWidget | None = None,
        assignment: GeomAssignment | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Material assignment")
        self.resize(360, 220)
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
        self.spn_pri = QSpinBox()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(a.priority))
        form.addRow("Object", self.cmb_obj)
        form.addRow("Role", self.cmb_role)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

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
        self.spn_eps = QDoubleSpinBox()
        self.spn_eps.setRange(1.0, 100.0)
        self.spn_eps.setDecimals(4)
        self.spn_eps.setValue(float(m.epsilon or 1.0))
        self.spn_kappa = QDoubleSpinBox()
        self.spn_kappa.setRange(0.0, 1e9)
        self.spn_kappa.setDecimals(6)
        self.spn_kappa.setValue(float(m.kappa or 0.0))
        self.spn_tand = QDoubleSpinBox()
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


class PrimitiveBoxDialog(QDialog):
    """Add/edit a box primitive (any antenna/PCB metal or dielectric volume)."""

    def __init__(self, parent: QWidget | None = None, prim: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Box primitive")
        self.resize(380, 360)
        p = prim or {"name": "box1", "material": "copper", "start": [0, 0, 0], "stop": [10, 10, 0.5]}
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "box1")))
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        mats = ["copper", "substrate", "vacuum", "pec", "dielectric"]
        self.cmb_mat.addItems(mats)
        self.cmb_mat.setCurrentText(str(p.get("material", "copper")))
        self.spn_pri = QSpinBox()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 10)))
        self.chk_edges = QCheckBox("Snap edges to mesh (edges2grid)")
        self.chk_edges.setChecked(bool(p.get("edges2grid")))
        form.addRow("Name", self.ed_name)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        form.addRow(self.chk_edges)
        self.spn_start = _vec3_row(form, "Start", list(p.get("start", [0, 0, 0])))
        self.spn_stop = _vec3_row(form, "Stop", list(p.get("stop", [10, 10, 0.5])))
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        name = self.ed_name.text().strip() or "box1"
        prim: dict[str, Any] = {
            "kind": "box",
            "name": name,
            "material": self.cmb_mat.currentText().strip() or "copper",
            "priority": self.spn_pri.value(),
            "start": [float(s.value()) for s in self.spn_start],
            "stop": [float(s.value()) for s in self.spn_stop],
        }
        if self.chk_edges.isChecked():
            prim["edges2grid"] = "xy"
        # keep imported STL fields (path, mesh_path, …) when editing
        for k, v in (self._keep_extra or {}).items():
            if k not in prim:
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitiveCylinderDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, prim: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cylinder primitive")
        self.resize(380, 320)
        p = prim or {"name": "cyl1", "material": "copper", "start": [0, 0, 0], "stop": [0, 0, 5], "radius": 0.5}
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "cyl1")))
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        self.cmb_mat.addItems(["copper", "substrate", "vacuum"])
        self.cmb_mat.setCurrentText(str(p.get("material", "copper")))
        self.spn_pri = QSpinBox()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 10)))
        self.spn_radius = QDoubleSpinBox()
        self.spn_radius.setRange(1e-4, 1e3)
        self.spn_radius.setDecimals(4)
        self.spn_radius.setValue(float(p.get("radius", 0.5)))
        form.addRow("Name", self.ed_name)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        form.addRow("Radius", self.spn_radius)
        self.spn_start = _vec3_row(form, "Start (axis A)", list(p.get("start", [0, 0, 0])))
        self.spn_stop = _vec3_row(form, "Stop (axis B)", list(p.get("stop", [0, 0, 5])))
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        prim: dict[str, Any] = {
            "kind": "cylinder",
            "name": self.ed_name.text().strip() or "cyl1",
            "material": self.cmb_mat.currentText().strip() or "copper",
            "priority": self.spn_pri.value(),
            "start": [float(s.value()) for s in self.spn_start],
            "stop": [float(s.value()) for s in self.spn_stop],
            "radius": float(self.spn_radius.value()),
        }
        for k, v in (self._keep_extra or {}).items():
            if k not in prim:
                prim[k] = v
        self.result = prim
        self.accept()


class DumpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, dump: DumpDef | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Field dump")
        self.resize(360, 380)
        d = dump or DumpDef(name="Et", quantity="E", frequency=2.45e9)
        self.result: DumpDef | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(d.name)
        self.cmb_qty = QComboBox()
        self.cmb_qty.addItems(DUMP_QTY)
        self.cmb_qty.setCurrentText(d.quantity)
        self.chk_fd = QCheckBox("Frequency domain")
        self.chk_fd.setChecked(bool(d.frequency_domain))
        self.chk_en = QCheckBox("Enabled")
        self.chk_en.setChecked(bool(d.enabled))
        self.spn_freq = QDoubleSpinBox()
        self.spn_freq.setKeyboardTracking(False)
        from app.ui.freq_units import configure_freq_spin, hz_to_freq, unit_combo_items

        self.cmb_freq_unit = QComboBox()
        unit_combo_items(self.cmb_freq_unit)
        self.cmb_freq_unit.setCurrentText("GHz")
        self._freq_unit_prev = "GHz"
        configure_freq_spin(self.spn_freq, "GHz")
        self.spn_freq.setValue(hz_to_freq(float(d.frequency or 2.45e9), "GHz"))
        form.addRow("Name", self.ed_name)
        form.addRow("Quantity", self.cmb_qty)
        form.addRow("Frequency unit", self.cmb_freq_unit)
        form.addRow("Frequency", self.spn_freq)
        form.addRow(self.chk_fd)
        form.addRow(self.chk_en)
        self.spn_start = _vec3_row(form, "Start", d.start)
        self.spn_stop = _vec3_row(form, "Stop", d.stop)
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

    def _accept(self) -> None:
        from app.ui.freq_units import spin_hz

        unit = getattr(self, "_freq_unit_prev", "GHz") or "GHz"
        self.result = DumpDef(
            name=self.ed_name.text().strip() or "dump",
            quantity=self.cmb_qty.currentText(),  # type: ignore[arg-type]
            start=[float(s.value()) for s in self.spn_start],
            stop=[float(s.value()) for s in self.spn_stop],
            frequency_domain=self.chk_fd.isChecked(),
            frequency=spin_hz(self.spn_freq, unit),
            enabled=self.chk_en.isChecked(),
        )
        self.accept()


class NF2FFBoxDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, start=None, stop=None, center=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("NF2FF box")
        self.resize(360, 340)
        start = list(start or [-40, -40, 0])
        stop = list(stop or [40, 40, 40])
        self.result_start: list[float] | None = None
        self.result_stop: list[float] | None = None
        form = QFormLayout(self)
        self.spn_start = _vec3_row(form, "Start", start)
        self.spn_stop = _vec3_row(form, "Stop", stop)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        self.result_start = [float(s.value()) for s in self.spn_start]
        self.result_stop = [float(s.value()) for s in self.spn_stop]
        self.accept()
