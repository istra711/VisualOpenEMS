"""Preview-then-apply dialog for a mesh repair (V1).

The Mesh Viewer right-click action plans a repair first and shows what it would
change - lines removed/moved, the new minimum cell, the CFL timestep and the
expected runtime - before anything touches the grid. The floor (minimum cell)
can be raised in the dialog to coarsen the region further; every change
recomputes the numbers.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)


class RepairDialog(QDialog):
    """Show a repair preview and let the user pick the minimum cell size.

    ``preview_fn(floor) -> (text, can_apply)`` is called on every change of the
    floor (drawing units); ``can_apply`` enables the OK button.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "Repair region",
        floor: float,
        floor_min: float,
        floor_max: float,
        unit: str = "mm",
        preview_fn: Callable[[float], tuple[str, bool]],
        note: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 520)
        self._preview_fn = preview_fn
        root = QVBoxLayout(self)
        if note:
            lbl = QLabel(note)
            lbl.setWordWrap(True)
            root.addWidget(lbl)
        form = QFormLayout()
        self.spn_floor = QDoubleSpinBox()
        self.spn_floor.setDecimals(6)
        lo = max(0.0, float(floor_min))
        hi = max(float(floor_max), lo + 1e-6)
        self.spn_floor.setRange(lo, hi)
        self.spn_floor.setSuffix(f" {unit}")
        self.spn_floor.setValue(min(max(float(floor), lo), hi))
        self.spn_floor.setSingleStep(max((hi - lo) / 200.0, 1e-5))
        self.spn_floor.setToolTip(
            "Smallest cell the repaired region may keep. Raise it to coarsen the\n"
            "region further (fewer cells, longer timestep); the preview updates."
        )
        form.addRow("Minimum cell (floor)", self.spn_floor)
        root.addLayout(form)
        self.txt = QPlainTextEdit()
        self.txt.setReadOnly(True)
        root.addWidget(self.txt, 1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.spn_floor.valueChanged.connect(self._refresh)
        self._refresh()

    def floor_value(self) -> float:
        return float(self.spn_floor.value())

    def _refresh(self, *_args: object) -> None:
        try:
            text, ok = self._preview_fn(self.floor_value())
        except Exception as e:  # noqa: BLE001 - a preview must not crash the GUI
            text, ok = f"Preview failed: {e}", False
        self.txt.setPlainText(text)
        btn = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if btn is not None:
            btn.setEnabled(bool(ok))
            btn.setText("Apply repair" if ok else "Nothing to change")
