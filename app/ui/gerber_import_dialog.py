"""Stackup / import dialog for Gerber + Excellon manufacturing data.

Gerber has no z information, so this dialog collects the values that cannot
come from the files themselves: board and copper thickness, substrate
material, via threshold and per-layer z positions.  Everything is stored in
``Project.gerber`` (``GerberSpec``) by the caller, so a later re-import is
reproducible.
"""

from __future__ import annotations

import copy
import zipfile
from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
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

from app.freecad_io.gerber_import import GERBER_SUFFIXES, GerberInspection
from app.models.project import GerberSpec, Project
from app.ui.input_alignment import double_spin_box


def _spin(
    lo: float,
    hi: float,
    decimals: int,
    step: float,
    suffix: str = " mm",
) -> QDoubleSpinBox:
    box = double_spin_box()
    box.setRange(lo, hi)
    box.setDecimals(decimals)
    box.setSingleStep(step)
    box.setSuffix(suffix)
    box.setKeyboardTracking(False)
    return box


def _find_side_index(combo: QComboBox, side: str) -> int:
    index = combo.findData(side)
    if index >= 0:
        return index
    # gerbonara uses inner_1, but older project files may store inner1.
    if side.startswith("inner") and "_" not in side:
        return combo.findData("inner_" + side[len("inner"):])
    if side.startswith("inner_"):
        return combo.findData("inner" + side[len("inner_"):])
    return -1


