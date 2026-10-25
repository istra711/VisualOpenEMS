"""Triangle-mesh helpers for CSXCAD (this build has no ContinuousStructure.AddMesh)."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import numpy as np

from app.csxcad_builder.quantize import align_coordinates, f32


def axis_aligned_box_bounds(
    triangles: np.ndarray, *, tol: float = 1e-6, volume_rtol: float = 1e-6
) -> tuple[np.ndarray, np.ndarray] | None:
    """Bounds of the triangle soup when it is exactly one axis-aligned box.

    openEMS samples metal at the Yee nodes (grid lines in the normal
    direction). CSPrimBox treats points on its faces as inside, while a
    polyhedron ray-cast does not (asymmetric on faces), so axis-aligned
    solids are emitted as boxes for identical, well-defined metal edges.
    """
    tris = np.asarray(triangles, dtype=float)
    if tris.ndim != 3 or tris.shape[1:] != (3, 3) or len(tris) == 0:
        return None
    pts = tris.reshape(-1, 3)
    lo = pts.min(axis=0)
    hi = pts.max(axis=0)
    extent = hi - lo
    if np.any(extent <= 0.0):
        return None
    deviation = np.minimum(np.abs(pts - lo), np.abs(pts - hi))
    if float(deviation.max()) > tol:
        return None
    signed = np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2]))
    volume = abs(float(signed.sum())) / 6.0
    box_volume = float(np.prod(extent))
    if abs(volume - box_volume) > volume_rtol * box_volume:
        return None
    return lo, hi


def _poly_from_triangles(
    prop: Any, triangles: np.ndarray, priority: int = 10, *, aligned: bool = True
) -> Any:
    """Add triangles as a CSPrimPolyhedron (triangle faces) to a CSXCAD property."""
    # Same coordinate grid as boxes/ports/dumps (see quantize.align_coordinates):
    # a vertex and a box face that should be equal must be bit-identical.
    # Legacy mode (aligned=False) rounds to 0.1 um instead.
    tris = align_coordinates(triangles, aligned=aligned)
    if tris.ndim != 3 or tris.shape[-1] != 3 or tris.shape[1] != 3:
        raise ValueError(f"triangles must be (N,3,3), got {tris.shape}")
    ph = prop.AddPolyhedron(priority=priority)
    if hasattr(ph, "Reset"):
        ph.Reset()
    base = 0
    for t in tris:
        for p in t:
            ph.AddVertex(float(p[0]), float(p[1]), float(p[2]))
        ph.AddFace([base, base + 1, base + 2])
        base += 3
    return ph


def _load_triangles_from_path(path: str | Path) -> np.ndarray | None:
    path = Path(path)
    if not path.exists():
        return None
    if path.suffix.lower() == ".npy":
        try:
            return np.load(path)
        except Exception:  # noqa: BLE001
            return None
    if path.suffix.lower() in {".stl", ".obj"}:
        try:
            from app.freecad_io.freecad_mesh import load_stl_or_obj_triangles

            return load_stl_or_obj_triangles(path)
        except Exception:  # noqa: BLE001
            return None
    return None


def add_triangle_mesh(
    prop: Any,
    *,
    name: str = "mesh",
    triangles: np.ndarray | None = None,
    stl_path: str | Path | None = None,
    mesh_path: str | Path | None = None,
    priority: int = 10,
    align: bool = True,
) -> Any:
    """Attach a triangle mesh to a CSXCAD property without CSX.AddMesh.

    Axis-aligned box meshes are emitted as an openEMS box (identical metal
    edges on the mesh lines); everything else goes through an STL file via
    PolyhedronReader.

    openEMS only simulates PolyhedronReader (file based) primitives — an
    in-memory AddPolyhedron is reported as "Unused primitive" and the geometry
    is silently skipped. So the STL is the primary path; an .stl is written
    next to the .npy when only the triangle array is given.

    ``align`` (default True) puts the vertices on the float32 grid that boxes,
    ports, dumps and the NF2FF box already use, so "equal" coordinates are
    bit-identical; ``False`` restores the legacy 0.1 um decimal rounding
    (``MeshSpec.align_coordinates``).

    Returns the created primitive.
    """
    stl = Path(stl_path) if stl_path else None
    npy = Path(mesh_path) if mesh_path else None

    tris = triangles
    if tris is None:
        src = npy or stl
        if src is not None and Path(src).exists():
            tris = _load_triangles_from_path(src)
    have_tris = tris is not None and len(np.asarray(tris)) > 0
    if have_tris:
        # One grid for all sources: boxes/ports/dumps use f32() (see
        # quantize.align_coordinates); idempotent, so the STL write, the
        # box-bounds check and the fallback below all see the same values.
        tris = align_coordinates(tris, aligned=align)

    # 0) Axis-aligned box mesh -> openEMS box (exact metal edges, see
    #    axis_aligned_box_bounds). Geometry still comes from FreeCAD.
    if have_tris:
        bounds = axis_aligned_box_bounds(np.asarray(tris))
        if bounds is not None:
            lo, hi = bounds
            print(f"mesh '{name}' is an axis-aligned box -> openEMS AddBox")
            return prop.AddBox(
                start=[f32(float(v)) for v in lo],
                stop=[f32(float(v)) for v in hi],
                priority=priority,
            )

    # 1) STL file via PolyhedronReader (what openEMS actually uses)
    stl_file = stl if (stl is not None and stl.suffix.lower() == ".stl") else None
    if stl_file is None and npy is not None:
        stl_file = npy.with_suffix(".stl")
    if stl_file is not None:
        stale = not stl_file.exists()
        if not stale and npy is not None and npy.exists():
            stale = npy.stat().st_mtime > stl_file.stat().st_mtime + 1.0
        if stale and have_tris:
            try:
                write_binary_stl(stl_file, np.asarray(tris, dtype=float))
            except OSError:
                stl_file = None
        if stl_file is not None and stl_file.exists():
            try:
                prim = _add_polyhedron_reader(prop, stl_file, priority=priority)
            except Exception:  # noqa: BLE001
                prim = None
            if prim is not None:
                return prim

    # 2) Last resort: in-memory polyhedron (CSXCAD displays it, openEMS does
    #    not simulate it — never silently fall back to this for a run)
    if have_tris:
        print(
            f"WARNING: triangle mesh '{name}' has no readable STL "
            f"(stl_path={stl}, mesh_path={npy}); openEMS ignores in-memory "
            "Polyhedron primitives - this geometry will NOT be simulated"
        )
        return _poly_from_triangles(
            prop, np.asarray(tris), priority=priority, aligned=align
        )

    raise FileNotFoundError(
        f"Triangle mesh '{name}' unavailable (stl={stl}, mesh_path={npy}, triangles={'yes' if triangles is not None else 'no'})"
    )


def _add_polyhedron_reader(prop: Any, stl_file: Path, *, priority: int) -> Any | None:
    """CSPrimPolyhedronReader for an STL file, or None when it holds no faces."""
    prim = prop.AddPolyhedronReader(str(stl_file), priority=priority)
    if hasattr(prim, "GetNumFaces") and prim.GetNumFaces() == 0:
        if hasattr(prim, "SetFilename"):
            prim.SetFilename(str(stl_file))
        if hasattr(prim, "SetFileType"):
            prim.SetFileType(1)  # STL
        if hasattr(prim, "ReadFile"):
            prim.ReadFile()
        if hasattr(prim, "GetNumFaces") and prim.GetNumFaces() == 0:
            return None
    return prim


def write_binary_stl(path: str | Path, triangles: np.ndarray) -> Path:
    """Write triangles to a binary STL file (flat triangle soup, own normals)."""
    tris = np.asarray(triangles, dtype=float)
    if tris.ndim != 3 or tris.shape[1:] != (3, 3):
        raise ValueError(f"triangles must be (N,3,3), got {tris.shape}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lens = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lens, out=np.zeros_like(normals), where=lens > 0)
    with open(path, "wb") as fh:
        fh.write(b"\0" * 80)
        fh.write(struct.pack("<I", len(tris)))
        for i, t in enumerate(tris):
            fh.write(struct.pack("<3f", *normals[i]))
            for p in t:
                fh.write(struct.pack("<3f", float(p[0]), float(p[1]), float(p[2])))
            fh.write(struct.pack("<H", 0))
    return path


def mesh_primitives_on_csx(csx: Any, prop_name: str) -> int:
    """Debug helper: count primitives on a property by name."""
    try:
        props = csx.GetPropertiesByName(prop_name)
        total = 0
        for p in props:
            total += int(p.GetQtyPrimitives())
        return total
    except Exception:  # noqa: BLE001
        return -1
