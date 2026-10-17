"""Meshing rules and openEMS cell-size heuristics."""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.project import CELLS_PER_WAVELENGTH_BY_PRESET, MeshSpec

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
