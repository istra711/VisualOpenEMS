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
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
        npy = OUT / f"{safe}.npy"
        stl = OUT / f"{safe}.stl"
        np.save(str(npy), tris)
        _write_stl(tris, str(stl))
        bb = solid.BoundBox
        results.append({
            "name": name,
            "object": base_label,
            "freecad_name": getattr(obj, "Name", ""),
            "stl": str(stl),
            "npy": str(npy),
            "bounds_min": [float(bb.XMin), float(bb.YMin), float(bb.ZMin)],
            "bounds_max": [float(bb.XMax), float(bb.YMax), float(bb.ZMax)],
            "triangles": int(len(tris)),
            "format": SRC.suffix.lower().lstrip("."),
        })

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
        npy = Path(item.get("npy") or "")
        stl = Path(item.get("stl") or "")
        obj = CadMeshObject(
            name=str(item.get("name") or npy.stem),
            format=fmt,
            source_path=str(path),
            stl_path=str(stl) if stl.exists() else "",
            npy_path=str(npy) if npy.exists() else "",
            bounds_min=item.get("bounds_min"),
            bounds_max=item.get("bounds_max"),
            triangle_count=int(item.get("triangles") or 0),
            notes=f"tessellated from {path.name}",
        )
        if not obj.npy_path and not obj.stl_path:
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
