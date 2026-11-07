"""Import Gerber/Excellon manufacturing data as native CSXCAD primitives.

Phase 1: read a Gerber/Excellon folder or ZIP with :mod:`gerbonara`, vectorize
every copper layer with :mod:`shapely`, and return ``Project.primitives``
dictionaries for copper polygons and plated through-hole vias.

The importer deliberately does not go through FreeCAD/STEP/STL: gerber copper
is 2-D artwork, and openEMS wants exact polygons/sheets, not tessellated CAD
solids.  Substrate stackup (z levels, epsilon_r, tan_delta, via spans) is not
part of Gerber and must come from the caller (GUI dialog or board source file).

Units are millimetres throughout, matching the default project drawing unit.
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.ops import triangulate, unary_union

try:  # optional at import time so the rest of the app still imports
    from gerbonara import LayerStack, MM
    from gerbonara import graphic_objects as go
    from gerbonara import graphic_primitives as gp
    from gerbonara.graphic_objects import Flash
    from gerbonara.utils import approximate_arc
except ImportError:  # pragma: no cover - exercised only without the dependency
    LayerStack = None  # type: ignore[assignment]
    MM = None  # type: ignore[assignment]
    go = None  # type: ignore[assignment]
    gp = None  # type: ignore[assignment]
    Flash = None  # type: ignore[assignment]
    approximate_arc = None  # type: ignore[assignment]


GERBER_SUFFIXES = {
    ".gbr", ".ger", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo",
    ".gtp", ".gbp", ".gko", ".gm1", ".gm2", ".gm3", ".gp1", ".gp2",
    ".gp3", ".gp4", ".drl", ".xln", ".nc", ".d356", ".pho", ".art",
}

#: Explicit fallback mapping for common KiCad/Altium/Protel filenames.
#: gerbonara's autoguess is used first; this map is only applied when it fails
#: (e.g. a fabrication folder also contains *-top-pos.csv, which autoguess
#: mistakes for a copper layer, or KiCad's ``B_Cu.gbr`` is left as
#: "bottom unknown").  Values are layer-stack keys, not tuples.
_GERBER_LAYER_OVERRIDES = {
    r"(?i).*[._-]F[._-]?Cu\.(?:gbr|ger|gtl|pho|art)$": "top copper",
    r"(?i).*[._-]B[._-]?Cu\.(?:gbr|ger|gbl|pho|art)$": "bottom copper",
    r"(?i).*[._-]F[._-]?Mask\.(?:gbr|ger|gts|gtp|pho|art)$": "top mask",
    r"(?i).*[._-]B[._-]?Mask\.(?:gbr|ger|gbs|gbp|pho|art)$": "bottom mask",
    r"(?i).*[._-]F[._-]?SilkS?\.(?:gbr|ger|gto|pho|art)$": "top silk",
    r"(?i).*[._-]B[._-]?SilkS?\.(?:gbr|ger|gbo|pho|art)$": "bottom silk",
    r"(?i).*[._-]F[._-]?Paste\.(?:gbr|ger|gtp|pho|art)$": "top paste",
    r"(?i).*[._-]B[._-]?Paste\.(?:gbr|ger|gbp|pho|art)$": "bottom paste",
    r"(?i).*[._-]Edge[._-]?Cuts\.(?:gbr|ger|gko|gm1|pho|art)$": "mechanical outline",
    r"(?i).*\.gko$": "mechanical outline",
    r"(?i).*\.gm1$": "mechanical outline",
    r"(?i).*[._-]NPTH\.(?:drl|xln|nc|txt)$": "nonplated drill",
    r"(?i).*[._-]PTH\.(?:drl|xln|nc|txt)$": "plated drill",
    r"(?i).*\.gtl$": "top copper",
    r"(?i).*\.gbl$": "bottom copper",
    r"(?i).*\.gts$": "top mask",
    r"(?i).*\.gbs$": "bottom mask",
    r"(?i).*\.gto$": "top silk",
    r"(?i).*\.gbo$": "bottom silk",
    r"(?i).*\.gtp$": "top paste",
    r"(?i).*\.gbp$": "bottom paste",
    r"(?i).*\.drl$": "plated drill",
    r"(?i).*\.xln$": "plated drill",
    r"(?i).*\.nc$": "plated drill",
}


@dataclass
class GerberImportOptions:
    """Stackup/import settings that Gerber itself cannot provide."""

    board_thickness_mm: float = 1.6
    copper_thickness_mm: float = 0.035
    #: ``True`` -> zero-thickness AddPolygon sheets (recommended for PCBs);
    #: ``False`` -> extruded AddLinPoly prisms with ``copper_thickness_mm``.
    copper_as_sheet: bool = True
    #: Radial plating thickness added to the drill radius for via cylinders.
    via_plating_radius_mm: float = 0.025
    #: Drill hits larger than this are treated as component/mounting holes,
    #: not vias.  Set to a large value to import every plated hole.
    via_max_diameter_mm: float = 0.8
    include_npth: bool = False
    #: Arc chord tolerance for tessellation, in mm.
    arc_error_mm: float = 0.01
    #: Douglas-Peucker simplification tolerance, in mm (0 = off).
    simplify_mm: float = 0.0
    #: Drop isolated copper islands smaller than this, in mm^2.
    min_area_mm2: float = 0.005
    clip_to_outline: bool = True
    create_substrate: bool = True
    substrate_material: str = "substrate"
    #: optional solder mask as a thin dielectric layer (top/bottom)
    include_mask: bool = False
    solder_mask_thickness_mm: float = 0.02
    solder_mask_epsilon: float = 3.3
    solder_mask_tand: float = 0.02
    solder_mask_material: str = "solder_mask"
    #: optional side -> z override; empty means evenly distributed over the
    #: board thickness (top at +t/2, bottom at -t/2)
    layer_z: dict[str, float] = field(default_factory=dict)
    #: drill diameter key ("0.4000") -> [from_side, to_side]; missing = through
    via_spans: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class GerberInspection:
    """Cheap pre-import view of a Gerber set for the stackup dialog."""

    board_name: str = ""
    layers: list[dict[str, Any]] = field(default_factory=list)
    drills: list[dict[str, Any]] = field(default_factory=list)
    slots: list[dict[str, Any]] = field(default_factory=list)
    mask_layers: list[str] = field(default_factory=list)
    outline_bounds_mm: list[float] | None = None
    warnings: list[str] = field(default_factory=list)
    ok: bool = False


@dataclass
class GerberImportResult:
    """Result of :func:`import_gerber_set`."""

    primitives: list[dict[str, Any]] = field(default_factory=list)
    layers: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    board_name: str = ""
    ok: bool = False

    @property
    def copper_layer_count(self) -> int:
        return len(self.layers)

    @property
    def via_count(self) -> int:
        return sum(1 for p in self.primitives if p.get("source_layer") == "via")

    @property
    def slot_count(self) -> int:
        return sum(1 for p in self.primitives if p.get("source_layer") == "slot")

    @property
    def mask_layer_count(self) -> int:
        return len(
            {
                str(p.get("source_layer"))
                for p in self.primitives
                if str(p.get("source_layer", "")).startswith("mask_")
            }
        )


def _require_gerbonara() -> None:
    if LayerStack is None:
        raise RuntimeError(
            "Gerber import needs the 'gerbonara' package. Install it with: "
            "python -m pip install \"gerbonara==1.5.0\" "
            "(1.6.x requires Python >= 3.12)."
        )


def _gerber_candidates(paths) -> list[Path]:
    """Only fabrication files: ignore pick-and-place CSV, JSON, docs, etc."""
    out: list[Path] = []
    for path in paths:
        try:
            if path.is_file() and path.suffix.lower() in GERBER_SUFFIXES:
                out.append(path)
        except OSError:
            continue
    return sorted(out, key=lambda p: str(p))


def _from_gerber_files(
    files: list[Path], *, board_name: str | None = None
):
    """Build a LayerStack from an already filtered file list.

    Autoguess is preferred (it understands many CAD exporters).  If it fails,
    retry with explicit common KiCad/Altium filename rules.  This fixes
    folders where e.g. ``basic-B_Cu.gbr`` is left as "bottom unknown" or a
    ``*-top-pos.csv`` is mistaken for copper.
    """
    if not files:
        raise ValueError(
            "No Gerber/Excellon files found (expected .gbr/.ger/.drl/.xln/...); "
            "CSV/JSON files in the folder are ignored."
        )
    first_error: Exception | None = None
    try:
        stack = LayerStack.from_files(
            files, board_name=board_name, autoguess=True
        )
        if stack.copper_layers or stack.outline or stack.drill_pth:
            return stack
    except Exception as exc:  # noqa: BLE001 - try the explicit map below
        first_error = exc

    try:
        stack = LayerStack.from_files(
            files,
            board_name=board_name,
            autoguess=False,
            overrides=_GERBER_LAYER_OVERRIDES,
        )
        if stack.copper_layers or stack.outline or stack.drill_pth:
            return stack
    except Exception as exc:  # noqa: BLE001 - report the original error
        if first_error is None:
            first_error = exc

    if first_error is not None:
        raise first_error
    raise ValueError(
        "Could not identify Gerber copper/outline/drill layers in: "
        + ", ".join(p.name for p in files)
    )


def _open_gerber_stack(source: str | Path):
    """Open a folder/ZIP without letting CSV/JSON files confuse gerbonara."""
    path = Path(source)
    if path.is_dir():
        return _from_gerber_files(
            _gerber_candidates(path.rglob("*")), board_name=path.stem
        )

    if path.is_file() and zipfile.is_zipfile(path):
        # Extract only manufacturing files; LayerStack.open_zip() would scan
        # pick-and-place CSV/JSON as well and produce the same bogus mapping.
        with tempfile.TemporaryDirectory(prefix="openems_gerber_") as td:
            root = Path(td)
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    member = Path(info.filename.replace("\\", "/"))
                    if member.is_absolute() or ".." in member.parts:
                        continue
                    if member.suffix.lower() not in GERBER_SUFFIXES:
                        continue
                    target = root / member
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
            return _from_gerber_files(
                _gerber_candidates(root.rglob("*")), board_name=path.stem
            )

    # Single Gerber/Excellon file: let gerbonara decide.
    return LayerStack.open(path)


# --------------------------------------------------------------------------- #
# 2-D vectorization
# --------------------------------------------------------------------------- #
def _arcpoly_points(apoly, max_error: float) -> list[tuple[float, float]]:
    """Convert a gerbonara ``ArcPoly`` into a closed-list of 2-D points.

    ``ArcPoly.approximate_arcs()`` is broken in gerbonara 1.5.0
    (``segments`` is a property but called as a method), so tessellate the
    segments directly with :func:`gerbonara.utils.approximate_arc`.
    """
    pts: list[tuple[float, float]] = []
    for p1, p2, (clockwise, center) in apoly.segments:
        if clockwise is None:
            pts.append((float(p1[0]), float(p1[1])))
        else:
            cx, cy = center
            seg = list(
                approximate_arc(
                    cx, cy,
                    p1[0], p1[1], p2[0], p2[1],
                    clockwise,
                    max_error=max_error,
                )
            )
            # The next segment starts at p2; avoid duplicate points.
            pts.extend((float(x), float(y)) for x, y in seg[:-1])
    return pts


def _primitive_geometry(prim, arc_error: float) -> Polygon | None:
    """Gerbonara graphic primitive -> shapely polygon (or ``None``)."""
    try:
        pts = _arcpoly_points(prim.to_arc_poly(), arc_error)
    except (AttributeError, TypeError, ValueError, ZeroDivisionError):
        return None
    if len(pts) < 3:
        return None
    geom = Polygon(pts)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom if not geom.is_empty else None


def _layer_geometry(layer, opts: GerberImportOptions):
    """Vectorize one Gerber layer, honouring dark/clear polarity.

    Returns a shapely Polygon/MultiPolygon in mm, or ``None`` when the layer
    has no usable dark geometry.
    """
    dark: list[Polygon] = []
    clear: list[Polygon] = []

    for obj in layer.objects:
        for prim in obj.to_primitives(unit=MM):
            geom = _primitive_geometry(prim, opts.arc_error_mm)
            if geom is None or geom.area < opts.min_area_mm2:
                continue
            (dark if prim.polarity_dark else clear).append(geom)

    if not dark:
        return None

    result = unary_union(dark)
    if clear:
        # Common Gerber usage: clear features cut holes into the dark image.
        # (Exact paint order is not preserved; this is the right result for
        # normal copper layers, including LPC clearances.)
        result = result.difference(unary_union(clear))
    if opts.simplify_mm > 0:
        result = result.simplify(opts.simplify_mm, preserve_topology=True)
    return result if not result.is_empty else None


def _iter_polygons(geom) -> Iterator[Polygon]:
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        for part in geom.geoms:
            yield from _iter_polygons(part)


def _filled_polygons(poly: Polygon) -> list[Polygon]:
    """Split a polygon-with-holes into simple polygons for CSXCAD.

    CSXCAD's AddPolygon/AddLinPoly have no hole support.  Triangulating the
    polygon and keeping triangles whose representative point is inside the
    original polygon preserves holes exactly.
    """
    if not poly.interiors:
        return [poly]
    tris = [
        tri
        for tri in triangulate(poly)
        if poly.contains(tri.representative_point())
    ]
    # Fallback: keep the exterior rather than dropping the copper entirely.
    return tris or [Polygon(poly.exterior)]


# --------------------------------------------------------------------------- #
# Primitive creation
# --------------------------------------------------------------------------- #
def _polygon_primitives(
    prefix: str,
    geom,
    z: float,
    thickness: float,
    material: str,
    priority: int,
) -> list[dict[str, Any]]:
    """Create ``kind="polygon"`` dicts for a 2-D geometry at height ``z``."""
    out: list[dict[str, Any]] = []
    for i, poly in enumerate(_iter_polygons(geom)):
        for j, part in enumerate(_filled_polygons(poly)):
            coords = list(part.exterior.coords)[:-1]
            if len(coords) < 3:
                continue
            xs = [float(p[0]) for p in coords]
            ys = [float(p[1]) for p in coords]
            prim: dict[str, Any] = {
                "kind": "polygon",
                "name": f"{prefix}_{i:03d}_{j:02d}",
                "points": [[round(x, 6), round(y, 6)] for x, y in coords],
                "axis": "z",
                "coord": float(z),
                # bbox for viewers / cartesian_primitive_bbox(); the polygon
                # builder itself only uses points/axis/coord/length.
                "start": [min(xs), min(ys), float(z)],
                "stop": [max(xs), max(ys), float(z) + float(thickness)],
                "material": material,
                "priority": priority,
                "source_layer": prefix,
            }
            if thickness > 0:
                prim["length"] = float(thickness)
            out.append(prim)
    return out


def _diameter_key(diameter: float) -> str:
    return f"{float(diameter):.4f}"


def _side_z(z_by_side: dict[str, float], side: str) -> float | None:
    """Resolve a side name; accept both inner1 and gerbonara's inner_1."""
    if side in z_by_side:
        return float(z_by_side[side])
    if side.startswith("inner") and "_" not in side:
        alias = "inner_" + side[len("inner"):]
        if alias in z_by_side:
            return float(z_by_side[alias])
    if side.startswith("inner_"):
        alias = "inner" + side[len("inner_"):]
        if alias in z_by_side:
            return float(z_by_side[alias])
    return None


