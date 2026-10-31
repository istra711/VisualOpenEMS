# FreeCAD OpenEMS GUI — Development Prompt (living document)

> **Purpose:** Capture context, decisions, and progress so work can be continued
> (by a developer or AI agent) without re-deriving the whole project.
> **Update rule:** After every meaningful session, append/adjust the sections below.
> Keep this file short enough to re-read in 5–10 minutes.

---

## 1. Goal

Standalone desktop GUI that:

1. Takes 3D geometry from FreeCAD (STEP / STL / OBJ / FCStd)
2. Enforces openEMS-conform meshing (easyMesh4openEMS) or auto-meshes
3. Runs FDTD via openEMS Python bindings
4. Post-processes S-params (always Touchstone), field dumps, NF2FF far-field
5. Shows results in GUI: Smith chart, markers, cursor measurements (scikit-rf)

UI language: **English**. FreeCAD-OpenEMS macros are reference only.

Project root:

```
C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI
```

---

## 2. Environment (locked)

| Item | Value |
|------|--------|
| App / GUI Python | `c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe` (3.11.5) |
| openEMS | 0.0.36, root `c:\openEMS` |
| CSXCAD | 0.6.3 |
| PySide6 | 6.11.2 |
| numpy / scipy / matplotlib | 1.26.4 / 1.17.1 / 3.11.0 |
| h5py | 3.7.0 |
| scikit-rf | 1.4.1 |
| easyMesh4openEMS | 0.0.1 |
| FreeCADCmd | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe` |

Settings file: `app_settings.json` (FreeCAD path, sim Python, openEMS root, work dir, **window_geometry**).

---

## 3. Architecture (keep call order)

```
FreeCAD STEP/STL/OBJ/FCStd
    → app/freecad_io/importer.py
Project JSON + primitives (boxes/polygons)
    → app/models/project.py
easyMesh enhance (BEFORE SetBoundaryCond / geometry)
- `enhance_csx`/`enhance_fdtd` share `primitives_mesh_setup`; `apply_boundary` after enhance_fdtd (PML)
- FreeCAD meshes: CSXCAD 0.6.3 has **no AddMesh** → `triangle_mesh.add_triangle_mesh`: axis-aligned box mesh → `AddBox` (detected by `axis_aligned_box_bounds`), anything else → `AddPolyhedronReader` (STL); in-memory `AddPolyhedron` is **not simulated** (openEMS reports *Unused primitive*)
- Polyhedron has **no GetCoords** → easyMesh geometryUtils uses AABB (`GetBoundBox`) for mesh lines/z
- generate_mesh injects `boundary_conditions` as `{'BoundaryCond': [...]}` into easyMesh setup
- easyMesh is **pip-installed in site-packages** — GUI must use `third_party/easyMesh4openEMS` first on `sys.path` (`app/main.py`, generated `project_run.py`); also `work/patch_site_easy_mesh.py` copies Polyhedron patches into site-packages. **After editing `third_party/.../easyMesh/*.py` re-run `work/patch_site_easy_mesh.py`** — anything importing `easyMesh` without `app/main.py` first (temp scripts, plain `python -c`) uses the stale site-packages copy
- FreeCAD FCStd import → `.npy`/`.stl` → `add_triangle_mesh` (box for axis-aligned solids, PolyhedronReader otherwise); easyMesh uses the AABB (`GetBoundBox`) for mesh lines/z either way
- **FreeCAD ports:** `PORT_<name>_start/stop` Part::Vertex points, or `openems_port_*` custom props, or doc `openems_ports` JSON; export/tessellation writes `ports` → GUI Import auto-adds PortDef (`app/freecad_io/port_marks.py`)
    → app/meshing/easy_wrapper.py
    → app/meshing/rules.py  (λ/10 hard limit; preset = generation target only)
CSX build (always CSX.Add* for materials)
    → app/csxcad_builder/*, script_gen.py
Sim script (subprocess)
    → app/simulation/script_gen.py + runner.py
Post: S-params (JSON + Touchstone + PNG), fields, NF2FF
    → app/postproc/*
GUI
    → app/ui/main_window.py
    → app/ui/sparam_viewer.py  (scikit-rf Smith, markers, cursor)
```

### Critical easyMesh order

1. Create FDTD + CSX, `SetCSX`
2. **`enhance_csx(CSX, primitives_mesh_setup)`**
3. **`enhance_fdtd(FDTD, primitives_mesh_setup)`**
4. `SetBoundaryCond` / materials / geometry / ports / dumps
5. **`GenerateMesh` with the SAME `primitives_mesh_setup` dict**

Do **not** register geometry only via `GetAllPrimitives()` — easyMesh tracks the shared dict.

### CSXCAD quirks

- No `GetBackgroundMaterial`
- `AddMaterial` only `epsilon` / `kappa`
- `FDTD.AddLumpedPort(port_nr:int, R, start, stop, p_dir, excite, …)`
- `GetProperty` may fail → use `GetPropertiesByName`
- **Never reuse empty easyMesh placeholder properties** from `GetPropertiesByName`
  (`AddBox` fails with `'NoneType' object has no attribute 'append'`).
  Always `CSX.AddMetal` / `AddMaterial` / `AddConductingSheet` fresh.

### Script generation

- Template placeholders (not f-string): `__APP_ROOT__`, `__WORK_DIR__`,
  `__PROJECT_JSON__`, `__GEOM_JSON__`
- After every run: `sp.export_sparams(...)` → `sparams.json` + `sparams.s1p`/`.sNp` + `sparams.png`
- skrf Touchstone write: `ntwk.write_touchstone(str(path))` — **no** `frequency_unit` kwarg
- Fallback: built-in Touchstone v1 writer (Mag/Ang); skrf may write RI

### Meshing rules

- Hard limit: max cell ≤ **λ_min / 10**
- Presets only set generation targets:
  - low ≈ λ/10, medium ≈ λ/12, high ≈ λ/16, very_high ≈ λ/25
- Grading ≤ `max_grading_ratio` (default 2)
- PML after air margin
- **Domain size** (viewer black frame) = geometry bbox + air margin + cell rounding:
  - `boundary.air_margin` per face; `auto` → λ/4 at the **stop** frequency, εr=1
    (`rules.air_margin_units`); the GUI writes one value to all 6 faces
    (`sim_params.spn_air`, uncheck "Air margin auto")
  - easyMesh: `boundary_distance` → graded air gap (`meshProcessing.add_graded_mesh_lines`),
    then `PML_size + 1` cells appended **outside** each PML face (`meshProcessing.py:355`)
    — no effect on MUR faces
  - air cell cap `max_cellsize_air` = λ/10…λ/40 by preset (`meshingUtils.py:43`) → the
    outer line snaps to cells (few mm off, can differ slightly per face)
  - levers: air margin (Sim Params → Boundary), stop frequency (auto margin), geometry
    size, PML size, mesh preset → then **Build Mesh** + **Fit NF2FF to Geometry**
  - check: `validate_mesh` → `_check_primitive_bounds` compares every primitive AABB
    (`preview.iter_primitive_infos`) against `GetSimArea` (`preview.primitives_outside_domain`,
    tol = 1e-6·span) → issue `primitive_outside_domain` (**error** for geometry,
    warning for dump/probe; openEMS would clip material outside the domain silently).
     The Mesh Viewer flags the same prims red + `OUTSIDE DOMAIN` note.
  - Cylinders along **x/y** are covered at both ends (`geometryUtils.process_primitive`
    loops `for end in (start, stop)`); regression `tests/test_cylinder_extent.py`

### Tuned reference geometry (Patch)

| Item | Value |
|------|--------|
| Patch | **30.2 × 37.8 mm** |
| Feed x | **−5.7 mm** |
| Substrate | 60 × 60 × 1.524 mm, εr = 3.38 |
| Boundary | MUR sides / PEC ground (tutorial style) |
| Scaling from 32×40 mm | ×(2.315 / 2.45) ≈ 0.945 |
| S11 (prod) | **−17.2 dB @ 2.470 GHz**, −9.4 dB @ 2.45 GHz |

NF2FF box must stay **inside** the air domain.

S11 postproc: `port.CalcPort(..., ref_impedance=50.0)` with `TypeError` fallback.
End criteria must not fire before Gaussian excitation ends (~6621 TS at f0=2.45 GHz).

---

## 4. Completed work (keep up to date)

### P0 – Foundation
- Project skeleton, easyMesh install, import checks (`verify_imports`)
- Models, FreeCAD IO, meshing, CSX builder, sim runner, postproc, UI

### Meshing / validation
- `compute_targets`: hard λ/10 + preset-based targets
- Shared `primitives_mesh_setup` in GUI mesh build and sim script
- Material creation always via `CSX.Add*`
- STEP bounds parser for OCCT `CARTESIAN_POINT('',(0.,0.,0.));` (nested parens)
- NF2FF eval freqs: `freq_mode` auto (S11 min) / count (N pts over mesh range) / list (Hz-kHz-MHz-GHz entries)
- NF2FF box tightened into air domain

### Simulation / postproc
- Port API fixed (`AddLumpedPort` with int; `BuildResult.fdtd`)
- Ground plane + MUR/PEC for patch example
- Always Touchstone export
- Production run: `work/patch_prod/simulation/` (S11, NF2FF, fields)
- Shared example: `work/patch_antenna/simulation/`
- **Multi-port S-params** (`app/postproc/sparams.py`): one FDTD run = one matrix column —
  excited port from `excite_port` flag (fallback: max `|uf_inc|`), column
  `S{idx}{exc_col} = uf_ref / exc_inc`, `S_{name}` aliases, **NaN for unmeasured cells**
  (never silent zeros); `load_sparams_json` + `merge_sparam_tables` combine runs;
  Touchstone write is **skipped while the matrix is incomplete**, NaNs sanitized on write
- **PCB filter example**: `examples/run_pcb_filter_example.py` — FreeCAD geometry
  (FCStd → per-object STL, 35 µm copper; `--analytic` = legacy analytic boxes),
  2 excitations → merged 2×2 (`work/pcb_filter/simulation/sparams.{json,s2p,png}`);
   FreeCAD **−29.99 dB @ 2.450 GHz**, analytic −28.96 dB @ 2.429 GHz (0.9 % = real copper)
- **NF2FF far-field postproc** (`app/postproc/nf2ff.py`): openEMS binding takes
  theta/phi in **degrees**; harvest + `_compute_directivity_gain` (`D = 4π·P_rad/Prad`,
  `Prad` = spherical integral of `P_rad` — authoritative, the openEMS attr can be
  sign-flipped), `η = Prad/P_acc` with `P_acc = 0.5·Re(U·I*)` from the port time
  series (openEMS pulse-DFT `2·Σ·Δt`, same spectral units as `Prad`), gain `= D·η`;
  exports `nf2ff/nf2ff.json` + `nf2ff_summary.png`; the recording box is built with
  **all 6 faces** (direct `nf2ff()` call, see §8 2026-10-12)

### GUI
- English UI; tabs Geometry / Mesh / Sim Params / Ports / S-Params — **generic antenna/PCB tool**, not patch-only
- **Antenna tab** (`app/ui/antenna_viewer.py`): NF2FF far-field viewer — 3D gain polar,
  2D E/H-plane cuts, directivity/gain/η vs frequency, pop-out window; menu
  `Simulation → Antenna Pattern Viewer` / `Pop-out Antenna Graph`; auto-refresh when a
  run produces `nf2ff/nf2ff.json` (log sniff); NF2FF checkbox + eval freqs in Sim Params
  (`project.sim.nf2ff_enabled` **and** `project.nf2ff.enabled` must both be set)
- **Generic workflow (any antenna / PCB):**
  - Import STEP/STL/OBJ/FCStd → triangles saved as `.npy` + auto `stl` primitives
  - Add Box / Cylinder primitives; Add Port (lumped/msl/rect_waveguide dialog)
  - Add Material / Assignment dialogs (role + material comboboxes)
  - Field dump editor; NF2FF box edit / **Fit to geometry + air margin**
  - `project.primitives` persisted with the project JSON
  - **Preset: Patch Antenna…** is optional; patch group in Sim Params only when preset active
  - **Preset: Yagi Antenna…** / **Preset: PCB Filter…** — replace the project (GFRP boom
    assignment kept; PCB filter = FR4 board + 50 Ω line + open stub + 2 lumped ports)
  - Mesh build supports box/cylinder/polygon/stl + port types (not lumped-only)
- **Script… toolbar button** (`app/ui/script_editor.py`, modeless `ScriptEditorDialog`):
  - View / edit `work/<project>/project_run.py`: **Save**, **Revert**, **Regenerate from project**, unsaved-close guard
  - `MainWindow._generate_script` = save JSON + regenerate + keep `project_run.py.lastgen`
    (pristine baseline); hand-edits detected against it → backed up to `project_run_edited.py`
  - **Run Simulation** with edits present asks: regenerate (back up) or run your edited script as-is
  - Second **Script…** click reuses the open dialog (`show_path`, keeps unsaved edits)
- **Frequency control (Mesh Parameters window, `Ctrl+Shift+M`):**
  - `f0` = Gauss excitation center + mesh target; `fc` = corner (bandwidth/2)
  - **Start / Stop frequency** are first-class editable widgets
  - Edit f0/fc → start/stop = f0±fc; edit start/stop → f0 clamped into band, fc = (stop−start)/2
  - Mesh λ hard limit uses `f_max = max(start, stop, target)` (free-space εr=1)
- **Sim Params** (`app/ui/sim_params.py`):
  - Read-only **computed** values (λ_air, max/target cell, air margin λ/4, effective boundary)
  - Editable: end_criteria, max_timesteps, NF2FF θ/φ, boundary ×6 + PML size, air margin auto/numeric
  - Mesh overrides: refined/min/max cellsize (0=auto), grading, smooth metal edge, easyMesh toggles
  - Patch geometry dialog (W/L/εr/h/ground/feed/f0/fc) → rebuilds primitives; stored in `project.patch_params`
  - Loaded projects fill all widgets (`_sync_form_from_project`); values applied on Build/Run/Save
  - `max_timesteps` user value **respected** (warning if &lt;150k, no silent override)
  - **Standalone window** (`SimParamsDialog`): Simulation menu → **Simulation Parameters…**
    (`Ctrl+Shift+P`) — modeless, hosts its own `SimParamsPanel` (`_sim_params_dlg`);
    **Apply** → `write_to_project` + `sync_from_project` mirror into the tab + patch params;
    **Close** discards; patch **Apply** uses the dialog's values and re-syncs the dialog
    after the rebuild; second menu click re-uses the same window
  - **Mesh Parameters window** (`app/ui/mesh_params.py` `MeshParamsDialog`/`MeshParamsPanel`):
    Mesh menu → **Mesh Parameters…** (`Ctrl+Shift+M`) — modeless, hosts its own
    `MeshParamsPanel` (`_mesh_params_dlg`); **lazy** (edits never touch the project,
    readout comes from a draft buffer),     **Apply** → `write_to_project` (f0±fc mirror
    into project.mesh) + mirror into the hidden Mesh form widgets, **Close** discards;
    second menu click re-uses + re-syncs the window
- **S-Params viewer** (`app/ui/sparam_viewer.py`):
  - scikit-rf: Smith (`chart_type="zy"`, labels, border, VSWR circles),
    |S| dB / mag, Phase, VSWR, Re/Im
  - Markers on click, table (f, S complex, |S|, dB, Phase, Γ Re/Im/|∠, Z [Ω], Y [mS], VSWR, RL)
  - Marker detail pane: rectangular + polar Γ, Z = Z0(1+Γ)/(1−Γ), Y=1/Z, VSWR, RL
  - Z/Y for reflection traces (Sii); Smith labels show f + R+jX; cursor shows Γ/Z
  - **Pop-out graph:** viewer “Pop out graph” + toolbar “Pop-out S-Graph” → separate window
    (plot/trace, markers, S-min, clear, **Restore to tab**); shared state with tab viewer
  - Cursor motion readout, “Go to S-min”
  - Scan `work/*/simulation`
  - Auto-open after sim finishes
- **Simulator log** (single instance — no dock, no Logs tab):
  - Vertical panel under the tabs (`right_split`)
  - Toolbar **Log** toggle show/hide; state saved as `log_panel_visible`
  - Auto-shown on Build Mesh / Run Simulation if hidden
  - Buttons: Clear / Pop-out… / Save log… / Auto-scroll + line count
  - Second tab **Mesh Report** (`log_tabs` / `log_mesh_report`): single source via
    `_set_mesh_report(text)` (Build Mesh / ERROR paths fill it)
- **Window geometry**:
  - Default ≈ 92% of available screen, centered
  - Adaptive minimum size
  - Saved to `app_settings.json` on close, restored on start
  - Safety clamp after `show` if still too large

### Stability fixes (do not regress)
| Issue | Fix |
|-------|-----|
| GUI crash Qt6Gui during sim | Log from worker thread must not touch widgets → **Queue + QTimer drain on GUI thread** |
| GUI crash 2nd Build Mesh | `matplotlib.use("Agg")` in preview + FDTD/CSX freed while viewer holds CSX + `clear()` on live canvas → **backend-safe preview, retire old CSX/FDTD, clear() data-only, recreate canvases in set_csx** |
| `log_cb error: Use the function PySide6.QtCore.SIGNAL on signals` | Pass `self._log`, **not** `self._log_bridge.emit` |
| easyMesh `RuntimeWarning: Mean of empty slice` | From `easyMesh/meshProcessing.py` — not fatal; suppress in mesh generate |
| Empty S11 / NaN in NF2FF eval | nan-safe min; keep default eval freq if no finite data |
| Window larger than screen | Fit available geometry; save/restore geometry |
| Empty easyMesh props + AddBox | Always recreate materials via `CSX.Add*` |
| Touchstone kwarg error | skrf `write_touchstone` without `frequency_unit` |

---

## 5. How to run / verify

```bat
cd C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI
set PYTHONIOENCODING=utf-8
set QT_QPA_PLATFORM=offscreen

