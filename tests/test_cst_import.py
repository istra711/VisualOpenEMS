"""Tests for the CST Studio export-package importer (Variant A).

The fixture is generated locally: a tiny ``manifest.json`` plus two binary STL
boxes.  CST exports STL coordinates in metres, so the geometry-unit conversion
is exercised explicitly.
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.csxcad_builder.triangle_mesh import write_binary_stl
from app.freecad_io.cst_import import (
    CstImportOptions,
    _open_msl_port_boundaries,
    apply_cst_to_project,
    import_cst_package,
    inspect_cst_package,
)
from app.models.project import CstSpec, PortDef, Project


def _box_triangles(
    lo: tuple[float, float, float], hi: tuple[float, float, float]
) -> np.ndarray:
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    p = np.array(
        [
            [x0, y0, z0],
            [x1, y0, z0],
            [x1, y1, z0],
            [x0, y1, z0],
            [x0, y0, z1],
            [x1, y0, z1],
            [x1, y1, z1],
            [x0, y1, z1],
        ],
        dtype=float,
    )
    faces = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]
    return np.asarray([[p[a], p[b], p[c]] for a, b, c in faces], dtype=float)


def _write_package(root: Path, *, nested: bool = False) -> Path:
    package = root / "pkg"
    stl_dir = package / "stl"
    stl_dir.mkdir(parents=True)
    write_binary_stl(
        stl_dir / "PEC__trace.stl",
        _box_triangles((0.002, 0.004, 0.001), (0.018, 0.006, 0.001035)),
    )
    write_binary_stl(
        stl_dir / "FR4__substrate.stl",
        _box_triangles((0.0, 0.0, 0.0), (0.020, 0.010, 0.001)),
    )
    manifest = {
        "format": "cst-export-v1",
        "project": "demo",
        "units": {"geometry": "m", "frequency": "GHz"},
        "frequency": {"start": 1.0, "stop": 6.0},
        "boundaries": {
            "xmin": "electric",
            "xmax": "electric",
            "ymin": "magnetic",
            "ymax": "magnetic",
            "zmin": "open",
            "zmax": "pml",
            "pml_size": 10,
        },
        "materials": [
            {"name": "PEC", "kind": "pec"},
            {
                "name": "FR4",
                "kind": "dielectric",
                "epsilon": 4.4,
                "tand": 0.02,
            },
        ],
        "objects": [
            {
                "name": "PEC/trace",
                "component": "PEC",
                "material": "pec",
                "file": "stl/PEC__trace.stl",
                "kind": "solid",
            },
            {
                "name": "FR4/substrate",
                "component": "FR4",
                "material": "FR4",
                "file": "stl/FR4__substrate.stl",
                "kind": "solid",
            },
        ],
        "ports": [
            {
                "name": "p1",
                "type": "lumped",
                "start": [0.010, 0.004, 0.001],
                "stop": [0.010, 0.006, 0.001],
                "direction": "y",
                "resistance": 50,
                "excite": True,
            }
        ],
    }
    (package / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if nested:
        return package
    return package


def test_inspect_and_import_folder(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    inspection = inspect_cst_package(package)

    assert inspection.ok
    assert inspection.project_name == "demo"
    assert inspection.geometry_unit == "m"
    assert inspection.object_count == 2
    assert inspection.port_count == 1
    assert inspection.frequency is not None
    assert inspection.frequency.start_hz == 1e9
    assert inspection.frequency.stop_hz == 6e9
    assert inspection.boundaries["xmin"] == "PEC"
    assert inspection.boundaries["ymin"] == "PMC"
    assert inspection.boundaries["zmin"] == "MUR"
    assert inspection.boundaries["zmax"] == "PML"
    assert inspection.pml_size == 10

    result = import_cst_package(
        inspection,
        CstImportOptions(target_unit="mm"),
        out_dir=tmp_path / "meshes",
    )
    assert result.ok
    assert result.object_count == 2
    assert len(result.ports) == 1
    assert result.ports[0].start == [10.0, 4.0, 1.0]
    assert result.ports[0].stop == [10.0, 6.0, 1.0]
    by_name = {p["name"]: p for p in result.primitives}
    assert by_name["PEC/trace"]["material"] == "pec"
    assert by_name["PEC/trace"]["start"] == pytest.approx([2.0, 4.0, 1.0])
    assert by_name["PEC/trace"]["stop"] == pytest.approx([18.0, 6.0, 1.035])
    assert Path(by_name["PEC/trace"]["path"]).is_file()
    assert Path(by_name["PEC/trace"]["stl_path"]).is_file()

    project = Project.new_default("cst")
    spec = CstSpec()
    summary = apply_cst_to_project(project, result, package.as_posix(), spec)
    assert summary["added"] == 2
    assert len(project.primitives or []) == 2
    applied = {p["name"]: p for p in project.primitives or []}
    assert applied["PEC/trace"]["material"] == "PEC"  # keep project spelling
    assert project.mesh.start_frequency == 1e9
    assert project.mesh.stop_frequency == 6e9
    assert project.boundary.xmin == "PEC"
    assert project.boundary.ymin == "PMC"
    assert project.boundary.zmin == "MUR"
    assert project.boundary.zmax == "PML"
    assert project.boundary.pml_size == 10
    assert len(project.ports) == 1
    assert project.ports[0].name == "p1"
    assert project.cst.port_names == ["p1"]
    assert any(m.name == "FR4" and m.epsilon == 4.4 for m in project.materials)
    gf = next(
        g
        for g in project.geometry_files
        if Path(g.path).name == package.name and g.format == "cst"
    )
    assert gf.object_names == ["PEC/trace", "FR4/substrate"]

    # Re-import from the same source replaces geometry and the package's ports.
    summary2 = apply_cst_to_project(project, result, package, spec)
    assert summary2["replaced"] == 2
    assert summary2["ports_replaced"] == 1
    assert summary2["ports_added"] == 1
    assert len(project.ports) == 1
    assert len(project.primitives or []) == 2

    restored = Project.from_dict(project.to_dict())
    assert Path(restored.cst.source).name == package.name
    assert restored.cst.port_names == ["p1"]
    assert restored.geometry_files[0].format == "cst"


def test_zip_package_and_um_target(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    archive = tmp_path / "demo_cst.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in package.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(package))

    inspection = inspect_cst_package(archive, work_dir=tmp_path / "work")
    assert inspection.ok
    assert inspection.is_zip
    assert Path(inspection.root).is_dir()
    assert (Path(inspection.root) / "manifest.json").is_file()
    assert inspection.object_count == 2

    result = import_cst_package(
        inspection,
        CstSpec().import_options(target_unit="um"),
        out_dir=tmp_path / "meshes_um",
    )
    assert result.ok
    prim = next(p for p in result.primitives if p["name"] == "PEC/trace")
    assert prim["start"] == pytest.approx([2000.0, 4000.0, 1000.0])
    assert prim["stop"] == pytest.approx([18000.0, 6000.0, 1035.0])
    port = result.ports[0]
    assert port.start == [10000.0, 4000.0, 1000.0]
    assert port.wg_a == 0.0


def test_planar_cst_waveguide_port_becomes_msl(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    write_binary_stl(
        package / "stl" / "PEC__feed.stl",
        _box_triangles((0.002, 0.0, 0.001), (0.018, 0.010, 0.001035)),
    )
    manifest_path = package / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["objects"].append(
        {
            "name": "PEC/feed",
            "component": "PEC",
            "material": "PEC",
            "file": "stl/PEC__feed.stl",
            "kind": "solid",
        }
    )
    data["ports"] = [
        {
            "name": "feedport",
            "type": "waveguide",
            "start": [0.0, 0.0, 0.0],
            "stop": [0.020, 0.0, 0.005],
            "direction": "y",
        }
    ]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    inspection = inspect_cst_package(package)
    result = import_cst_package(
        inspection,
        CstImportOptions(target_unit="mm"),
        out_dir=tmp_path / "meshes_msl",
    )
    assert result.ok
    assert len(result.ports) == 1
    port = result.ports[0]
    assert port.type == "msl"
    assert port.prop_direction == "y"
    assert port.exc_direction == "z"
    assert port.start == pytest.approx([2.0, 0.0, 1.035])
    assert port.stop == pytest.approx([18.0, 0.0, 0.0])
    assert port.reverse is False
    assert any("MSL port" in warning for warning in result.warnings)

    # The high-side counterpart must keep its metal strip on the signal face
    # after script_gen swaps start/stop for the reversed propagation sense.
    data["ports"][0]["start"][1] = 0.010
    data["ports"][0]["stop"][1] = 0.010
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    inspection = inspect_cst_package(package)
    result = import_cst_package(
        inspection,
        CstImportOptions(target_unit="mm"),
        out_dir=tmp_path / "meshes_msl_high",
    )
    high = result.ports[0]
    assert high.type == "msl"
    assert high.reverse is True
    assert high.start[2] == pytest.approx(0.0)      # after reverse -> signal
    assert high.stop[2] == pytest.approx(1.035)


def test_calculation_box_becomes_per_side_air_margin(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    manifest_path = package / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["domain"] = {
        "calculation_box": {
            "min": [-0.005, -0.003, -0.001],
            "max": [0.025, 0.013, 0.006],
        },
        "structure_box": {
            "min": [0.0, 0.0, 0.0],
            "max": [0.020, 0.010, 0.001035],
        },
    }
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    inspection = inspect_cst_package(package)
    assert inspection.calculation_box_min == pytest.approx(
        [-5.0, -3.0, -1.0]
    )
    assert inspection.calculation_box_max == pytest.approx([25.0, 13.0, 6.0])

    result = import_cst_package(
        inspection,
        CstImportOptions(target_unit="mm"),
        out_dir=tmp_path / "meshes_domain",
    )
    assert result.air_margin == pytest.approx(
        [5.0, 5.0, 3.0, 3.0, 1.0, 4.965], abs=1e-3
    )

    project = Project.new_default("domain")
    apply_cst_to_project(project, result, package, CstSpec())
    assert project.boundary.air_margin == pytest.approx(
        [5.0, 5.0, 3.0, 3.0, 1.0, 4.965], abs=1e-3
    )
    assert project.boundary.zmin == "MUR"


def test_waveguide_port_and_cross_section_inference(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    manifest_path = package / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["ports"].append(
        {
            "name": "wg1",
            "type": "waveguide",
            "start": [0.0, 0.0, 0.0],
            "stop": [0.0, 0.02286, 0.01016],
            "wg_mode": "TE10",
        }
    )
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    inspection = inspect_cst_package(package)
    assert inspection.port_count == 2
    wg = next(p for p in inspection.ports if p.name == "wg1")
    assert wg.type == "rect_waveguide"
    assert wg.direction == "x"
    assert wg.wg_a == pytest.approx(22.86)
    assert wg.wg_b == pytest.approx(10.16)

    result = import_cst_package(
        inspection,
        CstImportOptions(target_unit="mm"),
        out_dir=tmp_path / "meshes_wg",
    )
    assert result.ok
    wg_def = next(p for p in result.ports if p.name == "wg1")
    assert wg_def.type == "rect_waveguide"
    assert wg_def.direction == "x"
    assert wg_def.wg_a == pytest.approx(22.86)
    assert wg_def.wg_b == pytest.approx(10.16)


def test_warnings_for_unsupported_cst_features(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    manifest_path = package / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["materials"].append(
        {"name": "Dispersive", "kind": "dispersive", "epsilon": 2.0}
    )
    data["boundaries"]["xmin"] = "periodic"
    data["ports"].append(
        {
            "name": "floquet1",
            "type": "floquet",
            "start": [0, 0, 0],
            "stop": [0, 0, 0],
        }
    )
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    inspection = inspect_cst_package(package)
    assert inspection.ok
    assert inspection.object_count == 2
    assert inspection.port_count == 1
    joined = "\n".join(inspection.warnings)
    assert "dispersive" in joined.lower()
    assert "periodic" in joined.lower()
    assert "floquet" in joined.lower()


def test_msl_port_on_pec_boundary_opens_that_face() -> None:
    project = Project()
    project.primitives = [
        {
            "kind": "box",
            "name": "board",
            "start": [0.0, 0.0, 0.0],
            "stop": [10.0, 10.0, 1.0],
        }
    ]
    project.boundary.air_margin = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    project.boundary.ymin = "PEC"
    project.boundary.ymax = "PEC"
    project.ports = [
        PortDef(
            name="port_2",
            type="msl",
            start=[1.0, 10.0, 0.0],
            stop=[2.0, 10.0, 1.0],
            prop_direction="y",
            exc_direction="z",
        )
    ]

    changed = _open_msl_port_boundaries(project)

    assert changed == {"ymax": 1}
    assert project.boundary.ymax == "MUR"
    assert project.boundary.ymin == "PEC"
    assert project.boundary.air_margin[3] == pytest.approx(0.0)

    # With an air margin the port is no longer on the FDTD boundary.
    project.boundary.air_margin = [0.0, 0.0, 0.0, 2.0, 0.0, 0.0]
    project.boundary.ymax = "PEC"
    assert _open_msl_port_boundaries(project) == {}
    assert project.boundary.ymax == "PEC"


def test_missing_manifest_is_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    inspection = inspect_cst_package(folder)
    assert not inspection.ok
    assert any("manifest" in warning.lower() for warning in inspection.warnings)
