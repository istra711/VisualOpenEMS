"""Polar (cylindrical) mesh generator — v1.

Builds the r/alpha/z mesh lines for openEMS ``CoordSystem=1`` domains:

- axis 0 ``r``  : [0, r_max]            (the r=0 axis is always included)
- axis 1 ``a``  : [0, 2*pi]             (full turn => openEMS "closed Cylinder")
- axis 2 ``z``  : [z_lo, z_hi]          (geometry extent + air margins)

Angles are RADIANTS everywhere in the serializer/solver; the UI shows degrees.

v1 scope (verified by ``tests/cylindrical_spike.py``): analytic primitives
**box** (wedge in r/phi/z) and **cylinder**. STL/polyhedron/polygon imports
are rejected via :func:`ensure_polar_primitive`. easyMesh is bypassed
completely (it is cartesian-only); no fork changes are needed.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from app.csxcad_builder.quantize import f32
from app.meshing.rules import MeshTargets, air_margin_units
from app.models.project import BoundarySpec, MeshSpec, PortDef

TWO_PI = 2.0 * math.pi
AXIS_R, AXIS_A, AXIS_Z = 0, 1, 2
AXIS_NAMES = ("r", "a", "z")

#: primitive kinds supported in cylindrical mode (rest => clear error)
ALLOWED_PRIMITIVE_KINDS = {"box", "cylinder"}

#: CSXCAD primitive type ids (CSPrimitives.PrimitiveType) used as fallbacks
_TYPE_BOX = 1
_TYPE_CYLINDER = 5

# Values closer than this (relative) to an existing line are considered the
# same line — guards against float32 storage creating micro cells.
_SAME_LINE_EPS = 1e-6
#: A re-asserted line may not sit closer than this fraction of its cell.
_NUDGE_FRACTION = 0.25


def axis_labels(coord_system: str) -> tuple[str, str, str]:
    """Axis names for messages/UI: cartesian -> x/y/z, cylindrical -> r/a/z."""
    if coord_system == "cylindrical":
        return "\u03c1", "\u03c6", "z"
    return "x", "y", "z"


def ensure_polar_primitive(kind: str, name: str = "") -> None:
    """Raise a clear error for primitives that cylindrical mode cannot run."""
    if kind in ALLOWED_PRIMITIVE_KINDS:
        return
    what = f" '{name}'" if name else ""
    raise ValueError(
        f"Primitive kind '{kind}'{what} is not supported in cylindrical mode. "
        f"Allowed: {', '.join(sorted(ALLOWED_PRIMITIVE_KINDS))}. "
        "STL/FreeCAD imports, polygons, spheres and polyhedra need the "
        "Cartesian coordinate system (Mesh Parameters -> Coordinate system)."
    )


def _prim_type(prim: Any) -> int:
    try:
        return int(prim.GetType())
    except Exception:  # noqa: BLE001
        return -1


def polar_primitive_bbox(prim: Any) -> np.ndarray | None:
    """AABB of a primitive in mesh coordinates, shape (2, 3).

    Boxes report their exact wedge bbox via ``GetBoundBox`` (verified on a
    cylindrical grid). Cylinders do NOT: ``GetBoundBox`` returns a
    cartesian-style box with negative r/phi components, so the extent is
    derived from axis endpoints + radius instead. phi is always the full
    turn (the cylindrical domain never constrains phi).
    """
    ptype = _prim_type(prim)
    if ptype == _TYPE_CYLINDER:
        try:
            s = np.asarray(prim.GetStart(), dtype=float)
            e = np.asarray(prim.GetStop(), dtype=float)
            r = float(prim.GetRadius())
        except Exception:  # noqa: BLE001
            return None
        r_lo = max(0.0, min(s[AXIS_R], e[AXIS_R]) - r)
        r_hi = max(s[AXIS_R], e[AXIS_R]) + r
        z_lo = min(s[AXIS_Z], e[AXIS_Z])
        z_hi = max(s[AXIS_Z], e[AXIS_Z])
        return np.array([[r_lo, 0.0, z_lo], [r_hi, TWO_PI, z_hi]], dtype=float)
    try:
        bbox = np.asarray(prim.GetBoundBox(), dtype=float)
        if bbox.shape != (2, 3) or not np.all(np.isfinite(bbox)):
            return None
        return bbox
    except Exception:  # noqa: BLE001
        return None


def _collect_geometry(
    primitives_mesh_setup: dict | None, ports: list[PortDef]
) -> tuple[list[Any], set[float], set[float], set[float]]:
    """Split the registered setup into primitive objects + required lines.

    Returns ``(prims, r_lines, a_lines, z_lines)`` with the exact coordinates
    that MUST end up as mesh lines (primitive faces, port faces).
    """
    prims: list[Any] = []
    r_req: set[float] = {0.0}
    a_req: set[float] = {0.0, TWO_PI}
    z_req: set[float] = set()

    setup = primitives_mesh_setup or {}
    for key, hint in setup.items():
        if isinstance(key, str):
            continue  # e.g. 'boundary_conditions'
        if isinstance(hint, dict) and hint.get("is_port"):
            continue  # port coordinates come from the ports list
        prims.append(key)

    for prim in prims:
        bbox = polar_primitive_bbox(prim)
        if bbox is None:
            continue
        ptype = _prim_type(prim)
        if ptype == _TYPE_BOX:
            for i, req in ((AXIS_R, r_req), (AXIS_A, a_req), (AXIS_Z, z_req)):
                req.add(float(bbox[0][i]))
                req.add(float(bbox[1][i]))
        else:
            r_req.add(float(bbox[0][AXIS_R]))
            r_req.add(float(bbox[1][AXIS_R]))
            z_req.add(float(bbox[0][AXIS_Z]))
            z_req.add(float(bbox[1][AXIS_Z]))
            if ptype == _TYPE_CYLINDER:
                try:
                    # the phi of the cylinder axis (a full-turn cylinder
                    # contributes no phi constraint)
                    s = np.asarray(prim.GetStart(), dtype=float)
                    e = np.asarray(prim.GetStop(), dtype=float)
                    if abs(min(s[AXIS_R], e[AXIS_R])) > 1e-12 or abs(
                        max(s[AXIS_R], e[AXIS_R])
                    ) > 1e-12:
                        a_req.add(float(s[AXIS_A]))
                        a_req.add(float(e[AXIS_A]))
                except Exception:  # noqa: BLE001
                    pass

    for p in ports:
        # project_run.py quantizes port start/stop with f32() when it creates
        # the CSXCAD port box — mirror that here so the grid line lands
        # EXACTLY on the f32 value openEMS sees, otherwise the port misses
        # its cell by ~1e-8 and openEMS never excites it (NaN energy).
        for i, req in ((AXIS_R, r_req), (AXIS_A, a_req), (AXIS_Z, z_req)):
            req.add(float(f32(p.start[i])))
            req.add(float(f32(p.stop[i])))

    return prims, r_req, a_req, z_req


def _geometry_extent(
    prims: list[Any], ports: list[PortDef]
) -> tuple[float, float, float]:
    """(r_hi_geom, z_lo_geom, z_hi_geom) over primitives and ports."""
    r_hi = 0.0
    z_lo = math.inf
    z_hi = -math.inf
    for prim in prims:
        bbox = polar_primitive_bbox(prim)
        if bbox is None:
            continue
        r_hi = max(r_hi, float(bbox[1][AXIS_R]))
        z_lo = min(z_lo, float(bbox[0][AXIS_Z]))
        z_hi = max(z_hi, float(bbox[1][AXIS_Z]))
    for p in ports:
        for v in (p.start[AXIS_R], p.stop[AXIS_R]):
            r_hi = max(r_hi, float(v))
        for v in (p.start[AXIS_Z], p.stop[AXIS_Z]):
            z_lo = min(z_lo, float(v))
            z_hi = max(z_hi, float(v))
    if not math.isfinite(z_lo):
        z_lo, z_hi = 0.0, 0.0
    return float(r_hi), float(z_lo), float(z_hi)


def _resync_lines(grid, ny: int, required: set[float]) -> list[float]:
    """Re-assert required coordinates after smoothing, without micro cells.

    ``SmoothMeshLines`` may drop or merge lines (that is its job), so the
    primitive/port faces are restored afterwards:

    - coincident (within eps)  -> nothing to do
    - lands in the inner half of a cell -> NUDGE the nearer existing line
      onto the exact coordinate (shift <= _NUDGE_FRACTION of the cell)
    - otherwise -> insert a new line (both resulting pieces >= 25% of the
      original cell, so no dt-killing sliver appears — the spike lesson)

    Returns the final sorted line list (float64 python values).
    """
    lines = sorted({float(v) for v in np.asarray(grid.GetLines(ny), dtype=float)})
    for c in sorted({float(v) for v in required}):
        if not math.isfinite(c):
            continue
        if not lines:
            lines = [c]
            continue
        arr = np.asarray(lines, dtype=float)
        eps = _SAME_LINE_EPS * max(1.0, abs(c))
        j = int(np.argmin(np.abs(arr - c)))
        if abs(arr[j] - c) <= eps:
            continue
        k = int(np.searchsorted(arr, c))
        lo = float(arr[k - 1]) if k > 0 else float(arr[0]) - abs(c)
        hi = float(arr[k]) if k < len(arr) else float(arr[-1]) + abs(c)
        gap = hi - lo
        left, right = c - lo, hi - c
        if min(left, right) < _NUDGE_FRACTION * gap:
            # snap the nearer line onto the exact coordinate
            j = k - 1 if left < right else k
            j = max(0, min(j, len(lines) - 1))
            lines[j] = c
        else:
            lines.insert(max(0, min(k, len(lines))), c)
        lines = sorted({float(v) for v in lines})
    grid.SetLines(ny, lines)
    return lines


def generate_polar_mesh(
    CSX,
    mesh: MeshSpec,
    boundary: BoundarySpec,
    targets: MeshTargets,
    primitives_mesh_setup: dict | None = None,
    ports: list[PortDef] | None = None,
) -> dict[str, Any]:
    """Fill the (already cylindrical) CSX grid with r/a/z mesh lines.

    The caller must have created FDTD/CSX with ``CoordSystem=1`` and called
    ``SetDeltaUnit`` before. Returns a report dict (Mesh Report JSON).
    """
    grid = CSX.GetGrid()
    ports = list(ports or [])
    prims, r_req, a_req, z_req = _collect_geometry(primitives_mesh_setup, ports)
    r_hi_geom, z_lo_geom, z_hi_geom = _geometry_extent(prims, ports)

    if r_hi_geom <= 0.0:
        raise ValueError(
            "Cylindrical mesh: cannot size the domain — no primitive or port "
            "with a positive radius extent was found."
        )

    margins = air_margin_units(mesh, boundary.air_margin, targets)
    # polar roles (cartesian list order = [r-, r+, phi-, phi+, z-, z+]):
    # r- is the r=0 axis (no margin possible), phi-/phi+ are unused (full
    # 2*pi closed cylinder), r+ and z-/+ get the air margin.
    # NOTE: indices are into the 6-entry cartesian-order list — do NOT use
    # AXIS_* (0/1/2) here, z lives at indices 4/5.
    r_hi = r_hi_geom + float(margins[1])
    z_lo = z_lo_geom - float(margins[4])
    z_hi = z_hi_geom + float(margins[5])

    notes: list[str] = []
    in_margins = boundary.air_margin
    if any(
        m is not None
        and not isinstance(m, str)
        and float(m) > 0.0
        for m in (in_margins[0], in_margins[2], in_margins[3])
    ):
        notes.append("r-/phi air margins are unused in cylindrical mode")
    if r_hi <= 0.0 or z_hi <= z_lo:
        raise ValueError("Cylindrical mesh: degenerate domain extent")

    res = float(min(targets.target_cell_units, targets.max_cell_units))
    if res <= 0.0:
        raise ValueError("Cylindrical mesh: non-positive cell resolution")
    ratio = max(1.05, float(mesh.max_grading_ratio or 2.0))
    # alpha step so that the ARC at the outer radius stays <= res
    res_alpha = res / r_hi

    r_req.add(0.0)
    r_req.add(float(r_hi))
    a_req.add(0.0)
    a_req.add(TWO_PI)
    z_req.add(float(z_lo))
    z_req.add(float(z_hi))

    axis_res = {AXIS_R: res, AXIS_A: res_alpha, AXIS_Z: res}
    required = {AXIS_R: r_req, AXIS_A: a_req, AXIS_Z: z_req}
    lines: dict[str, list[float]] = {}
    for ny in (AXIS_R, AXIS_A, AXIS_Z):
        grid.SetLines(ny, sorted({float(v) for v in required[ny]}))
        grid.SmoothMeshLines(ny, axis_res[ny], ratio)
        lines[AXIS_NAMES[ny]] = _resync_lines(grid, ny, required[ny])

    n_cells = [max(0, len(lines[n]) - 1) for n in AXIS_NAMES]
    setup: dict[str, Any] = {
        "coord_system": "cylindrical",
        "axis_res": {
            "r": res,
            "alpha_rad": res_alpha,
            "alpha_deg": math.degrees(res_alpha),
            "z": res,
        },
        "grading_ratio": ratio,
        "domain": {
            "r": [0.0, r_hi],
            "alpha": [0.0, TWO_PI],
            "z": [z_lo, z_hi],
        },
        "n_lines": {n: len(lines[n]) for n in AXIS_NAMES},
        "n_cells": {n: c for n, c in zip(AXIS_NAMES, n_cells)},
        "easy_mesh_bypassed": True,
        "ports": len(ports),
    }
    if notes:
        setup["notes"] = notes
    return setup
