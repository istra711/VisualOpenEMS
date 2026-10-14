"""Wrapper around easyMesh4openEMS with correct call order and shared mesh-setup dict."""

from __future__ import annotations

from typing import Any

from app.meshing.rules import MeshTargets, air_margin_units
from app.models.project import BoundarySpec, MeshSpec


def enhance_csx(CSX, primitives_mesh_setup: dict | None = None):
    from easyMesh import enhance_csx_for_auto_mesh

    return enhance_csx_for_auto_mesh(CSX, primitives_mesh_setup if primitives_mesh_setup is not None else {})


def enhance_fdtd(FDTD, primitives_mesh_setup: dict | None = None):
    from easyMesh import enhance_FDTD_for_auto_mesh

    return enhance_FDTD_for_auto_mesh(FDTD, primitives_mesh_setup if primitives_mesh_setup is not None else {})


def apply_boundary(FDTD, boundary: BoundarySpec, mesh: MeshSpec, targets: MeshTargets):
    """Set boundary conditions AFTER enhance_fdtd so easyMesh can track PML."""
    margin = air_margin_units(mesh, boundary.air_margin, targets)
    resolved = list(boundary.air_margin)
    for i, m in enumerate(margin):
        if boundary.air_margin[i] in ("auto", None):
            resolved[i] = float(m)
    FDTD.SetBoundaryCond(boundary.as_openems_list())
    return resolved


def generate_mesh(
    CSX,
    mesh: MeshSpec,
    boundary: BoundarySpec,
    targets: MeshTargets,
    primitives_mesh_setup: dict | None = None,
    properties_mesh_setup: dict | None = None,
):
    """Generate mesh via easyMesh.

    IMPORTANT: pass the SAME primitives_mesh_setup dict that was given to
    enhance_csx/enhance_fdtd — easyMesh reads registered primitives/ports from it.
    """
    from easyMesh import GenerateMesh

    setup = mesh.global_setup(boundary)
    resolved = air_margin_units(mesh, boundary.air_margin, targets)
    setup["boundary_distance"] = resolved
    # easyMesh tutorials store BC as a dict; meshProcessing also accepts a list
    setup["boundary_conditions"] = {"BoundaryCond": boundary.as_openems_list()}
    # easyMesh tutorial uses start_frequency 0 or f_min; keep project values
    GenerateMesh(
        CSX,
        setup,
        primitives_mesh_setup if primitives_mesh_setup is not None else {},
        properties_mesh_setup if properties_mesh_setup is not None else {},
    )
    return setup
