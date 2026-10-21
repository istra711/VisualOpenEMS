"""Keep port boxes exactly on the generated (cartesian) mesh.

easyMesh registers a port as an ordinary — usually zero-length — polygon edge.
``handle_closely_placed_edges`` and ``SmoothMeshLines`` therefore treat the port
faces like any other edge: a clicked coordinate that sits a few microns away
from an interpolated line is dropped and replaced by the midpoint, and the exact
port coordinate disappears from the grid.

openEMS later snaps every port box onto the nearest grid lines
(``SnapBox2Mesh``). For a *line* lumped port that silently moves the port by up
to one cell; for an *area* port the box (and with it the reference impedance)
changes width. The mesh validator flags that as a hard error — but the correct
fix is to make the grid contain the port coordinates.

This pass runs after easyMesh (like ``interface_refine`` / ``manual_mesh``) and
re-asserts every coordinate of every *active* port box:

* a coordinate already on a line: nothing to do;
* a coordinate within ``MERGE_FRACTION`` of a local cell next to a movable
  (non-geometry) line: that line is **moved** onto the port coordinate, so no
  tiny cell is created;
* otherwise a line is **inserted**.

The few remaining cases (a port face within a fraction of a cell of a *geometry*
face) cannot get its own line without creating a sub-micron cell; they are
reported as ``snapped`` and only produce a validator *warning*, because openEMS
shifts the port by a negligible fraction of a cell.
"""

from __future__ import annotations

import numpy as np

#: Fraction of the local cell below which a port coordinate is merged into the
#: nearest (movable) grid line instead of inserting a tiny extra cell.
MERGE_FRACTION = 0.15

#: CSXCAD property kinds that are ports/probes/dumps rather than geometry. Their
#: coordinates are allowed to move; metal/dielectric faces are not.
_NON_GEOMETRY_KINDS = {
    "lumpedelement",
    "excitation",
    "probebox",
    "dumpbox",
    "resbox",
}


def _close(a: float, b: float, rel: float = 1e-6) -> bool:
    """Same coordinate? ``rel`` covers the float32 rounding CSXCAD stores."""
    return abs(float(a) - float(b)) <= rel * max(1.0, abs(float(a)), abs(float(b)))


def _is_active(port) -> bool:
    return bool(getattr(port, "active", True))


def required_coordinates(ports) -> dict[str, list[float]]:
    """Every x/y/z coordinate of every active port box (deduplicated).

    A port box is aligned when all six corner coordinates lie on grid lines;
    that is exactly what openEMS needs so it does not move the port itself.
    """
    out: dict[str, list[float]] = {"x": [], "y": [], "z": []}
    for port in ports or []:
        if not _is_active(port):
            continue
        for i, axis in enumerate("xyz"):
            try:
                values = (port.start[i], port.stop[i])
            except Exception:  # noqa: BLE001 — malformed port, validator reports it
                continue
            for value in values:
                value = float(value)
                if not any(_close(value, other) for other in out[axis]):
                    out[axis].append(value)
    return out


def geometry_coordinates(csx) -> dict[str, list[float]]:
    """Coordinates of the metal/dielectric faces — never moved by this pass."""
    out: dict[str, list[float]] = {"x": [], "y": [], "z": []}
    try:
        from app.meshing.preview import iter_primitive_infos

        infos = iter_primitive_infos(csx, coord_system="cartesian")
    except Exception:  # noqa: BLE001 — no protection is better than no alignment
        return out
    for info in infos:
        if (info.kind or "").strip().lower() in _NON_GEOMETRY_KINDS:
            continue
        for i, axis in enumerate("xyz"):
            out[axis].append(float(info.start[i]))
            out[axis].append(float(info.stop[i]))
    return out


