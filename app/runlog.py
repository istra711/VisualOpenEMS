"""Run-log formatting: START / END banners and elapsed step times.

Kept Qt-free so the format is testable without a GUI.
"""

from __future__ import annotations

from datetime import datetime

RUN_START_BANNER = "########### START ({clock}) #############"
RUN_END_BANNER = "################ END ({clock}, duration {elapsed}) ###############"
RUN_STEP_LINE = "--- [+{elapsed}] Step: {name} ---"


def format_elapsed(seconds: float) -> str:
    """Readable duration: ``12.3 s``, ``1 m 23.4 s``, ``1 h 02 m 03.4 s``."""
    s = max(0.0, float(seconds))
    if s < 60.0:
        return f"{s:.1f} s"
    if s < 3600.0:
        return f"{int(s // 60)} m {s % 60:04.1f} s"
    return f"{int(s // 3600)} h {int(s % 3600 // 60):02d} m {s % 60:04.1f} s"


def format_run_start(when: datetime | None = None) -> str:
    """START banner with the wall-clock time (HH:MM:SS)."""
    clock = (when or datetime.now()).strftime("%H:%M:%S")
    return RUN_START_BANNER.format(clock=clock)


def format_run_end(elapsed: float, when: datetime | None = None) -> str:
    """END banner with the wall-clock end time and the total duration."""
    clock = (when or datetime.now()).strftime("%H:%M:%S")
    return RUN_END_BANNER.format(clock=clock, elapsed=format_elapsed(elapsed))


def format_run_step(name: str, elapsed: float | None = None) -> str:
    """Marked step line: ``--- [+1 m 23.4 s] Step: <name> ---``.

    Without an active session (``elapsed is None``) the delta is omitted.
    """
    if elapsed is None:
        return f"--- Step: {name} ---"
    return RUN_STEP_LINE.format(elapsed=format_elapsed(elapsed), name=name)
