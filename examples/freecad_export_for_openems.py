#!/usr/bin/env python3
"""Export FreeCAD geometry for openEMS (STEP + per-object STL).

Run INSIDE FreeCAD (FreeCAD GUI macro or FreeCADCmd):

  FreeCADCmd examples/freecad_export_for_openems.py
  # or in FreeCAD GUI: Macro > Macros... > execute this file

Inputs (edit below or set env):
  FREECAD_SOURCE   — .FCStd path (optional; if empty, uses active document)
  FREECAD_OUT_DIR  — output directory (default: work/freecad_export)
  FREECAD_DEFLECTION — linear deflection mm (default 0.1)

Outputs:
  <name>.step           — for GUI Import / archive
  <name>_<obj>.stl      — per solid mesh
  openems_export.json   — object list for GUI/scripts
"""

from __future__ import annotations

import json
import os
import struct
from pathlib import Path


def _write_stl(tris, path: Path) -> None:
    n = len(tris)
    with open(path, "wb") as f:
        f.write(b"FreeCAD openEMS export".ljust(80, b" ")[:80])
        f.write(struct.pack("<I", n))
        for t in tris:
            ax, ay, az = t[1][0] - t[0][0], t[1][1] - t[0][1], t[1][2] - t[0][2]
            bx, by, bz = t[2][0] - t[0][0], t[2][1] - t[0][1], t[2][2] - t[0][2]
            nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
            norm = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            f.write(struct.pack("<3f", nx / norm, ny / norm, nz / norm))
            for p in t:
                f.write(struct.pack("<3f", float(p[0]), float(p[1]), float(p[2])))
            f.write(struct.pack("<H", 0))


def _tris(shape, defl):
    verts, faces = shape.tessellate(defl)
    arr = []
    for face in faces:
        if len(face) < 3:
            continue
        pts = [verts[i] for i in face]
        for k in range(1, len(pts) - 1):
            p0, p1, p2 = pts[0], pts[k], pts[k + 1]
            arr.append([[p0.x, p0.y, p0.z], [p1.x, p1.y, p1.z], [p2.x, p2.y, p2.z]])
    return arr


def _open_document(App, Import, src: str | None):
    """Open FCStd via App.open; STEP/STL via Import.insert into a new doc."""
    if not src:
        doc = App.ActiveDocument
        if doc is None:
            print("No active document and FREECAD_SOURCE not set")
            return None, None
        return doc, doc.Name
    path = Path(src)
    if not path.exists():
        print(f"FREECAD_SOURCE not found: {src}")
        return None, None
    fmt = path.suffix.lower().lstrip(".")
    if fmt in {"fcstd", "fcs"}:
        doc = App.open(str(path))
        return doc, path.stem
    # STEP/STP/STL/OBJ: Import.insert creates objects in a new document
    name = f"import_{path.stem}"
    doc = App.newDocument(name)
    try:
        Import.insert(str(path), doc.Name)
    except Exception as e:
        print(f"Import.insert failed for {path}: {e}")
        App.closeDocument(doc.Name)
        return None, None
    return doc, path.stem


