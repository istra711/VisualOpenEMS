"""Pure tests for the grid JSON export (app.meshing.grid_export).

The grid file is the counterpart of the STEP export for the CST round-trip:
both solvers are FDTD with a rectilinear grid, so sharing the line positions
lets them run on identical cells.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from app.meshing.grid_export import FORMAT_NAME, export_grid_json, grid_payload
from app.models.project import MeshSpec, mm_per_unit


def test_mm_per_unit_matches_mesh_spec():
    for unit in ("m", "cm", "mm", "um"):
        assert mm_per_unit(unit) == pytest.approx(
            MeshSpec(drawing_unit=unit).unit_to_meters() * 1000.0
        )


def test_payload_sorts_and_dedupes():
    payload = grid_payload(
        {"x": [2.0, 0.0, 1.0, 1.0], "y": [0.0, 5.0], "z": [0.0, 1.0, 2.0, 3.0]},
        unit="mm",
    )
    assert payload["axes"][0]["lines"] == pytest.approx([0.0, 1.0, 2.0])
    assert payload["shape"] == [2, 1, 3]


def test_payload_converts_to_mm():
    payload = grid_payload({"x": [0.0, 0.02], "y": [0.0, 0.01], "z": [0.0]}, unit="m")
    assert payload["unit"] == "mm"
    assert payload["source_unit"] == "m"
    assert payload["axes"][0]["lines"] == pytest.approx([0.0, 20.0])
    assert payload["axes"][1]["lines"] == pytest.approx([0.0, 10.0])


def test_payload_cell_stats():
    payload = grid_payload({"x": [0.0, 1.0, 4.0], "y": [0.0, 2.0], "z": [0.0, 1.0]})
    x = payload["axes"][0]
    assert x["cells"] == 2
    assert x["cell_size_min"] == pytest.approx(1.0)
    assert x["cell_size_max"] == pytest.approx(3.0)


def test_payload_cylindrical_axis_names():
    payload = grid_payload(
        {"x": [0.0, 1.0], "y": [0.0, 90.0], "z": [0.0, 1.0]},
        coord_system="cylindrical",
    )
    assert [a["name"] for a in payload["axes"]] == ["rho", "phi", "z"]


def test_payload_domain_scaled():
    payload = grid_payload(
        {"x": [0.0, 1.0], "y": [0.0, 1.0], "z": [0.0, 1.0]},
        unit="cm",
        domain=[[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]],
    )
    assert payload["domain"]["stop"] == pytest.approx([10.0, 20.0, 30.0])


def test_payload_drops_non_finite():
    payload = grid_payload({"x": [0.0, np.nan, 1.0, np.inf], "y": [0.0], "z": [0.0]})
    assert payload["axes"][0]["lines"] == pytest.approx([0.0, 1.0])


def test_empty_axis_gives_zero_cells():
    payload = grid_payload({}, unit="mm")
    assert payload["shape"] == [0, 0, 0]
    assert payload["axes"][0]["lines"] == []
    assert "cell_size_min" not in payload["axes"][0]


def test_unknown_unit_raises():
    with pytest.raises(ValueError, match="unsupported unit"):
        grid_payload({"x": [0.0]}, unit="inch")


def test_export_writes_json(tmp_path):
    out = export_grid_json(
        {"x": [0.0, 1.0], "y": [0.0, 2.0], "z": [0.0, 0.5]}, tmp_path / "g.json"
    )
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format"] == FORMAT_NAME
    assert data["version"] == 1
    assert data["unit"] == "mm"
    assert data["shape"] == [1, 1, 1]
    assert data["axes"][2]["lines"] == pytest.approx([0.0, 0.5])


def test_export_creates_parent_dir(tmp_path):
    out = export_grid_json({"x": [0.0, 1.0]}, tmp_path / "a" / "b" / "g.json")
    assert out.exists()
