"""Left-aligned numeric input fields for the geometry dialogs.

Windows draws the text of a native ``QSpinBox``/``QDoubleSpinBox`` at the right
edge, while the geometry dialogs use left-aligned line edits right next to them.
These factories create spin boxes with the same left alignment so every Start,
Stop, frequency, priority, subsampling and resolution field lines up.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QSpinBox,
    QWidget,
)

ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter


def align_left(widget: QAbstractSpinBox) -> QAbstractSpinBox:
    """Left-align the text of an existing spin box and return it."""
    widget.setAlignment(ALIGN_LEFT)
    return widget


def spin_box(parent: QWidget | None = None) -> QSpinBox:
    """A left-aligned ``QSpinBox``."""
    box = QSpinBox(parent)
    box.setAlignment(ALIGN_LEFT)
    return box


def double_spin_box(parent: QWidget | None = None) -> QDoubleSpinBox:
    """A left-aligned ``QDoubleSpinBox``."""
    box = QDoubleSpinBox(parent)
    box.setAlignment(ALIGN_LEFT)
    return box


def align_spin_boxes(widget: QWidget) -> None:
    """Left-align every spin box below ``widget`` (for dynamically built forms)."""
    for box in widget.findChildren(QAbstractSpinBox):
        box.setAlignment(ALIGN_LEFT)
