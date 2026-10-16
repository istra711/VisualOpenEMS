"""Tessellate FreeCAD CAD (FCStd/STEP/STP) into openEMS triangle meshes.

openEMS/CSXCAD simulate surfaces as triangle meshes (AddMesh), not as
BREP STEP solids. This module converts FreeCAD geometry to per-object
STL + .npy triangle arrays via FreeCADCmd.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.winproc import hidden_console_kwargs


@dataclass
class CadMeshObject:
    name: str
    format: str
    source_path: str
    stl_path: str = ""
    npy_path: str = ""
    bounds_min: list[float] | None = None
    bounds_max: list[float] | None = None
    triangle_count: int = 0
    notes: str = ""
    # Native openEMS primitive (kind=box/cylinder/sphere/polygon + params)
    # instead of a tessellated triangle mesh.
    primitive: dict | None = None


@dataclass
class CadMeshResult:
    objects: list[CadMeshObject] = field(default_factory=list)
    ports: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    freecad_cmd: str = ""
    ok: bool = False
    log: str = ""


_FREECAD_TESS_CODE = r"""
import json
import os
import struct
import sys
from pathlib import Path

import FreeCAD as App
import Part
import numpy as np

SRC = Path(r"__SRC__")
OUT = Path(r"__OUT__")
DEFL = float(__DEFL__)

OUT.mkdir(parents=True, exist_ok=True)

__PORT_HELPER__

def _tris_from_shape(shape, defl):
    try:
        verts, faces = shape.tessellate(defl)
    except Exception as e:
        print("tessellate failed:", e)
        return None
    if not faces:
        return None
    arr = []
    for face in faces:
        if len(face) < 3:
            continue
        pts = [verts[i] for i in face]
        for k in range(1, len(pts) - 1):
            p0, p1, p2 = pts[0], pts[k], pts[k + 1]
            arr.append([[p0.x, p0.y, p0.z], [p1.x, p1.y, p1.z], [p2.x, p2.y, p2.z]])
    if not arr:
        return None
    return np.asarray(arr, dtype=float)

def _write_stl(tris, path):
    n = len(tris)
    header = b"FreeCAD OpenEMS tessellation"
    with open(path, "wb") as f:
        f.write(header.ljust(80, b" ")[:80])
        f.write(struct.pack("<I", n))
        for t in tris:
            ax, ay, az = t[1][0] - t[0][0], t[1][1] - t[0][1], t[1][2] - t[0][2]
            bx, by, bz = t[2][0] - t[0][0], t[2][1] - t[0][1], t[2][2] - t[0][2]
            nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
            norm = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            f.write(struct.pack("<3f", nx / norm, ny / norm, nz / norm))
            for p in t:
                f.write(struct.pack("<3f", float(p[0]), float(p[1]), float(p[2])))
            f.write(struct.pack("<H", 0))

def _open_doc(src: Path):
    suf = src.suffix.lower()
    if suf in (".step", ".stp"):
        doc = App.newDocument("cad_import")
        try:
            import Import
            Import.insert(str(src), doc.Name)
        except Exception:
            shape = Part.Shape()
            shape.read(str(src))
            o = doc.addObject("Part::Feature", src.stem)
            o.Shape = shape
        return doc
    if suf == ".fcstd":
        return App.open(str(src))
    return None


def _axis_aligned(vec):
    # unit axis vector: one component ~+-1, the rest ~0
    a = sorted([abs(vec.x), abs(vec.y), abs(vec.z)])
    return a[2] > 1 - 1e-6 and a[1] < 1e-6


def _unit_axis(vec):
    n = (vec.x * vec.x + vec.y * vec.y + vec.z * vec.z) ** 0.5
    if n <= 0:
        return None
    v = App.Vector(vec.x / n, vec.y / n, vec.z / n)
    # canonical sign (first non-zero component positive)
    for c in (v.x, v.y, v.z):
        if abs(c) > 1e-9:
            if c < 0:
                v = App.Vector(-v.x, -v.y, -v.z)
            break
    return v


