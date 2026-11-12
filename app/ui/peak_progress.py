"""Progress bar with a peak-hold marker.

The openEMS residual-energy percentage is not monotonic: the reported energy
level can rise again after a small dip, so the bar may move backwards.  The
marker remembers the highest value reached during the current run and stays
there until the next run starts.
"""

from __future__ import annotations

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QProgressBar,
    QStyle,
    QStyleOptionProgressBar,
    QWidget,
)


class PeakHoldProgressBar(QProgressBar):
    """A QProgressBar that draws a marker at its highest reached value."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._peak: int | None = None

    @property
    def peak(self) -> int | None:
        """Highest value reached since the last ``reset_peak()``."""
        return self._peak

    def set_peak(self, value: int | None) -> None:
        if value is None:
            self.reset_peak()
            return
        peak = max(self.minimum(), min(self.maximum(), int(value)))
        if self._peak is None or peak > self._peak:
            self._peak = peak
            self.update()

    def reset_peak(self) -> None:
        if self._peak is not None:
            self._peak = None
            self.update()

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt naming
        super().setValue(value)
        value = self.value()
        if self._peak is None or value > self._peak:
            self._peak = value
        self.update()

    def _peak_marker_geometry(self) -> tuple[int, int, int, QRect] | None:
        """Marker x/y and the style's actual progress-track rectangle."""
        peak = self._peak
        if peak is None or peak <= self.minimum() or self.maximum() <= self.minimum():
            return None
        frac = (peak - self.minimum()) / (self.maximum() - self.minimum())
        # Use the style's actual progress-area rectangle, not the widget
        # contents: some styles reserve margins or a text area.  The extra
        # inset keeps the line, its caps and the white halo fully inside the
        # painted bar, including at 0 % and 100 %.
        opt = QStyleOptionProgressBar()
        self.initStyleOption(opt)
        track = self.style().subElementRect(
            QStyle.SubElement.SE_ProgressBarContents, opt, self
        )
        if track.width() < 12 or track.height() < 8:
            track = self.contentsRect()
        inset_x = 4
        left = track.left() + inset_x
        right = track.right() - inset_x
        if right <= left:
            return None
        x = left + round(frac * (right - left))
        y0 = track.top() + 3
        y1 = track.bottom() - 3
        if y1 <= y0:
            return None
        return x, y0, y1, track

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        geometry = self._peak_marker_geometry()
        if geometry is None:
            return
        x, y0, y1, track = geometry

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Clip to the track as a final guard against caps/halo bleeding over
        # the rounded frame.
        painter.setClipRect(track)
        # White halo keeps the marker visible on green (running) and red
        # (stopped before the end criterion).
        painter.setPen(QPen(QColor(255, 255, 255, 215), 3.0))
        painter.drawLine(x, y0, x, y1)
        painter.setPen(QPen(QColor(17, 24, 39), 1.4))
        painter.drawLine(x, y0, x, y1)
        # Small caps make the line read as a peak-hold marker rather than a
        # chunk boundary.
        painter.drawLine(x - 3, y0, x + 3, y0)
        painter.drawLine(x - 3, y1, x + 3, y1)
        painter.end()
