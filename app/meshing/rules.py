"""Meshing rules and openEMS cell-size heuristics."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.project import (
    CELLS_PER_WAVELENGTH_BY_PRESET,
    MeshSpec,
    mm_per_unit,
)

C0 = 299792458.0  # m/s


def wavelength_m(frequency_hz: float, eps_r: float = 1.0, mu_r: float = 1.0) -> float:
    v = C0 / math.sqrt(max(eps_r, 1e-12) * max(mu_r, 1e-12))
    return v / max(frequency_hz, 1e-9)


@dataclass
class MeshTargets:
    f_max_hz: float
    eps_max: float
    unit_m: float
    lambda_min_m: float
    lambda_min_units: float
    max_cell_units: float
    target_cell_units: float


def compute_targets(mesh: MeshSpec, eps_max: float = 1.0) -> MeshTargets:
    f_max = max(mesh.start_frequency, mesh.stop_frequency)
    if mesh.target_frequency:
        # target_frequency overrides wavelength reference for mesh sizing
        f_max = max(f_max, mesh.target_frequency)
    unit_m = mesh.unit_to_meters()
    # Hard openEMS validation limit uses FREE-SPACE wavelength (εr=1).
    # Substrate refinement is easyMesh's job (material transitions / min_cellsize).
    # Using εr>1 here wrongly rejects valid air cells (λ_air/16 > λ_sub/10).
    lam = wavelength_m(f_max, eps_r=1.0)
    lam_units = lam / unit_m
    max_cell = lam_units / 10.0
    if mesh.max_cellsize is not None:
        max_cell = min(max_cell, mesh.max_cellsize)
    # Generation target from preset — must match easyMesh's own mapping
    # (meshingUtils.py: low/medium/high/very_high/extreme => 10/15/20/25/40
    # cells per wavelength). "custom" falls back to the medium density.
    preset_factor = CELLS_PER_WAVELENGTH_BY_PRESET.get(
        "medium" if mesh.preset == "custom" else mesh.preset, 15.0
    )
    target_cell = lam_units / preset_factor
    if mesh.refined_cellsize is not None:
        target_cell = mesh.refined_cellsize
    return MeshTargets(
        f_max_hz=f_max,
        eps_max=1.0,
        unit_m=unit_m,
        lambda_min_m=lam,
        lambda_min_units=lam_units,
        max_cell_units=max_cell,
        target_cell_units=target_cell,
    )


MESH_PROFILES: tuple[str, ...] = ("default", "antenna", "pcb")
MESH_PROFILE_LABELS: dict[str, str] = {
    "default": "Default",
    "antenna": "Antenna",
    "pcb": "PCB",
}


def default_mesh_profile(mesh: MeshSpec) -> dict[str, object]:
    """Balanced fallback for general 3D models.

    Medium air density (15 cells/λ) and automatic cell-size overrides keep the
    model quick to mesh while still resolving the bulk of the field.  Use the
    Antenna or PCB profile for structures with fine edges or thin copper.
    """
    return {
        "preset": "medium",
        "refined_cellsize": 0.0,
        "min_cellsize": 0.0,
        "min_cellsize_z": 0.0,
        "max_cellsize": 0.0,
        "max_grading_ratio": 2.0,
        "num_lines": 5,
        "smooth_metal_edge": "one_third_two_thirds",
        "use_circle_detection": False,
        "use_arc_detection": False,
        "handle_closely_placed_edges": True,
        "add_graded_mesh_lines_at_material_transitions": True,
        "align_coordinates": True,
        "diagnose_cell_sources": True,
        "enforce_min_cellsize": False,
        "coord_system": "cartesian",
        "dirs": "xyz",
    }


def antenna_mesh_profile(mesh: MeshSpec) -> dict[str, object]:
    """Mesh profile for antennas and other open radiating structures.

    Antenna currents concentrate on metal edges and in feed gaps, so the
    profile uses 20 cells/λ in air, a 0.1 mm minimum cell, gentler grading
    (1.5) and both circle/arc detection.  Max/refined cells stay automatic so
    easyMesh can follow the antenna's own feature sizes; this avoids making
    large antennas prohibitively expensive.
    """
    per_mm = 1.0 / mm_per_unit(mesh.drawing_unit or "mm")
    min_mm = 0.1
    return {
        "preset": "high",
        "refined_cellsize": 0.0,
        "min_cellsize": min_mm * per_mm,
        "min_cellsize_z": min_mm * per_mm,
        "max_cellsize": 0.0,
        "max_grading_ratio": 1.5,
        "num_lines": 5,
        "smooth_metal_edge": "one_third_two_thirds",
        "use_circle_detection": True,
        "use_arc_detection": True,
        "handle_closely_placed_edges": True,
        "add_graded_mesh_lines_at_material_transitions": True,
        "align_coordinates": True,
        "diagnose_cell_sources": True,
        "enforce_min_cellsize": False,
        "coord_system": "cartesian",
        "dirs": "xyz",
    }


def pcb_mesh_profile(mesh: MeshSpec) -> dict[str, object]:
    """Recommended mesh overrides for a PCB (FR4/PTFE-style board).

    PCB structures are usually much thinner than the free-space wavelength and
    have small lateral features (track width, gaps, via drills).  A pure
    ``cells per wavelength`` preset therefore leaves far too few cells across
    the substrate and around copper edges.  This profile combines:

    * ``high`` air density (20 cells/lambda at f_max),
    * a 0.5 mm maximum cell cap (also limits PML growth),
    * 0.25 mm refinement around edges/gaps,
    * 0.05 mm minimum cells (thin copper, small gaps),
    * 1.3 grading, circle/arc detection and graded metal interfaces.

    Values are in drawing units.  The frequency-dependent cap uses the higher
    of start/stop so the whole band is covered.  Boundary air margin and PML
    size stay in Simulation Parameters; this profile only touches the mesh.
    """
    f_max = max(
        float(mesh.start_frequency or 0.0),
        float(mesh.stop_frequency or 0.0),
    )
    if f_max <= 0.0:
        f_max = 3e9
    per_mm = 1.0 / mm_per_unit(mesh.drawing_unit or "mm")
    lam_mm = wavelength_m(f_max, eps_r=1.0) * 1000.0
    max_mm = min(0.5, lam_mm / 20.0)
    refined_mm = min(0.25, max_mm / 2.0)
    min_mm = min(0.05, refined_mm / 4.0)
    return {
        "preset": "high",
        "refined_cellsize": refined_mm * per_mm,
        "min_cellsize": min_mm * per_mm,
        "min_cellsize_z": min_mm * per_mm,
        "max_cellsize": max_mm * per_mm,
        "max_grading_ratio": 1.3,
        "num_lines": 5,
        "smooth_metal_edge": "one_third_two_thirds",
        "use_circle_detection": True,
        "use_arc_detection": True,
        "handle_closely_placed_edges": True,
        "add_graded_mesh_lines_at_material_transitions": True,
        "align_coordinates": True,
        "diagnose_cell_sources": True,
        "enforce_min_cellsize": False,
        "coord_system": "cartesian",
        "dirs": "xyz",
    }


def mesh_profile(mesh: MeshSpec, profile: str) -> dict[str, object]:
    """Return the override set for one of the three high-level profiles."""
    key = str(profile or "default").strip().lower()
    if key == "pcb":
        return pcb_mesh_profile(mesh)
    if key == "antenna":
        return antenna_mesh_profile(mesh)
    return default_mesh_profile(mesh)


def resolve_mesh_profile(mesh: MeshSpec, antenna_mode: bool = False) -> str:
    """Best-effort profile for the UI, including projects saved before profiles.

    New projects store ``mesh_profile``.  For older projects we recognise the
    existing PCB override fingerprint and the antenna-simulation flag so the
    correct checkmark is shown instead of always falling back to Default.
    """
    stored = str(getattr(mesh, "mesh_profile", "") or "").strip().lower()
    if stored in MESH_PROFILES:
        return stored
    if antenna_mode:
        return "antenna"
    try:
        per_mm = 1.0 / mm_per_unit(mesh.drawing_unit or "mm")
        max_mm = float(mesh.max_cellsize or 0.0) / per_mm
        min_mm = float(mesh.min_cellsize or 0.0) / per_mm
        if (
            0.0 < max_mm <= 0.5001
            and 0.0 < min_mm <= 0.0501
            and abs(float(mesh.max_grading_ratio or 0.0) - 1.3) < 1e-3
            and bool(mesh.use_circle_detection)
            and bool(mesh.use_arc_detection)
        ):
            return "pcb"
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return "default"


def air_margin_units(mesh: MeshSpec, boundary_air: list[float | str], targets: MeshTargets) -> list[float]:
    """Resolve boundary air margin entries to drawing units.

    Air margin is the free-space region around the structure — always use εr=1,
    never the substrate εr (that shrinks the domain and pushes NF2FF outside).
    """
    out: list[float | str] = []
    # air region: εr = 1 regardless of materials inside the structure
    lam_max_units = (
        wavelength_m(max(mesh.start_frequency, mesh.stop_frequency), eps_r=1.0) / targets.unit_m
    )
    auto = lam_max_units / 4.0
    for i, b in enumerate(boundary_air):
        if b == "auto" or b is None:
            out.append(auto)
        else:
            out.append(float(b))
    if getattr(mesh, "coord_system", "cartesian") == "cylindrical":
        # Polar roles (list = [r-, r+, phi-, phi+, z-, z+]): r- is the r=0
        # axis (no margin possible) and phi+/- are unused — the cylinder is
        # closed over the full 2*pi. Only r+, z- and z+ take air margin.
        out[0] = 0.0
        out[2] = 0.0
        out[3] = 0.0
    return out
