"""Read-only report window (mesh timing, simulation status).

Shows a plain-text report with a monospace font and offers Copy / Save, so a
developer can paste the numbers into a bug report or hand them on. Used by the
Mesh menu ("Mesh Timing / Feasibility…") and the Simulation menu
("Simulation Status…"); the same text is appended to the run log.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ReportDialog(QDialog):
    """Modeless window showing one text report."""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        *,
        hint: str = "",
        save_name: str = "report.txt",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.resize(880, 640)
        self._text = text
        self._save_name = save_name

        root = QVBoxLayout(self)
        head = QLabel(title)
        head.setStyleSheet("font-weight: bold;")
        root.addWidget(head)
        if hint:
            sub = QLabel(hint)
            sub.setWordWrap(True)
            root.addWidget(sub)

        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        view.setFont(QFont("Consolas", 9))
        view.setPlainText(text)
        root.addWidget(view, 1)

        row = QHBoxLayout()
        copy_btn = QPushButton("Copy")
        copy_btn.setToolTip("Copy the report to the clipboard")
        copy_btn.clicked.connect(self._copy)
        row.addWidget(copy_btn)
        save_btn = QPushButton("Save…")
        save_btn.setToolTip("Write the report to a text file")
        save_btn.clicked.connect(self._save)
        row.addWidget(save_btn)
        row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        row.addWidget(close_btn)
        root.addLayout(row)

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._text)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save report", self._save_name)
        if not path:
            return
        Path(path).write_text(self._text, encoding="utf-8")


def show_report(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    hint: str = "",
    save_name: str = "report.txt",
) -> ReportDialog:
    """Open a report window (kept modeless so the main window stays usable)."""
    dlg = ReportDialog(parent, title, text, hint=hint, save_name=save_name)
    dlg.show()
    return dlg
