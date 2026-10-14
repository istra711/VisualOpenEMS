"""Smoke: easyMesh GenerateMesh with PolyhedronReader (.stl) — no GetCoords."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# do NOT insert third_party — use site-packages (what the GUI imports)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    import easyMesh
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS as OE

    print("easyMesh from", easyMesh.__file__)
    assert "site-packages" in str(easyMesh.__file__), "expected site-packages easyMesh"

    from app.csxcad_builder.triangle_mesh import add_triangle_mesh
    from app.meshing.easy_wrapper import apply_boundary, enhance_csx, enhance_fdtd, generate_mesh
    from app.meshing.rules import compute_targets
    from app.models.project import BoundarySpec, MeshSpec

    # write a tiny binary STL (one triangle) + use PolyhedronReader path
    stl = ROOT / "work" / "poly_reader_test.stl"
    stl.parent.mkdir(parents=True, exist_ok=True)
    # 80-byte header + 4-byte count + 50-byte tri
    tri = np.array([[0, 0, 0], [10, 0, 0], [10, 10, 0]], dtype=np.float32)
    n = np.array([0, 0, 1], dtype=np.float32)
    with open(stl, "wb") as f:
        f.write(b"smoke" + b"\x00" * 75)
        f.write(np.array([1], dtype=np.uint32).tobytes())
        f.write(n.tobytes())
        f.write(tri[0].tobytes())
        f.write(tri[1].tobytes())
        f.write(tri[2].tobytes())
        f.write(np.array([0], dtype=np.uint16).tobytes())

    mesh = MeshSpec(
        preset="low",
        drawing_unit="mm",
        start_frequency=2.0e9,
        stop_frequency=3.0e9,
        target_frequency=2.45e9,
    )
    boundary = BoundarySpec()

    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(mesh.unit_to_meters())
    primitives_mesh_setup = {}
    csx_w = enhance_csx(csx, primitives_mesh_setup)
    prop = csx_w.AddMetal("copper")
    prim = add_triangle_mesh(prop, name="reader", stl_path=stl, priority=10)
    print("prim type", type(prim).__name__, "has GetCoords", hasattr(prim, "GetCoords"))
    print("has GetBoundBox", hasattr(prim, "GetBoundBox"), "faces", prim.GetNumFaces() if hasattr(prim, "GetNumFaces") else "?")
    if hasattr(prim, "GetBoundBox"):
        print("bbox", prim.GetBoundBox())

    from easyMesh import geometryUtils

    class Fake:
        found_circles = []
        primitives_mesh_setup = {}
        properties_mesh_setup = {}
        global_mesh_setup = {"dirs": "xyz"}

    x, y, xe, ye, de = [], [], [], [], []
    geometryUtils.process_primitive(Fake(), prim, x, y, xe, ye, de)
    print("process_primitive ok", "x", x[:6])
    zs = geometryUtils.collect_z_coordinates([prim])
    print("z", zs)

    fdtd = OE()
    enhance_fdtd(fdtd, primitives_mesh_setup)
    targets = compute_targets(mesh)
    apply_boundary(fdtd, boundary, mesh, targets)
    setup = generate_mesh(csx, mesh, boundary, targets, primitives_mesh_setup)
    print("mesh setup keys", sorted(setup.keys()))
    print("POLYHEDRON READER SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
