"""Phase-2 project generator: template form, AI suggestion, preview, create."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from app.ai.backend import OpenCodeBackend
from app.ai.generation import (
    parse_template_suggestion,
    template_suggestion_prompt,
)
from app.ai.preflight import PreflightReport, preflight
from app.ai.templates import GeneratedProject, ProjectTemplate, templates
from app.ai.worker import AiWorker
from app.settings import AppSettings
from app.ui.ai_panel import show_prompt_dialog
from app.ui.tab_help import show_tab_help


class ProjectGeneratorDialog(QDialog):
    """Create a new project from a known template with a preflight review."""

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self._templates: list[ProjectTemplate] = templates()
        self._template_by_id = {item.id: item for item in self._templates}
        self._param_widgets: dict[str, QWidget] = {}
        self._worker: AiWorker | None = None
        self._ai_assumptions: list[str] = []
        self.result: GeneratedProject | None = None
        self.preflight_report: PreflightReport | None = None

        self.setWindowTitle("Generate project")
        self.resize(820, 760)
        root = QVBoxLayout(self)

        intro = QLabel(
            "Build a new project from a validated template, optionally ask "
            "OpenCode to fill the parameters from a description, then run the "
            "structural preflight. The current project is never modified until "
            "you confirm creation."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        ai_row = QHBoxLayout()
        self.edt_description = QPlainTextEdit()
        self.edt_description.setPlaceholderText(
            "Describe the design, e.g. '2.45 GHz probe-fed patch on FR4, "
            "1.6 mm, 50 ohm, S11 below -10 dB around 2.4-2.5 GHz'."
        )
        self.edt_description.setFixedHeight(62)
        ai_row.addWidget(self.edt_description, 1)
        ai_col = QVBoxLayout()
        self.btn_ai = QPushButton("AI suggest")
        self.btn_ai.setToolTip(
            "Ask OpenCode to choose a template and starting parameters from the "
            "description. The result still goes through the normal form and "
            "preflight."
        )
        self.btn_ai.clicked.connect(self._on_ai_suggest)
        ai_col.addWidget(self.btn_ai)
        self.btn_ai_preview = QPushButton("What AI sees")
        self.btn_ai_preview.setToolTip(
            "Preview the exact template-suggestion prompt before sending it"
        )
        self.btn_ai_preview.clicked.connect(self._on_ai_preview)
        ai_col.addWidget(self.btn_ai_preview)
        self.btn_ai_settings = QPushButton("AI settings")
        self.btn_ai_settings.clicked.connect(self._on_ai_settings)
        ai_col.addWidget(self.btn_ai_settings)
        ai_row.addLayout(ai_col)
        root.addLayout(ai_row)

        template_row = QHBoxLayout()
        template_row.addWidget(QLabel("Template"))
        self.cmb_template = QComboBox()
        for item in self._templates:
            self.cmb_template.addItem(item.title, item.id)
        self.cmb_template.currentIndexChanged.connect(self._on_template_changed)
        template_row.addWidget(self.cmb_template, 1)
        root.addLayout(template_row)

        self.lbl_template_help = QLabel("")
        self.lbl_template_help.setWordWrap(True)
        self.lbl_template_help.setStyleSheet("color: #455a64;")
        root.addWidget(self.lbl_template_help)
        units_note = QLabel(
            "Units are shown as suffixes: GHz for frequency, mm for geometry "
            "unless the template says otherwise. Defaults are starting values; "
            "verify them with the preflight and a real simulation."
        )
        units_note.setWordWrap(True)
        units_note.setStyleSheet("color: #607d8b;")
        root.addWidget(units_note)

        self.grp_params = QGroupBox("Template parameters")
        self.form = QFormLayout(self.grp_params)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.grp_params)
        preview_box = QGroupBox("Review")
        pv = QVBoxLayout(preview_box)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(
            "Press Preview to build the candidate project and run the "
            "structural preflight."
        )
        pv.addWidget(self.preview)
        split.addWidget(preview_box)
        split.setSizes([300, 310])
        root.addWidget(split, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: #5d4037;")
        root.addWidget(self.lbl_status)

        row = QHBoxLayout()
        self.btn_preview = QPushButton("Preview / preflight")
        self.btn_preview.clicked.connect(self._build_preview)
        row.addWidget(self.btn_preview)
        self.btn_create = QPushButton("Create project")
        self.btn_create.setToolTip(
            "Replace the current editor content with this new project. The "
            "project is not saved automatically; use File → Save As."
        )
        self.btn_create.clicked.connect(self._create)
        row.addWidget(self.btn_create)
        self.btn_help = QPushButton("Help")
        self.btn_help.clicked.connect(self._on_help)
        row.addWidget(self.btn_help)
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        root.addLayout(row)

        self._on_template_changed()

    # ------------------------------------------------------------------ UI
    def _current_template(self) -> ProjectTemplate:
        template_id = str(self.cmb_template.currentData() or "")
        return self._template_by_id.get(template_id) or self._templates[0]

    def _clear_form(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._param_widgets.clear()

    def _on_template_changed(self, *_args) -> None:
        template = self._current_template()
        self.lbl_template_help.setText(template.description)
        self._clear_form()
        for param in template.parameters:
            if param.kind == "float":
                widget = QDoubleSpinBox()
                decimals = max(
                    int(param.decimals),
                    6 if param.key == "f0" else 0,
                )
                widget.setDecimals(decimals)
                widget.setRange(
                    float(param.minimum if param.minimum is not None else -1e9),
                    float(param.maximum if param.maximum is not None else 1e9),
                )
                widget.setSingleStep(float(param.step))
                widget.setValue(float(param.default))
                if param.unit:
                    widget.setSuffix(f" {param.unit}")
            elif param.kind == "int":
                widget = QSpinBox()
                widget.setRange(
                    int(param.minimum if param.minimum is not None else -1000000),
                    int(param.maximum if param.maximum is not None else 1000000),
                )
                widget.setValue(int(param.default))
                if param.unit:
                    widget.setSuffix(f" {param.unit}")
            elif param.kind == "choice":
                widget = QComboBox()
                widget.addItems(list(param.choices))
                widget.setCurrentText(str(param.default))
            else:
                widget = QLineEdit(str(param.default))
            widget.setToolTip(param.help or param.label)
            self._param_widgets[param.key] = widget
            label = param.label
            self.form.addRow(label, widget)
            if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                widget.valueChanged.connect(self._invalidate_preview)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._invalidate_preview)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._invalidate_preview)
        self.result = None
        self.preflight_report = None
        self._ai_assumptions = []
        self.preview.clear()
        self.lbl_status.setText(
            "Choose a template or use AI suggest; then press Preview."
        )
        self.btn_create.setEnabled(False)

    def _invalidate_preview(self, *_args) -> None:
        self.result = None
        self.preflight_report = None
        self.btn_create.setEnabled(False)
        self.preview.clear()
        self.lbl_status.setText("Parameters changed; press Preview / preflight again.")

    def _values(self) -> dict:
        values: dict = {}
        for key, widget in self._param_widgets.items():
            if isinstance(widget, QDoubleSpinBox):
                values[key] = float(widget.value())
            elif isinstance(widget, QSpinBox):
                values[key] = int(widget.value())
            elif isinstance(widget, QComboBox):
                values[key] = widget.currentText()
            elif isinstance(widget, QLineEdit):
                values[key] = widget.text()
        return values

    def _apply_values(self, values: dict) -> None:
        for key, widget in self._param_widgets.items():
            if key not in values:
                continue
            value = values[key]
            if isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QComboBox):
                index = widget.findText(str(value))
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value))

    # ------------------------------------------------------------- preview
    def _build_preview(self) -> bool:
        template = self._current_template()
        try:
            generated = template.build(self._values())
            report = preflight(generated.project, generated.primitives)
        except Exception as exc:  # noqa: BLE001 - show template errors in the UI
            self.result = None
            self.preflight_report = None
            self.preview.setPlainText(f"Template build failed:\n{exc}")
            self.lbl_status.setText("Template build failed; see Review.")
            self.btn_create.setEnabled(False)
            return False
        self.result = generated
        self.preflight_report = report
        text = generated.summary()
        if self._ai_assumptions:
            text += "\n\nAI assumptions:\n" + "\n".join(
                f"  - {item}" for item in self._ai_assumptions
            )
        self.preview.setPlainText(text + "\n\n" + report.text())
        if report.ok:
            self.lbl_status.setText(
                "Preflight passed"
                + (
                    f" with {len(report.warnings)} warning(s)."
                    if report.warnings
                    else " without warnings."
                )
                + " Review the assumptions, then create the project."
            )
            self.btn_create.setEnabled(True)
        else:
            self.lbl_status.setText(
                f"Preflight found {len(report.errors)} error(s). Fix the "
                "parameters before creating the project."
            )
            self.btn_create.setEnabled(False)
        return report.ok

    def _create(self) -> None:
        if self.result is None or self.preflight_report is None:
            if not self._build_preview():
                return
        if self.result is None or self.preflight_report is None:
            return
        if not self.preflight_report.ok:
            QMessageBox.warning(
                self,
                "Generate project",
                "Fix the preflight errors before creating the project.",
            )
            return
        self.accept()

    # --------------------------------------------------------------- AI
    def _backend(self) -> OpenCodeBackend | None:
        if str(getattr(self.settings, "ai_provider", "opencode") or "offline") == "offline":
            return None
        return OpenCodeBackend(
            str(getattr(self.settings, "ai_opencode_cmd", "opencode") or "opencode")
        )

    def _on_ai_preview(self) -> None:
        description = self.edt_description.toPlainText().strip()
        if not description:
            QMessageBox.information(
                self, "AI suggest", "Describe the desired design first."
            )
            return
        show_prompt_dialog(
            self,
            "What the AI sees - template suggestion",
            template_suggestion_prompt(description),
        )

    def _on_ai_suggest(self) -> None:
        description = self.edt_description.toPlainText().strip()
        if not description:
            QMessageBox.information(
                self, "AI suggest", "Describe the desired design first."
            )
            return
        backend = self._backend()
        if backend is None:
            QMessageBox.information(
                self,
                "AI suggest",
                "Offline mode is active. Use the template form manually, or "
                "enable OpenCode in AI settings.",
            )
            return
        if not backend.is_available():
            QMessageBox.warning(
                self,
                "AI suggest",
                "OpenCode was not found. Set the executable in AI settings or "
                "use the template form manually.",
            )
            return
        if self._worker is not None:
            QMessageBox.information(self, "AI suggest", "An AI request is already running.")
            return
        self.result = None
        self.preflight_report = None
        self.btn_create.setEnabled(False)
        self.preview.clear()
        self.btn_ai.setEnabled(False)
        self.lbl_status.setText("Asking OpenCode for a template and parameters…")
        prompt = template_suggestion_prompt(description)
        worker = AiWorker(
            backend,
            prompt,
            title="VisualopenEMS project suggestion",
            model=str(getattr(self.settings, "ai_model", "") or ""),
            timeout_s=int(getattr(self.settings, "ai_timeout_s", 180) or 180),
            parent=self,
        )
        worker.done.connect(self._on_ai_done)
        worker.failed.connect(self._on_ai_failed)
        self._worker = worker
        worker.start()

    def _on_ai_done(self, reply) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.btn_ai.setEnabled(True)
        if getattr(reply, "error", ""):
            self.lbl_status.setText(f"AI suggestion failed: {reply.error}")
            return
        suggestion, errors = parse_template_suggestion(getattr(reply, "text", ""))
        if suggestion is None:
            self.preview.setPlainText(
                "The AI answer could not be used as a template proposal.\n\n"
                + "\n".join(errors)
                + "\n\nRaw answer:\n"
                + str(getattr(reply, "text", ""))
            )
            self.lbl_status.setText("AI answer was not a valid template proposal.")
            return
        index = self.cmb_template.findData(suggestion.template.id)
        if index >= 0:
            self.cmb_template.setCurrentIndex(index)
        self._on_template_changed()
        self._ai_assumptions = list(suggestion.assumptions)
        self._apply_values(suggestion.parameters)
        self.lbl_status.setText(
            "AI selected template "
            f"'{suggestion.template.title}'. Review the parameters and press "
            "Preview / preflight."
        )
        self._build_preview()

    def _on_ai_failed(self, message: str) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.btn_ai.setEnabled(True)
        self.lbl_status.setText(f"AI suggestion failed: {message}")

    def _on_ai_settings(self) -> None:
        from app.ui.ai_settings_dialog import AiSettingsDialog

        dlg = AiSettingsDialog(self.settings, self)
        if dlg.exec():
            self.lbl_status.setText("AI settings saved.")

    def _on_help(self) -> None:
        show_tab_help(self, "AI Project Generation")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._worker is not None:
            try:
                self._worker.cancel()
                self._worker.wait(2000)
            except Exception:  # noqa: BLE001
                pass
            self._worker = None
        super().closeEvent(event)
