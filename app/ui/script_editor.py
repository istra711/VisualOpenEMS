"""View / edit the generated openEMS simulation script (project_run.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class ScriptEditorDialog(QDialog):
    """Editable text view of a generated run script.

    script_path      file to display (created by the caller or Regenerate)
    regenerate_cb    optional callable -> Path: rebuilds the script from the
                     current project (the dialog reloads the returned path)
    """

    def __init__(
        self,
        script_path: Path | str,
        regenerate_cb: Callable[[], Path] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.script_path = Path(script_path)
        self.regenerate_cb = regenerate_cb
        self._saved_text: str | None = None

        self.setWindowTitle(f"Simulation script — {self.script_path.name}")
        self.resize(1000, 700)

        root = QVBoxLayout(self)
        lbl_title = QLabel(
            "Generated FDTD script (edit freely — Run Simulation asks before regenerating it):"
        )
        root.addWidget(lbl_title)

        self.lbl_path = QLabel(str(self.script_path))
        self.lbl_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_path.setWordWrap(True)
        self.lbl_path.setStyleSheet("color:#666;")
        root.addWidget(self.lbl_path)

        self.editor = QPlainTextEdit()
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.editor.setFont(font)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(" ") * 4)
        self.editor.setPlaceholderText("project_run.py not generated yet — press Regenerate")
        root.addWidget(self.editor, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        root.addWidget(self.lbl_status)

        bar = QHBoxLayout()
        self.btn_regenerate = QPushButton("Regenerate from project")
        self.btn_regenerate.setToolTip(
            "Overwrite this file with a fresh script built from the current project "
            "(discards edits)"
        )
        self.btn_reload = QPushButton("Revert")
        self.btn_reload.setToolTip("Discard edits and reload the file from disk")
        self.btn_save = QPushButton("Save")
        self.btn_save.setDefault(True)
        self.btn_close = QPushButton("Close")
        bar.addWidget(self.btn_regenerate)
        bar.addWidget(self.btn_reload)
        bar.addWidget(self.btn_save)
        bar.addStretch(1)
        bar.addWidget(self.btn_close)
        root.addLayout(bar)

        self.btn_regenerate.clicked.connect(self._on_regenerate)
        self.btn_reload.clicked.connect(self._on_reload)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_close.clicked.connect(self.close)
        self.editor.textChanged.connect(self._update_status)

        self.reload()

    # ----------------------------------------------------------------- io
    def load(self, path: Path | str) -> None:
        """Display `path` (used after Regenerate)."""
        self.script_path = Path(path)
        self.lbl_path.setText(str(self.script_path))
        self.reload()

    def show_path(self, path: Path | str) -> None:
        """Bring the dialog forward, switching the file only when safe.

        Unsaved edits are kept when the same file is requested again.
        """
        path = Path(path)
        if path != self.script_path:
            if self.is_modified() and not self._confirm(
                f"Switch to {path.name} and discard your edits?"
            ):
                pass
            else:
                self.load(path)
        elif not self.is_modified():
            self.reload()
        self.raise_()
        self.activateWindow()

    def reload(self) -> None:
        try:
            text = self.script_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            text = ""
        except OSError as e:
            self.lbl_status.setText(f"Read failed: {e}")
            return
        self.editor.setPlainText(text)
        self._saved_text = text
        self._update_status()

    def to_text(self) -> str:
        return self.editor.toPlainText()

    def is_modified(self) -> bool:
        return self.to_text() != (self._saved_text or "")

    def save(self) -> bool:
        try:
            self.script_path.parent.mkdir(parents=True, exist_ok=True)
            text = self.to_text()
            self.script_path.write_text(text, encoding="utf-8", newline="\n")
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return False
        self._saved_text = text
        self._update_status()
        return True

    # --------------------------------------------------------------- slots
    def _on_save(self) -> None:
        if self.save():
            self.lbl_status.setText(f"Saved: {self.script_path}")

    def _on_reload(self) -> None:
        if self.is_modified() and not self._confirm("Discard your edits and reload the file?"):
            return
        self.reload()

    def _on_regenerate(self) -> None:
        if self.regenerate_cb is None:
            QMessageBox.warning(self, "Regenerate", "No project generator attached to this dialog.")
            return
        if self.is_modified() and not self._confirm("Regenerate and discard your edits?"):
            return
        try:
            path = self.regenerate_cb()
        except Exception as e:  # noqa: BLE001 — show, don't crash the GUI
            QMessageBox.critical(self, "Regenerate failed", str(e))
            return
        self.load(path)
        self.lbl_status.setText(f"Regenerated: {self.script_path}")

    def _confirm(self, question: str) -> bool:
        return (
            QMessageBox.question(
                self,
                "Unsaved changes",
                question,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            == QMessageBox.StandardButton.Yes
        )

    def _update_status(self) -> None:
        if self.is_modified():
            self.lbl_status.setText("Edited (not saved)")
            self.setWindowTitle(f"Simulation script — {self.script_path.name} *")
        else:
            lines = self.to_text().count("\n") + 1
            self.lbl_status.setText(f"{lines} lines — loaded from {self.script_path}")
            self.setWindowTitle(f"Simulation script — {self.script_path.name}")

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt naming
        if self.is_modified() and not self._confirm("Close and discard your edits?"):
            event.ignore()
            return
        event.accept()
