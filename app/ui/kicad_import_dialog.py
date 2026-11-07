"""Native KiCad ``.kicad_pcb`` import dialog.

The board file supplies the real copper layer stackup and via spans.  This
dialog only controls how that geometry is turned into openEMS primitives and
what to use when a board has no custom stackup.
"""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.freecad_io.kicad_import import KiCadInspection, KiCadStackupLayer
from app.models.project import KiCadSpec, Project


def _spin(
    lo: float,
    hi: float,
    decimals: int,
    step: float,
    suffix: str = " mm",
) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setKeyboardTracking(False)
    return box


class KiCadImportDialog(QDialog):
    """Modal editor for :class:`KiCadSpec` before importing a board."""

    def __init__(
        self,
        parent: QWidget | None = None,
        project: Project | None = None,
        inspection: KiCadInspection | None = None,
        spec: KiCadSpec | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("KiCad PCB Import")
        self.resize(720, 720)
        self._inspection = inspection or KiCadInspection()
        base = spec
        if base is None and project is not None:
            base = getattr(project, "kicad", None)
        self._spec = copy.deepcopy(base or KiCadSpec())

        outer = QVBoxLayout(self)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        intro = QLabel(
            "Native KiCad-Import: Stackup, Via-Spans und Zonen kommen direkt "
            "aus der .kicad_pcb. Die Optionen hier steuern nur die Umsetzung "
            "in openEMS-Primitive und die Fallbacks ohne Custom-Stackup."
        )
        intro.setWordWrap(True)
        body.addWidget(intro)

        # --- detected board ------------------------------------------------
        g_board = QGroupBox("Erkanntes Board")
        fb = QVBoxLayout(g_board)
        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        fb.addWidget(self.lbl_summary)
        self.tbl_stack = QTableWidget(0, 5)
        self.tbl_stack.setHorizontalHeaderLabels(
            ["Layer", "Typ", "Dicke", "εr", "tanδ"]
        )
        self.tbl_stack.verticalHeader().setVisible(False)
        self.tbl_stack.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tbl_stack.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.tbl_stack.setMinimumHeight(150)
        fb.addWidget(self.tbl_stack)
        body.addWidget(g_board)

        # --- stackup/geometry ---------------------------------------------
        g_stack = QGroupBox("Stackup / Umsetzung")
        fs = QFormLayout(g_stack)
        self.chk_stack = QCheckBox("Stackup aus der KiCad-Datei verwenden")
        self.spin_board = _spin(0.05, 20.0, 3, 0.1)
        self.spin_copper = _spin(0.001, 2.0, 3, 0.005)
        self.cmb_copper_mode = QComboBox()
        self.cmb_copper_mode.addItem("Folie (AddPolygon, empfohlen)", "sheet")
        self.cmb_copper_mode.addItem("Prisma (AddLinPoly)", "prism")
        self.cmb_copper_mode.addItem(
            "Mesh (ein STL/Polyhedron pro Kupferlayer)", "mesh"
        )
        self.cmb_copper_mode.setToolTip(
            "Mesh ist für große Zonen mit vielen Aussparungen deutlich "
            "schneller als tausende AddPolygon-Primitive; erzeugt eine "
            "STL-Datei pro Kupferlayer."
        )
        self.chk_substrate = QCheckBox("Substrat/Dielektrika erzeugen")
        self.edit_substrate = QLineEdit()
        self.spin_eps = _spin(1.0, 30.0, 2, 0.1, "")
        self.spin_tand = _spin(0.0, 1.0, 4, 0.001, "")
        self.chk_flip = QCheckBox("Y spiegeln (KiCad y-down → Gerber-Ansicht)")
        fs.addRow("", self.chk_stack)
        fs.addRow("Board-Dicke (Fallback)", self.spin_board)
        fs.addRow("Kupferdicke (Fallback)", self.spin_copper)
        fs.addRow("Kupfer-Modus", self.cmb_copper_mode)
        fs.addRow("", self.chk_substrate)
        fs.addRow("Substratname", self.edit_substrate)
        fs.addRow("εr (Fallback)", self.spin_eps)
        fs.addRow("tanδ (Fallback)", self.spin_tand)
        fs.addRow("", self.chk_flip)
        body.addWidget(g_stack)

        # --- content selection --------------------------------------------
        g_content = QGroupBox("Geometrie")
        fc = QFormLayout(g_content)
        self.chk_tracks = QCheckBox("Tracks (Segment/Arc)")
        self.chk_pads = QCheckBox("SMD-Pads")
        self.chk_vias = QCheckBox("Vias")
        self.chk_zones = QCheckBox("Gefüllte Zonen")
        self.chk_unfilled = QCheckBox("Ungfüllte Zonen als Umriss importieren")
        self.chk_pad_barrels = QCheckBox("PTH-Pad-Bohrungen als Zylinder")
        self.chk_npth = QCheckBox("NPTH als Vacuum-Zylinder")
        self.chk_suggest_ports = QCheckBox("Nach dem Import Ports vorschlagen")
        fc.addRow("", self.chk_tracks)
        fc.addRow("", self.chk_pads)
        fc.addRow("", self.chk_vias)
        fc.addRow("", self.chk_zones)
        fc.addRow("", self.chk_unfilled)
        fc.addRow("", self.chk_pad_barrels)
        fc.addRow("", self.chk_npth)
        fc.addRow("", self.chk_suggest_ports)
        body.addWidget(g_content)

        # --- vias / precision ---------------------------------------------
        g_prec = QGroupBox("Vias / Genauigkeit")
        fp = QFormLayout(g_prec)
        self.spin_plating = _spin(0.0, 0.2, 3, 0.005)
        self.spin_arc = _spin(0.001, 0.5, 3, 0.005)
        self.spin_simplify = _spin(0.0, 0.5, 3, 0.005)
        self.spin_min_area = _spin(0.0, 10.0, 4, 0.005, " mm²")
        self.chk_clip = QCheckBox("Auf Board-Umriss zuschneiden")
        fp.addRow("Plating (radial)", self.spin_plating)
        fp.addRow("Arc-Toleranz", self.spin_arc)
        fp.addRow("Vereinfachung", self.spin_simplify)
        fp.addRow("Min. Fläche", self.spin_min_area)
        fp.addRow("", self.chk_clip)
        body.addWidget(g_prec)

        note = QLabel(
            "Hinweis: Sehr große Zonen mit vielen Aussparungen werden für "
            "CSXCAD trianguliert. Bei vielen Primitiven 'Vereinfachung' "
            "erhöhen oder Zonen/Pads gezielt abwählen."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#666;")
        body.addWidget(note)
        body.addStretch(1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btn_defaults = QPushButton("Standardwerte")
        btn_defaults.clicked.connect(self._restore_defaults)
        bb.addButton(btn_defaults, QDialogButtonBox.ButtonRole.ResetRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self._load_spec()
        self._populate()
        self.chk_stack.toggled.connect(self._update_enabled)
        self.chk_zones.toggled.connect(self._update_enabled)
        self._update_enabled()

    # ------------------------------------------------------------------ #
    def _load_spec(self) -> None:
        spec = self._spec
        self.chk_stack.setChecked(bool(spec.use_stackup))
        self.spin_board.setValue(float(spec.board_thickness_mm))
        self.spin_copper.setValue(float(spec.copper_thickness_mm))
        mode = str(spec.copper_mode or "").lower()
        if mode not in ("sheet", "prism", "mesh"):
            mode = "sheet" if spec.copper_as_sheet else "prism"
        self.cmb_copper_mode.setCurrentIndex(
            max(0, self.cmb_copper_mode.findData(mode))
        )
        self.chk_substrate.setChecked(bool(spec.create_substrate))
        self.edit_substrate.setText(str(spec.substrate_material or "kicad_substrate"))
        self.spin_eps.setValue(float(spec.substrate_epsilon))
        self.spin_tand.setValue(float(spec.substrate_tand))
        self.chk_tracks.setChecked(bool(spec.include_tracks))
        self.chk_pads.setChecked(bool(spec.include_smd_pads))
        self.chk_vias.setChecked(bool(spec.include_vias))
        self.chk_zones.setChecked(bool(spec.include_zones))
        self.chk_unfilled.setChecked(bool(spec.include_unfilled_zones))
        self.chk_pad_barrels.setChecked(bool(spec.include_pad_barrels))
        self.chk_npth.setChecked(bool(spec.include_npth))
        self.chk_suggest_ports.setChecked(bool(spec.suggest_ports))
        self.chk_flip.setChecked(bool(spec.flip_y))
        self.spin_plating.setValue(float(spec.via_plating_radius_mm))
        self.spin_arc.setValue(float(spec.arc_error_mm))
        self.spin_simplify.setValue(float(spec.simplify_mm))
        self.spin_min_area.setValue(float(spec.min_area_mm2))
        self.chk_clip.setChecked(bool(spec.clip_to_outline))

    def _populate(self) -> None:
        ins = self._inspection
        self.lbl_summary.setText(
            f"{ins.board_name or 'Board'}: {len(ins.copper_layers)} Kupferlayer, "
            f"{ins.via_count} Vias, {ins.pad_count} Pads, "
            f"{ins.track_count} Tracks, {ins.zone_count} Zonen"
            + (
                f", Board-Dicke {ins.board_thickness_mm:.3f} mm"
                if ins.board_thickness_mm
                else ""
            )
        )
        stackup = list(getattr(ins, "stackup", None) or [])
        self.tbl_stack.setRowCount(len(stackup))
        for row, layer in enumerate(stackup):
            self.tbl_stack.setItem(row, 0, QTableWidgetItem(layer.name))
            self.tbl_stack.setItem(row, 1, QTableWidgetItem(layer.kind))
            self.tbl_stack.setItem(
                row,
                2,
                QTableWidgetItem(
                    f"{layer.thickness_mm:g} mm" if layer.thickness_mm else "–"
                ),
            )
            self.tbl_stack.setItem(
                row,
                3,
                QTableWidgetItem(f"{layer.epsilon:g}" if layer.epsilon else "–"),
            )
            self.tbl_stack.setItem(
                row,
                4,
                QTableWidgetItem(f"{layer.tand:g}" if layer.tand else "–"),
            )
        if not stackup:
            self.tbl_stack.setRowCount(1)
            self.tbl_stack.setSpan(0, 0, 1, 5)
            self.tbl_stack.setItem(
                0, 0, QTableWidgetItem("Kein Custom-Stackup in der Datei")
            )

    def _update_enabled(self) -> None:
        use_stackup = self.chk_stack.isChecked()
        self.spin_board.setEnabled(not use_stackup)
        self.spin_copper.setEnabled(not use_stackup)
        self.chk_unfilled.setEnabled(self.chk_zones.isChecked())

    def _restore_defaults(self) -> None:
        self._spec = KiCadSpec()
        self._load_spec()
        self._populate()
        self._update_enabled()

    def _accept(self) -> None:
        spec = self._spec
        spec.use_stackup = bool(self.chk_stack.isChecked())
        spec.board_thickness_mm = float(self.spin_board.value())
        spec.copper_thickness_mm = float(self.spin_copper.value())
        spec.copper_as_sheet = str(self.cmb_copper_mode.currentData() or "sheet") == "sheet"
        spec.copper_mode = str(self.cmb_copper_mode.currentData() or "sheet")
        spec.create_substrate = bool(self.chk_substrate.isChecked())
        spec.substrate_material = (
            self.edit_substrate.text().strip() or "kicad_substrate"
        )
        spec.substrate_epsilon = float(self.spin_eps.value())
        spec.substrate_tand = float(self.spin_tand.value())
        spec.include_tracks = bool(self.chk_tracks.isChecked())
        spec.include_smd_pads = bool(self.chk_pads.isChecked())
        spec.include_vias = bool(self.chk_vias.isChecked())
        spec.include_zones = bool(self.chk_zones.isChecked())
        spec.include_unfilled_zones = bool(self.chk_unfilled.isChecked())
        spec.include_pad_barrels = bool(self.chk_pad_barrels.isChecked())
        spec.include_npth = bool(self.chk_npth.isChecked())
        spec.suggest_ports = bool(self.chk_suggest_ports.isChecked())
        spec.flip_y = bool(self.chk_flip.isChecked())
        spec.via_plating_radius_mm = float(self.spin_plating.value())
        spec.arc_error_mm = float(self.spin_arc.value())
        spec.simplify_mm = float(self.spin_simplify.value())
        spec.min_area_mm2 = float(self.spin_min_area.value())
        spec.clip_to_outline = bool(self.chk_clip.isChecked())
        self.accept()

    def result_spec(self) -> KiCadSpec:
        return self._spec
