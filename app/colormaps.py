"""Colour map helpers for the field plot (shared by viewer and settings).

Kept free of Qt/VTK imports so the settings module and the test suite can use
the same tables without starting a GUI.
"""

from __future__ import annotations

import colorsys

#: Colour maps offered in Settings (label -> matplotlib name). "jet" is the
#: previous fixed blue -> red table, so existing screenshots/videos still match.
COLORMAPS: tuple[tuple[str, str], ...] = (
    ("Blue → Red (jet)", "jet"),
    ("Turbo", "turbo"),
    ("Viridis", "viridis"),
    ("Plasma", "plasma"),
    ("Inferno", "inferno"),
    ("Magma", "magma"),
    ("Coolwarm (diverging)", "coolwarm"),
    ("Rainbow", "rainbow"),
    ("Greyscale", "gray"),
)

#: Defaults for the field colour scale (Settings dialog + pop-out mirroring):
#: ``cmap`` = matplotlib colour map name, ``reverse`` swaps low/high colours,
#: ``steps`` = number of discrete table entries (256 = smooth).
DEFAULT_COLOR_SCALE: dict = {"cmap": "jet", "reverse": False, "steps": 256}

COLOR_SCALE_MIN_STEPS = 2
COLOR_SCALE_MAX_STEPS = 256


def colormap_names() -> tuple[str, ...]:
    """Keys of the colour maps selectable in the settings dialog."""
    return tuple(key for _label, key in COLORMAPS)


def _fallback_colormap(steps: int) -> list[tuple[float, float, float]]:
    """Matplotlib-free blue -> red gradient (the historic VTK table)."""
    out: list[tuple[float, float, float]] = []
    last = max(1, steps - 1)
    for i in range(steps):
        hue = 0.667 - 0.667 * (i / last)
        out.append(colorsys.hsv_to_rgb(hue, 1.0, 1.0))
    return out


def colormap_colors(
    name: str, steps: int = 256, reverse: bool = False
) -> list[tuple[float, float, float]]:
    """RGB colours (0…1) of a colour map, sampled at ``steps`` positions.

    Uses matplotlib when available (the same maps as the antenna viewer) and
    falls back to the classic blue -> red gradient. Used both for the VTK
    lookup table and for the preview swatch in the settings dialog.
    """
    try:
        count = max(COLOR_SCALE_MIN_STEPS, min(COLOR_SCALE_MAX_STEPS, int(steps)))
    except (TypeError, ValueError):
        count = 256
    key = str(name or DEFAULT_COLOR_SCALE["cmap"])
    colors: list[tuple[float, float, float]]
    try:
        from matplotlib import colormaps

        cmap = colormaps[key]
        last = max(1, count - 1)
        colors = [
            tuple(float(c) for c in cmap(i / last)[:3])  # type: ignore[misc]
            for i in range(count)
        ]
    except Exception:  # noqa: BLE001 — no matplotlib / unknown name
        colors = _fallback_colormap(count)
    return list(reversed(colors)) if reverse else colors


def fill_lookup_table(lut, colors) -> None:
    """Copy ``colors`` (RGB triples) into a ``vtkLookupTable``."""
    lut.SetNumberOfTableValues(len(colors))
    for i, (r, g, b) in enumerate(colors):
        lut.SetTableValue(i, float(r), float(g), float(b), 1.0)
    lut.Build()
