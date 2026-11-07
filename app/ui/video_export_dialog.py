"""Options dialog for Field Viewer video / animated-GIF export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


@dataclass
class VideoExportOptions:
    path: Path
    fmt: str  # "mp4" | "gif"
    width: int
    height: int
    fps: int
    start: int  # 1-based, inclusive
    end: int  # 1-based, inclusive
    step: int = 1


_RESOLUTIONS: tuple[tuple[str, int, int], ...] = (
    ("Current view", 0, 0),
    ("1920 × 1080", 1920, 1080),
    ("1280 × 720", 1280, 720),
    ("960 × 540", 960, 540),
    ("640 × 480 (VGA)", 640, 480),
    ("640 × 360", 640, 360),
    ("480 × 480", 480, 480),
    ("320 × 240 (QVGA)", 320, 240),
    ("1080 × 1080", 1080, 1080),
    ("1080 × 1350", 1080, 1350),
    ("Custom", -1, -1),
)


class VideoExportDialog(QDialog):
    """Configure format, resolution, fps/duration and the frame range."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        suggested_path: Path,
        frame_count: int,
        current_size: tuple[int, int],
        initial_fmt: str = "mp4",
        initial_fps: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export field animation")
        self.setModal(True)
        self.resize(520, 430)

        self.frame_count = max(1, int(frame_count))
        self.current_size = (
            max(2, int(current_size[0])),
            max(2, int(current_size[1])),
        )
        self.options: VideoExportOptions | None = None
        self._sync_guard = False
        self._fmt = "gif" if str(initial_fmt).lower() == "gif" else "mp4"

        root = QVBoxLayout(self)
        form = QFormLayout()
        root.addLayout(form)

        self.cmb_format = QComboBox()
        self.cmb_format.addItem("MP4 video", "mp4")
        self.cmb_format.addItem("Animated GIF", "gif")
        self.cmb_format.setCurrentIndex(0 if self._fmt == "mp4" else 1)
        form.addRow("Format", self.cmb_format)

        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.edt_path = QLineEdit(str(suggested_path))
        self.btn_browse = QPushButton("…")
        self.btn_browse.setToolTip("Choose the output file")
        path_layout.addWidget(self.edt_path, 1)
        path_layout.addWidget(self.btn_browse)
        form.addRow("Output file", path_row)

        self.cmb_resolution = QComboBox()
        for label, w, h in _RESOLUTIONS:
            self.cmb_resolution.addItem(label, (w, h))
        form.addRow("Resolution", self.cmb_resolution)

        size_row = QWidget()
        size_layout = QHBoxLayout(size_row)
        size_layout.setContentsMargins(0, 0, 0, 0)
        self.spn_width = QSpinBox()
        self.spn_width.setRange(16, 4096)
        self.spn_width.setSingleStep(2)
        self.spn_height = QSpinBox()
        self.spn_height.setRange(16, 4096)
        self.spn_height.setSingleStep(2)
        size_layout.addWidget(QLabel("W"))
        size_layout.addWidget(self.spn_width, 1)
        size_layout.addWidget(QLabel("H"))
        size_layout.addWidget(self.spn_height, 1)
        form.addRow("Size (px)", size_row)

        self.spn_fps = QSpinBox()
        self.spn_fps.setRange(1, 60)
        self.spn_fps.setSuffix(" fps")
        form.addRow("Frame rate", self.spn_fps)

        self.spn_duration = QDoubleSpinBox()
        self.spn_duration.setRange(0.1, 3600.0)
        self.spn_duration.setDecimals(1)
        self.spn_duration.setSingleStep(0.5)
        self.spn_duration.setSuffix(" s")
        form.addRow("Duration", self.spn_duration)

        range_row = QWidget()
        range_layout = QHBoxLayout(range_row)
        range_layout.setContentsMargins(0, 0, 0, 0)
        self.spn_start = QSpinBox()
        self.spn_start.setRange(1, self.frame_count)
        self.spn_start.setValue(1)
        self.spn_end = QSpinBox()
        self.spn_end.setRange(1, self.frame_count)
        self.spn_end.setValue(self.frame_count)
        self.spn_step = QSpinBox()
        self.spn_step.setRange(1, self.frame_count)
        self.spn_step.setValue(1)
        range_layout.addWidget(QLabel("from"))
        range_layout.addWidget(self.spn_start, 1)
        range_layout.addWidget(QLabel("to"))
        range_layout.addWidget(self.spn_end, 1)
        range_layout.addWidget(QLabel("every"))
        range_layout.addWidget(self.spn_step, 1)
        form.addRow("Frames", range_row)

        self.lbl_info = QLabel()
        self.lbl_info.setWordWrap(True)
        self.lbl_info.setStyleSheet("color: #9eb1c8;")
        root.addWidget(self.lbl_info)
        root.addStretch(1)

        buttons = QDialogButtonBox(self)
        self.btn_ok = buttons.addButton(
            "Export", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        root.addWidget(buttons)

        # Defaults / connections
        default_fps = initial_fps
        if default_fps is None:
            default_fps = 24 if self._fmt == "mp4" else 10
        self.spn_fps.setValue(max(1, min(60, int(default_fps))))
        self._set_current_resolution()
        self._set_format(self._fmt, initial=True)
        for w in (
            self.spn_start,
            self.spn_end,
            self.spn_step,
            self.spn_fps,
        ):
            w.valueChanged.connect(self._refresh_duration)
        for w in (self.spn_start, self.spn_end, self.spn_step):
            w.valueChanged.connect(self._refresh_info)
        self.spn_duration.valueChanged.connect(self._on_duration_changed)
        self.cmb_format.currentIndexChanged.connect(
            lambda _i: self._set_format(str(self.cmb_format.currentData()))
        )
        self.cmb_resolution.currentIndexChanged.connect(self._on_resolution_changed)
        self.spn_width.valueChanged.connect(self._refresh_info)
        self.spn_height.valueChanged.connect(self._refresh_info)
        self.edt_path.textChanged.connect(self._refresh_info)
        self.btn_browse.clicked.connect(self._on_browse)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        self._refresh_duration()
        self._refresh_info()

    # --- helpers ---------------------------------------------------------
    def _is_gif(self) -> bool:
        return str(self.cmb_format.currentData() or "mp4") == "gif"

    def _selected_count(self) -> int:
        start = int(self.spn_start.value())
        end = int(self.spn_end.value())
        step = max(1, int(self.spn_step.value()))
        if end < start:
            return 0
        return ((end - start) // step) + 1

    def _set_current_resolution(self) -> None:
        self.cmb_resolution.blockSignals(True)
        self.cmb_resolution.setCurrentIndex(0)  # Current view
        self.cmb_resolution.blockSignals(False)
        w, h = self.current_size
        self.spn_width.setValue(w)
        self.spn_height.setValue(h)
        self.spn_width.setEnabled(False)
        self.spn_height.setEnabled(False)

    def _set_format(self, fmt: str, *, initial: bool = False) -> None:
        self._fmt = "gif" if str(fmt).lower() == "gif" else "mp4"
        is_gif = self._fmt == "gif"
        self.cmb_format.blockSignals(True)
        self.cmb_format.setCurrentIndex(0 if not is_gif else 1)
        self.cmb_format.blockSignals(False)
        # GIF's 20 ms minimum delay caps it at 50 fps.
        self.spn_fps.setMaximum(50 if is_gif else 60)
        if is_gif:
            self._replace_suffix(".gif")
            if not self.spn_fps.hasFocus():
                self.spn_fps.setValue(min(10, self.spn_fps.maximum()))
        else:
            self._replace_suffix(".mp4")
            if not self.spn_fps.hasFocus():
                self.spn_fps.setValue(min(24, self.spn_fps.maximum()))
        self._refresh_duration()
        self._refresh_info()

    def _replace_suffix(self, suffix: str) -> None:
        text = self.edt_path.text().strip()
        if not text:
            return
        p = Path(text)
        if p.suffix.lower() not in (".mp4", ".m4v", ".gif"):
            p = p.with_suffix(suffix)
        elif p.suffix.lower() != suffix:
            p = p.with_suffix(suffix)
        self.edt_path.setText(str(p))

    def _on_resolution_changed(self, _idx: int) -> None:
        data = self.cmb_resolution.currentData()
        if not data:
            return
        w, h = int(data[0]), int(data[1])
        custom = w < 0
        self.spn_width.setEnabled(custom)
        self.spn_height.setEnabled(custom)
        if not custom and w > 0 and h > 0:
            self.spn_width.setValue(w)
            self.spn_height.setValue(h)
        elif custom:
            # keep the last preset/current values, just make them editable
            pass
        self._refresh_info()

    def _current_size(self) -> tuple[int, int]:
        data = self.cmb_resolution.currentData()
        if data and int(data[0]) > 0 and int(data[1]) > 0:
            return int(data[0]), int(data[1])
        w = max(16, int(self.spn_width.value()))
        h = max(16, int(self.spn_height.value()))
        return (w - w % 2, h - h % 2)

    def _refresh_duration(self, *_args) -> None:
        if self._sync_guard:
            return
        count = self._selected_count()
        fps = max(1, int(self.spn_fps.value()))
        duration = count / fps if count else 0.0
        self._sync_guard = True
        try:
            self.spn_duration.setValue(max(0.1, duration))
        finally:
            self._sync_guard = False
        self._refresh_info()

    def _on_duration_changed(self, seconds: float) -> None:
        if self._sync_guard:
            return
        count = self._selected_count()
        if count <= 0:
            return
        fps = max(1, min(self.spn_fps.maximum(), int(round(count / max(0.1, seconds)))))
        self._sync_guard = True
        try:
            self.spn_fps.setValue(fps)
            self.spn_duration.setValue(count / fps)
        finally:
            self._sync_guard = False
        self._refresh_info()

    def _refresh_info(self, *_args) -> None:
        count = self._selected_count()
        fps = max(1, int(self.spn_fps.value()))
        w, h = self._current_size()
        duration = count / fps if count else 0.0
        fmt = "GIF" if self._is_gif() else "MP4"
        extra = " · GIF delay limited to 20 ms" if self._is_gif() else ""
        self.lbl_info.setText(
            f"{fmt} · {count} frames · {w}×{h} · {fps} fps · "
            f"{duration:.1f} s{extra}"
        )
        self.btn_ok.setEnabled(count >= 2 and bool(self.edt_path.text().strip()))

    def _on_browse(self) -> None:
        is_gif = self._is_gif()
        suffix = ".gif" if is_gif else ".mp4"
        filters = (
            "Animated GIF (*.gif);;All files (*)"
            if is_gif
            else "MP4 video (*.mp4);;All files (*)"
        )
        chosen, _selected = QFileDialog.getSaveFileName(
            self,
            "Save animation" if is_gif else "Save video",
            self.edt_path.text().strip() or str(Path("field" + suffix)),
            filters,
        )
        if not chosen:
            return
        p = Path(chosen)
        if p.suffix.lower() != suffix:
            p = p.with_suffix(suffix)
        self.edt_path.setText(str(p))
        self._refresh_info()

    def _on_accept(self) -> None:
        if int(self.spn_start.value()) > int(self.spn_end.value()):
            QMessageBox.warning(
                self, "Export field animation", "Start frame must not be after end frame."
            )
            return
        if self._selected_count() < 2:
            QMessageBox.warning(
                self, "Export field animation", "Select at least two frames."
            )
            return
        text = self.edt_path.text().strip()
        if not text:
            QMessageBox.warning(
                self, "Export field animation", "Choose an output file."
            )
            return
        is_gif = self._is_gif()
        p = Path(text)
        suffix = ".gif" if is_gif else ".mp4"
        if p.suffix.lower() != suffix:
            p = p.with_suffix(suffix)
        w, h = self._current_size()
        self.options = VideoExportOptions(
            path=p,
            fmt="gif" if is_gif else "mp4",
            width=max(2, w - w % 2),
            height=max(2, h - h % 2),
            fps=max(1, int(self.spn_fps.value())),
            start=max(1, int(self.spn_start.value())),
            end=min(self.frame_count, int(self.spn_end.value())),
            step=max(1, int(self.spn_step.value())),
        )
        self.accept()