c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\verify_imports.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\test_models.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\freecad_import.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\smoke_patch.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\sparam_viewer_smoke.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\mainwindow_sparam_smoke.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\menu_smoke.py
```

GUI:

```bat
cd C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe main.py
```

Standalone EXE (PyInstaller onefile, ~150 MB):

```bat
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe build_exe.py
```

→ `dist\FreeCAD_OpenEMS_GUI.exe` (windowed). Freeze-aware paths in `app/paths.py`
(`sys._MEIPASS` root; settings `app_settings.json` next to the EXE); the bundle
ships source copies of `app/`, `third_party/easyMesh4openEMS/`, `examples/`,
`docs/` for the external simulation Python / FreeCADCmd. Startup failures land
in `main_crash.log` next to the EXE.

Interactive path: **Load Patch Example** → **Build CSX + Mesh + Validate** → **Run Simulation**
→ S-Params tab opens automatically.

The GUI also has a menu bar (`File/Project/Geometry/Mesh/Simulation/View/Tools/Help`)
in `main_window._build_menu`; its actions delegate to the toolbar buttons.

PowerShell: avoid complex `-c` one-liners with nested quotes — write scripts under `work\`.

---

## 6. Key files

| Path | Role |
|------|------|
| `app/main.py` | Entry point |
| `app/settings.py` | Paths + `window_geometry` |
| `app/ui/main_window.py` | Main UI (`_build_menu` menu bar), log thread-safety, geometry, run flow |
| `app/ui/script_editor.py` | **Script…** modeless editor for `project_run.py` (Save/Revert/Regenerate) |
| `app/ui/sparam_viewer.py` | Smith / markers / cursor (scikit-rf) |
| `app/simulation/script_gen.py` | Generated sim script + Touchstone export |
| `app/simulation/runner.py` | Subprocess pump (log_cb try/except) |
| `app/meshing/rules.py` | λ/10 hard limit |
| `app/meshing/easy_wrapper.py` | enhance + GenerateMesh order |
| `app/csxcad_builder/primitives.py` | Patch / Yagi / **PCB filter** presets, NF2FF box |
| `app/postproc/sparams.py` | S-params + Touchstone + multiport column fix/merge |
| `app/freecad_io/importer.py` | STEP/STL/OBJ/FCStd + bounds parser |
| `app/freecad_io/stl_import.py` | STL/OBJ/manifest/folder → `.npy` meshes + `stl` primitives |
| `examples/run_pcb_filter_example.py` | FreeCAD geometry (default; `--analytic` legacy) → 2 excitations → merged 2×2 S-matrix (`work/pcb_filter`) |
| `tests/` | Smoke / model / import / prod / viewer tests |
| `work/patch_antenna/`, `work/patch_prod/` | Reference sim outputs |
| `README.md` | User-facing docs |

---

## 7. Known limitations / open items

- [ ] Manual GUI walkthrough with a **real FreeCAD STEP** (not only built-in patch)
- [ ] Full FreeCAD GUI document (not just FreeCADCmd export) as first-class workflow
- [x] Mesh preview polish → interactive Mesh/Geometry Viewer + AppCSXCAD 3D handoff
- [x] Multi-port (S2P) full viewer validation — fixed: viewer JSON key filter `S\d\d`
  (was `S11`-only), one run = one column with NaN gaps, Touchstone skipped until the
  matrix is complete; `examples/run_pcb_filter_example.py` merges both columns and the
  viewer loads the result (`nports 2` from `.s2p` and `.json`)
- [x] GUI **multi-run driver** — *Simulation → Run All Ports (full S-matrix)*
  (`Ctrl+Shift+R`, `app/simulation/multirun.py`) runs one FDTD run per port and
  merges the columns into the complete S-matrix under `<project>/sparams/`.
  (Single *Run Simulation* still measures one column.)
- [x] NF2FF summary `Prad`/`Dmax` implausible — **fixed 2026-10-12** (see §8): missing
  6th dump face (`CreateNF2FFBox` auto-skips faces of PEC domain boundaries — even for
  an interior box — and the installed build cannot override `directions`), patch box
  z-min inside the substrate (ground outside the closed surface), sign-flipped openEMS
  `Prad`/`Dmax` attrs, and η from `1−|S11|²` (dimensionless) vs spectral `Prad`.
  Patch now: `Prad 2.31e-24 W·s²`, `Dmax 8.42 dBi`, `η 0.78`, **gain 7.36 dBi**
- [x] `script_gen` port type `"msl"` was wrong — it passed `port.resistance` as the metal
  property to `AddMSLPort`. **Fixed**: correct signature
  `AddMSLPort(nr, metal, start, stop, prop_dir, exc_dir, excite=…)`, strip metal
  auto-created, axes via `app/simulation/msl.py` (`prop_direction`/`exc_direction`
  or box-extent derivation), MSL ports built after `generate_mesh` (needs ≥5
  grid lines). Tests `tests/test_msl_port.py` + `port_defaults_smoke`. Lumped
  ports remain the primary tested path.
- [x] Mesh build runs in a worker thread (`_on_build_mesh` → `MeshBuildThread` →
  `app.meshing.build_task`, Qt-free core; GUI-thread `_finish_mesh_build` does
  the preview/viewer/record). The old blocking path stays for
  `_ensure_mesh_viewer_content` + tests.
- [x] Energy/end-criteria progress bar from openEMS stdout — status-bar
  `QProgressBar` + label driven by `app.simulation.progress.EngineProgress`
  (timestep budget, residual energy dB, elapsed, converged / max-TS).
- [x] Graded refinement at ε interfaces on **both sides** —
  `app.meshing.interface_refine.material_jump_sides` now also emits the inward
  dielectric face (deduped for adjacent media, metal-overlapped faces still
  skipped), so the ramp is inserted into the dielectric **and** the air; the
  metal-face pass is unchanged. Tests: `tests/test_interface_refine.py`
  (CSXCAD-free) + updated `tests/graded_interface_smoke.py`.
- [x] NF2FF *Fit to geometry* dropped cylinder radii (and skipped spheres/polygons)
  — **fixed 2026-10-17**: cartesian envelope now via
  `app.meshing.bounds.cartesian_primitive_bbox` (axis ± radius on the two
  perpendicular axes; sphere from centre/radius; polygon from its bbox), mirroring
  `polar_primitive_bbox`. Tests `tests/test_primitive_bounds.py`.
- [x] NF2FF box margin was too conservative (`0.7·λ(f_stop)/4` ≈ 17.8 mm ≈ 0.15 λ
  for the 58 mm dipole, inside the reactive near field). **Fixed 2026-10-17**:
  antenna mode exposes `SimSpec.antenna_nf2ff_lambda` ("NF2FF box" spin, λ at
  f_start) and `_on_fit_nf2ff` sizes the box at `antenna_nf2ff_lambda·λ(f_start)`
  clamped to 0.8× the domain air margin (PML). PCB runs keep the small box.
  `app.meshing.bounds.nf2ff_fit_margin_units` + `tests/test_nf2ff_fit_margin.py`.
- [ ] Optional: true 3D inside Qt (no VTK Python bindings; AppCSXCAD is the 3D path)

---

## 8. Session log (append-only)

### 2026-10-02
- Validator: λ/10 hard limit; presets = generation targets
- easyMesh shared dict; materials always `CSX.Add*`
- STEP OCCT bounds parser fixed
- NF2FF freq priority + box inside domain
- Always Touchstone export (`sparams.s1p`)
- S-Params viewer with full scikit-rf Smith grid (zy, labels, VSWR)
- Large scrollable simulator log dock + pop-out
- **Crash fix:** worker-thread log → Queue + GUI-thread QTimer (Qt6Gui)
- **Crash fix:** `log_cb=self._log` (not `Signal.emit`)
- easyMesh RuntimeWarnings suppressed; nan-safe NF2FF frequency pick
- Window: fit screen, save/restore geometry, adaptive minimum size
- **Log UI single instance:** remove bottom dock + Logs tab mirror; log panel under tabs; toolbar **Log** toggle (`log_panel_visible`); auto-show on mesh/sim
- **Sim Params** (hidden panel + Simulation Parameters window): computed targets visible; user can override end_criteria, timesteps, boundary, air margin, mesh cellsizes, patch geometry
- **S-param post-sim reliability:**
  - Export happens **after** FDTD.Run; `"Simulation finished"` no longer triggers viewer scan too early
  - `project_run.py` wraps S-param postproc (exit 3 on failure, exit 4 if json missing)
  - `export_sparams` JSON write protected; louder CalcPort / NaN errors
  - Runner persists `work/{name}/sim_log.txt` + `last_exit`
  - GUI `_check` **reports missing** sparams (dialog + log), does not fail silently
  - S-Params viewer auto-loads **newest** file on scan / post-sim / toolbar
- **Generic antenna/PCB workflow:** import→STL primitives, port/material/box dialogs, NF2FF fit; patch = preset only
- **FreeCAD → openEMS CAD workflow (primary path):**
  1. Draw solids in FreeCAD (name objects clearly, e.g. `patch`, `gnd`, `via`)
  2. Export **STEP** (or save FCStd / export STL/OBJ)
  3. GUI **Import Geometry…** — STEP/FCStd are tessellated via FreeCADCmd → per-object `.npy` triangle meshes; `.stl/.obj`, an `openems_export.json` manifest or a folder of per-object STLs are loaded directly (one `stl` primitive per FreeCAD object, names preserved)
  4. Objects auto-become `stl` primitives (default copper PEC) + Assignment (role/material)
  5. Change material via **Add Assignment…** (syncs primitive material)
  6. Build Mesh / Run — `project_run.py` also meshes STEP/STL/manifest at runtime if meshes missing (`--mesh-only` builds CSX + easyMesh and exits before FDTD)
  7. openEMS uses `CSX.AddMesh` (BREP STEP cannot be read by openEMS directly)
  - Helper: `examples/freecad_export_for_openems.py` (FreeCAD macro → STEP + per-object STL + manifest)
  - Tessellator: `app/freecad_io/freecad_mesh.py` (`tessellate_cad_to_meshes`, deflection 0.15 mm)
  - STL loader: `app/freecad_io/stl_import.py` (`load_stl_meshes` = file/folder/manifest → `.npy`, `stl_primitives_from_meshes`)
  - **Toolbar FreeCAD…** (`app/ui/freecad_tools.py` + `app/freecad_io/freecad_launcher.py`):
    - **Launch FreeCAD** — starts FreeCAD.exe (Settings: FreeCAD GUI / FreeCADCmd), cwd = work dir; open optional source
    - **Export CAD…** — FreeCADCmd runs `examples/freecad_export_for_openems.py` → STEP + per-object STL + `openems_export.json`; **Import as** = `STEP` (tessellate later) or `STL (one mesh per FreeCAD object)` (imports the manifest); optional auto-import into project
    - Settings: `freecad_gui` (auto-detect next to FreeCADCmd if empty)
    - Smoke: `tests/freecad_gui_smoke.py`
  - Headless STL import: `python examples/run_stl_import_example.py` (FCStd → manifest → 7 mesh prims → `project_run.py`); smoke `tests/stl_import_smoke.py`
- **Yagi-Uda preset (2.45 GHz):**
  - Toolbar **Preset: Yagi Antenna…** loads wire Yagi (reflector + driven dipole + 3 directors + boom)
  - FreeCAD model: `examples/freecad_yagi_model.py` → `work/yagi_antenna/yagi_2p45.FCStd|.step|.stl`, `yagi_params.json`
  - CSX cylinders + lumped 50 Ω port at driven-element gap; `yagi_antenna_primitives()`
  - Headless: `python examples/run_yagi_example.py`
  - Coordinate system: X=elements, Y=boom, Z=up
- **Log review → fixes (interactive patch):**
  - Materials created once (no empty Property #0–#2)
  - Ground metal restored at **0.25 mm** (PEC zmin alone → 0 PEC z-edges, S11≈0)
  - `min_cellsize=ground_thickness` so mesh resolves ground
  - Air margin always **εr=1** (was substrate εr → domain too small, NF2FF outside)
  - Mesh hard limit always **λ_air/10** (εr=3.38 rejected valid air cells)
  - NF2FF box conservative ±40 mm
  - `max_timesteps=200_000`, `end_criteria=1e-5`
- **Measured:** 30.2×37.8 mm → S11 **−20 dB @ 2.57 GHz** (working antenna)
- **Tuned:** 31.7×39.7 mm, feed −6.0 mm → target 2.45 GHz
- **Verify PASS:** `tests/verify_patch_s11.py` @ 80k TS → **S11 −18.01 dB @ 2.450 GHz**, NF2FF inside domain, no PEC-z issues; Touchstone `work\patch_verify\simulation\sparams.s1p`
- NF2FF box conservative **±40 mm** (still inside easyMesh domain); grading_jump is warning-only (patch z≤2.3, PCB filter z 9.14 from 35 µm copper)
- Verify script checks: dip < −8 dB, resonance within 0.15 GHz of 2.45, no "outside domain", no mesh ERROR
- **Mesh/Geometry Viewer integrated** (`app/ui/mesh_viewer.py`):
  - Interactive 2D slices XY/XZ/YZ with mesh grid + primitive AABBs + ports + NF2FF overlay
  - Per-axis cell-size plots; domain Overview (2×2 projections + stats)
  - **Scene Info tab** (`mesh_viewer_scene_info`, `main_window.py`): a
    `QPlainTextEdit` inside the log panel's `log_tabs` (title "Mesh Viewer Scene
    Info") — filled by `_sync_scene_info()` after `set_csx` and rebuilt after any
    viewer refresh (same font/monospace style as the log panel). The viewer itself
    has **no info panel / Info button** anymore; `mesh_viewer.info_text()` returns
    the built string (`_info_text`)
  - "Open in AppCSXCAD (3D)" — exports CSX XML and launches `c:\openEMS\AppCSXCAD.exe`
  - "Export CSX XML" for manual 3D review
  - **Toolbar buttons** (`Export CSX XML…`, `Open in AppCSXCAD (3D)…`) moved to
    the Mesh menu (Mesh → Export CSX XML… / Open in AppCSXCAD (3D)…)
  - After `Build CSX + Mesh + Validate`, viewer auto-populates from `_last_csx`
  - **NF2FF frame refreshes in place:** `main_window._on_fit_nf2ff`/`_on_edit_nf2ff` end with `_update_viewer_nf2ff()` → `mesh_viewer.set_nf2ff(project.nf2ff)` (plain redraw, no rebuild; logs "build mesh first" when no CSX is loaded yet)
  - Data extraction via `CSX.GetAllProperties()` + `GetTypeString()` + `GetBoundBox()`; grid via `GetGrid().GetLines()`
  - Smoke: `tests/mesh_viewer_smoke.py` PASS (7 primitives typed correctly, 63/90/31 mesh lines, all views render)
- **Crash fix: 2nd "Build Mesh" click killed the GUI** (hard AV, not Python exception):
  1. `save_mesh_preview` called `matplotlib.use("Agg")` while GUI used QtAgg → backend switch corrupted Qt canvases.
     **Fix:** rewrite to `Figure` + `FigureCanvasAgg` + `fig.savefig` — never `use()`, never `pyplot`.
  2. Freeing previous `FDTD` while viewer still held its `CSX` → CSXCAD parent destroyed → AV on draw.
     **Fix:** retire old CSX/FDTD into `_retired_csx`/`_retired_fdtd` lists (keep last 4); only replace after `set_csx`.
  3. `mesh_viewer.clear()` called `clf()`+`draw_idle()` on live canvas, then `set_csx` recreated canvases → AV.
     **Fix:** `clear()` only drops Python refs; figure lifecycle owned solely by `set_csx` → `_recreate_canvases()`.
  4. NavigationToolbar holds artist refs; recreate Figure+Canvas+Toolbar on every `set_csx`.
  5. Reentrancy guard: Build Mesh button disabled while `_mesh_building`.
  - Regression: 4 consecutive builds + viewer feed, backend stays QtAgg (`work/final_build_test2.py` PASS)

### 2026-10-07
- **Yagi dump/NF2FF box fix** (`primitives.py`, `main_window._on_fit_nf2ff`, `script_gen`):
  - NF2FF measured from the **geometry bbox centre** (was origin-based → 4/6 faces outside the FDTD domain → `Prad≈0`), margin = 0.7 × resolved air margin
  - `E_boom` dump = geometry bbox + 0.5 × that margin (was `x_half*0.5`, narrower than the reflector)
  - generated `project_run.py` warns on dumps outside the domain and clamps the NF2FF box into it
  - verified: NF2FF inside domain (+4.3/+1.9 mm x/y, +8 mm z), dump span 82.6 mm > geometry 61.2 mm
- **STL import option** (FreeCAD objects → per-object STL → mesh):
  - `app/freecad_io/stl_import.py` — `load_stl_meshes` (file / folder / `openems_export.json`) + `stl_primitives_from_meshes`
  - GUI **Import Geometry…** takes manifest/folder/multi-STL (names preserved); re-import refreshes primitives instead of duplicating; `geometry_files` + `import_mesh_dir` deduped
  - `FreeCADToolsDialog` **Import as**: `STEP` vs `STL (one mesh per FreeCAD object)` → imports the manifest; `export_cad_for_openems` resolves the source path (relative source exported nothing, empty log)
  - `script_gen`: `ensure_simulation_meshes` + generation fallback handle stl/obj/manifest/folder and skip duplicate names
  - `project_run.py --mesh-only` — CSX + easyMesh + validation, skips the FDTD run
  - Example `examples/run_stl_import_example.py` (FCStd → manifest → 7 named mesh prims → script) and smoke `tests/stl_import_smoke.py` (folder/manifest/script/GUI: PASS)
  - Docs: README workflow + test list, `docs/ANLEITUNG_DE.md` Variante A/B
- **Yagi FDTD confirmation run** (`work/yagi_antenna`): no `outside the field domain` warnings (0 in stdout/stderr), energy decays (−18 → −37 dB over 22k TS), 15.6 MC/s
- **Crash fix: generated `project_run.py` died with `AssertionError` inside `openEMS.Run`** (openEMS.pyx:489 = `assert os.getcwd() == os.path.realpath(sim_path)`, checked right after `os.chdir(sim_path)`):
  - Cause: `generate_run_script` was handed a **relative** `work_dir` → `WORK_DIR`/`PROJECT_JSON`/`GEOM_JSON` were written relative → openEMS chdir'd into `work\yagi_antenna\simulation` and then re-resolved the still-relative `sim_path` from the new cwd → assert failed before the solver banner
  - Fix: normalize with `os.path.abspath()` (no symlink resolution — `realpath` would break on junctioned dirs like OneDrive `Documents`) in the generator **and** in the template (`Path(os.path.abspath(r"..."))`)
  - Regression: relative-path scripts (`work/stl_import`, `work/yagi_antenna`) regenerated absolute; short yagi run now reaches the FDTD loop
- **Yagi geometry dedupe**: `geometry_primitives.json` held two STL generations (`Part__Feature*` from the STEP export + `Yagi_Uda_2p45_*` from the FCStd export) → kept the named FCStd set (7 cylinders + 7 STL); `Unused primitive (type: PolyhedronReader)` warnings 11 → 4 (residual = intentional cylinder@pri50 + STL@pri10 overlap)
- **Yagi `max_timesteps` preset → 25 000** (`primitives.py` yagi `SimSpec`): short default run (~3 min at ~150 TS/s, 3.2× the 7862-TS excitation); `end_criteria=1e-5` still stops earlier when converged. `work/yagi_antenna/*.json` + `work/stl_import/*.json` updated from 250 000 → 25 000. GUI warning floor stays 150 000 (`main_window.py:2242`) → the Yagi preset now logs the "may not converge" hint by design (raise `max_timesteps` for tighter S-Params)
- **Yagi boom → GFRP dielectric** (was `copper`): the boom (r=1.5 mm along y at x=z=0) ran straight through the feed gap (port box y=0, x∈[−1,1]) → **port shorted** — baseline `|uf_ref|/|uf_inc|=1.008`, `Prad 8e-28 W`, S11 flat ≈ 0 dB
  - preset: `MaterialDef(name="gfrp", kind="dielectric", epsilon=4.3, kappa=tand·2πf₀ε₀εᵣ≈0.0117, tand=0.02)`, boom prim `material="gfrp", priority=40` (below copper 50 → element/boom junctions stay metal), `GeomAssignment(object_name="boom", role="dielectric", material="gfrp", priority=40)` for the CAD/STL path
  - `main_window._import_geometry_path`: imported STL prims take material/priority from an existing assignment instead of hardcoded `copper`/10 (the assignment itself was already preserved)
  - `examples/run_stl_import_example.py`: honors per-object preset assignments (boom → gfrp@40, others copper@50) instead of forcing everything to copper@50
  - CSXCAD priority semantics verified empirically with `GetPropertyByCoordPriority`: **higher priority wins** ⇒ GFRP@40 beats the duplicate STL boom mesh@10
  - after fix: `|uf_inc|=7.59e-12 |uf_ref|=5.42e-12` (Γ=0.71), S11 physical (−16.1 dB min @ 2.24 GHz, −3.1 dB @ 2.45 GHz), no `outside the field domain`, DONE in ~3 min
- **PCB filter preset + 2-port example (multi-port end-to-end):**
  - `primitives.pcb_filter_primitives(f0=2.45e9, fc=2.15e9, line_width=3.0, line_half_length=32, port_offset=30, substrate_eps=4.4/tand=0.02/t=1.6)` — FR4 board x ±34 mm, ground box z −0.25…0 (prio 90), substrate 0…1.6 (prio 10), 50 Ω line −32…+32 at z=1.6 (prio 100, `edges2grid="xy"`), open λg/4 stub (`0.3·t` open-end correction), 2 lumped edge ports at x=±30 (box z 0→1.6, y=width, `mesh_dirs="xy"`; line runs to ±32 → port cell sits under trace+ground), E-dump slice z=0.8, `nf2ff_enabled=False`, boundary MUR×4/**PEC** z-min/MUR, `air_margin=[10,10,10,10,0,10]`, mesh preset `high` min 0.4 mm, `SimSpec(end_criteria=1e-5, max_timesteps=50_000)`
  - `examples/run_pcb_filter_example.py` — flips excite between `in1`/`out1`, two subprocess runs (+ logs), merges JSONs → `work/pcb_filter/simulation/sparams.{json,s2p,png}`; args `--skip-run`, `--mesh-only`, `--analytic` (legacy analytic boxes), `--rebuild` (new FCStd model + export)
  - **FreeCAD geometry (default)** — FCStd → 4 per-object STLs (35 µm copper), `geometry_primitives.json` stl prims: S21 **−29.99 dB @ 2.450 GHz** (design target), passband −0.72 dB @ 0.3 GHz, reciprocity Δmax **0.0004**
  - analytic (`--analytic`, zero-thickness strips): S21 **−28.96 dB @ 2.429 GHz** (0.9 % from the 2.45 GHz target = real copper thickness), passband −0.73 dB @ 0.3 GHz, S11/S22 min −21 dB, reciprocity Δmax **0.0004**; convergence 50 000 vs 150 000 TS → Δmax **0.019 dB** (`work/pcb_filter_conv`, `Temp\opencode\conv_pcb.py`) → preset keeps 50 000 TS (the "max_timesteps reached" warning is expected here)
  - `[WARNING] grading_jump: Axis z 9.14` is expected too (0.035 mm Cu cells next to 0.32 mm substrate cells) — warning-only, exit code 0
  - **STL-as-Polyhedron gave a wrong stop band** (2.042 + 2.364 GHz double dip): openEMS clamps metal per Yee node (`FDTD/operator.cpp` `CalcPEC_Range` → `GetPropertyByCoordPriority` at staggered coords); `CSPrimBox.IsInside` is inclusive on faces while the polyhedron ray-cast returns *outside* on mesh-line-coincident faces (asymmetric: x=+32 inside, x=−32 outside) → edge/face nodes not clamped → effective line/stub shifted. Mesh lines and the 56 160-cell material map were **identical** for box vs STL — only the primitive type differed. Fix: `triangle_mesh.axis_aligned_box_bounds` emits `AddBox` for axis-aligned FreeCAD solids (re-verified: single notch 2.450 GHz, no *Unused primitive*); non-axis-aligned solids stay `PolyhedronReader` (same face caveat applies at AABB faces)
  - GUI **Preset: PCB Filter…** (`main_window._on_load_pcb`) + info block with stub length
- **PCB band-pass preset (5.6 GHz, five open stubs, Teflon):**
  - `primitives.pcb_bandpass_stub_primitives(f0=5.6e9, substrate_eps=2.1, substrate_tand=0.0002, substrate_thickness=0.254, ground_thickness=0.25, feed_length=7.0)` - 5-pole Chebyshev (0.1 dB), five open lambda/2 stubs + four lambda/4 inverter lines, no vias; `PCB_BANDPASS_DESIGN` holds Z/k tables (fit of the exact commensurate network), `msl_width/msl_eps_eff/msl_open_end` = Hammerstad synthesis. Widths 0.80/3.74/0.50 mm, lengths 18.37/18.69/22.91 mm; lines 43.85/61.01 ohm, 11.78/8.04 mm. Board 56.6 x 34.3 mm, PTFE, ground prio 90, substrate 10, copper 100 (`edges2grid="xy"`), E-dump z=0.127, MUR x/y/zmax + PEC zmin, `min_cellsize=0.15`, `min_cellsize_z=0.0847` (3 cells in the substrate), `smooth_metal_edge="False"` + `handle_closely_placed_edges=False` (easyMesh made a 4 um Y cell -> CFL dt /8, sim never decayed), `SimSpec(end_criteria=1e-5, max_timesteps=80_000)`.
  - `examples/run_pcb_bandpass_example.py` - project + scripts for in1/out1, two FDTD runs, merge to `work/pcb_bandpass/sparams/sparams.{json,s2p,png}` (older runs wrote `simulation/`, `_sparams_json` checks both); args `--skip-run`, `--mesh-only`; deletes `simulation/` before a run (openEMS refuses an existing output dir).
  - Simulated: |S11| < -10 dB 4.90-6.45 GHz, S21 -0.1..-0.3 dB, rejection -42 dB @4.0 GHz / -38 dB @7.2 GHz, 32 130 TS (78 s) per port, 259k cells. GUI preset `Preset: PCB Bandpass (5.6 GHz)...` (`_on_load_pcb_bandpass`, saves `work/pcb_bandpass/pcb_bandpass.fcsim.json`).
- **Multi-port S-param fix** (`app/postproc/sparams.py`):
  - `compute_sparams_from_ports` rewritten: `CalcPort` for all ports, `_excited_wave_position` (flag first → max `|uf_inc|` fallback → `None` = no silent fallback), column `S{idx}{exc_col} = uf_ref / exc_inc` (openEMS tutorial convention), aliases `S_{name}`, unmeasured cells stay **NaN** + NOTE/ERROR prints
  - new `load_sparams_json` + `merge_sparam_tables` (freq equality, alias keys dropped, first finite value wins, mismatch warning)
  - `write_touchstone` sanitizes non-finite (`nan_to_num` + comment); `export_sparams` **skips Touchstone** while `_collect_s_matrix` has NaN — JSON keeps the gaps
  - viewer `load_network_from_path` JSON branch: keys `S\d\d` instead of `if "S11" in data` (S21/S12/S22 were dropped before)
- **Script editor — view/edit `project_run.py` in the GUI:**
  - new `app/ui/script_editor.py` — modeless `ScriptEditorDialog`: path label, monospace `QPlainTextEdit`, **Regenerate from project / Revert / Save / Close**, `is_modified()` + unsaved guards on save/reload/close
  - `main_window`: toolbar **Script…** button, `_on_edit_script` (generates the script when missing, opens modeless, second click reuses the dialog via `show_path` which keeps unsaved edits), `_prepare_run_state` + `_generate_script` factored out of `_on_run`
  - baseline `project_run.py.lastgen` written on every generation **and** on first editor open; difference vs the on-disk file → hand edit → backup `project_run_edited.py` + NOTE in the log; **Run Simulation now asks** regenerate (back up) vs run your edited script as-is
  - help texts added: Sim Params (Run script block), Geometry (presets), Ports / Dumps (one run = one column + example), S-Params (NaN gaps / Touchstone skip)
  - test `tests/script_editor_smoke.py` — dialog load/edit/save/regenerate/revert, MainWindow open + reuse, backup + no-false-positive, PCB preset (PASS); regression: `pytest tests -q` 4 passed, all smoke scripts EXIT 0
  - docs: README (Script…, PCB preset, project files, test list), PROMPT §4/§6/§7, `docs/ANLEITUNG_DE.md` §4/§6/§7/§9/§10
- **`primitive_outside_domain` validator + Yagi cylinder domain fix** (user report: GUI build shows 3 Yagi prims poking out of the black domain frame):
  - `app/meshing/preview.py`: `primitives_outside_domain(infos, area, tol=None)` → `[(info, axis_idx, overflow, side)]`, `is_diagnostic_kind()` (dump/probe/res box = warning, everything else = error); `app/meshing/validators.py`: `_check_primitive_bounds` after the port check, new `report.stats["primitives_outside_domain"]`; error severity blocks `report.ok` → script generation stops instead of simulating clipped material
  - Mesh Viewer: `mesh_viewer._outside_names`, affected prims drawn **red** (slice + overview), overview panel `OUTSIDE DOMAIN: N` + up to 4 names, slice title suffix `[OUTSIDE DOMAIN]`
  - **Root cause**: the Yagi preset is **7 analytic cylinders** (not the 14 STLs) and `easyMesh/geometryUtils.process_primitive` CYLINDER/CYLINDRICALSHELL used **only `start ± radius`** → a cylinder along **x/y** registers just its start end as mesh lines → `mesh_data` stopped at x=+2.0 / y=+72.4 → domain `[-55.29,-75.79,-24.70] → [25.70,94.93,24.70]` (span 81.0×170.7×49.4) → reflector (+4.89), `driven_right` (+3.36), `director_1` (+1.22 mm) outside. Headless probes were clean because they also mesh the **STL** copies (poly AABB = both ends). Same root cause explains why x/y cylinders in other presets could silently clip
  - Fix in `third_party/easyMesh4openEMS/easyMesh/geometryUtils.py`: `for end in (start, stop)` in both branches (z-cylinders unchanged — duplicate entries de-dup downstream). **site-packages is stale by default** → run `work/patch_site_easy_mesh.py` after editing `third_party`; verified with it: domain `[-55.29,-75.79,-24.70] → [57.62,116.17,24.70]` (112.9×192.0×49.4), `outside: 0`, mesh lines 60/120/17, only the pre-existing `grading_jump z 2.3` warning
  - Tests: new `tests/test_cylinder_extent.py` (process_primitive covers start **and** stop for x/y + z; full `generate_mesh` → every prim AABB inside `GetSimArea`, no `primitive_outside_domain`), `tests/test_models.py::test_primitive_outside_domain`, `mesh_viewer_smoke` `out_of_domain#0` → `pytest tests -q` **11 passed**; smokes (mesh_viewer, mparam/mainwindow, polyhedron, dipole, stl, ports, script_editor, tab_help, generic, sim_params, freecad_mesh) all EXIT 0; `pcb_filter --mesh-only` still clean
  - docs: PROMPT §5 meshing rules + easyMesh site-packages note, `docs/ANLEITUNG_DE.md` §5.6 frames legend, `tab_help` meshing help
- **Menu bar** (`app/ui/main_window.py` `_build_menu`): QMenuBar `File / Project / Geometry / Mesh / Simulation / View / Tools / Help` over the existing toolbar buttons — every entry is a `QAction` that calls `btn.click()` or a plain slot, so buttons and their tests are unchanged; checkable buttons mirror their state; menu entries re-sync on `aboutToShow` (also grey out while a button is disabled); View lists the workflow tabs + `Log Panel`; shortcuts `Ctrl+N/O/S/Q`, `F5` (Build Mesh), `Ctrl+R` (Run), `Ctrl+L` (Log), `F1` (Help), `Del` (Delete Selected); `File` adds **Open Work Folder**, `Help` adds **ANLEITUNG/README/About**.   Test: `tests/menu_smoke.py` (standalone — Qt + the dash pytest plugin abort on import); docs: `ANLEITUNG_DE.md` §3, `tab_help` (Presets/F5), PROMPT §5/§6
  - **View-menu fix:** `QAction.triggered(bool checked)` overwrites the first lambda/def argument — `lambda i=idx: setCurrentIndex(i)` switched to tab 0/1 (the bool) for every entry. Slots must be 0-arg closures (`_make_switch(idx)` returning `def _switch(): ...`); `menu_smoke` now triggers every View entry (regression guard)
  - **Simulation Parameters window:** Simulation menu → **Simulation Parameters…** (`Ctrl+Shift+P`) opens `SimParamsDialog` (modeless, own `SimParamsPanel`, `_sim_params_dlg`); **Apply** writes project + mirrors the hidden Sim Params panel, **Close** discards, second click re-uses + re-syncs the window; NF2FF fit/edit reuse the MainWindow slots; patch **Apply patch** uses the dialog's values and     re-syncs after the rebuild; test `test_sim_params_dialog` in `tests/menu_smoke.py`; docs `ANLEITUNG_DE.md` §5.0, `tab_help` Simulation Parameters
- **Mesh Parameters window:** Mesh menu → **Mesh Parameters…** (`Ctrl+Shift+M`) opens `MeshParamsDialog` (modeless, own `MeshParamsPanel`, `_mesh_params_dlg`); **lazy** (edits never write the project — readout from a draft buffer), **Apply** writes project.mesh (f0±fc mirror) + mirrors the hidden `_mesh_form` widgets (see bullet below), **Close** discards, second click re-uses + re-syncs; test `test_mesh_params_dialog` in `tests/menu_smoke.py`; docs `ANLEITUNG_DE.md` §5.0, `tab_help` Mesh Viewer
- **Log panel Mesh Report + Scene Info tabs** (`main_window.py`): the vertical log panel (`right_split`) is now a `QTabWidget` (`log_tabs`) with **Log** (existing `log_view`) + **Mesh Report** (`log_mesh_report`, same font/no-wrap as Log) + **Mesh Viewer Scene Info** (`mesh_viewer_scene_info`). Single source for the report: `_set_mesh_report(text)` fills `log_mesh_report` only — the Mesh tab page was removed (see next bullet), the hidden `mesh_report` widget stays as the off-screen build target. Scene Info: `_sync_scene_info()` reads `mesh_viewer.info_text()` after `set_csx`; `log_mesh_report` and `mesh_viewer_scene_info` are `QPlainTextEdit` read-only, no wrap. Tests `test_log_mesh_report_tab` + `test_mesh_viewer_scene_info_tab` in `tests/menu_smoke.py` (tab exists, single-source fill, switch); docs `tab_help` Mesh
- **Mesh tab page removed** (`main_window.py`): the old "Mesh" workflow tab (preset, f0/fc/start/stop, drawing unit, `btn_build_mesh`, in-tab report) is gone — all of it lives in the **Mesh Parameters window** (Mesh menu → `Ctrl+Shift+M`). The form widgets survive as a hidden off-screen `QWidget` (`self._mesh_form`, **kept referenced** — a bare local would be GC'd and its child spins/combos become deleted C++ objects → `RuntimeError: libshiboken … already deleted`); they remain the canonical `_sync_project_from_form`/`_sync_form_from_project` target so Build (F5 / `btn_build_mesh` in the Mesh menu) keeps reading from them. `btn_build_mesh` is now only reachable via the Mesh menu (it is no longer on a tab); `self.mesh_report` (hidden QPlainTextEdit) stays as the report buffer `_set_mesh_report` writes before the log tab. Mesh menu "Mesh Report" now runs the slot `_show_mesh_report_tab` (shows the log panel's Mesh Report tab) instead of toggling the removed in-tab report button. `self.mesh_tab_index` dropped (was `None`); the View menu auto-lists tabs from `_tab_title_map`, so "Mesh" disappears there on its own. Tests: `tests/menu_smoke.py` View-tab list now asserts `"Mesh" not in tabs`; `tests/tab_help_smoke.py` iterates `TAB_ORDER` (removed "Mesh" **and** "Mesh Viewer Scene Info" — a log panel tab, not a workflow tab, so it had no `TAB_HELP` entry and `help_for` fell back to `_DEFAULT_HELP`). Docs `ANLEITUNG_DE.md` §3/§5.0 (no "doppelte Quelle" / "Mesh-Tab" wording) and `tab_help.py` `TAB_ORDER`+`TAB_HELP` cleaned; a stray `self._mesh_form` GC crash and the `info_view`→`info_text()` rename in `tests/mesh_viewer_smoke.py` were fixed along the way. Ver pytest **11 passed**; smokes `menu`, `tab_help`, `freq_units`, `generic_workflow`, `sim_params`, `mesh_viewer`, `script_editor`, `freecad_mesh` all **PASS / EXIT 0**.
- **Help for the Mesh / Sim Parameters windows** (`app/ui/tab_help.py`, `app/ui/mesh_params.py`, `app/ui/sim_params.py`): both dialogs get a **Help** button in a slim header row (label + `QPushButton("Help")`, not a `QDialogButtonBox` button). `help_btn.clicked → lambda _=False, p=self: show_tab_help(p, "Mesh Parameters")` / `show_tab_help(p, "Simulation Parameters")` — the `lambda _=False` is needed because `QPushButton.clicked` emits `checked: bool`, which would otherwise swallow the `p` binding. Two new `TAB_HELP` keys (deliberately **not** in `TAB_ORDER`, so the View menu and the workflow-tab list stay as they are — they are dialogs, not tabs): `"Mesh Parameters"` (presets, unit, f0/fc, start/stop, drawing unit, readout, Apply/Close, Apply-before-Build, domain-size pointer → Sim Params) and `"Simulation Parameters"` (Apply/Close, patch, NF2FF fit/edit, second-click re-sync, domain size, tips). `mesh_params.py`/`sim_params.py` add `show_tab_help` to their module imports (plus `QHBoxLayout`/`QPushButton` for `mesh_params`); the old `Mesh tab` wording in the Mesh dialog's hint is now `mesh form`. Tests: `test_mesh_params_dialog` / `test_sim_params_dialog` in `tests/menu_smoke.py` now assert the Help button exists (`findChildren(QPushButton)` with `text() == "Help"`) and the topic is in `TAB_HELP`. Ver pytest **11 passed**; smokes `menu`, `tab_help`, `sim_params`, `generic_workflow`, `mesh_viewer` all **PASS / EXIT 0**.
- **Sim Params tab page removed** (`main_window.py`): the old "Sim Params" workflow tab (computed targets, end_criteria, max_timesteps, boundary ×6 + PML size, air margin, mesh cellsize overrides, patch geometry dialog, NF2FF θ/φ) is gone — all of it lives in the **Simulation Parameters window** (Simulation menu → `Ctrl+Shift+P`). The panel survives as a hidden off-screen `QWidget` (`self._sim_form`, **kept referenced** — same GC pitfall as `_mesh_form`: a bare local would be GC'd and its child spins/combos become deleted C++ objects → `RuntimeError: libshiboken … already deleted`); it remains the canonical `_sync_project_from_form`/`_sync_form_from_project` target, so run/save/Build keep reading from it and the `Simulation Parameters` window mirrors into it. The `self._tab_title_map` is rebuilt from the remaining tabs, so the **View** menu and the Help-button wiring automatically drop "Sim Params". `TAB_ORDER` in `tab_help.py` no longer contains `"Sim Params"` (and the `"Sim Params"` `TAB_HELP` entry was folded into the `"Simulation Parameters"` entry, which now documents the window as the replacement for the former tab). `sim_params_smoke.py` now asserts `"Sim Params" not in tabs` and that `win.sim_params` still exists; `menu_smoke.py` asserts `"Sim Params" not in tabs`. Docstrings in `sim_params.py` (`SimParamsDialog`) and log strings in `main_window.py` (patch-preset geom_info, `_apply_sim_params_dialog` docstring) now say "hidden Sim Params panel" / "Simulation Parameters window" instead of "Sim Params tab". Ver pytest **11 passed**; smokes `menu`, `sim_params`, `tab_help`, `generic_workflow`, `mesh_viewer`, `freq_units` all **PASS / EXIT 0**.
- **Ports / Dumps tab page removed** (`main_window.py` + new `app/ui/ports_dumps.py`): the old "Ports / Dumps" workflow tab (port list + Edit/Remove, `chk_nf2ff` / `chk_dumps`, Add Field Dump…, Edit NF2FF Box…) is gone — all of it lives in the **Ports / Dumps window** (Geometry menu → **Ports / Dumps…**, `Ctrl+Shift+D`). Unlike the Sim/Mesh Parameters dialogs (own panel + Apply/Close round-trip), this window **hosts the existing live widgets directly** (`PortsDumpsDialog(ports_form, parent)` re-parents `self._ports_form` into its layout) — the port/dump/NF2FF widgets are already wired to the MainWindow slots (`_on_add_port`/`_on_edit_port`/`_on_remove_port`/`_on_add_dump`/`_on_edit_nf2ff`), so edits apply immediately with no Apply/Close. The widgets survive as a hidden off-screen `QWidget` (`self._ports_form`, **kept referenced** — same GC pitfall as `_mesh_form`/`_sim_form`); the dialog re-parents them on first open, and `_on_open_ports_dumps_dialog` calls `_refresh_ports()` on every open. `_ports_dlg` is created lazily and re-used (`self._ports_dlg`). `TAB_ORDER` in `tab_help.py` drops `"Ports / Dumps"`; the `TAB_HELP` entry stays (dialog topic, now opened from Geometry menu, live-editor wording) and the Geometry help no longer says "on Ports / Dumps tab". Tests: `menu_smoke.py` asserts `"Ports / Dumps" not in tabs` + the Geometry menu entry exists with `Ctrl+Shift+D`. Ver pytest **11 passed**; smokes `menu`, `tab_help`, `sim_params`, `generic_workflow`, `mesh_viewer`, `freq_units`, `port_defaults`, `script_editor`, `freecad_mesh` all **PASS / EXIT 0**.
- **easyMesh-Änderungen dokumentiert** (docs): new `docs/EASYMESH_CHANGES_DE.md` documents the local patches to `third_party/easyMesh4openEMS/easyMesh/` (vs the pristine upstream copy under `build/lib/easyMesh/`): (1) cylinder/cylindrical-shell **both end discs** contribute x/y coords + edges (`process_primitive`, `geometryUtils.py`) — fixes a Yagi/dipol X-oriented wire mesh being cut in half; (2) **polyhedron type detection** `_is_polyhedron_type()` + `GetCoords`/AABB guards in `process_primitive`, `collect_z_coordinates`, and `meshingUtils.get_mesh_map` — fixes `CSPrimPolyhedron` (no `GetCoords` API) crashing/being skipped; (3) `_prim_aabb_xy_z()` + `_handle_polyhedron()` fallback via `GetBoundBox()`. Includes the sync-pflicht command (`work/patch_site_easy_mesh.py`) and verification steps. Cross-linked from README (easyMesh4openEMS license line).

- **Toolbar buttons removed from the main window** (`main_window.py`): the horizontal button bar at the top of the central widget is gone — all 18 buttons (`btn_new`/`btn_open`/`btn_save`/`btn_import`/`btn_freecad`/`btn_patch`/`btn_yagi`/`btn_pcb`/`btn_mesh`/`btn_run`/`btn_abort`/`btn_script`/`btn_settings`/`btn_sparams`/`btn_sparams_popout`/`btn_log_toggle`/`btn_help`/`btn_exit`) are now **hidden off-screen** `QPushButton`s, kept referenced as `self._hidden_toolbar_buttons` (same GC pitfall as `_mesh_form`/`_sim_form`: a bare local would be GC'd → `RuntimeError: libshiboken … already deleted`). They remain the menu's click/state targets (via `add_action(..., btn=...)`) and the run-time enable/disable targets (`_on_build_mesh` toggles `btn_mesh`/`btn_build_mesh`); the `btn_log_toggle` check state and the `btn_help` label ("Help: <Tab>") keep working because they are still alive and wired to the View/Help menu actions. The left-panel buttons (`btn_edit_tree`/`btn_del_tree`/`btn_add_*`/`btn_edit_nf2ff`/`btn_fit_nf2ff`), the Mesh-tab button (`btn_build_mesh`) and the Ports/Dumps-panel buttons (`btn_edit_port`/`btn_del_port`/`btn_add_dump`/`btn_edit_nf2ff2`) are **unchanged** — only the top toolbar row was removed. Ver pytest **11 passed**; smokes `menu`, `tab_help`, `script_editor`, `tree_edit`, `log_toggle` all **PASS**.

- **FreeCAD-Workflow-Anleitung** (docs + Help-Menü): new `docs/FREECAD_WORKFLOW_DE.md` — full step-by-step guide (units → draw geometry → mark `PORT_<name>_start`/`_stop` points → save **FCStd** → Import Geometry / FreeCAD-Tools → check Ports / Dumps window → Mesh Parameters → Build Mesh (F5) → Simulation Parameters → Run Simulation (Ctrl+R)), with overview table, checklist, troubleshooting table, related-files table. `docs/FREECAD_PORTS_DE.md` updated for the removed Ports / Dumps tab (window wording, `Ctrl+Shift+D`, Mesh Parameters instead of Mesh-Tab). Cross-links added in `ANLEITUNG_DE.md` §1 and `FREECAD_PORTS_DE.md` checklist. Help menu (`main_window.py`) gets **Open FreeCAD Workflow (Geometry → Import)** → `_open_doc("docs/FREECAD_WORKFLOW_DE.md")` (uses the existing `_open_doc` pattern).

- **English documentation** (docs): the four German guides got English counterparts — `docs/ANLEITUNG_EN.md`, `docs/FREECAD_WORKFLOW_EN.md`, `docs/FREECAD_PORTS_EN.md`, `docs/EASYMESH_CHANGES_EN.md` (full translations, all internal cross-links point to the EN files). The German originals now carry a `> English version:` banner at the top; README lists the EN docs under the license section. GUI help texts (`tab_help.py` TAB_HELP entries, tooltips, labels) were already English — no change needed there.

### 2026-10-12
- **Antenna pattern integration (NF2FF far field) — complete:** `app/postproc/nf2ff.py` (degree-fix: the openEMS binding takes theta/phi in **degrees**; harvest, `_compute_directivity_gain`, `export_nf2ff_json`, `plot_nf2ff`, `compute_and_export`), `app/ui/antenna_viewer.py` (Antenna tab: 3D gain polar, E/H cuts, gain/directivity vs f, pop-out), `sim_params.py` NF2FF checkbox + eval freqs, menu `Simulation → Antenna Pattern Viewer` / `Pop-out Antenna Graph`, log-sniff auto-refresh + checkbox coupling (needs **both** `project.sim.nf2ff_enabled` and `project.nf2ff.enabled`), `tab_help.py`. Smokes: `tests/antenna_viewer_smoke.py` PASS, `tests/smoke_patch.py` PASS.
- **`Prad≈0`/negative root cause fixed (long-standing, affected every patch NF2FF run):**
  1. `FDTD.CreateNF2FFBox` derives `directions`/`mirror` from the *domain* boundary types: PEC `zmin` ⇒ z-min face skipped **+ PEC mirror at the box coordinate** (mismatch: box at z=0.5, wall at z=−24.7) ⇒ only **5 of 6** dump faces (`nf2ff_E_5.h5` missing) and an artificial θ↔180°-symmetric pattern (up/down ratio was exactly 1). The installed openEMS build cannot override `directions` (`TypeError: multiple values`), so `script_gen.py` builds the box directly: `nf2ff(CSX, "nf2ff", start, stop, directions=[True]*6, mirror=[0]*6)`.
  2. Patch preset box z-min `0.5` sat inside the substrate — the ground plate lay **outside** the closed integration surface → `primitives.py` now uses `-ground_thickness - 1.0` (encloses all sources; the domain clamp lifts it to the PEC floor when the mesh has no air gap below the ground — a zero-flux closure, both valid).
  3. openEMS `Prad`/`Dmax` attributes are sign-flipped (`Prad −2.11e-24` while the stored `P_rad` density integrates to `+2.31e-24`) → postproc uses the spherical integral of `P_rad` as authoritative, `Dmax = max(4π·P_rad/Prad)` from the grid; raw attrs kept as `Prad_openEMS`/`Dmax_openEMS`.
  4. η used `pin = 1−|S11|²` (dimensionless) against spectral `Prad` (W·s²) ⇒ gain −228 dBi. `_load_pin_spectral` now reads `port_ut_*`/`port_it_*`, applies the openEMS pulse-DFT (`2·Σ·Δt`, `utilities.DFT_time2freq`) and `P_acc = 0.5·Re(U·conj(I))` — same spectral units as `Prad`, so η is a physical ratio. (Physically: `uf_inc 1.75e-11 V·s` and `Prad ~2.3e-24 W·s²` are both normal for a pulse DFT — the small absolute numbers are the drive level, not a bug.)
  - **Verified** (`work/patch_prod`, 150k TS, 678 s, `run_full.log`): `Property #7 "nf2ff_E" Primitive Count: 6`, **6 E + 6 H dump files**, `Prad = +2.31e-24 W·s²`, `Dmax = 8.42 dBi`, `η = 0.78`, **gain = 7.36 dBi**, back-radiation −17 dB (physical patch values). pytest **11 passed**; smokes `antenna_viewer`, `smoke_patch` PASS. Docs updated: ANLEITUNG_DE/EN (menu row, evaluation section), README.

- **Polar-plot display fixes (2026-10-12, `app/ui/antenna_viewer.py` + `plot_nf2ff`):** plane cuts now draw the full 360° plane (phi0 half + phi0+180 mirror half, `_plane_cut`) — before, only the right half (theta 0..180) was shown; combo entry `H-Plane (theta=90)` → `H-Plane (phi=0)` (it duplicated the E-plane data; phi0 is the true H-plane for the patch preset) and redundant `Elevation (phi=0)` removed; dB radial tick labels at 90° (`set_rlabel_position(90)`) with ~5 nice tick steps for any range (`_radial_ticks`); PNG polar panels take over the cartesian grid slot (`_polar_slot`) — removes the stray 0..1 frame ticks behind the plots; `plot_nf2ff` accepts the exported `Dmax_dBi` key; titles show `Gain`/`Directivity` instead of raw keys; viewer info line `Prad … W*s^2`. `tab_help.py` combo list updated. Verified via rendered PNGs (`work/vis`), pytest **11 passed**, smokes `antenna_viewer`, `tab_help`, `verify_imports`, `smoke_patch` PASS.
- **3D pattern scaling + controls (`_plot_3d`):** matplotlib 3.11 removed `cm.get_cmap` → the `Poly3DCollection` branch always raised and silently fell back to `plot_surface` (colors mapped the *radius*, data-driven limits, no colorbar) — fixed with `matplotlib.colormaps`. **Absolute dBi window scaling:** one `dBi min` slider (0.1 dBi steps, bounds from the selected field's data, min 0.5 dBi window) sets the lower bound, max = pattern max; the window maps to both the surface radius (`(G-dmin)/(dmax-dmin)` clipped 0..1) and the colors (`Normalize(..., clip=True)`, colorbar with `extend` arrow below the window), title shows the window, **3D axes carry no tick labels** (colorbar = the dBi scale). `3D field` combo selects **Gain/Dmax** (`THREE_D_FIELDS`, `_selected_3d_field` with fallback when one field is absent). The 3D controls (field combo + slider) live **inside `right_panel`** in the matplotlib toolbar right of the save icon; the `cmap` combo sits in the **viewer's top button row** (next to the 3D checkbox) with a mirrored proxy combo in the pop-out's top row (`AntennaGraphWindow._on_cmap_changed`, viewer→popout sync in `_on_cmap_changed`/`_set_dbi_controls_visible`). All of them only appear while the 3D view is active — the pop-out reparents the panel, so the toolbar controls are available there with zero sync code. Verified: renders `work/vis/3D*.png`, pytest 11 passed, smokes `antenna_viewer` (slider + field-switch steps), `tab_help`, `verify_imports` PASS.
- **Antenna tab file list shows work-dir-relative subpaths:** list items show `p.relative_to(work_dir)` incl. file name (e.g. `myproj/simulation/nf2ff/nf2ff.json`), full path in `Qt.ItemDataRole.UserRole` + tooltip; an earlier `lbl_path` label above the list was removed again (path too long). `_on_file_selected` reads the UserRole data. Help updated.
- **Project/Geometry tree: Duplicate (right-click) + all buttons below the tree removed (`main_window.py`):** the context menu now offers **Edit… / Duplicate / Remove**; `Duplicate` (also `Geometry → Duplicate Selected` = **Ctrl+D**, `_on_duplicate_selected_tree`) deep-copies the selected entry right after itself — `prim`/`assign`/`mat`/`port`/`dump` (`DUP_TREE_KINDS`); prim/mat/port/dump get a unique `… (copy)` / `… (copy n)` name (suffix stripped first, so duplicating a copy chains `copy → copy 2`), the copy becomes the current selection; `geom` and `nf2ff` are singletons → disabled/no-op. The visible button rows below the tree (**Edit Selected…, Delete Selected, Add Box/Cylinder/Assignment/Material/Port…, Edit NF2FF Box…, Fit NF2FF to Geometry**) are gone — all reachable via the Geometry menu (btn→`slot=` conversion, `Del`/`Ctrl+D` shortcuts kept) or right-click; untouched: the ports-form buttons (`btn_add_dump`, `btn_edit_nf2ff2`), the Simulation-Parameters NF2FF buttons, hidden toolbar buttons. Tests: `tree_edit_smoke.py` (9 button attributes gone + duplicate chain/names/singleton no-op), `menu_smoke.py` (Ctrl+D) — pytest **11 passed**, smokes `tree_edit`, `menu`, `tab_help`, `generic_workflow`, `log_toggle`, `mainwindow_sparam`, `sim_params`, `verify_imports` PASS. Docs: ANLEITUNG_DE/EN menu row, tree tooltip (`Edit/Duplicate/Remove`).
- **3D control row spacing + wider field combo (`antenna_viewer.py`):** the toolbar 3D controls (`3D field` / combo / `dBi min` / slider / value) were one cramped row — they now live in a single container widget (`self.dbi_controls`, `QHBoxLayout` spacing 10, margins 6) added to the matplotlib toolbar, hidden/shown as one unit (`_set_dbi_controls_visible` toggles the container *and* the children so `isHidden()`-based smoke checks keep working); `cmb_3d_field` gets `AdjustToContents` + `setMinimumWidth(80)` so **Dmax** fits. Verified: `work/vis/tab_3d_layout.png`, smokes `antenna_viewer` + pytest 11 PASS.
- **S-Params "Loaded files" shows work-dir subpaths like the Antenna tab (`sparam_viewer.py`):** `_on_scan` items show `f.relative_to(work_dir)` (fallback `f.name`) with the full path in `Qt.ItemDataRole.UserRole` + tooltip; `_on_file_selected` reads the UserRole (text fallback) instead of the item text. `tab_help` S-Params bullet added. Verified: temp fake-workdir script (text=relative, UserRole=absolute, click loads absolute), smokes `sparam_viewer`, `mainwindow_sparam`, `sparam_postsim`, `tab_help`, pytest **11 passed**.
- **S-Params marker style is now selectable (`sparam_viewer.py`):** the marker table used to show **all 16 format columns at once** — new `Marker style` combo above the table (`MARKER_STYLES`: All formats / dB / |S| / Phase / S complex / Γ polar / Γ rect / Z / Y / VSWR / RL) hides every column outside the selection via `_apply_marker_style` (`f [GHz]` always visible, column names resolved against `marker_cols`); `_marker_list_text` formats the list rows in the selected style too (`_on_marker_style_changed` = apply + refresh). "All formats" keeps the old behaviour (default). `tab_help` bullet added. Tests: `sparam_viewer_smoke.py` asserts visible column sets for dB/Z/All formats + style-following list row (exit 5–8) — smoke PASS, pytest **11 passed**.
- **Antenna tab: "Radiated Efficiency vs Frequency" plot (`antenna_viewer.py`):** new `PLOT_TYPES` entry + `_redraw` branch (before the 3D branch) + `_plot_eff_vs_freq` — η(f) in **%** from the exported `efficiency` array (η = Prad/P_acc, fraction→% with a percent-input tolerance), fallback `eta = gain_max − Dmax` ratio when the key is missing/empty (label says which source); y-limit 0..105 % when data fits, title/legend/ylabel as in the other vs-f plots. 3D controls stay hidden (`_set_dbi_controls_visible` unchanged). `tab_help` plot list + Values bullet updated. Test: `antenna_viewer_smoke.py` now asserts 85 % for the fixture (direct key **and** derived fallback, ylabel has %, exit 13); the PLOT_TYPES loop covers the new entry — smoke PASS, pytest **11 passed**.
- **Antenna "Details" block reworked (`antenna_viewer.py`):** the header label is now a real widget `lbl_detail_title` showing **`Details (<file name>)`** (updated on load, reset on load error) and the body is `Dmax / Gmax / eta / Prad / theta / phi` — one value per line in that order (`_update_details`, all per-frequency values follow the **Freq combo** via `_freq_index`, Dmax falls back to `peak(D_dBi)`, Gmax = `peak(gain_dBi)` or `Dmax + 10·log10(eta)`, missing → `n/a`); the old body (theta/phi + the `lbl_info` line with `file:`) is gone — `lbl_info` top bar unchanged (still mirrored in the pop-out). Freq combo change refreshes details (`currentIndexChanged` → `_redraw` + `_update_details`). Tests: `antenna_viewer_smoke.py` asserts the header and the six-line order (exit 14) — smoke PASS, pytest **11 passed**; `tab_help` bullet added.
- **S-Params: marker table + detail removed, right-click marker delete (`sparam_viewer.py`):** the redundant 16-column **Marker table** and the **Selected marker detail** block are gone (with them `_marker_row`, `_marker_detail_text`, `_apply_marker_style`, `_update_marker_detail_label`, `_on_marker_list_changed`, `marker_cols`, the `QTableWidget` imports and the `currentRowChanged`/port-change/popp-out hooks that only fed the detail label); the `Marker style` combo stays and now only formats the marker **list** (`MARKER_STYLES` simplified to a plain ordered name tuple; `_on_marker_style_changed` = refresh). `_refresh_marker_table` renamed to **`_refresh_markers`** (list row *i* == `markers[i]`). New: **right-click a marker row → "Delete marker"** (`_on_marker_context_menu` with `CustomContextMenu` + `_delete_marker_at(row)` — removes it from `markers`, refreshes the list and redraws, so the plot vline / Smith dot disappears). Left panel now: Loaded files → Markers list → Marker style. `tab_help` bullets updated. Tests: `sparam_viewer_smoke.py` asserts style-following list text for dB/Z/All formats (exits 5–7), the context-menu policy (8) and delete correctness incl. list sync (9–10); `mainwindow_sparam_smoke.py` switched to `marker_list.count()`; both prints sanitized to ASCII (cp1252 `Ω` crash). Result: smokes `sparam_viewer`, `mainwindow_sparam`, `sparam_postsim`, `tab_help`, `antenna_viewer`, `smoke_patch`, `tree_edit`, `menu`, `generic_workflow`, `log_toggle`, `sim_params`, `verify_imports` all PASS, pytest **11 passed**; screenshot `work/vis/sparam_leftpanel.png`.
- **Antenna tab: click markers on the 2D plots (`antenna_viewer.py`):** left-click on a **2D plot** drops a marker that **snaps to the nearest curve data point** (nearest line by pixel distance via `transData`; only the axes registered by `_register_marker_ax` accept clicks — polar cuts `kind="polar"` stores **degrees**, Gain-vs-f / Efficiency-vs-f `kind="xy"` store x in GHz + y in dBi/%; the 3D view and the gain-plot twinx channel are not marker-enabled). Marker dict `{plot, kind, x, y, label, xunit, yunit}` — label (legend name, e.g. "Gain max") is kept only when the axes has >1 curve; identical points are deduped. `_draw_markers` (end of `_redraw`) draws crimson white-ringed dots for the markers of the current plot (skipped on 3D axes), `_refresh_markers` fills a new left-panel list **"Markers (click plot to add)"** (`<plot>: <x><unit>  <y><unit>  (label)`), rows are **right-click → Delete marker** (`_on_marker_context_menu`/`_delete_marker_at`, row *i* == `markers[i]`); new **Clear markers** button in the top bar **and** in the pop-out window; `load_path` resets markers; markers persist across plot switches (rows show their plot name, only matching ones are drawn). Tests: `antenna_viewer_smoke.py` fires synthetic `SimpleNamespace` clicks on a polar cut and the Gain-max curve (exits 15 add/draw/dedupe/policy, 16 delete, 17 xy+Clear-button) — smoke PASS, pytest **11 passed**; `tab_help` bullet added.
- **Mesh menu simplified (`main_window.py`):** the first Mesh-menu entry **"Build Mesh" (F5)** is removed — it was a duplicate of the second entry (both called `_on_build_mesh`); its hidden transporteur `btn_mesh` is gone too (creation, `_hidden_toolbar_buttons`, connect, the two `setEnabled` calls in `_on_build_mesh`, comment). **"Build CSX + Mesh + Validate" now carries the shortcut `Ctrl+M`** (no conflict: `Ctrl+Shift+M` stays Mesh Parameters). Docs: `tab_help` Mesh Parameters note, `ANLEITUNG_DE/EN` §3 menu line, `FREECAD_WORKFLOW_DE/EN` + `FREECAD_PORTS_DE/EN` F5 references → **Strg+M/Ctrl+M** with the full entry name. Tests: `menu_smoke.py` asserts `"Build Mesh" not in menu` + `Ctrl+M` on the combined entry; full sweep **PASS** (pytest **11 passed**, smokes `menu`, `tab_help`, `verify_imports`, `mainwindow_sparam`, `log_toggle`, `generic_workflow`, `tree_edit`, `sim_params`, `antenna_viewer`, `sparam_viewer`).
- **Crash fix: double-click reset view in the Mesh Viewer (`mesh_viewer.py`):** `bind_wheel_zoom._on_dblclick` called `canvas.figure.autoscale_view()` → `AttributeError: 'Figure' object has no attribute 'autoscale_view'` (it is an **Axes** method; mpl callbacks propagate the exception). Fixed: the per-axes loop now does `relim() → autoscale() → autoscale_view()` and the bogus figure-level call is gone (both bound canvases `canvas_slice`/`canvas_cells` are plain 2-D axes, so per-axes is correct). Regression: `mesh_viewer_smoke.py` dispatches a synthetic `MouseEvent(..., dblclick=True)` through `cv.callbacks.process("button_press_event", …)` on both canvases — smoke PASS, pytest **11 passed**.
- **Sim Params split into two tabs + NF2FF frequency modes (`sim_params.py`, `project.py`, `nf2ff.py`, `script_gen.py`):** the Simulation Parameters window (Ctrl+Shift+P) now has a `QTabWidget`: **Simulation-Parameter** (computed, excitation/convergence — end criteria + NrTS only, boundary, mesh overrides, patch, effective settings) and **NF2FF-Parameter** (enable checkbox, θ/φ steps — moved out of the Excitation group — fit/edit box, status label). New `NF2FFDef` fields `freq_mode` ("auto"|"count"|"list", default "auto" = old behavior; old project JSONs load with defaults) + `freq_count` (default 11). Frequency mode combo: **Auto (S11 minimum)** / **N points (mesh range)** — N points equally spaced over `mesh.start_frequency..stop_frequency` via `np.linspace` → Gain vs Frequency curve — / **Frequency list** with per-entry units `Hz/kHz/MHz/GHz` (bare = Hz). New Qt-free helpers in `app/postproc/nf2ff.py`: `parse_freq_list` (segment tokenizer: commas/semicolons/spaces separate; rejects junk between tokens, unknown units, non-positive) + `resolve_eval_frequencies(project, s_table)` (all three modes, nan-safe S11-min, n=1 → target frequency); the generated `project_run.py` template now calls `nf.resolve_eval_frequencies(project, s_table)` instead of the inline auto-only block (Run regenerates the script). UI: `cmb_nf2ff_mode`/`spn_nf2ff_count`/`txt_nf2ff_freqs` (enable states follow the mode), sync writes `fmt_hz` units back into the list field ("2.45 GHz"), effective-settings + NF2FF status labels show the eval description ("11 x 2.0 GHz…2.8 GHz" / "auto (S11 minimum)" / explicit list). tab_help "Simulation Parameters" + Antenna "Enabling NF2FF" updated; ANLEITUNG_DE/EN §5.0 (tab table + mode table) and §5.4 (step 4) updated. Tests: new `tests/test_nf2ff_freqs.py` (7 pure tests: unit parsing, all modes, JSON round-trip incl. missing-key defaults), `sim_params_smoke.py` asserts tab labels, list-mode unit round-trip (`2.4 GHz, 2.45 GHz, 2500 MHz` → Hz), count-mode linspace over the smoke's 2.15–2.75 GHz mesh, enable-state toggling and the "eval:" readout. Ver pytest **18 passed**; smokes `sim_params`, `menu`, `tab_help`, `generic_workflow`, `mainwindow_sparam` PASS.
- **Apply in the parameter windows now refreshes an existing run script (`main_window.py`):** Apply in Simulation Parameters / Mesh Parameters used to only write the project — `work/<project>/project_run.py` (and its `project_copy.json`) stayed stale until the next Run, so the Script editor showed outdated values. New `_refresh_run_script_if_generated()` (called at the end of `_apply_sim_params_dialog` + `_apply_mesh_params_dialog`): when `project_run.py` already exists it calls `_generate_script(work)` (hand-edits backed up via `_note_script_backup` as always) and logs `Run script updated: …`; a **visible Script editor reloads via `show_path()`** (keeps unsaved editor edits — `is_modified()` guard). When no script exists yet, nothing is created (first generation stays with Run Simulation / Script editor). Errors are logged, never break Apply. Hint text in the dialog + tab_help (Simulation Parameters Buttons/Run script, Mesh Parameters Buttons) + ANLEITUNG_DE/EN §5.0 Apply rows updated. Test: `sim_params_smoke.py` points `settings.work_dir` at a temp dir (restored before `close()` — closeEvent persists settings), generates a script, Applies `max_timesteps=123456` via the real dialog button and asserts `project_copy.json` carries the new value + `.lastgen` exists; a second Apply with a fresh project name must NOT create a script. Smokes `sim_params`, `menu`, `tab_help`, `mainwindow_sparam`, `script_editor` PASS, pytest **18 passed**.
- **Standalone Windows EXE via PyInstaller (`build_exe.py`, new `main.py`, new `app/paths.py`):** `python build_exe.py` → **`dist\FreeCAD_OpenEMS_GUI.exe`** (onefile, windowed, ~146 MB nach Größenreduktion; PySide6+matplotlib+scipy+h5py+pandas/scikit-rf+CSXCAD/openEMS binaries). New root `main.py` = thin shim over the existing `app/main.py` (keeps the easyMesh-from-third_party sys.path preference) + **`main_crash.log`** next to the EXE for windowed startup failures. New `app/paths.py`: `app_root()` = `sys._MEIPASS` when frozen (repo root otherwise) + `settings_path()` = EXE dir `app_settings.json` when frozen (settings persist next to the EXE, verified); consumed by `settings.py` (`APP_ROOT`/`DEFAULT_SETTINGS`), `simulation/runner.py` (default root for PYTHONPATH+cwd of the sim subprocess), `script_gen.generate_run_script` (APP_ROOT baked into `project_run.py`), `main_window.py` (`APP_ROOT` const), `freecad_launcher.py` (examples/ export script). The onefile bundle ships **source copies** of `app/`, `third_party/easyMesh4openEMS/`, `examples/`, `docs/` via `--add-data` so the external simulation Python (settings `simulation_python`, unchanged) and FreeCADCmd can import/run them while the GUI process is alive (`_MEIPASS` temp dir; Run always regenerates the script with a fresh APP_ROOT). Build flags: `--collect-binaries/submodules=CSXCAD,openEMS`, **`--exclude=PyQt5/PyQt6/PySide2`** (WinPython ships PyQt5 next to PySide6 → PyInstaller aborts otherwise). Größenreduktion 692→146 MB: zusätzlich ~60 `--exclude`-Module (torch/cv2/vtk/osgeo/pyarrow/bokeh/panel/llvmlite/numba/cvxopt/netCDF4/pyproj/zarr/xarray/IPython/sphinx/zmq/setuptools/tkinter/win32com/PySide6.QtQuick+QtQml u.a.) — das WinPython-Environment zieht per Transitiv-Import den kompletten Data-Science-Stack (~2 GB roh) ins Bundle; verifiziert per offscreen Launch-Test + pytest 18/18. Ver: build OK; offscreen launch test — process stays alive 150 s, no crash log, `dist\app_settings.json` created. NOTE: rebuilding takes ~14 min (`--clean`); delete a running EXE lock before rebuild (handled in build_exe.py).

### 2026-10-17
- **Both-sided grading at ε interfaces:** `material_jump_sides` in `app/meshing/interface_refine.py` emitted only the outward face of each dielectric, so a ramp could only ever be inserted into the air side; a fine air cell next to a coarse dielectric interior produced nothing into the dielectric. Now the inward face is emitted as well (`out.append((pos, direction))` + `(pos, -direction)`), deduped for adjacent media (shared plane emitted from both boxes) and still skipping faces that overlap a metal primitive. Because `interface_refine_lines(..., continuity=True)` anchors each direction to the cell on the opposite side, the two directions now grade the dielectric **and** the air symmetrically (controlled grid `[0,2,3.9,4,6,8,10]`, `fine=0.25`, `growth=2` → new lines `[4.133, 4.4, 4.933]` inside the dielectric, cross-ratio 1.33 ≤ 2; uniform coarse grid stays untouched). Added deterministic CSXCAD-free `tests/test_interface_refine.py` (9 cases: both-sided emission, adjacent dedup, metal/sheet skip, inward/outward ramps, uniform no-op, cross-ratio/grading bounds) and updated the `material_jump_sides` expectations in `tests/graded_interface_smoke.py`. Verified: pytest **50 passed** + `graded_interface_smoke` PASS (metal + mesh checks unchanged).
- **Energy/end-criteria progress bar from openEMS stdout (open item §7):** new Qt-free `app/simulation/progress.py` (`EngineProgress`) parses the engine's report lines — the `Max. number of timesteps: N ( --> …)` budget, the per-interval `[@ <elapsed>] Timestep: <n> || Speed: … || Energy: ~<val> (<db>dB)` lines and the `… end-criteria of -XXdB was reached …` / `… before the end-criteria …` messages — into `timestep`/`max_timesteps`/`energy_db`/`elapsed`/`end_criteria_hit`/`stopped_before_criteria` plus a `percent` and a status `text()`. `MainWindow` gets a status-bar `QProgressBar` + `QLabel` (`self.progress`/`self.lbl_engine`, `addPermanentWidget`, hidden until a run reports); `_feed_engine_progress` (called from `_append_log_on_gui`, with a cheap `"timestep"`/`"criteria"` pre-filter) feeds each stdout line and `_show_engine_progress` updates the widgets; `_reset_engine_progress` clears them at the start of every run (`_on_run` and each `_start_next_multi_run` step). The multi-run `[<port>]` prefix parses fine (regexes use `search`). Verified: pytest **41 passed** (new `tests/test_progress.py`, 8 cases incl. the real patch-antenna line samples) + `tests/progress_smoke.py` (feed via `_log`→`_drain_log_queue`, bar shows 87 % / `TS 173900/200000` / `Energy -25.9 dB`, reset hides it); full smoke sweep `progress`, `mesh_thread`, `multirun`, `menu`, `tab_help`, `generic_workflow`, `log_toggle`, `mainwindow_sparam`, `project_results_flow`, `tree_edit`, `mesh_viewer`, `verify_imports` all EXIT=0.

- **Mesh build in a worker thread (open item §7):** the long `MainWindow._build_and_validate` body was extracted into the **Qt-free** core `app/meshing/build_task.build_mesh_and_validate(project, primitives, work_dir, log=None) -> MeshBuildResult` (CSX build + easyMesh + `validate_mesh` + `csx.xml` export; the GUI-only bits — patch-preset fallback, matplotlib PNG preview, Mesh Viewer CSX swap, retired-object bookkeeping, `_record_results` — stay out of it). New `app/ui/mesh_worker.MeshBuildThread(QThread)` runs the core off the GUI thread with `log`/`done`/`failed` signals; `_on_build_mesh` now just guards re-entrancy (`_mesh_building` + `_mesh_thread.isRunning()`), keeps the patch fallback (`_ensure_patch_primitives`), starts the thread and returns immediately. GUI-thread slots `_on_mesh_built`/`_on_mesh_build_failed` call the shared `_finish_mesh_build(result)` (preview + report + viewer + record + re-enable button). `_build_and_validate()` remains as the **synchronous** path for `_ensure_mesh_viewer_content` (Mesh-Viewer auto-build) and for the `project_results_flow_smoke` stubs, delegating to the same core + `_finish_mesh_build`. `closeEvent` waits up to 5 s for a running build before teardown. Verified: pytest **33 passed**; new `tests/mesh_thread_smoke.py` (real easyMesh build through the thread: report has "Mesh setup", `csx.xml` written, viewer CSX set, button re-enabled) + smokes `project_results_flow`, `generic_workflow`, `mesh_params_flow`, `mesh_viewer`, `menu`, `tab_help`, `tree_edit`, `multirun`, `mainwindow_sparam`, `log_toggle` all EXIT=0.

- **GUI multi-run driver (open item §7):** new *Simulation → Run All Ports (full S-matrix)* (`Ctrl+Shift+R`) runs one FDTD simulation per port — each copy of the project excites exactly one port — then merges the partial `sparams.json` columns (`app.postproc.sparams.merge_sparam_tables`) and exports the complete matrix to `<project>/sparams/sparams.{json,sNp}` (+ plot), loading it in the S-param viewer and recording it in the project. Qt-free orchestration in `app/simulation/multirun.py`: `plan_port_runs(project, primitives, base_work)` deep-copies the project per port, sets the excite flags, writes scripts under `<project>/multi/port<n>/`; `merge_port_runs(base_work, plans)` loads each run's `sparams/sparams.json` (legacy `simulation/` fallback) and merges/exports; `summarise_table` prints `min |Sij|` lines. The GUI chains the runs with the existing `SimulationRunner` + a 1 s `QTimer` (`_on_run_all_ports`/`_start_next_multi_run`/`_poll_multi_run`/`_finish_multi_run`), prefixes each run's log with `[<port>]`, guards the Run buttons while a multi-run is active (`self._multi_plans`), and falls back to a normal single run for <2 ports. Verified: pytest **33 passed** (new `tests/test_multirun.py`) + `tests/multirun_smoke.py` (fake runner → merged `.s2p`), smokes `menu`, `tab_help`, `mainwindow_sparam`, `generic_workflow`, `project_results_flow`, `verify_imports` PASS. `tab_help` "Simulation Parameters" documents the new entry.

- **MSL port fix (open item §7):** the generated `project_run.py` built MSL ports with the wrong `AddMSLPort` signature — `port.resistance` landed in `metal_prop`, `port.direction` in `prop_dir`, and the excite flag in `exc_dir`; any MSL port therefore failed at runtime. Corrected to `AddMSLPort(nr, metal_prop, start, stop, prop_dir, exc_dir, excite=…)` with a strip metal created via `AddMetal("msl_<n>_<name>_strip")`. New pure helper `app/simulation/msl.py:resolve_msl_axes(port)` returns `(prop_dir, exc_dir)`: explicit `prop_direction`/`exc_direction` win, otherwise derived from the port-box extents (propagation = longest edge, excitation = shortest; openEMS `Tutorials/MSL_NotchFilter.py` uses `'x','z'`), rejecting zero-extent/collapsing boxes. `PortDef` gained `prop_direction`/`exc_direction` (old JSONs → empty = auto); the Port dialog has two "(auto)/x/y/z" combos (`geometry_editors._axis_combo`, tooltips). MSL ports are now created **after** `generate_mesh` + `dedupe_grid_lines` in the run-script template and in the GUI *Build CSX + Mesh* preview (AddMSLPort samples the propagation grid lines and needs ≥5 of them); lumped/rect_waveguide ports stay pre-mesh (their easyMesh registration is unchanged), and `port_objects` is re-sorted by port number so the S-param column indexing holds. `csxcad_builder/builder._add_port` got the same fix (and now receives the port index). Docs/help `tab_help` Ports row. Verified: pytest **27 passed** (new `tests/test_msl_port.py`, 9 tests), smokes `verify_imports`, `generic_workflow`, `mesh_params_flow`, `project_results_flow`, `mainwindow_sparam`, `menu`, `tree_edit`, `port_defaults`, `dipole_ports` all PASS.

- **Antenna viewer fixes (uncommitted, `app/ui/antenna_viewer.py`):** (1) *Gain / Directivity vs frequency* plot — Prad (`W*s^2`, ~1e-27) and unitless η were sharing one right axis hard-fixed to `0..1.05`, so Prad was flattened onto 0 under a mislabeled axis "Prad [W]" and the legend (built before the twin axis filled) dropped the η/Prad entries; Prad now gets its **own** right axis (`tab:brown`, `spine ("outward", 40)`, label `Prad [W*s^2]`), η keeps the `0..1` axis (`eta = Prad / Pin`, green dashed), and the legend is built after all axes list **all four** series. (2) right-click analysis menu was gated by `_polar_active` so it never appeared on the 3D / vs-frequency plots; `_on_click` now opens it on any plot and new `_set_analysis_mode(polar)` (called from `_redraw`) greys out the polar-only controls (r min/max/step, label offset, HPBW, VRV) on non-polar plots, title "Antenna analysis" vs "Polar analysis". (3) the pop-out window's top row mirrored only Plot+cmap, so the frequency could not be changed there; it now carries a *Freq* combo that mirrors the tab both ways (`sync_freq`, re-synced on file reload; `_on_freq_changed` sets the tab index + redraws). Docs `tab_help` + `CHANGELOG [Unreleased]` updated. Verified: pytest **50 passed** (smoke fixture now 2 frequencies), `tests/antenna_viewer_smoke.py` regression checks (gain/eta/Prad axes+legend, right-click opens on gain/3D/polar with polar controls gated, pop-out freq select) + smokes `antenna_viewer`, `antenna_mode`, `tab_help`, `menu`, `generic_workflow`, `mainwindow_sparam` EXIT=0.

- **NF2FF fit: cylinder radius bug:** `_on_fit_nf2ff` built the cartesian AABB from each primitive's `start`/`stop` and only for kinds `box`/`stl`/`cylinder`. For a cylinder `start`/`stop` are the **axis** endpoints, so the radius was dropped on the two perpendicular axes (the dipole fit to x/y=0), and `sphere`/`polygon` were skipped entirely → NF2FF surface hugged the wire (~0.15 λ, reactive near field). New pure `app/meshing/bounds.cartesian_primitive_bbox` (cylinder axis ± radius on perpendicular axes; sphere centre/radius fallback; polygon bbox; min/max order-agnostic), mirroring `polar_primitive_bbox`; used in `_on_fit_nf2ff`. Tests `tests/test_primitive_bounds.py` (11 cases). Verified pytest **61 passed** + `generic_workflow`, `mainwindow_sparam`, `tree_edit`, `verify_imports` EXIT=0. The overly tight `0.7·λ(f_stop)/4` margin is logged as a separate §7 open item.

- **Antenna NF2FF box distance is now configurable (closes the §7 margin item):** `app/models/project.py` `SimSpec.antenna_nf2ff_lambda: float = 0.5` ("NF2FF box", λ at f_start); new spin `spn_ant_nf2ff` in the Simulation-Parameters → "Antenna simulation" group (0.25–2.0, step 0.25, suffix ` × λ`, tooltip, enabled only with the mode; written/synced like `antenna_pml_lambda`; the antenna-mode checkbox tooltip documents it). The margin math was extracted from `_on_fit_nf2ff` into pure `app/meshing/bounds.nf2ff_fit_margin_units` + `domain_air_margin_units` (exported in `app/meshing/__init__.py`): antenna mode → `antenna_nf2ff_lambda·λ(f_start)` (λ at the LOWEST band frequency) clamped to `0.8·` the domain air margin so the box stays inside the PML; PCB/generic   runs keep the small `0.7·λ(f_stop)/4` box. Dipole (f 1.95–2.95 GHz, PML 0.5 λ): box 17.8 mm → **61.5 mm** (≈ λ/2 at f_start) at the default 0.5; 0.25 → 38.4 mm. `tab_help` §4/NF2FF + tooltip updated; CHANGELOG Added. Tests: new `tests/test_nf2ff_fit_margin.py` (4 cases) + `sim_params_smoke.py` round-trip assertion. Verified pytest **65 passed** + smokes `sim_params`, `antenna_mode`, `generic_workflow`, `mainwindow_sparam`, `tree_edit`, `menu`, `tab_help`, `sphere_polygon`, `verify_imports` EXIT=0.

- **Run abort fixed: `cell_too_large` from PML cells exactly over λ/10:** the dipole run (`work/Dipol_CST_openems_Test_cyl_z_V3`) aborted with exit code 2 — the auto mesh had **10.324 mm** cells vs the hard limit λ(f_max=2.95 GHz)/10 = **10.162 mm** (1.6 % over). Cause: easyMesh sizes the air cells from `target_frequency` (2.45 GHz → `max_cellsize_air` = λ/15 = 8.157 mm at preset *medium*) and then expands the PML region (PML_8) by ~1.27× (`work/.../csx.xml`: `77.84 → 88.16 → …`), so the absorbing-region cells overshoot. The run script's `validate_mesh` (`script_gen.py:681`) then returns 2. Fix: new pure `app/meshing/easy_wrapper.cap_max_cell_size(CSX, mesh, targets)` runs `GetGrid().SmoothMeshLines(axis, targets.max_cell_units, max_grading_ratio)` for x/y/z **after** easyMesh + the metal-interface refinement (cartesian only; polar returns earlier). Effect: max cell 9.177 mm, `cell_too_large` errors gone (the `grading_jump 3.5>2.0` warnings remain, non-fatal). Exported in `app/meshing/__init__.py`; new `tests/test_cap_max_cell.py` (splits oversized, leaves small cells untouched). Verified `build_mesh_and_validate` on the dipole (no errors) + pytest **67 passed** + smokes `graded_interface`, `mesh_thread`, `mesh_viewer`, `cylindrical_mesh`, `generic_workflow`, `sphere_polygon`, `polyhedron_mesh`, `smoke_patch` EXIT=0. Note: presets worse than *high* on a wide band with target_frequency are the typical trigger; the cap makes any preset pass.

- **Dipole-vs-CST: NF2FF pattern "egg" fix + S11 reference impedance fix:** two independent bugs surfaced while matching a 2×26 mm dipole (2 mm gap, r 1 mm) against CST (26 mm arms, Discrete Port 73 Ω). (1) **`_on_fit_nf2ff` box was asymmetric:** it used the full margin on ±x/±y but `0.3×`/`0.5×` in z, so the NF2FF surface sat only ~0.15 λ from the wire on the near z face → the far field came out **tilted** (Dmax 3.46 dBi @ θ=108°, D(0°)=−71 vs D(180°)=−83 dB, 12 dB mismatch) = the observed "egg" instead of a donut. New pure `app.meshing.bounds.nf2ff_fit_box(lo, hi, margin)` puts an **equal** margin on all six faces (exported; used in `_on_fit_nf2ff`); re-running with a symmetric box + air margin 0.7 λ gave Dmax **2.07 dBi @ θ=90°**, D(0°)≈D(180°)=−123 dB (0.5 dB), φ-spread 0.03 dB, efficiency 0.993 (was 0.884) → proper symmetric donut. (2) **S11 was always 50 Ω-referenced:** `compute_sparams_from_ports` called `CalcPort(ref_impedance=50)` unconditionally, so the port's `resistance` (e.g. 73 Ω, passed to `AddLumpedPort`) never reached the S11 normalisation; now the default is `ref_impedance=None` (openEMS uses each port's own R) and `export_sparams`/`write_touchstone` record the port R in the `.s1p` header. Dipole at 73 Ω → |S11|min −23.3 dB @ 2.375 GHz (was −19.8 @ 2.335 at 50 Ω). (3) **Air margin / PML matters:** raising `antenna_pml_lambda` 0.5→0.7 (76.9→107.6 mm) moved the dipole X=0 resonance +150 MHz (2.205→2.355 GHz) — the earlier runs had the PML too close, pulling the resonance down. Tests: `tests/test_nf2ff_fit_margin.py` (+2), new `tests/test_sparam_ref_impedance.py` (3); pytest **72 passed**. Runs: `work/Dipol_CST_openems_Test_cyl_z_sym`, `..._conv`. `tab_help` Ports + NF2FF bullets updated; CHANGELOG [Unreleased] Fixed ×2.

- **Antenna mode now overrides the mesh resolution (f_max reference) + CST geometry confirmed:** the "Antenna simulation mode" checkbox previously only set `mesh.preset`; easyMesh derives its air cell size from `target_frequency` (`easyMesh/meshingUtils.py`), so a 1.95–2.95 GHz band with design f 2.45 GHz was meshed at 2.45 GHz and under-resolved the top of the band. New `Project.effective_mesh()` returns a meshing copy (self.mesh untouched) that, while `sim.antenna_mode` is on, points `target_frequency` at **f_max** and caps `max_cellsize`/`refined_cellsize` at `wavelength(f_max)/density` (finer per-feature refinement never coarsened); density from the antenna group dropdown (new `extreme` = 40 cells/λ; `SimSpec.antenna_cells_per_wavelength`), falling back to `CELLS_PER_WAVELENGTH_BY_PRESET`. Used by both `build_task.build_mesh_and_validate` and the generated `project_run.py` (excitation f0 still the design frequency). `app/meshing/rules.py` preset factors aligned to easyMesh (medium 15, high 20). Measured on `Dipol_CST_openems_Test_cyl_z_V3`: max z-cell 4.19 → 3.52 mm (very_high), ~2.5 mm (extreme). Tests in `tests/antenna_mode_smoke.py`; CHANGELOG [Unreleased] Added.
  **CST cross-check** (`CST/Test_dipole_openems_cst_cyl_2mm_dp.*`): the CST STL is **identical** to our model — arms z = ±1…±27 (26 mm), Ø 2 mm (the header `dipole_length=56` is nominal). At the `.s1p` reference (R 50, not the 73 Ω port) CST dips at **2.358 GHz / −18.7 dB**; our 50 Ω run dips at **2.320 GHz / −20.1 dB** — mean Δ −0.03 dB, RMS 1.31 dB over 1.9–2.9 GHz. The old −33.5 dB @ 2.287 GHz was a different (73 Ω) reference. Residual ~1.5 % frequency offset = mesh-convergence.

- **NF2FF "egg" root cause = stale box `center` (not the box shape):** matching the dipole against the CST report (`CST/Report 1.html.pdf`; Dmax 2.21 dBi @ 90°, HPBW 78.3°, η≈1.0) exposed that our far field came out *distorted* (Dmax 3.3–3.6 dBi, HPBW 45°, a notch at θ=45°). Isolated it by (a) re-running the **current** postproc on the old good `_sym` recorded fields (reproduced Dmax 2.13 dBi → postproc fine), (b) comparing meshes (identical) and (c) the run metadata: the project stored `nf2ff.center = [0,0,6.15]` — the midpoint of the **old asymmetric "Fit" box** (`−45.4…+57.7`). `CalcNF2FF` expands the far field around `center`, so an off-centre reference distorts the pattern: on the **same** recorded fields, `center z 0 → 6.15` alone moved Dmax 2.13 → 3.30 dBi and HPBW 78° → 45°. Fix/safeguard: pure `app.meshing.bounds.nf2ff_box_center` / `nf2ff_center_offset` (box-geometry only — the source may be arbitrarily asymmetric) + a run-script check that warns and snaps `center` to the box centre. With the correct centre the dipole matches CST (2.13 dBi @ 90°, 78.0°, η 1.00). Tests `tests/test_nf2ff_fit_margin.py` (+3). `_on_fit_nf2ff` already sets `center` = the (now symmetric) box centre, so a fresh *Fit NF2FF to Geometry* repairs an old project.

### 2026-10-30
- **WR62 waveguide example + GUI preset** (`examples/run_wr62_waveguide_example.py`, `app/csxcad_builder/primitives.wr62_waveguide_primitives`): straight guide, port planes 100 mm apart, a=15.7988 mm / b=7.8994 mm, TE10 at 10 GHz (fc 9.488 GHz, λg 94.9 mm), four 2 mm PEC walls, PML x ends/PEC y/z, 9.5–11.5 GHz, **Et/Ht/Jt** dumps at 10 GHz. Preset menu entry `Presets → WR62 Waveguide (10 GHz)…`. Two excitation runs merge to `work/wr62_waveguide/sparams/sparams.s2p`; verified S21/S12 ≈ 0 dB, S11/S22 ≈ −34 dB @ 10 GHz.
- **Field Viewer colour-scale settings page** (`app/colormaps.py` new, `app/settings.py`, `app/ui/vtk_viewer.py`, `app/ui/main_window.py`): new **Settings → Viewer settings…** dialog (keeps the main Settings dialog short) with all Field Viewer defaults — auto-play, standard view and vector-arrow default (moved out of the main Settings dialog) plus colour map (jet/turbo/viridis/plasma/inferno/magma/coolwarm/rainbow/grey), reverse, colour steps (2…256) and a live swatch preview (banded when steps are low). The old toolbar **Title left** checkbox moved there too and became a **Colour bar title** group: left/above, X/Y position (normalized viewport), text size and rotation, stored in `AppSettings.field_viewer_title` (`DEFAULT_BAR_TITLE`); old `field_viewer_vectors["title_left"]` values are migrated at startup. A **Colour bar** group configures the bar's X/Y position, width/height, font family (Arial/Times/Courier) and label size in `AppSettings.field_viewer_color_bar` (`DEFAULT_COLOR_BAR`); `_make_scalar_bar` reads both option dicts. A **Preview** button in the dialog applies all controls to tab + pop-out without persisting (autoplay signal blocked); Cancel/close restores the snapshot, Save writes the settings. `AppSettings.field_viewer_color_scale` (`{cmap, reverse, steps}`) is applied at startup, saved on change and on Save, mirrored to the pop-out, and drives `VtkFieldViewer._lookup_table` via pure helpers (`colormap_colors`/`fill_lookup_table`, matplotlib with a blue→red fallback). Default stays jet = the previous fixed table. Docs: `ANLEITUNG_DE/EN` §7.2, README, CHANGELOG. Tests: new `tests/test_color_scale.py` (5) + `test_settings.py` roundtrip; `pytest` colour/settings **11 passed**; offscreen Settings + Viewer-settings dialog smoke OK. `vtk_viewer_smoke` PASS (its stale vector-option equality assertions now check the `DEFAULT_VECTORS` keys).

- **Field Viewer layout cleaned up** (`app/ui/vtk_viewer.py`): six flat control rows replaced by a top action bar (`Open`, `Export ▾`, `Video`, `Pop out`, `Reset view`), a collapsible left display panel (**Data**, **Slice / Iso**, **Colour**, **Vectors**) and the playback bar at the bottom. `Export ▾` opens a menu with *Per-step series* + *Step stride* (hidden state widgets kept for `export_and_load`). Slice/iso controls are hidden unless `Show` is Slice/Iso-surface (auto-expand on first switch), the colour-range readout moved into the *Colour* group, and section headers/separators structure the panel. Panel width 280–320 px; colour min/max are stacked, geometry opacity is a spin box (not a slider), vector spin boxes lost their redundant `size`/`arrows` prefixes and `% min` suffix (row labels already name them). Playback bar: Play first, capped time slider, fixed-width frame/time labels and a separate shrinkable arrow-motion label (tooltip has the full text), so the bar no longer jumps. The motion label uses `Preferred` (not `Ignored`) with max 360 px, otherwise it collapsed to zero width. New `AppSettings.field_viewer_layout` ("panel" = B, "minimal" = C): C hides the docked panel and puts a `Display…` button in the top bar that reparents the same panel into a QDialog (docked back on close); selectable in Viewer settings, Preview-aware, mirrored to the pop-out. Playback defaults (fps/Loop/Auto-play) moved from the bar into a **Playback** group in Viewer settings (`field_viewer_fps`/`field_viewer_loop`/`field_viewer_autoplay`). Dialog geometry (Settings, Viewer settings, Display popup) is stored in `dialog_geometry` / `field_viewer_display_geometry` and restored. Main-window state also persists splitter sizes (`main_splitter_sizes`, `log_splitter_sizes`) and the last active tab (`current_tab`); hidden panes keep their last sizes. Viewer settings dialog is tabbed (Viewer / Colour / Colour bar) and remembers its active tab (`viewer_settings_tab`). `DEFAULT_VECTORS` now includes `min_pct=5`, `random=True`, `geom_opacity=12`; Outline defaults off; the transparency spin stretches like the other fields. The loaded field moved from the top action bar into the page header, e.g. **Field Viewer (J_tee [A/m²])** (`tab_help.inject_help_button` exposes `_help_header_label`, viewer `_set_header_detail`); tooltip keeps file/series details. Display popup default 380×760 with a 340×620 minimum; smaller saved geometries are ignored. The Field Viewer context menu now also has a **Views** submenu with the standard presets (`build_views_menu`). Axes menu Size/Label font now use `triggered` + kept-alive action groups; font selection turns Labels on, and the corner marker scales its viewport with the Size preset. New `origin_back` axes option puts the in-scene triad on the far bbox corner (away from the camera) and switches the coordinate ruler to `SetFlyModeToFurthestTriad`, so both sit behind the field. Geometry ghosts now draw box/cylinder/sphere/polygon and stl/obj/npy triangle meshes (previously boxes only, so imported CAD looked invisible). Without an open project the viewer falls back to `<work>/geometry_primitives.json` / `project*.json`; `set_geometry` fits the camera once so ghosts outside the dump volume are visible. Streamlines seed n³ jittered points across the whole volume (default 6³, seeds spin 2³–10³) and are coloured by the selected scalar/LUT; they use long propagation (8× diagonal, 3000 steps, RK2, `LENGTH_UNIT` — the old cell-length unit produced tiny stick lines) for closed loops and rebuild on every time/frequency step. The Streamlines row is shared by Slice and Iso-surface. Tests: `vtk_viewer_smoke` end-to-end PASS, `menu_smoke` PASS, pytest 11 passed; offscreen layout/representation check plus screenshot reviewed.

---

## 9. Update checklist (when you touch this file)

- [ ] Mark completed items in §4
- [ ] Add crash/fix rows to the stability table
- [ ] Append a dated bullet to §8
- [ ] Adjust §7 open items
- [ ] If env/paths change, update §2
- [ ] Keep §3 call-order intact — this is the highest-risk area
