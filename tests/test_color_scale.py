"""Colour-scale helpers of the Field Viewer (settings dialog + lookup table)."""

from __future__ import annotations

import pytest

from app.colormaps import (
    COLORMAPS,
    COLOR_SCALE_MAX_STEPS,
    COLOR_SCALE_MIN_STEPS,
    DEFAULT_COLOR_SCALE,
    colormap_colors,
    colormap_names,
    fill_lookup_table,
)


def test_default_is_the_previous_blue_to_red_table():
    assert DEFAULT_COLOR_SCALE == {"cmap": "jet", "reverse": False, "steps": 256}
    assert "jet" in colormap_names()


def test_every_offered_colormap_has_a_valid_name():
    for label, key in COLORMAPS:
        assert label and key
        assert key in colormap_names()


def test_colormap_colors_respect_steps_reverse_and_clamping():
    colors = colormap_colors("viridis", 8)
    assert len(colors) == 8
    assert all(0.0 <= c <= 1.0 for rgb in colors for c in rgb)
    assert colormap_colors("viridis", 8, True) == list(reversed(colors))

    # out-of-range step counts are clamped, unknown maps fall back safely
    assert len(colormap_colors("viridis", 1)) == COLOR_SCALE_MIN_STEPS
    assert len(colormap_colors("viridis", 10_000)) == COLOR_SCALE_MAX_STEPS
    assert len(colormap_colors("no-such-map", 4)) == 4


def test_fill_lookup_table_keeps_colour_count_and_values():
    import vtk

    lut = vtk.vtkLookupTable()
    colors = colormap_colors("plasma", 16)
    fill_lookup_table(lut, colors)
    assert lut.GetNumberOfTableValues() == 16
    # the VTK table stores 8-bit entries, hence the 1/255 tolerance
    for i, (r, g, b) in enumerate(colors):
        got = lut.GetTableValue(i)
        assert got[0] == pytest.approx(r, abs=1 / 255)
        assert got[1] == pytest.approx(g, abs=1 / 255)
        assert got[2] == pytest.approx(b, abs=1 / 255)
        assert got[3] == pytest.approx(1.0)
