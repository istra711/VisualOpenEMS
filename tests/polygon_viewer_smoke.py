"""Mesh Viewer render check: polygons keep their outline (offscreen).

Regression: any polygon primitive was drawn as the bounding rectangle of its
points, so the hexagon patch of ``work/easymesh_45deg_repro.fcsim.json``
looked like a rectangular patch.

This is a *script* smoke test (not collected by pytest): importing PySide6
inside pytest crashes this environment, so run it directly:

    python tests/polygon_viewer_smoke.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROJECT = ROOT / "work" / "easymesh_45deg_repro.fcsim.json"
OUT = Path(os.environ.get("TEMP", ".")) / "opencode" / "mesh_viewer_polygon"

#: fallback when the local repro project is missing (work/ is gitignored)
HEX = [(-5.0, -10.0), (5.0, -10.0), (15.0, 0.0), (5.0, 10.0), (-5.0, 10.0), (-15.0, 0.0)]


def _polygon_prim() -> dict:
    return {
        "kind": "polygon",
        "name": "patch_hexagon",
        "material": "copper",
        "priority": 10,
        "axis": "z",
        "coord": 1.6,
        "length": 0.0,
        "points": [list(p) for p in HEX],
        "start": [-15.0, -10.0, 1.6],
        "stop": [15.0, 10.0, 1.6],
    }


def _load():
    """``(project, primitives, ports, nf2ff)`` from the repro file or fallback."""
    if PROJECT.is_file():
        from app.models import Project

        proj = Project.load(PROJECT)
        return proj, list(proj.primitives or []), list(proj.ports or []), proj.nf2ff
    return None, [_polygon_prim()], [], None


def _polygons(ax):
    from matplotlib.patches import Polygon as MplPolygon

    return [p for p in ax.patches if isinstance(p, MplPolygon)]


def _rects(ax):
    from matplotlib.patches import Rectangle

    return [p for p in ax.patches if isinstance(p, Rectangle)]


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.mesh_viewer import MeshGeometryViewer

    OUT.mkdir(parents=True, exist_ok=True)
    project, prims, ports, nf2ff = _load()
    print(f"primitives: {[p.get('name') for p in prims]}")

    viewer = MeshGeometryViewer()
    viewer.set_project_context(
        project=project, primitives=prims, ports=ports, nf2ff=nf2ff
    )

    # --- XY slice through the polygon plane: real hexagon, no bbox rectangle
    viewer.spn_plane.setValue(1.6)
    viewer._draw_slice("XY")
    ax = viewer.fig_slice.axes[0]
    polys = _polygons(ax)
    assert len(polys) == 1, [type(p).__name__ for p in ax.patches]
    xy = polys[0].get_xy()
    assert len(xy) == 7, f"expected 6 vertices + closing point, got {len(xy)}"
    xs = sorted({round(float(p[0]), 6) for p in xy[:-1]})
    ys = sorted({round(float(p[1]), 6) for p in xy[:-1]})
    assert xs == [-15.0, -5.0, 5.0, 15.0], xs
    assert ys == [-10.0, 0.0, 10.0], ys
    # the old bug: the 30 x 20 bounding box of the polygon drawn as a rectangle
    bbox_rects = [
        r
        for r in _rects(ax)
        if abs(r.get_width() - 30.0) < 1e-6 and abs(r.get_height() - 20.0) < 1e-6
    ]
    assert not bbox_rects, "polygon still drawn as its bounding rectangle"
    viewer.fig_slice.savefig(OUT / "slice_xy_hexagon.png", dpi=110)
    print(f"  XY slice: {len(xy) - 1} vertices, no 30x20 bbox rectangle  OK")

    # --- XZ slice (edge-on): a segment at z = 1.6, not a (degenerate) rect
    viewer.spn_plane.setValue(0.0)  # y = 0
    viewer._draw_slice("XZ")
    ax = viewer.fig_slice.axes[0]
    seg = [
        ln
        for ln in ax.lines
        if len(ln.get_xdata()) == 2
        and abs(float(ln.get_ydata()[0]) - 1.6) < 1e-6
        and abs(float(ln.get_ydata()[1]) - 1.6) < 1e-6
        and sorted(float(v) for v in ln.get_xdata()) == [-15.0, 15.0]
    ]
    assert seg, "edge-on cut must be a 15 mm segment at z = 1.6"
    assert not _polygons(ax), "edge-on cut must not draw a polygon"
    viewer.fig_slice.savefig(OUT / "slice_xz_edge_on.png", dpi=110)
    print("  XZ slice: edge-on segment  OK")

    # --- CSX path (as after a mesh build): the info branch + highlight + the
    # overview must use the outline too. No meshing needed here.
    from CSXCAD import ContinuousStructure

    csx = ContinuousStructure()
    metal = csx.AddMetal("patch")
    metal.AddPolygon(([p[0] for p in HEX], [p[1] for p in HEX]), "z", 1.6)
    viewer.set_csx(csx)
    viewer.spn_plane.setValue(1.6)
    viewer._draw_slice("XY")
    ax = viewer.fig_slice.axes[0]
    polys = _polygons(ax)
    # one filled outline from the CSX infos + one dotted from the project dicts
    assert len(polys) == 2, [type(p).__name__ for p in ax.patches]
    assert all(len(p.get_xy()) == 7 for p in polys), [len(p.get_xy()) for p in polys]
    viewer.fig_slice.savefig(OUT / "slice_xy_after_build.png", dpi=110)
    print("  XY slice (CSX infos + project dicts): hexagon  OK")

    # --- selection highlight must follow the outline (it was a rectangle)
    viewer.set_highlight(names=["patch"], label="Prim: patch")
    viewer._draw_slice("XY")
    ax = viewer.fig_slice.axes[0]
    hl = [
        p
        for p in _polygons(ax)
        if tuple(round(float(c), 3) for c in p.get_edgecolor()[:3]) == (1.0, 0.831, 0.0)
    ]
    assert hl, "no yellow highlight outline drawn"
    assert all(len(p.get_xy()) == 7 for p in hl), [len(p.get_xy()) for p in hl]
    assert not [
        r
        for r in _rects(ax)
        if tuple(round(float(c), 3) for c in r.get_edgecolor()[:3]) == (1.0, 0.831, 0.0)
    ], "highlight still drawn as a rectangle"
    viewer.fig_slice.savefig(OUT / "slice_xy_highlight.png", dpi=110)
    print("  highlight: yellow hexagon outline  OK")

    # --- overview: XY footprint is the hexagon, XZ is a segment
    viewer._draw_overview()
    axes = viewer.fig_slice.axes
    polys = _polygons(axes[0])
    assert polys and all(len(p.get_xy()) == 7 for p in polys), [
        len(p.get_xy()) for p in polys
    ]
    xz_lines = [
        ln
        for ln in axes[1].lines
        if len(ln.get_xdata()) == 2
        and sorted(float(v) for v in ln.get_xdata()) == [-15.0, 15.0]
    ]
    assert xz_lines, "overview XZ must show the polygon silhouette as a segment"
    viewer.fig_slice.savefig(OUT / "overview.png", dpi=110)
    print("  overview: hexagon footprint + edge-on silhouette  OK")

    print(f"renders: {OUT}")
    viewer.deleteLater()
    app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
