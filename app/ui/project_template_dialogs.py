"""Dialogs for saving and managing reusable user project templates."""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.models.project import Project
from app.project_templates import (
    TemplateError,
    TemplateInfo,
    delete_project_template,
    list_project_templates,
    load_project_template,
    save_project_template,
    template_path,
    template_summary,
    templates_dir,
)
from app.settings import AppSettings


def _html(text: str) -> str:
    return html.escape(str(text or "")).replace("\n", "<br>")


def _project_facts(project: Project, primitives: list[dict]) -> str:
    mesh = project.mesh
    parts = [
        f"Project: {project.name or 'untitled'}",
        f"Ports: {len(project.ports)} · Primitives: {len(primitives)} · "
        f"Materials: {len(project.materials)} · Variables: {len(project.variables)}",
        f"Frequency: {mesh.start_frequency / 1e9:g}–{mesh.stop_frequency / 1e9:g} GHz"
        + (
            f" · target {mesh.target_frequency / 1e9:g} GHz"
            if mesh.target_frequency
            else ""
        ),
        f"Mesh preset: {mesh.preset or '—'}",
    ]
    if project.sweep is not None:
        parts.append("Sweep: configured")
    return "\n".join(parts)


class SaveProjectTemplateDialog(QDialog):
    """Ask for a template name/description and write the current project."""

    def __init__(
        self,
        project: Project,
        primitives: list[dict],
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.project = project
        self.primitives = list(primitives)
        self.settings = settings
        self.template_info: TemplateInfo | None = None
        self.setWindowTitle("Save project as template")
        self.resize(620, 500)

        root = QVBoxLayout(self)
        intro = QLabel(
            "Save the current project as a reusable template. The template "
            "stores geometry, materials, variables, ports, mesh/solver settings "
            "and notes. Simulation results, markers and the S-parameter "
            "reference are not included."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        default_name = str(project.name or "").strip()
        if not default_name or default_name.lower() == "untitled":
            default_name = "My project template"
        self.ed_name = QLineEdit(default_name)
        self.ed_name.setPlaceholderText("e.g. 2.4 GHz patch on FR4")
        self.ed_name.selectAll()
        form.addRow("Template name", self.ed_name)

        self.ed_description = QPlainTextEdit()
        self.ed_description.setPlaceholderText(
            "Optional: what is this template for, which stackup/ports does it "
            "use, what should be adapted first?"
        )
        self.ed_description.setFixedHeight(90)
        form.addRow("Description", self.ed_description)
        root.addLayout(form)

        self.lbl_facts = QLabel(_project_facts(project, self.primitives))
        self.lbl_facts.setWordWrap(True)
        self.lbl_facts.setStyleSheet(
            "background: #eef4f8; border: 1px solid #b8d4e6; padding: 6px;"
        )
        root.addWidget(self.lbl_facts)

        self.lbl_folder = QLabel("")
        self.lbl_folder.setWordWrap(True)
        self.lbl_folder.setStyleSheet("color: #5d4037;")
        root.addWidget(self.lbl_folder)
        self._update_folder_label()

        root.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _update_folder_label(self) -> None:
        self.lbl_folder.setText(f"Template folder: {templates_dir(self.settings)}")

    def _save(self) -> None:
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.warning(
                self, "Save template", "Please enter a template name."
            )
            return
        description = self.ed_description.toPlainText().strip()
        folder = templates_dir(self.settings, create=True)
        path = template_path(folder, name)
        overwrite = False
        if path.exists():
            reply = QMessageBox.question(
                self,
                "Save template",
                f"A template named '{name}' already exists:\n{path}\n\n"
                "Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            overwrite = True
        try:
            self.template_info = save_project_template(
                folder,
                self.project,
                self.primitives,
                name,
                description,
                overwrite=overwrite,
            )
        except FileExistsError:
            QMessageBox.warning(
                self, "Save template", "The template already exists. Try again."
            )
            return
        except (ValueError, TemplateError, OSError) as exc:
            QMessageBox.warning(
                self, "Save template", f"Could not save the template:\n{exc}"
            )
            return
        except Exception as exc:  # noqa: BLE001 - never lose the dialog on IO
            QMessageBox.warning(
                self, "Save template", f"Could not save the template:\n{exc}"
            )
            return
        self.accept()


class ProjectTemplateDialog(QDialog):
    """Browse, preview, create from and delete user project templates."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.selected_info: TemplateInfo | None = None
        self.setWindowTitle("Project templates")
        self.resize(920, 620)

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        title = QLabel("Project templates")
        title.setStyleSheet("font-weight: bold; font-size: 13pt;")
        titles.addWidget(title)
        self.lbl_folder = QLabel("")
        self.lbl_folder.setWordWrap(True)
        self.lbl_folder.setStyleSheet("color: #5d4037;")
        titles.addWidget(self.lbl_folder)
        header.addLayout(titles, 1)
        self.btn_change_folder = QPushButton("Change folder…")
        self.btn_change_folder.setToolTip(
            "Choose where VisualopenEMS stores and searches user templates"
        )
        self.btn_change_folder.clicked.connect(self._change_folder)
        header.addWidget(self.btn_change_folder)
        self.btn_open_folder = QPushButton("Open folder")
        self.btn_open_folder.setToolTip("Open the template folder in Explorer/Finder")
        self.btn_open_folder.clicked.connect(self._open_folder)
        header.addWidget(self.btn_open_folder)
        root.addLayout(header)

        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Saved templates"))
        self.list = QListWidget()
        self.list.setToolTip(
            "Templates are normal .fcsim.json project files in the template "
            "folder. Double-click or press New project to use one."
        )
        self.list.currentItemChanged.connect(self._on_selected)
        self.list.itemDoubleClicked.connect(lambda *_: self._new_project())
        lv.addWidget(self.list, 1)
        split.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("Preview"))
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        self.preview.setHtml(
            "<p>Select a template to see its description and project setup.</p>"
        )
        rv.addWidget(self.preview, 1)
        split.addWidget(right)
        split.setSizes([300, 620])

        row = QHBoxLayout()
        self.btn_new = QPushButton("New project")
        self.btn_new.setToolTip(
            "Create a new unsaved project from the selected template"
        )
        self.btn_new.setDefault(True)
        self.btn_new.setEnabled(False)
        self.btn_new.clicked.connect(self._new_project)
        row.addWidget(self.btn_new)
        self.btn_delete = QPushButton("Delete…")
        self.btn_delete.setToolTip("Delete the selected template file")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._delete)
        row.addWidget(self.btn_delete)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        row.addWidget(buttons)
        root.addLayout(row)

        self._reload()

    # -- folder ----------------------------------------------------------
    def _folder(self) -> Path:
        return templates_dir(self.settings)

    def _update_folder_label(self) -> None:
        self.lbl_folder.setText(f"Template folder: {self._folder()}")

    def _reload(self) -> None:
        self._update_folder_label()
        current = self.selected_info.path if self.selected_info else None
        infos = list_project_templates(self._folder())
        self.list.blockSignals(True)
        self.list.clear()
        selected_row = -1
        for row, info in enumerate(infos):
            suffix = "  ⚠" if info.error else ""
            item = QListWidgetItem(info.name + suffix)
            item.setData(Qt.ItemDataRole.UserRole, info)
            item.setToolTip(info.error or info.description or str(info.path))
            if info.error:
                item.setForeground(QColor("#b71c1c"))
            self.list.addItem(item)
            if current is not None and info.path == current:
                selected_row = row
        self.list.blockSignals(False)
        if infos:
            self.list.setCurrentRow(selected_row if selected_row >= 0 else 0)
        else:
            self.selected_info = None
            self.btn_new.setEnabled(False)
            self.btn_delete.setEnabled(False)
            self.preview.setHtml(
                "<h3>No templates yet</h3>"
                "<p>Use <b>Presets → Save current project as template…</b> to "
                "store the current geometry, ports, materials and settings. "
                "The template folder can also contain any normal "
                "<code>.fcsim.json</code> project file.</p>"
            )
        self._update_folder_label()

    def _change_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose template folder", str(self._folder())
        )
        if not chosen:
            return
        self.settings.template_dir = chosen
        try:
            self.settings.save()
        except Exception as exc:  # noqa: BLE001 - keep the chosen folder usable
            QMessageBox.warning(
                self,
                "Project templates",
                f"The folder is active for this session, but the setting could "
                f"not be saved:\n{exc}",
            )
        self.selected_info = None
        self._reload()

    def _open_folder(self) -> None:
        folder = self._folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self, "Project templates", f"Could not create the folder:\n{exc}"
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            QMessageBox.information(
                self,
                "Project templates",
                f"Could not open the file browser.\n\nTemplate folder:\n{folder}",
            )

    # -- selection -------------------------------------------------------
    def _current_info(self) -> TemplateInfo | None:
        item = self.list.currentItem()
        if item is None:
            return None
        info = item.data(Qt.ItemDataRole.UserRole)
        return info if isinstance(info, TemplateInfo) else None

    def _on_selected(self, *_args) -> None:
        info = self._current_info()
        self.selected_info = info
        self.btn_new.setEnabled(bool(info and info.valid))
        self.btn_delete.setEnabled(info is not None)
        if info is None:
            self.preview.setHtml("<p>Select a template.</p>")
            return
        self.preview.setHtml(self._preview_html(info))

    def _preview_html(self, info: TemplateInfo) -> str:
        if info.error:
            return (
                f"<h2>{_html(info.name)}</h2>"
                f"<p style='color:#b71c1c'><b>Cannot use this template:</b><br>"
                f"{_html(info.error)}</p>"
                f"<p style='color:#607d8b'>{_html(info.path)}</p>"
            )
        facts = [
            ("Project name", info.project_name or "—"),
            ("Created", info.created or "—"),
            ("Saved with", f"VisualopenEMS {info.app_version}" if info.app_version else "—"),
            ("Contents", template_summary(info)),
            ("Sweep", "configured" if info.has_sweep else "none"),
        ]
        if info.has_results:
            facts.append(
                (
                    "Results",
                    "the file contains results, but templates load without them",
                )
            )
        rows = "".join(
            f"<tr><td style='color:#607d8b; padding-right:12px'>{_html(label)}"
            f"</td><td>{_html(value)}</td></tr>"
            for label, value in facts
        )
        description = (
            f"<p>{_html(info.description)}</p>"
            if info.description
            else "<p style='color:#607d8b'><i>No description.</i></p>"
        )
        return (
            f"<h2>{_html(info.name)}</h2>"
            f"{description}"
            f"<table>{rows}</table>"
            f"<p style='color:#607d8b'>File: {_html(info.path)}</p>"
            "<p style='color:#5d4037'>Creating a project from this template "
            "gives it a fresh project id and drops any stored simulation "
            "results, markers and S-parameter reference.</p>"
        )

    # -- actions ---------------------------------------------------------
    def _new_project(self) -> None:
        info = self._current_info()
        if info is None or not info.valid:
            return
        self.selected_info = info
        self.accept()

    def _delete(self) -> None:
        info = self._current_info()
        if info is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete template",
            f"Delete the template '{info.name}'?\n\n{info.path}\n\n"
            "This only removes the template file, not any project created from it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            delete_project_template(info.path)
        except OSError as exc:
            QMessageBox.warning(
                self, "Delete template", f"Could not delete the template:\n{exc}"
            )
            return
        self.selected_info = None
        self._reload()

    def selected_template(self) -> TemplateInfo | None:
        return self.selected_info

    def load_selected(self):
        """Convenience for callers/tests: load the selected template."""
        info = self.selected_info
        if info is None or not info.valid:
            return None
        return load_project_template(info.path)


__all__ = ["ProjectTemplateDialog", "SaveProjectTemplateDialog"]
