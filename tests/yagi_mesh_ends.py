"""Verify that Yagi director ends get dense mesh lines after the metal_edge fix."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "third_party" / "easyMesh4openEMS"))
sys.path.insert(0, str(ROOT))

import numpy as np
from openEMS import openEMS as OpenEMSClass
from CSXCAD import ContinuousStructure

from app.csxcad_builder.primitives import yagi_antenna_primitives
from app.meshing.easy_wrapper import enhance_csx, enhance_fdtd, apply_boundary, generate_mesh
from app.meshing.rules import MeshTargets, compute_targets
from app.models.project import BoundarySpec, MeshSpec


def main() -> int:
    project, prims = yagi_antenna_primitives()
    mesh = project.mesh
    boundary = project.boundary

    f0 = 2.45e9
    fc = 0.25e9

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
        elif mat.kind == "dielectric":
            props[mat.name] = CSX.AddMaterial(mat.name, epsilon=mat.epsilon)
        else:
            props[mat.name] = CSX.AddMaterial(mat.name, epsilon=1.0)

    for pr in prims:
        kind = pr.get("kind", "box")
        name = pr["name"]
        mat = pr.get("material", "copper")
        pri = int(pr.get("priority", 10))
        prop = props[mat]
        if kind == "cylinder":
            prop.AddCylinder(
                [float(x) for x in pr["start"]],
                [float(x) for x in pr["stop"]],
                float(pr.get("radius", 0.5)),
                priority=pri,
            )
        elif kind == "box":
            prop.AddBox(
                start=[float(x) for x in pr["start"]],
                stop=[float(x) for x in pr["stop"]],
                priority=pri,
            )

    print("Registered primitives:", len(primitives_mesh_setup))

    setup = generate_mesh(
        CSX, mesh, boundary, targets,
        primitives_mesh_setup=primitives_mesh_setup,
        properties_mesh_setup=properties_mesh_setup,
    )

    grid = CSX.GetGrid()
    x_lines = np.sort(np.array(grid.GetLines(0)))
    print(f"\nx mesh lines: {len(x_lines)}")

    # Yagi element positions (from primitives.py defaults, mm)
    lam = 122.4  # approx
    # reflector at y=-0.25*lam, len=0.5*lam -> ends at x=±(0.5*lam/2)=±30.6
    # directors: dir1 0.44*lam/2=±26.9, dir2 0.415*lam/2=±25.4, dir3 0.39*lam/2=±23.9
    # With r=1.0, cylinder end edges at end_x ± 1.0
    ends = {
        "reflector_left": -(0.500 * lam / 2),
        "reflector_right": +(0.500 * lam / 2),
        "dir1_left": -(0.440 * lam / 2),
        "dir1_right": +(0.440 * lam / 2),
        "dir2_left": -(0.415 * lam / 2),
        "dir2_right": +(0.415 * lam / 2),
        "dir3_left": -(0.390 * lam / 2),
        "dir3_right": +(0.390 * lam / 2),
    }

    r = 1.0  # element_radius

    def count_near(val, tol=0.6):
        return int(np.sum(np.abs(x_lines - val) < tol))

    print("\n--- Mesh lines near element ends (tol=0.6 mm) ---")
    print(f"{'end':<18} {'expected_x':>10} {'lines_near':>10}")
    all_ok = True
    for label, ex in ends.items():
        # edges at ex-r and ex+r; densification should add ~2 more near each
        n_left = count_near(ex - r)
        n_right = count_near(ex + r)
        total = n_left + n_right
        status = "OK" if total >= 4 else "MISSING"
        if total < 4:
            all_ok = False
        print(f"{label:<18} {ex:>10.2f} {total:>10}  ({n_left}+{n_right})  {status}")

    # Show actual lines near one director end for debugging
    d1r = 0.440 * lam / 2
    near = x_lines[(x_lines > d1r - 3) & (x_lines < d1r + 3)]
    print(f"\nLines within ±3 mm of dir1 right end ({d1r:.2f}):")
    for v in near:
        print(f"  {v:.4f}")

    near_ref = x_lines[(x_lines > 0.500 * lam / 2 - 3) & (x_lines < 0.500 * lam / 2 + 3)]
    print(f"\nLines within ±3 mm of reflector right end ({0.500 * lam / 2:.2f}):")
    for v in near_ref:
        print(f"  {v:.4f}")

    print("\nRESULT:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
