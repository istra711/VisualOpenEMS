"""Progress bar with a peak-hold marker.

The openEMS residual-energy percentage is not monotonic: the reported energy
level can rise again after a small dip, so the bar may move backwards.  The
marker remembers the highest value reached during the current run and stays
there until the next run starts.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QProgressBar, QWidget


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

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().paintEvent(event)
        peak = self._peak
        if peak is None or peak <= self.minimum() or self.maximum() <= self.minimum():
            return

        frac = (peak - self.minimum()) / (self.maximum() - self.minimum())
        track = self.contentsRect()
        # Keep the marker inside the rounded track even at 0/100 %.
        x = track.left() + round(frac * max(1, track.width() - 1))
        x = max(track.left() + 1, min(track.right() - 1, x))
        y0 = track.top() + 2
        y1 = track.bottom() - 2

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
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