def _span_z_range(
    z_by_side: dict[str, float],
    opts: GerberImportOptions,
    z0: float,
    z1: float,
    span,
) -> tuple[float, float]:
    """Resolve a [from_side, to_side] via span to a z range."""
    if not span or len(span) != 2 or not z_by_side:
        return z0, z1
    first_z = _side_z(z_by_side, str(span[0]))
    last_z = _side_z(z_by_side, str(span[1]))
    if first_z is None or last_z is None:
        return z0, z1
    lo, hi = sorted((first_z, last_z))
    if not opts.copper_as_sheet:
        # Extruded copper grows upwards from its assigned z.
        hi += float(opts.copper_thickness_mm)
    return lo, hi


def _via_primitives(
    drill_file,
    dx: float,
    dy: float,
    z0: float,
    z1: float,
    z_by_side: dict[str, float],
    opts: GerberImportOptions,
    *,
    plated: bool,
) -> list[dict[str, Any]]:
    """Excellon drill hits -> vertical PEC/vacuum cylinders."""
    out: list[dict[str, Any]] = []
    if drill_file is None:
        return out

    for obj in drill_file.objects:
        # Milled slots are handled separately by _slot_primitives().
        if not isinstance(obj, Flash):
            continue
        dia = float(obj.tool.equivalent_width(MM))
        if dia <= 0 or dia > opts.via_max_diameter_mm:
            continue
        if plated is False and not opts.include_npth:
            continue

        span = (opts.via_spans or {}).get(_diameter_key(dia)) if plated else None
        via_z0, via_z1 = _span_z_range(z_by_side, opts, z0, z1, span)
        x = float(obj.x) + dx
        y = float(obj.y) + dy
        out.append(
            {
                "kind": "cylinder",
                "name": f"via_{len(out):05d}",
                "start": [x, y, float(via_z0)],
                "stop": [x, y, float(via_z1)],
                "radius": dia / 2.0 + opts.via_plating_radius_mm,
                "material": "copper" if plated else "vacuum",
                "priority": 120 if plated else 130,
                "source_layer": "via",
                "drill_mm": dia,
                "span": list(span) if span else ["bottom", "top"],
            }
        )
    return out


