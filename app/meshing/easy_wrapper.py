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


def easy_mesh_setup(mesh: MeshSpec, boundary: BoundarySpec, targets: MeshTargets) -> dict:
    """The parameter dict easyMesh gets (pure: no CSX/Qt, unit-testable)."""
    setup = mesh.global_setup(boundary)
    setup["coord_system"] = getattr(mesh, "coord_system", "cartesian")
    if bool(getattr(mesh, "enforce_min_cellsize", False)):
        # V2 (opt-in): the repair pass enforces the floor AFTER easyMesh. Handing
        # the same number to easyMesh as well makes it re-refine the lateral
        # axes - measured on Filter_new_test1 with min=0.1: cells x2.42 and only
        # dt x1.35, so the repair had to undo easyMesh's own reaction. With the
        # floor kept out of easyMesh the same project gives dt x4.18 at cells
        # x0.93. Existing projects are unaffected (switch defaults to off).
        setup.pop("min_cellsize", None)
        setup.pop("min_cellsize_z", None)
    resolved = air_margin_units(mesh, boundary.air_margin, targets)
    setup["boundary_distance"] = resolved
    # easyMesh tutorials store BC as a dict; meshProcessing also accepts a list
    setup["boundary_conditions"] = {"BoundaryCond": boundary.as_openems_list()}
    return setup


def generate_mesh(
    CSX,
    mesh: MeshSpec,
    boundary: BoundarySpec,
    targets: MeshTargets,
    primitives_mesh_setup: dict | None = None,
    properties_mesh_setup: dict | None = None,
    ports: list | None = None,
    protected: dict | None = None,
):
    """Generate mesh via easyMesh (cartesian) or the polar mesher (cylindrical).

    IMPORTANT: pass the SAME primitives_mesh_setup dict that was given to
    enhance_csx/enhance_fdtd — easyMesh reads registered primitives/ports from it.

    ``ports`` (list of PortDef) is only used by the cylindrical branch to keep
    port faces on mesh lines.

    ``protected`` maps axis -> authored coordinates (primitives/ports/dumps/
    NF2FF); the repair pass never removes such a line. ``None`` protects only
    the domain edges and the manual lines.
    """
    setup = easy_mesh_setup(mesh, boundary, targets)

    if getattr(mesh, "coord_system", "cartesian") == "cylindrical":
        # easyMesh is cartesian-only (AABB edge extraction on x/y/z); the polar
        # mesher fills the r/alpha/z grid directly. easyMesh's enhance wrappers
        # above only REGISTER primitives/ports — they stay active so the polar
        # mesher can read faces and port coordinates from the shared dict.
        from app.meshing.polar import generate_polar_mesh

        if bool(getattr(mesh, "enforce_min_cellsize", False)):
            print(
                "  min-cell enforcement: not available for the cylindrical "
                "mesher (skipped; the polar grid is built from r/alpha/z)"
            )
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

    # NumPy 2 removed the ``np.atan`` alias that easyMesh's diagonal-edge
    # handler uses (polygons with 45 deg edges). ``np.arctan`` is the canonical
    # name, so provide the alias when missing.
    try:
        import numpy as _np

        if not hasattr(_np, "atan"):
            _np.atan = _np.arctan  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — never fail a build on the shim
        pass

    # easyMesh tutorial uses start_frequency 0 or f_min; keep project values
    GenerateMesh(
        CSX,
        setup,
        primitives_mesh_setup if primitives_mesh_setup is not None else {},
        properties_mesh_setup if properties_mesh_setup is not None else {},
    )

    def _align_ports() -> None:
        """Re-assert the port box coordinates on the grid (see port_align)."""
        try:
            from app.meshing.port_align import describe_stats, ensure_port_lines

            manual = {"x": [], "y": [], "z": []}
            _manual_all = getattr(mesh, "manual_mesh_lines", None)
            if isinstance(_manual_all, dict):
                for _axis in "xyz":
                    for _v in _manual_all.get(_axis) or []:
                        try:
                            manual[_axis].append(float(_v))
                        except (TypeError, ValueError):
                            continue
            stats = ensure_port_lines(CSX, ports, extra_protected=manual)
            if stats:
                print(f"  port alignment: {describe_stats(stats)}")
        except Exception as e:  # noqa: BLE001 — never fail a run on alignment
            print(f"  port alignment skipped: {e}")

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
    # A port clicked in the Mesh Viewer keeps its exact coordinate: easyMesh
    # treats the port face as an ordinary edge and may drop it (see port_align).
    _align_ports()
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
    # Mesh repairs (V1/V2) LAST: they re-space auto lines that only cost
    # runtime (cells below the floor / below the requested min cell size).
    # Protected lines (authored geometry, ports, manual lines) are never
    # removed, so a genuine gap keeps its resolution.
    try:
        from app.meshing.repair import apply_mesh_repairs

        _prot = dict(protected or {})
        # manual lines are authored coordinates too: a repair must keep them
        for _axis in ("x", "y", "z"):
            _vals = list(_prot.get(_axis) or [])
            _manual = (getattr(mesh, "manual_mesh_lines", None) or {}).get(_axis)
            for _v in _manual or []:
                try:
                    _vals.append(float(_v))
                except (TypeError, ValueError):
                    continue
            _prot[_axis] = _vals
        repairs = apply_mesh_repairs(CSX, mesh, targets, protected=_prot)
        setup["mesh_repairs"] = [
            {
                "axis": r.axis,
                "lo": r.lo,
                "hi": r.hi,
                "floor": r.floor,
                "min_before": r.min_before,
                "min_after": r.min_after,
                "removed": list(r.removed),
                "warning": r.warning,
            }
            for r in repairs
        ]
    except Exception as e:  # noqa: BLE001 — never fail a run on a repair
        print(f"  mesh repair skipped: {e}")
    # Manual edits may have removed a line a port needs — the run would abort in
    # the validator, so re-assert the port coordinates one last time.
    _align_ports()
    return setup
