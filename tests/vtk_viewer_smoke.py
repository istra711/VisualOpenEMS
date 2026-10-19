"""Smoke: field dumps -> VTK (.vtr) + the embedded VTK viewer tab.

Runs offscreen; the VTK Qt render window is intentionally skipped on the
offscreen platform (it would crash), so this checks the conversion + that the
viewer loads the grid without a render window.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_dump(path: Path, nx=6, ny=5, nz=4) -> None:
    import numpy as np
    import h5py

    rng = np.random.default_rng(3)
    with h5py.File(path, "w") as f:
        m = f.create_group("Mesh")
        m.create_dataset("x", data=np.linspace(0, 10, nx))
        m.create_dataset("y", data=np.linspace(0, 8, ny))
        m.create_dataset("z", data=np.linspace(0, 4, nz))
        fd = f.create_group("FieldData").create_group("FD")
        fd.create_dataset("f0_real", data=rng.standard_normal((3, nx, ny, nz)).astype("float32"))
        fd.create_dataset("f0_imag", data=rng.standard_normal((3, nx, ny, nz)).astype("float32"))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.postproc.vtk_export import export_dumps_to_vtk
    from app.ui.vtk_viewer import VtkFieldViewer

    app = QApplication.instance() or QApplication(sys.argv)

    td = Path(tempfile.mkdtemp(prefix="vtk_viewer_smoke_"))
    sim = td / "simulation"
    sim.mkdir()
    _make_dump(sim / "Et-E.h5")

    written = export_dumps_to_vtk(sim)
    assert written and written[0].suffix == ".vtr", written
    assert written[0].exists()
    print("  dump -> vtr:", written[0].name)

    v = VtkFieldViewer(work_dir=td)
    v.load(written[0])
    assert v._grid is not None, "grid not loaded"
    assert v._grid.GetNumberOfPoints() == 6 * 5 * 4, v._grid.GetNumberOfPoints()
    assert v._scalar_range[1] > v._scalar_range[0]
    # offscreen: the GL render window is (correctly) not created
    assert v._renderer is None
    print("  viewer loaded grid:", v._grid.GetNumberOfPoints(), "points")

    # the reader is format-agnostic: a .vtu (ParaView-style) also loads
    import numpy as np
    import vtk
    from vtk.util import numpy_support as ns

    pts = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], float)
    ug = vtk.vtkUnstructuredGrid()
    vp = vtk.vtkPoints()
    for p in pts:
        vp.InsertNextPoint(*p)
    ug.SetPoints(vp)
    tet = vtk.vtkTetra()
    for i in range(4):
        tet.GetPointIds().SetId(i, i)
    ug.InsertNextCell(tet.GetCellType(), tet.GetPointIds())
    arr = ns.numpy_to_vtk(np.linspace(1, 4, 4), deep=True)
    arr.SetName("temperature")
    ug.GetPointData().SetScalars(arr)
    vtu = td / "u.vtu"
    w = vtk.vtkXMLUnstructuredGridWriter()
    w.SetFileName(str(vtu))
    w.SetInputData(ug)
    w.Write()

    v.load(vtu)
    assert v._grid.GetNumberOfPoints() == 4
    assert v._scalar_name == "temperature", v._scalar_name
    print("  .vtu (non-.vtr) loaded, scalar:", v._scalar_name)

    # --- time series: film playback (fps / loop / auto-play / fixed colours) ---
    import shutil

    series_dir = written[0].parent
    steps = []
    for i in range(3):
        p = series_dir / f"Et-E_{i:04d}.vtr"
        shutil.copy(written[0], p)
        steps.append(p)
    import json

    (series_dir / "Et-E.series.json").write_text(
        json.dumps(
            {
                "labels": ["t0", "t62", "t124"],
                "files": [p.name for p in steps],
                "times": [3.398875e-13, 4.2485935e-11, 8.463198e-11],
                "frequencies": [None, None, None],
            }
        ),
        encoding="utf-8",
    )
    v.load(steps[0])
    assert len(v._series) == 3, v._series
    assert v.slider_time.maximum() == 2
    assert v.chk_autoplay.isChecked() and v.btn_play.isChecked(), "auto-play did not start"
    assert v._timer.isActive()
    assert v._series_range_for("magnitude") is not None, "no fixed colour range"
    print("  series loaded:", len(v._series), "steps, auto-playing")

    # physical simulation time of the frame (auto ps/ns unit)
    assert v.lbl_sim_time.text() == "t = 0.3399 ps", v.lbl_sim_time.text()
    v.slider_time.setValue(2)
    assert v.lbl_sim_time.text() == "t = 84.63 ps", v.lbl_sim_time.text()
    assert v.format_sim_time(1.5e-9) == "t = 1.5 ns"
    assert v.format_sim_time(4.2e-12) == "t = 4.2 ps"
    assert v.format_frequency(2.23e9) == "f = 2.23 GHz"
    assert v.format_sim_time(None) == ""
    print("  frame time label:", v.lbl_sim_time.text())

    v.spn_fps.setValue(10)
    assert v._timer.interval() == 100, v._timer.interval()

    # loop off -> playback stops on the last frame instead of wrapping
    v.chk_loop.setChecked(False)
    v.slider_time.setValue(2)
    v._on_play_tick()
    assert not v.btn_play.isChecked() and not v._timer.isActive()
    print("  fps + loop-off stop OK")

    # compact colour bar (no giant "field" title)
    import vtk as _vtk

    bar = v._make_scalar_bar(_vtk, v._lookup_table(), "field")
    assert bar.GetPosition() == (0.84, 0.06), bar.GetPosition()
    assert bar.GetPosition2() == (0.15, 0.88), bar.GetPosition2()
    assert bar.GetTitleTextProperty().GetFontSize() == 12
    assert bar.GetLabelTextProperty().GetFontSize() == 10
    assert bar.GetUnconstrainedFontSize() == 1
    print("  compact colour bar OK")

    # log scale must actually engage even when the range starts at 0.0
    v.chk_log.setChecked(True)
    v.chk_auto.setChecked(False)
    v.spn_min.setValue(0.0)
    v.spn_max.setValue(1e-12)
    lut_log = v._lookup_table()
    assert lut_log.GetScale() == 1, lut_log.GetScale()  # 1 = log10
    lo_l, hi_l = lut_log.GetTableRange()
    assert 0.0 < lo_l < hi_l, (lo_l, hi_l)
    v.chk_log.setChecked(False)
    assert v._lookup_table().GetScale() == 0
    print("  log-scale range (0 -> min positive) OK")

    # pop-out gets its OWN viewer (no GL widget reparenting, so no stale copy)
    before = v._root_layout.count()
    v.popout()
    assert v._popout_win is not None, "pop-out window not created"
    assert v.btn_popout.text() == "Dock view"
    win = v._popout_win
    assert win.viewer is not v
    assert win.viewer.parent() is win
    assert win.viewer._current_path == v._current_path
    assert win.viewer.cmb_rep.currentText() == v.cmb_rep.currentText()
    # closing the window leaves the tab exactly as it was
    win.close()
    assert v._popout_win is None, "close did not reset the pop-out state"
    assert v.btn_popout.text() == "Pop out"
    assert v._root_layout.count() == before, "tab layout gained items after close"
    assert v._host.parent() is v
    # toggle button: open then close again
    v.popout()
    assert v._popout_win is not None
    v.popout()
    assert v._popout_win is None
    assert v._root_layout.count() == before
    print("  pop-out / close (no duplicate) OK")

    # right-click menu: only outside the 3D pane (VTK uses it inside)
    from PySide6.QtCore import QPoint

    v.btn_play.setChecked(False)
    v.resize(900, 700)
    v.show()
    app.processEvents()
    host = v._host.geometry()
    assert host.height() > 50, host
    assert not v._context_menu_allowed(host.center()), "menu would open inside the 3D pane"
    assert v._context_menu_allowed(QPoint(5, 5)), "menu blocked on the controls"
    print("  right-click gating (pane excluded) OK")

    print("VTK VIEWER SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
