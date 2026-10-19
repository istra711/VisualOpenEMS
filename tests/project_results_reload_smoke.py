"""Smoke: opening a project restores Mesh Viewer + S-param Viewer from results.

Builds a fake project work dir (<work>/<name>/simulation) with the artifacts a
run leaves behind — csx.xml (meshed CSX) and sparams.s1p — then loads the
project through MainWindow._open_project_path and checks that

   * the Mesh Viewer shows the saved mesh (grid lines match csx.xml),
   * the S-param Viewer has the saved network loaded,
   * a project WITHOUT results EMPTIES the viewers (no stale data shown),
   * the S-param viewer is scoped to the project directory afterwards.

Run: python -u tests/project_results_reload_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402

_GRID = {
    "x": [-20.0, -10.0, 0.0, 10.0, 20.0],
    "y": [-20.0, -10.0, 0.0, 10.0, 20.0],
    "z": [-2.0, -1.0, 0.0, 1.0, 2.0],
}


def _write_csx(dst: Path) -> dict[str, int]:
    """Tiny cartesian CSX with 2 metals + explicit grid lines -> XML."""
    from CSXCAD import ContinuousStructure

    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(1e-3)
    grid = csx.GetGrid()
    for axis, d in enumerate("xyz"):
        grid.SetLines(axis, np.asarray(_GRID[d], dtype=float))
    metal = csx.AddMetal("copper")
    metal.AddBox(start=[-10.0, -5.0, 0.0], stop=[-1.0, 5.0, 1.0], priority=10)
    metal.AddBox(start=[1.0, -5.0, 0.0], stop=[10.0, 5.0, 1.0], priority=10)
    csx.Write2XML(str(dst))
    return {d: len(_GRID[d]) for d in "xyz"}


def _write_field_series(vtk_dir: Path) -> Path:
    """Export a tiny FD dump into vtk_dir; returns the first .vtr path."""
    import h5py

    from app.postproc.vtk_export import dump_series_to_vtr

    nx, ny, nz = 4, 3, 2
    h5 = vtk_dir / "Et-E.h5"
    with h5py.File(h5, "w") as f:
        m = f.create_group("Mesh")
        m.create_dataset("x", data=np.linspace(0.0, 3.0, nx))
        m.create_dataset("y", data=np.linspace(0.0, 2.0, ny))
        m.create_dataset("z", data=np.linspace(0.0, 1.0, nz))
        fd = f.create_group("FieldData").create_group("FD")
        fd.create_dataset("f0_real", data=np.ones((3, nx, ny, nz), dtype="float32"))
        fd.create_dataset("f0_imag", data=np.zeros((3, nx, ny, nz), dtype="float32"))
    paths, _labels = dump_series_to_vtr(h5, vtk_dir)
    h5.unlink()
    return paths[0]


def _write_sparams(sim_dir: Path) -> Path:
    from app.postproc.sparams import export_sparams

    freqs = np.linspace(2.0e9, 2.8e9, 9)
    s11 = 0.6 * np.exp(1j * np.linspace(0.0, -np.pi, freqs.size))
    written = export_sparams({"freq": freqs, "S11": s11}, sim_dir, basename="sparams")
    return written.get("touchstone") or written.get("json")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    from app.meshing.preview import mesh_lines_from_csx
    from app.models.project import Project
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    settings = AppSettings.load()
    work = settings.work_path()
    name = "results_reload_smoke"
    proj_dir = work / name
    empty_dir = work / "results_reload_smoke_empty"
    try:
        shutil.rmtree(proj_dir, ignore_errors=True)
        shutil.rmtree(empty_dir, ignore_errors=True)
        sim_dir = proj_dir / "simulation"
        sim_dir.mkdir(parents=True, exist_ok=True)

        # --- artifacts a run leaves behind -----------------------------------
        grid_counts = _write_csx(sim_dir / "csx.xml")
        touch = _write_sparams(sim_dir)
        assert touch is not None and Path(touch).exists(), "no touchstone written"
        proj_path = proj_dir / f"{name}.fcsim.json"
        Project.new_default(name).save(proj_path)

        win = MainWindow(settings)
        win._on_new()
        assert win.mesh_viewer._csx is None, "mesh viewer must start empty"

        # --- open the project -> both viewers restore ------------------------
        win._open_project_path(proj_path)
        assert win.mesh_viewer._csx is not None, "mesh viewer did not restore the CSX"
        lines = mesh_lines_from_csx(win.mesh_viewer._csx)
        got = {d: int(lines[d].size) for d in "xyz"}
        assert got == grid_counts, f"restored mesh lines {got} != saved {grid_counts}"
        assert win.sparam_viewer.network is not None, "S-param viewer did not load results"
        n_pts = len(win.sparam_viewer.network.f)
        assert n_pts == 9, f"unexpected S-param points: {n_pts}"
        assert win.sparam_viewer.work_dir == proj_dir, (
            f"S-param viewer not scoped to the project: {win.sparam_viewer.work_dir}"
        )
        net_ok = win.sparam_viewer.network
        print(f"[reload] mesh lines {got}, S-params {n_pts} pts, work_dir={proj_dir.name}")

        # --- the S-params tab stays scoped to the current project ------------
        win._on_show_sparams()
        assert win.sparam_viewer.network is not None
        assert win.sparam_viewer.work_dir == proj_dir
        net_ok = win.sparam_viewer.network  # tab refresh reloads the file object
        print("[reload] S-params tab scoped to the project OK")

        # --- a project without results must EMPTY the viewers ----------------
        empty_dir.mkdir(parents=True, exist_ok=True)
        empty_proj = empty_dir / "empty.fcsim.json"
        Project.new_default("results_reload_smoke_empty").save(empty_proj)
        win._open_project_path(empty_proj)
        assert win.mesh_viewer._csx is None, "mesh viewer must be cleared"
        assert win.sparam_viewer.network is None, "S-params must be cleared"
        assert win.antenna_viewer.data is None, "antenna pattern must be cleared"
        assert win.vtk_viewer._grid is None, "field viewer must be cleared"
        assert win.vtk_viewer._series == [], "field series must be cleared"
        print("[reload] project without results clears the viewers OK")

        # --- a project WITH exported dumps restores the Field Viewer --------
        field_dir = work / "results_reload_smoke_field"
        shutil.rmtree(field_dir, ignore_errors=True)
        (field_dir / "vtk").mkdir(parents=True, exist_ok=True)
        first_vtr = _write_field_series(field_dir / "vtk")
        field_proj = field_dir / "results_reload_smoke_field.fcsim.json"
        Project.new_default("results_reload_smoke_field").save(field_proj)
        win._open_project_path(field_proj)
        assert win.vtk_viewer._grid is not None, "field viewer did not restore the dump"
        assert win.vtk_viewer._current_path == first_vtr, win.vtk_viewer._current_path
        print("[reload] project with dumps restores the Field Viewer OK")

        win.close()
        print("PROJECT RESULTS RELOAD SMOKE PASS")
        return 0
    finally:
        shutil.rmtree(proj_dir, ignore_errors=True)
        shutil.rmtree(empty_dir, ignore_errors=True)
        shutil.rmtree(work / "results_reload_smoke_field", ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
