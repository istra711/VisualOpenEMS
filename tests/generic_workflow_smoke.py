"""Smoke: generic antenna/PCB workflow (not patch-only)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.settings import AppSettings  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow
    from app.ui.geometry_editors import PortDialog, PrimitiveBoxDialog

    settings = AppSettings.load()
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    # Generic new project — patch preset hidden
    win._on_new()
    app.processEvents()
    assert not win.sim_params.patch_group_visible, "patch group should be hidden"
    assert "substrate" in [m.name for m in win.project.materials]

    # Box primitive without patch preset
    win.primitives.append(
        {
            "kind": "box",
            "name": "trace",
            "material": "copper",
            "priority": 10,
            "start": [0.0, 0.0, 0.0],
            "stop": [20.0, 2.0, 0.035],
            "edges2grid": "xy",
        }
    )
    win.primitives.append(
        {
            "kind": "box",
            "name": "gnd",
            "material": "copper",
            "priority": 5,
            "start": [0.0, 0.0, -0.5],
            "stop": [20.0, 10.0, 0.0],
        }
    )
    win._refresh_tree()

    # Port dialog produces a real PortDef
    pd = PortDialog()
    pd.ed_name.setText("feed")
    pd.cmb_type.setCurrentText("lumped")
    pd.spn_start[0].setValue(0.0)
    pd.spn_start[1].setValue(1.0)
    pd.spn_start[2].setValue(0.0)
    pd.spn_stop[0].setValue(0.0)
    pd.spn_stop[1].setValue(1.0)
    pd.spn_stop[2].setValue(0.035)
    pd._accept()
    assert pd.result_port is not None
    win.project.ports.append(pd.result_port)
    win._refresh_ports()

    # Primitive dialog
    bd = PrimitiveBoxDialog()
    bd.ed_name.setText("via")
    bd.spn_start[2].setValue(0.0)
    bd.spn_stop[2].setValue(1.0)
    bd._accept()
    assert bd.result is not None and bd.result["kind"] == "box"

    # NF2FF fit from primitives
    win._on_fit_nf2ff()
    app.processEvents()
    assert win.project.nf2ff.enabled
    n = win.project.nf2ff
    assert n.stop[0] > n.start[0] and n.stop[1] > n.start[1]

    # Save/load project keeps generic primitives
    win._sync_project_from_form()
    win.project.primitives = list(win.primitives)
    out = ROOT / "work" / "generic_smoke" / "generic.fcsim.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    win.project.save(out)
    from app.models.project import Project

    loaded = Project.load(out)
    assert loaded.primitives and loaded.primitives[0]["name"] == "trace"
    assert loaded.ports and loaded.ports[0].name == "feed"
    assert loaded.patch_params is None

    # Patch preset still available and shows its group
    win._on_load_patch()
    app.processEvents()
    assert win.sim_params.patch_group_visible
    assert win.project.patch_params is not None

    win.close()
    print("GENERIC WORKFLOW OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
