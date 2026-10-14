#!/usr/bin/env python3
"""Yagi-Uda antenna FreeCAD model (2.45 GHz ISM example).

Coordinates (mm, drawing unit mm):
  X — element length (E-field / dipole axis)
  Y — boom / propagation axis
  Z — up

Named solids (for GUI import + material assignment):
  boom, reflector, driven_left, driven_right, director_1, director_2, director_3

Run with FreeCADCmd:
  FreeCADCmd make_yagi_model.py
Outputs next to this script (or YAGI_OUT_DIR):
  yagi_2p45.FCStd, yagi_2p45.step, stl/*.stl, yagi_params.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import FreeCAD as App
import Part
import Mesh

# --- design frequency / geometry (mm) ------------------------------------
FREQ_HZ = 2.45e9
C0_M_S = 299_792_458.0
LAMBDA_MM = C0_M_S / FREQ_HZ * 1e3  # ≈ 122.364 mm

ELEM_R = 1.0  # element wire radius [mm] → Ø 2 mm
BOOM_R = 1.5  # boom radius [mm] → Ø 3 mm
GAP = 2.0  # feed gap at driven element [mm]

# Classic Yagi ratios × λ
REF_LEN = 0.500 * LAMBDA_MM
DRV_LEN = 0.475 * LAMBDA_MM
DIR1_LEN = 0.440 * LAMBDA_MM
DIR2_LEN = 0.415 * LAMBDA_MM
DIR3_LEN = 0.390 * LAMBDA_MM

# Spacings along boom (Y); driven at y=0
REF_Y = -0.25 * LAMBDA_MM
DRV_Y = 0.0
DIR1_Y = 0.20 * LAMBDA_MM
DIR2_Y = 0.39 * LAMBDA_MM
DIR3_Y = 0.58 * LAMBDA_MM

BOOM_PAD = 20.0  # mm beyond outermost element
BOOM_Y0 = REF_Y - BOOM_PAD
BOOM_Y1 = DIR3_Y + BOOM_PAD
BOOM_LEN = BOOM_Y1 - BOOM_Y0

# Driven halves (gap in the middle)
DRV_HALF = (DRV_LEN - GAP) / 2.0
DRV_L_X0 = -DRV_LEN / 2.0
DRV_L_X1 = DRV_L_X0 + DRV_HALF
DRV_R_X0 = GAP / 2.0
DRV_R_X1 = DRV_R_X0 + DRV_HALF

PARAMS = {
    "frequency_hz": FREQ_HZ,
    "lambda_mm": round(LAMBDA_MM, 4),
    "element_radius_mm": ELEM_R,
    "boom_radius_mm": BOOM_R,
    "feed_gap_mm": GAP,
    "reflector": {"length_mm": round(REF_LEN, 4), "y_mm": round(REF_Y, 4)},
    "driven": {
        "length_mm": round(DRV_LEN, 4),
        "y_mm": round(DRV_Y, 4),
        "left": [round(DRV_L_X0, 4), round(DRV_L_X1, 4)],
        "right": [round(DRV_R_X0, 4), round(DRV_R_X1, 4)],
    },
    "director_1": {"length_mm": round(DIR1_LEN, 4), "y_mm": round(DIR1_Y, 4)},
    "director_2": {"length_mm": round(DIR2_LEN, 4), "y_mm": round(DIR2_Y, 4)},
    "director_3": {"length_mm": round(DIR3_LEN, 4), "y_mm": round(DIR3_Y, 4)},
    "boom": {
        "y0_mm": round(BOOM_Y0, 4),
        "y1_mm": round(BOOM_Y1, 4),
        "length_mm": round(BOOM_LEN, 4),
        "radius_mm": BOOM_R,
    },
    "axis": {"elements": "X", "boom": "Y", "up": "Z"},
    "polarization": "linear X (H-plane along boom)",
}


def out_dir() -> Path:
    env = __import__("os").environ.get("YAGI_OUT_DIR")
    if env:
        return Path(env)
    # FreeCADCmd -c / exec() has no __file__
    try:
        return Path(__file__).resolve().parent
    except NameError:
        return Path.cwd()


def _add_feature(doc, name: str, shape, color=(200, 160, 80)):
    o = doc.addObject("Part::Feature", name)
    o.Shape = shape
    try:
        o.ViewObject.ShapeColor = color
    except Exception:
        pass
    return o


def _cyl_x(length: float, y: float, x0: float, r: float = ELEM_R):
    return Part.makeCylinder(
        r, length, App.Vector(x0, y, 0.0), App.Vector(1.0, 0.0, 0.0)
    )


def _cyl_y(length: float, y0: float, r: float = BOOM_R):
    return Part.makeCylinder(
        r, length, App.Vector(0.0, y0, 0.0), App.Vector(0.0, 1.0, 0.0)
    )


def build_document():
    doc = App.newDocument("Yagi_Uda_2p45")
    copper = (200, 140, 60)
    steel = (160, 160, 170)

    objs = []
    objs.append(_add_feature(doc, "boom", _cyl_y(BOOM_LEN, BOOM_Y0), steel))
    objs.append(_add_feature(doc, "reflector", _cyl_x(REF_LEN, REF_Y, -REF_LEN / 2), copper))
    objs.append(_add_feature(doc, "driven_left", _cyl_x(DRV_HALF, DRV_Y, DRV_L_X0), copper))
    objs.append(_add_feature(doc, "driven_right", _cyl_x(DRV_HALF, DRV_Y, DRV_R_X0), copper))
    objs.append(_add_feature(doc, "director_1", _cyl_x(DIR1_LEN, DIR1_Y, -DIR1_LEN / 2), copper))
    objs.append(_add_feature(doc, "director_2", _cyl_x(DIR2_LEN, DIR2_Y, -DIR2_LEN / 2), copper))
    objs.append(_add_feature(doc, "director_3", _cyl_x(DIR3_LEN, DIR3_Y, -DIR3_LEN / 2), copper))
    doc.recompute()
    return doc, objs


def main() -> int:
    out = out_dir()
    out.mkdir(parents=True, exist_ok=True)
    stl_dir = out / "stl"
    stl_dir.mkdir(parents=True, exist_ok=True)

    doc, objs = build_document()

    fcstd = out / "yagi_2p45.FCStd"
    step = out / "yagi_2p45.step"
    stl_all = out / "yagi_2p45.stl"
    params_json = out / "yagi_params.json"

    doc.saveAs(str(fcstd))
    try:
        import Import

        Import.export(doc.Objects, str(step))
    except Exception as e:
        print("STEP export failed:", e)
        # fallback: export shapes via Part
        try:
            Part.export(doc.Objects, str(step))
        except Exception as e2:
            print("Part.export STEP failed:", e2)

    try:
        Mesh.export(doc.Objects, str(stl_all))
    except Exception as e:
        print("Combined STL export failed:", e)

    for o in objs:
        stl_i = stl_dir / f"{o.Name}.stl"
        try:
            Mesh.export([o], str(stl_i))
        except Exception as e:
            print(f"STL export {o.Name} failed:", e)

    params_json.write_text(json.dumps(PARAMS, indent=2), encoding="utf-8")

    print("Yagi FreeCAD model written:")
    print("  ", fcstd)
    print("  ", step)
    print("  ", stl_all)
    print("  ", params_json)
    print("  stl/ per object:", ", ".join(o.Name for o in objs))
    print(f"  lambda={LAMBDA_MM:.3f} mm @ {FREQ_HZ/1e9:.3f} GHz")
    return 0


if __name__ == "__main__":
    # FreeCADCmd may pass extra args; ignore
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print("YAGI MODEL ERROR:", e)
        raise
