#!/usr/bin/env python3
"""Half-wave dipole FreeCAD model with openEMS PORT markers (2.45 GHz).

Coordinates (mm):
  X — dipole axis / E-field
  Y — perpendicular
  Z — up

Named solids:
  dipole_left, dipole_right   (copper cylinders, gap at x=0)

Port marks (GUI auto-import):
  PORT_in1_start / PORT_in1_stop  — lumped port across the feed gap (x)
  Document property openems_ports — same port as JSON backup
  Custom props on dipole_left     — openems_port_* (demo, same name in1)

Run with FreeCADCmd:
  set DIPOLE_OUT_DIR=C:\\...\\work\\dipole_ports
  FreeCADCmd examples\\freecad_dipole_ports.py
  # or: python examples/run_dipole_ports_example.py

Outputs next to DIPOLE_OUT_DIR (default work/dipole_ports):
  dipole_2p45.FCStd, dipole_2p45.step, stl/, openems_ports.json,
  openems_export.json, dipole_params.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# --- design (mm) -------------------------------------------------------------
FREQ_HZ = 2.45e9
C0_M_S = 299_792_458.0
LAMBDA_MM = C0_M_S / FREQ_HZ * 1e3  # ≈ 122.364 mm

DIP_LEN = 0.475 * LAMBDA_MM  # total tip-to-tip (includes gap)
GAP = 2.0  # feed gap [mm]
WIRE_R = 1.0  # radius [mm]
HALF = (DIP_LEN - GAP) / 2.0
LEFT_X0 = -DIP_LEN / 2.0
LEFT_X1 = LEFT_X0 + HALF
RIGHT_X0 = GAP / 2.0
RIGHT_X1 = RIGHT_X0 + HALF

# Port across the gap (direction +X)
PORT_NAME = "in1"
PORT_START = (-GAP / 2.0, 0.0, 0.0)
PORT_STOP = (GAP / 2.0, 0.0, 0.0)
PORT_R = 50.0
PORT_TYPE = "lumped"

PARAMS = {
    "frequency_hz": FREQ_HZ,
    "lambda_mm": round(LAMBDA_MM, 4),
    "dipole_length_mm": round(DIP_LEN, 4),
    "wire_radius_mm": WIRE_R,
    "gap_mm": GAP,
    "left": [round(LEFT_X0, 4), round(LEFT_X1, 4)],
    "right": [round(RIGHT_X0, 4), round(RIGHT_X1, 4)],
    "port": {
        "name": PORT_NAME,
        "type": PORT_TYPE,
        "start": list(PORT_START),
        "stop": list(PORT_STOP),
        "resistance": PORT_R,
        "direction": "x",
        "excite_port": True,
        "source": "PORT_*_points + openems_ports doc + props",
    },
    "axis": {"dipole": "X", "up": "Z"},
    "polarization": "linear X",
}


def out_dir() -> Path:
    env = os.environ.get("DIPOLE_OUT_DIR")
    if env:
        return Path(env)
    try:
        return Path(__file__).resolve().parent / "dipole_ports"
    except NameError:
        return Path.cwd() / "dipole_ports"


def _add_feature(doc, name: str, shape, color=(200, 140, 60)):
    o = doc.addObject("Part::Feature", name)
    o.Label = name
    o.Shape = shape
    try:
        o.ViewObject.ShapeColor = color
    except Exception:
        pass
    return o


def _add_port_point(doc, kind: str, xyz):
    import FreeCAD as App
    import Part

    name = f"PORT_{PORT_NAME}_{kind}"
    vec = App.Vector(*xyz)
    try:
        v = Part.Vertex(vec)
    except Exception:
        # Older/newer FreeCAD: build via Part::Feature + Placement
        o0 = doc.addObject("Part::Feature", name)
        o0.Label = name
        o0.Placement = App.Placement(vec, App.Rotation())
        # empty shape fallback — Placement still read by extractor if Shape empty
        try:
            o0.Shape = Part.makeBox(0.001, 0.001, 0.001, vec)
        except Exception:
            pass
        return o0
    o = doc.addObject("Part::Feature", name)
    o.Label = name
    o.Shape = v
    try:
        o.ViewObject.ShapeColor = (40, 200, 80)
    except Exception:
        pass
    return o


def _add_openems_props(obj):
    """Custom properties on a solid (demo of Variante B in FREECAD_PORTS_DE.md)."""
    props = [
        ("openems_port_name", "App::PropertyString", PORT_NAME),
        ("openems_port_type", "App::PropertyString", PORT_TYPE),
        ("openems_port_start", "App::PropertyString", f"{PORT_START[0]}, {PORT_START[1]}, {PORT_START[2]}"),
        ("openems_port_stop", "App::PropertyString", f"{PORT_STOP[0]}, {PORT_STOP[1]}, {PORT_STOP[2]}"),
        ("openems_port_r", "App::PropertyFloat", float(PORT_R)),
        ("openems_port_excite", "App::PropertyBool", True),
    ]
    for pname, ptype, default in props:
        if not hasattr(obj, pname):
            try:
                obj.addProperty(ptype, pname, "openEMS", "openEMS port mark")
                setattr(obj, pname, default)
            except Exception as e:
                print(f"addProperty {pname} failed on {obj.Name}: {e}")


def build_document():
    import FreeCAD as App
    import Part

    doc = App.newDocument("Dipole_2p45_ports")
    copper = (200, 140, 60)

    left = _add_feature(
        doc,
        "dipole_left",
        Part.makeCylinder(
            WIRE_R,
            HALF,
            App.Vector(LEFT_X0, 0.0, 0.0),
            App.Vector(1.0, 0.0, 0.0),
        ),
        copper,
    )
    right = _add_feature(
        doc,
        "dipole_right",
        Part.makeCylinder(
            WIRE_R,
            HALF,
            App.Vector(RIGHT_X0, 0.0, 0.0),
            App.Vector(1.0, 0.0, 0.0),
        ),
        copper,
    )

    # Port marks
    pts = [
        _add_port_point(doc, "start", PORT_START),
        _add_port_point(doc, "stop", PORT_STOP),
    ]

    # Document JSON backup (Variante C)
    ports_json = [
        {
            "name": PORT_NAME,
            "type": PORT_TYPE,
            "start": list(PORT_START),
            "stop": list(PORT_STOP),
            "resistance": PORT_R,
            "direction": "x",
            "excite_port": True,
        }
    ]
    try:
        doc.addProperty(
            "App::PropertyString", "openems_ports", "openEMS", "openEMS ports JSON"
        )
        doc.openems_ports = json.dumps(ports_json)
    except Exception as e:
        print("doc openems_ports property failed:", e)

    # Custom props on left arm (same port name — extractor merges, no duplicate)
    _add_openems_props(left)

    doc.recompute()
    return doc, [left, right], pts, ports_json


def write_export_manifest(out: Path, base: str, objects: list, ports_json: list) -> Path:
    """openems_export.json so GUI/manifest path can find ports without FreeCAD."""
    manifest = {
        "name": base,
        "objects": [
            {"name": o.Name, "label": o.Label, "kind": "solid"} for o in objects
        ],
        "ports": ports_json,
        "frequency_hz": FREQ_HZ,
        "note": "Generated by examples/freecad_dipole_ports.py",
    }
    path = out / "openems_export.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def main() -> int:
    import FreeCAD as App
    import Mesh
    import Part
    import Import

    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    stl_dir = out / "stl"
    stl_dir.mkdir(parents=True, exist_ok=True)

    doc, solids, pts, ports_json = build_document()

    fcstd = out / "dipole_2p45.FCStd"
    step = out / "dipole_2p45.step"
    stl_all = out / "dipole_2p45.stl"
    params_json = out / "dipole_params.json"
    ports_file = out / "openems_ports.json"

    doc.saveAs(str(fcstd))

    try:
        # STEP without PORT vertices (geometry archive only)
        Import.export([solids[0], solids[1]], str(step))
    except Exception as e:
        print("STEP export failed:", e)
        try:
            Part.export([solids[0], solids[1]], str(step))
        except Exception as e2:
            print("Part.export STEP failed:", e2)

    try:
        Mesh.export(solids, str(stl_all))
    except Exception as e:
        print("Combined STL export failed:", e)

    for o in solids:
        stl_i = stl_dir / f"{o.Name}.stl"
        try:
            Mesh.export([o], str(stl_i))
        except Exception as e:
            print(f"STL export {o.Name} failed:", e)

    params_json.write_text(json.dumps(PARAMS, indent=2), encoding="utf-8")
    ports_file.write_text(json.dumps(ports_json, indent=2), encoding="utf-8")
    write_export_manifest(out, "dipole_2p45", solids, ports_json)

    print("Dipole FreeCAD model written:")
    print("  ", fcstd)
    print("  ", step)
    print("  ", stl_all)
    print("  ", params_json)
    print("  ", ports_file)
    print("  ", out / "openems_export.json")
    print(
        f"  port {PORT_NAME}: {PORT_START} -> {PORT_STOP} "
        f"R={PORT_R} dir=x @ {FREQ_HZ / 1e9:.3f} GHz"
    )
    print(f"  lambda={LAMBDA_MM:.3f} mm  dipole={DIP_LEN:.3f} mm  gap={GAP} mm")
    print("GUI: Import Geometry on the FCStd (or FreeCAD... Export CAD... on this file) - port auto-added.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print("DIPOLE MODEL ERROR:", e)
        raise
