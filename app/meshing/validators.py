"""Validate CSXCAD mesh lines against openEMS meshing rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.meshing.preview import get_sim_area, is_diagnostic_kind, iter_primitive_infos, primitives_outside_domain
from app.meshing.polar import axis_labels
from app.meshing.rules import MeshTargets
from app.models.project import PortDef


@dataclass
class Issue:
    severity: str  # "error" | "warning" | "info"
    code: str
    message: str


@dataclass
class MeshReport:
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _get_lines(csx) -> dict[str, np.ndarray]:
    grid = csx.GetGrid()
    out = {}
    for d in "xyz":
        try:
            lines = grid.GetLines(d)
            out[d] = np.asarray(lines, dtype=float)
        except Exception:  # noqa: BLE001
            out[d] = np.asarray([], dtype=float)
    return out


def _axis_stats(lines: np.ndarray) -> dict[str, float]:
    if lines.size < 2:
        return {"min_cell": 0.0, "max_cell": 0.0, "worst_grading": 0.0, "n_lines": float(lines.size)}
    cells = np.diff(np.sort(lines))
    cells = cells[cells > 0]
    if cells.size == 0:
        return {"min_cell": 0.0, "max_cell": 0.0, "worst_grading": 0.0, "n_lines": float(lines.size)}
    grading = 1.0
    if cells.size > 1:
        ratios = cells[1:] / cells[:-1]
        grading = float(np.max(np.maximum(ratios, 1.0 / ratios)))
    return {
        "min_cell": float(np.min(cells)),
        "max_cell": float(np.max(cells)),
        "worst_grading": grading,
        "n_lines": float(lines.size),
    }


def _value_on_lines(lines: np.ndarray, value: float, atol: float = 1e-9) -> bool:
    if lines.size == 0:
        return False
    return bool(np.any(np.isclose(lines, value, atol=atol)))


def validate_mesh(
    csx,
    targets: MeshTargets,
    ports: list[PortDef] | None = None,
    max_grading: float = 2.0,
    coord_system: str = "cartesian",
) -> MeshReport:
    report = MeshReport()
    lines = _get_lines(csx)
    polar = coord_system == "cylindrical"
    labels = ("ρ", "φ", "z") if polar else ("x", "y", "z")
    report.stats["lambda_min_units"] = targets.lambda_min_units
    report.stats["max_cell_allowed"] = targets.max_cell_units
    report.stats["f_max_hz"] = targets.f_max_hz
    report.stats["coord_system"] = coord_system

    # Cylindrical: the alpha axis stores RADIAN steps, not lengths — convert
    # to arc length at the outer radius before comparing against lambda-based
    # cell limits, otherwise meters-unit projects flag bogus cell_too_large.
    axis_lines = dict(lines)
    if polar and lines.get("y", np.asarray([])).size >= 2:
        r_ref = float(np.max(lines.get("x", np.asarray([1.0])))) or 1.0
        axis_lines["y"] = np.sort(lines["y"]) * r_ref

    for d, arr in axis_lines.items():
        name = labels["xyz".index(d)]
        st = _axis_stats(arr)
        report.stats[f"{d}"] = st
        if arr.size < 2:
            report.issues.append(Issue("error", "empty_mesh", f"Axis {name}: no mesh lines"))
            continue
        if st["max_cell"] > targets.max_cell_units * 1.0001:
            report.issues.append(
                Issue(
                    "error",
                    "cell_too_large",
                    f"Axis {name}: max cell {st['max_cell']:.4g} > allowed {targets.max_cell_units:.4g} "
                    f"(lambda_min/10..15)",
                )
            )
        if st["worst_grading"] > max_grading:
            report.issues.append(
                Issue(
                    "warning",
                    "grading_jump",
                    f"Axis {name}: worst neighbor grading {st['worst_grading']:.3g} > {max_grading}",
                )
            )
        if st["min_cell"] > 0 and st["min_cell"] < targets.max_cell_units / 500:
            report.issues.append(
                Issue(
                    "warning",
                    "very_small_cell",
                    f"Axis {name}: min cell {st['min_cell']:.4g} is extremely small; check for tiny gaps",
                )
            )
        if polar and d == "z" and st["n_lines"] - 1 < 8:
            report.issues.append(
                Issue(
                    "warning",
                    "pml_cells_low",
                    "Axis z: fewer than 8 cells — PML-8 boundaries silently reset to PEC",
                )
            )

    for p in ports or []:
        _check_port(report, lines, p, targets, polar=polar)

    _check_primitive_bounds(report, csx, coord_system=coord_system)

    # drawing unit sanity: geometry should not be thousands of times larger than lambda
    # (cylindrical: alpha radians are a different unit — exclude them from the span)
    span_axes = ("x", "z") if polar else tuple(lines.keys())
    vals = [lines[a] for a in span_axes if a in lines and lines[a].size]
    all_vals = np.concatenate(vals) if vals else np.array([0.0])
    span = float(np.max(all_vals) - np.min(all_vals))
    report.stats["domain_span_units"] = span
    if span > 0 and targets.lambda_min_units > 0:
        ratio = span / targets.lambda_min_units
        if ratio > 1e4:
            report.issues.append(
                Issue(
                    "error",
                    "unit_mismatch",
                    f"Domain span {span:.4g} units vs lambda_min {targets.lambda_min_units:.4g} "
                    f"— drawing unit looks wrong (span/lambda={ratio:.3g})",
                )
            )
        elif span < targets.lambda_min_units / 50:
            report.issues.append(
                Issue(
                    "warning",
                    "domain_too_small",
                    "Simulation domain is very small compared to wavelength; structure may be electrically tiny",
                )
            )
    return report


def _check_primitive_bounds(report: MeshReport, csx, coord_system: str = "cartesian") -> None:
    """Every primitive AABB must sit inside the FDTD domain (GetSimArea).

    Material outside the domain is silently dropped by openEMS, which would
    give wrong results without any runtime error - so flag it here.
    """
    try:
        area = get_sim_area(csx)
        infos = iter_primitive_infos(csx, coord_system=coord_system)
    except Exception:  # noqa: BLE001
        return
    hits = primitives_outside_domain(infos, area)
    report.stats["primitives_outside_domain"] = len(hits)
    axis_names = axis_labels(coord_system)
    for info, j, amount, side in hits:
        axis = axis_names[j]
        report.issues.append(
            Issue(
                "warning" if is_diagnostic_kind(info.kind) else "error",
                "primitive_outside_domain",
                f"{info.kind} '{info.name}': extends {amount:.4g} units beyond the domain "
                f"on {axis}-{side}; increase the air margin or shrink the geometry",
            )
        )


def _check_port(
    report: MeshReport,
    lines: dict[str, np.ndarray],
    port: PortDef,
    targets: MeshTargets,
    polar: bool = False,
):
    start = np.asarray(port.start, dtype=float)
    stop = np.asarray(port.stop, dtype=float)
    axes = "xyz"
    names = axis_labels("cylindrical" if polar else "cartesian")
    # Cylindrical stores lines with the same float32 rounding as phi angles;
    # allow the same relative epsilon the polar mesher uses for re-asserts.
    def _atol(v: float) -> float:
        return 1e-6 * max(1.0, abs(v)) if polar else 1e-9

    def _on(ax: str, v: float) -> bool:
        return _value_on_lines(lines[ax], v, atol=_atol(v))

    nonflat = [axes[i] for i in range(3) if abs(stop[i] - start[i]) > 1e-12]
    flat = [axes[i] for i in range(3) if abs(stop[i] - start[i]) <= 1e-12]

    for ax in flat:
        if not _on(ax, start[axes.index(ax)]):
            report.issues.append(
                Issue(
                    "error",
                    "port_alignment",
                    f"Port '{port.name}': {names[axes.index(ax)]}={start[axes.index(ax)]:.6g} "
                    f"is not on a mesh line",
                )
            )

    if port.type in {"lumped", "probe"} and len(nonflat) == 1:
        # 1D-like port along one axis: the two endpoints should sit on mesh lines
        ax = nonflat[0]
        i = axes.index(ax)
        for val in (start[i], stop[i]):
            if not _on(ax, val):
                report.issues.append(
                    Issue(
                        "error",
                        "port_alignment",
                        f"Port '{port.name}': {names[i]}={val:.6g} endpoint not on mesh line",
                    )
                )
    elif len(nonflat) == 2:
        # 2D port: should span exactly one mesh cell in the transverse flat axes
        for ax in flat:
            arr = lines[ax]
            i = axes.index(ax)
            val = start[i]
            # find nearest lines
            if arr.size == 0:
                continue
            idx = int(np.argmin(np.abs(arr - val)))
            if abs(arr[idx] - val) > _atol(val):
                report.issues.append(
                    Issue(
                        "error",
                        "port_alignment",
                        f"Port '{port.name}': 2D port face not aligned on {names[i]} mesh line",
                    )
                )


def report_to_lines(report: MeshReport) -> list[str]:
    out = []
    for i in report.issues:
        out.append(f"[{i.severity.upper()}] {i.code}: {i.message}")
    if not out:
        out.append("[OK] Mesh checks passed")
    return out
