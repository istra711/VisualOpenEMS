"""Right-side AI assistant: deterministic diagnosis, chat and safe fixes."""

from __future__ import annotations

import html

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
    QApplication,
)

from app.ai.types import Diagnosis, FixProposal


class AiAssistantPanel(QWidget):
    """Modeless panel used by the main window's AI dock."""

    send_requested = Signal(str)
    stop_requested = Signal()
    apply_fix_requested = Signal(object)
    undo_requested = Signal()
    settings_requested = Signal()
    preview_requested = Signal()
    help_requested = Signal()
    explain_requested = Signal()
    clear_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._diagnosis: Diagnosis | None = None
        self._fixes: list[FixProposal] = []
        self._busy = False
        self._assistant_open = False
        self._last_answer = ""

        root = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("AI Assistant")
        title.setStyleSheet("font-weight: bold; font-size: 11pt;")
        head.addWidget(title)
        head.addStretch(1)
        self.btn_settings = QPushButton("Settings")
        self.btn_settings.setToolTip(
            "Choose OpenCode CLI or offline-only mode, model and timeout"
        )
        self.btn_settings.clicked.connect(self.settings_requested)
        head.addWidget(self.btn_settings)
        self.btn_help = QPushButton("Help")
        self.btn_help.clicked.connect(self.help_requested)
        head.addWidget(self.btn_help)
        root.addLayout(head)

        self.lbl_context = QLabel("")
        self.lbl_context.setWordWrap(True)
        self.lbl_context.setStyleSheet("color: #455a64;")
        root.addWidget(self.lbl_context)

        self.btn_sees = QPushButton("What the AI sees…")
        self.btn_sees.setToolTip(
            "Preview the exact redacted prompt before it is sent to the provider"
        )
        self.btn_sees.clicked.connect(self.preview_requested)
        root.addWidget(self.btn_sees)

        split = QSplitter(Qt.Orientation.Vertical)
        root.addWidget(split, 1)

        diag = QGroupBox("Run diagnosis and safe fixes")
        dv = QVBoxLayout(diag)
        self.diag_view = QTextBrowser()
        self.diag_view.setOpenExternalLinks(False)
        self.diag_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.diag_view.setHtml(
            "<p>Open <b>AI → Explain last run</b> after a simulation, or use "
            "<b>Explain last run</b> below. The deterministic diagnosis works "
            "without a cloud provider.</p>"
        )
        dv.addWidget(self.diag_view, 1)

        self.fix_list = QListWidget()
        self.fix_list.setToolTip(
            "Validated parameter changes proposed by the deterministic rules. "
            "Applying is your decision and can be undone."
        )
        self.fix_list.setMaximumHeight(98)
        self.fix_list.currentRowChanged.connect(self._on_fix_selected)
        dv.addWidget(QLabel("Suggested fixes"))
        dv.addWidget(self.fix_list)

        self.fix_diff = QPlainTextEdit()
        self.fix_diff.setReadOnly(True)
        self.fix_diff.setFont(QFont("Consolas", 9))
        self.fix_diff.setMaximumHeight(112)
        self.fix_diff.setPlaceholderText(
            "Select a fix to see the exact project-field diff."
        )
        dv.addWidget(self.fix_diff)

        fix_row = QHBoxLayout()
        self.btn_apply = QPushButton("Apply fix")
        self.btn_apply.setToolTip(
            "Apply the shown change to the project, then validate and mark the "
            "project modified. No simulation is started."
        )
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply_selected)
        fix_row.addWidget(self.btn_apply)
        self.btn_undo = QPushButton("Undo")
        self.btn_undo.setToolTip(
            "Restore the project snapshot from before the last applied AI fix"
        )
        self.btn_undo.setEnabled(False)
        self.btn_undo.clicked.connect(self.undo_requested)
        fix_row.addWidget(self.btn_undo)
        fix_row.addStretch(1)
        dv.addLayout(fix_row)
        split.addWidget(diag)

        chat = QGroupBox("Conversation")
        cv = QVBoxLayout(chat)
        self.chat_view = QTextBrowser()
        self.chat_view.setOpenExternalLinks(False)
        self.chat_view.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        cv.addWidget(self.chat_view, 1)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(
            "Ask about the current project or the last failed run…  "
            "Ctrl+Enter sends."
        )
        self.input.setFixedHeight(76)
        self.input.installEventFilter(self)
        cv.addWidget(self.input)
        chat_row = QHBoxLayout()
        self.btn_send = QPushButton("Send")
        self.btn_send.setToolTip("Send the question with the current context")
        self.btn_send.clicked.connect(self._send)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_requested)
        self.btn_explain = QPushButton("Explain")
        self.btn_explain.setToolTip(
            "Run the deterministic diagnosis and ask the AI to explain it"
        )
        self.btn_explain.clicked.connect(self.explain_requested)
        self.btn_clear = QPushButton("Clear")
        self.btn_clear.setToolTip(
            "Clear the conversation and start a new AI session on the next question"
        )
        self.btn_clear.clicked.connect(self._clear_chat)
        for button in (
            self.btn_send,
            self.btn_stop,
            self.btn_explain,
            self.btn_clear,
        ):
            chat_row.addWidget(button)
        chat_row.addStretch(1)
        cv.addLayout(chat_row)
        split.addWidget(chat)
        split.setSizes([330, 330])

        self.lbl_backend = QLabel("")
        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: #5d4037;")
        status_row = QHBoxLayout()
        status_row.addWidget(self.lbl_backend)
        status_row.addWidget(self.lbl_status, 1)
        root.addLayout(status_row)

        self._welcome()
        self.set_busy(False)

    # -- public API -------------------------------------------------------
    def set_backend_status(self, text: str, available: bool) -> None:
        self.lbl_backend.setText(text)
        color = "#2e7d32" if available else "#b26a00"
        self.lbl_backend.setStyleSheet(f"color: {color}; font-weight: bold;")

    def set_context(self, text: str) -> None:
        self.lbl_context.setText(text)

    def set_status(self, text: str) -> None:
        self.lbl_status.setText(text)

    def set_undo_enabled(self, enabled: bool) -> None:
        self.btn_undo.setEnabled(bool(enabled))

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.btn_send.setEnabled(not self._busy)
        self.btn_stop.setEnabled(self._busy)
        self.btn_explain.setEnabled(not self._busy)
        self.btn_apply.setEnabled(
            not self._busy and self.fix_list.currentRow() >= 0
        )
        self.input.setReadOnly(self._busy)

    def clear_diagnosis(self) -> None:
        self._diagnosis = None
        self._fixes = []
        self.fix_list.blockSignals(True)
        self.fix_list.clear()
        self.fix_list.blockSignals(False)
        self.fix_diff.clear()
        self.btn_apply.setEnabled(False)
        self.diag_view.setHtml(
            "<p>Open <b>AI → Explain last run</b> after a simulation, or use "
            "<b>Explain last run</b> below. The deterministic diagnosis works "
            "without a cloud provider.</p>"
        )

    def set_diagnosis(self, diagnosis: Diagnosis) -> None:
        self._diagnosis = diagnosis
        self._fixes = diagnosis.all_fixes()
        self.fix_list.blockSignals(True)
        self.fix_list.clear()
        for fix in self._fixes:
            item = QListWidgetItem(fix.title)
            item.setData(Qt.ItemDataRole.UserRole, fix)
            item.setToolTip(fix.description)
            self.fix_list.addItem(item)
        self.fix_list.blockSignals(False)
        if self._fixes:
            self.fix_list.setCurrentRow(0)
        else:
            self.fix_diff.clear()
        self.btn_apply.setEnabled(bool(self._fixes) and not self._busy)

        severity_colors = {
            "error": "#b71c1c",
            "warning": "#8a4b00",
            "info": "#1565c0",
        }
        parts = [
            "<h3>Run diagnosis</h3>",
            f"<p><b>Verdict:</b> {html.escape(str(diagnosis.verdict))} — "
            f"{html.escape(str(diagnosis.headline))}</p>",
        ]
        if diagnosis.context_note:
            parts.append(
                f"<p style='color:#5d4037'>{html.escape(diagnosis.context_note)}</p>"
            )
        if diagnosis.checked:
            parts.append(
                "<p style='color:#607d8b'><b>Checked:</b> "
                + html.escape(", ".join(diagnosis.checked))
                + "</p>"
            )
        if diagnosis.issues:
            parts.append("<ul>")
            for issue in diagnosis.issues:
                color = severity_colors.get(issue.severity, "#455a64")
                parts.append(
                    "<li>"
                    f"<span style='color:{color}; font-weight:bold'>"
                    f"[{html.escape(issue.severity.upper())}] "
                    f"{html.escape(issue.title)}</span>"
                    f"<br>{html.escape(issue.detail)}"
                )
                if issue.evidence:
                    evidence = "<br>".join(
                        html.escape(str(item)) for item in issue.evidence[:8]
                    )
                    parts.append(
                        f"<br><span style='color:#607d8b'>Evidence:</span> "
                        f"<span style='font-family:Consolas'>{evidence}</span>"
                    )
                if issue.fixes:
                    parts.append(
                        "<br><i>Safe fix available in the list below.</i>"
                    )
                parts.append("</li>")
            parts.append("</ul>")
        else:
            parts.append(
                "<p>No known failure pattern was detected. Ask the AI in the "
                "conversation below for a deeper analysis.</p>"
            )
        if not self._fixes:
            parts.append(
                "<p style='color:#607d8b'>No automatic change is offered for "
                "this diagnosis. The assistant will suggest manual checks.</p>"
            )
        self.diag_view.setHtml("".join(parts))
        self._on_fix_selected(self.fix_list.currentRow())

    def selected_fix(self) -> FixProposal | None:
        item = self.fix_list.currentItem()
        if item is None:
            return None
        fix = item.data(Qt.ItemDataRole.UserRole)
        return fix if isinstance(fix, FixProposal) else None

    def prompt_text(self) -> str:
        return self.input.toPlainText().strip()

    def set_prompt(self, text: str) -> None:
        self.input.setPlainText(str(text or ""))

    def clear_input(self) -> None:
        self.input.clear()

    # -- conversation -----------------------------------------------------
    def _welcome(self) -> None:
        self.chat_view.setHtml(
            "<p><b>AI Assistant</b></p>"
            "<p>This assistant reads the project summary and the last run report. "
            "It can explain failures, rank likely causes and offer a small set of "
            "validated fixes. It never edits files or starts simulations by "
            "itself.</p>"
            "<p>Click <b>Explain last run</b> after a failed simulation, or ask a "
            "question below. <b>Settings</b> selects OpenCode CLI or offline-only "
            "mode.</p>"
        )

    def clear_chat(self) -> None:
        self._last_answer = ""
        self._welcome()

    def _clear_chat(self) -> None:
        self.clear_chat()
        self.clear_requested.emit()

    def append_user(self, text: str) -> None:
        body = html.escape(str(text or "")).replace("\n", "<br>")
        self.chat_view.append(f"<p><b>You</b><br>{body}</p>")
        self._scroll_chat()

    def begin_assistant(self) -> None:
        self.chat_view.append("<p><b>AI</b></p>")
        self._assistant_open = True
        self._last_answer = ""
        self._scroll_chat()

    def append_chunk(self, text: str) -> None:
        if not text:
            return
        prefix = "" if self._last_answer else "\n"
        if not self._assistant_open:
            self.begin_assistant()
            prefix = "\n"
        self._last_answer += str(text)
        cursor = self.chat_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.chat_view.setTextCursor(cursor)
        self.chat_view.insertPlainText(prefix + str(text))
        self._scroll_chat()

    def end_assistant(self, *, error: str = "") -> None:
        if error:
            self.chat_view.append(
                f"<p style='color:#b71c1c'><b>Error:</b> "
                f"{html.escape(str(error))}</p>"
            )
        self._assistant_open = False
        self._scroll_chat()

    def append_system(self, text: str) -> None:
        self.chat_view.append(
            f"<p style='color:#5d4037'><i>{html.escape(str(text))}</i></p>"
        )
        self._scroll_chat()

    def _scroll_chat(self) -> None:
        bar = self.chat_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    # -- internal ---------------------------------------------------------
    def _on_fix_selected(self, row: int) -> None:
        fix = self.selected_fix()
        if fix is None:
            self.fix_diff.clear()
            self.btn_apply.setEnabled(False)
            return
        self.fix_diff.setPlainText(fix.diff_text())
        self.btn_apply.setEnabled(not self._busy)

    def _apply_selected(self) -> None:
        fix = self.selected_fix()
        if fix is not None:
            self.apply_fix_requested.emit(fix)

    def _send(self) -> None:
        text = self.prompt_text()
        if not text or self._busy:
            return
        self.input.clear()
        self.send_requested.emit(text)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        if (
            obj is self.input
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._send()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self._send()
            event.accept()
            return
        super().keyPressEvent(event)

    def show_prompt_preview(self, prompt: str) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("What the AI sees")
        dlg.resize(820, 620)
        layout = QVBoxLayout(dlg)
        info = QLabel(
            "This is the exact prompt that will be sent to the configured "
            "provider. Obvious tokens and user-profile paths are redacted."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(str(prompt or ""))
        layout.addWidget(view, 1)
        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(
            lambda: QApplication.clipboard().setText(view.toPlainText())
        )
        row.addWidget(copy)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        row.addWidget(buttons)
        layout.addLayout(row)
        dlg.exec()
