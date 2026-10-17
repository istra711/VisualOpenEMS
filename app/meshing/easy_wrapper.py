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
    ports: list | None = None,
):
    """Generate mesh via easyMesh (cartesian) or the polar mesher (cylindrical).

    IMPORTANT: pass the SAME primitives_mesh_setup dict that was given to
    enhance_csx/enhance_fdtd — easyMesh reads registered primitives/ports from it.

    ``ports`` (list of PortDef) is only used by the cylindrical branch to keep
    port faces on mesh lines.
    """
    setup = mesh.global_setup(boundary)
    setup["coord_system"] = getattr(mesh, "coord_system", "cartesian")
    resolved = air_margin_units(mesh, boundary.air_margin, targets)
    setup["boundary_distance"] = resolved
    # easyMesh tutorials store BC as a dict; meshProcessing also accepts a list
    setup["boundary_conditions"] = {"BoundaryCond": boundary.as_openems_list()}

    if getattr(mesh, "coord_system", "cartesian") == "cylindrical":
        # easyMesh is cartesian-only (AABB edge extraction on x/y/z); the polar
        # mesher fills the r/alpha/z grid directly. easyMesh's enhance wrappers
        # above only REGISTER primitives/ports — they stay active so the polar
        # mesher can read faces and port coordinates from the shared dict.
        from app.meshing.polar import generate_polar_mesh

        polar_report = generate_polar_mesh(
            CSX,
            mesh,
            boundary,
            targets,
            primitives_mesh_setup=primitives_mesh_setup,
            ports=ports,
        )
        setup.update(polar_report)
        return setup

    from easyMesh import GenerateMesh

    # easyMesh tutorial uses start_frequency 0 or f_min; keep project values
    GenerateMesh(
        CSX,
        setup,
        primitives_mesh_setup if primitives_mesh_setup is not None else {},
        properties_mesh_setup if properties_mesh_setup is not None else {},
    )

    # Graded refinement on the vacuum/dielectric side of metal faces. Runs
    # AFTER easyMesh so the inserted lines survive SmoothMeshLines (the
    # easyMesh-internal graded-transition pass is smoothed away and misses
    # metal-in-air interfaces entirely).
    if getattr(mesh, "add_graded_mesh_lines_at_material_transitions", False):
        try:
            from app.meshing.interface_refine import refine_at_metal_interfaces

            stats = refine_at_metal_interfaces(CSX, mesh, coord_system="cartesian")
            if stats:
                print(
                    "  metal-interface graded refinement: "
                    + ", ".join(f"{k} +{v}" for k, v in sorted(stats.items()))
                    + " line(s)"
                )
        except Exception as e:  # noqa: BLE001 — never fail a run on refinement
            print(f"  metal-interface graded refinement skipped: {e}")
    return setup