def _wire_points_2d(wire, axis_idx):
    # World vertices of a planar closed wire, projected to the 2D plane
    # perpendicular to axis_idx (cyclic: z->(x,y), x->(y,z), y->(z,x)).
    edges = list(wire.Edges)
    if len(edges) < 3:
        return None
    # chain the edges (BRep edge order is not guaranteed)
    try:
        cur = edges[0].Vertexes[1].Point
        first = edges[0].Vertexes[0].Point
    except Exception:
        return None
    pts = [first, cur]
    used = {0}
    while len(used) < len(edges):
        nxt = None
        for k, e in enumerate(edges):
            if k in used:
                continue
            try:
                a, b = e.Vertexes[0].Point, e.Vertexes[1].Point
            except Exception:
                continue
            if a.distanceToPoint(cur) < 1e-6:
                nxt = b
            elif b.distanceToPoint(cur) < 1e-6:
                nxt = a
            else:
                continue
            used.add(k)
            break
        if nxt is None:
            return None  # open or broken chain
        cur = nxt
        pts.append(cur)
    if len(pts) < 4:
        return None
    if pts[0].distanceToPoint(pts[-1]) < 1e-6:
        pts.pop()  # closed: drop duplicate end vertex
    if len(pts) < 3:
        return None
    dims = [(axis_idx + 1) % 3, (axis_idx + 2) % 3]
    out = []
    for p in pts:
        c = (p.x, p.y, p.z)
        out.append([float(c[dims[0]]), float(c[dims[1]])])
    return out


def _analytic_prim(solid):
    # Native openEMS primitive (sphere/box/cylinder/polygon) or None.
    # Shape-based (works for parametric Part:: primitives AND imported STEP
    # solids). Conservative: every geometric check must pass, otherwise the
    # caller tessellates the solid to an STL mesh as before.
    import math
    try:
        faces = list(solid.Faces)
        bb = solid.BoundBox
        vol = float(solid.Volume)
    except Exception:
        return None
    if vol <= 0 or not faces:
        return None

    # --- sphere: one spherical face, bbox == 2r cube ------------------------
    if len(faces) == 1 and type(faces[0].Surface).__name__ == "Sphere":
        r = float(getattr(faces[0].Surface, "Radius", 0.0) or 0.0)
        if r <= 0:
            return None
        ref = 4.0 / 3.0 * math.pi * r ** 3
        if abs(vol - ref) > 1e-6 * ref:
            return None
        if (
            abs(bb.XLength - 2 * r) > 1e-6 * r
            or abs(bb.YLength - 2 * r) > 1e-6 * r
            or abs(bb.ZLength - 2 * r) > 1e-6 * r
        ):
            return None
        return {
            "kind": "sphere",
            "center": [
                0.5 * (bb.XMin + bb.XMax),
                0.5 * (bb.YMin + bb.YMax),
                0.5 * (bb.ZMin + bb.ZMax),
            ],
            "radius": r,
        }

    # --- axis-aligned box: 6 axis-aligned planes, volume == bbox volume -----
    if len(faces) == 6:
        aligned = True
        for f in faces:
            if type(f.Surface).__name__ != "Plane" or not _axis_aligned(f.Surface.Axis):
                aligned = False
                break
        if aligned:
            ref = bb.XLength * bb.YLength * bb.ZLength
            if ref > 0 and abs(vol - ref) <= 1e-6 * ref:
                return {
                    "kind": "box",
                    "start": [float(bb.XMin), float(bb.YMin), float(bb.ZMin)],
                    "stop": [float(bb.XMax), float(bb.YMax), float(bb.ZMax)],
                }

    # --- full circular cylinder: 3 faces (side + 2 centered caps) ----------
    if len(faces) == 3:
        side = None
        caps = []
        ok = True
        for f in faces:
            tn = type(f.Surface).__name__
            if tn == "Cylinder" and side is None:
                side = f
            elif tn == "Plane":
                caps.append(f)
            else:
                ok = False
                break
        if ok and side is not None and len(caps) == 2:
            srf = side.Surface
            r = float(getattr(srf, "Radius", 0.0) or 0.0)
            if r > 0:
                c0 = caps[0].CenterOfMass
                c1 = caps[1].CenterOfMass
                d = App.Vector(c1.x - c0.x, c1.y - c0.y, c1.z - c0.z)
                L = d.Length
                if L > 0:
                    ax = _unit_axis(srf.Axis)
                    dot = abs(d.dot(ax) / L) if ax else 0.0
                    ref = math.pi * r * r * L
                    # caps centered on the cylinder axis
                    rel = App.Vector(c0.x - srf.Center.x, c0.y - srf.Center.y, c0.z - srf.Center.z)
                    perp = (rel - ax * rel.dot(ax)).Length if ax else 1e9
                    if (
                        dot > 1 - 1e-9
                        and abs(vol - ref) <= 1e-6 * ref
                        and perp <= 1e-6 * max(1.0, r)
                    ):
                        return {
                            "kind": "cylinder",
                            "start": [float(c0.x), float(c0.y), float(c0.z)],
                            "stop": [float(c1.x), float(c1.y), float(c1.z)],
                            "radius": r,
                        }

    # --- extruded polygon: >=5 all-planar faces, 2 congruent parallel caps,
    #     all other faces perpendicular, volume == area * height --------------
    if len(faces) >= 5:
        planes = all(type(f.Surface).__name__ == "Plane" for f in faces)
        if planes:
            # try every face normal as the extrusion axis candidate — the
            # cap pair is not necessarily faces[0]/faces[1]
            for f0 in faces:
                d = _unit_axis(f0.Surface.Axis)
                if d is None or not _axis_aligned(d):
                    continue  # polygon schema needs an x/y/z axis
                par, perp_idx = [], []
                for i, f in enumerate(faces):
                    n = f.Surface.Axis
                    dp = abs(n.x * d.x + n.y * d.y + n.z * d.z)
                    if abs(dp - 1.0) < 1e-6:
                        par.append(i)
                    elif dp < 1e-6:
                        perp_idx.append(i)
                if len(par) != 2 or len(par) + len(perp_idx) != len(faces):
                    continue
                i0, i1 = par
                a0, a1 = faces[i0].Area, faces[i1].Area
                if a0 <= 0 or abs(a0 - a1) > 1e-6 * a0:
                    continue
                p0 = faces[i0].CenterOfMass
                p1 = faces[i1].CenterOfMass
                dv = App.Vector(p1.x - p0.x, p1.y - p0.y, p1.z - p0.z)
                h = abs(dv.dot(d))
                if h <= 0 or abs(vol - a0 * h) > 1e-6 * vol:
                    continue
                if len(faces[i0].Wires) != 1 or len(faces[i1].Wires) != 1:
                    continue  # holes not supported
                axis_idx = max(range(3), key=lambda k: abs((d.x, d.y, d.z)[k]))
                pts = _wire_points_2d(faces[i0].OuterWire, axis_idx)
                if not pts:
                    continue
                axis = "xyz"[axis_idx]
                # extrusion runs along +-d; elevation is the LOWER cap plane
                # (CSXCAD extrudes from elevation along the normal)
                elev = (p0.x, p0.y, p0.z)[axis_idx]
                if (
                    (d.x, d.y, d.z)[axis_idx] > 0
                    and (p1.x, p1.y, p1.z)[axis_idx] < elev
                ) or (
                    (d.x, d.y, d.z)[axis_idx] < 0
                    and (p1.x, p1.y, p1.z)[axis_idx] > elev
                ):
                    elev = (p1.x, p1.y, p1.z)[axis_idx]
                return {
                    "kind": "polygon",
                    "points": pts,
                    "axis": axis,
                    "coord": float(elev),
                    "length": float(h),
                }
    return None


