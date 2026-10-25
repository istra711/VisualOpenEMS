"""Smoke: Field Viewer vector glyphs (exclusive display, orientation, scale).

Run standalone (``python tests/vtk_vectors_smoke.py``): under pytest this
platform crashes while importing the VTK viewer tab (access violation in the
Qt/VTK binding), which is why the VTK/GUI checks of this project are standalone
scripts.

The arrow geometry is probed with a *line* instead of an arrow source, so the
direction of every arrow is directly readable from the glyph output points.
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


def _write_series(directory: Path, fields: list) -> Path:
    """Write ``Probe_0000.vtr`` … (3x2x2 grid, ``field`` + ``magnitude``)."""
    import numpy as np
    import vtk

    first = None
    for i, field in enumerate(fields):
        grid = vtk.vtkRectilinearGrid()
        grid.SetDimensions(3, 2, 2)
        coords = vtk.vtkDoubleArray(), vtk.vtkDoubleArray(), vtk.vtkDoubleArray()
        for arr, vals in zip(coords, ((0.0, 1.0, 2.0), (0.0, 1.0), (0.0, 1.0))):
            for v in vals:
                arr.InsertNextValue(v)
        grid.SetXCoordinates(coords[0])
        grid.SetYCoordinates(coords[1])
        grid.SetZCoordinates(coords[2])
        flat = np.asarray(field, dtype=float).reshape(12, 3)
        vec = vtk.vtkDoubleArray()
        vec.SetName("field")
        vec.SetNumberOfComponents(3)
        for v in flat:
            vec.InsertNextTuple3(*v)
        scal = vtk.vtkDoubleArray()
        scal.SetName("magnitude")
        for m in np.linalg.norm(flat, axis=1):
            scal.InsertNextValue(float(m))
        pd = grid.GetPointData()
        pd.SetScalars(scal)
        pd.AddArray(vec)
        pd.SetActiveVectors("field")
        p = directory / f"Probe_{i:04d}.vtr"
        w = vtk.vtkXMLRectilinearGridWriter()
        w.SetFileName(str(p))
        w.SetInputData(grid)
        w.Write()
        first = first or p
    return first


class _LineShim:
    """vtkArrowSource replacement: a line from (0,0,0) to (1,0,0)."""

    def __init__(self) -> None:
        import vtk

        self.ls = vtk.vtkLineSource()
        self.ls.SetPoint1(0.0, 0.0, 0.0)
        self.ls.SetPoint2(1.0, 0.0, 0.0)
        self.ls.Update()

    def SetTipLength(self, *_a) -> None:  # noqa: N802 - VTK API
        pass

    def SetTipRadius(self, *_a) -> None:  # noqa: N802
        pass

    def SetShaftRadius(self, *_a) -> None:  # noqa: N802
        pass

    def GetOutputPort(self):  # noqa: N802
        return self.ls.GetOutputPort()


def _arrow_vectors(viewer, vtk, step: int):
    """Arrow vectors (tip - base) of one step; line shim -> 2 points per arrow."""
    import numpy as np

    viewer._read_into_grid(viewer._series[step])
    viewer._populate_arrays()
    actor = viewer._make_vector_actor(
        vtk, viewer._grid, viewer.cmb_rep.currentText(), viewer._lookup_table()
    )
    assert actor is not None, "no glyph actor"
    pd = actor.GetMapper().GetInputAlgorithm().GetOutput()
    pts = np.asarray([pd.GetPoint(i) for i in range(pd.GetNumberOfPoints())])
    seg = pts.reshape(-1, 2, 3)
    return seg[:, 1, :] - seg[:, 0, :]


def main() -> int:
    import numpy as np
    import vtk
    from PySide6.QtWidgets import QApplication

    from app.ui.vtk_viewer import DEFAULT_VECTORS, VtkFieldViewer

    app = QApplication.instance() or QApplication([])

    tmp = Path(tempfile.mkdtemp(prefix="vtk_vectors_smoke"))
    s0 = np.tile([1e-3, 0.0, 0.0], (12, 1))  # +x
    s1 = np.tile([-1e-3, 0.0, 0.0], (12, 1))  # -x  (reversed)
    s2 = np.tile([0.0, 2e-3, 0.0], (12, 1))  # +y, twice the magnitude
    series = _write_series(tmp, [s0, s1, s2])

    v = VtkFieldViewer(work_dir=tmp)
    v.load(series)
    assert len(v._series) == 3, f"series not detected: {v._series}"

    # 1) the arrows replace the field representation (no overlay by default)
    assert DEFAULT_VECTORS["overlay"] is False
    v.set_vector_options({"enabled": False, "overlay": False}, notify=False)
    assert v._show_field_actor() is True, "field must stay visible without arrows"
    v.set_vector_options({"enabled": True, "overlay": False}, notify=False)
    assert v._show_field_actor() is False, "arrows must not be covered by the field"
    v.set_vector_options({"enabled": True, "overlay": True}, notify=False)
    assert v._show_field_actor() is True, "overlay requested -> field underneath"
    v._has_vectors = False
    assert v._show_field_actor() is True, "no vector array -> nothing to hide"
    v._has_vectors = True
    print("  exclusive display + overlay option OK")

    # 2) option round-trip
    v.set_vector_options(
        {"enabled": True, "size": 2.0, "max_arrows": 300, "normalize": False,
         "overlay": True},
        notify=False,
    )
    opts = v.vector_options()
    assert opts["overlay"] is True and opts["enabled"] is True
    assert opts["normalize"] is False and opts["max_arrows"] == 300
    v.set_vector_options({"overlay": True, "size": object()}, notify=False)
    assert v.vector_options()["overlay"] is False, "bad value -> defaults"
    print("  option round-trip OK")

    # 3) the arrows follow the field direction (line glyphs: direction readable)
    _orig_arrow = vtk.vtkArrowSource
    vtk.vtkArrowSource = _LineShim  # type: ignore[assignment]
    try:
        v.set_vector_options(
            {"enabled": True, "size": 2.0, "max_arrows": 100, "normalize": True},
            notify=False,
        )
        d0, d1 = _arrow_vectors(v, vtk, 0), _arrow_vectors(v, vtk, 1)
        assert d0.shape == d1.shape and d0.size, "no arrows produced"
        n0 = d0 / np.linalg.norm(d0, axis=1)[:, None]
        n1 = d1 / np.linalg.norm(d1, axis=1)[:, None]
        cos = np.sum(n0 * n1, axis=1)
        assert np.allclose(cos, -1.0, atol=1e-6), f"+x -> -x not reversed: {cos}"
        print("  arrows re-orient with the field OK")

        # 4) the length follows |E| with a series-wide reference: |E| doubles
        #    between step 0 and 2 -> the arrow length doubles as well (a
        #    per-frame reference would keep both the same size)
        v.set_vector_options(
            {"enabled": True, "size": 2.0, "max_arrows": 100, "normalize": False},
            notify=False,
        )
        l0 = float(np.linalg.norm(_arrow_vectors(v, vtk, 0), axis=1).max())
        l2 = float(np.linalg.norm(_arrow_vectors(v, vtk, 2), axis=1).max())
        ratio = l2 / l0
        assert abs(ratio - 2.0) < 0.05, f"length ratio {ratio} != 2"
        assert v._vec_series_ref and v._vec_series_ref > 0.0
        cached = v._vec_series_ref
        _arrow_vectors(v, vtk, 1)
        assert v._vec_series_ref == cached, "reference must be cached, not per frame"
        print(f"  length follows |E| (ratio {ratio:.3f}, reference {cached:.3g}) OK")

        # 5) the step label reports how many arrows flipped
        v.set_vector_options({"enabled": True}, notify=False)
        t1 = v._vector_motion_text(1)
        assert "100% reversed" in t1 and "median cos -1.00" in t1, t1
        t2 = v._vector_motion_text(2)
        assert "0% reversed" in t2, t2
        assert v._vector_motion_text(0) == "", "no previous step -> no readout"
        v.set_vector_options({"enabled": False}, notify=False)
        assert v._vector_motion_text(1) == "", "no readout without arrows"
        print("  arrow motion readout OK")
    finally:
        vtk.vtkArrowSource = _orig_arrow

    v.clear()
    print("VTK VECTORS SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
