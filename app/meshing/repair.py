"""Mesh repair: remove the cells that only cost runtime (V1/V2).

A repair never deletes a *protected* line - the outermost domain lines, the
authored geometry coordinates (primitives, ports, dumps, NF2FF) and the manual
lines - so a genuine gap (e.g. a 0.13 mm hairpin gap) stays resolved. It only
removes easyMesh-generated lines that create a cell smaller than its neighbours,
then re-spaces the remaining lines locally so the grading ratio stays valid.

Two entry points:

* :func:`repair_axis` + :func:`preview` - plan one region; the right-click
  action in the Mesh Viewer shows the numbers before anything changes.
* :func:`apply_mesh_repairs` - apply the rules stored on the ``MeshSpec``
  (``mesh_repairs``) and, when ``enforce_min_cellsize`` is on, every remaining
  cell below the requested minimum. It is called from ``generate_mesh`` so the
  preview and the generated solver script build exactly the same grid.

Why not simply delete the line? Deleting one line of a graded sequence leaves a
grading jump (0.048 -> 0.166 mm in the measured filter) that the validator then
reports. Merging into the *smaller* neighbour and relaxing the two adjacent
cells keeps the sequence monotone and inside ``max_grading_ratio``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from app.meshing.timing import courant_timestep

#: A cell this close to the floor is not "below" it (float noise).
FLOOR_TOL = 1e-9
#: Tolerance when checking a neighbour ratio (relative).
RATIO_TOL = 1e-6
#: Bounded relaxation: the clamp at the floor can block an exact move.
MAX_SWEEPS = 500
#: A stored coordinate is "the same line" within this fraction of the local cell.
MATCH_FRACTION = 0.5
#: Below this fraction of the local cell two lines count as a duplicate (snap).
SNAP_FRACTION = 0.01

_AXES = ("x", "y", "z")


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _as_array(values: Iterable[float] | None) -> np.ndarray:
    if values is None:
        return np.asarray([], dtype=float)
    return np.sort(np.asarray([float(v) for v in values], dtype=float))


def _local_cell(arr: np.ndarray, j: int) -> float:
    cands = []
    if j > 0:
        cands.append(float(arr[j] - arr[j - 1]))
    if j + 1 < arr.size:
        cands.append(float(arr[j + 1] - arr[j]))
    return min(cands) if cands else 0.0


def _nearest_index(arr: np.ndarray, value: float) -> int | None:
    """Index of the grid line at ``value`` (within half a local cell)."""
    if arr.size == 0:
        return None
    j = int(np.argmin(np.abs(arr - float(value))))
    cell = _local_cell(arr, j)
    tol = max(1e-9, MATCH_FRACTION * cell) if cell > 0 else 1e-9
    if abs(float(arr[j]) - float(value)) <= tol:
        return j
    return None


#: Same-line tolerance in drawing units. CSXCAD stores grid lines as float32
#: (1.635 becomes 1.6349999904632568) while the authored coordinates are float64
#: - a 1e-12 tolerance would treat the protected line as a different line and
#: let a repair delete it (measured on Filter_new_test1).
_SAME_TOL = 1e-6


def _same(a: float, b: float) -> bool:
    a, b = float(a), float(b)
    return abs(a - b) <= _SAME_TOL * max(1.0, abs(a), abs(b))


def min_cell(lines: Sequence[float]) -> float:
    if len(lines) < 2:
        return 0.0
    cells = np.diff(np.asarray(lines, dtype=float))
    cells = cells[cells > 0]
    return float(np.min(cells)) if cells.size else 0.0


def _count_cells(lines: Sequence[float]) -> int:
    return max(0, len(lines) - 1)


def unit_label(unit_m: float) -> str:
    return {0.001: "mm", 1e-6: "um", 1.0: "m"}.get(round(float(unit_m), 12), "units")


# --------------------------------------------------------------------------- #
# the three passes
# --------------------------------------------------------------------------- #
def _merge_below(
    seg: Sequence[float], floor: float
) -> tuple[list[float], list[float]]:
    """Remove interior lines until no cell is below ``floor`` (endpoints stay).

    A cell is merged into its *smaller* neighbour: that removes the inner line
    and leaves the outer structure (and the better resolution) alone.
    """
    out = [float(v) for v in seg]
    removed: list[float] = []
    if floor <= 0.0:
        return out, removed
    while len(out) >= 3:
        cells = np.diff(np.asarray(out, dtype=float))
        k = int(np.argmin(cells))
        if float(cells[k]) >= floor * (1.0 - FLOOR_TOL):
            break
        if k == 0:
            j = 1
        elif k == len(cells) - 1:
            j = len(cells) - 2
        else:
            j = k - 1 if cells[k - 1] <= cells[k + 1] else k + 1
        drop = min(k, j) + 1
        removed.append(out.pop(drop))
    return out, removed


def _merged_cells(seg: Sequence[float], removed: Sequence[float]) -> set[int]:
    """Cell indices of ``seg`` that contain a removed line (i.e. were merged)."""
    out: set[int] = set()
    for v in removed:
        for i in range(len(seg) - 1):
            if seg[i] < float(v) < seg[i + 1]:
                out.add(i)
                break
    return out


def _relax_grading(
    seg: Sequence[float],
    ratio: float,
    floor: float,
    focus: Iterable[int] | None = None,
) -> tuple[list[float], list[tuple[float, float]]]:
    """Move interior lines so neighbour cells differ by at most ``ratio``.

    ``focus`` restricts the pass to the cells a merge just created (plus their
    neighbours, reached sweep by sweep). A pre-existing jump (e.g. the coarse
    cell next to a fine band) is easyMesh's business - touching it would move
    lines without fixing the flagged cell.
    """
    out = [float(v) for v in seg]
    if ratio <= 1.0 or len(out) < 3:
        return out, []
    if focus is None:
        active = set(range(len(out) - 1))
    else:
        active = set(int(i) for i in focus)
    original = list(out)
    for _ in range(MAX_SWEEPS):
        changed = False
        for j in range(1, len(out) - 1):
            if (j - 1) not in active and j not in active:
                continue
            a = out[j] - out[j - 1]
            b = out[j + 1] - out[j]
            if a <= 0.0 or b <= 0.0:
                continue
            small, big = (a, b) if a <= b else (b, a)
            if big <= small * ratio * (1.0 + RATIO_TOL):
                continue
            total = a + b
            # put the shared line where the two cells reach exactly `ratio`
            new_a = total / (1.0 + ratio) if a < b else total * ratio / (1.0 + ratio)
            if floor > 0.0 and total > 2.0 * floor:
                new_a = min(max(new_a, floor), total - floor)
            new = out[j - 1] + new_a
            if not (out[j - 1] < new < out[j + 1]):
                continue
            if abs(new - out[j]) > 1e-12 * max(1.0, abs(new)):
                out[j] = new
                changed = True
                # the move can push the violation one cell further out
                active.add(j - 1)
                active.add(j)
        if not changed:
            break
    moved = [
        (o, n) for o, n in zip(original, out) if not _same(o, n)
    ]
    return out, moved


def _split_above(
    seg: Sequence[float], max_cell: float, floor: float
) -> tuple[list[float], list[float]]:
    """Split cells above the hard ``max_cell`` limit (never below ``floor``)."""
    if max_cell <= 0.0:
        return [float(v) for v in seg], []
    out = [float(seg[0])]
    added: list[float] = []
    for a, b in zip(seg, seg[1:]):
        cell = float(b) - float(a)
        k = int(math.ceil(cell / max_cell - 1e-12)) if cell > max_cell else 1
        if floor > 0.0 and cell / k < floor:
            k = max(1, int(cell / floor))
        for i in range(1, k):
            v = float(a) + cell * i / k
            out.append(v)
            added.append(v)
        out.append(float(b))
    return out, added


def _moved_pairs(
    before: Sequence[float], after: Sequence[float], removed: Sequence[float]
) -> list[tuple[float, float]]:
    """Lines that survived but were shifted (display only)."""
    rem = list(removed)
    arr = np.asarray([float(v) for v in after], dtype=float)
    out: list[tuple[float, float]] = []
    if arr.size == 0:
        return out
    for v in before:
        if any(_same(v, r) for r in rem):
            continue
        j = int(np.argmin(np.abs(arr - float(v))))
        if not _same(float(arr[j]), float(v)):
            out.append((float(v), float(arr[j])))
    return out


# --------------------------------------------------------------------------- #
# one axis / one region
# --------------------------------------------------------------------------- #
@dataclass
class AxisRepair:
    """One repaired region of one axis (drawing units)."""

    axis: str
    lo: float
    hi: float
    floor: float
    before: list[float]
    after: list[float]
    removed: list[float] = field(default_factory=list)
    added: list[float] = field(default_factory=list)
    moved: list[tuple[float, float]] = field(default_factory=list)
    warning: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.removed or self.added or self.moved)

    @property
    def min_before(self) -> float:
        return min_cell(self.before)

    @property
    def min_after(self) -> float:
        return min_cell(self.after)

    @property
    def cells_before(self) -> int:
        return _count_cells(self.before)

    @property
    def cells_after(self) -> int:
        return _count_cells(self.after)

    def describe(self, unit_m: float = 0.001) -> str:
        u = unit_label(unit_m)
        out = [
            f"Region: {self.axis} = {self.lo:.6g} … {self.hi:.6g} {u}",
            f"  floor (min cell)  : {self.floor:.6g} {u}",
            f"  lines             : {len(self.before)} -> {len(self.after)}"
            f"   (removed {len(self.removed)}, added {len(self.added)},"
            f" moved {len(self.moved)})",
            f"  min cell          : {self.min_before:.6g} -> {self.min_after:.6g} {u}",
            f"  cells             : {self.cells_before} -> {self.cells_after}",
        ]
        if self.removed:
            out.append(
                "  removed lines     : "
                + ", ".join(f"{v:.6g}" for v in self.removed)
            )
        if self.moved:
            out.append(
                "  moved lines       : "
                + ", ".join(f"{o:.6g}->{n:.6g}" for o, n in self.moved)
            )
        if self.warning:
            out.append(f"  WARNING           : {self.warning}")
        return "\n".join(out)


def repair_axis(
    lines: Iterable[float],
    axis: str,
    lo: float,
    hi: float,
    *,
    floor: float | None = None,
    ratio: float = 2.0,
    max_cell: float = 0.0,
    protected: Iterable[float] = (),
    expand: int = 1,
) -> AxisRepair | None:
    """Re-space ``lines[lo…hi]`` so no cell is smaller than ``floor``.

    ``lo``/``hi`` are the faces of the flagged region; the interval is expanded
    by ``expand`` cells on each side so the grading repair has room. ``floor``
    defaults to a local rule (drawing units):

    * a single-cell mark is a *dip* - the floor is the smaller of the two
      neighbouring cells, so the dip is merged into the finer side and the
      outer structure stays as it is;
    * a multi-cell mark is a *plateau* below its neighbours - the floor is the
      next larger neighbouring cell (capped at 4x the plateau), so the band is
      coarsened to the size around it instead of being smoothed away.

    ``protected`` lines split the interval and are never removed.
    """
    arr = _as_array(lines)
    if arr.size < 3:
        return None
    i0 = _nearest_index(arr, float(lo))
    i1 = _nearest_index(arr, float(hi))
    if i0 is None or i1 is None or i1 <= i0:
        return None
    # The flagged region itself (before the grading room is added).
    j0, j1 = i0, i1
    i0 = max(0, i0 - max(0, int(expand)))
    i1 = min(arr.size - 1, i1 + max(0, int(expand)))
    if i1 - i0 < 2:
        return None

    if floor is None:
        cell = min_cell([float(v) for v in arr[j0 : j1 + 1]])
        outside: list[float] = []
        if j0 > 0:
            outside.append(float(arr[j0] - arr[j0 - 1]))
        if j1 < arr.size - 1:
            outside.append(float(arr[j1 + 1] - arr[j1]))
        if not outside:
            floor = cell
        elif j1 - j0 == 1:
            floor = min(outside)  # a dip: merge into the finer neighbour
        else:
            bigger = [c for c in outside if c > cell * (1.0 + 1e-6)]
            floor = min(bigger) if bigger else min(outside)
            floor = min(floor, 4.0 * cell)
    floor = float(floor)
    if floor <= 0.0:
        return None

    prot = sorted(float(v) for v in protected or ())
    cuts = [i0]
    for j in range(i0 + 1, i1):
        v = float(arr[j])
        if any(_same(v, p) for p in prot):
            cuts.append(j)
    cuts.append(i1)

    new: list[float] = [float(v) for v in arr[: i0 + 1]]
    removed: list[float] = []
    added: list[float] = []
    moved: list[tuple[float, float]] = []
    worst = 1.0
    for a, b in zip(cuts, cuts[1:]):
        seg_before = [float(v) for v in arr[a : b + 1]]
        seg = list(seg_before)
        seg, rem = _merge_below(seg, floor)
        # Only the cells a merge just created (and their neighbours, reached
        # sweep by sweep) are re-spaced: a pre-existing grading jump elsewhere
        # in the interval is not the repair's business.
        focus = _merged_cells(seg, rem) if rem else set()
        seg, mov = _relax_grading(seg, ratio, floor, focus)
        seg, add = _split_above(seg, max_cell, floor)
        if add:
            seg, _ = _relax_grading(seg, ratio, floor)
        removed += rem
        added += add
        moved += _moved_pairs(seg_before, seg, rem)
        worst = max(worst, _worst_ratio_near(seg, focus))
        new.extend(seg[1:])
    new.extend(float(v) for v in arr[i1 + 1 :])

    before = [float(v) for v in arr]
    if len(new) == len(before) and all(_same(x, y) for x, y in zip(before, new)):
        return None

    warning = ""
    if ratio > 0 and worst > ratio * (1.0 + 1e-3):
        warning = (
            f"grading still {worst:.2f}x (max {ratio:.2f}x) - "
            "the floor is too high for the available room"
        )
    return AxisRepair(
        axis=str(axis),
        lo=float(arr[i0]),
        hi=float(arr[i1]),
        floor=floor,
        before=before,
        after=new,
        removed=removed,
        added=added,
        moved=moved,
        warning=warning,
    )


def _worst_ratio(lines: Sequence[float]) -> float:
    cells = np.diff(np.asarray(lines, dtype=float))
    cells = cells[cells > 0]
    if cells.size < 2:
        return 1.0
    return float(np.max(cells) / np.min(cells))


def _worst_ratio_near(lines: Sequence[float], focus: Iterable[int]) -> float:
    """Worst neighbour ratio next to the merged cells (pre-existing jumps skip)."""
    cells = np.diff(np.asarray(lines, dtype=float))
    if cells.size < 2:
        return 1.0
    worst = 1.0
    for i in set(int(v) for v in focus):
        for j in (i - 1, i):
            if 0 <= j < cells.size - 1:
                a, b = float(cells[j]), float(cells[j + 1])
                if a > 0.0 and b > 0.0:
                    worst = max(worst, max(a, b) / min(a, b))
    return worst


# --------------------------------------------------------------------------- #
# preview (mesh-level numbers)
# --------------------------------------------------------------------------- #
@dataclass
class RepairPreview:
    """The effect of one or more axis repairs on the whole mesh."""

    repairs: list[AxisRepair]
    unit_m: float = 0.001
    dt_before_s: float = 0.0
    dt_after_s: float = 0.0
    cells_before: int = 0
    cells_after: int = 0

    @property
    def speedup(self) -> float:
        """Expected runtime factor (>1 = faster)."""
        if self.dt_before_s <= 0.0 or self.dt_after_s <= 0.0:
            return 1.0
        cells = (self.cells_before / self.cells_after) if self.cells_after else 1.0
        return cells * (self.dt_after_s / self.dt_before_s)

    def text(self) -> str:
        u = unit_label(self.unit_m)
        out: list[str] = []
        for rep in self.repairs:
            out.append(rep.describe(self.unit_m))
            out.append("")
        out.append(
            f"Whole mesh: dt(CFL) {self.dt_before_s * 1e12:.4g} -> "
            f"{self.dt_after_s * 1e12:.4g} ps, "
            f"cells {self.cells_before} -> {self.cells_after}"
        )
        if self.speedup > 1.001:
            out.append(
                f"Expected runtime: ~{self.speedup:.2f}x shorter "
                "(timesteps x cell count, no FDTD yet)"
            )
        elif self.speedup < 0.999:
            out.append(
                f"Expected runtime: ~{1.0 / self.speedup:.2f}x LONGER - "
                "the repair adds cells"
            )
        else:
            out.append("Expected runtime: unchanged")
        if any(r.warning for r in self.repairs):
            out.append("")
            out.append(
                "The repair could not reach the requested floor everywhere - "
                "see the warning above."
            )
        return "\n".join(out)


def preview(
    repairs: Sequence[AxisRepair],
    lines: Mapping[str, Iterable[float]],
    unit_m: float = 0.001,
) -> RepairPreview:
    """Compute the mesh-level dt/cell effect of the planned repairs."""
    before = {ax: _as_array(lines.get(ax)) for ax in _AXES}
    after = {ax: np.asarray(before[ax], dtype=float) for ax in _AXES}
    for rep in repairs:
        if rep.axis in after:
            after[rep.axis] = np.asarray(rep.after, dtype=float)
    cells_before = int(np.prod([max(1, before[ax].size - 1) for ax in _AXES]))
    cells_after = int(np.prod([max(1, after[ax].size - 1) for ax in _AXES]))
    return RepairPreview(
        repairs=list(repairs),
        unit_m=float(unit_m),
        dt_before_s=courant_timestep(before, float(unit_m)),
        dt_after_s=courant_timestep(after, float(unit_m)),
        cells_before=cells_before,
        cells_after=cells_after,
    )


# --------------------------------------------------------------------------- #
# apply to a built CSX grid
# --------------------------------------------------------------------------- #
def apply_repairs(csx, repairs: Sequence[AxisRepair]) -> None:
    """Write the repaired line lists into the CSX grid."""
    grid = csx.GetGrid()
    for rep in repairs:
        if rep.axis not in _AXES:
            continue
        grid.SetLines(_AXES.index(rep.axis), [float(v) for v in rep.after])


def grid_lines(csx) -> dict[str, np.ndarray]:
    """Current grid lines per axis (drawing units)."""
    grid = csx.GetGrid()
    out: dict[str, np.ndarray] = {}
    for i, ax in enumerate(_AXES):
        try:
            out[ax] = np.sort(np.asarray(grid.GetLines(i), dtype=float))
        except Exception:  # noqa: BLE001 - an empty axis is fine
            out[ax] = np.asarray([], dtype=float)
    return out


def enforced_floors(mesh) -> dict[str, float]:
    """Per-axis floor the repair pass must keep when enforcement is on.

    Empty when the switch is off. ``min_cellsize`` is the floor for every axis,
    ``min_cellsize_z`` overrides the layer thickness - the same reading the
    diagnostics use, so the report and the repair can never disagree.
    """
    if not bool(getattr(mesh, "enforce_min_cellsize", False)):
        return {}
    from app.meshing.coord_diag import requested_min_cellsize

    base = requested_min_cellsize(mesh)
    out: dict[str, float] = {}
    for ax in _AXES:
        value = requested_min_cellsize(mesh, axis=ax) or base
        if value:
            out[ax] = float(value)
    return out


def apply_mesh_repairs(
    csx,
    mesh,
    targets,
    *,
    protected: Mapping[str, Sequence[float]] | None = None,
    log: Any = None,
) -> list[AxisRepair]:
    """Apply the stored repair rules and the optional min-cell enforcement.

    Called from :func:`app.meshing.easy_wrapper.generate_mesh` (so the preview
    and the solver script agree). Returns the repairs that changed something.
    """
    unit_m = float(mesh.unit_to_meters())
    ratio = float(mesh.effective_max_grading_ratio())
    max_cell = float(getattr(targets, "max_cell_units", 0.0) or 0.0)
    prot = {ax: list((protected or {}).get(ax, ()) or ()) for ax in _AXES}
    lines = grid_lines(csx)
    applied: list[AxisRepair] = []

    def _say(msg: str) -> None:
        if log is not None:
            log(msg)
        else:
            print(msg)

    # 1) explicit rules (right-click "Repair this region")
    for spec in getattr(mesh, "mesh_repairs", None) or []:
        try:
            axis = str(spec.get("axis", ""))
            if axis not in _AXES or lines[axis].size < 3:
                continue
            rep = repair_axis(
                lines[axis],
                axis,
                float(spec.get("lo", 0.0)),
                float(spec.get("hi", 0.0)),
                floor=spec.get("floor"),
                ratio=ratio,
                max_cell=max_cell,
                protected=prot[axis],
                expand=int(spec.get("expand", 1) or 1),
            )
        except (TypeError, ValueError) as e:  # noqa: BLE001
            _say(f"  mesh repair skipped ({spec}): {e}")
            continue
        if rep is not None and rep.changed:
            apply_repairs(csx, [rep])
            lines[axis] = np.asarray(rep.after, dtype=float)
            applied.append(rep)
            _say(f"  mesh repair: {rep.axis} {rep.lo:.6g}..{rep.hi:.6g} "
                 f"min {rep.min_before:.6g} -> {rep.min_after:.6g}")

    # 2) enforce the requested min cell size (opt-in, V2)
    floors = enforced_floors(mesh)
    enforced: list[AxisRepair] = []
    if floors:
        for ax in _AXES:
            floor = floors.get(ax)
            if not floor or lines[ax].size < 3:
                continue
            rep = repair_axis(
                lines[ax],
                ax,
                float(lines[ax][0]),
                float(lines[ax][-1]),
                floor=float(floor),
                ratio=ratio,
                max_cell=max_cell,
                protected=prot[ax],
                expand=0,
            )
            if rep is not None and rep.changed:
                apply_repairs(csx, [rep])
                lines[ax] = np.asarray(rep.after, dtype=float)
                applied.append(rep)
                enforced.append(rep)
        if enforced:
            _say(
                "  min-cell enforcement: "
                + ", ".join(
                    f"{r.axis} {r.min_before:.6g}->{r.min_after:.6g}"
                    for r in enforced
                )
            )
    return applied


def _get(rep: Any, key: str, default: Any = None) -> Any:
    if isinstance(rep, Mapping):
        return rep.get(key, default)
    return getattr(rep, key, default)


def repair_report(applied: Sequence[Any], unit_m: float = 0.001) -> list[str]:
    """Report block for the mesh report (empty when nothing was repaired).

    Accepts :class:`AxisRepair` objects (live plan) and the dicts stored in
    ``setup["mesh_repairs"]`` / ``MeshSpec.mesh_repairs``.
    """
    if not applied:
        return []
    u = unit_label(unit_m)
    out = ["", "Mesh repairs (cells that only cost runtime):"]
    for rep in applied:
        axis = _get(rep, "axis", "?")
        lo = float(_get(rep, "lo", 0.0) or 0.0)
        hi = float(_get(rep, "hi", 0.0) or 0.0)
        floor = float(_get(rep, "floor", 0.0) or 0.0)
        out.append(
            f"  {axis}: {lo:.6g}..{hi:.6g} {u} - floor {floor:.6g} {u}"
        )
        removed = list(_get(rep, "removed", None) or [])
        if removed:
            out.append(
                "    removed: " + ", ".join(f"{v:.6g}" for v in sorted(removed))
            )
        warning = str(_get(rep, "warning", "") or "")
        if warning:
            out.append(f"    WARNING: {warning}")
    return out


def protected_coordinates(
    sources: Mapping[str, Sequence[tuple[float, str]]] | None,
) -> dict[str, list[float]]:
    """Authored coordinates per axis - the lines a repair must never remove."""
    out: dict[str, list[float]] = {ax: [] for ax in _AXES}
    for ax in _AXES:
        vals = sorted({float(v) for v, _tag in (sources or {}).get(ax, []) or []})
        out[ax] = vals
    return out
