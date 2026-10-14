"""Smoke: easyMesh GenerateMesh with Polyhedron (no GetCoords / no AddMesh)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "third_party" / "easyMesh4openEMS"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS as OE

    from app.csxcad_builder.triangle_mesh import add_triangle_mesh
    from app.meshing.easy_wrapper import enhance_csx, enhance_fdtd, generate_mesh
    from app.models.project import BoundarySpec, MeshSpec

    # non-box solid (pyramid): must stay a Polyhedron, not become an AddBox
    def pyramid_tris(a, b):
        a = np.array(a, dtype=float)
        b = np.array(b, dtype=float)
        base = [
            [a[0], a[1], a[2]], [b[0], a[1], a[2]],
            [b[0], b[1], a[2]], [a[0], b[1], a[2]],
        ]
        apex = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2, b[2]]
        tris = [
            [base[0], base[2], base[1]],
            [base[0], base[3], base[2]],
            [base[0], base[1], apex],
            [base[1], base[2], apex],
            [base[2], base[3], apex],
            [base[3], base[0], apex],
        ]
        return np.array(tris, dtype=float)

    tris = pyramid_tris([-20, -20, -2], [20, 20, 2])  # mm solid, not a box

    from app.csxcad_builder.triangle_mesh import axis_aligned_box_bounds

    assert axis_aligned_box_bounds(tris) is None, "pyramid must not be detected as box"

    mesh = MeshSpec(
        preset="low",
        drawing_unit="mm",
        start_frequency=2.0e9,
        stop_frequency=3.0e9,
        target_frequency=2.45e9,
        max_grading_ratio=2.0,
        cells_per_wavelength=8.0,
    )
    boundary = BoundarySpec()
    csx = ContinuousStructure()
    try:
        csx.GetGrid().SetDeltaUnit(mesh.unit_to_meters())
    except Exception as e:
        print("SetDeltaUnit:", e)

    primitives_mesh_setup = {}
    csx_w = enhance_csx(csx, primitives_mesh_setup)
    prop = csx_w.AddMetal("copper")
    prim = add_triangle_mesh(prop, name="plate", triangles=tris, priority=10)
    assert prim is not None
    assert "Polyhedron" in type(prim).__name__, type(prim).__name__
    assert not hasattr(csx, "AddMesh")

    # process_primitive must not call GetCoords on polyhedron
    x, y, xe, ye, de = [], [], [], [], []
    from easyMesh import geometryUtils

    class Fake:
        found_circles = []
        primitives_mesh_setup = {}
        properties_mesh_setup = {}
        global_mesh_setup = {"dirs": "xyz"}

    geometryUtils.process_primitive(Fake(), prim, x, y, xe, ye, de)
    assert len(x) >= 4 and len(y) >= 4, (x, y)
    zs = geometryUtils.collect_z_coordinates([prim])
    assert zs, zs
    print("process_primitive poly OK", "x", x[:4], "z", zs)

    fdtd = OE()
    enhance_fdtd(fdtd, primitives_mesh_setup)

    from app.meshing.rules import compute_targets

    targets = compute_targets(mesh)
    # production order: enhance_fdtd -> SetBoundaryCond -> generate_mesh
    from app.meshing.easy_wrapper import apply_boundary

    apply_boundary(fdtd, boundary, mesh, targets)
    setup = generate_mesh(csx, mesh, boundary, targets, primitives_mesh_setup)
    print("mesh setup keys", sorted(setup.keys()))
    print("EASY MESH POLYHEDRON SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
