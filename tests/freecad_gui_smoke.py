"""Smoke: FreeCAD GUI resolve + FreeCADCmd export + import path helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from app.freecad_io.freecad_launcher import (
        export_cad_for_openems,
        freecad_paths,
        launch_freecad,
        list_export_files,
        resolve_freecad_gui,
    )
    from app.settings import AppSettings

    s = AppSettings.load()
    gui = s.freecad_gui_path()
    print("settings freecad_gui_path:", gui)
    assert gui is not None and gui.exists(), f"FreeCAD GUI not resolved: {gui}"
    cmd = Path(s.freecad_cmd)
    assert cmd.exists(), cmd
    assert resolve_freecad_gui(cmd, s.freecad_gui) is not None
    # case-insensitive freecad.exe on this machine
    assert (cmd.parent / "freecad.exe").exists() or gui.exists()

    paths = freecad_paths(s.freecad_cmd, s.freecad_gui, s.work_dir)
    print("paths:", paths.freecad_cmd, paths.freecad_gui)
    assert not paths.warnings, paths.warnings

    step = ROOT / "work" / "yagi_antenna" / "yagi_2p45.step"
    assert step.exists(), step
    out = ROOT / "work" / "freecad_export_smoke"
    ok, log, out_dir = export_cad_for_openems(
        step,
        out,
        freecad_cmd=s.freecad_cmd,
        freecad_gui=s.freecad_gui,
        deflection=0.2,
    )
    files = list_export_files(out_dir)
    print("export ok=", ok, "out=", out_dir, "n=", len(files))
    print([p.name for p in files])
    assert ok, log[-2000:]
    assert any(p.suffix.lower() in {".step", ".stp", ".stl"} for p in files), files
    assert any(p.suffix.lower() == ".json" for p in files), files

    # launch would open GUI — only verify path resolution, don't spawn UI here
    ok_path = resolve_freecad_gui(s.freecad_cmd, s.freecad_gui)
    assert ok_path is not None
    print("launch target:", ok_path)

    # dialog importable
    from app.ui.freecad_tools import FreeCADToolsDialog  # noqa: F401
    from app.ui import main_window  # noqa: F401

    print("FREECAD GUI SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
