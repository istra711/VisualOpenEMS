"""Smoke: FreeCAD STL import — folder/manifest → npy meshes → project primitives.

Headless (no FreeCAD needed):
  python tests/stl_import_smoke.py
"""

from __future__ import annotations

import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def write_box_stl(path: Path, lo, hi) -> None:
    """Binary STL of a closed box (6 faces × 2 triangles)."""
    a = [float(v) for v in lo]
    b = [float(v) for v in hi]
    corners = [
        [a[0], a[1], a[2]], [b[0], a[1], a[2]], [b[0], b[1], a[2]], [a[0], b[1], a[2]],
        [a[0], a[1], b[2]], [b[0], a[1], b[2]], [b[0], b[1], b[2]], [a[0], b[1], b[2]],
    ]
    faces = [
        (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
        (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
        (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0),
    ]
    tris = [[corners[i], corners[j], corners[k]] for i, j, k in faces]
    with open(path, "wb") as f:
        f.write(b"stl_import_smoke".ljust(80, b" ")[:80])
        f.write(struct.pack("<I", len(tris)))
        for t in tris:
            f.write(struct.pack("<3f", 0.0, 0.0, 0.0))
            for p in t:
                f.write(struct.pack("<3f", *p))
            f.write(struct.pack("<H", 0))


def main() -> int:
    from app.freecad_io.stl_import import is_stl_manifest, load_stl_meshes
    from app.models.project import GeometryFile, PortDef, Project
    from app.simulation.script_gen import generate_run_script

    tmp = Path(tempfile.mkdtemp(prefix="stl_import_smoke_"))
    ok = True
    try:
        # --- folder source --------------------------------------------------
        folder = tmp / "stl"
        folder.mkdir(parents=True)
        write_box_stl(folder / "ground.stl", [-30, -20, -2], [30, 20, 0])
        write_box_stl(folder / "patch.stl", [-10, -8, 2], [10, 8, 4])

        res_dir = load_stl_meshes(folder, out_dir=tmp / "meshes")
        names = [o.name for o in res_dir.objects]
        print("folder:", names, "warnings:", res_dir.warnings)
        if not res_dir.ok or names != ["ground", "patch"]:
            print("FAIL: folder import")
            ok = False
        if not all(o.npy_path and Path(o.npy_path).exists() for o in res_dir.objects):
            print("FAIL: npy not written for folder import")
            ok = False
        g = next(o for o in res_dir.objects if o.name == "ground")
        if g.bounds_min != [-30.0, -20.0, -2.0] or g.bounds_max != [30.0, 20.0, 0.0]:
            print("FAIL: bounds", g.bounds_min, g.bounds_max)
            ok = False
        if g.triangle_count != 12:
            print("FAIL: triangle count", g.triangle_count)
            ok = False

        # --- manifest source (FreeCAD export openems_export.json) -----------
        manifest = tmp / "openems_export.json"
        manifest.write_text(
            json.dumps(
                {
                    "source": "smoke",
                    "step": str(tmp / "smoke.step"),
                    "objects": [
                        {"name": "boom", "stl": str(folder / "ground.stl")},
                        {"name": "reflector", "stl": str(folder / "patch.stl")},
                    ],
                    "ports": [],
                }
            ),
            encoding="utf-8",
        )
        if not is_stl_manifest(manifest) or is_stl_manifest(folder / "ground.stl"):
            print("FAIL: is_stl_manifest")
            ok = False

        res_man = load_stl_meshes(manifest, out_dir=tmp / "meshes2")
        print("manifest:", [o.name for o in res_man.objects])
        if [o.name for o in res_man.objects] != ["boom", "reflector"]:
            print("FAIL: manifest names")
            ok = False

        # --- project: no primitives yet → script must mesh from manifest ----
        p = Project.new_default("stl_import_smoke")
        p.mesh.start_frequency = 2.2e9
        p.mesh.stop_frequency = 2.7e9
        p.mesh.target_frequency = 2.45e9
        p.mesh.drawing_unit = "mm"
        p.ports = [
            PortDef(
                name="feed",
                type="lumped",
                start=[-1.0, 0.0, -1.0],
                stop=[1.0, 0.0, 1.0],
                resistance=50.0,
                direction="x",
            )
        ]
        p.geometry_files = [
            GeometryFile(
                path=str(manifest),
                format="json",
                unit="mm",
                object_names=["boom", "reflector"],
            )
        ]
        work = tmp / "proj"
        script = generate_run_script(p, work / "stl.fcsim.json", work, primitives=[])
        geom = json.loads((work / "geometry_primitives.json").read_text(encoding="utf-8"))
        stl_prims = [g for g in geom if g.get("kind") == "stl"]
        print("generated prims:", [g["name"] for g in stl_prims])
        if [g["name"] for g in stl_prims] != ["boom", "reflector"]:
            print("FAIL: generate_run_script did not mesh the manifest")
            ok = False
        if not all(Path(g["path"]).exists() for g in stl_prims):
            print("FAIL: generated prim mesh missing")
            ok = False
        if [g["source_format"] for g in stl_prims] != ["json", "json"]:
            print("FAIL: source_format", [g["source_format"] for g in stl_prims])
            ok = False

        # --- generated script re-meshes from manifest when prims are lost ---
        import importlib.util

        spec = importlib.util.spec_from_file_location("stl_project_run", script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rebuilt = mod.ensure_simulation_meshes(p, work, [])
        rebuilt_names = [g.get("name") for g in rebuilt if g.get("kind") == "stl"]
        print("ensure_simulation_meshes:", rebuilt_names)
        if rebuilt_names != ["boom", "reflector"]:
            print("FAIL: ensure_simulation_meshes did not rebuild meshes")
            ok = False
        if not all(Path(g["path"]).exists() for g in rebuilt):
            print("FAIL: rebuilt mesh missing")
            ok = False

        # existing primitives must not be duplicated
        again = mod.ensure_simulation_meshes(p, work, list(stl_prims))
        if len(again) != len(stl_prims):
            print("FAIL: early-return duplicated primitives", len(again))
            ok = False

        # --- GUI: Import Geometry… on the manifest --------------------------
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from app.settings import AppSettings

        app = QApplication.instance() or QApplication(sys.argv)
        from app.ui.main_window import MainWindow

        win = MainWindow(AppSettings.load())
        win._on_new()
        win._import_geometry_path(str(manifest))
        gui_names = [pr.get("name") for pr in win.primitives if pr.get("kind") == "stl"]
        print("GUI imported primitives:", gui_names)
        if gui_names != ["boom", "reflector"]:
            print("FAIL: GUI import", gui_names)
            ok = False
        if [g.path for g in win.project.geometry_files] != [str(manifest)]:
            print("FAIL: geometry_files", [g.path for g in win.project.geometry_files])
            ok = False
        if [a.object_name for a in win.project.assignments] != ["boom", "reflector"]:
            print("FAIL: assignments", [a.object_name for a in win.project.assignments])
            ok = False
        if not all(
            Path(pr.get("path", "")).exists()
            for pr in win.primitives
            if pr.get("kind") == "stl"
        ):
            print("FAIL: GUI mesh files missing")
            ok = False
        # re-import refreshes instead of duplicating
        win._import_geometry_path(str(manifest))
        again_names = [pr.get("name") for pr in win.primitives if pr.get("kind") == "stl"]
        if len(again_names) != 2:
            print("FAIL: re-import duplicated primitives", again_names)
            ok = False
        win.close()

        print("STL IMPORT SMOKE OK" if ok else "STL IMPORT SMOKE FAIL")
        return 0 if ok else 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
