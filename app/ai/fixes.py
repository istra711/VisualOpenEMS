"""Apply and validate the small, explicitly allowed project fixes.

The AI never edits project files directly.  Diagnosis or the user creates a
:class:`FixProposal`; this module validates every path/value against a strict
allow-list and applies it atomically to a deep copy first.
"""

from __future__ import annotations

import copy
from dataclasses import fields
from typing import Any, Callable

from app.ai.types import FixChange, FixProposal
from app.models.project import Project

#: Fields the error assistant may change.  This is intentionally small: only
#: numeric solver/mesh knobs and port switches.  Geometry, materials and ports
#: positions are never modified automatically.
_ALLOWED: dict[str, str] = {
    "sim.max_timesteps": "int",
    "sim.end_criteria": "float01",
    "sim.nf2ff_enabled": "bool",
    "nf2ff.enabled": "bool",
    "nf2ff.box_mode": "box_mode",
    "mesh.preset": "mesh_preset",
    "mesh.max_cellsize": "positive_or_none",
    "mesh.min_cellsize": "positive_or_none",
    "mesh.refined_cellsize": "positive_or_none",
    "mesh.target_frequency": "positive",
    "boundary.pml_size": "int",
}

_PORT_FIELDS: dict[str, str] = {
    "active": "bool",
    "excite_port": "bool",
    "resistance": "positive",
    "excite_phase_deg": "phase",
}

_MESH_PRESETS = {"low", "medium", "high", "very_high", "extreme", "custom"}
_MAX_TIMESTEPS = 20_000_000


def _resolve(project: Project, path: str) -> tuple[object, str] | None:
    parts = [part for part in str(path or "").strip().split(".") if part]
    if not parts:
        return None
    obj: object = project
    for part in parts[:-1]:
        if part.startswith("ports[") and part.endswith("]"):
            try:
                index = int(part[6:-1])
                ports = getattr(obj, "ports", None)
                if not isinstance(ports, list) or not (0 <= index < len(ports)):
                    return None
                obj = ports[index]
            except (TypeError, ValueError):
                return None
        else:
            if not hasattr(obj, part):
                return None
            obj = getattr(obj, part)
    attr = parts[-1]
    return (obj, attr) if hasattr(obj, attr) else None


def _is_port_path(path: str) -> bool:
    return str(path).startswith("ports[") and str(path).count(".") >= 1


def _validate_value(path: str, value: Any) -> tuple[Any, str]:
    if _is_port_path(path):
        attr = str(path).rsplit(".", 1)[-1]
        kind = _PORT_FIELDS.get(attr)
        if kind is None:
            return value, f"port field '{attr}' is not allowed"
    else:
        kind = _ALLOWED.get(path)
        if kind is None:
            return value, f"field '{path}' is not allowed"

    if kind == "bool":
        if not isinstance(value, bool):
            return value, f"{path}: expected true/false, got {value!r}"
        return value, ""
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            return value, f"{path}: expected an integer, got {value!r}"
        if path == "sim.max_timesteps":
            if not (1 <= value <= _MAX_TIMESTEPS):
                return value, f"{path}: must be between 1 and {_MAX_TIMESTEPS}"
        elif path == "boundary.pml_size":
            if not (1 <= value <= 64):
                return value, f"{path}: PML layers must be 1...64"
        return value, ""
    if kind in ("positive", "phase"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value, f"{path}: expected a number, got {value!r}"
        number = float(value)
        if kind == "phase":
            if not (0.0 <= number <= 360.0):
                return value, f"{path}: phase must be 0...360 degrees"
            return number, ""
        if number <= 0.0:
            return value, f"{path}: value must be positive"
        return number, ""
    if kind == "positive_or_none":
        if value is None:
            return None, ""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value, f"{path}: expected a positive number or empty"
        number = float(value)
        if number <= 0.0:
            return value, f"{path}: value must be positive or empty"
        return number, ""
    if kind == "float01":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value, f"{path}: expected a number, got {value!r}"
        number = float(value)
        if not (0.0 < number < 1.0):
            return value, f"{path}: end criteria must be between 0 and 1"
        return number, ""
    if kind == "box_mode":
        if str(value) not in ("auto", "manual"):
            return value, f"{path}: expected auto or manual"
        return str(value), ""
    if kind == "mesh_preset":
        if str(value) not in _MESH_PRESETS:
            return value, f"{path}: unknown mesh preset {value!r}"
        return str(value), ""
    return value, f"{path}: unsupported value type"


def get_value(project: Project, path: str) -> Any:
    resolved = _resolve(project, path)
    return getattr(resolved[0], resolved[1]) if resolved else None


def make_change(
    project: Project,
    path: str,
    new: Any,
    label: str,
    *,
    risk: str = "low",
) -> FixChange:
    """Create a displayable change with the current value from ``project``."""
    return FixChange(
        path=path,
        label=label,
        old=get_value(project, path),
        new=new,
        risk=risk,
    )


def apply_changes(
    project: Project,
    changes: list[FixChange],
    *,
    inplace: bool = False,
) -> tuple[Project, list[str]]:
    """Validate and apply changes atomically.

    Returns ``(project, errors)``.  When ``inplace`` is true and validation
    succeeds, the existing project object is updated field-by-field so every
    widget that already references it keeps working.
    """
    clone = copy.deepcopy(project)
    errors: list[str] = []
    for change in changes:
        resolved = _resolve(clone, change.path)
        if resolved is None:
            errors.append(f"{change.path}: field not found")
            continue
        parent, attr = resolved
        value, error = _validate_value(change.path, change.new)
        if error:
            errors.append(error)
            continue
        try:
            setattr(parent, attr, value)
        except Exception as exc:  # noqa: BLE001 - report, never half-apply
            errors.append(f"{change.path}: {exc}")
    if errors:
        return project, errors

    roundtrip_error = project_roundtrip_error(clone)
    if roundtrip_error:
        return project, [roundtrip_error]

    if not inplace:
        return clone, []

    for item in fields(Project):
        setattr(project, item.name, getattr(clone, item.name))
    return project, []


def apply_fix(
    project: Project, fix: FixProposal, *, inplace: bool = False
) -> tuple[Project, list[str]]:
    if not fix.changes:
        return project, ["This proposal contains no changes."]
    return apply_changes(project, list(fix.changes), inplace=inplace)


def project_roundtrip_error(project: Project) -> str:
    """Check that the project still serialises/deserialises after a change."""
    try:
        Project.from_dict(project.to_dict())
    except Exception as exc:  # noqa: BLE001
        return f"project cannot be restored after the change: {exc}"
    return ""


def validate_project(
    project: Project,
    extra_validator: Callable[[Project], list[str]] | None = None,
) -> list[str]:
    errors: list[str] = []
    roundtrip = project_roundtrip_error(project)
    if roundtrip:
        errors.append(roundtrip)
    if extra_validator is not None:
        try:
            errors.extend(list(extra_validator(project)))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"project validation failed: {exc}")
    return errors
