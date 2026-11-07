"""Tests for the three high-level mesh profiles (no GUI)."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.meshing.rules import mesh_profile, resolve_mesh_profile
from app.models.project import MeshSpec, Project


def test_profiles_have_distinct_targets() -> None:
    mesh = MeshSpec(drawing_unit="mm", start_frequency=1.9e9, stop_frequency=2.5e9)

    default = mesh_profile(mesh, "default")
    antenna = mesh_profile(mesh, "antenna")
    pcb = mesh_profile(mesh, "pcb")

    assert default["preset"] == "medium"
    assert default["min_cellsize"] == 0.0
    assert default["max_cellsize"] == 0.0
    assert default["add_graded_mesh_lines_at_material_transitions"] is True

    assert antenna["preset"] == "high"
    assert antenna["min_cellsize"] == 0.1
    assert antenna["max_cellsize"] == 0.0
    assert antenna["max_grading_ratio"] == 1.5
    assert antenna["use_circle_detection"] is True
    assert antenna["use_arc_detection"] is True

    assert pcb["preset"] == "high"
    assert pcb["max_cellsize"] == 0.5
    assert pcb["refined_cellsize"] == 0.25
    assert pcb["min_cellsize"] == 0.05
    assert pcb["max_grading_ratio"] == 1.3
    assert pcb["use_circle_detection"] is True


def test_profile_resolution_and_roundtrip() -> None:
    assert resolve_mesh_profile(MeshSpec()) == "default"
    assert resolve_mesh_profile(MeshSpec(), antenna_mode=True) == "antenna"
    assert resolve_mesh_profile(MeshSpec(mesh_profile="pcb")) == "pcb"

    legacy_pcb = MeshSpec(
        max_cellsize=0.5,
        min_cellsize=0.05,
        max_grading_ratio=1.3,
        use_circle_detection=True,
        use_arc_detection=True,
    )
    assert resolve_mesh_profile(legacy_pcb) == "pcb"

    project = Project.new_default("profiles")
    project.mesh.mesh_profile = "antenna"
    restored = Project.from_dict(project.to_dict())
    assert restored.mesh.mesh_profile == "antenna"
