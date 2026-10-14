"""Cylinders along x/y must contribute BOTH ends to the mesh (domain coverage).

Regression: easyMesh's process_primitive used start +/- radius only, so a
cylinder whose axis runs along x or y (Yagi reflector/directors/boom) only
registered its start end - the FDTD domain collapsed inside the wire and the
element poked out of the black domain frame.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_EASY = ROOT / "third_party" / "easyMesh4openEMS"
if _EASY.is_dir() and str(_EASY) not in sys.path:
    sys.path.insert(0, str(_EASY))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _FakeAuto:
    found_circles: list = []
    primitives_mesh_setup: dict = {}
    properties_mesh_setup: dict = {}
    global_mesh_setup: dict = {"dirs": "xyz"}


def _add_cylinder(prop, start, stop, radius):
    prim = prop.AddCylinder(start, stop, radius, priority=10)
    if prim is None:
        prim = prop.GetPrimitive(prop.GetQtyPrimitives() - 1)
    return prim


def test_process_primitive_cylinder_uses_stop():
    from CSXCAD import ContinuousStructure
    from easyMesh import geometryUtils

    csx = ContinuousStructure()
    prop = csx.AddMetal("copper")
    prim = _add_cylinder(prop, [-30.0, 5.0, 0.0], [30.0, 5.0, 0.0], 1.0)

    x, y, x_edges, y_edges, diag = [], [], [], [], []
    geometryUtils.process_primitive(_FakeAuto(), prim, x, y, x_edges, y_edges, diag)

    # full extent along the axis (start AND stop end disc)
    assert min(x) <= -31.0 + 1e-9, x
    assert max(x) >= 31.0 - 1e-9, x
    assert min(y) <= 4.0 + 1e-9 and max(y) >= 6.0 - 1e-9, y

    xs = sorted({float(e[0]) for e in x_edges})
    assert xs and xs[0] <= -31.0 + 1e-9, xs
    assert xs[-1] >= 31.0 - 1e-9, xs

    # z-cylinder (classic case) must keep its transverse extent
    prim_z = _add_cylinder(prop, [100.0, 0.0, 0.0], [100.0, 0.0, 20.0], 2.0)
    x2, y2, xe2, ye2, d2 = [], [], [], [], []
    geometryUtils.process_primitive(_FakeAuto(), prim_z, x2, y2, xe2, ye2, d2)
    assert min(x2) <= 98.0 + 1e-9 and max(x2) >= 102.0 - 1e-9, x2
    assert min(y2) <= -2.0 + 1e-9 and max(y2) >= 2.0 - 1e-9, y2
    zs = geometryUtils.collect_z_coordinates([prim_z])
    assert [v for v, _ in zs] == [0.0, 20.0] or sorted(v for v, _ in zs) == [0.0, 20.0], zs


def test_generated_mesh_covers_cylinders():
    from CSXCAD import ContinuousStructure
    from openEMS import openEMS as OE

    from app.meshing.easy_wrapper import apply_boundary, enhance_csx, enhance_fdtd, generate_mesh
    from app.meshing.preview import get_sim_area, iter_primitive_infos, primitives_outside_domain
    from app.meshing.rules import compute_targets
    from app.meshing.validators import validate_mesh
    from app.models.project import BoundarySpec, MeshSpec

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
    csx.GetGrid().SetDeltaUnit(mesh.unit_to_meters())

    setup: dict = {}
    csx_w = enhance_csx(csx, setup)
    prop = csx_w.AddMetal("copper")
    _add_cylinder(prop, [-30.0, 5.0, 0.0], [30.0, 5.0, 0.0], 1.0)  # along x
    _add_cylinder(prop, [0.0, -60.0, 0.0], [0.0, 60.0, 0.0], 1.5)  # along y
    _add_cylinder(prop, [50.0, 0.0, 0.0], [50.0, 0.0, 25.0], 2.0)  # along z

    fdtd = OE(NrTS=10, EndCriteria=1e-3)
    enhance_fdtd(fdtd, setup)
    targets = compute_targets(mesh)
    apply_boundary(fdtd, boundary, mesh, targets)
    generate_mesh(csx, mesh, boundary, targets, primitives_mesh_setup=setup)

    area = get_sim_area(csx)
    assert area is not None
    infos = iter_primitive_infos(csx)
    assert len(infos) >= 3, [i.name for i in infos]

    hits = primitives_outside_domain(infos, area)
    assert not hits, [(h[0].name, "xyz"[h[1]], h[2]) for h in hits]

    for info in infos:
        for i in range(3):
            assert area[0][i] <= info.start[i] + 1e-9, (info.name, i, area, info.start)
            assert area[1][i] >= info.stop[i] - 1e-9, (info.name, i, area, info.stop)

    report = validate_mesh(csx, targets, ports=[])
    outside = [i for i in report.issues if i.code == "primitive_outside_domain"]
    assert not outside, [i.message for i in outside]


def main() -> int:
    failed = 0
    for t in (test_process_primitive_cylinder_uses_stop, test_generated_mesh_covers_cylinders):
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print("Cylinder extent tests:", "PASS" if failed == 0 else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
