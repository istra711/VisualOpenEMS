"""Smoke: Mesh Viewer "Port mode" (right-click) places a port at a click.

Offscreen Qt. Builds a real mesh (synchronous path) so the viewer has a CSX /
slice, arms port mode, fakes a left click (unsnapped coordinates) and stubs the
port dialog: the port must be appended, appear in the component/port list and
the red placement marker must be cleared afterwards.

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
    from PySide6.QtWidgets import QApplication

    from app.models.project import PortDef, Project
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="mesh_port_smoke_"))

    settings = AppSettings.load()
    settings.work_dir = str(tmp)
    win = MainWindow(settings)
    win.project = Project.new_default("mesh_port")
    win.primitives = [
        {
            "kind": "box",
            "name": "board",
            "material": "copper",
            "priority": 10,
            "start": [0.0, 0.0, 0.0],
            "stop": [10.0, 5.0, 1.6],
        }
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

    # --- fake click in the XY slice (default view) ------------------------
    click_x, click_y = 3.14159, -2.71828  # deliberately off any mesh line
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
            return PortDef(
                name=p.name,
                type=p.type,
                start=list(p.start),
                stop=[p.start[0], p.start[1], p.start[2] + 1.0],  # give it a gap
                resistance=p.resistance,
                direction=p.direction,
                excite_port=p.excite_port,
                mesh_dirs=p.mesh_dirs,
            )

    plane = float(v.spn_plane.value())
    n_ports = len(win.project.ports)
    event = SimpleNamespace(
        button=1, key=None, inaxes=object(), xdata=click_x, ydata=click_y
    )
    with patch("app.ui.geometry_editors.PortDialog", _StubDialog):
        v._on_slice_click(event)
    win._drain_log_queue()

    # --- the port was created at the clicked (unsnapped) coordinates ------
    assert len(win.project.ports) == n_ports + 1, win.project.ports
    port = win.project.ports[-1]
    assert abs(port.start[0] - click_x) < 1e-12, port.start
    assert abs(port.start[1] - click_y) < 1e-12, port.start
    assert abs(port.start[2] - plane) < 1e-12, (port.start, plane)
    assert port.start != port.stop, "stub must produce a usable port"
    assert seen["coord_system"] == "cartesian", seen["coord_system"]
    print("  port placed at unsnapped click:", port.name, port.start, "->", port.stop)

    # red marker existed while the dialog was open, cleared afterwards
    assert seen["marker"] is not None, "no red marker while the dialog was open"
    assert v._port_marker is None, "red marker not cleared after the dialog"

    # ... and it shows up in the component / port lists
    assert win.port_list.count() == n_ports + 1, win.port_list.count()
    names = [
        win.port_list.item(i).text() for i in range(win.port_list.count())
    ]
    assert any(port.name in n for n in names), names
    assert v._ports and v._ports[-1].name == port.name, "viewer overlay not updated"

    # port mode must NOT add manual mesh lines (no snapping / no editor writes)
    spec = win.project.mesh
    lines = getattr(spec, "manual_mesh_lines", None) or {}
    assert not any(lines.values()), lines

    # --- port mode off ----------------------------------------------------
    v.set_port_mode(False)
    assert v._port_mode is False and seen_modes == [True, False], seen_modes
    n_ports = len(win.project.ports)
    placed: list = []
    v.port_place_requested.connect(lambda *a: placed.append(a))
    v._on_slice_click(event)  # port mode off -> editor path, no port
    assert not placed, placed
    assert len(win.project.ports) == n_ports

    win.close()
    print("MESH PORT PLACE SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