def _slot_primitives(
    drill_file,
    dx: float,
    dy: float,
    z0: float,
    z1: float,
    z_by_side: dict[str, float],
    opts: GerberImportOptions,
    *,
    plated: bool,
) -> list[dict[str, Any]]:
    """Excellon milled slots (Line/Arc with a tool width) -> prisms."""
    out: list[dict[str, Any]] = []
    if drill_file is None:
        return out
    if plated is False and not opts.include_npth:
        return out

    index = 0
    for obj in drill_file.slots():
        tool = getattr(obj, "aperture", None) or getattr(obj, "tool", None)
        dia = float(tool.equivalent_width(MM)) if tool is not None else 0.0
        span = (
            (opts.via_spans or {}).get(_diameter_key(dia))
            if plated and dia > 0
            else None
        )
        slot_z0, slot_z1 = _span_z_range(z_by_side, opts, z0, z1, span)
        thickness = float(slot_z1 - slot_z0)
        if thickness <= 0:
            continue

        geoms = []
        for prim in obj.to_primitives(unit=MM):
            geom = _primitive_geometry(prim, opts.arc_error_mm)
            if geom is not None and not geom.is_empty:
                geoms.append(geom)
        if not geoms:
            continue
        geom = affinity.translate(unary_union(geoms), dx, dy)

        parts = _polygon_primitives(
            f"slot_{index:04d}",
            geom,
            slot_z0,
            thickness,
            "copper" if plated else "vacuum",
            120 if plated else 130,
        )
        for part in parts:
            part["source_layer"] = "slot"
            part["slot_mm"] = dia
            part["span"] = list(span) if span else ["bottom", "top"]
        out.extend(parts)
        index += 1
    return out


