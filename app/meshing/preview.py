"""Matplotlib-based mesh line preview + geometry bbox extraction (no GUI dependency)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


def mesh_lines_from_csx(csx) -> dict[str, np.ndarray]:
    grid = csx.GetGrid()
    out = {}
    for d in "xyz":
        try:
            out[d] = np.asarray(grid.GetLines(d), dtype=float)
        except Exception:  # noqa: BLE001
            out[d] = np.asarray([], dtype=float)
    return out


def mesh_cells_from_lines(lines: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (cell_midpoints, cell_sizes) from sorted line coordinates."""
    arr = np.sort(np.asarray(lines, dtype=float))
    if arr.size < 2:
        return np.asarray([]), np.asarray([])
    mids = 0.5 * (arr[:-1] + arr[1:])
    sizes = np.diff(arr)
    return mids, sizes


@dataclass
class PrimitiveInfo:
    name: str
    kind: str  # "Metal", "Material", "LumpedElement", ...
    start: tuple[float, float, float]
    stop: tuple[float, float, float]
    priority: int = 0
    color: tuple[float, float, float, float] = (0.8, 0.5, 0.2, 0.7)
    #: CSXCAD primitive type id (1 = box, 5 = cylinder, …) and cylinder radius,
    #: so viewers can draw the true cross-section (circle) instead of the AABB
    prim_type: int = 0
    radius: float = 0.0
    #: relative permittivity of the property (1.0 for metals / air boxes)
    epsilon: float = 1.0
    #: polygon / linpoly only: the in-plane outline points, the normal axis
    #: ("x" | "y" | "z"), the elevation of the polygon plane and — for an
    #: extruded ``AddLinPoly`` — the extrusion length. Viewers use these to
    #: draw the true outline instead of the AABB (a hexagon used to appear as
    #: a rectangle).
    poly_points: tuple[tuple[float, float], ...] = ()
    poly_axis: str = ""
    poly_coord: float = 0.0
    poly_length: float = 0.0


def iter_primitive_infos(csx, coord_system: str = "cartesian") -> list[PrimitiveInfo]:
    """Extract AABB + metadata for every primitive in the CSX.

    ``coord_system="cylindrical"`` derives the extent via
    ``app.meshing.polar.polar_primitive_bbox`` — CSXCAD's ``GetBoundBox``
    is only meaningful on a cartesian grid (cylinders report a bogus
    negative-r box on a cylindrical one).
    """
    infos: list[PrimitiveInfo] = []
    polar = coord_system == "cylindrical"
    if polar:
        from app.meshing.polar import polar_primitive_bbox
    try:
        qty = csx.GetQtyProperties()
    except Exception:  # noqa: BLE001
        return infos
    # CSProperties::PropertyType bit values (CSXCAD)
    _type_map = {
        1: "UNKNOWN",
        2: "Material",
        4: "Metal",
        8: "Excitation",
        16: "ProbeBox",
        32: "ResBox",
        64: "DumpBox",
        8192: "LumpedElement",
        16384: "ConductingSheet",
        16388: "ConductingSheet",
    }
    for i in range(qty):
        try:
            prop = csx.GetProperty(i)
        except Exception:  # noqa: BLE001
            continue
        try:
            ptype = prop.GetType()
            pname = prop.GetName() or f"prop_{i}"
        except Exception:  # noqa: BLE001
            ptype, pname = 0, f"prop_{i}"
        # Prefer human-readable GetTypeString()
        kind_name = None
        try:
            s = prop.GetTypeString()
            if s:
                kind_name = s.decode() if isinstance(s, bytes) else str(s)
        except Exception:  # noqa: BLE001
            kind_name = None
        if not kind_name:
            kind_name = _type_map.get(ptype, f"Type{ptype}")
        try:
            n_prims = prop.GetQtyPrimitives()
        except Exception:  # noqa: BLE001
            n_prims = 0
        for j in range(n_prims):
            try:
                prim = prop.GetPrimitive(j)
                if polar:
                    bbox = polar_primitive_bbox(prim)
                else:
                    bbox = np.asarray(prim.GetBoundBox(), dtype=float)
                if bbox is None or bbox.shape != (2, 3) or not np.all(np.isfinite(bbox)):
                    continue
                start = tuple(float(v) for v in bbox[0])
                stop = tuple(float(v) for v in bbox[1])
                prio = int(prim.GetPriority()) if hasattr(prim, "GetPriority") else 0
                ptype_id = 0
                radius = 0.0
                try:
                    if hasattr(prim, "GetType"):
                        ptype_id = int(prim.GetType())
                    if hasattr(prim, "GetRadius"):
                        radius = float(prim.GetRadius())
                except Exception:  # noqa: BLE001
                    ptype_id, radius = 0, 0.0
                eps_val = 1.0
                try:
                    prop_obj = prim.GetProperty()
                    if hasattr(prop_obj, "GetMaterialProperty"):
                        eps_val = float(prop_obj.GetMaterialProperty("epsilon"))
                except Exception:  # noqa: BLE001
                    eps_val = 1.0
                # Polygon (7) / LinPoly (8): keep the outline so the Mesh
                # Viewer can draw the real shape instead of its bounding box.
                poly_points: tuple[tuple[float, float], ...] = ()
                poly_axis = ""
                poly_coord = 0.0
                poly_length = 0.0
                if ptype_id in (7, 8):
                    try:
                        xs, ys = prim.GetCoords()
                        pts = tuple(
                            (float(u), float(v)) for u, v in zip(xs, ys)
                        )
                        if len(pts) >= 3:
                            poly_points = pts
                            poly_axis = "xyz"[int(prim.GetNormDir())]
                            poly_coord = float(prim.GetElevation())
                            if ptype_id == 8 and hasattr(prim, "GetLength"):
                                poly_length = max(0.0, float(prim.GetLength()))
                    except Exception:  # noqa: BLE001
                        poly_points = ()
            except Exception:  # noqa: BLE001
                continue
            infos.append(
                PrimitiveInfo(
                    name=f"{pname}#{j}",
                    kind=kind_name,
                    start=start,
                    stop=stop,
                    priority=prio,
                    prim_type=ptype_id,
                    radius=radius,
                    epsilon=eps_val,
                    poly_points=poly_points,
                    poly_axis=poly_axis,
                    poly_coord=poly_coord,
                    poly_length=poly_length,
                )
            )
    return infos


