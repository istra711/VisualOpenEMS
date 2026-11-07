"""Smoke/unit tests for the Gerber -> CSXCAD primitive importer (phase 1).

The test set is generated with gerbonara itself, so no binary fixtures are
needed.  File names follow KiCad's fabrication naming scheme, which lets
``LayerStack.autoguess`` map copper/outline/drill files.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _write_set(
    root: Path,
    *,
    top_hole: bool = False,
    pth_diameters: tuple[float, ...] = (0.4,),
    npth: bool = False,
    copper_layers: int = 2,
    mask_openings: bool = False,
    pth_slots: tuple[float, ...] = (),
    npth_slots: tuple[float, ...] = (),
) -> None:
    """Write a minimal KiCad-style 2- or 4-layer fabrication set."""
    from gerbonara import ExcellonFile, GerberFile, MM
    from gerbonara import apertures as ap
    from gerbonara.graphic_objects import Flash, Line, Region

    top = GerberFile()
    top.objects.append(Region(outline=[(0, 0), (10, 0), (10, 5), (0, 5)], unit=MM))
    if top_hole:
        # LPC (clear) rectangle -> copper polygon with a hole.
        top.objects.append(
            Region(
                outline=[(4, 2), (6, 2), (6, 3), (4, 3)],
                unit=MM,
                polarity_dark=False,
            )
        )
    top.save(root / "demo-F.Cu.gbr")

    bottom = GerberFile()
    bottom.objects.append(Region(outline=[(1, 1), (9, 1), (9, 4), (1, 4)], unit=MM))
    bottom.save(root / "demo-B.Cu.gbr")

    for index in range(1, max(0, copper_layers - 2) + 1):
        inner = GerberFile()
        inner.objects.append(
            Region(
                outline=[
                    (2.0 + index, 1.5),
                    (4.0 + index, 1.5),
                    (4.0 + index, 3.5),
                    (2.0 + index, 3.5),
                ],
                unit=MM,
            )
        )
        inner.save(root / f"demo-In{index}.Cu.gbr")

    outline = GerberFile()
    aperture = ap.CircleAperture(0.1, unit=MM)
    for p1, p2 in (
        ((0, 0), (10, 0)),
        ((10, 0), (10, 5)),
        ((10, 5), (0, 5)),
        ((0, 5), (0, 0)),
    ):
        outline.objects.append(Line(*p1, *p2, aperture=aperture, unit=MM))
    outline.save(root / "demo-Edge.Cuts.gbr")

    # Real fabrication sets have mask/silk layers too; empty files are enough
    # for gerbonara's layer autodetection (it wants >= 6 identified files).
    for side, name in (
        ("top", "demo-F.Mask.gbr"),
        ("bottom", "demo-B.Mask.gbr"),
    ):
        layer = GerberFile()
        if mask_openings:
            # Solder mask Gerbers are negative: dark = opening.
            offset = 0.5 if side == "top" else 5.0
            layer.objects.append(
                Region(
                    outline=[
                        (offset, 0.5),
                        (offset + 1.0, 0.5),
                        (offset + 1.0, 1.5),
                        (offset, 1.5),
                    ],
                    unit=MM,
                )
            )
        layer.save(root / name)
    for name in ("demo-F.SilkS.gbr", "demo-B.SilkS.gbr"):
        GerberFile().save(root / name)

    if pth_diameters or pth_slots:
        drill = ExcellonFile()
        for i, diameter in enumerate(pth_diameters):
            drill.objects.append(
                Flash(
                    1.0 + i,
                    2.0,
                    aperture=ap.ExcellonTool(diameter, unit=MM, plated=True),
                    unit=MM,
                )
            )
        for i, diameter in enumerate(pth_slots):
            drill.objects.append(
                Line(
                    1.0,
                    3.5 + i,
                    4.0,
                    3.5 + i,
                    aperture=ap.ExcellonTool(diameter, unit=MM, plated=True),
                    unit=MM,
                )
            )
        drill.save(root / "demo-PTH.drl")

    if npth or npth_slots:
        nonplated = ExcellonFile()
        if npth:
            nonplated.objects.append(
                Flash(
                    8.0,
                    4.0,
                    aperture=ap.ExcellonTool(1.0, unit=MM, plated=False),
                    unit=MM,
                )
            )
        for i, diameter in enumerate(npth_slots):
            nonplated.objects.append(
                Line(
                    6.0,
                    1.0 + i,
                    8.0,
                    1.0 + i,
                    aperture=ap.ExcellonTool(diameter, unit=MM, plated=False),
                    unit=MM,
                )
            )
        nonplated.save(root / "demo-NPTH.drl")


def _polygon_area(points) -> float:
    area = 0.0
    for i, (x1, y1) in enumerate(points):
        x2, y2 = points[(i + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _copper_primitives(result):
    return [p for p in result.primitives if p.get("source_layer", "").startswith("cu_")]


def test_import_two_copper_layers_substrate_and_via(tmp_path):
    from app.freecad_io.gerber_import import import_gerber_set

    _write_set(tmp_path)
    result = import_gerber_set(tmp_path)

    assert result.ok, result.warnings
    assert result.copper_layer_count == 2
    assert result.via_count == 1

    by_side = {layer["side"]: layer for layer in result.layers}
    assert set(by_side) == {"top", "bottom"}
    assert by_side["top"]["z_mm"] == 0.8
    assert by_side["bottom"]["z_mm"] == -0.8
    assert by_side["top"]["area_mm2"] == 50.0
    assert by_side["bottom"]["area_mm2"] == 24.0

    substrate = next(p for p in result.primitives if p["name"] == "substrate")
    assert substrate["kind"] == "box"
    assert substrate["material"] == "substrate"
    assert substrate["start"] == [-5.0, -2.5, -0.8]
    assert substrate["stop"] == [5.0, 2.5, 0.8]

    copper = _copper_primitives(result)
    assert copper and all(p["kind"] == "polygon" for p in copper)
    # Sheets by default (AddPolygon), so no extrusion length.
    assert all("length" not in p for p in copper)
    assert {p["source_layer"] for p in copper} == {"cu_top", "cu_bottom"}

    via = next(p for p in result.primitives if p["source_layer"] == "via")
    assert via["kind"] == "cylinder"
    assert via["material"] == "copper"
    assert via["start"] == [-4.0, -0.5, -0.8]
    assert via["stop"] == [-4.0, -0.5, 0.8]
    assert abs(via["radius"] - 0.225) < 1e-9


def test_copper_prisms_use_thickness_and_top_z(tmp_path):
    from app.freecad_io.gerber_import import GerberImportOptions, import_gerber_set

    _write_set(tmp_path)
    result = import_gerber_set(
        tmp_path,
        GerberImportOptions(copper_as_sheet=False, copper_thickness_mm=0.035),
    )

    assert result.ok, result.warnings
    copper = _copper_primitives(result)
    assert copper and all(p["kind"] == "polygon" for p in copper)
    assert all(p["length"] == 0.035 for p in copper)
    top = next(p for p in copper if p["source_layer"] == "cu_top")
    assert top["coord"] == 0.8
    assert top["stop"][2] == 0.8 + 0.035


def test_copper_holes_are_triangulated(tmp_path):
    from app.freecad_io.gerber_import import import_gerber_set

    _write_set(tmp_path, top_hole=True)
    result = import_gerber_set(tmp_path)
    assert result.ok, result.warnings

    top = [p for p in _copper_primitives(result) if p["source_layer"] == "cu_top"]
    assert len(top) > 1  # polygon with hole is split into triangles
    total = sum(_polygon_area(p["points"]) for p in top)
    assert abs(total - 48.0) < 0.2  # 10*5 minus 2*1 hole
    # The layer metadata reports the true area, not the triangle sum.
    top_layer = next(layer for layer in result.layers if layer["side"] == "top")
    assert abs(top_layer["area_mm2"] - 48.0) < 1e-6


def test_via_diameter_filter_reports_skipped_hits(tmp_path):
    from app.freecad_io.gerber_import import GerberImportOptions, import_gerber_set

    _write_set(tmp_path, pth_diameters=(0.4, 1.5))
    result = import_gerber_set(
        tmp_path,
        GerberImportOptions(via_max_diameter_mm=0.8),
    )

    assert result.ok, result.warnings
    assert result.via_count == 1
    assert any("skipped" in warning for warning in result.warnings)

    # Raising the threshold imports both plated holes.
    result_all = import_gerber_set(
        tmp_path,
        GerberImportOptions(via_max_diameter_mm=2.0),
    )
    assert result_all.via_count == 2


def test_npth_is_only_imported_when_requested(tmp_path):
    from app.freecad_io.gerber_import import GerberImportOptions, import_gerber_set

    _write_set(tmp_path, pth_diameters=(), npth=True)

    default = import_gerber_set(tmp_path)
    assert default.ok, default.warnings
    assert default.via_count == 0

    included = import_gerber_set(
        tmp_path,
        GerberImportOptions(include_npth=True, via_max_diameter_mm=2.0),
    )
    assert included.ok, included.warnings
    assert included.via_count == 1
    npth = next(p for p in included.primitives if p["source_layer"] == "via")
    assert npth["material"] == "vacuum"
    assert npth["priority"] > 100


def test_extra_csv_and_underscore_layer_names(tmp_path):
    """A fab folder may include pick-and-place CSV and KiCad B_Cu/F_Cu names.

    gerbonara's autoguess mistook those CSV files for copper and left
    ``basic-B_Cu.gbr`` as "bottom unknown"; the importer must filter them and
    fall back to the explicit layer map.
    """
    from app.freecad_io.gerber_import import import_gerber_set

    _write_set(tmp_path)
    for old, new in (
        ("demo-F.Cu.gbr", "demo-F_Cu.gbr"),
        ("demo-B.Cu.gbr", "demo-B_Cu.gbr"),
        ("demo-Edge.Cuts.gbr", "demo-Edge_Cuts.gbr"),
    ):
        (tmp_path / old).rename(tmp_path / new)
    (tmp_path / "demo-top-pos.csv").write_text(
        "Ref,X,Y\nR1,1,2\n", encoding="utf-8"
    )
    (tmp_path / "demo-bottom-pos.csv").write_text(
        "Ref,X,Y\nR2,3,4\n", encoding="utf-8"
    )

    result = import_gerber_set(tmp_path)
    assert result.ok, result.warnings
    assert result.copper_layer_count == 2
    assert {layer["side"] for layer in result.layers} == {"top", "bottom"}


def test_zip_source(tmp_path):
    from app.freecad_io.gerber_import import import_gerber_set

    source = tmp_path / "gerbers"
    source.mkdir()
    _write_set(source)
    # Pick-and-place data inside the ZIP must be ignored, not treated as copper.
    (source / "demo-top-pos.csv").write_text(
        "Ref,X,Y\nR1,1,2\n", encoding="utf-8"
    )

    archive = tmp_path / "gerbers.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in source.iterdir():
            zf.write(path, arcname=path.name)

    result = import_gerber_set(archive)
    assert result.ok, result.warnings
    assert result.copper_layer_count == 2
    assert result.via_count == 1


def test_bad_source_reports_warning_instead_of_raising(tmp_path):
    import warnings

    from app.freecad_io.gerber_import import import_gerber_set

    empty = tmp_path / "empty"
    empty.mkdir()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # gerbonara warns on ambiguous input
        result = import_gerber_set(empty)
    assert result.ok is False
    assert result.warnings


def test_inspect_lists_layers_and_drills(tmp_path):
    from app.freecad_io.gerber_import import inspect_gerber_set

    _write_set(tmp_path)
    inspection = inspect_gerber_set(tmp_path)

    assert inspection.ok, inspection.warnings
    assert [layer["side"] for layer in inspection.layers] == ["top", "bottom"]
    assert [layer["z_mm"] for layer in inspection.layers] == [0.8, -0.8]
    assert inspection.layers[0]["file"] == "demo-F.Cu.gbr"
    assert inspection.drills == [
        {"diameter_mm": 0.4, "count": 1, "plated": True}
    ]
    assert inspection.outline_bounds_mm is not None
    assert inspection.outline_bounds_mm == [0.0, 0.0, 10.0, 5.0]


def test_layer_z_override(tmp_path):
    from app.freecad_io.gerber_import import GerberImportOptions, import_gerber_set

    _write_set(tmp_path)
    result = import_gerber_set(
        tmp_path,
        GerberImportOptions(layer_z={"top": 1.2, "bottom": -0.4}),
    )
    assert result.ok, result.warnings
    by_side = {layer["side"]: layer for layer in result.layers}
    assert by_side["top"]["z_mm"] == 1.2
    assert by_side["bottom"]["z_mm"] == -0.4


def test_apply_import_and_reimport_preserve_material_override(tmp_path):
    from app.freecad_io.gerber_import import (
        apply_gerber_to_project,
        import_gerber_set,
    )
    from app.models.project import GerberSpec, Project

    _write_set(tmp_path)
    project = Project.new_default("pcb")
    spec = GerberSpec(
        board_thickness_mm=1.6,
        substrate_epsilon=4.2,
        substrate_tand=0.018,
    )

    result = import_gerber_set(tmp_path, spec.import_options())
    summary = apply_gerber_to_project(project, result, str(tmp_path), spec)

    assert summary == {
        "added": len(result.primitives),
        "replaced": 0,
        "kept": 0,
    }
    assert len(project.primitives) == len(result.primitives)
    assert {p["name"] for p in project.primitives} == {
        p["name"] for p in result.primitives
    }
    assert project.gerber is spec
    assert spec.source == str(tmp_path)
    assert project.geometry_files[-1].format == "gerber"
    substrate = project.material_by_name("substrate")
    assert substrate is not None
    assert substrate.epsilon == 4.2
    assert substrate.tand == 0.018

    # A manual material change on an imported copper primitive survives a
    # re-import of the same source, and no duplicate primitives appear.
    copper = next(
        p for p in project.primitives if p.get("source_layer") == "cu_top"
    )
    copper["material"] = "custom_copper"
    count_before = len(project.primitives)

    result2 = import_gerber_set(tmp_path, spec.import_options())
    summary2 = apply_gerber_to_project(project, result2, str(tmp_path), spec)
    assert summary2["replaced"] == len(result.primitives)
    assert len(project.primitives) == count_before
    copper2 = next(
        p for p in project.primitives if p.get("source_layer") == "cu_top"
    )
    assert copper2["material"] == "custom_copper"


def test_gerber_spec_project_roundtrip(tmp_path):
    from app.models.project import GerberSpec, Project

    project = Project.new_default("gerber_spec")
    project.gerber = GerberSpec(
        board_thickness_mm=2.0,
        copper_as_sheet=False,
        copper_thickness_mm=0.07,
        include_npth=True,
        substrate_epsilon=3.9,
        substrate_tand=0.012,
        include_mask=True,
        solder_mask_thickness_mm=0.025,
        solder_mask_epsilon=3.2,
        solder_mask_tand=0.03,
        layer_z={"top": 1.0, "bottom": -1.0},
        via_spans={"0.4000": ["top", "inner1"]},
        suggest_ports=False,
    )
    path = tmp_path / "gerber_spec.fcsim.json"
    project.save(path)

    loaded = Project.load(path)
    assert loaded.gerber.board_thickness_mm == 2.0
    assert loaded.gerber.copper_as_sheet is False
    assert loaded.gerber.copper_thickness_mm == 0.07
    assert loaded.gerber.include_npth is True
    assert loaded.gerber.substrate_epsilon == 3.9
    assert loaded.gerber.substrate_tand == 0.012
    assert loaded.gerber.include_mask is True
    assert loaded.gerber.solder_mask_thickness_mm == 0.025
    assert loaded.gerber.solder_mask_epsilon == 3.2
    assert loaded.gerber.solder_mask_tand == 0.03
    assert loaded.gerber.layer_z == {"top": 1.0, "bottom": -1.0}
    assert loaded.gerber.via_spans == {"0.4000": ["top", "inner1"]}
    assert loaded.gerber.suggest_ports is False
    # Old project files without the key get defaults.
    assert Project.from_dict({"name": "old"}).gerber.board_thickness_mm == 1.6


def test_via_spans_by_diameter(tmp_path):
    from app.freecad_io.gerber_import import GerberImportOptions, import_gerber_set

    _write_set(tmp_path, copper_layers=4)
    result = import_gerber_set(
        tmp_path,
        GerberImportOptions(via_spans={"0.4000": ["top", "inner_1"]}),
    )
    assert result.ok, result.warnings
    assert result.copper_layer_count == 4

    via = next(p for p in result.primitives if p["source_layer"] == "via")
    assert via["span"] == ["top", "inner_1"]
    assert abs(via["start"][2] - (1.6 / 2.0 - 1.6 / 3.0)) < 1e-5
    assert abs(via["stop"][2] - 0.8) < 1e-9

    through = import_gerber_set(tmp_path)
    via = next(p for p in through.primitives if p["source_layer"] == "via")
    assert via["start"][2] == -0.8
    assert via["stop"][2] == 0.8


def test_excellon_slots_are_imported(tmp_path):
    from app.freecad_io.gerber_import import import_gerber_set, inspect_gerber_set

    _write_set(tmp_path, pth_diameters=(0.4,), pth_slots=(0.3,))
    inspection = inspect_gerber_set(tmp_path)
    assert inspection.slots == [
        {"diameter_mm": 0.3, "count": 1, "plated": True}
    ]

    result = import_gerber_set(tmp_path)
    assert result.ok, result.warnings
    assert result.slot_count >= 1

    slot = next(p for p in result.primitives if p["source_layer"] == "slot")
    assert slot["kind"] == "polygon"
    assert slot["material"] == "copper"
    assert slot["slot_mm"] == 0.3
    assert slot["coord"] == -0.8
    assert slot["length"] == 1.6
    assert _polygon_area(slot["points"]) > 0.8


def test_solder_mask_layers(tmp_path):
    from app.freecad_io.gerber_import import GerberImportOptions, import_gerber_set

    _write_set(tmp_path, mask_openings=True)
    result = import_gerber_set(
        tmp_path,
        GerberImportOptions(include_mask=True),
    )
    assert result.ok, result.warnings
    assert result.mask_layer_count == 2

    top_parts = [
        p for p in result.primitives if p["source_layer"] == "mask_top"
    ]
    bottom_parts = [
        p for p in result.primitives if p["source_layer"] == "mask_bottom"
    ]
    top = top_parts[0]
    bottom = bottom_parts[0]
    assert top["material"] == "solder_mask"
    assert top["coord"] == 0.8
    assert abs(top["length"] - 0.02) < 1e-12
    assert abs(sum(_polygon_area(p["points"]) for p in top_parts) - 49.0) < 0.05
    assert abs(bottom["coord"] + 0.82) < 1e-9
    assert abs(bottom["length"] - 0.02) < 1e-12
    assert abs(sum(_polygon_area(p["points"]) for p in bottom_parts) - 49.0) < 0.05


def test_apply_creates_solder_mask_material(tmp_path):
    from app.freecad_io.gerber_import import (
        apply_gerber_to_project,
        import_gerber_set,
    )
    from app.models.project import GerberSpec, Project

    _write_set(tmp_path, mask_openings=True)
    spec = GerberSpec(
        include_mask=True,
        solder_mask_epsilon=3.1,
        solder_mask_tand=0.03,
    )
    result = import_gerber_set(tmp_path, spec.import_options())
    project = Project.new_default("mask")
    apply_gerber_to_project(project, result, str(tmp_path), spec)

    material = project.material_by_name("solder_mask")
    assert material is not None
    assert material.kind == "dielectric"
    assert material.epsilon == 3.1
    assert material.tand == 0.03


def test_npth_slot_is_vacuum(tmp_path):
    from app.freecad_io.gerber_import import GerberImportOptions, import_gerber_set

    _write_set(tmp_path, pth_diameters=(), npth_slots=(0.5,))
    result = import_gerber_set(
        tmp_path,
        GerberImportOptions(include_npth=True),
    )
    assert result.ok, result.warnings
    assert result.slot_count >= 1
    slot = next(p for p in result.primitives if p["source_layer"] == "slot")
    assert slot["material"] == "vacuum"
    assert slot["priority"] > 100



