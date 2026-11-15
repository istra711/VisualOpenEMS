"""Phase-2 tests: templates, preflight, structured patches and JSON parsing."""

from __future__ import annotations

import json

from app.ai.generation import (
    extract_json_object,
    parse_patch_suggestion,
    parse_template_suggestion,
    template_suggestion_prompt,
)
from app.ai.preflight import preflight
from app.ai.project_patch import apply_project_patch, parse_project_patch
from app.ai.templates import build_template, templates
from app.models.project import PortDef, Project


def test_all_templates_build_and_pass_preflight():
    ids = [item.id for item in templates()]
    assert "patch" in ids
    assert "yagi" in ids
    for template_id in ids:
        generated = build_template(template_id)
        report = preflight(generated.project, generated.primitives)
        assert report.ok, (template_id, report.errors_text())
        assert generated.project.primitives
        assert generated.primitives


def test_unknown_template_is_rejected():
    try:
        build_template("does-not-exist")
    except ValueError as exc:
        assert "unknown template" in str(exc)
    else:
        raise AssertionError("unknown template was accepted")


def test_structured_patch_sets_fields_variables_ports_and_mesh():
    generated = build_template("patch")
    payload = {
        "title": "Tune patch",
        "operations": [
            {"op": "set", "path": "sim.max_timesteps", "value": 300_000},
            {"op": "set_variable", "name": "Lg", "value": 20.0},
            {"op": "set_port", "name": "in1", "fields": {"excite_port": True}},
            {"op": "set_mesh", "fields": {"preset": "high"}},
        ],
    }
    patch, errors = parse_project_patch(payload)
    assert not errors and patch is not None

    result, primitives, errors = apply_project_patch(
        generated.project, generated.primitives, patch
    )
    assert not errors
    assert result.sim.max_timesteps == 300_000
    assert any(variable.name == "Lg" for variable in result.variables)
    assert result.mesh.preset == "high"
    assert result.ports[0].excite_port is True
    # The input project is not mutated.
    assert generated.project.sim.max_timesteps != 300_000
    assert preflight(result, primitives).ok


def test_patch_rejects_unknown_operation_and_field():
    generated = build_template("patch")
    patch, errors = parse_project_patch(
        {"operations": [{"op": "run_python", "code": "print(1)"}]}
    )
    assert patch is None and errors

    patch, errors = parse_project_patch(
        {"operations": [{"op": "set_port", "index": 0, "fields": {"evil": 1}}]}
    )
    assert not errors and patch is not None
    _project, _primitives, apply_errors = apply_project_patch(
        generated.project, generated.primitives, patch
    )
    assert apply_errors and "not allowed" in apply_errors[0]


def test_preflight_finds_no_active_port_and_bad_target():
    project = Project.new_default("bad")
    project.mesh.target_frequency = 5e9
    # No ports at all and a target outside the default sweep.
    report = preflight(project, [])
    codes = {issue.code for issue in report.errors}
    assert "no_active_port" in codes
    assert any(issue.code == "target_outside_sweep" for issue in report.warnings)


def test_extract_json_from_markdown_and_template_parse():
    text = """Here is the proposal:
```json
{"template":"patch","title":"Test","parameters":{"f0":2.45,"eps_r":4.4},
 "assumptions":["starting dimensions"]}
```
"""
    payload, error = extract_json_object(text)
    assert error == "" and payload["template"] == "patch"
    suggestion, errors = parse_template_suggestion(text)
    assert not errors and suggestion is not None
    assert suggestion.template.id == "patch"
    assert abs(float(suggestion.parameters["f0"]) - 2.45) < 1e-12


def test_parse_patch_suggestion_from_json_text():
    text = json.dumps(
        {
            "title": "Raise budget",
            "operations": [
                {"op": "set", "path": "sim.max_timesteps", "value": 250000}
            ],
        }
    )
    patch, errors = parse_patch_suggestion(text)
    assert not errors and patch is not None
    assert patch.operations[0].op == "set"


def test_template_prompt_lists_parameters():
    prompt = template_suggestion_prompt("2.45 GHz patch on FR4")
    assert "patch" in prompt
    assert "patch_width" in prompt
    assert "Return ONLY one JSON object" in prompt


def test_patch_adds_port_and_material():
    project = Project.new_default("add")
    project.ports = [
        PortDef(name="p1", start=[0, 0, 0], stop=[0, 0, 1], excite_port=True)
    ]
    patch, errors = parse_project_patch(
        {
            "operations": [
                {
                    "op": "add_material",
                    "fields": {
                        "name": "fr4",
                        "kind": "dielectric",
                        "epsilon": 4.4,
                    },
                },
                {
                    "op": "add_port",
                    "fields": {
                        "name": "p2",
                        "type": "lumped",
                        "start": [1, 0, 0],
                        "stop": [1, 0, 1],
                        "excite_port": False,
                    },
                },
            ]
        }
    )
    assert not errors and patch is not None
    result, _primitives, errors = apply_project_patch(project, [], patch)
    assert not errors
    assert any(material.name == "fr4" for material in result.materials)
    assert any(port.name == "p2" for port in result.ports)
