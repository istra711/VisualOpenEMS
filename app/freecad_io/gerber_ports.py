"""Automatic openEMS port suggestions from Gerber/Excellon data.

Unlike KiCad, Gerber has no consistent net/pad model.  This module therefore
uses, in order:

1. Gerber X2/X3 object attributes (``.Net``, ``.ComponentRef``, ``.Pin``)
2. an IPC-D-356 netlist next to the Gerber set (via ``LayerStack.netlist``)
3. geometric fallback: pads near the board edge / dangling tracks

The result is always a list of :class:`~app.freecad_io.kicad_ports.PortCandidate`
objects for the shared confirmation dialog.  Without net data the confidence
is deliberately low.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapely.geometry import Point
from shapely.ops import unary_union

from app.freecad_io.gerber_import import (
    Flash,
    GerberImportOptions,
    LayerStack,
    MM,
    _default_layer_z,
    _outline_geometry,
    _primitive_geometry,
    _require_gerbonara,
)
from app.freecad_io.kicad_ports import (
    PortCandidate,
    _edge_distance,
    _make_geometry,
    _nearest_track,
    _safe_name,
    _score_candidate,
    apply_port_candidates,
)


@dataclass
class GerberPadInfo:
    net: str
    reference: str
    footprint: str
    pin: str
    layers: list[str]
    x: float
    y: float
    width: float
    height: float
    object_function: str = ""
    explicit_port: str = ""
    explicit_type: str = ""


@dataclass
class GerberTrackInfo:
    net: str
    layer: str
    start: tuple[float, float]
    end: tuple[float, float]
    width: float


@dataclass
class GerberBoardModel:
    source: str = ""
    copper_layers: list[str] = field(default_factory=list)
    z_by_layer: dict[str, float] = field(default_factory=dict)
    board_thickness_mm: float = 1.6
    pads: list[GerberPadInfo] = field(default_factory=list)
    tracks: list[GerberTrackInfo] = field(default_factory=list)
    outline: Any = None
    outline_bounds: list[float] | None = None
    warnings: list[str] = field(default_factory=list)


def _attr(attrs: dict, *names: str) -> str:
    """Read a Gerber X2 object attribute tolerating key spellings."""
    if not attrs:
        return ""
    wanted = {str(name).lstrip(".").lower() for name in names}
    for key, value in attrs.items():
        if str(key).lstrip(".").lower() not in wanted:
            continue
        if isinstance(value, (tuple, list)):
            return str(value[0]) if value else ""
        return str(value)
    return ""


def _to_mm(value, unit) -> float:
    try:
        if unit is not None:
            return float(MM.convert_from(unit, value))
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def load_gerber_port_model(
    source: str | Path,
    opts: GerberImportOptions | None = None,
) -> GerberBoardModel:
    _require_gerbonara()
    opts = opts or GerberImportOptions()
    path = Path(source)
    model = GerberBoardModel(
        source=str(path), board_thickness_mm=float(opts.board_thickness_mm)
    )
    try:
        stack = LayerStack.open(source)
    except Exception as exc:  # noqa: BLE001
        model.warnings.append(f"Gerber could not be read: {exc}")
        return model

    copper = stack.copper_layers
    model.copper_layers = [side for (side, _use), _layer in copper]
    z_by = _default_layer_z(model.copper_layers, opts.board_thickness_mm)
    for side, z in (opts.layer_z or {}).items():
        if side in z_by:
            z_by[side] = float(z)
    model.z_by_layer = z_by

    outline = _outline_geometry(stack, opts)
    if outline is not None:
        centre = outline.centroid
        dx, dy = -centre.x, -centre.y
        from shapely import affinity

        outline = affinity.translate(outline, dx, dy)
        model.outline = outline
        model.outline_bounds = [float(v) for v in outline.bounds]
    else:
        dx = dy = 0.0

    def transform(x: float, y: float) -> tuple[float, float]:
        return x + dx, y + dy

    # IPC-D-356 fallback: map rounded pad coordinates to net/ref/pin.
    netlist_at: dict[tuple[float, float], tuple[str, str, str]] = {}
    netlist = getattr(stack, "netlist", None)
    if netlist is not None:
        for record in getattr(netlist, "test_records", []) or []:
            if record.x is None or record.y is None:
                continue
            x = _to_mm(record.x, getattr(record, "unit", None))
            y = _to_mm(record.y, getattr(record, "unit", None))
            netlist_at[(round(x, 2), round(y, 2))] = (
                str(record.net_name or ""),
                str(record.ref_des or ""),
                str(getattr(record, "pin_num", None) or getattr(record, "pin", "") or ""),
            )

    for (side, _use), layer in copper:
        for obj in getattr(layer, "objects", []):
            attrs = getattr(obj, "attrs", {}) or {}
            net = _attr(attrs, ".Net", "Net", ".N", "N")
            reference = _attr(
                attrs, ".ComponentRef", "ComponentRef", ".C", "C"
            )
            pin = _attr(attrs, ".Pin", "Pin", ".P", "P")
            obj_function = _attr(
                attrs, ".ObjectFunction", "ObjectFunction", ".O", "O"
            )

            if isinstance(obj, Flash):
                raw_x, raw_y = float(obj.x), float(obj.y)
                key = (round(raw_x, 2), round(raw_y, 2))
                if key in netlist_at:
                    n_net, n_ref, n_pin = netlist_at[key]
                    net = net or n_net
                    reference = reference or n_ref
                    pin = pin or n_pin

                geoms = []
                for prim in obj.to_primitives(unit=MM):
                    geom = _primitive_geometry(prim, opts.arc_error_mm)
                    if geom is not None and not geom.is_empty:
                        geoms.append(geom)
                if not geoms:
                    continue
                geom = unary_union(geoms)
                minx, miny, maxx, maxy = geom.bounds
                x, y = transform(raw_x, raw_y)
                model.pads.append(
                    GerberPadInfo(
                        net=net,
                        reference=reference,
                        footprint=reference,
                        pin=pin,
                        layers=[side],
                        x=float(x),
                        y=float(y),
                        width=float(maxx - minx),
                        height=float(maxy - miny),
                        object_function=obj_function,
                    )
                )
                continue

            # Lines and arcs are tracks; regions are copper areas (ignored for
            # the pad/track graph, but they still show up in the imported copper).
            if not hasattr(obj, "x1") or not hasattr(obj, "x2"):
                continue
            aperture = getattr(obj, "aperture", None)
            width = (
                float(aperture.equivalent_width(MM))
                if aperture is not None
                else 0.0
            )
            if width <= 0:
                continue
            sx, sy = float(obj.x1), float(obj.y1)
            ex, ey = float(obj.x2), float(obj.y2)
            tsx, tsy = transform(sx, sy)
            tex, tey = transform(ex, ey)
            model.tracks.append(
                GerberTrackInfo(
                    net=net,
                    layer=side,
                    start=(tsx, tsy),
                    end=(tex, tey),
                    width=width,
                )
            )

    return model


def _fallback_edge_candidates(model: GerberBoardModel) -> list[PortCandidate]:
    """No nets at all: suggest pads near the board edge with an attached track."""
    candidates: list[PortCandidate] = []
    for pad in model.pads:
        if _edge_distance(model, pad.x, pad.y) > 3.0:
            continue
        track = _nearest_track(model, pad)
        name = _safe_name(
            f"{pad.reference}_{pad.pin}" if pad.reference else "",
            f"port{len(candidates) + 1}",
        )
        candidate = _make_geometry(
            model,
            pad,
            track,
            port_type="lumped",
            name=name,
            net=pad.net,
            excite=False,
            confidence=0.30,
            reason="pad near board edge; no net data",
        )
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def suggest_gerber_ports(
    source: str | Path,
    opts: GerberImportOptions | None = None,
) -> list[PortCandidate]:
    """Return scored port suggestions for a Gerber/Excellon set."""
    model = load_gerber_port_model(source, opts)
    if model.warnings and not model.pads:
        return []

    by_net: dict[str, list[GerberPadInfo]] = {}
    for pad in model.pads:
        if pad.net:
            by_net.setdefault(pad.net, []).append(pad)
    tracks_by_net: dict[str, list[GerberTrackInfo]] = {}
    for track in model.tracks:
        if track.net:
            tracks_by_net.setdefault(track.net, []).append(track)

    candidates: list[PortCandidate] = []
    used_in = 0
    used_out = 0

    for net, pads in by_net.items():
        tracks = tracks_by_net.get(net, [])
        score, reason = _score_candidate(net, pads, tracks, model)
        if score < 0.25:
            continue
        pad = min(pads, key=lambda p: _edge_distance(model, p.x, p.y))
        track = _nearest_track(model, pad)

        upper = net.upper()
        if any(token in upper for token in ("IN", "TX", "FEED")):
            used_in += 1
            name = f"in{used_in}"
        elif any(token in upper for token in ("OUT", "RX", "ANT")):
            used_out += 1
            name = f"out{used_out}"
        else:
            name = _safe_name(net, f"port{len(candidates) + 1}")

        candidate = _make_geometry(
            model,
            pad,
            track,
            port_type="lumped",
            name=name,
            net=net,
            excite=False,
            confidence=score,
            reason=reason,
        )
        if candidate is not None:
            candidates.append(candidate)

    if not candidates:
        candidates = _fallback_edge_candidates(model)

    # Deduplicate by net/position.
    unique: dict[tuple, PortCandidate] = {}
    for candidate in candidates:
        key = (
            candidate.net,
            tuple(round(v, 3) for v in candidate.start),
            tuple(round(v, 3) for v in candidate.stop),
        )
        if key not in unique or candidate.confidence > unique[key].confidence:
            unique[key] = candidate
    candidates = sorted(unique.values(), key=lambda c: (-c.confidence, c.name))

    if candidates:
        excitation = next(
            (
                c
                for c in candidates
                if any(token in c.name.upper() for token in ("IN", "TX", "FEED"))
            ),
            candidates[0],
        )
        for candidate in candidates:
            candidate.excite = candidate is excitation
    return candidates


__all__ = [
    "GerberBoardModel",
    "GerberPadInfo",
    "GerberTrackInfo",
    "apply_port_candidates",
    "load_gerber_port_model",
    "suggest_gerber_ports",
]
