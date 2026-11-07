"""Automatic openEMS port suggestions from a native KiCad board.

KiCad has no simulation-port object, so this module derives *candidates* from
pads, tracks and net names.  Candidates are only suggestions; the GUI shows
them for confirmation before they become ``PortDef`` entries.

Priority order:

1. explicit KiCad marker: footprint property ``openems_port`` (+ optional
   ``openems_port_type``)
2. connector / antenna footprints (SMA, U.FL, BNC, TestPoint, ...)
3. net names (RF_IN, PORT_OUT, ANT, FEED, ...)
4. geometry (track endpoint near Edge.Cuts, feed gap, dangling track)

The generated port boxes follow the GUI's PCB convention: a lumped port spans
from the reference plane to the signal layer and across the feed width at the
track endpoint.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shapely.geometry import Point

from app.freecad_io.kicad_import import (
    KiCadImportOptions,
    _as_float,
    _atom,
    _board_thickness,
    _child,
    _children,
    _copper_layers,
    _load_board,
    _outline_geometry,
    _pad_copper_layers,
    _pad_drill,
    _parse_stackup,
    _rotate,
    _at,
)


_CONNECTOR_RE = re.compile(
    r"(sma|u\.?fl|mmcx|bnc|n[-_ ]?connector|connector|antenna|testpoint|"
    r"probe|edge[-_ ]?launch|coax)",
    re.IGNORECASE,
)
_PORT_NET_RE = re.compile(
    r"^(rf|ant|feed|port|tx|rx|sig|in|out)([-_ ].*)?$",
    re.IGNORECASE,
)
_POWER_NET_RE = re.compile(
    r"^(gnd|agnd|dgnd|earth|vcc|vdd|vss|vbus|vin|vout|"
    r"\+?\d+(\.\d+)?v\d*|\d+v\d*)$",
    re.IGNORECASE,
)


@dataclass
class KiCadPadInfo:
    net: str
    reference: str
    footprint: str
    pad: str
    pad_type: str
    shape: str
    layers: list[str]
    x: float
    y: float
    angle: float
    width: float
    height: float
    drill: float
    explicit_port: str = ""
    explicit_type: str = ""


@dataclass
class KiCadTrackInfo:
    net: str
    layer: str
    start: tuple[float, float]
    end: tuple[float, float]
    width: float
    kind: str = "segment"


@dataclass
class KiCadViaInfo:
    net: str
    x: float
    y: float
    layers: list[str]
    drill: float
    size: float


@dataclass
class KiCadBoardModel:
    source: str = ""
    copper_layers: list[str] = field(default_factory=list)
    z_by_layer: dict[str, float] = field(default_factory=dict)
    board_thickness_mm: float = 1.6
    pads: list[KiCadPadInfo] = field(default_factory=list)
    tracks: list[KiCadTrackInfo] = field(default_factory=list)
    vias: list[KiCadViaInfo] = field(default_factory=list)
    outline: Any = None
    outline_bounds: list[float] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PortCandidate:
    name: str
    net: str
    type: str = "lumped"  # lumped | msl | probe
    start: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    stop: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    direction: str = "z"
    prop_direction: str = ""
    exc_direction: str = ""
    metal: str = ""
    lumped_width: str = "line"
    signal_layer: str = ""
    port_axis: str = ""
    excite: bool = False
    confidence: float = 0.0
    reason: str = ""

    def to_port_def(self, *, resistance: float = 50.0):
        from app.models.project import PortDef

        return PortDef(
            name=self.name,
            type=self.type if self.type in ("lumped", "msl", "probe") else "lumped",
            start=list(self.start),
            stop=list(self.stop),
            resistance=float(resistance),
            direction=self.direction,
            excite_port=bool(self.excite),
            mesh_dirs="xy" if self.type != "probe" else "xyz",
            prop_direction=self.prop_direction,
            exc_direction=self.exc_direction,
            metal=self.metal,
            lumped_width=self.lumped_width,
        )


# --------------------------------------------------------------------------- #
# Board model
# --------------------------------------------------------------------------- #
def _net_map(board) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in _children(board, "net"):
        if len(node) >= 3:
            out[str(node[1])] = str(node[2])
        elif len(node) == 2:
            out[str(node[1])] = ""
    return out


def _net_name(node, nets: dict[str, str]) -> str:
    raw = _atom(node, "net")
    if raw is None:
        return ""
    return nets.get(str(raw), str(raw))


def _property(node, name: str) -> str:
    for prop in _children(node, "property"):
        if len(prop) >= 3 and str(prop[1]) == name:
            return str(prop[2])
    return ""


def _transform_factory(outline, flip_y: bool):
    """Return (outline, transform_point, dx, dy) matching the importer."""
    from shapely import affinity

    if outline is not None:
        if flip_y:
            outline = affinity.scale(outline, yfact=-1.0, origin=(0, 0))
        centre = outline.centroid
        dx, dy = -centre.x, -centre.y
        outline = affinity.translate(outline, dx, dy)
    else:
        dx = dy = 0.0

    def transform_point(x: float, y: float) -> tuple[float, float]:
        return x + dx, (-y if flip_y else y) + dy

    return outline, transform_point, dx, dy


def load_kicad_board_model(
    source: str | Path,
    opts: KiCadImportOptions | None = None,
) -> KiCadBoardModel:
    """Parse pads/tracks/nets and the transformed z/coordinate system."""
    opts = opts or KiCadImportOptions()
    path = Path(source)
    model = KiCadBoardModel(source=str(path))
    try:
        board = _load_board(path)
    except Exception as exc:  # noqa: BLE001
        model.warnings.append(f"KiCad board could not be read: {exc}")
        return model

    copper = _copper_layers(board)
    model.copper_layers = copper
    stackup = _parse_stackup(board)
    model.board_thickness_mm = (
        _board_thickness(board, stackup)
        if opts.use_stackup
        else float(opts.board_thickness_mm)
    )
    if not model.board_thickness_mm:
        model.board_thickness_mm = float(opts.board_thickness_mm or 1.6)

    n = len(copper)
    if n == 1:
        z_by = {copper[0]: 0.0} if copper else {}
    elif n > 1:
        top = model.board_thickness_mm / 2.0
        bottom = -top
        step = (top - bottom) / (n - 1)
        z_by = {name: top - i * step for i, name in enumerate(copper)}
    else:
        z_by = {}
    model.z_by_layer = z_by

    raw_outline = _outline_geometry(board, opts)
    outline, transform_point, _dx, _dy = _transform_factory(raw_outline, opts.flip_y)
    model.outline = outline
    if outline is not None:
        model.outline_bounds = [float(v) for v in outline.bounds]

    nets = _net_map(board)

    # --- pads ----------------------------------------------------------
    for footprint in _children(board, "footprint"):
        fp_name = str(footprint[1]) if len(footprint) > 1 else ""
        reference = _property(footprint, "Reference")
        explicit_port = _property(footprint, "openems_port")
        explicit_type = _property(footprint, "openems_port_type")
        fx, fy, fangle = _at(footprint)
        for pad in _children(footprint, "pad"):
            if len(pad) < 3:
                continue
            px, py, pangle = _at(pad)
            rx, ry = _rotate(px, py, fangle)
            tx, ty = transform_point(fx + rx, fy + ry)
            size = _child(pad, "size")
            width, height = (
                (_as_float(size[1]), _as_float(size[2]))
                if size is not None and len(size) >= 3
                else (1.0, 1.0)
            )
            model.pads.append(
                KiCadPadInfo(
                    net=_net_name(pad, nets),
                    reference=reference,
                    footprint=fp_name,
                    pad=str(pad[1]),
                    pad_type=str(pad[2]),
                    shape=str(pad[3]) if len(pad) > 3 else "rect",
                    layers=_pad_copper_layers(pad, copper),
                    x=float(tx),
                    y=float(ty),
                    angle=float(fangle + pangle),
                    width=abs(float(width)),
                    height=abs(float(height)),
                    drill=_pad_drill(pad),
                    explicit_port=explicit_port,
                    explicit_type=explicit_type,
                )
            )

    # --- tracks --------------------------------------------------------
    for tag in ("segment", "arc"):
        for node in _children(board, tag):
            layer = str(_atom(node, "layer", ""))
            width = _as_float(_atom(node, "width"), 0.0)
            start = _child(node, "start")
            end = _child(node, "end")
            if start is None or end is None or width <= 0:
                continue
            sx, sy = _xy(start)
            ex, ey = _xy(end)
            tsx, tsy = transform_point(sx, sy)
            tex, tey = transform_point(ex, ey)
            model.tracks.append(
                KiCadTrackInfo(
                    net=_net_name(node, nets),
                    layer=layer,
                    start=(float(tsx), float(tsy)),
                    end=(float(tex), float(tey)),
                    width=float(width),
                    kind=tag,
                )
            )

    # --- vias ----------------------------------------------------------
    for node in _children(board, "via"):
        x, y, _angle = _at(node)
        tx, ty = transform_point(x, y)
        layers_node = _child(node, "layers")
        layers = (
            [str(v) for v in layers_node[1:]]
            if layers_node is not None
            else list(copper)
        )
        model.vias.append(
            KiCadViaInfo(
                net=_net_name(node, nets),
                x=float(tx),
                y=float(ty),
                layers=layers,
                drill=_as_float(_atom(node, "drill"), 0.0),
                size=_as_float(_atom(node, "size"), 0.0),
            )
        )

    return model


def _xy(node) -> tuple[float, float]:
    if node is None or len(node) < 3:
        return 0.0, 0.0
    return _as_float(node[1]), _as_float(node[2])


# --------------------------------------------------------------------------- #
# Candidate scoring / geometry
# --------------------------------------------------------------------------- #
def _nearest_track(model: KiCadBoardModel, pad: KiCadPadInfo) -> KiCadTrackInfo | None:
    best = None
    best_d = float("inf")
    for track in model.tracks:
        if track.net != pad.net:
            continue
        d = min(
            math.dist((pad.x, pad.y), track.start),
            math.dist((pad.x, pad.y), track.end),
        )
        if d < best_d:
            best_d = d
            best = track
    return best


def _edge_distance(model: KiCadBoardModel, x: float, y: float) -> float:
    if model.outline is None:
        return float("inf")
    return float(model.outline.exterior.distance(Point(x, y)))


def _score_candidate(
    net: str,
    pads: list[KiCadPadInfo],
    tracks: list[KiCadTrackInfo],
    model: KiCadBoardModel,
) -> tuple[float, str]:
    if not net:
        return 0.0, ""
    upper = net.upper()
    if _POWER_NET_RE.match(upper):
        return 0.0, "power/ground net"

    score = 0.0
    reasons: list[str] = []

    explicit = next(
        (p for p in pads if getattr(p, "explicit_port", "")), None
    )
    if explicit is not None:
        score += 1.0
        reasons.append(f"KiCad marker openems_port={explicit.explicit_port}")

    if _PORT_NET_RE.match(upper):
        score += 0.55
        reasons.append("port-like net name")

    connector = next(
        (p for p in pads if _CONNECTOR_RE.search(p.footprint) or _CONNECTOR_RE.search(p.reference)),
        None,
    )
    if connector is not None:
        score += 0.35
        reasons.append(f"connector footprint {connector.footprint or connector.reference}")

    if len(pads) == 1:
        score += 0.18
        reasons.append("single-pad net")
    elif len(pads) == 2:
        score += 0.12
        reasons.append("two-pad net")
    elif len(pads) > 8:
        score -= 0.35

    if tracks:
        score += 0.10
    if pads and min(_edge_distance(model, p.x, p.y) for p in pads) < 3.0:
        score += 0.08
        reasons.append("near board edge")

    return max(0.0, min(1.0, score)), ", ".join(reasons) or "net topology"


def _signal_axis(track: KiCadTrackInfo | None, pad: KiCadPadInfo) -> tuple[str, float]:
    if track is not None:
        dx = track.end[0] - track.start[0]
        dy = track.end[1] - track.start[1]
        if abs(dx) >= abs(dy):
            return "x", float(track.width)
        return "y", float(track.width)
    if pad.width >= pad.height:
        return "x", float(pad.height)
    return "y", float(pad.width)


def _z_for_layer(model: KiCadBoardModel, layer: str) -> float:
    if layer in model.z_by_layer:
        return float(model.z_by_layer[layer])
    if not model.z_by_layer:
        return 0.0
    return float(next(iter(model.z_by_layer.values())))


def _reference_z(model: KiCadBoardModel, signal_layer: str) -> float:
    if not model.z_by_layer:
        return 0.0
    values = sorted(float(v) for v in model.z_by_layer.values())
    if signal_layer == "F.Cu":
        return values[0]
    if signal_layer == "B.Cu":
        return values[-1]
    z = _z_for_layer(model, signal_layer)
    lower = [v for v in values if v < z]
    upper = [v for v in values if v > z]
    # nearest copper plane; prefer the lower one for a top-side feed
    return max(lower) if lower else (min(upper) if upper else z)


def _make_geometry(
    model: KiCadBoardModel,
    pad: KiCadPadInfo,
    track: KiCadTrackInfo | None,
    *,
    port_type: str,
    name: str,
    net: str,
    excite: bool,
    confidence: float,
    reason: str,
) -> PortCandidate | None:
    layer = track.layer if track is not None else (pad.layers[0] if pad.layers else "")
    if layer not in model.z_by_layer:
        return None
    axis, width = _signal_axis(track, pad)
    width = max(float(width), 0.05)
    z_sig = _z_for_layer(model, layer)
    z_ref = _reference_z(model, layer)
    if abs(z_sig - z_ref) < 1e-9:
        return None

    x, y = pad.x, pad.y
    if track is not None:
        d_start = math.dist((x, y), track.start)
        d_end = math.dist((x, y), track.end)
        x, y = track.start if d_start <= d_end else track.end

    if axis == "x":
        start = [x, y - width / 2.0, z_ref]
        stop = [x, y + width / 2.0, z_sig]
        lumped_width = "y"
        prop_dir = "x"
    else:
        start = [x - width / 2.0, y, z_ref]
        stop = [x + width / 2.0, y, z_sig]
        lumped_width = "x"
        prop_dir = "y"

    if port_type == "probe":
        start = [x, y, z_ref]
        stop = [x, y, z_sig]
        direction = "z"
        lumped_width = "line"
        prop_dir = ""
    else:
        direction = "z"

    return PortCandidate(
        name=name,
        net=net,
        type=port_type,
        start=[float(v) for v in start],
        stop=[float(v) for v in stop],
        direction=direction,
        prop_direction=prop_dir if port_type == "msl" else "",
        exc_direction="z" if port_type == "msl" else "",
        metal=f"cu_{layer.replace('.', '_')}" if port_type == "msl" else "",
        lumped_width=lumped_width,
        signal_layer=layer,
        port_axis=prop_dir,
        excite=excite,
        confidence=float(confidence),
        reason=reason,
    )


def _safe_name(raw: str, fallback: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]+", "_", raw.strip()).strip("_").lower()
    return value or fallback


def suggest_kicad_ports(
    source: str | Path,
    opts: KiCadImportOptions | None = None,
) -> list[PortCandidate]:
    """Return scored port suggestions for a ``.kicad_pcb`` file.

    The caller should present these candidates for confirmation; they are not
    written to the project by this function.
    """
    model = load_kicad_board_model(source, opts)
    if model.warnings and not model.pads:
        return []

    by_net: dict[str, list[KiCadPadInfo]] = {}
    for pad in model.pads:
        if pad.net:
            by_net.setdefault(pad.net, []).append(pad)
    tracks_by_net: dict[str, list[KiCadTrackInfo]] = {}
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

        # Prefer an explicitly marked pad, then a connector pad, then a pad
        # closest to a track endpoint / board edge.
        explicit = next(
        (p for p in pads if getattr(p, "explicit_port", "")), None
    )
        connector = next(
            (p for p in pads if _CONNECTOR_RE.search(p.footprint) or _CONNECTOR_RE.search(p.reference)),
            None,
        )
        pad = explicit or connector or min(
            pads, key=lambda p: _edge_distance(model, p.x, p.y)
        )
        track = _nearest_track(model, pad)

        explicit_name = (pad.explicit_port or "").strip()
        port_type = (pad.explicit_type or "").strip().lower()
        if port_type not in ("lumped", "msl", "probe"):
            port_type = "lumped"
        if explicit_name:
            name = explicit_name
        else:
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
            port_type=port_type,
            name=name,
            net=net,
            excite=False,
            confidence=score,
            reason=reason,
        )
        if candidate is not None:
            candidates.append(candidate)

    # Deduplicate by rounded start/stop and net.
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

    # Exactly one excitation: explicit names containing IN/TX first, else the
    # highest-confidence candidate.
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


def apply_port_candidates(project, candidates: list[PortCandidate]) -> int:
    """Append confirmed candidates as ``PortDef`` entries (dedupe by name)."""
    existing = {str(p.name).lower() for p in project.ports}
    added = 0
    for candidate in candidates:
        if not candidate.name or candidate.name.lower() in existing:
            continue
        project.ports.append(candidate.to_port_def())
        existing.add(candidate.name.lower())
        added += 1
    return added


__all__ = [
    "KiCadBoardModel",
    "KiCadPadInfo",
    "KiCadTrackInfo",
    "KiCadViaInfo",
    "PortCandidate",
    "apply_port_candidates",
    "load_kicad_board_model",
    "suggest_kicad_ports",
]
