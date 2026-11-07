"""Confirmation dialog for automatic KiCad port suggestions."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.freecad_io.kicad_ports import PortCandidate


class KiCadPortsDialog(QDialog):
    """Review/accept the candidate ports derived from KiCad nets."""

    def __init__(
        self,
        parent: QWidget | None = None,
        candidates: list[PortCandidate] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("KiCad Port-Vorschläge")
        self.resize(980, 520)
        self._candidates = [copy.deepcopy(c) for c in (candidates or [])]
        self._result: list[PortCandidate] = []
        self._rows: list[dict] = []

        outer = QVBoxLayout(self)
        intro = QLabel(
            "Aus KiCad-Netzen, Pads und Tracks abgeleitete Ports. "
            "Abwählen, umbenennen oder Typ ändern; Ports werden erst nach "
            "OK in das Projekt übernommen."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        self.tbl = QTableWidget(0, 9)
        self.tbl.setHorizontalHeaderLabels(
            [
                "Aktiv",
                "Name",
                "Netz",
                "Typ",
                "Excite",
                "Start",
                "Stop",
                "Konfidenz",
                "Grund",
            ]
        )
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        header = self.tbl.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self.tbl.setMinimumHeight(300)
        outer.addWidget(self.tbl, 1)

        self._populate()

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btn_all = QPushButton("Alle auswählen")
        btn_all.clicked.connect(self._select_all)
        bb.addButton(btn_all, QDialogButtonBox.ButtonRole.ActionRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def _populate(self) -> None:
        self._rows.clear()
        self.tbl.setRowCount(len(self._candidates))
        for row, candidate in enumerate(self._candidates):
            active = QCheckBox()
            active.setChecked(True)
            active.setToolTip("Diesen Port übernehmen")
            self.tbl.setCellWidget(row, 0, active)

            name = QTableWidgetItem(candidate.name)
            self.tbl.setItem(row, 1, name)
            self.tbl.setItem(row, 2, QTableWidgetItem(candidate.net))

            type_combo = QComboBox()
            type_combo.addItem("Lumped", "lumped")
            type_combo.addItem("MSL", "msl")
            type_combo.addItem("Probe", "probe")
            index = type_combo.findData(candidate.type)
            type_combo.setCurrentIndex(index if index >= 0 else 0)
            self.tbl.setCellWidget(row, 3, type_combo)

            excite = QCheckBox()
            excite.setChecked(bool(candidate.excite))
            self.tbl.setCellWidget(row, 4, excite)

            start = ", ".join(f"{v:g}" for v in candidate.start)
            stop = ", ".join(f"{v:g}" for v in candidate.stop)
            self.tbl.setItem(row, 5, QTableWidgetItem(start))
            self.tbl.setItem(row, 6, QTableWidgetItem(stop))
            self.tbl.setItem(row, 7, QTableWidgetItem(f"{candidate.confidence:.2f}"))
            self.tbl.setItem(row, 8, QTableWidgetItem(candidate.reason))
            for col in (5, 6, 7, 8):
                self.tbl.item(row, col).setFlags(
                    self.tbl.item(row, col).flags()
                    & ~Qt.ItemFlag.ItemIsEditable
                )
            self._rows.append(
                {
                    "active": active,
                    "name": name,
                    "type": type_combo,
                    "excite": excite,
                }
            )

        for row in range(self.tbl.rowCount()):
            self.tbl.setRowHeight(row, 30)

    def _select_all(self) -> None:
        for row in self._rows:
            row["active"].setChecked(True)

    def _accept(self) -> None:
        selected: list[PortCandidate] = []
        excite_seen = False
        for candidate, row in zip(self._candidates, self._rows):
            if not row["active"].isChecked():
                continue
            item = copy.deepcopy(candidate)
            item.name = (row["name"].text() or item.name).strip()
            item.type = str(row["type"].currentData() or "lumped")
            item.excite = bool(row["excite"].isChecked()) and not excite_seen
            if item.excite:
                excite_seen = True

            if item.type == "msl":
                item.prop_direction = item.prop_direction or item.port_axis
                item.exc_direction = item.exc_direction or "z"
                if not item.metal and item.signal_layer:
                    item.metal = f"cu_{item.signal_layer.replace('.', '_')}"
            elif item.type == "probe":
                item.direction = "z"
                item.prop_direction = ""
                item.exc_direction = ""
                item.lumped_width = "line"
            selected.append(item)
        if selected and not excite_seen:
            selected[0].excite = True
        self._result = selected
        self.accept()

    def result_candidates(self) -> list[PortCandidate]:
        return self._result
