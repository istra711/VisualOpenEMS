"""FreeCAD port marks → openEMS PortDef.

Marking options in FreeCAD (FCStd keeps these; STEP usually does not):
1) Helper points: ``PORT_<name>_start`` and ``PORT_<name>_stop``
   (Part::Vertex / Draft Point — Label or Name).
2) Custom properties on a solid/object:
   - openems_port_name (string, optional)
   - openems_port_type (lumped|msl|rect_waveguide)
   - openems_port_start / openems_port_stop (vector or [x,y,z])
   - openems_port_r or openems_port_resistance (ohm)
   - openems_port_excite (bool, default True)
3) Document property ``openems_ports``: JSON list of port dicts.

Export writes ``ports`` into openems_export.json / tessellation JSON.
GUI Import creates PortDef entries automatically.
"""

from __future__ import annotations

import json
from typing import Any

from app.models.project import PortDef, PortType

PORT_OBJ_PREFIX = "PORT_"
DOC_PROP_PORTS = "openems_ports"
VALID_TYPES = {"lumped", "msl", "rect_waveguide"}
# Fallback when neither FreeCAD data nor settings provide a value
FALLBACK_PORT_DEFAULTS: dict[str, Any] = {
    "type": "lumped",
    "resistance": 50.0,
    "excite_port": True,
    "mesh_dirs": "",
    "direction": "",
}


def port_defaults_from(settings_like: Any) -> dict[str, Any]:
    """Extract defaults dict from AppSettings / dict / None."""
    if settings_like is None:
        return dict(FALLBACK_PORT_DEFAULTS)
    if isinstance(settings_like, dict):
        out = dict(FALLBACK_PORT_DEFAULTS)
        out.update({k: v for k, v in settings_like.items() if k in out})
        return out
    fn = getattr(settings_like, "port_defaults", None)
    if callable(fn):
        try:
            return port_defaults_from(fn())
        except Exception:  # noqa: BLE001
            pass
    out = dict(FALLBACK_PORT_DEFAULTS)
    for key in list(FALLBACK_PORT_DEFAULTS):
        val = getattr(settings_like, f"default_port_{key}", None)
        if key == "resistance":
            val = getattr(settings_like, "default_port_resistance", None)
        elif key == "type":
            val = getattr(settings_like, "default_port_type", None)
        elif key == "excite_port":
            val = getattr(settings_like, "default_port_excite", None)
        elif key == "mesh_dirs":
            val = getattr(settings_like, "default_port_mesh_dirs", None)
        elif key == "direction":
            val = getattr(settings_like, "default_port_direction", None)
        if val is not None and val != "":
            out[key] = val
    return out


