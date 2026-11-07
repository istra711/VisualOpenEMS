"""Smoke test for the embedded 3D Design viewer (offscreen)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from app.models.project import NF2FFDef, PortDef, Project


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from app.ui.design_viewer import Design3DViewer, DesignViewerWindow

    project = Project.new_default("design-smoke")
    prims = [
        {
            "kind": "box",
            "name": "trace",
            "material": "copper",
            "start": [-5.0, -1.0, 0.0],
            "stop": [5.0, 1.0, 0.035],
        },
        {
            "kind": "cylinder",
            "name": "via",
            "material": "copper",
            "start": [0.0, 0.0, -1.0],
            "stop": [0.0, 0.0, 1.0],
            "radius": 0.25,
        },
        {
            "kind": "sphere",
            "name": "ball",
            "material": "substrate",
            "center": [2.0, 2.0, 0.0],
            "radius": 0.5,
        },
    ]
    ports = [
        PortDef(name="P1", type="msl", start=[-4.0, -1.0, 0.0], stop=[-4.0, 1.0, 0.035]),
        PortDef(
            name="P2",
            type="lumped",
            start=[4.0, -1.0, 0.0],
            stop=[4.0, 1.0, 0.035],
            active=False,
        ),
    ]
    nf = NF2FFDef(enabled=True, start=[-10, -8, -5], stop=[10, 8, 5])

    viewer = Design3DViewer()
    viewer.set_design(
        project=project,
        primitives=prims,
        ports=ports,
        nf2ff=nf,
        unit_m=1e-3,
        domain=([-20, -20, -10], [20, 20, 10]),
        fit=True,
    )
    assert len(viewer._design_prims) == 3
    assert len(viewer._design_ports) == 2
    assert viewer._side_panel.maximumWidth() <= 165
    assert viewer._design_domain == ([-20, -20, -10], [20, 20, 10])
    assert "Primitives: 3" in viewer.lbl_design_info.text()
    assert "Ports: 1/2 active" in viewer.lbl_design_info.text()
    assert "NF2FF: on" in viewer.lbl_design_info.text()

    # Domain-box toggle: the ruler drops the domain and snaps to the visible
    # geometry/port union when the domain overlay is hidden.
    viewer.chk_domain.setChecked(True)
    with_domain = viewer._scene_bounds_m(1e-3)
    assert with_domain is not None
    assert abs(with_domain[0] - (-20e-3)) < 1e-12
    viewer.chk_domain.setChecked(False)
    assert viewer._domain_box_visible() is False
    without_domain = viewer._scene_bounds_m(1e-3, include_domain=False)
    assert without_domain is not None
    expected = (-5e-3, 5e-3, -1e-3, 2.5e-3, -1e-3, 1e-3)
    for got, want in zip(without_domain, expected):
        assert abs(got - want) < 1e-12, (without_domain, expected)
    viewer.chk_domain.setChecked(True)

    # The selected standard view is a mode: opening/fitting the design applies
    # it (the camera used to stay on a stale top/rolled direction because
    # _render_design always fitted with "keep").
    import math

    import vtk as _vtkmod

    from app.ui import vtk_viewer as _vv

    viewer._vtk = _vv._vtk()
    viewer._renderer = viewer._vtk.vtkRenderer()
    viewer._ensure_render_window = lambda: True  # offscreen: no GL pane
    viewer.set_view_default("iso")
    viewer.set_design(
        project=project,
        primitives=prims,
        ports=ports,
        nf2ff=nf,
        unit_m=1e-3,
        domain=([-20, -20, -10], [20, 20, 10]),
        fit=True,
    )
    viewer._render_design()

    def _cube_ruler_count(renderer) -> int:
        props = renderer.GetViewProps()
        props.InitTraversal()
        count = 0
        while True:
            prop = props.GetNextProp()
            if prop is None:
                break
            if prop.GetClassName() == "vtkCubeAxesActor":
                count += 1
        return count

    # The inherited Field-Viewer _apply_axes() must not add a second ruler.
    viewer._apply_axes()
    viewer._draw_design_overlays()
    assert _cube_ruler_count(viewer._renderer) == 1, (
        "3D Design must show exactly one coordinate ruler"
    )
    print("  single coordinate ruler (no domain/geometry duplicate) OK")

    cam = viewer._renderer.GetActiveCamera()
    d = [cam.GetPosition()[i] - cam.GetFocalPoint()[i] for i in range(3)]
    norm = math.sqrt(sum(v * v for v in d))
    assert norm > 0 and all(abs(v / norm - 0.5774) < 2e-3 for v in d), d
    print("  standard view applied on first render OK")

    # Camera fit follows the visible bounds too: with the domain hidden, the
    # focal point is the geometry/port center (y = 0.75 mm here), not (0,0,0).
    viewer.chk_domain.setChecked(False)
    viewer.apply_view_preset("iso")
    focal = viewer._renderer.GetActiveCamera().GetFocalPoint()
    assert abs(focal[1] - 0.75e-3) < 1e-6, focal
    viewer.chk_domain.setChecked(True)

    # Live redraw: with the domain overlay off, the actual cube ruler actor
    # uses the geometry/port union instead of the domain bounds.
    viewer.chk_domain.setChecked(False)
    cubes = [
        a for a in viewer._design_actors
        if a.GetClassName() == "vtkCubeAxesActor"
    ]
    assert cubes, "ruler actor missing after domain toggle"
    rb = cubes[-1].GetBounds()
    for got, want in zip(rb, (-5e-3, 5e-3, -1e-3, 2.5e-3, -1e-3, 1e-3)):
        assert abs(got - want) < 1e-9, (rb,)
    viewer.chk_domain.setChecked(True)
    print("  ruler snaps to geometry when domain box is off OK")
    viewer._renderer = None  # the remaining checks run without a VTK renderer

    # Toggles and the tool API must be safe even without a GL context.
    viewer.chk_geom.setChecked(False)
    viewer.chk_domain.setChecked(False)
    viewer.chk_ports.setChecked(False)
    viewer.chk_nf2ff.setChecked(False)
    viewer.set_clip_plane([0, 0, 0], [1, 0, 0])
    viewer.set_clip_plane()
    marker = object()
    viewer.add_overlay_actor(marker, category="test", name="marker")
    assert len(viewer._overlay_actors) == 1
    viewer.remove_overlay_actors(category="test")
    assert viewer._overlay_actors == []
    viewer.set_highlight(names=["trace"], label="Prim: trace")
    assert viewer._highlight_names == ["trace"]
    assert viewer._highlight_label == "Prim: trace"
    assert viewer._highlighted_boxes()
    viewer.set_highlight()
    assert viewer._highlight_names == []
    assert viewer.pick_at(10, 10) is None  # no VTK in this test

    # Coordinate system / ruler toggles are part of the right-click menu.
    assert viewer._axes["show"] is True and viewer._axes["ruler"] is True
    menu = viewer._build_context_menu()
    labels = [a.text() for a in menu.actions()]
    assert "Coordinate system" in labels and "Measure distance (tape, 2 clicks)" in labels
    coord = next(a.menu() for a in menu.actions() if a.text() == "Coordinate system")
    coord_labels = [a.text() for a in coord.actions()]
    assert "Axes size" in coord_labels and "Axes location" in coord_labels
    assert "Corner axes (bottom-left)" in coord_labels
    assert "Ruler placement" in coord_labels and "Ruler tick side" in coord_labels
    viewer._set_design_axes("corner", True)
    assert viewer._axes["corner"] is True
    assert "Axis tape (ticks on X/Y/Z arrows)" in coord_labels
    assert "Axis tape uses" in coord_labels
    viewer._set_ruler_fly_mode("furthest")
    viewer._set_ruler_tick_side("both")
    assert viewer._axes["ruler_fly"] == "furthest"
    assert viewer._axes["ruler_ticks"] == "both"
    viewer._set_design_axes("ruler", False)
    assert viewer._axes["ruler"] is False
    viewer._set_design_axes("ruler", True)
    viewer._set_design_axes("axis_tape", True)
    assert viewer._axes["axis_tape"] is True
    viewer._set_axis_tape_axis(2, False)
    assert viewer._axis_tape_axes == {0, 1}
    viewer._set_axis_tape_axis(2, True)
    assert viewer._axis_tape_axes == {0, 1, 2}
    viewer._set_design_axis_scale(2.0)
    viewer._set_design_axis_anchor("center")
    viewer._set_design_axis_font(18)
    assert viewer._axes["scale"] == 2.0
    assert viewer._axis_anchor == "center"
    assert viewer._axes["font"] == 18
    viewer._reset_design_axes()
    assert viewer._axes["scale"] == 2.0 and viewer._axis_anchor == "min"
    assert viewer._axes["axis_tape"] is False
    assert viewer._axes["corner"] is True
    assert viewer._axes["ruler"] is True and viewer._axes["font"] == 24
    assert viewer._axes["ruler_fly"] == "closest"
    assert viewer._axes["ruler_ticks"] == "outside"
    viewer.set_measure_mode(False)  # no GL context: must stay safe

    # Pop-out builds an independent viewer with the same scene snapshot.
    pop = DesignViewerWindow(viewer)
    assert pop.viewer._design_prims == viewer._design_prims
    assert pop.viewer._design_ports == viewer._design_ports
    pop.close()

    # Emptying the scene must not leave a stale design snapshot behind.
    viewer.set_design(project=None, primitives=[], ports=[], nf2ff=None, fit=False)
    assert viewer._design_domain is None
    viewer.clear("empty")
    assert viewer._design_prims == []
    assert viewer._design_ports == []
    assert viewer._design_nf2ff is None

    # Keep the QApplication alive for the widget destructors.
    app.processEvents()
    print("DESIGN 3D VIEWER SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
