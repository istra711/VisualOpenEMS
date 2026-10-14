"""STL import example - FreeCAD objects -> per-object STL meshes -> openEMS project.

Headless:
  python examples/run_stl_import_example.py [--run]

Steps (same path as GUI "FreeCAD... -> Export CAD... -> Import as STL"):
  1. FreeCADCmd builds a Yagi model (FCStd with named solids: boom, reflector, ...)
  2. export_cad_for_openems -> STEP + one STL per object + openems_export.json
  3. load_stl_meshes(manifest) -> one Polyhedron mesh primitive per FreeCAD object
  4. generate_run_script -> work/stl_import/project_run.py

Generates:
  work/stl_import/yagi_2p45.FCStd
  work/stl_import/freecad_export/{yagi_2p45.step, <obj>.stl, openems_export.json}
  work/stl_import/meshes/*.npy           (triangle arrays for CSXCAD)
  work/stl_import/stl_import.fcsim.json
  work/stl_import/geometry_primitives.json
  work/stl_import/project_run.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.settings import AppSettings
from app.simulation.script_gen import generate_run_script


def export_freecad_stl(fcstd: Path, out_dir: Path, freecad_cmd: Path, deflection: float = 0.1):
    """Export FCStd -> per-object STL + openems_export.json (FreeCADCmd)."""
    from app.freecad_io.freecad_launcher import export_cad_for_openems

    ok, log, out = export_cad_for_openems(
        fcstd, out_dir, freecad_cmd=freecad_cmd, deflection=deflection
    )
    if not ok:
        print(log[-2000:] if log else "export failed")
        return None, log
    return out / "openems_export.json", log


def build_project_from_manifest(manifest: Path, name: str = "stl_import_yagi"):
    """Project (ports/mesh/NF2FF from the Yagi preset) + STL mesh primitives."""
    from app.csxcad_builder.primitives import yagi_antenna_primitives
    from app.freecad_io.stl_import import load_stl_meshes, stl_primitives_from_meshes
    from app.models.project import GeomAssignment, GeometryFile

    project, _ = yagi_antenna_primitives()

    mesh_dir = manifest.parent.parent / "meshes"
    res = load_stl_meshes(manifest, out_dir=mesh_dir)
    for w in res.warnings:
        print("WARNING:", w)
    if not res.ok:
        return None, [], res

    # Per-object material/priority from the preset's assignments (Yagi: the boom
    # is the GFRP dielectric — a copper boom shorts the feed gap). Objects
    # without a preset assignment become copper PEC, priority 50, so the meshes
    # replace the analytic cylinders 1:1.
    preset_assign = {a.object_name: a for a in project.assignments}
    prims = []
    assignments = []
    for m in res.objects:
        a = preset_assign.get(m.name)
        if a is None:
            a = GeomAssignment(
                object_name=m.name,
                role="pec",
                material="copper",
                priority=50,
                notes=f"imported STL mesh from {manifest.name}",
            )
        if a.role == "ignore":
            continue
        mat = a.material or ("copper" if a.role in ("pec", "conducting_sheet") else "")
        prims.extend(
            stl_primitives_from_meshes(
                [m],
                material=mat or "copper",
                priority=a.priority,
                source_file=str(manifest),
                source_format="json",
            )
        )
        assignments.append(a)
    project.name = name
    project.primitives = list(prims)
    project.assignments = assignments
    project.geometry_files = [
        GeometryFile(
            path=str(manifest),
            format="json",
            unit="mm",
            mesh_path=prims[0]["path"] if prims else "",
            object_names=[m.name for m in res.objects],
        )
    ]
    project.notes = (
        (project.notes + "\n" if project.notes else "")
        + f"geometry from FreeCAD STL export: {manifest}"
    )
    return project, prims, res


def main() -> int:
    settings = AppSettings.load()
    work = settings.work_path() / "stl_import"
    work.mkdir(parents=True, exist_ok=True)

    # --- 1. FreeCAD model ---------------------------------------------------
    from examples.run_yagi_example import generate_freecad_model

    model = generate_freecad_model(work)
    fcstd = Path(model["fcstd"])
    if not fcstd.exists():
        print(f"FAIL: FreeCAD model missing: {fcstd}")
        print(model.get("log", "")[-1500:])
        return 1
    freecad_cmd = Path(model["freecad_cmd"])
    if not freecad_cmd.exists():
        print(f"FAIL: FreeCADCmd missing: {freecad_cmd}")
        return 1

    # --- 2. Export: STEP + per-object STL + manifest ------------------------
    manifest, log = export_freecad_stl(fcstd, work / "freecad_export", freecad_cmd)
    if manifest is None or not manifest.exists():
        print("FAIL: STL export produced no openems_export.json")
        return 1
    print("Manifest:", manifest)

    # --- 3. Import meshes ---------------------------------------------------
    project, prims, res = build_project_from_manifest(manifest)
    if project is None:
        print("FAIL: STL mesh import produced no objects")
        return 1
    print(f"Imported {len(res.objects)} FreeCAD objects as STL meshes:")
    for m in res.objects:
        print(
            f"  {m.name:14s} tris={m.triangle_count:5d} "
            f"bounds={m.bounds_min} .. {m.bounds_max}"
        )

    # --- 4. Project + run script -------------------------------------------
    project_path = project.save(work / "stl_import.fcsim.json")
    script = generate_run_script(project, project_path, work, primitives=prims)

    geom = json.loads((work / "geometry_primitives.json").read_text(encoding="utf-8"))
    stl_prims = [g for g in geom if g.get("kind") == "stl"]
    names = [g["name"] for g in stl_prims]
    print("geometry_primitives.json:", len(stl_prims), "stl prims ->", names)
    if len(stl_prims) != len(res.objects):
        print(f"FAIL: expected {len(res.objects)} mesh primitives, got {len(stl_prims)}")
        return 1
    missing = [g["name"] for g in stl_prims if not Path(g["path"]).exists()]
    if missing:
        print(f"FAIL: mesh files missing for {missing}")
        return 1
    if names != [m.name for m in res.objects]:
        print(f"FAIL: object names not preserved: {names}")
        return 1

    print("STL import project:", project_path)
    print("Simulation script:", script)
    print("FreeCAD export dir:", manifest.parent)
    print("Triangle meshes (.npy):", res.objects[0].npy_path, "...")
    print("Run simulation with:")
    print(f"  {settings.simulation_python} {script}")
    print("Or open the GUI -> FreeCAD... -> Export CAD... -> Import as STL "
          "/ Import Geometry... -> openems_export.json")

    if "--run" in sys.argv[1:]:
        import subprocess

        print("Running simulation ...")
        rc = subprocess.call([settings.simulation_python, str(script)])
        return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
