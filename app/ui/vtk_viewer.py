"""Embedded VTK viewer for openEMS field dumps (ParaView-style controls).

Mirrors the openEMS "Field Dumps Analysis via ParaView" workflow:
  * choose the displayed field array (point/cell),
  * represent it as Surface / Wireframe / Slice / Iso-surface,
  * rescale the colour range (data range / manual min-max / logarithmic),
  * animate the time steps (Play) when a per-step series exists.

Uses VTK's Qt interactor (``QVTKRenderWindowInteractor``) so the 3D view lives
inside the GUI. The GL window is not created on a headless/offscreen platform
(VTK would crash), but the data logic still runs.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QActionGroup, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.postproc.vtk_export import (  # noqa: F401
    count_dump_steps,
    dump_to_vtr,
    export_dumps_to_vtk,
    field_unit,
)
from app.postproc.video_timing import gif_frame_duration_ms
from app.colormaps import (  # noqa: F401 — re-exported for the settings dialog
    COLORMAPS,
    COLOR_SCALE_MAX_STEPS,
    COLOR_SCALE_MIN_STEPS,
    DEFAULT_COLOR_SCALE,
    colormap_colors,
    colormap_names,
    fill_lookup_table,
)
from app.ui.number_field import SciSpinBox

_AXIS_NORMAL = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}


def _positive_percentile(arr, percentile: float = 1.0) -> float | None:
    """``percentile`` of the values > 0 in a VTK array (None if there are none).

    Used as the lower end of the log colour table: the plain minimum is a single
    near-zero cell in practice (or a float32 denormal) and would stretch the
    table over dozens of decades — that is what made slices look one-coloured.
    """
    import numpy as np
    from vtk.util import numpy_support as ns

    values = np.asarray(ns.vtk_to_numpy(arr), dtype=float).ravel()
    values = values[np.isfinite(values) & (values > 0)]
    return float(np.percentile(values, percentile)) if values.size else None


#: X red / Y green / Z blue — used by the triad, its labels and the ruler.
AXIS_COLORS = ((0.95, 0.25, 0.25), (0.30, 0.85, 0.35), (0.35, 0.55, 1.0))

#: Size presets for the orientation triad (factor on the auto length).
AXES_SCALES: tuple[tuple[str, float], ...] = (
    ("Small (0.5×)", 0.5),
    ("Medium (1×)", 1.0),
    ("Large (2×)", 2.0),
    ("XXL (4×)", 4.0),
    ("XXXL (8×)", 8.0),
)

#: Label font sizes offered in the axes menu.
AXES_FONT_SIZES: tuple[int, ...] = (8, 10, 12, 14, 18, 24)

#: Ruler units: key -> (label, factor from metres)
RULER_UNITS: tuple[tuple[str, str, float], ...] = (
    ("m", "m", 1.0),
    ("cm", "cm", 1e2),
    ("mm", "mm", 1e3),
    ("um", "µm", 1e6),
    ("nm", "nm", 1e9),
)

#: Defaults for the axes display options (also persisted in AppSettings).
#: material/name tokens that identify metal (copper) primitives
_METAL_TOKENS = (
    "copper",
    "pec",
    "metal",
    "conducting",
    "gold",
    "silver",
    "aluminium",
    "aluminum",
)


def is_metal_primitive(prim: dict) -> bool:
    """True for copper/PEC traces that should win coplanar depth tests."""
    try:
        mat = str(prim.get("material", "") or "").strip().lower()
        name = str(prim.get("name", "") or "").strip().lower()
        kind = str(prim.get("kind", "") or "").strip().lower()
    except AttributeError:
        return False
    if kind in ("wire", "curve"):
        return True
    blob = f"{mat} {name}"
    return any(tok in blob for tok in _METAL_TOKENS)


def prefer_on_top(actor) -> None:
    """Depth-bias a metal actor so coplanar traces beat the substrate face.

    A zero-thickness copper trace at the substrate's top elevation is exactly
    coplanar with the substrate polygon. OpenGL then picks the substrate in
    some pixels (the trace looks broken/dashed). A negative polygon offset is
    the standard fix; it only changes the depth comparison, not the geometry.
    """
    try:
        mapper = actor.GetMapper()
        mapper.SetResolveCoincidentTopologyToPolygonOffset()
        mapper.SetRelativeCoincidentTopologyPolygonOffsetParameters(-1.0, -2.0)
        mapper.SetRelativeCoincidentTopologyLineOffsetParameters(-1.0, -2.0)
    except Exception:  # noqa: BLE001 — older VTK / no mapper
        pass


def prefer_metal_on_top(actor, prim: dict) -> None:
    """Apply :func:`prefer_on_top` only to copper/PEC primitives."""
    if is_metal_primitive(prim):
        prefer_on_top(actor)


#: 3D mouse navigation defaults (Viewer settings → Mouse)
DEFAULT_MOUSE = {
    "style": "trackball",  # trackball | joystick
    "motion_factor": 5.0,  # rotate/pan sensitivity (VTK default is 10)
    "wheel_factor": 1.0,  # wheel zoom step
    "invert_wheel": False,
    "swap_buttons": False,  # left drag pans, middle drag rotates
    "right_button_pan": True,  # right drag pans instead of zooming
}


def normalise_mouse_options(options: dict | None = None) -> dict:
    """Return a validated mouse-options dict (unknown values clamped/ignored)."""
    opts = dict(DEFAULT_MOUSE)
    if isinstance(options, dict):
        for key in DEFAULT_MOUSE:
            if key in options:
                opts[key] = options[key]
    opts["style"] = (
        "joystick"
        if str(opts.get("style", "trackball")).strip().lower() == "joystick"
        else "trackball"
    )
    try:
        opts["motion_factor"] = max(1.0, min(20.0, float(opts["motion_factor"])))
    except (TypeError, ValueError):
        opts["motion_factor"] = DEFAULT_MOUSE["motion_factor"]
    try:
        opts["wheel_factor"] = max(0.1, min(3.0, float(opts["wheel_factor"])))
    except (TypeError, ValueError):
        opts["wheel_factor"] = DEFAULT_MOUSE["wheel_factor"]
    opts["invert_wheel"] = bool(opts.get("invert_wheel", False))
    opts["swap_buttons"] = bool(opts.get("swap_buttons", False))
    opts["right_button_pan"] = bool(opts.get("right_button_pan", True))
    return opts


def make_mouse_style(vtk, options: dict | None = None):
    """Create a VTK camera style from the Viewer-settings mouse options.

    ``invert_wheel`` is implemented by negating VTK's wheel motion factor
    (forward then dollies out and backward dollies in).  Button remapping
    (``swap_buttons`` / ``right_button_pan``) is done in :meth:`eventFilter`
    by forwarding the gesture as the built-in middle-button pan.
    """
    opts = normalise_mouse_options(options)
    base = (
        vtk.vtkInteractorStyleJoystickCamera
        if opts["style"] == "joystick"
        else vtk.vtkInteractorStyleTrackballCamera
    )
    # Button remapping (right=pan, left/middle swap) is done at the Qt event
    # level in ``eventFilter``: VTK's interactor does not reliably dispatch
    # right-button events to Python event-handler overrides. The built-in
    # middle-button pan event is used for the translated gesture instead.
    style = base()
    try:
        style.SetMotionFactor(float(opts["motion_factor"]))
    except Exception:  # noqa: BLE001 — joystick style has no motion factor
        pass
    try:
        wheel = float(opts["wheel_factor"])
        style.SetMouseWheelMotionFactor(-wheel if opts["invert_wheel"] else wheel)
    except Exception:  # noqa: BLE001 — older VTK / unusual style
        pass
    return style


DEFAULT_AXES: dict = {
    "show": True,  # master switch for the triad / corner marker
    "corner": True,  # True = ParaView-style corner widget, False = in-scene triad
    "labels": True,  # X / Y / Z captions on the triad
    "font": 24,  # caption font size (pt)
    "scale": 2.0,  # triad size factor (see AXES_SCALES)
    "ruler": True,  # numeric coordinate ticks around the dataset bounds
    "ruler_unit": "mm",  # m | cm | mm | um | nm (follows the project on load)
    "ruler_labels": {"x": True, "y": True, "z": True},
    #: shift the x/y rulers along the z axis (% of the z range)
    "xy_ruler_z_offset": 0.0,
    "origin_back": True,  # put the in-scene triad on the far bbox corner
}

#: Defaults for the vector glyph display (ParaView "Glyph"; persisted in
#: AppSettings). ``size`` = arrow length in units of the arrow sampling distance,
#: ``max_arrows`` caps how many arrows are drawn (deterministic decimation, so a
#: time series does not flicker), ``normalize`` draws all arrows the same length
#: (direction only) instead of scaling them by |E|, ``overlay`` additionally
#: draws the coloured field representation underneath the arrows.
DEFAULT_VECTORS: dict = {
    "enabled": False,
    "size": 1.0,
    "max_arrows": 2000,
    "normalize": True,
    #: The arrows *are* the representation (ParaView style). Drawing the opaque
    #: slice/surface/volume as well hides up to half of every arrow, so the
    #: overlay is off by default and only drawn on request.
    "overlay": False,
    #: minimum arrow strength (share of the peak) and sampling mode
    "min_pct": 0,
    "random": True,
    #: geometry ghost opacity in percent
    "geom_opacity": 12,
}

#: Streamline appearance presets (Phase 1). They change the look only; the
#: seed grid and integration parameters stay as they are.
STREAMLINE_PRESETS: dict[str, dict] = {
    "Draft": {
        "representation": "line",
        "radius_pct": 0.20,
        "sides": 6,
        "line_width": 1.0,
        "opacity": 100,
        "color_mode": "field",
    },
    "Standard": {
        "representation": "tube",
        "radius_pct": 0.20,
        "sides": 8,
        "line_width": 2.0,
        "opacity": 100,
        "color_mode": "field",
    },
    "Publication": {
        "representation": "tube",
        "radius_pct": 0.12,
        "sides": 16,
        "line_width": 2.0,
        "opacity": 75,
        "color_mode": "field",
    },
}

#: Default streamline appearance / seeding (also persisted in AppSettings).
#: ``radius_pct`` is the tube radius as a percentage of the data diagonal;
#: ``color_mode`` is field | speed | solid.
DEFAULT_STREAMLINES: dict = {
    "enabled": False,
    "preset": "Standard",
    "representation": "tube",
    "radius_pct": 0.20,
    "sides": 8,
    "line_width": 2.0,
    "opacity": 100,
    "color_mode": "field",
    "color": [0.25, 0.65, 1.0],
    "seeds": 6,
}

#: Colour-bar title options (Viewer settings). "left" draws the title rotated
#: next to the bar at normalized viewport ``x``/``y``; "size" is the point size
#: and "rotation" the text angle (90 = vertical, as used beside the bar).
DEFAULT_BAR_TITLE: dict = {
    "left": False,
    "x": 0.826,
    "y": 0.5,
    "size": 12,
    "rotation": 90,
}

#: Colour-bar geometry and text (Viewer settings). Position/size are normalized
#: viewport coordinates: ``x``/``y`` = lower-left corner, ``width``/``height``
#: = the space the bar plus labels occupies.
DEFAULT_COLOR_BAR: dict = {
    "x": 0.84,
    "y": 0.06,
    "width": 0.15,
    "height": 0.88,
    "font": "Arial",
    "font_size": 10,
}

#: Font families offered for the colour bar / its title (VTK built-ins).
COLOR_BAR_FONTS: tuple[str, ...] = ("Arial", "Times", "Courier")

#: Field Viewer layout modes (Viewer settings):
#: "panel" = docked display panel (B), "minimal" = hidden panel + Display popup (C)
VIEWER_LAYOUTS: tuple[tuple[str, str], ...] = (
    ("Display panel (B)", "panel"),
    ("Minimal — Display… popup (C)", "minimal"),
)
DEFAULT_VIEWER_LAYOUT = "panel"

#: Standard views of the 3D pane (key -> label). "keep" only fits the camera and
#: keeps the direction it has (the old "Reset view" behaviour).
VIEW_PRESETS: tuple[tuple[str, str], ...] = (
    ("keep", "Fit (keep direction)"),
    ("iso", "Isometric"),
    ("x+", "From +X"),
    ("x-", "From -X"),
    ("y+", "From +Y"),
    ("y-", "From -Y"),
    ("z+", "From +Z (top)"),
    ("z-", "From -Z (bottom)"),
)

#: Camera direction (from the dataset centre) + up vector per preset.
VIEW_DIRECTIONS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "iso": ((1.0, 1.0, 1.0), (0.0, 0.0, 1.0)),
    "x+": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "x-": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "y+": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "y-": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "z+": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "z-": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),
}


def preset_camera(bounds, key: str):
    """Camera ``(position, focal point, view up)`` for a standard view.

    ``bounds`` is a VTK 6-tuple (xmin, xmax, ymin, ymax, zmin, zmax); the
    camera sits on the given axis at a unit distance from the centre — the
    caller fits the real distance with ``ResetCamera()``. Returns ``None`` for
    "keep"/unknown keys and when nothing visible is in the scene (VTK reports
    ``(1e308, -1e308, …)`` then).
    """
    direction = VIEW_DIRECTIONS.get(str(key))
    if direction is None:
        return None
    try:
        xmin, xmax, ymin, ymax, zmin, zmax = (float(b) for b in bounds)
    except (TypeError, ValueError):
        return None
    if xmax < xmin or xmin > 1e30:
        return None
    if not math.isfinite(xmin + xmax + ymin + ymax + zmin + zmax):
        return None
    pos, up = direction
    cx, cy, cz = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0
    return (
        (cx + pos[0], cy + pos[1], cz + pos[2]),
        (cx, cy, cz),
        (float(up[0]), float(up[1]), float(up[2])),
    )


def _field_kind(path) -> str:
    """File name without the frame number: ``E_substrate_0118`` -> ``E_substrate``.

    Tells which field is on screen (the openEMS dump kind) for single files and
    for per-step series (``<kind>_<index>.vtr``).
    """
    stem = getattr(path, "stem", None) or str(path)
    m = re.match(r"(?P<base>.+)_(?P<idx>\d+)$", str(stem))
    return m.group("base") if m else str(stem)


def axes_total_length(bounds, scale: float = 1.0) -> float:
    """Edge length of the orientation triad, derived from the dataset bounds.

    A fixed length (the old ``10, 10, 10``) dwarfs the metre-sized (or smaller)
    openEMS domains and dominates ``ResetCamera()``, so the field looked
    invisible at startup until the user zoomed in deeply. ``scale`` multiplies
    the auto length (axes size preset).
    """
    dx = abs(float(bounds[1]) - float(bounds[0]))
    dy = abs(float(bounds[3]) - float(bounds[2]))
    dz = abs(float(bounds[5]) - float(bounds[4]))
    try:
        factor = float(scale)
    except (TypeError, ValueError):
        factor = 1.0
    if not math.isfinite(factor) or factor <= 0.0:
        factor = 1.0
    diag = math.sqrt(dx * dx + dy * dy + dz * dz)
    if not math.isfinite(diag) or diag <= 0.0:
        return factor  # fallback: auto length 1.0 (times the size factor)
    return 0.18 * diag * factor


PANEL_LABEL_W = 76   # display panel: every label column gets this width …
PANEL_FIELD_W = 100  # … and every input field this width, so rows line up


def _uniform_form(form: QFormLayout) -> None:
    """Give every row of a display-panel form the same label/field widths."""
    form.setContentsMargins(0, 0, 0, 0)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
    for row in range(form.rowCount()):
        label_item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
        field_item = form.itemAt(row, QFormLayout.ItemRole.FieldRole)
        if label_item is None:
            # spanning row (slider, checkbox, button): keep the full width
            continue
        if label_item.widget() is not None:
            label_item.widget().setFixedWidth(PANEL_LABEL_W)
        if field_item is None:
            continue
        widget = field_item.widget()
        if widget is not None:
            widget.setFixedWidth(PANEL_FIELD_W)
            continue
        layout = field_item.layout()
        if layout is not None:
            # e.g. "Position": slider + spin box -> only the spin gets a width
            for i in range(layout.count()):
                sub = layout.itemAt(i).widget()
                if sub is not None and sub.__class__.__name__.endswith("SpinBox"):
                    sub.setFixedWidth(64)


def style_axes_triad(
    axes, *, show_labels: bool = True, font_size: int = 12
) -> None:
    """Colour and label a ``vtkAxesActor`` (X red / Y green / Z blue).

    ``show_labels`` toggles the X/Y/Z captions (the colours stay either way),
    ``font_size`` sets their point size. Shared by the in-scene triad and the
    corner orientation marker.
    """
    try:
        size = max(1, int(font_size))
    except (TypeError, ValueError):
        size = 12
    axes.SetAxisLabels(1 if show_labels else 0)
    for axis, (r, g, b) in zip(("X", "Y", "Z"), AXIS_COLORS):
        shaft = getattr(axes, f"Get{axis}AxisShaftProperty")()
        tip = getattr(axes, f"Get{axis}AxisTipProperty")()
        shaft.SetColor(r, g, b)
        tip.SetColor(r, g, b)
        getattr(axes, f"Set{axis}AxisLabelText")(axis)
        text = getattr(axes, f"Get{axis}AxisCaptionActor2D")().GetCaptionTextProperty()
        text.SetColor(r, g, b)
        text.SetFontSize(size)


def _vtk():
    """Import vtk lazily (heavy) and make the render backend available."""
    import vtk
    import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 — register OpenGL factory
    import vtkmodules.vtkInteractionStyle  # noqa: F401
    import vtkmodules.vtkRenderingFreeType  # noqa: F401

    install_vtk_output_window(vtk)
    return vtk


_vtk_output_installed = False


def vtk_message_log_path() -> Path:
    """Where VTK's warnings/errors are written (next to app_settings.json)."""
    try:
        from app.paths import settings_path

        return Path(settings_path()).parent / "vtk_messages.log"
    except Exception:  # noqa: BLE001
        import tempfile

        return Path(tempfile.gettempdir()) / "vtk_messages.log"


def install_vtk_output_window(vtk, path: Path | None = None) -> Path:
    """Write VTK messages to a file instead of VTK's pop-up output window.

    In a windowed app (no console — notably the bundled EXE) VTK's default
    output window is a native window that pops up on every warning. Redirecting
    it to ``vtk_messages.log`` keeps the UI clean; the path is printed once so
    it can still be inspected.
    """
    global _vtk_output_installed
    log = Path(path) if path else vtk_message_log_path()
    if _vtk_output_installed and path is None:
        return log
    try:
        ow = vtk.vtkFileOutputWindow()
        ow.SetFileName(str(log))
        ow.SetAppend(0)
        vtk.vtkOutputWindow.SetInstance(ow)
        _vtk_output_installed = True
        print(f"VTK messages -> {log}")
    except Exception as e:  # noqa: BLE001 — never break the viewer on this
        print(f"Could not redirect the VTK output window: {e}")
    return log


def configure_marker_widget(widget, viewport=(0.0, 0.0, 0.16, 0.22)) -> None:
    """Enable the corner orientation widget and switch its interactivity off.

    The order matters: ``SetInteractive``/``InteractiveOff`` BEFORE
    ``SetEnabled(1)`` makes VTK print "Set interactor and Enabled before
    changing interaction" (vtkOrientationMarkerWidget.cxx) and pop up its
    output window. Enable first, then turn the interaction off.
    """
    widget.SetViewport(*viewport)
    widget.SetEnabled(1)
    widget.InteractiveOff()


def _make_reader(vtk, path: Path):
    """Pick a VTK reader from the file extension (not just our own .vtr)."""
    cls = {
        ".vtr": "vtkXMLRectilinearGridReader",
        ".vti": "vtkXMLImageDataReader",
        ".vtu": "vtkXMLUnstructuredGridReader",
        ".vtp": "vtkXMLPolyDataReader",
        ".vts": "vtkXMLStructuredGridReader",
        ".vtk": "vtkGenericDataObjectReader",  # legacy .vtk
    }.get(path.suffix.lower(), "vtkXMLGenericDataObjectReader")
    return getattr(vtk, cls)()


class FieldViewerWindow(QMainWindow):
    """Standalone window with its OWN field viewer.

    Re-parenting the embedded QVTKRenderWindowInteractor between top-level
    windows leaves a stale native window behind (a duplicated view), so the
    pop-out gets a second VtkFieldViewer that mirrors the current state instead.
    """

    def __init__(self, source: "VtkFieldViewer") -> None:
        super().__init__(None)
        self.source: VtkFieldViewer | None = source
        self.project_name = getattr(source, "project_name", "Untitled")
        self._field_suffix = ""
        self.viewer = VtkFieldViewer(work_dir=source.work_dir)
        self.viewer.set_project_name(self.project_name)
        self.viewer.btn_popout.setVisible(False)  # no nested pop-outs
        self.viewer.on_axes_changed = self._on_viewer_axes_changed
        self.viewer.on_view_changed = self._on_viewer_view_changed
        self.viewer.on_vectors_changed = self._on_viewer_vectors_changed
        self.viewer.on_streamlines_changed = self._on_viewer_streamlines_changed
        self.viewer.on_color_scale_changed = self._on_viewer_color_scale_changed
        self.resize(1000, 780)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setCentralWidget(self.viewer)

        if source._current_path is not None:
            # the title tells which field this window shows (no frame number)
            self._field_suffix = f" - {_field_kind(source._current_path)}"
            self.viewer.load(source._current_path)
        self.viewer._copy_state_from(source)
        self.set_project_name(self.project_name)

    def set_project_name(self, name: str) -> None:
        self.project_name = str(name or "").strip() or "Untitled"
        self.setWindowTitle(
            f"Field Viewer — {self.project_name}{self._field_suffix}"
        )
        if self.viewer is not None:
            self.viewer.set_project_name(self.project_name)

    def _on_viewer_axes_changed(self, options: dict) -> None:
        """Axes changed in the pop-out: mirror to the tab view + persist."""
        src = self.source
        if src is not None and src is not self.viewer:
            try:
                src.set_axes_options(options, notify=True)
            except Exception:  # noqa: BLE001
                pass

    def _on_viewer_vectors_changed(self, options: dict) -> None:
        """Vector glyphs changed in the pop-out: mirror to the tab view + persist."""
        src = self.source
        if src is not None and src is not self.viewer:
            try:
                src.set_vector_options(options, notify=True)
            except Exception:  # noqa: BLE001
                pass

    def _on_viewer_streamlines_changed(self, options: dict) -> None:
        """Streamlines changed in the pop-out: mirror to the tab view + persist."""
        src = self.source
        if src is not None and src is not self.viewer:
            try:
                src.set_streamline_options(options, notify=True)
            except Exception:  # noqa: BLE001
                pass

    def _on_viewer_color_scale_changed(self, options: dict) -> None:
        """Colour scale changed in the pop-out: mirror to the tab view + persist."""
        src = self.source
        if src is not None and src is not self.viewer:
            try:
                src.set_color_scale_options(options, notify=True)
            except Exception:  # noqa: BLE001
                pass

    def _on_viewer_view_changed(self, key: str) -> None:
        """Standard view changed in the pop-out: mirror to the tab view + persist."""
        src = self.source
        if src is not None and src is not self.viewer:
            try:
                src.set_view_default(key)
                src.apply_view_preset(key)
                if callable(src.on_view_changed):
                    src.on_view_changed(src.view_preset())
            except Exception:  # noqa: BLE001
                pass

    def closeEvent(self, event) -> None:  # noqa: N802
        src = self.source
        self.source = None
        if src is not None and src._popout_win is self:
            src._popout_win = None
            src.btn_popout.setText("Pop out")
        super().closeEvent(event)


