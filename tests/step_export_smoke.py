"""STEP export smoke: real FreeCADCmd round-trip through the GUI path.

Verifies what the pure tests cannot:
  * the FreeCADCmd runner builds every primitive kind (box / zero-thickness
    sheet / prism / sheet-polygon / cylinder / sphere / mesh shell),
  * the STEP file is written in millimetres,
  * object names survive as STEP PRODUCT names (Import.export, not Part.export),
  * a *closed* mesh becomes a solid and an *open* shell stays a shell,
  * ``MainWindow._on_export_step`` writes the file end to end,
  * a non-mm drawing unit is converted (cm -> mm).

Needs FreeCADCmd. Run: python -u tests/step_export_smoke.py
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FREECAD_CMD = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")


def _write_stl(path: Path, quads: bool = True) -> None:
    v = [
        (0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
        (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1),
    ]
    if quads:
        faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
        tris = [(v[a], v[b], v[c]) for a, b, c, _d in faces] + [
            (v[a], v[c], v[d]) for a, _b, c, d in faces
        ]
    else:  # a single triangle: an open shell, not watertight
        tris = [((0, 0, 0), (2, 0, 0), (0, 2, 0))]
    with open(path, "wb") as f:
        f.write(b"step export smoke".ljust(80, b" ")[:80])
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for p in t:
                f.write(struct.pack("<3f", *p))
            f.write(struct.pack("<H", 0))


def _verify_with_freecad(step_path: Path, expect) -> None:
    """Read the STEP back inside FreeCADCmd and print/compare the shapes."""
    script = f"""
import FreeCAD as App, Part, math
sh = Part.read(r"{step_path}")
print("SOLIDS", len(sh.Solids))
for s in sh.Solids:
    bb = s.BoundBox
    print("SOLID", round(s.Volume, 6), round(bb.XMin,6), round(bb.YMin,6), round(bb.ZMin,6),
          round(bb.XMax,6), round(bb.YMax,6), round(bb.ZMax,6))
print("SHELLS", len(sh.Shells))
for s in sh.Shells:
    print("SHELL", s.isClosed(), round(s.Area, 6))
