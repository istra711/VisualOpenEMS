"""Smoke: sphere/polygon primitive kinds — dialogs, run path, polar gate.

Covers the new openEMS primitive kinds added alongside the FreeCAD analytic
import mix:
  1. PrimitiveSphereDialog / PrimitivePolygonDialog produce correct dicts
     (including the viewer bbox start/stop and extra-field keep on edit).
  2. cartesian --mesh-only run accepts sphere + flat polygon + extruded
     polygon (AddSphere / AddPolygon / AddLinPoly with columnar [xs, ys]).
  3. short cartesian FDTD free-response run (proves the primitives survive
     the actual solver setup).
  4. cylindrical mode rejects sphere/polygon with a clear message
     (run-time gate + GUI dialog gate).

Run: python -u tests/sphere_polygon_smoke.py
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

from app.models.project import MaterialDef, Project  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.simulation.script_gen import generate_run_script  # noqa: E402

SPHERE_PRIM = {
    "kind": "sphere",
    "name": "ball",
    "material": "copper",
    "priority": 10,
    "center": [30.0, 0.0, 0.0],
    "radius": 5.0,
    "start": [25.0, -5.0, -5.0],
    "stop": [35.0, 5.0, 5.0],
}
POLY_SHEET = {
    "kind": "polygon",
    "name": "sheet",
    "material": "copper",
    "priority": 10,
    "axis": "z",
    "coord": 0.0,
    "length": 0.0,
    "points": [[-20.0, -10.0], [20.0, -10.0], [20.0, 10.0], [-20.0, 10.0]],
    "start": [-20.0, -10.0, 0.0],
    "stop": [20.0, 10.0, 0.0],
}
POLY_PRISM = {
    "kind": "polygon",
    "name": "prism",
    "material": "dielectric",
    "priority": 12,
    "axis": "z",
    "coord": -6.0,
    "length": 1.5,
    "points": [[-8.0, -4.0], [8.0, -4.0], [8.0, 4.0], [-8.0, 4.0]],
    "start": [-8.0, -4.0, -6.0],
    "stop": [8.0, 4.0, -4.5],
}


def _base_project(coord: str = "cartesian") -> Project:
    p = Project(name="sphere_poly_smoke")
    p.mesh.drawing_unit = "mm"
    p.mesh.preset = "medium"
    p.mesh.start_frequency = 0.5e9
    p.mesh.stop_frequency = 1.5e9
    p.mesh.target_frequency = 1.0e9
    p.mesh.coord_system = coord  # type: ignore[assignment]
    p.materials = [
        MaterialDef(name="copper", kind="pec"),
        MaterialDef(name="dielectric", kind="dielectric", epsilon=4.0),
    ]
    # free response: no ports, no excitation → short run
    p.ports = []
    p.sim.max_timesteps = 20_000
    p.sim.end_criteria = 1e-4
    return p


def _run(script: Path, work: Path, mesh_only: bool = True) -> tuple[int, str]:
    cmd = [sys.executable, "-u", str(script)]
    if mesh_only:
        cmd.append("--mesh-only")
    proc = subprocess.run(
        cmd,
        cwd=str(work),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _assert(cond: bool, msg: str, log: str = "") -> None:
    if not cond:
        tail = "\n".join((log or "").splitlines()[-60:])
        raise AssertionError(f"{msg}\n--- log tail ---\n{tail}")


def _test_dialogs() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    from app.ui import geometry_editors
    from app.ui.geometry_editors import PrimitivePolygonDialog, PrimitiveSphereDialog

    # --- sphere ------------------------------------------------------------
    d = PrimitiveSphereDialog()
    d.ed_name.setText("ball2")
    d.cmb_mat.setCurrentText("dielectric")
    d.spn_radius.setValue(7.5)
    for sp, v in zip(d.spn_center, (1.0, 2.0, 3.0)):
        sp.setValue(float(v))
    d._accept()
    r = d.result
    _assert(r is not None, "sphere dialog produced no result")
    _assert(r["kind"] == "sphere", f"sphere kind: {r['kind']}")
    _assert(r["name"] == "ball2", f"name: {r['name']}")
    _assert(r["material"] == "dielectric", f"material: {r['material']}")
    _assert(r["center"] == [1.0, 2.0, 3.0], f"center: {r['center']}")
    _assert(r["radius"] == 7.5, f"radius: {r['radius']}")
    _assert(r["start"] == [1.0 - 7.5, 2.0 - 7.5, 3.0 - 7.5], f"bbox start: {r['start']}")
    _assert(r["stop"] == [1.0 + 7.5, 2.0 + 7.5, 3.0 + 7.5], f"bbox stop: {r['stop']}")
    print("[dialog] sphere OK")

    # edit keeps imported extras (source_file, …)
    seed = dict(SPHERE_PRIM, source_file=r"C:\tmp\ball.step")
    d2 = PrimitiveSphereDialog(prim=seed)
    d2._accept()
    r2 = d2.result
    _assert(r2 is not None and r2.get("source_file") == r"C:\tmp\ball.step",
            "sphere edit must keep extra fields")
    print("[dialog] sphere edit keeps extras OK")

    # --- polygon (flat sheet, default axis z) ------------------------------
    pd = PrimitivePolygonDialog()
    pd._accept()
    pr = pd.result
    _assert(pr is not None, "polygon dialog produced no result")
    _assert(pr["kind"] == "polygon", f"polygon kind: {pr['kind']}")
    _assert(len(pr["points"]) == 4, f"points: {pr['points']}")
    _assert(pr["axis"] == "z" and pr["coord"] == 0.0 and pr["length"] == 0.0,
            f"axis/coord/length: {pr['axis']} {pr['coord']} {pr['length']}")
    _assert(pr["start"] == [0.0, 0.0, 0.0], f"bbox start: {pr['start']}")
    _assert(pr["stop"] == [20.0, 10.0, 0.0], f"bbox stop: {pr['stop']}")
    print("[dialog] polygon sheet OK")

    # --- polygon (extruded, axis y — cyclic point mapping) -----------------
    pd2 = PrimitivePolygonDialog()
    pd2.cmb_axis.setCurrentText("y")
    pd2.spn_coord.setValue(5.0)
    pd2.spn_length.setValue(2.0)
    pd2.ed_points.setPlainText("-10, 0\n10, 0\n10, 8\n-10, 8")
    pd2._accept()
    pr2 = pd2.result
    _assert(pr2 is not None, "extruded polygon dialog produced no result")
    # axis y -> points are (z, x); c0=z from xs, c1=x from ys; slab y 5..7
    _assert(pr2["start"] == [0.0, 5.0, -10.0], f"bbox start: {pr2['start']}")
    _assert(pr2["stop"] == [8.0, 7.0, 10.0], f"bbox stop: {pr2['stop']}")
    _assert(pr2["length"] == 2.0, f"length: {pr2['length']}")
    print("[dialog] polygon extruded OK")

    # --- invalid points: warning, no accept (patch the modal) --------------
    warned: list[str] = []

    class _FakeMB:
        @staticmethod
        def warning(*a, **k):  # noqa: ANN002, ANN003
            warned.append(str(a[1]) if len(a) > 1 else "?")

    real_mb = geometry_editors.QMessageBox
    geometry_editors.QMessageBox = _FakeMB
    try:
        pd3 = PrimitivePolygonDialog()
        pd3.ed_points.setPlainText("0, 0\n1, 1")  # only 2 points
        pd3._accept()
        _assert(pd3.result is None, "invalid polygon must not accept")
        _assert(bool(warned), "invalid polygon must show a warning")
    finally:
        geometry_editors.QMessageBox = real_mb
    print("[dialog] polygon validation OK")


def _test_gui_gate() -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    win = MainWindow(AppSettings.load())
    win._on_new()
    win.project.mesh.coord_system = "cylindrical"
    _assert(win._polar_prim_blocked("sphere") is True, "sphere must be gated in polar mode")
    _assert(win._polar_prim_blocked("polygon") is True, "polygon must be gated in polar mode")
    _assert(win._polar_prim_blocked("box") is False, "box must stay allowed in polar mode")
    win.project.mesh.coord_system = "cartesian"
    _assert(win._polar_prim_blocked("sphere") is False, "sphere must be allowed in cartesian")
    win.close()
    print("[gui] polar dialog gate OK")


def _test_run_path() -> None:
    settings = AppSettings.load()
    work = settings.work_path() / "sphere_poly_smoke"
    work.mkdir(parents=True, exist_ok=True)
    prims = [dict(SPHERE_PRIM), dict(POLY_SHEET), dict(POLY_PRISM)]

    # 1. cartesian mesh-only
    p = _base_project("cartesian")
    project_path = p.save(work / "sp.fcsim.json")
    script = generate_run_script(p, project_path, work, primitives=prims)
    rc, log = _run(script, work, mesh_only=True)
    print(f"[mesh-only] exit={rc}")
    _assert(rc == 0, "cartesian mesh-only run must exit 0", log)
    _assert("not supported in cylindrical mode" not in log, "no polar gate expected", log)
    _assert("Unknown primitive kind" not in log, "sphere/polygon must be known kinds", log)
    _assert("must be a list of length 2" not in log, "CSXCAD polygon format must match", log)
    _assert("mesh validation failed" not in log, "mesh validation must pass", log)
    print("[mesh-only] OK: sphere + flat polygon + extruded polygon accepted")

    # 2. short FDTD free-response run (proves AddSphere/AddPolygon/AddLinPoly)
    proc = subprocess.run(
        [sys.executable, "-u", str(script)],
        cwd=str(work),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    fdtd_log = (proc.stdout or "") + (proc.stderr or "")
    print(f"[fdtd] exit={proc.returncode}")
    _assert(proc.returncode == 0, "short cartesian FDTD run must exit 0", fdtd_log)
    _assert(
        "Simulation finished (FDTD.Run complete)" in fdtd_log,
        "FDTD.Run must complete with sphere/polygon primitives",
        fdtd_log,
    )
    print("[fdtd] OK: sphere/polygon survive the solver")

    # 3. cylindrical gate (run-time)
    p2 = _base_project("cylindrical")
    project_path2 = p2.save(work / "sp_polar.fcsim.json")
    script2 = generate_run_script(
        p2, project_path2, work, primitives=[dict(SPHERE_PRIM)]
    )
    rc2, log2 = _run(script2, work, mesh_only=True)
    print(f"[polar-gate sphere] exit={rc2}")
    _assert(rc2 != 0, "sphere in cylindrical mode must abort", log2)
    _assert(
        "not supported in cylindrical mode" in log2,
        "gate must print the polar unsupported-kind message",
        log2,
    )

    p3 = _base_project("cylindrical")
    project_path3 = p3.save(work / "sp_polar2.fcsim.json")
    script3 = generate_run_script(
        p3, project_path3, work, primitives=[dict(POLY_SHEET)]
    )
    rc3, log3 = _run(script3, work, mesh_only=True)
    print(f"[polar-gate polygon] exit={rc3}")
    _assert(rc3 != 0, "polygon in cylindrical mode must abort", log3)
    _assert(
        "not supported in cylindrical mode" in log3,
        "gate must print the polar unsupported-kind message",
        log3,
    )
    print("[polar-gate] OK: sphere/polygon rejected in cylindrical mode")


def main() -> int:
    _test_dialogs()
    _test_gui_gate()
    _test_run_path()
    print("SPHERE POLYGON SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
