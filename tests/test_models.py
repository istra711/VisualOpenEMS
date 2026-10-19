"""Unit tests for project model and meshing validators (no GUI, no full FDTD)."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def test_project_roundtrip():
    from app.models.project import Project

    p = Project.new_default("roundtrip")
    p.mesh.start_frequency = 1e9
    p.mesh.stop_frequency = 3e9
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "proj.fcsim.json"
        p.save(path)
        q = Project.load(path)
    assert q.name == "roundtrip"
    assert q.mesh.stop_frequency == 3e9
    assert any(m.name == "copper" for m in q.materials)


def test_num_lines_clamped_for_easymesh():
    """num_lines < 2 breaks easyMesh ("zero-size array to reduction ... min")."""
    from app.models.project import BoundarySpec, MeshSpec

    boundary = BoundarySpec(air_margin=["auto"] * 6)
    # value -> expected (0/None = "unset" -> default 5; 1 -> 2; else 2..50)
    for value, want in ((None, 5), (0, 5), (1, 2), (2, 2), (5, 5), (80, 50)):
        mesh = MeshSpec(drawing_unit="mm", num_lines=value)
        assert mesh.effective_num_lines() == want, (value, mesh.effective_num_lines())
        assert mesh.global_setup(boundary)["num_lines"] == want, value


def test_max_grading_ratio_clamped():
    """A ratio of 1.0 breaks CSXCAD's SmoothMeshLines cap -> cell_too_large."""
    from app.models.project import BoundarySpec, MeshSpec

    boundary = BoundarySpec(air_margin=["auto"] * 6)
    for value, want in ((None, 2.0), (0.0, 2.0), (1.0, 1.05), (1.05, 1.05),
                        (2.0, 2.0), (50.0, 10.0)):
        mesh = MeshSpec(drawing_unit="mm", max_grading_ratio=value)
        assert mesh.effective_max_grading_ratio() == want, (
            value, mesh.effective_max_grading_ratio()
        )
        assert mesh.global_setup(boundary)["max_grading_ratio"] == want, value


def test_mesh_targets():
    from app.models.project import MeshSpec
    from app.meshing.rules import compute_targets, wavelength_m

    # c0 / 3e8 Hz ≈ 0.9993 m
    lam = wavelength_m(3e8)
    assert 0.99 < lam < 1.01
    mesh = MeshSpec(drawing_unit="mm", start_frequency=1e9, stop_frequency=3e9, target_frequency=3e9)
    t = compute_targets(mesh, eps_max=1.0)
    assert t.lambda_min_units > 0
    assert t.max_cell_units < t.lambda_min_units


def test_validate_mesh_errors_on_coarse():
    from CSXCAD import ContinuousStructure
    from app.meshing.rules import compute_targets
    from app.meshing.validators import validate_mesh
    from app.models.project import MeshSpec

    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(1e-3)
    mesh = csx.GetGrid()
    # extremely coarse lines: 100 mm cells at 10 GHz => lambda ~ 30 mm
    mesh.SetLines("x", [0, 100, 200])
    mesh.SetLines("y", [0, 100, 200])
    mesh.SetLines("z", [0, 100, 200])
    spec = MeshSpec(drawing_unit="mm", start_frequency=1e10, stop_frequency=1e10)
    targets = compute_targets(spec, eps_max=1.0)
    report = validate_mesh(csx, targets, ports=[], max_grading=2.0)
    assert not report.ok
    assert any(i.code == "cell_too_large" for i in report.errors)


def test_port_alignment_error():
    from CSXCAD import ContinuousStructure
    from app.meshing.rules import compute_targets
    from app.meshing.validators import validate_mesh
    from app.models.project import MeshSpec, PortDef

    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(1e-3)
    mesh = csx.GetGrid()
    mesh.SetLines("x", [0, 1, 2, 3])
    mesh.SetLines("y", [0, 1, 2, 3])
    mesh.SetLines("z", [0, 1, 2, 3])
    spec = MeshSpec(drawing_unit="mm", start_frequency=1e6, stop_frequency=1e6)  # long lambda, cells ok
    targets = compute_targets(spec, eps_max=1.0)
    # port not on mesh line
    port = PortDef(name="p1", type="lumped", start=[0.5, 0.5, 0], stop=[0.5, 0.5, 1], direction="z")
    report = validate_mesh(csx, targets, ports=[port], max_grading=2.0)
    assert any(i.code == "port_alignment" for i in report.issues)


def test_primitive_outside_domain():
    from CSXCAD import ContinuousStructure
    from app.meshing.rules import compute_targets
    from app.meshing.validators import validate_mesh
    from app.models.project import MeshSpec

    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(1e-3)
    mesh = csx.GetGrid()
    mesh.SetLines("x", [0, 1, 2, 3, 4])
    mesh.SetLines("y", [0, 1, 2, 3, 4])
    mesh.SetLines("z", [0, 1, 2, 3, 4])
    spec = MeshSpec(drawing_unit="mm", start_frequency=1e6, stop_frequency=1e6)
    targets = compute_targets(spec, eps_max=1.0)

    prop = csx.AddMetal("pec_in")
    prop.AddBox([1, 1, 1], [2, 2, 2])
    report = validate_mesh(csx, targets, ports=[], max_grading=2.0)
    assert not any(i.code == "primitive_outside_domain" for i in report.issues)

    prop2 = csx.AddMetal("pec_out")
    prop2.AddBox([1, 1, 1], [9, 2, 2])
    report2 = validate_mesh(csx, targets, ports=[], max_grading=2.0)
    hits = [i for i in report2.issues if i.code == "primitive_outside_domain"]
    assert hits, "outside primitive must be flagged"
    assert hits[0].severity == "error"
    assert "x-max" in hits[0].message
    assert not report2.ok
    assert report2.stats.get("primitives_outside_domain") == 1


def main() -> int:
    tests = [
        test_project_roundtrip,
        test_mesh_targets,
        test_validate_mesh_errors_on_coarse,
        test_port_alignment_error,
        test_primitive_outside_domain,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"[PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {t.__name__}: {e}")
    print("Unit tests:", "PASS" if failed == 0 else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
