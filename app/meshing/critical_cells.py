"""Locate the grid cells that cost runtime — the ones that set the timestep.

Read-only: nothing here changes the mesh. The result is a list of 3-D boxes in
drawing units that the Mesh Viewer draws on top of the grid, so the user can
*see* where a finding sits instead of only reading a line in the report.

Only cells that actually hurt the simulation are marked:

==================  ========================================================
``cell_below_min``   cell below half the requested ``min_cellsize``
``very_small_cell``  cell below ``max_cell / 500`` (``validators`` threshold)
``timestep_cell``    cell at the minimum of its axis (within float noise) on a
                     *graded* axis — it sets (or nearly sets) the FDTD timestep
``engine_timestep``  the cell the FDTD operator reported as the smallest
                     timestep (parsed from the run log)
==================  ========================================================

Neighbour grading jumps and cells that are too *large* are quality findings of
the mesh report (``validators``), not runtime problems: they are deliberately
**not** marked, because they cover large parts of the domain and would hide the
cells that matter.

Consecutive offending cells of one axis are merged into a single mark (a
region), so a 138-cell arc shows up as one band instead of 138 boxes. An axis
mark spans the whole domain in the other two axes (the grid is too fine at that
*position*); an ``engine_timestep`` box is the exact 3-D cell.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

#: Same threshold as ``app.meshing.validators`` (max_cell / 500).
TINY_CELL_FRACTION = 1.0 / 500.0
#: Relative tolerance for "this cell is the axis minimum". Only float noise: a
#: cell 1.000001× the smallest cell sets the same timestep to within a ppm.
TIMESTEP_REL_TOL = 1e-6
#: Default cap for the number of marks handed to the viewer (clusters, so this
#: is a lot: a real grid normally produces a handful).
MAX_MARKS = 24
#: Print order: the smallest cells hurt most.
_KIND_ORDER = {
    "cell_below_min": 0,
    "very_small_cell": 1,
    "timestep_cell": 2,
    "engine_timestep": 3,
}

_RE_ENGINE_CELL = re.compile(
    r"Smallest timestep\s*\(([-\d.eE+]+)s\)\s*found at position:\s*(\d+)\s*:\s*"
    r"(\d+);(\d+);(\d+)"
)


@dataclass
class CriticalCell:
    """One flagged grid cell as a 3-D box (drawing units)."""

    kind: str
    axis: int  # 0=x, 1=y, 2=z (the axis of the offending interval)
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]
    detail: str = ""
    #: authored source(s) at the cell faces (P2 attribution), "" when unknown
    source: str = ""
    #: smallest cell inside the mark (a merged region holds many cells); 0 when
    #: the mark is a single cell, then :attr:`size` is the cell size
    min_cell: float = 0.0

    @property
    def size(self) -> float:
        return float(self.hi[self.axis] - self.lo[self.axis])

    @property
    def cell_size(self) -> float:
        """Size of the smallest cell in the mark (not the width of a region)."""
        return float(self.min_cell) if self.min_cell > 0 else self.size

    @property
    def label(self) -> str:
        unit = {0: "x", 1: "y", 2: "z"}.get(self.axis, "?")
        text = f"{self.kind} ({unit}={self.cell_size:.4g}"
        if self.detail:
            text += f" {self.detail}"
        if self.source:
            text += f" | {self.source}"
        return text + ")"


def _domain(lines: Mapping[str, np.ndarray]) -> tuple[list[float], list[float]]:
    """Outermost lines per axis (±1 when an axis is missing)."""
    lo: list[float] = []
    hi: list[float] = []
    for axis in "xyz":
        arr = lines.get(axis)
        if arr is None or len(arr) == 0:
            lo.append(-1.0)
            hi.append(1.0)
            continue
        lo.append(float(np.min(arr)))
        hi.append(float(np.max(arr)))
    return lo, hi


def _box(
    axis: int,
    lo_v: float,
    hi_v: float,
    area_lo: Sequence[float],
    area_hi: Sequence[float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    lo = [float(v) for v in area_lo]
    hi = [float(v) for v in area_hi]
    lo[axis], hi[axis] = float(lo_v), float(hi_v)
    return tuple(lo), tuple(hi)  # type: ignore[return-value]


def find_critical_cells(
    lines: Mapping[str, Any],
    targets: Any = None,
    *,
    min_cellsize: float | None = None,
    max_marks: int = MAX_MARKS,
    sources: Mapping[str, Sequence[tuple[float, str]]] | None = None,
) -> list[CriticalCell]:
    """Flag the cells of one built grid (``{axis: lines}`` in drawing units).

    ``targets`` is the :class:`app.meshing.rules.MeshTargets` of the build (only
    ``max_cell_units`` is used); ``min_cellsize`` is the requested smallest cell
    (``MeshSpec``/probe).

    ``sources`` (optional) is ``{axis: [(coordinate, tag), …]}`` as returned by
    :func:`app.meshing.coord_diag.source_coordinates`: every mark is then
    attributed to the primitive / port / manual line that forced it (P2). Pass
    ``None`` to skip the attribution (``MeshSpec.diagnose_cell_sources``).
    """
    clean: dict[str, np.ndarray] = {}
    for axis in "xyz":
        arr = np.sort(np.asarray(lines.get(axis, []), dtype=float))
        if arr.size >= 2:
            clean[axis] = arr
    if len(clean) < 1:
        return []
    area_lo, area_hi = _domain(clean)
    max_cell = float(getattr(targets, "max_cell_units", 0.0) or 0.0)
    tiny = max_cell * TINY_CELL_FRACTION if max_cell > 0 else 0.0
    requested_min = float(min_cellsize or 0.0)

    out: list[CriticalCell] = []
    for axis_idx, axis in enumerate("xyz"):
        arr = clean.get(axis)
        if arr is None:
            continue
        cells = np.diff(arr)
        # The FDTD timestep is set by the *smallest cell of each axis*. Only
        # those cells are marked (plus the absolute "below min / very small"
        # findings): a grading jump or a uniform grid is not a problem cell.
        axis_min = float(np.min(cells))
        # A (nearly) uniform axis has no outlier to fix: the min cell is the
        # typical cell. Only a graded axis can have cells that cost timestep.
        graded = float(np.max(cells)) > 2.0 * axis_min
        kinds: list[str] = []
        for cell in cells:
            if tiny and cell < tiny:
                kinds.append("very_small_cell")
            elif requested_min and cell < 0.5 * requested_min:
                kinds.append("cell_below_min")
            elif graded and cell <= axis_min * (1.0 + TIMESTEP_REL_TOL):
                kinds.append("timestep_cell")
            else:
                kinds.append("")
        # Merge consecutive offending cells into one region: a 138-cell arc
        # becomes one band instead of 138 boxes.
        i = 0
        while i < cells.size:
            if not kinds[i]:
                i += 1
                continue
            j = i
            while j + 1 < cells.size and kinds[j + 1]:
                j += 1
            block = kinds[i:j + 1]
            kind = next(
                (k for k in ("cell_below_min", "very_small_cell", "timestep_cell")
                 if k in block),
                "timestep_cell",
            )
            smallest_in = float(np.min(cells[i:j + 1]))
            n = j - i + 1
            detail = f"{n} cells, min {smallest_in:.4g}" if n > 1 else ""
            lo, hi = _box(axis_idx, arr[i], arr[j + 1], area_lo, area_hi)
            out.append(
                CriticalCell(
                    kind, axis_idx, lo, hi, detail, min_cell=smallest_in
                )
            )
            i = j + 1
    out.sort(key=lambda c: (_KIND_ORDER.get(c.kind, 9), c.cell_size))
    out = out[:max_marks]
    if sources:
        attribute_cells(out, sources)
    return out


#: Tolerance for "this authored coordinate is the cell face", as a fraction of
#: the cell width. easyMesh copies a source coordinate onto its grid line, so a
#: match is normally exact; 5 % also catches a line that was snapped by the
#: dedupe / SmoothMeshLines.
SOURCE_MATCH_FRACTION = 0.05


def attribute_cells(
    cells: Sequence[CriticalCell],
    sources: Mapping[str, Sequence[tuple[float, str]]],
    *,
    fraction: float = SOURCE_MATCH_FRACTION,
) -> list[CriticalCell]:
    """Set ``cell.source`` to the authored coordinate(s) at the cell faces.

    P2: this answers *which primitive / port / manual line forced the fine cell*
    instead of only where it sits. Both faces are checked, so a cell that exists
    because two sources are 1 µm apart is reported as ``"box:a + box:b"``.
    """
    for cell in cells:
        tagged = sources.get("xyz"[cell.axis]) or []
        if not tagged:
            continue
        width = abs(float(cell.hi[cell.axis]) - float(cell.lo[cell.axis]))
        tol = max(1e-9, float(fraction) * width)
        hits: list[str] = []
        for face in (cell.lo[cell.axis], cell.hi[cell.axis]):
            best: tuple[float, str] | None = None
            for value, tag in tagged:
                try:
                    dist = abs(float(value) - float(face))
                except (TypeError, ValueError):
                    continue
                if dist <= tol and (best is None or dist < best[0]):
                    best = (dist, str(tag))
            if best is not None and best[1] not in hits:
                hits.append(best[1])
        if hits:
            cell.source = " + ".join(hits)
    return list(cells)


def source_summary(cells: Sequence[CriticalCell]) -> list[str]:
    """Report lines: how many flagged cells each source causes (P2)."""
    if not cells:
        return []
    counts: dict[str, int] = {}
    for cell in cells:
        tag = cell.source or "easyMesh (no authored source)"
        counts[tag] = counts.get(tag, 0) + 1
    out = ["Fine-cell sources (which geometry forced them):"]
    for tag, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"  {n:4d}  {tag}")
    if any(not c.source for c in cells):
        out.append(
            "  (cells without an authored source come from easyMesh itself: "
            "circle/arc detection, the graded interface or the PML transition)"
        )
    return out


def parse_engine_cell(
    log_text: str, lines: Mapping[str, Any]
) -> CriticalCell | None:
    """The cell the FDTD operator reported as the smallest timestep.

    openEMS prints ``Operator::CalcTimestep_Var3: Smallest timestep (2.29e-13s)
    found at position: 1 : 39;14;0``. The three indices are cell indices; they
    are clamped to the grid so a shifted report can never crash the viewer.
    """
    match = _RE_ENGINE_CELL.search(log_text or "")
    if not match:
        return None
    indices = [int(match.group(n)) for n in (3, 4, 5)]
    lo: list[float] = []
    hi: list[float] = []
    for axis, name in enumerate("xyz"):
        arr = np.sort(np.asarray(lines.get(name, []), dtype=float))
        if arr.size < 2:
            return None
        i = max(0, min(indices[axis], arr.size - 2))
        lo.append(float(arr[i]))
        hi.append(float(arr[i + 1]))
    try:
        dt_ps = float(match.group(1)) * 1e12
        detail = f"dt {dt_ps:.4g} ps"
    except (TypeError, ValueError):
        detail = ""
    direction = int(match.group(2)) % 3
    return CriticalCell("engine_timestep", direction, tuple(lo), tuple(hi), detail)


def summary_line(cells: Sequence[CriticalCell]) -> str:
    """One log line for the mesh report / status report."""
    if not cells:
        return "Critical cells: none"
    counts: dict[str, int] = {}
    for c in cells:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    parts = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    return f"Critical cells: {len(cells)} ({parts})"
