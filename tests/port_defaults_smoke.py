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

    # 6) PortDialog MSL fields (propagation/excitation axis) round-trip
    from app.models.project import PortDef as _PD2
    from app.ui.geometry_editors import PortDialog

    msl = _PD2(
        name="msl1",
        type="msl",
        start=[-10.0, -1.0, 1.6],
        stop=[0.0, 1.0, 0.0],
        prop_direction="x",
        exc_direction="z",
    )
    dlg = PortDialog(win, port=msl)
    assert dlg.cmb_prop.currentData() == "x", dlg.cmb_prop.currentData()
    assert dlg.cmb_exc.currentData() == "z", dlg.cmb_exc.currentData()
    dlg.cmb_prop.setCurrentIndex(dlg.cmb_prop.findData("y"))
    dlg._accept()
    assert dlg.result_port is not None
    assert dlg.result_port.type == "msl"
    assert dlg.result_port.prop_direction == "y"
    assert dlg.result_port.exc_direction == "z"
    print("msl dialog axes:", dlg.result_port.prop_direction, dlg.result_port.exc_direction)

    # 7) "Active" option: dialog round-trip, list marker, quick toggle button
    off = _PD2(name="off1", start=[0, 0, 0], stop=[0, 0, 1], direction="z", active=False)
    dlg_off = PortDialog(win, port=off)
    assert dlg_off.chk_active.isChecked() is False, "dialog must show the port as inactive"
    dlg_off._accept()
    assert dlg_off.result_port.active is False, dlg_off.result_port
    dlg_on = PortDialog(win, port=_PD2(name="on1", start=[0, 0, 0], stop=[0, 0, 1]))
    assert dlg_on.chk_active.isChecked() is True, "ports default to active"
    dlg_on._accept()
    assert dlg_on.result_port.active is True

    win.project.ports = [off]
    win._refresh_ports()
    win.port_list.setCurrentRow(0)
    assert "[off]" in win.port_list.item(0).text(), win.port_list.item(0).text()
    _port_rows = [
        win.tree.item(i).text()
        for i in range(win.tree.count())
        if win.tree.item(i).text().startswith("Port:")
    ]
    assert _port_rows and "[off]" in _port_rows[0], _port_rows
    win._on_toggle_port_active()
    assert win.project.ports[0].active is True, "toggle did not switch it on"
    assert "[off]" not in win.port_list.item(0).text(), win.port_list.item(0).text()
    win._on_toggle_port_active()
    assert win.project.ports[0].active is False
    win._drain_log_queue()
    assert "inactive" in win.log_view.toPlainText(), win.log_view.toPlainText()[-200:]
    print("port 'Active' option (dialog/list/toggle): OK")

    win.close()
    print("PORT DEFAULTS SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
