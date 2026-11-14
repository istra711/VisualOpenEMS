"""Variable-sweep planning and result collection (Phase A).

A sweep is one staging/committed run with one folder per sweep point.  Every
point receives a complete project snapshot and its own generated run script;
the open GUI project is never mutated.
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from app.models.project import Project
from app.models.sweep import SweepSpec
from app.models.variables import (
    resolve_dump,
    resolve_nf2ff,
    resolve_port,
    resolve_primitive,
    resolve_variable_defs,
)


@dataclass
class SweepPoint:
    index: int
    values: dict[str, float]
    label: str
    work_dir: Path
    project_path: Path
    port_plans: list = field(default_factory=list)
    status: str = "pending"
    error: str = ""
    sparams: str = ""
    nf2ff: str = ""


@dataclass
class SweepStep:
    """One FDTD process in the flattened sweep driver."""

    label: str
    script_path: Path
    work_dir: Path
    point_index: int
    port_number: int | None = None
    port_name: str = ""


def _resolve_variant_with_value(
    project: Project,
    primitives: list[dict] | None,
    target: str,
    value: float,
    spec: SweepSpec,
) -> tuple[Project, list[dict], dict[str, float]]:
    run_project = copy.deepcopy(project)
    definitions = copy.deepcopy(list(project.variables))
    kind = str(spec.target_kind or "variable")
    wanted = str(target or "").strip().casefold()

    if kind != "port_phase":
        found = False
        for definition in definitions:
            if str(getattr(definition, "name", "") or "").strip().casefold() == wanted:
                definition.value = float(value)
                # Sweeping an expression variable overrides its formula for
                # this point.  Dependent variables still resolve normally.
                definition.expression = ""
                found = True
                break
        if not found:
            raise ValueError(f"sweep: variable '{target}' is not defined")

    values, errors = resolve_variable_defs(definitions)
    if errors:
        raise ValueError("; ".join(errors[:8]))
    for definition in definitions:
        name = str(getattr(definition, "name", "") or "").strip()
        if name in values:
            definition.value = values[name]

    resolved_primitives: list[dict] = []
    for prim in primitives or []:
        resolved, prim_errors = resolve_primitive(copy.deepcopy(prim), values)
        resolved_primitives.append(resolved)
        errors.extend(prim_errors)

    ports = []
    phase_applied = False
    for port in run_project.ports:
        resolved, port_errors = resolve_port(copy.deepcopy(port), values)
        if (
            kind == "port_phase"
            and bool(getattr(resolved, "active", True))
            and str(getattr(resolved, "name", "") or "").strip().casefold()
            == wanted
        ):
            resolved.excite_phase_deg = float(value)
            phase_applied = True
        ports.append(resolved)
        errors.extend(port_errors)
    if kind == "port_phase" and not phase_applied:
        raise ValueError(f"sweep: active port '{target}' is not defined")

    dumps = []
    for dump in run_project.dumps:
        resolved, dump_errors = resolve_dump(copy.deepcopy(dump), values)
        dumps.append(resolved)
        errors.extend(dump_errors)

    nf2ff, nf_errors = resolve_nf2ff(copy.deepcopy(run_project.nf2ff), values)
    errors.extend(nf_errors)
    if errors:
        raise ValueError("; ".join(errors[:8]))

    run_project.variables = definitions
    run_project.primitives = list(resolved_primitives)
    run_project.ports = ports
    run_project.dumps = dumps if spec.collect_fields else []
    run_project.sim.field_dumps_enabled = bool(spec.collect_fields)
    run_project.nf2ff = nf2ff
    run_project.sim.nf2ff_enabled = bool(spec.collect_nf2ff)
    run_project.nf2ff.enabled = bool(spec.collect_nf2ff)
    # A point snapshot is a simulation input, not a project result and not
    # another sweep definition.
    run_project.results = None
    run_project.sparam_reference = None
    run_project.sweep = None
    return run_project, resolved_primitives, values


def _partial_sparams(work_dir: Path) -> Path | None:
    for folder in (work_dir / "sparams", work_dir / "simulation"):
        for pattern in ("sparams.s*p", "sparams.json"):
            hits = sorted(folder.glob(pattern))
            if hits:
                return hits[-1]
    return None


def plan_sweep_runs(
    project: Project,
    primitives: list[dict] | None,
    base_work: Path | str,
    spec: SweepSpec,
) -> tuple[list[SweepPoint], list[SweepStep]]:
    """Create one point folder and the flattened FDTD step list."""
    if not spec.is_runnable():
        raise ValueError("sweep: configure a target and at least one value")
    if spec.is_phase() and not spec.collect_nf2ff:
        raise ValueError(
            "sweep: a port-phase sweep needs NF2FF (S-parameters do not change "
            "with the excitation phase)"
        )
    base_work = Path(base_work)
    root = base_work / "sweep"
    root.mkdir(parents=True, exist_ok=True)
    points: list[SweepPoint] = []
    steps: list[SweepStep] = []
    total = len(spec.values)
    nports = len([p for p in project.ports if bool(getattr(p, "active", True))])
    target = spec.target_name()
    phase_target = spec.is_phase()

    from app.simulation.script_gen import generate_run_script

    for index, value in enumerate(spec.values):
        point_label = (
            f"phase {target}={float(value):g}°"
            if phase_target
            else f"{target}={float(value):g}"
        )
        point_dir = root / f"point-{index:03d}"
        point_dir.mkdir(parents=True, exist_ok=True)
        run_project, run_primitives, _values = _resolve_variant_with_value(
            project, primitives, target, float(value), spec
        )
        point_project = run_project.save(point_dir / "point_project.json")
        point = SweepPoint(
            index=index,
            values={target: float(value)},
            label=point_label,
            work_dir=point_dir,
            project_path=point_project,
        )
        points.append(point)

        if spec.full_smatrix and nports >= 2 and not phase_target:
            from app.simulation.multirun import plan_port_runs

            port_plans = plan_port_runs(
                run_project, run_primitives, point_dir
            )
            point.port_plans = port_plans
            for port_plan in port_plans:
                steps.append(
                    SweepStep(
                        label=(
                            f"Point {index + 1}/{total} · {point_label} · "
                            f"Port {port_plan.port_number}"
                        ),
                        script_path=port_plan.script_path,
                        work_dir=port_plan.work_dir,
                        point_index=index,
                        port_number=port_plan.port_number,
                        port_name=port_plan.port_name,
                    )
                )
        else:
            script = generate_run_script(
                run_project,
                point_project,
                point_dir,
                primitives=run_primitives,
            )
            steps.append(
                SweepStep(
                    label=f"Point {index + 1}/{total} · {point_label}",
                    script_path=script,
                    work_dir=point_dir,
                    point_index=index,
                )
            )
    return points, steps


def _relative(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def finish_sweep(
    work_dir: Path | str,
    points: list[SweepPoint],
    spec: SweepSpec,
) -> dict:
    """Merge per-port runs, write ``sweep.json`` and return the job summary."""
    from app.simulation.multirun import merge_port_runs

    work_dir = Path(work_dir)
    primary: Path | None = None
    for point in points:
        try:
            if point.port_plans:
                merged = merge_port_runs(point.work_dir, point.port_plans)
                written = merged.get("written") or {}
                candidate = written.get("touchstone") or written.get("json")
                point.status = "partial" if merged.get("missing") else "complete"
                if not candidate:
                    candidate = _partial_sparams(point.work_dir)
            else:
                candidate = _partial_sparams(point.work_dir)
                point.status = "complete" if candidate else "failed"
        except Exception as exc:  # noqa: BLE001 - one point must not kill the sweep
            point.status = "failed"
            point.error = str(exc)
            candidate = _partial_sparams(point.work_dir)
        if candidate is not None:
            point.sparams = _relative(Path(candidate), work_dir)
            if primary is None:
                primary = Path(candidate)
        nf2ff_path = point.work_dir / "nf2ff" / "nf2ff.json"
        if nf2ff_path.is_file():
            point.nf2ff = _relative(nf2ff_path, work_dir)

    statuses = [p.status for p in points]
    if statuses and all(s == "complete" for s in statuses):
        status = "complete"
    elif any(s in ("complete", "partial") for s in statuses):
        status = "partial"
    else:
        status = "failed"

    payload = {
        "version": 1,
        "created": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "spec": asdict(spec),
        "points": [
            {
                "index": point.index,
                "values": point.values,
                "label": point.label,
                "work_dir": _relative(point.work_dir, work_dir),
                "project_path": _relative(point.project_path, work_dir),
                "status": point.status,
                "error": point.error,
                "sparams": point.sparams,
                "nf2ff": point.nf2ff,
            }
            for point in points
        ],
    }
    sweep_json = work_dir / "sweep.json"
    sweep_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {
        "path": sweep_json,
        "status": status,
        "points": points,
        "primary_sparams": primary,
    }
