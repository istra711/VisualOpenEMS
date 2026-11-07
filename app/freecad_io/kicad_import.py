"""Native KiCad ``.kicad_pcb`` -> CSXCAD primitives (phase 4).

Unlike the Gerber import, a KiCad board file contains the real layer stackup,
via spans, nets, tracks, pads and filled zones.  This module parses the
S-expression format directly (no KiCad installation required), unions the
copper geometry per layer and emits the same ``Project.primitives`` dicts as
the Gerber importer.

Supported KiCad 6/7/8 constructs:

* copper layers + custom ``(stackup ...)``
* ``segment`` / ``arc`` tracks
* SMD, through-hole and NPTH pads (rect/roundrect/oval/circle/custom)
* ``via`` with exact ``(layers ...)`` span
* filled ``zone`` polygons (unfilled outlines optional)
* board outline from ``Edge.Cuts`` graphic items

Unsupported/approximated: footprint-embedded Edge.Cuts cutouts, 3-D models,
text, non-copper graphics.  They are ignored or reported as warnings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import polygonize, triangulate, unary_union

from app.csxcad_builder.triangle_mesh import write_binary_stl
from app.freecad_io.gerber_import import _iter_polygons, _polygon_primitives


# --------------------------------------------------------------------------- #
# Settings / results
# --------------------------------------------------------------------------- #
@dataclass
class KiCadImportOptions:
    use_stackup: bool = True
    board_thickness_mm: float = 1.6
    copper_thickness_mm: float = 0.035
    copper_as_sheet: bool = True
    #: "" = derive from copper_as_sheet; "sheet" | "prism" | "mesh"
    copper_mode: str = ""
    #: output directory for "mesh" mode; empty = next to the source board
    mesh_dir: str = ""
    substrate_material: str = "kicad_substrate"
    substrate_epsilon: float = 4.4
    substrate_tand: float = 0.02
    via_plating_radius_mm: float = 0.025
    include_npth: bool = False
    include_zones: bool = True
    include_unfilled_zones: bool = False
    include_pad_barrels: bool = True
    include_tracks: bool = True
    include_smd_pads: bool = True
    include_vias: bool = True
    flip_y: bool = True
    arc_error_mm: float = 0.01
    simplify_mm: float = 0.0
    min_area_mm2: float = 0.005
    clip_to_outline: bool = True
    create_substrate: bool = True


@dataclass
class KiCadStackupLayer:
    name: str
    kind: str = "other"  # copper | dielectric | mask | silk | paste | other
    thickness_mm: float = 0.0
    epsilon: float = 1.0
    tand: float = 0.0
    material: str = ""


@dataclass
class KiCadInspection:
    board_name: str = ""
    copper_layers: list[str] = field(default_factory=list)
    stackup: list[KiCadStackupLayer] = field(default_factory=list)
    board_thickness_mm: float = 0.0
    via_count: int = 0
    pad_count: int = 0
    track_count: int = 0
    zone_count: int = 0
    outline_bounds_mm: list[float] | None = None
    warnings: list[str] = field(default_factory=list)
    ok: bool = False


@dataclass
class KiCadImportResult:
    primitives: list[dict[str, Any]] = field(default_factory=list)
    layers: list[dict[str, Any]] = field(default_factory=list)
    materials: list[dict[str, Any]] = field(default_factory=list)
    stackup: list[KiCadStackupLayer] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    board_name: str = ""
    via_count: int = 0
    pad_count: int = 0
    track_count: int = 0
    zone_count: int = 0
    pad_barrel_count: int = 0
    ok: bool = False


# --------------------------------------------------------------------------- #
# Minimal S-expression reader
# --------------------------------------------------------------------------- #
def _tokenize(text: str):
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    esc = text[i + 1]
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(esc, esc))
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    buf.append(text[i])
                    i += 1
            yield ("atom", "".join(buf))
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '()"':
                i += 1
            yield ("atom", text[start:i])


def _parse_sexpr(text: str):
    stack: list[list] = [[]]
    for token in _tokenize(text):
        if token == "(":
            node: list = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ")":
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(token[1])
    while len(stack) > 1:
        stack.pop()
    return stack[0][0] if stack[0] else []


def _child(node, name: str):
    if not isinstance(node, list):
        return None
    for item in node[1:]:
        if isinstance(item, list) and item and str(item[0]) == name:
            return item
    return None


def _children(node, name: str):
    if not isinstance(node, list):
        return []
    return [
        item
        for item in node[1:]
        if isinstance(item, list) and item and str(item[0]) == name
    ]


def _atom(node, name: str, default=None):
    child = _child(node, name)
    if child is not None and len(child) >= 2 and not isinstance(child[1], list):
        return child[1]
    return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _xy(node) -> tuple[float, float]:
    if node is None or len(node) < 3:
        return 0.0, 0.0
    return _as_float(node[1]), _as_float(node[2])


def _at(node) -> tuple[float, float, float]:
    at = _child(node, "at")
    if at is None:
        return 0.0, 0.0, 0.0
    x = _as_float(at[1]) if len(at) > 1 else 0.0
    y = _as_float(at[2]) if len(at) > 2 else 0.0
    angle = _as_float(at[3]) if len(at) > 3 else 0.0
    return x, y, angle


def _rotate(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    if abs(angle_deg) < 1e-12:
        return x, y
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return x * ca - y * sa, x * sa + y * ca


def _load_board(path: Path) -> list:
    text = path.read_text(encoding="utf-8", errors="replace")
    root = _parse_sexpr(text)
    if isinstance(root, list) and root and str(root[0]) == "kicad_pcb":
        return root
    if isinstance(root, list):
        for item in root:
            if isinstance(item, list) and item and str(item[0]) == "kicad_pcb":
                return item
    raise ValueError("No (kicad_pcb ...) root found")


# --------------------------------------------------------------------------- #
# Board metadata
# --------------------------------------------------------------------------- #
def _copper_layers(board) -> list[str]:
    layers = _child(board, "layers")
    names: list[str] = []
    if layers is not None:
        for entry in layers[1:]:
            if (
                isinstance(entry, list)
                and len(entry) >= 3
                and str(entry[1]).endswith(".Cu")
            ):
                names.append(str(entry[1]))
    if not names:
        # Fallback: collect copper names from any layer-bearing node.
        found: set[str] = set()
        for tag in ("segment", "arc", "via", "zone", "filled_polygon", "pad"):
            for node in _children(board, tag):
                layer = _atom(node, "layer")
                if layer and str(layer).endswith(".Cu"):
                    found.add(str(layer))
                layers_node = _child(node, "layers")
                if layers_node:
                    for value in layers_node[1:]:
                        if str(value).endswith(".Cu") and str(value) != "*.Cu":
                            found.add(str(value))
        names = sorted(found, key=_layer_sort_key)
    return names


def _layer_sort_key(name: str):
    if name == "F.Cu":
        return (0, 0)
    if name == "B.Cu":
        return (2, 0)
    if name.startswith("In") and name.endswith(".Cu"):
        try:
            return (1, int(name[2:-3]))
        except ValueError:
            return (1, 0)
    return (3, name)


def _parse_stackup(board) -> list[KiCadStackupLayer]:
    setup = _child(board, "setup")
    stackup = _child(setup, "stackup") if setup is not None else None
    if stackup is None:
        return []

    entries: list[KiCadStackupLayer] = []
    for layer in _children(stackup, "layer"):
        if len(layer) < 2:
            continue
        name = str(layer[1])
        typ = str(_atom(layer, "type", "") or "")
        thickness = _as_float(_atom(layer, "thickness"), 0.0)
        eps = _as_float(_atom(layer, "epsilon_r"), 1.0)
        tand = _as_float(_atom(layer, "loss_tangent"), 0.0)
        material = str(_atom(layer, "material", "") or "")
        low = typ.lower()
        if name.endswith(".Cu") or low == "copper":
            kind = "copper"
        elif "dielectric" in name.lower() or low in ("core", "prepreg"):
            kind = "dielectric"
        elif "mask" in low:
            kind = "mask"
        elif "silk" in low:
            kind = "silk"
        elif "paste" in low:
            kind = "paste"
        else:
            kind = "other"
        entries.append(
            KiCadStackupLayer(
                name=name,
                kind=kind,
                thickness_mm=thickness,
                epsilon=eps,
                tand=tand,
                material=material,
            )
        )
    return entries


def _board_thickness(board, stackup: list[KiCadStackupLayer]) -> float:
    general = _child(board, "general")
    value = _as_float(_atom(general, "thickness"), 0.0) if general is not None else 0.0
    if value > 0:
        return value
    total = sum(
        layer.thickness_mm
        for layer in stackup
        if layer.kind in ("copper", "dielectric", "mask", "silk", "paste")
    )
    return total if total > 0 else 1.6


def _copper_thicknesses(
    stackup: list[KiCadStackupLayer], fallback: float
) -> dict[str, float]:
    out = {
        layer.name: layer.thickness_mm
        for layer in stackup
        if layer.kind == "copper" and layer.thickness_mm > 0
    }
    return out or {}


# --------------------------------------------------------------------------- #
# Outline / arc helpers
# --------------------------------------------------------------------------- #
def _arc_points(
    start: tuple[float, float],
    mid: tuple[float, float],
    end: tuple[float, float],
    max_error: float,
) -> list[tuple[float, float]]:
    ax, ay = start
    bx, by = mid
    cx, cy = end
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [start, mid, end]
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    r = math.dist((ax, ay), (ux, uy))
    if r <= 0:
        return [start, mid, end]

    a0 = math.atan2(ay - uy, ax - ux)
    a1 = math.atan2(by - uy, bx - ux)
    a2 = math.atan2(cy - uy, cx - ux)

    def norm(a: float) -> float:
        while a < 0:
            a += 2 * math.pi
        while a >= 2 * math.pi:
            a -= 2 * math.pi
        return a

    ccw = norm(a2 - a0)
    if norm(a1 - a0) <= ccw + 1e-12:
        sweep, sign = ccw, 1.0
    else:
        sweep, sign = norm(a0 - a2), -1.0
    if sweep < 1e-9:
        sweep = 2 * math.pi
    step = 2 * math.acos(max(-1.0, min(1.0, 1.0 - max_error / max(r, 1e-9))))
    steps = max(6, min(720, int(math.ceil(sweep / max(step, 1e-6)))))
    return [
        (
            ux + r * math.cos(a0 + sign * sweep * i / steps),
            uy + r * math.sin(a0 + sign * sweep * i / steps),
        )
        for i in range(steps + 1)
    ]


def _graphic_geometry(node, opts: KiCadImportOptions):
    """One gr_* / fp_* primitive as shapely geometry (centerline/shape)."""
    tag = str(node[0])
    if tag.endswith("circle"):
        center_node = _child(node, "center")
        end_node = _child(node, "end")
        if center_node is None or end_node is None:
            return None
        center = _xy(center_node)
        radius = math.dist(center, _xy(end_node))
        return Point(center).buffer(radius, quad_segs=32)

    if tag.endswith("poly"):
        pts_node = _child(node, "pts")
        if pts_node is None:
            return None
        points = [_xy(p) for p in _children(pts_node, "xy")]
        return Polygon(points) if len(points) >= 3 else None

    start_node = _child(node, "start")
    end_node = _child(node, "end")
    if start_node is None or end_node is None:
        return None
    start = _xy(start_node)
    end = _xy(end_node)

    if tag.endswith("line"):
        return LineString([start, end])
    if tag.endswith("rect"):
        return box(min(start[0], end[0]), min(start[1], end[1]),
                   max(start[0], end[0]), max(start[1], end[1]))
    if tag.endswith("arc"):
        mid_node = _child(node, "mid")
        mid = _xy(mid_node) if mid_node is not None else (
            (start[0] + end[0]) / 2.0,
            (start[1] + end[1]) / 2.0,
        )
        pts = _arc_points(start, mid, end, opts.arc_error_mm)
        return LineString(pts)
    return None


def _outline_geometry(board, opts: KiCadImportOptions):
    direct: list[Polygon] = []
    linework: list[LineString] = []
    for tag in ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly"):
        for node in _children(board, tag):
            if str(_atom(node, "layer", "")) != "Edge.Cuts":
                continue
            geom = _graphic_geometry(node, opts)
            if geom is None:
                continue
            if isinstance(geom, Polygon):
                direct.append(geom)
            elif isinstance(geom, LineString):
                linework.append(geom)

    polygons = list(direct)
    if linework:
        try:
            polygons.extend(polygonize(unary_union(linework)))
        except Exception:  # noqa: BLE001 - malformed outline is best effort
            pass
    if not polygons:
        return None

    merged = unary_union(polygons)
    polys = sorted(_iter_polygons(merged), key=lambda p: p.area, reverse=True)
    if not polys:
        return None
    outer = polys[0]
    holes = [p for p in polys[1:] if outer.contains(p)]
    return outer.difference(unary_union(holes)) if holes else outer


def _cap_triangles(poly: Polygon) -> list[Polygon]:
    if poly.is_empty:
        return []
    if not poly.is_valid:
        poly = poly.buffer(0)
    out = []
    for tri in triangulate(poly):
        if poly.contains(tri.representative_point()):
            out.append(tri)
    if not out:
        # Degenerate / self-intersecting input: keep the exterior as a fan.
        pts = list(poly.exterior.coords)[:-1]
        for i in range(1, len(pts) - 1):
            out.append(Polygon([pts[0], pts[i], pts[i + 1]]))
    return out


def _prism_triangles(geom, z0: float, z1: float) -> np.ndarray:
    """Closed triangle soup for a 2-D copper layer extruded between z0..z1."""
    tris: list[list[tuple[float, float, float]]] = []
    for poly in _iter_polygons(geom):
        caps = _cap_triangles(poly)
        for tri in caps:
            pts = list(tri.exterior.coords)[:3]
            top = [(x, y, z1) for x, y in pts]
            bot = [(x, y, z0) for x, y in pts]
            tris.append([top[0], top[1], top[2]])
            tris.append([bot[0], bot[2], bot[1]])

        rings = [poly.exterior, *poly.interiors]
        for ring in rings:
            coords = list(ring.coords)
            for i in range(len(coords) - 1):
                x1, y1 = coords[i]
                x2, y2 = coords[i + 1]
                p0 = (float(x1), float(y1), z0)
                p1 = (float(x1), float(y1), z1)
                q0 = (float(x2), float(y2), z0)
                q1 = (float(x2), float(y2), z1)
                tris.append([p0, q0, q1])
                tris.append([p0, q1, p1])
    return np.asarray(tris, dtype=float)


def _copper_mesh_primitives(
    prefix: str,
    geom,
    z0: float,
    z1: float,
    material: str,
    priority: int,
    mesh_dir: Path,
) -> list[dict[str, Any]]:
    """One Polyhedron/STL primitive for a complete copper layer."""
    triangles = _prism_triangles(geom, z0, z1)
    if len(triangles) == 0:
        return []
    mesh_dir.mkdir(parents=True, exist_ok=True)
    npy_path = mesh_dir / f"{prefix}.npy"
    stl_path = mesh_dir / f"{prefix}.stl"
    np.save(str(npy_path), triangles)
    write_binary_stl(stl_path, triangles)
    minx, miny, maxx, maxy = geom.bounds
    return [
        {
            "kind": "stl",
            "name": prefix,
            "material": material,
            "priority": priority,
            "path": str(npy_path),
            "mesh_path": str(npy_path),
            "stl_path": str(stl_path),
            "start": [minx, miny, z0],
            "stop": [maxx, maxy, z1],
            "source_layer": prefix,
            "mesh_triangles": int(len(triangles)),
        }
    ]


# --------------------------------------------------------------------------- #
# Pads, tracks, zones, vias
# --------------------------------------------------------------------------- #
def _roundrect(w: float, h: float, radius: float) -> Polygon:
    r = max(0.0, min(radius, min(w, h) / 2.0 - 1e-9))
    if r <= 0:
        return box(-w / 2, -h / 2, w / 2, h / 2)
    core = box(-w / 2 + r, -h / 2 + r, w / 2 - r, h / 2 - r)
    return core.buffer(r, quad_segs=16, join_style=1)


def _pad_local_geometry(pad, opts: KiCadImportOptions):
    shape = str(pad[3]) if len(pad) > 3 else "rect"
    size = _child(pad, "size")
    w, h = _xy(size) if size is not None else (1.0, 1.0)
    w, h = abs(w), abs(h)

    if shape == "circle":
        return Point(0, 0).buffer(w / 2.0, quad_segs=24)
    if shape == "oval":
        if w >= h:
            r = h / 2.0
            return LineString([(-(w / 2 - r), 0), (w / 2 - r, 0)]).buffer(
                r, cap_style=1, join_style=1
            )
        r = w / 2.0
        return LineString([(0, -(h / 2 - r)), (0, h / 2 - r)]).buffer(
            r, cap_style=1, join_style=1
        )
    if shape == "roundrect":
        ratio = _as_float(_atom(pad, "roundrect_rratio"), 0.25)
        return _roundrect(w, h, min(w, h) * ratio)
    if shape == "trapezoid":
        # Approximate; KiCad's rect_delta semantics are not worth guessing.
        return box(-w / 2, -h / 2, w / 2, h / 2)
    if shape == "custom":
        prims = _child(pad, "primitives")
        if prims is not None:
            geoms = []
            for node in prims[1:]:
                if not isinstance(node, list):
                    continue
                geom = _graphic_geometry(node, opts)
                if geom is not None:
                    if isinstance(geom, LineString):
                        width = _as_float(_atom(node, "width"), 0.1)
                        geom = geom.buffer(width / 2.0, cap_style=1, join_style=1)
                    geoms.append(geom)
            if geoms:
                return unary_union(geoms)
        return box(-w / 2, -h / 2, w / 2, h / 2)
    return box(-w / 2, -h / 2, w / 2, h / 2)


def _pad_global_geometry(pad, fp_at, opts: KiCadImportOptions):
    px, py, pangle = _at(pad)
    fx, fy, fangle = fp_at
    gx, gy = _rotate(px, py, fangle)
    gx += fx
    gy += fy
    angle = fangle + pangle
    geom = _pad_local_geometry(pad, opts)
    if abs(angle) > 1e-12:
        geom = affinity.rotate(geom, angle, origin=(0, 0), use_radians=False)
    return affinity.translate(geom, gx, gy)


def _pad_copper_layers(pad, copper_names: list[str]) -> list[str]:
    layers = _child(pad, "layers")
    if layers is None:
        return []
    values = [str(v) for v in layers[1:]]
    if "*.Cu" in values:
        return list(copper_names)
    return [name for name in copper_names if name in values]


def _pad_drill(pad) -> float:
    drill = _child(pad, "drill")
    if drill is None:
        return 0.0
    values = [
        _as_float(v, 0.0)
        for v in drill[1:]
        if not isinstance(v, list) and str(v) not in ("oval", "circle")
    ]
    return max(values) if values else 0.0


def _parse_pads(board, copper_names, opts, warnings) -> tuple[dict, list]:
    """Return per-layer pad polygons and PTH/NPTH barrel descriptors."""
    per_layer: dict[str, list] = {name: [] for name in copper_names}
    barrels: list[dict[str, Any]] = []
    pad_count = 0
    for footprint in _children(board, "footprint"):
        fp_at = _at(footprint)
        for pad in _children(footprint, "pad"):
            pad_count += 1
            pad_type = str(pad[2]) if len(pad) > 2 else "smd"
            layers = _pad_copper_layers(pad, copper_names)
            drill = _pad_drill(pad)
            if pad_type == "np_thru_hole":
                if opts.include_npth and drill > 0:
                    barrels.append(
                        {"kind": "npth", "at": _pad_global_geometry(pad, fp_at, opts),
                         "x": 0.0, "y": 0.0, "drill": drill}
                    )
                continue
            if pad_type in ("thru_hole", "smd", "connect"):
                if pad_type == "smd" and not opts.include_smd_pads:
                    continue
                geom = _pad_global_geometry(pad, fp_at, opts)
                for layer in layers:
                    per_layer[layer].append(geom)
                if pad_type == "thru_hole" and drill > 0:
                    # Barrel position is the pad origin (global).
                    px, py, pangle = _at(pad)
                    fx, fy, fangle = fp_at
                    bx, by = _rotate(px, py, fangle)
                    barrels.append(
                        {"kind": "pth", "x": fx + bx, "y": fy + by,
                         "drill": drill, "layers": layers}
                    )
    return per_layer, barrels


def _parse_tracks(board, copper_names, opts) -> dict:
    per_layer: dict[str, list] = {name: [] for name in copper_names}
    if not opts.include_tracks:
        return per_layer
    for tag in ("segment", "arc"):
        for node in _children(board, tag):
            layer = str(_atom(node, "layer", ""))
            if layer not in per_layer:
                continue
            width = _as_float(_atom(node, "width"), 0.0)
            if width <= 0:
                continue
            start = _xy(_child(node, "start"))
            end = _xy(_child(node, "end"))
            if tag == "arc":
                mid = _xy(_child(node, "mid"))
                pts = _arc_points(start, mid, end, opts.arc_error_mm)
                centerline = LineString(pts)
            else:
                centerline = LineString([start, end])
            per_layer[layer].append(
                centerline.buffer(width / 2.0, cap_style=1, join_style=1)
            )
    return per_layer


def _parse_zones(board, copper_names, opts, warnings) -> tuple[dict, int]:
    per_layer: dict[str, list] = {name: [] for name in copper_names}
    if not opts.include_zones:
        return per_layer, 0

    zone_count = 0
    for zone in _children(board, "zone"):
        zone_count += 1
        filled = _children(zone, "filled_polygon")
        if not filled:
            if opts.include_unfilled_zones:
                outlines = _children(zone, "polygon")
                layer_names = _zone_layers(zone, copper_names)
                for outline in outlines:
                    pts_node = _child(outline, "pts")
                    if pts_node is None:
                        continue
                    points = [_xy(p) for p in _children(pts_node, "xy")]
                    if len(points) >= 3:
                        poly = Polygon(points)
                        if not poly.is_valid:
                            poly = poly.buffer(0)
                        for layer in layer_names:
                            per_layer[layer].append(poly)
                warnings.append(
                    f"Zone without filled polygon imported as outline: "
                    f"{str(_atom(zone, 'net_name', '?'))}"
                )
            else:
                warnings.append(
                    "Zone without filled polygon skipped (enable "
                    "'unfilled zones' to import its outline)."
                )
            continue

        for filled_polygon in filled:
            layer = str(_atom(filled_polygon, "layer", ""))
            if layer not in per_layer:
                continue
            pts_node = _child(filled_polygon, "pts")
            if pts_node is None:
                continue
            points = [_xy(p) for p in _children(pts_node, "xy")]
            if len(points) < 3:
                continue
            poly = Polygon(points)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if not poly.is_empty:
                per_layer[layer].append(poly)
    return per_layer, zone_count


def _zone_layers(zone, copper_names) -> list[str]:
    layer = _atom(zone, "layer")
    if layer:
        return [str(layer)] if str(layer) in copper_names else []
    layers = _child(zone, "layers")
    if layers:
        return [
            str(v) for v in layers[1:] if str(v) in copper_names
        ]
    return list(copper_names)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def inspect_kicad_pcb(path: str | Path) -> KiCadInspection:
    path = Path(path)
    result = KiCadInspection(board_name=path.stem)
    try:
        board = _load_board(path)
    except Exception as exc:  # noqa: BLE001 - report to GUI
        result.warnings.append(f"KiCad board could not be read: {exc}")
        return result

    result.copper_layers = _copper_layers(board)
    result.stackup = _parse_stackup(board)
    result.board_thickness_mm = _board_thickness(board, result.stackup)
    result.via_count = len(_children(board, "via"))
    result.track_count = len(_children(board, "segment")) + len(_children(board, "arc"))
    result.zone_count = len(_children(board, "zone"))
    result.pad_count = sum(
        len(_children(fp, "pad")) for fp in _children(board, "footprint")
    )

    outline = _outline_geometry(board, KiCadImportOptions())
    if outline is not None:
        result.outline_bounds_mm = [float(v) for v in outline.bounds]
    else:
        result.warnings.append("No closed Edge.Cuts outline found.")
    if not result.copper_layers:
        result.warnings.append("No copper layers found.")
    result.ok = True
    return result


def import_kicad_pcb(
    source: str | Path,
    opts: KiCadImportOptions | None = None,
    mesh_dir: str | Path | None = None,
) -> KiCadImportResult:
    """Parse a ``.kicad_pcb`` and return CSXCAD primitive dictionaries."""
    opts = opts or KiCadImportOptions()
    source = Path(source)
    result = KiCadImportResult(board_name=source.stem)
    if not source.exists():
        result.warnings.append(f"File not found: {source}")
        return result
    try:
        board = _load_board(source)
    except Exception as exc:  # noqa: BLE001
        result.warnings.append(f"KiCad board could not be read: {exc}")
        return result

    copper_names = _copper_layers(board)
    if not copper_names:
        result.warnings.append("No copper layers found.")
        return result

    stackup = _parse_stackup(board)
    result.stackup = stackup
    board_thickness = (
        _board_thickness(board, stackup)
        if opts.use_stackup
        else float(opts.board_thickness_mm)
    )
    if not (board_thickness > 0):
        board_thickness = float(opts.board_thickness_mm or 1.6)

    outline = _outline_geometry(board, opts)
    if outline is None:
        result.warnings.append(
            "No closed Edge.Cuts outline; substrate/centre fall back to the "
            "copper bounding box."
        )

    warnings: list[str] = []
    pad_geoms, barrels = _parse_pads(board, copper_names, opts, warnings)
    track_geoms = _parse_tracks(board, copper_names, opts)
    zone_geoms, zone_count = _parse_zones(board, copper_names, opts, warnings)
    result.pad_count = sum(
        len(_children(fp, "pad")) for fp in _children(board, "footprint")
    )
    result.track_count = len(_children(board, "segment")) + len(_children(board, "arc"))
    result.zone_count = zone_count

    # Union all copper for each layer.
    raw_copper: dict[str, Any] = {}
    for layer in copper_names:
        geoms = pad_geoms[layer] + track_geoms[layer] + zone_geoms[layer]
        if not geoms:
            continue
        geom = unary_union(geoms)
        if opts.clip_to_outline and outline is not None:
            geom = geom.intersection(outline)
        if opts.simplify_mm > 0:
            geom = geom.simplify(opts.simplify_mm, preserve_topology=True)
        if geom.is_empty or geom.area < opts.min_area_mm2:
            continue
        raw_copper[layer] = geom

    content = unary_union(list(raw_copper.values())) if raw_copper else None
    if outline is None and content is not None:
        minx, miny, maxx, maxy = content.bounds
        outline = box(minx, miny, maxx, maxy)
    if outline is None:
        result.warnings.append("No board outline or copper geometry available.")
        return result

    # Flip Y (KiCad file coordinates are y-down) and centre the board.
    if opts.flip_y:
        outline = affinity.scale(outline, yfact=-1.0, origin=(0, 0))
        raw_copper = {
            layer: affinity.scale(geom, yfact=-1.0, origin=(0, 0))
            for layer, geom in raw_copper.items()
        }
    centre = outline.centroid
    dx, dy = -centre.x, -centre.y
    outline = affinity.translate(outline, dx, dy)
    raw_copper = {
        layer: affinity.translate(geom, dx, dy)
        for layer, geom in raw_copper.items()
    }

    # z planes: evenly distribute copper through the board, consistent with
    # the Gerber importer.  Stackup material data is mapped to the gaps.
    n = len(copper_names)
    if n == 1:
        z_by_layer = {copper_names[0]: 0.0}
    else:
        top, bottom = board_thickness / 2.0, -board_thickness / 2.0
        step = (top - bottom) / (n - 1)
        z_by_layer = {name: top - i * step for i, name in enumerate(copper_names)}

    # --- substrate / dielectric stackup --------------------------------
    dielectric_layers = (
        [entry for entry in stackup if entry.kind == "dielectric"]
        if opts.use_stackup
        else []
    )
    if stackup and opts.use_stackup and len(dielectric_layers) != max(0, n - 1):
        result.warnings.append(
            f"Stackup has {len(dielectric_layers)} dielectric layer(s) for "
            f"{n} copper layers; using a single fallback substrate."
        )
    created_materials: dict[str, dict[str, Any]] = {}

    if opts.create_substrate:
        if len(dielectric_layers) == max(0, n - 1) and n > 1:
            for i, entry in enumerate(dielectric_layers):
                z_hi = z_by_layer[copper_names[i]]
                z_lo = z_by_layer[copper_names[i + 1]]
                if z_hi <= z_lo:
                    continue
                name = f"kicad_dielectric_{i + 1}"
                eps = entry.epsilon if entry.epsilon > 0 else opts.substrate_epsilon
                tand = entry.tand if entry.tand >= 0 else opts.substrate_tand
                created_materials[name] = {
                    "name": name, "kind": "dielectric",
                    "epsilon": float(eps), "tand": float(tand),
                }
                result.primitives.extend(
                    _polygon_primitives(
                        name, outline, z_lo, z_hi - z_lo, name, 10
                    )
                )
        else:
            name = str(opts.substrate_material or "kicad_substrate")
            created_materials[name] = {
                "name": name, "kind": "dielectric",
                "epsilon": float(opts.substrate_epsilon),
                "tand": float(opts.substrate_tand),
            }
            result.primitives.extend(
                _polygon_primitives(
                    name, outline,
                    -board_thickness / 2.0, board_thickness,
                    name, 10,
                )
            )

    # --- copper ---------------------------------------------------------
    copper_thicknesses = (
        _copper_thicknesses(stackup, opts.copper_thickness_mm)
        if opts.use_stackup
        else {}
    )
    copper_mode = str(opts.copper_mode or "").lower()
    if copper_mode not in ("sheet", "prism", "mesh"):
        copper_mode = "sheet" if opts.copper_as_sheet else "prism"
    mesh_path = (
        Path(mesh_dir)
        if mesh_dir
        else source.parent / f"{source.stem}_openems"
    )
    for index, layer in enumerate(copper_names):
        geom = raw_copper.get(layer)
        if geom is None:
            continue
        z = z_by_layer[layer]
        thickness = float(copper_thicknesses.get(layer, opts.copper_thickness_mm))
        before = len(result.primitives)
        prefix = f"cu_{layer.replace('.', '_')}"
        if copper_mode == "mesh":
            z0 = z - thickness / 2.0
            z1 = z + thickness / 2.0
            result.primitives.extend(
                _copper_mesh_primitives(
                    prefix, geom, z0, z1, "copper", 100 + index, mesh_path
                )
            )
        else:
            use_thickness = 0.0 if copper_mode == "sheet" else thickness
            z0 = z if copper_mode == "sheet" else z - use_thickness / 2.0
            result.primitives.extend(
                _polygon_primitives(
                    prefix,
                    geom,
                    z0,
                    use_thickness,
                    "copper",
                    100 + index,
                )
            )
        result.layers.append(
            {
                "name": layer,
                "z_mm": float(z),
                "area_mm2": float(geom.area),
                "primitive_count": len(result.primitives) - before,
            }
        )

    # --- vias -----------------------------------------------------------
    if opts.include_vias:
        for via in _children(board, "via"):
            x, y, _angle = _at(via)
            size = _as_float(_atom(via, "size"), 0.0)
            drill = _as_float(_atom(via, "drill"), 0.0)
            diameter = drill if drill > 0 else size
            if diameter <= 0:
                continue
            layers_node = _child(via, "layers")
            via_layers = (
                [str(v) for v in layers_node[1:]]
                if layers_node is not None
                else [copper_names[0], copper_names[-1]]
            )
            zs = [z_by_layer[name] for name in via_layers if name in z_by_layer]
            z0 = min(zs) if zs else min(z_by_layer.values())
            z1 = max(zs) if zs else max(z_by_layer.values())
            if not opts.copper_as_sheet:
                z0 -= opts.copper_thickness_mm / 2.0
                z1 += opts.copper_thickness_mm / 2.0
            tx = x + dx
            ty = (-y if opts.flip_y else y) + dy
            result.primitives.append(
                {
                    "kind": "cylinder",
                    "name": f"via_{result.via_count:05d}",
                    "start": [tx, ty, z0],
                    "stop": [tx, ty, z1],
                    "radius": diameter / 2.0 + opts.via_plating_radius_mm,
                    "material": "copper",
                    "priority": 120,
                    "source_layer": "via",
                    "drill_mm": diameter,
                    "span": via_layers,
                }
            )
            result.via_count += 1

    # --- through-hole pad barrels / NPTH -------------------------------
    for barrel in barrels:
        if barrel["kind"] == "pth":
            if not opts.include_pad_barrels:
                continue
            layers = barrel.get("layers") or copper_names
            zs = [z_by_layer[name] for name in layers if name in z_by_layer]
            z0 = min(zs) if zs else min(z_by_layer.values())
            z1 = max(zs) if zs else max(z_by_layer.values())
            if not opts.copper_as_sheet:
                z0 -= opts.copper_thickness_mm / 2.0
                z1 += opts.copper_thickness_mm / 2.0
            x = barrel["x"] + dx
            y = (-barrel["y"] if opts.flip_y else barrel["y"]) + dy
            result.primitives.append(
                {
                    "kind": "cylinder",
                    "name": f"padbarrel_{result.pad_barrel_count:05d}",
                    "start": [x, y, z0],
                    "stop": [x, y, z1],
                    "radius": barrel["drill"] / 2.0 + opts.via_plating_radius_mm,
                    "material": "copper",
                    "priority": 121,
                    "source_layer": "pad_barrel",
                    "drill_mm": barrel["drill"],
                }
            )
            result.pad_barrel_count += 1
        else:
            if not opts.include_npth:
                continue
            geom = barrel["at"]
            # Recompute a compact bbox after the global transform.
            if opts.flip_y:
                geom = affinity.scale(geom, yfact=-1.0, origin=(0, 0))
            geom = affinity.translate(geom, dx, dy)
            result.primitives.extend(
                _polygon_primitives(
                    f"npth_{result.pad_barrel_count:05d}",
                    geom,
                    -board_thickness / 2.0,
                    board_thickness,
                    "vacuum",
                    130,
                )
            )
            result.pad_barrel_count += 1

    result.materials = [{"name": "copper", "kind": "pec"}, *created_materials.values()]
    if opts.include_npth:
        result.materials.append({"name": "vacuum", "kind": "dielectric", "epsilon": 1.0})
    if len(result.primitives) > 5000:
        result.warnings.append(
            f"Large geometry: {len(result.primitives)} primitives. Consider "
            "raising 'simplify' or disabling zones/pads/tracks."
        )
    result.warnings.extend(warnings)
    result.ok = True
    return result


def apply_kicad_to_project(
    project,
    result: KiCadImportResult,
    source: str | Path,
    spec=None,
) -> dict[str, int]:
    """Merge a native KiCad import into a project (re-import safe)."""
    from app.models.project import GeometryFile, MaterialDef

    source = str(source)
    old = list(getattr(project, "primitives", None) or [])
    replaced = [p for p in old if p.get("source_file") == source]
    kept = [p for p in old if p.get("source_file") != source]
    overrides = {
        str(p.get("name")): (p.get("material"), int(p.get("priority", 10)))
        for p in replaced
        if p.get("name")
    }

    added: list[dict[str, Any]] = []
    for raw in result.primitives:
        prim = dict(raw)
        prim["source_file"] = source
        prim["source_format"] = "kicad"
        override = overrides.get(str(prim.get("name")))
        if override is not None:
            material, priority = override
            if material:
                prim["material"] = material
            prim["priority"] = priority
        added.append(prim)
    project.primitives = kept + added

    def ensure_material(name: str, kind: str, **kwargs):
        existing = next((m for m in project.materials if m.name == name), None)
        if existing is None:
            existing = MaterialDef(name=name, kind=kind, **kwargs)
            project.materials.append(existing)
        else:
            for key, value in kwargs.items():
                setattr(existing, key, value)
        return existing

    for mat in result.materials:
        name = str(mat.get("name", "")).strip()
        if not name:
            continue
        ensure_material(
            name,
            str(mat.get("kind", "dielectric")),
            **{
                key: mat[key]
                for key in ("epsilon", "tand", "kappa")
                if key in mat
            },
        )

    names = sorted({str(layer.get("name")) for layer in result.layers})
    geometry_file = GeometryFile(
        path=source, format="kicad", unit="mm", object_names=names
    )
    project.geometry_files = [
        gf for gf in project.geometry_files if gf.path != source
    ] + [geometry_file]

    if spec is not None:
        spec.source = source
        project.kicad = spec

    return {"added": len(added), "replaced": len(replaced), "kept": len(kept)}


__all__ = [
    "KiCadImportOptions",
    "KiCadImportResult",
    "KiCadInspection",
    "KiCadStackupLayer",
    "apply_kicad_to_project",
    "import_kicad_pcb",
    "inspect_kicad_pcb",
]
