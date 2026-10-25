# Mesh/simulation diagnostics, Field Viewer vectors, coordinate probe — change log

> Deutsche Fassung: **[DIAGNOSTICS_CHANGES_DE.md](DIAGNOSTICS_CHANGES_DE.md)**

**Scope:** seven completed work packages (six diagnostics/viewer packages plus P1
from the quantization plan). Every file compiles, the test suite is green
(**256 pytest checks**, plus the standalone smokes), and every statement was
measured on real projects from `work/`. There is **no change to the solver
behaviour, to presets or to existing projects** — with two exceptions: the
vector display of the Field Viewer has a new default (see change 3) and
coordinate alignment is on by default (change 5, switchable).

**Why:** the developer should see, *without reading the solver log*, whether a
run can work at all (mesh/timestep/frequency resolution) and how trustworthy the
result is (convergence, passivity, verdict) — and field vectors should be
visible and correctly animated.

---

## Change 1 — Mesh timing / feasibility right after meshing

**Files:** new `app/meshing/timing.py`; wired into `app/meshing/build_task.py`;
shown via Mesh menu → *Mesh Timing / Feasibility…* (`app/ui/main_window.py`) and
the *Mesh Report* tab.

**What:** after *Build Mesh* the built grid is evaluated against the simulation
settings — pure analysis, no solver run:

| Quantity | Content |
|---|---|
| Cell sizes | min / typical / max per axis, plus where the smallest cell sits |
| Courant step | `dt = Δunit / (c₀·√(Σ 1/dᵢ²))` from the smallest cell per axis (= the formula behind `openEMS.automesh.mesh_estimate_cfl_timestep`) |
| Dielectric | lower bound `dt/√εr,max` — inside a dielectric the solver uses less |
| Resolution | cells per wavelength at `f_max` per axis (target ≥ 10) |
| Timestep check | `1/(2·dt)` against `f_max` |
| Domain crossing | minimum timesteps for a wave to cross the domain |
| DFT resolution | `Δf = 1/(NrTS·dt)` against the sweep step (span/200) |
| Runtime penalty | smallest cell against the typical cell (factor) |

**Example `pcb_hairpin`** (5-pole filter, 1.608–3.192 GHz, NrTS 120 000):

```
Mesh timing: dt(CFL, vacuum)=0.3247 ps (>= 0.3247 ps in eps_r=1); 16.1 cells/lambda @ f_max;
min cell 0.1304 mm (5.6x vs. typical); DFT dF=25.7..25.7 MHz for NrTS=120000; 2 warning(s), 0 error(s)
```

* cells `53 × 102 × 13 = 70 278`; smallest cell y = 0.1304 mm at y = 14.13 mm
* cells per wavelength: x 21.2 · y 23.8 · z 16.1 (f_max = 3.192 GHz) → fine
* **runtime penalty 5.6×** (x axis) → warning `tiny_cell_slows_run`
* DFT resolution 25.7 MHz against a 7.92 MHz sweep step → warning
  `resolution_limited` with a concrete remedy ("raise max_timesteps to ≥ 388 897")
* the solver later reported `dt = 0.2294 ps` = **71 %** of the vacuum estimate —
  exactly the expected dielectric reduction (see change 2)

**Issue codes:** `tiny_cell_slows_run`, `cells_per_lambda_low`,
`timestep_too_coarse`, `timesteps_too_few`, `resolution_limited`, `empty_mesh`.

**Limits:** this is an **estimate** from the mesh. The exact `dt` only exists
after the FDTD operator is built — which is why change 2 compares both numbers.
The DFT resolution assumes the *full* `NrTS`; a run that stops early on the end
criteria shortens the window and gets coarser (the status report then shows the
*actual* value).

---

## Change 2 — Simulation status after the run (log + menu)

**Files:** new `app/simulation/report.py` (Qt-free) and
`app/ui/report_dialog.py` (window with Copy/Save); wired into
`app/ui/main_window.py` (Simulation menu → *Simulation Status…*, automatic log
append after every run).

**What:** the report reads the solver log and the S-parameters and answers "did
the run finish and is the result trustworthy?".