doc = _open_doc(SRC)
results = []
if doc is None:
    print("__EMPTY__")
    sys.exit(0)

doc.recompute()
idx = 0
seen_labels = set()
for obj in doc.Objects:
    # Only real, visible solid geometry. doc.Objects also contains
    # construction helpers (Origin, axes, planes), container groups
    # (App::Part), port markers, and — for files edited in FreeCAD —
    # deleted "ghost" solids that stay in the document for Undo history.
    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id.startswith(("App::Origin", "App::Line", "App::Plane", "App::Part",
                           "App::DocumentObjectGroup", "App::Link")):
        continue
    label = getattr(obj, "Label", "") or getattr(obj, "Name", "")
    # Skip FreeCAD port marker objects (PORT_* points) — not solid geometry
    if str(label).startswith("PORT_") or str(getattr(obj, "Name", "")).startswith("PORT_"):
        continue
    # Skip objects deleted in the GUI but retained in the Undo history.
    # FreeCAD 1.x marks these with "Removing"/"Invalid" in State; older
    # versions expose no reliable flag, so we also drop empty/null shapes.
    state = list(getattr(obj, "State", []) or [])
    if any(s in ("Removing", "Invalid", "Error") for s in state):
        continue
    if not getattr(obj, "Visibility", True):
        continue
    shape = getattr(obj, "Shape", None)
    if shape is None:
        continue
    try:
        if shape.isNull():
            continue
    except Exception:
        continue
    try:
        solids = list(shape.Solids) if (hasattr(shape, "Solids") and shape.Solids) else [shape]
    except Exception:
        solids = [shape]
    if not solids:
        continue

    # Prefer the user-visible Label over the internal Name. FreeCAD's
    # internal Name ("Cylinder", "Part__Feature") is an implementation
    # detail that changes on copy/import; the Label ("Arm_links") is what
    # the user actually named the object and is stable across edits.
    base_label = str(label).strip() or str(getattr(obj, "Name", "")).strip()
    for si, solid in enumerate(solids):
        # Prefer a native openEMS primitive (sphere/box/cylinder/polygon)
        # over tessellation — exact geometry, cheaper FDTD mesh, and the
        # element stays editable in the GUI. Unknown shapes fall back to STL.
        prim = _analytic_prim(solid)
        tris = None
        if prim is None:
            tris = _tris_from_shape(solid, DEFL)
            if tris is None:
                continue
        idx += 1
        if len(solids) == 1:
            name = base_label
        else:
            name = f"{base_label}_{si+1}"
        # Disambiguate duplicate labels (FreeCAD allows them).
        if name in seen_labels:
            name = f"{name}_{idx}"
        seen_labels.add(name)
        bb = solid.BoundBox
        entry = {
            "name": name,
            "object": base_label,
            "freecad_name": getattr(obj, "Name", ""),
            "bounds_min": [float(bb.XMin), float(bb.YMin), float(bb.ZMin)],
            "bounds_max": [float(bb.XMax), float(bb.YMax), float(bb.ZMax)],
            "format": SRC.suffix.lower().lstrip("."),
        }
        if prim is not None:
            entry["primitive"] = prim
            entry["stl"] = ""
            entry["npy"] = ""
            entry["triangles"] = 0
        else:
            safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
            npy = OUT / f"{safe}.npy"
            stl = OUT / f"{safe}.stl"
            np.save(str(npy), tris)
            _write_stl(tris, str(stl))
            entry["stl"] = str(stl)
            entry["npy"] = str(npy)
            entry["triangles"] = int(len(tris))
        results.append(entry)

