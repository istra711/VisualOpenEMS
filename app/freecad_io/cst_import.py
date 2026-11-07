"""Import a CST Studio Suite export package (Variant A).

CST's ``.cst`` file is a proprietary container, so this module never tries to
parse it.  Instead it reads a small, documented interchange package exported
from CST:

``manifest.json``
    Units, frequency range, boundaries, materials, solids and ports.
``stl/<component>__<solid>.stl``
    One binary/ASCII STL per CST solid.  CST itself writes STL coordinates in
    metres; the manifest's ``units.geometry`` value is therefore authoritative.

The package may be a folder or a ZIP file.  Both are resolved to
:class:`CstInspection`, then :func:`import_cst_package` turns the STLs into
openEMS/CSXCAD triangle-mesh primitives and fills the project's materials,
ports, frequency range and boundary spec.

Only the CST features that map cleanly to this application's project model are
imported.  Floquet/unit-cell ports, dispersive/anisotropic materials, CST's own
mesh and schematic/field-monitor data are reported as warnings rather than
silently approximated.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from app.models.project import PortDef, mm_per_unit

CST_MANIFEST_NAMES = (
    "manifest.json",
    "cst_manifest.json",
    "cst_export.json",
    "openems_cst.json",
)
MESH_SUFFIXES = {".stl", ".obj"}
BOUNDARY_SIDES = ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
VALID_MATERIAL_KINDS = {
    "pec",
    "dielectric",
    "conducting_sheet",
    "lumped_rlc",
    "ignore",
}

_LENGTH_TO_MM = {
    "m": 1000.0,
    "meter": 1000.0,
    "metre": 1000.0,
    "meters": 1000.0,
    "metres": 1000.0,
    "cm": 10.0,
    "mm": 1.0,
    "millimeter": 1.0,
    "millimetre": 1.0,
    "um": 0.001,
    "µm": 0.001,
    "micron": 0.001,
    "nm": 1e-6,
    "in": 25.4,
    "inch": 25.4,
    "inches": 25.4,
    "ft": 304.8,
    "mil": 0.0254,
}
_FREQ_TO_HZ = {
    "hz": 1.0,
    "khz": 1e3,
    "mhz": 1e6,
    "ghz": 1e9,
    "thz": 1e12,
    "phz": 1e15,
}

_SIDE_ALIASES = {
    "xmin": ("xmin", "x_min", "x-", "xneg", "xnegative"),
    "xmax": ("xmax", "x_max", "x+", "xpos", "xpositive"),
    "ymin": ("ymin", "y_min", "y-", "yneg", "ynegative"),
    "ymax": ("ymax", "y_max", "y+", "ypos", "ypositive"),
    "zmin": ("zmin", "z_min", "z-", "zneg", "znegative"),
    "zmax": ("zmax", "z_max", "z+", "zpos", "zpositive"),
}


# --------------------------------------------------------------------------- #
# Data containers
# --------------------------------------------------------------------------- #
@dataclass
class CstImportOptions:
    """Options for :func:`import_cst_package`."""

    include_materials: bool = True
    include_ports: bool = True
    include_frequency: bool = True
    include_boundaries: bool = True
    include_sheets: bool = True
    flip_y: bool = False
    #: Drawing unit of the target project (CST coordinates are converted to it)
    target_unit: str = "mm"


@dataclass
class CstObject:
    name: str
    file: str
    material: str = "PEC"
    kind: str = "solid"  # solid|sheet
    component: str = ""
    priority: int = 10
    visible: bool = True
    exists: bool = True
    bounds_min: list[float] | None = None
    bounds_max: list[float] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CstMaterial:
    name: str
    kind: str = "dielectric"
    epsilon: float = 1.0
    permeability: float = 1.0
    kappa: float = 0.0
    tand: float = 0.0
    color: tuple[float, float, float, float] | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "epsilon": float(self.epsilon),
            "permeability": float(self.permeability),
            "kappa": float(self.kappa),
            "tand": float(self.tand),
        }
        if self.color is not None:
            out["color"] = list(self.color)
        return out


@dataclass
class CstPort:
    name: str
    type: str = "lumped"
    start: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    stop: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    resistance: float = 50.0
    inductance: float = 0.0
    capacitance: float = 0.0
    direction: str = "x"
    excite_port: bool = True
    active: bool = True
    prop_direction: str = ""
    exc_direction: str = ""
    wg_a: float = 0.0
    wg_b: float = 0.0
    wg_mode: str = "TE10"
    lumped_width: str = "line"
    mesh_dirs: str = "xyz"
    reverse: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CstFrequency:
    start_hz: float = 0.0
    stop_hz: float = 0.0
    unit: str = "GHz"


@dataclass
class CstInspection:
    """Normalized contents of a CST export package."""

    source_path: str = ""
    root: str = ""
    manifest_path: str = ""
    project_name: str = ""
    geometry_unit: str = "mm"
    frequency_unit: str = "GHz"
    objects: list[CstObject] = field(default_factory=list)
    materials: list[CstMaterial] = field(default_factory=list)
    ports: list[CstPort] = field(default_factory=list)
    frequency: CstFrequency | None = None
    boundaries: dict[str, str] = field(default_factory=dict)
    #: CST calculation-domain box in millimetres, when exported by the macro
    calculation_box_min: list[float] | None = None
    calculation_box_max: list[float] | None = None
    #: 0 = manifest did not specify a PML layer count (keep project value)
    pml_size: int = 0
    warnings: list[str] = field(default_factory=list)
    ok: bool = False

    @property
    def object_count(self) -> int:
        return len(self.objects)

    @property
    def port_count(self) -> int:
        return len(self.ports)

    @property
    def is_zip(self) -> bool:
        return Path(self.source_path).suffix.lower() == ".zip"


@dataclass
class CstImportResult:
    primitives: list[dict[str, Any]] = field(default_factory=list)
    materials: list[dict[str, Any]] = field(default_factory=list)
    ports: list[Any] = field(default_factory=list)
    frequency: CstFrequency | None = None
    boundaries: dict[str, str] = field(default_factory=dict)
    #: xmin,xmax,ymin,ymax,zmin,zmax in the target drawing unit
    air_margin: list[float] = field(default_factory=list)
    pml_size: int = 0
    object_names: list[str] = field(default_factory=list)
    #: Drawing unit of the generated primitive coordinates
    target_unit: str = "mm"
    warnings: list[str] = field(default_factory=list)
    ok: bool = False

    @property
    def object_count(self) -> int:
        return len(self.object_names)


# --------------------------------------------------------------------------- #
# Small parsing helpers
# --------------------------------------------------------------------------- #
def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return float(default)
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return float(default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(_as_float(value, float(default))))
    except (TypeError, ValueError):
        return int(default)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled", "active", "excited"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", "inactive"}:
        return False
    return bool(default)


def _as_axis(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^xyz]", "", text[:1])
    return text if text in {"x", "y", "z"} else ""


def _shortest_axis(start: list[float], stop: list[float]) -> str:
    """Axis with the smallest extent (including zero-thickness port planes)."""
    extents = [abs(float(stop[i]) - float(start[i])) for i in range(3)]
    return "xyz"[min(range(3), key=lambda i: extents[i])]


def _longest_axis(start: list[float], stop: list[float]) -> str:
    """Axis of a discrete/lumped port line (largest start->stop extent)."""
    extents = [abs(float(stop[i]) - float(start[i])) for i in range(3)]
    return "xyz"[max(range(3), key=lambda i: extents[i])]


def _transverse_axes(direction: str) -> tuple[str, str]:
    return {
        "x": ("y", "z"),
        "y": ("z", "x"),
        "z": ("x", "y"),
    }.get(direction, ("x", "y"))


def _unique_name(base: str, taken: set[str]) -> str:
    name = str(base or "object").strip() or "object"
    if name not in taken:
        taken.add(name)
        return name
    n = 2
    while f"{name}_{n}" in taken:
        n += 1
    out = f"{name}_{n}"
    taken.add(out)
    return out


def _safe_file_stem(name: str, taken: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name or "object")).strip("._")
    if not stem:
        stem = "object"
    if stem not in taken:
        taken.add(stem)
        return stem
    n = 2
    while f"{stem}_{n}" in taken:
        n += 1
    out = f"{stem}_{n}"
    taken.add(out)
    return out


def _source_key(path: str | Path) -> str:
    """Stable source identity for re-imports (separators and case on Windows)."""
    text = str(path or "")
    if not text:
        return ""
    try:
        text = str(Path(text).resolve())
    except OSError:
        text = str(Path(text).absolute())
    return os.path.normcase(text) if os.name == "nt" else text


def _length_unit(value: Any, warnings: list[str]) -> str:
    text = str(value or "mm").strip().lower()
    if text in _LENGTH_TO_MM:
        return text
    warnings.append(
        f"Unknown geometry unit {value!r}; assuming millimetres. "
        "Use m, cm, mm, um or in in the manifest."
    )
    return "mm"


def _length_to_mm(value: Any, unit: str) -> float:
    return _LENGTH_TO_MM.get(str(unit or "mm").lower(), 1.0)


def _freq_unit(value: Any, warnings: list[str]) -> str:
    text = str(value or "GHz").strip()
    if text.lower() in _FREQ_TO_HZ:
        return text
    warnings.append(
        f"Unknown frequency unit {value!r}; assuming GHz. "
        "Use Hz, kHz, MHz, GHz, THz or PHz in the manifest."
    )
    return "GHz"


def _parse_frequency_value(value: Any, unit: str) -> float | None:
    """Parse ``2.4``, ``2.4 GHz`` or ``2.4e9`` into Hz."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) * _FREQ_TO_HZ.get(str(unit).lower(), 1e9)
    text = str(value).strip()
    match = re.match(
        r"^\s*([-+]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:e[-+]?\d+)?)\s*([a-zA-Zµ]*)\s*$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    number = _as_float(match.group(1), 0.0)
    suffix = (match.group(2) or unit or "GHz").lower()
    return number * _FREQ_TO_HZ.get(suffix, _FREQ_TO_HZ.get(str(unit).lower(), 1e9))


def _vec3(value: Any, scale: float = 1.0) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = [
            _first(value, "x", "X"),
            _first(value, "y", "Y"),
            _first(value, "z", "Z"),
        ]
    if isinstance(value, str):
        parts = re.split(r"[,;\s]+", value.strip())
    else:
        try:
            parts = list(value)
        except TypeError:
            return None
    if len(parts) < 3:
        return None
    try:
        return [float(parts[0]) * scale, float(parts[1]) * scale, float(parts[2]) * scale]
    except (TypeError, ValueError):
        return None


def _bounds_from_entry(entry: dict[str, Any], scale: float) -> tuple[list[float] | None, list[float] | None]:
    bmin = _vec3(_first(entry, "bounds_min", "min", "start", "lower"), scale)
    bmax = _vec3(_first(entry, "bounds_max", "max", "stop", "upper"), scale)
    bounds = entry.get("bounds")
    if (bmin is None or bmax is None) and isinstance(bounds, (list, tuple)):
        if len(bounds) == 2:
            b0 = _vec3(bounds[0], scale)
            b1 = _vec3(bounds[1], scale)
            bmin, bmax = bmin or b0, bmax or b1
        elif len(bounds) == 6:
            bmin = [float(bounds[0]) * scale, float(bounds[1]) * scale, float(bounds[2]) * scale]
            bmax = [float(bounds[3]) * scale, float(bounds[4]) * scale, float(bounds[5]) * scale]
    return bmin, bmax


def _box_pair(
    raw: Any, scale: float
) -> tuple[list[float] | None, list[float] | None]:
    """Parse a min/max (or start/stop) 3-D box from a manifest value."""
    if raw is None:
        return None, None
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        return _vec3(raw[0], scale), _vec3(raw[1], scale)
    if not isinstance(raw, dict):
        return None, None
    lo = _vec3(_first(raw, "min", "minimum", "start", "lower", "from"), scale)
    hi = _vec3(_first(raw, "max", "maximum", "stop", "upper", "to"), scale)
    if lo is None or hi is None:
        vals = [
            _as_float(_first(raw, key, default=None), float("nan"))
            for key in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
        ]
        if all(math.isfinite(v) for v in vals):
            lo = [vals[0] * scale, vals[2] * scale, vals[4] * scale]
            hi = [vals[1] * scale, vals[3] * scale, vals[5] * scale]
    return lo, hi


def _color_tuple(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("#") and len(text) in (7, 9):
            try:
                vals = [
                    int(text[i : i + 2], 16) / 255.0
                    for i in range(1, len(text), 2)
                ]
                return tuple(vals + ([1.0] if len(vals) == 3 else []))[:4]  # type: ignore[return-value]
            except ValueError:
                return None
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        vals = [_as_float(v, 0.0) for v in value[:4]]
        if max(vals) > 1.0:
            vals = [v / 255.0 for v in vals]
        if len(vals) == 3:
            vals.append(1.0)
        return (vals[0], vals[1], vals[2], vals[3])
    return None


# --------------------------------------------------------------------------- #
# Package discovery and loading
# --------------------------------------------------------------------------- #
def _extract_zip(zip_path: Path, work_dir: Path | None, warnings: list[str]) -> Path:
    base = Path(work_dir) if work_dir is not None else Path(
        tempfile.mkdtemp(prefix="cst_import_")
    )
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"{zip_path.stem}_cst"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                member = info.filename.replace("\\", "/")
                first = member.split("/", 1)[0]
                if (
                    member.startswith("/")
                    or ".." in Path(member).parts
                    or ":" in first
                ):
                    warnings.append(f"Skipped unsafe ZIP member: {info.filename}")
                    continue
                archive.extract(info, target)
            # If the ZIP has one top-level wrapper folder, descend into it.
            entries = [p for p in target.iterdir() if not p.name.startswith(".")]
            if len(entries) == 1 and entries[0].is_dir():
                return entries[0]
    except (OSError, zipfile.BadZipFile) as exc:
        warnings.append(f"Could not extract CST ZIP package: {exc}")
        return target
    return target


def _find_manifest(root: Path) -> Path | None:
    for name in CST_MANIFEST_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    # A package can be zipped with its folder; accept one wrapped level too.
    found = sorted(root.glob("*/manifest.json")) + sorted(root.glob("*/cst_export.json"))
    if len(found) == 1:
        return found[0]
    if len(found) > 1:
        return found[0]
    return None


def _resolve_package(
    path: str | Path, work_dir: str | Path | None, warnings: list[str]
) -> tuple[Path, Path | None, str]:
    source = Path(path)
    source_text = str(source)
    if not source.exists():
        warnings.append(f"CST package not found: {source}")
        return source, None, source_text
    if source.is_dir():
        return source, _find_manifest(source), source_text
    suffix = source.suffix.lower()
    if suffix == ".zip":
        extracted = _extract_zip(source, Path(work_dir) if work_dir else None, warnings)
        return extracted, _find_manifest(extracted), source_text
    if suffix == ".json":
        return source.parent, source, source_text
    if suffix == ".cst":
        warnings.append(
            "A native .cst project is not a supported interchange format. "
            "Export a CST package (manifest.json + one STL per solid) and "
            "import the folder or ZIP instead."
        )
        return source, None, source_text
    warnings.append(
        f"Unsupported CST package source {source.name!r}. Select the package "
        "folder, its manifest.json, or a ZIP file."
    )
    return source, None, source_text


def inspect_cst_package(
    path: str | Path, work_dir: str | Path | None = None
) -> CstInspection:
    """Read and normalize a CST export package without loading STL triangles."""
    warnings: list[str] = []
    root, manifest_path, source_text = _resolve_package(path, work_dir, warnings)
    if manifest_path is not None and manifest_path.parent != root:
        # Accept a package folder wrapped inside the selected/ZIP root.
        root = manifest_path.parent
    inspection = CstInspection(
        source_path=source_text,
        root=str(root),
        manifest_path=str(manifest_path or ""),
        warnings=warnings,
    )
    if manifest_path is None or not manifest_path.is_file():
        if not warnings:
            warnings.append(
                "No manifest.json found. The CST export package needs a "
                "manifest.json (units, objects, materials, ports, boundaries) "
                "next to the stl/ folder."
            )
        return inspection

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"Could not parse {manifest_path.name}: {exc}")
        return inspection

    if isinstance(raw, list):
        data: dict[str, Any] = {"objects": raw}
    elif isinstance(raw, dict):
        data = raw
    else:
        warnings.append(
            f"{manifest_path.name} must contain a JSON object or a list of objects."
        )
        return inspection

    units_raw = data.get("units")
    if isinstance(units_raw, str):
        geometry_unit = units_raw
        frequency_unit = data.get("frequency_unit", "GHz")
    elif isinstance(units_raw, dict):
        geometry_unit = _first(
            units_raw, "geometry", "length", "length_unit", "geometry_unit", default="mm"
        )
        frequency_unit = _first(
            units_raw, "frequency", "frequency_unit", default=data.get("frequency_unit", "GHz")
        )
    else:
        geometry_unit = _first(
            data, "geometry_unit", "length_unit", "unit", default="mm"
        )
        frequency_unit = _first(data, "frequency_unit", default="GHz")
    inspection.geometry_unit = _length_unit(geometry_unit, warnings)
    inspection.frequency_unit = _freq_unit(frequency_unit, warnings)
    scale = _length_to_mm(1.0, inspection.geometry_unit)
    inspection.project_name = str(
        _first(data, "project", "name", "project_name", default=Path(source_text).stem)
    )
    raw_warnings = data.get("warnings")
    if isinstance(raw_warnings, list):
        for warning in raw_warnings:
            text = str(warning or "").strip()
            if text:
                warnings.append(text)
    elif raw_warnings:
        warnings.append(str(raw_warnings))

    # --- objects ---------------------------------------------------------
    raw_objects = _first(data, "objects", "solids", "meshes", "geometry", "parts", default=[])
    entries: list[tuple[str | None, Any]] = []
    if isinstance(raw_objects, dict):
        entries = [(str(k), v) for k, v in raw_objects.items()]
    elif isinstance(raw_objects, list):
        entries = [(None, v) for v in raw_objects]
    elif raw_objects:
        warnings.append("Manifest field 'objects' must be a list or an object map.")
    for index, (key_name, entry) in enumerate(entries):
        if isinstance(entry, str):
            entry = {"file": entry}
        if not isinstance(entry, dict):
            warnings.append(f"Skipped object #{index + 1}: expected an object entry.")
            continue
        raw_file = _first(
            entry,
            "stl",
            "file",
            "path",
            "mesh",
            "geometry_file",
            "stl_path",
            "mesh_path",
        )
        if not raw_file:
            warnings.append(
                f"Skipped object {key_name or index + 1!r}: no 'stl'/'file' path."
            )
            continue
        object_path = Path(str(raw_file))
        if not object_path.is_absolute():
            object_path = root / object_path
        object_name = str(
            _first(entry, "name", "object", "id", default=key_name or object_path.stem)
        ).strip() or object_path.stem
        component = str(
            _first(entry, "component", "component_name", default="")
        ).strip()
        if not component and "/" in object_name:
            component = object_name.split("/", 1)[0]
        kind_raw = str(_first(entry, "kind", "type", default="solid")).strip().lower()
        kind = "sheet" if kind_raw in {"sheet", "surface", "face", "2d"} else "solid"
        material = str(
            _first(entry, "material", "material_name", "mat", default="PEC")
        ).strip() or "PEC"
        bmin, bmax = _bounds_from_entry(entry, scale)
        exists = object_path.is_file() and object_path.suffix.lower() in MESH_SUFFIXES
        if not exists:
            if not object_path.is_file():
                warnings.append(f"Mesh file missing for {object_name}: {object_path}")
            else:
                warnings.append(
                    f"Skipped mesh for {object_name}: {object_path.suffix} is not "
                    "supported (Variant A imports STL/OBJ)."
                )
        inspection.objects.append(
            CstObject(
                name=object_name,
                file=str(object_path),
                material=material,
                kind=kind,
                component=component,
                priority=_as_int(_first(entry, "priority", default=10), 10),
                visible=_as_bool(_first(entry, "visible", "shown", default=True), True),
                exists=bool(exists),
                bounds_min=bmin,
                bounds_max=bmax,
                raw=dict(entry),
            )
        )
    if not inspection.objects:
        warnings.append("The manifest contains no geometry objects.")

    # --- materials -------------------------------------------------------
    raw_materials = _first(data, "materials", "material", "material_defs", default=[])
    material_entries: list[tuple[str | None, Any]] = []
    if isinstance(raw_materials, dict):
        material_entries = [(str(k), v) for k, v in raw_materials.items()]
    elif isinstance(raw_materials, list):
        material_entries = [(None, v) for v in raw_materials]
    seen_materials: set[str] = set()
    for index, (key_name, entry) in enumerate(material_entries):
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            warnings.append(
                f"Skipped material #{index + 1}: expected an object entry."
            )
            continue
        material = _material_from_entry(entry, key_name, warnings)
        if material is None:
            continue
        lowered = material.name.lower()
        if lowered in seen_materials:
            warnings.append(f"Duplicate material {material.name!r}; first one kept.")
            continue
        seen_materials.add(lowered)
        inspection.materials.append(material)

    # Every object material must exist in the project.  Add a sensible fallback
    # (PEC for obviously metallic names, vacuum-like dielectric otherwise).
    referenced = {obj.material for obj in inspection.objects if obj.material}
    known = {m.name.lower() for m in inspection.materials}
    for name in sorted(referenced):
        if name.lower() in known:
            continue
        kind = "pec" if _looks_metallic(name) else "dielectric"
        inspection.materials.append(
            CstMaterial(
                name=name,
                kind=kind,
                epsilon=1.0,
                kappa=5.8e7 if kind == "conducting_sheet" else 0.0,
                color=_default_color(kind),
            )
        )
        warnings.append(
            f"Material {name!r} is used by an object but not defined in the "
            f"manifest; using {'PEC' if kind == 'pec' else 'vacuum-like dielectric'}."
        )
        known.add(name.lower())

    # --- ports -----------------------------------------------------------
    raw_ports = _first(data, "ports", "port_defs", "excitations", default=[])
    port_entries: list[tuple[str | None, Any]] = []
    if isinstance(raw_ports, dict):
        port_entries = [(str(k), v) for k, v in raw_ports.items()]
    elif isinstance(raw_ports, list):
        port_entries = [(None, v) for v in raw_ports]
    taken_ports: set[str] = set()
    object_bounds = {
        obj.name: (obj.bounds_min, obj.bounds_max)
        for obj in inspection.objects
        if obj.bounds_min is not None and obj.bounds_max is not None
    }
    for index, (key_name, entry) in enumerate(port_entries):
        if not isinstance(entry, dict):
            warnings.append(
                f"Skipped port #{index + 1}: expected an object entry."
            )
            continue
        port = _port_from_entry(
            entry,
            key_name,
            scale,
            object_bounds,
            taken_ports,
            warnings,
        )
        if port is not None:
            inspection.ports.append(port)

    if inspection.ports and not any(
        p.excite_port and p.active for p in inspection.ports
    ):
        first = next((p for p in inspection.ports if p.active), None)
        if first is not None:
            first.excite_port = True
            warnings.append(
                f"No port was marked as excited; exciting the first port "
                f"{first.name!r}."
            )

    # --- frequency -------------------------------------------------------
    inspection.frequency = _parse_frequency(data, inspection.frequency_unit, warnings)
    if inspection.frequency is None:
        warnings.append(
            "No usable frequency range in the manifest; the project's current "
            "range is kept."
        )

    # --- boundaries ------------------------------------------------------
    raw_boundaries = _first(data, "boundaries", "boundary", "bc", default=None)
    inspection.boundaries, pml_size = _parse_boundaries(raw_boundaries, warnings)
    top_pml = _first(data, "pml_size", "pml_cells", "pml", default=None)
    if top_pml is not None:
        inspection.pml_size = _as_int(top_pml, 0)
    if pml_size is not None:
        inspection.pml_size = _as_int(pml_size, inspection.pml_size)
    if inspection.pml_size > 0:
        inspection.pml_size = max(1, min(200, inspection.pml_size))

    # --- CST calculation domain / boundary distance ----------------------
    raw_domain = _first(
        data, "domain", "calculation_domain", "simulation_domain", default=None
    )
    if raw_domain is None:
        raw_domain = _first(data, "calculation_box", "boundary_box", default=None)
    if isinstance(raw_domain, dict):
        domain_box = _first(
            raw_domain,
            "calculation_box",
            "calculation",
            "bounds",
            "box",
            default=raw_domain,
        )
    else:
        domain_box = raw_domain
    dom_lo, dom_hi = _box_pair(domain_box, scale)
    if dom_lo is not None and dom_hi is not None:
        inspection.calculation_box_min = [
            min(dom_lo[i], dom_hi[i]) for i in range(3)
        ]
        inspection.calculation_box_max = [
            max(dom_lo[i], dom_hi[i]) for i in range(3)
        ]
    elif raw_domain is not None:
        warnings.append(
            "CST calculation-domain box is malformed; the project air margin "
            "will be kept (auto)."
        )
    else:
        warnings.append(
            "No CST calculation-domain box in the manifest; the project air "
            "margin (auto) is kept. Re-export with the updated CST macro to "
            "reproduce the CST boundary distance, especially for PEC grounds."
        )

    inspection.ok = any(obj.exists for obj in inspection.objects)
    if not inspection.ok:
        warnings.append("No usable CST geometry could be read from the package.")
    if inspection.object_count and not any(obj.exists for obj in inspection.objects):
        warnings.append("All manifest mesh files are missing or unsupported.")
    return inspection


def _looks_metallic(name: str) -> bool:
    text = str(name or "").strip().lower()
    return any(
        token in text
        for token in (
            "pec",
            "perfect electric",
            "copper",
            "aluminium",
            "aluminum",
            "silver",
            "gold",
            "brass",
            "metal",
        )
    )


def _default_color(kind: str) -> tuple[float, float, float, float]:
    if kind == "pec":
        return (0.85, 0.55, 0.2, 1.0)
    if kind == "conducting_sheet":
        return (0.9, 0.7, 0.2, 1.0)
    if kind == "lumped_rlc":
        return (0.9, 0.25, 0.2, 1.0)
    return (0.2, 0.4, 0.8, 0.6)


def _material_from_entry(
    entry: dict[str, Any], key_name: str | None, warnings: list[str]
) -> CstMaterial | None:
    name = str(_first(entry, "name", "material", "id", default=key_name or "")).strip()
    if not name:
        warnings.append("Skipped a material entry without a name.")
        return None
    kind_raw = str(_first(entry, "kind", "type", "model", default="")).strip().lower()
    epsilon = _as_float(
        _first(entry, "epsilon", "eps", "epsilon_r", "permittivity", "eps_r", default=1.0),
        1.0,
    )
    permeability = _as_float(
        _first(entry, "permeability", "mu", "mue", "mu_r", default=1.0), 1.0
    )
    kappa = _as_float(
        _first(entry, "kappa", "conductivity", "sigma", default=0.0), 0.0
    )
    tand = _as_float(
        _first(
            entry,
            "tand",
            "tan_delta",
            "loss_tangent",
            "electric_loss_tangent",
            default=0.0,
        ),
        0.0,
    )
    if kind_raw in {"pec", "perfect electric conductor", "perfect conductor"}:
        kind = "pec"
    elif kind_raw in {
        "conducting_sheet",
        "conducting sheet",
        "lossy metal",
        "surface impedance",
        "sheet",
    }:
        kind = "conducting_sheet"
    elif kind_raw in {"lumped_rlc", "rlc", "lumped element"}:
        kind = "lumped_rlc"
    elif kind_raw in {"ignore", "ignored", "none"}:
        kind = "ignore"
    elif kind_raw in {
        "dielectric",
        "normal",
        "lossy dielectric",
        "material",
        "isotropic",
    }:
        kind = "dielectric"
    elif not kind_raw:
        if _looks_metallic(name):
            kind = "pec"
        elif kappa > 0.0:
            kind = "conducting_sheet"
        else:
            kind = "dielectric"
    else:
        warnings.append(
            f"Material {name!r}: CST type {kind_raw!r} is not mapped exactly; "
            "using a dielectric with the exported epsilon/kappa values."
        )
        kind = "dielectric"
    if kind not in VALID_MATERIAL_KINDS:
        kind = "dielectric"
    if kind == "conducting_sheet" and kappa <= 0.0:
        kappa = 5.8e7
        warnings.append(
            f"Material {name!r} is a conducting sheet without conductivity; "
            "using copper's 5.8e7 S/m."
        )
    if not math.isfinite(epsilon) or epsilon <= 0.0:
        warnings.append(f"Material {name!r}: invalid epsilon; using 1.0.")
        epsilon = 1.0
    if not math.isfinite(permeability) or permeability <= 0.0:
        permeability = 1.0
    if not math.isfinite(kappa) or kappa < 0.0:
        kappa = 0.0
    if not math.isfinite(tand) or tand < 0.0:
        tand = 0.0
    color = _color_tuple(_first(entry, "color", "colour", default=None))
    return CstMaterial(
        name=name,
        kind=kind,
        epsilon=float(epsilon),
        permeability=float(permeability),
        kappa=float(kappa),
        tand=float(tand),
        color=color or _default_color(kind),
    )


def _port_type(value: Any) -> tuple[str, str]:
    """Return ``(app_type, problem)``; problem is empty when mapped."""
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    if text in {
        "",
        "lumped",
        "discrete",
        "discrete port",
        "discrete edge port",
        "discrete face",
        "discrete face port",
        "sparam",
        "s-parameter",
    }:
        return "lumped", ""
    if text in {"msl", "msl port", "microstrip", "microstrip line", "microstrip port"}:
        return "msl", ""
    if text in {
        "waveguide",
        "waveguide port",
        "rect_waveguide",
        "rectangular waveguide",
        "rectangular waveguide port",
    }:
        return "rect_waveguide", ""
    if text in {"probe", "field probe", "voltage probe"}:
        return "probe", ""
    if any(token in text for token in ("floquet", "unit cell", "unit-cell")):
        return "", "Floquet/unit-cell ports are not supported by openEMS/lumped-port import."
    return "", f"Unsupported CST port type {value!r}."


def _port_from_entry(
    entry: dict[str, Any],
    key_name: str | None,
    scale: float,
    object_bounds: dict[str, tuple[list[float] | None, list[float] | None]],
    taken: set[str],
    warnings: list[str],
) -> CstPort | None:
    name = str(_first(entry, "name", "port", "id", default=key_name or "")).strip()
    if not name:
        name = f"port_{len(taken) + 1}"
    unique = _unique_name(name, taken)
    if unique != name:
        warnings.append(
            f"Duplicate port name {name!r}; imported as {unique!r}."
        )
        name = unique
    port_type, problem = _port_type(_first(entry, "type", "port_type", "kind", default="lumped"))
    if not port_type:
        warnings.append(f"Port {name!r} skipped: {problem}")
        return None
    if problem:
        warnings.append(f"Port {name!r}: {problem}")

    start = _vec3(_first(entry, "start", "from", "p1", "lower"), scale)
    stop = _vec3(_first(entry, "stop", "to", "p2", "upper"), scale)
    if start is None or stop is None:
        obj_name = str(_first(entry, "object", "solid", "on_object", default=""))
        bmin, bmax = object_bounds.get(obj_name, (None, None))
        # Object bounds were already normalized to mm during inspection.
        if start is None and bmin is not None:
            start = [float(v) for v in bmin]
        if stop is None and bmax is not None:
            stop = [float(v) for v in bmax]
    if start is None or stop is None:
        warnings.append(
            f"Port {name!r} skipped: start/stop missing (and no object bounds "
            "were available)."
        )
        return None

    direction = _as_axis(
        _first(entry, "direction", "axis", "normal", "port_direction", default="")
    )
    if not direction:
        direction = (
            _shortest_axis(start, stop)
            if port_type == "rect_waveguide"
            else _longest_axis(start, stop)
        )
    prop_direction = _as_axis(
        _first(entry, "prop_direction", "propagation_direction", default="")
    )
    exc_direction = _as_axis(
        _first(entry, "exc_direction", "excitation_direction", default="")
    )
    wg_a = _as_float(
        _first(entry, "wg_a", "a", "width", "wg_width", default=0.0), 0.0
    ) * scale
    wg_b = _as_float(
        _first(entry, "wg_b", "b", "height", "wg_height", default=0.0), 0.0
    ) * scale
    if port_type == "rect_waveguide" and (wg_a <= 0.0 or wg_b <= 0.0):
        first_axis, second_axis = _transverse_axes(direction)
        idx = {"x": 0, "y": 1, "z": 2}
        if wg_a <= 0.0:
            wg_a = abs(stop[idx[first_axis]] - start[idx[first_axis]])
        if wg_b <= 0.0:
            wg_b = abs(stop[idx[second_axis]] - start[idx[second_axis]])
        if wg_a > 0.0 and wg_b > 0.0:
            warnings.append(
                f"Waveguide port {name!r}: cross-section inferred from its box "
                f"(a={wg_a:g} mm, b={wg_b:g} mm)."
            )
    if port_type == "rect_waveguide" and (wg_a <= 0.0 or wg_b <= 0.0):
        warnings.append(
            f"Waveguide port {name!r}: width/height missing; set 'Waveguide a/b' "
            "in the port dialog after import."
        )

    mode = str(_first(entry, "wg_mode", "mode", default="TE10") or "TE10").strip().upper()
    if len(mode) < 4:
        warnings.append(
            f"Port {name!r}: waveguide mode {mode!r} is not a four-character "
            "TE/TM mode; using TE10."
        )
        mode = "TE10"
    resistance = _as_float(_first(entry, "resistance", "r", "impedance", default=50.0), 50.0)
    if resistance <= 0.0:
        resistance = 50.0
    active = _as_bool(_first(entry, "active", "enabled", default=True), True)
    excite = _as_bool(
        _first(entry, "excite_port", "excite", "excited", "excitation", default=False),
        False,
    ) and active
    return CstPort(
        name=name,
        type=port_type,
        start=start,
        stop=stop,
        resistance=resistance,
        inductance=_as_float(_first(entry, "inductance", "l", default=0.0), 0.0),
        capacitance=_as_float(_first(entry, "capacitance", "c", default=0.0), 0.0),
        direction=direction,
        excite_port=excite,
        active=active,
        prop_direction=prop_direction,
        exc_direction=exc_direction,
        wg_a=wg_a,
        wg_b=wg_b,
        wg_mode=mode,
        lumped_width=str(_first(entry, "lumped_width", "width_mode", default="line")),
        mesh_dirs=str(_first(entry, "mesh_dirs", default="xyz")),
        reverse=_as_bool(_first(entry, "reverse", "invert", default=False), False),
        raw=dict(entry),
    )


def _parse_frequency(
    data: dict[str, Any], default_unit: str, warnings: list[str]
) -> CstFrequency | None:
    raw = data.get("frequency", data.get("frequencies"))
    if isinstance(raw, dict):
        freq = raw
        unit = _freq_unit(
            _first(freq, "unit", "frequency_unit", default=default_unit), warnings
        )
        start_raw = _first(
            freq, "start", "fmin", "f_min", "min", "start_frequency", "lower"
        )
        stop_raw = _first(
            freq, "stop", "fmax", "f_max", "max", "stop_frequency", "upper"
        )
        if start_raw is None and "start_hz" in freq:
            start_raw = _first(freq, "start_hz")
            unit = "Hz"
        if stop_raw is None and "stop_hz" in freq:
            stop_raw = _first(freq, "stop_hz")
            unit = "Hz"
    elif isinstance(raw, (list, tuple)) and len(raw) >= 2:
        freq = {}
        unit = default_unit
        start_raw, stop_raw = raw[0], raw[1]
    else:
        freq = {}
        unit = _freq_unit(data.get("frequency_unit", default_unit), warnings)
        start_raw = _first(
            data, "start_frequency", "fmin", "f_min", "start", default=None
        )
        stop_raw = _first(
            data, "stop_frequency", "fmax", "f_max", "stop", default=None
        )
        if start_raw is None and "start_hz" in data:
            start_raw = data.get("start_hz")
            unit = "Hz"
        if stop_raw is None and "stop_hz" in data:
            stop_raw = data.get("stop_hz")
            unit = "Hz"
    start = _parse_frequency_value(start_raw, unit)
    stop = _parse_frequency_value(stop_raw, unit)
    if start is None or stop is None:
        return None
    if start < 0.0:
        start = 0.0
    if stop < start:
        warnings.append(
            f"Frequency range is reversed ({start:g}..{stop:g} Hz); swapping."
        )
        start, stop = stop, start
    if stop <= 0.0:
        return None
    return CstFrequency(start_hz=float(start), stop_hz=float(stop), unit=unit)


def _boundary_kind(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    text = re.sub(r"[()_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if text in {
        "e",
        "electric",
        "electrical",
        "pec",
        "perfect electric conductor",
        "tangential",
    }:
        return "PEC"
    if text in {
        "m",
        "magnetic",
        "pmc",
        "perfect magnetic conductor",
        "normal",
    }:
        return "PMC"
    if text in {
        "open",
        "open add space",
        "open space",
        "expanded open",
        "mur",
        "absorbing",
        "abc",
        "radiation",
    }:
        return "MUR"
    if text in {"pml", "pml add space", "perfectly matched layer"}:
        return "PML"
    return None


def _parse_boundaries(
    raw: Any, warnings: list[str]
) -> tuple[dict[str, str], int | None]:
    out: dict[str, str] = {}
    pml_size: int | None = None
    if raw is None:
        return out, pml_size
    if isinstance(raw, str):
        kind = _boundary_kind(raw)
        if kind:
            out.update({side: kind for side in BOUNDARY_SIDES})
        else:
            warnings.append(f"Unsupported CST boundary condition {raw!r}; kept defaults.")
        return out, pml_size
    if isinstance(raw, (list, tuple)):
        if len(raw) >= 6:
            for side, value in zip(BOUNDARY_SIDES, raw[:6]):
                kind = _boundary_kind(value)
                if kind:
                    out[side] = kind
                else:
                    warnings.append(
                        f"Boundary {side}: unsupported condition {value!r}; kept default."
                    )
        elif len(raw) == 1:
            return _parse_boundaries(raw[0], warnings)
        return out, pml_size
    if not isinstance(raw, dict):
        warnings.append("Field 'boundaries' must be a string, list or object.")
        return out, pml_size

    raw = {str(key).strip().lower(): value for key, value in raw.items()}
    pml_raw = _first(raw, "pml_size", "pml_cells", "pml", default=None)
    if pml_raw is not None and not isinstance(pml_raw, dict):
        pml_size = _as_int(pml_raw, 8)
    all_value = _first(raw, "all", "default", "type", default=None)
    explicit = False
    for side in BOUNDARY_SIDES:
        value = _first(raw, *(_SIDE_ALIASES[side] + (side,)), default=None)
        if value is None:
            continue
        explicit = True
        kind = _boundary_kind(value)
        if kind:
            out[side] = kind
        else:
            warnings.append(
                f"Boundary {side}: unsupported condition {value!r}; kept default."
            )
    if not explicit and all_value is not None:
        kind = _boundary_kind(all_value)
        if kind:
            out.update({side: kind for side in BOUNDARY_SIDES})
        else:
            warnings.append(
                f"Unsupported CST boundary condition {all_value!r}; kept defaults."
            )
    return out, pml_size


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #
def _primitive_bounds(prim: dict[str, Any]) -> tuple[list[float], list[float]] | None:
    try:
        a = [float(v) for v in prim.get("start", [])]
        b = [float(v) for v in prim.get("stop", [])]
    except (TypeError, ValueError):
        return None
    if len(a) != 3 or len(b) != 3:
        return None
    return [min(a[i], b[i]) for i in range(3)], [max(a[i], b[i]) for i in range(3)]


def _overlaps(
    a_lo: list[float],
    a_hi: list[float],
    b_lo: list[float],
    b_hi: list[float],
    axes: tuple[int, ...],
    tol: float = 1e-7,
) -> bool:
    return all(
        a_hi[i] >= b_lo[i] - tol and b_hi[i] >= a_lo[i] - tol for i in axes
    )


def _primitive_material_kind(
    prim: dict[str, Any], material_kinds: dict[str, str]
) -> str:
    name = str(prim.get("material") or "").strip().lower()
    kind = material_kinds.get(name, "")
    if kind:
        return kind
    if name in {"copper", "pec"} or "copper" in name:
        return "pec"
    return ""


def _planar_msl_from_geometry(
    port: PortDef,
    primitives: list[dict[str, Any]],
    material_kinds: dict[str, str],
) -> dict[str, Any] | None:
    """Recognize a CST waveguide port on a planar substrate as an MSL port.

    CST calls every modal port a "waveguide port", including microstrip feeds.
    A CST feed port is a zero-thickness plane crossing a dielectric slab with a
    thin PEC trace at one slab face.  That is exactly what openEMS's MSL port
    models; importing it as a rectangular hollow-waveguide port would use the
    wrong mode (and openEMS rejects its zero-length excitation direction).
    """
    axis = "xyz".find(str(port.direction or ""))
    if axis < 0:
        return None
    try:
        plane = 0.5 * (float(port.start[axis]) + float(port.stop[axis]))
    except (TypeError, ValueError, IndexError):
        return None
    tol = 1e-5 * max(1.0, abs(plane))
    if abs(float(port.stop[axis]) - float(port.start[axis])) > tol:
        return None
    port_lo = [min(float(port.start[i]), float(port.stop[i])) for i in range(3)]
    port_hi = [max(float(port.start[i]), float(port.stop[i])) for i in range(3)]
    transverse = tuple(i for i in range(3) if i != axis)

    for substrate in primitives:
        if _primitive_material_kind(substrate, material_kinds) != "dielectric":
            continue
        sname = str(substrate.get("material") or "").strip().lower()
        if any(token in sname for token in ("vacuum", "air", "background")):
            continue
        sb = _primitive_bounds(substrate)
        if sb is None:
            continue
        slo, shi = sb
        if not (slo[axis] - tol <= plane <= shi[axis] + tol):
            continue
        if not _overlaps(slo, shi, port_lo, port_hi, transverse, tol=tol):
            continue

        # The excitation axis is the thin substrate direction; the width axis
        # is the remaining transverse direction.
        candidates = [i for i in transverse if shi[i] - slo[i] > tol]
        if len(candidates) < 2:
            continue
        exc_i = min(candidates, key=lambda i: shi[i] - slo[i])
        width_i = next(i for i in transverse if i != exc_i)
        sub_thickness = shi[exc_i] - slo[exc_i]
        if sub_thickness <= tol:
            continue

        trace: tuple[float, list[float], list[float], str] | None = None
        for prim in primitives:
            if _primitive_material_kind(prim, material_kinds) != "pec":
                continue
            pb = _primitive_bounds(prim)
            if pb is None:
                continue
            plo, phi = pb
            if not (plo[axis] - tol <= plane <= phi[axis] + tol):
                continue
            if not _overlaps(plo, phi, port_lo, port_hi, transverse, tol=tol):
                continue
            if phi[exc_i] < slo[exc_i] - tol or plo[exc_i] > shi[exc_i] + tol:
                continue
            trace_thickness = phi[exc_i] - plo[exc_i]
            trace_width = phi[width_i] - plo[width_i]
            if trace_width <= tol or trace_thickness <= tol:
                continue
            # The feed is a thin strip, not a large ground plane.
            if trace_thickness > max(0.5 * sub_thickness, 4.0 * tol):
                continue
            if trace_width > 0.8 * (port_hi[width_i] - port_lo[width_i]) + tol:
                continue
            score = trace_width + 10.0 * trace_thickness
            if trace is None or score < trace[0]:
                trace = (score, plo, phi, str(prim.get("name") or ""))

        if trace is None:
            continue
        _score, tlo, thi, trace_name = trace
        # Signal is the trace face nearer to the substrate; ground is the
        # opposite substrate face.
        high_side = abs(thi[exc_i] - shi[exc_i]) <= abs(tlo[exc_i] - slo[exc_i])
        signal_face = thi[exc_i] if high_side else tlo[exc_i]
        ground_face = slo[exc_i] if high_side else shi[exc_i]
        if abs(signal_face - ground_face) <= tol:
            continue

        start = [float(v) for v in port.start]
        stop = [float(v) for v in port.stop]
        start[axis] = stop[axis] = plane
        start[width_i] = tlo[width_i]
        stop[width_i] = thi[width_i]
        # The port-plane coordinate along the propagation axis is zero length;
        # script_gen extends it by one grid cell.  Reverse ports on the high
        # side so the propagation sense points into the filter.  Because
        # script_gen swaps start/stop for a reversed MSL port (and MSLPort puts
        # its metal strip at start[exc]), store the signal/ground faces in the
        # order that leaves the metal strip on the trace after that swap.
        reverse = plane > 0.5 * (slo[axis] + shi[axis])
        if reverse:
            start[exc_i] = ground_face
            stop[exc_i] = signal_face
        else:
            start[exc_i] = signal_face
            stop[exc_i] = ground_face
        return {
            "type": "msl",
            "start": start,
            "stop": stop,
            "prop_direction": "xyz"[axis],
            "exc_direction": "xyz"[exc_i],
            "direction": "xyz"[axis],
            "metal": trace_name,
            "reverse": bool(reverse),
            "wg_a": 0.0,
            "wg_b": 0.0,
        }
    return None


def _extend_zero_length_waveguide(
    port: PortDef, primitives: list[dict[str, Any]]
) -> bool:
    """Give a truly zero-thickness hollow-waveguide port a small length.

    CST port planes are zero-thickness.  openEMS rejects a zero length in the
    excitation direction; for a non-planar (true hollow-waveguide) port we add
    a small, inward-pointing length instead of failing the whole run.
    """
    axis = "xyz".find(str(port.direction or ""))
    if axis < 0:
        return False
    try:
        length = abs(float(port.stop[axis]) - float(port.start[axis]))
    except (TypeError, ValueError, IndexError):
        return False
    if length > 1e-7:
        return False
    extents = [
        abs(float(port.stop[i]) - float(port.start[i]))
        for i in range(3)
        if i != axis
    ]
    positive = [value for value in extents if value > 1e-9]
    thickness = max(1e-3, min(positive) * 0.02) if positive else 0.1
    lows: list[float] = []
    highs: list[float] = []
    for prim in primitives:
        pb = _primitive_bounds(prim)
        if pb is not None:
            lows.append(pb[0][axis])
            highs.append(pb[1][axis])
    center = 0.5 * (min(lows) + max(highs)) if lows else float(port.start[axis])
    sign = 1.0 if float(port.start[axis]) <= center else -1.0
    port.stop[axis] = float(port.start[axis]) + sign * thickness
    return True


def _apply_calculation_box_margins(
    result: CstImportResult, inspection: CstInspection, target_factor: float
) -> None:
    """Translate the CST calculation box into per-side air margins."""
    if (
        inspection.calculation_box_min is None
        or inspection.calculation_box_max is None
        or not result.primitives
    ):
        return
    lows = [float("inf")] * 3
    highs = [float("-inf")] * 3
    for prim in result.primitives:
        bounds = _primitive_bounds(prim)
        if bounds is None:
            continue
        for i in range(3):
            lows[i] = min(lows[i], bounds[0][i])
            highs[i] = max(highs[i], bounds[1][i])
    if not all(math.isfinite(v) for v in lows + highs):
        return
    factor = 1.0 / float(target_factor)
    dom_lo = [float(v) * factor for v in inspection.calculation_box_min]
    dom_hi = [float(v) * factor for v in inspection.calculation_box_max]
    tol = 1e-6 * max(1.0, max(abs(v) for v in dom_lo + dom_hi))
    margins = [
        max(0.0, lows[0] - dom_lo[0]),
        max(0.0, dom_hi[0] - highs[0]),
        max(0.0, lows[1] - dom_lo[1]),
        max(0.0, dom_hi[1] - highs[1]),
        max(0.0, lows[2] - dom_lo[2]),
        max(0.0, dom_hi[2] - highs[2]),
    ]
    result.air_margin = [
        0.0 if float(v) < tol else float(v) for v in margins
    ]
    if any(
        lows[i] < dom_lo[i] - tol or highs[i] > dom_hi[i] + tol
        for i in range(3)
    ):
        result.warnings.append(
            "CST calculation domain is smaller than the imported geometry on "
            "at least one side; the negative margin was clamped to 0. Check "
            "the CST export and the geometry."
        )


def _open_msl_port_boundaries(project) -> dict[str, int]:
    """Turn PEC/PMC faces behind imported zero-length MSL ports into MUR.

    CST waveguide ports may sit on a closed PEC calculation-box face; in CST
    that plane is the waveguide port itself.  openEMS's ``AddMSLPort`` instead
    treats the domain face as a metal wall and shorts the port.  The openEMS
    MSL tutorial therefore uses an open/absorbing boundary on the port sides.
    Only the zero-length imported MSL ports are considered, and only when they
    actually sit on the FDTD domain edge (margin zero).
    """
    prims = list(getattr(project, "primitives", None) or [])
    if not prims:
        return {}
    lows = [float("inf")] * 3
    highs = [float("-inf")] * 3
    for prim in prims:
        bounds = _primitive_bounds(prim)
        if bounds is None:
            continue
        for i in range(3):
            lows[i] = min(lows[i], bounds[0][i])
            highs[i] = max(highs[i], bounds[1][i])
    if not all(math.isfinite(v) for v in lows + highs):
        return {}
    boundary = getattr(project, "boundary", None)
    if boundary is None:
        return {}
    air = list(getattr(boundary, "air_margin", []) or [])
    if len(air) != 6 or any(isinstance(v, str) for v in air):
        return {}
    margins = [float(v) for v in air]
    dom_lo = [lows[i] - margins[2 * i] for i in range(3)]
    dom_hi = [highs[i] + margins[2 * i + 1] for i in range(3)]
    tol = 1e-6 * max(1.0, max(abs(v) for v in dom_lo + dom_hi))
    changed: dict[str, int] = {}
    for port in getattr(project, "ports", []) or []:
        if str(getattr(port, "type", "")) != "msl":
            continue
        prop = str(getattr(port, "prop_direction", "") or "").lower()
        if prop not in ("x", "y", "z"):
            ext = [
                abs(float(port.stop[i]) - float(port.start[i]))
                for i in range(3)
            ]
            prop = "xyz"[max(range(3), key=lambda i: ext[i])]
        axis = "xyz".index(prop)
        if abs(float(port.stop[axis]) - float(port.start[axis])) > tol:
            continue
        coord = float(port.start[axis])
        side = None
        if abs(coord - dom_lo[axis]) <= tol:
            side = f"{prop}min"
        elif abs(coord - dom_hi[axis]) <= tol:
            side = f"{prop}max"
        if side is None:
            continue
        if str(getattr(boundary, side, "")) in ("PEC", "PMC"):
            setattr(boundary, side, "MUR")
            changed[side] = changed.get(side, 0) + 1
    return changed


def _add_msl_port_extensions(
    result: CstImportResult, inspection: CstInspection, target_factor: float
) -> None:
    """Add a matched line section outside CST MSL ports on a box boundary.

    CST waveguide ports at the calculation-box edge include their own absorbing
    port plane.  openEMS's ``MSLPort`` does not: it observes the line at the
    port plane, so a PCB that simply ends at a PEC/MUR/PML boundary behaves
    like an open-circuited microstrip and traps energy.  Extend the trace and
    substrate by a short matched section and put an absorbing boundary at its
    outer end.  The original CST port plane remains the reference plane.
    """
    if not result.ports or not result.primitives:
        return
    material_kinds = {
        str(m.get("name", "")).strip().lower(): str(m.get("kind", ""))
        for m in result.materials
    }
    lows = [float("inf")] * 3
    highs = [float("-inf")] * 3
    bounds: list[tuple[dict, list[list[float]]]] = []
    for prim in result.primitives:
        b = _primitive_bounds(prim)
        if b is None:
            continue
        bounds.append((prim, b))
        for i in range(3):
            lows[i] = min(lows[i], b[0][i])
            highs[i] = max(highs[i], b[1][i])
    if not all(math.isfinite(v) for v in lows + highs):
        return
    tol = 1e-6 * max(1.0, max(abs(v) for v in lows + highs))

    def _kind(prim: dict) -> str:
        return material_kinds.get(str(prim.get("material", "")).lower(), "")

    made: list[str] = []
    for port in result.ports:
        if str(getattr(port, "type", "")) != "msl":
            continue
        prop = str(getattr(port, "prop_direction", "") or "").lower()
        if prop not in ("x", "y", "z"):
            continue
        axis = "xyz".index(prop)
        plane = float(port.start[axis])
        if abs(plane - lows[axis]) <= tol:
            outward = -1.0
        elif abs(plane - highs[axis]) <= tol:
            outward = +1.0
        else:
            continue
        transverse = [i for i in range(3) if i != axis]
        exc_axis = "xyz".index(
            str(getattr(port, "exc_direction", "") or "z").lower()
        ) if str(getattr(port, "exc_direction", "") or "z").lower() in ("x", "y", "z") else 2
        width_axis = next((i for i in transverse if i != exc_axis), transverse[0])
        width_lo = min(float(port.start[width_axis]), float(port.stop[width_axis]))
        width_hi = max(float(port.start[width_axis]), float(port.stop[width_axis]))
        trace_width = width_hi - width_lo
        if trace_width <= tol:
            trace_width = 2.0 / float(target_factor or 1.0)
        # Short matched continuation requested for CST edge ports: one trace
        # width of copper, two trace widths of substrate from the port plane.
        # If the imported CST box already has a larger margin, keep the
        # substrate running to that boundary instead of ending it early.
        trace_len = 1.0 * trace_width
        substrate_len = 2.0 * trace_width
        existing_margin = 0.0
        if len(result.air_margin) == 6:
            try:
                existing_margin = max(
                    0.0, float(result.air_margin[axis * 2 + (1 if outward > 0 else 0)])
                )
            except (TypeError, ValueError):
                existing_margin = 0.0
        substrate_len = max(substrate_len, existing_margin)
        # Prefer the feed box sitting on the port plane and overlapping the
        # port width in the transverse direction.
        trace: tuple[dict, list[list[float]]] | None = None
        for prim, b in bounds:
            kind = _kind(prim)
            if kind in ("dielectric", "vacuum", "conducting_sheet"):
                continue
            at_plane = abs(b[0][axis] - plane) <= tol or abs(b[1][axis] - plane) <= tol
            overlaps_width = (
                b[0][width_axis] < width_hi - tol
                and b[1][width_axis] > width_lo + tol
            )
            if at_plane and overlaps_width:
                if trace is None or (
                    (b[1][width_axis] - b[0][width_axis])
                    < (trace[1][1][width_axis] - trace[1][0][width_axis])
                ):
                    trace = (prim, b)
        substrate: tuple[dict, list[list[float]]] | None = None
        for prim, b in bounds:
            if _kind(prim) != "dielectric":
                continue
            overlaps_width = (
                b[0][width_axis] < width_hi - tol
                and b[1][width_axis] > width_lo + tol
            )
            at_plane = abs(b[0][axis] - plane) <= tol or abs(b[1][axis] - plane) <= tol
            if at_plane and overlaps_width:
                if substrate is None or (
                    (b[1][0] - b[0][0]) * (b[1][1] - b[0][1])
                    > (substrate[1][1][0] - substrate[1][0][0])
                    * (substrate[1][1][1] - substrate[1][0][1])
                ):
                    substrate = (prim, b)
        if trace is None or substrate is None:
            result.warnings.append(
                f"MSL port {port.name!r} is on the box edge, but no feed/substrate "
                "could be identified for a matched port extension; the port may "
                "see an open circuit."
            )
            continue
        for label, (src, b), material, length in (
            ("trace", trace, trace[0].get("material", "copper"), trace_len),
            (
                "substrate",
                substrate,
                substrate[0].get("material", "substrate"),
                substrate_len,
            ),
        ):
            outer = plane + outward * length
            ext = {
                "kind": "box",
                "name": f"port_ext_{label}_{port.name}",
                "material": material,
                "priority": int(src.get("priority", 10)),
                "start": list(b[0]),
                "stop": list(b[1]),
                "source_format": "cst-port-extension",
                "source_file": getattr(inspection, "source_path", ""),
            }
            if outward > 0:
                ext["start"][axis] = plane
                ext["stop"][axis] = outer
            else:
                ext["start"][axis] = outer
                ext["stop"][axis] = plane
            result.primitives.append(ext)
            made.append(ext["name"])
        idx = axis * 2 + (1 if outward > 0 else 0)
        if len(result.air_margin) == 6:
            result.air_margin[idx] = max(
                float(result.air_margin[idx]), substrate_len
            )
        side = f"{prop}{'max' if outward > 0 else 'min'}"
        if side in result.boundaries:
            result.boundaries[side] = "PML"
        result.warnings.append(
            f"MSL port {port.name!r} on the {side.upper()} box edge: added "
            f"{trace_len / trace_width:g}x trace-width line extension and a "
            f"substrate extension to {substrate_len / trace_width:g}x trace "
            "width; that side is PML. The port reference plane stays at the "
            "CST PCB edge."
        )


def _infer_imported_port_geometry(
    result: CstImportResult, inspection: CstInspection
) -> None:
    material_kinds = {
        material.name.strip().lower(): material.kind
        for material in inspection.materials
    }
    for port in result.ports:
        if not isinstance(port, PortDef) or port.type != "rect_waveguide":
            continue
        converted = _planar_msl_from_geometry(
            port, result.primitives, material_kinds
        )
        if converted is not None:
            for key, value in converted.items():
                setattr(port, key, value)
            result.warnings.append(
                f"CST waveguide port {port.name!r} lies on a planar substrate; "
                "imported as an MSL port with the feed trace as signal and the "
                "substrate face as ground. Review the port geometry after import."
            )
            continue
        if _extend_zero_length_waveguide(port, result.primitives):
            result.warnings.append(
                f"CST waveguide port {port.name!r} had zero length in its "
                "excitation direction; extended inward by a small length for "
                "openEMS. Check the port box and reference plane."
            )


def import_cst_package(
    inspection: CstInspection,
    options: CstImportOptions | None = None,
    out_dir: str | Path | None = None,
) -> CstImportResult:
    """Convert inspected STL objects into project primitives.

    ``out_dir`` receives scaled ``.npy`` and ``.stl`` copies, so the imported
    project stays usable when the original package is moved or deleted.
    """
    opts = options or CstImportOptions()
    result = CstImportResult(
        materials=[m.as_dict() for m in inspection.materials],
        frequency=inspection.frequency,
        boundaries=dict(inspection.boundaries),
        pml_size=int(inspection.pml_size),
        target_unit=str(opts.target_unit or "mm"),
        warnings=list(inspection.warnings),
    )
    if not inspection.ok and not inspection.objects:
        result.warnings.append("No CST objects to import.")
        return result

    try:
        target_factor = mm_per_unit(opts.target_unit)
    except ValueError as exc:
        result.warnings.append(f"{exc}; using mm.")
        opts.target_unit = "mm"
        target_factor = 1.0
    result.target_unit = str(opts.target_unit or "mm")
    source_mm = _length_to_mm(1.0, inspection.geometry_unit)
    scale = source_mm / target_factor
    root = Path(inspection.root)
    target = Path(out_dir) if out_dir else root / "imported_meshes"
    target.mkdir(parents=True, exist_ok=True)

    taken_names: set[str] = set()
    taken_files: set[str] = set()
    for obj in inspection.objects:
        if obj.kind == "sheet" and not opts.include_sheets:
            result.warnings.append(f"Skipped sheet object {obj.name!r} (option off).")
            continue
        if not obj.exists:
            continue
        mesh_path = Path(obj.file)
        if not mesh_path.is_file():
            result.warnings.append(f"Mesh file missing for {obj.name!r}: {mesh_path}")
            continue
        if mesh_path.suffix.lower() not in MESH_SUFFIXES:
            result.warnings.append(
                f"Skipped {obj.name!r}: {mesh_path.suffix} is not supported "
                "(Variant A imports STL/OBJ)."
            )
            continue
        try:
            from app.freecad_io.freecad_mesh import load_stl_or_obj_triangles

            tris = load_stl_or_obj_triangles(mesh_path)
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"Could not read mesh for {obj.name!r}: {exc}")
            continue
        if tris is None or len(tris) == 0:
            result.warnings.append(f"No triangles in mesh for {obj.name!r}.")
            continue
        tris = np.asarray(tris, dtype=np.float64)
        if tris.ndim != 3 or tris.shape[1:] != (3, 3):
            result.warnings.append(
                f"Mesh for {obj.name!r} is not a triangle array "
                f"(shape {tris.shape}); skipped."
            )
            continue
        if opts.flip_y:
            tris = tris.copy()
            tris[:, :, 1] *= -1.0
            # A reflection flips the winding; restore outward-facing triangles.
            tris = tris[:, [0, 2, 1], :]
        if abs(scale - 1.0) > 1e-15:
            tris *= scale

        name = _unique_name(obj.name, taken_names)
        if name != obj.name:
            result.warnings.append(
                f"Duplicate object name {obj.name!r}; imported as {name!r}."
            )
        stem = _safe_file_stem(name, taken_files)
        npy_path = target / f"{stem}.npy"
        stl_path = target / f"{stem}.stl"
        try:
            np.save(str(npy_path), tris)
            from app.csxcad_builder.triangle_mesh import write_binary_stl

            write_binary_stl(stl_path, tris)
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"Could not write mesh copy for {name!r}: {exc}")
            continue

        pts = tris.reshape(-1, 3)
        bmin = pts.min(axis=0).tolist()
        bmax = pts.max(axis=0).tolist()
        prim: dict[str, Any] = {
            "kind": "stl",
            "name": name,
            "material": obj.material or "copper",
            "priority": int(obj.priority),
            "path": str(npy_path),
            "mesh_path": str(npy_path),
            "stl_path": str(stl_path),
            "start": [float(v) for v in bmin],
            "stop": [float(v) for v in bmax],
            "source_format": "cst",
            "source_file": inspection.source_path,
            "source_object": obj.name,
            "object_kind": obj.kind,
        }
        if obj.component:
            prim["component"] = obj.component
        result.primitives.append(prim)
        result.object_names.append(name)

    _apply_calculation_box_margins(result, inspection, target_factor)

    if opts.include_ports and inspection.ports:
        # Inspection ports are already normalized to mm.
        port_factor = 1.0 / target_factor
        for port in inspection.ports:
            result.ports.append(
                PortDef(
                    name=port.name,
                    type=port.type,
                    start=[float(v) * port_factor for v in port.start],
                    stop=[float(v) * port_factor for v in port.stop],
                    resistance=float(port.resistance),
                    inductance=float(port.inductance),
                    capacitance=float(port.capacitance),
                    direction=port.direction,
                    excite_port=bool(port.excite_port),
                    active=bool(port.active),
                    prop_direction=port.prop_direction,
                    exc_direction=port.exc_direction,
                    mesh_dirs=port.mesh_dirs,
                    lumped_width=port.lumped_width,
                    wg_a=float(port.wg_a) * port_factor,
                    wg_b=float(port.wg_b) * port_factor,
                    wg_mode=port.wg_mode,
                    reverse=bool(port.reverse),
                )
            )
        _infer_imported_port_geometry(result, inspection)
        _add_msl_port_extensions(result, inspection, target_factor)

    result.ok = bool(result.primitives)
    if not result.ok and not result.warnings:
        result.warnings.append("No usable CST geometry could be imported.")
    return result


