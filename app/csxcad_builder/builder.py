"""Build CSXCAD structures from Project assignments (primitives + optional STL)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from app.models.project import GeomAssignment, MaterialDef, PortDef, Project


class BuildResult:
    def __init__(self) -> None:
        self.csx = None
        self.fdtd = None
        self.warnings: list[str] = []
        self.objects_built: list[str] = []


def _apply_material(csx, mat: MaterialDef):
    if mat.kind == "pec":
        prop = csx.AddMetal(mat.name)
        return prop
    if mat.kind == "conducting_sheet":
        prop = csx.AddConductingSheet(mat.name, conductivity=mat.kappa, thickness=0.0)
        return prop
    if mat.kind == "dielectric":
        kwargs = {"epsilon": mat.epsilon}
        if mat.kappa:
            kwargs["kappa"] = mat.kappa
        prop = csx.AddMaterial(mat.name, **kwargs)
        return prop
    if mat.kind == "lumped_rlc":
        prop = csx.AddMaterial(mat.name, epsilon=1.0)
        return prop
    return None


def _default_primitives_from_bounds(
    project: Project,
    bounds_min: list[float] | None,
    bounds_max: list[float] | None,
    result: BuildResult,
) -> None:
    """If no STEP/STL solids available, create substrate+ground+patch style placeholders
    only when user has at least one assignment with dielectric/pec roles using box extents.
    """
    if not bounds_min or not bounds_max:
        return
    # Intentionally empty: real geometry comes from FreeCAD objects or user boxes.
    # Kept as extension point for auto-substrate helpers.


def build_csx_from_project(
    project: Project,
    csx=None,
    geometry_triangles: dict[str, np.ndarray] | None = None,
    bounds: tuple[list[float] | None, list[float] | None] | None = None,
    fdtd=None,
) -> BuildResult:
    """Create ContinuousStructure and add materials/geometry/ports from Project."""
    if csx is None:
        from CSXCAD import ContinuousStructure

        csx = ContinuousStructure()

    result = BuildResult()
    result.csx = csx
    result.fdtd = fdtd

    unit = project.mesh.unit_to_meters()
    csx.GetGrid().SetDeltaUnit(unit)

    mat_props: dict[str, Any] = {}
    for mat in project.materials:
        prop = _apply_material(csx, mat)
        if prop is not None:
            mat_props[mat.name] = prop

    geometry_triangles = geometry_triangles or {}

    for assign in project.assignments:
        name = assign.object_name
        if assign.role == "ignore":
            continue
        prop = mat_props.get(assign.material)
        if prop is None:
            # create on the fly
            if assign.role == "pec":
                prop = csx.AddMetal(assign.material or name)
            elif assign.role == "dielectric":
                prop = csx.AddMaterial(assign.material or name, epsilon=2.0)
            elif assign.role == "conducting_sheet":
                prop = csx.AddConductingSheet(assign.material or name, conductivity=5.8e7)
            else:
                result.warnings.append(f"No property for assignment {name}")
                continue
            mat_props[assign.material or name] = prop

        tris = geometry_triangles.get(name)
        stl_path = None
        if tris is None or not len(tris):
            # optional stl path from assignment notes / project geometry
            for gf in project.geometry_files:
                if gf.object_name == name and gf.path:
                    stl_path = gf.path
        if (tris is not None and len(tris)) or (stl_path and Path(stl_path).exists()):
            try:
                from app.csxcad_builder.triangle_mesh import add_triangle_mesh

                mesh_prim = add_triangle_mesh(
                    prop,
                    name=name,
                    triangles=tris if tris is not None and len(tris) else None,
                    stl_path=stl_path,
                    priority=assign.priority,
                )
                label = "axis-aligned box" if type(mesh_prim).__name__ == "CSPrimBox" else "triangle mesh"
                result.objects_built.append(f"{name} ({label})")
            except Exception as e:  # noqa: BLE001
                result.warnings.append(f"STL mesh failed for {name}: {e}")
        else:
            # Geometry without triangles: leave a marker; user must define primitives in GUI
            result.warnings.append(
                f"Object '{name}' has no primitive/STL data yet — assign a box/polygon in Geometry tab "
                f"(role={assign.role}, material={assign.material})"
            )

    _default_primitives_from_bounds(project, bounds[0] if bounds else None, bounds[1] if bounds else None, result)

    # Ports
    for port in project.ports:
        try:
            _add_port(csx, port, result, fdtd=fdtd)
        except Exception as e:  # noqa: BLE001
            result.warnings.append(f"Port '{port.name}' failed: {e}")

    return result


def add_box(csx, name: str, start, stop, priority: int = 10, metal: str | None = None):
    if metal:
        prop = csx.AddMetal(metal)
    else:
        prop = csx.AddMaterial(name, epsilon=1.0)
    prop.AddBox([float(x) for x in start], [float(x) for x in stop], priority=priority)
    return prop


def _add_port(csx, port: PortDef, result: BuildResult, fdtd=None):
    start = [float(x) for x in port.start]
    stop = [float(x) for x in port.stop]
    # openEMS Python bindings: lumped/MSL ports live on FDTD with integer port numbers
    if port.type in {"lumped", "msl"}:
        if fdtd is None:
            result.warnings.append(
                f"Port '{port.name}' ({port.type}) needs FDTD object — created in simulation script"
            )
            return
        if port.type == "lumped":
            fdtd.AddLumpedPort(
                port.resistance,
                start,
                stop,
                port.direction,
                1.0 if port.excite_port else 0.0,
                priority=15,
            )
        else:
            fdtd.AddMSLPort(
                port.resistance,
                start,
                stop,
                port.direction,
                1.0 if port.excite_port else 0.0,
                priority=15,
            )
        result.objects_built.append(f"port:{port.name}")
    elif port.type == "rect_waveguide":
        result.warnings.append(
            f"Port '{port.name}' rect_waveguide will be created in simulation script (needs FDTD)"
        )
    else:
        result.warnings.append(f"Unsupported port type in builder: {port.type}")


def create_sample_patch_project() -> Project:
    """Minimal patch-like project used by smoke tests (geometry in drawing units = mm)."""
    p = Project.new_default("patch_sample")
    p.mesh.drawing_unit = "mm"
    p.mesh.start_frequency = 1e9
    p.mesh.stop_frequency = 3e9
    p.mesh.target_frequency = 2.4e9
    p.ports = [
        PortDef(
            name="in1",
            type="lumped",
            start=[0.0, 0.0, 0.0],
            stop=[0.0, 0.0, 1.57],
            resistance=50.0,
            direction="z",
            excite_port=True,
        )
    ]
    # user will assign substrate/patch via primitives in builder helpers
    return p
