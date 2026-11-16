"""Unit tests for reusable user project templates (no Qt, no network)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.project import GeometryFile, PortDef, Project, VariableDef
from app.project_templates import (
    TEMPLATE_KEY,
    TemplateError,
    delete_project_template,
    list_project_templates,
    load_project_template,
    sanitize_template_name,
    save_project_template,
    template_path,
    template_summary,
    templates_dir,
)


def _project() -> Project:
    project = Project.new_default("My Template Project")
    project.project_id = "original-id"
    project.primitives = [
        {
            "kind": "box",
            "name": "patch",
            "material": "copper",
            "start": [-5.0, -5.0, 1.6],
            "stop": [5.0, 5.0, 1.635],
        }
    ]
    project.ports = [
        PortDef(
            name="p1",
            start=[0.0, 0.0, 0.0],
            stop=[0.0, 0.0, 1.0],
            excite_port=True,
        )
    ]
    project.variables = [VariableDef(name="L", value=20.0)]
    project.mesh.start_frequency = 2.0e9
    project.mesh.stop_frequency = 3.0e9
    project.mesh.target_frequency = 2.45e9
    project.mesh.preset = "high"
    project.results = {"sparams": "old-result.json"}
    project.sparam_reference = {"path": "old-reference.s2p"}
    project.sparam_markers = [{"f": 2.45e9, "tag": "marker"}]
    return project


def test_templates_dir_uses_setting(tmp_path):
    settings = SimpleNamespace(template_dir=str(tmp_path / "my-templates"))
    assert templates_dir(settings) == tmp_path / "my-templates"


def test_sanitize_and_template_path():
    assert sanitize_template_name("2.4 GHz Patch / FR4") == "2.4_GHz_Patch_FR4"
    assert sanitize_template_name("../../evil") == "evil"
    assert sanitize_template_name("") == "project_template"
    path = template_path("C:/templates", "My template")
    assert path.name == "My_template.fcsim.json"


def test_save_and_load_template_keeps_design_strips_results(tmp_path):
    project = _project()
    info = save_project_template(
        tmp_path,
        project,
        project.primitives or [],
        "2.4 GHz Patch / FR4",
        "Probe-fed patch with a 50 ohm port.",
    )
    assert info.path.exists()
    assert info.path.name == "2.4_GHz_Patch_FR4.fcsim.json"
    assert info.name == "2.4 GHz Patch / FR4"
    assert info.description.startswith("Probe-fed")
    assert info.ports == 1
    assert info.primitives == 1
    assert info.variables == 1
    assert info.mesh_preset == "high"
    assert info.start_frequency == 2.0e9
    assert info.stop_frequency == 3.0e9
    assert info.target_frequency == 2.45e9
    assert not info.has_results

    raw = info.path.read_text(encoding="utf-8")
    assert TEMPLATE_KEY in raw
    assert '"results": null' in raw
    assert '"sparam_reference": null' in raw

    loaded = load_project_template(info.path)
    assert loaded.project.name == "My Template Project"
    assert loaded.project.project_id != "original-id"
    assert loaded.project.results is None
    assert loaded.project.sparam_reference is None
    assert loaded.project.sparam_markers == []
    assert loaded.project.ports[0].name == "p1"
    assert loaded.project.ports[0].excite_port is True
    assert loaded.project.variables[0].name == "L"
    assert loaded.project.mesh.preset == "high"
    assert loaded.primitives == project.primitives
    assert "2.4 GHz Patch" in loaded.summary()


def test_list_sorting_and_delete(tmp_path):
    project = _project()
    save_project_template(tmp_path, project, project.primitives or [], "Zebra")
    save_project_template(tmp_path, project, project.primitives or [], "Alpha")
    infos = list_project_templates(tmp_path)
    assert [info.name for info in infos] == ["Alpha", "Zebra"]
    assert all(info.valid for info in infos)
    delete_project_template(infos[0].path)
    assert [info.name for info in list_project_templates(tmp_path)] == ["Zebra"]


def test_overwrite_requires_flag(tmp_path):
    project = _project()
    save_project_template(tmp_path, project, project.primitives or [], "Same")
    with pytest.raises(FileExistsError):
        save_project_template(tmp_path, project, project.primitives or [], "Same")
    info = save_project_template(
        tmp_path,
        project,
        project.primitives or [],
        "Same",
        "replacement",
        overwrite=True,
    )
    assert info.description == "replacement"


def test_empty_name_rejected(tmp_path):
    project = _project()
    with pytest.raises(ValueError):
        save_project_template(tmp_path, project, project.primitives or [], "   ")


def test_invalid_file_is_listed_with_error(tmp_path):
    bad = tmp_path / "broken.fcsim.json"
    bad.write_text("{not json", encoding="utf-8")
    not_project = tmp_path / "notes.fcsim.json"
    not_project.write_text('{"hello": "world"}', encoding="utf-8")
    infos = list_project_templates(tmp_path)
    assert {info.name for info in infos} == {"broken.fcsim.json", "notes.fcsim.json"}
    assert all(not info.valid for info in infos)
    assert all(info.error for info in infos)
    with pytest.raises(TemplateError):
        load_project_template(bad)


def test_manual_project_in_template_folder_is_usable(tmp_path):
    project = _project()
    manual = project.save(tmp_path / "manual.fcsim.json")
    infos = list_project_templates(tmp_path)
    assert len(infos) == 1
    info = infos[0]
    assert info.valid
    assert info.name == "My Template Project"
    loaded = load_project_template(manual)
    assert loaded.info.name == "My Template Project"
    assert loaded.primitives == project.primitives
    assert loaded.project.results is None


def test_template_summary_contains_key_facts(tmp_path):
    project = _project()
    info = save_project_template(
        tmp_path, project, project.primitives or [], "Summary test"
    )
    text = template_summary(info)
    assert "1 port" in text
    assert "1 primitive" in text
    assert "2–3 GHz" in text
    assert "f0 2.45 GHz" in text
    assert "mesh high" in text


def test_template_geometry_paths_resolve_relative_to_template_folder(tmp_path):
    project = _project()
    cad = tmp_path / "cad" / "part.step"
    cad.parent.mkdir()
    cad.write_text("STEP", encoding="utf-8")
    project.geometry_files = [GeometryFile(path=str(cad), format="step")]

    folder = tmp_path / "templates"
    info = save_project_template(
        folder, project, project.primitives or [], "Path test"
    )
    raw = json.loads(info.path.read_text(encoding="utf-8"))
    stored = raw["geometry_files"][0]["path"]
    assert stored.replace("\\", "/") == "../cad/part.step"

    loaded = load_project_template(info.path)
    assert Path(loaded.project.geometry_files[0].path).resolve() == cad.resolve()
