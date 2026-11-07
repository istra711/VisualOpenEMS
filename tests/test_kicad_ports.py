"""Tests for automatic KiCad port suggestions (no Qt)."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def _write_ports_board(path: Path, *, explicit: bool = False) -> None:
    marker = ""
    if explicit:
        marker = """
    (property "openems_port" "rf_in")
    (property "openems_port_type" "msl")"""
    text = f"""(kicad_pcb
  (version 20240108)
  (generator "test")
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )
  (setup
    (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.4) (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))
    )
  )
  (net 0 "")
  (net 1 "GND")
  (net 2 "RF_IN")
  (net 3 "RF_OUT")
  (footprint "Connector:SMA" (layer "F.Cu") (at 5 5)
    (property "Reference" "J1"){marker}
    (pad "1" smd rect (at 0 0) (size 2 1) (layers "F.Cu") (net 2 "RF_IN"))
    (pad "2" smd rect (at 0 -2) (size 2 1) (layers "F.Cu") (net 1 "GND"))
  )
  (footprint "Connector:SMA" (layer "F.Cu") (at 15 5)
    (property "Reference" "J2")
    (pad "1" smd rect (at 0 0) (size 2 1) (layers "F.Cu") (net 3 "RF_OUT"))
    (pad "2" smd rect (at 0 -2) (size 2 1) (layers "F.Cu") (net 1 "GND"))
  )
  (segment (start 5 5) (end 12 5) (width 0.5) (layer "F.Cu") (net 2))
  (segment (start 8 5) (end 15 5) (width 0.5) (layer "F.Cu") (net 3))
  (gr_line (start 0 0) (end 20 0) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 20 0) (end 20 10) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 20 10) (end 0 10) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 0 10) (end 0 0) (layer "Edge.Cuts") (width 0.1))
)
"""
    path.write_text(text, encoding="utf-8")


def test_load_board_model_and_suggest_ports(tmp_path):
    from app.freecad_io.kicad_ports import load_kicad_board_model, suggest_kicad_ports

    path = tmp_path / "ports.kicad_pcb"
    _write_ports_board(path)

    model = load_kicad_board_model(path)
    assert model.copper_layers == ["F.Cu", "B.Cu"]
    assert model.z_by_layer["F.Cu"] == 0.8
    assert model.z_by_layer["B.Cu"] == -0.8
    assert len(model.pads) == 4
    assert len(model.tracks) == 2
    assert {pad.net for pad in model.pads} == {"GND", "RF_IN", "RF_OUT"}

    candidates = suggest_kicad_ports(path)
    assert len(candidates) == 2
    by_name = {candidate.name: candidate for candidate in candidates}
    assert set(by_name) == {"in1", "out1"}
    assert by_name["in1"].net == "RF_IN"
    assert by_name["out1"].net == "RF_OUT"
    assert by_name["in1"].type == "lumped"

    in1 = by_name["in1"]
    # Board centre is (10, 5): RF_IN pad/track endpoint at x=5 -> -5; y=5 -> 0.
    assert in1.start == [-5.0, -0.25, -0.8]
    assert in1.stop == [-5.0, 0.25, 0.8]
    assert in1.direction == "z"
    assert in1.lumped_width == "y"
    assert in1.excite is True
    assert by_name["out1"].excite is False
    assert by_name["in1"].confidence > 0.5


def test_explicit_openems_port_marker_overrides_name_and_type(tmp_path):
    from app.freecad_io.kicad_ports import suggest_kicad_ports

    path = tmp_path / "ports.kicad_pcb"
    _write_ports_board(path, explicit=True)
    candidates = suggest_kicad_ports(path)
    marked = next(c for c in candidates if c.net == "RF_IN")
    assert marked.name == "rf_in"
    assert marked.type == "msl"
    assert marked.prop_direction == "x"
    assert marked.exc_direction == "z"
    assert marked.metal == "cu_F_Cu"


def test_apply_port_candidates_to_project(tmp_path):
    from app.freecad_io.kicad_ports import apply_port_candidates, suggest_kicad_ports
    from app.models.project import Project

    path = tmp_path / "ports.kicad_pcb"
    _write_ports_board(path)
    candidates = suggest_kicad_ports(path)
    project = Project.new_default("ports")

    added = apply_port_candidates(project, candidates)
    assert added == 2
    assert [p.name for p in project.ports] == ["in1", "out1"]
    assert project.ports[0].type == "lumped"
    assert project.ports[0].excite_port is True
    assert project.ports[1].excite_port is False

    # Re-applying does not duplicate existing ports.
    assert apply_port_candidates(project, candidates) == 0
    assert len(project.ports) == 2
