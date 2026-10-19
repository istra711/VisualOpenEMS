"""Generate a reproducible openEMS simulation script from a Project."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.models.project import Project


def _stl_primitive_from(
    project: Project,
    name: str,
    mesh: str,
    stl_path: str,
    bounds_min: list | None,
    bounds_max: list | None,
    fmt: str,
    src: str,
) -> dict:
    """kind=stl primitive for a meshed CAD/FreeCAD object (material from assignments)."""
    mat = "copper"
    pri = 10
    for a in project.assignments:
        if a.object_name == name and a.role != "ignore":
            mat = a.material or ("copper" if a.role == "pec" else a.role)
            pri = int(a.priority)
    return {
        "kind": "stl",
        "name": name,
        "material": mat,
        "priority": pri,
        "path": mesh,
        "mesh_path": mesh,
        "stl_path": stl_path or "",
        "start": list(bounds_min or [0, 0, 0]),
        "stop": list(bounds_max or [0, 0, 0]),
        "source_format": fmt,
        "source_file": src,
    }


def generate_run_script(
    project: Project,
    project_path: Path | str,
    work_dir: Path | str,
    primitives: list[dict] | None = None,
) -> Path:
    """Write project_run.py + companion JSON files into work_dir."""
    # Keep paths absolute (no symlink resolution): openEMS asserts
    # os.getcwd() == os.path.realpath(sim_path) after chdir().
    project_path = Path(os.path.abspath(os.fspath(project_path)))
    work_dir = Path(os.path.abspath(os.fspath(work_dir)))
    work_dir.mkdir(parents=True, exist_ok=True)
    out = work_dir / "project_run.py"
    from app.paths import app_root as resolve_app_root

    app_root = resolve_app_root()

    prims = list(primitives or [])
    # Analytic primitives (box/cylinder/...) fully define the geometry for
    # presets like the Yagi. Tessellating a reference STEP next to them would
    # add duplicate STL primitives — skip CAD meshing in that case.
    has_analytic = any(p.get("kind") not in (None, "stl") for p in prims)
    # If project references STEP/FCStd/STL geometry without mesh primitives, mesh it now
    try:
        if project.geometry_files and not has_analytic and not any(
            p.get("kind") == "stl" and (p.get("path") or p.get("mesh_path"))
            for p in prims
        ):
            from app.freecad_io.freecad_mesh import tessellate_cad_to_meshes

            mesh_dir = work_dir / "cad_meshes"
            mesh_dir.mkdir(parents=True, exist_ok=True)
            freecad_cmd = Path(project.freecad_cmd) if project.freecad_cmd else Path(
                r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe"
            )
            for gf in project.geometry_files:
                src = Path(gf.path)
                fmt = (gf.format or src.suffix.lower().lstrip(".")).lower()
                if not src.exists():
                    print(f"generate_run_script: geometry missing {src}")
                    continue
                if fmt in {"step", "stp", "fcstd"}:
                    print(f"generate_run_script: tessellating {src}")
                    res = tessellate_cad_to_meshes(src, freecad_cmd, mesh_dir)
                    from app.freecad_io.freecad_mesh import analytic_prim_dict

                    have = {p.get("name") for p in prims}
                    for obj in res.objects:
                        if obj.name in have:
                            continue
                        if obj.primitive:
                            # analytic shape (box/cylinder/sphere/polygon) —
                            # no STL needed
                            prims.append(
                                analytic_prim_dict(
                                    obj, project.assignments, fmt, str(src)
                                )
                            )
                            continue
                        npy = obj.npy_path or ""
                        if not npy or not Path(npy).exists():
                            continue
                        prims.append(
                            _stl_primitive_from(project, obj.name, npy, obj.stl_path or "",
                                                obj.bounds_min, obj.bounds_max, fmt, str(src))
                        )
                elif fmt in {"stl", "obj", "json"} or src.is_dir():
                    from app.freecad_io.stl_import import load_stl_meshes

                    print(f"generate_run_script: loading FreeCAD meshes {src}")
                    mesh_res = load_stl_meshes(src, out_dir=mesh_dir)
                    for w in mesh_res.warnings:
                        print(f"generate_run_script: WARNING: {w}")
                    for obj in mesh_res.objects:
                        prims.append(
                            _stl_primitive_from(project, obj.name, obj.npy_path or obj.stl_path,
                                                obj.stl_path, obj.bounds_min, obj.bounds_max,
                                                fmt, str(src))
                        )
                else:
                    print(f"generate_run_script: unsupported geometry format {fmt} ({src})")
    except Exception as e:  # noqa: BLE001
        print(f"generate_run_script: CAD tessellation warning: {e}")

    geometry_json = work_dir / "geometry_primitives.json"
    geometry_json.write_text(json.dumps(prims, indent=2), encoding="utf-8")

    project_json = work_dir / "project_copy.json"
    project_json.write_text(json.dumps(project.to_dict(), indent=2), encoding="utf-8")

    script = SCRIPT_TEMPLATE
    script = script.replace("__APP_ROOT__", str(app_root))
    script = script.replace("__WORK_DIR__", str(work_dir))
    script = script.replace("__PROJECT_JSON__", str(project_json))
    script = script.replace("__GEOM_JSON__", str(geometry_json))
    out.write_text(script, encoding="utf-8")
    return out


SCRIPT_TEMPLATE = r'''#!/usr/bin/env python3
# Auto-generated by FreeCAD OpenEMS GUI — reproducible simulation script
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

APP_ROOT = Path(os.path.abspath(r"__APP_ROOT__"))
WORK_DIR = Path(os.path.abspath(r"__WORK_DIR__"))
PROJECT_JSON = Path(os.path.abspath(r"__PROJECT_JSON__"))
GEOM_JSON = Path(os.path.abspath(r"__GEOM_JSON__"))

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
_EASY = Path(r"__APP_ROOT__") / "third_party" / "easyMesh4openEMS"
if _EASY.is_dir() and str(_EASY) not in sys.path:
    sys.path.insert(0, str(_EASY))

from app.models.project import Project
from app.meshing.easy_wrapper import enhance_csx, enhance_fdtd, apply_boundary, generate_mesh
from app.meshing.polar import ensure_polar_primitive
from app.meshing.rules import compute_targets
from app.meshing.bounds import nf2ff_box_center, nf2ff_center_offset
from app.meshing.validators import validate_mesh, report_to_lines
from app.postproc import fields as fld
from app.postproc import nf2ff as nf
from app.postproc import sparams as sp
from app.csxcad_builder.quantize import f32
from app.simulation.msl import resolve_msl_axes


def load_project() -> Project:
    return Project.load(PROJECT_JSON)


def load_primitives() -> list:
    if not GEOM_JSON.exists():
        return []
    return json.loads(GEOM_JSON.read_text(encoding="utf-8"))


def ensure_simulation_meshes(project: Project, work_dir: Path, prims: list) -> list:
    """Ensure STEP/FCStd geometry_files have triangle meshes for openEMS.

    openEMS cannot read BREP STEP directly — FreeCAD tessellates to STL/npy.
    If primitives already contain valid stl meshes, nothing is done.
    """
    import numpy as np

    def _valid_stl(p: dict) -> bool:
        if p.get("kind") != "stl":
            return False
        path = p.get("path") or p.get("mesh_path") or ""
        return bool(path) and Path(path).exists()

    # Analytic primitives fully define the geometry — do not tessellate a
    # reference STEP/FCStd next to them (that duplicates the structure).
    has_analytic = any(p.get("kind") not in (None, "stl") for p in prims)
    if prims and (has_analytic or any(_valid_stl(p) for p in prims)):
        return prims

    mesh_dir = work_dir / "cad_meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)
    freecad_cmd = Path(project.freecad_cmd) if project.freecad_cmd else Path(
        r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe"
    )
    if not freecad_cmd.exists():
        try:
            from app.settings import AppSettings

            freecad_cmd = Path(AppSettings.load().freecad_cmd)
        except Exception:
            pass

    existing = {p.get("name") for p in prims}
    for gf in project.geometry_files:
        src = Path(gf.path)
        if not src.exists():
            print(f"WARNING: geometry file missing: {src}")
            continue
        fmt = (gf.format or src.suffix.lower().lstrip(".")).lower()
        if fmt in {"step", "stp", "fcstd"}:
            try:
                from app.freecad_io.freecad_mesh import analytic_prim_dict, tessellate_cad_to_meshes

                print(f"Tessellating FreeCAD CAD for simulation: {src}")
                res = tessellate_cad_to_meshes(src, freecad_cmd, mesh_dir)
                for w in res.warnings:
                    print("WARNING:", w)
                for obj in res.objects:
                    if obj.name in existing:
                        print(f"  primitive already present: {obj.name}")
                        continue
                    if obj.primitive:
                        # analytic shape (box/cylinder/sphere/polygon) — no STL
                        existing.add(obj.name)
                        prims.append(
                            analytic_prim_dict(obj, project.assignments, fmt, str(src))
                        )
                        print(
                            f"  analytic primitive: {obj.name} "
                            f"({obj.primitive.get('kind')})"
                        )
                        continue
                    npy = obj.npy_path or ""
                    if not npy or not Path(npy).exists():
                        print(f"WARNING: no mesh for {obj.name}")
                        continue
                    existing.add(obj.name)
                    # material from assignment if present
                    mat = "copper"
                    pri = 10
                    for a in project.assignments:
                        if a.object_name == obj.name and a.role != "ignore":
                            mat = a.material or ("copper" if a.role == "pec" else a.role)
                            pri = int(a.priority)
                    print(f"  mesh primitive: {obj.name} ({obj.triangle_count} tris) -> {npy}")
                    prims.append(
                        {
                            "kind": "stl",
                            "name": obj.name,
                            "material": mat,
                            "priority": pri,
                            "path": npy,
                            "mesh_path": npy,
                            "stl_path": obj.stl_path or "",
                            "start": list(obj.bounds_min or [0, 0, 0]),
                            "stop": list(obj.bounds_max or [0, 0, 0]),
                            "source_format": fmt,
                            "source_file": str(src),
                        }
                    )
            except Exception as e:  # noqa: BLE001
                print(f"ERROR: FreeCAD tessellation failed for {src}: {e}")
        elif fmt in {"stl", "obj", "json"} or src.is_dir():
            # FreeCAD per-object meshes: single STL/OBJ, export manifest or stl/ folder
            try:
                from app.freecad_io.stl_import import load_stl_meshes

                print(f"Loading FreeCAD meshes for simulation: {src}")
                mesh_res = load_stl_meshes(src, out_dir=mesh_dir)
                for w in mesh_res.warnings:
                    print("WARNING:", w)
                for obj in mesh_res.objects:
                    mesh = obj.npy_path or obj.stl_path
                    if not mesh or not Path(mesh).exists():
                        print(f"WARNING: no mesh for {obj.name}")
                        continue
                    if obj.name in existing:
                        print(f"  mesh primitive already present: {obj.name}")
                        continue
                    existing.add(obj.name)
                    # material from assignment if present
                    mat = "copper"
                    pri = 10
                    for a in project.assignments:
                        if a.object_name == obj.name and a.role != "ignore":
                            mat = a.material or ("copper" if a.role == "pec" else a.role)
                            pri = int(a.priority)
                    print(f"  mesh primitive: {obj.name} ({obj.triangle_count} tris) -> {mesh}")
                    prims.append(
                        {
                            "kind": "stl",
                            "name": obj.name,
                            "material": mat,
                            "priority": pri,
                            "path": mesh,
                            "mesh_path": mesh,
                            "stl_path": obj.stl_path or "",
                            "start": list(obj.bounds_min or [0, 0, 0]),
                            "stop": list(obj.bounds_max or [0, 0, 0]),
                            "source_format": fmt or obj.format,
                            "source_file": str(src),
                        }
                    )
            except Exception as e:  # noqa: BLE001
                print(f"ERROR: mesh import failed for {src}: {e}")
        else:
            print(f"WARNING: unsupported geometry format for simulation: {fmt} ({src})")

    # Drop primitives that point to missing mesh files
    out = []
    for p in prims:
        if p.get("kind") == "stl":
            path = p.get("path") or p.get("mesh_path") or ""
            if not path or not Path(path).exists():
                print(f"Dropping stl primitive with missing mesh: {p.get('name')}")
                continue
        out.append(p)
    return out


def get_or_create_prop(CSX, mat_name: str, materials):
    # Always create via CSX.Add*: easyMesh enhance_csx may pre-create empty
    # properties whose AddBox is unusable; wrappers register primitives.
    for m in materials:
        if m.name != mat_name:
            continue
        if m.kind == "pec":
            return CSX.AddMetal(m.name)
        if m.kind == "conducting_sheet":
            return CSX.AddConductingSheet(m.name, conductivity=m.kappa)
        if m.kind == "dielectric":
            kwargs = {"epsilon": m.epsilon}
            if m.kappa:
                kwargs["kappa"] = m.kappa
            return CSX.AddMaterial(m.name, **kwargs)
        return CSX.AddMaterial(m.name, epsilon=1.0)
    # Fallback for primitives that reference a material NOT in project.materials
    # (e.g. the importer uses "copper"). These are metals — creating them as a
    # dielectric (epsilon=1) loses the PEC and easyMesh's metal-edge refinement
    # (the port then no longer sits on a mesh line). Mirror builder._prop_for.
    mate = str(mat_name or "copper")
    if mate in ("copper", "pec", "ground") or "copper" in mate or "ground" in mate:
        return CSX.AddMetal(mate)
    return CSX.AddMaterial(mate, epsilon=1.0)


def get_domain_box(CSX):
    """Return (lo, hi) of the FDTD domain as floats, or None if no mesh lines yet."""
    from app.meshing.preview import get_sim_area

    area = get_sim_area(CSX)
    if area is None:
        return None
    return [float(v) for v in area[0]], [float(v) for v in area[1]]


def dedupe_grid_lines(CSX, tol: float = 1e-12) -> None:
    """Remove exact-duplicate grid lines that create zero-size FDTD cells.

    Port ``edges2grid`` inserts the arm-axis line (x=0 / y=0) which collides with
    the mesh line already on that coordinate, leaving a zero-width cell that
    collapses the Courant timestep. Domain-boundary lines are never touched —
    they define the FDTD box edges.
    """
    import numpy as np

    grid = CSX.GetGrid()
    dom = get_domain_box(CSX)
    removed_total = 0
    for axis in range(3):
        raw = grid.GetLines(axis)
        if raw is None:
            continue
        vals = np.array(raw, dtype=float)
        if vals.size < 2:
            continue
        keep: list[float] = []
        removed = 0
        # domain edges are structural: never drop them
        dom_lo = dom[0][axis] if dom is not None else None
        dom_hi = dom[1][axis] if dom is not None else None
        for v in vals:
            if dom_lo is not None and (abs(v - dom_lo) < tol or abs(v - dom_hi) < tol):
                keep.append(float(v))
                continue
            if any(abs(v - k) < tol for k in keep):
                removed += 1
            else:
                keep.append(float(v))
        if removed:
            grid.SetLines(axis, np.array(keep, dtype=float))
            removed_total += removed
            print(f"  dedupe axis {axis}: removed {removed} zero-size cell(s), {len(keep)} lines left")
    if removed_total == 0:
        print("  dedupe: no zero-size cells found")


def clamp_box_into_domain(start, stop, dom_lo, dom_hi, pad=1.0):
    """Shrink [start, stop] so it stays strictly inside the FDTD domain.

    openEMS silently disables any dump/NF2FF box lying outside the field domain.
    Returns (start, stop) or None if the box cannot fit with the given pad.
    """
    out_start: list[float] = []
    out_stop: list[float] = []
    for i in range(3):
        lo = max(float(start[i]), float(dom_lo[i]) + pad)
        hi = min(float(stop[i]), float(dom_hi[i]) - pad)
        if lo > hi:
            return None
        out_start.append(lo)
        out_stop.append(hi)
    return out_start, out_stop


def main() -> int:
    project = load_project()
    # Mesh spec with the antenna-mode overrides applied (no-op when off): while
    # antenna_mode is on the reference wavelength is taken at f_max and the air
    # cell size is capped at wavelength(f_max)/density.
    mesh = project.effective_mesh()
    prims = load_primitives()
    # FreeCAD STEP/FCStd → triangle meshes (openEMS AddMesh) if needed
    prims = ensure_simulation_meshes(project, WORK_DIR, prims)

    if getattr(mesh, "is_cylindrical", False) and project.ports:
        # openEMS's LumpedPort is not cylindrical-mesh aware: its Capacity
        # computation returns NaN on a polar grid, which poisons the
        # excitation and the whole field (Energy ~nan). The cylindrical-aware
        # CoaxialPort exists only in openEMS >= 0.37; until the user upgrades
        # we refuse to run instead of producing garbage S-params.
        raise ValueError(
            "Ports are not supported in cylindrical (polar) mode with this "
            "openEMS build: the LumpedPort is not cylindrical-mesh aware and "
            "produces NaN fields (Capacity=nan). Remove all ports "
            "(free-response run with dumps) or switch Mesh Parameters -> "
            "Coordinate system to cartesian."
        )

    from openEMS import openEMS
    from CSXCAD import ContinuousStructure
    from openEMS.nf2ff import nf2ff as NF2FFBox

    f0 = project.mesh.target_frequency or 0.5 * (project.mesh.start_frequency + project.mesh.stop_frequency)
    fc = max(1e6, 0.5 * abs(project.mesh.stop_frequency - project.mesh.start_frequency))
    nr_ts = int(project.sim.max_timesteps or 100000)

    # CoordSystem=1 => cylindrical (r, alpha, z) operator; 0 = cartesian (default)
    _cs = 1 if mesh.is_cylindrical else 0
    FDTD = openEMS(NrTS=nr_ts, EndCriteria=project.sim.end_criteria, CoordSystem=_cs)
    FDTD.SetGaussExcite(f0, fc)

    CSX = ContinuousStructure(CoordSystem=_cs)
    FDTD.SetCSX(CSX)

    # Shared dict: easyMesh auto-registers primitives/ports when objects are added
    primitives_mesh_setup = {}
    properties_mesh_setup = {}

    # easyMesh wrappers BEFORE SetBoundaryCond and geometry
    CSX = enhance_csx(CSX, primitives_mesh_setup)
    FDTD = enhance_fdtd(FDTD, primitives_mesh_setup)

    targets = compute_targets(mesh, eps_max=1.0)
    apply_boundary(FDTD, project.boundary, mesh, targets)

    CSX.GetGrid().SetDeltaUnit(mesh.unit_to_meters())

    # Create materials ONCE and reuse the same CSX properties for primitives.
    # (Creating twice left empty Property #0..#2 placeholders in openEMS dumps.)
    props = {}
    for mat in project.materials:
        if mat.kind == "pec":
            props[mat.name] = CSX.AddMetal(mat.name)
        elif mat.kind == "conducting_sheet":
            props[mat.name] = CSX.AddConductingSheet(mat.name, conductivity=mat.kappa)
        elif mat.kind == "dielectric":
            kwargs = {"epsilon": mat.epsilon}
            if mat.kappa:
                kwargs["kappa"] = mat.kappa
            props[mat.name] = CSX.AddMaterial(mat.name, **kwargs)
        else:
            props[mat.name] = CSX.AddMaterial(mat.name, epsilon=1.0)

    for pr in prims:
        kind = pr.get("kind", "box")
        name = pr["name"]
        if project.mesh.is_cylindrical:
            ensure_polar_primitive(kind, name)
        mat = pr.get("material", "copper")
        pri = int(pr.get("priority", 10))
        if mat not in props:
            props[mat] = get_or_create_prop(CSX, mat, project.materials)
        prop = props[mat]
        if kind == "box":
            prop.AddBox(
                start=[f32(x) for x in pr["start"]],
                stop=[f32(x) for x in pr["stop"]],
                priority=pri,
            )
            if pr.get("edges2grid"):
                dirs = pr.get("edges2grid", "xy")
                try:
                    FDTD.AddEdges2Grid(dirs=dirs, properties=prop, metal_edge_res=pr.get("metal_edge_res"))
                except Exception as e:
                    print("AddEdges2Grid warning:", e)
        elif kind == "polygon":
            axis = pr.get("axis", "z")
            coord = f32(pr.get("coord", 0.0))
            pts = pr.get("points") or []
            if len(pts) < 3:
                raise ValueError(f"Polygon primitive '{name}' needs >= 3 points")
            # CSXCAD expects columnar [xs, ys], NOT a list of [x, y] pairs
            xs = [f32(pt[0]) for pt in pts]
            ys = [f32(pt[1]) for pt in pts]
            length = float(pr.get("length") or 0.0)
            if length > 0:
                # extruded solid prism (thickness = length)
                prop.AddLinPoly([xs, ys], axis, coord, f32(length), priority=pri)
            else:
                prop.AddPolygon([xs, ys], axis, coord, priority=pri)
        elif kind == "sphere":
            center = [f32(x) for x in pr.get("center", [])]
            if len(center) != 3:
                raise ValueError(f"Sphere primitive '{name}' needs a 3D center")
            prop.AddSphere(center, float(pr.get("radius", 0.5)), priority=pri)
        elif kind == "cylinder":
            prop.AddCylinder(
                [f32(x) for x in pr["start"]],
                [f32(x) for x in pr["stop"]],
                float(pr.get("radius", 0.5)),
                priority=pri,
            )
        elif kind == "stl":
            path = pr.get("path") or pr.get("mesh_path")
            stl_path = pr.get("stl_path") or ""
            if stl_path and not Path(stl_path).exists():
                stl_path = ""
            if (not path or not Path(path).exists()) and (not stl_path):
                raise ValueError(f"STL primitive '{name}' missing path: {path}")
            tris = None
            if path and Path(path).exists() and Path(path).suffix.lower() == ".npy":
                tris = np.load(path)
            from app.csxcad_builder.triangle_mesh import add_triangle_mesh

            # CSXCAD 0.6.x: no ContinuousStructure.AddMesh — Polyhedron instead
            mesh_prim = add_triangle_mesh(
                prop,
                name=name,
                triangles=tris,
                stl_path=stl_path or None,
                mesh_path=path if path and Path(path).exists() else None,
                priority=pri,
            )
            ntri = 0 if tris is None else len(tris)
            kind_txt = "axis-aligned box" if type(mesh_prim).__name__ == "CSPrimBox" else "polyhedron"
            print(f"Added mesh primitive '{name}' as openEMS {kind_txt} ({ntri} triangles)")
            if pr.get("edges2grid"):
                dirs = pr.get("edges2grid", "xy")
                try:
                    FDTD.AddEdges2Grid(
                        dirs=dirs, properties=prop, metal_edge_res=pr.get("metal_edge_res")
                    )
                except Exception as e:
                    print("AddEdges2Grid warning (stl):", e)
        else:
            raise ValueError("Unknown primitive kind " + str(kind))

    port_objects = []
    # MSL (microstrip) ports are built AFTER the mesh exists: AddMSLPort samples
    # the propagation-direction grid lines and needs >= 5 of them, and its
    # signature is AddMSLPort(nr, metal_prop, start, stop, prop_dir, exc_dir).
    deferred_msl = []
    for idx, port in enumerate(project.ports, start=1):
        start = [f32(x) for x in port.start]
        stop = [f32(x) for x in port.stop]
        if port.type == "lumped":
            pobj = FDTD.AddLumpedPort(
                idx,
                port.resistance,
                start,
                stop,
                port.direction,
                1.0 if port.excite_port else 0.0,
                priority=15,
                edges2grid=port.mesh_dirs if port.mesh_dirs else "xy",
            )
            port_objects.append((port, pobj))
        elif port.type == "msl":
            deferred_msl.append((idx, port, start, stop))
        elif port.type == "rect_waveguide":
            FDTD.AddRectWaveGuidePort(
                idx,
                start,
                stop,
                port.direction,
                port.resistance,
                excite=1.0 if port.excite_port else 0.0,
            )
        else:
            print("Unsupported port type:", port.type)

    if project.sim.field_dumps_enabled:
        for dump in project.dumps:
            if not dump.enabled:
                continue
            dump_type = {"E": 10, "H": 11, "J": 13, "Jtot": 13, "D": 14, "B": 15}.get(dump.quantity, 10)
            if dump.quantity == "SAR":
                dump_type = 20  # SAR lives in its own 20..22 range
            elif not dump.frequency_domain:
                dump_type = dump_type - 10
            props_kw = dict(
                file_type=int(getattr(dump, "file_type", 1) or 1),
                dump_mode=int(getattr(dump, "dump_mode", 1) or 1),
            )
            if dump.frequency_domain:
                freqs = getattr(dump, "frequencies", None) or [dump.frequency]
                props_kw["frequency"] = [float(f) for f in freqs]
            if any(int(s) != 1 for s in dump.sub_sampling):
                props_kw["sub_sampling"] = list(dump.sub_sampling)
            d = CSX.AddDump(dump.name, dump_type=dump_type, **props_kw)
            d.AddBox([f32(x) for x in dump.start], [f32(x) for x in dump.stop])

    print("Registered easyMesh primitives:", len(primitives_mesh_setup))
    # easyMesh can emit numpy RuntimeWarnings (mean of empty slice) — not fatal
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        setup = generate_mesh(
            CSX,
            mesh,
            project.boundary,
            targets,
            primitives_mesh_setup=primitives_mesh_setup,
            properties_mesh_setup=properties_mesh_setup,
            ports=project.ports,
        )
    print("Mesh setup:", json.dumps(setup, indent=2, default=str))

    dedupe_grid_lines(CSX)

    # Now that the mesh exists, add the deferred MSL ports. AddMSLPort adds the
    # microstrip metal strip (prop_dir x transverse) and needs the grid lines.
    for idx, port, start, stop in deferred_msl:
        try:
            prop_dir, exc_dir = resolve_msl_axes(port)
        except ValueError as e:
            print(f"ERROR: MSL port '{port.name}' skipped: {e}")
            continue
        metal = CSX.AddMetal(f"msl_{idx}_{port.name}_strip")
        pobj = FDTD.AddMSLPort(
            idx,
            metal,
            start,
            stop,
            prop_dir,
            exc_dir,
            excite=1.0 if port.excite_port else 0.0,
            priority=15,
        )
        port_objects.append((port, pobj))
        print(f"MSL port {idx} '{port.name}': prop_dir={prop_dir} exc_dir={exc_dir}")
    # Keep S-param indexing in port-number order (deferred MSL ports were added
    # last): compute_sparams_from_ports numbers the columns by list position.
    port_objects.sort(key=lambda po: getattr(po[1], "number", 0))

    report = validate_mesh(
        CSX,
        targets,
        ports=project.ports,
        max_grading=mesh.max_grading_ratio,
        coord_system=mesh.coord_system,
    )
    for line in report_to_lines(report):
        try:
            print(line)
        except UnicodeEncodeError:
            # e.g. rho/phi axis labels on a cp1252 Windows console
            print(line.encode("cp1252", errors="replace").decode("cp1252"))
    if not report.ok:
        print("ERROR: mesh validation failed — aborting simulation")
        return 2

    nf2ff_box = None
    domain = get_domain_box(CSX)
    if domain is not None:
        for dump in project.dumps:
            if not dump.enabled:
                continue
            lo = [min(float(dump.start[i]), float(dump.stop[i])) for i in range(3)]
            hi = [max(float(dump.start[i]), float(dump.stop[i])) for i in range(3)]
            if any(
                lo[i] < domain[0][i] - 1e-6 or hi[i] > domain[1][i] + 1e-6
                for i in range(3)
            ):
                print(
                    f"WARNING: dump '{dump.name}' box {dump.start} -> {dump.stop} is outside "
                    f"the FDTD domain {domain[0]} -> {domain[1]}; openEMS will disable it"
                )
    if project.sim.nf2ff_enabled and project.nf2ff.enabled and project.mesh.is_cylindrical:
        print("NF2FF skipped: far-field post-processing is not supported in "
              "cylindrical mode (S-params and field dumps still run)")
    if (
        project.sim.nf2ff_enabled
        and project.nf2ff.enabled
        and not project.mesh.is_cylindrical
    ):
        nf_start = [f32(x) for x in project.nf2ff.start]
        nf_stop = [f32(x) for x in project.nf2ff.stop]
        if domain is not None:
            clamped = clamp_box_into_domain(nf_start, nf_stop, domain[0], domain[1])
            if clamped is None:
                print(
                    f"WARNING: NF2FF box {nf_start} -> {nf_stop} does not fit into the FDTD "
                    f"domain {domain[0]} -> {domain[1]}; openEMS will disable it"
                )
            else:
                if any(
                    abs(a - b) > 1e-9
                    for a, b in zip(clamped[0] + clamped[1], nf_start + nf_stop)
                ):
                    print(f"NF2FF box clamped into FDTD domain: {nf_start} -> {nf_stop} => {clamped[0]} -> {clamped[1]}")
                nf_start, nf_stop = clamped
        # Snap all six box faces onto existing grid lines. openEMS expands
        # each dump face independently to the enclosing cell: a zero-extent
        # face snaps to the nearest line while a finite-extent face expands
        # outward.  If the box coordinates are not exactly on grid lines the
        # adjacent faces end up on different lines and the recording surface
        # has gaps -> leaked fields -> systematically underestimated Prad /
        # efficiency.  Snapping the corner coordinates to the grid makes all
        # six faces share the same lines, closing the surface.
        _grid = CSX.GetGrid()
        _snapped = False
        for _ny in range(3):
            _lines = np.asarray(_grid.GetLines(_ny), dtype=float)
            if _lines.size == 0:
                continue
            _s = float(_lines[int(np.argmin(np.abs(_lines - nf_start[_ny])))])
            _e = float(_lines[int(np.argmin(np.abs(_lines - nf_stop[_ny])))])
            if _s > _e:
                _s, _e = _e, _s
            if _s == _e:
                print(
                    f"WARNING: NF2FF box collapses in axis {_ny} after grid snap "
                    f"({_s}); NF2FF surface will be degenerate"
                )
            if _s != nf_start[_ny] or _e != nf_stop[_ny]:
                _snapped = True
            nf_start[_ny] = _s
            nf_stop[_ny] = _e
        if _snapped:
            print(f"NF2FF box snapped to grid lines: {nf_start} -> {nf_stop}")
        # The far-field sphere is centred on project.nf2ff.center; the box must be
        # centred on that same point, otherwise the surface integral is evaluated
        # off-centre and the pattern comes out distorted (a dipole went from
        # Dmax 2.1 / 78 deg to 3.3 dBi / 45 deg with center z=6.15). This is the
        # common stale-state bug after the old asymmetric "Fit". Snap to the box
        # centre — an off-centre reference is never wanted.
        _box_center = nf2ff_box_center(nf_start, nf_stop)
        _c = [float(x) for x in (getattr(project.nf2ff, "center", None) or [0.0, 0.0, 0.0])]
        _off = nf2ff_center_offset(nf_start, nf_stop, _c)
        if _off > 1e-6:
            print(
                f"WARNING: NF2FF center {_c} != box center {_box_center} "
                f"(off by {_off:.3g}); an off-centre reference distorts the far "
                f"field -> using the box center"
            )
            project.nf2ff.center = _box_center
        # Force all six faces: FDTD.CreateNF2FFBox derives directions/mirror from
        # the *domain* boundary types (PEC/PMC face -> skip + mirrored copy),
        # which is only correct for a box pinned to the domain edges — and the
        # installed openEMS build cannot override the derived directions.
        # Our box is explicitly placed (usually interior): build it directly with
        # every face recorded and no mirroring.
        nf2ff_box = NF2FFBox(
            CSX, "nf2ff", nf_start, nf_stop, directions=[True] * 6, mirror=[0] * 6
        )

    # Keep the meshed CSX in the project folder: when the project is re-opened
    # the GUI restores the Mesh Viewer (exact mesh + geometry) and the S-param
    # Viewer from the recorded paths. Written here so it exists for --mesh-only
    # builds too and survives a later FDTD failure/crash.
    try:
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        CSX.Write2XML(str(WORK_DIR / "csx.xml"))
        print("CSX saved:", WORK_DIR / "csx.xml")
    except Exception as e:  # noqa: BLE001
        print("WARNING: could not save CSX xml:", e)

    if "--mesh-only" in sys.argv[1:]:
        print("MESH ONLY: CSX + easyMesh build verified, skipping FDTD run")
        return 0

    # Project folder layout: raw openEMS outputs (et/ht, port dumps, nf2ff_*.h5)
    # live in WORK_DIR/simulation (FDTD.Run creates it itself), processed
    # results go to WORK_DIR/sparams, WORK_DIR/fields and WORK_DIR/nf2ff.
    out_dir = WORK_DIR / "simulation"
    out_dir.mkdir(parents=True, exist_ok=True)
    print("Running openEMS into", out_dir)
    run_kw: dict = {"verbose": 3, "cleanup": True}
    n_threads = int(getattr(project.sim, "num_threads", 0) or 0)
    if n_threads > 0:
        run_kw["numThreads"] = n_threads
        print(f"FDTD threads (numThreads) = {n_threads}")
    else:
        print("FDTD threads: auto (all cores)")
    # openEMS 0.0.36's Run() asserts os.getcwd() == os.path.realpath(sim_path):
    # a drive-letter case difference (getcwd returns 'c:' but realpath 'C:') or a
    # short/symlink name trips it (openEMS issues #113/#284). Passing the
    # realpath makes the chdir'd cwd match, so the assertion passes.
    FDTD.Run(os.path.realpath(str(out_dir)), **run_kw)
    print("Simulation finished (FDTD.Run complete)")

    # Evaluate S-params over the EXCITATION band only. Outside
    # [f_start, f_stop] the Gaussian pulse carries (almost) no energy, so
    # |S11| = |uf_ref| / |uf_inc| divides noise by ~zero and explodes
    # (|S11| >> 1) — that garbage dominated the S-param plots. The mesh is
    # sized for this band, so a resonance can only be trusted here anyway.
    freqs = np.linspace(project.mesh.start_frequency, project.mesh.stop_frequency, 201)
    s_table = {}
    written = {}
    # S-params are referenced to the port's own resistance (openEMS default when
    # CalcPort gets no ref_impedance): a 73 Ω port then yields a 73 Ω-referenced
    # S11. Use the excited port's R for the Touchstone header.
    _exc = next(
        (po for po in port_objects if getattr(po[0], "excite_port", False)),
        port_objects[0] if port_objects else None,
    )
    ref_z = float(getattr(_exc[0], "resistance", 50.0)) if _exc else 50.0
    try:
        s_table = sp.compute_sparams_from_ports(out_dir, port_objects, freqs)
        print(f"S-param reference impedance: {ref_z:g} ohm")
        print("S-param keys:", list(s_table.keys()))
        finite_ok = False
        for k, v in s_table.items():
            if k == "freq":
                continue
            arr = np.asarray(v, dtype=complex)
            if arr.size and np.any(np.isfinite(arr)):
                finite_ok = True
                break
        if not finite_ok:
            print("ERROR: S-param table has no finite values (CalcPort failed?)")
        written = sp.export_sparams(s_table, WORK_DIR / "sparams", basename="sparams", z0=ref_z)
        print("S-param outputs:", {k: str(v) for k, v in written.items()})
        touch = written.get("touchstone")
        if touch and Path(touch).exists():
            print("Wrote Touchstone:", touch)
        json_out = written.get("json")
        if not json_out or not Path(json_out).exists():
            print("ERROR: sparams.json was not written")
            return 4
    except Exception as e:
        print(f"S-param postproc FAILED: {e}")
        import traceback

        traceback.print_exc()
        return 3

    try:
        fld.plot_field_slices(out_dir, project.dumps, WORK_DIR / "fields")
    except Exception as e:
        print("Field postproc warning:", e)

    if nf2ff_box is not None:
        try:
            # auto: S11 minimum; count: N points over the mesh range; list: explicit
            f_eval = nf.resolve_eval_frequencies(project, s_table)
            result = nf.compute_and_export(
                out_dir, nf2ff_box, f_eval, project.nf2ff, WORK_DIR / "nf2ff"
            )
            print("NF2FF summary:", result.get("summary"))
        except Exception as e:
            print("NF2FF postproc warning:", e)

    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
