"""Structured project patches for AI-assisted edits.

A patch is a list of small, reviewable operations.  It is applied to a deep
copy first, preflighted, and only then handed to the main window.  There is no
arbitrary Python or file access in this module.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, fields
from typing import Any

from app.ai.fixes import FixChange, apply_changes, get_value
from app.ai.types import FixProposal
from app.models.project import (
    MaterialDef,
    PortDef,
    Project,
    VariableDef,
)
from app.models.variables import validate_variable_name

_ALLOWED_OPS = {
    "set",
    "set_variable",
    "remove_variable",
    "set_port",
    "add_port",
    "remove_port",
    "add_primitive",
    "update_primitive",
    "remove_primitive",
    "add_material",
    "update_material",
    "set_mesh",
    "set_sim",
    "set_nf2ff",
    "set_boundary",
}
_PRIMITIVE_KINDS = {
    "box",
    "cylinder",
    "sphere",
    "polygon",
    "wire",
    "curve",
    "stl",
    "lumped",
}
_MATERIAL_KINDS = {"pec", "dielectric", "conducting_sheet", "lumped_rlc", "ignore"}
_PORT_TYPES = {"lumped", "msl", "rect_waveguide", "waveguide", "probe"}


@dataclass(frozen=True)
class PatchOperation:
    op: str
    payload: dict[str, Any]


@dataclass
class ProjectPatch:
    title: str = "AI project change"
    summary: str = ""
    operations: list[PatchOperation] = None  # type: ignore[assignment]
    assumptions: list[str] = None  # type: ignore[assignment]
    risk: str = "medium"

    def __post_init__(self) -> None:
        if self.operations is None:
            self.operations = []
        if self.assumptions is None:
            self.assumptions = []

    def diff_text(self, project: Project, primitives: list[dict] | None = None) -> str:
        lines = [f"Proposal: {self.title}"]
        if self.summary:
            lines.append(self.summary)
        lines.append("")
        for index, operation in enumerate(self.operations, start=1):
            lines.append(f"{index}. {operation.op}")
            payload = operation.payload
            if operation.op == "set":
                path = str(payload.get("path") or "")
                old = get_value(project, path)
                lines.append(f"   {path}: {old!r} -> {payload.get('value')!r}")
            elif operation.op in ("set_variable", "remove_variable"):
                lines.append(f"   name: {payload.get('name')}")
                if "value" in payload:
                    lines.append(f"   value: {payload.get('value')!r}")
                if payload.get("expression"):
                    lines.append(f"   expression: {payload.get('expression')}")
            elif operation.op in ("set_port", "add_port", "remove_port"):
                lines.append(f"   port: {payload.get('name') or payload.get('index')}")
                if "fields" in payload:
                    for key, value in dict(payload.get("fields") or {}).items():
                        lines.append(f"   {key}: {value!r}")
            elif operation.op in (
                "add_primitive",
                "update_primitive",
                "remove_primitive",
            ):
                lines.append(f"   primitive: {payload.get('name')}")
                if "fields" in payload:
                    for key, value in dict(payload.get("fields") or {}).items():
                        lines.append(f"   {key}: {value!r}")
            elif operation.op in ("add_material", "update_material"):
                lines.append(f"   material: {payload.get('name')}")
                for key, value in dict(payload.get("fields") or {}).items():
                    lines.append(f"   {key}: {value!r}")
            elif operation.op in ("set_mesh", "set_sim", "set_nf2ff", "set_boundary"):
                for key, value in dict(payload.get("fields") or {}).items():
                    lines.append(f"   {key}: {value!r}")
        if self.assumptions:
            lines.extend(["", "Assumptions:"])
            lines.extend(f"- {item}" for item in self.assumptions)
        lines.extend(["", f"Risk: {self.risk}"])
        return "\n".join(lines)


def parse_project_patch(payload: Any) -> tuple[ProjectPatch | None, list[str]]:
    """Parse the JSON shape returned by the language model."""
    if not isinstance(payload, dict):
        return None, ["The proposal is not a JSON object."]
    raw_ops = payload.get("operations")
    if not isinstance(raw_ops, list) or not raw_ops:
        return None, ["The proposal has no operations."]
    if len(raw_ops) > 100:
        return None, ["The proposal has more than 100 operations."]
    operations: list[PatchOperation] = []
    errors: list[str] = []
    for index, raw in enumerate(raw_ops):
        if not isinstance(raw, dict):
            errors.append(f"Operation {index + 1} is not an object.")
            continue
        op = str(raw.get("op") or "").strip()
        if op not in _ALLOWED_OPS:
            errors.append(f"Operation {index + 1}: unsupported op '{op}'.")
            continue
        body = {key: value for key, value in raw.items() if key != "op"}
        operations.append(PatchOperation(op=op, payload=body))
    if errors:
        return None, errors
    assumptions = payload.get("assumptions") or []
    if isinstance(assumptions, str):
        assumptions = [assumptions]
    if not isinstance(assumptions, list):
        assumptions = []
    return (
        ProjectPatch(
            title=str(payload.get("title") or "AI project change"),
            summary=str(payload.get("summary") or ""),
            operations=operations,
            assumptions=[str(item) for item in assumptions][:20],
            risk=str(payload.get("risk") or "medium"),
        ),
        [],
    )


def patch_from_fix(fix: FixProposal) -> ProjectPatch:
    return ProjectPatch(
        title=fix.title,
        summary=fix.description,
        operations=[PatchOperation("set", {"path": change.path, "value": change.new}) for change in fix.changes],
        assumptions=[],
        risk="low",
    )


def _port_index(project: Project, payload: dict[str, Any]) -> tuple[int, str]:
    raw_index = payload.get("index")
    name = str(payload.get("name") or "").strip()
    if raw_index is not None:
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            return -1, "port index is not an integer"
        if not (0 <= index < len(project.ports)):
            return -1, f"port index {index} is out of range"
        return index, ""
    if name:
        for index, port in enumerate(project.ports):
            if str(port.name or "").casefold() == name.casefold():
                return index, ""
        return -1, f"port '{name}' was not found"
    return -1, "port operation needs an index or name"


def _primitive_index(primitives: list[dict], payload: dict[str, Any]) -> tuple[int, str]:
    name = str(payload.get("name") or "").strip()
    if not name:
        return -1, "primitive operation needs a name"
    for index, primitive in enumerate(primitives):
        if str(primitive.get("name") or "").casefold() == name.casefold():
            return index, ""
    return -1, f"primitive '{name}' was not found"


def _as_vec3(value: Any) -> list[float] | None:
    try:
        vector = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if len(vector) != 3 or not all(math.isfinite(item) for item in vector):
        return None
    return vector


def _port_fields(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    allowed = {item.name for item in fields(PortDef)}
    raw = payload.get("fields")
    if not isinstance(raw, dict):
        raw = {key: value for key, value in payload.items() if key not in ("index", "name")}
    else:
        raw = dict(raw)
        if not str(raw.get("name") or "").strip() and payload.get("name"):
            raw["name"] = payload.get("name")
    errors: list[str] = []
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed:
            errors.append(f"Port field '{key}' is not allowed.")
            continue
        if key in ("start", "stop"):
            vector = _as_vec3(value)
            if vector is None:
                errors.append(f"Port field '{key}' must be three finite numbers.")
            else:
                out[key] = vector
        elif key in ("active", "excite_port", "reverse"):
            if not isinstance(value, bool):
                errors.append(f"Port field '{key}' must be true/false.")
            else:
                out[key] = value
        elif key == "type":
            text = str(value)
            if text not in _PORT_TYPES:
                errors.append(f"Unsupported port type '{text}'.")
            else:
                out[key] = text
        elif key == "name":
            text = str(value).strip()
            if not text:
                errors.append("Port name must not be empty.")
            else:
                out[key] = text
        elif key == "expressions":
            if not isinstance(value, dict):
                errors.append("Port expressions must be an object.")
            else:
                out[key] = dict(value)
        elif key in ("direction", "prop_direction", "exc_direction", "mesh_dirs", "lumped_width", "metal", "wg_mode"):
            out[key] = str(value)
        else:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                errors.append(f"Port field '{key}' must be numeric.")
    return out, errors


def _primitive_fields(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    raw = payload.get("fields")
    if not isinstance(raw, dict):
        return {}, ["Primitive operation needs a 'fields' object."]
    raw = dict(raw)
    if not str(raw.get("name") or "").strip() and payload.get("name"):
        raw["name"] = payload.get("name")
    allowed = {
        "kind",
        "name",
        "material",
        "priority",
        "start",
        "stop",
        "center",
        "radius",
        "points",
        "axis",
        "coord",
        "length",
        "expressions",
        "edges2grid",
        "ratio",
        "corner_radius",
        "inner_radius",
        "outer_radius",
        "angle",
        "thickness",
    }
    errors: list[str] = []
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed:
            errors.append(f"Primitive field '{key}' is not allowed.")
            continue
        if key in ("start", "stop", "center"):
            vector = _as_vec3(value)
            if vector is None:
                errors.append(f"Primitive field '{key}' must be three finite numbers.")
            else:
                out[key] = vector
        elif key == "points":
            if not isinstance(value, list) or len(value) < 2:
                errors.append("Primitive points must be a list.")
            else:
                out[key] = list(value)
        elif key == "expressions":
            if not isinstance(value, dict):
                errors.append("Primitive expressions must be an object.")
            else:
                out[key] = dict(value)
        elif key in ("kind", "name", "material", "axis", "edges2grid"):
            out[key] = str(value)
        else:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                errors.append(f"Primitive field '{key}' must be numeric.")
    return out, errors


def _material_fields(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    raw = payload.get("fields")
    if not isinstance(raw, dict):
        return {}, ["Material operation needs a 'fields' object."]
    raw = dict(raw)
    if not str(raw.get("name") or "").strip() and payload.get("name"):
        raw["name"] = payload.get("name")
    allowed = {item.name for item in fields(MaterialDef)}
    errors: list[str] = []
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in allowed:
            errors.append(f"Material field '{key}' is not allowed.")
            continue
        if key == "name":
            text = str(value).strip()
            if not text:
                errors.append("Material name must not be empty.")
            else:
                out[key] = text
        elif key == "kind":
            text = str(value)
            if text not in _MATERIAL_KINDS:
                errors.append(f"Unsupported material kind '{text}'.")
            else:
                out[key] = text
        elif key == "color":
            if not isinstance(value, (list, tuple)) or len(value) != 4:
                errors.append("Material color must have four components.")
            else:
                try:
                    out[key] = tuple(float(item) for item in value)
                except (TypeError, ValueError):
                    errors.append("Material color must be numeric.")
        else:
            try:
                out[key] = float(value)
            except (TypeError, ValueError):
                errors.append(f"Material field '{key}' must be numeric.")
    return out, errors


def _set_dataclass_fields(obj: Any, raw: Any) -> list[str]:
    if not isinstance(raw, dict):
        return ["Operation needs a 'fields' object."]
    allowed = {item.name for item in fields(type(obj))}
    errors: list[str] = []
    for key, value in raw.items():
        if key not in allowed:
            errors.append(f"Field '{key}' is not allowed on {type(obj).__name__}.")
            continue
        if key.endswith("_file") or key in ("path", "source"):
            errors.append(
                f"{type(obj).__name__}.{key} is a file reference and is not "
                "changed by an AI patch."
            )
            continue
        current = getattr(obj, key, None)
        if isinstance(current, bool):
            if not isinstance(value, bool):
                errors.append(f"{type(obj).__name__}.{key} must be true/false.")
                continue
        elif isinstance(current, int):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{type(obj).__name__}.{key} must be a number.")
                continue
            value = int(value)
        elif isinstance(current, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{type(obj).__name__}.{key} must be a number.")
                continue
            value = float(value)
        elif isinstance(current, list):
            if not isinstance(value, list):
                errors.append(f"{type(obj).__name__}.{key} must be a list.")
                continue
        elif isinstance(current, str):
            if not isinstance(value, str):
                errors.append(f"{type(obj).__name__}.{key} must be text.")
                continue
        try:
            setattr(obj, key, copy.deepcopy(value))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Could not set {type(obj).__name__}.{key}: {exc}")
    return errors


def apply_project_patch(
    project: Project,
    primitives: list[dict] | None,
    patch: ProjectPatch,
) -> tuple[Project, list[dict], list[str]]:
    """Apply a patch to a copy.  The caller decides whether to publish it."""
    clone = copy.deepcopy(project)
    prims = copy.deepcopy(list(primitives if primitives is not None else (project.primitives or [])))
    errors: list[str] = []
    scalar_changes: list[FixChange] = []

    for index, operation in enumerate(patch.operations, start=1):
        op = operation.op
        payload = operation.payload or {}
        before = len(errors)
        try:
            if op == "set":
                path = str(payload.get("path") or "").strip()
                if path in ("name", "notes"):
                    setattr(clone, path, str(payload.get("value") or ""))
                else:
                    scalar_changes.append(
                        FixChange(
                            path=path,
                            label=path,
                            old=get_value(clone, path),
                            new=payload.get("value"),
                        )
                    )
            elif op in ("set_variable", "remove_variable"):
                name = str(payload.get("name") or "").strip()
                if not name:
                    errors.append(f"Operation {index}: variable name is missing.")
                else:
                    existing = next(
                        (
                            variable
                            for variable in clone.variables
                            if str(variable.name or "").casefold() == name.casefold()
                        ),
                        None,
                    )
                    if op == "remove_variable":
                        if existing is None:
                            errors.append(f"Operation {index}: variable '{name}' not found.")
                        else:
                            clone.variables = [
                                variable
                                for variable in clone.variables
                                if variable is not existing
                            ]
                    else:
                        name_error = validate_variable_name(name)
                        if name_error:
                            errors.append(f"Operation {index}: variable '{name}': {name_error}.")
                        else:
                            try:
                                value = float(payload.get("value", 0.0))
                            except (TypeError, ValueError):
                                errors.append(f"Operation {index}: variable value is not numeric.")
                                value = 0.0
                            expression = str(payload.get("expression") or "")
                            comment = str(payload.get("comment") or "")
                            if existing is None:
                                clone.variables.append(
                                    VariableDef(
                                        name=name,
                                        value=value,
                                        expression=expression,
                                        comment=comment,
                                    )
                                )
                            else:
                                existing.value = value
                                existing.expression = expression
                                existing.comment = comment
            elif op in ("set_port", "add_port", "remove_port"):
                if op == "add_port":
                    fields_in, field_errors = _port_fields(payload)
                    errors.extend(
                        f"Operation {index}: {item}" for item in field_errors
                    )
                    if not str(fields_in.get("name") or "").strip():
                        errors.append(f"Operation {index}: new port needs a name.")
                    if len(errors) == before:
                        if any(
                            str(port.name or "").casefold()
                            == str(fields_in["name"]).casefold()
                            for port in clone.ports
                        ):
                            errors.append(f"Operation {index}: port name already exists.")
                        else:
                            clone.ports.append(PortDef(**fields_in))
                else:
                    port_index, error = _port_index(clone, payload)
                    if error:
                        errors.append(f"Operation {index}: {error}")
                    elif op == "remove_port":
                        clone.ports.pop(port_index)
                    else:
                        fields_in, field_errors = _port_fields(payload)
                        errors.extend(
                            f"Operation {index}: {item}" for item in field_errors
                        )
                        if len(errors) == before:
                            port = clone.ports[port_index]
                            for key, value in fields_in.items():
                                setattr(port, key, value)
            elif op in ("add_primitive", "update_primitive", "remove_primitive"):
                if op == "add_primitive":
                    fields_in, field_errors = _primitive_fields(payload)
                    errors.extend(f"Operation {index}: {item}" for item in field_errors)
                    if str(fields_in.get("kind") or "") not in _PRIMITIVE_KINDS:
                        errors.append(f"Operation {index}: unsupported primitive kind.")
                    if not str(fields_in.get("name") or "").strip():
                        errors.append(f"Operation {index}: new primitive needs a name.")
                    if len(errors) == before:
                        if any(
                            str(item.get("name") or "").casefold()
                            == str(fields_in["name"]).casefold()
                            for item in prims
                        ):
                            errors.append(f"Operation {index}: primitive name already exists.")
                        else:
                            prims.append(fields_in)
                else:
                    prim_index, error = _primitive_index(prims, payload)
                    if error:
                        errors.append(f"Operation {index}: {error}")
                    elif op == "remove_primitive":
                        prims.pop(prim_index)
                    else:
                        fields_in, field_errors = _primitive_fields(payload)
                        errors.extend(
                            f"Operation {index}: {item}" for item in field_errors
                        )
                        if len(errors) == before:
                            prims[prim_index].update(fields_in)
            elif op in ("add_material", "update_material"):
                fields_in, field_errors = _material_fields(payload)
                errors.extend(f"Operation {index}: {item}" for item in field_errors)
                name = str(fields_in.get("name") or "").strip()
                if not name:
                    errors.append(f"Operation {index}: material needs a name.")
                elif len(errors) == before:
                    existing = next(
                        (
                            material
                            for material in clone.materials
                            if str(material.name or "").casefold() == name.casefold()
                        ),
                        None,
                    )
                    if op == "add_material":
                        if existing is not None:
                            errors.append(f"Operation {index}: material already exists.")
                        else:
                            clone.materials.append(MaterialDef(**fields_in))
                    elif existing is None:
                        errors.append(f"Operation {index}: material '{name}' not found.")
                    else:
                        for key, value in fields_in.items():
                            setattr(existing, key, value)
            elif op in ("set_mesh", "set_sim", "set_nf2ff", "set_boundary"):
                target = {
                    "set_mesh": clone.mesh,
                    "set_sim": clone.sim,
                    "set_nf2ff": clone.nf2ff,
                    "set_boundary": clone.boundary,
                }[op]
                errors.extend(
                    f"Operation {index}: {item}"
                    for item in _set_dataclass_fields(target, payload.get("fields"))
                )
            else:
                errors.append(f"Operation {index}: unsupported op '{op}'.")
        except Exception as exc:  # noqa: BLE001 - one bad operation must not crash the UI
            errors.append(f"Operation {index}: {exc}")

    if scalar_changes:
        clone, scalar_errors = apply_changes(clone, scalar_changes, inplace=True)
        errors.extend(scalar_errors)

    if errors:
        return project, list(primitives or []), errors
    clone.primitives = list(prims)
    return clone, prims, []


def patch_from_json(payload: Any) -> tuple[ProjectPatch | None, list[str]]:
    return parse_project_patch(payload)
