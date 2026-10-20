"""Tests for the scientific number field used by the Field Viewer colour limits."""

from __future__ import annotations

import pytest

from app.numfmt import format_number, parse_number


@pytest.mark.parametrize(
    "value, text",
    [
        (0.0, "0"),
        (0.095199, "0.095199"),
        (1.264e-9, "1.264e-09"),
        (2.5e-8, "2.5e-08"),
        (1234.5, "1234.5"),
        (-3.5e-12, "-3.5e-12"),
    ],
)
def test_format_number(value: float, text: str) -> None:
    assert format_number(value) == text


def test_format_number_handles_non_finite() -> None:
    assert format_number(float("nan")) == "0"
    assert format_number(float("inf")) == "0"


@pytest.mark.parametrize(
    "text, value",
    [
        ("1.5e-9", 1.5e-9),
        ("1,5e-9", 1.5e-9),  # German locale form
        (" 0,001 ", 0.001),
        ("-3.5e-12", -3.5e-12),
        ("6.49E-07", 6.49e-7),
        ("12345", 12345.0),
    ],
)
def test_parse_number(text: str, value: float) -> None:
    assert parse_number(text) == pytest.approx(value)


@pytest.mark.parametrize("text", ["", "   ", "garbage", "1e", "nan", "inf"])
def test_parse_number_rejects_invalid(text: str) -> None:
    assert parse_number(text) is None


def test_round_trip_keeps_field_magnitudes() -> None:
    # the old QDoubleSpinBox (6 decimals) turned all of these into 0.000000
    for value in (1.264e-9, 6.49e-7, 2.3e-11, 0.095199, 34.676):
        assert parse_number(format_number(value)) == pytest.approx(value)
