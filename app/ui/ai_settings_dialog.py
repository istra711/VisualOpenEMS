"""Settings for the AI error assistant (provider, model, context limits)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

from app.ai.backend import OpenCodeBackend
from app.settings import AppSettings
from app.ui.tab_help import show_tab_help


class AiSettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AI assistant settings")
        self.resize(560, 430)

        root = QVBoxLayout(self)
        intro = QLabel(
            "The assistant analyzes the current project and the last run report. "
            "It only sends data when you click <b>Explain last run</b> or "
            "<b>Send</b>; nothing is transmitted in the background."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        self.cmb_provider = QComboBox()
        self.cmb_provider.addItem("Offline only (deterministic diagnosis)", "offline")
        self.cmb_provider.addItem("OpenCode CLI (existing subscription)", "opencode")
        index = self.cmb_provider.findData(str(settings.ai_provider or "offline"))
        self.cmb_provider.setCurrentIndex(index if index >= 0 else 0)
        self.cmb_provider.setToolTip(
            "Offline mode still shows all deterministic findings and safe fixes, "
            "but does not ask a language model.\n"
            "OpenCode uses the locally installed CLI and its existing login."
        )
        form.addRow("Provider", self.cmb_provider)

        self.ed_cmd = QLineEdit(str(settings.ai_opencode_cmd or "opencode"))
        self.ed_cmd.setPlaceholderText("opencode or full path to opencode.exe")
        self.ed_cmd.setToolTip(
            "Command used for 'opencode run'. It must be on PATH or given as a "
            "full executable path."
        )
        form.addRow("OpenCode command", self.ed_cmd)

        self.ed_model = QLineEdit(str(settings.ai_model or ""))
        self.ed_model.setPlaceholderText(
            "empty = OpenCode default, e.g. opencode-go/deepseek-v4.1-flash"
        )
        self.ed_model.setToolTip(
            "Optional provider/model ID. Leave empty to use the OpenCode default."
        )
        form.addRow("Model (optional)", self.ed_model)

        self.spn_timeout = QSpinBox()
        self.spn_timeout.setRange(30, 1800)
        self.spn_timeout.setSuffix(" s")
        self.spn_timeout.setValue(max(30, int(settings.ai_timeout_s or 180)))
        self.spn_timeout.setToolTip(
            "The CLI process is stopped when no answer arrives within this time."
        )
        form.addRow("Timeout", self.spn_timeout)

        self.cmb_language = QComboBox()
        self.cmb_language.addItem("Same as the question", "auto")
        self.cmb_language.addItem("Deutsch", "de")
        self.cmb_language.addItem("English", "en")
        index = self.cmb_language.findData(
            str(settings.ai_answer_language or "auto")
        )
        self.cmb_language.setCurrentIndex(index if index >= 0 else 0)
        form.addRow("Answer language", self.cmb_language)

        self.spn_log = QSpinBox()
        self.spn_log.setRange(40, 500)
        self.spn_log.setValue(max(40, int(settings.ai_max_log_lines or 160)))
        self.spn_log.setToolTip(
            "Maximum filtered simulation-log lines sent as context. "
            "Lower values reduce token usage and privacy exposure."
        )
        form.addRow("Log lines in context", self.spn_log)

        self.chk_script = QCheckBox("Include an excerpt of the generated run script")
        self.chk_script.setChecked(bool(settings.ai_include_script))
        self.chk_script.setToolTip(
            "The script shows solver/port settings and is often useful for error "
            "analysis. Disable it to send less code."
        )
        form.addRow(self.chk_script)
        root.addLayout(form)

        note = QLabel(
            "Privacy: the prompt contains the project summary, the last run "
            "report, filtered log lines and optionally the script. Obvious API "
            "tokens and paths under your user profile are redacted. Use "
            "<b>What the AI sees…</b> in the panel to inspect the exact prompt."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #5d4037;")
        root.addWidget(note)

        row = QHBoxLayout()
        self.btn_test = QPushButton("Test OpenCode")
        self.btn_test.setToolTip("Check whether the configured command is found")
        self.btn_test.clicked.connect(self._test)
        row.addWidget(self.btn_test)
        self.btn_help = QPushButton("Help")
        self.btn_help.clicked.connect(lambda: show_tab_help(self, "AI Assistant"))
        row.addWidget(self.btn_help)
        row.addStretch(1)
        root.addLayout(row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _test(self) -> None:
        provider = str(self.cmb_provider.currentData() or "offline")
        if provider == "offline":
            QMessageBox.information(
                self,
                "AI assistant",
                "Offline mode is always available: it shows the deterministic "
                "diagnosis and safe fixes without sending data.",
            )
            return
        backend = OpenCodeBackend(self.ed_cmd.text())
        if backend.is_available():
            QMessageBox.information(
                self,
                "AI assistant",
                f"OpenCode command found:\n{backend.resolved_command()}",
            )
        else:
            QMessageBox.warning(
                self,
                "AI assistant",
                f"Command not found:\n{self.ed_cmd.text()}\n\n"
                "Install OpenCode or enter the full executable path.",
            )

    def _save(self) -> None:
        self.settings.ai_provider = str(
            self.cmb_provider.currentData() or "offline"
        )
        self.settings.ai_opencode_cmd = self.ed_cmd.text().strip() or "opencode"
        self.settings.ai_model = self.ed_model.text().strip()
        self.settings.ai_timeout_s = int(self.spn_timeout.value())
        self.settings.ai_answer_language = str(
            self.cmb_language.currentData() or "auto"
        )
        self.settings.ai_max_log_lines = int(self.spn_log.value())
        self.settings.ai_include_script = bool(self.chk_script.isChecked())
        try:
            self.settings.save()
        except Exception as exc:  # noqa: BLE001 - settings save is best-effort
            QMessageBox.warning(self, "AI assistant", f"Could not save settings:\n{exc}")
            return
        self.accept()
