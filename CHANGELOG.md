# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Field dumps → VTK (.vtr) + an embedded VTK 3D viewer**
  (`app/postproc/vtk_export.py`, `app/ui/vtk_viewer.py`, `app/ui/main_window.py`,
  `app/ui/tab_help.py`): a new **VTK** tab (Simulation menu → *VTK Viewer* /
  *Export field dumps to VTK…*) converts the openEMS HDF5 field dumps
  (`simulation/*.h5`, excluding `nf2ff_*`) into VTK **rectilinear grids**
  `<project>/vtk/*.vtr` with the vector **`field`** (real part) and the scalar
  **`magnitude`** = |field| as point data, and shows them in a 3D view embedded
  via VTK's Qt interactor (`QVTKRenderWindowInteractor`). UI: iso-surface of
  |field| with a level slider, domain outline, orientation axes, reset view;
  "Open .vtr…" for existing files. The `.vtr` files also open in ParaView.
  Dumps that are empty/degenerate (e.g. a single point — `start == stop`) are
  skipped with a hint. On a headless/offscreen session OpenGL is unavailable, so
  the embedded 3D view is disabled gracefully (the files are still written) —
  VTK's render window hard-crashes without a display. `build_exe.py` no longer
  excludes `vtk` (and collects its binaries) so the bundled EXE includes the
  viewer. Tests: `tests/test_vtk_export.py` (converter round-trip) +
  `tests/vtk_viewer_smoke.py` (conversion + viewer load). The viewer's
  "Open…" reader is **format-agnostic** — it picks the reader from the
  extension (.vtr/.vti/.vtu/.vtp/.vts XML or legacy .vtk), so ParaView example
  files open too; the scalar shown is "magnitude" if present, else the active /
  first point array. The default representation is **Surface** (the dataset
  coloured by the scalar, with a colour bar — like ParaView); Iso-surface and
  Wireframe are selectable.
  Also: **new field dumps default to the geometry bbox** (`_on_add_dump` /
  `_default_dump_box`) instead of a zero-size `[0,0,0]`→`[0,0,0]` box — a
  single-point dump produced an empty `.vtr`; the exporter now skips such dumps
  with a clear message, and the VTK tab is pointed at the **current project
  folder** (`<work>/<project>/simulation`), not the base work dir.

- **VTK viewer: ParaView-style field-dump analysis** (`app/ui/vtk_viewer.py`,
  `app/postproc/vtk_export.py`): reworked after the openEMS "Field Dumps
  Analysis via ParaView" guide. New controls: an **Array** selector (all point +
  cell arrays, e.g. magnitude/field/RotH-Field), a **Representation** combo
  (Surface / Wireframe / **Slice** with axis+position, for 3D boxes / Iso-surface
  with the level slider), **Colour** rescaling (**Auto** data range / manual
  min-max / **Log scale** / "Rescale to data range"), and a **Time** slider +
  **Play** that animates a per-step series. The exporter now writes one
  `.vtr` per stored step (`<name>_0000.vtr …`) plus `<name>.series.json` in the
  batch export; a single `<name>.vtr` (first step) is kept. Tests:
  `tests/test_vtk_export.py` (+ time-series) and `tests/vtk_viewer_smoke.py`.

- **Field dumps: CSXCAD AddDump parameters in the GUI** (`app/models/project.py`
  `DumpDef`, `app/ui/geometry_editors.py` `DumpDialog`, `app/simulation/script_gen.py`):
  the "Add Field Dump…" dialog now exposes **File type** (HDF5 `.h5` default, or
  **VTK `.vtr` written directly by openEMS**), **Interpolation** (None / Node /
  Cell = CSXCAD `dump_mode`), and an optional **Frequencies** list for
  frequency-domain dumps (comma-separated, overrides the single Frequency).
  `DumpDef` gained `file_type`, `dump_mode`, `frequencies` (old projects load
  with defaults); the run script passes them to `CSX.AddDump(...)`, and SAR dumps
  use the 20..22 type range. Test `tests/dump_params_smoke.py`; help updated.

## [0.5.0] - 2026-10-18

### Added

- **Two status-bar progress bars (TS + energy, side by side) and dB end criteria**
  (`app/simulation/progress.py`, `app/ui/main_window.py`, `app/ui/sim_params.py`):
  the run's stopping condition is the residual-energy end criterion (EndCriteria),
  not the timestep budget. The status bar now shows **two bars side by side**:
  a **TS** bar (timesteps/max) and an
  **energy** bar in **percent** — 0 % = energy at start (0 dB), 100 % = the end
  criterion / minimum energy reached. `EngineProgress.energy_percent` /
  `energy_text` + `target_db` (set per run from `sim.end_criteria`).
  The **End criteria (energy)** field in Simulation Parameters is now entered in
  **dB** (e.g. -50 dB = linear 1e-5; stored linearly for openEMS). Tests in
  `tests/test_progress.py`, `progress_smoke.py`, `sim_params_smoke.py`.

- **Mesh Editor — manual mesh lines (Mesh Viewer)**
  (`app/meshing/manual_mesh.py`, `app/models/project.py` `MeshSpec.manual_mesh_lines`
  / `manual_mesh_lines_removed`, `app/meshing/easy_wrapper.py`, `app/ui/mesh_viewer.py`):
  the Mesh Viewer's right-hand panel can now freely add grid lines per axis,
  edit them, and delete auto lines. Pick an axis, type a coordinate and *Add
  line*, or tick *Add by click in slice* and click in a slice view. The list tags
  every line (`+` added / `−` removed / blank = easyMesh) and supports
  Remove/Restore selected, Reset axis / Reset all. Overrides are stored in the
  project and applied app-side **after** easyMesh + the cap, so they survive
  automatic meshing and are not smoothed away; pending edits are overlaid on the
  slice (green dashed = add, red dotted = remove). Click *Build Mesh* to apply
  (the result is validated as usual). Cartesian only. Pure tests in
  `tests/test_manual_mesh.py`; verified end-to-end on the dipole (a manual
  `z=13.5` line is present, a removed `z=1.0` line is gone).

