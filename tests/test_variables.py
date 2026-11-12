"""Pure tests for named geometry variables and expression resolution."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def test_evaluate_expression_numbers_variables_and_functions():
    from app.models.variables import VariableError, evaluate_expression

    assert evaluate_expression("12.5") == 12.5
    assert evaluate_expression("1,5") == 1.5
    assert evaluate_expression("L / 2", {"L": 20}) == 10.0
    assert evaluate_expression("w * 2 + 1", {"W": 4}) == 9.0  # case-insensitive
    assert evaluate_expression("sqrt(L*L + W*W)", {"L": 3, "W": 4}) == 5.0
    assert abs(evaluate_expression("sin(pi/2)") - 1.0) < 1e-12
    assert evaluate_expression("2**3") == 8.0
    assert evaluate_expression("7 // 2") == 3.0
    assert evaluate_expression("7 % 4") == 3.0
    assert evaluate_expression("-(L+1)", {"L": 2}) == -3.0
    for bad in ("", "L +", "__import__('os')", "L.real", "L[0]", "max(1,2)"):
        try:
            evaluate_expression(bad, {"L": 1})
        except VariableError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"expression should be rejected: {bad!r}")


def test_variable_validation_and_rename():
    from app.models.project import VariableDef
    from app.models.variables import (
        rename_expression,
        rename_expression_entry,
        rename_expression_map,
        validate_variable_defs,
        variable_values_from_defs,
    )

    defs = [
        VariableDef("L", 20.0, "length"),
        VariableDef("W", 10.0),
    ]
    assert validate_variable_defs(defs) == []
    assert variable_values_from_defs(defs) == {"L": 20.0, "W": 10.0}

    bad = [
        VariableDef("", 1.0),
        VariableDef("2L", 1.0),
        VariableDef("sqrt", 1.0),
        VariableDef("and", 1.0),
        VariableDef("L", 1.0),
        VariableDef("l", 2.0),
        VariableDef("X", float("nan")),
    ]
    errors = validate_variable_defs(bad)
    assert any("empty" in e for e in errors)
    assert any("already used" in e for e in errors)
    assert any("reserved" in e for e in errors)
    assert any("finite" in e for e in errors)

    renames = {"L": "length", "W": "width"}
    assert rename_expression("L/2 + W", renames) == "length/2 + width"
    assert rename_expression("l/2", renames) == "length/2"  # case-insensitive
    assert rename_expression("sqrt(L)", renames) == "sqrt(length)"
    assert rename_expression_entry({"expr": "L", "scale": 1.0}, renames) == {
        "expr": "length",
        "scale": 1.0,
    }
    assert rename_expression_map({"stop.0": "L+W"}, renames) == {
        "stop.0": "length+width"
    }


def test_resolve_primitive_keeps_expressions_and_updates_bounds():
    from app.models.variables import resolve_primitive

    prim = {
        "kind": "box",
        "name": "b",
        "material": "copper",
        "start": [0.0, 0.0, 0.0],
        "stop": [1.0, 1.0, 1.0],
        "expressions": {"start.0": "L/2", "stop.1": "W + 2"},
    }
    out, errors = resolve_primitive(prim, {"L": 20, "W": 4})
    assert errors == []
    assert out["start"][0] == 10.0
    assert out["stop"][1] == 6.0
    assert out["expressions"]["start.0"] == "L/2"
    # the original is untouched
    assert prim["start"][0] == 0.0

    sphere = {
        "kind": "sphere",
        "name": "s",
        "center": [0.0, 0.0, 0.0],
        "radius": 1.0,
        "start": [-1.0, -1.0, -1.0],
        "stop": [1.0, 1.0, 1.0],
        "expressions": {"center.0": "C", "radius": "R"},
    }
    out, errors = resolve_primitive(sphere, {"C": 5, "R": 2})
    assert errors == []
    assert out["center"] == [5.0, 0.0, 0.0]
    assert out["radius"] == 2.0
    assert out["start"] == [3.0, -2.0, -2.0]
    assert out["stop"] == [7.0, 2.0, 2.0]


def test_resolve_polygon_points_and_wire_radius():
    from app.models.variables import resolve_primitive

    polygon = {
        "kind": "polygon",
        "name": "p",
        "axis": "z",
        "coord": 0.0,
        "length": 1.0,
        "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
        "expressions": {
            "points.0.0": "W/2",
            "points.1.0": "W",
            "coord": "Z",
            "length": "T",
        },
    }
    out, errors = resolve_primitive(polygon, {"W": 10, "Z": 2, "T": 3})
    assert errors == []
    assert out["points"][0][0] == 5.0
    assert out["points"][1][0] == 10.0
    assert out["coord"] == 2.0
    assert out["length"] == 3.0
    assert out["start"] == [1.0, 0.0, 2.0]
    assert out["stop"] == [10.0, 1.0, 5.0]

    wire = {
        "kind": "wire",
        "name": "w",
        "radius": 0.5,
        "points": [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]],
        "expressions": {"radius": "R"},
    }
    out, errors = resolve_primitive(wire, {"R": 2})
    assert errors == []
    assert out["radius"] == 2.0
    assert out["start"] == [-2.0, -2.0, -2.0]
    assert out["stop"] == [12.0, 2.0, 2.0]


def test_resolve_port_dump_and_nf2ff():
    from app.models.project import DumpDef, NF2FFDef, PortDef
    from app.models.variables import resolve_dump, resolve_nf2ff, resolve_port

    port = PortDef(
        name="p1",
        start=[0, 0, 0],
        stop=[1, 1, 1],
        wg_a=0,
        expressions={"start.0": "X", "wg_a": "A"},
    )
    out, errors = resolve_port(port, {"X": -2, "A": 10})
    assert errors == []
    assert out.start[0] == -2.0
    assert out.wg_a == 10.0
    assert port.start[0] == 0.0

    import math

    polar = PortDef(
        name="p2",
        start=[0, 0, 0],
        stop=[0, 1, 0],
        expressions={"start.1": {"expr": "PHI", "scale": math.pi / 180.0}},
    )
    out, errors = resolve_port(polar, {"PHI": 90})
    assert errors == []
    assert abs(out.start[1] - math.pi / 2) < 1e-12
    assert abs(polar.start[1]) < 1e-12

    dump = DumpDef(name="d", start=[0, 0, 0], stop=[1, 1, 1], expressions={"stop.2": "Z"})
    out, errors = resolve_dump(dump, {"Z": 7})
    assert errors == []
    assert out.stop[2] == 7.0

    nf = NF2FFDef(start=[-1, -2, -3], stop=[1, 2, 3], expressions={"start.0": "A"})
    out, errors = resolve_nf2ff(nf, {"A": -5})
    assert errors == []
    assert out.start[0] == -5.0
    assert out.center == [(-5 + 1) / 2, 0.0, 0.0]


def test_unknown_variable_reports_context():
    from app.models.variables import resolve_primitive

    prim = {
        "kind": "box",
        "name": "b",
        "start": [0, 0, 0],
        "stop": [1, 1, 1],
        "expressions": {"stop.0": "Missing/2"},
    }
    out, errors = resolve_primitive(prim, {})
    assert out["stop"][0] == 1.0
    assert errors and "Missing" in errors[0] and "b.stop.0" in errors[0]


def test_project_roundtrip_variables_and_expressions():
    from app.models.project import (
        DumpDef,
        NF2FFDef,
        PortDef,
        Project,
        VariableDef,
    )

    project = Project.new_default("vars")
    project.variables = [VariableDef("L", 20.0, "length"), VariableDef("W", 10.0)]
    project.primitives = [
        {
            "kind": "box",
            "name": "b",
            "material": "copper",
            "start": [0, 0, 0],
            "stop": [1, 1, 1],
            "expressions": {"stop.0": "L", "stop.1": {"expr": "W/2", "scale": 1.0}},
        }
    ]
    project.ports = [
        PortDef(name="p", start=[0, 0, 0], stop=[1, 1, 1], expressions={"stop.0": "L"})
    ]
    project.dumps = [
        DumpDef(name="d", start=[0, 0, 0], stop=[1, 1, 1], expressions={"stop.1": "W"})
    ]
    project.nf2ff = NF2FFDef(start=[-1, -1, -1], stop=[1, 1, 1],
                            expressions={"stop.2": "L"})
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "vars.fcsim.json"
        project.save(path)
        loaded = Project.load(path)

    assert [v.name for v in loaded.variables] == ["L", "W"]
    assert loaded.variables[0].value == 20.0
    assert loaded.variables[0].comment == "length"
    assert loaded.variable_values() == {"L": 20.0, "W": 10.0}
    assert loaded.primitives[0]["expressions"]["stop.1"]["expr"] == "W/2"
    assert loaded.ports[0].expressions["stop.0"] == "L"
    assert loaded.dumps[0].expressions["stop.1"] == "W"
    assert loaded.nf2ff.expressions["stop.2"] == "L"


def test_old_project_without_variables_loads():
    from app.models.project import Project

    old = Project.from_dict({"name": "old", "variables": None})
    assert old.variables == []
    assert old.variable_values() == {}
    old2 = Project.from_dict({"name": "old2", "variables": {"L": 3}})
    assert old2.variables[0].name == "L"
    assert old2.variables[0].value == 3.0
