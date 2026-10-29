"""Rectangular waveguide ports: model round-trip and run-script guards."""

from __future__ import annotations

import pytest

from app.models.project import MeshSpec, PortDef, Project
from app.simulation.script_gen import generate_run_script


def _project(port: PortDef) -> Project:
    p = Project(name="wg")
    p.mesh = MeshSpec(drawing_unit="mm", start_frequency=8.5e9, stop_frequency=11.5e9)
    p.ports = [port]
    return p


def test_waveguide_fields_survive_save_and_load(tmp_path):
    port = PortDef(
        name="p1",
        type="rect_waveguide",
        start=[0.0, 0.0, 10.0],
        stop=[22.86, 10.16, 13.0],
        direction="z",
        wg_a=22.86,
        wg_b=10.16,
        wg_mode="TE01",
    )
    path = tmp_path / "p.fcsim.json"
    _project(port).save(path)

    loaded = Project.load(path)
    back = loaded.ports[0]
    assert back.type == "rect_waveguide"
    assert back.wg_a == pytest.approx(22.86)
    assert back.wg_b == pytest.approx(10.16)
    assert back.wg_mode == "TE01"


def test_waveguide_port_without_dimensions_is_rejected(tmp_path):
    port = PortDef(
        name="p1",
        type="rect_waveguide",
        start=[0.0, 0.0, 10.0],
        stop=[22.86, 10.16, 13.0],
        direction="z",
    )
    with pytest.raises(ValueError, match="width a"):
        generate_run_script(
            _project(port), tmp_path / "p.fcsim.json", tmp_path, primitives=[]
        )
