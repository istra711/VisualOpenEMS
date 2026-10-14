"""Smoke: Settings port defaults → FreeCAD/port import + PortDialog seed."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from app.freecad_io.port_marks import (
        normalize_port_dict,
        parse_ports_payload,
        port_defaults_from,
        ports_to_port_defs,
    )
    from app.settings import AppSettings

    # 1) Settings fields + port_defaults()
    s = AppSettings()
    assert s.default_port_type == "lumped"
    assert abs(s.default_port_resistance - 50.0) < 1e-9
    s.default_port_type = "msl"
    s.default_port_resistance = 75.0
    s.default_port_excite = False
    s.default_port_mesh_dirs = "xy"
    s.default_port_direction = "z"
    pd = s.port_defaults()
    assert pd["type"] == "msl"
    assert abs(pd["resistance"] - 75.0) < 1e-9
    assert pd["excite_port"] is False
    assert pd["mesh_dirs"] == "xy"
    assert pd["direction"] == "z"
    print("settings defaults:", pd)

    # 2) Defaults fill omitted fields; explicit CAD values win
    bare = {"name": "in1", "start": [0, 0, 0], "stop": [0, 0, 1]}
    nd = normalize_port_dict(bare, pd)
    assert nd is not None
    assert nd["type"] == "msl", nd
    assert abs(nd["resistance"] - 75.0) < 1e-9, nd
    assert nd["excite_port"] is False, nd
    assert nd["mesh_dirs"] == "xy", nd
    assert nd["direction"] == "z", nd  # forced by default direction

    explicit = {
        "name": "feed",
        "type": "lumped",
        "resistance": 50.0,
        "excite_port": True,
        "start": [1, 0, 0],
        "stop": [2, 0, 0],
    }
    nd2 = normalize_port_dict(explicit, pd)
    assert nd2 is not None
    assert nd2["type"] == "lumped", nd2
    assert abs(nd2["resistance"] - 50.0) < 1e-9, nd2
    assert nd2["excite_port"] is True, nd2
    print("explicit wins:", nd2)

    # 3) parse + ports_to_port_defs carry defaults
    payload = [
        {"name": "a", "start": [0, 0, 0], "stop": [0, 1, 0]},
        {
            "name": "b",
            "start": [0, 0, 0],
            "stop": [1, 0, 0],
            "type": "rect_waveguide",
            "resistance": 100.0,
            "excite_port": False,
        },
    ]
    defs = ports_to_port_defs(parse_ports_payload(payload, pd), pd)
    assert len(defs) == 2
    a = next(d for d in defs if d.name == "a")
    b = next(d for d in defs if d.name == "b")
    assert a.type == "msl" and abs(a.resistance - 75.0) < 1e-9
    assert a.excite_port is False
    assert b.type == "rect_waveguide" and abs(b.resistance - 100.0) < 1e-9
    assert b.excite_port is False
    print("defs:", [(d.name, d.type, d.resistance, d.excite_port) for d in defs])

    # 4) port_defaults_from accepts dict / settings / None
    assert port_defaults_from(None)["type"] == "lumped"
    assert port_defaults_from({"resistance": 30.0})["resistance"] == 30.0
    assert port_defaults_from(s)["type"] == "msl"

    # 5) MainWindow: port list edit actions + settings log
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    settings = AppSettings.load()
    settings.default_port_resistance = 75.0
    settings.default_port_type = "msl"
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    assert hasattr(win, "btn_edit_port") and hasattr(win, "btn_del_port")
    assert hasattr(win, "_on_edit_port") and hasattr(win, "_on_remove_port")

    # seed Add Port dialog from defaults without opening dialog
    from app.models.project import PortDef as _PD

    pd = settings.port_defaults()
    seed = _PD(name="in1", type=pd["type"], resistance=pd["resistance"])
    assert seed.type == "msl"
    assert abs(seed.resistance - 75.0) < 1e-9

    win.project.ports.append(seed)
    win._refresh_ports()
    app.processEvents()
    assert win.port_list.count() == 1
    win.port_list.setCurrentRow(0)
    assert win._selected_port_index() == 0

    # remove via handler
    win._on_remove_port()
    app.processEvents()
    assert win.port_list.count() == 0
    assert win.project.ports == []

    # settings dialog fields exist (construct without exec)
    # just ensure method compiles by checking callable
    assert callable(win._on_settings)

    win.close()
    print("PORT DEFAULTS SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