class VtkFieldViewer(QWidget):
    """A VTK render window + ParaView-style controls for field dumps."""

    def __init__(self, work_dir: Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.work_dir = Path(work_dir) if work_dir else None
        self.project_name = "Untitled"
        self._vtk = None
        self._iren = None
        self._renderer = None
        self._geom_actors: list = []
        self._geom_prims: list = []
        self._geom_unit: float = 1e-3
        #: optional right-click display mode: opaque geometry + bright outline
        self._high_contrast = False
        self._title_actor = None
        self._bar_title: str = ""
        self._grid = None
        #: exact slice position in metres typed into the spinbox (None = the
        #: slider governs the position)
        self._slice_pos_exact: float | None = None
        #: SI unit of the loaded dump (V/m, A/m, …; "" when unknown)
        self._field_unit = ""
        self._data_range = (0.0, 1.0)
        self._scalar_name = None  # kept for compatibility / status text
        self._scalar_range = (0.0, 1.0)
        self._series: list[Path] = []
        self._series_labels: list[str] = []
        self._series_times: list[float | None] = []  # seconds (TD dumps)
        self._series_freqs: list[float | None] = []  # Hz (FD dumps)
        self._current_path: Path | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._on_play_tick)
        #: lower end of the log colour table (1 % percentile of the current
        #: frame, see ``_positive_percentile``)
        self._robust_min: float | None = None
        self._popout_win: FieldViewerWindow | None = None
        # axes display options (menu + persisted settings)
        self._axes = dict(DEFAULT_AXES)
        #: 3D mouse navigation (Viewer settings → Mouse)
        self._mouse = dict(DEFAULT_MOUSE)
        #: True once the user picked a ruler unit; then project loading must not
        #: overwrite it (the default follows the project's drawing unit).
        self._ruler_unit_user = False
        self.on_axes_changed = None  # callback(options: dict) for persistence
        #: standard view key (VIEW_PRESETS), persisted as the Field Viewer default
        self.on_view_changed = None  # callback(key: str) for persistence
        #: vector glyph settings (DEFAULT_VECTORS); the widgets mirror them
        self._vectors = dict(DEFAULT_VECTORS)
        self.on_vectors_changed = None  # callback(options: dict) for persistence
        #: streamline appearance / seeding (DEFAULT_STREAMLINES)
        self._stream = dict(DEFAULT_STREAMLINES)
        self.on_streamlines_changed = None  # callback(options: dict) for persistence
        #: colour scale of the field plot (DEFAULT_COLOR_SCALE); changed in
        #: Settings, mirrored to the pop-out
        self._color_scale = dict(DEFAULT_COLOR_SCALE)
        self.on_color_scale_changed = None  # callback(options: dict)
        #: colour-bar title (DEFAULT_BAR_TITLE); edited in Viewer settings
        self._bar_title_opts = dict(DEFAULT_BAR_TITLE)
        #: colour-bar geometry/font (DEFAULT_COLOR_BAR); edited in Viewer settings
        self._color_bar_opts = dict(DEFAULT_COLOR_BAR)
        #: layout "panel" (B) or "minimal" (C); edited in Viewer settings
        self._layout_mode = DEFAULT_VIEWER_LAYOUT
        self._display_dialog = None
        #: last Display-popup geometry (base64) + persistence callback
        self._display_geometry = ""
        self.on_display_geometry_changed = None
        self._has_vectors = False  # loaded grid carries a 3-component field array
        #: cached reference |field| over the series + the array it was taken from
        self._vec_series_ref: float | None = None
        self._vec_ref_name: str | None = None
        self._marker = None  # vtkOrientationMarkerWidget (corner axes)
        self._marker_axes = None  # its vtkAxesActor
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        self._root_layout = root  # holds the control rows + the 3D pane

        # Right-click menu on the controls/background only — never on the 3D
        # pane (VTK uses right-click there, see _context_menu_allowed).
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        bar = QHBoxLayout()
        self.btn_open = QPushButton("Open…")
        self.btn_open.clicked.connect(self._on_open)
        self.btn_export = QPushButton("Export ▾")
        self.btn_export.setToolTip(
            "Export the current project's field dumps to VTK.\n"
            "The menu also holds the series options (per-step, step stride).\n\n"
            "Only changed dumps are rewritten (outputs newer than the dump are\n"
            "reused); a progress dialog shows the work and can cancel it."
        )
        self.btn_export.clicked.connect(self._show_export_menu)
        self.chk_per_step = QCheckBox("per-step")
        self.chk_per_step.setChecked(True)
        self.chk_per_step.setToolTip(
            "Also write one .vtr per stored time/frequency step (needed for the\n"
            "time slider and the video export).\n"
            "A time-domain dump can have hundreds of steps — untick for a quick\n"
            "static view (much faster, far less disk space)."
        )
        self.spin_stride = QSpinBox()
        self.spin_stride.setRange(1, 50)
        self.spin_stride.setValue(1)
        self.spin_stride.setPrefix("every ")
        self.spin_stride.setSuffix(". step")
        self.spin_stride.setFixedWidth(110)
        self.spin_stride.setToolTip(
            "Per-step export: keep only every N-th stored sample.\n"
            "470 samples at 27 ps → every 4th step = ~118 frames (~3.5 GB\n"
            "instead of ~14 GB); the time slider still spans the full run.\n"
            "Changing this re-exports the series (the stride is stored in the\n"
            "sidecar)."
        )
        self.chk_per_step.toggled.connect(self.spin_stride.setEnabled)
        self.cmb_rep = QComboBox()
        self.cmb_rep.addItems(["Surface", "Wireframe", "Slice", "Iso-surface"])
        self.cmb_rep.setCurrentText("Surface")  # ParaView-like default
        self.cmb_rep.currentIndexChanged.connect(self._on_rep_changed)
        self.cmb_array = QComboBox()
        self.cmb_array.setMinimumWidth(100)
        self.cmb_array.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.cmb_array.setMinimumContentsLength(8)
        self.cmb_array.setToolTip("Field array to display (point or cell data).")
        self.cmb_array.currentIndexChanged.connect(self._on_array_changed)
        self.chk_outline = QCheckBox("Outline")
        self.chk_outline.setChecked(False)
        self.chk_outline.toggled.connect(self._refresh_display)
        self.chk_geom = QCheckBox("Geometry")
        self.chk_geom.setChecked(True)
        self.chk_geom.setToolTip(
            "Draw the project's box primitives as translucent ghosts.\n"
            "They are set when a project is loaded or the Field Viewer is opened."
        )
        self.chk_geom.toggled.connect(self._draw_geometry)
        self.sld_geom = QSpinBox()
        self.sld_geom.setRange(0, 40)
        self.sld_geom.setValue(12)
        self.sld_geom.setSuffix(" %")
        self.sld_geom.setToolTip("Geometry opacity in percent (0 = invisible).")
        self.sld_geom.valueChanged.connect(self._draw_geometry)
        self.btn_reset = QPushButton("Reset view")
        self.btn_reset.clicked.connect(self._reset_camera)
        self.btn_display = QPushButton("Display…")
        self.btn_display.setToolTip(
            "Layout C: open the display panel (Data / Slice / Colour / Vectors)\n"
            "in a popup instead of docking it beside the 3D view."
        )
        self.btn_display.clicked.connect(self._open_display_popup)
        self.btn_display.setVisible(False)
        self.btn_video = QPushButton("Video…")
        self.btn_video.setToolTip(
            "Open the export dialog for the loaded field sequence.\n"
            "Choose MP4 or animated GIF, output resolution, fps/duration and\n"
            "a start/end frame range. The current 3D settings (representation,\n"
            "array, colour range, axes, camera) are used for every frame.\n"
            "MP4 needs imageio-ffmpeg; GIF only needs Pillow (256 colours, can\n"
            "get large for long series)."
        )
        self.btn_video.setEnabled(False)  # needs a loaded time series
        self.btn_video.clicked.connect(self._on_export_video)
        self.btn_popout = QPushButton("Pop out")
        self.btn_popout.setToolTip("Show the 3D view in a separate window (click again to dock).")
        self.btn_popout.clicked.connect(self.popout)
        # Two rows: one row of all controls was ~1670 px wide and thereby set the
        # minimum width of the whole main window - the Project/Geometry splitter
        # could not be dragged any more.
        for w in (self.btn_open, self.btn_export, self.chk_per_step, self.spin_stride,
                  self.chk_outline, self.chk_geom, self.sld_geom):
            bar.addWidget(w)
        # standard view (applied now + used for every newly loaded field) and the
        # info which field is on screen (file name without the frame number);
        # left-aligned with the rest, the leftover width stays at the right end
        self.cmb_view_preset = QComboBox()
        self.cmb_view_preset.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.cmb_view_preset.setMinimumContentsLength(8)
        for key, label in VIEW_PRESETS:
            self.cmb_view_preset.addItem(label, key)
        self.cmb_view_preset.setToolTip(
            "Standard view of the 3D pane.\n"
            "Picking one applies it right away AND is remembered: every field\n"
            "loaded afterwards starts in this view (also in the pop-out).\n"
            "'Fit (keep direction)' only fits the camera, like the old Reset view."
        )
        self.cmb_view_preset.currentIndexChanged.connect(self._on_view_preset_changed)
        lbl_view = QLabel("View")
        lbl_view.setToolTip(self.cmb_view_preset.toolTip())
        bar.addWidget(lbl_view)
        bar.addWidget(self.cmb_view_preset)
        self.lbl_field = QLabel("")
        field_font = self.lbl_field.font()
        field_font.setBold(True)
        self.lbl_field.setFont(field_font)
        self.lbl_field.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_field.setToolTip(
            "Field on screen: the loaded file name without the frame number.\n"
            "Hover for the file, the series length and the array."
        )
        bar.addWidget(self.lbl_field)
        bar.addStretch(1)
        self.lbl_project = QLabel("Project: Untitled")
        self.lbl_project.setToolTip("Currently open VisualopenEMS project")
        _pf = self.lbl_project.font()
        _pf.setBold(True)
        self.lbl_project.setFont(_pf)
        bar.addWidget(self.lbl_project)


        bar_view = QHBoxLayout()
        lbl_rep = QLabel("Show")
        lbl_rep.setToolTip(
            "Representation: Surface / Wireframe / Slice / Iso-surface\n"
            "(the ParaView 'Representation' option)."
        )
        for w in (lbl_rep, self.cmb_rep, QLabel("Array"), self.cmb_array):
            bar_view.addWidget(w)
        for w in (self.btn_video, self.btn_popout, self.btn_reset):
            bar_view.addWidget(w)
        bar_view.addStretch(1)


        # slice + iso level
        row2 = QHBoxLayout()
        lbl_axis = QLabel("Axis")
        lbl_axis.setToolTip("Slice axis (X / Y / Z).")
        row2.addWidget(lbl_axis)
        self.cmb_slice_axis = QComboBox()
        self.cmb_slice_axis.addItems(["X", "Y", "Z"])
        self.cmb_slice_axis.currentIndexChanged.connect(self._on_slice_axis_changed)
        self.slider_slice = QSlider(Qt.Orientation.Horizontal)
        self.slider_slice.setRange(0, 1000)
        self.slider_slice.setValue(500)
        self.slider_slice.setMinimumWidth(100)
        self.slider_slice.valueChanged.connect(self._on_slice_slider)
        self.slider_slice.setToolTip(
            "Slice position along the selected axis (0…1000 of the bounds).\n"
            "The exact position is shown next to it and can be typed in (mm)."
        )
        self.spn_slice = QDoubleSpinBox()
        self.spn_slice.setDecimals(3)
        # ±1 km in mm: plenty for any domain, and a wider range only widens the
        # box (Qt derives the minimum width from the largest value text)
        self.spn_slice.setRange(-1e6, 1e6)
        self.spn_slice.setMaximumWidth(100)
        self.spn_slice.setSuffix(" mm")
        self.spn_slice.setKeyboardTracking(False)  # apply on Enter / focus-out
        self.spn_slice.valueChanged.connect(self._on_slice_value)
        self.spn_slice.setToolTip(
            "Slice position — direct numeric input in mm.\n"
            "The 3D axes use metres (1 mm = 0.001 m); the value snaps the\n"
            "slider and cuts exactly at the typed position."
        )
        row2.addWidget(self.cmb_slice_axis)
        # Capped lengths: both sliders used to stretch over the whole row (the
        # slice slider ~740 px at a 1600 px window). 3:2 keeps them at their caps
        # from ~1500 px on, and the leftover space collects in front of the
        # range/unit readout instead of stretching the sliders.
        self.slider_slice.setMaximumWidth(320)
        row2.addWidget(self.slider_slice, 3)
        row2.addWidget(self.spn_slice)
        self.chk_planes = QCheckBox("3 planes")
        self.chk_planes.setToolTip(
            "Show the X, Y and Z slices at the same time (ParaView style).\n"
            "The selected axis follows the slider, the other two cut through\n"
            "the centre of their range."
        )
        self.chk_planes.toggled.connect(self._on_slider)
        row2.addWidget(self.chk_planes)
        self.chk_contours = QCheckBox("Contours")
        self.chk_contours.setToolTip(
            "Iso-lines of the selected array drawn on the slice."
        )
        self.chk_contours.toggled.connect(self._on_slider)
        row2.addWidget(self.chk_contours)
        self.spn_contours = QSpinBox()
        self.spn_contours.setRange(2, 24)
        self.spn_contours.setValue(8)
        self.spn_contours.setToolTip("Number of contour lines.")
        self.spn_contours.valueChanged.connect(self._on_slider)
        row2.addWidget(self.spn_contours)
        row2.addWidget(QLabel("Iso-level"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(500)
        self.slider.setMaximumWidth(200)
        self.slider.valueChanged.connect(self._on_slider)
        row2.addWidget(self.slider, 2)
        row2.addStretch(1)
        self.lbl_level = QLabel("—")
        self.lbl_level.setMinimumWidth(0)
        self.lbl_level.setWordWrap(True)
        self.lbl_level.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        row2.addWidget(self.lbl_level)


        # colour range (ParaView "Rescale to ... / log scale")
        row3 = QHBoxLayout()
        self.chk_auto = QCheckBox("Auto")
        self.chk_auto.setChecked(True)
        self.chk_auto.setToolTip(
            "Colour limits follow the current frame, so every frame uses the\n"
            "full colour table and changes over time stay visible.\n"
            "Untick to stop following: the limits keep their current values\n"
            "and can be typed in - handy to compare frames with one scale."
        )
        self.chk_auto.toggled.connect(self._on_auto_toggled)
        # scientific fields: QDoubleSpinBox rounds to its decimals, which made
        # field magnitudes (1e-9 ...) show up as 0.000000 and impossible to type
        self.spn_min = SciSpinBox(0.0)
        self.spn_max = SciSpinBox(1.0)
        for s in (self.spn_min, self.spn_max):
            s.setEnabled(False)
            s.valueChanged.connect(self._on_color_spin_changed)
        self.chk_log = QCheckBox("Log scale")
        self.chk_log.setToolTip(
            "Log10 colour mapping (like ParaView's 'Use log scale').\n"
            "In log mode Auto starts at the 1 % percentile of the positive\n"
            "values (at least 1e-7 of the maximum): a single near-zero cell\n"
            "would otherwise stretch the table over decades and squeeze\n"
            "everything into one colour. A manual minimum of 0 is replaced\n"
            "by that value; values below it are drawn in the lowest colour."
        )
        self.chk_log.toggled.connect(self._on_log_toggled)
        self.btn_rescale = QPushButton("Rescale")
        self.btn_rescale.setToolTip(
            "Rescale min/max to the range of the current frame.\n"
            "Not needed while Auto is on - it does exactly that."
        )
        self.btn_rescale.clicked.connect(self._rescale_to_data)
        row3.addWidget(QLabel("Colour"))
        row3.addWidget(self.chk_auto)
        row3.addWidget(QLabel("min"))
        row3.addWidget(self.spn_min)
        row3.addWidget(QLabel("max"))
        row3.addWidget(self.spn_max)
        row3.addWidget(self.chk_log)
        row3.addWidget(self.btn_rescale)
        row3.addStretch(1)


        # vector glyphs (ParaView "Glyph"): small arrows show the field
        # direction, so a time series visibly "moves" (rotating E field etc.)
        row_vec = QHBoxLayout()
        self.chk_vectors = QCheckBox("Vectors")
        self.chk_vectors.setToolTip(
            "Draw small arrows (glyphs) for the vector field — ParaView's\n"
            "Glyph filter. The arrows show the direction and (optionally)\n"
            "the strength of the field in the current representation:\n"
            "Slice → arrows in the cut plane, Surface → on the surface,\n"
            "Iso-surface → on the iso-surface.\n"
            "The arrows replace the coloured field plot (an opaque slice or\n"
            "volume would cover the arrows pointing into it) — tick 'field\n"
            "underneath' to get the overlay back.\n"
            "During playback the arrows follow the field; while stepping by\n"
            "hand the step label adds how many arrows flipped."
        )
        self.chk_vectors.toggled.connect(self._on_vectors_toggled)
        self._vec_tip = self.chk_vectors.toolTip()
        self.spn_vec_size = QDoubleSpinBox()
        self.spn_vec_size.setRange(0.1, 8.0)
        self.spn_vec_size.setSingleStep(0.1)
        self.spn_vec_size.setDecimals(2)
        self.spn_vec_size.setValue(DEFAULT_VECTORS["size"])
        self.spn_vec_size.setToolTip(
            "Arrow length relative to the arrow spacing (1.0 = arrows just\n"
            "about touch). Increase for long arrows, decrease for small ticks."
        )
        self.spn_vec_size.valueChanged.connect(self._on_vector_option_changed)
        self.spn_vec_count = QSpinBox()
        self.spn_vec_count.setRange(50, 50000)
        self.spn_vec_count.setSingleStep(250)
        self.spn_vec_count.setValue(DEFAULT_VECTORS["max_arrows"])
        self.spn_vec_count.setToolTip(
            "Maximum number of arrows. The points are decimated uniformly and\n"
            "deterministically (every N-th point), so the arrows stay put from\n"
            "frame to frame instead of flickering."
        )
        self.spn_vec_count.valueChanged.connect(self._on_vector_option_changed)
        self.spn_vec_min = QSpinBox()
        self.spn_vec_min.setRange(0, 99)
        self.spn_vec_min.setValue(int(DEFAULT_VECTORS["min_pct"]))
        self.spn_vec_min.setSuffix(" %")
        self.spn_vec_min.setToolTip(
            "Hide arrows whose field is below this share of the strongest |v|\n"
            "in the current frame (linear). 0 % keeps every sampled point;\n"
            "1-3 % already remove most of the near-zero clutter because the\n"
            "field spans decades."
        )
        self.spn_vec_min.valueChanged.connect(self._on_vector_option_changed)
        self.chk_vec_random = QCheckBox("random")
        self.chk_vec_random.setChecked(bool(DEFAULT_VECTORS["random"]))
        self.chk_vec_random.setToolTip(
            "Sample the arrows at random (fixed seed 12345) instead of every\n"
            "N-th point. Prints the point counts to the log while it runs."
        )
        self.chk_vec_random.toggled.connect(self._on_vector_option_changed)
        self.chk_vec_norm = QCheckBox("same length")
        self.chk_vec_norm.setChecked(DEFAULT_VECTORS["normalize"])
        self.chk_vec_norm.setToolTip(
            "All arrows the same length: purely the direction is visible\n"
            "(best for watching the field rotate).\n"
            "Untick to scale the length with the field strength: the same\n"
            "field strength gives the same arrow length in every frame (the\n"
            "reference is measured once over the whole series), so the real\n"
            "amplitude change over time stays visible (99 % of it maps to the\n"
            "'size' length)."
        )
        self.chk_vec_norm.toggled.connect(self._on_vector_option_changed)
        self.chk_vec_overlay = QCheckBox("field below")
        self.chk_vec_overlay.setChecked(DEFAULT_VECTORS["overlay"])
        self.chk_vec_overlay.setToolTip(
            "Also draw the coloured field representation (slice / surface /\n"
            "iso-surface) underneath the arrows. Off by default: an opaque\n"
            "surface or volume hides the arrows that point into it, so the\n"
            "arrows alone are the representation (as in ParaView)."
        )
        self.chk_vec_overlay.toggled.connect(self._on_vector_option_changed)
        row_vec.addWidget(self.chk_vectors)
        row_vec.addWidget(self.spn_vec_size)
        row_vec.addWidget(self.spn_vec_count)
        row_vec.addWidget(self.spn_vec_min)
        row_vec.addWidget(self.chk_vec_random)
        row_vec.addWidget(self.chk_vec_norm)
        row_vec.addWidget(self.chk_vec_overlay)
        row_vec.addStretch(1)


        # time series (animation)
        row4 = QHBoxLayout()
        row4.setContentsMargins(0, 0, 0, 0)
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.setCheckable(True)
        self.btn_play.setMinimumWidth(84)
        self.btn_play.toggled.connect(self._on_play_toggled)
        self.lbl_time = QLabel("Time")
        self.slider_time = QSlider(Qt.Orientation.Horizontal)
        self.slider_time.setRange(0, 0)
        self.slider_time.setMaximumWidth(520)
        self.slider_time.valueChanged.connect(self._on_time_slider)
        self.lbl_time_info = QLabel("")
        self.lbl_time_info.setFixedWidth(150)
        self.lbl_sim_time = QLabel("")
        self.lbl_sim_time.setFixedWidth(110)
        self.lbl_sim_time.setToolTip(
            "Physical simulation time of this frame (ps/ns) — or frequency for "
            "frequency-domain dumps — taken from the openEMS HDF5 dump."
        )
        #: arrow-motion diagnostics: fixed width so the playback bar does not
        #: jump when the text changes from step to step (full text in tooltip)
        self.lbl_motion = QLabel("")
        self.lbl_motion.setMinimumWidth(0)
        self.lbl_motion.setMaximumWidth(360)
        self.lbl_motion.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        self.lbl_motion.setToolTip("Arrow motion compared with the previous step.")
        self.spn_fps = QSpinBox(self)
        self.spn_fps.setRange(1, 60)
        self.spn_fps.setValue(4)
        self.spn_fps.setSuffix(" fps")
        self.spn_fps.setToolTip("Playback speed of the time-series animation.")
        self.spn_fps.valueChanged.connect(self._on_fps_changed)
        self.chk_loop = QCheckBox("Loop", self)
        self.chk_loop.setChecked(True)
        self.chk_loop.setToolTip("Restart at the first step after the last one.")
        self.chk_autoplay = QCheckBox("Auto-play", self)
        self.chk_autoplay.setChecked(False)  # default off; enable in Settings
        self.chk_autoplay.setToolTip(
            "Start the animation automatically after loading / exporting a series.\n"
            "Default can be changed in Settings → Field Viewer."
        )
        row4.addWidget(self.btn_play)
        row4.addWidget(self.lbl_time)
        row4.addWidget(self.slider_time, 1)
        row4.addWidget(self.lbl_time_info)
        row4.addWidget(self.lbl_sim_time)
        row4.addWidget(self.lbl_motion)
        row4.addStretch(1)
        # fps / Loop / Auto-play moved to Viewer settings; keep the widgets as
        # hidden state holders for the playback code.
        self.spn_fps.hide()
        self.chk_loop.hide()
        self.chk_autoplay.hide()

        self._set_time_visible(False)

        # VTK render window (created lazily so importing vtk is deferred)
        self._host = QWidget()
        self._host_layout = QVBoxLayout(self._host)
        self._host_layout.setContentsMargins(0, 0, 0, 0)
        self.lbl_hint = QLabel(
            "No VTK data. Use “Export project dumps → VTK” (field dumps must cover "
            "a volume/plane, not a single point) or “Open…”."
        )
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.setStyleSheet("color:#666;")
        self._host_layout.addWidget(self.lbl_hint)

        # --- top action bar -------------------------------------------------
        topbar = QHBoxLayout()
        for w in (
            self.btn_open,
            self.btn_export,
            self.btn_video,
            self.btn_popout,
            self.btn_reset,
            self.btn_display,
        ):
            topbar.addWidget(w)
        topbar.addStretch(1)
        self.lbl_field.hide()
        root.addLayout(topbar)
        root.addWidget(self._hline())

        # --- display panel (left) + 3D view (right) -------------------------
        panel_body = QWidget()
        panel_layout = QVBoxLayout(panel_body)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(1)

        data_btn, data_body, data_layout = self._add_section(
            panel_layout, "Data"
        )
        data_form = QFormLayout()
        data_form.setContentsMargins(0, 0, 0, 0)
        data_form.addRow("Show", self.cmb_rep)
        data_form.addRow("Array", self.cmb_array)
        data_form.addRow("View", self.cmb_view_preset)
        data_form.addRow(self.chk_outline)
        data_form.addRow(self.chk_geom, self.sld_geom)
        data_layout.addLayout(data_form)

        slice_btn, slice_body, slice_layout = self._add_section(
            panel_layout, "Slice / Iso"
        )
        self._slice_btn = slice_btn
        self._slice_body = slice_body
        self._slice_controls = QWidget()
        slice_form = QFormLayout(self._slice_controls)
        slice_form.setContentsMargins(0, 0, 0, 0)
        slice_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        slice_form.addRow("Axis", self.cmb_slice_axis)
        # The slider takes the full panel width, its numeric value sits on the
        # next row in the same column as every other input: side by side the
        # value field was pushed out of the panel once it got narrow.
        slice_form.addRow(self.slider_slice)
        slice_form.addRow("Position", self.spn_slice)
        slice_form.addRow(self.chk_planes)
        contours_row = QHBoxLayout()
        contours_row.setContentsMargins(0, 0, 0, 0)
        contours_row.addWidget(self.chk_contours)
        contours_row.addWidget(self.spn_contours)
        contours_row.addStretch(1)
        slice_form.addRow(contours_row)
        slice_layout.addWidget(self._slice_controls)
        self._iso_controls = QWidget()
        iso_form = QFormLayout(self._iso_controls)
        iso_form.setContentsMargins(0, 0, 0, 0)
        iso_form.addRow("Iso-level", self.slider)
        slice_layout.addWidget(self._iso_controls)
        # Streamlines apply to the whole volume and work for both Slice and
        # Iso-surface, so they get their own collapsible section.  The
        # appearance controls are hidden behind its header (the panel is only
        # 200 px wide); the seed count stays with them.
        stream_btn, stream_body, stream_layout = self._add_section(
            panel_layout, "Streamlines", expanded=True
        )
        self._stream_btn = stream_btn
        self.chk_stream = QCheckBox("Enable streamlines")
        self.chk_stream.setToolTip(
            "Field lines integrated through the whole volume (n³ seeds, like\n"
            "ParaView's Stream Tracer). They update with every time/frequency step."
        )
        self.cmb_stream_preset = QComboBox()
        for _name in ("Standard", "Draft", "Publication", "Custom"):
            self.cmb_stream_preset.addItem(_name, _name)
        self.cmb_stream_preset.setToolTip(
            "Appearance preset. Draft = thin lines (fast), Standard = tubes,\n"
            "Publication = slim high-resolution tubes with slight transparency."
        )
        self.cmb_stream_rep = QComboBox()
        self.cmb_stream_rep.addItem("Tube", "tube")
        self.cmb_stream_rep.addItem("Line", "line")
        self.cmb_stream_rep.setToolTip(
            "Tube is shaded and volumetric; Line is faster and clearer for\n"
            "dense seed grids."
        )
        self.spn_stream_radius = QDoubleSpinBox()
        self.spn_stream_radius.setRange(0.01, 5.0)
        self.spn_stream_radius.setDecimals(3)
        self.spn_stream_radius.setSingleStep(0.02)
        self.spn_stream_radius.setSuffix(" % diag")
        self.spn_stream_radius.setToolTip(
            "Tube radius as a percentage of the data diagonal.\n"
            "0.20 % ≈ the previous fixed radius on a 60 mm wide domain."
        )
        self.spn_stream_sides = QSpinBox()
        self.spn_stream_sides.setRange(3, 24)
        self.spn_stream_sides.setSuffix(" sides")
        self.spn_stream_sides.setToolTip(
            "Tube cross-section: 6 = fast, 12–16 = smooth publication quality."
        )
        self.spn_stream_width = QDoubleSpinBox()
        self.spn_stream_width.setRange(0.5, 10.0)
        self.spn_stream_width.setDecimals(1)
        self.spn_stream_width.setSingleStep(0.5)
        self.spn_stream_width.setSuffix(" px")
        self.spn_stream_width.setToolTip(
            "Line width in screen pixels (only for the Line representation)."
        )
        self.spn_stream_opacity = QSpinBox()
        self.spn_stream_opacity.setRange(5, 100)
        self.spn_stream_opacity.setSuffix(" %")
        self.spn_stream_opacity.setToolTip(
            "Line opacity. 60–80 % keeps the field plot visible underneath."
        )
        self.cmb_stream_color = QComboBox()
        self.cmb_stream_color.addItem("Field (selected array)", "field")
        self.cmb_stream_color.addItem("Vector magnitude |v|", "speed")
        self.cmb_stream_color.addItem("Solid colour", "solid")
        self.cmb_stream_color.setToolTip(
            "Field = colour by the selected array; |v| = vector magnitude;\n"
            "Solid = one fixed colour for every line."
        )
        self.btn_stream_color = QPushButton("Colour…")
        self.btn_stream_color.setToolTip("Colour used for the Solid colour mode.")
        self.spn_stream_seeds = QSpinBox()
        self.spn_stream_seeds.setRange(2, 10)
        self.spn_stream_seeds.setSuffix("³")
        self.spn_stream_seeds.setToolTip(
            "Seeds per axis: n³ start points spread through the whole volume.\n"
            "6³ = 216 lines (default, like the ParaView reference); raise for\n"
            "density, lower for faster playback."
        )
        self.btn_stream_reset = QPushButton("Reset appearance")
        self.btn_stream_reset.setToolTip(
            "Restore the standard streamline appearance and seed count.\n"
            "The on/off state is kept."
        )
        stream_form = QFormLayout()
        stream_form.setContentsMargins(0, 0, 0, 0)
        stream_form.addRow(self.chk_stream)
        stream_form.addRow("Preset", self.cmb_stream_preset)
        stream_form.addRow("Show as", self.cmb_stream_rep)
        stream_form.addRow("Radius", self.spn_stream_radius)
        stream_form.addRow("Sides", self.spn_stream_sides)
        stream_form.addRow("Line width", self.spn_stream_width)
        stream_form.addRow("Opacity", self.spn_stream_opacity)
        stream_form.addRow("Colour by", self.cmb_stream_color)
        stream_form.addRow("Colour", self.btn_stream_color)
        stream_form.addRow("Seeds", self.spn_stream_seeds)
        stream_form.addRow(self.btn_stream_reset)
        stream_layout.addLayout(stream_form)

        self.chk_stream.toggled.connect(self._on_stream_toggled)
        self.cmb_stream_preset.currentTextChanged.connect(
            self._on_stream_preset_changed
        )
        for _widget in (
            self.cmb_stream_rep,
            self.spn_stream_radius,
            self.spn_stream_sides,
            self.spn_stream_width,
            self.spn_stream_opacity,
            self.cmb_stream_color,
            self.spn_stream_seeds,
        ):
            signal = (
                _widget.currentIndexChanged
                if isinstance(_widget, QComboBox)
                else _widget.valueChanged
            )
            signal.connect(self._on_stream_option_changed)
        self.btn_stream_color.clicked.connect(self._choose_stream_color)
        self.btn_stream_reset.clicked.connect(self._reset_streamlines)
        self._set_streamline_widgets(DEFAULT_STREAMLINES)
        self._update_stream_controls()

        colour_btn, colour_body, colour_layout = self._add_section(
            panel_layout, "Colour"
        )
        colour_form = QFormLayout()
        colour_form.setContentsMargins(0, 0, 0, 0)
        colour_form.addRow("min", self.spn_min)
        colour_form.addRow("max", self.spn_max)
        # full-width rows (a QFormLayout field column would squeeze them to
        # 104 px and clip the wrapped readout)
        colour_layout.addWidget(self.chk_auto)
        colour_layout.addLayout(colour_form)
        self.lbl_level.setWordWrap(True)
        colour_layout.addWidget(self.lbl_level)
        colour_layout.addWidget(self.chk_log)
        colour_layout.addWidget(self.btn_rescale)

        vec_btn, vec_body, vec_layout = self._add_section(
            panel_layout, "Vectors"
        )
        vec_form = QFormLayout()
        vec_form.setContentsMargins(0, 0, 0, 0)
        vec_form.addRow(self.chk_vectors)
        vec_form.addRow("Size", self.spn_vec_size)
        vec_form.addRow("Max arrows", self.spn_vec_count)
        vec_form.addRow("Hide below", self.spn_vec_min)
        vec_form.addRow(self.chk_vec_norm)
        vec_form.addRow(self.chk_vec_overlay)
        vec_form.addRow(self.chk_vec_random)
        vec_layout.addLayout(vec_form)

        # same label/field width in every section -> all inputs line up
        for _form in (data_form, slice_form, iso_form, stream_form,
                      colour_form, vec_form):
            _uniform_form(_form)

        panel_layout.addStretch(1)
        panel_scroll = QScrollArea()
        panel_scroll.setWidgetResizable(True)
        panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        panel_scroll.setWidget(panel_body)
        panel_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # docked panel: compact; uniform label/field widths (above) keep
        # every row aligned. The pop-out dialog may widen it.
        panel_scroll.setFixedWidth(200)
        self.panel_scroll = panel_scroll

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        self._main_row = main_row
        main_row.addWidget(panel_scroll)
        main_row.addWidget(self._host, 1)
        root.addLayout(main_row, 1)
        root.addWidget(self._hline())
        root.addLayout(row4)
        self._update_representation_controls()
        self.set_layout_mode(self._layout_mode)

        self._set_time_visible(False)

    def _set_time_visible(self, on: bool) -> None:
        self._time_controls_on = on
        for w in (
            self.lbl_time,
            self.slider_time,
            self.btn_play,
            self.lbl_time_info,
            self.lbl_sim_time,
            self.lbl_motion,
        ):
            w.setVisible(on)

    @staticmethod
    def _hline() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    def _add_section(self, parent_layout, title: str, expanded: bool = True):
        """Collapsible display-panel section (bold header + body widget)."""
        if parent_layout.count():
            parent_layout.addWidget(self._hline())
        btn = QToolButton()
        btn.setText(title)
        btn.setCheckable(True)
        btn.setChecked(expanded)
        btn.setAutoRaise(True)
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        font = btn.font()
        font.setBold(True)
        btn.setFont(font)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(2, 0, 0, 4)
        body_layout.setSpacing(2)
        body.setVisible(expanded)

        def _toggle(on: bool) -> None:
            btn.setArrowType(
                Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow
            )
            body.setVisible(on)

        btn.toggled.connect(_toggle)
        parent_layout.addWidget(btn)
        parent_layout.addWidget(body)
        return btn, body, body_layout

    def _build_export_menu(self) -> QMenu:
        """Export menu: action + per-step / step-stride options."""
        menu = QMenu(self)
        act_export = menu.addAction("Export project dumps → VTK")
        act_export.triggered.connect(lambda _checked=False: self.export_and_load())
        menu.addSeparator()
        act_per = menu.addAction("Per-step series")
        act_per.setCheckable(True)
        act_per.setChecked(self.chk_per_step.isChecked())
        act_per.toggled.connect(self.chk_per_step.setChecked)
        stride_menu = menu.addMenu("Step stride")
        stride_group = QActionGroup(stride_menu)
        stride_group.setExclusive(True)
        for n in (1, 2, 4, 8, 16, 25, 50):
            label = "every step" if n == 1 else f"every {n}. step"
            act = stride_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(int(self.spin_stride.value()) == n)
            stride_group.addAction(act)
            act.triggered.connect(
                lambda _checked=False, n=n: self.spin_stride.setValue(n)
            )
        return menu

    def _show_export_menu(self) -> None:
        self._build_export_menu().exec(
            self.btn_export.mapToGlobal(QPoint(0, self.btn_export.height()))
        )

    def _on_rep_changed(self, *_args) -> None:
        self._update_representation_controls()
        self._refresh_display()

    def _update_representation_controls(self) -> None:
        """Show slice/iso controls only for the representations that use them."""
        rep = self.cmb_rep.currentText()
        active = rep in ("Slice", "Iso-surface")
        for widget in (
            getattr(self, "_slice_btn", None),
            getattr(self, "_slice_body", None),
        ):
            if widget is not None:
                widget.setVisible(active)
        if getattr(self, "_slice_controls", None) is not None:
            self._slice_controls.setVisible(rep == "Slice")
        if getattr(self, "_iso_controls", None) is not None:
            self._iso_controls.setVisible(rep == "Iso-surface")
        # first switch to a slice/iso representation opens the section
        if active and not getattr(self, "_slice_was_active", False):
            btn = getattr(self, "_slice_btn", None)
            if btn is not None:
                btn.setChecked(True)
        self._slice_was_active = active

    # -------------------------------------------------------------- layout mode
    def layout_mode(self) -> str:
        """Current layout key: "panel" (B) or "minimal" (C)."""
        return self._layout_mode

    def set_display_geometry(self, value: str) -> None:
        """Restore the Display-popup geometry (base64 QByteArray)."""
        self._display_geometry = str(value or "")

    def set_layout_mode(self, mode: str) -> None:
        """Switch between the docked display panel (B) and minimal UI (C).

        In minimal mode the panel is hidden and the top bar offers a
        ``Display…`` button that opens the same panel in a popup window.
        """
        mode = str(mode or "").lower()
        if mode not in ("panel", "minimal"):
            mode = DEFAULT_VIEWER_LAYOUT
        self._layout_mode = mode
        if mode == "panel" and self._display_dialog is not None:
            self._display_dialog.close()
        btn = getattr(self, "btn_display", None)
        if btn is not None:
            btn.setVisible(mode == "minimal")
        panel = getattr(self, "panel_scroll", None)
        if panel is not None and self._display_dialog is None:
            panel.setVisible(mode == "panel")

    def _open_display_popup(self) -> None:
        """Minimal layout: show the display panel in its own window."""
        if self._layout_mode != "minimal":
            return
        if self._display_dialog is not None:
            self._display_dialog.raise_()
            self._display_dialog.activateWindow()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Field Viewer — display")
        dlg.setMinimumSize(340, 620)
        dlg.resize(380, 760)
        if self._display_geometry:
            try:
                dlg.restoreGeometry(
                    QByteArray(base64.b64decode(self._display_geometry))
                )
                if dlg.width() < 340 or dlg.height() < 620:
                    dlg.resize(380, 760)
            except Exception:  # noqa: BLE001 — corrupt geometry: keep the default
                pass
        else:
            dlg.resize(380, 760)
        layout = QVBoxLayout(dlg)
        self._main_row.removeWidget(self.panel_scroll)
        self.panel_scroll.setMinimumWidth(260)
        self.panel_scroll.setMaximumWidth(420)
        self.panel_scroll.setVisible(True)
        layout.addWidget(self.panel_scroll)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        layout.addWidget(bb)
        self._display_dialog = dlg
        dlg.finished.connect(self._dock_display_panel)
        dlg.show()

    def _dock_display_panel(self, *_args) -> None:
        """Return the panel to the main row after the popup closes."""
        dlg = self._display_dialog
        self._display_dialog = None
        if dlg is not None:
            try:
                self._display_geometry = base64.b64encode(
                    bytes(dlg.saveGeometry())
                ).decode("ascii")
                if callable(self.on_display_geometry_changed):
                    self.on_display_geometry_changed(self._display_geometry)
            except Exception:  # noqa: BLE001 — never block docking
                pass
            layout = dlg.layout()
            if layout is not None:
                layout.removeWidget(self.panel_scroll)
            dlg.deleteLater()
        self._main_row.insertWidget(0, self.panel_scroll)
        self.panel_scroll.setMinimumWidth(200)
        self.panel_scroll.setMaximumWidth(200)
        self.panel_scroll.setVisible(self._layout_mode == "panel")

    def mouse_options(self) -> dict:
        """Current 3D mouse navigation options (JSON-serialisable)."""
        return dict(self._mouse)

    def set_mouse_options(self, options: dict | None = None) -> None:
        """Apply 3D mouse navigation options; live if the view exists."""
        self._mouse = normalise_mouse_options(options)
        self._install_interactor_style()

    def _install_interactor_style(self) -> None:
        """(Re-)install the camera style after an option change."""
        if self._iren is None:
            return
        try:
            vtk = self._vtk or _vtk()
            self._vtk = vtk
            self._iren.SetInteractorStyle(make_mouse_style(vtk, self._mouse))
        except Exception as e:  # noqa: BLE001 — navigation must not break the view
            print(f"Mouse style setup failed: {e}")

    def _configure_render_window(self, render_window) -> None:
        """Hook: configure GL window attributes before ``Initialize()``.

        Subclasses (e.g. the 3D Design mesh overlay) need alpha bit planes
        before the OpenGL context is created; setting them afterwards has no
        effect on the pixel format.
        """
        return None

    def _ensure_render_window(self) -> bool:
        """Create the VTK Qt interactor once; False if unavailable/cannot init."""
        if self._iren is not None:
            return True
        if getattr(self, "_vtk_failed", False):
            return False
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
            self._vtk_failed = True
            self.lbl_hint.setText(
                "VTK 3D view unavailable on the offscreen platform. The converted "
                "files can be opened in ParaView."
            )
            self.lbl_hint.setVisible(True)
            return False
        try:
            vtk = _vtk()
            self._vtk = vtk
            from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

            self.lbl_hint.setVisible(False)
            self._iren = QVTKRenderWindowInteractor(self._host)
            # VTK consumes right-clicks on its own widget, so the containing
            # tab's CustomContextMenu never fires there.  Filter the 3D pane
            # as well, so the same menu is available inside the render window.
            self._iren.installEventFilter(self)
            self._host_layout.addWidget(self._iren, 1)
            self._renderer = vtk.vtkRenderer()
            self._renderer.SetBackground(0.15, 0.15, 0.18)
            render_window = self._iren.GetRenderWindow()
            # GL pixel-format attributes (e.g. alpha bit planes for a
            # transparent overlay layer) must be set before Initialize().
            self._configure_render_window(render_window)
            render_window.AddRenderer(self._renderer)
            self._iren.Initialize()
            # Camera navigation is configurable in Viewer settings → Mouse
            # (default motion factor 5; VTK's 10 is very fast).
            self._install_interactor_style()
            self._draw_geometry()
            return True
        except Exception as e:  # noqa: BLE001 — e.g. no OpenGL in this session
            self._vtk_failed = True
            self.lbl_hint.setText(
                f"VTK 3D view unavailable (OpenGL init failed): {e}\n"
                "The converted files can still be opened in ParaView."
            )
            self.lbl_hint.setVisible(True)
            print(f"VTK viewer disabled: {e}")
            return False

    # --------------------------------------------------------------- actions
    def set_geometry(self, primitives, unit_m: float = 1e-3) -> None:
        """Draw the project's box primitives as translucent ghosts.

        The dumps are in metres (openEMS SI), the primitives in drawing units -
        ``unit_m`` converts them. Only boxes are drawn; polygons/STL are skipped
        (they would need tessellation and the viewer only has the field grid).
        """
        self._geom_prims = [dict(p) for p in (primitives or [])]
        self._geom_unit = float(unit_m or 1e-3)
        # The render window is normally created when a dump is loaded; a
        # geometry-only preview (CAD import before any field) creates it here
        # when the pane is already on screen, otherwise in showEvent() - the
        # primitives are stored either way and drawn there.
        if self._iren is not None or self.isVisible():
            self._ensure_render_window()
        self._apply_geometry_scene()

    def _apply_geometry_scene(self) -> None:
        """Draw the ghosts, the axes/ruler and fit/apply the standard view."""
        if self._renderer is None:
            return  # created later in showEvent()
        self._draw_geometry()
        self._apply_axes()  # corner triad / in-scene triad / coordinate ruler
        if self._geom_actors:
            # geometry may extend far beyond the field dump; fit once so the
            # freshly added ghosts are actually inside the current view. With
            # no field loaded the configured standard view is applied; "keep"
            # has no previous direction to keep here, so it falls back to the
            # isometric default instead of VTK's top view.
            key = self.view_preset()
            if self._grid is not None:
                key = "keep"
            elif key in ("", "keep"):
                key = "iso"
            try:
                self.apply_view_preset(key)
            except Exception:  # noqa: BLE001 - never block on a camera fit
                pass

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Create the 3D pane when the tab becomes visible (e.g. after import)."""
        super().showEvent(event)
        if self._iren is None and not getattr(self, "_vtk_failed", False):
            if self._ensure_render_window():
                self._apply_geometry_scene()

    def _draw_geometry(self, *_args, render: bool = True) -> None:
        if self._renderer is None:
            return
        vtk = self._vtk or _vtk()
        self._vtk = vtk
        for actor in getattr(self, "_geom_actors", []):
            self._renderer.RemoveActor(actor)
        self._geom_actors = []
        if hasattr(self, "chk_geom") and not self.chk_geom.isChecked():
            return
        opacity = 0.12
        if hasattr(self, "sld_geom"):
            opacity = max(0.0, min(1.0, self.sld_geom.value() / 100.0))
        scale = float(getattr(self, "_geom_unit", 1e-3) or 1e-3)
        for pr in getattr(self, "_geom_prims", []):
            actor = self._make_geometry_actor(vtk, pr, scale)
            if actor is None:
                continue
            prop = actor.GetProperty()
            prop.SetOpacity(
                min(1.0, max(opacity, 0.35)) if self._high_contrast else opacity
            )
            prop.SetColor(0.62, 0.70, 0.88)
            prop.SetEdgeVisibility(True)
            if self._high_contrast:
                prop.SetEdgeColor(1.0, 1.0, 1.0)
                prop.SetLineWidth(2.0)
                prop.SetAmbient(0.35)
                prop.SetDiffuse(0.9)
            else:
                prop.SetEdgeColor(0.35, 0.42, 0.60)
                prop.SetLineWidth(1.0)
            prefer_metal_on_top(actor, pr)
            self._renderer.AddActor(actor)
            self._geom_actors.append(actor)

        if not render:
            # Called from _refresh_display: rendering here would present the
            # scene without the field actor (geometry only) and the next Render
            # would show the field again -> the view flickers on every step.
            return
        try:
            if self._iren is not None:
                self._iren.GetRenderWindow().Render()
        except Exception:  # noqa: BLE001 - never block on a redraw
            pass

    def set_high_contrast(self, on: bool) -> None:
        """Toggle high-contrast object styling (visible fill + bright outline).

        A right-click display option, off by default.  Geometry is drawn more
        opaque with a bright 2 px edge and flatter lighting, so objects whose
        fill colour is close to the dark background remain clearly visible.
        """
        self._high_contrast = bool(on)
        try:
            self._draw_geometry()
        except Exception:  # noqa: BLE001 — a display option must never block
            pass
        if self._iren is not None:
            try:
                self._iren.GetRenderWindow().Render()
            except Exception:  # noqa: BLE001
                pass

    def _make_geometry_actor(self, vtk, pr: dict, scale: float):
        """Polydata actor for one project primitive (box/cylinder/sphere/polygon/stl)."""
        kind = str(pr.get("kind", "box") or "box")
        mapper = None
        try:
            if kind == "box":
                s = [float(v) * scale for v in pr["start"]]
                e = [float(v) * scale for v in pr["stop"]]
                lo = [min(s[i], e[i]) for i in range(3)]
                hi = [max(s[i], e[i]) for i in range(3)]
                src = vtk.vtkCubeSource()
                src.SetBounds(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])
                mapper = vtk.vtkPolyDataMapper()
                mapper.SetInputConnection(src.GetOutputPort())
            elif kind == "cylinder":
                s = [float(v) * scale for v in pr["start"]]
                e = [float(v) * scale for v in pr["stop"]]
                radius = abs(float(pr.get("radius", 0.5))) * scale
                line = vtk.vtkLineSource()
                line.SetPoint1(*s)
                line.SetPoint2(*e)
                tube = vtk.vtkTubeFilter()
                tube.SetInputConnection(line.GetOutputPort())
                tube.SetRadius(radius)
                tube.SetNumberOfSides(20)
                tube.CappingOn()
                mapper = vtk.vtkPolyDataMapper()
                mapper.SetInputConnection(tube.GetOutputPort())
            elif kind == "sphere":
                center = pr.get("center")
                if not center:
                    s = [float(v) for v in pr.get("start", [0, 0, 0])]
                    e = [float(v) for v in pr.get("stop", s)]
                    center = [(s[i] + e[i]) / 2.0 for i in range(3)]
                src = vtk.vtkSphereSource()
                src.SetCenter(*[float(v) * scale for v in center])
                src.SetRadius(abs(float(pr.get("radius", 0.5))) * scale)
                src.SetThetaResolution(24)
                src.SetPhiResolution(16)
                mapper = vtk.vtkPolyDataMapper()
                mapper.SetInputConnection(src.GetOutputPort())
            elif kind == "polygon":
                pd = self._polygon_polydata(vtk, pr, scale)
                if pd is None:
                    return None
                mapper = vtk.vtkPolyDataMapper()
                mapper.SetInputData(pd)
            elif kind == "stl":
                pd = self._stl_polydata(vtk, pr, scale)
                if pd is None:
                    return None
                mapper = vtk.vtkPolyDataMapper()
                mapper.SetInputData(pd)
            elif kind == "lumped":
                # Lumped R/L/C component: a small tube along start->stop (or a
                # sphere for a point element) so it is visible in the 3D views.
                s = [float(v) * scale for v in pr.get("start", [0, 0, 0])]
                e = [float(v) * scale for v in pr.get("stop", s)]
                length = math.dist(s, e)
                if length > 1e-15:
                    line = vtk.vtkLineSource()
                    line.SetPoint1(*s)
                    line.SetPoint2(*e)
                    tube = vtk.vtkTubeFilter()
                    tube.SetInputConnection(line.GetOutputPort())
                    # visible marker: 15 % of the element length, capped at
                    # half a drawing unit (0.5 mm for a mm project)
                    radius = max(length * 0.15, 1e-9)
                    if scale > 0:
                        radius = min(radius, 0.5 * scale)
                    tube.SetRadius(radius)
                    tube.SetNumberOfSides(12)
                    tube.CappingOn()
                    mapper = vtk.vtkPolyDataMapper()
                    mapper.SetInputConnection(tube.GetOutputPort())
                else:
                    src = vtk.vtkSphereSource()
                    src.SetCenter(*s)
                    src.SetRadius(max(0.6 * scale, 1e-9))
                    src.SetThetaResolution(16)
                    src.SetPhiResolution(10)
                    mapper = vtk.vtkPolyDataMapper()
                    mapper.SetInputConnection(src.GetOutputPort())
            else:
                return None
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            return actor
        except (KeyError, TypeError, ValueError, IndexError):
            return None

    @staticmethod
    def _stl_polydata(vtk, pr: dict, scale: float):
        """Triangle mesh from ``kind=stl`` (npy triangle array or stl/obj file)."""
        import numpy as np
        from vtk.util import numpy_support as ns

        path = str(pr.get("path") or pr.get("mesh_path") or "")
        stl_path = str(pr.get("stl_path") or "")
        tris = None
        for candidate in (path, stl_path):
            if not candidate or not Path(candidate).exists():
                continue
            try:
                if Path(candidate).suffix.lower() == ".npy":
                    tris = np.load(candidate)
                else:
                    from app.freecad_io.freecad_mesh import load_stl_or_obj_triangles

                    tris = load_stl_or_obj_triangles(candidate)
                break
            except Exception:  # noqa: BLE001 — try the next candidate
                tris = None
        if tris is None:
            return None
        tri = np.asarray(tris, dtype=float).reshape(-1, 3, 3) * float(scale)
        n = len(tri)
        if n == 0:
            return None
        points = tri.reshape(-1, 3)
        cells = np.empty((n, 4), dtype=np.int64)
        cells[:, 0] = 3
        cells[:, 1:] = np.arange(3 * n, dtype=np.int64).reshape(n, 3)
        pd = vtk.vtkPolyData()
        pts = vtk.vtkPoints()
        pts.SetData(ns.numpy_to_vtk(points, deep=True))
        pd.SetPoints(pts)
        ca = vtk.vtkCellArray()
        ca.SetCells(n, ns.numpy_to_vtkIdTypeArray(cells.ravel(), deep=True))
        pd.SetPolys(ca)
        return pd

    @staticmethod
    def _polygon_polydata(vtk, pr: dict, scale: float):
        """Extruded 2-D polygon (``kind=polygon``) as closed polydata."""
        pts2 = pr.get("points") or []
        if len(pts2) < 3:
            return None
        axis = str(pr.get("axis", "z") or "z").lower()
        ai = {"x": 0, "y": 1, "z": 2}.get(axis, 2)
        start = pr.get("start") or [0, 0, 0]
        stop = pr.get("stop") or [0, 0, 0]
        coord = float(pr.get("coord", start[ai]))
        length = float(pr.get("length", float(stop[ai]) - float(start[ai])))
        c0 = (ai + 1) % 3
        c1 = (ai + 2) % 3
        n = len(pts2)
        points = []
        for plane in (coord, coord + length):
            for pt in pts2:
                p = [0.0, 0.0, 0.0]
                p[ai] = plane
                p[c0] = float(pt[0])
                p[c1] = float(pt[1])
                points.append([v * float(scale) for v in p])
        cells = [list(range(n)), list(range(n, 2 * n))]
        for i in range(n):
            j = (i + 1) % n
            cells.append([i, j, n + j, n + i])
        pd = vtk.vtkPolyData()
        vpts = vtk.vtkPoints()
        for p in points:
            vpts.InsertNextPoint(*p)
        pd.SetPoints(vpts)
        ca = vtk.vtkCellArray()
        for cell in cells:
            ca.InsertNextCell(len(cell))
            for idx in cell:
                ca.InsertCellPoint(idx)
        pd.SetPolys(ca)
        return pd

    def set_project_name(self, name: str) -> None:
        """Show the current project name here and in an open pop-out."""
        self.project_name = str(name or "").strip() or "Untitled"
        if hasattr(self, "lbl_project"):
            self.lbl_project.setText(f"Project: {self.project_name}")
        win = getattr(self, "_popout_win", None)
        if win is not None and hasattr(win, "set_project_name"):
            win.set_project_name(self.project_name)

    def set_work_dir(self, work_dir: Path | None) -> None:
        new_dir = Path(work_dir) if work_dir else None
        if new_dir != getattr(self, "work_dir", None):
            # Switching projects/folders must not leave geometry ghosts from
            # the previous dump's work dir on screen.
            self._geom_prims = []
            if self._renderer is not None:
                self._draw_geometry(render=False)  # removes the old actors
            else:
                self._geom_actors = []
        self.work_dir = new_dir
        self._load_workdir_geometry()

    def _load_workdir_geometry(self) -> None:
        """Fallback: reuse the project's primitives when no project is open.

        Opening a dump directly (Field Viewer → Open…) used to leave the
        *Geometry* overlay empty because only the project hands primitives to
        the viewer. Load ``geometry_primitives.json`` / ``project*.json`` from
        the dump's work folder so the ghosts still show.
        """
        if getattr(self, "_geom_prims", None):
            return
        work = getattr(self, "work_dir", None)
        if work is None:
            return
        prims: list[dict] = []
        unit = 1e-3
        try:
            geo = work / "geometry_primitives.json"
            if geo.exists():
                data = json.loads(geo.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    prims = [dict(p) for p in data if isinstance(p, dict)]
        except Exception:  # noqa: BLE001 — fall back to the project JSON
            prims = []
        try:
            candidates = [
                work / "project.fcsim.json",
                work / "project_copy.json",
                work / "project.json",
                # strict layout: JSON is the sibling of the data folder
                work.parent / f"{work.name}.fcsim.json",
            ]
            for proj in candidates:
                if not proj.exists():
                    continue
                data = json.loads(proj.read_text(encoding="utf-8"))
                if not prims:
                    prims = [
                        dict(p)
                        for p in (data.get("primitives") or [])
                        if isinstance(p, dict)
                    ]
                drawing = str((data.get("mesh") or {}).get("drawing_unit") or "mm")
                unit = {
                    "m": 1.0,
                    "cm": 1e-2,
                    "mm": 1e-3,
                    "um": 1e-6,
                    "nm": 1e-9,
                }.get(drawing, 1e-3)
                # Ruler labels follow the project's drawing unit unless the user
                # picked one explicitly (otherwise a mm PCB shows 0.09 "m").
                if not self._ruler_unit_user and drawing in {
                    u[0] for u in RULER_UNITS
                }:
                    self._axes["ruler_unit"] = drawing
                break
        except Exception:  # noqa: BLE001 — never block loading the dump
            pass
        if prims:
            self._geom_prims = prims
            self._geom_unit = unit
            self._draw_geometry()

    def set_autoplay_default(self, on: bool) -> None:
        """Apply the Settings default for the Auto-play checkbox."""
        self.chk_autoplay.setChecked(bool(on))

    # ----------------------------------------------------------- axes options
    def axes_options(self) -> dict:
        """Current axes display options (JSON-serialisable)."""
        return dict(self._axes)

    def set_axes_options(self, options: dict | None, *, notify: bool = False) -> None:
        """Apply axes options; unknown keys are ignored, missing ones unchanged.

        ``notify=True`` calls :attr:`on_axes_changed` afterwards (used by the
        menus so the main window can persist the choice).
        """
        if isinstance(options, dict):
            for key, default in DEFAULT_AXES.items():
                if key not in options:
                    continue
                value = options[key]
                try:
                    if key in ("show", "corner", "labels", "ruler", "origin_back"):
                        value = bool(value)
                    elif key == "ruler_unit":
                        value = str(value)
                        if value not in {u[0] for u in RULER_UNITS}:
                            value = "mm"
                    elif key == "ruler_labels":
                        current = dict(self._axes.get("ruler_labels", {}) or {})
                        incoming = value if isinstance(value, dict) else {}
                        value = {
                            axis: bool(incoming.get(axis, current.get(axis, True)))
                            for axis in ("x", "y", "z")
                        }
                    elif key == "xy_ruler_z_offset":
                        value = max(-200.0, min(200.0, float(value)))
                    elif key == "font":
                        value = max(6, min(72, int(value)))
                    elif key == "scale":
                        value = float(value)
                        if not math.isfinite(value) or value <= 0.0:
                            value = 1.0
                    else:
                        value = default
                except (TypeError, ValueError):
                    value = default
                self._axes[key] = value
        self._apply_axes()
        if notify and callable(self.on_axes_changed):
            self.on_axes_changed(self.axes_options())

    def set_axes_option(self, key: str, value) -> None:
        """Change one axes option and notify (menu slots)."""
        if key == "ruler_unit":
            self._ruler_unit_user = True
        self.set_axes_options({key: value}, notify=True)

    def _set_ruler_label_visible(self, axis: str, on: bool) -> None:
        """Show/hide the numeric labels of one ruler axis only."""
        labels = dict(self._axes.get("ruler_labels", {}) or {})
        if axis not in ("x", "y", "z"):
            return
        labels[axis] = bool(on)
        self.set_axes_options({"ruler_labels": labels}, notify=True)

    def _apply_axes(self) -> None:
        """Re-render the triad / corner marker / ruler with the current options."""
        if self._renderer is None:
            return
        if self._grid is not None:
            self._refresh_display()
            return
        # Geometry-only scene (imported CAD, no field dump loaded): there is no
        # _refresh_display path, so apply the axes/ruler here - otherwise the
        # configured defaults (corner triad, ruler, labels) never showed up.
        vtk = self._vtk or _vtk()
        self._vtk = vtk
        if self._axes["show"] and not self._axes["corner"]:
            self._add_orientation_triad(vtk)
        elif getattr(self, "_triad_actor", None) is not None:
            self._renderer.RemoveActor(self._triad_actor)
            self._triad_actor = None
        if self._axes["ruler"]:
            self._add_coordinate_ruler(vtk)
        else:
            for attr in ("_ruler_actor", "_z_ruler_actor"):
                actor = getattr(self, attr, None)
                if actor is not None:
                    self._renderer.RemoveActor(actor)
                    setattr(self, attr, None)
        self._update_corner_marker()
        if self._iren is not None:
            try:
                self._iren.GetRenderWindow().Render()
            except Exception:  # noqa: BLE001
                pass

    def _update_corner_marker(self) -> None:
        """Small, fixed-size orientation axes in the lower-left corner."""
        if self._iren is None:
            return
        show = bool(self._axes["show"] and self._axes["corner"])
        if show and self._marker is None:
            try:
                vtk = self._vtk or _vtk()
                self._vtk = vtk
                axes = vtk.vtkAxesActor()
                axes.SetTotalLength(1.0, 1.0, 1.0)
                widget = vtk.vtkOrientationMarkerWidget()
                widget.SetOrientationMarker(axes)
                widget.SetInteractor(self._iren)
                configure_marker_widget(widget)
                self._marker_axes, self._marker = axes, widget
            except Exception as e:  # noqa: BLE001 — no GL: keep the scene usable
                print(f"Corner marker unavailable: {e}")
                self._marker = None
                self._marker_axes = None
                return
        if self._marker is None:
            return
        if show:
            self._marker.SetEnabled(1)
            style_axes_triad(
                self._marker_axes,
                show_labels=bool(self._axes["labels"]),
                font_size=int(self._axes["font"]),
            )
            # the marker ignores SetTotalLength (it normalises the actor), so
            # the size preset scales its viewport instead
            try:
                factor = max(0.5, min(8.0, float(self._axes["scale"])))
                width = min(0.45, 0.16 * math.sqrt(factor))
                self._marker.SetViewport(0.0, 0.0, width, width * 1.375)
            except Exception:  # noqa: BLE001 — older VTK / no render window
                pass
        else:
            self._marker.SetEnabled(0)

    def _scene_bounds(self):
        """Bounds of what is on screen: the field grid, else the geometry ghosts."""
        if self._grid is not None:
            b = self._grid.GetBounds()
            if b[0] <= b[1]:
                return b
        actors = getattr(self, "_geom_actors", [])
        if not actors:
            return None
        bounds = [1e308, -1e308, 1e308, -1e308, 1e308, -1e308]
        for actor in actors:
            try:
                b = actor.GetBounds()
            except Exception:  # noqa: BLE001 - skip actors without bounds
                continue
            if not b or len(b) != 6 or b[0] > b[1]:
                continue
            bounds[0] = min(bounds[0], b[0])
            bounds[1] = max(bounds[1], b[1])
            bounds[2] = min(bounds[2], b[2])
            bounds[3] = max(bounds[3], b[3])
            bounds[4] = min(bounds[4], b[4])
            bounds[5] = max(bounds[5], b[5])
        if bounds[0] > bounds[1]:
            return None
        return tuple(bounds)

    def _add_orientation_triad(self, vtk) -> None:
        """In-scene triad at the near (or far) bbox corner, sized to the scene."""
        if self._renderer is None:
            return
        if getattr(self, "_triad_actor", None) is not None:
            self._renderer.RemoveActor(self._triad_actor)
            self._triad_actor = None
        bounds = self._scene_bounds()
        if bounds is None:
            return
        axes = vtk.vtkAxesActor()
        length = axes_total_length(bounds, self._axes["scale"])
        axes.SetTotalLength(length, length, length)
        origin = (bounds[0], bounds[2], bounds[4])
        if self._axes.get("origin_back"):
            try:
                cam = self._renderer.GetActiveCamera().GetPosition()
                corners = [
                    (bounds[i], bounds[j], bounds[k])
                    for i in (0, 1)
                    for j in (2, 3)
                    for k in (4, 5)
                ]
                origin = max(
                    corners,
                    key=lambda p: (
                        (p[0] - cam[0]) ** 2
                        + (p[1] - cam[1]) ** 2
                        + (p[2] - cam[2]) ** 2
                    ),
                )
            except Exception:  # noqa: BLE001 — keep the near corner
                pass
        axes.SetPosition(*origin)
        style_axes_triad(
            axes,
            show_labels=bool(self._axes["labels"]),
            font_size=int(self._axes["font"]),
        )
        self._renderer.AddActor(axes)
        self._triad_actor = axes

    def _add_coordinate_ruler(self, vtk) -> None:
        """Numeric x/y/z ticks around the dataset bounds (vtkCubeAxesActor).

        ``xy_ruler_z_offset`` shifts the x/y rulers along z, as a percentage of
        the z range.  The z ruler is a separate actor and stays at the original
        bounds, so the horizontal rulers can be pulled away from the origin
        corner without moving the z scale.
        """
        for attr in ("_ruler_actor", "_z_ruler_actor"):
            actor = getattr(self, attr, None)
            if actor is not None:
                self._renderer.RemoveActor(actor)
                setattr(self, attr, None)
        bounds = self._scene_bounds()
        if bounds is None:
            return
        unit_key = str(self._axes.get("ruler_unit", "mm") or "mm")
        unit_label, unit_factor = next(
            ((label, factor) for key, label, factor in RULER_UNITS if key == unit_key),
            ("mm", 1e3),
        )
        font = int(self._axes["font"])
        titles = (
            f"x [{unit_label}]",
            f"y [{unit_label}]",
            f"z [{unit_label}]",
        )
        label_vis = dict(self._axes.get("ruler_labels", {}) or {})
        b = bounds
        dx, dy, dz = b[1] - b[0], b[3] - b[2], b[5] - b[4]
        diag = math.sqrt(dx * dx + dy * dy + dz * dz)
        eps = max(diag * 1e-6, 1e-12)
        flat_x = abs(dx) <= eps
        flat_y = abs(dy) <= eps
        flat_z = abs(dz) <= eps
        try:
            offset = float(self._axes.get("xy_ruler_z_offset", 0.0) or 0.0)
        except (TypeError, ValueError):
            offset = 0.0
        shift = offset / 100.0 * dz

        def _style(cube, axes) -> None:
            for axis in axes:
                color = AXIS_COLORS[("X", "Y", "Z").index(axis)]
                label = getattr(cube, f"Get{axis}AxesLabelProperty")()
                label.SetColor(0.9, 0.9, 0.9)
                label.SetFontSize(font)
                caption = getattr(cube, f"Get{axis}AxesTitleProperty")()
                caption.SetColor(0.9, 0.9, 0.9)
                caption.SetFontSize(font)
                getattr(cube, f"Get{axis}AxesLinesProperty")().SetColor(*color)

        def _hide_axis(cube, axis) -> None:
            """Hide one axis completely (visibility last: VTK re-enables it)."""
            for suffix in (
                "AxisTitleVisibility",
                "AxisLabelVisibility",
                "AxisTickVisibility",
                "AxisMinorTickVisibility",
            ):
                try:
                    getattr(cube, f"Set{axis}{suffix}")(False)
                except Exception:  # noqa: BLE001 — older VTK
                    pass
            try:
                getattr(cube, f"Set{axis}Title")("")
            except Exception:  # noqa: BLE001
                pass
            try:
                getattr(cube, f"Set{axis}AxisVisibility")(False)
            except Exception:  # noqa: BLE001
                pass

        def _fly(cube) -> None:
            if self._axes.get("origin_back"):
                cube.SetFlyModeToFurthestTriad()
            else:
                cube.SetFlyModeToClosestTriad()

        # z-only actor at the original bounds.  A planar dump (dz ~ 0) must not
        # draw a z ruler: VTK would auto-scale numerical noise to "(x10^-6)".
        if not flat_z:
            zcube = vtk.vtkCubeAxesActor()
            zcube.SetBounds(b)
            zcube.SetZAxisRange(b[4] * unit_factor, b[5] * unit_factor)
            zcube.SetCamera(self._renderer.GetActiveCamera())
            _fly(zcube)
            zcube.SetUseTextActor3D(False)
            zcube.SetPickable(False)
            _hide_axis(zcube, "X")
            _hide_axis(zcube, "Y")
            zcube.SetZTitle(titles[2])
            zcube.SetZAxisVisibility(True)
            zcube.SetZAxisTickVisibility(True)
            zcube.SetZAxisMinorTickVisibility(True)
            zcube.SetZAxisLabelVisibility(bool(label_vis.get("z", True)))
            zcube.SetZLabelFormat("%.4g")
            _style(zcube, ("Z",))
            self._renderer.AddActor(zcube)
            self._z_ruler_actor = zcube

        # x/y ruler: same x/y extents, translated along z.
        xy_bounds = (b[0], b[1], b[2], b[3], b[4] + shift, b[5] + shift)
        cube = vtk.vtkCubeAxesActor()
        cube.SetBounds(xy_bounds)
        # Scale only the numeric ranges; the box itself stays attached to the
        # simulation bounds.  VTK then draws the labels in the chosen unit.
        cube.SetXAxisRange(b[0] * unit_factor, b[1] * unit_factor)
        cube.SetYAxisRange(b[2] * unit_factor, b[3] * unit_factor)
        cube.SetCamera(self._renderer.GetActiveCamera())
        _fly(cube)
        cube.SetUseTextActor3D(False)
        cube.SetPickable(False)
        _hide_axis(cube, "Z")
        for axis in ("X", "Y"):
            if (axis == "X" and flat_x) or (axis == "Y" and flat_y):
                _hide_axis(cube, axis)
                continue
            getattr(cube, f"Set{axis}Title")(titles[("X", "Y").index(axis)])
            getattr(cube, f"Set{axis}AxisLabelVisibility")(
                bool(label_vis.get(axis.lower(), True))
            )
            getattr(cube, f"Set{axis}LabelFormat")("%.4g")
        _style(cube, ("X", "Y"))
        self._renderer.AddActor(cube)
        self._ruler_actor = cube

    def build_views_menu(self, parent=None) -> QMenu:
        """Standard-view submenu (same presets as the View combo/View menu)."""
        menu = QMenu("Views", parent)
        group = QActionGroup(menu)
        group.setExclusive(True)
        current = self.view_preset()
        for key, label in VIEW_PRESETS:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(key == current)
            group.addAction(act)
            act.triggered.connect(
                lambda _checked=False, key=key: self._apply_view_from_menu(key)
            )
        return menu

    def _apply_view_from_menu(self, key: str) -> None:
        """Apply a standard view picked in the context menu and remember it."""
        self.set_view_default(key)
        self._on_view_preset_changed()

    def build_axes_menu(self, parent=None) -> QMenu:
        """Menu with all axes options (View menu + Field Viewer context menu).

        The check states are refreshed in ``aboutToShow`` so several menus can
        show the same state and stay in sync.
        """
        menu = QMenu("Axes", parent)
        acts: dict = {}
        acts["show"] = menu.addAction("Show axes")
        acts["corner"] = menu.addAction("Corner marker (ParaView style)")
        acts["corner"].setToolTip(
            "Show a small orientation triad in the lower-left corner instead of "
            "the triad inside the 3D scene."
        )
        acts["origin_back"] = menu.addAction("Origin behind object")
        acts["origin_back"].setToolTip(
            "Place the in-scene triad on the far bounding-box corner (away from "
            "the camera), so it sits behind the field instead of in front of it."
        )
        acts["labels"] = menu.addAction("Labels X/Y/Z")
        acts["labels"].setToolTip("Show the X/Y/Z captions on the axes (colours stay).")
        acts["ruler"] = menu.addAction("Coordinate ruler (numbers)")
        acts["ruler"].setToolTip(
            "Numeric coordinate ticks around the dataset bounds (unit selectable)."
        )
        unit_menu = menu.addMenu("Ruler units")
        unit_group = QActionGroup(unit_menu)
        unit_group.setExclusive(True)
        self._axes_unit_group = unit_group
        acts["ruler_unit"] = {}
        for key, label, _factor in RULER_UNITS:
            act = unit_menu.addAction(label)
            act.setCheckable(True)
            unit_group.addAction(act)
            acts["ruler_unit"][key] = act
            act.triggered.connect(
                lambda _checked=False, k=key: self.set_axes_option("ruler_unit", k)
            )
        labels_menu = menu.addMenu("Ruler labels")
        acts["ruler_labels"] = {}
        for axis in ("x", "y", "z"):
            act = labels_menu.addAction(f"{axis.upper()} labels")
            act.setCheckable(True)
            acts["ruler_labels"][axis] = act
            act.toggled.connect(
                lambda on, a=axis: self._set_ruler_label_visible(a, bool(on))
            )
        zoff_menu = menu.addMenu("XY ruler Z offset")
        zoff_menu.setToolTip(
            "Move the x/y rulers along the z axis (percentage of the z range).\n"
            "The z ruler itself stays at its original position."
        )
        zoff_group = QActionGroup(zoff_menu)
        zoff_group.setExclusive(True)
        self._axes_zoff_group = zoff_group
        acts["xy_ruler_z_offset"] = {}
        for value in (-50.0, -20.0, -10.0, 0.0, 10.0, 20.0, 50.0):
            text = f"{value:+.0f} %" if value else "0 %"
            act = zoff_menu.addAction(text)
            act.setCheckable(True)
            zoff_group.addAction(act)
            acts["xy_ruler_z_offset"][value] = act
            act.triggered.connect(
                lambda _checked=False, v=value: self.set_axes_option(
                    "xy_ruler_z_offset", v
                )
            )
        menu.addSeparator()
        size_menu = menu.addMenu("Size")
        acts["size"] = {}
        size_group = QActionGroup(size_menu)
        size_group.setExclusive(True)
        self._axes_size_group = size_group  # keep alive (PySide ownership)
        for text, factor in AXES_SCALES:
            act = size_menu.addAction(text)
            act.setCheckable(True)
            size_group.addAction(act)
            acts["size"][factor] = act
        font_menu = menu.addMenu("Label font")
        acts["font"] = {}
        font_group = QActionGroup(font_menu)
        font_group.setExclusive(True)
        self._axes_font_group = font_group  # keep alive (PySide ownership)
        for size in AXES_FONT_SIZES:
            act = font_menu.addAction(f"{size} pt")
            act.setCheckable(True)
            font_group.addAction(act)
            acts["font"][size] = act

        for key in ("show", "corner", "origin_back", "labels", "ruler"):
            act = acts[key]
            act.setCheckable(True)
            act.toggled.connect(lambda on, k=key: self.set_axes_option(k, bool(on)))
        for factor, act in acts["size"].items():
            act.triggered.connect(
                lambda _checked=False, f=factor: self.set_axes_option("scale", f)
            )
        for size, act in acts["font"].items():
            act.triggered.connect(
                lambda _checked=False, s=size: self.set_axes_options(
                    {"font": s, "labels": True}, notify=True
                )
            )

        menu._axes_actions = acts  # type: ignore[attr-defined]
        self._sync_axes_menu(menu)
        menu.aboutToShow.connect(lambda: self._sync_axes_menu(menu))
        return menu

    def _sync_axes_menu(self, menu) -> None:
        acts = getattr(menu, "_axes_actions", None)
        if not acts:
            return
        for key in ("show", "corner", "origin_back", "labels", "ruler"):
            act = acts[key]
            act.blockSignals(True)
            act.setChecked(bool(self._axes[key]))
            act.blockSignals(False)
        for factor, act in acts["size"].items():
            act.blockSignals(True)
            act.setChecked(abs(float(self._axes["scale"]) - factor) < 1e-9)
            act.blockSignals(False)
        for size, act in acts["font"].items():
            act.blockSignals(True)
            act.setChecked(int(self._axes["font"]) == size)
            act.blockSignals(False)
        current_unit = str(self._axes.get("ruler_unit", "mm") or "mm")
        for key, act in acts.get("ruler_unit", {}).items():
            act.blockSignals(True)
            act.setChecked(key == current_unit)
            act.blockSignals(False)
        label_vis = dict(self._axes.get("ruler_labels", {}) or {})
        for axis, act in acts.get("ruler_labels", {}).items():
            act.blockSignals(True)
            act.setChecked(bool(label_vis.get(axis, True)))
            act.blockSignals(False)
        current_zoff = float(self._axes.get("xy_ruler_z_offset", 0.0) or 0.0)
        for value, act in acts.get("xy_ruler_z_offset", {}).items():
            act.blockSignals(True)
            act.setChecked(abs(current_zoff - value) < 1e-9)
            act.blockSignals(False)


    def clear(self, message: str | None = None) -> None:
        """Drop the loaded field/series so no stale scene stays visible.

        Used when a project without field dumps is opened (nothing must be
        shown); the pop-out mirror (if any) is emptied as well.
        """
        self._timer.stop()
        if self.btn_play.isChecked():
            self.btn_play.blockSignals(True)
            self.btn_play.setChecked(False)
            self.btn_play.setText("▶ Play")
            self.btn_play.blockSignals(False)
        self._series, self._series_labels = [], []
        self._series_times, self._series_freqs = [], []
        self._current_path = None
        self._grid = None
        # Geometry ghosts are project-owned too: clearing the field without
        # dropping them left the previous project's PCB/Antenna visible.
        self._geom_prims = []
        self._geom_actors = []
        self._vec_series_ref = None
        self._vec_ref_name = None
        self._slice_pos_exact = None
        self._scalar_name = None
        self._data_range = (0.0, 1.0)
        self._scalar_range = (0.0, 1.0)
        self._robust_min = None
        self._has_vectors = False
        self.lbl_level.setText("")
        self.lbl_time_info.setText("")
        self.lbl_motion.setText("")
        self.lbl_sim_time.setText("")
        self.lbl_field.setText("")
        self.lbl_field.setToolTip("No field loaded.")
        self._set_header_detail("")
        self._set_time_visible(False)
        self.btn_video.setEnabled(False)
        self.cmb_array.blockSignals(True)
        self.cmb_array.clear()
        self.cmb_array.blockSignals(False)
        self._update_vector_controls()
        if self._renderer is not None:
            try:
                self._renderer.RemoveAllViewProps()
                self._draw_geometry()
                self._update_corner_marker()  # no data -> hide the corner axes
                self._iren.GetRenderWindow().Render()
            except Exception:  # noqa: BLE001
                pass
        if not getattr(self, "_vtk_failed", False):
            if message:
                self.lbl_hint.setText(message)
            self.lbl_hint.setVisible(True)
        win = self._popout_win
        if win is not None and getattr(win, "viewer", None) is not self:
            try:
                win.viewer.clear()
            except Exception:  # noqa: BLE001
                pass

    def popout(self) -> None:
        """Open a separate window mirroring the view (toggle: click again to close)."""
        if self._popout_win is not None:
            self.restore_from_popout()
            return
        win = FieldViewerWindow(self)
        self._popout_win = win
        win.show()
        win.raise_()
        win.activateWindow()
        self.btn_popout.setText("Dock view")

    def restore_from_popout(self) -> None:
        """Close the pop-out window (the tab view is unaffected)."""
        win = self._popout_win
        if win is None:
            return
        self._popout_win = None
        win.source = None  # avoid a re-entrant closeEvent
        try:
            win.close()
        except Exception:  # noqa: BLE001
            pass
        self.btn_popout.setText("Pop out")

    def _context_menu_allowed(self, pos) -> bool:
        """The menu is available on the controls *and* inside the 3D pane."""
        return True

    def _forward_vtk_button(self, name: str, press: bool, event) -> None:
        """Forward a Qt gesture as a VTK button event (middle-button pan)."""
        if self._iren is None:
            return
        try:
            pos = event.position().toPoint()
        except AttributeError:  # older Qt
            pos = event.pos()
        try:
            self._iren.SetEventPosition(int(pos.x()), int(pos.y()))
            suffix = "Press" if press else "Release"
            getattr(self._iren, f"{name}Button{suffix}Event")()
        except Exception:  # noqa: BLE001 — never break navigation
            pass

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt API
        """Mouse remapping, right-drag pan/zoom and the context-menu click.

        VTK does not reliably dispatch right-button events to Python overrides
        of the interaction style, so the remapping is done here: a right drag
        is forwarded as VTK's built-in middle-button pan; a right click without
        a drag still opens the context menu on release.  ``swap_buttons`` maps
        left/middle to the opposite gesture the same way.
        """
        if obj is getattr(self, "_iren", None):
            etype = event.type()
            mouse = getattr(self, "_mouse", {}) or {}
            press = etype == QEvent.Type.MouseButtonPress
            release = etype == QEvent.Type.MouseButtonRelease
            if (press or release) and bool(mouse.get("swap_buttons", False)):
                # In the 3D Design viewer a left click is the tape-measure
                # pick; keep the default mapping while measure mode is active.
                if not bool(getattr(self, "_measure_mode", False)):
                    btn = event.button()
                    if btn in (
                        Qt.MouseButton.LeftButton,
                        Qt.MouseButton.MiddleButton,
                    ):
                        target = (
                            "Middle"
                            if btn == Qt.MouseButton.LeftButton
                            else "Left"
                        )
                        self._forward_vtk_button(target, press, event)
                        return True
            if press and event.button() == Qt.MouseButton.RightButton:
                try:
                    self._right_menu_press = event.position().toPoint()
                except AttributeError:  # older Qt
                    self._right_menu_press = event.pos()
                if bool(mouse.get("right_button_pan", True)):
                    self._forward_vtk_button("Middle", True, event)
                    return True
                return False  # let VTK start the right-drag zoom
            if release and event.button() == Qt.MouseButton.RightButton:
                start = getattr(self, "_right_menu_press", None)
                self._right_menu_press = None
                right_pan = bool(mouse.get("right_button_pan", True))
                if right_pan:
                    self._forward_vtk_button("Middle", False, event)
                if start is not None:
                    try:
                        end = event.position().toPoint()
                    except AttributeError:  # older Qt
                        end = event.pos()
                    # Suppress a platform ContextMenu event that may follow the
                    # release; a drag must not open the menu either.
                    self._right_release_seen = True
                    QTimer.singleShot(
                        400,
                        lambda: setattr(self, "_right_release_seen", False),
                    )
                    if (end - start).manhattanLength() <= 5:
                        QTimer.singleShot(
                            0,
                            lambda p=end: self._show_context_menu(p, source=obj),
                        )
                return True if right_pan else False
            if etype == QEvent.Type.ContextMenu:
                # Some platforms send only ContextMenu (no press/release
                # pair). Do not open twice after a normal right-click.
                if getattr(self, "_right_release_seen", False):
                    return True
                self._show_context_menu(event.pos(), source=obj)
                return True
        return super().eventFilter(obj, event)

    def _show_context_menu(self, pos, source=None) -> None:
        menu = QMenu(self)
        menu.addAction("Open…", self._on_open)
        menu.addAction("Export project dumps → VTK", self.export_and_load)
        menu.addAction("Export video…", self._on_export_video)
        menu.addAction("Export animated GIF…", self._on_export_gif)
        menu.addSeparator()
        if self._popout_win is not None:
            menu.addAction("Dock view (close pop-out)", self.restore_from_popout)
        elif self.btn_popout.isHidden():
            win = self.window()
            if isinstance(win, FieldViewerWindow):
                menu.addAction("Close pop-out window", win.close)
        else:
            menu.addAction("Pop out view", self.popout)
        menu.addAction("Reset view", self._reset_camera)
        act_contrast = menu.addAction("High contrast objects")
        act_contrast.setCheckable(True)
        act_contrast.setChecked(bool(self._high_contrast))
        act_contrast.setToolTip(
            "Draw the geometry more opaque with a bright outline so it stands\n"
            "out against the dark background. Optional; off by default."
        )
        act_contrast.toggled.connect(self.set_high_contrast)
        menu.addSeparator()
        menu.addMenu(self.build_views_menu(menu))
        menu.addMenu(self.build_axes_menu(menu))
        widget = source if source is not None else self.sender()
        if not isinstance(widget, QWidget):
            widget = self
        menu.exec(widget.mapToGlobal(pos))

    def _copy_state_from(self, other: "VtkFieldViewer") -> None:
        """Mirror representation / colour / playback settings (for the pop-out)."""
        for setter in (
            lambda: self.cmb_rep.setCurrentText(other.cmb_rep.currentText()),
            lambda: self.chk_outline.setChecked(other.chk_outline.isChecked()),
            lambda: self.chk_per_step.setChecked(other.chk_per_step.isChecked()),
            lambda: self.spin_stride.setValue(other.spin_stride.value()),
            lambda: self.cmb_slice_axis.setCurrentText(other.cmb_slice_axis.currentText()),
            lambda: self.slider_slice.setValue(other.slider_slice.value()),
            lambda: self.spn_slice.setValue(other.spn_slice.value()),
            lambda: self.slider.setValue(other.slider.value()),
            lambda: self.chk_log.setChecked(other.chk_log.isChecked()),
            lambda: self.chk_auto.setChecked(other.chk_auto.isChecked()),
            lambda: self.spn_min.setValue(other.spn_min.value()),
            lambda: self.spn_max.setValue(other.spn_max.value()),
            lambda: self.spn_fps.setValue(other.spn_fps.value()),
            lambda: self.chk_loop.setChecked(other.chk_loop.isChecked()),
            lambda: self.chk_autoplay.setChecked(other.chk_autoplay.isChecked()),
            lambda: self.set_axes_options(other.axes_options()),
            lambda: self.set_mouse_options(other.mouse_options()),
            lambda: self.set_vector_options(other.vector_options()),
            lambda: self.set_streamline_options(other.streamline_options()),
            lambda: self.set_color_scale_options(other.color_scale_options()),
            lambda: self.set_bar_title_options(other.bar_title_options()),
            lambda: self.set_color_bar_options(other.color_bar_options()),
            lambda: self.set_layout_mode(other.layout_mode()),
            lambda: self.set_view_default(other.view_preset()),
            lambda: self.set_high_contrast(other._high_contrast),
        ):
            try:
                setter()
            except Exception:  # noqa: BLE001
                pass
        # the array combo is rebuilt on load; select the same entry by name
        i = self.cmb_array.findText(other.cmb_array.currentText())
        if i >= 0:
            self.cmb_array.setCurrentIndex(i)
        if self._series and other._series:
            self.slider_time.setValue(min(other.slider_time.value(), len(self._series) - 1))

    def _on_open(self) -> None:
        start = ""
        if self.work_dir:
            vtk_dir = Path(self.work_dir) / "vtk"
            # the converted files live in <project>/vtk — start there
            start = str(vtk_dir if vtk_dir.is_dir() else self.work_dir)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open VTK file",
            start,
            "VTK files (*.vtr *.vti *.vtu *.vtp *.vts *.vtk);;All files (*)",
        )
        if path:
            self.load(path)

    def _latest_dump_dir(self) -> Path | None:
        """Run folder that actually contains field dumps.

        ``script_gen`` writes each run into ``simulation/`` or a unique
        ``simulation_<timestamp>/`` (when the old folder already exists).  The
        exporter used to look only at ``simulation/``, so every later run
        appeared to have "no field data".  Use the newest folder that has
        ``*.h5`` dumps.
        """
        if self.work_dir is None:
            return None
        base = Path(self.work_dir)
        candidates = [base / "simulation"]
        candidates += sorted(
            base.glob("simulation_*"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for folder in candidates:
            if folder.is_dir() and any(folder.glob("*.h5")):
                return folder
        return None

    def export_and_load(self) -> None:
        if not self.btn_export.isEnabled():  # an export is already running
            return
        if self.work_dir is None:
            QMessageBox.information(self, "VTK", "No work directory set.")
            return
        sim = self._latest_dump_dir()
        if sim is None:
            QMessageBox.information(
                self,
                "VTK",
                f"No simulation field dump found in:\n{self.work_dir}\n\n"
                "Run a simulation with field dumps enabled first.",
            )
            return

        per_step = self.chk_per_step.isChecked()
        stride = self.spin_stride.value() if per_step else 1
        progress = QProgressDialog("Converting field dumps to VTK…", "Cancel", 0, 100, self)
        progress.setWindowTitle("Generate VTK")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        def _on_progress(done: int, total: int, text: str) -> None:
            if total > 0:
                progress.setMaximum(total)
            progress.setValue(done)
            progress.setLabelText(text)
            # keeps the window repainting / the Cancel button clickable
            QApplication.processEvents()

        self.btn_export.setEnabled(False)
        cancelled = False
        try:
            written = export_dumps_to_vtk(
                sim,
                per_step=per_step,
                progress=_on_progress,
                should_stop=progress.wasCanceled,
                step_stride=stride,
            )
            cancelled = progress.wasCanceled()
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "VTK", f"VTK export failed:\n{e}")
            return
        finally:
            progress.close()
            self.btn_export.setEnabled(True)

        if cancelled:
            print(f"VTK: export cancelled — {len(written)} file(s) written")
        if not written:
            if cancelled:
                return
            QMessageBox.information(
                self,
                "VTK",
                "No usable field dump found. Check that the dumps cover a volume/plane "
                "(Edit Dump: start≠stop), then run again.",
            )
            return
        self.load(written[0])
        print(f"VTK: converted {len(written)} file(s) -> {written[0].parent}")
        # one .vtr per *stored* sample: make it obvious why a dump has few steps
        for h5 in sorted(sim.glob("*.h5")):
            if "nf2ff" in h5.name.lower():
                continue
            print(f"VTK:   {h5.stem}: {count_dump_steps(h5)} stored step(s) in {h5.name}")

    def load(self, path: Path | str) -> None:
        path = Path(path)
        if not path.exists():
            return
        self._detect_series(path)
        self._vec_series_ref = None  # a new series needs a new length reference
        self._vec_ref_name = None
        if not self._read_into_grid(path, show_errors=True):
            return
        self._current_path = path
        if path.parent.name.lower() == "vtk":
            # A dump opened directly may come from a different project than the
            # last one; switch the work dir (and drop its stale geometry).
            self.set_work_dir(path.parent.parent)
        elif self.work_dir is None:
            self.work_dir = path.parent
        self._load_workdir_geometry()
        if self._series:
            self.slider_time.blockSignals(True)
            self.slider_time.setRange(0, len(self._series) - 1)
            self.slider_time.setValue(self._series.index(path) if path in self._series else 0)
            self.slider_time.blockSignals(False)
            self._set_time_visible(True)
        else:
            self._set_time_visible(False)
        self.btn_video.setEnabled(bool(self._series) and len(self._series) > 1)
        self._populate_arrays()
        if self._ensure_render_window():
            self._refresh_display()
            self.apply_view_preset()  # standard view (default: fit, keep direction)
        self._log_title(path)
        self._autoplay()

    def _read_into_grid(self, path: Path, show_errors: bool = False) -> bool:
        vtk = _vtk()
        try:
            reader = _make_reader(vtk, path)
            reader.SetFileName(str(path))
            reader.Update()
            grid = reader.GetOutput()
        except Exception as e:  # noqa: BLE001
            if show_errors:
                QMessageBox.warning(self, "VTK", f"Could not read {path.name}:\n{e}")
            return False
        if grid is None or grid.GetNumberOfPoints() == 0:
            if show_errors:
                QMessageBox.warning(
                    self, "VTK", f"Could not read {path.name} ({path.suffix} unsupported?)."
                )
            return False
        self._grid = grid
        self._vtk = vtk
        # unit from the dump name (E -> V/m, H -> A/m …), shown in the colour bar
        self._field_unit = field_unit(path.stem)
        tip = "Field array to display (point or cell data)."
        if self._field_unit:
            tip += f"\nValues are in {self._field_unit}."
        self.cmb_array.setToolTip(tip)
        # new bounds -> a previously typed slice position is meaningless
        self._slice_pos_exact = None
        self._sync_slice_readout()
        return True

    def _detect_series(self, path: Path) -> None:
        self._series, self._series_labels = [], []
        self._series_times, self._series_freqs = [], []
        m = re.match(r"(?P<base>.+)_(?P<idx>\d+)$", path.stem)
        if not m:
            return
        base = m.group("base")
        files = sorted(
            path.parent.glob(base + "_*.vtr"),
            key=lambda p: int(re.search(r"_(\d+)$", p.stem).group(1)),  # noqa: B023
        )
        if len(files) < 2:
            return
        labels = [p.stem.rsplit("_", 1)[1] for p in files]
        sidecar = path.parent / (base + ".series.json")
        times: list[float | None] = []
        freqs: list[float | None] = []
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text(encoding="utf-8"))
                labels = list(data.get("labels", labels))
                times = list(data.get("times") or [])
                freqs = list(data.get("frequencies") or [])
            except Exception:  # noqa: BLE001
                pass
        self._series = files
        self._series_labels = labels
        n = len(files)
        # keep the lists aligned with the files (sidecars from older exports
        # have no times/frequencies)
        self._series_times = (times + [None] * n)[:n]
        self._series_freqs = (freqs + [None] * n)[:n]

    def _arrays_match_selection(self) -> bool:
        """True when the loaded grid carries exactly the arrays the combo lists.

        Frame changes then only need ``_on_array_changed`` (data range + one
        redraw) instead of rebuilding the array combo, which fires an extra
        refresh per slider step.
        """
        if self._grid is None or self.cmb_array.count() == 0:
            return False
        pd = self._grid.GetPointData()
        cd = self._grid.GetCellData()
        have = {("point", pd.GetArrayName(i)) for i in range(pd.GetNumberOfArrays())}
        have |= {("cell", cd.GetArrayName(i)) for i in range(cd.GetNumberOfArrays())}
        want = {
            tuple(self.cmb_array.itemData(i))
            for i in range(self.cmb_array.count())
            if self.cmb_array.itemData(i)
        }
        return have == want

    def _populate_arrays(self) -> None:
        self.cmb_array.blockSignals(True)
        self.cmb_array.clear()
        if self._grid is not None:
            pd = self._grid.GetPointData()
            cd = self._grid.GetCellData()
            for i in range(pd.GetNumberOfArrays()):
                self.cmb_array.addItem(f"pt:{pd.GetArrayName(i)}", ("point", pd.GetArrayName(i)))
            for i in range(cd.GetNumberOfArrays()):
                self.cmb_array.addItem(f"cell:{cd.GetArrayName(i)}", ("cell", cd.GetArrayName(i)))
            # default: our magnitude, else the first array
            idx = 0
            for i in range(self.cmb_array.count()):
                d = self.cmb_array.itemData(i)
                if d and d[1] == "magnitude":
                    idx = i
                    break
            self.cmb_array.setCurrentIndex(idx if self.cmb_array.count() else -1)
        self.cmb_array.blockSignals(False)
        self._has_vectors = bool(self._vector_array_name())
        self._update_vector_controls()
        self._on_array_changed()

    def _array(self):
        d = self.cmb_array.currentData()
        return d if d else (None, None)

    def _on_array_changed(self, *_a) -> None:
        loc, name = self._array()
        self._scalar_name = name
        if self._grid is None or not name:
            self._data_range = (0.0, 1.0)
            self._robust_min = None
        else:
            data = self._grid.GetPointData() if loc == "point" else self._grid.GetCellData()
            arr = data.GetArray(name)
            rng = arr.GetRange() if arr else (0.0, 1.0)
            self._data_range = (float(rng[0]), float(rng[1]))
            # floor for the log mapping: 1 % of the positive values. A single
            # near-zero cell (or a float32 denormal) must not stretch the table
            # over decades - that is what made every slice look one-coloured.
            self._robust_min = _positive_percentile(arr, 1.0) if arr else None
        self._scalar_range = self._data_range  # compatibility alias
        if self.chk_auto.isChecked():
            # Auto follows the CURRENT frame: full colour use in every frame and
            # a visible change over time (a range over the whole series is
            # dominated by the excitation pulse and flattens everything else)
            self._rescale_to_data()
        else:
            # a manual range belongs to the user: a frame change must not
            # overwrite it (that used to reset the limits on every step)
            self._update_color_widgets()
            self._refresh_display()

    def _rescale_to_data(self, *_a) -> None:
        lo, hi = self._data_range
        if hi <= lo:
            hi = lo * 10.0 if lo > 0 else 1.0
        self._set_color_spins(lo, hi)
        self._update_color_widgets()  # Auto/Log may replace a non-positive min
        self._refresh_display()

    def _set_color_spins(self, lo: float, hi: float) -> None:
        """Write min/max without re-triggering the display (signals blocked)."""
        for spin, value in ((self.spn_min, lo), (self.spn_max, hi)):
            spin.blockSignals(True)
            spin.setValue(float(value))
            spin.blockSignals(False)

    def _on_color_spin_changed(self, *_a) -> None:
        """A typed limit: log needs min > 0, so show the value really used."""
        if not self.chk_auto.isChecked():
            lo, hi = float(self.spn_min.value()), float(self.spn_max.value())
            if self.chk_log.isChecked() and hi > 0 and lo <= 0:
                self._set_color_spins(self._log_floor(hi), hi)
        self._refresh_display()

    def _on_auto_toggled(self, *_a) -> None:
        self._update_color_widgets()
        self._refresh_display()

    def _on_log_toggled(self, *_a) -> None:
        self._update_color_widgets()  # log needs a positive minimum
        self._refresh_display()

    def _update_color_widgets(self) -> None:
        """Keep the min/max fields in step with Auto and Log scale.

        * Auto: the fields show the range that is really used for the current
          frame (data range and, in log mode, the 1 % percentile floor) and are
          read-only.
        * Manual: the fields ARE the range - only a log minimum <= 0 is
          replaced by the percentile floor, so the numbers match the colour
          bar instead of claiming 0.
        """
        auto = self.chk_auto.isChecked()
        log = self.chk_log.isChecked()
        self.spn_min.setEnabled(not auto)
        self.spn_max.setEnabled(not auto)
        self.btn_rescale.setEnabled(not auto)
        hint = (
            "Controlled by Auto - untick it to type a limit."
            if auto
            else "Enter applies. Scientific notation: 1.5e-9 or 1,5e-9."
        )
        log_hint = (
            "\nIn log mode Auto starts at the 1 % percentile of the positive "
            "values; a manual minimum of 0 is replaced by it."
            if log
            else ""
        )
        self.spn_min.setToolTip("Lower colour limit." + log_hint + "\n" + hint)
        self.spn_max.setToolTip("Upper colour limit.\n" + hint)
        if auto or (log and self._color_range()[0] <= 0):
            lo, hi = self._effective_range()
            self._set_color_spins(lo, hi)

    def _color_range(self) -> tuple[float, float]:
        if self.chk_auto.isChecked():
            lo, hi = self._data_range
        else:
            lo, hi = float(self.spn_min.value()), float(self.spn_max.value())
        if hi <= lo:
            hi = lo * 10.0 if lo > 0 else 1.0
        return lo, hi

    def _effective_range(self) -> tuple[float, float]:
        """Colour range actually used by the LUT / mapper.

        log10 is undefined at 0, but field magnitudes normally reach exactly 0
        somewhere. In log mode the lower limit is therefore raised to the 1 %
        percentile of the positive values of the frame (the plain minimum is one
        near-zero cell and would squeeze the visible range into a few colours);
        values below it are drawn in the lowest colour. A manual limit is kept —
        only a minimum of 0 is substituted.
        """
        lo, hi = self._color_range()
        if hi <= lo:
            hi = lo * 10.0 if lo > 0 else 1.0
        if self.chk_log.isChecked() and hi > 0:
            if lo <= 0 or self.chk_auto.isChecked():
                lo = max(lo, self._log_floor(hi))
            if lo >= hi:
                lo = hi * 1e-6
        return lo, hi

    def _log_floor(self, hi: float) -> float:
        """Lower limit for the log mapping (Auto and a manual minimum of 0).

        The smallest positive value alone is a bad floor: real dumps contain
        near-zero cells and float32 denormals (5e-35 next to 35), which turn the
        table into 30 decades of noise and paint the whole slice one colour.
        ``_robust_min`` (1 % percentile of the positive values of this frame) is
        used instead, never below ``1e-7 * maximum`` (float32 resolution).
        """
        pos = self._robust_min
        return max(pos, hi * 1e-7) if pos else hi * 1e-6

    def _lookup_table(self):
        vtk = self._vtk
        lut = vtk.vtkLookupTable()
        colors = colormap_colors(
            str(self._color_scale.get("cmap", DEFAULT_COLOR_SCALE["cmap"])),
            int(self._color_scale.get("steps", DEFAULT_COLOR_SCALE["steps"])),
            bool(self._color_scale.get("reverse", False)),
        )
        if colors:
            fill_lookup_table(lut, colors)
        else:  # defensive: keep the old fixed blue -> red table
            lut.SetNumberOfTableValues(256)
            lut.SetHueRange(0.667, 0.0)
            lut.Build()
        lo, hi = self._effective_range()
        if self.chk_log.isChecked() and lo > 0 and hi > lo:
            lut.SetScaleToLog10()
        else:
            lut.SetScaleToLinear()
        lut.SetTableRange(lo, hi)
        lut.Build()
        return lut

    def _on_slider(self, _v: int) -> None:
        self._refresh_display()

    def _iso_value(self) -> float:
        lo, hi = self._color_range()
        return lo + (hi - lo) * (self.slider.value() / 1000.0)

    # ------------------------------------------------------------ slice plane
    def _slice_bounds(self) -> tuple[float, float] | None:
        """``(lo, hi)`` of the current slice axis from the loaded grid."""
        if self._grid is None:
            return None
        b = self._grid.GetBounds()  # x0,x1,y0,y1,z0,z1
        return {
            "X": (b[0], b[1]),
            "Y": (b[2], b[3]),
        }.get(self.cmb_slice_axis.currentText(), (b[4], b[5]))

    def _slice_pos_m(self) -> float | None:
        """Slice position along the current axis in metres (None = no grid)."""
        if self._slice_pos_exact is not None:
            return self._slice_pos_exact
        rng = self._slice_bounds()
        if rng is None:
            return None
        lo, hi = rng
        return lo + (hi - lo) * (self.slider_slice.value() / 1000.0)

    def _sync_slice_readout(self) -> None:
        """Keep the numeric input in step with the slider."""
        pos = self._slice_pos_m()
        if pos is None:
            return
        rng = self._slice_bounds()
        self.spn_slice.blockSignals(True)
        if rng is not None:
            # numeric input follows the data extent (mm)
            self.spn_slice.setRange(rng[0] * 1000.0, rng[1] * 1000.0)
        self.spn_slice.setValue(pos * 1000.0)
        self.spn_slice.blockSignals(False)

    def _on_slice_slider(self, _value: int = 0) -> None:
        self._slice_pos_exact = None  # the slider is authoritative again
        self._sync_slice_readout()
        self._refresh_display()

    def _on_slice_value(self, mm: float) -> None:
        """Numeric slice input (mm): cut exactly there, move the slider along."""
        pos = mm / 1000.0
        rng = self._slice_bounds()
        if rng is not None:
            lo, hi = rng
            if hi > lo:
                pos = min(max(pos, lo), hi)  # keep the cut inside the data
                self.slider_slice.blockSignals(True)
                self.slider_slice.setValue(int(round((pos - lo) / (hi - lo) * 1000)))
                self.slider_slice.blockSignals(False)
        self._slice_pos_exact = pos
        self._sync_slice_readout()
        self._refresh_display()

    def _on_slice_axis_changed(self, *_args) -> None:
        # a typed position belongs to the old axis -> fall back to the slider
        self._slice_pos_exact = None
        self._sync_slice_readout()
        self._refresh_display()

    def _slice_origin(self):
        axis = self.cmb_slice_axis.currentText()
        pos = self._slice_pos_m()
        if pos is None:
            return (0.0, 0.0, 0.0)
        if axis == "X":
            return (pos, 0.0, 0.0)
        if axis == "Y":
            return (0.0, pos, 0.0)
        return (0.0, 0.0, pos)

    # ---------------------------------------------------------- vector glyphs
    def vector_options(self) -> dict:
        """Current vector glyph settings (JSON-serialisable)."""
        return {
            "enabled": bool(self.chk_vectors.isChecked()),
            "size": float(self.spn_vec_size.value()),
            "max_arrows": int(self.spn_vec_count.value()),
            "normalize": bool(self.chk_vec_norm.isChecked()),
            "overlay": bool(self.chk_vec_overlay.isChecked()),
            "min_pct": int(self.spn_vec_min.value()),
            "random": bool(self.chk_vec_random.isChecked()),
            "geom_opacity": int(self.sld_geom.value()),
        }

    def set_vector_options(self, options: dict | None, *, notify: bool = False) -> None:
        """Apply vector glyph settings (startup / pop-out mirroring).

        Unknown keys are ignored and values are clamped; a dump without a
        3-component field array keeps the choice but does not draw anything.
        """
        opts = dict(DEFAULT_VECTORS)
        if isinstance(options, dict):
            for key in DEFAULT_VECTORS:
                if key in options:
                    opts[key] = options[key]
        try:
            opts["enabled"] = bool(opts["enabled"])
            opts["normalize"] = bool(opts["normalize"])
            opts["overlay"] = bool(opts["overlay"])
            opts["random"] = bool(opts["random"])
            opts["size"] = min(8.0, max(0.1, float(opts["size"])))
            opts["max_arrows"] = min(50000, max(50, int(opts["max_arrows"])))
            opts["min_pct"] = max(0, min(99, int(opts["min_pct"])))
            opts["geom_opacity"] = max(0, min(40, int(opts["geom_opacity"])))
        except (TypeError, ValueError):
            opts = dict(DEFAULT_VECTORS)
        for widget, value in (
            (self.chk_vectors, opts["enabled"]),
            (self.spn_vec_size, opts["size"]),
            (self.spn_vec_count, opts["max_arrows"]),
            (self.spn_vec_min, opts["min_pct"]),
            (self.chk_vec_random, opts["random"]),
            (self.sld_geom, opts["geom_opacity"]),
            (self.chk_vec_norm, opts["normalize"]),
            (self.chk_vec_overlay, opts["overlay"]),
        ):
            widget.blockSignals(True)
            if isinstance(widget, QCheckBox):
                widget.setChecked(value)
            else:
                widget.setValue(value)
            widget.blockSignals(False)
        self._vectors = dict(opts)
        self._update_vector_controls()
        self._refresh_display()
        if notify and callable(self.on_vectors_changed):
            self.on_vectors_changed(self.vector_options())

    def _on_vectors_toggled(self, _on: bool) -> None:
        self._vectors["enabled"] = bool(self.chk_vectors.isChecked())
        self._update_vector_controls()
        self._refresh_display()
        if callable(self.on_vectors_changed):
            self.on_vectors_changed(self.vector_options())

    def _on_vector_option_changed(self, *_a) -> None:
        self._vectors.update(self.vector_options())
        self._refresh_display()
        if callable(self.on_vectors_changed):
            self.on_vectors_changed(self.vector_options())

    def _update_vector_controls(self) -> None:
        """Enable the vector widgets only when the dump has a vector array."""
        has = bool(self._has_vectors and self._grid is not None)
        on = bool(self.chk_vectors.isChecked()) and has
        self.chk_vectors.setEnabled(has)
        for widget in (self.spn_vec_size, self.spn_vec_count, self.chk_vec_norm):
            widget.setEnabled(on)
        self.chk_vectors.setToolTip(
            getattr(self, "_vec_tip", "")
            + ("" if has else "\n\n(this dump has no 3-component field array)")
        )

    # ------------------------------------------------------------- streamlines
    def streamline_options(self) -> dict:
        """Current streamline appearance / seeding options (JSON-serialisable)."""
        color = self._stream.get("color", DEFAULT_STREAMLINES["color"])
        return {
            "enabled": bool(self.chk_stream.isChecked()),
            "preset": self.cmb_stream_preset.currentText() or "Custom",
            "representation": str(self.cmb_stream_rep.currentData() or "tube"),
            "radius_pct": float(self.spn_stream_radius.value()),
            "sides": int(self.spn_stream_sides.value()),
            "line_width": float(self.spn_stream_width.value()),
            "opacity": int(self.spn_stream_opacity.value()),
            "color_mode": str(self.cmb_stream_color.currentData() or "field"),
            "color": [float(c) for c in color],
            "seeds": int(self.spn_stream_seeds.value()),
        }

    def _set_streamline_widgets(self, opts: dict) -> None:
        """Mirror a normalised options dict into the streamline widgets."""
        for widget in (
            self.chk_stream,
            self.cmb_stream_preset,
            self.cmb_stream_rep,
            self.spn_stream_radius,
            self.spn_stream_sides,
            self.spn_stream_width,
            self.spn_stream_opacity,
            self.cmb_stream_color,
            self.spn_stream_seeds,
        ):
            widget.blockSignals(True)
        try:
            self.chk_stream.setChecked(bool(opts["enabled"]))
            preset = str(opts.get("preset", "Custom"))
            if self.cmb_stream_preset.findText(preset) < 0:
                preset = "Custom"
            self.cmb_stream_preset.setCurrentText(preset)
            index = self.cmb_stream_rep.findData(str(opts["representation"]))
            self.cmb_stream_rep.setCurrentIndex(max(0, index))
            self.spn_stream_radius.setValue(float(opts["radius_pct"]))
            self.spn_stream_sides.setValue(int(opts["sides"]))
            self.spn_stream_width.setValue(float(opts["line_width"]))
            self.spn_stream_opacity.setValue(int(opts["opacity"]))
            index = self.cmb_stream_color.findData(str(opts["color_mode"]))
            self.cmb_stream_color.setCurrentIndex(max(0, index))
            self.spn_stream_seeds.setValue(int(opts["seeds"]))
        finally:
            for widget in (
                self.chk_stream,
                self.cmb_stream_preset,
                self.cmb_stream_rep,
                self.spn_stream_radius,
                self.spn_stream_sides,
                self.spn_stream_width,
                self.spn_stream_opacity,
                self.cmb_stream_color,
                self.spn_stream_seeds,
            ):
                widget.blockSignals(False)
        self._update_stream_color_button()

    def set_streamline_options(
        self, options: dict | None, *, notify: bool = False
    ) -> None:
        """Apply streamline appearance options (startup / preset / pop-out).

        Unknown keys and out-of-range values are ignored or clamped; the
        streamline actor is rebuilt immediately when a grid is loaded.
        """
        opts = dict(DEFAULT_STREAMLINES)
        if isinstance(options, dict):
            for key in DEFAULT_STREAMLINES:
                if key in options:
                    opts[key] = options[key]
        try:
            opts["enabled"] = bool(opts["enabled"])
            preset = str(opts.get("preset") or "Custom")
            if preset not in ("Standard", "Draft", "Publication", "Custom"):
                preset = "Custom"
            opts["preset"] = preset
            representation = str(opts.get("representation") or "tube")
            opts["representation"] = (
                representation if representation in ("tube", "line") else "tube"
            )
            opts["radius_pct"] = max(
                0.01, min(5.0, float(opts.get("radius_pct", 0.20)))
            )
            opts["sides"] = max(3, min(24, int(opts.get("sides", 8))))
            opts["line_width"] = max(
                0.5, min(10.0, float(opts.get("line_width", 2.0)))
            )
            opts["opacity"] = max(5, min(100, int(opts.get("opacity", 100))))
            mode = str(opts.get("color_mode") or "field")
            opts["color_mode"] = (
                mode if mode in ("field", "speed", "solid") else "field"
            )
            color = opts.get("color")
            if isinstance(color, (list, tuple)) and len(color) >= 3:
                opts["color"] = [
                    max(0.0, min(1.0, float(c))) for c in color[:3]
                ]
            else:
                opts["color"] = list(DEFAULT_STREAMLINES["color"])
            opts["seeds"] = max(2, min(10, int(opts.get("seeds", 6))))
        except (TypeError, ValueError):
            opts = dict(DEFAULT_STREAMLINES)
        self._stream = dict(opts)
        self._set_streamline_widgets(opts)
        self._update_stream_controls()
        if self._grid is not None:
            self._refresh_display()
        if notify and callable(self.on_streamlines_changed):
            self.on_streamlines_changed(self.streamline_options())

    def _update_stream_color_button(self) -> None:
        color = self._stream.get("color", DEFAULT_STREAMLINES["color"])
        try:
            r, g, b = (max(0.0, min(1.0, float(c))) for c in color[:3])
        except (TypeError, ValueError):
            r, g, b = DEFAULT_STREAMLINES["color"]
        ri, gi, bi = (int(round(v * 255)) for v in (r, g, b))
        # readable text on both dark and light swatches
        lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
        fg = "black" if lum > 0.55 else "white"
        self.btn_stream_color.setText(f"#{ri:02x}{gi:02x}{bi:02x}")
        self.btn_stream_color.setStyleSheet(
            f"QPushButton {{ background-color: rgb({ri},{gi},{bi});"
            f" color: {fg}; }}"
        )

    def _update_stream_controls(self) -> None:
        """Enable only the controls that apply to the current appearance."""
        on = bool(self.chk_stream.isChecked())
        rep = str(self.cmb_stream_rep.currentData() or "tube")
        mode = str(self.cmb_stream_color.currentData() or "field")
        for widget in (
            self.cmb_stream_preset,
            self.cmb_stream_rep,
            self.spn_stream_opacity,
            self.cmb_stream_color,
            self.spn_stream_seeds,
            self.btn_stream_reset,
        ):
            widget.setEnabled(on)
        for widget in (self.spn_stream_radius, self.spn_stream_sides):
            widget.setEnabled(on and rep == "tube")
        self.spn_stream_width.setEnabled(on and rep == "line")
        self.btn_stream_color.setEnabled(on and mode == "solid")

    def _on_stream_toggled(self, _on: bool) -> None:
        self._stream["enabled"] = bool(self.chk_stream.isChecked())
        self._update_stream_controls()
        if self._grid is not None:
            self._refresh_display()
        if callable(self.on_streamlines_changed):
            self.on_streamlines_changed(self.streamline_options())

    def _on_stream_option_changed(self, *_args) -> None:
        # Any manual tweak makes the selected preset "Custom".
        self.cmb_stream_preset.blockSignals(True)
        self.cmb_stream_preset.setCurrentText("Custom")
        self.cmb_stream_preset.blockSignals(False)
        self._stream.update(self.streamline_options())
        self._update_stream_controls()
        if self._grid is not None:
            self._refresh_display()
        if callable(self.on_streamlines_changed):
            self.on_streamlines_changed(self.streamline_options())

    def _on_stream_preset_changed(self, name: str) -> None:
        if name == "Custom":
            return
        preset = STREAMLINE_PRESETS.get(str(name))
        if preset is None:
            return
        opts = self.streamline_options()
        opts.update(preset)
        opts["preset"] = str(name)
        self.set_streamline_options(opts, notify=True)

    def _choose_stream_color(self) -> None:
        color = self._stream.get("color", DEFAULT_STREAMLINES["color"])
        try:
            initial = QColor.fromRgbF(*(max(0.0, min(1.0, float(c))) for c in color[:3]))
        except (TypeError, ValueError):
            initial = QColor.fromRgbF(*DEFAULT_STREAMLINES["color"])
        chosen = QColorDialog.getColor(initial, self, "Streamline colour")
        if not chosen.isValid():
            return
        self._stream["color"] = [chosen.redF(), chosen.greenF(), chosen.blueF()]
        self.cmb_stream_preset.blockSignals(True)
        self.cmb_stream_preset.setCurrentText("Custom")
        self.cmb_stream_preset.blockSignals(False)
        self._stream.update(self.streamline_options())
        self._update_stream_color_button()
        if self._grid is not None:
            self._refresh_display()
        if callable(self.on_streamlines_changed):
            self.on_streamlines_changed(self.streamline_options())

    def _reset_streamlines(self) -> None:
        opts = dict(DEFAULT_STREAMLINES)
        opts["enabled"] = bool(self.chk_stream.isChecked())  # keep the switch
        self.set_streamline_options(opts, notify=True)

    def _stream_lookup_table(self, vtk, lo: float, hi: float):
        """Separate LUT for |v| colouring (must not alter the field LUT)."""
        lut = vtk.vtkLookupTable()
        colors = colormap_colors(
            str(self._color_scale.get("cmap", DEFAULT_COLOR_SCALE["cmap"])),
            int(self._color_scale.get("steps", DEFAULT_COLOR_SCALE["steps"])),
            bool(self._color_scale.get("reverse", False)),
        )
        if colors:
            fill_lookup_table(lut, colors)
        else:  # defensive fallback, same as _lookup_table
            lut.SetNumberOfTableValues(256)
            lut.SetHueRange(0.667, 0.0)
        if hi <= lo:
            hi = lo + 1.0
        lut.SetScaleToLinear()
        lut.SetTableRange(float(lo), float(hi))
        lut.Build()
        return lut

    # ------------------------------------------------------------ colour scale
    def color_scale_options(self) -> dict:
        """Current colour scale settings (JSON-serialisable)."""
        return dict(self._color_scale)

    def set_color_scale_options(self, options: dict | None, *, notify: bool = False) -> None:
        """Apply colour map / reverse / steps (Settings dialog, pop-out mirror).

        Unknown maps and out-of-range step counts fall back to valid values;
        the field is redrawn immediately when one is loaded.
        """
        opts = dict(DEFAULT_COLOR_SCALE)
        if isinstance(options, dict):
            if "cmap" in options:
                name = str(options.get("cmap") or "")
                if name in colormap_names():
                    opts["cmap"] = name
            if "reverse" in options:
                opts["reverse"] = bool(options.get("reverse"))
            if "steps" in options:
                try:
                    steps = int(options.get("steps"))
                except (TypeError, ValueError):
                    steps = int(DEFAULT_COLOR_SCALE["steps"])
                opts["steps"] = max(
                    COLOR_SCALE_MIN_STEPS, min(COLOR_SCALE_MAX_STEPS, steps)
                )
        self._color_scale = opts
        if self._grid is not None:
            self._refresh_display()
        if notify and callable(self.on_color_scale_changed):
            self.on_color_scale_changed(self.color_scale_options())

    # ---------------------------------------------------------- colour bar title
    def bar_title_options(self) -> dict:
        """Current colour-bar title settings (JSON-serialisable)."""
        return dict(self._bar_title_opts)

    def set_bar_title_options(self, options: dict | None) -> None:
        """Apply title side / position / size / rotation (Viewer settings).

        ``left`` puts the title rotated next to the bar at normalized viewport
        ``x``/``y``; otherwise it sits above the bar. ``size`` is the title font
        size in points, ``rotation`` the left-mode text angle (90 = vertical).
        """
        opts = dict(self._bar_title_opts)
        if isinstance(options, dict):
            if "left" in options:
                opts["left"] = bool(options.get("left"))
            for key, lo, hi in (("x", 0.0, 1.0), ("y", 0.0, 1.0)):
                if key not in options:
                    continue
                try:
                    value = float(options.get(key))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    opts[key] = max(lo, min(hi, value))
            if "size" in options:
                try:
                    opts["size"] = max(6, min(72, int(options.get("size"))))
                except (TypeError, ValueError):
                    pass
            if "rotation" in options:
                try:
                    rotation = float(options.get("rotation"))
                except (TypeError, ValueError):
                    rotation = 90.0
                if math.isfinite(rotation):
                    opts["rotation"] = rotation % 360.0
        self._bar_title_opts = opts
        if self._grid is not None:
            self._refresh_display()

    # ---------------------------------------------------------------- colour bar
    def color_bar_options(self) -> dict:
        """Current colour-bar geometry/font settings (JSON-serialisable)."""
        return dict(self._color_bar_opts)

    def set_color_bar_options(self, options: dict | None) -> None:
        """Apply colour-bar position/size/font (Viewer settings).

        Position and size use normalized viewport coordinates; the width/height
        describe the whole bar including its labels. Missing keys keep their
        current value, invalid values are clamped or ignored.
        """
        opts = dict(self._color_bar_opts)
        if isinstance(options, dict):
            for key in ("x", "y"):
                if key not in options:
                    continue
                try:
                    value = float(options.get(key))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    opts[key] = max(0.0, min(1.0, value))
            for key, lo, hi in (("width", 0.02, 1.0), ("height", 0.02, 1.0)):
                if key not in options:
                    continue
                try:
                    value = float(options.get(key))
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    opts[key] = max(lo, min(hi, value))
            if "font" in options:
                family = str(options.get("font") or "")
                if family in COLOR_BAR_FONTS:
                    opts["font"] = family
            if "font_size" in options:
                try:
                    opts["font_size"] = max(6, min(72, int(options.get("font_size"))))
                except (TypeError, ValueError):
                    pass
        self._color_bar_opts = opts
        if self._grid is not None:
            self._refresh_display()

    def _vector_array_name(self) -> str | None:
        """Name of the 3-component point array used for the arrows."""
        if self._grid is None:
            return None
        pd = self._grid.GetPointData()
        names = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
        for candidate in ("field",):  # our exporter writes real(E) as "field"
            arr = pd.GetArray(candidate) if candidate in names else None
            if arr is not None and arr.GetNumberOfComponents() == 3:
                return candidate
        active = pd.GetVectors()
        if active is not None and active.GetNumberOfComponents() == 3:
            return active.GetName()
        for name in names:
            arr = pd.GetArray(name)
            if arr is not None and arr.GetNumberOfComponents() == 3:
                return name
        return None

    def _characteristic_spacing(self) -> float:
        """Smallest cell size of the loaded grid (metres) — arrow scale base."""
        if self._grid is None:
            return 1.0
        b = self._grid.GetBounds()
        dims = self._grid.GetDimensions()
        spans = (b[1] - b[0], b[3] - b[2], b[5] - b[4])
        cells = (dims[0] - 1, dims[1] - 1, dims[2] - 1)
        steps = [s / c for s, c in zip(spans, cells) if c > 0 and s > 0]
        return min(steps) if steps else 1.0

    def _vector_reference(self, per_frame: float) -> float:
        """Reference |field| the arrow lengths are scaled against.

        With a loaded series the reference is measured once over the whole
        series (sampled): an arrow of equal strength then keeps its length from
        frame to frame. The old per-frame 99 % value made every frame rescale
        itself, so the arrows "breathed" and the real amplitude change over
        time was masked. Without a series the current frame is used.
        """
        if not self._series:
            return per_frame
        name = self._vector_array_name()
        if self._vec_series_ref is None or self._vec_ref_name != name:
            self._vec_ref_name = name
            self._vec_series_ref = self._measure_series_reference()
        return self._vec_series_ref or per_frame

    def _measure_series_reference(self, sample: int = 24) -> float:
        """99th percentile of |field| over the series (sampled, cached).

        Reads at most ``sample`` frames, so even a series with thousands of
        steps costs only a fraction of a second.
        """
        name = self._vector_array_name()
        if not name or not self._series:
            return 0.0
        import numpy as np
        from vtk.util import numpy_support as ns

        vtk = getattr(self, "_vtk", None) or _vtk()
        files = list(self._series)
        if len(files) > sample:
            stride = len(files) / float(sample)
            idxs = sorted({min(int(i * stride), len(files) - 1) for i in range(sample)})
        else:
            idxs = list(range(len(files)))
        refs: list[float] = []
        for i in idxs:
            try:
                reader = _make_reader(vtk, files[i])
                reader.SetFileName(str(files[i]))
                reader.Update()
                arr = reader.GetOutput().GetPointData().GetArray(name)
                if arr is None or arr.GetNumberOfTuples() == 0:
                    continue
                vals = np.asarray(ns.vtk_to_numpy(arr), dtype=float)
                mags = np.linalg.norm(vals, axis=1) if vals.ndim == 2 else np.abs(vals)
                mags = mags[np.isfinite(mags)]
                if mags.size:
                    refs.append(float(np.percentile(mags, 99.0)))
            except Exception:  # noqa: BLE001 — one broken frame must not stop this
                continue
        refs = [r for r in refs if r > 0.0]  # empty frames (t = 0) carry no scale
        if not refs:
            return 0.0
        # Median (not max): a series that starts with the huge excitation pulse
        # would otherwise shrink every steady-state arrow to nothing. The
        # median keeps the lengths comparable across the frames that matter.
        ref = float(np.median(refs))
        print(
            f"VTK viewer: vector length reference |{name}| = {ref:.4g} "
            f"(median over {len(refs)} of {len(files)} step(s), "
            f"range {min(refs):.3g}..{max(refs):.3g})"
        )
        return ref

    @staticmethod
    def _vector_input(vtk, data, rep: str):
        """Geometry the arrows sit on (same shape as the visible representation).

        Surface/Wireframe glyph the whole volume like ParaView's Glyph filter:
        the arrow count is capped by ``vtkMaskPoints`` and the translucent
        geometry ghosts keep the inner arrows visible. Slice/Iso-surface glyph
        the cut / contour polydata that is already on screen.
        """
        return data

    def _logmsg(self, text: str) -> None:
        """Diagnostics: stdout plus ``field_viewer.log`` next to the results."""
        print(text, flush=True)
        try:
            wd = getattr(self, "work_dir", None)
            if wd:
                with open(Path(wd) / "field_viewer.log", "a", encoding="utf-8") as fh:
                    fh.write(text + "\n")
        except OSError:
            pass

    @staticmethod
    def _subset_points(vtk, ns, poly, keep):
        """New polydata with only the points where ``keep`` is true."""
        import numpy as np

        pts = ns.vtk_to_numpy(poly.GetPoints().GetData())[keep]
        out = vtk.vtkPolyData()
        vpts = vtk.vtkPoints()
        vpts.SetData(ns.numpy_to_vtk(np.ascontiguousarray(pts), deep=True))
        out.SetPoints(vpts)
        cells = vtk.vtkCellArray()
        cells.InsertNextCell(int(pts.shape[0]))
        for i in range(int(pts.shape[0])):
            cells.InsertCellPoint(i)
        out.SetVerts(cells)
        pdata = poly.GetPointData()
        for i in range(pdata.GetNumberOfArrays()):
            src = pdata.GetArray(i)
            if src is None:
                continue
            v = ns.vtk_to_numpy(src)
            if v.shape[0] != keep.size:
                continue
            dst = ns.numpy_to_vtk(np.ascontiguousarray(v[keep]), deep=True)
            dst.SetName(src.GetName())
            out.GetPointData().AddArray(dst)
        return out

    def _make_vector_actor(self, vtk, data, rep: str, lut):
        """Arrow glyphs for the current representation (ParaView "Glyph")."""
        import numpy as np
        from vtk.util import numpy_support as ns

        name = self._vector_array_name()
        poly = self._vector_input(vtk, data, rep)
        if not name or poly is None or poly.GetNumberOfPoints() < 2:
            return None

        npts = poly.GetNumberOfPoints()
        max_arrows = max(1, int(self.spn_vec_count.value()))
        if (
            getattr(self, "chk_vec_random", None) is not None
            and self.chk_vec_random.isChecked()
        ):
            # Random subset in numpy. vtkMaskPoints' random mode returned an
            # empty set here, and the raw input is a rectilinear grid whose
            # GetPoints() is None - so first pass every point through
            # vtkMaskPoints (ratio 1) to get a polydata, then pick in numpy.
            allpts = vtk.vtkMaskPoints()
            allpts.SetInputData(poly)
            allpts.SetOnRatio(1)
            allpts.SetRandomMode(0)
            allpts.GenerateVerticesOff()
            allpts.Update()
            cloud = allpts.GetOutput()
            n_all = cloud.GetNumberOfPoints()
            rng = np.random.default_rng(12345)
            take = min(max_arrows, n_all)
            idx = rng.choice(n_all, size=take, replace=False)
            sel = np.zeros(n_all, dtype=bool)
            sel[idx] = True
            try:
                sampled = self._subset_points(vtk, ns, cloud, sel)
            except Exception as exc:  # noqa: BLE001
                self._logmsg(f"random: subset failed: {exc!r}")
                return None
        else:
            ratio = max(1, int(round(npts / max_arrows)))
            mask = vtk.vtkMaskPoints()
            mask.SetInputData(poly)
            mask.SetOnRatio(ratio)
            mask.SetRandomMode(0)  # deterministic: arrows must not jump per frame
            mask.GenerateVerticesOff()
            mask.Update()
            sampled = mask.GetOutput()
        arr = sampled.GetPointData().GetArray(name)
        if arr is None or arr.GetNumberOfTuples() == 0:
            return None
        try:
            min_pct = float(self.spn_vec_min.value())
        except Exception:  # noqa: BLE001
            min_pct = 0.0
        vals = np.asarray(ns.vtk_to_numpy(arr), dtype=float)
        if vals.ndim != 2 or vals.shape[0] == 0:
            return None
        # Magnitude threshold in NumPy: vtkThresholdPoints kept dropping every
        # point here, whatever the array name, so the cut is done by hand.
        mags_all = np.linalg.norm(vals, axis=1)
        keep = np.ones(mags_all.size, dtype=bool)
        if self.chk_vec_norm.isChecked():
            # "same length" draws an arrow at every point; where |v| is zero the
            # direction is undefined, so those arrows carry no information.
            keep &= mags_all > 0.0
        if min_pct > 0.0:
            # Linear share of this frame's peak: "3 %" hides the arrows below
            # 3 % of the strongest |v|. Interpolating on the logarithmic colour
            # axis (with a max*1e-9 floor for exactly-zero points) stretched
            # the scale over ~9 decades and made the slider do nothing below
            # ~70 % - it looked broken.
            finite = mags_all[np.isfinite(mags_all)]
            if finite.size:
                lo = float(np.min(finite))
                hi = float(np.max(finite))
                cut = lo + (hi - lo) * (min_pct / 100.0)
                keep &= mags_all >= cut
        n_keep = int(keep.sum())
        if n_keep == 0:
            return None
        if n_keep < mags_all.size:
            sampled = self._subset_points(vtk, ns, sampled, keep)
            arr = sampled.GetPointData().GetArray(name)
            if arr is None:
                return None
            vals = np.asarray(ns.vtk_to_numpy(arr), dtype=float)
        # vtkGlyph3D orients along the *active* vectors of its input; a filter
        # chain or an older export may have lost that flag, which leaves the
        # arrows pointing in a constant direction while only their length
        # follows the field - set it explicitly.
        sampled.GetPointData().SetActiveVectors(name)

        # 99th percentile instead of the maximum: one hot spot must not shrink
        # every other arrow to nothing.
        vals = np.asarray(ns.vtk_to_numpy(arr), dtype=float)
        mags = np.linalg.norm(vals, axis=1) if vals.ndim == 2 else np.abs(vals)
        mags = mags[np.isfinite(mags)]
        vref = self._vector_reference(
            float(np.percentile(mags, 99.0)) if mags.size else 0.0
        )

        spacing = self._characteristic_spacing()
        # mean distance of the sampled points: cell size scaled by the decimation
        base = spacing
        target = base * float(self.spn_vec_size.value()) * 0.6

        arrow = vtk.vtkArrowSource()
        arrow.SetTipLength(0.35)
        arrow.SetTipRadius(0.12)
        arrow.SetShaftRadius(0.045)

        glyph = vtk.vtkGlyph3D()
        glyph.SetInputData(sampled)
        glyph.SetSourceConnection(arrow.GetOutputPort())
        glyph.SetInputArrayToProcess(
            0, 0, 0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, name
        )
        glyph.SetVectorModeToUseVector()
        glyph.OrientOn()
        if self.chk_vec_norm.isChecked() or vref <= 0.0:
            # same length for every arrow: pure direction (good for animation)
            glyph.SetScaleModeToDataScalingOff()
            glyph.SetScaleFactor(target)
        else:
            # length follows |E|; the 99 % value maps to the "size" length
            glyph.SetScaleModeToScaleByVector()
            glyph.SetScaleFactor(target / vref)
        glyph.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(glyph.GetOutputPort())
        scalar = self._scalar_name
        out_pd = glyph.GetOutput().GetPointData()
        if scalar and out_pd.GetArray(scalar) is not None:
            mapper.SetLookupTable(lut)
            mapper.SetScalarModeToUsePointFieldData()
            mapper.SelectColorArray(scalar)
            mapper.SetScalarRange(*self._effective_range())
            mapper.ScalarVisibilityOn()
        else:
            mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(1.0, 0.86, 0.25)  # only seen without scalars
        return actor

    # ------------------------------------------------------------- rendering
    def _apply_bar_title(self, bar):
        """Title above the bar, or rotated beside it (Viewer settings).

        Always take the freshly built bar title first.  The old code cached the
        first field's title (e.g. "E-Field … [V/m]") and kept showing it after
        loading an H dump, even though the data panel and tab header were
        already correct.
        """
        text = bar.GetTitle() or getattr(self, "_bar_title", "")
        self._bar_title = text
        if self._renderer is None:
            return bar
        opts = self._bar_title_opts
        size = int(opts.get("size", DEFAULT_BAR_TITLE["size"]))
        family = str(self._color_bar_opts.get("font", "Arial"))
        bar.GetTitleTextProperty().SetFontSize(size)
        try:
            bar.GetTitleTextProperty().SetFontFamilyAsString(family)
        except Exception:  # noqa: BLE001 - older VTK text property
            pass
        if self._title_actor is None:
            vtk = self._vtk or _vtk()
            self._title_actor = vtk.vtkTextActor()
            prop = self._title_actor.GetTextProperty()
            prop.SetColor(1, 1, 1)
            prop.SetFontSize(size)
            prop.SetOrientation(float(opts.get("rotation", 90.0)))
            prop.SetJustificationToCentered()
            prop.SetVerticalJustificationToCentered()
            # The bar is placed in normalized viewport coordinates (0.84..0.99),
            # but a vtkActor2D defaults to display pixels -- which put the title
            # in the bottom-left corner instead of beside the bar.
            self._title_actor.GetPositionCoordinate().SetCoordinateSystemToNormalizedViewport()
        # _refresh_display calls RemoveAllViewProps, which also drops 2D actors -
        # so the title has to be attached again on every refresh.
        try:
            self._renderer.RemoveActor2D(self._title_actor)
        except Exception:  # noqa: BLE001
            pass
        self._renderer.AddActor2D(self._title_actor)
        if bool(opts.get("left")):
            bar.SetTitle("")
            self._title_actor.SetInput(text)
            prop = self._title_actor.GetTextProperty()
            prop.SetFontSize(size)
            prop.SetOrientation(float(opts.get("rotation", 90.0)))
            try:
                prop.SetFontFamilyAsString(family)
            except Exception:  # noqa: BLE001 - older VTK text property
                pass
            self._title_actor.SetPosition(
                float(opts.get("x", 0.826)), float(opts.get("y", 0.5))
            )
            self._title_actor.SetVisibility(1)
        else:
            bar.SetTitle(text)
            self._title_actor.SetVisibility(0)
        return bar

    def _make_scalar_bar(self, vtk, lut, name: str, unit: str = ""):
        """Compact colour bar: VTK otherwise scales title/labels with the bar
        height, which produced a huge "field" filling the pane.

        ``unit`` (e.g. "V/m") is appended to the title so the physical unit of
        the displayed values is visible. Position, size and font come from the
        Viewer settings (:data:`DEFAULT_COLOR_BAR`).
        """
        opts = self._color_bar_opts
        bar = vtk.vtkScalarBarActor()
        bar.SetLookupTable(lut)
        kind = ""
        if unit.startswith("V"):
            kind = "E-Field "
        elif unit.startswith("A/m"):
            kind = "H-Field " if unit.strip() == "A/m" else "J-Field "
        bar.SetTitle(f"{kind}{name} [{unit}]" if unit else f"{kind}{name}")
        bar.SetNumberOfLabels(5)
        bar.UnconstrainedFontSizeOn()  # font size in points, not bar-relative
        # Normalised viewport coordinates (0 = left/bottom, 1 = right/top).
        # (MaximumWidth/HeightInPixels override the position and centre the bar,
        # which is why it drifted into the middle of the pane.)
        bar.SetPosition(float(opts.get("x", 0.84)), float(opts.get("y", 0.06)))
        bar.SetPosition2(
            float(opts.get("width", 0.15)), float(opts.get("height", 0.88))
        )
        bar.SetBarRatio(0.15)
        family = str(opts.get("font", "Arial"))
        title_size = int(self._bar_title_opts.get("size", 12))
        label_size = int(opts.get("font_size", 10))
        bar.GetTitleTextProperty().SetFontSize(title_size)
        bar.GetTitleTextProperty().SetColor(1, 1, 1)
        bar.GetLabelTextProperty().SetFontSize(label_size)
        bar.GetLabelTextProperty().SetColor(1, 1, 1)
        bar.SetLabelFormat("%.2g")
        bar.SetDrawTickLabels(True)
        # Title tight above the bar, upright (the reference uses a plain label,
        # not the italic default).
        bar.SetVerticalTitleSeparation(6)
        try:
            bar.GetTitleTextProperty().SetItalic(0)
        except Exception:  # noqa: BLE001 - older VTK text property
            pass
        for prop in (bar.GetTitleTextProperty(), bar.GetLabelTextProperty()):
            try:
                prop.SetFontFamilyAsString(family)
            except Exception:  # noqa: BLE001 - older VTK text property
                pass
        bar.SetTextPad(3)
        bar.SetVerticalTitleSeparation(4)
        return bar

    def _make_mapper(self, vtk, lut, loc, name):
        """Return ``(mapper, data)`` for the current representation.

        ``data`` is the dataset the mapper consumes (grid, slice or contour) —
        the vector glyphs sit on exactly the same geometry. ``(None, None)``
        when nothing can be shown.
        """
        if self._grid is None:
            return None, None
        rep = self.cmb_rep.currentText()
        if rep == "Iso-surface" and name:
            flt = vtk.vtkContourFilter()
            flt.SetInputData(self._grid)
            flt.SetInputArrayToProcess(0, 0, 0, 0, name)
            flt.SetValue(0, self._iso_value())
            flt.Update()
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(flt.GetOutputPort())
            data = flt.GetOutput()
        elif rep == "Slice":
            nx, ny, nz = _AXIS_NORMAL[self.cmb_slice_axis.currentText()]
            plane = vtk.vtkPlane()
            plane.SetNormal(nx, ny, nz)
            plane.SetOrigin(*self._slice_origin())
            cutter = vtk.vtkCutter()
            cutter.SetInputData(self._grid)
            cutter.SetCutFunction(plane)
            cutter.Update()
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputConnection(cutter.GetOutputPort())
            data = cutter.GetOutput()
        else:
            mapper = vtk.vtkDataSetMapper()
            mapper.SetInputData(self._grid)
            data = self._grid
        if name:
            mapper.SetLookupTable(lut)
            mapper.SetScalarModeToUsePointFieldData() if loc == "point" else mapper.SetScalarModeToUseCellFieldData()
            mapper.SelectColorArray(name)
            mapper.SetScalarRange(*self._effective_range())
            mapper.ScalarVisibilityOn()
        else:
            mapper.ScalarVisibilityOff()
        return mapper, data

    def _show_field_actor(self) -> bool:
        """Draw the scalar field representation underneath the arrows?

        False while the arrows are shown (the default): an opaque slice,
        surface or volume covers every arrow that points into it, which is what
        made the vector display unusable. The "field below" option brings
        the colour plot back for those who want the overlay.
        """
        vectors_on = bool(self.chk_vectors.isChecked()) and self._has_vectors
        return (not vectors_on) or bool(self._vectors.get("overlay"))

    def _axis_mid(self, axis: str) -> float:
        """Centre of the current grid along ``axis`` (metres)."""
        b = self._grid.GetBounds()
        return {
            "X": (b[0] + b[1]) / 2.0,
            "Y": (b[2] + b[3]) / 2.0,
        }.get(axis, (b[4] + b[5]) / 2.0)

    def _plane_actor(self, vtk, lut, loc, name, axis: str, pos_m: float):
        """One slice plane as ``(actor, cut_polydata)`` (3-plane mode)."""
        nx, ny, nz = _AXIS_NORMAL[axis]
        origin = [0.0, 0.0, 0.0]
        origin[{"X": 0, "Y": 1, "Z": 2}[axis]] = float(pos_m)
        plane = vtk.vtkPlane()
        plane.SetNormal(nx, ny, nz)
        plane.SetOrigin(*origin)
        cutter = vtk.vtkCutter()
        cutter.SetInputData(self._grid)
        cutter.SetCutFunction(plane)
        cutter.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(cutter.GetOutputPort())
        if name:
            mapper.SetLookupTable(lut)
            if loc == "point":
                mapper.SetScalarModeToUsePointFieldData()
            else:
                mapper.SetScalarModeToUseCellFieldData()
            mapper.SelectColorArray(name)
            mapper.SetScalarRange(*self._effective_range())
            mapper.ScalarVisibilityOn()
        else:
            mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetOpacity(0.85)
        return actor, cutter.GetOutput()

    def _contour_actor(self, vtk, data, name: str, count: int):
        """Iso-lines of ``name`` on a 2D slice."""
        lo, hi = self._effective_range()
        if data is None or not name or hi <= lo:
            return None
        cf = vtk.vtkContourFilter()
        cf.SetInputData(data)
        cf.SetInputArrayToProcess(0, 0, 0, 0, name)
        for i in range(1, int(count) + 1):
            cf.SetValue(i - 1, lo + (hi - lo) * i / (int(count) + 1))
        cf.Update()
        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(cf.GetOutputPort())
        mapper.ScalarVisibilityOff()
        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(0.12, 0.12, 0.12)
        actor.GetProperty().SetLineWidth(1.0)
        return actor

    def _streamline_actor(self, vtk, name: str | None, lut=None):
        """Field lines through the volume (appearance from ``self._stream``)."""
        try:
            # the tracer needs a VECTOR array; a scalar (e.g. "magnitude")
            # cannot be integrated.
            vec_name = self._vector_array_name()
            if vec_name is None:
                return None
            opts = self.streamline_options()
            b = self._grid.GetBounds()
            dx, dy, dz = b[1] - b[0], b[3] - b[2], b[5] - b[4]
            diag = math.sqrt(dx * dx + dy * dy + dz * dz)
            n = max(2, min(10, int(opts["seeds"])))
            # n³ start points spread evenly through the whole volume (6 % margin
            # keeps the seeds/integration away from the domain boundary). A small
            # deterministic jitter breaks the regular lattice look.
            import numpy as np

            rng = np.random.default_rng(12345)
            cell = [dx / max(1, n - 1), dy / max(1, n - 1), dz / max(1, n - 1)]
            pts = vtk.vtkPoints()
            lo, span = 0.06, 0.88
            for ix in range(n):
                x = b[0] + (lo + span * ix / (n - 1)) * dx
                for iy in range(n):
                    y = b[2] + (lo + span * iy / (n - 1)) * dy
                    for iz in range(n):
                        z = b[4] + (lo + span * iz / (n - 1)) * dz
                        jitter = (rng.random(3) - 0.5) * 0.55 * np.asarray(cell)
                        pts.InsertNextPoint(
                            x + float(jitter[0]),
                            y + float(jitter[1]),
                            z + float(jitter[2]),
                        )
            seed_pd = vtk.vtkPolyData()
            seed_pd.SetPoints(pts)
            tracer = vtk.vtkStreamTracer()
            tracer.SetInputData(self._grid)
            tracer.SetSourceData(seed_pd)
            tracer.SetInputArrayToProcess(
                0, 0, 0, vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, vec_name
            )
            tracer.SetIntegrationDirectionToBoth()
            tracer.SetIntegratorTypeToRungeKutta2()
            # VTK defaults to CELL_LENGTH_UNIT; our propagation/step values are
            # in metres, otherwise lines stop after a fraction of a cell.
            tracer.SetIntegrationStepUnit(vtk.vtkStreamTracer.LENGTH_UNIT)
            # long enough for closed loops, but bounded steps keep playback usable
            tracer.SetMaximumPropagation(8.0 * diag)
            tracer.SetMaximumNumberOfSteps(3000)
            tracer.SetInitialIntegrationStep(0.01 * diag)
            tracer.SetMinimumIntegrationStep(1e-6 * diag)
            tracer.SetMaximumIntegrationStep(0.05 * diag)
            tracer.SetTerminalSpeed(1e-12)
            tracer.Update()
            traced = tracer.GetOutput()

            # Colour by |v|: derive the scalar on the traced lines so the mapper
            # gets a real array (the field LUT must not be modified in place).
            scalar_name = name
            speed_range = None
            if opts["color_mode"] == "speed":
                from vtk.util import numpy_support as ns

                arr = traced.GetPointData().GetArray(vec_name)
                if arr is not None:
                    vals = np.asarray(ns.vtk_to_numpy(arr), dtype=float)
                    if vals.ndim == 2:
                        speed = np.linalg.norm(vals, axis=1)
                    else:
                        speed = np.abs(vals)
                    speed = np.nan_to_num(speed, nan=0.0, posinf=0.0, neginf=0.0)
                    vtk_speed = ns.numpy_to_vtk(
                        np.ascontiguousarray(speed), deep=True
                    )
                    vtk_speed.SetName("|v|")
                    traced.GetPointData().AddArray(vtk_speed)
                    traced.GetPointData().SetActiveScalars("|v|")
                    scalar_name = "|v|"
                    finite = speed[np.isfinite(speed)]
                    if finite.size:
                        lo_v = float(finite.min())
                        hi_v = float(finite.max())
                        speed_range = (lo_v, hi_v if hi_v > lo_v else lo_v + 1.0)

            if opts["representation"] == "line":
                poly = traced
                mapper = vtk.vtkPolyDataMapper()
                mapper.SetInputData(poly)
            else:
                tube = vtk.vtkTubeFilter()
                tube.SetInputData(traced)
                tube.SetRadius(max(1e-15, diag * float(opts["radius_pct"]) / 100.0))
                tube.SetNumberOfSides(int(opts["sides"]))
                tube.Update()
                poly = tube.GetOutput()
                mapper = vtk.vtkPolyDataMapper()
                mapper.SetInputConnection(tube.GetOutputPort())

            colored = False
            if opts["color_mode"] == "speed" and speed_range is not None:
                mapper.SetLookupTable(
                    self._stream_lookup_table(vtk, speed_range[0], speed_range[1])
                )
                mapper.SetScalarModeToUsePointFieldData()
                mapper.SelectColorArray("|v|")
                mapper.SetScalarRange(*speed_range)
                mapper.ScalarVisibilityOn()
                colored = True
            elif (
                opts["color_mode"] == "field"
                and scalar_name
                and lut is not None
                and poly.GetPointData().GetArray(scalar_name) is not None
            ):
                mapper.SetLookupTable(lut)
                mapper.SetScalarModeToUsePointFieldData()
                mapper.SelectColorArray(scalar_name)
                mapper.SetScalarRange(*self._effective_range())
                mapper.ScalarVisibilityOn()
                colored = True
            if not colored:
                mapper.ScalarVisibilityOff()

            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            color = opts.get("color") or DEFAULT_STREAMLINES["color"]
            actor.GetProperty().SetColor(*[float(c) for c in color[:3]])
            actor.GetProperty().SetOpacity(float(opts["opacity"]) / 100.0)
            if opts["representation"] == "line":
                actor.GetProperty().SetLineWidth(float(opts["line_width"]))
            return actor
        except Exception as exc:  # noqa: BLE001 - a failed tracer must not kill the view
            print("streamline failed:", type(exc).__name__, exc)
            return None

    def _refresh_display(self, *_args) -> None:
        if self._renderer is None or self._grid is None:
            return
        vtk = self._vtk
        self._renderer.RemoveAllViewProps()
        self._triad_actor = None  # removed with the props, re-created below
        self._ruler_actor = None
        self._z_ruler_actor = None
        self._draw_geometry(render=False)

        rep = self.cmb_rep.currentText()
        loc, name = self._array()
        self.slider.setEnabled(rep == "Iso-surface")
        self.slider_slice.setEnabled(rep == "Slice")
        self.spn_slice.setEnabled(rep == "Slice")
        self.cmb_slice_axis.setEnabled(rep == "Slice")
        self._sync_slice_readout()

        lut = self._lookup_table()
        mapper, base_data = self._make_mapper(vtk, lut, loc, name)
        # The arrows replace the scalar representation: an opaque slice /
        # surface / volume covers the arrows that point into it. "field
        # underneath" brings the colour plot back as an overlay.
        show_field = self._show_field_actor()
        if mapper is not None and show_field:
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            if rep == "Wireframe":
                # representation lives on the property, not on the mapper
                actor.GetProperty().SetRepresentationToWireframe()
            if name:
                actor.GetMapper().SetLookupTable(lut)
            self._renderer.AddActor(actor)

        # --- ParaView-style extras: 3 planes, contours, streamlines ---------
        if rep == "Slice" and self.chk_planes.isChecked():
            for axis in ("X", "Y", "Z"):
                if axis == self.cmb_slice_axis.currentText():
                    continue
                extra, extra_data = self._plane_actor(
                    vtk, lut, loc, name, axis, self._axis_mid(axis)
                )
                self._renderer.AddActor(extra)
                if self.chk_contours.isChecked() and name:
                    ca = self._contour_actor(
                        vtk, extra_data, name, self.spn_contours.value()
                    )
                    if ca is not None:
                        self._renderer.AddActor(ca)
        if rep == "Slice" and self.chk_contours.isChecked() and name:
            ca = self._contour_actor(vtk, base_data, name, self.spn_contours.value())
            if ca is not None:
                self._renderer.AddActor(ca)
        pd_grid = self._grid.GetPointData()
        has_vec = False
        for i in range(pd_grid.GetNumberOfArrays()):
            arr_i = pd_grid.GetArray(i)
            if arr_i is not None and arr_i.GetNumberOfComponents() == 3:
                has_vec = True
                break
        self.chk_stream.setEnabled(bool(has_vec))
        self._update_stream_controls()
        if self.chk_stream.isChecked() and has_vec:
            sa = self._streamline_actor(vtk, name, lut)
            if sa is not None:
                self._renderer.AddActor(sa)
        if mapper is not None and name:
            # also explains the arrow colours while the field plot is hidden
            self._renderer.AddActor2D(
                self._apply_bar_title(self._make_scalar_bar(vtk, lut, name, self._field_unit))
            )

        # vector glyphs (ParaView "Glyph") on top of the current representation
        self._update_vector_controls()
        if self.chk_vectors.isChecked() and self._has_vectors:
            vec_actor = self._make_vector_actor(vtk, base_data, rep, lut)
            if vec_actor is not None:
                self._renderer.AddActor(vec_actor)

        if self.chk_outline.isChecked():
            outline = vtk.vtkOutlineFilter()
            outline.SetInputData(self._grid)
            om = vtk.vtkPolyDataMapper()
            om.SetInputConnection(outline.GetOutputPort())
            oa = vtk.vtkActor()
            oa.SetMapper(om)
            oa.GetProperty().SetColor(0.8, 0.8, 0.8)
            self._renderer.AddActor(oa)

        # Orientation triad, scaled to the dataset and placed at the lower
        # corner (a fixed-size triad used to make the field appear tiny).
        # Options: corner marker instead of the in-scene triad, labels on/off,
        # font size, size factor, numeric coordinate ruler.
        if self._axes["show"] and not self._axes["corner"]:
            self._add_orientation_triad(vtk)
        if self._axes["ruler"]:
            self._add_coordinate_ruler(vtk)
        self._update_corner_marker()

        if self._iren is not None:
            self._iren.GetRenderWindow().Render()

    def _reset_camera(self) -> None:
        """Fit the camera without changing its direction (Reset view button)."""
        self.apply_view_preset("keep")

    # ------------------------------------------------------------ view presets
    def view_preset(self) -> str:
        """Key of the selected standard view (see VIEW_PRESETS)."""
        data = self.cmb_view_preset.currentData()
        return str(data) if data else "keep"

    def set_view_default(self, key: str) -> None:
        """Select the standard view without touching the camera (startup/mirror)."""
        idx = self.cmb_view_preset.findData(str(key or "keep"))
        if idx >= 0:
            self.cmb_view_preset.blockSignals(True)
            self.cmb_view_preset.setCurrentIndex(idx)
            self.cmb_view_preset.blockSignals(False)

    def _on_view_preset_changed(self, *_a) -> None:
        """Apply the picked standard view now and remember it as the default."""
        self.apply_view_preset()
        if callable(self.on_view_changed):
            self.on_view_changed(self.view_preset())

    def apply_view_preset(self, key: str | None = None) -> None:
        """Put the camera into a standard view and fit the scene.

        ``key=None`` uses the combo selection; "keep" (or an unknown key) only
        fits, i.e. behaves like the old Reset view.
        """
        if self._renderer is None:
            return
        key = str(key if key is not None else self.view_preset())
        camera = preset_camera(self._renderer.ComputeVisiblePropBounds(), key)
        if camera is not None:
            position, focal, up = camera
            cam = self._renderer.GetActiveCamera()
            cam.SetPosition(*position)
            cam.SetFocalPoint(*focal)
            cam.SetViewUp(*up)
            cam.OrthogonalizeViewUp()
        self._renderer.ResetCamera()  # distance that fits the scene
        # keep near/far planes tight around the fitted scene
        self._renderer.ResetCameraClippingRange()
        if self._iren is not None:
            self._iren.GetRenderWindow().Render()

    # -------------------------------------------------------------- field info
    def _set_header_detail(self, detail: str = "") -> None:
        """Show the loaded field next to the tab header, e.g. "Field Viewer (J [A/m²])"."""
        lbl = getattr(self, "_help_header_label", None)
        if lbl is None:
            return
        title = str(getattr(self, "_help_header_title", "Field Viewer") or "Field Viewer")
        lbl.setText(f"{title} ({detail})" if detail else title)

    def _update_field_label(self, path: Path | None = None) -> None:
        """Show "Field: E_substrate [V/m]" - the name without the frame number."""
        p = path or self._current_path
        if p is None:
            self.lbl_field.setText("")
            self.lbl_field.setToolTip("No field loaded.")
            self._set_header_detail("")
            return
        kind = _field_kind(p)
        unit = f" [{self._field_unit}]" if self._field_unit else ""
        self.lbl_field.setText(f"Field: {kind}{unit}")
        self._set_header_detail(f"{kind}{unit}")
        tip = [f"Field on screen: {kind}", f"File: {Path(p).name}"]
        if self._series:
            tip.append(
                f"Series: {len(self._series)} frames "
                f"({self._series[0].name} … {self._series[-1].name})"
            )
        else:
            tip.append("Single file (no per-step series found next to it)")
        if self._field_unit:
            tip.append(f"Values in {self._field_unit} (from the dump name)")
        if self._scalar_name:
            tip.append(f"Array: {self._scalar_name}")
        self.lbl_field.setToolTip("\n".join(tip))
        header = getattr(self, "_help_header_label", None)
        if header is not None:
            header.setToolTip("\n".join(tip))

    # ------------------------------------------------------------ time series
    def _on_time_slider(self, idx: int) -> None:
        if not self._series or idx < 0 or idx >= len(self._series):
            return
        if self._read_into_grid(self._series[idx]):
            if self._arrays_match_selection():
                # same arrays as before: keep the combo, only update the range
                # and repaint once (a full _populate_arrays rebuild used to
                # redraw twice per frame and made the view flicker)
                self._on_array_changed()
            else:
                self._populate_arrays()  # rebuilds arrays + rescales + refreshes
            self._log_title(self._series[idx])

    def _on_play_toggled(self, on: bool) -> None:
        self.btn_play.setText("■ Stop" if on else "▶ Play")
        if on and self._series:
            if self.slider_time.value() >= len(self._series) - 1:
                self.slider_time.setValue(0)  # restart from the beginning
            self._timer.start()
        else:
            self._timer.stop()

    def _on_fps_changed(self, fps: int) -> None:
        self._timer.setInterval(max(1, int(round(1000.0 / max(1, fps)))))

    def _on_play_tick(self) -> None:
        if not self._series:
            return
        cur = self.slider_time.value()
        if cur >= len(self._series) - 1:
            if not self.chk_loop.isChecked():
                self.btn_play.setChecked(False)
                return
            nxt = 0
        else:
            nxt = cur + 1
        self.slider_time.setValue(nxt)

    def _autoplay(self) -> None:
        if self._series and self.chk_autoplay.isChecked():
            self.btn_play.setChecked(True)

    def _log_title(self, path: Path) -> None:
        n = self._grid.GetNumberOfPoints() if self._grid else 0
        name = self._scalar_name
        lo, hi = self._data_range
        if name:
            unit = f" {self._field_unit}" if self._field_unit else ""
            self.lbl_level.setText(
                f"{name} {lo:.3g} … {hi:.3g}{unit}  ({n} pts)"
            )
        else:
            self.lbl_level.setText(f"{n} points")
        if self._series:
            i = self.slider_time.value()
            self.lbl_time_info.setText(
                f"{i + 1}/{len(self._series)}  ({self._step_label(i)})"
            )
            motion = self._vector_motion_text(i)
            self.lbl_motion.setText(motion)
            self.lbl_motion.setToolTip(
                motion or "Arrow motion compared with the previous step."
            )
        else:
            self.lbl_motion.setText("")
            self.lbl_motion.setToolTip("Arrow motion compared with the previous step.")
        self._update_sim_time_label()
        self._update_field_label(path)
        print(f"VTK viewer: loaded {path.name} ({n} points, scalar={name})")

    def _step_label(self, i: int) -> str:
        """``step 6324`` for openEMS TD keys, else the sidecar label as-is."""
        lab = self._series_labels[i] if i < len(self._series_labels) else str(i)
        m = re.fullmatch(r"t(\d+)", str(lab))
        return f"step {m.group(1)}" if m else str(lab)

    def _vector_motion_text(self, idx: int) -> str:
        """How much the arrows re-orient from the previous step, as short text.

        Answers "do the vectors actually move?" with a number: the share of
        arrows that flipped and the median cosine between the field direction
        of this and the previous step (-1 = reversed, +1 = unchanged). Only
        while stepping by hand — during Play the extra file reads are not worth
        it.
        """
        if not self._series or not self.chk_vectors.isChecked():
            return ""
        if self.btn_play.isChecked() or idx <= 0 or idx >= len(self._series):
            return ""
        name = self._vector_array_name()
        if not name:
            return ""
        import numpy as np
        from vtk.util import numpy_support as ns

        vtk = getattr(self, "_vtk", None) or _vtk()

        def directions(path):
            try:
                reader = _make_reader(vtk, path)
                reader.SetFileName(str(path))
                reader.Update()
                arr = reader.GetOutput().GetPointData().GetArray(name)
            except Exception:  # noqa: BLE001 — a broken frame: no readout
                return None
            if arr is None or arr.GetNumberOfTuples() == 0:
                return None
            vals = np.asarray(ns.vtk_to_numpy(arr), dtype=float)
            if vals.ndim != 2:
                return None
            mags = np.linalg.norm(vals, axis=1)
            ok = mags > 1e-30
            if not ok.any():
                return None
            return vals[ok] / mags[ok, None]

        prev = directions(self._series[idx - 1])
        cur = directions(self._series[idx])
        if prev is None or cur is None or prev.shape != cur.shape or not prev.size:
            return ""
        cos = np.sum(prev * cur, axis=1)
        return (
            f"arrows: {100.0 * float(np.mean(cos < 0)):.0f}% reversed vs. previous "
            f"step (median cos {float(np.median(cos)):+.2f})"
        )

    @staticmethod
    def format_sim_time(seconds: float | None) -> str:
        """``t = 339.9 ps`` / ``t = 1.268 ns`` (auto unit)."""
        if seconds is None:
            return ""
        a = abs(seconds)
        if a < 1e-9:
            return f"t = {seconds * 1e12:.4g} ps"
        if a < 1e-6:
            return f"t = {seconds * 1e9:.4g} ns"
        if a < 1e-3:
            return f"t = {seconds * 1e6:.4g} µs"
        return f"t = {seconds:.4g} s"

    @staticmethod
    def format_frequency(hz: float | None) -> str:
        if hz is None:
            return ""
        a = abs(hz)
        if a >= 1e9:
            return f"f = {hz / 1e9:.4g} GHz"
        if a >= 1e6:
            return f"f = {hz / 1e6:.4g} MHz"
        if a >= 1e3:
            return f"f = {hz / 1e3:.4g} kHz"
        return f"f = {hz:.4g} Hz"

    def _update_sim_time_label(self) -> None:
        text = ""
        if self._series:
            i = self.slider_time.value()
            if 0 <= i < len(self._series_times):
                t = self._series_times[i]
                f = self._series_freqs[i] if i < len(self._series_freqs) else None
                text = self.format_frequency(f) or self.format_sim_time(t)
        self.lbl_sim_time.setText(text)
        self.lbl_sim_time.setVisible(bool(text) and getattr(self, "_time_controls_on", False))

    # ------------------------------------------------------------ video export
    def _default_video_path(self, suffix: str = ".mp4") -> Path:
        """``<series>.<suffix>`` next to the loaded frames (``Et-E_0000`` -> Et-E)."""
        p = self._current_path or (self._series[0] if self._series else None)
        if p is None:
            return Path("field" + suffix)
        p = Path(p)
        stem = p.stem
        m = re.match(r"(?P<base>.+)_(?P<idx>\d+)$", stem)
        if m and self._series:
            stem = m.group("base")
        return p.parent / (stem + suffix)

    def _select_array(self, loc, name) -> None:
        for i in range(self.cmb_array.count()):
            if self.cmb_array.itemData(i) == (loc, name):
                self.cmb_array.setCurrentIndex(i)  # emits -> refreshes the view
                return

    @staticmethod
    def _image_to_rgb(image):
        """``vtkImageData`` (RGB) -> top-down, even-sized uint8 ``(h, w, 3)``."""
        import numpy as np
        from vtk.util import numpy_support as ns

        w, h, _ = image.GetDimensions()
        arr = ns.vtk_to_numpy(image.GetPointData().GetScalars()).reshape(h, w, -1)
        rgb = arr[::-1, :, :3]  # VTK images are bottom-up
        h -= h % 2  # H.264 needs even dimensions
        w -= w % 2
        return np.ascontiguousarray(rgb[:h, :w])

    def _grab_frame_rgb(self, target_size: tuple[int, int] | None = None):
        """Render the current view and return it as an RGB numpy array.

        ``target_size`` letterboxes/scales the grabbed frame to the requested
        output resolution; the VTK scene is not resized or cropped.
        """
        rw = self._iren.GetRenderWindow()
        rw.Render()
        grab = self._vtk.vtkWindowToImageFilter()
        grab.SetInput(rw)
        grab.ReadFrontBufferOff()  # read the back buffer (no swap needed)
        grab.SetInputBufferTypeToRGB()
        grab.Update()
        frame = self._image_to_rgb(grab.GetOutput())
        if target_size:
            tw = max(2, int(target_size[0]))
            th = max(2, int(target_size[1]))
            tw -= tw % 2
            th -= th % 2
            if (frame.shape[1], frame.shape[0]) != (tw, th):
                from PIL import Image, ImageOps
                import numpy as np

                img = Image.fromarray(frame)
                img = ImageOps.pad(
                    img,
                    (tw, th),
                    method=Image.Resampling.LANCZOS,
                    color=(0, 0, 0),
                )
                frame = np.asarray(img)
        return frame

    def _apply_frame_range(self, loc, name, auto: bool) -> None:
        """Update the data range for a new frame without touching the widgets."""
        if not name or self._grid is None:
            return
        data = self._grid.GetPointData() if loc == "point" else self._grid.GetCellData()
        arr = data.GetArray(name)
        rng = arr.GetRange() if arr else (0.0, 1.0)
        self._data_range = (float(rng[0]), float(rng[1]))
        if auto:
            # same mapping as during playback: Auto follows the current frame
            self._robust_min = _positive_percentile(arr, 1.0) if arr else None
        self._scalar_range = self._data_range
        self._scalar_name = name

    def _on_export_video(self) -> None:
        self.open_video_export_dialog(fmt="mp4")

    def _on_export_gif(self) -> None:
        self.open_video_export_dialog(fmt="gif")

    def open_video_export_dialog(self, fmt: str = "mp4") -> None:
        """Open the video-export options dialog and run the export."""
        is_gif = str(fmt).strip().lower() == "gif"
        if self._iren is None or self._renderer is None:
            QMessageBox.information(
                self,
                "Save animation" if is_gif else "Save video",
                "The 3D view is not available (no OpenGL), so no animation can "
                "be rendered.",
            )
            return
        if len(self._series) < 2:
            QMessageBox.information(
                self,
                "Save animation" if is_gif else "Save video",
                "Load a time series first (multiple steps), e.g. via\n"
                "\"Export project dumps → VTK\".",
            )
            return
        from app.ui.video_export_dialog import VideoExportDialog

        try:
            size = self._iren.GetRenderWindow().GetSize()
        except Exception:  # noqa: BLE001
            size = (0, 0)
        if int(size[0]) <= 2 or int(size[1]) <= 2:
            size = (1280, 720)
        dialog = VideoExportDialog(
            self,
            suggested_path=self._default_video_path(".gif" if is_gif else ".mp4"),
            frame_count=len(self._series),
            current_size=(int(size[0]), int(size[1])),
            initial_fmt="gif" if is_gif else "mp4",
        )
        if dialog.exec() and dialog.options is not None:
            self.export_video(options=dialog.options)

    def export_video(
        self,
        path: Path | str | None = None,
        fmt: str | None = None,
        options=None,
    ) -> Path | None:
        """Render the loaded time series to MP4 or an animated GIF.

        ``fmt`` is ``"mp4"`` (default) or ``"gif"`` and only chooses the dialog
        default/filter; an explicit ``path`` suffix wins.  ``options`` is a
        :class:`app.ui.video_export_dialog.VideoExportOptions` from the export
        dialog: it supplies the exact path, size, fps and frame range.  All
        display settings are used as they are (representation, array,
        slice/iso position, colour range/scale, outline, axes, camera).  MP4
        needs imageio + imageio-ffmpeg; GIF only needs Pillow.
        Returns the written file (None on cancel/error).
        """
        preferred = str(getattr(options, "fmt", fmt) or "mp4").strip().lower()
        is_gif = preferred == "gif"
        if self._iren is None or self._renderer is None:
            QMessageBox.information(
                self,
                "Save animation" if is_gif else "Save video",
                "The 3D view is not available (no OpenGL), so no animation can "
                "be rendered.",
            )
            return None
        n = len(self._series)
        if n < 2:
            QMessageBox.information(
                self,
                "Save animation" if is_gif else "Save video",
                "Load a time series first (multiple steps), e.g. via\n"
                "\"Export project dumps → VTK\".",
            )
            return None

        target_size: tuple[int, int] | None = None
        if options is not None:
            is_gif = str(getattr(options, "fmt", "mp4")).lower() == "gif"
            target = Path(options.path)
            fps = max(1, int(getattr(options, "fps", 24)))
            target_size = (
                max(2, int(getattr(options, "width", 1280))),
                max(2, int(getattr(options, "height", 720))),
            )
            total = len(self._series)
            start = max(1, min(total, int(getattr(options, "start", 1))))
            end = max(start, min(total, int(getattr(options, "end", total))))
            step = max(1, int(getattr(options, "step", 1)))
            frame_paths = self._series[start - 1:end:step]
            n = len(frame_paths)
            if n < 1:
                QMessageBox.warning(
                    self,
                    "Save animation" if is_gif else "Save video",
                    "The selected frame range is empty.",
                )
                return None
        else:
            target = Path(path) if path else None
            if target is not None:
                suffix = target.suffix.lower()
                if suffix == ".gif":
                    is_gif = True
                elif suffix in (".mp4", ".m4v"):
                    is_gif = False
                elif not suffix:
                    target = target.with_suffix(".gif" if is_gif else ".mp4")

            if target is None:
                default_suffix = ".gif" if is_gif else ".mp4"
                filters = (
                    "Animated GIF (*.gif);;MP4 video (*.mp4);;All files (*)"
                    if is_gif
                    else "MP4 video (*.mp4);;Animated GIF (*.gif);;All files (*)"
                )
                chosen, selected = QFileDialog.getSaveFileName(
                    self,
                    "Save field animation" if is_gif else "Save field video",
                    str(self._default_video_path(default_suffix)),
                    filters,
                )
                if not chosen:
                    return None
                target = Path(chosen)
                suffix = target.suffix.lower()
                sel = (selected or "").lower()
                if "gif" in sel:
                    # the chosen filter wins; make sure the extension matches it
                    is_gif = True
                    if suffix != ".gif":
                        target = target.with_suffix(".gif")
                elif "mp4" in sel or "m4v" in sel:
                    is_gif = False
                    if suffix not in (".mp4", ".m4v"):
                        target = target.with_suffix(".mp4")
                elif suffix == ".gif":
                    is_gif = True
                elif suffix in (".mp4", ".m4v"):
                    is_gif = False
                else:
                    # "All files" without a known suffix: keep the requested format
                    target = target.with_suffix(".gif" if is_gif else ".mp4")
            fps = max(1, int(self.spn_fps.value()))
            frame_paths = list(self._series)
            n = len(frame_paths)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(
                self, "Save animation" if is_gif else "Save video",
                f"Cannot create folder:\n{e}",
            )
            return None

        loc, name = self._array()
        auto = self.chk_auto.isChecked()
        manual = (self.spn_min.value(), self.spn_max.value())
        play_on = self.btn_play.isChecked()
        cur = self.slider_time.value()
        self._timer.stop()
        self.btn_play.blockSignals(True)
        self.btn_play.setChecked(False)
        self.btn_play.setText("▶ Play")
        self.btn_play.blockSignals(False)

        progress = QProgressDialog(
            "Rendering field animation…" if is_gif else "Rendering field video…",
            "Cancel",
            0,
            n,
            self,
        )
        progress.setWindowTitle("Save animation" if is_gif else "Save video")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        written = 0
        writer = None
        cancelled = False
        try:
            try:
                import imageio
            except ImportError:
                QMessageBox.warning(
                    self,
                    "Save animation" if is_gif else "Save video",
                    "Animation export needs imageio\n(pip install imageio).",
                )
                return None
            if is_gif:
                # GIF uses imageio's Pillow plugin - no ffmpeg needed.
                try:
                    from PIL import Image  # noqa: F401
                except ImportError:
                    QMessageBox.warning(
                        self,
                        "Save animation",
                        "GIF export needs Pillow\n(pip install Pillow).",
                    )
                    return None
                try:
                    writer = imageio.get_writer(
                        str(target),
                        mode="I",
                        duration=gif_frame_duration_ms(fps),
                        loop=0,  # loop forever
                    )
                except Exception as e:  # noqa: BLE001 — Pillow/GIF writer broken
                    QMessageBox.warning(
                        self, "Save animation", f"Cannot open the GIF writer:\n{e}"
                    )
                    return None
            else:
                # The distributed EXE does not bundle ffmpeg (it would add ~60 MB
                # to the download); the user installs imageio-ffmpeg once or points
                # IMAGEIO_FFMPEG_EXE at an ffmpeg.exe.
                try:
                    import imageio_ffmpeg  # noqa: F401
                except ImportError:
                    QMessageBox.warning(
                        self,
                        "Save video",
                        "MP4 export needs ffmpeg, which this build does not bundle\n"
                        "(it would add ~60 MB to the download).\n\n"
                        "Install it once with:  pip install imageio-ffmpeg\n"
                        "or set IMAGEIO_FFMPEG_EXE to an ffmpeg.exe on this machine.",
                    )
                    return None
                try:
                    writer = imageio.get_writer(
                        str(target),
                        fps=fps,
                        codec="libx264",
                        quality=8,
                        macro_block_size=1,  # keep the exact render size
                    )
                except Exception as e:  # noqa: BLE001 — ffmpeg missing/broken
                    QMessageBox.warning(
                        self, "Save video", f"Cannot open the video writer:\n{e}"
                    )
                    return None
            for i, frame_path in enumerate(frame_paths, start=1):
                if progress.wasCanceled():
                    cancelled = True
                    break
                if not self._read_into_grid(frame_path):
                    continue
                self._apply_frame_range(loc, name, auto)
                self._refresh_display()
                frame = (
                    self._grab_frame_rgb(target_size)
                    if target_size
                    else self._grab_frame_rgb()
                )
                writer.append_data(frame)
                written += 1
                progress.setValue(i)
                progress.setLabelText(
                    f"Rendering field "
                    f"{'animation' if is_gif else 'video'}… {i}/{n}  "
                    f"({int(100 * i / n)} %)"
                )
                QApplication.processEvents()
        finally:
            progress.close()
            if writer is not None:
                try:
                    writer.close()
                except Exception:  # noqa: BLE001
                    pass
            # restore the frame/array/colour the user had before the export
            self._restore_after_export(cur, loc, name, auto, manual, play_on)

        if cancelled or not written:
            try:
                target.unlink()
            except OSError:
                pass
            if not cancelled:
                QMessageBox.warning(
                    self,
                    "Save animation" if is_gif else "Save video",
                    "No frames could be rendered.",
                )
            return None
        QMessageBox.information(
            self,
            "Save animation" if is_gif else "Save video",
            f"{'Animation' if is_gif else 'Video'} written:\n{target}\n\n"
            f"{written} frames at {fps} fps.",
        )
        return target

    def _restore_after_export(self, index, loc, name, auto, manual, play_on) -> None:
        """Put the viewer back on the frame/settings it showed before export."""
        if self._series and 0 <= index < len(self._series):
            if self._read_into_grid(self._series[index]):
                self._populate_arrays()
                self._select_array(loc, name)
                self.slider_time.blockSignals(True)
                self.slider_time.setValue(index)
                self.slider_time.blockSignals(False)
                if not auto:
                    for spn, value in ((self.spn_min, manual[0]), (self.spn_max, manual[1])):
                        spn.blockSignals(True)
                        spn.setValue(value)
                        spn.blockSignals(False)
                    self._update_color_widgets()
                    self._refresh_display()
                self._log_title(self._series[index])
        if play_on and self._series:
            self.btn_play.setChecked(True)
