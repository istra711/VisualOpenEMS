"""Microstrip (MSL) port helpers — pure Python, no Qt/openEMS import.

The openEMS ``AddMSLPort`` needs two axes that the ``PortDef`` only carries
implicitly: the propagation direction (along the line) and the excitation
direction (through the substrate). :func:`resolve_msl_axes` derives them from
the port box when the explicit ``prop_direction``/``exc_direction`` fields are
empty.
"""

from __future__ import annotations

_AXES = ("x", "y", "z")


def _axis(value) -> str | None:
    """Normalise a direction to an axis letter, or None (empty/auto/invalid)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return _AXES[value] if 0 <= value < len(_AXES) else None
    txt = str(value).strip().lower()
    return txt if txt in _AXES else None


def resolve_msl_axes(port) -> tuple[str, str]:
    """Return ``(prop_dir, exc_dir)`` axis letters ('x'|'y'|'z') for an MSL port.

    Explicit ``prop_direction`` / ``exc_direction`` win; otherwise the axes are
    taken from the port-box extents: propagation = longest edge, excitation =
    shortest edge (openEMS ``Tutorials/MSL_NotchFilter.py`` uses ``'x', 'z'``
    for a microstrip whose length along x dominates the substrate height z).

    Raises ``ValueError`` when the axes collapse (all edges equal is fine as
    long as both chosen edges are non-zero).
    """
    ext = [abs(float(port.stop[i]) - float(port.start[i])) for i in range(3)]
    prop = _axis(getattr(port, "prop_direction", ""))
    exc = _axis(getattr(port, "exc_direction", ""))
    explicit_prop = prop is not None

    by_extent = sorted(range(3), key=lambda i: ext[i])  # ascending
    nonzero = [i for i in by_extent if ext[i] > 0]
    if prop is None:
        if not nonzero:
            raise ValueError(
                f"MSL port '{_port_name(port)}': zero-size port box — start and "
                f"stop are identical"
            )
        prop = _AXES[nonzero[-1]]  # longest edge
    if exc is None:
        exc = next((_AXES[i] for i in nonzero if _AXES[i] != prop), None)
        if exc is None:
            raise ValueError(
                f"MSL port '{_port_name(port)}': needs non-zero extent on two "
                f"axes (propagation + excitation); only '{prop}' has one"
            )

    if prop == exc:
        raise ValueError(
            f"MSL port '{_port_name(port)}': propagation and excitation axis are "
            f"both '{prop}' — set prop_direction/exc_direction explicitly"
        )
    pi, ei = _AXES.index(prop), _AXES.index(exc)
    if ext[pi] <= 0 and not explicit_prop:
        # a derived propagation axis must come from a real extent; an explicit
        # one may sit on a zero-length port plane (the generator extends the box
        # by one grid cell, openEMS needs it for its current probes)
        raise ValueError(
            f"MSL port '{_port_name(port)}': zero extent in propagation axis "
            f"'{prop}' — the port box must span the line direction"
        )
    if ext[ei] <= 0:
        raise ValueError(
            f"MSL port '{_port_name(port)}': zero extent in excitation axis "
            f"'{exc}' — the port box must span the substrate height"
        )
    return prop, exc


def _port_name(port) -> str:
    return str(getattr(port, "name", "") or "port")


def extend_zero_propagation(
    start,
    stop,
    prop_dir,
    grid_lines,
    *,
    reverse: bool = False,
):
    """Give an MSL port box one grid cell along the line when it has none.

    openEMS computes ``MSLPort.direction = sign(stop - start)`` along
    ``prop_dir``: a zero length yields 0, which zeroes the weight of its current
    probes (and adds no strip), so the port plane is widened to the next grid
    line. The port in the project keeps its zero length — only the generated
    simulation gets the cell. Returns ``(start, stop, extended)``.

    ``reverse`` matters: ``PortDef.oriented()`` swaps start/stop *after* this
    extension.  For a normal port the strip grows from ``start`` to ``stop``;
    for a reversed port that swap makes the old ``start`` the outer port plane,
    so the cell has to be added to ``start`` instead.  Otherwise a port at the
    high edge of a PCB grows outwards into the air margin and its excitation
    plane ends up outside the feed line.

    The growth direction points towards the centre of the grid, not simply to
    the next line: with an air margin the next line may be outside the
    structure (the high-side port would be extended away from the filter).
    """
    idx = _AXES.index(prop_dir) if str(prop_dir) in _AXES else None
    if idx is None or abs(float(stop[idx]) - float(start[idx])) > 0.0:
        return list(start), list(stop), False
    lines = sorted(float(v) for v in (grid_lines if grid_lines is not None else []))
    if len(lines) < 2:
        return list(start), list(stop), False
    pos = float(start[idx])
    # A grid line can be a few float32 ULPs away from the port plane.  Ignore
    # such near-duplicates; otherwise a port on the upper domain edge would
    # "extend" to a line at the same coordinate and get a zero-length metal
    # strip (openEMS reports it as an unused primitive).
    tol = 1e-6 * max(1.0, abs(pos))
    forward = [value for value in lines if value - pos > tol]
    backward = [value for value in lines if pos - value > tol]
    # Pick the side facing the domain interior. A port near the high edge grows
    # inwards (backward); one near the low edge grows inwards (forward). For a
    # port in the middle keep the historical forward choice.
    if abs(pos - lines[-1]) < abs(pos - lines[0]):
        target = max(backward) if backward else (min(forward) if forward else None)
    else:
        target = min(forward) if forward else (max(backward) if backward else None)
    if target is None:
        return list(start), list(stop), False
    widened_start = list(start)
    widened_stop = list(stop)
    if reverse:
        widened_start[idx] = target
    else:
        widened_stop[idx] = target
    return widened_start, widened_stop, True
