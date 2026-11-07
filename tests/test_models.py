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


def test_project_paths_are_relative_and_follow_the_json():
    """Project-owned paths are stored relative to the JSON and survive a move."""
    import json
    import shutil

    from app.models.project import GeometryFile, Project

    with tempfile.TemporaryDirectory() as td:
        base = Path(td) / "case"
        data = base / "proj"
        data.mkdir(parents=True)
        path = base / "proj.fcsim.json"
        p = Project.new_default("proj")
        p.geometry_files = [
            GeometryFile(
                path=str(data / "geom.step"),
                format="step",
                mesh_path=str(data / "geom.npy"),
            )
        ]
        p.primitives = [
            {"kind": "stl", "name": "s", "path": str(data / "a.stl"),
             "mesh_path": str(data / "a.npy")}
        ]
        p.results = {
            "project_dir": str(data),
            "sim_dir": str(data / "simulation"),
            "sparams": str(data / "sparams" / "sparams.s2p"),
            "time": "now",
        }
        p.save(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["results"]["sparams"] == "proj/sparams/sparams.s2p"
        assert raw["geometry_files"][0]["path"] == "proj/geom.step"
        assert raw["geometry_files"][0]["mesh_path"] == "proj/geom.npy"
        assert raw["primitives"][0]["path"] == "proj/a.stl"

        moved = Path(td) / "moved"
        shutil.copytree(base, moved)
        q = Project.load(moved / "proj.fcsim.json")
        assert Path(q.results["sparams"]) == moved / "proj" / "sparams" / "sparams.s2p"
        assert Path(q.geometry_files[0].path) == moved / "proj" / "geom.step"
        assert Path(q.primitives[0]["path"]) == moved / "proj" / "a.stl"


def test_project_object_display_roundtrip():
    """Per-object Mesh Viewer appearance travels with the project file."""
    from app.models.project import Project

    p = Project.new_default("display")
    p.object_display = {
        "ground": {"color": [0.1, 0.2, 0.3], "transparency": 75, "visible": False},
    }
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "proj.fcsim.json"
        p.save(path)
        q = Project.load(path)
    assert q.object_display == p.object_display
    # old project files without the key fall back to an empty dict
    assert Project.from_dict({"name": "old"}).object_display == {}
    assert Project.from_dict({"object_display": "broken"}).object_display == {}


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


def test_pcb_mesh_profile():
    """PCB profile: fine enough for tracks/substrate, capped for large boards."""
    from app.meshing.rules import pcb_mesh_profile
    from app.models.project import MeshSpec

    m = MeshSpec(
        drawing_unit="mm",
        start_frequency=2.45e9,
        stop_frequency=2.95e9,
    )
    p = pcb_mesh_profile(m)
    assert p["preset"] == "high"
    assert abs(float(p["max_cellsize"]) - 0.5) < 1e-9
    assert abs(float(p["refined_cellsize"]) - 0.25) < 1e-9
    assert abs(float(p["min_cellsize"]) - 0.05) < 1e-9
    assert p["min_cellsize"] == p["min_cellsize_z"]
    assert float(p["max_grading_ratio"]) == 1.3
    assert p["smooth_metal_edge"] == "one_third_two_thirds"
    assert p["use_circle_detection"] and p["use_arc_detection"]
    assert p["add_graded_mesh_lines_at_material_transitions"]
    # Above ~60 GHz the lambda/20 cap must tighten the 0.5 mm default.
    high = pcb_mesh_profile(
        MeshSpec(
            drawing_unit="mm",
            start_frequency=80e9,
            stop_frequency=100e9,
        )
    )
    assert float(high["max_cellsize"]) < 0.5
    assert float(high["min_cellsize"]) < 0.05
    # Drawing-unit conversion: 0.5 mm is 500 um in a um project.
    um = pcb_mesh_profile(
        MeshSpec(
            drawing_unit="um",
            start_frequency=2.45e9,
            stop_frequency=2.95e9,
        )
    )
    assert abs(float(um["max_cellsize"]) - 500.0) < 1e-9
    assert abs(float(um["min_cellsize"]) - 50.0) < 1e-9


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


def test_port_alignment_skips_inactive_ports():
    """script_gen drops switched-off ports -> no mesh lines, no hard error."""
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
    spec = MeshSpec(drawing_unit="mm", start_frequency=1e6, stop_frequency=1e6)
    targets = compute_targets(spec, eps_max=1.0)
    off = PortDef(
        name="off",
        type="lumped",
        start=[0.5, 0.5, 0],
        stop=[0.5, 0.5, 1],
        direction="z",
        active=False,
    )
    report = validate_mesh(csx, targets, ports=[off], max_grading=2.0)
    assert not any(i.code == "port_alignment" for i in report.issues)
    assert report.ok


def test_port_alignment_tolerates_a_sub_cell_snap():
    """Within 15 % of a local cell openEMS' snap is negligible -> warning only."""
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
    spec = MeshSpec(drawing_unit="mm", start_frequency=1e6, stop_frequency=1e6)
    targets = compute_targets(spec, eps_max=1.0)
    close = PortDef(
        name="near", type="lumped", start=[1.05, 1.0, 0], stop=[1.05, 1.0, 1], direction="z"
    )
    report = validate_mesh(csx, targets, ports=[close], max_grading=2.0)
    snapped = [i for i in report.issues if i.code == "port_snapped"]
    assert snapped and snapped[0].severity == "warning", report.issues
    assert report.ok


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
        test_port_alignment_skips_inactive_ports,
        test_port_alignment_tolerates_a_sub_cell_snap,
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
