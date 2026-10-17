"""Qt-free mesh build + validate core.

Extracted from ``MainWindow._build_and_validate`` so the heavy easyMesh /
validator / CSX-XML-export work can run in a worker thread. Only plain Python +
CSXCAD/openEMS is used here — no Qt, no matplotlib, no GUI state. The GUI
wrapper (:meth:`MainWindow._on_build_mesh`) keeps the patch-preset fallback, the
mesh viewer update, the result recording and the matplotlib PNG preview on the
GUI thread.

``log`` is an optional ``callable(str)``; the GUI passes its thread-safe
``_log`` (which queues onto the GUI thread).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.csxcad_builder.quantize import f32 as _f32


@dataclass
class MeshBuildResult:
    """Everything the GUI needs from a mesh build."""

    lines: list[str] = field(default_factory=list)
    csx: object = None
    fdtd: object = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def build_mesh_and_validate(
    project,
    primitives,
    work_dir: Path | str,
    log: Callable[[str], None] | None = None,
) -> MeshBuildResult:
    """Build the CSX, run easyMesh, validate and export ``csx.xml``.

    Returns the report lines plus the live CSX/FDTD objects. Raises on hard
    errors (the caller reports them); soft warnings are appended to ``lines``.
    """
    import json
    import warnings

    import numpy as np

    from app.meshing.easy_wrapper import apply_boundary, enhance_csx, enhance_fdtd, generate_mesh
    from app.meshing.rules import compute_targets
    from app.meshing.validators import report_to_lines, validate_mesh
    from app.meshing.preview import export_csx_xml
    from openEMS import openEMS
    from CSXCAD import ContinuousStructure

    def _log(msg: str) -> None:
        if log is not None:
            log(msg)

    work_dir = Path(work_dir)
    lines: list[str] = []

    _cs = 1 if project.mesh.is_cylindrical else 0
    FDTD = openEMS(NrTS=1000, EndCriteria=1e-3, CoordSystem=_cs)
    f0 = project.mesh.target_frequency or 2.45e9
    fc = 0.5 * abs(project.mesh.stop_frequency - project.mesh.start_frequency) or 1e6
    FDTD.SetGaussExcite(f0, fc)
    CSX = ContinuousStructure(CoordSystem=_cs)
    FDTD.SetCSX(CSX)
    primitives_mesh_setup: dict = {}
    CSX = enhance_csx(CSX, primitives_mesh_setup)
    FDTD = enhance_fdtd(FDTD, primitives_mesh_setup)
    targets = compute_targets(project.mesh, eps_max=1.0)
    apply_boundary(FDTD, project.boundary, project.mesh, targets)
    CSX.GetGrid().SetDeltaUnit(project.mesh.unit_to_meters())

    mat_by_name = {m.name: m for m in project.materials}
    props: dict = {}
    for mat in project.materials:
        if mat.kind == "pec":
            props[mat.name] = CSX.AddMetal(mat.name)
        elif mat.kind == "dielectric":
            kwargs = {"epsilon": mat.epsilon}
            if mat.kappa:
                kwargs["kappa"] = mat.kappa
            props[mat.name] = CSX.AddMaterial(mat.name, **kwargs)
        elif mat.kind == "conducting_sheet":
            props[mat.name] = CSX.AddConductingSheet(mat.name, conductivity=mat.kappa)

    def _prop_for(mat_name: str):
        if mat_name in props:
            return props[mat_name]
        m = mat_by_name.get(mat_name)
        if m is None:
            if mat_name in ("copper", "pec") or "copper" in mat_name:
                props[mat_name] = CSX.AddMetal(mat_name)
            else:
                props[mat_name] = CSX.AddMaterial(mat_name, epsilon=2.2)
            return props[mat_name]
        if m.kind == "pec":
            props[mat_name] = CSX.AddMetal(m.name)
        elif m.kind == "conducting_sheet":
            props[mat_name] = CSX.AddConductingSheet(m.name, conductivity=m.kappa)
        else:
            kwargs = {"epsilon": m.epsilon}
            if m.kappa:
                kwargs["kappa"] = m.kappa
            props[mat_name] = CSX.AddMaterial(m.name, **kwargs)
        return props[mat_name]

    for pr in primitives:
        kind = pr.get("kind", "box")
        name = str(pr.get("name", "prim"))
        if project.mesh.is_cylindrical:
            from app.meshing.polar import ensure_polar_primitive

            ensure_polar_primitive(kind, name)
        mat = pr.get("material", "copper")
        pri = int(pr.get("priority", 10))
        prop = _prop_for(mat)
        if kind == "box":
            prop.AddBox(
                start=[_f32(x) for x in pr["start"]],
                stop=[_f32(x) for x in pr["stop"]],
                priority=pri,
            )
            if pr.get("edges2grid"):
                try:
                    FDTD.AddEdges2Grid(
                        dirs=pr.get("edges2grid", "xy"),
                        properties=prop,
                        metal_edge_res=pr.get("metal_edge_res"),
                    )
                except Exception as e:  # noqa: BLE001
                    _log(f"AddEdges2Grid warning: {e}")
        elif kind == "cylinder":
            prop.AddCylinder(
                [_f32(x) for x in pr["start"]],
                [_f32(x) for x in pr["stop"]],
                float(pr.get("radius", 0.5)),
                priority=pri,
            )
        elif kind == "polygon":
            pts = pr.get("points") or []
            if len(pts) < 3:
                raise ValueError(f"Polygon primitive '{name}' needs >= 3 points")
            # CSXCAD expects columnar [xs, ys], NOT a list of [x, y] pairs
            xs = [_f32(pt[0]) for pt in pts]
            ys = [_f32(pt[1]) for pt in pts]
            length = float(pr.get("length") or 0.0)
            if length > 0:
                prop.AddLinPoly(
                    [xs, ys],
                    pr.get("axis", "z"),
                    _f32(pr.get("coord", 0)),
                    _f32(length),
                    priority=pri,
                )
            else:
                prop.AddPolygon(
                    [xs, ys],
                    pr.get("axis", "z"),
                    _f32(pr.get("coord", 0)),
                    priority=pri,
                )
        elif kind == "sphere":
            center = [_f32(x) for x in pr.get("center", [])]
            if len(center) != 3:
                raise ValueError(f"Sphere primitive '{name}' needs a 3D center")
            prop.AddSphere(center, float(pr.get("radius", 0.5)), priority=pri)
        elif kind == "stl":
            from app.csxcad_builder.triangle_mesh import add_triangle_mesh

            path = pr.get("path") or pr.get("mesh_path")
            stl_path = pr.get("stl_path") or ""
            if stl_path and not Path(stl_path).exists():
                stl_path = ""
            if (not path or not Path(path).exists()) and (not stl_path):
                raise FileNotFoundError(f"STL primitive '{name}' missing: {path}")
            tris = None
            if path and Path(path).exists() and Path(path).suffix.lower() == ".npy":
                tris = np.load(path)
            # CSXCAD has no AddMesh — use Polyhedron / PolyhedronReader
            add_triangle_mesh(
                prop,
                name=name,
                triangles=tris,
                stl_path=stl_path or None,
                mesh_path=path if path and Path(path).exists() else None,
                priority=pri,
            )
            if pr.get("edges2grid"):
                try:
                    FDTD.AddEdges2Grid(
                        dirs=pr.get("edges2grid", "xy"),
                        properties=prop,
                        metal_edge_res=pr.get("metal_edge_res"),
                    )
                except Exception as e:  # noqa: BLE001
                    _log(f"AddEdges2Grid warning (stl): {e}")
        else:
            raise ValueError(f"Unknown primitive kind: {kind}")

    # MSL ports need the generated grid lines -> build them after the mesh.
    deferred_msl_ports: list[tuple[int, object, list, list]] = []
    for idx, port in enumerate(project.ports, start=1):
        start = [_f32(x) for x in port.start]
        stop = [_f32(x) for x in port.stop]
        excite = 1.0 if port.excite_port else 0.0
        if port.type == "msl":
            deferred_msl_ports.append((idx, port, start, stop))
        elif port.type == "rect_waveguide":
            try:
                FDTD.AddRectWaveGuidePort(
                    idx,
                    start,
                    stop,
                    port.direction,
                    port.resistance,
                    excite=excite,
                )
            except Exception as e:  # noqa: BLE001
                _log(f"rect_waveguide port preview failed: {e}")
        else:
            FDTD.AddLumpedPort(
                idx,
                port.resistance,
                start,
                stop,
                port.direction,
                excite,
                priority=15,
            )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        setup = generate_mesh(
            CSX,
            project.mesh,
            project.boundary,
            targets,
            primitives_mesh_setup=primitives_mesh_setup,
            ports=project.ports,
        )

    # MSL ports need the grid: AddMSLPort(nr, metal, start, stop, prop_dir, exc_dir)
    from app.simulation.msl import resolve_msl_axes

    for idx, port, start, stop in deferred_msl_ports:
        try:
            prop_dir, exc_dir = resolve_msl_axes(port)
            metal = CSX.AddMetal(f"msl_{idx}_{port.name}_strip")
            FDTD.AddMSLPort(
                idx,
                metal,
                start,
                stop,
                prop_dir,
                exc_dir,
                excite=1.0 if port.excite_port else 0.0,
                priority=15,
            )
        except Exception as e:  # noqa: BLE001
            _log(f"MSL port '{port.name}' preview failed: {e}")

    report = validate_mesh(
        CSX,
        targets,
        ports=project.ports,
        max_grading=project.mesh.max_grading_ratio,
        coord_system=project.mesh.coord_system,
    )
    lines.extend(report_to_lines(report))
    lines.append("")
    lines.append("Mesh setup:")
    lines.append(json.dumps(setup, indent=2, default=str))

    # Keep the built mesh next to the project so a re-opened project shows it
    # without meshing again (separate from the run's <sim>/csx.xml).
    try:
        xml = export_csx_xml(CSX, work_dir / "csx.xml")
        lines.append(f"CSX XML: {xml}")
    except Exception as e:  # noqa: BLE001
        lines.append(f"(csx xml export failed: {e})")

    _log("Mesh build finished")
    return MeshBuildResult(lines=lines, csx=CSX, fdtd=FDTD)
