"""Variable table/editor dialog for named geometry values."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.models.project import VariableDef
from app.models.variables import validate_variable_defs
from app.numfmt import parse_number


class VariableTableDialog(QDialog):
    """Name / value / comment table with an explicit row editor.

    The table is intentionally read-only; the fields below it edit the selected
    row so a name/value pair is never half-typed in a grid cell.  ``validate``
    is an optional callback ``(definitions, renames) -> list[str]`` supplied by
    MainWindow to check against all geometry expressions before closing.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        variables: Iterable[VariableDef] | None = None,
        usage: Mapping[str, int] | None = None,
        validate=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Geometry variables")
        self.resize(660, 470)
        self.setMinimumSize(540, 380)
        self._usage = {str(k).casefold(): int(v) for k, v in (usage or {}).items()}
        self._validate = validate
        self._row_originals: list[str] = []
        self._loading = False
        root = QVBoxLayout(self)

        intro = QLabel(
            "Named values in the project's drawing unit. They can be used in "
            "geometry fields instead of numbers, or in arithmetic such as "
            "<b>L/2</b> or <b>sqrt(W*W + L*L)</b>. Names are case-insensitive."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Name", "Value", "Comment"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        root.addWidget(self.table, 1)

        edit = QFormLayout()
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("e.g. L")
        self.ed_value = QLineEdit()
        self.ed_value.setPlaceholderText("number in the drawing unit")
        self.ed_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.ed_comment = QLineEdit()
        self.ed_comment.setPlaceholderText("optional note")
        edit.addRow("Name", self.ed_name)
        edit.addRow("Value", self.ed_value)
        edit.addRow("Comment", self.ed_comment)
        root.addLayout(edit)

        row = QHBoxLayout()
        self.btn_add = QPushButton("Add")
        self.btn_remove = QPushButton("Remove")
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_remove)
        row.addStretch(1)
        self.lbl_usage = QLabel("")
        self.lbl_usage.setWordWrap(True)
        row.addWidget(self.lbl_usage, 2)
        root.addLayout(row)

        self.hint = QLabel(
            "Tip: the geometry editor shows the expression again when you edit "
            "an object; changing a variable here updates every field that uses it."
        )
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(mid);")
        root.addWidget(self.hint)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        root.addWidget(bb)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)

        self._definitions: list[VariableDef] = []
        self.renames: dict[str, str] = {}
        for variable in variables or []:
            self._append_row(
                str(getattr(variable, "name", "") or ""),
                str(getattr(variable, "value", 0.0) or 0.0),
                str(getattr(variable, "comment", "") or ""),
                original=str(getattr(variable, "name", "") or ""),
            )
        if self.table.rowCount() == 0:
            self._append_row("", "", "")
        self.table.selectRow(0)
        self._load_row(0)

        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.ed_name.textChanged.connect(self._on_editor_changed)
        self.ed_value.textChanged.connect(self._on_editor_changed)
        self.ed_comment.textChanged.connect(self._on_editor_changed)
        self.btn_add.clicked.connect(self._add_row)
        self.btn_remove.clicked.connect(self._remove_row)

    # -- table <-> editor -------------------------------------------------
    def _append_row(
        self, name: str, value: str, comment: str, *, original: str = ""
    ) -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem(value))
        self.table.setItem(row, 2, QTableWidgetItem(comment))
        self._row_originals.append(str(original or ""))
        return row

    def _current_row(self) -> int:
        row = self.table.currentRow()
        if row < 0 and self.table.rowCount():
            row = 0
        return row

    def _on_selection_changed(self) -> None:
        if not self._loading:
            self._load_row(self._current_row())

    def _load_row(self, row: int) -> None:
        if row < 0 or row >= self.table.rowCount():
            self._loading = True
            try:
                self.ed_name.clear()
                self.ed_value.clear()
                self.ed_comment.clear()
            finally:
                self._loading = False
            self.lbl_usage.setText("")
            return
        self._loading = True
        try:
            self.ed_name.setText(self._cell_text(row, 0))
            self.ed_value.setText(self._cell_text(row, 1))
            self.ed_comment.setText(self._cell_text(row, 2))
        finally:
            self._loading = False
        self._update_usage(row)

    def _on_editor_changed(self, *_args) -> None:
        if self._loading:
            return
        row = self._current_row()
        if row < 0:
            return
        self.table.item(row, 0).setText(self.ed_name.text())
        self.table.item(row, 1).setText(self.ed_value.text())
        self.table.item(row, 2).setText(self.ed_comment.text())
        self._update_usage(row)

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    def _sync_editor(self) -> None:
        self._on_editor_changed()

    # -- add / remove -----------------------------------------------------
    def _add_row(self) -> None:
        self._sync_editor()
        n = self.table.rowCount() + 1
        row = self._append_row(f"var{n}", "0", "")
        self.table.selectRow(row)
        self.table.setCurrentCell(row, 0)
        self._load_row(row)
        self.ed_name.setFocus()
        self.ed_name.selectAll()

    def _remove_row(self) -> None:
        self._sync_editor()
        row = self._current_row()
        if row < 0:
            return
        self.table.removeRow(row)
        self._row_originals.pop(row)
        if self.table.rowCount() == 0:
            self._append_row("", "", "")
        next_row = min(row, self.table.rowCount() - 1)
        self.table.selectRow(next_row)
        self.table.setCurrentCell(next_row, 0)
        self._load_row(next_row)

    def _update_usage(self, row: int) -> None:
        if row < 0 or row >= self.table.rowCount():
            self.lbl_usage.setText("")
            return
        name = self._cell_text(row, 0).strip()
        if not name:
            self.lbl_usage.setText("")
            return
        count = self._usage.get(name.casefold(), 0)
        if count:
            self.lbl_usage.setText(
                f"Used by {count} geometry field{'s' if count != 1 else ''}."
            )
        else:
            self.lbl_usage.setText("Not used by any geometry field yet.")

    # -- validation / result ---------------------------------------------
    def _accept(self) -> None:
        self._sync_editor()
        definitions: list[VariableDef] = []
        value_errors: list[str] = []
        for row in range(self.table.rowCount()):
            name = self._cell_text(row, 0).strip()
            raw_value = self._cell_text(row, 1).strip()
            comment = self._cell_text(row, 2).strip()
            if not name and not raw_value and not comment:
                continue
            value = parse_number(raw_value)
            if value is None:
                value_errors.append(
                    f"Variable '{name or f'row {row + 1}'}': value must be a number."
                )
                value = 0.0
            definitions.append(
                VariableDef(
                    name=name,
                    value=float(value),
                    comment=comment,
                )
            )

        errors = value_errors + validate_variable_defs(definitions)
        if errors:
            self._show_errors(errors)
            return

        renames: dict[str, str] = {}
        for row, original in enumerate(self._row_originals):
            old = str(original or "").strip()
            new = self._cell_text(row, 0).strip()
            if old and new and old != new:
                renames[old] = new

        if callable(self._validate):
            try:
                extra = self._validate(definitions, renames) or []
            except Exception as e:  # noqa: BLE001 - validation must stay in-dialog
                extra = [f"Could not validate the geometry: {e}"]
            if extra:
                self._show_errors([str(e) for e in extra])
                return

        self._definitions = definitions
        self.renames = renames
        self.accept()

    def _show_errors(self, errors: list[str]) -> None:
        shown = errors[:12]
        text = "\n".join(f"• {e}" for e in shown)
        if len(errors) > len(shown):
            text += f"\n… and {len(errors) - len(shown)} more."
        QMessageBox.warning(self, "Variables", text)

    def variables(self) -> list[VariableDef]:
        """Definitions accepted by the dialog (only valid after ``exec()``)."""
        return list(self._definitions)
