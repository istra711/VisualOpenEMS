"""Graded mesh refinement at metal (PEC) interfaces.

An FDTD grid resolves the fields best right next to a metal surface and may
coarsen with distance. This pass inserts geometric (graded) lines on the
NON-metal side of every metal face — i.e. into the vacuum/air or dielectric
region — with the finest cell at the interface, growing by
``max_grading_ratio`` until it merges into the existing (easyMesh) grid.

It runs AFTER ``easyMesh.GenerateMesh`` so the inserted lines survive
``SmoothMeshLines``, and only for the cartesian mesher (the polar mesher
manages its r/alpha/z grid on its own).

Pure geometry helpers (``graded_fill``, ``interface_refine_lines``) are
unit-testable without CSXCAD.
"""

from __future__ import annotations

from typing import Any, Iterable

import numpy as np

#: how many existing cells outward an interface may refine (normally 1)
MAX_CELLS_OUTWARD = 3
#: cap of inserted lines per interface position and per axis
MAX_LINES_PER_INTERFACE = 6
MAX_LINES_PER_AXIS = 200
#: growth clamp (the MeshSpec value is used when inside this range)
MIN_GROWTH = 1.2
MAX_GROWTH = 5.0


def metal_face_sides(infos: Iterable[Any], axis_idx: int) -> list[tuple[float, int]]:
    """Return ``(position, direction)`` of the non-metal side of every metal face.

    ``direction`` is +1 for the region above the face, -1 for below. A
    zero-thickness metal sheet (patch / ground plane) refines on both sides;
    a solid metal box only outside (never into the metal).
    """
    out: list[tuple[float, int]] = []
    for info in infos:
        kind = str(getattr(info, "kind", "") or "").lower()
        if "metal" not in kind:
            continue
        try:
            a = float(info.start[axis_idx])
            b = float(info.stop[axis_idx])
        except Exception:  # noqa: BLE001
            continue
        if b - a <= 0.0:
            out.append((a, +1))
            out.append((a, -1))
        else:
            out.append((a, -1))
            out.append((b, +1))
    return out


def graded_fill(
    p: float,
    q: float,
    first: float,
    growth: float,
    max_lines: int = MAX_LINES_PER_INTERFACE,
) -> list[float]:
    """Interior grid lines between ``p`` (fine end) and ``q``.

    Geometric ramp: first cell ``first``, every following cell ``growth``
    times larger, scaled so the last cell lands exactly on ``q`` (the
    existing line). Consecutive cell ratios stay <= ``growth``.
    """
    span = abs(float(q) - float(p))
    if span <= 0 or first <= 0 or growth <= 1.0 or max_lines < 1 or span <= first:
        return []
    # Largest geometric series of <= max_lines steps that still fits into the
    # span: scaling it up by alpha >= 1 keeps the FIRST cell >= ``first`` so the
    # caps (never more than one grading step off the neighbouring cell) hold.
    n = 1
    while (
        first * (growth ** (n + 1) - 1.0) / (growth - 1.0) <= span
        and n < max_lines
    ):
        n += 1
    total = first * (growth**n - 1.0) / (growth - 1.0)
    if total <= 0:
        return []
    sign = 1.0 if q > p else -1.0
    alpha = span / total  # >= 1 (n was chosen to fit)
    out: list[float] = []
    acc = 0.0
    for i in range(n):
        acc += alpha * first * (growth**i)
        if i < n - 1:  # the last step lands exactly on q → q already exists
            out.append(float(p) + sign * acc)
    return out


