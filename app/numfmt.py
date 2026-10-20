"""Number text helpers shared by the GUI fields (no Qt import on purpose).

``format_number`` shows a float with ``%.6g`` (``0.095199`` / ``1.264e-09``) and
``parse_number`` accepts what users type into a scientific field: ``1.5e-9``,
``1,5e-9`` (comma locale) or plain decimals.
"""

from __future__ import annotations

import math


def format_number(value: float, digits: int = 6) -> str:
    """Plain/scientific text for ``value`` (``%.6g``: 0.095199, 1.264e-09)."""
    value = float(value)
    if not math.isfinite(value):
        return "0"
    return f"{value:.{int(digits)}g}"


def parse_number(text: str) -> float | None:
    """Parse ``"1,5e-9"`` / ``"1.5e-9"`` / ``"0.001"``; ``None`` if invalid."""
    text = text.strip().replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    try:
        value = float(text.replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None
