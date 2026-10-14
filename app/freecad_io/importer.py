"""Geometry import from FreeCAD-exported formats (STEP/STL/OBJ/FCStd)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class ImportedObject:
    name: str
    format: str
    path: str
    # triangle soup if available (N, 3, 3)
    triangles: np.ndarray | None = None
    # axis-aligned bounds (min, max) in model units if known
    bounds_min: list[float] | None = None
    bounds_max: list[float] | None = None
    notes: str = ""
    source_path: str = ""


@dataclass
class ImportResult:
    objects: list[ImportedObject] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    converted_from_fcstd: str = ""


def _read_stl(path: Path) -> np.ndarray | None:
    try:
        from stl import mesh as np_stl_mesh  # numpy-stl
    except ImportError:
        np_stl_mesh = None

    if np_stl_mesh is not None:
        m = np_stl_mesh.Mesh.from_file(str(path))
        return np.asarray(m.vectors, dtype=float)

    # minimal binary STL reader
    data = path.read_bytes()
    if len(data) < 84:
        return None
    n = int(np.frombuffer(data, dtype="<u4", count=1, offset=80)[0])
    expected = 84 + n * 50
    if expected > len(data):
        # maybe ASCII
        text = data.decode("utf-8", errors="ignore")
        if "facet" not in text.lower():
            return None
        verts: list[list[float]] = []
        for line in text.splitlines():
            parts = line.strip().split()
            if len(parts) >= 4 and parts[0].lower() == "vertex":
                verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        if len(verts) < 3:
            return None
        arr = np.asarray(verts, dtype=float)
        return arr.reshape(-1, 3, 3)

    tris = np.zeros((n, 3, 3), dtype=float)
    for i in range(n):
        off = 84 + i * 50 + 12  # skip normal
        tris[i] = np.frombuffer(data, dtype="<f4", count=9, offset=off).reshape(3, 3)
    return tris.astype(float)


def _read_obj(path: Path) -> np.ndarray | None:
    verts: list[list[float]] = []
    faces: list[tuple[int, int, int]] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("v "):
            parts = line.split()
            verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
        elif line.startswith("f "):
            parts = line.split()[1:]
            idx = [int(p.split("/")[0]) - 1 for p in parts]
            for i in range(1, len(idx) - 1):
                faces.append((idx[0], idx[i], idx[i + 1]))
    if not verts or not faces:
        return None
    v = np.asarray(verts, dtype=float)
    tris = np.asarray([[v[a], v[b], v[c]] for a, b, c in faces], dtype=float)
    return tris


def _read_step_bounds(path: Path) -> tuple[list[float] | None, list[float] | None, str]:
    """Best-effort STEP bounds without CAD kernel (text scan of CARTESIAN_POINT)."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        return None, None, f"STEP read error: {e}"
    pts: list[list[float]] = []
    # OCCT/FreeCAD: CARTESIAN_POINT('',(0.,0.,0.)); — allow trailing parens/semicolon
    for raw in text.splitlines():
        if "CARTESIAN_POINT" not in raw:
            continue
        start = raw.find("(")
        if start < 0:
            continue
        # numeric group is the last (...) that contains only numbers/commas/spaces
        candidates: list[str] = []
        depth = 0
        buf_start = None
        for i, ch in enumerate(raw[start:], start=start):
            if ch == "(":
                depth += 1
                buf_start = i
            elif ch == ")":
                if buf_start is not None and depth >= 1:
                    candidates.append(raw[buf_start + 1 : i])
                depth -= 1
                buf_start = None
        inner = ""
        for cand in candidates:
            parts = cand.replace(",", " ").split()
            if len(parts) >= 3:
                inner = cand
        if not inner:
            continue
        nums = inner.replace(",", " ").split()
        vals: list[float] = []
        ok = True
        for n in nums[:3]:
            try:
                vals.append(float(n.rstrip(".,")))
            except ValueError:
                ok = False
                break
        if ok and len(vals) >= 3:
            pts.append(vals[:3])
    if not pts:
        return None, None, "STEP: no CARTESIAN_POINT found (binary STEP not supported)"
    arr = np.asarray(pts, dtype=float)
    return arr.min(axis=0).tolist(), arr.max(axis=0).tolist(), ""