- **Antenna simulation mode now OVERRIDES the mesh resolution at f_max**
  (`app/models/project.py:Project.effective_mesh`, `SimSpec.antenna_cells_per_wavelength`,
  `app/ui/sim_params.py`, `app/meshing/build_task.py`, `app/simulation/script_gen.py`):
  while the mode is on, the density chosen in the antenna group (low/medium/high/
  very_high/**extreme** = 10/15/20/25/40 cells per λ) is enforced with the reference
  wavelength taken at **f_max** (highest band frequency) instead of the design
  frequency. easyMesh derives its air cell size from `target_frequency`
  (`easyMesh/meshingUtils.py`), so the old setup sized the grid at 2.45 GHz and
  under-resolved the top of a 1.95–2.95 GHz band. `effective_mesh()` returns a
  meshing copy (`self.mesh` untouched) that points `target_frequency` at f_max and
  caps `max_cellsize`/`refined_cellsize` at `wavelength(f_max)/density` (a finer
  per-feature refinement is never coarsened). Both the GUI preview and the
  generated run script use it. `app/meshing/rules.py` preset factors now match
  easyMesh's own mapping (medium 15, high 20). For the 26 mm dipole
  (`Dipol_CST_openems_Test_cyl_z_V3`) the max z-cell drops 4.19 → 3.52 mm
  (very_high) and "extreme" reaches ~2.5 mm. Cross-checked against CST
  (`CST/Test_dipole_openems_cst_cyl_2mm_dp`): the CST STL is identical to our
  model (arms z = ±1…±27, Ø 2 mm); at the `.s1p` R 50 reference CST dips at
  2.358 GHz / −18.7 dB vs our 2.320 GHz / −20.1 dB (mean Δ −0.03 dB, RMS 1.31 dB
  over 1.9–2.9 GHz). Tests in `tests/antenna_mode_smoke.py`.

- **Antenna simulation mode: configurable NF2FF box distance**
  (`app/models/project.py` `SimSpec.antenna_nf2ff_lambda`, `app/ui/sim_params.py`,
  `app/meshing/bounds.py`): the far-field integration box only needs to be large
  for antennas, not for PCB-style runs, so the "Antenna simulation" group now
  has an **NF2FF box** spin (distance in λ at f_start). While antenna mode is on,
  *Fit NF2FF to Geometry* places the box at `antenna_nf2ff_lambda × λ(f_start)`
  (λ at the **lowest** band frequency → far field across the whole band), clamped
  to 0.8× the domain air margin so it stays inside the PML. PCB / non-antenna
  runs keep the small `0.7·λ(f_stop)/4` box. Pure helper
  `app.meshing.bounds.nf2ff_fit_margin_units` (+ `domain_air_margin_units`),
  tests in `tests/test_nf2ff_fit_margin.py`; help + antenna tooltip updated. For
  the 58 mm dipole (f 1.95–2.95 GHz, PML 0.5 λ) the box grows from 17.8 mm to
  61.5 mm (≈ λ/2 at f_start) at the default 0.5.

- **Antenna viewer pop-out: frequency is selectable there too**
  (`app/ui/antenna_viewer.py`): the standalone graph window mirrored only the
  plot-type and cmap combos, so the evaluation frequency could not be changed
  while the graph was popped out. The window's top row now also carries a
  *Freq* combo that mirrors the tab combo both ways (selection in the pop-out
  updates the tab and redraws; reloading a file re-syncs the list). Regression
  check in `tests/antenna_viewer_smoke.py`.

### Fixed

- **Run script created imported metal primitives as a dielectric**
  (`app/simulation/script_gen.py:get_or_create_prop`): a primitive whose
  material is not in `project.materials` (e.g. the FreeCAD importer's
  `"copper"`) fell through to `AddMaterial(epsilon=1.0)` — a dielectric, not
  PEC. easyMesh then lost the metal-edge refinement, so the port no longer sat
  on a mesh line and validation aborted with `port_alignment: x=0 is not on a
  mesh line` (exit 2). The fallback now mirrors `build_task._prop_for`:
  `copper`/`pec`/`ground` → `AddMetal`.

- **Tests no longer overwrite the user's `app_settings.json`**
  (`app/paths.py:settings_path`): smoke/pytest runs used the real settings file;
  `MainWindow.save()` on close then wrote the test's temp work dir (and port
  defaults) back, clobbering the user's configuration. When the running script
  is a test/smoke (`…/tests/…` or pytest) a throwaway file in the temp dir is
  used instead.

- **"Run failed: [WinError 87]" — robust subprocess launch**
  (`app/winproc.py`, `app/simulation/runner.py`): the hidden-console flags
  (`STARTUPINFO` + `CREATE_NO_WINDOW`) can make `CreateProcess` fail with
  WinError 87 on some setups (e.g. under a security/sandbox product). The window
  is now suppressed with `CREATE_NO_WINDOW` alone, and `SimulationRunner.start`
  validates `simulation_python` with `is_file()` (an empty value is `Path(".")`
  and would spawn `Popen([".", …])` → WinError 87) and retries a plain `Popen`
  on any `OSError`.

- **openEMS "Run() AssertionError" on a non-canonical sim path**
  (`app/simulation/script_gen.py`): openEMS 0.0.36's `Run` asserts
  `os.getcwd() == os.path.realpath(sim_path)`; a drive-letter case difference
  (`getcwd` returns `c:` but `realpath` `C:`) or a short/symlink name trips it
  (openEMS issues #113/#284). The generated run script now calls
  `FDTD.Run(os.path.realpath(str(out_dir)), …)` so the assertion passes.

- **Mesh Editor: removed line lingered; rebuild now runs in the background**
  (`app/ui/mesh_viewer.py`, `app/ui/main_window.py`): deleting a line left a red
  "removed line" overlay in the slice and the entry in the list even after the
  grid line was actually gone. The overlay and the list now show the *effective*
  grid — a pending removal is tagged `−` and drawn while the auto line is still
  present, and both disappear once the rebuild has dropped it. Editing a line now
  triggers an automatic **background** rebuild (debounced, 400 ms) that preserves
  the visible view: `set_csx(keep_view=True)` keeps the view type, slice plane
  and zoom, and no tab is forced to the front — the edited line simply vanishes.
  Regression checks in `tests/mesh_viewer_smoke.py` (visibility, selection
  highlight, `keep_view` plane preservation). The line list supports
  **multi-selection** (Shift/Ctrl) with Delete/Backspace removing the whole
  selection, a **single left-click** on a line in the slice selects it (axis
  combo follows), and a **right-click** menu toggles the editor, deletes the
  selected line(s) (like Del) and pops the viewer out.

- **Mesh Editor snaps a new line onto geometry edges**
  (`app/ui/mesh_viewer.py`): clicking to add a line now snaps the coordinate
  onto the nearest geometry edge (primitive AABB faces from the CSX infos +
  port start/stop) when the click is within ~1 % of the axis span — so a line
  can be placed exactly on a feature edge (useful when easyMesh left no line
  there). `_edge_coords` / `_snap_to_edge`; `mesh_viewer_smoke` covers it.

- **"New Scene (no mesh)" — build the mesh by hand**
  (`app/meshing/build_task.py` `build_scene_csx` / `build_mesh=False`,
  `app/ui/main_window.py` `_on_prepare_scene`, Mesh menu): builds the CSX with
  geometry, ports and boundary but runs **NO easyMesh** — the grid is set to the
  domain box (geometry bbox + air margin) only, so the Mesh Viewer shows the
  geometry, the FDTD domain and the NF2FF box on an empty grid. The domain-corner
  grid is the editor's base, so manual lines build the grid up visibly; nothing
  is written to disk. Menu item "New Scene (no mesh)…"; `menu_smoke` checks it.

- **Mesh Editor: no mesh lines outside the domain**
  (`app/ui/mesh_viewer.py`, `app/meshing/manual_mesh.py`): the coordinate spin is
  limited to the FDTD domain range, and a click or typed value outside it is
  clamped to the domain edge (`_domain_range` / `_clamp_to_domain`). As a safety
  net the kernel (`apply_lines_axis`) ignores any add outside the auto-grid
  extent (= the meshed domain) and reports it (`stats["outside"]`), surfaced as a
  WARNING in the mesh report. Tests in `tests/test_manual_mesh.py` +
  `mesh_viewer_smoke`.

- **Mesh Editor overrides are robust to a changed auto mesh**
  (`app/meshing/manual_mesh.py`, `app/meshing/easy_wrapper.py`,
  `app/meshing/build_task.py`): the overrides are absolute coordinates but the
  auto grid is rebuilt from scratch on every build, so a slightly changed mesh
  could make a removal miss its line (silent no-op) or land elsewhere. A removal
  now drops the grid line **nearest** the stored coordinate within a local-cell
  tolerance; removals that find no line are reported (`missed`) and surfaced as
  a **WARNING in the mesh report** together with the applied `+added/-removed`
  counts (and the snap `drift`), instead of silently producing a different mesh.
  Tests in `tests/test_manual_mesh.py` (nearest-line snap, miss report).

- **Mesh Editor interaction: click in a slice adds a line; Del removes the
  selection** (`app/ui/mesh_viewer.py`): a left-click in **free space** now adds
  a line at that coordinate (previously you had to tick "Add by click"); a click
  **on** a line selects it only within a **narrow band** (~0.6 % of the axis),
  and **Shift+click** always adds (place several in a row). The double-click
  view reset no longer inserts a stray line (click debounce). The line list keeps
  its Shift/Ctrl multi-selection and now removes the whole selection with
  **Del/Backspace** via an event filter (works regardless of which child has
  focus — the previous `QShortcut` needed the list itself focused). The
  "Add by click in slice" checkbox is gone (add-on-click is the default now).

- **Mesh Editor edits the existing mesh; "Rebuild from scratch" + "Build Mesh"
  label** (`app/meshing/easy_wrapper.py`, `app/meshing/build_task.py`,
  `app/meshing/manual_mesh.py`, `app/ui/mesh_viewer.py`, `app/ui/main_window.py`):
  adding/removing a line now re-derives the grid from the **last auto mesh** +
  the overrides (cached via `MeshBuildResult.auto_mesh_lines` /
  `MeshViewer.set_auto_grid`) and updates the current mesh in place — no
  easyMesh re-run, no view reset ("always use the existing mesh"). A new
  **Rebuild from scratch…** button (also a **Mesh menu** item) discards all
  manual lines after a confirmation dialog and rebuilds with easyMesh. The meshing action is renamed
  from the unwieldy "Build CSX + Mesh + Validate" to **"Build Mesh"** (button +
  Ctrl+M menu, help updated). `manual_mesh.apply_lines_axis` is the shared pure
  add/remove step. `tests/menu_smoke.py` updated.

- **Delete Mesh (Mesh menu)** (`app/ui/main_window.py` `_on_delete_mesh`,
  `app/ui/mesh_viewer.py` `clear_mesh`): a new **Delete Mesh…** menu item clears
  the Mesh Viewer and removes the stored `csx.xml` / `mesh_preview.png` from the
  project folder, after a confirmation dialog. The project, geometry and manual
  lines are untouched; the automatic re-mesh on opening the viewer is suppressed
  until the next explicit Build Mesh. `menu_smoke` checks the entry.

- **NF2FF "egg" pattern was a stale box `center`, not the box shape**
  (`app/meshing/bounds.py`, `app/simulation/script_gen.py`): the far-field sphere
  is expanded around `project.nf2ff.center`, so the box must be centred on that
  same point. Projects fitted *before* the symmetric-margin fix carry a `center`
  taken from the old asymmetric box (e.g. the 26 mm dipole had `center z = 6.15`
  = midpoint of `-45.4…+57.7`). An off-centre reference distorts the pattern —
  on identical recorded fields, `center z 0 → 6.15` alone moved Dmax 2.13 → 3.30 dBi
  and the beamwidth 78° → 45°. The run now checks the pure box geometry
  (`nf2ff_center_offset`, no knowledge of the source shape needed — the radiator
  may be arbitrarily asymmetric) and snaps `center` to the box centre with a
  warning. With the correct centre our dipole matches CST (Dmax 2.13 dBi @ 90°,
  HPBW 78.0°, η 1.00 vs CST 2.21 dBi / 78.3° / ≈1.0). Tests in
  `tests/test_nf2ff_fit_margin.py`. Help documents the rule (Antenna Simulation
  → NF2FF box §5, Antenna tips, Ports / Dumps): the evaluation centre must equal
  the box centre (hard requirement), equal margins around the geometry are the
  recommendation, and there is only one box (the FDTD domain containing the
  NF2FF box).

- **NF2FF far-field box was asymmetric in z → tilted ("egg") antenna pattern**
  (`app/ui/main_window.py:_on_fit_nf2ff`, new `app.meshing.bounds.nf2ff_fit_box`):
  *Fit NF2FF to Geometry* applied the full margin on ±x/±y but squeezed z to
  `0.3×`/`0.5×` of it, so the integration surface sat only ~0.15 λ from the
  source on the near z face and at a different distance on every face. Even for a
  symmetric z-directed dipole that produced a **tilted** pattern — Dmax 3.46 dBi
  at θ=108° with D(0°)=−71 dB vs D(180°)=−83 dB (12 dB mis-match) instead of a
  rotationally-symmetric donut. The fit now places an **equal** margin on all six
  faces (symmetric about the source), which restores Dmax 2.07 dBi at θ=90°,
  D(0°)≈D(180°) (−123 dB, 0.5 dB), φ-spread 0.03 dB and efficiency 0.99. Unit
  tests in `tests/test_nf2ff_fit_margin.py`.

- **S11 was always 50 Ω-referenced, ignoring the port resistance**
  (`app/postproc/sparams.py`, `app/simulation/script_gen.py`): the lumped port's
  `resistance` was passed to `AddLumpedPort` but `CalcPort` was always called
  with `ref_impedance=50`, so a run with a 73 Ω port still produced a 50 Ω
  S11 (and a `.s1p` labelled `z0=50`). `compute_sparams_from_ports` now defaults
  to `ref_impedance=None` — openEMS then uses each port's own R — and
  `export_sparams`/`write_touchstone` record the port resistance in the header.
  For the dipole a 73 Ω port now yields |S11|min −23.3 dB @ 2.375 GHz instead of
  −19.8 dB @ 2.335 GHz (port 50 Ω projects are unchanged). Tests in
  `tests/test_sparam_ref_impedance.py`.

- **Run aborted with `cell_too_large` after easyMesh stretched the PML cells**
  (`app/meshing/easy_wrapper.py` `cap_max_cell_size`): easyMesh sizes the air
  cells from `target_frequency` (e.g. 2.45 GHz → λ/15 = 8.16 mm at the *medium*
  preset) and then expands the PML region (PML_8) by ~1.27×, so the
  absorbing-region cells came out at ~10.32 mm — 1.6 % over the openEMS hard
  limit λ(f_max)/10 = 10.16 mm (f_max = 2.95 GHz). The mesh validator flags that
  as an error, so `project_run.py` aborted with exit code 2 before FDTD. The
  cartesian mesher now runs `SmoothMeshLines(axis, λ(f_max)/10, max_grading)`
  after easyMesh (and the metal-interface refinement), splitting any oversized
  cell — including in the PML — so the validator passes without changing the
  preset's characteristic cellsize. Pure check in `tests/test_cap_max_cell.py`.

- **NF2FF "Fit to geometry" ignored cylinder radii (and spheres/polygons)**
  (`app/ui/main_window.py:_on_fit_nf2ff`, new `app/meshing/bounds.py`): the
  cartesian geometry envelope was built by reading each primitive's `start`/
  `stop` only. For a cylinder those are the **axis** endpoints, so the radius
  never entered the two perpendicular axes — a 58 mm dipole of radius 1 mm fit
  a box of zero transverse width, leaving the NF2FF integration surface only
  ~17.8 mm (~0.15 λ) from the wire, inside the reactive near field. Spheres and
  polygons were skipped entirely. New pure helper
  `app.meshing.bounds.cartesian_primitive_bbox` derives the true AABB (cylinder
  axis ± radius on the perpendicular axes, sphere from centre/radius, polygons
  from their stored bbox), mirroring the existing cylindrical
  `polar_primitive_bbox`. Unit tests in `tests/test_primitive_bounds.py`.
  (Antenna-mode runs now also size the box via the configurable
  `antenna_nf2ff_lambda` — see Added.)

- **Antenna viewer: *Gain / Directivity vs frequency* axes and legend**
  (`app/ui/antenna_viewer.py`): the plot crammed Prad (`W*s^2`, pulse
  spectrum ≈ 1e-27) and the unitless efficiency η onto one right axis fixed to
  `0..1.05`, so Prad was flattened invisibly onto 0 while the axis was
  mislabeled "Prad [W]"; and the legend was built before the twin axis was
  filled, so the η and Prad curves had no entry. Prad now gets its own right
  axis (own scale + `W*s^2` label), η keeps the `0..1` axis (`eta = Prad / Pin`),
  and the combined legend lists all four series. Regression checks in
  `tests/antenna_viewer_smoke.py`.

- **Antenna viewer: analysis menu (right-click) now opens on every plot**
  (`app/ui/antenna_viewer.py`): the `_on_click` right-click branch was gated by
  `_polar_active`, so the menu (r min/max/step, label offset, HPBW, VRV, Clear
  markers, Pop out graph) only appeared on the three polar cuts and did nothing
  on the **3D pattern** / gain- / efficiency-vs-frequency plots. It now opens on
  any antenna plot; the polar-only controls are greyed out on the non-polar
  plots (title "Antenna analysis" vs "Polar analysis"). Tooltip + Antenna help
  updated. Regression checks in `tests/antenna_viewer_smoke.py`.

## [0.4.0] - 2026-10-17

### Added

- **Graded refinement at ε (material) interfaces works on BOTH sides**
  (`app/meshing/interface_refine.py`): `material_jump_sides` now emits the
  *inward* face of every dielectric in addition to the outward one (deduped for
  adjacent media, metal-overlapped faces still skipped). Since each direction
  is anchored to the cell on the opposite side, the ramp is now produced into
  the dielectric **and** into the surrounding air — previously only the outward
  (air) side could ever be refined. Metal-face refinement is unchanged. New
  deterministic, CSXCAD-free geometry tests: `tests/test_interface_refine.py`
  (+ updated expectations in `tests/graded_interface_smoke.py`).

- **FDTD engine progress bar (timestep / residual energy)** — the status bar
  gets a `QProgressBar` + label fed from the openEMS stdout lines by the
  Qt-free `app.simulation.progress.EngineProgress` (parses `Max. number of
  timesteps: …`, the `[@ 4s] Timestep: … Energy: ~… (-25.91dB)` report lines and
  the `end-criteria … reached` / `before the end-criteria` messages). Shows
  `TS <n>/<max>`, residual energy in dB, elapsed time and a converged /
  max-TS-reached status; resets on every run (incl. each *Run All Ports* step).
  Tests: `tests/test_progress.py` + `tests/progress_smoke.py`.

- **Mesh build runs in a background worker thread** — *Build CSX + Mesh +
  Validate* (`Ctrl+M`) no longer freezes the GUI: `MainWindow._on_build_mesh`
  starts an `app.ui.mesh_worker.MeshBuildThread` running the new Qt-free core
  `app.meshing.build_task.build_mesh_and_validate` (CSX build + easyMesh +
  validation + `csx.xml` export). The live CSX/FDTD come back to the GUI thread
  via a `done` signal; `_finish_mesh_build` then does the matplotlib preview,
  the Mesh Viewer CSX swap (with the existing retired-object bookkeeping) and
  the result recording on the GUI thread. `closeEvent` waits for a running
  build. The blocking `_build_and_validate` path is kept for
  `_ensure_mesh_viewer_content` and the tests that stub it; patch-preset
  fallback moved to `_ensure_patch_primitives`. Test:
  `tests/mesh_thread_smoke.py` (real easyMesh build off the GUI thread).

- **Multi-excitation driver — the full S-matrix in one click**
  (`app/ui/main_window.py`, new `app/simulation/multirun.py`): *Simulation →
  Run All Ports (full S-matrix)* (`Ctrl+Shift+R`) runs one FDTD simulation per
  port — each with exactly that port excited — then merges the partial
  `sparams.json` columns (`app.postproc.sparams.merge_sparam_tables`) and
  exports the complete matrix (`<project>/sparams/sparams.json` + `.sNp` +
  plot), loading it into the S-param viewer. The per-run scripts/projects are
  written under `<project>/multi/port<n>/`; the runs execute sequentially in
  the GUI with per-port log prefixes, and a missing column is reported without
  aborting the merge. Fewer than 2 ports falls back to a normal single run.
  `app/simulation/multirun.py` (`plan_port_runs`, `merge_port_runs`,
  `summarise_table`) is Qt-free and unit-tested. Tests:
  `tests/test_multirun.py` (planning/deep-copy/merge/`.s2p`) and
  `tests/multirun_smoke.py` (GUI driver with a fake runner → merged matrix).
- **Mesh Viewer draws cylinders as circles** (`app/ui/mesh_viewer.py`,
  `app/meshing/preview.py`): `iter_primitive_infos` now reports the CSX
  primitive type and the cylinder radius, and the slice/overview plots draw an
  axis-aligned cylinder's true cross-section as a **circle** (project
  primitives from their axis endpoints + radius) instead of its bounding box;
  tilted cylinders keep the box. Regression check in `tests/mesh_viewer_smoke.py`.
- **Project results are recorded in the project JSON and restored on open**
  (`app/models/project.py`, `app/ui/main_window.py`): after a run — and after
  *Build CSX + Mesh + Validate*, which now also writes `<work>/<project>/csx.xml`
  — the GUI stores the artifact paths in the project file (`results`:
  `sim_dir`, `csx_xml`/`mesh_xml`, `sparams`, `nf2ff`, `time`; the project file
  is saved after a run). Opening a project loads the S-params from the recorded
  path, and opening the **Mesh Viewer** shows the last mesh — loaded from the
  recorded mesh or, when nothing is recorded yet, generated automatically (no
  need to press *Build CSX + Mesh + Validate* again).
- **Opening a project restores the viewers from the last results**
  (`app/ui/main_window.py`, `app/simulation/script_gen.py`): the run script now
  also writes the meshed CSX to `<work>/<project>/simulation/csx.xml` (also in
  `--mesh-only` mode, before the FDTD so it survives a crash). Loading a project
  puts that CSX into the **Mesh Viewer** (exact mesh + geometry of that run) and
  the newest `sparams.*` into the **S-param Viewer**; the S-param viewer's file
  browser/auto-load is scoped to the current project instead of the global work
  dir. Projects without results leave the viewers untouched.
- **Mesh Viewer: mesh-grid visibility slider** (`app/ui/mesh_viewer.py`): a
  slider next to *Show mesh grid* controls the mesh grid lines' opacity and
  line width (0 = invisible, 100 = strong) so they can be told apart from the
  geometry/port outlines; it is disabled while the grid is hidden or no slice
  view / mesh is active.
- **S-param viewer: combined Re/Im + Phase plot** (`app/ui/sparam_viewer.py`):
  new *Re/Im + Phase* plot type — real and imaginary parts on the left axis,
  the phase (deg) on a twin right axis, in one plot.
- **Graded mesh refinement at metal interfaces** (`app/meshing/interface_refine.py`,
  `app/meshing/easy_wrapper.py`): the *Mesh Parameters → Refine mesh at metal
  interfaces (graded)* option (new projects: on) inserts geometric mesh lines
  on the vacuum/air or dielectric side of every metal (PEC) face — the cell
  next to the metal is the finest (`min_cellsize`, fallback local cell / 4)
  and grows with distance by *Max grading ratio* until it merges into the
  existing grid. Zero-thickness sheets (patch/ground) are refined on both
  sides, solid metals only outside. The pass runs after easyMesh (so the
  lines survive `SmoothMeshLines`) and is cartesian-only.

### Changed

- **Dielectric/air (ε) jumps are refined on both sides**
  (`app/meshing/interface_refine.py`, `app/meshing/preview.py`):
  `iter_primitive_infos` now reports the property permittivity,
  `material_jump_sides()` detects medium jumps that no metal covers, and the
  ramp there starts at the *other* side's cell size (continuity) so the cells
  across the interface stay within *Max grading ratio*. Measured on the
  standard fixtures nothing is added where easyMesh already grades smoothly
  (patch/dipole), so existing results are unchanged. Unit checks added to
  `tests/graded_interface_smoke.py`.
- **Project directory layout** (`app/simulation/script_gen.py`,
  `app/ui/main_window.py`, `sparam_viewer.py`, `antenna_viewer.py`): the project
  JSON is saved in the work directory and every other project file goes into a
  folder **named after the JSON file** (`<work>/<name>.fcsim.json` +
  `<work>/<name>/`) — independent of `Project.name` (which previously sent the
  results of an “untitled” project to `work/untitled/`). Inside that folder the
  processed results have their own subfolders: **`sparams/`** (Touchstone, JSON,
  plot), **`fields/`** (field-slice plots) and **`nf2ff/`** (far-field
  results); the raw openEMS outputs (et/ht, port dumps, nf2ff_*.h5) stay in
  `<name>/simulation/`, the meshed CSX is written as `<name>/csx.xml`. The
  project folder (including the `sparams/`, `fields/` and `nf2ff/`
  subfolders) is created as soon as the project is saved — plain *Save* and
  *Save As*. Viewers,
  result recording and the open-on-load restore use the new locations; the old
  `<name>/simulation/` layout is still read for existing projects.
- **Metal-interface refinement is app-side now** — the flag is no longer
  forwarded to easyMesh (`MeshSpec.global_setup`): easyMesh's internal
  `add_graded_mesh_lines_at_material_transitions` was smoothed away, missed
  metal-in-air interfaces entirely (no second primitive edge) and could blow
  up the max cell size elsewhere (e.g. z cells 5.8 → 15.3 mm). `max_grading_ratio`
  is now forwarded as well. Old projects keep the previous behaviour (the
  field loads as off); new projects default to on.
- **S-parameter sweep limited to the excitation band**
  (`app/simulation/script_gen.py`): the post-processing sweep was widened to
  `f_start·0.5 … f_stop·1.15` (301 pts); outside the excitation band the
  Gaussian pulse carries no energy, so `|S11| = |uf_ref|/|uf_inc|` divided
  noise by ~zero and exploded (measured |S11| up to 107). That garbage
  dominated the S-param plots and the exported Touchstone. The sweep now
  covers `[f_start, f_stop]` (201 pts) where the S-parameters are meaningful.

### Fixed

- **MSL (microstrip) ports generated an invalid `AddMSLPort` call**
  (`app/simulation/script_gen.py`, `app/ui/main_window.py`,
  `app/csxcad_builder/builder.py`, new `app/simulation/msl.py`): the generated
  `project_run.py` passed `port.resistance` as openEMS's `metal_prop` and
  `port.direction` as `prop_dir`, leaving `exc_dir` to swallow the excite flag —
  so any MSL port raised at runtime. The call is now
  `AddMSLPort(nr, metal, start, stop, prop_dir, exc_dir, excite=…)` with a strip
  metal property (`AddMetal("msl_<n>_<name>_strip")`) and the two axes resolved
  by `app.simulation.msl.resolve_msl_axes` (explicit `prop_direction`/
  `exc_direction`, else derived from the port box: propagation = longest edge,
  excitation = shortest — the openEMS `MSL_NotchFilter.py` convention `x`/`z`).
  MSL ports are now added **after** `generate_mesh` (in the run script and the
  GUI *Build CSX + Mesh* preview), because `AddMSLPort` samples the
  propagation-direction grid lines and needs ≥ 5 of them; non-MSL ports keep
  their pre-mesh registration so their meshes are unchanged. `PortDef` gains
  `prop_direction`/`exc_direction` (old JSONs load with empty = auto) and the
  Port dialog two "(auto)/x/y/z" combos. Tests: `tests/test_msl_port.py` (axis
  derivation/overrides/errors + generated-script signature) and the PortDialog
  round-trip in `tests/port_defaults_smoke.py`.
- **Graded interface refinement: two bugs** (`app/meshing/interface_refine.py`):
  the geometric ramp fit could push the first inserted cell far below the
  intended floor, giving mesh jumps of 3–4.6× across an interface instead of
  ≤ *Max grading ratio*; the series is now fitted from below (α ≥ 1) so the
  caps hold (measured: 4.61× → 1.97×). The continuity cap was also applied
  across PEC faces, which needlessly weakened the metal-in-air refinement and
  inserted lines where the mesh was already smooth — it is now an explicit
  ``continuity`` flag used only where the fields are continuous.
- **Mesh Viewer kept a stale mesh after geometry changes**
  (`app/ui/main_window.py`): the viewer rebuilt only when *nothing* was
  loaded, so editing primitives, ports, dumps or mesh parameters left the old
  geometry on screen. A signature of the mesh-relevant project state
  (primitives, materials, assignments, ports, dumps, NF2FF, mesh, boundary) is
  now stored when the CSX is built/loaded and compared when the Mesh Viewer tab
  is opened — the mesh is rebuilt automatically when it changed (no need to
  press *Build CSX + Mesh + Validate*). Also fixed the *Build Mesh* tab switch
  (it went to the wrong tab widget).
- **Antenna tab showed no (or a stale) pattern** (`app/ui/main_window.py`,
  `app/ui/antenna_viewer.py`): opening the *Antenna* tab did nothing — the file
  list was only filled by the menu action / *Scan work dir*, so the plot stayed
  empty (or kept the file loaded at app start). The tab now refreshes the list
  and loads the current project's recorded NF2FF result (fallback: the newest
  file) when nothing is loaded; a user-selected file is never replaced
  (`set_work_dir` no longer auto-loads). Regression check added to
  `tests/project_results_flow_smoke.py` (NF2FF restored from the project JSON).
- **Antenna viewer missed the new project layout** (`app/ui/antenna_viewer.py`):
  `find_latest_nf2ff_files` appended `nf2ff` twice for the new layout
  (`<project>/nf2ff/nf2ff/nf2ff.json`), so freshly created project folders never
  appeared in *Loaded files* — only the legacy `*/simulation/nf2ff/…` entries
  did. Both layouts are discovered now (regression check added to
  `tests/antenna_viewer_smoke.py`).
- **Viewer re-scan replaced the loaded data** (`app/ui/antenna_viewer.py`,
  `app/ui/main_window.py`): *Scan work dir* always selected list row 0, which
  fired the load callback and overwrote whatever the user had open. The scan
  now only refreshes the list — the loaded file stays selected/loaded, and the
  newest entry is loaded only when nothing is loaded yet. *Open nf2ff.json…*
  starts in the folder of the current file and adds/selects the opened file in
  the list (even when it lies outside the work directory). The S-param
  viewer's auto-load when the tab is opened is limited the same way.
- **Field-dump plots were never produced** (`app/postproc/fields.py`): the HDF5
  reader only descended one level — but `FieldData` → `FD`/`TD` are *groups* —
  so `FieldData/FD/f<N>_real|_imag` (frequency domain) and `FieldData/TD/t<N>`
  (time domain) were missed and `fields/` stayed empty even for projects with
  field dumps (e.g. the patch preset's `Et_substrate`). The reader now handles
  both layouts, reduces the xyz component axis to the vector magnitude and the
  plots use the dump's mesh as axis extent/labels. New regression test
  `tests/field_dump_postproc_smoke.py` (synthetic HDF5, no FDTD run).
- **Empty recorded path turned into the working directory**
  (`app/ui/main_window.py`): a missing entry in `Project.results` produced
  `Path("")` — which *is* `.` and “exists” — so opening a project without
  recorded results tried to load the cwd as S-params ("Permission denied: '.'")
  and the fallback discovery never ran (S-params stayed empty). Recorded
  strings are now validated via `_existing_path()`.
- **Mesh Viewer: slice plane starts on the model origin** (`app/ui/mesh_viewer.py`):
  after *Build CSX + Mesh + Validate* (and after loading a project's last
  results, and on every view switch) the *Plane* value is reset to **0 mm**
  instead of the domain's lower bound, so the slice always starts at the model
  origin. The slider still spans the whole FDTD domain; its handle sits where
  0 mm lies and can be dragged to the structure.
- **Mesh Viewer: ports vs. structure + legend** (`app/ui/mesh_viewer.py`):
  openEMS stores every port as CSX primitives (LumpedElement, Excitation,
  ProbeBox/ResBox) whose slice footprints were drawn by the *Show structure*
  block — so *Show ports* did not hide all of them (a box/line stayed visible,
  and their `port_*` names cluttered the legend) and hiding the structure made
  the ports disappear. Port artefacts are now excluded from the structure
  block and drawn only by *Show ports*, with a single "Ports" legend entry
  that follows the toggle. A port that lies perpendicular to the slice (e.g.
  an x-directed dipole feed in the XY view, zero extent along the normal) is
  now drawn whenever the slice cuts one of its cells (tolerance = local cell
  size) instead of only when the plane hits its exact coordinate.
- **Mesh Viewer: structure/port toggles + zoom reset** (`app/ui/mesh_viewer.py`):
  the ports block used an `eps` that was only defined inside the *Show
  geometry* blocks, so hiding the geometry raised a `NameError` (swallowed by
  the redraw handler) and the slice — including the ports — was not drawn;
  `eps` is now defined once at the top of `_draw_slice`. Also, every
  visibility toggle (Show mesh/structure/ports, and the new mesh slider)
  rebuilt the plot from scratch and reset the user's zoom: the axes limits are
  now captured and re-applied when only the visibility/style changes (a view
  switch still autoscales).
- **Silent `max_grading_ratio` gap** (`tests/mesh_params_flow.py`): the
  grading ratio is now part of `global_mesh_setup` (the flow test previously
  reported FAIL for it).
- **Graded refinement could collapse the timestep** (`app/meshing/interface_refine.py`):
  the first version ramped down to 57 µm cells at a dipole feed (a 49× grading
  jump against the neighbouring 2.8 mm cell), shrinking the CFL timestep 11×
  (88 365 steps / 465 s, and the very long run tripped a native heap-corruption
  crash). The inserted cells are now capped: never finer than one grading step
  below the smallest existing cell on that axis and never more than one grading
  step off the cell on the other side of the interface — a mesh that is already
  fine next to the metal is left alone (the dipole keeps its easyMesh cells:
  timestep 3.2e-13 s, 89 s).

## [0.3.0] - 2026-10-16

### Added

- **FreeCAD analytic import mix** (`app/freecad_io/freecad_mesh.py`,
  `app/ui/main_window.py`): CAD import (FCStd/STEP) now detects native openEMS
  primitives per solid instead of tessellating everything to STL — axis-aligned
  boxes → *box*, full cylinders (any axis) → *cylinder*, spheres → *sphere*,
  extruded polygons/prisms (incl. 90°-rotated boxes) → *polygon*; only the
  rest (cones, booleans, odd rotations) is tessellated. One entry per object
  (prim OR stl, no duplicates), the same run-script/`ensure_simulation_meshes`
  fallback maps analytic objects for old projects, and the detector is
  shape-based so it also works on STEP-imported solids.
- **Sphere and polygon primitive kinds end-to-end** (`app/ui/geometry_editors.py`,
  `app/ui/main_window.py`, `app/simulation/script_gen.py`): new
  *Geometry → Add Sphere… / Add Polygon…* dialogs and tree-edit routing;
  run-script and Build-Mesh emit `AddSphere` / `AddPolygon` / `AddLinPoly`
  (polygon `length` 0 = flat sheet, > 0 = extruded prism). Both kinds are
  gated in cylindrical mode (dialog + run) like STL. The Mesh Viewer slice
  rect works via the prim bbox `start`/`stop`.
- **Tests**: `tests/freecad_analytic_smoke.py` (FCStd box/rotated
  cylinder/sphere/triangular prism/cone/rotated box → detection, one prim per
  object, no auto-assignments, material persistence, assignment precedence)
  and `tests/sphere_polygon_smoke.py` (dialogs, mesh-only + short FDTD run
  with `AddSphere`/`AddPolygon`/`AddLinPoly`, polar gates for both the GUI
  and the run path).

### Changed

- **No auto-assignments on geometry import** (`app/ui/main_window.py`):
  importing FreeCAD/STL geometry no longer creates copper assignment rows —
  assignments come from presets or manual dialogs only. Materials live on the
  primitives and now survive a re-import (existing assignment wins, else the
  primitive's current material, else copper/10); the assignment→primitive
  material sync applies to all primitive kinds, not just stl.

### Fixed

- **Mesh Viewer slice plane** (`app/ui/mesh_viewer.py`): the view combo labels
  ("XY slice") never matched the `_SLICE_AXES` keys ("XY"), so slice views
  silently drew the cell-sizes plot, *Show mesh grid* had no effect, and the
  plane slider/spinbox did nothing (and stayed greyed). Added a `_slice_key()`
  label→key mapping used by all comparisons; the slider scale now matches the
  handlers (0..1000 from creation, was 0..100), the plane range recalibrates
  when the slice axis changes, and both controls are disabled until a mesh is
  built. The slider moves the slice plane and snaps to the nearest mesh line.
- **Polygon primitive CSXCAD format** (`app/simulation/script_gen.py`,
  `app/ui/main_window.py`): points were passed as a list of `[x, y]` pairs —
  CSXCAD 0.6.x expects columnar `[xs, ys]` and raised `points must be a list
  of length 2` at run time (the kind was never exercised before). Both the
  run script and the GUI Build-Mesh path now convert correctly and support
  extruded prisms via `AddLinPoly`.

## [0.2.0] - 2026-10-15

### Added

- **Cylindrical (polar) meshing** (`app/meshing/polar.py`, `app/meshing/easy_wrapper.py`,
  `app/models/project.py`): new *Coordinate system* option in Mesh Parameters
  (cartesian / cylindrical). Cylindrical mode drives openEMS's
  `CoordSystem=1` (rho/alpha/z) with a dedicated polar mesher that bypasses
  easyMesh (no fork changes): full-turn alpha grid, rho=0 axis always a mesh
  line, primitive/port faces re-synced after smoothing (nudge or insert,
  never sliver cells), air margin limited to rho+ and z±. Supported
  primitives: box (wedge in rho/phi/z) and cylinder; STL/FreeCAD imports,
  polygons and polyhedra are rejected with a clear message. Box/cylinder and
  port editors show rho/phi/z suffixes (phi edited in degrees, stored as
  radians); boundary rows are relabelled rho/phi/z with the phi rows
  disabled. Mesh validation and the mesh preview are coordinate-system aware
  (phi axis checked via arc length at the outer radius).
- **Ports / NF2FF gates in cylindrical mode**: openEMS 0.0.36's LumpedPort is
  not cylindrical-mesh aware (Capacity=NaN poisons the excitation and the
  whole field) and NF2FF far-field is unsupported, so *Add/Edit Port* is
  disabled, a run with ports aborts with a clear message, NF2FF is skipped
  with a note, and the run stays a valid free-response simulation (dumps).
  The cylindrical-aware CoaxialPort exists only in openEMS >= 0.37.
- **Help topic "Cylindrical Mesh"** (`app/ui/tab_help.py`): covers the
  coordinate system, supported primitives, degree-based phi editing,
  margins, the ports/NF2FF limits and when to prefer polar over cartesian;
  reachable from **Help → Cylindrical (polar) mesh…**.
- **Antenna simulation mode** (`app/ui/sim_params.py`, `app/models/project.py`):
  new *Antenna simulation* group in the Simulation Parameters window
  (Ctrl+Shift+P) implementing the FDTD antenna checklist — mesh density
  presets as cells/λ (low 10 / medium 15 / high 20 / very_high 25), PML
  distance as a multiple of λ at *f_start* (default 0.5×λ, i.e. λ/2 on every
  face instead of the auto λ/4-at-*f_stop* rule), automatic NF2FF enable and
  air-margin controls that take over while the mode is on. State persists in
  `SimSpec` (`antenna_mode`, `antenna_pml_lambda`).
- **Help topic "Antenna Simulation"** (`app/ui/tab_help.py`): English guide
  covering numerical dispersion (≥ 15–20 cells/λ), sub-pixel edge placement,
  frequency-dependent εr, PML distance at *f_start*, general FDTD limits
  (cost, open boundaries, staircasing, narrowband vs. time domain) and a
  recommended workflow — reachable from **Help → Antenna Simulation (FDTD
  hints)…** and from the Simulation Parameters help.
- **Application icon**: `assets/logo.svg` → multi-size `assets/app_icon.ico`
  (generated by `tools_svg_to_ico.py`) used as the EXE icon and the window
  icon.
- **Tests**: `tests/cylindrical_spike.py` (Phase-0 proof: polar grid API,
  closed PEC cavity at the analytic TM010 frequency, wedge/cylinder
  coordinate semantics), `tests/cylindrical_mesh_smoke.py` (end-to-end:
  polar mesh + validation + NF2FF skip, STL gate, cartesian control, short
  polar FDTD free-response run, port gate) and `tests/antenna_mode_smoke.py`
  (help topic content, checkbox behaviour, preset/PML/air-margin values,
  project JSON round trip, switch-off behaviour).

### Fixed

- **Polar port face alignment** (`app/meshing/polar.py`): port grid lines are
  now quantized with the same `f32` rounding the run script uses for the
  CSXCAD port box; previously the line missed the port by ~2.6e-8 and openEMS
  reported "Unused primitive … port_excite".
- **`tests/sim_params_smoke.py`**: updated for the min-cell-size widget that
  moved to the Mesh Parameters window (dialog apply flow).

## [0.1.0] - 2026-10-15

First public pre-alpha release. Standalone Windows GUI (one-file, ~147 MB)
bundling PySide6, openEMS 0.0.36, CSXCAD 0.6.3 and easyMesh4openEMS.

[Release](https://github.com/istra711/VisualOpenEMS/releases/tag/v0.1.0)
· [Compare with previous](https://github.com/istra711/VisualOpenEMS/commits/v0.1.0)

### Added

- **Initial application** (`4c4b846`, 2026-10-14): full FreeCAD → openEMS
  workflow — geometry import (FCStd/STEP/STL/OBJ), CSXCAD model builder,
  easyMesh integration, FDTD runner, S-parameter / field-dump / NF2FF
  post-processing, and a PySide6 GUI (main window, mesh & S-param viewers,
  antenna pattern viewer, script editor, settings).
- **Regression tests** (`9608f5c`): `dipol_mesh_gap.py` (port-gap
  resolution), `dipol_stability.py` (CFL timestep + finite port waveforms),
  `dipol_stl_bounds.py` (polyhedron AABB sanity).
- **`mesh_params_flow.py`** (`c9dfbec`): end-to-end test of mesh-parameter
  overrides from `global_mesh_setup` into the easyMesh pipeline.
- **`app/winproc.py`** (`64cefde`): Windows helper that starts console
  child processes without a visible terminal window
  (`CREATE_NO_WINDOW` + `STARTUPINFO`/`SW_HIDE`).
- Comprehensive **README** (`45fdf1d`) with architecture, workflow,
  requirements, and external-source attribution; **pre-alpha warning**
  (`340b4b2`).
- Documentation: `docs/ANLEITUNG_DE.md` / `ANLEITUNG_EN.md`,
  `FREECAD_WORKFLOW_*`, `FREECAD_PORTS_*`, `EASYMESH_CHANGES_*`.

### Fixed

- **NF2FF radiation efficiency off by r²** (`9608f5c`, `app/postproc/nf2ff.py`):
  openEMS stores `P_rad` in the far-field H5 as a *power density* `S_r`
  (W/m² at radius r), not as total radiated power.  The spherical integral
  was used directly as `Prad`, underestimating it by r² (e.g. 3600× at
  r = 60 m) and yielding ~0.03 % efficiency for a PEC dipole.  The integral
  is now multiplied by r²; efficiency is `Prad_total / Pin` and correctly
  returns ~100 % for lossless metals.  Directivity is unaffected (r² cancels
  in `D = 4π·S_r / (integral/r²)`).
- **NF2FF recording box not closed** (`9608f5c`,
  `app/simulation/script_gen.py`): openEMS snaps each dump-box face
  independently (zero-extent faces snap to the nearest line, finite faces
  snap outward to cell boundaries), producing misaligned faces with gaps and
  leaked near-fields.  Box corner coordinates are now snapped onto existing
  FDTD grid lines before the `NF2FFBox` is created, so all six faces share
  the same planes.
- **FreeCAD ghost / construction objects imported** (`9608f5c`,
  `app/freecad_io/freecad_mesh.py`): the tessellator now skips
  `App::Origin`, `App::Line`, `App::Plane`, `App::Part`,
  `App::DocumentObjectGroup`, `App::Link`, objects marked
  `Removing`/`Invalid`/`Error` in their State, and invisible objects.
  The user-visible `Label` is preferred over the internal `Name`.
- **Lumped-port gap collapsed by smoothing** (easyMesh `c9fae70`):
  port face endpoints ("anchors") are recorded and re-inserted after
  `SmoothMeshLines`, so a port gap thinner than `mesh_res` is no longer
  merged into a short circuit.
- **Zero-size FDTD cells collapsing the CFL timestep** (easyMesh
  `c9fae70`): the old port-anchor re-insertion in `easyMesher` produced
  exact duplicate grid lines.  Anchors are now restored only in
  `meshProcessing._densify_port_gap`; a dedupe pass removes any remaining
  zero-size cells.
- **Tapered STL cylinder end-discs creating nm-scale cells** (easyMesh
  `c9fae70`): triangle edges a few nm apart at tapered end discs are snapped
  onto the disc outline; native cylinders are densified over the end disc
  (radius + one-third rule).
- **Mesh parameter overrides ignored** (easyMesh `7f9d94f`,
  test `c9dfbec`): `global_mesh_setup` parameters now propagate correctly
  into the easyMesh pipeline.
- **Cylinder-end mesh densification and bounds checks** (easyMesh
  `4fc6dc7`).
- **Console window flash on child-process spawn** (`64cefde`): FreeCADCmd
  and the FDTD Python subprocesses now start with
  `CREATE_NO_WINDOW` + `STARTUPINFO`/`SW_HIDE`.  The `--windowed` PyInstaller
  bundle has no console of its own, so each console child previously flashed
  a black terminal window.  GUI children (FreeCAD GUI, AppCSXCAD) are
  unaffected.

### Changed

- **easyMesh submodule pointed at the fork** (`704ca3c`): the submodule URL
  now targets `istra711/easyMesh4openEMS` instead of the upstream repo, so
  the local meshing fixes travel with the project.
- **S11 sweep widened** (`9608f5c`, generated `project_run.py`): the
  post-processing S-parameter sweep now covers
  `linspace(max(1e8, f_start·0.5), f_stop·1.15, 301)` instead of only the
  configured mesh range, giving context around the band edges.
- **Deduplicate grid lines** (`9608f5c`, `script_gen.py`): a
  `dedupe_grid_lines` pass removes duplicate mesh lines that would become
  zero-size FDTD cells.

### Removed

- `tests/debug_old_load.py` (obsolete debug script, `9608f5c`).

### Verification

- **Dipole (PEC, 56.6 mm) at 2.103 GHz**: S₁₁ = −23.3 dB, −10 dB band
  1.467–2.240 GHz, efficiency ≈ 100.06 %, P_rad ≈ 1.179 × 10⁻²⁸ W·s²
  (matches the openEMS `Prad` attribute), D_max ≈ 2.05 dBi, Gain ≈ 2.05 dBi.
- Regression tests: `dipol_mesh_gap` ✓, `dipol_stability` ✓,
  `yagi_mesh_ends` ✓, `mesh_params_flow` (only the known pre-existing
  `max_grading_ratio` FAIL).
- PyInstaller one-file build: `dist/FreeCAD_OpenEMS_GUI.exe` (146.5 MB),
  launches and responds.

### Known limitations (pre-alpha)

- Mesh `max_grading_ratio` override is not yet surfaced in the UI
  (`mesh_params_flow.py` reports it MISSING).
- Port excitation amplitude uses the subsampled pulse-DFT convention;
  relative results (S-parameters, efficiency) are unaffected, but absolute
  voltage/current values are scaled.

[0.1.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.1.0
[0.2.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.2.0
[0.3.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.3.0
[0.4.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.4.0
[0.5.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.0
[Unreleased]: https://github.com/istra711/VisualOpenEMS/compare/v0.5.0...HEAD
