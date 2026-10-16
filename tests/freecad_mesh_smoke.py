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
    for o in res.objects:
        if o.primitive:
            # analytic shape — no tessellation needed (see _analytic_prim)
            assert o.primitive.get("kind") in {"box", "cylinder", "sphere", "polygon"}, (
                o.name,
                o.primitive,
            )
            continue
        assert o.npy_path and Path(o.npy_path).exists(), o.name
        assert o.triangle_count > 0, o.name

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
    print("script:", script)
    print("geom prims:", [(g["name"], g.get("kind")) for g in geom])
    # every imported object must be covered by a generated primitive —
    # analytic (box/cylinder/sphere/polygon) or stl with a real mesh file
    obj_names = {o.name for o in res.objects}
    geom_names = {g["name"] for g in geom}
    assert obj_names <= geom_names, f"missing prims: {obj_names - geom_names}"
    stl_prims = [g for g in geom if g.get("kind") == "stl"]
    analytic_prims = [g for g in geom if g.get("kind") != "stl"]
    print("stl prims:", len(stl_prims), [g["name"] for g in stl_prims])
    print("analytic prims:", len(analytic_prims), [g["name"] for g in analytic_prims])
    # the yagi elements are cylinders/boxes — detection must not duplicate
    # them as tessellated STL meshes
    assert analytic_prims, "expected analytic primitives from STEP detection"
    assert all(Path(g["path"]).exists() for g in stl_prims)
    assert all(g.get("kind") in {"box", "cylinder", "sphere", "polygon"} for g in analytic_prims)
    script_text = script.read_text(encoding="utf-8")
    assert "ensure_simulation_meshes" in script_text
    assert "AddPolyhedron" in script_text or "add_triangle_mesh" in script_text
    assert "AddMesh" not in script_text or "no ContinuousStructure.AddMesh" in script_text
    # live CSXCAD path without AddMesh (synthetic triangles — analytic prims
    # have no npy to load)
    from CSXCAD import ContinuousStructure
    from app.csxcad_builder.triangle_mesh import add_triangle_mesh

    assert not hasattr(ContinuousStructure(), "AddMesh")
    csx = ContinuousStructure()
    prop = csx.AddMetal("m")
    tris = np.array(
        [
            [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 1.0, 0.0]],
            [[0.0, 0.0, 0.0], [1.0, 1.0, 0.0], [0.0, 1.0, 0.0]],
        ]
    )
    prim = add_triangle_mesh(prop, name="t", triangles=tris, priority=10)
    assert prim is not None
    assert prop.GetQtyPrimitives() >= 1

    print("FREECAD MESH SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