"""
    tmp = Path(tempfile.mkdtemp(prefix="step_verify_")) / "verify.py"
    tmp.write_text(script, encoding="utf-8")
    import subprocess

    proc = subprocess.run(
        [str(FREECAD_CMD), "-c", f"import runpy; runpy.run_path(r'{tmp}', run_name='__main__')"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    out = proc.stdout + proc.stderr
    volumes = []
    closed = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0] == "SOLID":
            volumes.append(float(parts[1]))
        elif parts and parts[0] == "SHELL":
            closed.append(parts[1] == "True")
    print("  read back: solids", len(volumes), "shells", len(closed))
    for name, expected in expect.items():
        print(f"  expect {name}: {expected:g}")
    return volumes, closed, out


def main() -> int:
    if not FREECAD_CMD.exists():
        print(f"SKIP: FreeCADCmd not found: {FREECAD_CMD}")
        return 0

    from app.freecad_io.step_export import build_shape_specs, export_step

    tmp = Path(tempfile.mkdtemp(prefix="step_smoke_"))

    # --- 1) all primitive kinds, mm ----------------------------------------
    cube = tmp / "cube.stl"
    _write_stl(cube, quads=True)
    open_shell = tmp / "open.stl"
    _write_stl(open_shell, quads=False)

    prims = [
        {"kind": "box", "name": "ground", "start": [-20, -20, -0.035], "stop": [20, 20, 0]},
        # zero thickness in z -> planar sheet (the patch preset's metal plane)
        {"kind": "box", "name": "patch", "start": [-15, -10, 1.6], "stop": [15, 10, 1.6]},
        {"kind": "cylinder", "name": "feed", "start": [0, 0, 0], "stop": [0, 0, 1.6], "radius": 0.5},
        {"kind": "sphere", "name": "ball", "center": [30, 0, 3], "radius": 2},
        {"kind": "polygon", "name": "prism", "axis": "z", "coord": 0, "length": 1.5,
         "points": [[0, 30], [10, 30], [5, 40]]},
        {"kind": "polygon", "name": "poly_sheet", "axis": "x", "coord": 2, "length": 0,
         "points": [[0, 0], [5, 0], [5, 5]]},
        {"kind": "stl", "name": "closed_mesh", "path": str(cube), "stl_path": str(cube)},
        {"kind": "stl", "name": "open_mesh", "path": str(open_shell), "stl_path": str(open_shell)},
        # skipped, must be reported but must not abort the export
        {"kind": "box", "name": "line", "start": [0, 0, 0], "stop": [5, 0, 0]},
    ]

    specs, warns = build_shape_specs(prims, unit="mm", mesh_dir=tmp)
    kinds = {s["name"]: s["kind"] for s in specs}
    print("specs:", kinds)
    assert kinds["ground"] == "box"
    assert kinds["patch"] == "sheet"
    assert kinds["poly_sheet"] == "sheet"
    assert kinds["prism"] == "prism"
    assert "line" not in kinds, "the line box must be skipped"
    assert any("line" in w for w in warns)

    out = tmp / "all_kinds.step"
    ok, log, warns = export_step(prims, out, unit="mm", freecad_cmd=FREECAD_CMD)
    print("export ok:", ok)
    for w in warns:
        print("  warn:", w)
    assert ok, log[-2000:]
    assert out.exists() and out.stat().st_size > 0

    text = out.read_text(encoding="utf-8", errors="replace")
    assert "SI_UNIT(.MILLI.,.METRE.)" in text, "STEP must be in millimetres"
    for name in ("ground", "patch", "feed", "ball", "prism", "closed_mesh"):
        assert f"PRODUCT('{name}','{name}'" in text, f"missing STEP product name {name!r}"
    print("  STEP names + mm unit: OK")

    volumes, closed, raw = _verify_with_freecad(
        out,
        {
            "ground": 56.0,
            "feed": math.pi * 0.25 * 1.6,
            "ball": 4.0 / 3.0 * math.pi * 8.0,
            "prism": 75.0,
        },
    )
    # ground + feed + ball + prism + closed_mesh = 5 solids
    # (patch, poly_sheet, open_mesh stay open shells)
    assert len(volumes) == 5, f"expected 5 solids, got {len(volumes)}\n{raw[-1500:]}"
    for expected in (56.0, math.pi * 0.25 * 1.6, 4.0 / 3.0 * math.pi * 8.0, 75.0):
        assert any(abs(v - expected) < 1e-6 for v in volumes), (
            f"volume {expected} missing in {volumes}"
        )
    # the open meshes must remain shells, the watertight cube a solid
    assert closed.count(False) >= 2, f"open shells not preserved: {closed}"
    print("  volumes + shells: OK")

    # --- 2) unit conversion: cm -> mm --------------------------------------
    out_cm = tmp / "unit_cm.step"
    ok, log, _ = export_step(
        [{"kind": "box", "name": "b", "start": [0, 0, 0], "stop": [2, 1, 0.5]}],
        out_cm,
        unit="cm",
        freecad_cmd=FREECAD_CMD,
    )
    assert ok, log[-1500:]
    text_cm = out_cm.read_text(encoding="utf-8", errors="replace")
    # 2 cm -> 20 mm: the STEP (in mm) must carry the scaled numbers
    assert "20." in text_cm or "20," in text_cm
    print("  cm -> mm conversion: OK")

    # --- 3) GUI handler end to end -----------------------------------------
    from PySide6.QtWidgets import QApplication

    from app.settings import AppSettings
    from app.ui import main_window as mw

    app = QApplication.instance() or QApplication(sys.argv)
    w = mw.MainWindow(AppSettings.load())
    w.primitives = [
        {"kind": "box", "name": "gnd", "start": [-5, -5, 0], "stop": [5, 5, 1]},
        {"kind": "sphere", "name": "ball", "center": [0, 0, 5], "radius": 1},
    ]
    gui_out = tmp / "gui_export.step"
    original = mw.QFileDialog.getSaveFileName
    mw.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(gui_out), ""))
    try:
        w._on_export_step()
    finally:
        mw.QFileDialog.getSaveFileName = original
    assert gui_out.exists() and gui_out.stat().st_size > 0, "GUI export wrote no file"
    gui_text = gui_out.read_text(encoding="utf-8", errors="replace")
    assert "PRODUCT('gnd','gnd'" in gui_text
    assert "PRODUCT('ball','ball'" in gui_text
    print("  GUI handler: OK")

    # --- 4) no geometry -> no file, informative failure ---------------------
    empty = tmp / "empty.step"
    assert build_shape_specs([], unit="mm") == ([], [])
    ok2, log2, _ = export_step([], empty, unit="mm", freecad_cmd=FREECAD_CMD)
    assert not ok2 and "Nothing to export" in log2
    assert not empty.exists()
    print("  empty project handled: OK")

    print("STEP export smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
