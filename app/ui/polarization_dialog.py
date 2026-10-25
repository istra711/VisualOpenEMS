"""Polarization (axial ratio) and phase-center table for the Antenna tab.

Reads the already-loaded ``nf2ff.json`` payload (complex ``E_theta``/``E_phi``)
and shows the derived values. Nothing here changes the simulation or the stored
result; the numbers are post-processing estimates with the conventions
documented in :mod:`app.postproc.polarization`.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.postproc import polarization
from app.ui.tab_help import show_tab_help

_COLUMNS = [
    "f [GHz]",
    "AR [dB]",
    "Sense",
    "Tilt [°]",
    "Peak θ [°]",
    "Peak φ [°]",
    "Δx [mm]",
    "Δy [mm]",
    "Δz [mm]",
    "RMS [°]",
    "Note",
]


class PolarizationDialog(QDialog):
    """Axial ratio and phase center of one NF2FF result."""

    def __init__(self, parent=None, *, flip_sense: bool = False, beam_db: float = 10.0):
        super().__init__(parent)
        self.flip_sense = bool(flip_sense)
        self.beam_db = float(beam_db)
        self.setWindowTitle("Polarization / phase center")
        self.resize(980, 460)

        root = QVBoxLayout(self)
        note = QLabel(
            "Derived from the complex E_theta/E_phi of nf2ff.json (no re-run).\n"
            "AR from the Stokes parameters; 'linear' means AR > 40 dB.\n"
            "Phase center is a least-squares estimate relative to the NF2FF box\n"
            "centre and is only meaningful in the main beam."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        self.lbl_summary = QLabel("No NF2FF data loaded.")
        self.lbl_summary.setWordWrap(True)
        root.addWidget(self.lbl_summary)

        row = QDialogButtonBox()
        btn_copy = QPushButton("Copy")
        btn_copy.setToolTip("Copy the table as tab-separated text (Excel/Sheets)")
        btn_copy.clicked.connect(self._copy)
        btn_help = QPushButton("Help")
        btn_help.clicked.connect(
            lambda: show_tab_help(self, "Polarization & Phase Center")
        )
        btn_close = row.addButton(QDialogButtonBox.StandardButton.Close)
        btn_close.clicked.connect(self.reject)
        row.addButton(btn_copy, QDialogButtonBox.ButtonRole.ActionRole)
        row.addButton(btn_help, QDialogButtonBox.ButtonRole.ActionRole)
        root.addWidget(row)

    # ------------------------------------------------------------------ data
    def refresh(self, payload: dict | None) -> None:
        """Fill the table from an ``nf2ff.json`` payload (``None`` clears it)."""
        self.table.setRowCount(0)
        if not payload:
            self.lbl_summary.setText("No NF2FF data loaded.")
            return
        ar_rows = polarization.ar_summary(payload, flip_sense=self.flip_sense)
        pc_rows = polarization.phase_center_summary(payload, beam_db=self.beam_db)
        if not ar_rows:
            self.lbl_summary.setText(
                "This result has no complex E_theta/E_phi — re-run the NF2FF "
                "post-processing to store them."
            )
            return
        self.table.setRowCount(len(ar_rows))
        for i, (ar, pc) in enumerate(zip(ar_rows, pc_rows)):
            ar_txt = (
                f"{ar['ar_db']:.2f}"
                if math.isfinite(ar["ar_db"])
                else "linear"
            )
            phase_ok = bool(pc.get("ok"))
            values = [
                f"{ar['frequency'] / 1e9:.4f}",
                ar_txt,
                ar["sense"],
                f"{ar['tilt_deg']:+.1f}",
                f"{ar['theta_deg']:.1f}",
                f"{ar['phi_deg']:.1f}",
                _fmt(pc.get("dx_mm")) if phase_ok else "—",
                _fmt(pc.get("dy_mm")) if phase_ok else "—",
                _fmt(pc.get("dz_mm")) if phase_ok else "—",
                _fmt(pc.get("rms_deg"), 1) if phase_ok else "—",
                _note(ar, pc),
            ]
            for j, text in enumerate(values):
                item = QTableWidgetItem(text)
                if j in (0, 1, 3, 4, 5, 6, 7, 8, 9):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(i, j, item)
        self.table.resizeColumnsToContents()

        bw = polarization.ar_bandwidth(ar_rows)
        parts = [
            f"{len(ar_rows)} frequency point(s)",
            f"AR <= 3 dB at {bw * 100:.0f} % of them" if bw is not None else "",
            f"sense: {sorted({r['sense'] for r in ar_rows})}",
        ]
        self.lbl_summary.setText("  ·  ".join(p for p in parts if p))

    def _copy(self) -> None:
        lines = ["\t".join(_COLUMNS)]
        for i in range(self.table.rowCount()):
            lines.append(
                "\t".join(
                    self.table.item(i, j).text() if self.table.item(i, j) else ""
                    for j in range(self.table.columnCount())
                )
            )
        QApplication.clipboard().setText("\n".join(lines))


def _fmt(value, digits: int = 2) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v:+.{digits}f}"


def _note(ar: dict, pc: dict) -> str:
    bits = []
    if not math.isfinite(ar.get("ar_db", float("nan"))):
        bits.append("linear")
    if pc.get("ok"):
        n = int(pc.get("n_points") or 0)
        if n:
            bits.append(f"{n} pts")
    else:
        bits.append(pc.get("note") or "no phase-center fit")
    return ", ".join(bits)
