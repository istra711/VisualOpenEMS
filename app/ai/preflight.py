"""Deterministic preflight checks for generated or AI-edited projects.

The preflight is intentionally independent of the Qt GUI and of openEMS mesh
building.  It catches the expensive-to-find structural mistakes before the
user applies a proposal or presses Build Mesh.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from app.models.project import Project
from app.models.variables import (
    resolve_dump,
    resolve_nf2ff,
    resolve_port,
    resolve_primitive,
    resolve_variable_defs,
)

_ALLOWED_PORT_TYPES = {"lumped", "msl", "rect_waveguide", "waveguide", "probe"}
_METAL_KINDS = {"pec", "conducting_sheet"}
_MATERIAL_KINDS = {"pec", "dielectric", "conducting_sheet", "lumped_rlc", "ignore"}


@dataclass(frozen=True)
class PreflightIssue:
    severity: str  # error | warning | info
    code: str
    message: str


@dataclass
class PreflightReport:
    errors: list[PreflightIssue] = field(default_factory=list)
    warnings: list[PreflightIssue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, severity: str, code: str, message: str) -> None:
        issue = PreflightIssue(severity, code, message)
        if severity == "error":
            self.errors.append(issue)
        elif severity == "warning":
            self.warnings.append(issue)
        else:
            self.notes.append(message)

    def errors_text(self) -> str:
        return "\n".join(f"- {item.message}" for item in self.errors)

    def text(self) -> str:
        lines = [
            "PREFLIGHT",
            "Status: "
            + (
                "OK"
                if self.ok and not self.warnings
                else ("WARNINGS" if self.ok else "ERRORS")
            ),
        ]
        if self.errors:
            lines.append("")
            lines.append("Errors:")
            lines.extend(f"  [ERROR] {item.code}: {item.message}" for item in self.errors)
        if self.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(
                f"  [WARNING] {item.code}: {item.message}" for item in self.warnings
            )
        if self.notes:
            lines.append("")
            lines.append("Notes:")
            lines.extend(f"  - {note}" for note in self.notes)
        if not (self.errors or self.warnings or self.notes):
            lines.append("No structural errors or warnings detected.")
        lines.append("")
        lines.append(
            "This is a structural check, not a mesh build or an EM simulation."
        )
        return "\n".join(lines)


def _finite_vec(value: Any) -> list[float] | None:
    try:
        vec = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if len(vec) != 3 or not all(math.isfinite(v) for v in vec):
        return None
    return vec


def _contains(box: tuple[list[float], list[float]], point: list[float], tol: float) -> bool:
    lo, hi = box
    return all(lo[i] - tol <= point[i] <= hi[i] + tol for i in range(3))


def _inside_domain(
    domain: tuple[list[float], list[float]],
    box: tuple[list[float], list[float]],
    tol: float,
) -> bool:
    dlo, dhi = domain
    lo, hi = box
    return all(dlo[i] - tol <= lo[i] and hi[i] <= dhi[i] + tol for i in range(3))


def preflight(project: Project, primitives: list[dict] | None = None) -> PreflightReport:
    report = PreflightReport()
    primitives = list(primitives if primitives is not None else (project.primitives or []))

    # Project serialisation round-trip.
    try:
        Project.from_dict(project.to_dict())
    except Exception as exc:  # noqa: BLE001
        report.add("error", "project_roundtrip", f"project cannot be serialised: {exc}")

    # Variables first: primitives and ports depend on them.
    values, variable_errors = resolve_variable_defs(project.variables)
    for error in variable_errors:
        report.add("error", "variable", error)

    resolved_primitives: list[dict] = []
    primitive_names: set[str] = set()
    material_names = {str(m.name or "").casefold(): m for m in project.materials}
    if len(material_names) != len(project.materials):
        report.add("error", "material_name", "duplicate material names")
    for material in project.materials:
        label = str(material.name or "?")
        if str(material.kind) not in _MATERIAL_KINDS:
            report.add(
                "error",
                "material_kind",
                f"material '{label}' has unknown kind '{material.kind}'",
            )
        if float(material.epsilon or 0.0) <= 0:
            report.add("error", "material_epsilon", f"material '{label}' epsilon must be positive")
        if float(material.permeability or 0.0) <= 0:
            report.add(
                "error",
                "material_permeability",
                f"material '{label}' permeability must be positive",
            )
    for index, primitive in enumerate(primitives):
        if not isinstance(primitive, dict):
            report.add("error", "primitive", f"primitive {index + 1} is not an object")
            continue
        name = str(primitive.get("name") or "").strip()
        kind = str(primitive.get("kind") or "").strip()
        if not name:
            report.add("error", "primitive_name", f"primitive {index + 1} has no name")
        elif name.casefold() in primitive_names:
            report.add("error", "primitive_name", f"duplicate primitive name '{name}'")
        primitive_names.add(name.casefold())
        if not kind:
            report.add("error", "primitive_kind", f"primitive '{name or index + 1}' has no kind")
        material = str(primitive.get("material") or "").strip()
        if material and material.casefold() not in material_names:
            report.add(
                "error",
                "primitive_material",
                f"primitive '{name or index + 1}' references unknown material '{material}'",
            )
        resolved, errors = resolve_primitive(primitive, values)
        for error in errors:
            report.add("error", "primitive_expression", error)
        resolved_primitives.append(resolved)

    # Domain (geometry + air margin) and primitive extents.
    domain = None
    try:
        from app.meshing.bounds import domain_box_units, primitive_view_bbox

        domain = domain_box_units(project, resolved_primitives)
        primitive_boxes: list[tuple[str, tuple[list[float], list[float]]]] = []
        for primitive in resolved_primitives:
            name = str(primitive.get("name") or "?")
            box = primitive_view_bbox(primitive)
            if box is None:
                report.add(
                    "warning",
                    "primitive_bounds",
                    f"primitive '{name}' has no usable bounding box",
                )
                continue
            primitive_boxes.append((name, box))
            if domain is not None and not _inside_domain(domain, box, 1e-6):
                report.add(
                    "warning",
                    "outside_domain",
                    f"primitive '{name}' lies outside the estimated FDTD domain",
                )
    except Exception as exc:  # noqa: BLE001
        primitive_boxes = []
        report.add("warning", "domain_check", f"could not estimate the domain: {exc}")

    # Mesh and solver sanity.
    mesh = project.mesh
    if str(mesh.drawing_unit) not in {"m", "cm", "mm", "um"}:
        report.add("error", "mesh_unit", f"unsupported drawing unit '{mesh.drawing_unit}'")
    if str(mesh.preset) not in {
        "low",
        "medium",
        "high",
        "very_high",
        "extreme",
        "custom",
    }:
        report.add("error", "mesh_preset", f"unknown mesh preset '{mesh.preset}'")
    for name, kind in (
        ("xmin", project.boundary.xmin),
        ("xmax", project.boundary.xmax),
        ("ymin", project.boundary.ymin),
        ("ymax", project.boundary.ymax),
        ("zmin", project.boundary.zmin),
        ("zmax", project.boundary.zmax),
    ):
        if str(kind) not in {"PEC", "PMC", "MUR", "PML"}:
            report.add("error", "boundary", f"boundary {name} has unknown kind '{kind}'")
    if str(project.nf2ff.box_mode) not in {"auto", "manual"}:
        report.add(
            "error",
            "nf2ff_box_mode",
            f"unknown NF2FF box mode '{project.nf2ff.box_mode}'",
        )
    if mesh.start_frequency <= 0 or mesh.stop_frequency <= 0:
        report.add("error", "mesh_frequency", "start and stop frequencies must be positive")
    if mesh.stop_frequency <= mesh.start_frequency:
        report.add("error", "mesh_frequency", "stop frequency must be above start frequency")
    if mesh.target_frequency is not None:
        low = min(mesh.start_frequency, mesh.stop_frequency)
        high = max(mesh.start_frequency, mesh.stop_frequency)
        if not (low <= mesh.target_frequency <= high):
            report.add(
                "warning",
                "target_outside_sweep",
                f"target frequency {mesh.target_frequency:.6g} Hz is outside the sweep",
            )
    if mesh.cells_per_wavelength <= 0:
        report.add("error", "mesh_density", "cells per wavelength must be positive")
    for label, value in (
        ("min cell", mesh.min_cellsize),
        ("max cell", mesh.max_cellsize),
        ("refined cell", mesh.refined_cellsize),
    ):
        if value is not None and value <= 0:
            report.add("error", "mesh_cell", f"{label} must be positive or empty")
    if (
        mesh.min_cellsize is not None
        and mesh.max_cellsize is not None
        and mesh.min_cellsize > mesh.max_cellsize
    ):
        report.add("error", "mesh_cell", "min cell size is larger than max cell size")
    if int(project.sim.max_timesteps or 0) <= 0:
        report.add("error", "sim_timesteps", "max timesteps must be positive")
    if not (0.0 < float(project.sim.end_criteria) < 1.0):
        report.add("error", "sim_end_criteria", "end criteria must be between 0 and 1")

    # Ports.
    port_names: set[str] = set()
    active = 0
    excited = 0
    metal_boxes: list[tuple[list[float], list[float]]] = []
    for primitive in resolved_primitives:
        material = str(primitive.get("material") or "").casefold()
        definition = material_names.get(material)
        if definition is not None and definition.kind in _METAL_KINDS:
            try:
                from app.meshing.bounds import primitive_view_bbox

                box = primitive_view_bbox(primitive)
                if box is not None:
                    metal_boxes.append(box)
            except Exception:  # noqa: BLE001
                pass
    for index, port in enumerate(project.ports):
        name = str(getattr(port, "name", "") or f"port {index + 1}")
        if name.casefold() in port_names:
            report.add("error", "port_name", f"duplicate port name '{name}'")
        port_names.add(name.casefold())
        resolved_port, port_errors = resolve_port(port, values)
        for error in port_errors:
            report.add("error", "port_expression", error)
        if str(resolved_port.type) not in _ALLOWED_PORT_TYPES:
            report.add(
                "error",
                "port_type",
                f"port '{name}' has unknown type '{resolved_port.type}'",
            )
        start = _finite_vec(getattr(resolved_port, "start", None))
        stop = _finite_vec(getattr(resolved_port, "stop", None))
        if start is None or stop is None:
            report.add("error", "port_geometry", f"port '{name}' has invalid start/stop")
            continue
        if max(abs(stop[i] - start[i]) for i in range(3)) <= 1e-12:
            report.add("error", "port_geometry", f"port '{name}' has zero length")
        if float(getattr(resolved_port, "resistance", 0.0) or 0.0) <= 0:
            report.add("warning", "port_resistance", f"port '{name}' has non-positive resistance")
        phase = float(getattr(resolved_port, "excite_phase_deg", 0.0) or 0.0)
        if not (0.0 <= phase <= 360.0):
            report.add("warning", "port_phase", f"port '{name}' phase is outside 0...360 deg")
        if not bool(getattr(resolved_port, "active", True)):
            continue
        active += 1
        if bool(getattr(resolved_port, "excite_port", False)):
            excited += 1
        if domain is not None and not _inside_domain(
            domain, (start, stop), 1e-6
        ):
            report.add(
                "warning",
                "port_outside_domain",
                f"port '{name}' is outside the estimated FDTD domain",
            )
        if str(resolved_port.type) in ("lumped", "msl", "probe") and metal_boxes:
            tol = max(0.05, 1e-4 * max(1.0, *(abs(v) for v in start + stop)))
            if not (
                any(_contains(box, start, tol) for box in metal_boxes)
                or any(_contains(box, stop, tol) for box in metal_boxes)
            ):
                report.add(
                    "warning",
                    "port_metal",
                    f"port '{name}': neither endpoint touches a metal primitive",
                )
    if active == 0:
        report.add("error", "no_active_port", "no active port; a simulation needs a source")
    elif excited == 0:
        report.add("warning", "no_excited_port", "no active port is marked as excited")

    # Dumps and NF2FF expressions.
    dump_names: set[str] = set()
    for dump in project.dumps:
        dump_name = str(getattr(dump, "name", "") or "").strip()
        if dump_name.casefold() in dump_names:
            report.add("error", "dump_name", f"duplicate dump name '{dump_name}'")
        dump_names.add(dump_name.casefold())
        _resolved, errors = resolve_dump(dump, values)
        for error in errors:
            report.add("error", "dump_expression", error)
    nf2ff, nf_errors = resolve_nf2ff(project.nf2ff, values)
    for error in nf_errors:
        report.add("error", "nf2ff_expression", error)
    if project.nf2ff.enabled:
        nf_start = _finite_vec(nf2ff.start)
        nf_stop = _finite_vec(nf2ff.stop)
        if nf_start is None or nf_stop is None:
            report.add("error", "nf2ff_geometry", "NF2FF box start/stop is invalid")
        elif max(abs(nf_stop[i] - nf_start[i]) for i in range(3)) <= 1e-12:
            report.add("error", "nf2ff_geometry", "NF2FF box has zero volume")
        elif domain is not None and not _inside_domain(domain, (nf_start, nf_stop), 1e-6):
            report.add(
                "warning",
                "nf2ff_outside_domain",
                "NF2FF box is not fully inside the estimated FDTD domain",
            )
        if not project.sim.nf2ff_enabled:
            report.add(
                "warning",
                "nf2ff_flag",
                "NF2FF box is enabled but simulation.nf2ff_enabled is false",
            )
    if not primitives:
        report.add("warning", "no_geometry", "project has no geometry primitives")
    if not project.materials:
        report.add("warning", "no_materials", "project has no material definitions")

    return report
