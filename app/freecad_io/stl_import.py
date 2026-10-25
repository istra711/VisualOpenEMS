"""Import FreeCAD-exported triangle meshes (STL / OBJ / export manifest / folder).

FreeCAD exports one STL per solid (`stl/<obj>.stl` from the model scripts, or
`<base>_<obj>.stl` + `openems_export.json` from examples/freecad_export_for_openems.py).
This module resolves any of those sources into per-object mesh entries with the
.npy triangle arrays openEMS (CSXCAD Polyhedron) needs, so a whole FreeCAD
document can be imported as simulation meshes in one step.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.freecad_io.freecad_mesh import CadMeshObject

MESH_SUFFIXES = {".stl", ".obj"}
MANIFEST_NAMES = {"openems_export.json", "openems_meshes.json"}


@dataclass
class StlImportResult:
    objects: list[CadMeshObject] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ok: bool = False


def is_stl_manifest(path: Path | str) -> bool:
    """True for a FreeCAD export manifest that lists per-object mesh files."""
    p = Path(path)
    if p.suffix.lower() != ".json" or not p.exists():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    items = data.get("objects") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return False
    for entry in items:
        if isinstance(entry, str) and Path(entry).suffix.lower() in MESH_SUFFIXES:
            return True
        if isinstance(entry, dict) and (
            Path(str(entry.get("stl") or entry.get("path") or "")).suffix.lower() in MESH_SUFFIXES
        ):
            return True
    return False


def _mesh_files_in_dir(folder: Path) -> list[Path]:
    files = [
        p
        for p in sorted(folder.iterdir())
        if p.is_file()
        and p.suffix.lower() in MESH_SUFFIXES
        and not p.name.startswith("_")
        and not p.name.startswith(".")
    ]
    return files


def _load_triangles(path: Path) -> np.ndarray | None:
    from app.freecad_io.freecad_mesh import load_stl_or_obj_triangles

    try:
        return load_stl_or_obj_triangles(path)
    except Exception:  # noqa: BLE001
        return None


def _unique_name(name: str, taken: set[str]) -> str:
    base = name or "mesh"
    if base not in taken:
        taken.add(base)
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    out = f"{base}_{n}"
    taken.add(out)
    return out


def load_stl_meshes(
    source: Path | str,
    out_dir: Path | str | None = None,
    write_npy: bool = True,
) -> StlImportResult:
    """Resolve STL/OBJ meshes from a file, folder or FreeCAD export manifest.

    Returns per-object entries with .npy triangle arrays ready for CSXCAD
    Polyhedron primitives. Objects keep their FreeCAD names (one entry per STL).
    """
    src = Path(source)
    result = StlImportResult()
    if not src.exists():
        result.warnings.append(f"Mesh source not found: {src}")
        return result

    base_dir = src if src.is_dir() else src.parent
    target = Path(out_dir) if out_dir else base_dir
    target.mkdir(parents=True, exist_ok=True)

    # --- collect (name, mesh_path, bounds) candidates -----------------------
    candidates: list[dict] = []
    if src.is_dir():
        files = _mesh_files_in_dir(src)
        if not files:
            result.warnings.append(f"No .stl/.obj files in {src}")
            return result
        candidates = [{"name": p.stem, "path": str(p)} for p in files]
    elif src.suffix.lower() == ".json":
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            result.warnings.append(f"Could not parse mesh manifest {src}: {e}")
            return result
        items = data.get("objects") if isinstance(data, dict) else data
        if not isinstance(items, list):
            result.warnings.append(f"Mesh manifest has no object list: {src}")
            return result
        for i, entry in enumerate(items):
            if isinstance(entry, str):
                entry = {"stl": entry}
            if not isinstance(entry, dict):
                continue
            raw = entry.get("stl") or entry.get("path") or entry.get("mesh") or ""
            if not raw:
                continue
            p = Path(str(raw))
            if not p.is_absolute():
                p = base_dir / p
            candidates.append(
                {
                    "name": str(entry.get("name") or p.stem or f"object_{i + 1}"),
                    "path": str(p),
                    "bounds_min": entry.get("bounds_min"),
                    "bounds_max": entry.get("bounds_max"),
                    "source": entry,
                }
            )
        if not candidates:
            result.warnings.append(f"No mesh entries in manifest: {src}")
            return result
    elif src.suffix.lower() in MESH_SUFFIXES:
        candidates = [{"name": src.stem, "path": str(src)}]
    else:
        result.warnings.append(f"Unsupported mesh source: {src.suffix or src.name}")
        return result

    # --- load triangles, write npy, build entries ---------------------------
    taken: set[str] = set()
    for cand in candidates:
        mesh_path = Path(cand["path"])
        if not mesh_path.exists():
            result.warnings.append(f"Mesh file missing for {cand['name']}: {mesh_path}")
            continue
        tris = _load_triangles(mesh_path)
        if tris is None or not len(tris):
            result.warnings.append(f"No triangles in {mesh_path}")
            continue
        # Keep the full tessellation here: the *coordinate grid* is decided when
        # the CSX is built (MeshSpec.align_coordinates -> align_coordinates()),
        # so one switch rules box / port / dump / triangle coordinates alike.
        # (Historically this rounded to 0.1 um to avoid sliver cells next to the
        # then-float64 box coordinates; f32() on the boxes superseded that.)
        tris = np.asarray(tris, dtype=np.float64)
        name = _unique_name(str(cand["name"]), taken)
        npy = ""
        if write_npy:
            npy_path = target / f"{name}.npy"
            try:
                np.save(str(npy_path), tris)
                npy = str(npy_path)
            except Exception as e:  # noqa: BLE001
                result.warnings.append(f"Could not write npy for {name}: {e}")
        bmin = cand.get("bounds_min")
        bmax = cand.get("bounds_max")
        if not bmin or not bmax:
            pts = tris.reshape(-1, 3)
            bmin = pts.min(axis=0).tolist()
            bmax = pts.max(axis=0).tolist()
        result.objects.append(
            CadMeshObject(
                name=name,
                format=mesh_path.suffix.lower().lstrip("."),
                source_path=str(mesh_path),
                stl_path=str(mesh_path) if mesh_path.suffix.lower() == ".stl" else "",
                npy_path=npy,
                bounds_min=[float(v) for v in bmin],
                bounds_max=[float(v) for v in bmax],
                triangle_count=int(len(tris)),
                notes=f"mesh import from {src.name}",
            )
        )

    result.ok = bool(result.objects)
    if not result.ok and not result.warnings:
        result.warnings.append(f"No usable meshes in {src}")
    return result


def stl_primitives_from_meshes(
    objects: list[CadMeshObject],
    *,
    material: str = "copper",
    priority: int = 10,
    source_file: str = "",
    source_format: str = "stl",
) -> list[dict]:
    """Build project primitives (kind=stl) from imported mesh entries."""
    prims: list[dict] = []
    for m in objects:
        mesh = m.npy_path or m.stl_path
        if not mesh or not Path(mesh).exists():
            continue
        prims.append(
            {
                "kind": "stl",
                "name": m.name,
                "material": material,
                "priority": int(priority),
                "path": mesh,
                "mesh_path": mesh,
                "stl_path": m.stl_path or "",
                "start": list(m.bounds_min or [0, 0, 0]),
                "stop": list(m.bounds_max or [0, 0, 0]),
                "source_format": source_format or m.format,
                "source_file": source_file or m.source_path,
            }
        )
    return prims
