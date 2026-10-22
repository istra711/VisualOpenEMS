"""Per-object display options of the Mesh Viewer.

Every geometry object (primitive, imported CAD body) can carry its own
colour, transparency and visibility. The values are edited in the object
dialogs (Geometry menu / project tree) and stored per object name in the
project (:attr:`app.models.project.Project.object_display`), so they travel
with the ``.fcsim.json`` file.

The Mesh Viewer maps the object names onto the CSX primitives it draws and
applies the settings there; ports are never affected.
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QPushButton,
    QSpinBox,
    QWidget,
)

#: transparency used when an object has no explicit value (0 % = solid)
DEFAULT_TRANSPARENCY = 30


def _parse_rgb(raw) -> tuple[float, float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        return None
    try:
        return tuple(  # type: ignore[return-value]
            min(1.0, max(0.0, float(v))) for v in raw[:3]
        )
    except (TypeError, ValueError):
        return None


def normalize_display(raw) -> dict:
    """Return a well-formed, JSON-friendly display dict for one object.

    Unknown/malformed values fall back to the defaults: automatic (= per-kind)
    colour, 30 % transparency and visible.
    """
    color = None
    transparency = DEFAULT_TRANSPARENCY
    visible = True
    if isinstance(raw, dict):
        color = _parse_rgb(raw.get("color"))
        try:
            transparency = min(100, max(0, int(raw.get("transparency", DEFAULT_TRANSPARENCY))))
        except (TypeError, ValueError):
            transparency = DEFAULT_TRANSPARENCY
        if "visible" in raw:
            visible = bool(raw.get("visible"))
    return {
        "color": [float(c) for c in color] if color is not None else None,
        "transparency": int(transparency),
        "visible": bool(visible),
    }


def display_rgb(display: dict | None) -> tuple[float, float, float] | None:
    """Explicit object colour as ``(r, g, b)`` or ``None`` (= per-kind default)."""
    return _parse_rgb((display or {}).get("color"))


def display_alpha(display: dict | None) -> float:
    """Face alpha (0..1) of an object: transparency 0 % -> 1.0 (solid)."""
    try:
        transparency = int((display or {}).get("transparency", DEFAULT_TRANSPARENCY))
    except (TypeError, ValueError):
        transparency = DEFAULT_TRANSPARENCY
    transparency = min(100, max(0, transparency))
    return 1.0 - transparency / 100.0


def display_visible(display: dict | None) -> bool:
    """False when the object is hidden in the Mesh Viewer."""
    if not isinstance(display, dict):
        return True
    return bool(display.get("visible", True))


def _contrast_text(r: int, g: int, b: int) -> str:
    return "black" if 0.299 * r + 0.587 * g + 0.114 * b > 150 else "white"


class DisplayOptionsWidget(QGroupBox):
    """Colour / transparency / visibility of one object (Geometry dialogs)."""

    def __init__(self, display: dict | None = None, parent: QWidget | None = None) -> None:
        super().__init__("Mesh Viewer display", parent)
        d = normalize_display(display)
        self._color: tuple[float, float, float] | None = display_rgb(d)
        self.setToolTip(
            "How this object is drawn in the Mesh Viewer (slices and overview).\n"
            "Colour and transparency are display only — they never change the\n"
            "simulation. 'Visible' hides the object in the viewer."
        )
        form = QFormLayout(self)
        row = QWidget(self)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        self.btn_color = QPushButton("Colour…", row)
        self.btn_color.setToolTip("Pick a fixed colour for this object.")
        self.btn_color.clicked.connect(self._pick_color)
        self.btn_auto = QPushButton("Automatic", row)
        self.btn_auto.setToolTip(
            "Use the colour of the object kind (metal orange, dielectric blue, ...)."
        )
        self.btn_auto.clicked.connect(self._auto_color)
        rl.addWidget(self.btn_color, 1)
        rl.addWidget(self.btn_auto)
        self.spn_transparency = QSpinBox(self)
        self.spn_transparency.setRange(0, 100)
        self.spn_transparency.setSuffix(" %")
        self.spn_transparency.setValue(int(d["transparency"]))
        self.spn_transparency.setToolTip(
            "0 % = solid, 100 % = invisible. More transparency reveals the mesh\n"
            "grid and the objects behind; the outline stays visible."
        )
        self.chk_visible = QCheckBox("Visible", self)
        self.chk_visible.setChecked(bool(d["visible"]))
        self.chk_visible.setToolTip(
            "Show this object in the Mesh Viewer (display only — the object stays\n"
            "part of the simulation)."
        )
        form.addRow("Colour", row)
        form.addRow("Transparency", self.spn_transparency)
        form.addRow(self.chk_visible)
        self._sync_color_button()

    # ------------------------------------------------------------- internals
    def _sync_color_button(self) -> None:
        if self._color is None:
            self.btn_color.setStyleSheet("")
            self.btn_color.setText("Colour…")
            self.btn_auto.setEnabled(False)
            return
        r, g, b = (int(round(c * 255)) for c in self._color)
        self.btn_color.setStyleSheet(
            "QPushButton {"
            f" background-color: rgb({r}, {g}, {b});"
            f" color: {_contrast_text(r, g, b)};"
            " }"
        )
        self.btn_color.setText(f"rgb({r}, {g}, {b})")
        self.btn_auto.setEnabled(True)

    def _pick_color(self) -> None:
        start = (
            QColor.fromRgbF(*self._color)
            if self._color is not None
            else QColor.fromRgbF(0.85, 0.55, 0.2)
        )
        color = QColorDialog.getColor(start, self, "Object colour")
        if not color.isValid():
            return
        self._color = (color.redF(), color.greenF(), color.blueF())
        self._sync_color_button()

    def _auto_color(self) -> None:
        self._color = None
        self._sync_color_button()

    # ---------------------------------------------------------------- public
    def set_values(self, display: dict | None) -> None:
        """Load another object's settings into the widgets (e.g. on rename)."""
        d = normalize_display(display)
        self._color = display_rgb(d)
        self.spn_transparency.setValue(int(d["transparency"]))
        self.chk_visible.setChecked(bool(d["visible"]))
        self._sync_color_button()

    def values(self) -> dict:
        """Current settings as a JSON-friendly dict (for Project.object_display)."""
        return {
            "color": list(self._color) if self._color is not None else None,
            "transparency": int(self.spn_transparency.value()),
            "visible": bool(self.chk_visible.isChecked()),
        }