def _mask_primitives(
    stack,
    outline,
    z_by_side: dict[str, float],
    dx: float,
    dy: float,
    opts: GerberImportOptions,
) -> list[dict[str, Any]]:
    """Solder mask as a thin dielectric: board minus mask openings."""
    if not opts.include_mask or outline is None or not z_by_side:
        return []

    out: list[dict[str, Any]] = []
    for side in ("top", "bottom"):
        layer = stack.get((side, "mask"))
        if layer is None:
            continue
        openings = _layer_geometry(layer, opts)
        mask_geom = outline if openings is None else outline.difference(openings)
        if mask_geom.is_empty:
            continue
        mask_geom = affinity.translate(mask_geom, dx, dy)

        if side == "top":
            base = float(z_by_side.get("top", max(z_by_side.values())))
            if not opts.copper_as_sheet:
                base += float(opts.copper_thickness_mm)
            z0 = base
            z1 = base + float(opts.solder_mask_thickness_mm)
        else:
            base = float(z_by_side.get("bottom", min(z_by_side.values())))
            z0 = base - float(opts.solder_mask_thickness_mm)
            z1 = base

        parts = _polygon_primitives(
            f"mask_{side}",
            mask_geom,
            z0,
            z1 - z0,
            opts.solder_mask_material,
            5,
        )
        out.extend(parts)
    return out