def main() -> int:
    import FreeCAD as App
    import Part
    import Import

    src = os.environ.get("FREECAD_SOURCE", "")
    out = Path(os.environ.get("FREECAD_OUT_DIR", r"C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\freecad_export"))
    defl = float(os.environ.get("FREECAD_DEFLECTION", "0.1"))
    out.mkdir(parents=True, exist_ok=True)

    doc, base = _open_document(App, Import, src or None)
    if doc is None:
        return 2

    step_path = out / f"{base}.step"
    # If source is already STEP, copy/export a clean STEP for the project archive
    try:
        Import.export(doc.Objects, str(step_path))
        print("STEP:", step_path)
    except Exception as e:
        print("STEP export failed:", e)
        if Path(src).suffix.lower() in {".step", ".stp"}:
            try:
                step_path.write_bytes(Path(src).read_bytes())
                print("STEP (copied source):", step_path)
            except OSError as e2:
                print("STEP copy failed:", e2)

    manifest = {"source": src or base, "step": str(step_path), "objects": [], "ports": []}
    for obj in doc.Objects:
        label = getattr(obj, "Label", "") or getattr(obj, "Name", "")
        name0 = getattr(obj, "Name", "") or label
        if str(label).startswith("PORT_") or str(name0).startswith("PORT_"):
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            continue
        solids = list(shape.Solids) if (hasattr(shape, "Solids") and shape.Solids) else [shape]
        for si, solid in enumerate(solids):
            tris = _tris(solid, defl)
            if not tris:
                continue
            name = obj.Name if len(solids) == 1 else f"{obj.Name}_{si+1}"
            safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
            stl = out / f"{base}_{safe}.stl"
            _write_stl(tris, stl)
            bb = solid.BoundBox
            entry = {
                "name": name,
                "stl": str(stl),
                "triangles": len(tris),
                "bounds_min": [bb.XMin, bb.YMin, bb.ZMin],
                "bounds_max": [bb.XMax, bb.YMax, bb.ZMax],
            }
            manifest["objects"].append(entry)
            print("STL:", stl, f"({len(tris)} tris)")

    # Ports: PORT_* points + openems_port_* props + doc openems_ports JSON
    try:
        from pathlib import Path as _P

        helper_src = _P(__file__).resolve().parents[1] / "app" / "freecad_io" / "port_marks.py"
        # Prefer in-process helper via import path of the repo if available
        try:
            from app.freecad_io.port_marks import freecad_extract_ports_code  # type: ignore

            ns: dict = {"json": json}
            exec(freecad_extract_ports_code(), ns)
            manifest["ports"] = ns["extract_openems_ports"](doc)
        except Exception:
            # Standalone FreeCADCmd: inline minimal extraction (doc props + PORT_* labels)
            ports = []
            try:
                raw = getattr(doc, "openems_ports", None)
                if isinstance(raw, str) and raw.strip():
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        data = data.get("ports") or []
                    if isinstance(data, list):
                        ports.extend(data)
            except Exception:
                pass
            named = {}
            for obj in doc.Objects:
                label = getattr(obj, "Label", "") or getattr(obj, "Name", "")
                if not str(label).startswith("PORT_"):
                    continue
                base_name = str(label)[5:]
                kind = ""
                pname = ""
                if base_name.endswith("_start"):
                    kind, pname = "start", base_name[: -len("_start")]
                elif base_name.endswith("_stop") or base_name.endswith("_end"):
                    kind = "stop"
                    pname = base_name[: -len("_stop")] if base_name.endswith("_stop") else base_name[: -len("_end")]
                if not pname or kind not in ("start", "stop"):
                    continue
                shape = getattr(obj, "Shape", None)
                pt = None
                try:
                    if shape is not None and hasattr(shape, "Vertexes") and shape.Vertexes:
                        pt = shape.Vertexes[0].Point
                    elif hasattr(obj, "Placement"):
                        pt = obj.Placement.Base
                except Exception:
                    pt = None
                if pt is None:
                    continue
                named.setdefault(pname, {})[kind] = [float(pt.x), float(pt.y), float(pt.z)]
            for pname, slot in named.items():
                if slot.get("start") and slot.get("stop"):
                    ports.append(
                        {
                            "name": pname,
                            "type": "lumped",
                            "start": slot["start"],
                            "stop": slot["stop"],
                            "resistance": 50.0,
                            "source": "PORT_*_points",
                        }
                    )
            manifest["ports"] = ports
    except Exception as e:
        print("port extract failed:", e)

    if manifest["ports"]:
        print("Ports:", json.dumps(manifest["ports"]))
    (out / "openems_export.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Manifest:", out / "openems_export.json")
    print("Next: GUI Import Geometry -> choose the .step (or .stl) file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
