"""Dialogs for generic antenna/PCB projects: ports, materials, primitives, dumps."""

from __future__ import annotations

import math
import re
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.models.project import DumpDef, GeomAssignment, MaterialDef, PortDef

MATERIAL_KINDS = ["pec", "dielectric", "conducting_sheet", "lumped_rlc", "ignore"]
PORT_TYPES = ["lumped", "msl", "rect_waveguide"]
DUMP_QTY = ["E", "H", "J", "Jtot", "D", "B", "SAR", "custom"]


def _axis_suffixes(coord_system: str) -> tuple[str, str, str]:
    if coord_system == "cylindrical":
        return "ρ", "φ", "z"
    return "x", "y", "z"


def _vec3_row(
    form: QFormLayout,
    label: str,
    values: list[float],
    lo: float = -1e4,
    hi: float = 1e4,
    coord_system: str = "cartesian",
):
    """Three stacked spins (one per axis). Cylindrical: suffixes ρ/φ/z and
    the phi spin shows DEGREES (project values are always radians)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    spins: list[QDoubleSpinBox] = []
    polar = coord_system == "cylindrical"
    for i, axis in enumerate(_axis_suffixes(coord_system)):
        sp = QDoubleSpinBox()
        sp.setDecimals(4)
        raw = float(values[i]) if i < len(values) else 0.0
        if polar and i == 1:
            # phi is radians in the project; show degrees (a full turn +-36000 deg)
            sp.setRange(-36000.0, 36000.0)
            sp.setSuffix(" °")
            sp.setValue(math.degrees(raw))
        else:
            sp.setRange(lo, hi)
            sp.setSuffix(f" {axis}")
            sp.setValue(raw)
        spins.append(sp)
        lay.addWidget(sp)
    form.addRow(label, w)
    return spins


def _axis_combo(current: str) -> QComboBox:
    """x/y/z combo with an explicit '(auto)' entry (stored as empty string)."""
    cmb = QComboBox()
    for label, value in (("(auto)", ""), ("x", "x"), ("y", "y"), ("z", "z")):
        cmb.addItem(label, value)
    idx = cmb.findData((current or "").strip().lower())
    cmb.setCurrentIndex(idx if idx >= 0 else 0)
    return cmb


def _vec3_read(spins: list[QDoubleSpinBox], coord_system: str = "cartesian") -> list[float]:
    """Spins -> project values (cylindrical phi: degrees -> radians)."""
    vals = [float(s.value()) for s in spins]
    if coord_system == "cylindrical" and len(vals) == 3:
        vals[1] = math.radians(vals[1])
    return vals


class PortDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        port: PortDef | None = None,
        coord_system: str = "cartesian",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Port")
        self.resize(380, 420)
        self._coord_system = coord_system
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
        self.cmb_mesh = QComboBox()
        self.cmb_mesh.addItems(["xyz", "xy", "xz", "yz", "x", "y", "z", ""])
        self.cmb_mesh.setCurrentText(p.mesh_dirs or "xy")

        form.addRow("Name", self.ed_name)
        form.addRow("Type", self.cmb_type)
        form.addRow("Resistance (Ω)", self.spn_r)
        form.addRow("Direction", self.cmb_dir)
        form.addRow("Propagation dir (MSL)", self.cmb_prop)
        form.addRow("Excitation dir (MSL)", self.cmb_exc)
        form.addRow(self.chk_excite)
        form.addRow("Mesh dirs", self.cmb_mesh)
        self.spn_start = _vec3_row(form, "Start", p.start, coord_system=coord_system)
        self.spn_stop = _vec3_row(form, "Stop", p.stop, coord_system=coord_system)
        if coord_system == "cylindrical":
            self.cmb_dir.setToolTip(
                "Excitation/normal axis letter: x=ρ, y=φ, z (openEMS always\n"
                "uses x/y/z letters, also on a cylindrical mesh)."
            )
            self.cmb_mesh.setToolTip("Mesh axis letters: x=ρ, y=φ, z")

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
            start=_vec3_read(self.spn_start, self._coord_system),
            stop=_vec3_read(self.spn_stop, self._coord_system),
            resistance=float(self.spn_r.value()),
            direction=self.cmb_dir.currentText(),
            prop_direction=self.cmb_prop.currentData() or "",
            exc_direction=self.cmb_exc.currentData() or "",
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

    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        coord_system: str = "cartesian",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Box primitive")
        self.resize(380, 360)
        self._coord_system = coord_system
        if coord_system == "cylindrical":
            # In cylindrical mode a "box" is a wedge in (rho, phi, z).
            self.setWindowTitle("Wedge primitive (cylindrical)")
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
        self.spn_start = _vec3_row(
            form, "Start", list(p.get("start", [0, 0, 0])), coord_system=coord_system
        )
        self.spn_stop = _vec3_row(
            form, "Stop", list(p.get("stop", [10, 10, 0.5])), coord_system=coord_system
        )
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
            "start": _vec3_read(self.spn_start, self._coord_system),
            "stop": _vec3_read(self.spn_stop, self._coord_system),
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
    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        coord_system: str = "cartesian",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Cylinder primitive")
        self.resize(380, 320)
        self._coord_system = coord_system
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
        self.spn_start = _vec3_row(
            form, "Start (axis A)", list(p.get("start", [0, 0, 0])), coord_system=coord_system
        )
        self.spn_stop = _vec3_row(
            form, "Stop (axis B)", list(p.get("stop", [0, 0, 5])), coord_system=coord_system
        )
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
            "start": _vec3_read(self.spn_start, self._coord_system),
            "stop": _vec3_read(self.spn_stop, self._coord_system),
            "radius": float(self.spn_radius.value()),
        }
        for k, v in (self._keep_extra or {}).items():
            if k not in prim:
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitiveSphereDialog(QDialog):
    """Add/edit a sphere primitive (exact openEMS AddSphere)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        prim: dict[str, Any] | None = None,
        coord_system: str = "cartesian",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sphere primitive")
        self.resize(380, 340)
        self._coord_system = coord_system
        p = prim or {
            "name": "sphere1",
            "material": "copper",
            "center": [0, 0, 0],
            "radius": 5.0,
        }
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "sphere1")))
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
        self.spn_radius.setValue(float(p.get("radius", 5.0)))
        form.addRow("Name", self.ed_name)
        form.addRow("Material", self.cmb_mat)
        form.addRow("Priority", self.spn_pri)
        form.addRow("Radius", self.spn_radius)
        self.spn_center = _vec3_row(
            form, "Center", list(p.get("center", [0, 0, 0])), coord_system=coord_system
        )
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    def _accept(self) -> None:
        center = _vec3_read(self.spn_center, self._coord_system)
        radius = float(self.spn_radius.value())
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
        for k, v in (self._keep_extra or {}).items():
            if k not in prim:
                prim[k] = v
        self.result = prim
        self.accept()


