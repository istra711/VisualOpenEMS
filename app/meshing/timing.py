"""Mesh timing and resolution diagnostics (Qt-free).

Everything here is derived from the *built* mesh plus the simulation settings,
so it can be reported right after meshing — before a single FDTD step runs:

* smallest / largest / typical cell per axis and the Courant timestep limit
* the runtime penalty of an over-refined cell against a typical cell
* cells per wavelength at f_max
* the Nyquist limit of the timestep
* the frequency resolution one run can deliver versus the requested sweep
* the minimum number of timesteps for a wave to cross the domain

The engine prints the *exact* timestep once the FDTD operator is built
(``FDTD timestep is: 2.29369e-13 s; Nyquist rate: 682 timesteps @3.196e+09 Hz``).
The numbers here are the mesh-only *estimates* available before a run;
:mod:`app.simulation.report` compares them against the engine's values.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

import numpy as np

from app.meshing.validators import Issue

C0 = 299792458.0

#: Warn when the smallest cell is this many times smaller than the typical cell
#: (the timestep, and with it the whole runtime, scales with the smallest cell).
PENALTY_WARN = 5.0
#: Minimum cells per wavelength at f_max for a trustworthy result.
CELLS_PER_LAMBDA_MIN = 10.0
#: How many domain crossings a run should be able to afford (settling).
MIN_DOMAIN_CROSSINGS = 5.0


@dataclass
class AxisTiming:
    """Cell statistics of one grid axis (all lengths in metres)."""

    name: str
    n_cells: int
    min_cell_m: float
    min_cell_at_units: float
    median_cell_m: float
    max_cell_m: float


@dataclass
class MeshTiming:
    """Mesh-imposed timing/resolution picture for one build."""

    unit_m: float = 0.001
    axes: dict[str, AxisTiming] = field(default_factory=dict)
    cells_total: int = 0
    #: Courant limit in pure vacuum from the openEMS mesh estimate
    dt_courant_s: float = 0.0
    #: lower bound of the actual timestep: the densest material slows the
    #: smallest cells down by up to sqrt(eps_r)
    dt_dielectric_s: float = 0.0
    eps_max: float = 1.0
    f_max_hz: float = 0.0
    nyquist_hz: float = 0.0
    cells_per_lambda: dict[str, float] = field(default_factory=dict)
    domain_diagonal_m: float = 0.0
    ts_cross_domain: float = 0.0
    nr_ts: int = 0
    #: requested S-parameter point spacing of the sweep (Hz)
    target_resolution_hz: float = 0.0
    #: DFT window resolution 1/(NrTS*dt) for the two timestep bounds (Hz)
    resolution_best_hz: float = 0.0
    resolution_worst_hz: float = 0.0
    #: NrTS needed so that even the best case resolves the sweep step
    nr_ts_for_resolution: int = 0
    refinement_penalty: float = 1.0
    penalty_axis: str = ""
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _axis_timing(name: str, lines: np.ndarray, unit_m: float) -> AxisTiming | None:
    vals = np.sort(np.asarray(lines, dtype=float))
    if vals.size < 2:
        return None
    cells = np.diff(vals)
    keep = cells > 0
    if not keep.any():
        return None
    vals = vals[:-1][keep]
    cells = cells[keep]
    idx = int(np.argmin(cells))
    return AxisTiming(
        name=name,
        n_cells=int(cells.size),
        min_cell_m=float(cells[idx]) * unit_m,
        min_cell_at_units=float(vals[idx] + 0.5 * cells[idx]),
        median_cell_m=float(np.median(cells)) * unit_m,
        max_cell_m=float(np.max(cells)) * unit_m,
    )


def courant_timestep(lines_units: Mapping[str, np.ndarray], unit_m: float) -> float:
    """Vacuum CFL limit from the smallest cell of every axis (seconds).

    Same formula openEMS uses in ``automesh.mesh_estimate_cfl_timestep``; kept
    local so the analysis also works without an openEMS import.
    """
    inv = 0.0
    for arr in lines_units.values():
        vals = np.sort(np.asarray(arr, dtype=float))
        if vals.size < 2:
            continue
        cells = np.diff(vals)
        cells = cells[cells > 0]
        if cells.size:
            inv += 1.0 / float(np.min(cells)) ** 2
    if inv <= 0.0:
        return 0.0
    return float(unit_m) / (C0 * math.sqrt(inv))


def analyse_lines(
    lines_units: Mapping[str, np.ndarray],
    *,
    unit_m: float,
    f_max_hz: float,
    eps_max: float = 1.0,
    nr_ts: int = 0,
    sweep_points: int = 201,
    sweep_span_hz: float = 0.0,
) -> MeshTiming:
    """Pure analysis of grid lines given in project units (no CSX needed)."""
    rep = MeshTiming(unit_m=float(unit_m), eps_max=max(1.0, float(eps_max)),
                     f_max_hz=float(f_max_hz), nr_ts=int(nr_ts or 0))
    axes: dict[str, AxisTiming] = {}
    for name in ("x", "y", "z"):
        arr = lines_units.get(name)
        if arr is None:
            continue
        ax = _axis_timing(name, arr, unit_m)
        if ax is not None:
            axes[name] = ax
    rep.axes = axes
    if not axes:
        rep.issues.append(Issue("error", "empty_mesh", "No usable grid lines to analyse"))
        return rep

    rep.cells_total = int(np.prod([a.n_cells for a in axes.values()]))
    rep.dt_courant_s = courant_timestep(lines_units, unit_m)
    rep.dt_dielectric_s = rep.dt_courant_s / math.sqrt(rep.eps_max)
    rep.nyquist_hz = (1.0 / (2.0 * rep.dt_dielectric_s)) if rep.dt_dielectric_s else 0.0

    # refinement penalty: an over-fine cell sets dt for the whole run
    for ax in axes.values():
        if ax.min_cell_m > 0:
            pen = ax.median_cell_m / ax.min_cell_m
            if pen > rep.refinement_penalty:
                rep.refinement_penalty = pen
                rep.penalty_axis = ax.name

    # cells per wavelength at f_max per axis (geometry resolution)
    lam_min_m = C0 / f_max_hz if f_max_hz else 0.0
    for ax in axes.values():
        if lam_min_m and ax.max_cell_m:
            rep.cells_per_lambda[ax.name] = lam_min_m / ax.max_cell_m

    # domain extent from the outermost lines
    span = {}
    for name, arr in lines_units.items():
        vals = np.asarray(arr, dtype=float)
        if vals.size >= 2:
            span[name] = float(vals.max() - vals.min()) * unit_m
    rep.domain_diagonal_m = math.sqrt(sum(v * v for v in span.values()))
    if rep.dt_courant_s and rep.domain_diagonal_m:
        v_worst = C0 / math.sqrt(rep.eps_max)
        rep.ts_cross_domain = rep.domain_diagonal_m / (v_worst * rep.dt_courant_s)

    # DFT window resolution of one run
    if sweep_span_hz and sweep_points > 1:
        rep.target_resolution_hz = float(sweep_span_hz) / (sweep_points - 1)
    if rep.nr_ts:
        if rep.dt_courant_s:
            rep.resolution_best_hz = 1.0 / (rep.nr_ts * rep.dt_courant_s)
        if rep.dt_dielectric_s:
            rep.resolution_worst_hz = 1.0 / (rep.nr_ts * rep.dt_dielectric_s)
        if rep.target_resolution_hz and rep.dt_courant_s:
            rep.nr_ts_for_resolution = int(
                math.ceil(1.0 / (rep.target_resolution_hz * rep.dt_courant_s))
            )

    # --- verdicts ---------------------------------------------------------
    if rep.refinement_penalty > PENALTY_WARN:
        ax = axes.get(rep.penalty_axis)
        where = ""
        if ax is not None:
            where = (
                f" at {rep.penalty_axis}={ax.min_cell_at_units:.6g} "
                f"{_unit_name(unit_m)}"
            )
        rep.issues.append(
            Issue(
                "warning",
                "tiny_cell_slows_run",
                f"Smallest cell ({rep.penalty_axis} axis{where}) is "
                f"{rep.refinement_penalty:.1f}x smaller than the typical cell — "
                "the timestep (and the whole runtime) scales with it; consider "
                "relaxing the mesh there if that detail is not needed",
            )
        )
    worst_cells = min(rep.cells_per_lambda.values()) if rep.cells_per_lambda else 0.0
    if worst_cells and worst_cells < CELLS_PER_LAMBDA_MIN:
        rep.issues.append(
            Issue(
                "error",
                "cells_per_lambda_low",
                f"Only {worst_cells:.1f} cells per wavelength at f_max="
                f"{f_max_hz / 1e9:.4g} GHz — the geometry is under-resolved "
                f"(need >= {CELLS_PER_LAMBDA_MIN:.0f})",
            )
        )
    if rep.nyquist_hz and f_max_hz > rep.nyquist_hz:
        rep.issues.append(
            Issue(
                "error",
                "timestep_too_coarse",
                f"f_max={f_max_hz / 1e9:.4g} GHz is above the Nyquist limit of the "
                f"timestep ({rep.nyquist_hz / 1e9:.4g} GHz) — refine the mesh",
            )
        )
    if rep.nr_ts and rep.ts_cross_domain:
        need = MIN_DOMAIN_CROSSINGS * rep.ts_cross_domain
        if rep.nr_ts < need:
            rep.issues.append(
                Issue(
                    "warning",
                    "timesteps_too_few",
                    f"max_timesteps={rep.nr_ts} is below {need:.0f} timesteps "
                    f"({MIN_DOMAIN_CROSSINGS:.0f} domain crossings of "
                    f"{rep.ts_cross_domain:.0f}) — the pulse may not settle",
                )
            )
    if rep.target_resolution_hz and rep.resolution_worst_hz:
        if rep.resolution_worst_hz > rep.target_resolution_hz:
            hint = (
                f"raise max_timesteps to >= {rep.nr_ts_for_resolution}"
                if rep.nr_ts_for_resolution > rep.nr_ts
                else "accept the coarser resolution"
            )
            rep.issues.append(
                Issue(
                    "warning",
                    "resolution_limited",
                    f"Sweep step {rep.target_resolution_hz / 1e6:.3g} MHz is finer "
                    f"than the DFT window of one run "
                    f"({rep.resolution_worst_hz / 1e6:.3g}.."
                    f"{rep.resolution_best_hz / 1e6:.3g} MHz for "
                    f"NrTS={rep.nr_ts}) — the curve is window-limited; {hint}",
                )
            )
    return rep


def analyse_mesh_timing(csx, mesh, sim, targets, eps_max: float | None = None) -> MeshTiming:
    """Analyse the built CSX grid (``csx.GetGrid()``) against mesh/sim settings.

    ``eps_max`` is the highest relative permittivity of the project's materials:
    it bounds how much the densest cells slow the timestep down. ``None`` falls
    back to the meshing targets (which the app builds for air, eps_r = 1).
    """
    lines: dict[str, np.ndarray] = {}
    try:
        grid = csx.GetGrid()
    except Exception:  # noqa: BLE001 — report instead of raising into the build
        return MeshTiming()
    for d in "xyz":
        try:
            lines[d] = np.asarray(grid.GetLines(d), dtype=float)
        except Exception:  # noqa: BLE001
            lines[d] = np.asarray([], dtype=float)
    span_hz = abs(float(mesh.stop_frequency) - float(mesh.start_frequency))
    eps = float(eps_max) if eps_max else float(getattr(targets, "eps_max", 1.0) or 1.0)
    return analyse_lines(
        lines,
        unit_m=float(mesh.unit_to_meters()),
        f_max_hz=float(getattr(targets, "f_max_hz", 0.0) or 0.0),
        eps_max=eps,
        nr_ts=int(getattr(sim, "max_timesteps", 0) or 0),
        sweep_span_hz=span_hz,
    )


def _unit_name(unit_m: float) -> str:
    return {0.001: "mm", 1e-6: "um", 1.0: "m"}.get(round(float(unit_m), 12), "units")


def _fmt_len(metres: float, unit_m: float) -> str:
    unit_m = unit_m or 0.001
    value = metres / unit_m
    if unit_m == 1e-6:
        return f"{value:.4g} um"
    if unit_m == 1.0:
        return f"{value:.4g} m"
    return f"{value:.4g} mm"


def summary_line(rep: MeshTiming) -> str:
    """One log line with the essentials (used right after a mesh build)."""
    if not rep.axes or not rep.dt_courant_s:
        return "Mesh timing: not available"
    worst_cells = min(rep.cells_per_lambda.values()) if rep.cells_per_lambda else 0.0
    return (
        f"Mesh timing: dt(CFL, vacuum)={rep.dt_courant_s * 1e12:.4g} ps "
        f"(>= {rep.dt_dielectric_s * 1e12:.4g} ps in eps_r={rep.eps_max:g}); "
        f"{worst_cells:.1f} cells/lambda @ f_max; "
        f"min cell {_fmt_len(min(a.min_cell_m for a in rep.axes.values()), rep.unit_m)} "
        f"({rep.refinement_penalty:.1f}x vs. typical); "
        f"DFT dF={rep.resolution_worst_hz / 1e6:.3g}..{rep.resolution_best_hz / 1e6:.3g} MHz "
        f"for NrTS={rep.nr_ts}; {len(rep.warnings)} warning(s), {len(rep.errors)} error(s)"
    )


def report_lines(rep: MeshTiming) -> list[str]:
    """Human-readable block appended to the mesh report."""
    if not rep.axes:
        return ["Mesh timing: no grid to analyse"]
    unit_m = rep.unit_m or 0.001
    out: list[str] = []
    out.append("")
    out.append("Mesh timing / feasibility (mesh-only estimates, no FDTD yet):")
    out.append(
        f"  Units            : {_unit_name(unit_m)} (1 unit = {unit_m:g} m)"
    )
    cells = " x ".join(f"{rep.axes[a].n_cells}" for a in ("x", "y", "z") if a in rep.axes)
    out.append(f"  Cells            : {cells} = {rep.cells_total} cells")
    for a in ("x", "y", "z"):
        ax = rep.axes.get(a)
        if ax is None:
            continue
        out.append(
            f"  Cell size {a}      : min {_fmt_len(ax.min_cell_m, unit_m)}"
            f" @ {a}={ax.min_cell_at_units:.6g} {_unit_name(unit_m)}"
            f" | typ {_fmt_len(ax.median_cell_m, unit_m)}"
            f" | max {_fmt_len(ax.max_cell_m, unit_m)}"
        )
    out.append(
        f"  Courant limit dt : {rep.dt_courant_s * 1e12:.4g} ps (vacuum, "
        f"worst cell of each axis)"
    )
    if rep.eps_max > 1.0:
        out.append(
            f"                     inside a dielectric the engine uses less: "
            f">= {rep.dt_dielectric_s * 1e12:.4g} ps for eps_r={rep.eps_max:g}"
        )
    if rep.cells_per_lambda:
        per_axis = ", ".join(
            f"{a} {v:.1f}" for a, v in rep.cells_per_lambda.items()
        )
        out.append(
            f"  Resolution       : {per_axis} cells per wavelength "
            f"(f_max={rep.f_max_hz / 1e9:.4g} GHz, need >= {CELLS_PER_LAMBDA_MIN:.0f})"
        )
    if rep.nyquist_hz and rep.f_max_hz:
        out.append(
            f"  Timestep check   : 1/(2*dt_min) = {rep.nyquist_hz / 1e9:.4g} GHz vs. "
            f"f_max {rep.f_max_hz / 1e9:.4g} GHz -> the timestep is not the limit, "
            "the cell size vs. wavelength is"
        )
    if rep.ts_cross_domain:
        out.append(
            f"  Domain crossing  : >= {rep.ts_cross_domain:.0f} timesteps one way "
            f"(diagonal {rep.domain_diagonal_m * 1e3:.4g} mm, "
            f"{MIN_DOMAIN_CROSSINGS:.0f} crossings recommended)"
        )
    if rep.nr_ts:
        out.append(
            f"  DFT resolution   : {rep.resolution_worst_hz / 1e6:.3g}.."
            f"{rep.resolution_best_hz / 1e6:.3g} MHz for NrTS={rep.nr_ts} "
            f"(1/(NrTS*dt), shorter if the end criteria stop the run early)"
        )
        if rep.target_resolution_hz:
            out.append(
                f"                     sweep asks for "
                f"{rep.target_resolution_hz / 1e6:.3g} MHz per point"
            )
    if rep.refinement_penalty > 1.0:
        out.append(
            f"  Runtime penalty  : {rep.refinement_penalty:.1f}x from the smallest "
            f"cell vs. the typical cell ({rep.penalty_axis} axis)"
        )
    if rep.issues:
        out.append("  Findings:")
        for i in rep.issues:
            out.append(f"    [{i.severity.upper()}] {i.code}: {i.message}")
    else:
        out.append("  Findings         : none — the mesh and the settings match")
    return out
