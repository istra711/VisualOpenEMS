"""Settings for the AI error assistant (provider, model, context limits)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

from app.ai.backend import OpenCodeBackend
from app.settings import AppSettings
from app.ui.tab_help import show_tab_help


class AiSettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("AI assistant settings")
        self.resize(640, 720)

        root = QVBoxLayout(self)
        intro = QLabel(
            "The assistant analyzes the current project, the last run report and "
            "matching excerpts from local openEMS/CSXCAD sources and VisualopenEMS "
            "documentation. It only sends data when you click <b>Explain last run</b> "
            "or <b>Send</b>; nothing is transmitted in the background."
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

        self.chk_knowledge = QCheckBox(
            "Use local openEMS/CSXCAD knowledge (read-only)"
        )
        self.chk_knowledge.setChecked(bool(settings.ai_knowledge_enabled))
        self.chk_knowledge.setToolTip(
            "Index version-matched local openEMS/CSXCAD sources, VisualopenEMS "
            "docs/examples and easyMesh code. Retrieval runs entirely on this "
            "computer; only the matching excerpts are added to the prompt."
        )
        form.addRow(self.chk_knowledge)

        self.spn_knowledge = QSpinBox()
        self.spn_knowledge.setRange(1200, 24000)
        self.spn_knowledge.setSingleStep(1000)
        self.spn_knowledge.setSuffix(" chars")
        self.spn_knowledge.setValue(
            max(1200, min(24000, int(settings.ai_knowledge_max_chars or 8000)))
        )
        self.spn_knowledge.setToolTip(
            "Maximum characters of cited local excerpts per prompt. Smaller "
            "values save tokens; larger values allow more source context."
        )
        form.addRow("Knowledge budget", self.spn_knowledge)

        self.ed_knowledge_paths = QLineEdit(str(settings.ai_knowledge_paths or ""))
        self.ed_knowledge_paths.setPlaceholderText(
            "optional: D:\\openems-notes; C:\\my-docs (semicolon or newline)"
        )
        self.ed_knowledge_paths.setToolTip(
            "Extra folders or files to index in addition to the built-in "
            "openEMS, CSXCAD, VisualopenEMS and easyMesh locations. Separate "
            "multiple paths with ';' or one per line. Nothing is uploaded."
        )
        path_row = QHBoxLayout()
        path_row.addWidget(self.ed_knowledge_paths, 1)
        self.btn_add_folder = QPushButton("Add folder…")
        self.btn_add_folder.setToolTip("Choose an extra documentation folder")
        self.btn_add_folder.clicked.connect(self._add_knowledge_folder)
        path_row.addWidget(self.btn_add_folder)
        self.btn_clear_paths = QPushButton("Clear")
        self.btn_clear_paths.setToolTip("Remove all extra knowledge folders")
        self.btn_clear_paths.clicked.connect(self.ed_knowledge_paths.clear)
        path_row.addWidget(self.btn_clear_paths)
        form.addRow("Extra knowledge folders", path_row)
        root.addLayout(form)

        note = QLabel(
            "Privacy: the prompt contains the project summary, the last run "
            "report, filtered log lines, optionally the script, and the matching "
            "local knowledge excerpts. Retrieval happens locally; only the "
            "selected excerpts are sent with your prompt. Obvious API tokens and "
            "paths under your user profile are redacted. Use <b>What the AI sees…</b> "
            "in the panel to inspect the exact prompt, and <b>Sources…</b> to "
            "review the retrieved files and line ranges."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #5d4037;")
        root.addWidget(note)

        row = QHBoxLayout()
        self.btn_test = QPushButton("Test OpenCode")
        self.btn_test.setToolTip("Check whether the configured command is found")
        self.btn_test.clicked.connect(self._test)
        row.addWidget(self.btn_test)
        self.btn_test_knowledge = QPushButton("Check sources")
        self.btn_test_knowledge.setToolTip(
            "Build the local index with the current dialog values and show a "
            "sample retrieval; nothing is sent anywhere"
        )
        self.btn_test_knowledge.clicked.connect(self._test_knowledge)
        row.addWidget(self.btn_test_knowledge)
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

    def _add_knowledge_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Add knowledge folder", str(Path.home())
        )
        if not folder:
            return
        current = self.ed_knowledge_paths.text().strip()
        if folder in current:
            return
        separator = "; " if current else ""
        self.ed_knowledge_paths.setText(current + separator + folder)

    def _test_knowledge(self) -> None:
        if not self.chk_knowledge.isChecked():
            QMessageBox.information(
                self,
                "Local knowledge",
                "Local knowledge is disabled. Enable the option to index the "
                "built-in OpenEMS/CSXCAD sources and your extra folders.",
            )
            return
        from dataclasses import replace

        from app.ai.knowledge import build_knowledge_bundle

        probe = replace(
            self.settings,
            ai_knowledge_enabled=True,
            ai_knowledge_paths=self.ed_knowledge_paths.text().strip(),
            ai_knowledge_max_chars=int(self.spn_knowledge.value()),
        )
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        bundle = None
        error: Exception | None = None
        try:
            bundle = build_knowledge_bundle(
                "openEMS AddLumpedPort mesh PML end criteria S-parameter",
                probe,
                limit=5,
                force=True,
            )
        except Exception as exc:  # noqa: BLE001 - settings helper must not crash
            error = exc
        finally:
            QApplication.restoreOverrideCursor()
        if bundle is None:
            QMessageBox.warning(
                self,
                "Local knowledge",
                f"Could not build the index:\n{error}",
            )
            return
        lines = [bundle.summary, ""]
        if bundle.hits:
            lines.append("Sample excerpts:")
            for hit in bundle.hits:
                lines.append(
                    f"[{hit.id}] {hit.root}: {hit.title}\n"
                    f"    {hit.path}:{hit.line_start}-{hit.line_end}"
                )
        else:
            lines.append("Indexed roots:")
            lines.extend(f"  {path}" for path in bundle.roots[:20])
            if not bundle.roots:
                lines.append("  (none found)")
            if bundle.indexed_files:
                lines.append(
                    "\nThe index exists, but the probe question had no match. "
                    "Ask a more specific question in the AI panel."
                )
        QMessageBox.information(self, "Local knowledge", "\n".join(lines))

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
        self.settings.ai_knowledge_enabled = bool(self.chk_knowledge.isChecked())
        self.settings.ai_knowledge_paths = self.ed_knowledge_paths.text().strip()
        self.settings.ai_knowledge_max_chars = int(self.spn_knowledge.value())
        try:
            self.settings.save()
        except Exception as exc:  # noqa: BLE001 - settings save is best-effort
            QMessageBox.warning(self, "AI assistant", f"Could not save settings:\n{exc}")
            return
        self.accept()
