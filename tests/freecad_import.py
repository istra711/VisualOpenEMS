"""FreeCAD geometry import smoke test (FCStd -> STEP/STL via FreeCADCmd)."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.freecad_io.importer import convert_fcstd_to_step, load_geometry_file
from app.settings import AppSettings


def main() -> int:
    settings = AppSettings.load()
    freecad = settings.freecad_cmd
    work = settings.work_path() / "freecad_import"
    work.mkdir(parents=True, exist_ok=True)
    if not Path(freecad).exists():
        print(f"FAIL: FreeCADCmd missing: {freecad}")
        return 1

    fcstd = work / "box.FCStd"
    stl = work / "box.stl"
    make = (
        "import FreeCAD as App, Mesh\n"
        "d = App.newDocument('box')\n"
        "part = d.addObject('Part::Box', 'Box')\n"
        "part.Length = 40\n"
        "part.Width = 40\n"
        "part.Height = 2\n"
        "d.recompute()\n"
        f"part.Shape.exportStl(r'{stl.as_posix()}')\n"
        f"d.saveAs(r'{fcstd.as_posix()}')\n"
        "print('MAKE_OK')\n"
    )
    import subprocess

    proc = subprocess.run(
        [str(freecad), "-c", make],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if not fcstd.exists() or not stl.exists():
        print("FAIL: FreeCAD did not create FCStd/STL")
        print(proc.stdout)
        print(proc.stderr)
        return 1
    print("Created:", fcstd, "and", stl)

    step = work / "box_from_fcstd.step"
    convert_fcstd_to_step(fcstd, step, freecad)
    print("Converted STEP exists:", step.exists(), "size:", step.stat().st_size if step.exists() else 0)
    if not step.exists() or step.stat().st_size < 100:
        print("FAIL: STEP conversion empty")
        return 1

    res_stl = load_geometry_file(stl, freecad_cmd=freecad, work_dir=work)
    res_step = load_geometry_file(step, freecad_cmd=freecad, work_dir=work)
    res_fc = load_geometry_file(fcstd, freecad_cmd=freecad, work_dir=work)

    ok = True
    if not res_stl.objects or res_stl.objects[0].triangles is None:
        print("FAIL: STL triangles missing")
        ok = False
    else:
        tris = res_stl.objects[0].triangles
        print(f"STL triangles: {len(tris)} bounds={res_stl.objects[0].bounds_min} .. {res_stl.objects[0].bounds_max}")

    if not res_step.objects or res_step.objects[0].bounds_min is None:
        print("FAIL: STEP bounds missing")
        ok = False
    else:
        print("STEP bounds:", res_step.objects[0].bounds_min, "..", res_step.objects[0].bounds_max)

    if not res_fc.objects:
        print("FAIL: FCStd load produced no object")
        ok = False
    else:
        print("FCStd notes:", res_fc.objects[0].notes, "converted_from=", res_fc.converted_from_fcstd)

    if ok:
        print("FREECAD IMPORT PASS")
        return 0
    print("FREECAD IMPORT FAIL")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
