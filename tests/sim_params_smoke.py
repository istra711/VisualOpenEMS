"""Smoke: Sim Params tab — computed values + overrides + patch apply."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.settings import AppSettings  # noqa: E402


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    settings = AppSettings.load()
    win = MainWindow(settings)
    win.show()
    app.processEvents()

    tabs = [win.sparam_tabs.tabText(i) for i in range(win.sparam_tabs.count())]
    # the Sim Params tab page was removed — the Simulation Parameters window
    # (Simulation menu) replaced it; the panel stays as a hidden sync target
    assert "Sim Params" not in tabs, f"Sim Params tab should be removed: {tabs}"
    assert win.sim_params is not None

    # Load patch → project + sim params populated
    win._on_load_patch()
    app.processEvents()
    assert win.project.name == "patch_antenna"
    assert win.project.sim.max_timesteps == 200_000
    assert win.project.boundary.zmin == "PEC"
    assert "λ_air" in win.sim_params.lbl_computed.text()
    assert "200000" in win.sim_params.lbl_effective.text()

    # Min cell size moved out of Sim Params → Mesh Parameters window
    win._on_open_mesh_params_dialog()
    win._mesh_params_dlg.panel.spn_min_cell.setValue(0.2)
    win._apply_mesh_params_dialog()
    app.processEvents()

    # Override parameters via form
    win.sim_params.spn_max_ts.setValue(120_000)
    win.sim_params.spn_end_criteria.setValue(-40.0)  # dB -> linear 1e-4
    assert int(win.sim_params.spn_num_threads.value()) == int(
        win.project.sim.num_threads or 0
    )
    win.sim_params.spn_num_threads.setValue(4)
    win.sim_params.cmb_b["xmin"].setCurrentText("PML")
    win.sim_params.spn_air.setValue(30.0)
    win.sim_params.chk_air_auto.setChecked(False)
    win._on_sim_params_changed()
    app.processEvents()

    win._sync_project_from_form()
    assert win.project.sim.max_timesteps == 120_000
    assert abs(win.project.sim.end_criteria - 1e-4) < 1e-12
    assert int(win.project.sim.num_threads) == 4
    assert "4" in win.sim_params.lbl_effective.text()
    assert win.project.boundary.xmin == "PML"
    assert win.project.boundary.air_margin == [30.0] * 6
    assert win.project.mesh.min_cellsize == 0.2
    assert "120000" in win.sim_params.lbl_effective.text()

    # Patch geometry edit + apply
    win.sim_params.patch_params["patch_width"] = 33.0
    win.sim_params.patch_params["feed_pos"] = -5.5
    win._on_apply_patch_params()
    app.processEvents()
    names = [p["name"] for p in win.primitives]
    assert "patch" in names
    assert win.project.patch_params["patch_width"] == 33.0
    # port start uses feed_pos
    assert abs(win.project.ports[0].start[0] - (-5.5)) < 1e-9
    assert "33.0" in win.sim_params.lbl_patch.text() or "33" in win.sim_params.lbl_patch.text()

    # Max timesteps respected (warn only) — check project not force-raised on run sync
    win.sim_params.spn_max_ts.setValue(50_000)
    win._sync_project_from_form()
    assert win.project.sim.max_timesteps == 50_000

    # Independent start/stop frequencies (not forced to f0±fc)
    # Spins show the current display unit (GHz by default) — set display values
    win.spn_start_f.setValue(2.0)
    win.spn_stop_f.setValue(2.8)
    win._on_freq_range_changed()
    app.processEvents()
    win._sync_project_from_form()
    assert abs(win.project.mesh.start_frequency - 2.0e9) < 1.0
    assert abs(win.project.mesh.stop_frequency - 2.8e9) < 1.0
    # f0 kept in band, fc updated to half-width
    assert 2.0e9 <= win.project.mesh.target_frequency <= 2.8e9
    assert abs(win.project.mesh.target_frequency - win.spn_f0.value() * win._freq_unit_factor) < 1.0

    # f0/fc path still derives start/stop
    win.spn_f0.setValue(2.45)
    win.spn_fc.setValue(0.3)
    win._on_mesh_form_changed()
    app.processEvents()
    win._sync_project_from_form()
    assert abs(win.project.mesh.start_frequency - (2.45e9 - 0.3e9)) < 1.0
    assert abs(win.project.mesh.stop_frequency - (2.45e9 + 0.3e9)) < 1.0

    # --- NF2FF tab: two tabs, frequency modes, unit list, count expansion ---
    from app.postproc.nf2ff import resolve_eval_frequencies

    panel = win.sim_params
    tab_labels = [panel.tabs.tabText(i) for i in range(panel.tabs.count())]
    assert tab_labels == ["Simulation-Parameter", "NF2FF-Parameter"], tab_labels
    # Frequency list mode with per-entry units
    panel.cmb_nf2ff_mode.setCurrentText("Frequency list")
    assert panel.txt_nf2ff_freqs.isEnabled()
    assert not panel.spn_nf2ff_count.isEnabled()
    panel.txt_nf2ff_freqs.setText("2.4 GHz, 2.45 GHz, 2500 MHz")
    win._sync_project_from_form()
    assert win.project.nf2ff.freq_mode == "list"
    assert win.project.nf2ff.frequencies == [2.4e9, 2.45e9, 2.5e9]
    assert resolve_eval_frequencies(win.project) == [2.4e9, 2.45e9, 2.5e9]
    # N-points mode -> equally spaced over the mesh range (2.15..2.75 GHz here)
    panel.cmb_nf2ff_mode.setCurrentText("N points (mesh range)")
    assert panel.spn_nf2ff_count.isEnabled()
    assert not panel.txt_nf2ff_freqs.isEnabled()
    panel.spn_nf2ff_count.setValue(5)
    win._sync_project_from_form()
    assert win.project.nf2ff.freq_mode == "count"
    assert win.project.nf2ff.freq_count == 5
    f_eval = resolve_eval_frequencies(win.project)
    assert len(f_eval) == 5
    assert abs(f_eval[0] - 2.15e9) < 1.0
    assert abs(f_eval[-1] - 2.75e9) < 1.0
    # effective-settings readout mentions the eval mode
    win.sim_params.refresh_computed(win.project)
    app.processEvents()
    assert "eval:" in win.sim_params.lbl_effective.text()
    assert "5 x" in win.sim_params.lbl_effective.text()

    # --- Apply in the dialog refreshes an existing run script ---
    import json as _json
    import shutil
    import tempfile

    old_work_dir = win.settings.work_dir
    tmp = Path(tempfile.mkdtemp(prefix="simparams_smoke_"))
    try:
        win.settings.work_dir = str(tmp)
        work = tmp / win.project.name
        win._generate_script(work)  # script exists -> Apply must refresh it
        copy_path = work / "project_copy.json"
        assert copy_path.exists()
        assert _json.loads(copy_path.read_text(encoding="utf-8"))["sim"][
            "max_timesteps"
        ] == 50_000
        win._on_open_sim_params_dialog()
        app.processEvents()
        dlg = win._sim_params_dlg
        dlg.panel.spn_max_ts.setValue(123_456)
        dlg.btn_apply.click()
        app.processEvents()
        data = _json.loads(copy_path.read_text(encoding="utf-8"))
        assert data["sim"]["max_timesteps"] == 123_456, data["sim"]
        assert (work / "project_run.py.lastgen").exists()
        # no script yet -> Apply must NOT create one (first gen via Run/editor)
        shutil.rmtree(tmp / "other_project", ignore_errors=True)
        win.project.name = "other_project"
        win._apply_sim_params_dialog()
        app.processEvents()
        assert not (tmp / "other_project" / "project_run.py").exists()
        win.project.name = "patch_antenna"
    finally:
        win.settings.work_dir = old_work_dir
        shutil.rmtree(tmp, ignore_errors=True)

    # --- Antenna simulation mode: NF2FF box parameter round-trips ---
    win.sim_params.chk_antenna.setChecked(True)
    win.sim_params.spn_ant_nf2ff.setValue(0.75)
    win.sim_params.spn_ant_pml.setValue(0.75)
    win._sync_project_from_form()
    assert win.project.sim.antenna_mode is True
    assert abs(win.project.sim.antenna_nf2ff_lambda - 0.75) < 1e-9
    assert win.sim_params.spn_ant_nf2ff.isEnabled()
    win.sim_params.chk_antenna.setChecked(False)
    assert not win.sim_params.spn_ant_nf2ff.isEnabled()
    win._sync_project_from_form()
    assert win.project.sim.antenna_mode is False
    app.processEvents()

    # --- Per-side air margins, global/auto gating and PCB presets ---------
    panel = win.sim_params
    panel.sync_from_project(win.project)
    panel.chk_air_auto.setChecked(False)
    panel.chk_air_uniform.setChecked(False)
    assert all(sp.isEnabled() for sp in panel.spn_air_sides.values())
    for axis, value in zip(
        ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax"),
        (0.0, 0.0, 0.0, 0.0, 0.0, 8.6614),
    ):
        panel.spn_air_sides[axis].setValue(value)
    win._sync_project_from_form()
    assert win.project.boundary.air_margin == [0.0, 0.0, 0.0, 0.0, 0.0, 8.6614]
    panel.chk_air_uniform.setChecked(True)
    assert not any(sp.isEnabled() for sp in panel.spn_air_sides.values())
    assert panel.spn_air.isEnabled()
    panel.chk_air_auto.setChecked(True)
    assert not panel.chk_air_uniform.isEnabled()
    assert not panel.spn_air.isEnabled()

    panel.chk_pcb_defaults.setChecked(True)
    assert [round(panel.spn_air_sides[a].value(), 4) for a in (
        "xmin", "xmax", "ymin", "ymax", "zmin", "zmax"
    )] == [10.0, 10.0, 10.0, 10.0, 0.0, 10.0]
    assert panel.cmb_b["zmin"].currentText() == "PEC"
    assert panel.cmb_b["xmin"].currentText() == "MUR"
    panel.spn_pcb_lid_z.setValue(6.5)
    panel.chk_pcb_case.setChecked(True)
    assert [round(panel.spn_air_sides[a].value(), 4) for a in (
        "xmin", "xmax", "ymin", "ymax", "zmin", "zmax"
    )] == [0.0, 0.0, 0.0, 0.0, 0.0, 6.5]
    assert all(cb.currentText() == "PEC" for cb in panel.cmb_b.values())
    win._sync_project_from_form()
    assert win.project.boundary.air_margin == [0.0, 0.0, 0.0, 0.0, 0.0, 6.5]

    # NF2FF on -> automatic box fit; switching to Manual keeps the box.
    app.processEvents()
    win.chk_nf2ff.setChecked(False)
    app.processEvents()
    win.chk_nf2ff.setChecked(True)
    app.processEvents()
    assert win.project.nf2ff.enabled and win.project.sim.nf2ff_enabled
    assert win.project.nf2ff.box_mode == "auto"
    assert win.project.nf2ff.start != [-1.0, -1.0, -1.0], (
        "enabling NF2FF must fit the box to the geometry, not keep the default"
    )
    manual_idx = panel.cmb_nf2ff_box_mode.findData("manual")
    assert manual_idx >= 0
    panel.cmb_nf2ff_box_mode.setCurrentIndex(manual_idx)
    win._sync_project_from_form()
    assert win.project.nf2ff.box_mode == "manual"

    win.close()
    print("SIM PARAMS OK", tabs, tab_labels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