ports = []
try:
    ports = extract_openems_ports(doc)
except Exception as e:
    print("port extract failed:", e)

print("__JSON_START__")
print(json.dumps({"objects": results, "ports": ports}))
print("__JSON_END__")
"""


def tessellate_cad_to_meshes(
    path: Path | str,
    freecad_cmd: Path | str,
    out_dir: Path | str,
    linear_deflection: float = 0.15,
    timeout: int = 180,
) -> CadMeshResult:
    """Convert FCStd/STEP/STP into per-object STL + npy meshes via FreeCADCmd."""
    path = Path(path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result = CadMeshResult(freecad_cmd=str(freecad_cmd))
    if not path.exists():
        result.warnings.append(f"File not found: {path}")
        return result
    cmd = Path(freecad_cmd) if freecad_cmd else None
    if cmd is None or not cmd.exists():
        result.warnings.append(
            f"FreeCADCmd not found: {freecad_cmd} — cannot tessellate {path.suffix}"
        )
        return result

    fmt = path.suffix.lower().lstrip(".")
    if fmt in {"stl", "obj"}:
        result.warnings.append(
            f".{fmt} is already a mesh format — load triangles directly (no FreeCAD tessellation)."
        )
        return result
    if fmt not in {"step", "stp", "fcstd"}:
        result.warnings.append(f"Unsupported CAD format for tessellation: .{fmt}")
        return result

    from app.freecad_io.port_marks import freecad_extract_ports_code, parse_ports_payload

    code = (
        _FREECAD_TESS_CODE.replace("__SRC__", str(path).replace("\\", "/"))
        .replace("__OUT__", str(out_dir).replace("\\", "/"))
        .replace("__DEFL__", repr(float(linear_deflection)))
        .replace("__PORT_HELPER__", freecad_extract_ports_code())
    )
    # FreeCADCmd -c can crash on large scripts — write a real file instead
    script_path = out_dir / "_openems_tessellate.py"
    script_path.write_text(code, encoding="utf-8")

    env = os.environ.copy()
    env["FRECAD_MESH_SOURCE"] = str(path)
    try:
        proc = subprocess.run(
            [
                str(cmd),
                "-c",
                "import os, runpy\n"
                f"runpy.run_path(r'{script_path.as_posix()}', run_name='__main__')",
            ],
            cwd=str(out_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            **hidden_console_kwargs(),
        )
    except subprocess.TimeoutExpired as e:
        result.warnings.append(f"FreeCADCmd tessellation timed out: {e}")
        return result

    result.log = (proc.stdout or "") + ("\n" + (proc.stderr or "") if proc.stderr else "")
    payload: Any = None
    if "__JSON_START__" in result.log:
        try:
            chunk = result.log.split("__JSON_START__", 1)[1].split("__JSON_END__", 1)[0]
            payload = json.loads(chunk.strip())
        except Exception as e:  # noqa: BLE001
            result.warnings.append(f"Could not parse FreeCAD mesh JSON: {e}")
    if payload is None:
        result.warnings.append(
            f"FreeCADCmd tessellation produced no mesh JSON.\nLog tail:\n{result.log[-1500:]}"
        )
        return result

    # New format: {"objects":[…], "ports":[…]}; old format: list of objects
    if isinstance(payload, dict):
        objects_payload = payload.get("objects") or []
        result.ports = parse_ports_payload(payload)
    else:
        objects_payload = payload
        result.ports = parse_ports_payload(objects_payload)

    for item in objects_payload:
        # NOTE: Path("") == Path(".") (the cwd) — empty strings must stay None
        npy_raw = str(item.get("npy") or "").strip()
        stl_raw = str(item.get("stl") or "").strip()
        npy = Path(npy_raw) if npy_raw else None
        stl = Path(stl_raw) if stl_raw else None
        prim = item.get("primitive") or None
        if prim is not None and not isinstance(prim, dict):
            prim = None
        obj = CadMeshObject(
            name=str(item.get("name") or (npy.stem if npy else "object")),
            format=fmt,
            source_path=str(path),
            stl_path=str(stl) if stl is not None and stl.exists() else "",
            npy_path=str(npy) if npy is not None and npy.exists() else "",
            bounds_min=item.get("bounds_min"),
            bounds_max=item.get("bounds_max"),
            triangle_count=int(item.get("triangles") or 0),
            notes=f"tessellated from {path.name}",
            primitive=prim,
        )
        if prim is None and not obj.npy_path and not obj.stl_path:
            result.warnings.append(f"Mesh files missing for object {obj.name}")
            continue
        # Load triangles for bounds fallback / immediate use
        if obj.npy_path and Path(obj.npy_path).exists():
            try:
                tris = np.load(obj.npy_path)
                if tris.size:
                    if not obj.bounds_min:
                        obj.bounds_min = tris.reshape(-1, 3).min(axis=0).tolist()
                    if not obj.bounds_max:
                        obj.bounds_max = tris.reshape(-1, 3).max(axis=0).tolist()
                    obj.triangle_count = int(len(tris))
            except Exception as e:  # noqa: BLE001
                result.warnings.append(f"Failed to load npy for {obj.name}: {e}")
        result.objects.append(obj)

    result.ok = len(result.objects) > 0
    return result


def analytic_prim_dict(
    obj: CadMeshObject,
    assignments: list[Any],
    fmt: str,
    src: str,
) -> dict:
    """Primitive dict for a CadMeshObject detected as a native openEMS
    primitive (box/cylinder/sphere/polygon); material/priority from the
    Geometry assignments (copper/10 when unassigned)."""
    pd = dict(obj.primitive or {})
    kind = str(pd.pop("kind", "") or "")
    mat = "copper"
    pri = 10
    for a in assignments:
        if a.object_name == obj.name and a.role != "ignore":
            mat = a.material or ("copper" if a.role == "pec" else a.role)
            pri = int(a.priority)
    prim: dict = {"kind": kind, "name": obj.name}
    prim.update(pd)
    # bbox start/stop for the viewer slice rect (sphere/polygon params carry
    # no bbox of their own)
    prim.setdefault("start", list(obj.bounds_min or [0, 0, 0]))
    prim.setdefault("stop", list(obj.bounds_max or [0, 0, 0]))
    prim["material"] = mat
    prim["priority"] = pri
    prim["source_format"] = fmt
    prim["source_file"] = str(src)
    return prim


def load_stl_or_obj_triangles(path: Path | str) -> np.ndarray | None:
    """Read STL/OBJ triangles (N,3,3)."""
    path = Path(path)
    try:
        from app.freecad_io.importer import _read_obj, _read_stl
    except Exception:
        return None
    if path.suffix.lower() == ".stl":
        return _read_stl(path)
    if path.suffix.lower() == ".obj":
        return _read_obj(path)
    return None
