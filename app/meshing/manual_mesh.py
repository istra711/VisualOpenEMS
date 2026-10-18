"""Manual mesh-line edits (Mesh Editor) applied after easyMesh.

The Mesh Editor lets the user freely add lines to, and delete auto lines from,
the generated grid per axis. The overrides live on
:class:`app.models.project.MeshSpec` (``manual_mesh_lines`` /
``manual_mesh_lines_removed``, in drawing units) and are applied app-side
*after* easyMesh, the metal-interface refinement and the max-cell cap — so a
user edit is not smoothed away again. Cartesian only.

Robustness: the overrides are absolute coordinates, but the auto grid can shift
when mesh parameters change. A removal therefore drops the grid line **nearest**
to the stored coordinate (within a local-cell tolerance) instead of requiring an
exact match, and lines that no longer have a target are reported back so the
caller can warn instead of silently producing a different mesh. This mirrors the
other post-easyMesh passes (``interface_refine``, ``cap_max_cell_size``).
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


def _local_cell(lines: np.ndarray, c: float) -> float:
    """Smallest cell adjacent to ``c`` in the (unsorted) grid ``lines``."""
    if lines.size < 2:
        return 0.0
    s = np.sort(np.asarray(lines, dtype=float))
    i = int(np.argmin(np.abs(s - c)))
    cands = []
    if i > 0:
        cands.append(s[i] - s[i - 1])
    if i < s.size - 1:
        cands.append(s[i + 1] - s[i])
    return float(min(cands)) if cands else float(np.median(np.diff(s)))


def apply_lines_axis(
    lines: Any, add: list[float], rem: list[float]
) -> tuple[np.ndarray, dict]:
    """Apply the add/remove overrides to one axis' grid lines (pure).

    Returns ``(new_lines, stats)`` with the same stats shape as
    :func:`apply_manual_mesh_lines`. Removals drop the line NEAREST the stored
    coordinate within a local-cell tolerance; removals with no target are
    reported in ``stats["missed"]``.
    """
    lines = np.asarray(lines, dtype=float)
    removed = 0
    missed: list[float] = []
    drift = 0.0
    for c in rem:
        if lines.size == 0:
            missed.append(c)
            continue
        j = int(np.argmin(np.abs(lines - c)))
        dist = abs(float(lines[j]) - c)
        cell = _local_cell(lines, c)
        tol = max(_TOL, 0.5 * cell) if cell > 0 else max(_TOL, 1e-3)
        if dist <= tol:
            drift = max(drift, dist)
            lines = np.delete(lines, j)
            removed += 1
        else:
            missed.append(c)
    added = 0
    for c in add:
        if lines.size and float(np.min(np.abs(lines - c))) <= _TOL:
            continue
        lines = np.append(lines, c)
        added += 1
    return lines, {"added": added, "removed": removed, "missed": missed, "drift": drift}


def apply_manual_mesh_lines(
    CSX, mesh: Any, coord_system: str = "cartesian"
) -> dict[str, dict]:
    """Add/remove the user's manual mesh lines on the CSX grid.

    Returns, per axis that changed, a dict::

        {"removed": int, "added": int, "missed": [float, ...],
         "drift": float}   # largest distance between a stored removal and the
                           # auto line it actually hit (drawing units)

    ``missed`` holds removals that found no grid line (e.g. the mesh changed so
    much that the intended line is gone) — the caller should warn about these.
    Empty dict when there is nothing to do or the build is not cartesian.
    """
    if coord_system != "cartesian":
        return {}
    add = getattr(mesh, "manual_mesh_lines", None)
    rem = getattr(mesh, "manual_mesh_lines_removed", None)
    if not add and not rem:
        return {}

    grid = CSX.GetGrid()
    stats: dict[str, dict] = {}
    for d, name in enumerate(_AXIS_NAME):
        lines = np.asarray(grid.GetLines(d), dtype=float)
        drop = _coords(rem, name)
        added_c = _coords(add, name)
        if lines.size == 0 and not added_c:
            continue
        new_lines, s = apply_lines_axis(lines, added_c, drop)
        if s["added"] or s["removed"]:
            grid.SetLines(d, [float(v) for v in new_lines])
        if s["added"] or s["removed"] or s["missed"]:
            stats[name] = s
    return stats
