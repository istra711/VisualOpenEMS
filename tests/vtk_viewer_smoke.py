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
    from app.ui.vtk_viewer import VtkFieldViewer, axes_total_length

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
    assert not v.chk_autoplay.isChecked(), "Auto-play must default to off"
    v.load(written[0])
    assert v._grid is not None, "grid not loaded"
    assert v._grid.GetNumberOfPoints() == 6 * 5 * 4, v._grid.GetNumberOfPoints()
    assert v._scalar_range[1] > v._scalar_range[0]
    # offscreen: the GL render window is (correctly) not created
    assert v._renderer is None
    print("  viewer loaded grid:", v._grid.GetNumberOfPoints(), "points")

    # --- slice position readout (number + unit) and numeric input in mm ---
    v.cmb_rep.setCurrentText("Slice")
    v.cmb_slice_axis.setCurrentText("Z")  # dump z: 0..4 (metres)
    v.slider_slice.setValue(1000)
    assert v.lbl_slice.text() == "z = 4000.000 mm", v.lbl_slice.text()
    assert abs(v.spn_slice.value() - 4000.0) < 1e-6, v.spn_slice.value()
    v.slider_slice.setValue(250)
    assert v.lbl_slice.text() == "z = 1000.000 mm", v.lbl_slice.text()
    # typing a position cuts EXACTLY there (no 1/1000 slider quantisation)
    v.spn_slice.setValue(1234.5)
    assert v._slice_origin() == (0.0, 0.0, 1.2345), v._slice_origin()
    assert v.lbl_slice.text() == "z = 1234.500 mm", v.lbl_slice.text()
    assert v.slider_slice.value() == 309, v.slider_slice.value()
    # out-of-range input is clamped to the data extent
    v.spn_slice.setValue(9999.0)
    assert v._slice_origin() == (0.0, 0.0, 4.0), v._slice_origin()
    assert v.lbl_slice.text() == "z = 4000.000 mm", v.lbl_slice.text()
    # moving the slider makes it authoritative again; readout follows
    v.slider_slice.setValue(500)
    assert v._slice_origin() == (0.0, 0.0, 2.0), v._slice_origin()
    assert v.lbl_slice.text() == "z = 2000.000 mm", v.lbl_slice.text()
    # axis switch re-derives the position from the slider (dump x: 0..10 m)
    v.cmb_slice_axis.setCurrentText("X")
    assert v.lbl_slice.text() == "x = 5000.000 mm", v.lbl_slice.text()
    assert v._slice_origin() == (5.0, 0.0, 0.0), v._slice_origin()
    assert v.slider_slice.toolTip() and v.spn_slice.toolTip() and v.lbl_slice.toolTip()
    print("  slice readout + numeric input OK")

    # orientation triad scales with the data (a fixed 10-unit actor dominated
    # ResetCamera(), so the field looked empty until the user zoomed in)
    b = (0.0, 0.10, 0.0, 0.12, -0.02, 0.15)
    diag = ((b[1] - b[0]) ** 2 + (b[3] - b[2]) ** 2 + (b[5] - b[4]) ** 2) ** 0.5
    assert abs(axes_total_length(b) - 0.18 * diag) < 1e-12
    assert axes_total_length((0, 0, 0, 0, 0, 0)) == 1.0
    assert axes_total_length(b) < 0.05, "mm-scale data must not get a huge triad"
    assert abs(axes_total_length(b, 2.0) - 2 * axes_total_length(b)) < 1e-12
    assert abs(axes_total_length(b, 0.0) - axes_total_length(b)) < 1e-12
    print("  axes triad length:", axes_total_length(b))

    # --- axes options: colours / labels / font / size / corner / ruler ---
    import vtk

    from app.ui.vtk_viewer import AXIS_COLORS, DEFAULT_AXES, style_axes_triad

    assert v.axes_options()["corner"] is False, "in-scene triad must stay the default"
    assert v.axes_options()["labels"] is True and v.axes_options()["font"] == 12

    triad = vtk.vtkAxesActor()
    style_axes_triad(triad, show_labels=True, font_size=18)
    assert triad.GetAxisLabels() == 1, "labels must start visible"
    assert triad.GetXAxisLabelText() == "X"
    assert triad.GetXAxisCaptionActor2D().GetCaptionTextProperty().GetFontSize() == 18
    for axis, color in zip(("X", "Y", "Z"), AXIS_COLORS):
        shaft = getattr(triad, f"Get{axis}AxisShaftProperty")()
        tip = getattr(triad, f"Get{axis}AxisTipProperty")()
        assert shaft.GetColor() == color, (axis, shaft.GetColor())
        assert tip.GetColor() == color, (axis, tip.GetColor())
        text = getattr(triad, f"Get{axis}AxisCaptionActor2D")().GetCaptionTextProperty()
        assert text.GetColor() == color, (axis, text.GetColor())
    style_axes_triad(triad, show_labels=False, font_size=8)
    assert triad.GetAxisLabels() == 0, "labels must be hidden"
    assert triad.GetYAxisCaptionActor2D().GetCaptionTextProperty().GetFontSize() == 8
    print("  axes colours / labels / font size OK")

    v.set_axes_options({"font": "odd"})  # invalid -> default, never crashes
    assert v.axes_options()["font"] == DEFAULT_AXES["font"]
    v.set_axes_options({"labels": False, "font": 18, "scale": 2.0, "ruler": True, "bogus": 1})
    opts = v.axes_options()
    assert opts["labels"] is False and opts["font"] == 18 and opts["scale"] == 2.0
    assert opts["ruler"] is True and "bogus" not in opts, opts

    # View / context menu: check states follow the options (and vice versa)
    menu = v.build_axes_menu()
    acts = menu._axes_actions
    assert acts["labels"].isChecked() is False and acts["ruler"].isChecked() is True
    assert acts["size"][2.0].isChecked() is True
    assert acts["font"][18].isChecked() is True
    acts["corner"].trigger()
    assert v.axes_options()["corner"] is True, "menu action did not change the options"
    acts["size"][0.5].trigger()
    assert v.axes_options()["scale"] == 0.5
    acts["size"][8.0].trigger()  # XXL / XXXL presets
    assert v.axes_options()["scale"] == 8.0
    acts["font"][14].trigger()
    assert v.axes_options()["font"] == 14
    v.set_axes_option("corner", False)
    v._sync_axes_menu(menu)
    assert acts["corner"].isChecked() is False, "menu check state out of sync"
    print("  axes menu (View + context) OK")

    # numeric coordinate ruler (a plain renderer is enough, no GL needed)
    v.set_axes_options({"font": 20})
    renderer = vtk.vtkRenderer()
    v._renderer = renderer
    v._add_coordinate_ruler(vtk)
    cube = renderer.GetActors().GetLastActor()
    assert [round(b, 9) for b in cube.GetBounds()] == [
        round(b, 9) for b in v._grid.GetBounds()
    ], (cube.GetBounds(), v._grid.GetBounds())
    assert cube.GetXAxesLabelProperty().GetFontSize() == 20
    assert cube.GetZAxesLinesProperty().GetColor() == AXIS_COLORS[2]
    assert cube.GetYTitle() == "y [m]", cube.GetYTitle()
    v._renderer = None
    print("  coordinate ruler OK")

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
    v.set_autoplay_default(True)  # opt in (the app default is off)
    v.load(steps[0])
    assert len(v._series) == 3, v._series
    assert v.slider_time.maximum() == 2
    assert v.chk_autoplay.isChecked() and v.btn_play.isChecked(), "auto-play did not start"
    assert v._timer.isActive()
    assert v._series_range_for("magnitude") is not None, "no fixed colour range"
    print("  series loaded:", len(v._series), "steps, auto-playing")

    # physical simulation time of the frame (auto ps/ns unit)
    assert v.lbl_sim_time.text() == "t = 0.3399 ps", v.lbl_sim_time.text()
    assert v.lbl_time_info.text() == "1/3  (step 0)", v.lbl_time_info.text()
    v.slider_time.setValue(2)
    assert v.lbl_sim_time.text() == "t = 84.63 ps", v.lbl_sim_time.text()
    assert v.lbl_time_info.text() == "3/3  (step 124)", v.lbl_time_info.text()
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
    assert win.viewer.axes_options() == v.axes_options(), "axes options not mirrored"
    win.viewer.set_axes_option("labels", True)  # pop-out changes mirror back
    assert v.axes_options()["labels"] is True
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

    # --- video export (offscreen has no GL window -> test the pure parts) ---
    from unittest.mock import patch

    assert v.btn_video.isEnabled(), "Video… must be enabled for a series"
    assert v._default_video_path().name == "Et-E.mp4", v._default_video_path()
    with patch("app.ui.vtk_viewer.QMessageBox.information"):
        assert v.export_video(td / "no_gl.mp4") is None, "export without GL must refuse"
    assert not (td / "no_gl.mp4").exists()

    img = vtk.vtkImageData()
    img.SetDimensions(5, 3, 1)  # odd width/height
    img.AllocateScalars(vtk.VTK_UNSIGNED_CHAR, 3)
    buf = ns.vtk_to_numpy(img.GetPointData().GetScalars()).reshape(3, 5, 3)
    for row in range(3):
        buf[row, :, :] = row
    rgb = v._image_to_rgb(img)
    assert rgb.shape == (2, 4, 3), rgb.shape  # cropped to even dims
    assert int(rgb[0, 0, 0]) == 2, "image must be flipped (VTK is bottom-up)"
    assert int(rgb[1, 0, 0]) == 1
    v._apply_frame_range("point", "magnitude", False)
    assert v._data_range[0] <= v._data_range[1]
    # --- VTK messages must not pop up a window; corner marker must not warn ---
    from app.ui.vtk_viewer import configure_marker_widget, install_vtk_output_window

    msg_log = td / "vtk_messages.log"
    install_vtk_output_window(vtk, msg_log)

    keep = []  # keep the interactor/window alive (VTK does not own them here)
    rw = vtk.vtkRenderWindow()
    rw.AddRenderer(vtk.vtkRenderer())
    iren = vtk.vtkRenderWindowInteractor()
    iren.SetRenderWindow(rw)
    keep.extend([rw, iren])

    def _marker():
        w = vtk.vtkOrientationMarkerWidget()
        w.SetOrientationMarker(vtk.vtkAxesActor())
        w.SetInteractor(iren)
        return w

    def _log_text() -> str:
        if not msg_log.exists():
            return ""
        return msg_log.read_text(encoding="utf-8", errors="replace")

    good = _marker()
    configure_marker_widget(good)  # enable first, then disable interaction
    assert good.GetEnabled() == 1 and good.GetInteractive() == 0
    assert "Generic Warning" not in _log_text(), (
        "configure_marker_widget must call SetEnabled(1) BEFORE InteractiveOff()"
    )
    # ... and the redirect itself works: a deliberately wrong order is captured
    # in the log file instead of popping up a VTK output window.
    bad = _marker()
    bad.InteractiveOff()  # warns (interactor/Enabled not set yet)
    assert "Generic Warning" in _log_text(), "VTK messages not redirected to a file"
    print("  VTK message redirect + corner marker order OK")

    v.clear()
    assert not v.btn_video.isEnabled(), "Video… must be disabled without a series"
    print("  video export helpers OK")

    print("VTK VIEWER SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
