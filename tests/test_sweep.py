"""Project persistence for the variable-sweep definition."""

from __future__ import annotations

from app.models.project import PortDef, Project
from app.models.sweep import SweepSpec
import json

from app.simulation.sweep import SweepPoint, finish_sweep, plan_sweep_runs


def test_sweep_spec_roundtrip_with_project(tmp_path):
    project = Project.new_default("sweep-project")
    project.sweep = SweepSpec(
        enabled=True,
        variable="L",
        values=[10.0, 12.0, 14.0],
        full_smatrix=True,
        collect_nf2ff=False,
        collect_fields=False,
        stop_on_error=True,
    )
    path = tmp_path / "sweep-project.fcsim.json"
    project.save(path)

    restored = Project.load(path)
    assert restored.sweep is not None
    assert restored.sweep.enabled is True
    assert restored.sweep.variable == "L"
    assert restored.sweep.values == [10.0, 12.0, 14.0]
    assert restored.sweep.full_smatrix is True
    assert restored.sweep.stop_on_error is True
    assert restored.sweep.summary() == "L 10→14 (3 points)"


def test_port_phase_sweep_plans_one_run_per_phase(tmp_path):
    project = Project.new_default("phase-project")
    project.ports = [
        PortDef(
            name="p1",
            start=[0, 0, 0],
            stop=[0, 0, 1],
            excite_port=True,
        )
    ]
    spec = SweepSpec(
        enabled=True,
        target_kind="port_phase",
        target="p1",
        values=[0.0, 90.0, 180.0],
        full_smatrix=True,
        collect_nf2ff=True,
    )

    points, steps = plan_sweep_runs(project, [], tmp_path, spec)

    assert len(points) == 3
    assert len(steps) == 3
    assert all(step.port_number is None for step in steps)
    assert [p.values["p1"] for p in points] == [0.0, 90.0, 180.0]
    assert "phase" in points[0].label
    saved = Project.load(points[1].project_path)
    assert saved.ports[0].excite_phase_deg == 90.0
    assert saved.sim.nf2ff_enabled is True


def test_finish_sweep_records_nf2ff_path(tmp_path):
    point_dir = tmp_path / "sweep" / "point-000"
    (point_dir / "sparams").mkdir(parents=True)
    (point_dir / "sparams" / "sparams.json").write_text("{}", encoding="utf-8")
    (point_dir / "nf2ff").mkdir()
    (point_dir / "nf2ff" / "nf2ff.json").write_text("{}", encoding="utf-8")
    point = SweepPoint(
        index=0,
        values={"p1": 0.0},
        label="phase p1=0°",
        work_dir=point_dir,
        project_path=point_dir / "point_project.json",
    )
    spec = SweepSpec(
        enabled=True,
        target_kind="port_phase",
        target="p1",
        values=[0.0],
        collect_nf2ff=True,
    )

    result = finish_sweep(tmp_path, [point], spec)

    assert result["status"] == "complete"
    payload = json.loads((tmp_path / "sweep.json").read_text(encoding="utf-8"))
    assert payload["points"][0]["nf2ff"] == "sweep/point-000/nf2ff/nf2ff.json"
    assert payload["spec"]["target_kind"] == "port_phase"
