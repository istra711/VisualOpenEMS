"""Tests for the native KiCad .kicad_pcb importer (phase 4)."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _write_kicad(path: Path, *, stackup: bool = True, four_layers: bool = True) -> None:
    if four_layers:
        layers = """
    (0 "F.Cu" signal)
    (1 "In1.Cu" signal)
    (2 "In2.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
        """
        stack = """
    (setup
      (stackup
        (layer "F.Cu" (type "copper") (thickness 0.035))
        (layer "dielectric 1" (type "core") (thickness 0.5) (epsilon_r 4.4) (loss_tangent 0.02))
        (layer "In1.Cu" (type "copper") (thickness 0.035))
        (layer "dielectric 2" (type "prepreg") (thickness 0.5) (epsilon_r 4.2) (loss_tangent 0.018))
        (layer "In2.Cu" (type "copper") (thickness 0.035))
        (layer "dielectric 3" (type "core") (thickness 0.5) (epsilon_r 4.1) (loss_tangent 0.016))
        (layer "B.Cu" (type "copper") (thickness 0.035))
      )
    )"""
    else:
        layers = """
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
        """
        stack = """
    (setup
      (stackup
        (layer "F.Cu" (type "copper") (thickness 0.035))
        (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.5) (loss_tangent 0.02))
        (layer "B.Cu" (type "copper") (thickness 0.035))
      )
    )"""
    if not stackup:
        stack = ""

    text = f"""(kicad_pcb
  (version 20240108)
  (generator "test")
  (general (thickness 1.6))
  (layers {layers})
  {stack}
  (net 0 "")
  (net 1 "GND")
  (footprint "Test:FP" (layer "F.Cu") (at 10 10)
    (pad "1" smd rect (at 0 0) (size 2 1) (layers "F.Cu"))
    (pad "2" thru_hole circle (at 5 0) (size 2 2) (drill 1) (layers "*.Cu" "*.Mask"))
  )
  (segment (start 2 2) (end 8 2) (width 0.3) (layer "F.Cu") (net 1))
  (arc (start 2 6) (mid 5 8) (end 8 6) (width 0.3) (layer "F.Cu") (net 1))
  (via (at 5 2) (size 1.0) (drill 0.5) (layers "F.Cu" "In1.Cu") (net 1))
  (zone (net 1) (net_name "GND") (layer "B.Cu")
    (polygon (pts (xy 0 0) (xy 20 0) (xy 20 10) (xy 0 10)))
    (filled_polygon (layer "B.Cu")
      (pts (xy 1 1) (xy 19 1) (xy 19 9) (xy 1 9))))
  (gr_line (start 0 0) (end 20 0) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 20 0) (end 20 10) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 20 10) (end 0 10) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 0 10) (end 0 0) (layer "Edge.Cuts") (width 0.1))
)
"""
    path.write_text(text, encoding="utf-8")


def test_inspect_kicad(tmp_path):
    from app.freecad_io.kicad_import import inspect_kicad_pcb

    path = tmp_path / "demo.kicad_pcb"
    _write_kicad(path)
    inspection = inspect_kicad_pcb(path)

    assert inspection.ok, inspection.warnings
    assert inspection.copper_layers == ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    assert inspection.board_thickness_mm == 1.6
    assert inspection.via_count == 1
    assert inspection.pad_count == 2
    assert inspection.track_count == 2
    assert inspection.zone_count == 1
    assert inspection.outline_bounds_mm == [0.0, 0.0, 20.0, 10.0]
    assert [layer.kind for layer in inspection.stackup].count("dielectric") == 3
    assert inspection.stackup[1].epsilon == 4.4


def test_import_kicad_layers_vias_pads_and_zones(tmp_path):
    from app.freecad_io.kicad_import import KiCadImportOptions, import_kicad_pcb

    path = tmp_path / "demo.kicad_pcb"
    _write_kicad(path)
    result = import_kicad_pcb(path, KiCadImportOptions(flip_y=False))

    assert result.ok, result.warnings
    assert len(result.layers) == 4
    assert result.via_count == 1
    assert result.pad_count == 2
    assert result.pad_barrel_count == 1
    assert result.track_count == 2
    assert result.zone_count == 1

    via = next(p for p in result.primitives if p["source_layer"] == "via")
    assert via["span"] == ["F.Cu", "In1.Cu"]
    assert via["start"][2] == 1.6 / 2.0 - 1.6 / 3.0  # In1.Cu
    assert via["stop"][2] == 0.8                      # F.Cu
    assert abs(via["radius"] - 0.275) < 1e-9

    barrel = next(
        p for p in result.primitives if p["source_layer"] == "pad_barrel"
    )
    assert barrel["start"][2] == -0.8
    assert barrel["stop"][2] == 0.8

    # Three dielectric materials from the KiCad stackup.
    names = {m["name"] for m in result.materials}
    assert {"copper", "kicad_dielectric_1", "kicad_dielectric_2",
            "kicad_dielectric_3"} <= names
    assert any(
        m["name"] == "kicad_dielectric_2" and m["epsilon"] == 4.2
        for m in result.materials
    )


def test_import_without_stackup_uses_fallback(tmp_path):
    from app.freecad_io.kicad_import import KiCadImportOptions, import_kicad_pcb

    path = tmp_path / "demo.kicad_pcb"
    _write_kicad(path, stackup=False, four_layers=False)
    result = import_kicad_pcb(
        path,
        KiCadImportOptions(
            use_stackup=False,
            board_thickness_mm=2.0,
            substrate_epsilon=4.0,
            substrate_tand=0.01,
            flip_y=False,
        ),
    )
    assert result.ok, result.warnings
    assert len(result.layers) == 2
    assert {layer["name"]: layer["z_mm"] for layer in result.layers} == {
        "F.Cu": 1.0,
        "B.Cu": -1.0,
    }
    substrate = next(
        p for p in result.primitives if p.get("source_layer") == "kicad_substrate"
    )
    assert substrate["kind"] == "polygon"
    assert substrate["coord"] == -1.0
    assert substrate["length"] == 2.0
    material = next(
        m for m in result.materials if m["name"] == "kicad_substrate"
    )
    assert material["epsilon"] == 4.0
    assert material["tand"] == 0.01


def test_flip_y(tmp_path):
    from app.freecad_io.kicad_import import KiCadImportOptions, import_kicad_pcb

    path = tmp_path / "demo.kicad_pcb"
    _write_kicad(path)
    normal = import_kicad_pcb(path, KiCadImportOptions(flip_y=False))
    flipped = import_kicad_pcb(path, KiCadImportOptions(flip_y=True))

    normal_via = next(p for p in normal.primitives if p["source_layer"] == "via")
    flipped_via = next(p for p in flipped.primitives if p["source_layer"] == "via")
    # Board centre y = 5; via at y = 2 -> -3 normal, +3 flipped.
    assert normal_via["start"][1] == -3.0
    assert flipped_via["start"][1] == 3.0


def test_apply_kicad_to_project_and_reimport(tmp_path):
    from app.freecad_io.kicad_import import (
        apply_kicad_to_project,
        import_kicad_pcb,
    )
    from app.models.project import KiCadSpec, Project

    path = tmp_path / "demo.kicad_pcb"
    _write_kicad(path)
    spec = KiCadSpec(flip_y=False)
    result = import_kicad_pcb(path, spec.import_options())
    project = Project.new_default("kicad")
    summary = apply_kicad_to_project(project, result, str(path), spec)

    assert summary == {
        "added": len(result.primitives),
        "replaced": 0,
        "kept": 0,
    }
    assert project.kicad is spec
    assert spec.source == str(path)
    assert project.geometry_files[-1].format == "kicad"
    material = project.material_by_name("kicad_dielectric_3")
    assert material is not None and material.epsilon == 4.1

    copper = next(
        p
        for p in project.primitives
        if str(p.get("source_layer", "")).startswith("cu_")
    )
    copper["material"] = "custom_copper"
    count_before = len(project.primitives)
    result2 = import_kicad_pcb(path, spec.import_options())
    summary2 = apply_kicad_to_project(project, result2, str(path), spec)
    assert summary2["replaced"] == len(result.primitives)
    assert len(project.primitives) == count_before
    copper2 = next(
        p
        for p in project.primitives
        if str(p.get("source_layer", "")).startswith("cu_")
    )
    assert copper2["material"] == "custom_copper"


def test_kicad_mesh_mode_creates_stl_per_layer(tmp_path):
    from app.freecad_io.kicad_import import KiCadImportOptions, import_kicad_pcb

    path = tmp_path / "demo.kicad_pcb"
    _write_kicad(path)
    mesh_dir = tmp_path / "meshes"
    result = import_kicad_pcb(
        path,
        KiCadImportOptions(copper_mode="mesh", flip_y=False),
        mesh_dir=mesh_dir,
    )
    assert result.ok, result.warnings
    copper = [
        p for p in result.primitives
        if str(p.get("source_layer", "")).startswith("cu_")
    ]
    assert len(copper) == 4
    assert all(p["kind"] == "stl" for p in copper)
    assert all(Path(p["path"]).exists() for p in copper)
    assert all(Path(p["stl_path"]).exists() for p in copper)
    assert all(layer["primitive_count"] == 1 for layer in result.layers)
    # The generated triangle soup is non-empty.
    import numpy as np

    tris = np.load(copper[0]["path"])
    assert tris.ndim == 3 and tris.shape[1:] == (3, 3)
    assert len(tris) > 0


def test_kicad_spec_roundtrip(tmp_path):
    from app.models.project import KiCadSpec, Project

    project = Project.new_default("kicad_spec")
    project.kicad = KiCadSpec(
        use_stackup=False,
        board_thickness_mm=2.4,
        copper_as_sheet=False,
        copper_thickness_mm=0.07,
        substrate_epsilon=3.8,
        substrate_tand=0.011,
        include_npth=True,
        include_zones=False,
        flip_y=False,
        suggest_ports=False,
        simplify_mm=0.02,
    )
    path = tmp_path / "kicad_spec.fcsim.json"
    project.save(path)
    loaded = Project.load(path)

    assert loaded.kicad.use_stackup is False
    assert loaded.kicad.board_thickness_mm == 2.4
    assert loaded.kicad.copper_as_sheet is False
    assert loaded.kicad.copper_thickness_mm == 0.07
    assert loaded.kicad.substrate_epsilon == 3.8
    assert loaded.kicad.substrate_tand == 0.011
    assert loaded.kicad.include_npth is True
    assert loaded.kicad.include_zones is False
    assert loaded.kicad.flip_y is False
    assert loaded.kicad.suggest_ports is False
    assert loaded.kicad.simplify_mm == 0.02
    assert Project.from_dict({"name": "old"}).kicad.use_stackup is True


def test_invalid_kicad_file(tmp_path):
    from app.freecad_io.kicad_import import import_kicad_pcb, inspect_kicad_pcb

    path = tmp_path / "broken.kicad_pcb"
    path.write_text("(not_a_kicad_board", encoding="utf-8")
    inspection = inspect_kicad_pcb(path)
    result = import_kicad_pcb(path)
    assert inspection.ok is False
    assert inspection.warnings
    assert result.ok is False
    assert result.warnings


def test_real_kicad_demo_if_available():
    """Parser regression against a real KiCad 8 demo (skipped without KiCad)."""
    import pytest

    from app.freecad_io.kicad_import import (
        KiCadImportOptions,
        import_kicad_pcb,
        inspect_kicad_pcb,
    )

    path = Path(
        r"C:\Program Files\KiCad\8.0\share\kicad\demos"
        r"\custom_pads_test\custom_pads_test.kicad_pcb"
    )
    if not path.exists():
        pytest.skip("KiCad 8 demo board not installed")

    inspection = inspect_kicad_pcb(path)
    assert inspection.ok, inspection.warnings
    assert inspection.copper_layers == ["F.Cu", "B.Cu"]
    assert inspection.pad_count == 11

    result = import_kicad_pcb(
        path,
        KiCadImportOptions(include_zones=False, include_pad_barrels=False),
    )
    assert result.ok, result.warnings
    assert len(result.layers) == 2
    assert result.pad_count == 11