def interface_refine_lines(
    lines,
    interfaces: Iterable[tuple[float, int]],
    fine: float | None = None,
    growth: float = 2.0,
    max_per_axis: int = MAX_LINES_PER_AXIS,
    continuity: bool = False,
) -> list[float]:
    """New grid lines (sorted, collision-free) for the given interfaces.

    ``lines`` is the existing sorted grid of one axis; ``fine`` is the target
    cell size at the interface (``min_cellsize``), ``None`` → local cell/4.

    ``continuity=True`` additionally keeps the first inserted cell within one
    grading step of the cell on the OTHER side of the interface — only needed
    where the fields must stay continuous (dielectric/air interfaces). Across a
    PEC face the field is zero, so the metal-interface refinement leaves the
    other side unconstrained (apart from the global ``floor``).

    The finest inserted cell is capped at ``(smallest existing cell)/growth``:
    never make the global timestep worse than one grading step below the
    finest cell easyMesh already produced.
    """
    arr = np.sort(np.asarray(lines, dtype=float))
    if arr.size < 2:
        return []
    growth = min(max(float(growth), MIN_GROWTH), MAX_GROWTH)
    sizes = np.diff(arr)
    smallest = float(sizes.min()) if sizes.size else 0.0
    floor = smallest / growth if smallest > 0 else 0.0

    fine_val = float(fine) if fine and float(fine) > 0 else None

    added: list[float] = []
    for pos, direction in interfaces:
        x = float(pos)
        step = fine_val
        for _ in range(MAX_CELLS_OUTWARD):
            if direction > 0:
                nxt = arr[arr > x + 1e-12]
                q = float(nxt[0]) if nxt.size else None
            else:
                prv = arr[arr < x - 1e-12]
                q = float(prv[-1]) if prv.size else None
            if q is None:
                break
            span = abs(q - x)
            s0 = step if step is not None else span / 4.0
            if floor > 0:
                s0 = max(s0, floor)
            if continuity and x == pos:
                # field is continuous across this interface: continue the
                # neighbouring grading — the first inserted cell gets (about)
                # the size of the cell on the other side (ratio across ~1);
                # further cells keep the geometric progression. With the jump
                # emitted on BOTH sides (material_jump_sides) each side anchors
                # to the opposite side, so a ramp into the dielectric and into
                # the air is produced by the two directions in turn.
                other = _neighbour_cell(arr, pos, direction)
                if other is not None and other > 0:
                    s0 = other
                    if floor > 0:
                        s0 = max(s0, floor)
            if span <= s0 * 1.0001:
                break  # local mesh is already fine enough
            added.extend(graded_fill(x, q, s0, growth))
            x, step = q, span
        if len(added) > max_per_axis:
            break

    # drop lines colliding with existing ones or with each other
    merged = [float(v) for v in arr]
    keep: list[float] = []
    for v in sorted(added):
        tol = 1e-9 * max(1.0, abs(v))
        if any(abs(v - m) < tol for m in merged):
            continue
        merged.append(v)
        keep.append(float(v))
        if len(keep) >= max_per_axis:
            break
    return keep


def _neighbour_cell(arr: np.ndarray, pos: float, direction: int) -> float | None:
    """Size of the existing cell adjacent to ``pos`` on the side OPPOSITE to
    ``direction`` (the cell we would be grading away from)."""
    on_line = bool(np.any(np.abs(arr - pos) < 1e-9))
    if on_line:
        if direction > 0:  # refining towards +, look at the cell just below
            prv = arr[arr < pos - 1e-12]
            return float(pos - prv[-1]) if prv.size else None
        nxt = arr[arr > pos + 1e-12]
        return float(nxt[0] - pos) if nxt.size else None
    # pos lies inside a cell -> that cell borders the interface on both sides
    prv = arr[arr < pos]
    nxt = arr[arr > pos]
    if prv.size and nxt.size:
        return float(nxt[0] - prv[-1])
    return None


