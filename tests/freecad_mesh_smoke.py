"""Smoke: FreeCAD STEP → tessellated meshes → openEMS primitives path."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np


def main() -> int:
    from app.freecad_io.freecad_mesh import tessellate_cad_to_meshes
    from app.models.project import GeometryFile, Project
    from app.simulation.script_gen import generate_run_script

    freecad_cmd = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")
    step_candidates = [
        ROOT / "work" / "yagi_antenna" / "yagi_2p45.step",
        ROOT / "work" / "yagi_antenna" / "yagi_2p45.FCStd",
    ]
    step = next((p for p in step_candidates if p.exists()), None)
    if step is None:
        print("SKIP: no yagi STEP/FCStd found — run examples/run_yagi_example.py first")
        return 0

    out_dir = ROOT / "work" / "freecad_mesh_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    res = tessellate_cad_to_meshes(step, freecad_cmd, out_dir)
    print("ok=", res.ok, "objects=", [o.name for o in res.objects])
    if not res.ok:
        print(res.log[-2000:] if res.log else "no log")
        print(res.warnings)
        return 1
    assert res.objects, "no mesh objects"
    assert all(o.npy_path and Path(o.npy_path).exists() for o in res.objects)
    assert all(o.triangle_count > 0 for o in res.objects)

    # Build project with only geometry_files (no primitives) → generate_run_script must tessellate
    p = Project.new_default("cad_import_smoke")
    p.mesh.start_frequency = 2.2e9
    p.mesh.stop_frequency = 2.7e9
    p.mesh.target_frequency = 2.45e9
    p.mesh.drawing_unit = "mm"
    p.geometry_files.append(
        GeometryFile(
            path=str(step),
            format=step.suffix.lower().lstrip("."),
            unit="mm",
            object_names=[o.name for o in res.objects],
        )
    )
    # ports/NF2FF so project is valid
    from app.models.project import NF2FFDef, PortDef

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
    p.nf2ff = NF2FFDef(enabled=True, start=[-40, -40, -20], stop=[40, 80, 20], center=[0, 20, 0])

    work = ROOT / "work" / "freecad_mesh_smoke" / "proj"
    script = generate_run_script(p, work / "cad.fcsim.json", work, primitives=[])
    geom = json.loads((work / "geometry_primitives.json").read_text(encoding="utf-8"))
    stl_prims = [g for g in geom if g.get("kind") == "stl"]
    print("script:", script)
    print("stl prims:", len(stl_prims), [g["name"] for g in stl_prims])
    assert stl_prims, "generate_run_script should create stl primitives from STEP"
    assert all(Path(g["path"]).exists() for g in stl_prims)
    script_text = script.read_text(encoding="utf-8")
    assert "ensure_simulation_meshes" in script_text
    assert "AddPolyhedron" in script_text or "add_triangle_mesh" in script_text
    assert "AddMesh" not in script_text or "no ContinuousStructure.AddMesh" in script_text
    # live CSXCAD path without AddMesh
    from CSXCAD import ContinuousStructure
    from app.csxcad_builder.triangle_mesh import add_triangle_mesh

    assert not hasattr(ContinuousStructure(), "AddMesh")
    csx = ContinuousStructure()
    prop = csx.AddMetal("m")
    npy = stl_prims[0]["path"]
    tris = np.load(npy)
    prim = add_triangle_mesh(prop, name="t", triangles=tris, stl_path=stl_prims[0].get("stl_path"), priority=10)
    assert prim is not None
    assert prop.GetQtyPrimitives() >= 1

    print("FREECAD MESH SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