def _vec3(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            return [float(value[0]), float(value[1]), float(value[2])]
        except (TypeError, ValueError):
            return None
    # FreeCAD.Vector
    try:
        return [float(value.x), float(value.y), float(value.z)]
    except Exception:  # noqa: BLE001
        return None


def infer_direction(start: list[float], stop: list[float]) -> str:
    d = [abs(float(stop[i]) - float(start[i])) for i in range(3)]
    return "xyz"[d.index(max(d))] if d else "z"


def normalize_port_dict(
    raw: dict[str, Any],
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Normalize one FreeCAD-exported port dict; return None if unusable.

    ``defaults`` fills fields the CAD/export omitted (Settings → port defaults).
    Explicit CAD values always win over defaults.
    """
    if not isinstance(raw, dict):
        return None
    start = _vec3(raw.get("start") or raw.get("openems_port_start"))
    stop = _vec3(raw.get("stop") or raw.get("openems_port_stop"))
    if start is None or stop is None:
        return None
    if start == stop:
        return None
    d = port_defaults_from(defaults)

    name = str(raw.get("name") or raw.get("openems_port_name") or "in1").strip() or "in1"

    ptype_raw = raw.get("type") or raw.get("openems_port_type")
    ptype = str(ptype_raw or d.get("type") or "lumped").strip().lower()
    if ptype not in VALID_TYPES:
        ptype = "lumped"

    r = raw.get("resistance")
    if r is None:
        r = raw.get("openems_port_r")
    if r is None:
        r = raw.get("openems_port_resistance")
    if r is None:
        r = d.get("resistance", 50.0)
    try:
        resistance = float(r)
    except (TypeError, ValueError):
        resistance = float(d.get("resistance", 50.0) or 50.0)

    if "excite_port" in raw:
        excite = raw["excite_port"]
    elif "openems_port_excite" in raw:
        excite = raw["openems_port_excite"]
    else:
        excite = d.get("excite_port", True)
    excite = bool(excite)

    direction = str(raw.get("direction") or "").strip()
    if not direction:
        direction = str(d.get("direction") or "").strip()
    if not direction:
        direction = infer_direction(start, stop)

    mesh_dirs = str(raw.get("mesh_dirs") or "").strip()
    if not mesh_dirs:
        mesh_dirs = str(d.get("mesh_dirs") or "").strip()
    if not mesh_dirs:
        mesh_dirs = "xy" if direction == "z" else "xyz"

    return {
        "name": name,
        "type": ptype,
        "start": start,
        "stop": stop,
        "resistance": resistance,
        "direction": direction,
        "excite_port": excite,
        "mesh_dirs": mesh_dirs,
        "source": str(raw.get("source") or "freecad"),
    }


def merge_named_points(
    named: dict[str, dict[str, Any]],
    raw_list: list[Any],
    defaults: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Merge PORT_*_start/stop points with property/doc port dicts."""
    d = port_defaults_from(defaults)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_list or []:
        nd = normalize_port_dict(item, d) if isinstance(item, dict) else None
        if nd is None:
            continue
        key = nd["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(nd)
    for name, pts in named.items():
        key = name.lower()
        if key in seen:
            continue
        start, stop = pts.get("start"), pts.get("stop")
        if not start or not stop:
            continue
        nd = normalize_port_dict(
            {
                "name": name,
                "start": start,
                "stop": stop,
                "type": pts.get("type"),
                "resistance": pts.get("resistance"),
                "excite_port": pts.get("excite_port"),
                "direction": pts.get("direction"),
                "mesh_dirs": pts.get("mesh_dirs"),
                "source": "PORT_*_points",
            },
            d,
        )
        if nd is None:
            continue
        seen.add(key)
        out.append(nd)
    return out


def parse_ports_payload(
    payload: Any,
    defaults: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Accept list of ports or dict {"objects":…, "ports":[…]} / {"ports":[…]}."""
    if payload is None:
        return []
    if isinstance(payload, dict):
        raw = payload.get("ports")
        named = payload.get("named_ports") or {}
        if isinstance(raw, dict):
            raw = [raw]
        return merge_named_points(
            named if isinstance(named, dict) else {}, raw or [], defaults
        )
    if isinstance(payload, list):
        return merge_named_points({}, payload, defaults)
    if isinstance(payload, str):
        try:
            return parse_ports_payload(json.loads(payload), defaults)
        except Exception:  # noqa: BLE001
            return []
    return []


def ports_to_port_defs(
    ports: list[dict[str, Any]],
    defaults: dict[str, Any] | Any | None = None,
) -> list[PortDef]:
    """Convert port dicts to PortDef, applying Settings port defaults."""
    d = port_defaults_from(defaults)
    defs: list[PortDef] = []
    for raw in ports or []:
        nd = normalize_port_dict(raw, d)
        if nd is None:
            continue
        defs.append(
            PortDef(
                name=nd["name"],
                type=nd["type"],  # type: ignore[arg-type]
                start=list(nd["start"]),
                stop=list(nd["stop"]),
                resistance=float(nd["resistance"]),
                direction=str(nd["direction"]),
                excite_port=bool(nd["excite_port"]),
                mesh_dirs=str(nd["mesh_dirs"]),
            )
        )
    return defs


def find_manifest_ports(
    manifest_path: str,
    defaults: dict[str, Any] | Any | None = None,
) -> list[dict[str, Any]]:
    """Read ports from an openems_export.json next to an export/STEP file."""
    from pathlib import Path

    p = Path(manifest_path)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return parse_ports_payload(
        data if isinstance(data, dict) else {"ports": data}, defaults
    )


def freecad_extract_ports_code() -> str:
    """Python fragment for FreeCADCmd scripts (self-contained)."""
    return r'''
import json as _pm_json

def _pm_vec(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        try:
            return [float(v[0]), float(v[1]), float(v[2])]
        except Exception:
            return None
    try:
        return [float(v.x), float(v.y), float(v.z)]
    except Exception:
        return None

def _pm_norm(raw):
    if not isinstance(raw, dict):
        return None
    start = _pm_vec(raw.get("start") or raw.get("openems_port_start"))
    stop = _pm_vec(raw.get("stop") or raw.get("openems_port_stop"))
    if not start or not stop or start == stop:
        return None
    name = str(raw.get("name") or raw.get("openems_port_name") or "in1").strip() or "in1"
    # Only pass through fields the CAD actually set — GUI Settings port defaults fill the rest.
    out = {
        "name": name,
        "start": start,
        "stop": stop,
        "source": str(raw.get("source") or "freecad"),
    }
    ptype = raw.get("type") or raw.get("openems_port_type")
    if ptype:
        ptype = str(ptype).strip().lower()
        if ptype in ("lumped", "msl", "rect_waveguide"):
            out["type"] = ptype
    r = raw.get("resistance")
    if r is None:
        r = raw.get("openems_port_r")
    if r is None:
        r = raw.get("openems_port_resistance")
    if r is not None:
        try:
            out["resistance"] = float(r)
        except Exception:
            pass
    if "excite_port" in raw and raw.get("excite_port") is not None:
        out["excite_port"] = bool(raw.get("excite_port"))
    elif "openems_port_excite" in raw and raw.get("openems_port_excite") is not None:
        out["excite_port"] = bool(raw.get("openems_port_excite"))
    direction = str(raw.get("direction") or "").strip()
    if direction:
        out["direction"] = direction
    mesh_dirs = str(raw.get("mesh_dirs") or "").strip()
    if mesh_dirs:
        out["mesh_dirs"] = mesh_dirs
    return out

def extract_openems_ports(doc):
    """Collect ports from PORT_* points, object props, and doc property."""
    named = {}
    raw_list = []
    # 1) Document JSON property
    try:
        if hasattr(doc, "openems_ports"):
            val = getattr(doc, "openems_ports")
            if isinstance(val, str) and val.strip():
                data = json.loads(val)
                if isinstance(data, dict):
                    data = data.get("ports") or []
                if isinstance(data, list):
                    raw_list.extend(data)
    except Exception as e:
        print("openems_ports doc prop parse:", e)
    # 2) Objects: named points + custom properties
    for obj in doc.Objects:
        label = getattr(obj, "Label", None) or getattr(obj, "Name", "") or ""
        name = getattr(obj, "Name", "") or label
        # custom properties
        props = {}
        try:
            for pname in obj.PropertiesList:
                if str(pname).startswith("openems_port"):
                    props[str(pname)] = getattr(obj, pname)
        except Exception:
            props = {}
        # also pick up common props even if PropertiesList is incomplete (tests)
        for key in (
            "openems_port_name",
            "openems_port_type",
            "openems_port_start",
            "openems_port_stop",
            "openems_port_r",
            "openems_port_resistance",
            "openems_port_excite",
        ):
            if key not in props and hasattr(obj, key):
                try:
                    props[key] = getattr(obj, key)
                except Exception:
                    pass
        if props:
            props.setdefault("openems_port_name", label or name)
            props.setdefault("source", "freecad_props")
            raw_list.append(props)
        # PORT_<id>_start / PORT_<id>_stop via Label or Name
        for key in (label, name):
            if not key or not str(key).startswith("PORT_"):
                continue
            base = str(key)[5:]
            kind = ""
            pname = ""
            if base.endswith("_start"):
                kind = "start"
                pname = base[: -len("_start")]
            elif base.endswith("_stop"):
                kind = "stop"
                pname = base[: -len("_stop")]
            elif base.endswith("_end"):
                kind = "stop"
                pname = base[: -len("_end")]
            if not pname:
                continue
            shape = getattr(obj, "Shape", None)
            if shape is None:
                continue
            try:
                if hasattr(shape, "isNull") and shape.isNull():
                    continue
            except Exception:
                continue
            # vertex: use Shape.Vertexes[0] or Placement.Base
            pt = None
            try:
                if hasattr(shape, "Vertexes") and shape.Vertexes:
                    pt = shape.Vertexes[0].Point
                elif hasattr(shape, "CenterOfMass"):
                    pt = shape.CenterOfMass
                elif hasattr(obj, "Placement"):
                    pt = obj.Placement.Base
            except Exception:
                pt = None
            vec = _pm_vec(pt)
            if vec is None:
                continue
            slot = named.setdefault(pname, {})
            slot[kind] = vec
            slot.setdefault("source", "PORT_*_points")
    ports = []
    seen = set()
    for item in raw_list:
        nd = _pm_norm(item)
        if nd is None:
            continue
        key = nd["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        ports.append(nd)
    for pname, slot in named.items():
        key = pname.lower()
        if key in seen:
            continue
        if not slot.get("start") or not slot.get("stop"):
            continue
        nd = _pm_norm(
            {
                "name": pname,
                "start": slot.get("start"),
                "stop": slot.get("stop"),
                "type": slot.get("type"),
                "resistance": slot.get("resistance"),
                "excite_port": slot.get("excite_port"),
                "direction": slot.get("direction"),
                "mesh_dirs": slot.get("mesh_dirs"),
                "source": "PORT_*_points",
            }
        )
        if nd is None:
            continue
        seen.add(key)
        ports.append(nd)
    return ports
'''
