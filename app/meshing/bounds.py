"""Cartesian axis-aligned bounding boxes for project primitive dicts.

The project stores most primitives with an exact AABB in ``start``/``stop``
(boxes carry their corners, STL/imported meshes their triangle bounds, spheres
and polygons their derived bbox). Cylinders are the exception: they only store
their **axis** endpoints, so the radius must be added on every axis that is
perpendicular to the cylinder axis. The NF2FF fit and any other consumer that
needs the true geometry envelope must go through this helper — reading
``start``/``stop`` directly silently drops the radius (and makes a thin dipole
look like an infinitely thin line).

This mirrors :func:`app.meshing.polar.polar_primitive_bbox` for the cylindrical
coordinate system.
"""

from __future__ import annotations

from typing import Any

_ALLOWED_KINDS = ("box", "stl", "cylinder", "sphere", "polygon")


def _span(a: float, b: float) -> tuple[float, float]:
    return (a, b) if a <= b else (b, a)


def cartesian_primitive_bbox(
    prim: dict[str, Any],
) -> tuple[list[float], list[float]] | None:
    """AABB ``(lo, hi)`` in drawing units for a primitive dict, or ``None``.

    Uses ``start``/``stop`` when present (already the exact bbox for every kind
    except cylinders) and additionally expands the radius of cylinders onto the
    two axes perpendicular to the cylinder axis.
    """
    kind = str(prim.get("kind", "box"))
    if kind not in _ALLOWED_KINDS:
        return None

    start = prim.get("start")
    stop = prim.get("stop")

    if kind == "cylinder":
        if not start or not stop:
            return None
        lo = [0.0, 0.0, 0.0]
        hi = [0.0, 0.0, 0.0]
        axis = None
        for i in range(3):
            lo[i], hi[i] = _span(float(start[i]), float(stop[i]))
            if axis is None and hi[i] - lo[i] > 0.0:
                axis = i
        radius = abs(float(prim.get("radius", 0.0)))
        for i in range(3):
            if i != axis:
                lo[i] -= radius
                hi[i] += radius
        return lo, hi

    if kind == "sphere" and (not start or not stop):
        center = prim.get("center")
        if not center or len(center) != 3:
            return None
        radius = abs(float(prim.get("radius", 0.0)))
        lo = [float(center[i]) - radius for i in range(3)]
        hi = [float(center[i]) + radius for i in range(3)]
        return lo, hi

    if not start or not stop:
        return None
    lo = [0.0, 0.0, 0.0]
    hi = [0.0, 0.0, 0.0]
    for i in range(3):
        lo[i], hi[i] = _span(float(start[i]), float(stop[i]))
    return lo, hi


def domain_air_margin_units(mesh: Any, sim: Any, boundary: Any, targets: Any) -> float:
    """FDTD domain air margin (structure → PML) in drawing units.

    Antenna mode owns a symmetric margin driven by ``sim.antenna_pml_lambda``
    (λ at f_start); otherwise the boundary's own air-margin list applies
    ("auto" resolves to λ/4 at f_max).
    """
    from app.meshing.rules import air_margin_units, wavelength_m

    if getattr(sim, "antenna_mode", False):
        f_lo = min(float(mesh.start_frequency or 0.0), float(mesh.stop_frequency or 0.0))
        if f_lo <= 0.0:
            f_lo = targets.f_max_hz
        frac = float(getattr(sim, "antenna_pml_lambda", 0.5) or 0.5)
        return frac * wavelength_m(f_lo, 1.0) / targets.unit_m
    air = air_margin_units(mesh, list(boundary.air_margin), targets)
    vals = [float(a) for a in air if isinstance(a, (int, float))]
    return min(vals) if vals else 0.0


def nf2ff_fit_box(
    lo: list[float], hi: list[float], margin: float
) -> tuple[list[float], list[float]]:
    """NF2FF integration box: an *equal* ``margin`` on all six faces of the
    geometry bbox ``(lo, hi)``.

    The surface must sit at the same stand-off distance on every face: an
    anisotropic box (e.g. a squeezed z margin) sits partly inside the reactive
    near field on the close faces and yields a tilted ("egg"-shaped) far field
    even for a symmetric source.
    """
    start = [float(lo[i]) - margin for i in range(3)]
    stop = [float(hi[i]) + margin for i in range(3)]
    return start, stop


def nf2ff_box_center(start: list[float], stop: list[float]) -> list[float]:
    """Geometric centre of an NF2FF box ``(start, stop)``."""
    return [0.5 * (float(start[i]) + float(stop[i])) for i in range(3)]


def nf2ff_center_offset(
    start: list[float], stop: list[float], center: list[float] | None
) -> float:
    """Max |``center`` − box centre| over the three axes (drawing units).

    openEMS expands the recorded box faces onto a far-field sphere centred at
    ``nf2ff.center``; the box must be centred on that same point, otherwise the
    surface integral is evaluated off-centre and the pattern comes out distorted
    (e.g. a dipole Dmax 2.1 → 3.3 dBi, a 45° instead of 78° beamwidth). This is a
    pure box-geometry check — it needs no knowledge of the geometry (the source
    may be arbitrarily asymmetric).
    """
    c = nf2ff_box_center(start, stop)
    if not center:
        return max(abs(c[i]) for i in range(3))
    return max(abs(float(center[i]) - c[i]) for i in range(3))


def nf2ff_fit_margin_units(mesh: Any, sim: Any, boundary: Any, targets: Any) -> float:
    """Distance from the geometry bbox to the NF2FF box, in drawing units.

    Antenna mode (``sim.antenna_mode``): ``sim.antenna_nf2ff_lambda`` × λ at
    f_start — λ at the LOWEST band frequency so the surface reaches the far
    field for the whole band — clamped to 0.8× the domain air margin so the box
    stays strictly inside the PML.

    Otherwise (PCB / generic runs): the conservative ``0.7 × λ(f_max)/4`` used
    before — there the box is only an energy/field probe, not a far-field
    surface, so a tight box is fine.
    """
    from app.meshing.rules import wavelength_m

    if getattr(sim, "antenna_mode", False):
        f_lo = min(float(mesh.start_frequency or 0.0), float(mesh.stop_frequency or 0.0))
        if f_lo <= 0.0:
            f_lo = targets.f_max_hz
        frac = float(getattr(sim, "antenna_nf2ff_lambda", 0.5) or 0.5)
        margin = frac * wavelength_m(f_lo, 1.0) / targets.unit_m
        air = domain_air_margin_units(mesh, sim, boundary, targets)
        if air > 0.0:
            margin = min(margin, 0.8 * air)
        return margin
    lam = wavelength_m(max(mesh.start_frequency, mesh.stop_frequency), 1.0)
    return 0.7 * (lam / 4.0) / targets.unit_m
