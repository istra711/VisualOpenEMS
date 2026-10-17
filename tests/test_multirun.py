"""Pure tests for the multi-excitation driver (app.simulation.multirun).

No Qt, no openEMS: it plans one run per port (script generation only) and
merges hand-written partial sparams.json tables into a full S-matrix.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.models.project import PortDef, Project
from app.simulation.multirun import merge_port_runs, plan_port_runs, summarise_table

_PRIM = [
    {
        "kind": "box",
        "name": "board",
        "material": "copper",
        "start": [0.0, 0.0, 0.0],
        "stop": [10.0, 5.0, 1.6],
    }
]


def _project() -> Project:
    p = Project.new_default("mr")
    p.ports = [
        PortDef(name="in1", type="lumped", start=[0, 0, 0], stop=[0, 0, 1], direction="z"),
        PortDef(name="out1", type="lumped", start=[5, 0, 0], stop=[5, 0, 1], direction="z"),
    ]
    return p


def _write_table(path, entries, n=5) -> None:
    freq = list(np.linspace(1e9, 3e9, n))
    data = {"freq": freq}
    for key, val in entries.items():
        arr = np.asarray(val, dtype=complex)
        data[key] = {
            "real": arr.real.tolist(),
            "imag": arr.imag.tolist(),
            "db": (20 * np.log10(np.maximum(np.abs(arr), 1e-12))).tolist(),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_plan_one_run_per_port(tmp_path):
    proj = _project()
    plans = plan_port_runs(proj, _PRIM, tmp_path)
    assert [pl.port_number for pl in plans] == [1, 2]
    assert [pl.port_name for pl in plans] == ["in1", "out1"]
    for plan, number in zip(plans, (1, 2)):
        assert plan.script_path.exists()
        copy = json.loads((plan.work_dir / "project_copy.json").read_text(encoding="utf-8"))
        excited = [i for i, p in enumerate(copy["ports"], start=1) if p["excite_port"]]
        assert excited == [number], (number, excited)
    # the caller's project must be untouched (deep copies only)
    assert [p.excite_port for p in proj.ports] == [True, True]


def test_plan_requires_two_ports(tmp_path):
    proj = Project.new_default("one")
    proj.ports = [PortDef(name="only")]
    with pytest.raises(ValueError):
        plan_port_runs(proj, _PRIM, tmp_path)


def test_plan_only_ports_subset(tmp_path):
    proj = _project()
    with pytest.raises(ValueError):
        plan_port_runs(proj, _PRIM, tmp_path, only_ports=[1])
    plans = plan_port_runs(proj, _PRIM, tmp_path, only_ports=[2, 1])
    assert [pl.port_number for pl in plans] == [2, 1]


def test_merge_builds_full_matrix(tmp_path):
    proj = _project()
    plans = plan_port_runs(proj, _PRIM, tmp_path)
    # run 1 (port 1 excited) measures column 1: S11, S21
    _write_table(
        port_sparams_json_path(plans[0]),
        {"S11": np.full(5, 0.2 + 0.1j), "S21": np.full(5, 0.7 - 0.2j)},
    )
    # run 2 (port 2 excited) measures column 2: S12, S22
    _write_table(
        port_sparams_json_path(plans[1]),
        {"S12": np.full(5, 0.6 - 0.1j), "S22": np.full(5, 0.3 + 0.05j)},
    )
    result = merge_port_runs(tmp_path, plans)
    table = result["table"]
    assert result["missing"] == []
    for key in ("S11", "S21", "S12", "S22"):
        assert key in table
        assert np.isfinite(np.asarray(table[key], dtype=complex)).all()
    # exported into <base>/sparams: json + complete .s2p
    written = result["written"]
    assert "json" in written and "touchstone" in written
    assert str(written["touchstone"]).endswith(".s2p")


def test_merge_reports_missing_run(tmp_path):
    proj = _project()
    plans = plan_port_runs(proj, _PRIM, tmp_path)
    _write_table(
        port_sparams_json_path(plans[0]),
        {"S11": np.full(5, 0.2 + 0.1j), "S21": np.full(5, 0.7 - 0.2j)},
    )
    # run 2 produced nothing
    result = merge_port_runs(tmp_path, plans)
    assert result["missing"] == [2]
    assert "S11" in result["table"]


def test_summarise_table_lines(tmp_path):
    table = {
        "freq": np.linspace(1e9, 3e9, 5),
        "S11": np.full(5, 0.5 + 0j),
        "S21": np.full(5, 0.1 + 0j),
    }
    lines = summarise_table(table)
    assert any("S11" in ln for ln in lines)
    assert any("S21" in ln for ln in lines)


def port_sparams_json_path(plan):
    """Path the run's own post-processing writes (new layout)."""
    return plan.work_dir / "sparams" / "sparams.json"