* **Engine facts** (from the log): `dt`, timestep method, Nyquist rate
  (TS + frequency), excitation length (TS + s), `NrTS` + ratio to the excitation
  length, cells, operator/field size, steps, energy, speed, elapsed.
* **Errors/warnings**: engine warnings, tracebacks/crashes **and the mesh
  validation from *before* the run** (codes `mesh_*`, marked "pre-run mesh").
  Known-benign lines are filtered — e.g. the `Capacity: -nan(ind)` output of the
  lumped-port preparation must **not** appear as an error (covered by
  `tests/test_sim_report.py`).
* **Convergence**: end criteria reached vs. "Max. number of timesteps was
  reached before the end-criteria" (that sentence is deliberately *not* read as
  convergence).
* **Passivity versus frequency**: per *excited port* `Σᵢ |Sᵢⱼ|² ≤ 1` (driven at
  port 1 that is `|S11|²+|S21|²`). The sweep is split into a **core band**
  (edges trimmed by 5 %) and the **edge**; a violation at the edge only is a
  **DFT window artefact** and reported as info.
* **Design quality**: S11 minimum (depth + frequency), the −10 dB band, whether
  the design frequency lies inside it, S21 at the design frequency.
* **Actual DFT window**: `steps × dt` → `Δf`, compared with the sweep step.
* **Verdict + trust level**: `OK`/reliable · `WARNING`/use with care ·
  `ERROR`/unusable.

**Example `pcb_hairpin`** (GUI run of 23 Oct, reproduced from its log):

```
Verdict   : WARNING - passivity violated in band; not converged (max timesteps); 4 warning(s)
Trust     : use with care
Run       : exit 0 | not converged | 119340/120000 TS | energy -16.3 dB | 57 s
Engine    : dt 0.2294 ps (Rennings_2) | Nyquist 682 TS @ 3.196 GHz
          : cells 54x103x14 = 77868 | operator 3.6 MiB | field 1.8 MiB
          : excitation 15771 TS (3.617 ns) | max TS = 7.61 x excitation
Results   : 201 points | 1.608 GHz .. 3.192 GHz
          : S11 min -5.43 dB @ 2.281 GHz | no -10 dB match in the sweep
          : S21 at the design frequency -41.44 dB
          : passivity max |S|^2 = 1.084 @ 2.25 GHz | in core band 1.084
          : VIOLATIONS inside the core band: 40 point(s) > 1
          : DFT window 119340 TS x 0.2294 ps = 27.37 ns -> dF = 36.5 MHz
            (sweep step 7.92 MHz -> window-limited)
Findings  :
  [INFO]     timestep_check: engine dt 0.2294 ps vs. vacuum Courant estimate 0.3247 ps (71%)
  [WARNING]  not_converged: max timesteps reached before the end criteria
  [WARNING]  passivity_core: |S|^2 sums to 1.084 > 1 at 2.25 GHz (40 point(s))
  [WARNING]  mesh_grading_jump: [pre-run mesh] Axis x: worst neighbor grading 5.58 > 2.0
```

