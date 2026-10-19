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


def cap_max_cell_size(CSX, mesh: MeshSpec, targets: MeshTargets) -> None:
    """Guarantee no grid cell exceeds the openEMS hard limit (lambda_air/10).

    easyMesh sizes the AIR cells from ``target_frequency`` and then expands the
    PML region (``PML_8``) by ~1.27x, which can push the absorbing-region cells
    just past ``lambda(f_max)/10``; the mesh validator then aborts the whole run.
    Splitting the oversized cells (``SmoothMeshLines``) keeps the mesh valid
    without touching the preset's characteristic air cellsize.
    """
    grid = CSX.GetGrid()
    max_cell = float(targets.max_cell_units)
    # CSXCAD needs ratio > 1 (1.0 raises with an empty message -> the oversized
    # PML cell would stay and the validator aborts the run with cell_too_large).
    ratio = float(mesh.effective_max_grading_ratio())
    for axis in "xyz":
        try:
            grid.SmoothMeshLines(axis, max_cell, ratio)
        except Exception as e:  # noqa: BLE001 — never fail a run on the cap
            print(f"  max-cell cap ({max_cell:.4g}) skipped on {axis}: {e!r}")


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
    # Finally clamp EVERY cell (incl. the PML/absorbing region easyMesh may have
    # stretched) to the hard lambda/10 limit the validator enforces.
    cap_max_cell_size(CSX, mesh, targets)

    # Snapshot the auto grid (before the manual overrides) so the Mesh Editor
    # can re-apply edits to the *existing* mesh without re-running easyMesh.
    try:
        import numpy as _np

        _g = CSX.GetGrid()
        auto_lines: dict[str, list[float]] = {}
        for _d, _name in enumerate(("x", "y", "z")):
            _arr = _np.asarray(_g.GetLines(_d), dtype=float)
            if _arr.size:
                auto_lines[_name] = [float(v) for v in _arr]
        setup["auto_mesh_lines"] = auto_lines
    except Exception as e:  # noqa: BLE001
        print(f"  auto-grid snapshot skipped: {e}")

    # Manual Mesh-Editor overrides LAST, so user-placed lines are not smoothed
    # away again by easyMesh / the cap. Removals first, then additions.
    try:
        from app.meshing.manual_mesh import apply_manual_mesh_lines

        stats = apply_manual_mesh_lines(CSX, mesh, coord_system="cartesian")
        if stats:
            parts: list[str] = []
            for k in sorted(stats):
                s = stats[k]
                parts.append(f"{k}: +{s['added']}/-{s['removed']}")
                for c in s.get("missed", []):
                    print(
                        f"  WARNING: manual line removal on {k} found no matching "
                        f"grid line (did the mesh change?) near {c:.4g}"
                    )
                for c in s.get("outside", []):
                    print(
                        f"  WARNING: manual line on {k} at {c:.4g} is outside the "
                        f"domain — ignored"
                    )
                if s.get("drift", 0.0) > 0.0:
                    print(
                        f"  note: manual removal on {k} snapped by up to "
                        f"{s['drift']:.4g} to the nearest grid line"
                    )
            print("  manual mesh lines: " + ", ".join(parts))
        setup["manual_mesh_lines"] = stats
    except Exception as e:  # noqa: BLE001 — never fail a run on the editor
        print(f"  manual mesh lines skipped: {e}")
    return setup
