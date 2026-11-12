"""A line edit for geometry numbers or named-variable expressions."""

from __future__ import annotations

import math
import re
from typing import Iterable, Mapping

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtWidgets import QCompleter, QLineEdit, QWidget

from app.models.variables import (
    VariableError,
    evaluate_expression,
    expression_entry,
    expression_text,
)
from app.numfmt import format_number, parse_number

_TOKEN_AT_END_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_AUTOCOMPLETE_ENABLED = True


def set_variable_autocomplete_enabled(enabled: bool) -> None:
    """Enable/disable variable-name popups for newly created fields."""
    global _AUTOCOMPLETE_ENABLED
    _AUTOCOMPLETE_ENABLED = bool(enabled)


def variable_autocomplete_enabled() -> bool:
    return bool(_AUTOCOMPLETE_ENABLED)


class VariableField(QLineEdit):
    """Numeric geometry field that also accepts ``L``, ``W/2``, ``sqrt(...)``.

    ``value()`` always returns the project-unit value.  ``setValue()`` keeps the
    old ``QDoubleSpinBox`` call pattern used throughout the dialogs and tests;
    ``setExpression()`` shows a saved expression instead of its last value.

    Completion is intentionally optional (Settings -> Geometry variables) and
    replaces only the identifier under the cursor, so ``L/2`` still suggests
    ``L`` after the operator.
    """

    valueChanged = Signal(float)

    def __init__(
        self,
        value: float = 0.0,
        *,
        variables: Mapping[str, float] | None = None,
        scale: float = 1.0,
        label: str = "",
        minimum: float | None = None,
        maximum: float | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = str(label or "")
        try:
            self._scale = float(scale)
        except (TypeError, ValueError):
            self._scale = 1.0
        if not math.isfinite(self._scale) or abs(self._scale) <= 1e-300:
            self._scale = 1.0
        self._minimum = minimum
        self._maximum = maximum
        self._last_value = float(value)
        self._variables: dict[str, float] = {}
        self._loading = True
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.setMinimumWidth(92)
        self.setClearButtonEnabled(False)
        self.setToolTip(self._tooltip())

        self._completion_prefix = ""
        self._completion_model = QStringListModel([], self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setWidget(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setFilterMode(Qt.MatchFlag.MatchStartsWith)
        self._completer.activated[str].connect(self._insert_completion)

        self.textEdited.connect(self._on_text_edited)
        self.editingFinished.connect(self._on_editing_finished)
        self.set_variables(variables or {})
        self.setValue(value)
        self._loading = False

    # -- compatibility with the old QDoubleSpinBox fields -----------------
    def value(self) -> float:
        """Evaluated project-unit value; raises ``VariableError`` if invalid."""
        text = self.text().strip()
        try:
            display_value = evaluate_expression(text, self._variables, context=self._label)
        except VariableError:
            self._set_invalid(True)
            raise
        value = display_value * self._scale
        if not math.isfinite(value):
            self._set_invalid(True)
            raise VariableError(
                f"{self._label + ': ' if self._label else ''}the result is not finite"
            )
        if self._minimum is not None and display_value < float(self._minimum) - 1e-12:
            self._set_invalid(True)
            raise VariableError(
                f"{self._label + ': ' if self._label else ''}"
                f"value must be at least {format_number(float(self._minimum))}"
            )
        if self._maximum is not None and display_value > float(self._maximum) + 1e-12:
            self._set_invalid(True)
            raise VariableError(
                f"{self._label + ': ' if self._label else ''}"
                f"value must be at most {format_number(float(self._maximum))}"
            )
        self._set_invalid(False)
        self._last_value = float(value)
        return float(value)

    def setValue(self, value: float) -> None:  # noqa: N802 - QDoubleSpinBox API
        """Replace the expression with a plain numeric value (project units)."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        self._last_value = value
        display = value / self._scale
        self._loading = True
        try:
            self.setText(format_number(display))
        finally:
            self._loading = False
        self._set_invalid(False)
        self._hide_completion()
        self.valueChanged.emit(float(value))

    def setExpression(self, text: str) -> None:  # noqa: N802 - dialog helper
        """Show a saved expression; keep its last numeric value for previews."""
        self._loading = True
        try:
            self.setText(expression_text(text))
        finally:
            self._loading = False
        try:
            self._last_value = self.value()
        except VariableError:
            pass
        self._hide_completion()

    # -- helpers used by the geometry dialogs -----------------------------
    @property
    def last_value(self) -> float:
        """Last valid project-unit value (safe for live previews)."""
        return float(self._last_value)

    def safe_value(self) -> float:
        try:
            return self.value()
        except VariableError:
            return self.last_value

    def expression(self) -> str:
        text = self.text().strip()
        return "" if parse_number(text) is not None else text

    def expression_entry(self):
        """``None`` for a plain number, otherwise the persisted expression entry."""
        text = self.text().strip()
        if not text or parse_number(text) is not None:
            return None
        return expression_entry(text, self._scale)

    def set_variables(self, variables: Mapping[str, float] | None) -> None:
        self._variables = {
            str(name): float(value)
            for name, value in (variables or {}).items()
            if str(name).strip()
        }
        names = sorted(self._variables, key=str.casefold)
        self._completion_model.setStringList(names)
        self.setToolTip(self._tooltip())

    def completion_names(self) -> list[str]:
        """Current names offered by the completer (public for tests/tools)."""
        return list(self._completion_model.stringList())

    # -- completion -------------------------------------------------------
    def _on_text_edited(self, _text: str) -> None:
        self._set_invalid(False)
        if self._loading or not variable_autocomplete_enabled():
            self._hide_completion()
            return
        token = self._token_before_cursor()
        if not token or not self._completion_model.stringList():
            self._hide_completion()
            return
        self._completion_prefix = token
        self._completer.setCompletionPrefix(token)
        if self._completer.completionCount() <= 0:
            self._hide_completion()
            return
        popup = self._completer.popup()
        if popup is not None:
            popup.setMinimumWidth(max(120, self.width()))
        self._completer.complete()

    def _token_before_cursor(self) -> str:
        prefix = self.text()[: self.cursorPosition()]
        match = _TOKEN_AT_END_RE.search(prefix)
        return match.group(0) if match else ""

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        """Let the variable popup handle Up/Down/Enter while it is open."""
        popup = self._completer.popup()
        if popup is not None and popup.isVisible():
            key = event.key()
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                row = self._completer.currentRow()
                if key == Qt.Key.Key_Down:
                    row = max(0, row + 1)
                else:
                    row = max(0, row - 1)
                self._completer.setCurrentRow(row)
                event.accept()
                return
            if key in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
                Qt.Key.Key_Tab,
            ):
                row = self._completer.currentRow()
                if row < 0:
                    self._completer.setCurrentRow(0)
                completion = self._completer.currentCompletion()
                if completion:
                    self._insert_completion(completion)
                event.accept()
                return
            if key == Qt.Key.Key_Escape:
                self._hide_completion()
                event.accept()
                return
        super().keyPressEvent(event)

    def _insert_completion(self, completion: str) -> None:
        completion = str(completion or "")
        if not completion:
            return
        text = self.text()
        cursor = self.cursorPosition()
        prefix = text[:cursor]
        match = _TOKEN_AT_END_RE.search(prefix)
        if match:
            start = match.start()
            suffix = text[cursor:]
            self.setText(prefix[:start] + completion + suffix)
            self.setCursorPosition(start + len(completion))
        else:
            self.setText(text[:cursor] + completion + text[cursor:])
            self.setCursorPosition(cursor + len(completion))
        self._hide_completion()
        self._on_editing_finished()

    def _hide_completion(self) -> None:
        try:
            popup = self._completer.popup()
            if popup is not None:
                popup.hide()
        except Exception:  # noqa: BLE001 - optional UI sugar
            pass

    # -- validation styling ----------------------------------------------
    def _on_editing_finished(self) -> None:
        text = self.text().strip()
        if not text:
            return
        try:
            value = self.value()
        except VariableError:
            return
        if parse_number(text) is not None:
            display = value / self._scale
            normalized = format_number(display)
            if normalized != text:
                self._loading = True
                try:
                    self.setText(normalized)
                finally:
                    self._loading = False
        self.valueChanged.emit(float(value))

    def _set_invalid(self, invalid: bool) -> None:
        self.setStyleSheet(
            "QLineEdit { border: 1px solid #c0392b; }" if invalid else ""
        )

    def _tooltip(self) -> str:
        lines = [
            "Number or named-variable expression (e.g. L, W/2, sqrt(L*L+W*W)).",
        ]
        if self._variables:
            lines.append("Variables: " + ", ".join(sorted(self._variables, key=str.casefold)))
        if variable_autocomplete_enabled() and self._variables:
            lines.append("Start typing a variable name for suggestions.")
        return "\n".join(lines)


def variable_field_expressions(fields: Mapping[str, VariableField]) -> dict:
    """Collect non-numeric expressions from ``key -> field`` mappings."""
    out: dict = {}
    for key, field in fields.items():
        entry = field.expression_entry()
        if entry is not None:
            out[str(key)] = entry
    return out


def variable_fields_expression_texts(
    fields: Iterable[VariableField],
) -> Iterable[str]:
    for field in fields:
        yield field.expression()
