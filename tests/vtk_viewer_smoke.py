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

    print("VTK VIEWER SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
