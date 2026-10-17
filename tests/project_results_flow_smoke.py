"""Smoke: recorded results drive the viewers (no mesh build needed).

Covers the flow "open a simulated project → viewers restore themselves":
  * `Project.results` in the JSON records the artifact paths
    (`_collect_result_paths` / `_record_results`),
  * opening the project loads mesh (csx_xml/mesh_xml) + S-params from the
    recorded paths,
  * opening the Mesh Viewer tab loads the recorded mesh, and falls back to
    *building* the mesh when nothing is recorded,
  * without geometry nothing is built.

The mesh build itself is stubbed (fast, no easyMesh).

Run: python -u tests/project_results_flow_smoke.py
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

_GRID = {"x": [-20.0, -10.0, 0.0, 10.0, 20.0], "y": [-20.0, -5.0, 0.0, 5.0, 20.0], "z": [-2.0, -1.0, 0.0, 1.0, 2.0]}


def _write_csx(dst: Path) -> None:
    from CSXCAD import ContinuousStructure

    csx = ContinuousStructure()
    csx.GetGrid().SetDeltaUnit(1e-3)
    for axis, d in enumerate("xyz"):
        csx.GetGrid().SetLines(axis, np.asarray(_GRID[d], dtype=float))
    metal = csx.AddMetal("copper")
    metal.AddBox(start=[-10.0, -5.0, 0.0], stop=[-1.0, 5.0, 1.0], priority=10)
    metal.AddBox(start=[1.0, -5.0, 0.0], stop=[10.0, 5.0, 1.0], priority=10)
    dst.parent.mkdir(parents=True, exist_ok=True)
    csx.Write2XML(str(dst))


def _write_sparams(sim_dir: Path) -> Path:
    from app.postproc.sparams import export_sparams

    freqs = np.linspace(2.0e9, 2.8e9, 9)
    written = export_sparams(
        {"freq": freqs, "S11": 0.5 * np.exp(1j * np.linspace(0.0, -3.0, freqs.size))},
        sim_dir,
        basename="sparams",
    )
    return written.get("touchstone") or written["json"]


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    import json

    from app.models.project import Project
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    settings = AppSettings.load()
    work = settings.work_path()
    name = "results_flow_smoke"
    proj_dir = work / name
    proj_path = work / f"{name}.fcsim.json"  # JSON in the work dir, see below
    try:
        shutil.rmtree(proj_dir, ignore_errors=True)
        proj_path.unlink(missing_ok=True)

        # --- 0. saving creates the project folder (+ result subfolders) ------
        from PySide6.QtWidgets import QFileDialog

        Project.new_default(name).save(proj_path)
        win0 = MainWindow(settings)
        win0._open_project_path(proj_path)
        assert not (proj_dir / "sparams").exists(), "folder must not pre-exist"
        win0._on_save()  # plain Save -> no dialog
        for sub in ("", "sparams", "fields", "nf2ff"):
            assert (proj_dir / sub).is_dir(), f"save must create {proj_dir / sub}"
        print("[flow] Save created the project folder + result subfolders OK")

        # Save As (dialog stubbed) must create the folder as well
        other_dir = work / "results_flow_smoke_as"
        other_json = work / "results_flow_smoke_as.fcsim.json"
        shutil.rmtree(other_dir, ignore_errors=True)
        other_json.unlink(missing_ok=True)
        real_dialog = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(other_json), ""))
        try:
            win0._on_save_as()
        finally:
            QFileDialog.getSaveFileName = real_dialog
        for sub in ("", "sparams", "fields", "nf2ff"):
            assert (other_dir / sub).is_dir(), f"Save As must create {other_dir / sub}"
        shutil.rmtree(other_dir, ignore_errors=True)
        other_json.unlink(missing_ok=True)
        win0.close()
        print("[flow] Save As created the project folder + result subfolders OK")

        (proj_dir / "sparams").mkdir(parents=True, exist_ok=True)

        # artifacts: a built/runned mesh (<name>/csx.xml) + S-params (<name>/sparams/)
        # + an NF2FF result (<name>/nf2ff/nf2ff.json)
        _write_csx(proj_dir / "csx.xml")
        touch = _write_sparams(proj_dir / "sparams")
        assert Path(touch).exists()
        (proj_dir / "nf2ff").mkdir(parents=True, exist_ok=True)
        nf2ff_file = proj_dir / "nf2ff" / "nf2ff.json"
        nf2ff_file.write_text(
            json.dumps(
                {
                    "frequencies": [2.4e9],
                    "theta_deg": [0.0, 90.0, 180.0],
                    "phi_deg": [0.0, 90.0, 180.0, 360.0],
                    "D_dBi": [[[10.0, 9.0, 8.0, 7.0], [5.0, 4.0, 3.0, 2.0], [0.0, -1.0, -2.0, -3.0]]],
                    "gain_dBi": [[[10.0, 9.0, 8.0, 7.0], [5.0, 4.0, 3.0, 2.0], [0.0, -1.0, -2.0, -3.0]]],
                    "Prad": [1e-10],
                    "Dmax_dBi": [10.0],
                    "efficiency": [0.9],
                }
            ),
            encoding="utf-8",
        )

        # --- 1. record the paths (as after Build/Run) ------------------------
        win = MainWindow(settings)
        win._open_project_path(proj_path)
        assert win.project.results is None, "fresh project must have no results yet"
        win._record_results()  # save=True -> writes the project JSON
        rec = json.loads(proj_path.read_text(encoding="utf-8")).get("results") or {}
        print("recorded:", {k: Path(str(v)).name for k, v in rec.items() if k not in ("sim_dir",)})
        assert rec.get("csx_xml", "").endswith("csx.xml"), rec
        assert rec.get("sparams", "").endswith(("sparams.s1p", "sparams.json")), rec
        assert rec.get("nf2ff", "").endswith("nf2ff.json"), rec
        assert rec.get("time"), "timestamp missing"
        win.close()

        # --- 2. reopen: viewers come back from the recorded paths ------------
        win2 = MainWindow(settings)
        win2._open_project_path(proj_path)
        assert win2.mesh_viewer._csx is not None, "recorded mesh not restored on open"
        from app.meshing.preview import mesh_lines_from_csx

        lines = mesh_lines_from_csx(win2.mesh_viewer._csx)
        assert {d: int(lines[d].size) for d in "xyz"} == {d: len(_GRID[d]) for d in "xyz"}
        assert win2.sparam_viewer.network is not None, "recorded S-params not loaded"
        assert len(win2.sparam_viewer.network.f) == 9
        # antenna pattern: comes from the project's recorded NF2FF
        assert win2.antenna_viewer.data_path == nf2ff_file, win2.antenna_viewer.data_path
        assert win2.antenna_viewer.data is not None
        print("[flow] reopen restored mesh + S-params + NF2FF from the project JSON OK")

        # opening the viewer with a loaded CSX must not build again
        def _boom() -> str:
            raise AssertionError("must not rebuild when the mesh is already shown")

        win2._build_and_validate = _boom  # type: ignore[method-assign]
        win2._ensure_mesh_viewer_content()
        print("[flow] viewer-open with mesh present: no rebuild OK")

        # --- geometry changes must invalidate the loaded mesh ----------------
        built2: list[int] = []

        def _rebuild_stub() -> str:
            built2.append(1)
            return "stub rebuild"

        win2._build_and_validate = _rebuild_stub  # type: ignore[method-assign]
        win2.primitives.append(
            {"kind": "box", "name": "extra", "material": "copper", "priority": 10,
             "start": [0, 0, 0], "stop": [2, 2, 2]}
        )
        win2._ensure_mesh_viewer_content()
        assert built2, "geometry change must rebuild the Mesh Viewer mesh"
        print("[flow] geometry change invalidates + rebuilds the mesh OK")

        # --- 3. nothing recorded: viewer-open builds (stub) ------------------
        proj_b = proj_dir / "flow_b.fcsim.json"
        pb = Project.new_default("results_flow_smoke_b")
        pb.primitives = [{"kind": "box", "name": "box1", "material": "copper",
                          "priority": 10, "start": [0, 0, 0], "stop": [1, 1, 1]}]
        pb.save(proj_b)
        win3 = MainWindow(settings)
        win3._open_project_path(proj_b)
        assert win3.mesh_viewer._csx is None
        built: list[int] = []

        def _fake_build() -> str:
            built.append(1)
            win3.mesh_viewer.set_csx(win2.mesh_viewer._csx)  # any CSX is enough here
            return "stub build"

        win3._build_and_validate = _fake_build  # type: ignore[method-assign]
        win3._ensure_mesh_viewer_content()  # == opening the Mesh Viewer tab
        assert built, "opening the viewer must generate the mesh when nothing is recorded"
        assert win3.mesh_viewer._csx is not None
        print("[flow] viewer-open without results: mesh generated OK")

        # --- 4. no geometry at all: nothing to do ---------------------------
        empty = work / "results_flow_smoke_empty"
        shutil.rmtree(empty, ignore_errors=True)
        empty.mkdir(parents=True)
        pe = empty / "empty.fcsim.json"
        Project.new_default("results_flow_smoke_empty").save(pe)
        win4 = MainWindow(settings)
        win4._open_project_path(pe)
        win4._build_and_validate = _boom  # type: ignore[method-assign]
        win4._ensure_mesh_viewer_content()
        print("[flow] viewer-open without geometry: nothing built OK")

        for w in (win2, win3, win4):
            w.close()
        print("PROJECT RESULTS FLOW SMOKE PASS")
        return 0
    finally:
        shutil.rmtree(proj_dir, ignore_errors=True)
        shutil.rmtree(work / "results_flow_smoke_empty", ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
