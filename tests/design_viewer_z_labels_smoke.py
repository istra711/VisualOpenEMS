"""Smoke: hide Z-ruler numbers when the ruler is too short on screen."""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.models.project import Project  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication([])
    from app.ui import vtk_viewer as vv
    from app.ui.design_viewer import Design3DViewer

    viewer = Design3DViewer()
    viewer._vtk = vv._vtk()
    viewer._renderer = viewer._vtk.vtkRenderer()
    viewer._ensure_render_window = lambda: True
    viewer.set_design(
        project=Project.new_default("z-label-smoke"),
        primitives=[
            {
                "kind": "box",
                "name": "pcb",
                "material": "substrate",
                "start": [-20.0, -10.0, 0.0],
                "stop": [20.0, 10.0, 0.1],
            }
        ],
        ports=[],
        nf2ff=None,
        unit_m=1e-3,
        domain=([-40.0, -30.0, -20.0], [40.0, 30.0, 20.0]),
        fit=True,
    )
    # A render window supplies the viewport size used by WorldToDisplay;
    # no GL render is needed for the coordinate projection.
    window = viewer._vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(412, 310)  # small window: every axis is too short
    window.AddRenderer(viewer._renderer)

    viewer._render_design()
    cube = viewer._design_ruler_actor
    assert cube is not None, "coordinate ruler missing"
    assert not any(
        (
            cube.GetXAxisLabelVisibility(),
            cube.GetYAxisLabelVisibility(),
            cube.GetZAxisLabelVisibility(),
        )
    ), "small viewport must hide all crowded axis numbers"

    # Zooming in / enlarging the viewport brings the numbers back.
    window.SetSize(1200, 800)
    viewer.apply_view_preset("iso")
    cube = viewer._design_ruler_actor
    assert cube is not None, "coordinate ruler missing after resize"
    assert all(
        (
            cube.GetXAxisLabelVisibility(),
            cube.GetYAxisLabelVisibility(),
            cube.GetZAxisLabelVisibility(),
        )
    ), "large viewport must show the axis numbers again"

    # A 0.1 mm Z extent is still too short even at the larger size.
    viewer.chk_domain.setChecked(False)
    viewer._draw_design_overlays()
    cube = viewer._design_ruler_actor
    assert cube is not None, "geometry ruler missing"
    assert cube.GetBounds()[5] - cube.GetBounds()[4] <= 1.1e-4
    assert cube.GetZAxisLabelVisibility() is False, (
        "0.1 mm Z ruler must hide overlapping numbers"
    )
    assert cube.GetZAxisMinorTickVisibility() is False
    print("AXIS LABEL VISIBILITY SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