def _outline_geometry(stack, opts: GerberImportOptions):
    """Board outline as a shapely polygon (falls back to its bbox)."""
    if not stack.outline:
        return None

    try:
        chains = list(stack.outline_polygons(tol=opts.arc_error_mm, unit=MM))
    except Exception:  # noqa: BLE001 - outline chains are best effort
        chains = []

    polys: list[Polygon] = []
    for chain in chains:
        pts: list[tuple[float, float]] = []
        for obj in chain:
            # outline_polygons() yields graphic_primitives.Line/Arc; older
            # gerbonara versions may yield graphic_objects.Line/Arc instead.
            if isinstance(obj, (gp.Line, go.Line)):
                pts.append((float(obj.x1), float(obj.y1)))
            elif isinstance(obj, gp.Arc):
                # graphic_primitives.Arc stores the center in absolute
                # coordinates.
                seg = list(
                    approximate_arc(
                        float(obj.cx), float(obj.cy),
                        float(obj.x1), float(obj.y1),
                        float(obj.x2), float(obj.y2),
                        obj.clockwise,
                        max_error=opts.arc_error_mm,
                    )
                )
                pts.extend((float(x), float(y)) for x, y in seg[:-1])
            elif isinstance(obj, go.Arc):
                # graphic_objects.Arc stores the center relative to (x1, y1).
                cx = float(obj.cx) + float(obj.x1)
                cy = float(obj.cy) + float(obj.y1)
                seg = list(
                    approximate_arc(
                        cx, cy,
                        float(obj.x1), float(obj.y1),
                        float(obj.x2), float(obj.y2),
                        obj.clockwise,
                        max_error=opts.arc_error_mm,
                    )
                )
                pts.extend((float(x), float(y)) for x, y in seg[:-1])
        if len(pts) < 3:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if not poly.is_empty:
            polys.append(poly)

    if not polys:
        bbox = stack.outline.bounding_box(unit=MM)
        if bbox:
            (minx, miny), (maxx, maxy) = bbox
            polys = [box(minx, miny, maxx, maxy)]

    return unary_union(polys) if polys else None


