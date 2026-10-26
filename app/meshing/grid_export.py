"""Export the FDTD grid lines as JSON for external solvers (CST, â€¦).

Both openEMS and CST's time-domain solver use a rectilinear (hexahedral) grid.
Sharing the *line positions* â€” not just the geometry â€” lets a comparison run on
bit-identical cells instead of two independently meshed models, which is exactly
where the two solvers would otherwise disagree.

The payload is deliberately small and plain: one sorted coordinate list per
axis, in millimetres (CST's default unit, same as the STEP export), plus the
derived cell counts. A CST VBA macro can feed it straight into
``Mesh.SetLines``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from app.models.project import mm_per_unit

FORMAT_NAME = "visualopenems-grid"
FORMAT_VERSION = 1


def _axis_names(coord_system: str) -> tuple[str, str, str]:
    return ("rho", "phi", "z") if coord_system == "cylindrical" else ("x", "y", "z")


def grid_payload(
    lines: Mapping[str, Any],
    *,
    unit: str = "mm",
    coord_system: str = "cartesian",
    domain: Any = None,
) -> dict:
    """Build the JSON payload from per-axis grid lines (in ``unit``).

    ``lines`` is ``{"x": [...], "y": [...], "z": [...]}``; every axis is sorted
    and converted to millimetres. ``domain`` (optional) is
    ``[[xmin, ymin, zmin], [xmax, ymax, zmax]]`` in the same drawing unit.
    """
    try:
        scale = mm_per_unit(unit)
    except ValueError:
        raise ValueError(f"unsupported unit {unit!r} (expected m, cm, mm or um)") from None

    names = _axis_names(coord_system)
    axes: list[dict] = []
    shape: list[int] = []
    for key, name in zip(("x", "y", "z"), names):
        values = lines.get(key) if isinstance(lines, Mapping) else None
        raw = np.asarray(values, dtype=float).ravel() if values is not None else np.asarray([])
        coords = np.unique(np.sort(raw[np.isfinite(raw)])) * scale if raw.size else np.asarray([])
        cells = max(0, int(coords.size) - 1)
        entry = {
            "name": name,
            "lines": [float(v) for v in coords],
            "cells": cells,
        }
        if cells:
            sizes = np.diff(coords)
            entry["cell_size_min"] = float(sizes.min())
            entry["cell_size_max"] = float(sizes.max())
        axes.append(entry)
        shape.append(cells)

    payload: dict = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "unit": "mm",
        "source_unit": str(unit or "mm").lower(),
        "coord_system": str(coord_system or "cartesian"),
        "axes": axes,
        "shape": shape,
    }
    if domain is not None:
        try:
            arr = np.asarray(domain, dtype=float) * scale
            if arr.shape == (2, 3) and np.all(np.isfinite(arr)):
                payload["domain"] = {
                    "start": [float(v) for v in arr[0]],
                    "stop": [float(v) for v in arr[1]],
                }
        except (TypeError, ValueError):
            pass
    return payload


def export_grid_json(
    lines: Mapping[str, Any],
    out_path: str | Path,
    *,
    unit: str = "mm",
    coord_system: str = "cartesian",
    domain: Any = None,
) -> Path:
    """Write the grid payload as JSON. Returns the written path."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = grid_payload(lines, unit=unit, coord_system=coord_system, domain=domain)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