**Limits:** the passivity check needs the **JSON** form of the S-parameters
(the app's loader returns complex arrays, converted to dB internally). If only a
Touchstone exists, the report says `results_not_json` (info) instead of silently
claiming "no results".

---

## Change 3 — Field Viewer: vector glyphs fixed

**File:** `app/ui/vtk_viewer.py` (+ tooltips/in-app help `app/ui/tab_help.py`,
manual `docs/ANLEITUNG_DE.md`/`_EN.md`).

Two symptoms were reported; three defects were measured:

**3.1 Overlay (behaviour changed).** The arrows were drawn *on top of* the
coloured slice/surface/volume plot; an opaque cut or body covers every arrow
pointing into it. Now: `_show_field_actor()` — with vectors on, the field
representation is **not** drawn (the arrows *are* the representation, as in
ParaView). The new checkbox **"field underneath"** (`DEFAULT_VECTORS["overlay"]`,
default **off**) brings the overlay back. The colour bar stays because it
explains the arrow colours.

**3.2 Orientation was lost.** `vtkGlyph3D` orients along the *active* vector
array of its input. When that flag is missing (filter chain, older export) it
only scales the arrows — exactly the reported "only the length changes". Now:
`sampled.GetPointData().SetActiveVectors(name)` on the glyph input.

**3.3 Per-frame length reference → "breathing".** The 99 % reference was
recomputed **per frame**, so arrow lengths were not comparable over time. Now:
`_vector_reference()` / `_measure_series_reference()` — the reference is
measured **once over the whole series** (at most 24 sampled frames, median
instead of maximum so the excitation pulse at the start does not shrink every
arrow to nothing) and cached per series + vector array. Without a series the
current frame is used.

**Evidence (measured on a real time-domain export, 153 steps):**

| Check | Result |
|---|---|
| Direction step 1 → 6, median cos | −0.97 (Surface/Wireframe), −0.99 (Slice), −1.00 (Iso-surface) |
| Share of rotated arrows | 92–100 % |
| Raw `field` array (data itself) | cos −0.98, 100 % rotated |
| Arrow length ∝ amplitude | length ratio 3.316 vs. |E| ratio 3.172 |
| Series reference | 0.01426 (median over 24 of 153 steps, range 0…0.228) |
| with "same length" | constant length 0.003592 in both steps (as intended) |

**New: motion readout.** While stepping through a series **by hand**, the step
label reports how much the arrows turned, e.g.
`arrows: 24% reversed vs. previous step (median cos +1.00)`. Once per series the
viewer logs the length reference:
`VTK viewer: vector length reference |field| = 0.01426 (median over 24 of 153 step(s), …)`.

**Physics note (important for expectations):** animated are **time-domain dumps**
(label `t…` / `step …`) — there the arrows rotate with the field.
**Frequency-domain dumps** have no time axis: the slider shows `f = … GHz`, and a
mode shape that is nearly constant over frequency means a constant arrow
direction — that is correct, not a display problem. Example: the hairpin dump
`work/pcb_hairpin/simulation/E_substrate.h5` holds
`FieldData/FD/f0_real|f0_imag` with **exactly one** step at 2.4 GHz.

**Test:** `tests/vtk_vectors_smoke.py` (standalone — under pytest importing the
VTK tab crashes with an access violation on this platform, as with the other VTK
checks). The script verifies exclusive mode, option round-trip, orientation and
the length reference using a **line probe** instead of arrow geometry (so the
direction is directly readable).

---

## Change 4 — Coordinate quantization: Phase-0 probe (measurement only)

**File:** new `app/meshing/coord_diag.py`; wired into
`app/meshing/build_task.py` (block in the mesh report + one log line) and into
the timing window; tests `tests/test_coord_diag.py` (21 checks).

Background: the plan `VisualOpenEMS_CoordinateQuantization_Plan` proposes a
central coordinate lattice against "micro deviations in the mesh". Phase 0 is
meant to **measure**, not to change.

**What is measured:**

* **Near-duplicate grid lines**, bucketed by distance (`<1e-9 … <1e-3` units),
  so the magnitude becomes visible.
* **Authored coordinate pairs** below the planned tolerance, **tagged with their
  source** (`box`, `cylinder`, `polygon`, `stl`, `port`, `dump`, `nf2ff`,
  `manual`) — the cause is immediately attributable.
* **Distance of every authored coordinate to the next line as a fraction of the
  local cell** ("on_line" / "snapped" ≤ 15 % / "off_grid" > 15 %). That is the
  physically meaningful metric (the one `port_align`/`validators` use): ports
  above 15 % are a warning, NF2FF/dump corners are info.
* **Smallest cell against the requested `min_cellsize`** (`cell_below_min`).

**Corrected auto formula.** The plan states `q ≤ c_min/1000` but implements
`decimals = clamp(ceil(log10(c_min)) + 3, 2, 7)` — which contradicts its own
examples (0.1 mm ⇒ 2 instead of 4, 0.01 mm ⇒ 2 instead of 5). The probe uses:

```
N = clamp(ceil(3 - log10(c_min_units)), 2, 7)
```

| `c_min` | N | `q` | plan example |
|---|---|---|---|
| 10 mm | 2 | 10 µm | – |
| 1 mm | 3 | 1 µm | 3 ✓ |
| 0.5 mm | 4 | 0.1 µm | – |
| 0.1 mm | 4 | 0.1 µm | 4 ✓ |
| 0.05 mm | 5 | 10 nm | – |
| 0.01 mm | 5 | 10 nm | 5 ✓ |
| 0.001 mm | 6 | 1 nm | – |

**Measurement over 15 real projects in `work/`** (all with a project file and a
saved `csx.xml`):

| Quantity | Sum |
|---|---|
| near-duplicate grid lines (< 1 µm) | **0** |
| authored pairs below the planned tolerance | **0** |
| ports further than 15 % of a cell from a line | **0** |
| coordinates "barely off" (< 15 % of a cell) | 0–89 per project (harmless, openEMS snaps) |

So the plan's core premise ("duplicate grid lines → micro cell") is **not
supported** for these projects. The same holds for the neighbouring paths: two
boxes 0.1 µm or 1 nm apart, box versus polyhedron, and a manual line 1e-7 next
to a box edge were all **merged** by easyMesh (smallest cell stayed at
`min_cellsize`, no `very_small_cell` warning). Reason: the app already applies
`f32()` to **all** boxes/ports/dumps, `port_align` puts port coordinates on
lines, and `dedupe_grid_lines` removes duplicates. A *genuine* gap (thin plate)
is kept and correctly raises a `grading_jump` warning.

**What the plan still gets right:** the **one** measurable inconsistency is the
decimal rounding `np.round(..., 4)` (0.1 µm) in the triangle/STL path versus
`f32()` used everywhere else. Example: an STL edge at 20.00006 mm lands at
20.0001 mm after `round(,4)` — **9.918e-5 mm** away from the box edge. That is
**3.3× to 830×** larger than the float32 resolution (ULP: 0.12 nm at 1.6 mm ·
7.6 nm at 100 mm · 30.5 nm at 300 mm) — and the only candidate that justifies a
lattice at all.

**Side finding (runtime):** the smallest cell is in some projects *far* smaller
than requested — `cyl_mesh_smoke` 0.045 mm against 13.3 mm requested (**297×**),
dipole-cyl 0.051 against 0.5 (**10×**). Through `dt` that costs runtime directly
(see change 1) and is probably the more rewarding target than duplicate lines.

---

## Change 5 — P1: one coordinate grid for every source (switchable)

**Files:** `app/csxcad_builder/quantize.py` (`align_coordinates()`,
`f32_array()`, `ulp_tolerance()`), `app/csxcad_builder/triangle_mesh.py`,
`app/freecad_io/stl_import.py`, `app/simulation/grid_dedupe.py` (new),
`app/models/project.py` (`MeshSpec.align_coordinates`),
`app/ui/mesh_params.py` (checkbox), `app/meshing/build_task.py`,
`app/simulation/script_gen.py`.

**Problem (measured):** boxes/ports/dumps/NF2FF go through `f32()` (the float32
grid), triangle meshes through `np.round(..., 4)` (0.1 µm *decimal*). An edge at
20.00006 mm therefore lands 0.1 µm next to the box face — 3× to 830× the float32
resolution, and the only *measurable* source inconsistency (Phase 0).

**Fix:** both paths now use the **same** rule (`align_coordinates()`): triangle
vertices are put on the float32 grid CSXCAD stores everything on, so a vertex and
a box face that are meant to be equal are **bit-identical** — no second grid
point, no sliver cell.

**Switch:** `MeshSpec.align_coordinates` (default **on**), checkbox *Mesh
Parameters → "Align coordinates (float32 grid)"*, stored per project. "Off" =
legacy (0.1 µm decimal rounding). Old project files load with the default (on).
Stored `.npy` meshes keep their historical rounding until re-imported.

**Additionally:** `dedupe_grid_lines` gets a **float32 ULP floor** (`ulp_align`,
8 ULP of the largest coordinate): pairs below the float32 resolution merge even
where the `frac·cell` rule stays below that noise. It never merges less than
before and is a no-op in all 15 measured projects. The function now lives in
`app/simulation/grid_dedupe.py` (instead of the `SCRIPT_TEMPLATE` string) so the
code that actually runs is unit-testable; the generated script imports it like
the other `app.*` modules (it puts `APP_ROOT` on `sys.path` itself).

**Effect (A/B on real projects):** the *mesh* does not change —
`Dipol_CST_mesh_test` (cylinders) and `pcb_bandpass` (boxes) give identical
grids with `align=on/off` (101 and 270 cells, same smallest cell). That is
exactly the Phase-0 prediction: no duplicate lines for easyMesh to merge. The
gain is the *consistency of the stored geometry* (vertex == box face), not a
different grid.

---

## Change 6 — Critical cells marked in the Mesh Viewer (right-click)

**Files:** new `app/meshing/critical_cells.py`; `app/ui/mesh_viewer.py` (marks +
right-click menu); `app/meshing/build_task.py`
(`MeshBuildResult.critical_cells`); `app/ui/main_window.py` (hand-over after the
build, engine cell after a run); tests `tests/test_critical_cells.py`.

**What:** only the cells that **cost runtime** are drawn — the ones that set the
FDTD timestep:

| Kind | Meaning |
|---|---|
| `cell_below_min` | cell below half the requested `min_cellsize` |
| `very_small_cell` | cell below `max_cell/500` (validator threshold) |
| `timestep_cell` | smallest cell of its axis (on a *graded* axis) — it sets the timestep |
| `engine_timestep` | the cell the FDTD operator reports as the smallest timestep (`… found at position: 1 : 39;14;0`) — after a run |

Neighbour grading jumps (`grading_jump`) and too-large cells (`cell_too_large`)
are **not** marked: they are quality findings of the mesh report, not runtime
problem cells — they covered large parts of the domain and hid exactly the cells
that matter. (The validator warnings themselves are unchanged.)

**Drawing:** consecutive problem cells of one axis are merged into **one region**
(a 138-cell arc edge = one band, not 138 boxes). Axis findings span the other two
axes over the whole domain (the grid is too fine at that *position*) and are
drawn as **dashed outlines without fill**, so the grid stays visible. In a slice,
a finding whose axis is the slice normal is not drawn (it would be the whole
frame); the engine cell is the exact 3-D box. In the *Cell sizes* plots each
region shows a light band, the size of its smallest cell and a marker. At most 24
marks, sorted by severity (smallest first).

**How to use:** right-click in the Mesh Viewer → **"Mark critical cells (N)"**
(on/off, default **off**). The marks are a diagnostic overlay: they are always
computed, but drawn only on request — the count in the menu entry tells you
beforehand whether there is anything to see. One log line per build
(`Critical cells: …`) and a block in the mesh report. After a run the status
report additionally marks the engine cell
(`Mesh Viewer: engine_timestep (dt … ps) marked`).

**Verification:** 17 real projects from `work/` — 3–9 marks each, none covering
more than 12 % of an axis (before: up to 22 full planes with fill). A uniformly
fine grid deliberately yields no mark (it has no outlier cell; the report still
names its smallest cell). Test mesh with a 1e-6 mm line next to a box edge → 1
`very_small_cell` + the axis minima; all five views redraw without errors;
toggling removes and restores the marks. Full suite: **256 pytest checks**.

---

## Change 7 — P2: fine cells are attributed to their source (switchable)

**Files:** `app/meshing/critical_cells.py` (`attribute_cells()`,
`source_summary()`, `CriticalCell.source`), `app/meshing/build_task.py`,
`app/models/project.py` (`MeshSpec.diagnose_cell_sources`),
`app/ui/mesh_params.py` (checkbox), `tests/test_critical_cells.py`.

**What:** Phase 0/P1 showed *that* cells are up to 300× too fine. P2 says **who
forces them**: for every flagged cell both faces are compared against all
authored coordinates (box / cylinder / polygon / STL faces, ports, dumps, NF2FF,
manual lines); the nearest source is written onto the mark.

Example (test mesh with a 1e-6 mm line next to a box edge):

```
Critical cells: 3 (timestep_cell 2, very_small_cell 1)
  very_small_cell (x=1e-06 | box:feed + manual)
  timestep_cell (y=0.0625 40 cells, min 0.0625)
  timestep_cell (z=0.05 2 cells, min 0.05)
Fine-cell sources (which geometry forced them):
     2  easyMesh (no authored source)
     1  box:feed + manual
```

The 1e-6 mm cell is clearly attributable to the manual line plus the box edge;
the y/z minima sit on no authored edge ("easyMesh") — they come from the grid
distribution itself.

**Switch:** `MeshSpec.diagnose_cell_sources` (default **on**), checkbox *Mesh
Parameters → "Attribute fine cells to their source"*. Off = marks without a
source, and the report says "Fine-cell attribution off" instead of a misleading
list.

**Limits:** cells *without* an authored source come from easyMesh itself
(circle/arc detection, `interface_refine`, the PML transition) — the report calls
them "easyMesh (no authored source)" and lists the candidates. The attribution is
a heuristic (face distance ≤ 5 % of the cell width), not a solver-side root-cause
analysis.

**Verification:** A/B on the test mesh (on/off) as above; full suite **256
pytest checks**.

---

## Not changed

* Solver behaviour, presets, examples and saved projects.
* The `f32()` quantization of boxes/ports/dumps/NF2FF, `port_align`,
  `interface_refine` and all tolerances in `validators.py`, `manual_mesh.py`,
  `preview.py` — unchanged. (Only the *triangle* path was changed to use the same
  grid, plus the ULP floor in the dedupe — see change 5.)
* `SHEET_HALF_THICKNESS = 1e-6` (1 nm sheet) and `zero_extent_axes()`.
  Note for later: in float32, 1 nm is **not representable** from ≈ 16 mm onwards
  (`f32(100 mm + 1 nm) == f32(100 mm)`) — the sheet only works today because
  `inflate_sheet_box()` uses doubles (`SetStart/SetStop`). Any lattice must keep
  this inflation outside.
* Menu structure (only new entries: Mesh → *Mesh Timing / Feasibility…*,
  Simulation → *Simulation Status…*) and `AppSettings` (the new vector options
  ride in the existing options dict).

---

## Verification

```powershell
$PY = "c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe"
cd c:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI

# 1) full suite (contains the new tests)
& $PY -m pytest tests -q                     # expect: 233 passed

# 2) standalone smokes (VTK/GUI do NOT run under pytest)
& $PY tests\mesh_thread_smoke.py             # MESH THREAD SMOKE OK
& $PY tests\vtk_viewer_smoke.py              # VTK VIEWER SMOKE PASS
& $PY tests\vtk_vectors_smoke.py             # VTK VECTORS SMOKE PASS
& $PY tests\mesh_params_flow.py              # ALL PASS

# 3) in the app
#    Mesh -> Build Mesh                   -> log lines "Mesh timing: …" and "Coord probe: …"
#    Mesh -> Mesh Timing / Feasibility…   -> both blocks in the window
#    Simulation -> Run Simulation         -> block "SIMULATION STATUS" at the end of the log
#    Simulation -> Simulation Status…     -> the same block with Copy/Save
```

Expected log lines (example, numbers depend on the project):

```
Mesh timing: dt(CFL, vacuum)=0.3247 ps (>= 0.3247 ps in eps_r=1); 16.1 cells/lambda @ f_max;
min cell 0.1304 mm (5.6x vs. typical); DFT dF=25.7..25.7 MHz for NrTS=120000; 2 warning(s), 0 error(s)
Coord probe: 0 near-duplicate line pair(s) (<0.001 mm), 0 authored pair(s) below 0.0005 mm,
0 coordinate(s) off the grid (0 of them ports), 14 snapped; smallest cell 0.25 mm (1.0x min_cellsize)
```

---

## Open items / next steps

Phase 0 did **not** confirm the plan's premise for the 15 projects. Recommended
order, by benefit/risk:

1. ~~**P1 (small, risk-free):** replace `np.round(...,4)` in the triangle/STL
   path by the rule used everywhere else (`f32`, optionally `f32(snap(v))`) —
   removes the only measured source inconsistency (0.1 µm) without mesh risk.
   Extend `dedupe_grid_lines` to **`max(τ, frac·cell)`** (never weaker than
   today) plus a regression test.~~ **done** (change 5): the triangle path is
   aligned to the float32 grid (switchable, default on), the dedupe got the ULP
   floor; the `max(τ, frac·cell)` rule was already implemented.
2. **P2 (the real gain):** explain the 10–300× too-fine cells — *which
   primitives* create them (candidates: arc/circle detection, `interface_refine`,
   the PML transition)? Extend the probe with the primitive name, then fix it
   there. That delivers exactly the promised benefit (a stable CFL step), just
   in the right place.
3. **P3 (only if needed):** snap mesh-editor lines to a **visible** grid
   (UI "snap 1 µm"), the model fields + dialog from the plan; only after that a
   lattice as a whole — with the corrected auto formula, `τ = max(q/2, 8·ULP)`
   and the rule "never pull two faces of the same material together".

**Plan document corrections (open):** in
`VisualOpenEMS_CoordinateQuantization_Plan\Coordinate_Quantization_Plan_DE.md` +
`_EN.md` these still need to go in: the wrong auto formula (line 84), the
100 mm float32 value (7.63e-6 instead of 1.2e-5), `max(τ, frac·cell)` instead of
replacing, "auto" as the default, the sheet/material-collapse rule, snapping at
*apply* time (not only when authoring) and acceptance criteria for the E2E
tests. The Phase-0 measurement itself belongs in the plan as its own section.

---

## Files touched

| Area | File | Kind |
|---|---|---|
| Mesh timing | `app/meshing/timing.py` | new |
| Mesh timing | `app/meshing/build_task.py` | extended (timing + coord probe on the result) |
| Run status | `app/simulation/report.py` | new |
| Run status | `app/ui/report_dialog.py` | new |
| GUI | `app/ui/main_window.py` | menus, log appends, windows |
| GUI | `app/ui/tab_help.py` | vector help text |
| Field Viewer | `app/ui/vtk_viewer.py` | exclusive mode, orientation, series reference, readout |
| Coordinate probe | `app/meshing/coord_diag.py` | new |
| P1 coordinates | `app/csxcad_builder/quantize.py` | `align_coordinates()`, `f32_array()`, `ulp_tolerance()` |
| P1 coordinates | `app/csxcad_builder/triangle_mesh.py` | vertices on the float32 grid (`align` flag) |
| P1 coordinates | `app/freecad_io/stl_import.py` | stores full precision (rounding happens at CSX build time) |
| P1 coordinates | `app/simulation/grid_dedupe.py` | new — extracted from the script template, with the ULP floor |
| P1 coordinates | `app/simulation/script_gen.py` | imports `grid_dedupe`, passes `align`/`ulp_align` |
| P1 coordinates | `app/models/project.py`, `app/ui/mesh_params.py` | `MeshSpec.align_coordinates` + checkbox |
| Critical cells | `app/meshing/critical_cells.py` | new (marks for the Mesh Viewer) |
| Critical cells | `app/ui/mesh_viewer.py`, `app/meshing/build_task.py`, `app/ui/main_window.py` | marks, right-click menu, hand-over |
| P2 sources | `app/meshing/critical_cells.py` | `attribute_cells()`, `source_summary()`, `CriticalCell.source` |
| P2 sources | `app/models/project.py`, `app/ui/mesh_params.py` | `MeshSpec.diagnose_cell_sources` + checkbox |
| Tests | `tests/test_critical_cells.py` | extended (attribution, switch, fallback) |
| Tests | `tests/test_critical_cells.py` | new |
| Tests | `tests/test_dedupe_grid_lines.py` | new |
| Tests | `tests/test_quantize.py`, `tests/test_axis_aligned_box.py` | extended (bit identity, legacy offset, ULP) |
| Tests | `tests/test_mesh_timing.py`, `tests/test_sim_report.py`, `tests/test_coord_diag.py` | new |
| Tests | `tests/vtk_vectors_smoke.py` | new |
| Tests | `tests/vtk_viewer_smoke.py` | options dict extended by `overlay` |
| Docs | `docs/ANLEITUNG_DE.md` / `_EN.md` (+ `ANLEITUNG_DE.docx`) | sections 6.1 and 7.5 |
| Docs | `CHANGELOG.md` | entries under *Unreleased* |
| Docs | this document | new |
