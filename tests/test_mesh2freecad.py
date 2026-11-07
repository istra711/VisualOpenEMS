"""Mesh2FreeCAD: options, project snapshot and temporary-file cleanup."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from app.freecad_io import mesh2freecad as m2f
from app.freecad_io.discretized_export.exporter import ExportOptions, export
from app.freecad_io.discretized_export.stl_io import read_binary_stl_count
from app.models.project import PortDef, Project


class _FakeCSX:
    def Write2XML(self, path) -> None:
        Path(path).write_text("<CSXCAD/>", encoding="utf-8")


class _FakeProcess:
    def __init__(self) -> None:
        self.waited = False

    def wait(self) -> int:
        self.waited = True
        return 0


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_project_snapshot_contains_live_primitives_and_mesh(tmp_path):
    project = Project.new_default("demo")
    project.mesh.drawing_unit = "mm"
    project.sim.cell_constant_material = True
    prim = {
        "kind": "box",
        "name": "patch",
        "material": "copper",
        "start": [0, 0, 0],
        "stop": [1, 1, 1],
    }

    path = m2f._project_snapshot(
        project, [prim], tmp_path / "csx.xml", tmp_path, "demo"
    )
    data = json.loads(path.read_text(encoding="utf-8"))

    assert path.name == "demo.fcsim.json"
    assert data["name"] == "demo"
    assert data["primitives"] == [prim]
    assert data["results"]["csx_xml"] == str(tmp_path / "csx.xml")
    assert data["mesh"]["drawing_unit"] == "mm"
    assert data["sim"]["cell_constant_material"] is True


def test_temporary_folder_is_removed_when_freecad_closes(tmp_path):
    temp = tmp_path / "mesh2freecad"
    temp.mkdir()
    (temp / "model.FCStd").write_text("x", encoding="utf-8")

    proc = _FakeProcess()
    m2f.schedule_cleanup(temp, proc)

    assert _wait_until(lambda: not temp.exists())
    assert proc.waited


def test_keep_temporary_files(tmp_path):
    temp = tmp_path / "mesh2freecad"
    temp.mkdir()
    m2f.schedule_cleanup(temp, None, keep=True)
    assert temp.is_dir()
    m2f.cleanup_all()
    assert temp.is_dir()  # keep is intentional


def test_cleanup_all_removes_registered_folders(tmp_path):
    temp = tmp_path / "mesh2freecad"
    temp.mkdir()
    m2f.schedule_cleanup(temp, None)
    assert not temp.exists()


def test_export_and_open_passes_options_and_cleans_up(tmp_path, monkeypatch):
    project = Project.new_default("demo")
    project.mesh.drawing_unit = "mm"
    prim = {
        "kind": "box",
        "name": "patch",
        "material": "copper",
        "start": [0, 0, 0],
        "stop": [1, 1, 1],
    }
    seen = {}

    def fake_export(options):
        seen["options"] = options
        out = Path(options.output_dir)
        fcstd = out / "demo_discretized.FCStd"
        fcstd.write_text("fake", encoding="utf-8")
        return SimpleNamespace(
            fcstd_file=fcstd,
            step_file=None,
            objects=[SimpleNamespace(name="patch")],
            warnings=["demo warning"],
            log="fake log",
        )

    proc = _FakeProcess()
    monkeypatch.setattr(m2f, "export", fake_export)
    monkeypatch.setattr(
        m2f, "_ensure_visible_document", lambda fcstd, gui, **kw: (True, "visible")
    )
    monkeypatch.setattr(
        m2f, "_open_document", lambda target, cmd, gui: (proc, f"Opened {target.name}")
    )

    result = m2f.export_and_open(
        project,
        [prim],
        _FakeCSX(),
        options=m2f.Mesh2FreeCADOptions(
            export_step=True,
            fcstd_object_type="mesh",
            voxel_grid=True,
            voxel_grid_mode="all",
            include_sheets=False,
            export_ports=False,
        ),
        name="demo",
    )

    assert result.ok
    assert result.fcstd is not None and result.fcstd.name == "demo_discretized.FCStd"
    assert result.warnings == ["demo warning"]
    opts = seen["options"]
    assert opts.export_step is True
    assert opts.export_fcstd is True
    assert opts.fcstd_object_type == "mesh"
    assert opts.export_voxel_grid is True
    assert opts.voxel_grid_mode == "all"
    assert opts.include_sheets is False
    assert _wait_until(lambda: not Path(result.temp_dir).exists())


def test_export_and_open_forwards_port_points(tmp_path, monkeypatch):
    project = Project.new_default("demo")
    project.mesh.drawing_unit = "mm"
    project.ports = [PortDef(name="in1", start=[1, 2, 1], stop=[1, 2, 3])]
    seen = {}

    def fake_export(options):
        fcstd = Path(options.output_dir) / "demo_discretized.FCStd"
        fcstd.write_text("fake", encoding="utf-8")
        return SimpleNamespace(
            fcstd_file=fcstd,
            step_file=None,
            objects=[SimpleNamespace(name="patch")],
            warnings=[],
            log="",
        )

    def fake_visible(fcstd, gui, **kwargs):
        seen["ports"] = kwargs.get("ports")
        return True, "ok"

    proc = _FakeProcess()
    monkeypatch.setattr(m2f, "export", fake_export)
    monkeypatch.setattr(m2f, "resolve_freecad_gui", lambda cmd, gui: Path("FreeCAD.exe"))
    monkeypatch.setattr(m2f, "_ensure_visible_document", fake_visible)
    monkeypatch.setattr(
        m2f, "_open_document", lambda target, cmd, gui: (proc, "opened")
    )

    result = m2f.export_and_open(
        project,
        [{"kind": "box"}],
        _FakeCSX(),
        options=m2f.Mesh2FreeCADOptions(keep_temp=False),
        name="demo",
    )

    assert result.ok
    assert seen["ports"] == [
        {"name": "in1", "type": "lumped", "start": [1.0, 2.0, 1.0],
         "stop": [1.0, 2.0, 3.0]}
    ]


def test_export_and_open_rejects_non_millimetre_projects():
    project = Project.new_default("demo")
    project.mesh.drawing_unit = "um"
    result = m2f.export_and_open(project, [{"kind": "box"}], _FakeCSX())
    assert not result.ok
    assert "millimetre" in result.message.lower()


def test_parameters_are_saved_in_the_project_file(tmp_path):
    project = Project.new_default("demo")
    project.mesh2freecad.export_step = True
    project.mesh2freecad.fcstd_object_type = "mesh"
    project.mesh2freecad.voxel_grid = True
    project.mesh2freecad.voxel_grid_mode = "all"
    project.mesh2freecad.include_sheets = False
    project.mesh2freecad.export_ports = False
    project.mesh2freecad.keep_temp = True

    path = project.save(tmp_path / "demo.fcsim.json")
    loaded = Project.load(path)

    assert loaded.mesh2freecad.export_step is True
    assert loaded.mesh2freecad.fcstd_object_type == "mesh"
    assert loaded.mesh2freecad.voxel_grid is True
    assert loaded.mesh2freecad.voxel_grid_mode == "all"
    assert loaded.mesh2freecad.include_sheets is False
    assert loaded.mesh2freecad.export_ports is False
    assert loaded.mesh2freecad.keep_temp is True


def test_port_points_use_start_and_stop():
    project = Project.new_default("demo")
    project.ports = [
        PortDef(name="in1", start=[1.0, 2.0, 3.0], stop=[1.0, 2.0, 4.0]),
        PortDef(name="bad", start=[1.0, 2.0, 3.0], stop=[1.0, 2.0, 3.0]),
    ]
    points = m2f._port_points(project)
    assert points == [
        {
            "name": "in1",
            "type": "lumped",
            "start": [1.0, 2.0, 3.0],
            "stop": [1.0, 2.0, 4.0],
        }
    ]


def test_old_projects_get_default_parameters(tmp_path):
    path = tmp_path / "old.fcsim.json"
    path.write_text('{"name": "old", "version": 1}', encoding="utf-8")
    loaded = Project.load(path)
    assert loaded.mesh2freecad.fcstd_object_type == "mesh"
    assert loaded.mesh2freecad.export_step is False
    assert loaded.mesh2freecad.voxel_grid is True
    assert loaded.mesh2freecad.voxel_grid_mode == "all"
    assert loaded.mesh2freecad.include_sheets is True
    assert loaded.mesh2freecad.export_ports is True
    assert loaded.mesh2freecad.keep_temp is False


def test_vendored_exporter_uses_cell_centre_rule(tmp_path):
    """The integrated exporter still separates objects that share a material."""
    from CSXCAD import ContinuousStructure

    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(0.001)
    lines = np.arange(0.0, 11.0, 1.0)
    for axis in range(3):
        csx.GetGrid().SetLines(axis, lines)
    copper = csx.AddMetal("copper")
    copper.AddBox(start=[1, 1, 1], stop=[3, 3, 3], priority=10)
    copper.AddBox(start=[5, 5, 1], stop=[7, 7, 3], priority=10)
    csx.Write2XML(str(tmp_path / "csx.xml"))

    primitives = [
        {
            "kind": "box",
            "name": "A",
            "material": "copper",
            "priority": 10,
            "start": [1, 1, 1],
            "stop": [3, 3, 3],
        },
        {
            "kind": "box",
            "name": "B",
            "material": "copper",
            "priority": 10,
            "start": [5, 5, 1],
            "stop": [7, 7, 3],
        },
    ]
    project = {
        "name": "test",
        "materials": [{"name": "copper", "kind": "pec"}],
        "primitives": primitives,
        "mesh": {"drawing_unit": "mm"},
        "sim": {"cell_constant_material": True},
        "results": {"csx_xml": str(tmp_path / "csx.xml")},
    }
    project_path = tmp_path / "test.fcsim.json"
    project_path.write_text(json.dumps(project), encoding="utf-8")

    result = export(ExportOptions(project_path=project_path, output_dir=tmp_path / "out"))

    assert {obj.name for obj in result.objects} == {"A", "B"}
    assert result.exactness.startswith("CellConstantMaterial=1")
    for obj in result.objects:
        assert obj.cells == 8
        assert obj.triangles == 48
        assert read_binary_stl_count(obj.files[0]) == 48
