"""Smoke: Mesh Viewer "Port mode" (right-click) places a port at a click.

Offscreen Qt. Builds a real mesh (synchronous path) so the viewer has a CSX /
slice, arms port mode, fakes a left click and stubs the port dialog: the port
must be appended, appear in the component/port list and the red placement marker
must be cleared afterwards.

The box is derived from the metal surfaces around the click:
* Lumped: a line along the slice normal from the bottom face of the clicked
  metal to the top face of the next metal surface (below, else above).
* MSL: the same box, but as wide as the clicked metal along the chosen in-plane
  axis and one mesh cell long along the line (propagation) axis.

Standalone (``python tests/mesh_port_place_smoke.py``).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from PySide6.QtWidgets import QApplication, QMenu

    from app.models.project import PortDef, Project
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="mesh_port_smoke_"))

    settings = AppSettings.load()
    settings.work_dir = str(tmp)
    win = MainWindow(settings)
    win.project = Project.new_default("mesh_port")
    # ground plane + strip above it: the port must span the substrate between them
    win.primitives = [
        {
            "kind": "box",
            "name": "gnd",
            "material": "copper",
            "priority": 10,
            "start": [0.0, 0.0, 0.0],
            "stop": [10.0, 5.0, 0.035],
        },
        {
            "kind": "box",
            "name": "strip",
            "material": "copper",
            "priority": 10,
            "start": [0.0, 2.0, 1.0],
            "stop": [10.0, 3.0, 1.035],
        },
    ]
    win.project.primitives = list(win.primitives)

    # synchronous build -> the viewer gets a CSX (slices + plane slider usable)
    win._build_and_validate()
    v = win.mesh_viewer
    assert v._csx is not None, "mesh build did not reach the viewer"

    # --- port mode toggle -------------------------------------------------
    assert v._port_mode is False
    seen_modes: list[bool] = []
    v.port_mode_changed.connect(seen_modes.append)
    v.set_port_mode(True)
    assert v._port_mode is True and seen_modes == [True], seen_modes
    win._drain_log_queue()  # the mode change logs a hint
    assert "Port mode ON" in win.log_view.toPlainText(), win.log_view.toPlainText()[-300:]

    # --- the port type submenu (right-click) ------------------------------
    menu = QMenu()
    v._add_port_type_menu(menu)
    sub = menu.actions()[0].menu()
    labels = [a.text() for a in sub.actions()]
    # XY slice -> the in-plane axes are X and Y
    assert labels == ["Lumped", "MSL, width along X", "MSL, width along Y"], labels
    assert sub.actions()[0].isChecked(), "Lumped must be the default"
    types_seen: list[tuple[str, str]] = []
    v.port_type_changed.connect(lambda k, o: types_seen.append((k, o)))
    sub.actions()[2].trigger()  # MSL, width along Y
    assert v.port_type() == ("msl", "y"), v.port_type()
    assert types_seen == [("msl", "y")], types_seen
    assert v._port_mode is True, "picking MSL must arm port mode"
    win._drain_log_queue()
    assert "Port type: MSL" in win.log_view.toPlainText()
    v.set_port_type("lumped")
    assert v.port_type() == ("lumped", "y")

    # --- fake click in the XY slice, ON the strip -------------------------
    click_x, click_y = 4.25, 2.5  # inside the strip (x 0..10, y 2..3)
    seen: dict = {}

    class _StubDialog:
        """Stand-in for PortDialog: records the seed + the red marker state."""

        def __init__(self, parent=None, port=None, coord_system="cartesian") -> None:
            seen["seed"] = port
            seen["coord_system"] = coord_system

        def exec(self) -> int:
            seen["marker"] = v._port_marker  # shown while the dialog is open
            return 1  # Accepted

        @property
        def result_port(self):
            p = seen["seed"]
            stop = list(p.stop)
            if stop == list(p.start):  # give a degenerate seed a gap (as before)
                stop = [p.start[0], p.start[1], p.start[2] + 1.0]
            return PortDef(
                name=p.name,
                type=p.type,
                start=list(p.start),
                stop=stop,
                resistance=p.resistance,
                direction=p.direction,
                prop_direction=p.prop_direction,
                exc_direction=p.exc_direction,
                excite_port=p.excite_port,
                mesh_dirs=p.mesh_dirs,
            )

    # the plane sits inside the strip (so the strip is the "current" metal)
    v.spn_plane.setValue(1.01)
    plane = float(v.spn_plane.value())

    def _click(x: float, y: float) -> None:
        """Simulate a slice left-click (never a double-click) + drain the log."""
        v._last_click_t = 0.0
        ev = SimpleNamespace(button=1, key=None, inaxes=object(), xdata=x, ydata=y)
        with patch("app.ui.geometry_editors.PortDialog", _StubDialog):
            v._on_slice_click(ev)
        win._drain_log_queue()

    n_ports = len(win.project.ports)
    _click(click_x, click_y)

    assert len(win.project.ports) == n_ports + 1, win.project.ports
    port = win.project.ports[-1]
    assert port.type == "lumped", port.type
    # in-plane coordinates come from the click, unsnapped
    assert abs(port.start[0] - click_x) < 1e-12, port.start
    assert abs(port.start[1] - click_y) < 1e-12, port.start
    # ... and the length was found automatically: bottom of the strip down to
    # the top of the ground plane
    assert abs(port.start[2] - 1.0) < 1e-4, port.start
    assert abs(port.stop[2] - 0.035) < 1e-4, port.stop
    assert port.direction == "z", port.direction
    assert seen["coord_system"] == "cartesian", seen["coord_system"]
    log = win.log_view.toPlainText()
    assert "strip" in log and "gnd" in log and "below" in log, log[-400:]
    print("  lumped port spans strip -> ground:", port.start, "->", port.stop)

    # red marker existed while the dialog was open, cleared afterwards
    assert seen["marker"] is not None, "no red marker while the dialog was open"
    assert v._port_marker is None, "red marker not cleared after the dialog"

    # ... and it shows up in the component / port lists
    assert win.port_list.count() == n_ports + 1, win.port_list.count()
    names = [win.port_list.item(i).text() for i in range(win.port_list.count())]
    assert any(port.name in n for n in names), names
    assert v._ports and v._ports[-1].name == port.name, "viewer overlay not updated"

    # --- MSL: width from the metal, one mesh cell along the line ----------
    v.set_port_type("msl", "y")
    n_ports = len(win.project.ports)
    _click(click_x, click_y)
    msl = win.project.ports[-1]
    assert len(win.project.ports) == n_ports + 1, win.project.ports
    assert msl.type == "msl", msl.type
    # width along y = the strip's width (2..3)
    assert abs(min(msl.start[1], msl.stop[1]) - 2.0) < 1e-4, msl
    assert abs(max(msl.start[1], msl.stop[1]) - 3.0) < 1e-4, msl
    # propagation along x: one mesh cell (non-zero, far below the strip length)
    prop = abs(msl.stop[0] - msl.start[0])
    assert 0.0 < prop < 10.0, msl
    assert msl.prop_direction == "x" and msl.exc_direction == "z", (
        msl.prop_direction,
        msl.exc_direction,
    )
    # excitation spans the substrate (strip bottom -> ground top)
    assert abs(msl.start[2] - 1.0) < 1e-4 and abs(msl.stop[2] - 0.035) < 1e-4, msl
    assert "width along Y" in win.log_view.toPlainText()
    # the axes resolve for the generator (openEMS AddMSLPort)
    from app.simulation.msl import resolve_msl_axes

    assert resolve_msl_axes(msl) == ("x", "z"), resolve_msl_axes(msl)
    print("  MSL port: width", min(msl.start[1], msl.stop[1]), "->",
          max(msl.start[1], msl.stop[1]), "prop", prop)

    # --- click next to the strip: no second metal -> click as both ends ---
    v.set_port_type("lumped")
    n_ports = len(win.project.ports)
    _click(4.0, 4.5)
    fallback = win.project.ports[-1]
    assert len(win.project.ports) == n_ports + 1
    assert abs(fallback.start[0] - 4.0) < 1e-12, fallback.start
    assert abs(fallback.start[1] - 4.5) < 1e-12, fallback.start
    assert abs(fallback.start[2] - plane) < 1e-9, (fallback.start, plane)
    assert "no second metal surface" in win.log_view.toPlainText()
    print("  fallback (no second metal): click kept as", fallback.start)

    # port mode must NOT add manual mesh lines (no snapping / no editor writes)
    spec = win.project.mesh
    lines = getattr(spec, "manual_mesh_lines", None) or {}
    assert not any(lines.values()), lines

    # --- port mode off ----------------------------------------------------
    v.set_port_mode(False)
    assert v._port_mode is False and seen_modes[-1] is False, seen_modes
    n_ports = len(win.project.ports)
    placed: list = []
    v.port_place_requested.connect(lambda *a: placed.append(a))
    v._last_click_t = 0.0
    v._on_slice_click(
        SimpleNamespace(button=1, key=None, inaxes=object(), xdata=4.25, ydata=2.5)
    )  # port mode off -> editor path, no port
    assert not placed, placed
    assert len(win.project.ports) == n_ports

    win.close()
    print("MESH PORT PLACE SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