def default_color_for(kind: str, name: str) -> tuple[float, float, float, float]:
    """Heuristic RGBA for a primitive kind / name."""
    k = (kind or "").lower()
    n = (name or "").lower()
    if "metal" in k or "copper" in n or "pec" in n or "ground" in n:
        return (0.85, 0.55, 0.2, 0.75)
    if "material" in k or "substrate" in n or "dielectric" in n or "rogers" in n:
        return (0.2, 0.45, 0.8, 0.55)
    if "lumped" in k or "port" in n or "resist" in n:
        return (0.9, 0.2, 0.2, 0.85)
    if "excit" in k or "excite" in n:
        return (0.2, 0.85, 0.3, 0.85)
    if "probe" in k or "dump" in k:
        return (0.7, 0.2, 0.85, 0.5)
    if "conduct" in k:
        return (0.9, 0.75, 0.2, 0.7)
    return (0.6, 0.6, 0.6, 0.5)


def get_sim_area(csx) -> np.ndarray | None:
    """Return [[xmin,ymin,zmin],[xmax,ymax,zmax]] or None."""
    try:
        area = np.asarray(csx.GetGrid().GetSimArea(), dtype=float)
        if area.shape == (2, 3) and np.all(np.isfinite(area)) and np.any(area[1] > area[0]):
            return area
    except Exception:  # noqa: BLE001
        pass
    return None


def primitives_outside_domain(
    infos: list[PrimitiveInfo],
    area: np.ndarray | None,
    tol: float | None = None,
) -> list[tuple[PrimitiveInfo, int, float, str]]:
    """Return [(info, axis_index, overflow, side)] for primitives sticking out.

    ``area`` is the FDTD domain as [[xmin,ymin,zmin],[xmax,ymax,zmax]].
    ``side`` is "min" or "max" (the domain face that is violated).
    Tolerance defaults to 1e-6 of the largest domain span, so primitives
    that merely touch the domain boundary are not reported.
    """
    if area is None or not infos:
        return []
    try:
        lo = np.asarray(area[0], dtype=float)
        hi = np.asarray(area[1], dtype=float)
    except Exception:  # noqa: BLE001
        return []
    if lo.shape != (3,) or hi.shape != (3,):
        return []
    if not (np.all(np.isfinite(lo)) and np.all(np.isfinite(hi)) and np.any(hi > lo)):
        return []
    if tol is None:
        tol = max(1e-12, 1e-6 * float(np.max(hi - lo)))
    out: list[tuple[PrimitiveInfo, int, float, str]] = []
    for info in infos:
        try:
            blo = np.asarray(info.start, dtype=float)
            bhi = np.asarray(info.stop, dtype=float)
        except Exception:  # noqa: BLE001
            continue
        if blo.shape != (3,) or bhi.shape != (3,):
            continue
        if not (np.all(np.isfinite(blo)) and np.all(np.isfinite(bhi))):
            continue
        below = lo - blo
        above = bhi - hi
        over = np.maximum(below, above)
        j = int(np.argmax(over))
        if over[j] > tol:
            side = "min" if below[j] >= above[j] else "max"
            out.append((info, j, float(over[j]), side))
    return out


def is_diagnostic_kind(kind: str) -> bool:
    """True for prims that only record/observe (dump/probe/res box)."""
    k = (kind or "").lower()
    return any(tok in k for tok in ("probe", "dump", "resbox", "res_box"))


def export_csx_xml(csx, out_path: Path | str) -> Path:
    """Write CSX to XML for AppCSXCAD 3D viewing."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    csx.Write2XML(str(out_path))
    return out_path


def save_mesh_preview(
    csx,
    out_path: Path | str,
    title: str = "Mesh lines",
    coord_system: str = "cartesian",
) -> Path:
    """Save mesh line plot to PNG.

    Uses Figure + FigureCanvasAgg directly — never calls matplotlib.use()
    and never imports pyplot. Safe to call from a QtAgg GUI process.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    lines = mesh_lines_from_csx(csx)
    labels = ("ρ", "φ", "z") if coord_system == "cylindrical" else ("x", "y", "z")
    fig = Figure(figsize=(12, 4))
    FigureCanvasAgg(fig)
    axes = fig.subplots(1, 3)
    for ax, d, lbl in zip(axes, "xyz", labels):
        arr = lines[d]
        if arr.size == 0:
            ax.set_title(f"{lbl}: empty")
            continue
        cells = np.diff(np.sort(arr))
        ax.plot(np.sort(arr)[:-1], cells, ".", ms=3)
        ax.set_xlabel(f"{lbl} position")
        ax.set_ylabel("cell size")
        ax.set_title(f"{lbl}: {arr.size} lines")
        ax.grid(True, alpha=0.3)
    fig.suptitle(title)
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    fig.clear()
    return out_path
