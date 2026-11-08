"""CSXCAD lumped-element placement (R/L/C) used by the project builders.

A lumped element is a CSXCAD *property* (not a material): a small box/line at
the component location, with a direction axis and R/L/C values.  The installed
CSXCAD supports ``AddLumpedElement(name, ny=..., caps=..., R=..., C=..., L=...)``;
older wrappers only expose the setters, so both paths are handled.
"""

from __future__ import annotations

from typing import Any

_AXIS = {"x": 0, "y": 1, "z": 2}


def add_lumped_element(csx, prim: dict[str, Any]):
    """Add one lumped element from a project primitive dict.

    Primitive keys: ``name``, ``start``, ``stop``, ``direction`` (x/y/z),
    ``resistance`` [Ohm], ``inductance`` [H], ``capacitance`` [F], ``caps``
    (PEC end plates, default True) and ``priority``.
    """
    name = str(prim.get("name") or "lumped")
    axis = _AXIS.get(str(prim.get("direction", "z")).strip().lower(), 2)
    try:
        start = [float(v) for v in prim.get("start", [0.0, 0.0, 0.0])]
        stop = [float(v) for v in prim.get("stop", start)]
        resistance = float(prim.get("resistance", 50.0) or 0.0)
        inductance = float(prim.get("inductance", 0.0) or 0.0)
        capacitance = float(prim.get("capacitance", 0.0) or 0.0)
        caps = bool(prim.get("caps", True))
        priority = int(prim.get("priority", 15))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Lumped element '{name}' has invalid values: {exc}") from exc

    try:
        prop = csx.AddLumpedElement(
            name,
            ny=axis,
            caps=caps,
            R=resistance,
            C=capacitance,
            L=inductance,
        )
    except (TypeError, AssertionError):
        # Older CSXCAD wrappers do not accept keyword arguments.
        prop = csx.AddLumpedElement(name)
        for setter, value in (
            ("SetDirection", axis),
            ("SetCaps", caps),
            ("SetResistance", resistance),
            ("SetCapacity", capacitance),
            ("SetInductance", inductance),
        ):
            try:
                getattr(prop, setter)(value)
            except Exception:  # noqa: BLE001 — best effort for old wrappers
                pass
    prop.AddBox(start=start, stop=stop, priority=priority)
    return prop
