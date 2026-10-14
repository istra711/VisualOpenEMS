"""Ports / Dumps popup window (Geometry menu).

The old "Ports / Dumps" tab page was removed in favour of this modeless
window (Geometry menu -> Ports / Dumps..., Ctrl+Shift+D). Unlike the
Simulation / Mesh Parameters dialogs (which own a separate panel and sync
through the project), the Ports / Dumps widgets are live editors that are
already wired to the MainWindow slots (_on_add_port, _on_edit_port,
_on_remove_port, _on_add_dump, _on_edit_nf2ff). The dialog therefore hosts
the *existing* widgets directly - no copy, no Apply/Close round-trip.

MainWindow builds the widgets in a hidden off-screen container
(``self._ports_form``) and hands it to :class:`PortsDumpsDialog`, which
re-parents them into its own layout. ``refresh()`` is called on every open
so the port list / checkboxes match the current project.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.tab_help import show_tab_help


class PortsDumpsDialog(QDialog):
    """Modeless popup hosting the live Ports / Dumps widgets."""

    def __init__(
        self,
        ports_form: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ports / Dumps")
        self.setModal(False)
        self.resize(460, 560)

        root = QVBoxLayout(self)
        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Ports / Dumps")
        title.setStyleSheet("font-weight: bold;")
        hl.addWidget(title)
        hl.addStretch(1)
        help_btn = QPushButton("Help")
        help_btn.setToolTip("Help for the Ports / Dumps window")
        help_btn.clicked.connect(
            lambda _=False, p=self: show_tab_help(p, "Ports / Dumps")
        )
        hl.addWidget(help_btn)
        root.addWidget(header)

        hint = QLabel(
            "Live editors - changes apply immediately. "
            "Ports: double-click a row to edit. Dumps / NF2FF: build the mesh "
            "first so the box can be fitted."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        # Host the existing (already wired) widgets. Re-parenting moves them
        # out of the hidden off-screen container into this dialog.
        ports_form.setParent(self)
        ports_form.show()
        root.addWidget(ports_form, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)