def apply_cst_to_project(project, result: CstImportResult, source: str | Path, spec=None) -> dict[str, int]:
    """Merge a CST import into ``project`` (re-import safe).

    Primitives from the same source are replaced, while manual material and
    priority changes are carried over by object name.  Materials, ports,
    frequency and boundaries are merged according to the import options.
    """
    from app.models.project import CstSpec, GeometryFile, MaterialDef, PortDef

    source = _source_key(source)
    if spec is None:
        spec = getattr(project, "cst", None) or CstSpec()
    if not isinstance(spec, CstSpec):
        spec = CstSpec()

    old = list(getattr(project, "primitives", None) or [])
    replaced = [
        p for p in old if _source_key(p.get("source_file", "")) == source
    ]
    kept = [
        p for p in old if _source_key(p.get("source_file", "")) != source
    ]
    overrides = {
        str(p.get("name")): (p.get("material"), int(p.get("priority", 10)))
        for p in replaced
        if p.get("name")
    }

    added: list[dict[str, Any]] = []
    for raw in result.primitives:
        prim = dict(raw)
        prim["source_file"] = source
        prim["source_format"] = "cst"
        override = overrides.get(str(prim.get("name")))
        if override is not None:
            material, priority = override
            if material:
                prim["material"] = material
            prim["priority"] = priority
        added.append(prim)
    project.primitives = kept + added

    def ensure_material(name: str, kind: str, **kwargs):
        existing = next(
            (m for m in project.materials if m.name.lower() == name.lower()),
            None,
        )
        if existing is None:
            existing = MaterialDef(name=name, kind=kind, **kwargs)
            project.materials.append(existing)
        else:
            existing.kind = kind
            for key, value in kwargs.items():
                setattr(existing, key, value)
        return existing

    material_added = 0
    if spec.include_materials:
        before = {m.name for m in project.materials}
        for mat in result.materials:
            name = str(mat.get("name", "")).strip()
            if not name:
                continue
            kwargs = {
                key: mat[key]
                for key in ("epsilon", "permeability", "kappa", "tand", "color")
                if key in mat
            }
            ensure_material(name, str(mat.get("kind", "dielectric")), **kwargs)
        material_added = len({m.name for m in project.materials} - before)

    # If material import was disabled (or a manifest had no definition), never
    # leave primitives pointing at a missing material.  Match case-insensitively
    # but write the project's actual material spelling into the primitive.
    known = {m.name.lower(): m.name for m in project.materials}
    fallback_used = False
    for prim in added:
        name = str(prim.get("material") or "").strip()
        if not name:
            prim["material"] = "copper"
            fallback_used = True
            continue
        actual = known.get(name.lower())
        if actual is None:
            prim["material"] = "copper"
            fallback_used = True
        else:
            prim["material"] = actual
    if fallback_used and "copper" not in known:
        ensure_material("copper", "pec", color=(0.85, 0.55, 0.2, 1.0))

    if spec.include_frequency and result.frequency is not None:
        start = float(result.frequency.start_hz)
        stop = float(result.frequency.stop_hz)
        if start >= 0.0 and stop > start:
            project.mesh.start_frequency = start
            project.mesh.stop_frequency = stop
            target_f = getattr(project.mesh, "target_frequency", None)
            if target_f is None or not (start <= float(target_f) <= stop):
                project.mesh.target_frequency = 0.5 * (start + stop)

    if spec.include_boundaries:
        valid = {"PEC", "PMC", "MUR", "PML"}
        for side, kind in (result.boundaries or {}).items():
            if side in BOUNDARY_SIDES and kind in valid:
                setattr(project.boundary, side, kind)
        if result.pml_size > 0:
            project.boundary.pml_size = int(result.pml_size)
        if len(result.air_margin) == 6:
            project.boundary.air_margin = [
                float(v) for v in result.air_margin
            ]

    ports_added = 0
    ports_replaced = 0
    imported_port_names: list[str] = []
    if spec.include_ports and result.ports:
        previous = (
            set(spec.port_names)
            if _source_key(spec.source) == source
            else set()
        )
        if previous:
            before = len(project.ports)
            project.ports = [p for p in project.ports if p.name not in previous]
            ports_replaced = before - len(project.ports)
        existing = {str(p.name).lower() for p in project.ports}
        for port in result.ports:
            if not isinstance(port, PortDef):
                continue
            lowered = str(port.name).lower()
            if lowered in existing:
                # A manually created port with the same name wins.  Re-imports
                # replace their own ports above, so this is normally a real
                # conflict rather than a duplicate from this package.
                result.warnings.append(
                    f"Port {port.name!r} already exists; imported port skipped."
                )
                continue
            project.ports.append(port)
            existing.add(lowered)
            imported_port_names.append(port.name)
            ports_added += 1
    elif spec.include_ports:
        # A package without ports should not silently keep old imported ports
        # from the previous version of the same package.
        if _source_key(spec.source) == source and spec.port_names:
            previous = set(spec.port_names)
            before = len(project.ports)
            project.ports = [p for p in project.ports if p.name not in previous]
            ports_replaced = before - len(project.ports)
    spec.port_names = imported_port_names

    if spec.include_ports and spec.include_boundaries:
        opened = _open_msl_port_boundaries(project)
        for side, count in sorted(opened.items()):
            result.warnings.append(
                f"{count} MSL port(s) at the {side.upper()} PEC/PMC boundary: "
                f"set {side} to MUR with 0 mm air margin (the MSL port plane "
                "must stay on the substrate edge; CST waveguide ports do not "
                "short at a PEC calculation-box face)."
            )

    geometry_file = GeometryFile(
        path=source,
        format="cst",
        unit=str(
            getattr(result, "target_unit", "")
            or getattr(getattr(project, "mesh", None), "drawing_unit", "mm")
            or "mm"
        ),
        object_names=list(result.object_names),
    )
    project.geometry_files = [
        gf for gf in project.geometry_files if _source_key(gf.path) != source
    ] + [geometry_file]

    spec.source = source
    project.cst = spec

    return {
        "added": len(added),
        "replaced": len(replaced),
        "kept": len(kept),
        "materials_added": material_added,
        "ports_added": ports_added,
        "ports_replaced": ports_replaced,
    }


__all__ = [
    "CST_MANIFEST_NAMES",
    "CstFrequency",
    "CstImportOptions",
    "CstImportResult",
    "CstInspection",
    "CstMaterial",
    "CstObject",
    "CstPort",
    "apply_cst_to_project",
    "import_cst_package",
    "inspect_cst_package",
]
