#!/usr/bin/env python3
"""Microstrip notch filter (PCB) FreeCAD model — 2.45 GHz, FR4 1.6 mm.

Named solids (GUI import + material assignment):
  ground     — ground plane, z = -0.25 .. 0      -> pec / copper, priority 90
  substrate  — FR4 board,   z = 0 .. 1.6         -> dielectric / fr4, priority 10
  msl_line   — 50 ohm strip, x = -32 .. 32       -> pec / copper, priority 100
  msl_stub   — open lambda_g/4 stub at the middle -> pec / copper, priority 100

Port marks (lumped 50 ohm edge ports, z from ground to strip):
  PORT_in1_start / PORT_in1_stop     at x = -30
  PORT_out1_start / PORT_out1_stop   at x = +30
Document property ``openems_ports`` carries the same ports as JSON (type,
direction, mesh_dirs, excite flags — the PORT_* points alone cannot express
the port direction).

Run with FreeCADCmd:
  FreeCADCmd make_pcb_filter_model.py
Outputs next to this script (or PCB_FILTER_OUT_DIR):
  pcb_filter.FCStd, pcb_filter.step, pcb_filter_params.json
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import FreeCAD as App
import Part

# --- design (mm, mirrors app.csxcad_builder.primitives.pcb_filter_primitives)
F0 = 2.45e9
FC = 2.15e9
C0 = 299_792_458.0

LINE_WIDTH = 3.0
LINE_HALF = 32.0
PORT_OFFSET = 30.0
SUB_EPS = 4.4
SUB_TAND = 0.02
SUB_T = 1.6
GROUND_T = 0.25
CU_T = 0.035  # 1 oz copper — solid strip (zero-height boxes are not CAD)


def stub_length_mm() -> float:
    """Open lambda_g/4 stub: short at the junction on f0, minus open-end end."""
    w_over_h = LINE_WIDTH / SUB_T
    eps_eff = (SUB_EPS + 1.0) / 2.0 + (SUB_EPS - 1.0) / 2.0 / math.sqrt(
        1.0 + 12.0 / w_over_h
    )
    return C0 / (F0 * math.sqrt(eps_eff)) / 4.0 * 1e3 - 0.3 * SUB_T


STUB = stub_length_mm()
HW = LINE_WIDTH / 2.0
BOARD_X = LINE_HALF + 2.0
BOARD_Y0 = -(HW + 8.0)
BOARD_Y1 = HW + STUB + 6.0

PORTS_JSON = [
    {
        "name": "in1",
        "type": "lumped",
        "start": [-PORT_OFFSET, -HW, 0.0],
        "stop": [-PORT_OFFSET, HW, SUB_T],
        "resistance": 50.0,
        "direction": "z",
        "mesh_dirs": "xy",
        "excite_port": True,
        "source": "freecad_pcb_filter_model",
    },
    {
        "name": "out1",
        "type": "lumped",
        "start": [PORT_OFFSET, -HW, 0.0],
        "stop": [PORT_OFFSET, HW, SUB_T],
        "resistance": 50.0,
        "direction": "z",
        "mesh_dirs": "xy",
        "excite_port": False,
        "source": "freecad_pcb_filter_model",
    },
]

PARAMS = {
    "frequency_hz": F0,
    "corner_hz": FC,
    "board_mm": {
        "x": [-BOARD_X, BOARD_X],
        "y": [BOARD_Y0, BOARD_Y1],
        "substrate_z": [0.0, SUB_T],
        "ground_z": [-GROUND_T, 0.0],
    },
    "line": {"width_mm": LINE_WIDTH, "half_length_mm": LINE_HALF, "copper_mm": CU_T},
    "stub": {"length_mm": round(STUB, 4), "copper_mm": CU_T},
    "ports": {
        p["name"]: {"x": p["start"][0], "y": [-HW, HW], "z": [0.0, SUB_T]}
        for p in PORTS_JSON
    },
    "substrate": {"eps_r": SUB_EPS, "tand": SUB_TAND, "thickness_mm": SUB_T},
}


def out_dir() -> Path:
    env = os.environ.get("PCB_FILTER_OUT_DIR")
    if env:
        return Path(env)
    # FreeCADCmd -c / runpy has no __file__
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()


def _add_feature(doc, name, shape, color):
    obj = doc.addObject("Part::Feature", name)
    obj.Label = name
    obj.Shape = shape
    try:
        obj.ViewObject.ShapeColor = color
    except Exception:
        pass
    return obj


def _add_port_point(doc, name, xyz):
    vec = App.Vector(*xyz)
    try:
        shape = Part.Vertex(vec)
    except Exception:
        obj = doc.addObject("Part::Feature", name)
        obj.Label = name
        obj.Placement = App.Placement(vec, App.Rotation())
        return obj
    obj = doc.addObject("Part::Feature", name)
    obj.Label = name
    obj.Shape = shape
    try:
        obj.ViewObject.ShapeColor = (40, 200, 80)
    except Exception:
        pass
    return obj


def build_document():
    doc = App.newDocument("PcbFilter_2p45")
    copper = (200, 140, 60)
    fr4 = (30, 120, 60)

    _add_feature(
        doc,
        "ground",
        Part.makeBox(
            2.0 * BOARD_X, BOARD_Y1 - BOARD_Y0, GROUND_T,
            App.Vector(-BOARD_X, BOARD_Y0, -GROUND_T),
        ),
        copper,
    )
    _add_feature(
        doc,
        "substrate",
        Part.makeBox(
            2.0 * BOARD_X, BOARD_Y1 - BOARD_Y0, SUB_T,
            App.Vector(-BOARD_X, BOARD_Y0, 0.0),
        ),
        fr4,
    )
    _add_feature(
        doc,
        "msl_line",
        Part.makeBox(
            2.0 * LINE_HALF, LINE_WIDTH, CU_T,
            App.Vector(-LINE_HALF, -HW, SUB_T),
        ),
        copper,
    )
    _add_feature(
        doc,
        "msl_stub",
        Part.makeBox(
            LINE_WIDTH, STUB, CU_T,
            App.Vector(-HW, HW, SUB_T),
        ),
        copper,
    )

    for port in PORTS_JSON:
        tag = port["name"]
        _add_port_point(doc, f"PORT_{tag}_start", port["start"])
        _add_port_point(doc, f"PORT_{tag}_stop", port["stop"])

    try:
        doc.addProperty(
            "App::PropertyString", "openems_ports", "openEMS", "openEMS ports JSON"
        )
        doc.openems_ports = json.dumps(PORTS_JSON)
    except Exception as e:
        print("doc openems_ports property failed:", e)

    doc.recompute()
    return doc


def main() -> int:
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    doc = build_document()

    fcstd = out / "pcb_filter.FCStd"
    step = out / "pcb_filter.step"
    doc.saveAs(str(fcstd))
    try:
        import Import

        Import.export(doc.Objects, str(step))
    except Exception as e:
        print("STEP export failed:", e)
        try:
            Part.export(doc.Objects, str(step))
        except Exception as e2:
            print("Part.export STEP failed:", e2)

    params_path = out / "pcb_filter_params.json"
    params_path.write_text(json.dumps(PARAMS, indent=2), encoding="utf-8")

    print("PCB filter FreeCAD model written:")
    print("  ", fcstd)
    print("  ", step)
    print("  ", params_path)
    print("  solids: ground, substrate, msl_line, msl_stub")
    print(
        f"  board {2 * BOARD_X:.1f} x {BOARD_Y1 - BOARD_Y0:.1f} mm, "
        f"line {LINE_WIDTH:g} mm, stub {STUB:.3f} mm, cu {CU_T} mm"
    )
    for port in PORTS_JSON:
        print(
            f"  port {port['name']}: {port['start']} -> {port['stop']} "
            f"R={port['resistance']:g} dir={port['direction']} "
            f"excite={port['excite_port']}"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print("PCB FILTER MODEL ERROR:", e)
        raise
