"""Smoke test for MeshGeometryViewer (offscreen)."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from unittest.mock import patch

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
        from app.ui.mesh_viewer import (
            SLIDER_BASE_STEP,
            SLIDER_RANGE,
            _AXIS,
            _SLICE_AXES,
        )

        assert (viewer.slider.minimum(), viewer.slider.maximum()) == (0, SLIDER_RANGE), (
            viewer.slider.minimum(),
            viewer.slider.maximum(),
        )
        # 2x the old travel; Ctrl gives SLIDER_BASE_STEP-times finer steps
        assert SLIDER_RANGE // SLIDER_BASE_STEP == 2000, SLIDER_RANGE
        assert viewer.slider.minimumWidth() >= 300, viewer.slider.minimumWidth()
        assert viewer.slider.isEnabled(), "slider must enable after set_csx (slice view)"
        assert viewer.spn_plane.isEnabled(), "plane spinbox must enable with the slider"
        viewer.cmb_view.setCurrentText("XY slice")
        assert viewer.plot_tabs.currentIndex() == 0, (
            "'XY slice' must draw the slice plot (label/key mismatch regression)"
        )
        area0 = get_sim_area(CSX)
        n0 = _SLICE_AXES["XY"][2]  # normal z
        lo0, hi0 = float(area0[0][_AXIS[n0]]), float(area0[1][_AXIS[n0]])
        # a freshly built/loaded mesh starts on the model origin (plane 0 mm)
        assert abs(viewer.spn_plane.value()) < 1e-9, (
            f"plane must be 0 mm after meshing: {viewer.spn_plane.value()}"
        )
        viewer.slider.setValue(SLIDER_RANGE)  # full drag must reach the domain end
        # (slider values snap to mesh lines; spn_plane stores 4 decimals)
        want_hi = viewer._nearest_mesh_line(n0, hi0)
        assert abs(viewer.spn_plane.value() - want_hi) < 1e-3, (
            viewer.spn_plane.value(),
            want_hi,
        )
        viewer.slider.setValue(0)
        want_lo = viewer._nearest_mesh_line(n0, lo0)
        assert abs(viewer.spn_plane.value() - want_lo) < 1e-3, (
            viewer.spn_plane.value(),
            want_lo,
        )
        # base resolution: positions quantise to every SLIDER_BASE_STEP-th step
        viewer.slider.setValue(12345)
        assert viewer.slider.value() == 12340, viewer.slider.value()
        assert viewer._slider_step() == SLIDER_BASE_STEP, "no Ctrl -> base step"
        assert "fine" not in viewer.lbl_plane.text(), viewer.lbl_plane.text()
        # Ctrl (faked): raw 1-step positions (10x finer) AND no mesh-line
        # snapping — otherwise the finer steps would be invisible because both
        # modes snap onto the same mesh line anyway
        viewer.slider.setValue(0)
        with patch.object(MeshGeometryViewer, "_slider_fine", lambda self: True):
            viewer.slider.setValue(12345)
        assert viewer.slider.value() == 12345, viewer.slider.value()
        want_free = lo0 + (hi0 - lo0) * (12345 / SLIDER_RANGE)
        assert abs(viewer.spn_plane.value() - want_free) < 1e-3, (
            viewer.spn_plane.value(),
            want_free,
        )
        assert "fine" in viewer.lbl_plane.text(), viewer.lbl_plane.text()
        # the value itself is NOT repeated: it lives in the spinbox (mm suffix)
        assert "mm" not in viewer.lbl_plane.text(), viewer.lbl_plane.text()
        assert viewer.spn_plane.suffix().strip() == "mm", viewer.spn_plane.suffix()
        assert viewer.lbl_mesh.text().endswith("%"), viewer.lbl_mesh.text()
        assert viewer.slider.toolTip() and viewer.slider_mesh.toolTip()
        assert viewer.lbl_mesh.toolTip(), "grid readout needs its own tooltip"
        # grid readout follows the slider
        viewer.slider_mesh.setValue(70)
        assert viewer.lbl_mesh.text().startswith("70%"), viewer.lbl_mesh.text()
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

        # --- mesh grid visibility slider (alpha + line width) ---------------
        def _mesh_alphas(axx):
            out = []
            for coll in axx.collections:
                a = coll.get_alpha()
                if a is None:
                    try:  # alpha baked into the colour instead
                        cols = np.asarray(coll.get_edgecolor(), dtype=float)
                        if cols.ndim == 2 and cols.shape[1] >= 4:
                            a = float(cols[0, 3])
                    except Exception:  # noqa: BLE001
                        a = None
                if a is not None:
                    out.append(float(a))
            return out

        viewer.cmb_view.setCurrentText("XY slice")
        viewer.chk_mesh.setChecked(True)
        viewer.slider_mesh.setValue(100)
        viewer._redraw()
        hi = _mesh_alphas(viewer.fig_slice.axes[0])
        viewer.slider_mesh.setValue(0)
        viewer._redraw()
        lo = _mesh_alphas(viewer.fig_slice.axes[0])
        assert hi and max(hi) > 0.9, f"mesh lines not strong at 100%: {hi}"
        assert lo and max(lo) < 0.1, f"mesh lines not faint at 0%: {lo}"
        # the slider only makes sense while the grid is shown in a slice view
        viewer.chk_mesh.setChecked(False)
        assert not viewer.slider_mesh.isEnabled(), "slider must disable with hidden grid"
        viewer.chk_mesh.setChecked(True)
        assert viewer.slider_mesh.isEnabled(), "slider must enable with shown grid"
        viewer.slider_mesh.setValue(35)
        viewer._redraw()
        print(f"  mesh grid visibility slider: OK (alpha {max(hi):.2f} -> {max(lo):.2f})")

        # --- visibility toggles must work and must NOT reset the zoom --------
        import contextlib
        import io

        viewer.cmb_view.setCurrentText("XY slice")
        viewer.spn_plane.setValue(0.7)  # inside the patch feed port's z-range
        for chk in (viewer.chk_geom, viewer.chk_ports):
            chk.setChecked(True)
            artist_ax = viewer.fig_slice.axes[0]
            n_on = (len(artist_ax.patches), len(artist_ax.lines))
            chk.setChecked(False)
            artist_ax = viewer.fig_slice.axes[0]
            n_off = (len(artist_ax.patches), len(artist_ax.lines))
            assert n_on != n_off, f"visibility toggle has no effect: {n_on} == {n_off}"
        viewer.chk_geom.setChecked(True)
        viewer.chk_ports.setChecked(True)
        # no redraw errors: the ports block used to raise NameError (eps)
        # whenever the geometry was hidden
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            viewer.chk_geom.setChecked(False)
            viewer.chk_geom.setChecked(True)
            viewer.chk_ports.setChecked(False)
            viewer.chk_ports.setChecked(True)
        assert "redraw error" not in buf.getvalue(), buf.getvalue()
        # zoom survives the toggles / mesh style changes (same view)
        artist_ax = viewer.fig_slice.axes[0]
        artist_ax.set_xlim(-7.0, 7.0)
        artist_ax.set_ylim(-4.0, 4.0)
        viewer.slider_mesh.setValue(70)
        viewer.chk_ports.setChecked(False)
        viewer.chk_geom.setChecked(False)
        artist_ax = viewer.fig_slice.axes[0]
        assert abs(artist_ax.get_xlim()[1] - 7.0) < 1e-9, artist_ax.get_xlim()
        assert abs(artist_ax.get_ylim()[1] - 4.0) < 1e-9, artist_ax.get_ylim()
        viewer.chk_ports.setChecked(True)
        viewer.chk_geom.setChecked(True)
        viewer.slider_mesh.setValue(35)
        viewer._redraw()
        print("  visibility toggles + zoom preservation: OK")

        # --- object colour / transparency -----------------------------------
        viewer.cmb_view.setCurrentText("XY slice")
        viewer.spn_plane.setValue(0.7)
        viewer.chk_geom.setChecked(True)
        viewer.chk_ports.setChecked(False)
        viewer._redraw()

        def _obj_patches(ax):
            """Filled geometry patches (not ports / outlines / highlight)."""
            return [
                p for p in ax.patches
                if p.get_label() not in ("", "_nolegend_", "Ports", "Ports (off)")
                and p.get_facecolor()[3] > 0.0
            ]

        assert viewer.object_style() == {"color": None, "transparency": 30}, (
            viewer.object_style()
        )
        base_faces = {
            tuple(round(c, 3) for c in p.get_facecolor()[:3])
            for p in _obj_patches(viewer.fig_slice.axes[0])
        }
        assert len(base_faces) >= 2, f"per-kind colours missing: {base_faces}"

        seen_styles: list[dict] = []
        viewer.on_object_style_changed = seen_styles.append
        viewer.set_object_style(
            {"color": [0.1, 0.8, 0.2], "transparency": 75}, notify=True
        )
        expected = {"color": [0.1, 0.8, 0.2], "transparency": 75}
        assert seen_styles and seen_styles[-1] == expected, seen_styles
        patches = _obj_patches(viewer.fig_slice.axes[0])
        assert patches, "no geometry drawn"
        for p in patches:
            face = p.get_facecolor()
            assert [round(c, 3) for c in face[:3]] == [0.1, 0.8, 0.2], face
            assert abs(face[3] - 0.25) < 1e-6, f"transparency not applied: {face}"
            edge = p.get_edgecolor()
            if tuple(round(c, 3) for c in edge[:3]) == (1.0, 0.0, 0.0):
                continue  # OUTSIDE DOMAIN marker stays red
            assert edge[3] >= 0.8, f"outline vanished: {edge}"
        assert viewer.lbl_obj_alpha.text() == "75 %", viewer.lbl_obj_alpha.text()
        # the Overview uses the same colour/alpha
        viewer.cmb_view.setCurrentText("Overview")
        ov_faces = {
            tuple(round(c, 3) for c in p.get_facecolor()[:3])
            for p in viewer.fig_slice.axes[0].patches
            if abs(p.get_facecolor()[3] - 0.25) < 1e-6
        }
        assert ov_faces == {(0.1, 0.8, 0.2)}, ov_faces

        # reset -> per-kind colours again
        viewer._reset_object_style()
        assert viewer.object_style() == {"color": None, "transparency": 30}, (
            viewer.object_style()
        )
        assert viewer.lbl_obj_alpha.text() == "30 %", viewer.lbl_obj_alpha.text()
        viewer.on_object_style_changed = None
        viewer.chk_ports.setChecked(True)
        viewer.cmb_view.setCurrentText("XY slice")
        viewer._redraw()
        print("  object colour / transparency (slice + overview): OK")

        # --- ports independent of the structure; legend follows the toggles --
        viewer.cmb_view.setCurrentText("XY slice")
        viewer.spn_plane.setValue(0.7)  # inside the patch feed port z-range
        viewer.chk_geom.setChecked(True)
        viewer.chk_ports.setChecked(True)

        def _legend_texts():
            ax = viewer.fig_slice.axes[0]
            leg = ax.get_legend()
            return [t.get_text() for t in leg.get_texts()] if leg else []

        legend = _legend_texts()
        assert "Ports" in legend, f"port legend entry missing: {legend}"
        assert not any(
            tok in l.lower()
            for l in legend
            for tok in ("port_resist", "port_excite", "port_ut", "port_it", "lumped", "excitation", "probebox")
        ), f"CSX port artefacts leaked into the legend: {legend}"
        # hiding the STRUCTURE must keep the ports visible
        viewer.chk_geom.setChecked(False)
        ax = viewer.fig_slice.axes[0]
        assert len(ax.lines) >= 1, "ports vanished together with the structure"
        assert "Ports" in _legend_texts(), "port legend entry vanished with the structure"
        # hiding the PORTS removes them from plot AND legend
        viewer.chk_ports.setChecked(False)
        ax = viewer.fig_slice.axes[0]
        assert len(ax.lines) == 0, "port marker still drawn with 'Show ports' off"
        assert "Ports" not in _legend_texts(), "port legend entry kept with 'Show ports' off"
        viewer.chk_geom.setChecked(True)
        viewer.chk_ports.setChecked(True)
        viewer._redraw()
        print("  ports independent of structure + legend follows toggles: OK")

        # --- highlight of the object selected in the Project/Geometry list ----
        _YELLOW = (1.0, 212 / 255, 0.0)

        def _yellow(fig) -> int:
            n = 0
            for _ax in fig.axes:
                for _p in _ax.patches:
                    _ec = tuple(_p.get_edgecolor())
                    if _ec[:3] == _YELLOW and abs(_ec[3] - 1.0) < 1e-9:
                        n += 1
            return n

        _pr = max(primitives, key=lambda p: abs(float(p["stop"][2]) - float(p["start"][2])))
        viewer.cmb_view.setCurrentText("Overview")
        viewer._redraw()
        assert _yellow(viewer.fig_slice) == 0, "nothing selected but something is highlighted"
        viewer.set_highlight(
            boxes=[(_pr["start"], _pr["stop"])],
            names=[str(_pr.get("name", ""))],
            label="Prim: test",
        )
        assert viewer.highlight()["label"] == "Prim: test"
        assert viewer._is_highlighted(_pr["start"], _pr["stop"]), "box match failed"
        assert not viewer._is_highlighted((0.0, 0.0, 0.0), (0.1, 0.1, 0.1)), "false match"
        assert _yellow(viewer.fig_slice) >= 1, "highlighted object not outlined"
        # name matching also accepts the CSX form ("copper" -> "copper#1")
        viewer.set_highlight(names=[str(_pr.get("material", "copper"))])
        assert viewer._is_highlighted(None, None, f"{_pr.get('material', 'copper')}#1")
        viewer.clear_highlight()
        assert not viewer.highlight()["boxes"] and not viewer.highlight()["names"]
        assert _yellow(viewer.fig_slice) == 0, "highlight not cleared"
        print("  geometry-list highlight (box + name, overview/slice): OK")

        # --- the outline/label must follow EVERY click, even when the slice
        # plane does not move ------------------------------------------------
        # The plane spinbox rounds to 4 decimals and the CSX boxes are
        # float32-rounded, so moving the plane onto the object's middle is
        # often a no-op: no valueChanged -> no redraw. The slice then kept the
        # PREVIOUS object's outline and title (report: "msl_line" selected in
        # the list, "msl_stub" still highlighted in the slice).
        _pr2 = max(
            primitives, key=lambda p: abs(float(p["stop"][2]) - float(p["start"][2]))
        )
        assert abs(float(_pr2["stop"][2]) - float(_pr2["start"][2])) > 0.1, (
            "test needs a primitive with a z extent (substrate)"
        )
        _mid2 = 0.5 * (float(_pr2["start"][2]) + float(_pr2["stop"][2]))

        class _RoundedSpin:
            """Spinbox stand-in: setValue() is swallowed (value unchanged)."""

            def __init__(self, value: float) -> None:
                self._value = float(value)

            def value(self) -> float:
                return self._value

            def setValue(self, value: float) -> None:  # noqa: ARG002
                pass

        viewer.cmb_view.setCurrentText("XY slice")

        def _slice_title() -> str:
            return viewer.fig_slice.axes[0].get_title()

        _real_spin = viewer.spn_plane
        try:
            viewer.spn_plane = _RoundedSpin(_mid2 + 1e-5)  # 4 decimals -> same
            viewer.set_highlight(
                boxes=[(_pr2["start"], _pr2["stop"])], label="Prim: first"
            )
            assert "Prim: first" in _slice_title(), _slice_title()
            viewer.set_highlight(
                boxes=[(_pr2["start"], _pr2["stop"])],
                label="Prim: second",
                focus=True,
            )
            assert "Prim: second" in _slice_title(), (
                "highlight did not repaint without a plane change: "
                f"{_slice_title()!r}"
            )
            # a selection that matches nothing must refresh the title as well
            viewer.set_highlight(
                boxes=[], names=["does-not-exist"], label="Prim: nowhere", focus=True
            )
            assert "Prim: nowhere" in _slice_title(), _slice_title()
        finally:
            viewer.spn_plane = _real_spin
        viewer.clear_highlight()
        print("  highlight repaints on every click (same plane): OK")

        # --- port perpendicular to the slice (dipole style) -------------------
        # x-directed port sitting in the z=0 plane: in the XY view its extent
        # along the normal is zero, so belonging to the local cell it must
        # still follow "Show ports" (regression: only the exact plane worked).
        from app.models.project import PortDef

        viewer.set_project_context(
            project=project,
            primitives=primitives,
            ports=[
                PortDef(
                    name="inX",
                    start=[-5.0, 1.0, 0.0],
                    stop=[5.0, 1.0, 0.0],
                    direction="x",
                )
            ],
            nf2ff=project.nf2ff,
        )
        viewer.cmb_view.setCurrentText("XY slice")
        # slice inside the port's own cell but off the port plane (the tolerance
        # is the local cell size)
        mesh_z = mesh_lines_from_csx(CSX)
        span = viewer._cell_span(mesh_z, "z", 0.0)
        viewer.spn_plane.setValue(0.5 * span)
        viewer._redraw()
        viewer.chk_ports.setChecked(True)
        ax = viewer.fig_slice.axes[0]
        n_on = (len(ax.lines), len(ax.patches))
        viewer.chk_ports.setChecked(False)
        ax = viewer.fig_slice.axes[0]
        n_off = (len(ax.lines), len(ax.patches))
        assert n_on != n_off, f"perpendicular port must follow 'Show ports': {n_on} == {n_off}"
        viewer.set_project_context(
            project=project,
            primitives=primitives,
            ports=project.ports,
            nf2ff=project.nf2ff,
        )
        viewer.spn_plane.setValue(0.7)
        viewer.chk_ports.setChecked(True)
        viewer._redraw()
        print("  perpendicular port (dipole style) follows 'Show ports': OK")

        # --- every slice view keeps the domain mapping ------------------------
        for view in ("XY slice", "XZ slice", "YZ slice"):
            viewer.cmb_view.setCurrentText("Cell sizes")  # force a view change
            viewer.cmb_view.setCurrentText(view)
            normal = _SLICE_AXES[view.split()[0]][2]
            dom = get_sim_area(CSX)
            assert abs(viewer.spn_plane.minimum() - float(dom[0][_AXIS[normal]])) < 1e-3
            assert abs(viewer.spn_plane.maximum() - float(dom[1][_AXIS[normal]])) < 1e-3
            assert viewer.slider.isEnabled() and viewer.spn_plane.isEnabled()
        print("  per-view plane controls span the domain: OK")

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
        # --- cylinders are drawn as circles (true cross-section) -------------
        from matplotlib.patches import Circle

        cyl = CSX.AddMetal("copper")
        cyl.AddCylinder([0.0, 0.0, -5.0], [0.0, 0.0, 5.0], 2.0, priority=30)
        viewer.set_project_context(
            project=project,
            primitives=primitives,
            ports=project.ports,
            nf2ff=project.nf2ff,
        )
        viewer.set_csx(CSX)
        viewer.cmb_view.setCurrentText("XY slice")
        viewer.spn_plane.setValue(0.0)
        ax = viewer.fig_slice.axes[0]
        radii = [c.radius for c in ax.patches if isinstance(c, Circle)]
        assert any(abs(r - 2.0) < 1e-6 for r in radii), radii
        # project-primitive dicts (axis endpoints + radius) as well
        viewer.set_project_context(
            project=project,
            primitives=list(primitives)
            + [
                {
                    "kind": "cylinder",
                    "name": "cyl1",
                    "material": "copper",
                    "priority": 10,
                    "start": [0.0, 0.0, -5.0],
                    "stop": [0.0, 0.0, 5.0],
                    "radius": 2.0,
                }
            ],
            ports=project.ports,
            nf2ff=project.nf2ff,
        )
        viewer._redraw()
        radii = [c.radius for c in viewer.fig_slice.axes[0].patches if isinstance(c, Circle)]
        assert len(radii) >= 2 and all(abs(r - 2.0) < 1e-6 for r in radii), radii
        print("  cylinders drawn as circles (CSX + project dict): OK")

        # --- Mesh editor: hidden until activated; selection highlights a line ---
        assert viewer.chk_editor.isChecked() is False
        assert viewer._editor_panel.isHidden() is True
        viewer.chk_editor.setChecked(True)
        app.processEvents()
        assert viewer._editor_panel.isHidden() is False
        viewer.cmb_edit_axis.setCurrentText("z")
        assert viewer._sel_line is None
        if viewer.lst_edit_lines.count():
            viewer.lst_edit_lines.setCurrentRow(0)
            app.processEvents()
            assert viewer._sel_line is not None, "selecting a row must set the highlight"
            assert viewer._sel_line[0] == "z"
        viewer.chk_editor.setChecked(False)
        app.processEvents()
        assert viewer._editor_panel.isHidden() is True
        print("  mesh editor panel visibility + selection highlight: OK")

        # set_csx(keep_view=True) keeps the slice plane (auto background rebuild)
        viewer.cmb_view.setCurrentText("XY slice")
        mid = 0.5 * (viewer.spn_plane.minimum() + viewer.spn_plane.maximum())
        viewer.spn_plane.setValue(mid)  # spin rounds to 4 decimals
        viewer.set_csx(viewer._csx, keep_view=True)
        assert abs(viewer.spn_plane.value() - mid) < 1e-3, (viewer.spn_plane.value(), mid)
        print("  set_csx keep_view preserves the plane: OK")

        # clicking a line in the slice selects it in the editor (single click)
        import types as _types

        import numpy as _np
        viewer.chk_editor.setChecked(True)
        viewer.cmb_view.setCurrentText("XY slice")
        xl = _np.sort(_np.asarray(mesh_lines_from_csx(viewer._csx)["x"], float))
        target = float(xl[len(xl) // 2])
        ev = _types.SimpleNamespace(
            button=1, key=None, inaxes=viewer.fig_slice.axes[0], xdata=target, ydata=0.0
        )
        viewer._last_click_t = 0.0  # bypass the double-click debounce
        viewer._on_slice_click(ev)
        assert viewer._sel_line is not None and viewer._sel_line[0] == "x", viewer._sel_line
        print("  click-select a line in the slice: OK")

        # clicking the SAME line again undoes the selection (toggle)
        viewer._last_click_t = 0.0
        viewer._on_slice_click(ev)
        assert not viewer._selected_lines, viewer._selected_lines
        assert viewer._sel_line is None, viewer._sel_line
        assert not viewer.lst_edit_lines.selectedItems(), "editor row still selected"
        viewer._last_click_t = 0.0
        viewer._on_slice_click(ev)  # ... and a third click selects it again
        assert viewer._selected_lines == [("x", target)], viewer._selected_lines
        print("  clicking the selected line again deselects it: OK")

        # right-click "Refresh view (keep zoom)": redraw, zoom kept, selection off
        _ax = viewer.fig_slice.axes[0]
        _ax.set_xlim(-3.0, 4.0)
        _ax.set_ylim(-1.0, 2.0)
        _before = (_ax.get_xlim(), _ax.get_ylim())
        viewer.refresh_view()
        _after = (
            viewer.fig_slice.axes[0].get_xlim(),
            viewer.fig_slice.axes[0].get_ylim(),
        )
        assert _before == _after, (_before, _after)
        assert not viewer._selected_lines and viewer._sel_line is None
        assert not viewer.lst_edit_lines.selectedItems(), "editor row kept after refresh"
        # ... and the mesh is drawn again (the slice still has its collections)
        assert viewer.fig_slice.axes[0].collections, "refresh drew nothing"
        print("  refresh view keeps the zoom + drops the selection: OK")

        # a click in free space ADDS a line (no snap band there)
        viewer.cmb_edit_axis.setCurrentText("x")
        # pick a coordinate a full band away from any x line
        span = float(_np.max(xl) - _np.min(xl))
        free = None
        for cand in _np.linspace(float(_np.min(xl)), float(_np.max(xl)), 2000):
            if _np.min(_np.abs(xl - cand)) > 0.01 * span:
                free = float(cand)
                break
        if free is not None:
            ev2 = _types.SimpleNamespace(
                button=1, key=None, inaxes=viewer.fig_slice.axes[0], xdata=free, ydata=0.0
            )
            viewer._last_click_t = 0.0
            viewer._on_slice_click(ev2)
            _adds = (viewer._project.mesh.manual_mesh_lines or {}).get("x", [])
            assert any(abs(v - free) < 1e-6 for v in _adds), (free, _adds)
            print("  click in free space adds a line: OK")

        # multi-selection + Del-style removal of several lines at once
        viewer.cmb_edit_axis.setCurrentText("z")
        viewer.spn_edit_line.setValue(12.5)
        viewer._editor_add()
        viewer.spn_edit_line.setValue(-12.5)
        viewer._editor_add()
        for _i in range(viewer.lst_edit_lines.count()):
            if viewer.lst_edit_lines.item(_i).text().startswith("+"):
                viewer.lst_edit_lines.item(_i).setSelected(True)
        assert len(viewer._selected_lines) >= 2, viewer._selected_lines
        viewer._editor_remove_selected()
        _zadds = (viewer._project.mesh.manual_mesh_lines or {}).get("z", [])
        assert not _zadds, _zadds
        print("  multi-select + delete several lines: OK")

        # geometry-edge snapping: a click near an edge snaps onto it
        _ez = viewer._edge_coords("z")
        assert _ez, "expected geometry edges"
        _span = viewer._axis_span("z")
        _e = _ez[len(_ez) // 2]
        assert abs(viewer._snap_to_edge("z", _e + 0.005 * _span) - _e) < 1e-9
        assert abs(
            viewer._snap_to_edge("z", _e + 0.4 * _span) - (_e + 0.4 * _span)
        ) < 1e-9  # far away -> unchanged
        print("  geometry-edge snapping: OK")

        # no mesh lines outside the domain
        _dr = viewer._domain_range("z")
        if _dr is not None:
            _lo, _hi = _dr
            assert abs(viewer._clamp_to_domain("z", _lo - 1000.0) - _lo) < 1e-9
            assert abs(viewer._clamp_to_domain("z", _hi + 1000.0) - _hi) < 1e-9
            viewer.cmb_edit_axis.setCurrentText("z")
            viewer._editor_reset_all()
            viewer._editor_add(_hi + 1000.0)  # clamped into the domain
            _zadds = (viewer._project.mesh.manual_mesh_lines or {}).get("z", [])
            assert _zadds and all(_lo - 1e-6 <= v <= _hi + 1e-6 for v in _zadds), _zadds
            print("  no lines outside the domain (clamp): OK")

        print("MESH VIEWER SMOKE PASS")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