def align_axis(
    lines,
    wanted,
    protected=(),
    merge_fraction: float = MERGE_FRACTION,
) -> tuple[np.ndarray, dict[str, list]]:
    """Insert/move grid lines so that every ``wanted`` coordinate is on a line.

    Pure helper (no CSXCAD) so it can be unit tested. ``protected`` holds
    geometry coordinates that must never be moved; ``lines``/the return value are
    one axis' grid sorted ascending.

    Returns ``(new_lines, stats)`` with ``stats`` holding

    * ``moved``   — ``[(old, new), ...]`` lines shifted onto a port coordinate,
    * ``added``   — ``[coord, ...]`` newly inserted lines,
    * ``snapped`` — ``[(wanted, nearest), ...]`` coordinates left alone because
      they sit too close to a protected line (openEMS snaps the port there).
    """
    work = [float(v) for v in np.sort(np.asarray(lines, dtype=float))]
    moved: list[tuple[float, float]] = []
    added: list[float] = []
    snapped: list[tuple[float, float]] = []
    if len(work) < 2:
        return np.asarray(work, dtype=float), {
            "moved": moved,
            "added": added,
            "snapped": snapped,
        }
    wanted_sorted = sorted({float(v) for v in wanted})
    prot = [float(v) for v in protected]
    for value in wanted_sorted:
        if value < work[0] or value > work[-1]:
            continue  # outside the meshed domain — nothing we can align
        if any(_close(value, line) for line in work):
            continue
        idx = int(np.argmin([abs(line - value) for line in work]))
        nearest = work[idx]
        near = abs(nearest - value)
        prev_gap = nearest - work[idx - 1] if idx > 0 else float("inf")
        next_gap = work[idx + 1] - nearest if idx + 1 < len(work) else float("inf")
        cell = min(prev_gap, next_gap)
        if not np.isfinite(cell) or cell <= 0.0:
            cell = near
        movable = (
            0 < idx < len(work) - 1
            and near <= merge_fraction * cell
            and not any(_close(nearest, p) for p in prot)
            and not any(_close(nearest, other) for other in wanted_sorted if other != value)
        )
        if movable:
            work[idx] = value
            moved.append((nearest, value))
        elif near > merge_fraction * cell:
            work.append(value)
            work.sort()
            added.append(value)
        else:
            snapped.append((value, nearest))
    return np.asarray(work, dtype=float), {
        "moved": moved,
        "added": added,
        "snapped": snapped,
    }


def ensure_port_lines(
    CSX,
    ports,
    *,
    coord_system: str = "cartesian",
    extra_protected: dict[str, list[float]] | None = None,
    merge_fraction: float = MERGE_FRACTION,
) -> dict[str, dict]:
    """Re-assert all active port coordinates on the CSX grid (cartesian only).

    ``extra_protected`` (per-axis coordinates) holds lines the user placed by
    hand in the Mesh Editor — like geometry faces they are never moved; the port
    coordinate is then only snapped onto them (a warning, see the validator).

    Returns per changed axis ``{"moved": [...], "added": [...], "snapped": [...]}``
    (empty dict when there is nothing to do).
    """
    if coord_system != "cartesian":
        return {}
    required = required_coordinates(ports)
    if not any(required.values()):
        return {}
    protected = geometry_coordinates(CSX)
    if isinstance(extra_protected, dict):
        for axis in "xyz":
            for v in extra_protected.get(axis) or []:
                try:
                    protected[axis].append(float(v))
                except (TypeError, ValueError):
                    continue
    grid = CSX.GetGrid()
    stats: dict[str, dict] = {}
    for d, axis in enumerate("xyz"):
        if not required[axis]:
            continue
        lines = np.asarray(grid.GetLines(d), dtype=float)
        if lines.size < 2:
            continue
        new_lines, s = align_axis(
            lines, required[axis], protected[axis], merge_fraction=merge_fraction
        )
        if s["moved"] or s["added"]:
            grid.SetLines(d, [float(v) for v in new_lines])
        if s["moved"] or s["added"] or s["snapped"]:
            stats[axis] = s
    return stats


def describe_stats(stats: dict[str, dict]) -> str:
    """One-line summary for the mesh log (e.g. ``y +1 line, x 1 moved``)."""
    parts = []
    for axis in "xyz":
        s = stats.get(axis)
        if not s:
            continue
        bits = []
        if s.get("added"):
            bits.append(f"+{len(s['added'])} line(s)")
        if s.get("moved"):
            bits.append(f"{len(s['moved'])} moved")
        if s.get("snapped"):
            bits.append(f"{len(s['snapped'])} snapped")
        if bits:
            parts.append(f"{axis} " + ", ".join(bits))
    return "; ".join(parts)
