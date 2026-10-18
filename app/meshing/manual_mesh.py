"""Manual mesh-line edits (Mesh Editor) applied after easyMesh.

The Mesh Editor lets the user freely add lines to, and delete auto lines from,
the generated grid per axis. The overrides live on
:class:`app.models.project.MeshSpec` (``manual_mesh_lines`` /
``manual_mesh_lines_removed``, in drawing units) and are applied app-side
*after* easyMesh, the metal-interface refinement and the max-cell cap — so a
user edit is not smoothed away again. Cartesian only.

This mirrors the other post-easyMesh passes (``interface_refine``,
``cap_max_cell_size``).
"""

from __future__ import annotations

from typing import Any

import numpy as np

_AXIS_NAME = ("x", "y", "z")
_TOL = 1e-6


def _coords(d: Any, name: str) -> list[float]:
    if not isinstance(d, dict):
        return []
    vals = d.get(name) or []
    out: list[float] = []
    for v in vals:
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def apply_manual_mesh_lines(
    CSX, mesh: Any, coord_system: str = "cartesian"
) -> dict[str, int]:
    """Add/remove the user's manual mesh lines on the CSX grid.

    Returns a per-axis count of lines added (``{"x": 2, ...}``), empty when
    there is nothing to do or the build is not cartesian.
    """
    if coord_system != "cartesian":
        return {}
    add = getattr(mesh, "manual_mesh_lines", None)
    rem = getattr(mesh, "manual_mesh_lines_removed", None)
    if not add and not rem:
        return {}

    grid = CSX.GetGrid()
    stats: dict[str, int] = {}
    for d, name in enumerate(_AXIS_NAME):
        lines = np.asarray(grid.GetLines(d), dtype=float)
        if lines.size == 0:
            continue

        drop = _coords(rem, name)
        if drop:
            keep = lines
            for c in drop:
                keep = keep[np.abs(keep - c) > _TOL]
            if keep.size != lines.size:
                grid.SetLines(d, [float(v) for v in keep])
                lines = keep

        added = 0
        for c in _coords(add, name):
            if lines.size and float(np.min(np.abs(lines - c))) <= _TOL:
                continue  # already on the grid
            grid.AddLine(d, c)
            lines = np.append(lines, c)
            added += 1
        if added:
            stats[name] = added
    return stats
