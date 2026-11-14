"""Port mapping dialog for S-parameter reference comparison."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class SParamPortMapDialog(QDialog):
    """Map every current port to one reference port (1-based values)."""

    def __init__(
        self,
        current_nports: int,
        reference_nports: int,
        mapping: list[int] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.current_nports = max(1, int(current_nports))
        self.reference_nports = max(1, int(reference_nports))
        self.setWindowTitle("S-parameter port mapping")
        self.setMinimumWidth(360)

        root = QVBoxLayout(self)
        info = QLabel(
            "Map each current port to a reference port. Unmapped ports and\n"
            "reference ports that are not used are ignored in the comparison."
        )
        info.setWordWrap(True)
        root.addWidget(info)

        form = QFormLayout()
        self._combos: list[QComboBox] = []
        initial = self._initial_mapping(mapping)
        for i in range(self.current_nports):
            combo = QComboBox()
            combo.addItem("— (unmapped)", 0)
            for ref in range(1, self.reference_nports + 1):
                combo.addItem(f"Reference S{ref}", ref)
            wanted = initial[i] if i < len(initial) else 0
            index = combo.findData(wanted)
            combo.setCurrentIndex(index if index >= 0 else 0)
            self._combos.append(combo)
            form.addRow(f"Current S{i + 1}", combo)
        root.addLayout(form)

        buttons = QHBoxLayout()
        btn_reset = QPushButton("Reset 1:1")
        btn_reset.setToolTip("Map port 1 to port 1, port 2 to port 2, …")
        buttons.addWidget(btn_reset)
        buttons.addStretch(1)
        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.addWidget(box)
        root.addLayout(buttons)

        btn_reset.clicked.connect(self._reset_identity)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)

    def _initial_mapping(self, mapping: list[int] | None) -> list[int]:
        if not mapping:
            return [
                i + 1 if i < self.reference_nports else 0
                for i in range(self.current_nports)
            ]
        out: list[int] = []
        for i in range(self.current_nports):
            if i < len(mapping):
                out.append(int(mapping[i]))
            else:
                out.append(i + 1 if i < self.reference_nports else 0)
        return out

    def _reset_identity(self) -> None:
        for i, combo in enumerate(self._combos):
            value = i + 1 if i < self.reference_nports else 0
            index = combo.findData(value)
            combo.setCurrentIndex(index if index >= 0 else 0)

    def selected_map(self) -> list[int]:
        """1-based mapping; 0 means unmapped."""
        return [int(combo.currentData() or 0) for combo in self._combos]

    def accept(self) -> None:  # noqa: D401 - Qt override
        used = [value for value in self.selected_map() if value > 0]
        if len(used) != len(set(used)):
            QMessageBox.warning(
                self,
                "Duplicate reference port",
                "Each reference port may be used only once. Choose a different "
                "mapping or leave a current port unmapped.",
            )
            return
        super().accept()
