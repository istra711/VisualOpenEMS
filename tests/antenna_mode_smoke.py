"""Smoke: Simulation Parameters → Antenna simulation mode (FDTD hints)."""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

C0 = 299792458.0


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from app.meshing.rules import air_margin_units, compute_targets
    from app.models.project import Project
    from app.ui.sim_params import SimParamsPanel
    from app.ui.tab_help import TAB_HELP, TAB_ORDER

    # --- help topic exists and is reachable from the dialog combo ---
    assert "Antenna Simulation" in TAB_ORDER, TAB_ORDER
    helptext = TAB_HELP["Antenna Simulation"]
    for needle in (
        "15",
        "numerical dispersion",
        "sub-pixel",
        "permittivity",
        "PML",
        "staircasing",
        "MoM / FEM",
        "Antenna simulation mode",
    ):
        assert needle.lower() in helptext.lower(), f"help topic misses: {needle}"
    assert "Antenna simulation" in TAB_HELP["Simulation Parameters"]

    # --- panel round trip -------------------------------------------------
    panel = SimParamsPanel()
    p = Project.new_default("antenna_smoke")
    p.mesh.start_frequency = 1.95e9
    p.mesh.stop_frequency = 2.95e9
    p.mesh.preset = "medium"
    p.sim.nf2ff_enabled = False
    p.nf2ff.enabled = False
    panel.sync_from_project(p)
    app.processEvents()

    # OFF by default: air margin controls are usable, nothing forced
    assert not panel.chk_antenna.isChecked()
    assert panel.chk_air_auto.isEnabled()
    assert panel.cmb_ant_density.isEnabled() is False
    assert panel.spn_ant_pml.isEnabled() is False

    # --- switch the mode on (user click path) ---
    panel.chk_antenna.setChecked(True)
    app.processEvents()
    assert panel.cmb_ant_density.isEnabled()
    assert panel.spn_ant_pml.isEnabled()
    assert not panel.chk_air_auto.isEnabled()
    assert not panel.spn_air.isEnabled()
    assert panel.chk_nf2ff_enabled.isChecked(), "antenna mode must turn NF2FF on"

    # density combo default = high (20 cells/λ) → project preset
    preset = panel.cmb_ant_density.currentData()
    assert preset in {"low", "medium", "high", "very_high"}
    panel.cmb_ant_density.setCurrentIndex(
        next(
            i for i in range(panel.cmb_ant_density.count())
            if panel.cmb_ant_density.itemData(i) == "very_high"
        )
    )

    panel.write_to_project(p)
    assert p.sim.antenna_mode is True
    assert math.isclose(p.sim.antenna_pml_lambda, 0.5)
    assert p.mesh.preset == "very_high", p.mesh.preset
    assert p.nf2ff.enabled and p.sim.nf2ff_enabled

    # air margin = λ/2 at f_start on all faces (drawing units = mm here)
    lam_half_m = C0 / p.mesh.start_frequency / 2.0
    want = lam_half_m / p.mesh.unit_to_meters()
    got = list(p.boundary.air_margin)
    assert all(not isinstance(a, str) for a in got), got
    assert all(math.isclose(float(a), want, rel_tol=1e-9) for a in got), (got, want)
    # the (disabled) form controls mirror what was stored (spin: 3 decimals)
    assert math.isclose(float(panel.spn_air.value()), want, abs_tol=0.001)
    assert not panel.chk_air_auto.isChecked()

    # PML distance knob: λ at f_start (frac = 1.0)
    panel.spn_ant_pml.setValue(1.0)
    panel.write_to_project(p)
    assert all(
        math.isclose(float(a), 2.0 * want, rel_tol=1e-9) for a in p.boundary.air_margin
    )

    # resolved margin matches the meshing rules helper
    resolved = air_margin_units(p.mesh, p.boundary.air_margin, compute_targets(p.mesh))
    assert all(math.isclose(r, 2.0 * want, rel_tol=1e-9) for r in resolved), resolved

    # --- project JSON round trip keeps the mode ---
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "ant.fcsim.json"
        p.save(path)
        q = Project.load(path)
    assert q.sim.antenna_mode is True
    assert math.isclose(q.sim.antenna_pml_lambda, 1.0)

    # --- turn it off: normal auto/fixed controls come back ---
    panel.sync_from_project(q)
    panel.chk_antenna.setChecked(False)
    assert panel.chk_air_auto.isEnabled()
    assert panel.spn_air.isEnabled()  # auto was off in q → fixed value editable
    panel.chk_air_auto.setChecked(True)
    panel.write_to_project(q)
    assert q.sim.antenna_mode is False
    assert list(q.boundary.air_margin) == ["auto"] * 6
    # preset is left alone while the mode is off
    assert q.mesh.preset == "very_high"

    # --- effective_mesh(): antenna mode OVERRIDES the Mesh-tab resolution ----
    from app.meshing.rules import wavelength_m

    em_proj = Project.new_default("antenna_override")
    em_proj.mesh.start_frequency = 1.95e9
    em_proj.mesh.stop_frequency = 2.95e9
    em_proj.mesh.preset = "very_high"
    em_proj.mesh.target_frequency = 2.45e9  # design frequency (coarser!)
    em_proj.mesh.refined_cellsize = 1.0  # deliberately finer -> must survive
    em_proj.mesh.max_cellsize = None
    em_proj.sim.antenna_mode = False

    # mode OFF: identical object, nothing touched
    assert em_proj.effective_mesh() is em_proj.mesh

    em_proj.sim.antenna_mode = True
    em_proj.sim.antenna_cells_per_wavelength = 25.0
    em = em_proj.effective_mesh()
    assert em is not em_proj.mesh, "override must return a copy"
    # reference wavelength at f_max (2.95 GHz), NOT the design frequency
    assert math.isclose(em.target_frequency, 2.95e9), em.target_frequency
    cell_units = wavelength_m(2.95e9) / em.unit_to_meters() / 25.0
    assert em.max_cellsize is not None
    assert math.isclose(em.max_cellsize, cell_units, rel_tol=1e-9), em.max_cellsize
    # a finer per-feature refinement is never coarsened
    assert math.isclose(em.refined_cellsize, 1.0), em.refined_cellsize
    # original spec is untouched
    assert math.isclose(em_proj.mesh.target_frequency, 2.45e9)
    assert em_proj.mesh.max_cellsize is None

    # the validator target now caps cells at the antenna density ...
    t_em = compute_targets(em)
    t_off = compute_targets(em_proj.mesh)
    assert math.isclose(t_em.max_cell_units, cell_units, rel_tol=1e-9)
    # ... whereas the un-overridden spec keeps the loose lambda/10 cap
    assert math.isclose(
        t_off.max_cell_units, wavelength_m(2.95e9) / t_off.unit_m / 10.0, rel_tol=1e-9
    )

    # a coarser explicit refined_cellsize / max_cellsize is tightened
    em_proj.mesh.refined_cellsize = 10.0
    em_proj.mesh.max_cellsize = 50.0
    em2 = em_proj.effective_mesh()
    assert math.isclose(em2.refined_cellsize, cell_units, rel_tol=1e-9)
    assert math.isclose(em2.max_cellsize, cell_units, rel_tol=1e-9)

    # density falls back to the preset mapping when the field is unset
    em_proj.sim.antenna_cells_per_wavelength = None
    em3 = em_proj.effective_mesh()
    assert math.isclose(
        em3.max_cellsize, wavelength_m(2.95e9) / em3.unit_to_meters() / 25.0, rel_tol=1e-9
    )

    print("ANTENNA MODE: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
