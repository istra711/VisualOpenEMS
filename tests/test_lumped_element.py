"""Pure tests for the CSXCAD lumped-element helper and primitive bbox."""

from __future__ import annotations

from pathlib import Path

import pytest
from CSXCAD import ContinuousStructure

from app.csxcad_builder.lumped import add_lumped_element
from app.meshing.bounds import primitive_view_bbox


def _prim() -> dict:
    return {
        "kind": "lumped",
        "name": "R1",
        "direction": "z",
        "start": [0.0, 0.0, 0.0],
        "stop": [0.0, 0.0, 1.0],
        "resistance": 50.0,
        "inductance": 10e-9,
        "capacitance": 2e-12,
        "caps": True,
        "priority": 15,
    }


def test_lumped_element_property_and_box():
    csx = ContinuousStructure()
    prop = add_lumped_element(csx, _prim())
    assert prop.GetDirection() == 2  # z
    assert prop.GetResistance() == pytest.approx(50.0)
    assert prop.GetInductance() == pytest.approx(10e-9)
    assert prop.GetCapacity() == pytest.approx(2e-12)
    assert bool(prop.GetCaps()) is True
    assert prop.GetQtyPrimitives() == 1
    assert prop.GetName() == "R1"


def test_lumped_element_bbox():
    lo, hi = primitive_view_bbox(_prim())
    assert lo == [0.0, 0.0, 0.0]
    assert hi == [0.0, 0.0, 1.0]


def test_build_scene_csx_contains_lumped_element(tmp_path):
    from app.meshing.build_task import build_scene_csx
    from app.models.project import Project

    project = Project.new_default("lumped")
    result = build_scene_csx(project, [_prim()], tmp_path)
    assert result.csx is not None
    props = result.csx.GetAllProperties()
    lumped = [p for p in props if "lumped" in str(p.GetTypeString()).lower()]
    assert lumped and lumped[0].GetName() == "R1"


def test_generated_run_script_contains_lumped_element(tmp_path):
    from app.models.project import Project
    from app.simulation.script_gen import generate_run_script

    project = Project.new_default("lumped")
    script = generate_run_script(
        project,
        tmp_path / "lumped.fcsim.json",
        tmp_path,
        primitives=[_prim()],
    )
    text = Path(script).read_text(encoding="utf-8")
    assert "add_lumped_element" in text
