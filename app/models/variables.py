"""Named geometry variables and safe arithmetic expressions.

Geometry values are stored as plain numbers in the project model.  A primitive
may additionally carry an ``expressions`` map whose entries keep the text the
user typed (for example ``"L/2"``).  Whenever variables change, the expressions
are evaluated again and the numeric geometry fields are refreshed.

The evaluator is deliberately small and safe: literals, named variables,
parentheses, ``+ - * / // % **`` and a handful of single-argument math
functions.  No attributes, subscripts, comprehensions or arbitrary calls are
allowed.
"""

from __future__ import annotations

import ast
import copy
import keyword
import math
import re
from typing import Any, Iterable, Mapping

from app.numfmt import parse_number

#: Valid variable names.  Must be a valid expression identifier and must not
#: collide with a built-in function or constant (compared case-insensitively).
_VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_CONSTANTS = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
}

_FUNCTIONS = {
    "abs": abs,
    "acos": math.acos,
    "asin": math.asin,
    "atan": math.atan,
    "ceil": math.ceil,
    "cos": math.cos,
    "degrees": math.degrees,
    "exp": math.exp,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "radians": math.radians,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}

RESERVED_NAMES = (
    frozenset(_CONSTANTS)
    | frozenset(_FUNCTIONS)
    | frozenset(str(word).casefold() for word in keyword.kwlist)
)

_MAX_EXPONENT = 1000.0


class VariableError(ValueError):
    """Invalid variable definition or geometry expression."""


# ---------------------------------------------------------------------------
# Variable table helpers


def clean_name(name: Any) -> str:
    return str(name or "").strip()


def validate_variable_name(name: Any) -> str:
    """Return an error message for an invalid name, or ``""`` when valid."""
    text = clean_name(name)
    if not text:
        return "the name is empty"
    if not _VARIABLE_NAME_RE.match(text):
        return (
            "names must start with a letter or '_' and contain only letters, "
            "digits and '_'"
        )
    if text.casefold() in {r.casefold() for r in RESERVED_NAMES}:
        return f"'{text}' is reserved by a math function or constant"
    return ""


def _def_name(defn: Any) -> str:
    if isinstance(defn, Mapping):
        return clean_name(defn.get("name"))
    return clean_name(getattr(defn, "name", ""))


def _def_value(defn: Any) -> float | None:
    raw = defn.get("value") if isinstance(defn, Mapping) else getattr(defn, "value", None)
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        value = parse_number(str(raw if raw is not None else ""))
    if value is None or not math.isfinite(value):
        return None
    return value


def _def_raw_value(defn: Any) -> Any:
    return defn.get("value") if isinstance(defn, Mapping) else getattr(defn, "value", None)


def _def_expression(defn: Any) -> str:
    """Expression text for a definition, falling back to its numeric value."""
    raw = (
        defn.get("expression")
        if isinstance(defn, Mapping)
        else getattr(defn, "expression", "")
    )
    text = str(raw or "").strip()
    if text:
        return text
    value = _def_raw_value(defn)
    if isinstance(value, bool):
        return ""
    return str(value if value is not None else "").strip()


def _def_has_expression(defn: Any) -> bool:
    raw = (
        defn.get("expression")
        if isinstance(defn, Mapping)
        else getattr(defn, "expression", "")
    )
    return bool(str(raw or "").strip())


def _def_invalid_numeric_value(defn: Any) -> bool:
    value = _def_raw_value(defn)
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        try:
            return not math.isfinite(float(value))
        except (TypeError, ValueError, OverflowError):
            return True
    return False


def _def_comment(defn: Any) -> str:
    raw = defn.get("comment") if isinstance(defn, Mapping) else getattr(defn, "comment", "")
    return str(raw or "").strip()