def _default_layer_z(sides: list[str], board_thickness_mm: float) -> dict[str, float]:
    """Evenly distribute copper layers through the board thickness."""
    n = len(sides)
    if n == 0:
        return {}
    if n == 1:
        return {sides[0]: 0.0}
    top = board_thickness_mm / 2.0
    bottom = -board_thickness_mm / 2.0
    step = (top - bottom) / (n - 1)
    return {side: top - i * step for i, side in enumerate(sides)}


def inspect_gerber_set(
    source: str | Path,
    board_thickness_mm: float = 1.6,
) -> GerberInspection:
    """Return copper layer and drill statistics without vectorizing copper.

    Used by the import dialog to populate the stackup table before the
    potentially expensive polygon extraction.
    """
    _require_gerbonara()
    result = GerberInspection(board_name=Path(source).stem)

    try:
        stack = _open_gerber_stack(source)
    except Exception as exc:  # noqa: BLE001 - report to the GUI
        result.warnings.append(f"Gerber could not be read: {exc}")
        return result

    result.board_name = stack.board_name or result.board_name
    copper = stack.copper_layers
    sides = [side for (side, _use), _layer in copper]
    z_by_side = _default_layer_z(sides, board_thickness_mm)

    for (side, use), layer in copper:
        original = getattr(layer, "original_path", None)
        result.layers.append(
            {
                "side": side,
                "use": use,
                "file": Path(original).name if original else "",
                "z_mm": float(z_by_side[side]),
                "default_z_mm": float(z_by_side[side]),
            }
        )

    for plated, drill in ((True, stack.drill_pth), (False, stack.drill_npth)):
        if drill is None:
            continue
        counts: Counter[float] = Counter()
        slot_counts: Counter[float] = Counter()
        for obj in drill.objects:
            if isinstance(obj, Flash):
                counts[round(float(obj.tool.equivalent_width(MM)), 4)] += 1
            else:
                tool = getattr(obj, "aperture", None) or getattr(obj, "tool", None)
                if tool is not None:
                    slot_counts[round(float(tool.equivalent_width(MM)), 4)] += 1
        for diameter, count in sorted(counts.items()):
            result.drills.append(
                {
                    "diameter_mm": float(diameter),
                    "count": int(count),
                    "plated": bool(plated),
                }
            )
        for diameter, count in sorted(slot_counts.items()):
            result.slots.append(
                {
                    "diameter_mm": float(diameter),
                    "count": int(count),
                    "plated": bool(plated),
                }
            )

    result.mask_layers = [
        side for side in ("top", "bottom") if stack.get((side, "mask")) is not None
    ]

    outline = _outline_geometry(stack, GerberImportOptions(
        board_thickness_mm=board_thickness_mm,
    ))
    if outline is not None:
        result.outline_bounds_mm = [float(v) for v in outline.bounds]

    if not copper:
        result.warnings.append(
            "No copper layers recognised. Check the Gerber file names."
        )
    if stack.drill_pth is None:
        result.warnings.append("No plated drill file recognised.")

    result.ok = True
    return result


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def import_gerber_set(
    source: str | Path,
    opts: GerberImportOptions | None = None,
) -> GerberImportResult:
    """Import a Gerber/Excellon folder, ZIP or single manufacturing file.

    Parameters
    ----------
    source:
        Directory containing Gerber + Excellon files, a ZIP of such a folder,
        or a single Gerber/Excellon file.
    opts:
        Stackup and import options.  Mutation is not persisted here; the GUI
        stores it in the project.

    Returns
    -------
    GerberImportResult
        ``primitives`` ready for ``Project.primitives`` plus per-layer metadata
        and warnings.  On a hard parse failure ``ok`` is ``False`` and the
        error is reported in ``warnings``.
    """
    _require_gerbonara()
    opts = opts or GerberImportOptions()
    result = GerberImportResult(board_name=Path(source).stem)

    try:
        stack = _open_gerber_stack(source)
    except Exception as exc:  # noqa: BLE001 - surface parser errors to the GUI
        result.warnings.append(f"Gerber could not be read: {exc}")
        return result

    result.board_name = stack.board_name or result.board_name
    outline = _outline_geometry(stack, opts)

    # Put the board centre at the origin so the Mesh Viewer opens on it.
    dx = dy = 0.0
    if outline is not None:
        centre = outline.centroid
        dx, dy = -centre.x, -centre.y

    copper_layers = stack.copper_layers  # [((side, use), GerberFile), ...]
    sides = [side for (side, _use), _layer in copper_layers]
    z_by_side = _default_layer_z(sides, opts.board_thickness_mm)
    for side, z in (opts.layer_z or {}).items():
        if side in z_by_side:
            z_by_side[side] = float(z)
        else:
            result.warnings.append(
                f"z override for unknown copper layer '{side}' ignored."
            )

    if opts.create_substrate and outline is not None:
        minx, miny, maxx, maxy = outline.bounds
        result.primitives.append(
            {
                "kind": "box",
                "name": "substrate",
                "start": [minx + dx, miny + dy, -opts.board_thickness_mm / 2.0],
                "stop": [maxx + dx, maxy + dy, opts.board_thickness_mm / 2.0],
                "material": opts.substrate_material,
                "priority": 10,
                "source_layer": "substrate",
            }
        )
    elif opts.create_substrate:
        result.warnings.append(
            "No board outline found - substrate box was not created."
        )

    for idx, ((side, _use), layer) in enumerate(copper_layers):
        geom = _layer_geometry(layer, opts)
        if geom is None:
            result.warnings.append(f"No usable copper geometry on layer '{side}'.")
            continue

        if opts.clip_to_outline and outline is not None:
            geom = geom.intersection(outline)
        geom = affinity.translate(geom, dx, dy)
        if geom.is_empty:
            result.warnings.append(f"Layer '{side}' is empty after outline clip.")
            continue

        z = z_by_side[side]
        thickness = 0.0 if opts.copper_as_sheet else opts.copper_thickness_mm
        before = len(result.primitives)
        result.primitives.extend(
            _polygon_primitives(
                f"cu_{side}",
                geom,
                z,
                thickness,
                "copper",
                100 + idx,
            )
        )
        result.layers.append(
            {
                "side": side,
                "z_mm": float(z),
                "area_mm2": float(geom.area),
                "primitive_count": len(result.primitives) - before,
            }
        )

    if not copper_layers:
        result.warnings.append(
            "No copper layers recognised. Check the Gerber file names or pass "
            "LayerStack overrides."
        )

    if z_by_side:
        via_z0 = min(float(z) for z in z_by_side.values())
        via_z1 = max(float(z) for z in z_by_side.values())
        if not opts.copper_as_sheet:
            via_z1 += float(opts.copper_thickness_mm)
    else:
        via_z0 = -opts.board_thickness_mm / 2.0
        via_z1 = opts.board_thickness_mm / 2.0

    result.primitives.extend(
        _via_primitives(
            stack.drill_pth,
            dx,
            dy,
            via_z0,
            via_z1,
            z_by_side,
            opts,
            plated=True,
        )
    )
    result.primitives.extend(
        _slot_primitives(
            stack.drill_pth,
            dx,
            dy,
            via_z0,
            via_z1,
            z_by_side,
            opts,
            plated=True,
        )
    )
    if opts.include_npth:
        result.primitives.extend(
            _via_primitives(
                stack.drill_npth,
                dx,
                dy,
                via_z0,
                via_z1,
                z_by_side,
                opts,
                plated=False,
            )
        )
        result.primitives.extend(
            _slot_primitives(
                stack.drill_npth,
                dx,
                dy,
                via_z0,
                via_z1,
                z_by_side,
                opts,
                plated=False,
            )
        )

    if opts.include_mask:
        mask_prims = _mask_primitives(
            stack, outline, z_by_side, dx, dy, opts
        )
        if mask_prims:
            result.primitives.extend(mask_prims)
        else:
            result.warnings.append(
                "No solder mask layers found (or the mask covers the whole board)."
            )

    if stack.drill_pth is None:
        result.warnings.append("No plated drill file recognised - no vias imported.")
    else:
        all_hits = sum(1 for obj in stack.drill_pth.objects if isinstance(obj, Flash))
        imported_pth = sum(
            1
            for p in result.primitives
            if p.get("source_layer") == "via" and p.get("material") == "copper"
        )
        if all_hits > imported_pth:
            result.warnings.append(
                f"{all_hits - imported_pth} plated drill hit(s) skipped "
                f"(diameter > {opts.via_max_diameter_mm:g} mm); adjust "
                "'via_max_diameter_mm' to include them."
            )

    result.ok = True
    return result


