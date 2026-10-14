#!/usr/bin/env python3
"""Create FreeCAD port marker points in the active or source document.

FreeCADCmd examples/freecad_add_port_marks.py
  env FREECAD_SOURCE optional .FCStd path
  env FREECAD_OUT_DIR optional — saves marked FCStd there

Markers: Part::Vertex objects named PORT_<name>_start / PORT_<name>_stop.
GUI Import / Export CAD reads these automatically into openEMS ports.
"""

from __future__ import annotations

import os
from pathlib import Path


def main() -> int:
    import FreeCAD as App
    import Part

    src = os.environ.get("FREECAD_SOURCE", "")
    out_dir = Path(os.environ.get("FREECAD_OUT_DIR", r"C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\freecad_export"))
    out_dir.mkdir(parents=True, exist_ok=True)

    if src:
        doc = App.open(str(src)) if Path(src).suffix.lower() == ".fcstd" else None
        if doc is None:
            doc = App.newDocument("port_marks")
    else:
        doc = App.ActiveDocument or App.newDocument("port_marks")

    doc.recompute()
    # Place markers near existing solids' centers (or defaults)
    solids = []
    for obj in doc.Objects:
        label = getattr(obj, "Label", "") or ""
        if str(label).startswith("PORT_"):
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            continue
        bb = shape.BoundBox
        solids.append((obj.Name, bb))

    if solids:
        name0, bb0 = solids[0]
        # vertical probe through first solid center
        cx, cy = 0.5 * (bb0.XMin + bb0.XMax), 0.5 * (bb0.YMin + bb0.YMax)
        z0, z1 = bb0.ZMin, bb0.ZMax
        pairs = [("in1", (cx, cy, z0), (cx, cy, z1))]
    else:
        pairs = [("in1", (0.0, 0.0, 0.0), (0.0, 0.0, 1.5))]

    for pname, start, stop in pairs:
        for kind, pt in (("start", start), ("stop", stop)):
            oname = f"PORT_{pname}_{kind}"
            existing = next((o for o in doc.Objects if o.Name == oname or o.Label == oname), None)
            if existing is not None:
                doc.removeObject(existing.Name)
            vec = App.Vector(*pt)
            try:
                shape = Part.Vertex(vec)
            except Exception:
                shape = Part.makeBox(0.001, 0.001, 0.001, vec)
            obj = doc.addObject("Part::Feature", oname)
            obj.Label = oname
            obj.Shape = shape
            try:
                obj.Placement = App.Placement(vec, App.Rotation())
            except Exception:
                pass
            print("added", oname, pt)

    doc.recompute()
    out_fcstd = out_dir / "port_marks_example.FCStd"
    doc.saveAs(str(out_fcstd))
    print("saved", out_fcstd)
    print("Import this FCStd in the GUI - port in1 should appear automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
