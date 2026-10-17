"""Pure tests for the MSL (microstrip) port fix (no Qt, no openEMS run).

Covers app.simulation.msl.resolve_msl_axes and the generated run script's
AddMSLPort call (metal property + prop/exc axis, no resistance-as-metal bug).
"""

from __future__ import annotations

import json

import pytest

from app.models.project import PortDef, Project
from app.simulation.msl import resolve_msl_axes


def _msl(**kw) -> PortDef:
    return PortDef(name="p", type="msl", **kw)


def test_derive_axes_from_extents_tutorial_geometry():
    # openEMS MSL tutorial: x = line length (longest), z = substrate (shortest)
    p = _msl(start=[-10.0, -1.0, 1.6], stop=[0.0, 1.0, 0.0])
    assert resolve_msl_axes(p) == ("x", "z")


def test_derive_axes_y_propagation():
    p = _msl(start=[-1.0, -20.0, 1.2], stop=[1.0, 20.0, 0.0])
    assert resolve_msl_axes(p) == ("y", "z")


def test_explicit_directions_win():
    p = _msl(
        start=[-10.0, -1.0, 1.6],
        stop=[0.0, 1.0, 0.0],
        prop_direction="y",
        exc_direction="x",
    )
    assert resolve_msl_axes(p) == ("y", "x")


def test_invalid_axis_strings_are_ignored():
    p = _msl(
        start=[-10.0, -1.0, 1.6],
        stop=[0.0, 1.0, 0.0],
        prop_direction="auto",
        exc_direction="",
    )
    assert resolve_msl_axes(p) == ("x", "z")


def test_zero_extent_propagation_raises():
    p = _msl(start=[0.0, -1.0, 1.6], stop=[0.0, 1.0, 0.0], prop_direction="x")
    with pytest.raises(ValueError):
        resolve_msl_axes(p)


def test_single_axis_port_raises():
    p = _msl(start=[0.0, 0.0, 0.0], stop=[5.0, 0.0, 0.0])
    with pytest.raises(ValueError):
        resolve_msl_axes(p)


def test_identical_start_stop_raises():
    p = _msl(start=[1.0, 1.0, 1.0], stop=[1.0, 1.0, 1.0])
    with pytest.raises(ValueError):
        resolve_msl_axes(p)


def test_port_json_round_trip_keeps_msl_fields():
    p = _msl(
        start=[-10.0, -1.0, 1.6],
        stop=[0.0, 1.0, 0.0],
        prop_direction="x",
        exc_direction="z",
    )
    proj = Project.new_default("msl")
    proj.ports = [p]
    restored = Project.from_dict(json.loads(json.dumps(proj.to_dict())))
    assert restored.ports[0].prop_direction == "x"
    assert restored.ports[0].exc_direction == "z"


def test_generated_script_uses_metal_and_axes(tmp_path):
    from app.simulation.script_gen import generate_run_script

    proj = Project.new_default("msl_gen")
    proj.ports = [
        _msl(start=[-10.0, -1.0, 1.6], stop=[0.0, 1.0, 0.0], excite_port=True)
    ]
    script = generate_run_script(proj, tmp_path / "p.fcsim.json", tmp_path, primitives=[])
    text = script.read_text(encoding="utf-8")
    assert "resolve_msl_axes(port)" in text
    # the strip metal (not the port resistance) is passed as metal_prop
    assert "AddMSLPort(\n            idx,\n            metal," in text
    assert "AddMSLPort(\n                idx,\n                port.resistance" not in text