def apply_gerber_to_project(
    project,
    result: GerberImportResult,
    source: str | Path,
    spec=None,
) -> dict[str, int]:
    """Merge an import result into ``project`` (re-import safe).

    Old primitives from the same source are replaced, but manual material and
    priority changes are carried over by primitive name.  The substrate
    material is created/updated from ``spec`` and a ``GeometryFile`` entry is
    kept for provenance.

    Returns a small summary dict (``added``, ``replaced``, ``kept``).
    """
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
        prim["source_format"] = "gerber"
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

    ensure_material("copper", "pec")
    if spec is not None and bool(getattr(spec, "create_substrate", True)):
        ensure_material(
            str(getattr(spec, "substrate_material", "") or "substrate"),
            "dielectric",
            epsilon=float(getattr(spec, "substrate_epsilon", 4.4)),
            tand=float(getattr(spec, "substrate_tand", 0.02)),
        )
    if spec is not None and bool(getattr(spec, "include_npth", False)):
        ensure_material("vacuum", "dielectric", epsilon=1.0)
    if spec is not None and bool(getattr(spec, "include_mask", False)):
        ensure_material(
            str(getattr(spec, "solder_mask_material", "") or "solder_mask"),
            "dielectric",
            epsilon=float(getattr(spec, "solder_mask_epsilon", 3.3)),
            tand=float(getattr(spec, "solder_mask_tand", 0.02)),
        )

    names = sorted(
        {
            str(p.get("source_layer"))
            for p in added
            if p.get("source_layer") not in (None, "", "via", "substrate")
        }
    )
    geometry_file = GeometryFile(
        path=source,
        format="gerber",
        unit="mm",
        object_names=names,
    )
    project.geometry_files = [
        gf for gf in project.geometry_files if gf.path != source
    ] + [geometry_file]

    if spec is not None:
        spec.source = source
        project.gerber = spec

    return {"added": len(added), "replaced": len(replaced), "kept": len(kept)}


__all__ = [
    "GERBER_SUFFIXES",
    "GerberImportOptions",
    "GerberImportResult",
    "GerberInspection",
    "apply_gerber_to_project",
    "import_gerber_set",
    "inspect_gerber_set",
]

