"""Live 2-D previews for primitive dialogs.

``PolygonPreview`` draws the polygon currently typed into
:class:`app.ui.geometry_editors.PrimitivePolygonDialog` while the user edits
the point list - no OpenGL, no matplotlib, no CSXCAD: a plain ``QPainter``
widget so a repaint costs microseconds and works with the offscreen platform.

The drawing follows the same cyclic plane mapping CSXCAD uses for
``AddPolygon`` / ``AddLinPoly`` (axis x -> (y, z), y -> (z, x), z -> (x, y)),
so the picture matches what the solver will see.  Extruded polygons
(``length > 0``) get an offset outline as a depth cue.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

#: normal axis -> (first, second) in-plane axis, CSXCAD's cyclic mapping
PLANE_AXES: dict[str, tuple[str, str]] = {
    "x": ("y", "z"),
    "y": ("z", "x"),
    "z": ("x", "y"),
}


def _nice_step(raw: float) -> float:
    """Round ``raw`` up to 1/2/5 * 10^n (axis tick spacing)."""
    if not math.isfinite(raw) or raw <= 0.0:
        return 1.0
    exp = math.floor(math.log10(raw))
    base = 10.0**exp
    for mult in (1.0, 2.0, 5.0, 10.0):
        if raw <= mult * base:
            return mult * base
    return 10.0 * base


def _fmt(value: float) -> str:
    """Compact tick label without float noise (``1e-16`` -> ``0``)."""
    v = round(float(value), 6)
    if v == 0.0:
        return "0"
    return f"{v:g}"


class PolygonPreview(QWidget):
    """Top view of a polygon in its own plane, updated live while editing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[float, float]] = []
        self._axis = "z"
        self._coord = 0.0
        self._length = 0.0
        self._unit = "mm"
        self.setMinimumSize(220, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setToolTip(
            "Live preview of the polygon in its plane.\n"
            "Points are shown in the order you type them (numbered)."
        )

    # ------------------------------------------------------------------ API
    def set_polygon(
        self,
        points: list[list[float]] | list[tuple[float, float]],
        *,
        axis: str = "z",
        coord: float = 0.0,
        length: float = 0.0,
        unit: str = "mm",
    ) -> None:
        """Replace the drawn polygon and repaint.

        Non-finite points (``nan``/``inf`` typed by mistake) are dropped, so a
        half-typed line never breaks the paint code.
        """
        clean: list[tuple[float, float]] = []
        for pt in points:
            if len(pt) < 2:
                continue
            x, y = float(pt[0]), float(pt[1])
            if math.isfinite(x) and math.isfinite(y):
                clean.append((x, y))
        self._points = clean
        self._axis = axis if axis in PLANE_AXES else "z"
        self._coord = float(coord) if math.isfinite(float(coord)) else 0.0
        self._length = max(0.0, float(length)) if math.isfinite(float(length)) else 0.0
        self._unit = unit or "mm"
        self.update()

    def point_count(self) -> int:
        return len(self._points)

    def plane_axes(self) -> tuple[str, str]:
        return PLANE_AXES[self._axis]

    def is_extruded(self) -> bool:
        return self._length > 0.0

    def summary(self) -> str:
        """Caption line (also used by tests instead of pixel comparisons)."""
        a0, a1 = self.plane_axes()
        left = f"plane {self._axis} = {_fmt(self._coord)} {self._unit}  ({a0}/{a1})"
        if self.is_extruded():
            return f"{left}  ·  extruded {_fmt(self._length)} {self._unit} along {self._axis}"
        return f"{left}  ·  flat sheet"

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(300, 240)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(220, 180)

    # -------------------------------------------------------------- painting
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = self.palette()
        base = pal.color(QPalette.ColorRole.Base)
        text = pal.color(QPalette.ColorRole.WindowText)
        accent = QColor(0x2F, 0x81, 0xF7)

        painter.fillRect(self.rect(), base)
        painter.setPen(QPen(pal.color(QPalette.ColorRole.Mid), 1.0))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))

        caption_font = QFont(self.font())
        caption_font.setPointSizeF(max(7.5, self.font().pointSizeF() - 0.5))
        painter.setFont(caption_font)
        fm = QFontMetricsF(caption_font)
        cap_h = fm.height() + 4.0

        # caption row
        caption = fm.elidedText(
            self.summary(), Qt.TextElideMode.ElideRight, self.width() - 16.0
        )
        painter.setPen(QPen(text))
        painter.drawText(
            QRectF(8.0, 3.0, self.width() - 16.0, cap_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            caption,
        )

        plot = QRectF(
            46.0,
            cap_h + 6.0,
            max(10.0, self.width() - 56.0),
            max(10.0, self.height() - cap_h - 30.0),
        )

        if not self._points:
            painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 140)))
            painter.drawText(
                plot,
                int(Qt.AlignmentFlag.AlignCenter),
                "Type one “x, y” point per line",
            )
            painter.end()
            return

        xs = [p[0] for p in self._points]
        ys = [p[1] for p in self._points]
        xmin, xmax = min(xs), max(xs)
        ymin, ymax = min(ys), max(ys)
        span_x, span_y = xmax - xmin, ymax - ymin
        if span_x <= 0.0:
            xmin, xmax = xmin - 5.0, xmax + 5.0
            span_x = 10.0
        if span_y <= 0.0:
            ymin, ymax = ymin - 5.0, ymax + 5.0
            span_y = 10.0
        pad = 0.12 * max(span_x, span_y)
        xmin, xmax = xmin - pad, xmax + pad
        ymin, ymax = ymin - pad, ymax + pad
        span_x, span_y = xmax - xmin, ymax - ymin

        # equal aspect: a 45 deg edge must look like 45 deg
        scale = min(plot.width() / span_x, plot.height() / span_y)
        cx = 0.5 * (xmin + xmax)
        cy = 0.5 * (ymin + ymax)
        ox = plot.center().x() - cx * scale
        oy = plot.center().y() + cy * scale

        def to_px(x: float, y: float) -> QPointF:
            return QPointF(ox + x * scale, oy - y * scale)

        # ---- grid + ticks
        step = _nice_step(max(span_x, span_y) / 5.0)
        grid_pen = QPen(QColor(text.red(), text.green(), text.blue(), 34), 1.0)
        zero_pen = QPen(QColor(text.red(), text.green(), text.blue(), 90), 1.0)
        tick_font = QFont(caption_font)
        tick_font.setPointSizeF(max(6.5, caption_font.pointSizeF() - 1.0))
        painter.setFont(tick_font)
        tick_fm = QFontMetricsF(tick_font)

        v = math.ceil(xmin / step) * step
        while v <= xmax + 1e-12:
            px = to_px(v, 0.0).x()
            painter.setPen(zero_pen if abs(v) < 1e-12 else grid_pen)
            painter.drawLine(QPointF(px, plot.top()), QPointF(px, plot.bottom()))
            painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 170)))
            label = _fmt(v)
            painter.drawText(
                QRectF(px - 26.0, plot.bottom() + 2.0, 52.0, tick_fm.height()),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                label,
            )
            v += step

        v = math.ceil(ymin / step) * step
        while v <= ymax + 1e-12:
            py = to_px(0.0, v).y()
            painter.setPen(zero_pen if abs(v) < 1e-12 else grid_pen)
            painter.drawLine(QPointF(plot.left(), py), QPointF(plot.right(), py))
            painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 170)))
            painter.drawText(
                QRectF(0.0, py - tick_fm.height() / 2.0, plot.left() - 4.0, tick_fm.height()),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                _fmt(v),
            )
            v += step

        painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 120), 1.0))
        painter.drawRect(plot)

        # ---- polygon
        pts_px = [to_px(x, y) for x, y in self._points]
        if len(pts_px) >= 2:
            path = QPainterPath(pts_px[0])
            for pt in pts_px[1:]:
                path.lineTo(pt)
            if len(pts_px) >= 3:
                path.closeSubpath()

            if self.is_extruded():
                # depth cue: the same outline shifted, thickness not to scale
                shift = QPointF(5.0, 5.0)
                ghost = QPainterPath(path)
                ghost.translate(shift.x(), shift.y())
                ghost_pen = QPen(QColor(accent.red(), accent.green(), accent.blue(), 110), 1.0)
                ghost_pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(ghost_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(ghost)

            if len(pts_px) >= 3:
                fill = QColor(accent)
                fill.setAlpha(55)
                painter.setBrush(fill)
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
            pen = QPen(accent, 1.8)
            if len(pts_px) < 3:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)

        # ---- vertices + order numbers
        marker_font = QFont(tick_font)
        marker_font.setPointSizeF(max(6.5, tick_font.pointSizeF()))
        painter.setFont(marker_font)
        marker_fm = QFontMetricsF(marker_font)
        for idx, pt in enumerate(pts_px, start=1):
            painter.setPen(QPen(accent, 1.2))
            painter.setBrush(base)
            painter.drawEllipse(pt, 3.0, 3.0)
            painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 200)))
            painter.drawText(
                QRectF(
                    pt.x() + 5.0,
                    pt.y() - marker_fm.height() - 1.0,
                    marker_fm.horizontalAdvance(str(idx)) + 6.0,
                    marker_fm.height(),
                ),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                str(idx),
            )

        if len(pts_px) < 3:
            painter.setPen(QPen(QColor(text.red(), text.green(), text.blue(), 150)))
            painter.drawText(
                QRectF(plot.left(), plot.top() + 4.0, plot.width(), tick_fm.height() * 1.4),
                int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
                "3 points needed",
            )

        painter.end()
