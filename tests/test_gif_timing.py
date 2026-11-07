"""GIF export timing: Pillow expects milliseconds, not seconds."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.postproc.video_timing import gif_frame_duration_ms


def test_gif_frame_duration_uses_milliseconds() -> None:
    assert gif_frame_duration_ms(10) == 100
    assert gif_frame_duration_ms(5) == 200
    # Very high fps is clamped to the browser-safe GIF floor.
    assert gif_frame_duration_ms(60) == 20
    assert gif_frame_duration_ms(0) == 1000
