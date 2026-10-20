"""One-line numeric field that shows and accepts scientific notation.

``QDoubleSpinBox`` has no scientific-notation mode in Qt 6 and rounds its value
to ``decimals`` places.  The Field Viewer's colour limits span many decades
(field magnitudes reach 1e-12 and less), so with 6 decimals a magnitude of
1.26e-9 was *displayed* as ``0.000000`` and a manual lower limit like 1e-9
could not be entered at all (it silently became 0).  This field stores a double,
shows it with ``%.6g`` (``0.095199`` / ``1.264e-09``) and accepts ``1.5e-9``
as well as the locale form ``1,5e-9``.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QLineEdit, QWidget

from app.numfmt import format_number, parse_number

__all__ = ["SciSpinBox", "format_number", "parse_number"]


class SciSpinBox(QLineEdit):
    """Right-aligned numeric field with ``value()`` / ``setValue()``.

    ``valueChanged`` is emitted when the value actually changes - on Enter /
    focus-out (typed text) and on ``setValue`` (unless the signals are blocked),
    mirroring ``QDoubleSpinBox``.
    """

    valueChanged = Signal(float)

    def __init__(self, value: float = 0.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # scientific notation: QDoubleValidator accepts "1.2e-9" (and "1,2e-9"
        # with a comma locale) while still rejecting letters
        self.setValidator(QDoubleValidator(-1e300, 1e300, 12, self))
        self.setMinimumWidth(96)
        self.editingFinished.connect(self._commit)
        self.setValue(float(value))

    # ------------------------------------------------------------- value
    def value(self) -> float:
        return self._value

    def setValue(self, value: float) -> None:
        value = float(value)
        if not math.isfinite(value):
            value = 0.0
        changed = value != self._value
        self._value = value
        self.setText(format_number(value))
        if changed:
            self.valueChanged.emit(value)

    def _commit(self) -> None:
        value = parse_number(self.text())
        if value is None:
            self.setText(format_number(self._value))  # undo an invalid entry
            return
        changed = value != self._value
        self._value = value
        self.setText(format_number(value))
        if changed:
            self.valueChanged.emit(value)
