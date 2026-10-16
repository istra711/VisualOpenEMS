# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing pending.

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
