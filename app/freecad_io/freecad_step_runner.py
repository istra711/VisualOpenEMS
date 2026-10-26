"""Build STEP shapes from a manifest — runs INSIDE FreeCADCmd.

Launched by :func:`app.freecad_io.step_export.export_step`; do not run it in the
application interpreter (it imports ``FreeCAD``/``Part``/``Mesh``).

Environment:
  STEP_MANIFEST   JSON ``{"unit": "mm", "shapes": [ ... ]}`` (all coords in mm)
  STEP_OUT        destination ``.step`` / ``.stp`` path

Shape kinds (see ``build_shape_specs``):
  box       start, stop                       -> solid
  sheet     axis, coord, points               -> planar face (zero thickness)
  prism     axis, coord, length, points       -> extruded solid
  cylinder  start, stop, radius               -> solid
  sphere    center, radius                    -> solid
  mesh      stl                               -> shell (solid when watertight)

Each shape becomes a ``Part::Feature`` whose ``Label`` is the primitive name.
``Import.export`` (not ``Part.export``) is what keeps those labels as STEP
``PRODUCT`` names — ``Part.export`` writes a single anonymous product.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


def _vec(values):
    import FreeCAD as App

    return App.Vector(float(values[0]), float(values[1]), float(values[2]))


def _plane_face(axis: str, coord: float, points: list):
    """Planar face from 2D points in the cyclic plane perpendicular to ``axis``.

    z -> (x, y), x -> (y, z), y -> (z, x): the same order CSXCAD uses for
    ``AddPolygon``/``AddLinPoly``, so sheets and prisms match openEMS exactly.
    """
    import FreeCAD as App
    import Part

    idx = AXIS_INDEX.get(str(axis).lower(), 2)
    c0, c1 = (idx + 1) % 3, (idx + 2) % 3
    verts = []
    for pt in points:
        p = [0.0, 0.0, 0.0]
        p[c0], p[c1], p[idx] = float(pt[0]), float(pt[1]), float(coord)
        verts.append(App.Vector(*p))
    if len(verts) < 3:
        raise ValueError("a face needs at least 3 points")
    if verts[0].distanceToPoint(verts[-1]) > 1e-9:
        verts.append(verts[0])
    return Part.Face(Part.makePolygon(verts))


def _make_shape(shape: dict):
    import FreeCAD as App
    import Mesh
    import Part

    kind = str(shape.get("kind") or "").lower()

    if kind == "box":
        start = _vec(shape["start"])
        stop = _vec(shape["stop"])
        size = stop - start
        return Part.makeBox(size.x, size.y, size.z, start)

    if kind == "sheet":
        return _plane_face(shape["axis"], shape["coord"], shape["points"])

    if kind == "prism":
        length = float(shape.get("length") or 0.0)
        face = _plane_face(shape["axis"], shape["coord"], shape["points"])
        if length <= 0:
            return face
        idx = AXIS_INDEX.get(str(shape["axis"]).lower(), 2)
        direction = App.Vector(*[1.0 if k == idx else 0.0 for k in range(3)])
        return face.extrude(direction.multiply(length))

    if kind == "cylinder":
        start = _vec(shape["start"])
        stop = _vec(shape["stop"])
        axis = stop - start
        if axis.Length <= 0:
            raise ValueError("cylinder has no length")
        return Part.makeCylinder(float(shape["radius"]), axis.Length, start, axis)

    if kind == "sphere":
        return Part.makeSphere(float(shape["radius"]), _vec(shape["center"]))

    if kind == "mesh":
        path = str(shape["stl"])
        if not Path(path).exists():
            raise ValueError(f"mesh not found: {path}")
        mesh = Mesh.Mesh(path)
        if not mesh.CountFacets:
            raise ValueError(f"mesh has no facets: {path}")
        result = Part.Shape()
        result.makeShapeFromMesh(mesh.Topology, 0.0)
        # A watertight mesh becomes a solid; an open shell stays a shell
        # (both are valid STEP and either can be assigned a material in CST).
        if result.isClosed():
            try:
                result = Part.makeSolid(result)
            except Exception:  # noqa: BLE001 — keep the shell if sewing fails
                pass
        return result

    raise ValueError(f"unsupported shape kind {kind!r}")


def main() -> int:
    import FreeCAD as App
    import Import

    manifest_path = os.environ.get("STEP_MANIFEST", "")
    out_path = os.environ.get("STEP_OUT", "")
    if not manifest_path or not out_path:
        print("ERROR: STEP_MANIFEST and STEP_OUT must be set")
        return 2

    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    shapes = data.get("shapes") or []
    if not shapes:
        print("WARNING: manifest contains no shapes")
        print("ERROR: nothing to export")
        return 1

    doc = App.newDocument("VisualOpenEMS_STEP")
    built = 0
    for i, shape in enumerate(shapes):
        name = str(shape.get("name") or f"shape{i + 1}")
        try:
            obj = doc.addObject("Part::Feature", f"shape_{i}")
            obj.Shape = _make_shape(shape)
            obj.Label = name
            built += 1
        except Exception as e:  # noqa: BLE001 — one bad shape must not kill the file
            print(f"WARNING: {name}: {e}")

    if not built:
        print("ERROR: no shape could be built")
        return 1

    doc.recompute()
    try:
        Import.export(doc.Objects, out_path)
    except Exception as e:  # noqa: BLE001 — fall back to the anonymous exporter
        print(f"WARNING: Import.export failed ({e}); object names are lost")
        import Part

        Part.export(doc.Objects, out_path)

    print(f"STEP written: {out_path} ({built}/{len(shapes)} shapes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
