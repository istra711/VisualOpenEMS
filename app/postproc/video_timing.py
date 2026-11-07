"""Playback/export timing helpers that do not import Qt or VTK."""

from __future__ import annotations


def gif_frame_duration_ms(fps: int) -> int:
    """Pillow GIF frame delay in milliseconds for a target fps.

    imageio's Pillow GIF writer passes ``duration`` straight to Pillow, which
    expects milliseconds.  A value of ``1.0 / fps`` (0.1–0.2) is rounded to zero,
    so different fps settings both fall back to Pillow's default ~100 ms.
    Keep a 20 ms floor: browsers treat shorter GIF delays as ~100 ms anyway.
    """
    return max(20, int(round(1000.0 / max(1, int(fps)))))
