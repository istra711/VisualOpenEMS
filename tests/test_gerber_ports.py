"""Tests for Gerber/Excellon port suggestions (no Qt)."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _write_ports_set(path: Path, *, attributes: bool = True) -> None:
    from gerbonara import ExcellonFile, GerberFile, MM
    from gerbonara import apertures as ap
    from gerbonara.graphic_objects import Line

    if attributes:
        attrs_in = """%TO.N,RF_IN*%
%TO.C,J1*%
%TO.P,1*%
"""
        attrs_out = """%TO.N,RF_OUT*%
%TO.C,J2*%
%TO.P,1*%
"""
    else:
        attrs_in = attrs_out = ""

    # Raw Gerber because gerbonara's writer does not emit object attributes.
    top = f"""%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.500000*%
{attrs_in}D10*
X5000000Y5000000D03*
{attrs_in}X5000000Y5000000D02*
X10000000Y5000000D01*
{attrs_out}D10*
X15000000Y5000000D03*
{attrs_out}X10000000Y5000000D02*
X15000000Y5000000D01*
M02*
"""
    (path / "demo-F.Cu.gbr").write_text(top, encoding="utf-8")

    bottom = GerberFile()
    bottom.save(path / "demo-B.Cu.gbr")

    outline = GerberFile()
    aperture = ap.CircleAperture(0.1, unit=MM)
    for p1, p2 in (
        ((0, 0), (20, 0)),
        ((20, 0), (20, 10)),
        ((20, 10), (0, 10)),
        ((0, 10), (0, 0)),
    ):
        outline.objects.append(Line(*p1, *p2, aperture=aperture, unit=MM))
    outline.save(path / "demo-Edge.Cuts.gbr")

    for name in (
        "demo-F.Mask.gbr",
        "demo-B.Mask.gbr",
        "demo-F.SilkS.gbr",
        "demo-B.SilkS.gbr",
    ):
        GerberFile().save(path / name)

    ExcellonFile().save(path / "demo-PTH.drl")


def test_load_gerber_port_model_and_suggest(tmp_path):
    from app.freecad_io.gerber_ports import (
        load_gerber_port_model,
        suggest_gerber_ports,
    )

    _write_ports_set(tmp_path)
    model = load_gerber_port_model(tmp_path)
    assert model.copper_layers == ["top", "bottom"]
    assert model.z_by_layer["top"] == 0.8
    assert model.z_by_layer["bottom"] == -0.8
    assert len(model.pads) == 2
    assert len(model.tracks) == 2
    assert {pad.net for pad in model.pads} == {"RF_IN", "RF_OUT"}
    assert {track.net for track in model.tracks} == {"RF_IN", "RF_OUT"}

    candidates = suggest_gerber_ports(tmp_path)
    assert len(candidates) == 2
    by_name = {candidate.name: candidate for candidate in candidates}
    assert set(by_name) == {"in1", "out1"}
    assert by_name["in1"].net == "RF_IN"
    assert by_name["out1"].net == "RF_OUT"
    assert by_name["in1"].type == "lumped"
    assert by_name["in1"].excite is True
    assert by_name["out1"].excite is False

    in1 = by_name["in1"]
    # Board centre is (10, 5): RF_IN pad at x=5 -> -5; y=5 -> 0.
    assert in1.start == [-5.0, -0.25, -0.8]
    assert in1.stop == [-5.0, 0.25, 0.8]
    assert in1.lumped_width == "y"


def test_gerber_without_net_attributes_has_no_net_candidates(tmp_path):
    from app.freecad_io.gerber_ports import (
        load_gerber_port_model,
        suggest_gerber_ports,
    )

    _write_ports_set(tmp_path, attributes=False)
    model = load_gerber_port_model(tmp_path)
    assert len(model.pads) == 2
    assert all(not pad.net for pad in model.pads)
    # Pads are not near the edge, so the geometric fallback finds nothing.
    assert suggest_gerber_ports(tmp_path) == []


def test_apply_gerber_port_candidates(tmp_path):
    from app.freecad_io.gerber_ports import suggest_gerber_ports
    from app.freecad_io.kicad_ports import apply_port_candidates
    from app.models.project import Project

    _write_ports_set(tmp_path)
    candidates = suggest_gerber_ports(tmp_path)
    project = Project.new_default("gerber_ports")
    added = apply_port_candidates(project, candidates)
    assert added == 2
    assert [p.name for p in project.ports] == ["in1", "out1"]
    assert project.ports[0].excite_port is True
    assert apply_port_candidates(project, candidates) == 0