def resolve_variable_defs(
    definitions: Iterable[Any],
) -> tuple[dict[str, float], list[str]]:
    """Evaluate a complete variable table, including equations.

    Variable values may reference other variables, including definitions that
    appear later in the table.  Names are case-insensitive.  The return value
    is ``(canonical name -> resolved value, errors)``.  Cycles, unknown names,
    invalid arithmetic and non-finite results are reported as user-facing
    errors.
    """
    entries = list(definitions or [])
    errors: list[str] = []
    seen: dict[str, str] = {}
    table: dict[str, tuple[str, str, Any, bool]] = {}

    for index, defn in enumerate(entries):
        name = _def_name(defn)
        label = name or f"row {index + 1}"
        name_error = validate_variable_name(name)
        if name_error:
            errors.append(f"Variable '{label}': {name_error}.")
            continue
        key = name.casefold()
        if key in seen:
            errors.append(
                f"Variable '{name}': the name is already used by "
                f"'{seen[key]}' (names are case-insensitive)."
            )
        else:
            seen[key] = name
        table[key] = (name, _def_expression(defn), defn, _def_has_expression(defn))

    values: dict[str, float] = {}
    state: dict[str, int] = {}

    def _visit(key: str, stack: list[str]) -> bool:
        status = state.get(key, 0)
        if status == 2:
            return True
        if status == 3:
            return False
        if status == 1:
            start = stack.index(key) if key in stack else 0
            cycle = [table[item][0] for item in stack[start:]] + [table[key][0]]
            errors.append(
                f"Variable '{table[key][0]}': circular reference "
                f"({' -> '.join(cycle)})."
            )
            return False

        state[key] = 1
        name, raw, defn, has_expression = table[key]
        if not has_expression and _def_invalid_numeric_value(defn):
            errors.append(f"Variable '{name}': value must be a finite number.")
            state[key] = 3
            return False

        failed = False
        for token in names_in_expression(raw):
            ref_key = token.casefold()
            if ref_key in table and not _visit(ref_key, stack + [key]):
                failed = True
        if failed:
            state[key] = 3
            return False

        try:
            value = evaluate_expression(raw, values, context=f"Variable '{name}'")
        except VariableError as exc:
            errors.append(str(exc))
            state[key] = 3
            return False

        values[name] = value
        state[key] = 2
        return True

    for key in table:
        _visit(key, [])
    return values, errors


def validate_variable_defs(definitions: Iterable[Any]) -> list[str]:
    """Validate a complete table; returns one human-readable error per problem."""
    return resolve_variable_defs(definitions)[1]


def variable_values_from_defs(definitions: Iterable[Any]) -> dict[str, float]:
    """Name -> resolved numeric value for valid entries (later duplicates win)."""
    return resolve_variable_defs(definitions)[0]


def canonical_variable_names(definitions: Iterable[Any]) -> dict[str, str]:
    """Case-insensitive lookup map for the names currently defined."""
    return {_def_name(d).casefold(): _def_name(d) for d in definitions or [] if _def_name(d)}


# ---------------------------------------------------------------------------
# Expression entries stored on primitives / ports / dumps


def expression_text(entry: Any) -> str:
    """Expression text from a plain string or ``{"expr": ..., "scale": ...}``."""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, Mapping):
        return str(entry.get("expr", "") or "").strip()
    return ""


