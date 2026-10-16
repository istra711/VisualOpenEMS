"""Smoke test for MeshGeometryViewer (offscreen)."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication


def main() -> int:
    try:
        from openEMS import openEMS
        from CSXCAD import ContinuousStructure
        from app.meshing.preview import (
            iter_primitive_infos,
            mesh_lines_from_csx,
            mesh_cells_from_lines,
            get_sim_area,
        )
        from app.csxcad_builder.primitives import patch_antenna_primitives
        from app.meshing.easy_wrapper import (
            enhance_csx,
            enhance_fdtd,
            generate_mesh,
            apply_boundary,
        )
        from app.meshing.rules import compute_targets
        import warnings

        project, primitives = patch_antenna_primitives()
        FDTD = openEMS(NrTS=10, EndCriteria=1e-3)
        FDTD.SetGaussExcite(2.45e9, 0.5e9)
        CSX = ContinuousStructure()
        FDTD.SetCSX(CSX)
        mesh_setup: dict = {}
        CSX = enhance_csx(CSX, mesh_setup)
        FDTD = enhance_fdtd(FDTD, mesh_setup)
        targets = compute_targets(project.mesh, eps_max=1.0)
        apply_boundary(FDTD, project.boundary, project.mesh, targets)
        CSX.GetGrid().SetDeltaUnit(project.mesh.unit_to_meters())

        for mat in project.materials:
            if mat.kind == "pec":
                CSX.AddMetal(mat.name)
            elif mat.kind == "dielectric":
                CSX.AddMaterial(mat.name, epsilon=mat.epsilon, kappa=mat.kappa or 0)

        for pr in primitives:
            mat = pr.get("material", "copper")
            prop = (
                CSX.AddMetal(mat)
                if "copper" in mat
                else CSX.AddMaterial(mat, epsilon=3.38)
            )
            prop.AddBox(
                start=pr["start"],
                stop=pr["stop"],
                priority=pr.get("priority", 10),
            )

        for idx, port in enumerate(project.ports, start=1):
            FDTD.AddLumpedPort(
                idx,
                port.resistance,
                port.start,
                port.stop,
                port.direction,
                1.0,
                priority=15,
            )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            generate_mesh(
                CSX,
                project.mesh,
                project.boundary,
                targets,
                primitives_mesh_setup=mesh_setup,
            )

        # --- data extraction checks ---
        infos = iter_primitive_infos(CSX)
        print(f"primitives: {len(infos)}")
        for info in infos:
            print(
                f"  {info.name:24s} [{info.kind:16s}] "
                f"({info.start[0]:.3g},{info.start[1]:.3g},{info.start[2]:.3g}) -> "
                f"({info.stop[0]:.3g},{info.stop[1]:.3g},{info.stop[2]:.3g})"
            )
        kinds = {i.kind for i in infos}
        assert any("Metal" in k for k in kinds), f"no Metal in {kinds}"
        assert any("Material" in k or "metal" in k.lower() for k in kinds)

        mesh = mesh_lines_from_csx(CSX)
        for d in "xyz":
            mids, sizes = mesh_cells_from_lines(mesh[d])
            print(f"  mesh {d}: lines={mesh[d].size} cells={sizes.size}")
            assert sizes.size > 0, f"no cells on {d}"

        area = get_sim_area(CSX)
        assert area is not None
        print(f"  area: {area[0]} -> {area[1]}")

        # --- geometry must sit inside the FDTD domain ---
        from app.meshing.preview import primitives_outside_domain

        clean = primitives_outside_domain(iter_primitive_infos(CSX), area)
        assert not clean, f"patch antenna prims outside domain: {clean}"
        far = CSX.AddMetal("out_of_domain")
        far.AddBox(
            start=[float(area[1][0]) + 1.0, 0.0, 0.0],
            stop=[float(area[1][0]) + 5.0, 2.0, 2.0],
        )
        hits = primitives_outside_domain(iter_primitive_infos(CSX), area)
        assert len(hits) == 1, f"expected 1 outside prim, got {hits}"
        assert hits[0][1] == 0 and hits[0][3] == "max", hits[0]
        print(f"  outside-domain flag: {hits[0][0].name} +{hits[0][2]:.4g} on x-max")

        # --- widget ---
        app = QApplication.instance() or QApplication(sys.argv)
        from app.ui.mesh_viewer import MeshGeometryViewer

        viewer = MeshGeometryViewer()
        viewer.set_project_context(
            project=project,
            primitives=primitives,
            ports=project.ports,
            nf2ff=project.nf2ff,
        )
        viewer.set_csx(CSX)

        # --- plane slider calibration (regression: init 0..100 vs handler
        # scale 0..1000, 'XY slice' label vs 'XY' key mismatch, and view
        # switches must recalibrate the range) ---
        from app.ui.mesh_viewer import _AXIS, _SLICE_AXES

        assert (viewer.slider.minimum(), viewer.slider.maximum()) == (0, 1000), (
            viewer.slider.minimum(),
            viewer.slider.maximum(),
        )
        assert viewer.slider.isEnabled(), "slider must enable after set_csx (slice view)"
        assert viewer.spn_plane.isEnabled(), "plane spinbox must enable with the slider"
        viewer.cmb_view.setCurrentText("XY slice")
        assert viewer.plot_tabs.currentIndex() == 0, (
            "'XY slice' must draw the slice plot (label/key mismatch regression)"
        )
        area0 = get_sim_area(CSX)
        n0 = _SLICE_AXES["XY"][2]  # normal z
        lo0, hi0 = float(area0[0][_AXIS[n0]]), float(area0[1][_AXIS[n0]])
        viewer.slider.setValue(1000)  # full drag must reach the domain end
        # (spn_plane stores 4 decimals — see setDecimals(4))
        assert abs(viewer.spn_plane.value() - hi0) < 1e-3, (
            viewer.spn_plane.value(),
            hi0,
        )
        viewer.slider.setValue(0)
        assert abs(viewer.spn_plane.value() - lo0) < 1e-3, (
            viewer.spn_plane.value(),
            lo0,
        )
        # switching the slice axis must recalibrate the plane spinbox range
        viewer.cmb_view.setCurrentText("XZ slice")  # normal y
        n1 = _SLICE_AXES["XZ"][2]
        lo1, hi1 = float(area0[0][_AXIS[n1]]), float(area0[1][_AXIS[n1]])
        assert abs(viewer.spn_plane.minimum() - lo1) < 1e-3, (
            viewer.spn_plane.minimum(),
            lo1,
        )
        assert abs(viewer.spn_plane.maximum() - hi1) < 1e-3, (
            viewer.spn_plane.maximum(),
            hi1,
        )
        # non-slice views disable both controls, slice views re-enable them
        viewer.cmb_view.setCurrentText("Overview")
        assert not viewer.slider.isEnabled() and not viewer.spn_plane.isEnabled()
        viewer.cmb_view.setCurrentText("XY slice")
        assert viewer.slider.isEnabled() and viewer.spn_plane.isEnabled()
        print("  plane slider calibration: OK")

        for i in range(viewer.cmb_view.count()):
            name = viewer.cmb_view.currentText()
            viewer.cmb_view.setCurrentIndex(i)
            print(f"  view switch -> {name}: OK")
        txt = viewer.info_text()
        print(f"  info panel lines: {txt.count(chr(10)) + 1}")
        assert "Domain" in txt or "domain" in txt.lower() or "Mesh" in txt

        # --- viewer must flag the primitive that pokes out of the domain ---
        names = viewer._outside_names(iter_primitive_infos(CSX), get_sim_area(CSX))
        assert "out_of_domain#0" in names, f"viewer missed outside prim: {names}"
        for i in range(viewer.cmb_view.count()):
            if viewer.cmb_view.itemText(i) == "Overview":
                viewer.cmb_view.setCurrentIndex(i)
                break
        found = any(
            "OUTSIDE DOMAIN" in t.get_text()
            for axx in viewer.fig_slice.axes
            for t in axx.texts
        )
        assert found, "overview stats panel missing OUTSIDE DOMAIN note"
        print("  outside-domain viewer note: OK")

        # --- in-place NF2FF frame update (Fit/Edit NF2FF must refresh now) ---
        import types

        box = types.SimpleNamespace(start=[-20.0, -30.0, -10.0], stop=[20.0, 40.0, 10.0])
        viewer.set_nf2ff(box)
        assert viewer._nf2ff is box, "set_nf2ff did not store the new box"
        viewer.set_nf2ff(project.nf2ff)
        assert viewer._nf2ff is project.nf2ff
        empty = MeshGeometryViewer()
        assert not empty.slider.isEnabled() and not empty.spn_plane.isEnabled(), (
            "plane controls must start disabled without a CSX"
        )
        empty.set_nf2ff(box)  # no CSX yet -> must not raise
        assert empty._nf2ff is box
        print("  nf2ff in-place update: OK")

        # --- wheel-zoom double-click "reset view" (Figure has no autoscale_view) ---
        from matplotlib.backend_bases import MouseEvent

        for cv in (viewer.canvas_slice, viewer.canvas_cells):
            cx = cv.figure.bbox.width / 2.0
            cy = cv.figure.bbox.height / 2.0
            ev = MouseEvent("button_press_event", cv, cx, cy, button=1, dblclick=True)
            cv.callbacks.process("button_press_event", ev)  # must not raise
        assert len(viewer.canvas_slice.figure.axes) >= 1, "slice canvas has no axes"
        print("  dblclick reset view: OK")
        print("MESH VIEWER SMOKE PASS")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
