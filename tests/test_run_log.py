"""Run-log banners and elapsed-time formatting."""

from __future__ import annotations

from datetime import datetime

from app.runlog import format_elapsed, format_run_end, format_run_start


def test_format_elapsed_seconds() -> None:
    assert format_elapsed(0) == "0.0 s"
    assert format_elapsed(12.34) == "12.3 s"
    assert format_elapsed(59.94) == "59.9 s"


def test_format_elapsed_minutes_hours() -> None:
    assert format_elapsed(83.4) == "1 m 23.4 s"
    assert format_elapsed(60) == "1 m 00.0 s"
    assert format_elapsed(3723.4) == "1 h 02 m 03.4 s"


def test_start_banner() -> None:
    when = datetime(2026, 10, 19, 14, 3, 7)
    line = format_run_start(when)
    assert line == "########### START (14:03:07) #############"


def test_end_banner() -> None:
    when = datetime(2026, 10, 19, 14, 4, 30)
    line = format_run_end(83.4, when)
    assert line == (
        "################ END (14:04:30, duration 1 m 23.4 s) ###############"
    )
