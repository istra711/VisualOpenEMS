"""CSXCAD loading and the per-object identity model.

The simulation CSX groups primitives by material.  For the export we need one
property per object, so the add-on rebuilds a small identity CSX from the saved
project primitives.  The simulation CSX remains untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .project_data import MaterialInfo, ObjectInfo

#: CSXCAD property types that describe geometry (ports/dumps are excluded).
_GEOMETRY_TYPES = {"Metal", "Material", "ConductingSheet"}


def load_csx(path: str | Path):
    """Load a CSXCAD XML and prepare it for point queries."""
    from CSXCAD import ContinuousStructure

    csx = ContinuousStructure()
    csx.ReadFromXML(str(Path(path).expanduser().resolve()))
    # Needed before GetPropertyByCoordPriority() works for all primitives
    # (cylinders, polyhedra, ...).
    csx.Update()
    return csx


def grid_arrays(csx) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Return X/Y/Z grid lines and CSXCAD's DeltaUnit (metres per unit)."""
    grid = csx.GetGrid()
    x = np.asarray(grid.GetLines(0), dtype=float)
    y = np.asarray(grid.GetLines(1), dtype=float)
    z = np.asarray(grid.GetLines(2), dtype=float)
    if len(x) < 2 or len(y) < 2 or len(z) < 2:
        raise ValueError("The mesh has fewer than two lines in at least one direction")
    unit = float(grid.GetDeltaUnit() or 0.0)
    if unit <= 0.0:
        raise ValueError("CSXCAD DeltaUnit is missing or not positive")
    return x, y, z, unit


def geometry_properties(csx) -> list[Any]:
    """Metal/material/sheet properties (no ports, excitations or probes)."""
    result = []
    for prop in csx.GetAllProperties():
        try:
            type_name = str(prop.GetTypeString())
            name = str(prop.GetName() or "")
        except Exception:  # noqa: BLE001 - keep going with the other properties
            continue
        if name and type_name in _GEOMETRY_TYPES:
            result.append(prop)
    return result


def objects_from_csx(csx) -> list[ObjectInfo]:
    """Fallback object list when the project JSON has no primitives."""
    return [
        ObjectInfo(
            name=str(prop.GetName()),
            material=str(prop.GetName()),
            property_name=str(prop.GetName()),
            source="csx",
        )
        for prop in geometry_properties(csx)
    ]


def build_identity_csx(
    objects: Iterable[ObjectInfo],
    materials: dict[str, MaterialInfo],
    delta_unit: float,
) -> tuple[Any, list[str]]:
    """Build a CSX with one uniquely named property per object.

    The coordinate system and DeltaUnit match the simulation CSX.  Primitives
    are copied from the saved project data, not from the simulation CSX, so two
    objects that share a material remain distinguishable.
    """
    from CSXCAD import ContinuousStructure

    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(float(delta_unit))
    warnings: list[str] = []

    for obj in objects:
        prop = _make_property(csx, obj, materials)
        if prop is None:
            warnings.append(f"{obj.name}: unsupported material - skipped")
            continue
        if not obj.primitives:
            warnings.append(
                f"{obj.name}: no saved primitive geometry - object will be empty"
            )
            continue
        for prim in obj.primitives:
            _add_primitive(prop, obj, prim, warnings)

    csx.Update()
    return csx, warnings


def _make_property(csx, obj: ObjectInfo, materials: dict[str, MaterialInfo]):
    mat = materials.get(obj.material)
    kind = mat.kind if mat else "dielectric"
    if kind == "pec":
        return csx.AddMetal(obj.name)
    if kind == "conducting_sheet":
        kappa = float(mat.kappa) if mat else 0.0
        return csx.AddConductingSheet(obj.name, conductivity=kappa)
    epsilon = float(mat.epsilon) if mat else 1.0
    kappa = float(mat.kappa) if mat else 0.0
    if kappa:
        return csx.AddMaterial(obj.name, epsilon=epsilon, kappa=kappa)
    return csx.AddMaterial(obj.name, epsilon=epsilon)


def _add_primitive(prop, obj: ObjectInfo, prim: dict[str, Any], warnings: list[str]) -> None:
    kind = str(prim.get("kind") or "box").lower()
    try:
        priority = int(prim.get("priority", obj.priority))
    except (TypeError, ValueError):
        priority = int(obj.priority)

    try:
        if kind == "box":
            start = _vec3(prim["start"])
            stop = _vec3(prim["stop"])
            if _zero_extent_axes(start, stop):
                # Zero-thickness sheets have no cell volume.  They are handled
                # separately as planar triangles.
                return
            prop.AddBox(start=start, stop=stop, priority=priority)
            return

        if kind == "cylinder":
            start = _vec3(prim["start"])
            stop = _vec3(prim["stop"])
            radius = float(prim.get("radius", 0.0) or 0.0)
            if radius <= 0.0:
                warnings.append(f"{obj.name}: cylinder radius <= 0 - skipped")
                return
            if np.allclose(start, stop):
                warnings.append(f"{obj.name}: cylinder has zero length - skipped")
                return
            prop.AddCylinder(start, stop, radius, priority=priority)
            return

        if kind == "sphere":
            center = _vec3(prim["center"])
            radius = float(prim.get("radius", 0.0) or 0.0)
            if radius <= 0.0:
                warnings.append(f"{obj.name}: sphere radius <= 0 - skipped")
                return
            prop.AddSphere(center, radius, priority=priority)
            return

        if kind == "polygon":
            axis = str(prim.get("axis") or "z").lower()
            points = prim.get("points") or []
            length = float(prim.get("length") or 0.0)
            if len(points) < 3:
                warnings.append(f"{obj.name}: polygon needs >= 3 points - skipped")
                return
            if length <= 0.0:
                # Polygon sheet: handled as a planar triangle fan later.
                return
            xs = [float(p[0]) for p in points]
            ys = [float(p[1]) for p in points]
            coord = float(prim.get("coord") or 0.0)
            prop.AddLinPoly([xs, ys], axis, coord, length, priority=priority)
            return

        if kind == "stl":
            stl_path = prim.get("stl_path") or prim.get("path") or prim.get("mesh_path")
            if not stl_path:
                warnings.append(f"{obj.name}: STL primitive has no path - skipped")
                return
            stl = Path(str(stl_path))
            if stl.suffix.lower() != ".stl" or not stl.is_file():
                warnings.append(
                    f"{obj.name}: only existing .stl files are supported for "
                    "volume meshes in this MVP - skipped"
                )
                return
            reader = prop.AddPolyhedronReader(str(stl), priority=priority)
            if hasattr(reader, "ReadFile"):
                reader.ReadFile()
            return

        warnings.append(f"{obj.name}: unsupported primitive kind {kind!r} - skipped")
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        warnings.append(f"{obj.name}: invalid {kind} primitive ({exc}) - skipped")


def _vec3(values) -> list[float]:
    values = list(values)
    if len(values) != 3:
        raise ValueError(f"expected 3 coordinates, got {len(values)}")
    return [float(v) for v in values]


def _zero_extent_axes(start: list[float], stop: list[float]) -> list[int]:
    scale = max(1.0, *(abs(v) for v in start + stop))
    return [
        axis
        for axis in range(3)
        if abs(stop[axis] - start[axis]) <= 1e-12 * scale
    ]
