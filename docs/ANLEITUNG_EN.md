# Guide: Draw an antenna in FreeCAD and simulate it with the GUI

**Project:** FreeCAD OpenEMS GUI  
**Goal:** model a 3D antenna in FreeCAD, import it as STEP, simulate with openEMS and evaluate the S-parameters  
**GUI language:** English (guide: English)

---

## 1. Overview

| Step | Tool | Result |
|------|------|--------|
| Draw antenna | FreeCAD | FCStd / STEP |
| Export | FreeCAD → Export STEP | `.step` |
| Import | GUI **Import Geometry…** or **FreeCAD…** | Triangle meshes + stl primitives |
| Terminations | **Add Port…**, **Add Assignment…** | 50 Ω port, material |
| Simulation | **Build Mesh** → **Run Simulation** | S-params, fields |
| Evaluation | **S-Params Viewer**; **Antenna** tab | S11/S21, Smith, markers; pattern (gain/directivity) |

**Important:** openEMS does not read BREP-STEP directly. The GUI tessellates the CAD into triangle meshes automatically (`CSX.AddMesh`).

**Compact step-by-step guide (FreeCAD → Geometry → Ports → Import → Simulation):**
**[docs/FREECAD_WORKFLOW_EN.md](FREECAD_WORKFLOW_EN.md)** — with checklist and error table.
Details on port marking only: **[docs/FREECAD_PORTS_EN.md](FREECAD_PORTS_EN.md)**.

---

## 2. FreeCAD: draw the antenna

Recommendation: **one solid = one object**, with a clear name (it becomes the name in the project later).

### 2.1 Units

- FreeCAD: **View → Units** → **mm**
- Draw objects in mm (GUI default `drawing_unit=mm`)

### 2.2 Concrete example: patch antenna 2.45 GHz (FR4, εr≈4.4, h=1.6 mm)

Orientation: **Z = height**, patch in the XY plane.

| Part | Dimensions (mm) | FreeCAD tip |
|------|-----------------|-------------|
| Substrate | 60 × 60 × 1.6 | Box, position z = 0…1.6 |
| Ground (GND) | 60 × 60 × 1 | Box, z = −1…0 |
| Patch | ~36 × 28 × 1 | Box on substrate, z = 1.6…2.6 |
| Feed point | 5–7 mm from the patch edge | later port definition |

**Names in the model** (object tree): `gnd`, `substrate`, `patch`.

**Port (lumped):**  
Lumped port on an edge of the patch: start/stop on the patch, 50 Ω, direction `x` or `y` (set later in the GUI).

**Mark ports already in FreeCAD (recommended):**  
Create points `PORT_in1_start` / `PORT_in1_stop` → the import adds the port automatically.  
Step-by-step: **[docs/FREECAD_PORTS_EN.md](FREECAD_PORTS_EN.md)**

### 2.3 Wire / Yagi structure (alternative)

- **Preset: Yagi Antenna…** in the GUI (reference, 2.45 GHz) or FreeCAD cylinders / Part tools
- Yagi coordinate system: **X = elements, Y = boom, Z = up**
- Driven dipole with a gap → the port sits there

### 2.4 FreeCAD export

1. **File → Export…**
2. Type: **STEP** (`*.step` / `*.stp`) — recommended
3. Save to e.g.
   `C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\freecad_export\antenna.step`
4. Optionally also save **FCStd** or export **STL**

---

## 3. Start the GUI

```text
C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI
python app\main.py
```

