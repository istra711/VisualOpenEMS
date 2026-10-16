"""FreeCAD analytic import smoke: shape detection + import mix.

Builds an FCStd with a mix of solids and verifies _analytic_prim:
  Box      -> kind=box (start/stop)
  Cyl      -> kind=cylinder (rotated axis, radius)
  Sph      -> kind=sphere (center, radius)
  TriPrism -> kind=polygon (points, axis, coord, length)
  Cone     -> NOT analytic (tessellated STL fallback)
  RotBox   -> kind=polygon (a 30-deg box is an extruded rotated rectangle:
              exact AddLinPoly geometry, better than STL)
and the GUI import path:
  - analytic + stl mixed primitives, one entry per object
  - NO auto assignments on import
  - re-import keeps a manually changed primitive material
  - an existing assignment wins over the default/prim material

Needs FreeCADCmd. Run: python -u tests/freecad_analytic_smoke.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FREECAD_CMD = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")


def _close(a, b, tol=1e-4) -> bool:
    return abs(float(a) - float(b)) <= tol


def _vec_close(v, expected, tol=1e-4) -> bool:
    return len(v) == 3 and all(_close(v[i], expected[i], tol) for i in range(3))


def _build_fcstd(fcstd: Path) -> None:
    make = (
        "import FreeCAD as App\n"
        "d = App.newDocument('mix')\n"
        # axis-aligned box
        "b = d.addObject('Part::Box', 'Box')\n"
        "b.Length = 40; b.Width = 20; b.Height = 2\n"
        # cylinder rotated 90 deg about y -> axis along +x from (50,5,2)
        "c = d.addObject('Part::Cylinder', 'Cyl')\n"
        "c.Radius = 3; c.Height = 10\n"
        "c.Placement = App.Placement(App.Vector(50, 5, 2), App.Rotation(App.Vector(0, 1, 0), 90))\n"
        # sphere
        "s = d.addObject('Part::Sphere', 'Sph')\n"
        "s.Radius = 4\n"
        "s.Placement = App.Placement(App.Vector(0, 40, 0), App.Rotation())\n"
        # triangular prism (polygon extrusion) as a shape feature —
        # the same solid type a STEP import produces
        "import Part\n"
        "pr = d.addObject('Part::Feature', 'TriPrism')\n"
        "w = Part.Wire(Part.makePolygon([App.Vector(80, 0, 0), App.Vector(90, 0, 0), App.Vector(85, 8, 0), App.Vector(80, 0, 0)]))\n"
        "pr.Shape = Part.Face(w).extrude(App.Vector(0, 0, 3))\n"
        # cone — NOT analytic
        "co = d.addObject('Part::Cone', 'Cone')\n"
        "co.Radius1 = 5; co.Radius2 = 0; co.Height = 8\n"
        "co.Placement = App.Placement(App.Vector(0, 60, 0), App.Rotation())\n"
        # box rotated 30 deg about z -> extruded rotated rectangle (polygon)
        "rb = d.addObject('Part::Box', 'RotBox')\n"
        "rb.Length = 10; rb.Width = 10; rb.Height = 2\n"
        "rb.Placement = App.Placement(App.Vector(100, 0, 0), App.Rotation(App.Vector(0, 0, 1), 30))\n"
        "d.recompute()\n"
        f"d.saveAs(r'{fcstd.as_posix()}')\n"
        "print('MAKE_OK')\n"
    )
    proc = subprocess.run(
        [str(FREECAD_CMD), "-c", make],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if not fcstd.exists() or "MAKE_OK" not in ((proc.stdout or "") + (proc.stderr or "")):
        raise AssertionError(
            "FreeCAD did not build the test FCStd\n"
            + (proc.stdout or "")
            + (proc.stderr or "")
        )


def _test_detection(fcstd: Path, out_dir: Path) -> dict:
    from app.freecad_io.freecad_mesh import tessellate_cad_to_meshes

    res = tessellate_cad_to_meshes(fcstd, FREECAD_CMD, out_dir, timeout=180)
    print("ok=", res.ok, "objects=", [o.name for o in res.objects])
    if not res.ok:
        raise AssertionError(f"tessellation failed: {res.warnings}\n{res.log[-2000:]}")
    mesh_missing = [w for w in res.warnings if "Mesh files missing" in w]
    assert not mesh_missing, f"analytic objects must not warn about meshes: {mesh_missing}"

    by = {o.name: o for o in res.objects}
    expected = {"Box", "Cyl", "Sph", "TriPrism", "Cone", "RotBox"}
    assert set(by) == expected, f"objects: {set(by)} != {expected}"

    # Box
    bp = by["Box"].primitive
    assert bp and bp["kind"] == "box", f"Box -> {bp}"
    assert _vec_close(bp["start"], [0, 0, 0]) and _vec_close(bp["stop"], [40, 20, 2]), bp
    # Cylinder (rotated: axis (50,5,2)->(60,5,2))
    cp = by["Cyl"].primitive
    assert cp and cp["kind"] == "cylinder", f"Cyl -> {cp}"
    assert _close(cp["radius"], 3.0), cp
    a, b = cp["start"], cp["stop"]
    ok_ends = (_vec_close(a, [50, 5, 2]) and _vec_close(b, [60, 5, 2])) or (
        _vec_close(a, [60, 5, 2]) and _vec_close(b, [50, 5, 2])
    )
    assert ok_ends, f"cylinder axis {a}->{b}"
    # Sphere
    sp = by["Sph"].primitive
    assert sp and sp["kind"] == "sphere", f"Sph -> {sp}"
    assert _vec_close(sp["center"], [0, 40, 0]) and _close(sp["radius"], 4.0), sp
    # Triangular prism -> polygon
    pp = by["TriPrism"].primitive
    assert pp and pp["kind"] == "polygon", f"TriPrism -> {pp}"
    assert pp["axis"] == "z", pp
    assert _close(pp["coord"], 0.0) and _close(pp["length"], 3.0, 1e-3), pp
    assert len(pp["points"]) == 3, f"polygon points: {pp['points']}"
    xs = [pt[0] for pt in pp["points"]]
    ys = [pt[1] for pt in pp["points"]]
    assert 79.9 <= min(xs) and max(xs) <= 90.1, xs  # placed at x=80
    assert -0.1 <= min(ys) and max(ys) <= 8.1, ys
    # Cone: not a primitive — tessellated fallback
    cone = by["Cone"]
    assert cone.primitive is None, f"Cone must NOT be analytic: {cone.primitive}"
    assert cone.npy_path and Path(cone.npy_path).exists(), "Cone: no npy"
    assert cone.triangle_count > 0, "Cone: no triangles"
    # RotBox (30 deg about z): extruded rotated rectangle -> exact polygon
    rp = by["RotBox"].primitive
    assert rp and rp["kind"] == "polygon", f"RotBox -> {rp}"
    assert rp["axis"] == "z" and len(rp["points"]) == 4, rp
    assert _close(rp["length"], 2.0, 1e-3), rp
    rxs = [pt[0] for pt in rp["points"]]
    rys = [pt[1] for pt in rp["points"]]
    assert 94.9 <= min(rxs) and max(rxs) <= 108.8, rxs
    assert -0.1 <= min(rys) and max(rys) <= 13.8, rys
    print("[detection] OK: box/cylinder/sphere/polygon + cone stl fallback")
    return by


def _test_gui_import(fcstd: Path) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from app.models.project import GeomAssignment
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    win = MainWindow(AppSettings.load())
    win._on_new()
    win._import_geometry_path(str(fcstd))

    kinds = {p.get("name"): p.get("kind") for p in win.primitives}
    print("GUI kinds:", kinds)
    expected = {
        "Box": "box",
        "Cyl": "cylinder",
        "Sph": "sphere",
        "TriPrism": "polygon",
        "Cone": "stl",
        "RotBox": "polygon",
    }
    assert kinds == expected, f"kinds mismatch: {kinds} != {expected}"
    assert len(win.primitives) == 6, "exactly one primitive per object (no duplicates)"
    # NO auto assignments
    assert list(win.project.assignments) == [], (
        "import must not create assignments: "
        + str([a.object_name for a in win.project.assignments])
    )
    # defaults + source link
    for p in win.primitives:
        assert p.get("material") == "copper" and int(p.get("priority", -1)) == 10, p
        assert p.get("source_file") == str(fcstd), p
    # analytic prims carry viewer bbox start/stop
    for p in win.primitives:
        assert "start" in p and "stop" in p, p
    print("[gui] first import OK")

    # re-import: no duplicates, manual material survives
    sph = next(p for p in win.primitives if p["name"] == "Sph")
    sph["material"] = "dielectric"
    sph["priority"] = 42
    win._import_geometry_path(str(fcstd))
    assert len(win.primitives) == 6, f"re-import duplicated: {len(win.primitives)}"
    sph2 = next(p for p in win.primitives if p["name"] == "Sph")
    assert sph2["material"] == "dielectric" and sph2["priority"] == 42, sph2
    print("[gui] re-import keeps manual material OK")

    # assignment wins on the next import (preset/manual assignment path)
    win.project.assignments.append(
        GeomAssignment(object_name="Box", role="dielectric", material="fr4", priority=5)
    )
    win._import_geometry_path(str(fcstd))
    box = next(p for p in win.primitives if p["name"] == "Box")
    assert box["material"] == "fr4" and box["priority"] == 5, box
    # other prims unaffected by that assignment
    cone = next(p for p in win.primitives if p["name"] == "Cone")
    assert cone["material"] == "copper", cone
    win.close()
    print("[gui] assignment wins over default OK")


def main() -> int:
    if not FREECAD_CMD.exists():
        print(f"SKIP: FreeCADCmd missing: {FREECAD_CMD}")
        return 0
    work = APP_ROOT / "work" / "freecad_analytic_smoke"
    work.mkdir(parents=True, exist_ok=True)
    fcstd = work / "mix.FCStd"
    _build_fcstd(fcstd)
    _test_detection(fcstd, work / "tess")
    _test_gui_import(fcstd)
    print("FREECAD ANALYTIC SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