class GerberStackupDialog(QDialog):
    """Modal editor for :class:`GerberSpec` before a Gerber import."""

    def __init__(
        self,
        parent: QWidget | None = None,
        project: Project | None = None,
        inspection: GerberInspection | None = None,
        spec: GerberSpec | None = None,
        source: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Gerber/Excellon Import")
        self.resize(720, 760)
        self._project = project
        self._inspection = inspection or GerberInspection()
        self._source = Path(source) if source else None
        base = spec
        if base is None and project is not None:
            base = getattr(project, "gerber", None)
        self._spec = copy.deepcopy(base or GerberSpec())
        self._layer_z_spins: dict[str, QDoubleSpinBox] = {}
        self._layer_z_manual: set[str] = set()
        self._via_span_combos: dict[str, tuple[QComboBox, QComboBox]] = {}
        self._populating = False

        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        outer = QVBoxLayout(content)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        intro = QLabel(
            "Gerber enthält keine Stackup-Informationen. Board-/Kupferdicke, "
            "Substrat und Via-Grenzen hier festlegen; sie werden im Projekt "
            "gespeichert."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        # --- selected folder / ZIP contents -------------------------------
        self.tbl_files: QTableWidget | None = None
        if self._source is not None:
            g_src = QGroupBox("Quelldateien")
            fl_src = QVBoxLayout(g_src)
            self.lbl_source = QLabel(str(self._source))
            self.lbl_source.setWordWrap(True)
            self.lbl_source.setToolTip(str(self._source))
            fl_src.addWidget(self.lbl_source)
            self.tbl_files = QTableWidget(0, 2)
            self.tbl_files.setHorizontalHeaderLabels(["Datei", "Erkennung"])
            self.tbl_files.verticalHeader().setVisible(False)
            self.tbl_files.setEditTriggers(
                QAbstractItemView.EditTrigger.NoEditTriggers
            )
            self.tbl_files.horizontalHeader().setSectionResizeMode(
                0, QHeaderView.ResizeMode.Stretch
            )
            self.tbl_files.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.ResizeMode.ResizeToContents
            )
            self.tbl_files.setMaximumHeight(150)
            fl_src.addWidget(self.tbl_files)
            self.lbl_files_note = QLabel(
                "Nur Gerber-/Excellon-Dateien werden importiert; CSV, JSON, "
                "Dokumentation und Pick-and-Place-Daten werden ignoriert."
            )
            self.lbl_files_note.setWordWrap(True)
            self.lbl_files_note.setStyleSheet("color:#666;")
            fl_src.addWidget(self.lbl_files_note)
            outer.addWidget(g_src)

        # --- stackup ------------------------------------------------------
        g_stack = QGroupBox("Stackup")
        fs = QFormLayout(g_stack)
        self.spin_board = _spin(0.05, 20.0, 3, 0.1)
        self.spin_copper = _spin(0.001, 2.0, 3, 0.005)
        self.chk_sheet = QCheckBox("Kupfer als Folie (AddPolygon, empfohlen)")
        self.chk_sheet.setToolTip(
            "Folie = keine z-Zellgröße nötig. Ausschalten für extrudiertes "
            "Kupfer mit der angegebenen Dicke (AddLinPoly)."
        )
        self.chk_substrate = QCheckBox("Substrat aus Board-Umriss erzeugen")
        self.spin_eps = _spin(1.0, 30.0, 2, 0.1, "")
        self.spin_tand = _spin(0.0, 1.0, 4, 0.001, "")
        self.edit_substrate = QLineEdit()
        fs.addRow("Board-Dicke", self.spin_board)
        fs.addRow("Kupferdicke", self.spin_copper)
        fs.addRow("", self.chk_sheet)
        fs.addRow("", self.chk_substrate)
        fs.addRow("Substratname", self.edit_substrate)
        fs.addRow("εr", self.spin_eps)
        fs.addRow("tanδ", self.spin_tand)
        outer.addWidget(g_stack)

        # --- vias ---------------------------------------------------------
        g_via = QGroupBox("Vias / Bohrungen")
        fv = QFormLayout(g_via)
        self.spin_via_max = _spin(0.05, 10.0, 2, 0.05)
        self.spin_plating = _spin(0.0, 0.2, 3, 0.005)
        self.chk_npth = QCheckBox("NPTH als Vacuum-Zylinder importieren")
        fv.addRow("Max. Via-Durchmesser", self.spin_via_max)
        fv.addRow("Plating (radial)", self.spin_plating)
        fv.addRow("", self.chk_npth)
        outer.addWidget(g_via)

        # --- optional mask -------------------------------------------------
        g_mask = QGroupBox("Lötstoppmaske (optional)")
        fmask = QFormLayout(g_mask)
        self.chk_mask = QCheckBox("Lötstoppmaske als Dielektrikum importieren")
        self.spin_mask_t = _spin(0.001, 0.5, 3, 0.005)
        self.spin_mask_eps = _spin(1.0, 20.0, 2, 0.1, "")
        self.spin_mask_tand = _spin(0.0, 1.0, 4, 0.001, "")
        self.lbl_mask = QLabel("")
        self.lbl_mask.setWordWrap(True)
        fmask.addRow("", self.chk_mask)
        fmask.addRow("Dicke", self.spin_mask_t)
        fmask.addRow("εr", self.spin_mask_eps)
        fmask.addRow("tanδ", self.spin_mask_tand)
        fmask.addRow("Erkannt", self.lbl_mask)
        outer.addWidget(g_mask)

        # --- detected copper layers --------------------------------------
        g_layers = QGroupBox("Erkannte Kupferlayer")
        fl = QVBoxLayout(g_layers)
        self.tbl_layers = QTableWidget(0, 3)
        self.tbl_layers.setHorizontalHeaderLabels(["Layer", "Datei", "z (mm)"])
        self.tbl_layers.verticalHeader().setVisible(False)
        self.tbl_layers.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.tbl_layers.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tbl_layers.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.tbl_layers.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.tbl_layers.setMinimumHeight(130)
        fl.addWidget(self.tbl_layers)
        self.lbl_layers = QLabel("")
        self.lbl_layers.setWordWrap(True)
        fl.addWidget(self.lbl_layers)
        outer.addWidget(g_layers)

        # --- detected drills ---------------------------------------------
        g_drill = QGroupBox("Erkannte Bohrungen / Slots")
        fd = QVBoxLayout(g_drill)
        self.tbl_drills = QTableWidget(0, 5)
        self.tbl_drills.setHorizontalHeaderLabels(
            ["Durchmesser", "Anzahl", "Plating", "Von", "Bis"]
        )
        self.tbl_drills.verticalHeader().setVisible(False)
        self.tbl_drills.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self.tbl_drills.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tbl_drills.setMinimumHeight(130)
        fd.addWidget(self.tbl_drills)
        self.lbl_slots = QLabel("")
        self.lbl_slots.setWordWrap(True)
        fd.addWidget(self.lbl_slots)
        outer.addWidget(g_drill)

        # --- precision ----------------------------------------------------
        g_prec = QGroupBox("Genauigkeit")
        fp = QFormLayout(g_prec)
        self.spin_arc = _spin(0.001, 0.5, 3, 0.005)
        self.spin_simplify = _spin(0.0, 1.0, 3, 0.005)
        self.spin_min_area = _spin(0.0, 10.0, 4, 0.005, " mm²")
        self.chk_clip = QCheckBox("Auf Board-Umriss zuschneiden")
        self.chk_suggest_ports = QCheckBox("Nach dem Import Ports vorschlagen")
        fp.addRow("Arc-Toleranz", self.spin_arc)
        fp.addRow("Vereinfachung", self.spin_simplify)
        fp.addRow("Min. Fläche", self.spin_min_area)
        fp.addRow("", self.chk_clip)
        fp.addRow("", self.chk_suggest_ports)
        outer.addWidget(g_prec)

        note = QLabel(
            "Hinweis: Vias werden aus allen plated Bohrhits bis zum "
            "Maximaldurchmesser erzeugt. Größere Bohrungen gelten als "
            "Bestückungs-/Montagelöcher. Via-Spans gelten pro "
            "Bohrdurchmesser (Gerber/Excellon kennt keine einzelnen "
            "Via-Layer); gleich große Through- und Blindvias können daher "
            "nicht getrennt werden."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#666;")
        outer.addWidget(note)
        outer.addStretch(1)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btn_defaults = QPushButton("Standardwerte")
        btn_defaults.clicked.connect(self._restore_defaults)
        bb.addButton(btn_defaults, QDialogButtonBox.ButtonRole.ResetRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self._load_spec()
        self._populate()
        self._populate_source_files()
        self.chk_sheet.toggled.connect(self._update_sheet_state)
        self.spin_board.valueChanged.connect(self._sync_layer_z_defaults)
        self._update_sheet_state()

    # ------------------------------------------------------------------ #
    def _source_files(self) -> list[str]:
        """List every file in the selected folder/ZIP (relative names)."""
        src = self._source
        if src is None:
            return []
        try:
            if src.is_dir():
                return sorted(
                    str(p.relative_to(src))
                    for p in src.rglob("*")
                    if p.is_file()
                )
            if zipfile.is_zipfile(src):
                with zipfile.ZipFile(src) as zf:
                    return sorted(
                        entry.filename.replace("\\", "/")
                        for entry in zf.infolist()
                        if not entry.is_dir()
                    )
            if src.is_file():
                return [src.name]
        except Exception as e:  # noqa: BLE001 - listing is best effort
            print(f"Gerber source listing failed: {e}")
        return []

    def _file_role(self, name: str) -> str:
        """Short human-readable role of one source file for the dialog list."""
        base = Path(name).name
        lower = base.lower()
        suffix = Path(base).suffix.lower()
        for layer in self._inspection.layers:
            if str(layer.get("file") or "") == base:
                side = str(layer.get("side") or "")
                return f"Kupfer {side}" if side else "Kupfer"
        if suffix == ".d356":
            return "Netzliste / IPC-356"
        if suffix in (".drl", ".xln", ".nc") or "pth" in lower:
            if "npth" in lower:
                return "Bohrungen (NPTH)"
            return "Bohrungen (PTH)"
        if (
            "edge" in lower
            or "outline" in lower
            or suffix in (".gko", ".gm1")
        ):
            return "Board-Umriss"
        if "mask" in lower or suffix in (".gts", ".gbs", ".gtp", ".gbp"):
            return "Lötstoppmaske"
        if suffix in GERBER_SUFFIXES:
            return "Gerber (nicht zugeordnet)"
        return "ignoriert (keine Fertigungsdatei)"

    def _populate_source_files(self) -> None:
        if self.tbl_files is None:
            return
        files = self._source_files()
        self.tbl_files.setRowCount(max(1, len(files)))
        if not files:
            self.tbl_files.setItem(
                0, 0, QTableWidgetItem("(keine Dateien gefunden)")
            )
            self.tbl_files.setItem(
                0, 1, QTableWidgetItem("Ordner/ZIP ist leer")
            )
            return
        for row, name in enumerate(files):
            self.tbl_files.setItem(row, 0, QTableWidgetItem(name))
            self.tbl_files.setItem(
                row, 1, QTableWidgetItem(self._file_role(name))
            )

    def _load_spec(self) -> None:
        spec = self._spec
        self.spin_board.setValue(float(spec.board_thickness_mm))
        self.spin_copper.setValue(float(spec.copper_thickness_mm))
        self.chk_sheet.setChecked(bool(spec.copper_as_sheet))
        self.spin_via_max.setValue(float(spec.via_max_diameter_mm))
        self.spin_plating.setValue(float(spec.via_plating_radius_mm))
        self.chk_npth.setChecked(bool(spec.include_npth))
        self.chk_substrate.setChecked(bool(spec.create_substrate))
        self.edit_substrate.setText(str(spec.substrate_material or "substrate"))
        self.spin_eps.setValue(float(spec.substrate_epsilon))
        self.spin_tand.setValue(float(spec.substrate_tand))
        self.spin_arc.setValue(float(spec.arc_error_mm))
        self.spin_simplify.setValue(float(spec.simplify_mm))
        self.spin_min_area.setValue(float(spec.min_area_mm2))
        self.chk_clip.setChecked(bool(spec.clip_to_outline))
        self.chk_suggest_ports.setChecked(bool(spec.suggest_ports))
        self.chk_mask.setChecked(bool(spec.include_mask))
        self.spin_mask_t.setValue(float(spec.solder_mask_thickness_mm))
        self.spin_mask_eps.setValue(float(spec.solder_mask_epsilon))
        self.spin_mask_tand.setValue(float(spec.solder_mask_tand))

    def _populate(self) -> None:
        self._layer_z_spins.clear()
        self._layer_z_manual.clear()
        self._populating = True
        layers = list(getattr(self._inspection, "layers", None) or [])
        sides = [str(layer.get("side", "")) for layer in layers]
        initial_defaults = self._layer_defaults(sides)
        self.tbl_layers.clearSpans()
        self.tbl_layers.setRowCount(len(layers))
        for row, layer in enumerate(layers):
            side = str(layer.get("side", ""))
            file_name = str(layer.get("file", ""))
            fallback_z = float(layer.get("default_z_mm", layer.get("z_mm", 0.0)))
            z = float(
                (self._spec.layer_z or {}).get(
                    side, initial_defaults.get(side, fallback_z)
                )
            )
            self.tbl_layers.setItem(row, 0, QTableWidgetItem(side))
            self.tbl_layers.setItem(row, 1, QTableWidgetItem(file_name))
            spin = _spin(-100.0, 100.0, 3, 0.05)
            spin.setValue(z)
            spin.valueChanged.connect(
                lambda _value, _side=side: self._mark_layer_z_manual(_side)
            )
            self.tbl_layers.setCellWidget(row, 2, spin)
            self._layer_z_spins[side] = spin
        defaults = self._layer_defaults()
        for side, spin in self._layer_z_spins.items():
            if abs(float(spin.value()) - defaults.get(side, spin.value())) > 1e-9:
                self._layer_z_manual.add(side)
        self._populating = False
        self._sync_layer_z_defaults()

        if layers:
            self.lbl_layers.setText(
                f"{len(layers)} Kupferlayer erkannt. z-Werte können pro Layer "
                "überschrieben werden."
            )
        else:
            self.lbl_layers.setText("Keine Kupferlayer erkannt.")
            self.tbl_layers.setRowCount(1)
            self.tbl_layers.setSpan(0, 0, 1, 3)
            self.tbl_layers.setItem(
                0, 0, QTableWidgetItem("Keine Kupferlayer erkannt")
            )

        drills = list(getattr(self._inspection, "drills", None) or [])
        sides = [str(layer.get("side", "")) for layer in layers]
        self._via_span_combos.clear()
        self.tbl_drills.clearSpans()
        self.tbl_drills.setRowCount(len(drills))
        for row, drill in enumerate(drills):
            diameter = float(drill.get("diameter_mm", 0.0))
            plated = bool(drill.get("plated"))
            key = f"{diameter:.4f}"
            self.tbl_drills.setItem(
                row, 0, QTableWidgetItem(f"{diameter:g} mm")
            )
            self.tbl_drills.setItem(
                row, 1, QTableWidgetItem(str(drill.get("count", 0)))
            )
            self.tbl_drills.setItem(
                row, 2, QTableWidgetItem("PTH" if plated else "NPTH")
            )

            from_combo = QComboBox()
            to_combo = QComboBox()
            for combo in (from_combo, to_combo):
                combo.addItem("Durchgehend", "")
                for side in sides:
                    combo.addItem(side, side)
            span = (self._spec.via_spans or {}).get(key)
            if isinstance(span, (list, tuple)) and len(span) == 2:
                index = _find_side_index(from_combo, str(span[0]))
                if index >= 0:
                    from_combo.setCurrentIndex(index)
                index = _find_side_index(to_combo, str(span[1]))
                if index >= 0:
                    to_combo.setCurrentIndex(index)

            if not plated:
                from_combo.setEnabled(False)
                to_combo.setEnabled(False)
            else:
                self._via_span_combos[key] = (from_combo, to_combo)
            self.tbl_drills.setCellWidget(row, 3, from_combo)
            self.tbl_drills.setCellWidget(row, 4, to_combo)

        if not drills:
            self.tbl_drills.setRowCount(1)
            self.tbl_drills.setSpan(0, 0, 1, 5)
            self.tbl_drills.setItem(0, 0, QTableWidgetItem("Keine Bohrungen erkannt"))

        slots = list(getattr(self._inspection, "slots", None) or [])
        if slots:
            pth_slots = sum(
                int(slot.get("count", 0)) for slot in slots if slot.get("plated")
            )
            npth_slots = sum(
                int(slot.get("count", 0)) for slot in slots if not slot.get("plated")
            )
            self.lbl_slots.setText(
                f"Slots: {pth_slots} PTH, {npth_slots} NPTH "
                "(werden als extrudierte Polygone importiert)."
            )
        else:
            self.lbl_slots.setText("Keine Slots erkannt.")

        mask_layers = list(getattr(self._inspection, "mask_layers", None) or [])
        if mask_layers:
            self.lbl_mask.setText(", ".join(mask_layers))
            self.chk_mask.setEnabled(True)
        else:
            self.lbl_mask.setText("keine Lötstoppmaskenlayer erkannt")
            self.chk_mask.setChecked(False)
            self.chk_mask.setEnabled(False)

    def _update_sheet_state(self) -> None:
        self.spin_copper.setEnabled(not self.chk_sheet.isChecked())

    def _mark_layer_z_manual(self, side: str) -> None:
        if not self._populating:
            self._layer_z_manual.add(side)

    def _layer_defaults(self, sides: list[str] | None = None) -> dict[str, float]:
        sides = list(self._layer_z_spins) if sides is None else list(sides)
        if not sides:
            return {}
        thickness = float(self.spin_board.value())
        n = len(sides)
        if n == 1:
            return {sides[0]: 0.0}
        top, bottom = thickness / 2.0, -thickness / 2.0
        step = (top - bottom) / (n - 1)
        return {side: top - i * step for i, side in enumerate(sides)}

    def _sync_layer_z_defaults(self, *_args) -> None:
        """Follow board-thickness changes for layers not edited by hand."""
        defaults = self._layer_defaults()
        if not defaults:
            return
        self._populating = True
        for side, spin in self._layer_z_spins.items():
            if side not in self._layer_z_manual:
                spin.setValue(defaults[side])
        self._populating = False

    def _restore_defaults(self) -> None:
        self._spec = GerberSpec()
        self._load_spec()
        self._populate()
        self._update_sheet_state()

    def _accept(self) -> None:
        spec = self._spec
        spec.board_thickness_mm = float(self.spin_board.value())
        spec.copper_thickness_mm = float(self.spin_copper.value())
        spec.copper_as_sheet = bool(self.chk_sheet.isChecked())
        spec.via_max_diameter_mm = float(self.spin_via_max.value())
        spec.via_plating_radius_mm = float(self.spin_plating.value())
        spec.include_npth = bool(self.chk_npth.isChecked())
        spec.create_substrate = bool(self.chk_substrate.isChecked())
        spec.substrate_material = (
            self.edit_substrate.text().strip() or "substrate"
        )
        spec.substrate_epsilon = float(self.spin_eps.value())
        spec.substrate_tand = float(self.spin_tand.value())
        spec.arc_error_mm = float(self.spin_arc.value())
        spec.simplify_mm = float(self.spin_simplify.value())
        spec.min_area_mm2 = float(self.spin_min_area.value())
        spec.clip_to_outline = bool(self.chk_clip.isChecked())
        spec.suggest_ports = bool(self.chk_suggest_ports.isChecked())
        spec.include_mask = bool(self.chk_mask.isChecked())
        spec.solder_mask_thickness_mm = float(self.spin_mask_t.value())
        spec.solder_mask_epsilon = float(self.spin_mask_eps.value())
        spec.solder_mask_tand = float(self.spin_mask_tand.value())
        spec.solder_mask_material = (
            str(spec.solder_mask_material or "solder_mask")
        )
        spec.via_spans = {}
        for key, (from_combo, to_combo) in self._via_span_combos.items():
            first = str(from_combo.currentData() or "")
            last = str(to_combo.currentData() or "")
            if first and last and first != last:
                spec.via_spans[key] = [first, last]
        defaults = self._layer_defaults()
        spec.layer_z = {
            side: float(spin.value())
            for side, spin in self._layer_z_spins.items()
            if abs(float(spin.value()) - defaults.get(side, spin.value())) > 1e-9
        }
        self.accept()

    def result_spec(self) -> GerberSpec:
        """Return the edited spec (valid after ``exec()`` returned Accepted)."""
        return self._spec
