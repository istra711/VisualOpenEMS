"""Frequency unit helpers (Hz / kHz / MHz / GHz) for GUI spinboxes.

Project models always store frequencies in Hz. Widgets may display any unit.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QDoubleSpinBox, QWidget

FREQ_UNITS: dict[str, float] = {
    "Hz": 1.0,
    "kHz": 1e3,
    "MHz": 1e6,
    "GHz": 1e9,
}
FREQ_UNIT_NAMES: list[str] = ["Hz", "kHz", "MHz", "GHz"]

# Display ranges (always in Hz internally for spinbox min/max)
HZ_MIN = 1e3
HZ_MAX = 1e12


def freq_to_hz(value: float, unit: str) -> float:
    return float(value) * FREQ_UNITS.get(unit, 1.0)


def hz_to_freq(hz: float, unit: str) -> float:
    return float(hz) / FREQ_UNITS.get(unit, 1.0)


def suggest_freq_unit(hz: float) -> str:
    a = abs(float(hz) or 0.0)
    if a >= 0.5e9:
        return "GHz"
    if a >= 0.5e6:
        return "MHz"
    if a >= 0.5e3:
        return "kHz"
    return "Hz"


def freq_unit_decimals(unit: str) -> int:
    return {"Hz": 0, "kHz": 3, "MHz": 4, "GHz": 6}.get(unit, 4)


def freq_unit_step(unit: str) -> float:
    return {"Hz": 1e3, "kHz": 1.0, "MHz": 1e-3, "GHz": 1e-6}.get(unit, 1e-6)


def fmt_hz(hz: float) -> str:
    a = abs(float(hz) or 0.0)
    if a >= 1e9:
        return f"{hz / 1e9:.6g} GHz"
    if a >= 1e6:
        return f"{hz / 1e6:.6g} MHz"
    if a >= 1e3:
        return f"{hz / 1e3:.6g} kHz"
    return f"{hz:.6g} Hz"


def configure_freq_spin(
    spin: QDoubleSpinBox,
    unit: str,
    *,
    decimals: int | None = None,
    hz_min: float = HZ_MIN,
    hz_max: float = HZ_MAX,
) -> None:
    factor = FREQ_UNITS.get(unit, 1.0)
    dec = freq_unit_decimals(unit) if decimals is None else decimals
    spin.blockSignals(True)
    spin.setRange(hz_min / factor, hz_max / factor)
    spin.setDecimals(dec)
    spin.setSingleStep(freq_unit_step(unit))
    spin.setSuffix(f" {unit}")
    spin.blockSignals(False)


def spin_hz(spin: QDoubleSpinBox, unit: str) -> float:
    return freq_to_hz(spin.value(), unit)


def set_spin_hz(spin: QDoubleSpinBox, hz: float, unit: str) -> None:
    factor = FREQ_UNITS.get(unit, 1.0)
    spin.blockSignals(True)
    spin.setValue(float(hz) / factor)
    spin.blockSignals(False)


def unit_combo_items(combo: QComboBox) -> None:
    combo.clear()
    combo.addItems(FREQ_UNIT_NAMES)


class FreqEdit(QWidget):
    """Spinbox + unit combo; value is always exposed in Hz."""

    valueChanged = Signal()

    def __init__(
        self,
        hz: float = 0.0,
        *,
        unit: str = "GHz",
        decimals: int | None = None,
        hz_min: float = HZ_MIN,
        hz_max: float = HZ_MAX,
        show_unit_combo: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._hz_min = hz_min
        self._hz_max = hz_max
        self._unit = unit if unit in FREQ_UNITS else "Hz"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._spin = QDoubleSpinBox()
        self._spin.setKeyboardTracking(False)
        configure_freq_spin(self._spin, self._unit, decimals=decimals, hz_min=hz_min, hz_max=hz_max)
        self._spin.setValue(hz_to_freq(hz, self._unit))
        self._spin.valueChanged.connect(lambda *_: self.valueChanged.emit())
        lay.addWidget(self._spin, 1)
        self._cmb = QComboBox()
        unit_combo_items(self._cmb)
        self._cmb.setCurrentText(self._unit)
        self._cmb.currentTextChanged.connect(self._on_unit_changed)
        if show_unit_combo:
            lay.addWidget(self._cmb)
        else:
            self._cmb.hide()

    def _on_unit_changed(self, unit: str) -> None:
        if unit == self._unit or unit not in FREQ_UNITS:
            return
        hz = self.value_hz()
        self._unit = unit
        configure_freq_spin(
            self._spin, unit, hz_min=self._hz_min, hz_max=self._hz_max
        )
        set_spin_hz(self._spin, hz, unit)
        self.valueChanged.emit()

    def unit(self) -> str:
        return self._unit

    def set_unit(self, unit: str) -> None:
        if unit not in FREQ_UNITS:
            return
        self._cmb.setCurrentText(unit)  # triggers _on_unit_changed

    def value_hz(self) -> float:
        return spin_hz(self._spin, self._unit)

    def set_hz(self, hz: float) -> None:
        set_spin_hz(self._spin, hz, self._unit)

    def spin(self) -> QDoubleSpinBox:
        return self._spin

    def unit_combo(self) -> QComboBox:
        return self._cmb
