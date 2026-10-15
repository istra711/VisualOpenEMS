"""Convert assets/logo.svg to assets/app_icon.ico (multi-size Windows icon).

Uses PySide6's QSvgRenderer to rasterize the SVG at 256px (no external
tools) and Pillow to write the .ico with the sizes Windows uses (16..256).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent
SVG = ROOT / "assets" / "logo.svg"
OUT = ROOT / "assets" / "app_icon.ico"
SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def render(size: int) -> QImage:
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        raise RuntimeError(f"invalid SVG: {SVG}")
    # RGBA8888 so the raw bytes match Pillow's "RGBA" (ARGB32 is BGRA on disk)
    img = QImage(size, size, QImage.Format.Format_RGBA8888)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    # scale the SVG's viewBox to a square, centered (letterboxed)
    vb = renderer.viewBox()
    side = min(vb.width(), vb.height())
    target = QRectF((vb.width() - side) / 2, (vb.height() - side) / 2, side, side)
    renderer.render(painter, target)
    painter.end()
    return img


def main() -> int:
    QGuiApplication(sys.argv[:1])
    if not SVG.exists():
        print(f"missing {SVG}")
        return 1
    from PIL import Image

    qimg = render(256)
    data = bytes(qimg.constBits())  # already the full buffer in PySide6
    pil = Image.frombuffer(
        "RGBA", (qimg.width(), qimg.height()), data, "raw", "RGBA", 0, 1
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Pillow scales the 256px master down for every requested size
    pil.save(OUT, format="ICO", sizes=SIZES)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
