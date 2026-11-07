"""CST Studio export-package import dialog (Variant A).

The package is inspected before this dialog opens.  The dialog shows what was
found and lets the user choose which parts to merge into the project.  It does
not parse CST's native ``.cst`` format.
"""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.freecad_io.cst_import import CstInspection
from app.models.project import CstSpec, Project


def _fmt_freq(hz: float) -> str:
    value = float(hz or 0.0)
    if value >= 1e12:
        return f"{value / 1e12:g} THz"
    if value >= 1e9:
        return f"{value / 1e9:g} GHz"
    if value >= 1e6:
        return f"{value / 1e6:g} MHz"
    if value >= 1e3:
        return f"{value / 1e3:g} kHz"
    return f"{value:g} Hz"


def _fmt_vec(values) -> str:
    return ", ".join(f"{float(v):g}" for v in (values or []))


class CstImportDialog(QDialog):
    """Preview and option editor for a CST export package."""

    def __init__(
        self,
        parent: QWidget | None = None,
        project: Project | None = None,
        inspection: CstInspection | None = None,
        spec: CstSpec | None = None,
        *,
        source: str = "",
        target_unit: str = "mm",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("CST Studio Import")
        self.resize(900, 760)
        self._inspection = inspection or CstInspection()
        self._source = source or self._inspection.source_path
        self._target_unit = str(target_unit or "mm")
        base = spec
        if base is None and project is not None:
            base = getattr(project, "cst", None)
        self._spec = copy.deepcopy(base or CstSpec())
        # Keep the previous package source: apply_cst_to_project() uses it to
        # decide whether the package's ports should be replaced on re-import.
        if not self._spec.source:
            self._spec.source = self._source

        outer = QVBoxLayout(self)

        intro = QLabel(
            "Importiert ein CST-Exportpaket (manifest.json + eine STL pro "
            "Solid). Das native .cst-Projekt wird nicht geparst. Nicht "
            "unterstützte CST-Konstrukte werden in der Warnungsliste genannt "
            "und nicht stillschweigend angenähert."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        g_info = QGroupBox("Erkanntes Paket")
        info_layout = QVBoxLayout(g_info)
        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        info_layout.addWidget(self.lbl_summary)
        outer.addWidget(g_info)

        g_options = QGroupBox("Importumfang")
        options = QFormLayout(g_options)
        self.chk_materials = QCheckBox(
            "Materialien, εr/µr, tanδ und Leitfähigkeit übernehmen"
        )
        self.chk_ports = QCheckBox("Ports übernehmen")
        self.chk_frequency = QCheckBox("Frequenzbereich übernehmen")
        self.chk_boundaries = QCheckBox("Randbedingungen übernehmen")
        self.chk_sheets = QCheckBox("Flächen/Sheets als Polyhedron importieren")
        self.chk_flip = QCheckBox(
            "Y-Achse spiegeln (nur für gespiegelte CST/CAD-Modelle)"
        )
        options.addRow("", self.chk_materials)
        options.addRow("", self.chk_ports)
        options.addRow("", self.chk_frequency)
        options.addRow("", self.chk_boundaries)
        options.addRow("", self.chk_sheets)
        options.addRow("", self.chk_flip)
        self.lbl_target = QLabel("")
        options.addRow("Ziel-Einheit", self.lbl_target)
        outer.addWidget(g_options)

        self.tabs = QTabWidget()
        self.tbl_objects = self._make_table(
            ["Objekt", "Komponente", "Material", "Typ", "STL", "Status"]
        )
        self.tbl_materials = self._make_table(
            ["Material", "Typ", "εr", "µr", "κ (S/m)", "tanδ"]
        )
        self.tbl_ports = self._make_table(
            ["Port", "Typ", "Start", "Stop", "R (Ω)", "Anregung"]
        )
        self.txt_warnings = QPlainTextEdit()
        self.txt_warnings.setReadOnly(True)
        self.txt_warnings.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.tabs.addTab(self.tbl_objects, "Objekte")
        self.tabs.addTab(self.tbl_materials, "Materialien")
        self.tabs.addTab(self.tbl_ports, "Ports")
        self.tabs.addTab(self.txt_warnings, "Warnungen")
        outer.addWidget(self.tabs, 1)

        note = QLabel(
            "Hinweis: CST-eigene Mesh-Einstellungen, Floquet-/Unit-Cell-Ports, "
            "dispersive/anisotrope Materialien, Schematic und Feldmonitore "
            "werden nicht übernommen. Planare CST-Waveguide-Ports auf einem "
            "Substrat mit Leiterbahn werden beim Import automatisch als "
            "MSL-Ports erkannt. Eine im Manifest enthaltene CST-Rechenbox "
            "wird als sechs Randabstände übernommen."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#666;")
        outer.addWidget(note)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("Importieren")
        bb.button(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        btn_defaults = QPushButton("Standardwerte")
        btn_defaults.clicked.connect(self._restore_defaults)
        bb.addButton(btn_defaults, QDialogButtonBox.ButtonRole.ResetRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self._load_spec()
        self._populate()
        self._update_enabled()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setAlternatingRowColors(True)
        return table

    def _load_spec(self) -> None:
        spec = self._spec
        self.chk_materials.setChecked(bool(spec.include_materials))
        self.chk_ports.setChecked(bool(spec.include_ports))
        self.chk_frequency.setChecked(bool(spec.include_frequency))
        self.chk_boundaries.setChecked(bool(spec.include_boundaries))
        self.chk_sheets.setChecked(bool(spec.include_sheets))
        self.chk_flip.setChecked(bool(spec.flip_y))
        self.lbl_target.setText(self._target_unit)

    def _populate(self) -> None:
        ins = self._inspection
        frequency = ins.frequency
        freq_text = (
            f"{_fmt_freq(frequency.start_hz)} – {_fmt_freq(frequency.stop_hz)}"
            if frequency is not None
            else "nicht angegeben"
        )
        boundary_text = ", ".join(
            f"{side}={kind}" for side, kind in sorted((ins.boundaries or {}).items())
        ) or "Standardwerte"
        domain_text = ""
        if (
            ins.calculation_box_min is not None
            and ins.calculation_box_max is not None
        ):
            domain_text = (
                "<br>CST-Rechenbox (mm): "
                f"{_fmt_vec(ins.calculation_box_min)} – "
                f"{_fmt_vec(ins.calculation_box_max)}"
            )
        missing = sum(1 for obj in ins.objects if not obj.exists)
        pml_text = (
            f"PML {ins.pml_size} Zellen"
            if ins.pml_size > 0
            else "PML-Einstellung unverändert"
        )
        self.lbl_summary.setText(
            f"<b>{ins.project_name or Path(self._source).name}</b><br>"
            f"Quelle: {self._source}<br>"
            f"Geometrie: {ins.object_count} Objekte"
            + (f" ({missing} ohne nutzbare STL)" if missing else "")
            + f", Einheit {ins.geometry_unit} → {self._target_unit}<br>"
            f"Frequenz: {freq_text}<br>"
            f"Ränder: {boundary_text}, {pml_text}"
            f"{domain_text}<br>"
            f"Materialien: {len(ins.materials)}, Ports: {len(ins.ports)}"
        )

        self.tbl_objects.setRowCount(len(ins.objects))
        for row, obj in enumerate(ins.objects):
            values = [
                obj.name,
                obj.component,
                obj.material,
                obj.kind,
                Path(obj.file).name,
                "OK" if obj.exists else "fehlt / nicht unterstützt",
            ]
            for col, value in enumerate(values):
                self.tbl_objects.setItem(row, col, QTableWidgetItem(str(value)))
            if not obj.exists:
                for col in range(self.tbl_objects.columnCount()):
                    item = self.tbl_objects.item(row, col)
                    if item is not None:
                        item.setForeground(Qt.GlobalColor.red)

        self.tbl_materials.setRowCount(len(ins.materials))
        for row, mat in enumerate(ins.materials):
            values = [
                mat.name,
                mat.kind,
                f"{mat.epsilon:g}",
                f"{mat.permeability:g}",
                f"{mat.kappa:g}",
                f"{mat.tand:g}",
            ]
            for col, value in enumerate(values):
                self.tbl_materials.setItem(row, col, QTableWidgetItem(value))

        self.tbl_ports.setRowCount(len(ins.ports))
        for row, port in enumerate(ins.ports):
            values = [
                port.name,
                port.type,
                _fmt_vec(port.start),
                _fmt_vec(port.stop),
                f"{port.resistance:g}",
                "ja" if port.excite_port else "nein",
            ]
            for col, value in enumerate(values):
                self.tbl_ports.setItem(row, col, QTableWidgetItem(value))

        warnings = list(ins.warnings)
        if not warnings:
            warnings = ["Keine Warnungen."]
        self.txt_warnings.setPlainText("\n".join(warnings))
        self.tabs.setTabText(3, f"Warnungen ({len(ins.warnings)})")

    def _update_enabled(self) -> None:
        has_ports = bool(self._inspection.ports)
        has_frequency = self._inspection.frequency is not None
        has_boundaries = bool(self._inspection.boundaries)
        has_sheets = any(obj.kind == "sheet" for obj in self._inspection.objects)
        self.chk_ports.setEnabled(has_ports)
        self.chk_frequency.setEnabled(has_frequency)
        self.chk_boundaries.setEnabled(has_boundaries)
        self.chk_sheets.setEnabled(has_sheets)
        if not has_ports:
            self.chk_ports.setChecked(False)
        if not has_frequency:
            self.chk_frequency.setChecked(False)
        if not has_boundaries:
            self.chk_boundaries.setChecked(False)

    def _restore_defaults(self) -> None:
        self._spec = CstSpec(source=self._spec.source or self._source)
        self._load_spec()
        self._update_enabled()

    def _accept(self) -> None:
        spec = self._spec
        spec.include_materials = bool(self.chk_materials.isChecked())
        spec.include_ports = bool(self.chk_ports.isChecked())
        spec.include_frequency = bool(self.chk_frequency.isChecked())
        spec.include_boundaries = bool(self.chk_boundaries.isChecked())
        spec.include_sheets = bool(self.chk_sheets.isChecked())
        spec.flip_y = bool(self.chk_flip.isChecked())
        self.accept()

    def result_spec(self) -> CstSpec:
        return copy.deepcopy(self._spec)


__all__ = ["CstImportDialog"]
