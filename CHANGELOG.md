# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
[Unreleased]: https://github.com/istra711/VisualOpenEMS/commits/master