def material_jump_sides(
    infos: Iterable[Any], axis_idx: int, eps_tol: float = 1e-6
) -> list[tuple[float, int]]:
    """``(position, direction)`` of the medium (ε) jumps on BOTH sides of every
    dielectric.

    A dielectric face borders either a different dielectric or air (ε = 1); the
    fields are continuous across such an interface, so both sides should stay
    within one grading step of each other. Each jumping face is therefore
    emitted twice — outward (into the other medium / air) and inward (into this
    dielectric) — so the ramp is inserted on both sides. Faces that coincide
    with a metal primitive are skipped — the metal pass handles those (no
    continuity needed inside/next to a PEC).
    """
    media: list[tuple[tuple[float, float, float], tuple[float, float, float], float]] = []
    metal_boxes: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    for info in infos:
        kind = str(getattr(info, "kind", "") or "").lower()
        try:
            lo = tuple(min(float(info.start[k]), float(info.stop[k])) for k in range(3))
            hi = tuple(max(float(info.start[k]), float(info.stop[k])) for k in range(3))
        except Exception:  # noqa: BLE001
            continue
        if "metal" in kind:
            metal_boxes.append((lo, hi))
            continue
        if "sheet" in kind:  # conducting sheet: not a volumetric medium
            continue
        media.append((lo, hi, float(getattr(info, "epsilon", 1.0) or 1.0)))

    out: list[tuple[float, int]] = []
    for lo, hi, eps_in in media:
        for pos, direction in ((lo[axis_idx], -1), (hi[axis_idx], +1)):
            scale = max(1.0, abs(pos))
            # metal on the same plane that overlaps this medium in the other
            # axes -> the metal pass covers it (no continuity constraint there)
            if any(
                mlo[axis_idx] - 1e-9 <= pos <= mhi[axis_idx] + 1e-9
                and all(
                    mlo[k] - 1e-9 <= hi[k] and lo[k] <= mhi[k] + 1e-9
                    for k in range(3)
                    if k != axis_idx
                )
                for mlo, mhi in metal_boxes
            ):
                continue
            probe = pos + direction * 1e-6 * scale
            eps_out = 1.0  # no primitive on that side -> air
            for lo2, hi2, eps2 in media:
                if (lo2, hi2) == (lo, hi):
                    continue
                if lo2[axis_idx] - 1e-9 * scale <= probe <= hi2[axis_idx] + 1e-9 * scale:
                    eps_out = eps2
                    break
            if abs(eps_out - eps_in) <= eps_tol:
                continue  # same medium -> no jump
            # grade BOTH sides of the jump: the outward face (into the other
            # medium / air) and the inward face (into this dielectric), so the
            # interface is resolved from both sides instead of only anchoring
            # to whichever side happens to be fine already.
            out.append((pos, direction))
            out.append((pos, -direction))
    # adjacent dielectrics emit the shared plane from both boxes -> dedup
    seen: set[tuple[float, int]] = set()
    uniq: list[tuple[float, int]] = []
    for item in out:
        key = (round(float(item[0]), 12), int(item[1]))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


def refine_at_metal_interfaces(
    CSX,
    mesh,
    coord_system: str = "cartesian",
) -> dict[str, int]:
    """Insert graded lines next to metal faces and medium (ε) jumps.

    Metal faces: refine into the non-metal side(s) (no continuity constraint —
    the field is zero inside a PEC). Dielectric/air jumps: refine with
    ``continuity=True`` so both sides stay within ``max_grading_ratio``.

    Returns ``{axis: n_added}``. No-op in cylindrical mode and when the
    MeshSpec option is off. Never raises for CSX/mesh quirks.
    """
    if coord_system == "cylindrical":
        return {}
    if not getattr(mesh, "add_graded_mesh_lines_at_material_transitions", False):
        return {}
    from app.meshing.preview import iter_primitive_infos, mesh_lines_from_csx

    infos = iter_primitive_infos(CSX, coord_system="cartesian")
    if not infos:
        return {}
    lines = mesh_lines_from_csx(CSX)
    grid = CSX.GetGrid()
    fine = getattr(mesh, "min_cellsize", None)
    fine_z = getattr(mesh, "min_cellsize_z", None)
    growth = float(getattr(mesh, "max_grading_ratio", 2.0) or 2.0)

    stats: dict[str, int] = {}
    for axis_idx, axis_name in enumerate("xyz"):
        metal = metal_face_sides(infos, axis_idx)
        medium = material_jump_sides(infos, axis_idx)
        if not metal and not medium:
            continue
        f = fine_z if (axis_idx == 2 and fine_z) else fine
        base = np.asarray(lines[axis_name], dtype=float)
        new = interface_refine_lines(base, metal, fine=f, growth=growth)
        if medium:
            merged = (
                np.sort(np.concatenate([base, np.asarray(new, dtype=float)]))
                if new
                else base
            )
            new = list(new) + interface_refine_lines(
                merged, medium, fine=f, growth=growth, continuity=True
            )
        if not new:
            continue
        merged = np.sort(np.concatenate([base, np.asarray(new, dtype=float)]))
        grid.SetLines(axis_idx, merged)
        stats[axis_name] = len(new)
    return stats