**Menu bar:** `File` (New/Open/Save, Import Geometry, Open Work Folder, Exit),
`Project` (Presets), `Geometry` (Box/Cylinder/Port/Dump, Ports / Dumps… = **Ctrl+Shift+D**, Edit/Duplicate/Delete Selected = **Ctrl+D**/**Del**, Fit NF2FF — the same via right-click on a tree entry),
`Mesh` (`Build CSX + Mesh + Validate` = **Ctrl+M**), `Simulation` (`Run Simulation` = **Ctrl+R**, Script,
S-Params Viewer, Antenna Pattern Viewer / Pop-out Antenna Graph),
`View` (Tabs, Log Panel = **Ctrl+L**), `Tools` (Settings…),
`Help` (Help = **F1**, ANLEITUNG, README, About).

The **log panel** (below the tabs, toggled with **Ctrl+L**) has two
tabs: **Log** (openEMS/app log) and **Mesh Report** (the mesh-check report,
single source after a build).

**Settings…** (once):

| Field | Value |
|-------|-------|
| FreeCADCmd | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe` |
| FreeCAD GUI | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCAD.exe` |
| Simulation Python | `c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe` |
| openEMS root | `c:\openEMS` |
| Work dir | `C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work` |

---

## 4. Bring the model into the GUI

### Variant A — import (standard)

1. **Import Geometry…**
2. Choose the file: `.step` / `.stp` / `.fcstd` / `.stl` / `.obj` / `openems_export.json`
   (a folder of per-object STLs, e.g. `stl/`, also works)
3. FreeCADCmd tessellates STEP/FCStd → `.npy` meshes; STL/manifests are loaded
   directly — **one primitive per FreeCAD object**, object names are preserved
4. Objects appear as **stl primitives** (default material: copper / PEC)
5. Check the log: mesh paths, any WARNINGS

### Variant B — FreeCAD tools in the GUI

1. **FreeCAD…**
2. **Launch FreeCAD** (opens FreeCAD, cwd = work dir) — draw/edit, export STEP
3. **Export CAD…**
   - Source: `.fcstd` (preferred — object names are preserved) or `.step`
   - **Import as:** `STEP` (the app tessellates it later) or
     `STL (one mesh per FreeCAD object)` (uses `openems_export.json`,
     no FreeCAD needed at simulation time)
   - Deflection: 0.1 mm (default)
   - Check **Import meshes into project after export**
4. OK → export → auto-import into the project

Headless (without GUI):

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe examples\run_stl_import_example.py
```

### Variant C — preset (only to test the simulation)

1. **Preset: Patch Antenna…**, **Preset: Yagi Antenna…** or **Preset: PCB Filter…**
2. **PCB Filter** = 2-port microstrip notch filter on FR4 (stopband at 2.45 GHz):
   two lumped ports, so the example measures both S-matrix columns.
   The geometry comes from **FreeCAD** (FCStd → STL, 35 µm copper); `--analytic`
   uses the analytic box primitives for comparison instead

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe examples\run_pcb_filter_example.py
```

Result (`work\pcb_filter\simulation\`): FreeCAD run **−29.99 dB at 2.450 GHz**
target, insertion −0.72 dB at 0.3 GHz; analytic run −28.96 dB at 2.429 GHz
(0.9 % difference = real 35 µm copper layer vs. zero-thickness trace in the tutorial).

Notes:

- **Axis-aligned** FreeCAD solids are created as openEMS **boxes** (exact
  metal edges on the grid lines), everything else stays an STL
  `PolyhedronReader`. In-memory `Polyhedron` primitives are **not** simulated
  by openEMS (warning *Unused primitive*).
- `[WARNING] grading_jump: Axis z 9.14` is normal: the 35 µm copper forces
  0.035 mm cells next to the 0.32 mm substrate cells — just a warning,
  exit code 0.
- `Max. number of timesteps was reached` is expected here; the stopband is
  stable (0.019 dB change from 50 000 to 150 000 steps, `work\pcb_filter_conv`).

---

## 5. Configure the project

### 5.0 Simulation parameters (own window)

**Menu → Simulation → Simulation Parameters…** (or **Ctrl+Shift+P**) opens a
separate, movable window with **all** simulation parameters — two tabs:

| Tab | Contents |
|-----|----------|
| **Simulation-Parameter** | Computed (read-only), excitation & convergence (end criteria, NrTS), boundary conditions ×6 + PML, air margin, mesh overrides, patch geometry |
| **NF2FF-Parameter** | Enable far-field (NF2FF), **evaluation frequencies** (mode: *Auto (S11 minimum)* / *N points (mesh range)* / *Frequency list*), θ/φ steps, Fit/Edit NF2FF box |

**Evaluation frequencies** (NF2FF tab) select at how many frequencies the
far field is recomputed:

| Mode | Effect |
|------|--------|
| **Auto (S11 minimum)** | one frequency at the S11 dip (or the target frequency) |
| **N points (mesh range)** | N points equally spaced over the mesh frequency range (start/stop from the Mesh tab) — e.g. 11 points from 2.0…2.8 GHz → *Gain vs Frequency* curve in the Antenna tab |
| **Frequency list** | explicit list, each entry with a unit **Hz / kHz / MHz / GHz** (bare number = Hz), e.g. `2.4 GHz, 2.45 GHz, 2500 MHz` |

| Button | Effect |
|--------|--------|
| **Apply** | Writes the values into the project, mirrors them into the hidden Sim Params panel (save/build use it) and refreshes `work/<project>/project_run.py` when it already exists (a visible Script editor reloads the file unless it holds unsaved edits) |
| **Close** | Discards unapplied changes |

The window is **modeless** — a second menu click brings the same window
to the front and syncs it with the current project. Changes take effect only
after **Apply**; then **Build Mesh** so the mesh picks them up.

**Menu → Mesh → Mesh Parameters…** (or **Ctrl+Shift+M**) opens a
separate, movable window with **all** mesh parameters (preset, drawing unit,
f0/fc, start/stop, air margin). **Apply** writes them into the project and
mirrors them into the hidden mesh form, **Close** discards unapplied changes.
This window is also **modeless**.

### 5.1 Mesh / frequencies

Mesh parameters (see §5.0):

| Parameter | Patch 2.45 GHz | Yagi 2.45 GHz |
|-----------|----------------|---------------|
| start_frequency | 2.2e9 | 2.2e9 |
| stop_frequency | 2.7e9 | 2.7e9 |
| target_frequency | 2.45e9 | 2.45e9 |
| drawing_unit | mm | mm |
| max_timesteps | 150000+ | 25000 (preset) |

### 5.2 Material / assignment

1. **Add Material…** — e.g. `substrate` (εr=4.4, tanδ=0.02), `copper`
2. **Add Assignment…**
   - Object: `substrate` (or the substrate name from the import)
   - Role: `dielectric` / `pec` / `conductor` …
   - Choose the material
3. The assignment syncs stl primitives (pec → copper, dielectric → substrate …)

### 5.3 Port

1. **Add Port…**
2. Type: **lumped** (patch) — resistance **50**
3. start / stop: edge of the patch (mm), e.g.
   `start=[-1.0, 0.0, -1.0]`, `stop=[1.0, 0.0, 1.0]`
4. Direction: `x` or `y` (parallel to the voltage drop across the port)
5. `excite_port = true` (port 1)
6. Check the geometry: viewer / mesh preview

**Alternative (FreeCAD):** mark ports as `PORT_*` points in the FCStd — they then appear automatically on import.  
→ **[docs/FREECAD_PORTS_EN.md](FREECAD_PORTS_EN.md)**

### 5.4 NF2FF (radiation pattern)

1. **Edit NF2FF Box…** or **Fit NF2FF to Geometry**
2. Box around the antenna, e.g. start `[-40,-40,-20]`, stop `[40,80,20]`, center `[0,20,0]`
3. `enabled = true`
4. Pick the **evaluation frequencies** (Sim Params → NF2FF tab, see §5.0):
   *Auto* (one frequency at the S11 minimum), *N points* (Gain vs Frequency
   curve) or an explicit list with units (e.g. `2.4 GHz, 2.45 GHz`).
5. The **mesh viewer** shows the new box immediately (violet dash-dot line) —
   provided the mesh has already been built; the black dashed line is the
   simulation domain and only changes on the next **Build Mesh**.

### 5.5 Save the project

**Save…** → e.g. `work\my_antenna\antenna.fcsim.json`

### 5.6 Domain size (black frame in the mesh viewer)

The domain cannot be edited directly — it results from **geometry + air margin +
grid rounding**. Levers:

| Lever | Effect |
|-------|--------|
| **Air margin auto** (Sim Params → Boundary conditions) | λ/4 at the **stop frequency**, always εr=1 (at 2.7 GHz = 27.76 mm per face). Changing the stop frequency therefore also changes the margin |
| **Air margin (all faces)** | Auto off → fixed value in mm for all 6 faces. In the project JSON settable as a 6-element list `boundary.air_margin` = `[xmin,xmax,ymin,ymax,zmin,zmax]` per face (the GUI writes the same value for all) |
| **Geometry** | Domain = geometry bbox + margin (easyMesh `boundary_distance`) |
| **PML size** | per PML face easyMesh adds `PML_size + 1` cells **outside** → larger domain. Only effective for boundary = PML; no effect for MUR |
| **Mesh preset / min cell size** | Air cells grow to λ/10…λ/40 per preset (`max_cellsize_air`) → the outer grid line is rounded to that: deviation of a few mm, slightly different per side (that is the small asymmetry of the black line) |

**Procedure:** change values → **Build Mesh** (black line updates) →
**Fit NF2FF to Geometry** so the violet box stays inside. The result
appears in the viewer info panel as *Domain size*.

**Limits:** too small → warning `outside the field domain`, NF2FF/dumps are
clamped or disabled; too large → significantly more cells (RAM/runtime).
Geometry that sticks out past the black line is reported by the validator as
`primitive_outside_domain` (**error**, blocks the run; dumps/probes only as a
warning) — the mesh viewer outlines such primitives in red and writes
*OUTSIDE DOMAIN* in the overview.

---

## 6. Simulate

1. **Build Mesh**
   - Check: mesh cells, ports, preview (`mesh_preview.png`)
   - Viewer shows the 3D geometry
2. **Run Simulation**
   - Log panel: openEMS running, max_timesteps, end_criteria
   - A warning for `max_timesteps < 150000` is possible
3. **Script…** (Simulation menu) — view and edit `project_run.py`
   - The script is what openEMS actually runs (paths, mesh, ports, export)
   - **Save** / **Revert** / **Regenerate from project**
   - **Run Simulation** regenerates the script; your own changes are backed up to
     `project_run_edited.py` first — the GUI asks whether to regenerate or run
     your edited script as-is
   - Manual: `<python> work\<project_name>\project_run.py`
4. Result files under `work\<project_name>\` (among others `project_run.py`, meshes, S-params)

---

## 7. Evaluation

1. **S-Params Viewer**
   - S11 / S21, dB, phase
   - Marker: frequency, S, Γ, Z/Y, VSWR, RL
   - Smith chart: Z and Y markers
2. **Pop-out S-Graph** — plot in a separate window
3. **Antenna** tab / **Simulation → Antenna Pattern Viewer** — NF2FF far field:
   3D polar gain plot, 2D polar cuts (E/H plane), directivity/gain/η vs
   frequency; **Pop-out Antenna Graph** = separate window; field dumps remain
   under `work/<project>/fields/` (S-parameters in
   `work/<project>/sparams/`, NF2FF in `work/<project>/nf2ff/`)
4. **2-port (S2P)** — one FDTD run measures **one** column of the S-matrix (for 2 ports:
   S11 + S21). Further columns need further runs with the other excited port
   (`excite_port` switched); the JSON results are merged afterwards —
   example: `examples\run_pcb_filter_example.py`.
   Unmeasured entries stay empty (NaN) in `sparams.json`; the Touchstone file
   (`.s2p`) is only written once the matrix is complete.

---

## 8. Workflow checklist (2.45 GHz antenna)

- [ ] FreeCAD: `gnd`, `substrate`, `patch` in mm
- [ ] Ports in FreeCAD: `PORT_in1_start` / `PORT_in1_stop` (optional, recommended)
- [ ] Export: `.step` (or `.fcstd` with ports)
- [ ] GUI: **New Project**
- [ ] Mesh: 2.2–2.7 GHz, target 2.45e9, unit mm
- [ ] **Import Geometry…** → STEP / FCStd
- [ ] **Add Assignment…** substrate (dielectric) + copper
- [ ] **Add Port…** lumped 50 Ω at the feed (or check the FreeCAD ports)
- [ ] **Fit NF2FF to Geometry** + enabled
- [ ] **Save…**
- [ ] **Build Mesh** → **Run Simulation**
- [ ] **S-Params Viewer** → S11 minimum near 2.45 GHz

---

## 9. Common problems

| Problem | Cause / fix |
|---------|-------------|
| Import does nothing for STEP | Check the FreeCADCmd path in Settings |
| Port missing or wrong | start/stop not on the edge of the patch |
| S11 bad | feed position, patch dimensions, mesh too coarse, max_timesteps too low |
| Material wrong | assignment missing; default is copper/PEC |
| FreeCAD does not start | Settings **FreeCAD GUI** (`FreeCAD.exe` next to `FreeCADCmd`) |
| Export without STL | export in FreeCAD / FreeCAD… → Export CAD… |
| Simulation aborts / empty S-params | check mesh/port; log path `work\<project>\` |
| `AssertionError` in `openEMS.Run` | `project_run.py` has relative paths (`WORK_DIR=…`) → regenerate the script (paths are saved absolute) |
| Warning `outside the field domain` | dump/NF2FF box too small → **Fit NF2FF to Geometry**; the script warns and clamps the box into the FDTD domain |
| Domain (black frame) too large/small | **Air margin** in Sim Params → Boundary conditions (auto = λ/4 at the stop frequency, otherwise an mm value for all faces) → then **Build Mesh** + **Fit NF2FF** (see 5.6) |
| S11 flat at ≈ 0 dB, Prad ≈ 0 | port short-circuited — for the Yagi that is the boom: the preset sets `gfrp` (dielectric, assignment `boom`), not `copper` |
| Own script “gone” after the run | `project_run.py` is regenerated on every run — your version is in `project_run_edited.py`; the GUI asks first (regenerate or run the script as-is) |
| 2-port missing S12/S22 | one run = one column → second run with the other `excite_port`, then merge (`examples\run_pcb_filter_example.py`) |

---

## 10. Tips

- **Preset first:** test the Patch/Yagi preset, then import your own FreeCAD geometry.
- **Clear object names** in FreeCAD (`patch`, `gnd`, `via`) — they appear as primitive names.
- **STEP import:** tessellation deflection ~0.1 mm; choose smaller for fine pins.
- **FreeCAD export helper (CLI):**  
  `examples\freecad_export_for_openems.py` (FreeCADCmd with `FREECAD_SOURCE` / `FREECAD_OUT_DIR`)
- Re-run **Build Mesh** after a material change.
- **Make script changes via "Script…"** — then the next run automatically backs up
  your version to `project_run_edited.py` and asks whether to regenerate or
  run it as-is.
- **2-port example (filter):** `Preset: PCB Filter…` in the GUI or
  `examples\run_pcb_filter_example.py` — both S-matrix columns, ready `sparams.s2p`.

---

## 11. Paths & tools (reference)

| Purpose | Path |
|---------|------|
| Start GUI | `app\main.py` |
| FreeCADCmd | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe` |
| FreeCAD GUI | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCAD.exe` |
| openEMS | `c:\openEMS` |
| Work directory | `work\` |
| Export script | `examples\freecad_export_for_openems.py` |
| Port marker demo | `examples\freecad_add_port_marks.py` |
| Dipole + ports | `examples\freecad_dipole_ports.py`, `examples\run_dipole_ports_example.py` |
| FreeCAD port guide | `docs\FREECAD_PORTS_EN.md` |
| Yagi example | `examples\freecad_yagi_model.py`, `work\yagi_antenna\` |
| Smoke tests | `tests\freecad_gui_smoke.py`, `tests\freecad_mesh_smoke.py`, `tests\port_marks_smoke.py`, `tests\dipole_ports_smoke.py` |
