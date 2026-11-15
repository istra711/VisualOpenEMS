"""Phase-2 AI editor: structured project patch with diff and preflight."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ai.backend import OpenCodeBackend
from app.ai.generation import parse_patch_suggestion, project_patch_prompt
from app.ai.preflight import PreflightReport, preflight
from app.ai.project_patch import ProjectPatch, apply_project_patch
from app.ai.worker import AiWorker
from app.models.project import Project
from app.settings import AppSettings
from app.ui.ai_panel import show_prompt_dialog
from app.ui.tab_help import show_tab_help


class ProjectPatchDialog(QDialog):
    """Ask OpenCode for a structured edit, then show diff and preflight."""

    def __init__(
        self,
        project: Project,
        primitives: list[dict],
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.primitives = list(primitives or [])
        self.settings = settings
        self.patch: ProjectPatch | None = None
        self.result_project: Project | None = None
        self.result_primitives: list[dict] | None = None
        self.preflight_report: PreflightReport | None = None
        self._worker: AiWorker | None = None

        self.setWindowTitle("Edit project with AI")
        self.resize(860, 720)
        root = QVBoxLayout(self)
        intro = QLabel(
            "Describe a change. OpenCode returns a structured project patch "
            "(variables, ports, materials, mesh/solver fields or primitives). "
            "The patch is applied to a copy, preflighted and shown as a diff. "
            "Nothing changes until you press Apply."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.edt_instruction = QPlainTextEdit()
        self.edt_instruction.setPlaceholderText(
            "e.g. 'Move the design frequency to 2.45 GHz, raise max_timesteps "
            "to 300000 and mark port in1 as excited.'"
        )
        self.edt_instruction.setFixedHeight(74)
        self.edt_instruction.textChanged.connect(self._invalidate_proposal)
        root.addWidget(self.edt_instruction)

        row = QHBoxLayout()
        self.btn_ask = QPushButton("Ask AI for proposal")
        self.btn_ask.clicked.connect(self._on_ask)
        row.addWidget(self.btn_ask)
        self.btn_preview = QPushButton("What AI sees")
        self.btn_preview.setToolTip(
            "Preview the exact project-patch prompt before sending it"
        )
        self.btn_preview.clicked.connect(self._on_preview)
        row.addWidget(self.btn_preview)
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply)
        row.addWidget(self.btn_apply)
        self.btn_help = QPushButton("Help")
        self.btn_help.clicked.connect(lambda: show_tab_help(self, "AI Project Generation"))
        row.addWidget(self.btn_help)
        self.btn_ai_settings = QPushButton("AI settings")
        self.btn_ai_settings.clicked.connect(self._on_ai_settings)
        row.addWidget(self.btn_ai_settings)
        row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_cancel)
        root.addLayout(row)

        self.lbl_status = QLabel("")
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet("color: #5d4037;")
        root.addWidget(self.lbl_status)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText(
            "The proposed diff, assumptions and preflight result appear here."
        )
        root.addWidget(self.preview, 1)

    def _backend(self) -> OpenCodeBackend | None:
        if str(getattr(self.settings, "ai_provider", "opencode") or "offline") == "offline":
            return None
        return OpenCodeBackend(
            str(getattr(self.settings, "ai_opencode_cmd", "opencode") or "opencode")
        )

    def _invalidate_proposal(self, *_args) -> None:
        self.patch = None
        self.result_project = None
        self.result_primitives = None
        self.preflight_report = None
        self.btn_apply.setEnabled(False)
        if self.preview.toPlainText().strip():
            self.preview.clear()
            self.lbl_status.setText(
                "Instruction changed; ask AI for a new proposal before applying."
            )

    def _on_preview(self) -> None:
        instruction = self.edt_instruction.toPlainText().strip()
        if not instruction:
            QMessageBox.information(
                self, "Edit project", "Describe the desired change first."
            )
            return
        show_prompt_dialog(
            self,
            "What the AI sees - project edit",
            project_patch_prompt(self.project, self.primitives, instruction),
        )

    def _on_ai_settings(self) -> None:
        from app.ui.ai_settings_dialog import AiSettingsDialog

        dlg = AiSettingsDialog(self.settings, self)
        if dlg.exec():
            self.lbl_status.setText("AI settings saved.")

    def _on_ask(self) -> None:
        instruction = self.edt_instruction.toPlainText().strip()
        if not instruction:
            QMessageBox.information(
                self, "Edit project", "Describe the desired change first."
            )
            return
        backend = self._backend()
        if backend is None:
            QMessageBox.information(
                self,
                "Edit project",
                "Offline mode is active. AI project edits need OpenCode; the "
                "deterministic error assistant remains available.",
            )
            return
        if not backend.is_available():
            QMessageBox.warning(
                self,
                "Edit project",
                "OpenCode was not found. Set the executable in AI settings.",
            )
            return
        if self._worker is not None:
            QMessageBox.information(self, "Edit project", "An AI request is already running.")
            return
        self.patch = None
        self.result_project = None
        self.result_primitives = None
        self.preflight_report = None
        self.preview.clear()
        self.btn_ask.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.lbl_status.setText("Asking OpenCode for a structured project patch…")
        prompt = project_patch_prompt(self.project, self.primitives, instruction)
        worker = AiWorker(
            backend,
            prompt,
            title="VisualopenEMS project edit",
            model=str(getattr(self.settings, "ai_model", "") or ""),
            timeout_s=int(getattr(self.settings, "ai_timeout_s", 180) or 180),
            parent=self,
        )
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_done(self, reply) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.btn_ask.setEnabled(True)
        if getattr(reply, "error", ""):
            self.lbl_status.setText(f"AI request failed: {reply.error}")
            return
        patch, errors = parse_patch_suggestion(getattr(reply, "text", ""))
        if patch is None:
            self.preview.setPlainText(
                "The AI answer could not be parsed as a project patch.\n\n"
                + "\n".join(errors)
                + "\n\nRaw answer:\n"
                + str(getattr(reply, "text", ""))
            )
            self.lbl_status.setText("AI answer is not a valid structured patch.")
            return
        try:
            result_project, result_primitives, apply_errors = apply_project_patch(
                self.project, self.primitives, patch
            )
        except Exception as exc:  # noqa: BLE001 - never crash on a bad proposal
            self.patch = None
            self.preview.setPlainText(f"The proposal could not be applied:\n{exc}")
            self.lbl_status.setText("Proposal could not be applied; see the error above.")
            return
        if apply_errors:
            self.patch = None
            self.preview.setPlainText(
                "The proposal was rejected before preflight:\n\n"
                + "\n".join(f"- {item}" for item in apply_errors)
                + "\n\n"
                + patch.diff_text(self.project, self.primitives)
            )
            self.lbl_status.setText("Proposal rejected; see the reasons above.")
            return
        try:
            report = preflight(result_project, result_primitives)
        except Exception as exc:  # noqa: BLE001
            self.patch = None
            self.preview.setPlainText(f"Preflight failed:\n{exc}")
            self.lbl_status.setText("Preflight failed; the proposal is not applicable.")
            return
        self.patch = patch
        self.result_project = result_project
        self.result_primitives = result_primitives
        self.preflight_report = report
        self.preview.setPlainText(
            patch.diff_text(self.project, self.primitives)
            + "\n\n"
            + report.text()
        )
        if report.ok:
            self.btn_apply.setEnabled(True)
            self.lbl_status.setText(
                "Proposal is structurally valid. Review the diff and press Apply; "
                "the project will still need a mesh rebuild and a rerun."
            )
        else:
            self.btn_apply.setEnabled(False)
            self.lbl_status.setText(
                f"Preflight found {len(report.errors)} error(s); Apply is disabled."
            )

    def _on_failed(self, message: str) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.btn_ask.setEnabled(True)
        self.lbl_status.setText(f"AI request failed: {message}")

    def _apply(self) -> None:
        if (
            self.patch is None
            or self.result_project is None
            or self.result_primitives is None
            or self.preflight_report is None
            or not self.preflight_report.ok
        ):
            QMessageBox.warning(
                self, "Edit project", "There is no valid preflighted proposal to apply."
            )
            return
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._worker is not None:
            try:
                self._worker.cancel()
                self._worker.wait(2000)
            except Exception:  # noqa: BLE001
                pass
            self._worker = None
        super().closeEvent(event)