def convert_fcstd_to_step(fcstd: Path, out_step: Path, freecad_cmd: Path | str) -> Path:
    freecad_cmd = Path(freecad_cmd)
    if not freecad_cmd.exists():
        raise FileNotFoundError(f"FreeCADCmd not found: {freecad_cmd}")
    out_step.parent.mkdir(parents=True, exist_ok=True)
    # FreeCADCmd -c script
    script = (
        "import FreeCAD, Import, sys\n"
        f"d = FreeCAD.open(r'{fcstd.as_posix()}')\n"
        f"Import.export(d.Objects, r'{out_step.as_posix()}')\n"
        "FreeCAD.closeDocument(d.Name)\n"
        "print('FCSTD_EXPORT_OK')\n"
    )
    proc = subprocess.run(
        [str(freecad_cmd), "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if not out_step.exists():
        raise RuntimeError(
            f"FCStd conversion failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return out_step


def load_geometry_file(
    path: Path | str,
    freecad_cmd: Path | str | None = None,
    work_dir: Path | str | None = None,
) -> ImportResult:
    """Load a geometry file and return named objects with optional triangle data."""
    path = Path(path)
    result = ImportResult()
    if not path.exists():
        result.warnings.append(f"File not found: {path}")
        return result

    fmt = path.suffix.lower().lstrip(".")
    obj_name = path.stem
    imported = ImportedObject(
        name=obj_name, format=fmt, path=str(path), source_path=str(path)
    )

    if fmt == "fcstd":
        if freecad_cmd is None:
            result.warnings.append(
                "FCStd requires FreeCADCmd path in Settings; export STEP/STL from FreeCAD instead."
            )
            result.objects.append(imported)
            return result
        work = Path(work_dir) if work_dir else path.parent
        out_step = work / f"{path.stem}_from_fcstd.step"
        try:
            convert_fcstd_to_step(path, out_step, freecad_cmd)
            result.converted_from_fcstd = str(out_step)
            imported.notes = f"Converted to {out_step.name}"
            path = out_step
            fmt = "step"
            imported.format = "step"
            imported.path = str(out_step)
        except Exception as e:  # noqa: BLE001
            result.warnings.append(str(e))
            result.objects.append(imported)
            return result

    if fmt in {"stl"}:
        tris = _read_stl(path)
        if tris is None:
            result.warnings.append(f"Could not parse STL: {path}")
        else:
            imported.triangles = tris
            imported.bounds_min = tris.reshape(-1, 3).min(axis=0).tolist()
            imported.bounds_max = tris.reshape(-1, 3).max(axis=0).tolist()
    elif fmt in {"obj"}:
        tris = _read_obj(path)
        if tris is None:
            result.warnings.append(f"Could not parse OBJ: {path}")
        else:
            imported.triangles = tris
            imported.bounds_min = tris.reshape(-1, 3).min(axis=0).tolist()
            imported.bounds_max = tris.reshape(-1, 3).max(axis=0).tolist()
    elif fmt in {"step", "stp"}:
        bmin, bmax, warn = _read_step_bounds(path)
        imported.bounds_min = bmin
        imported.bounds_max = bmax
        if warn:
            result.warnings.append(warn)
        imported.notes = (
            "STEP bounds estimated from CARTESIAN_POINT; assign primitives or use FreeCADCmd+mesh for exact solids."
        )
    else:
        result.warnings.append(f"Unsupported geometry format: .{fmt}")

    result.objects.append(imported)
    return result


def overall_bounds(objects: list[ImportedObject]) -> tuple[list[float] | None, list[float] | None]:
    mins: list[list[float]] = []
    maxs: list[list[float]] = []
    for o in objects:
        if o.bounds_min and o.bounds_max:
            mins.append(o.bounds_min)
            maxs.append(o.bounds_max)
        elif o.triangles is not None:
            t = o.triangles.reshape(-1, 3)
            mins.append(t.min(axis=0).tolist())
            maxs.append(t.max(axis=0).tolist())
    if not mins:
        return None, None
    return (
        np.min(np.asarray(mins, dtype=float), axis=0).tolist(),
        np.max(np.asarray(maxs, dtype=float), axis=0).tolist(),
    )
