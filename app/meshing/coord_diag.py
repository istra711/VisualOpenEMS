"""Phase-0 diagnostics for the coordinate-quantization plan (read-only).

Measures what the plan ("Coordinate Quantization Plan", §7 Phase 0) has to
decide on — **without changing anything**:

* near-duplicate grid lines, bucketed by distance (1e-9 … 1e-3 units), so the
  *magnitude* is visible instead of assumed
* the smallest cell against the requested ``min_cellsize``
* authored coordinates (box / polyhedron / polygon / port / dump / NF2FF /
  manual mesh line): which pairs sit below the planned lattice tolerance and
  which ones have no grid line at all

The numbers decide whether the lattice (Phase 2/3) is worth its risk. Nothing
in here modifies the CSX, the grid or the run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from app.meshing.bounds import cartesian_primitive_bbox
from app.meshing.validators import Issue

#: Distance decades (project units) used to bucket near-duplicate lines, with
#: the label the report prints for each bucket.
BUCKETS: tuple[tuple[float, str], ...] = (
    (1e-9, "<1e-9"),
    (1e-8, "<1e-8"),
    (1e-7, "<1e-7"),
    (1e-6, "<1e-6"),
    (1e-5, "<1e-5"),
    (1e-4, "<1e-4"),
    (1e-3, "<1e-3"),
)
#: Everything above this is a "normal" cell, not a duplicate.
PAIR_LIMIT = 1e-3
#: A cell below this fraction of the requested min_cellsize is suspicious.
MIN_CELL_FRACTION = 0.5
#: A coordinate within this fraction of the local cell counts as "snapped"
#: (the same rule ``port_align``/``validators`` use for port faces).
SNAP_FRACTION = 0.15
#: How many example pairs the report prints per category.
MAX_EXAMPLES = 6


@dataclass
class TargetHit:
    """One authored coordinate measured against the grid of its axis."""

    axis: str
    value: float
    tag: str
    dist: float
    cell: float
    #: "on_line" | "snapped" | "off_grid"
    cls: str = "off_grid"

    @property
    def kind(self) -> str:
        return self.tag.split(":")[0]

    @property
    def ratio(self) -> float:
        return self.dist / self.cell if self.cell > 0 else float("inf")


@dataclass
class NearPair:
    """Two grid lines (or two authored coordinates) closer than the limit."""

    axis: str
    a: float
    b: float
    delta: float
    tag_a: str = ""
    tag_b: str = ""

    @property
    def bucket(self) -> str:
        for edge, label in BUCKETS:
            if self.delta < edge:
                return label
        return f">={BUCKETS[-1][1][1:]}"


@dataclass
class AxisDiag:
    axis: str
    n_lines: int = 0
    min_cell: float = 0.0
    median_cell: float = 0.0
    max_cell: float = 0.0
    line_pairs: list[NearPair] = field(default_factory=list)
    n_targets: int = 0
    target_pairs: list[NearPair] = field(default_factory=list)
    #: every authored coordinate of this axis with its distance to the grid
    hits: list[TargetHit] = field(default_factory=list)

    @property
    def off_grid(self) -> list[TargetHit]:
        return [h for h in self.hits if h.cls == "off_grid"]

    @property
    def snapped(self) -> list[TargetHit]:
        return [h for h in self.hits if h.cls == "snapped"]

    @property
    def on_line(self) -> list[TargetHit]:
        return [h for h in self.hits if h.cls == "on_line"]

    def class_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for h in self.hits:
            out[h.cls] = out.get(h.cls, 0) + 1
        return out

    def bucket_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.line_pairs:
            out[p.bucket] = out.get(p.bucket, 0) + 1
        return out


@dataclass
class CoordDiag:
    unit: str = "mm"
    unit_m: float = 0.001
    quantum_units: float | None = None
    tol_units: float = 0.0
    min_cellsize_units: float | None = None
    #: whether the coordinates are aligned on the float32 grid (MeshSpec)
    align_coordinates: bool = True
    axes: dict[str, AxisDiag] = field(default_factory=dict)
    source_counts: dict[str, int] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def line_pairs(self) -> list[NearPair]:
        return [p for ax in self.axes.values() for p in ax.line_pairs]

    @property
    def target_pairs(self) -> list[NearPair]:
        return [p for ax in self.axes.values() for p in ax.target_pairs]

    @property
    def off_grid(self) -> list[TargetHit]:
        return [h for ax in self.axes.values() for h in ax.off_grid]

    @property
    def snapped(self) -> list[TargetHit]:
        return [h for ax in self.axes.values() for h in ax.snapped]

    @property
    def port_off_grid(self) -> list[TargetHit]:
        return [h for h in self.off_grid if h.kind == "port"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]


# --------------------------------------------------------------------------- #
# planned lattice (informational: what Phase 2 would choose)
# --------------------------------------------------------------------------- #
def min_cellsize_units(mesh, targets=None) -> float | None:
    """Requested smallest cell in project units (``None`` when unknown).

    Order: ``min_cellsize`` → ``min_cellsize_z`` → the effective
    ``refined_cellsize`` (which antenna mode / the lambda-density cap derives)
    → ``target_cell_units``.
    """
    for value in (
        getattr(mesh, "min_cellsize", None),
        getattr(mesh, "min_cellsize_z", None),
    ):
        try:
            v = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    refined = getattr(mesh, "refined_cellsize", None)
    try:
        effective = mesh.effective_mesh()
        if getattr(effective, "refined_cellsize", None):
            refined = effective.refined_cellsize
    except Exception:  # noqa: BLE001 — informational only
        pass
    try:
        if refined and float(refined) > 0:
            return float(refined)
    except (TypeError, ValueError):
        pass
    tgt = getattr(targets, "target_cell_units", None)
    try:
        return float(tgt) if tgt and float(tgt) > 0 else None
    except (TypeError, ValueError):
        return None


def planned_quantum_units(mesh, targets=None) -> float | None:
    """Lattice step the *corrected* auto rule would pick, in project units.

    ``N = clamp(ceil(3 - log10(c_min)), 2, 7)`` — i.e. ``q <= c_min / 1000``,
    which is the rule the plan states but (in its current formula) does not
    implement. Informational for Phase 0: it shows how many coordinate pairs a
    later lattice would merge.
    """
    c_min = min_cellsize_units(mesh, targets)
    if not c_min or c_min <= 0:
        return None
    n = max(2, min(7, math.ceil(3.0 - math.log10(c_min))))
    return 10.0 ** (-n)


# --------------------------------------------------------------------------- #
# authored coordinates
# --------------------------------------------------------------------------- #
def source_coordinates(
    project=None,
    primitives: Sequence[Mapping[str, Any]] | None = None,
    manual_lines: Mapping[str, Iterable[float]] | None = None,
) -> dict[str, list[tuple[float, str]]]:
    """Every authored coordinate per axis, tagged with its source."""
    out: dict[str, list[tuple[float, str]]] = {"x": [], "y": [], "z": []}

    def add(values, tag: str, axes: str = "xyz") -> None:
        if values is None:
            return
        seq = list(values)
        for i, ax in enumerate(axes):
            if i < len(seq):
                try:
                    out[ax].append((float(seq[i]), tag))
                except (TypeError, ValueError):
                    continue

    for pr in primitives or []:
        name = str(pr.get("name", "?"))
        kind = str(pr.get("kind", "box"))
        tag = f"{kind}:{name}"
        if kind == "box":
            add(pr.get("start"), tag)
            add(pr.get("stop"), tag)
        elif kind == "polygon":
            axis = str(pr.get("axis", "z")).lower()
            plane = "".join(a for a in "xyz" if a != axis)
            for pt in pr.get("points") or []:
                add(pt, tag, plane)
            add([pr.get("coord")], tag, axis)
        else:  # cylinder / sphere / stl -> bounding box faces
            bb = None
            try:
                bb = cartesian_primitive_bbox(pr)
            except Exception:  # noqa: BLE001 — informational only
                bb = None
            if bb is not None:
                add(bb[0], tag)
                add(bb[1], tag)

    for port in getattr(project, "ports", []) or []:
        if not bool(getattr(port, "active", True)):
            continue
        add(getattr(port, "start", None), f"port:{port.name}")
        add(getattr(port, "stop", None), f"port:{port.name}")
    for dump in getattr(project, "dumps", []) or []:
        add(getattr(dump, "start", None), f"dump:{dump.name}")
        add(getattr(dump, "stop", None), f"dump:{dump.name}")
    nf2ff = getattr(project, "nf2ff", None)
    if nf2ff is not None and bool(getattr(nf2ff, "enabled", False)):
        add(getattr(nf2ff, "start", None), "nf2ff")
        add(getattr(nf2ff, "stop", None), "nf2ff")

    for axis, values in (manual_lines or {}).items():
        if axis not in out:
            continue
        for value in values or []:
            try:
                out[axis].append((float(value), "manual"))
            except (TypeError, ValueError):
                continue
    return out


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #
def _pairs_within(
    values: Sequence[tuple[float, str]], limit: float
) -> list[tuple[float, float, float, str, str]]:
    ordered = sorted(values, key=lambda item: item[0])
    out = []
    for (a, ta), (b, tb) in zip(ordered, ordered[1:]):
        delta = b - a
        if 0.0 < delta < limit:
            out.append((a, b, delta, ta, tb))
    return out


def _local_cell(arr: np.ndarray, value: float) -> float:
    """Cell size next to ``value`` — the notion ``port_align`` uses."""
    if arr.size < 2:
        return 0.0
    idx = int(np.argmin(np.abs(arr - value)))
    cands = []
    if idx > 0:
        cands.append(float(arr[idx] - arr[idx - 1]))
    if idx + 1 < arr.size:
        cands.append(float(arr[idx + 1] - arr[idx]))
    return min(cands) if cands else 0.0


def analyse_axis(
    axis: str,
    *,
    lines: np.ndarray | None,
    targets: Sequence[tuple[float, str]] = (),
    tol: float = 0.0,
    pair_limit: float = PAIR_LIMIT,
) -> AxisDiag:
    """Near-duplicate lines and off-lattice targets of one axis."""
    diag = AxisDiag(axis=axis)
    arr = np.asarray(lines, dtype=float) if lines is not None else np.asarray([])
    if arr.size >= 2:
        cells = np.diff(np.sort(arr))
        cells = cells[cells > 0]
        if cells.size:
            diag.n_lines = int(arr.size)
            diag.min_cell = float(cells.min())
            diag.median_cell = float(np.median(cells))
            diag.max_cell = float(cells.max())
    if arr.size >= 2:
        for a, b, delta, _ta, _tb in _pairs_within(
            [(float(v), "line") for v in arr], pair_limit
        ):
            diag.line_pairs.append(NearPair(axis, a, b, delta, "line", "line"))

    tagged = [(float(v), str(tag)) for v, tag in targets]
    diag.n_targets = len(tagged)
    for a, b, delta, ta, tb in _pairs_within(tagged, tol or 0.0):
        diag.target_pairs.append(NearPair(axis, a, b, delta, ta, tb))
    if arr.size >= 2:
        for value, tag in tagged:
            dist = float(np.min(np.abs(arr - value)))
            cell = _local_cell(arr, value)
            if cell <= 0:
                cls = "on_line" if dist <= 0 else "off_grid"
            elif dist <= 1e-9 * max(1.0, abs(value)):
                cls = "on_line"
            elif dist <= SNAP_FRACTION * cell:
                cls = "snapped"
            else:
                cls = "off_grid"
            diag.hits.append(TargetHit(axis, value, tag, dist, cell, cls))
    return diag


def analyse_coord_diag(
    csx=None,
    project=None,
    primitives: Sequence[Mapping[str, Any]] | None = None,
    mesh=None,
    targets=None,
    manual_lines: Mapping[str, Iterable[float]] | None = None,
    *,
    unit: str = "mm",
    unit_m: float = 0.001,
) -> CoordDiag:
    """Phase-0 probe: measure, never modify.

    ``csx`` may be ``None`` (scene without a mesh): the authored-coordinate
    analysis still runs, the grid part stays empty.
    """
    diag = CoordDiag(unit=unit, unit_m=float(unit_m))
    diag.quantum_units = planned_quantum_units(mesh, targets) if mesh is not None else None
    diag.tol_units = 0.5 * diag.quantum_units if diag.quantum_units else 0.0
    diag.min_cellsize_units = min_cellsize_units(mesh, targets) if mesh is not None else None
    diag.align_coordinates = (
        bool(getattr(mesh, "align_coordinates", True)) if mesh is not None else True
    )

    grid_lines: dict[str, np.ndarray] = {}
    if csx is not None:
        try:
            grid = csx.GetGrid()
        except Exception:  # noqa: BLE001 — report instead of raising
            grid = None
        if grid is not None:
            for ax in "xyz":
                try:
                    grid_lines[ax] = np.asarray(grid.GetLines(ax), dtype=float)
                except Exception:  # noqa: BLE001
                    grid_lines[ax] = np.asarray([], dtype=float)

    sources = source_coordinates(project, primitives, manual_lines)
    diag.source_counts = {}
    for values in sources.values():
        for _v, tag in values:
            diag.source_counts[tag.split(":")[0]] = (
                diag.source_counts.get(tag.split(":")[0], 0) + 1
            )

    for ax in "xyz":
        diag.axes[ax] = analyse_axis(
            ax,
            lines=grid_lines.get(ax),
            targets=sources.get(ax, []),
            tol=diag.tol_units,
        )

    # --- findings (informational: Phase 0 does not act on them) ------------ #
    n_line = len(diag.line_pairs)
    if n_line:
        worst = min(p.delta for p in diag.line_pairs)
        diag.issues.append(
            Issue(
                "info",
                "double_line",
                f"{n_line} near-duplicate grid line pair(s) below "
                f"{PAIR_LIMIT:g} {unit}; closest {worst:.3g} {unit} "
                f"(a later lattice with q={_fmt(diag.quantum_units, unit)} would "
                f"merge {len([p for p in diag.line_pairs if diag.tol_units and p.delta < diag.tol_units])})",
            )
        )
    n_tgt = len(diag.target_pairs)
    if n_tgt and diag.tol_units:
        tags = {f"{p.tag_a} <-> {p.tag_b}" for p in diag.target_pairs}
        diag.issues.append(
            Issue(
                "info",
                "coord_pair",
                f"{n_tgt} authored coordinate pair(s) closer than the planned "
                f"tolerance {diag.tol_units:g} {unit} "
                f"({', '.join(sorted(tags)[:4])})",
            )
        )
    off = diag.off_grid
    if off or diag.snapped:
        parts: list[str] = []
        ports = diag.port_off_grid
        if ports:
            worst = max(ports, key=lambda h: h.ratio)
            parts.append(
                f"{len(ports)} port coordinate(s) sit {worst.ratio:.0%} of the local "
                f"cell ({worst.cell:.4g} {unit}) away from the next grid line — worst "
                f"{worst.value:.6g} {unit}, '{worst.tag}' (openEMS snaps the port box there)"
            )
        others = [h for h in off if h.kind != "port"]
        if others:
            kinds = sorted({h.kind for h in others})
            parts.append(
                f"{len(others)} coordinate(s) of {', '.join(kinds)} are more than "
                f"{SNAP_FRACTION:.0%} of a cell off the grid (harmless for NF2FF/dumps, "
                "relevant for geometry faces)"
            )
        if diag.snapped:
            parts.append(
                f"{len(diag.snapped)} coordinate(s) within {SNAP_FRACTION:.0%} of a cell "
                "of the next line — the nearest line represents them (no extra line "
                "needed; ports are snapped by openEMS by that amount)"
            )
        diag.issues.append(
            Issue("warning" if ports else "info", "coord_off_line", "; ".join(parts))
        )
    c_min = diag.min_cellsize_units
    min_cell = min((a.min_cell for a in diag.axes.values() if a.min_cell), default=0.0)
    if c_min and min_cell and min_cell < MIN_CELL_FRACTION * c_min:
        axis = min(
            (a for a in diag.axes.values() if a.min_cell),
            key=lambda a: a.min_cell,
        )
        diag.issues.append(
            Issue(
                "warning",
                "cell_below_min",
                f"smallest cell {min_cell:.4g} {unit} ({axis.axis}) is "
                f"{c_min / min_cell:.1f}x below the requested min_cellsize "
                f"{c_min:.4g} {unit}",
            )
        )
    return diag


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def _fmt(value: float | None, unit: str) -> str:
    return f"{value:g} {unit}" if value else "n/a"


def summary_line(diag: CoordDiag) -> str:
    """One log line with the Phase-0 numbers."""
    min_cell = min((a.min_cell for a in diag.axes.values() if a.min_cell), default=0.0)
    ratio = (
        f"{diag.min_cellsize_units / min_cell:.1f}x min_cellsize"
        if diag.min_cellsize_units and min_cell
        else "min_cellsize n/a"
    )
    return (
        f"Coord probe: {len(diag.line_pairs)} near-duplicate line pair(s) "
        f"(<{PAIR_LIMIT:g} {diag.unit}), {len(diag.target_pairs)} authored pair(s) "
        f"below {_fmt(diag.tol_units, diag.unit)}, {len(diag.off_grid)} coordinate(s) "
        f"off the grid ({len(diag.port_off_grid)} of them ports), "
        f"{len(diag.snapped)} snapped; smallest cell {min_cell:.4g} {diag.unit} ({ratio})"
    )


def report_lines(diag: CoordDiag, *, examples: int = MAX_EXAMPLES) -> list[str]:
    """Human-readable block for the mesh report."""
    out: list[str] = ["", "Coordinate quantization probe (Phase 0, read-only):"]
    out.append(
        f"  Planned lattice  : q = {_fmt(diag.quantum_units, diag.unit)}"
        f"  (tolerance {_fmt(diag.tol_units, diag.unit)})"
    )
    out.append(
        "  Coordinates      : "
        + (
            "aligned on the float32 grid (every source bit-identical)"
            if diag.align_coordinates
            else "LEGACY: triangle meshes rounded to 0.1 um"
        )
    )
    out.append(
        f"  Requested min cell: {_fmt(diag.min_cellsize_units, diag.unit)}"
    )
    src = ", ".join(f"{k} {v}" for k, v in sorted(diag.source_counts.items()))
    out.append(f"  Authored coords  : {src or 'none'}")
    for ax in "xyz":
        a = diag.axes.get(ax)
        if a is None:
            continue
        if a.n_lines:
            out.append(
                f"  Grid {ax}           : {a.n_lines} lines | cell min "
                f"{a.min_cell:.4g} / median {a.median_cell:.4g} / max {a.max_cell:.4g}"
            )
        else:
            out.append(f"  Grid {ax}           : (no mesh)")
        if a.line_pairs:
            buckets = ", ".join(f"{k}: {v}" for k, v in sorted(a.bucket_counts().items()))
            out.append(f"    near-duplicate lines ({buckets}):")
            for p in sorted(a.line_pairs, key=lambda p: p.delta)[:examples]:
                out.append(
                    f"      {p.delta:.3g} {diag.unit}: {p.a:.9g} vs {p.b:.9g}"
                )
        if a.target_pairs:
            out.append("    authored pairs below tolerance:")
            for p in sorted(a.target_pairs, key=lambda p: p.delta)[:examples]:
                out.append(
                    f"      {p.delta:.3g} {diag.unit}: {p.tag_a} {p.a:.9g} vs "
                    f"{p.tag_b} {p.b:.9g}"
                )
        if a.hits:
            counts = ", ".join(f"{k} {v}" for k, v in sorted(a.class_counts().items()))
            out.append(f"    coordinates: {counts}")
            for h in sorted(a.off_grid, key=lambda h: -h.ratio)[:examples]:
                out.append(
                    f"      {h.value:.9g} ({h.tag}) — {h.dist:.3g} {diag.unit} = "
                    f"{h.ratio:.0%} of the local cell ({h.cell:.4g} {diag.unit})"
                )
    if diag.issues:
        out.append("  Findings:")
        for issue in diag.issues:
            out.append(f"    [{issue.severity.upper()}] {issue.code}: {issue.message}")
    else:
        out.append("  Findings         : none (no near-duplicate lines, all coordinates on the grid)")
    return out
