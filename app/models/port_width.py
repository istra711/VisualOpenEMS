"""Pure geometry helpers for lumped-port width modes.

A lumped port is either a 1-D ``"line"`` (Start/Stop differ only along the
excitation direction) or an ``"area"``/sheet port that is as wide as the metal
it sits on along one in-plane axis.  Keeping these rules free of Qt lets the
Port dialog preview the box while editing and gives the Main Window one place
to rebuild it on OK.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

AXES = "xyz"


def _as_vec3(value: Any) -> list[float] | None:
    try:
        vec = [float(value[index]) for index in range(3)]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    return vec if all(math.isfinite(item) for item in vec) else None


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def primitive_bounds(primitive: Any) -> tuple[list[float], list[float]] | None:
    """Axis-aligned bounds of a primitive dict/object, or ``None``."""
    start = _as_vec3(_field(primitive, "start"))
    stop = _as_vec3(_field(primitive, "stop"))
    if start is None or stop is None:
        return None
    lo = [min(start[axis], stop[axis]) for axis in range(3)]
    hi = [max(start[axis], stop[axis]) for axis in range(3)]
    return lo, hi


def primitive_span(primitive: Any, axis: str) -> tuple[float, float] | None:
    """Extent of ``primitive`` along ``axis`` as ``(lo, hi)``."""
    letter = str(axis or "").strip().lower()
    if letter not in AXES:
        return None
    bounds = primitive_bounds(primitive)
    if bounds is None:
        return None
    index = AXES.index(letter)
    return bounds[0][index], bounds[1][index]


def normalize_mode(mode: Any, direction: Any = "z") -> str:
    """``"line"`` or a width axis letter; a width along the direction is a line."""
    text = str(mode or "").strip().lower()
    if text in ("", "none", "-"):
        text = "line"
    letter = str(direction or "z").strip().lower()
    if letter not in AXES:
        letter = "z"
    if text in AXES and text == letter:
        return "line"
    return text if text in AXES or text == "line" else "line"


def rebuild_box(
    start: Any,
    stop: Any,
    direction: Any,
    mode: Any,
    span: tuple[float, float] | None = None,
    *,
    tolerance: float = 1e-9,
) -> tuple[list[float], list[float]] | None:
    """Start/Stop for a width ``mode`` or ``None`` when it cannot be built.

    ``"line"`` collapses both axes across ``direction`` to their midpoints.  An
    axis letter widens that axis to ``span`` and keeps the other transverse axis
    at its midpoint (zero-thickness sheet).  A missing/degenerate ``span`` means
    the metal has no usable width and leaves the box untouched (``None``).
    """
    new_start = _as_vec3(start)
    new_stop = _as_vec3(stop)
    if new_start is None or new_stop is None:
        return None
    letter = str(direction or "z").strip().lower()
    if letter not in AXES:
        letter = "z"
    direction_index = AXES.index(letter)
    width_mode = normalize_mode(mode, letter)
    if width_mode == "line":
        for index in range(3):
            if index == direction_index:
                continue
            mid = 0.5 * (new_start[index] + new_stop[index])
            new_start[index] = new_stop[index] = mid
        return new_start, new_stop
    try:
        lo, hi = float(span[0]), float(span[1])  # type: ignore[index]
    except (IndexError, TypeError, ValueError):
        return None
    if not (math.isfinite(lo) and math.isfinite(hi)) or abs(hi - lo) <= tolerance:
        return None
    if hi < lo:
        lo, hi = hi, lo
    width_index = AXES.index(width_mode)
    new_start[width_index], new_stop[width_index] = lo, hi
    for index in range(3):
        if index in (direction_index, width_index):
            continue
        mid = 0.5 * (new_start[index] + new_stop[index])
        new_start[index] = new_stop[index] = mid
    return new_start, new_stop


def metal_name_at_port(
    primitives: Iterable[Any],
    metal_names: Iterable[str],
    start: Any,
    stop: Any,
    direction: Any,
    width_axis: Any,
    *,
    tolerance: float = 1e-6,
) -> str:
    """Best metal primitive under ``start`` (or the port midpoint), else ``""``.

    Used when a port has no saved metal reference (manual entry, old project or
    an import) so choosing "Area - as wide as the metal" can still find the
    trace it sits on.  Candidates must be metal and have an extent along the
    width axis; the smallest one whose box contains the port start wins, which
    prefers a narrow trace over the ground plane beneath it.
    """
    new_start = _as_vec3(start)
    new_stop = _as_vec3(stop)
    if new_start is None or new_stop is None:
        return ""
    letter = str(direction or "z").strip().lower()
    width_mode = str(width_axis or "").strip().lower()
    if letter not in AXES or width_mode not in AXES or letter == width_mode:
        return ""
    direction_index = AXES.index(letter)
    width_index = AXES.index(width_mode)
    other_index = 3 - direction_index - width_index
    metals = {str(name) for name in metal_names or () if str(name)}

    candidates: list[tuple[float, str, list[float], list[float], float]] = []
    for primitive in primitives or ():
        name = str(_field(primitive, "name", "") or "")
        if not name or name not in metals:
            continue
        bounds = primitive_bounds(primitive)
        if bounds is None:
            continue
        lo, hi = bounds
        if hi[width_index] - lo[width_index] <= tolerance:
            continue  # a sheet edge-on to the width axis has no width to give
        scale = max(1.0, *(abs(value) for value in lo), *(abs(value) for value in hi))
        area = (hi[direction_index] - lo[direction_index]) * (
            hi[other_index] - lo[other_index]
        )
        candidates.append((area, name, lo, hi, tolerance * scale))
    if not candidates:
        return ""

    def hits(point: list[float], axes: tuple[int, ...]) -> list[tuple[float, str]]:
        return [
            (area, name)
            for area, name, lo, hi, tol in candidates
            if all(lo[index] - tol <= point[index] <= hi[index] + tol for index in axes)
        ]

    midpoint = [
        0.5 * (new_start[index] + new_stop[index]) for index in range(3)
    ]
    for point in (new_start, midpoint):
        found = hits(point, (0, 1, 2))
        if found:
            return min(found)[1]
    found = hits(new_start, (width_index, other_index))
    if found:
        return min(found)[1]
    return candidates[0][1] if len(candidates) == 1 else ""
