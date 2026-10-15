"""Build the dipol CSX exactly like project_run.py and report what openEMS actually uses."""
import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "third_party" / "easyMesh4openEMS"))
sys.path.insert(0, str(ROOT))

from openEMS import openEMS as OpenEMSClass
from CSXCAD import ContinuousStructure

from app.csxcad_builder.triangle_mesh import add_triangle_mesh
from app.meshing.easy_wrapper import enhance_csx, enhance_fdtd, apply_boundary, generate_mesh
from app.meshing.rules import compute_targets
from app.models.project import Project

PROJ = ROOT / "work" / "Dipol_CST_openems_Test_v2" / "Dipol_CST_openems_Test.fcsim.json"
OUT_LINES = Path(__file__).with_name("dipol_mesh_lines.json")


def main() -> int:
    project = Project.from_dict(json.loads(PROJ.read_text(encoding="utf-8")))
    mesh = project.mesh
    boundary = project.boundary

    f0 = mesh.target_frequency or 0.5 * (mesh.start_frequency + mesh.stop_frequency)
    fc = max(1e6, 0.5 * abs(mesh.stop_frequency - mesh.start_frequency))

    FDTD = OpenEMSClass(NrTS=1, EndCriteria=0)
    FDTD.SetGaussExcite(f0, fc)
    CSX = ContinuousStructure()
    FDTD.SetCSX(CSX)

    primitives_mesh_setup: dict = {}
    properties_mesh_setup: dict = {}
    CSX = enhance_csx(CSX, primitives_mesh_setup)
    FDTD = enhance_fdtd(FDTD, primitives_mesh_setup)

    targets = compute_targets(mesh, eps_max=1.0)
    apply_boundary(FDTD, boundary, mesh, targets)
    CSX.GetGrid().SetDeltaUnit(mesh.unit_to_meters())

    props = {}
    for mat in project.materials:
        if mat.kind == "pec":
            props[mat.name] = CSX.AddMetal(mat.name)
        elif mat.kind == "conducting_sheet":
            props[mat.name] = CSX.AddConductingSheet(mat.name, conductivity=mat.kappa)
        elif mat.kind == "dielectric":
            kwargs = {"epsilon": mat.epsilon}
            if mat.kappa:
                kwargs["kappa"] = mat.kappa
            props[mat.name] = CSX.AddMaterial(mat.name, **kwargs)
        else:
            props[mat.name] = CSX.AddMaterial(mat.name, epsilon=1.0)

    # same assignment material as the real project
    assign = {a.object_name: a.material for a in project.assignments}
    # Use the project primitives directly (they point at the tessellated npy/stl).
    for pr_def in project.primitives:
        name = pr_def["name"]
        mat = assign.get(name, pr_def.get("material", "copper"))
        prop = props[mat]
        npy = Path(pr_def.get("path") or pr_def.get("mesh_path") or "")
        stl = Path(pr_def.get("stl_path") or "")
        pr = add_triangle_mesh(
            prop,
            name=name,
            triangles=np.load(npy) if npy.exists() else None,
            stl_path=str(stl) if stl.exists() else None,
            mesh_path=str(npy) if npy.exists() else None,
            priority=int(pr_def.get("priority", 10)),
        )
        print(f"prim {name}: type={type(pr).__name__} mat={mat}")

    port = project.ports[0]
    pobj = FDTD.AddLumpedPort(
        1,
        port.resistance,
        [float(x) for x in port.start],
        [float(x) for x in port.stop],
        port.direction,
        1.0 if port.excite_port else 0.0,
        priority=15,
        edges2grid=port.mesh_dirs or "xy",
    )
    print(f"port registered: {pobj is not None}")

    print("Registered easyMesh primitives:", len(primitives_mesh_setup))
    for k, v in primitives_mesh_setup.items():
        print(f"  mesh_setup key={type(k).__name__} hint={v}")

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        generate_mesh(
            CSX,
            mesh,
            boundary,
            targets,
            primitives_mesh_setup=primitives_mesh_setup,
            properties_mesh_setup=properties_mesh_setup,
        )

    # Report what CSXCAD thinks is simulated vs unused
    for prop in CSX.GetAllProperties():
        prims = prop.GetAllPrimitives()
        print(f"\nproperty {prop.GetName()} ({prop.GetTypeString()}): {len(prims)} primitives")
        for pr in prims:
            tn = type(pr).__name__
            extra = ""
            if hasattr(pr, "GetNumFaces"):
                extra = f" faces={pr.GetNumFaces()}"
            if hasattr(pr, "GetFilename"):
                extra += f" file={pr.GetFilename()}"
            print(f"  {tn}{extra}")

    grid = CSX.GetGrid()
    z = np.sort(np.array(grid.GetLines(2), dtype=float))
    gap_lo = float(port.start[2])
    gap_hi = float(port.stop[2])
    if gap_lo > gap_hi:
        gap_lo, gap_hi = gap_hi, gap_lo

    print(f"\nfeed gap z in [{gap_lo:.6f}, {gap_hi:.6f}] width={gap_hi - gap_lo:.6f} mm")
    print(f"z mesh lines: {len(z)}  range [{z.min():.6f}, {z.max():.6f}]")
    in_gap = z[(z > gap_lo + 1e-9) & (z < gap_hi - 1e-9)]
    print(f"z lines strictly INSIDE gap: {len(in_gap)} -> {list(in_gap)}")
    around = z[(z > gap_lo - 2) & (z < gap_hi + 2)]
    print("cells near feed gap:")
    for a, b in zip(around[:-1], around[1:]):
        mark = "  <-- SPANS GAP" if a < gap_lo and b > gap_hi else ""
        print(f"  [{a:.6f}, {b:.6f}] size={b - a:.6f}{mark}")

    # A correctly resolved lumped-port gap: both port faces are grid lines,
    # no single cell spans the whole gap, and no nm-scale (float32-STL twin)
    # cells remain anywhere on the axis. Interior lines are optional: a gap
    # thinner than min_cellsize legitimately stays one open cell.
    nm_cells = [(a, b, b - a) for a, b in zip(z[:-1], z[1:]) if 0 < (b - a) < 1e-6]
    face_on_grid = [any(abs(L - f) < 1e-9 for L in z) for f in (gap_lo, gap_hi)]
    spanning = any(
        a < gap_lo - 1e-9 and b > gap_hi + 1e-9 for a, b in zip(z[:-1], z[1:])
    )
    print(f"port faces on grid: {face_on_grid}")
    print(f"nm-scale cells on z: {len(nm_cells)} -> {[(f'{a:.12f}', f'{b:.12f}') for a, b, _ in nm_cells]}")
    resolved = all(face_on_grid) and not spanning and not nm_cells
    print("\nGAP RESOLVED:", "YES" if resolved else "NO")
    OUT_LINES.write_text(
        json.dumps(
            {
                "z": z.tolist(),
                "x": np.sort(np.array(grid.GetLines(0), dtype=float)).tolist(),
                "y": np.sort(np.array(grid.GetLines(1), dtype=float)).tolist(),
            }
        ),
        encoding="utf-8",
    )
    print("wrote", OUT_LINES)
    return 0 if resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