def expression_scale(entry: Any) -> float:
    """Multiplier stored with an expression (default 1.0)."""
    if not isinstance(entry, Mapping):
        return 1.0
    try:
        scale = float(entry.get("scale", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return scale if math.isfinite(scale) else 1.0


def expression_entry(text: str, scale: float = 1.0) -> Any:
    """Store ``text`` compactly; scaled expressions keep the scale explicitly."""
    text = str(text or "").strip()
    if not text:
        return ""
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    if not math.isfinite(scale) or abs(scale - 1.0) <= 1e-15:
        return text
    return {"expr": text, "scale": scale}


def rename_expression(text: str, renames: Mapping[str, str]) -> str:
    """Rename identifiers in an expression in one pass.

    ``renames`` maps old names to new names.  Matching is case-insensitive so
    a variable typed in a different case follows a rename too.  Unknown names
    and math functions/constants are left untouched.
    """
    lookup = {
        clean_name(old).casefold(): clean_name(new)
        for old, new in (renames or {}).items()
        if clean_name(old) and clean_name(new)
    }
    if not lookup:
        return str(text or "")

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        return lookup.get(token.casefold(), token)

    return _IDENTIFIER_RE.sub(_replace, str(text or ""))


def rename_expression_entry(entry: Any, renames: Mapping[str, str]) -> Any:
    if isinstance(entry, str):
        return rename_expression(entry, renames)
    if isinstance(entry, Mapping):
        out = dict(entry)
        out["expr"] = rename_expression(str(entry.get("expr", "") or ""), renames)
        return out
    return entry


def rename_expression_map(
    expressions: Mapping[str, Any] | None, renames: Mapping[str, str]
) -> dict[str, Any]:
    if not isinstance(expressions, Mapping) or not renames:
        return dict(expressions or {})
    return {
        str(key): rename_expression_entry(value, renames)
        for key, value in expressions.items()
    }


def names_in_expression(text: str) -> list[str]:
    """All identifier-like tokens except built-in functions and constants."""
    reserved = {name.casefold() for name in RESERVED_NAMES}
    return [
        token
        for token in _IDENTIFIER_RE.findall(str(text or ""))
        if token.casefold() not in reserved
    ]


def expression_references(
    expressions: Mapping[str, Any] | None, names: Iterable[str]
) -> dict[str, int]:
    """Count references to each defined variable in an expression map."""
    canonical = {clean_name(n).casefold(): clean_name(n) for n in names if clean_name(n)}
    counts: dict[str, int] = {}
    if not isinstance(expressions, Mapping):
        return counts
    for entry in expressions.values():
        for token in names_in_expression(expression_text(entry)):
            name = canonical.get(token.casefold())
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Safe expression evaluator


def _casefold_values(variables: Mapping[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in (variables or {}).items():
        name = clean_name(key)
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if name and math.isfinite(number):
            out[name.casefold()] = number
    return out


def _context(prefix: str, message: str) -> str:
    return f"{prefix}: {message}" if prefix else message


def evaluate_expression(
    text: Any,
    variables: Mapping[str, float] | None = None,
    *,
    context: str = "",
) -> float:
    """Evaluate a number or safe arithmetic expression.

    Names are looked up case-insensitively.  The returned value is always a
    finite float.  ``VariableError`` describes what is wrong in user terms.
    """
    raw = str(text if text is not None else "").strip()
    if not raw:
        raise VariableError(_context(context, "enter a number or variable"))
    plain = parse_number(raw)
    if plain is not None:
        return float(plain)

    try:
        tree = ast.parse(raw, mode="eval")
    except SyntaxError:
        raise VariableError(
            _context(context, f"invalid expression {raw!r}")
        ) from None

    lookup = _casefold_values(variables or {})
    try:
        value = _eval_node(tree.body, lookup)
    except VariableError as e:
        raise VariableError(_context(context, str(e))) from None
    except ZeroDivisionError:
        raise VariableError(_context(context, "division by zero")) from None
    except (OverflowError, ValueError, TypeError) as e:
        raise VariableError(_context(context, str(e) or "invalid expression")) from None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VariableError(_context(context, f"{raw!r} is not a number"))
    try:
        result = float(value)
    except OverflowError:
        raise VariableError(_context(context, "the result is too large")) from None
    if not math.isfinite(result):
        raise VariableError(_context(context, "the result is not finite"))
    return result


def _eval_node(node: ast.AST, variables: Mapping[str, float]) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise VariableError("only numbers are allowed")
        return float(node.value)

    if isinstance(node, ast.Name):
        key = node.id.casefold()
        if key in variables:
            return float(variables[key])
        if key in _CONSTANTS:
            return float(_CONSTANTS[key])
        if key in _FUNCTIONS:
            raise VariableError(f"function '{node.id}' needs arguments")
        raise VariableError(f"unknown variable '{node.id}'")

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        op = node.op
        try:
            if isinstance(op, ast.Add):
                return left + right
            if isinstance(op, ast.Sub):
                return left - right
            if isinstance(op, ast.Mult):
                return left * right
            if isinstance(op, ast.Div):
                return left / right
            if isinstance(op, ast.FloorDiv):
                return left // right
            if isinstance(op, ast.Mod):
                return left % right
            if isinstance(op, ast.Pow):
                if abs(right) > _MAX_EXPONENT:
                    raise VariableError("exponent is too large")
                return left**right
        except VariableError:
            raise
        except ZeroDivisionError:
            raise VariableError("division by zero") from None
        except (OverflowError, ValueError) as e:
            raise VariableError(str(e) or "invalid arithmetic") from None
        raise VariableError("unsupported operator")

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, variables)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise VariableError("unsupported unary operator")

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise VariableError("unsupported function call")
        func_name = node.func.id
        func = _FUNCTIONS.get(func_name.casefold())
        if func is None:
            raise VariableError(f"unknown function '{func_name}'")
        if node.keywords:
            raise VariableError("keyword arguments are not supported")
        if len(node.args) != 1:
            raise VariableError(f"function '{func_name}' takes exactly one argument")
        argument = _eval_node(node.args[0], variables)
        try:
            return float(func(argument))
        except (OverflowError, ValueError, TypeError) as e:
            raise VariableError(str(e) or f"invalid argument for '{func_name}'") from None

    if isinstance(node, ast.Expression):
        return _eval_node(node.body, variables)

    raise VariableError("unsupported expression")


# ---------------------------------------------------------------------------
# Apply / rename expressions on geometry objects


def _prim_name(prim: Mapping[str, Any]) -> str:
    return str(prim.get("name") or "primitive")


def _expression_error(context: str, key: str, exc: Exception) -> str:
    return f"{context}.{key}: {exc}"


def _assign_primitive_field(prim: dict[str, Any], key: str, value: float) -> bool:
    parts = str(key).split(".")
    if len(parts) == 2 and parts[0] in ("start", "stop", "center"):
        idx = int(parts[1]) if parts[1].isdigit() else -1
        arr = prim.get(parts[0])
        if not isinstance(arr, list) or not (0 <= idx < len(arr)):
            return False
        arr[idx] = float(value)
        return True
    if len(parts) == 3 and parts[0] == "points":
        idx = int(parts[1]) if parts[1].isdigit() else -1
        axis = int(parts[2]) if parts[2].isdigit() else -1
        points = prim.get("points")
        if not isinstance(points, list) or not (0 <= idx < len(points)):
            return False
        point = points[idx]
        if not isinstance(point, list) or not (0 <= axis < len(point)):
            return False
        point[axis] = float(value)
        return True
    if not parts or len(parts) != 1:
        return False
    scalar = parts[0]
    if scalar in {
        "radius",
        "coord",
        "length",
        "resistance",
        "inductance",
        "capacitance",
    }:
        prim[scalar] = float(value)
        return True
    return False


def _refresh_primitive_bbox(prim: dict[str, Any]) -> None:
    """Recompute derived bounds after expressions changed polygon/wire data."""
    kind = str(prim.get("kind") or "box").strip().lower()
    if kind == "sphere":
        center = [float(v) for v in (prim.get("center") or [0.0, 0.0, 0.0])]
        radius = float(prim.get("radius", 0.0) or 0.0)
        if radius < 0.0:
            raise ValueError("sphere radius must be >= 0")
        if len(center) != 3:
            raise ValueError("sphere center needs three coordinates")
        prim["start"] = [center[i] - radius for i in range(3)]
        prim["stop"] = [center[i] + radius for i in range(3)]
        return
    if kind == "polygon":
        axis = str(prim.get("axis") or "z").strip().lower()
        axis_idx = "xyz".index(axis) if axis in "xyz" else 2
        coord = float(prim.get("coord", 0.0) or 0.0)
        length = max(0.0, float(prim.get("length") or 0.0))
        points = prim.get("points") or []
        if len(points) < 3:
            raise ValueError("polygon needs at least three points")
        c0 = (axis_idx + 1) % 3
        c1 = (axis_idx + 2) % 3
        xs = [float(pt[0]) for pt in points]
        ys = [float(pt[1]) for pt in points]
        start = [0.0, 0.0, 0.0]
        stop = [0.0, 0.0, 0.0]
        start[axis_idx] = coord
        stop[axis_idx] = coord + length
        start[c0], stop[c0] = min(xs), max(xs)
        start[c1], stop[c1] = min(ys), max(ys)
        prim["start"], prim["stop"] = start, stop
        return
    if kind in ("wire", "curve"):
        points = prim.get("points") or []
        if len(points) < 2:
            raise ValueError(f"{kind} needs at least two points")
        radius = max(0.0, float(prim.get("radius", 0.0) or 0.0))
        xs = [float(pt[0]) for pt in points]
        ys = [float(pt[1]) for pt in points]
        zs = [float(pt[2]) for pt in points]
        start = [min(xs), min(ys), min(zs)]
        stop = [max(xs), max(ys), max(zs)]
        if kind == "wire":
            start = [v - radius for v in start]
            stop = [v + radius for v in stop]
        prim["start"], prim["stop"] = start, stop


def resolve_primitive(
    prim: Mapping[str, Any], variables: Mapping[str, float]
) -> tuple[dict[str, Any], list[str]]:
    """Return a copy with numeric fields refreshed from ``expressions``."""
    out = copy.deepcopy(dict(prim))
    errors: list[str] = []
    expressions = out.get("expressions")
    if not isinstance(expressions, Mapping) or not expressions:
        return out, errors

    name = _prim_name(out)
    for key, entry in expressions.items():
        text = expression_text(entry)
        if not text:
            continue
        try:
            value = evaluate_expression(text, variables, context=f"{name}.{key}")
            value *= expression_scale(entry)
        except VariableError as e:
            errors.append(str(e))
            continue
        if not _assign_primitive_field(out, str(key), value):
            errors.append(f"{name}.{key}: unsupported geometry field")
    if not errors:
        try:
            _refresh_primitive_bbox(out)
        except (TypeError, ValueError, IndexError) as e:
            errors.append(f"{name}: could not update derived bounds ({e})")
    return out, errors


def _assign_object_field(obj: Any, key: str, value: float) -> bool:
    parts = str(key).split(".")
    if len(parts) == 2 and parts[0] in ("start", "stop", "center"):
        idx = int(parts[1]) if parts[1].isdigit() else -1
        arr = getattr(obj, parts[0], None)
        if not isinstance(arr, list) or not (0 <= idx < len(arr)):
            return False
        arr[idx] = float(value)
        return True
    if len(parts) == 1:
        if hasattr(obj, parts[0]):
            current = getattr(obj, parts[0])
            if isinstance(current, (int, float)) or current is None:
                setattr(obj, parts[0], float(value))
                return True
    return False


def _resolve_object(obj: Any, variables: Mapping[str, float], label: str) -> list[str]:
    errors: list[str] = []
    expressions = getattr(obj, "expressions", None)
    if not isinstance(expressions, Mapping) or not expressions:
        return errors
    for key, entry in expressions.items():
        text = expression_text(entry)
        if not text:
            continue
        try:
            value = evaluate_expression(text, variables, context=f"{label}.{key}")
            value *= expression_scale(entry)
        except VariableError as e:
            errors.append(str(e))
            continue
        if not _assign_object_field(obj, str(key), value):
            errors.append(f"{label}.{key}: unsupported field")
    return errors


def resolve_port(port: Any, variables: Mapping[str, float]) -> tuple[Any, list[str]]:
    out = copy.deepcopy(port)
    return out, _resolve_object(out, variables, f"port {getattr(out, 'name', '?')}")


def resolve_dump(dump: Any, variables: Mapping[str, float]) -> tuple[Any, list[str]]:
    out = copy.deepcopy(dump)
    return out, _resolve_object(out, variables, f"dump {getattr(out, 'name', '?')}")


def resolve_nf2ff(nf2ff: Any, variables: Mapping[str, float]) -> tuple[Any, list[str]]:
    out = copy.deepcopy(nf2ff)
    errors = _resolve_object(out, variables, "NF2FF box")
    if not errors:
        try:
            start = [float(v) for v in out.start]
            stop = [float(v) for v in out.stop]
            out.center = [
                0.5 * (start[i] + stop[i]) for i in range(3)
            ]
        except (TypeError, ValueError, IndexError) as e:
            errors.append(f"NF2FF box: could not update center ({e})")
    return out, errors