class PrimitivePolygonDialog(QDialog):
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
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Polygon primitive")
        self.resize(420, 460)
        self._coord_system = coord_system
        p = prim or {
            "name": "poly1",
            "material": "copper",
            "axis": "z",
            "coord": 0.0,
            "length": 0.0,
            "points": [[0, 0], [20, 0], [20, 10], [0, 10]],
        }
        self._keep_extra = dict(p) if prim else {}
        self.result: dict[str, Any] | None = None
        form = QFormLayout(self)
        self.ed_name = QLineEdit(str(p.get("name", "poly1")))
        self.cmb_mat = QComboBox()
        self.cmb_mat.setEditable(True)
        self.cmb_mat.addItems(["copper", "substrate", "vacuum"])
        self.cmb_mat.setCurrentText(str(p.get("material", "copper")))
        self.spn_pri = QSpinBox()
        self.spn_pri.setRange(0, 200)
        self.spn_pri.setValue(int(p.get("priority", 10)))
        self.cmb_axis = QComboBox()
        self.cmb_axis.addItems(["x", "y", "z"])
        self.cmb_axis.setCurrentText(str(p.get("axis", "z")))
        self.spn_coord = QDoubleSpinBox()
        self.spn_coord.setRange(-1e4, 1e4)
        self.spn_coord.setDecimals(4)
        self.spn_coord.setValue(float(p.get("coord", 0.0)))
        self.spn_length = QDoubleSpinBox()
        self.spn_length.setRange(0.0, 1e4)
        self.spn_length.setDecimals(4)
        self.spn_length.setValue(float(p.get("length") or 0.0))
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
        pts = p.get("points") or []
        self.ed_points.setPlainText(
            "\n".join(
                f"{float(pt[0]):g}, {float(pt[1]):g}"
                for pt in pts
                if len(pt) >= 2
            )
        )
        self.ed_points.setPlaceholderText("x, y  (one point per line, >= 3 points)")
        form.addRow("Points", self.ed_points)
        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

    @staticmethod
    def _parse_points(text: str) -> list[list[float]]:
        pts: list[list[float]] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [s for s in re.split(r"[,;\s]+", line) if s]
            if len(parts) < 2:
                continue
            try:
                pts.append([float(parts[0]), float(parts[1])])
            except ValueError:
                continue
        return pts

    def _accept(self) -> None:
        pts = self._parse_points(self.ed_points.toPlainText())
        if len(pts) < 3:
            QMessageBox.warning(
                self,
                "Polygon primitive",
                "A polygon needs at least 3 points (one \"x, y\" per line).",
            )
            return
        axis = self.cmb_axis.currentText()
        axis_idx = "xyz".index(axis) if axis in "xyz" else 2
        coord = float(self.spn_coord.value())
        length = max(0.0, float(self.spn_length.value()))
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
        for k, v in (self._keep_extra or {}).items():
            if k not in prim:
                prim[k] = v
        self.result = prim
        self.accept()


class DumpDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        dump: DumpDef | None = None,
        coord_system: str = "cartesian",
        fit_box: tuple[list[float], list[float]] | None = None,
        nf2ff_box: tuple[list[float], list[float]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Field dump")
        self.resize(380, 470)
        self._coord_system = coord_system
        self._fit_box = fit_box
        self._nf2ff_box = nf2ff_box
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
        form.addRow(self.chk_fd)
        form.addRow(self.chk_en)
        self.spn_start = _vec3_row(form, "Start", d.start, coord_system=coord_system)
        self.spn_stop = _vec3_row(form, "Stop", d.stop, coord_system=coord_system)

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
        _sync_freq_enabled()

    def _accept(self) -> None:
        from app.postproc.nf2ff import parse_freq_list
        from app.ui.freq_units import spin_hz

        unit = getattr(self, "_freq_unit_prev", "GHz") or "GHz"
        freqs = parse_freq_list(self.ed_freqs.text()) or None
        self.result = DumpDef(
            name=self.ed_name.text().strip() or "dump",
            quantity=self.cmb_qty.currentText(),  # type: ignore[arg-type]
            start=_vec3_read(self.spn_start, self._coord_system),
            stop=_vec3_read(self.spn_stop, self._coord_system),
            frequency_domain=self.chk_fd.isChecked(),
            frequency=spin_hz(self.spn_freq, unit),
            enabled=self.chk_en.isChecked(),
            file_type=int(self.cmb_file_type.currentData()),
            dump_mode=int(self.cmb_mode.currentData()),
            frequencies=freqs,
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
