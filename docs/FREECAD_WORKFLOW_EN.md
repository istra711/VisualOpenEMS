# Guide: FreeCAD → Geometry → Ports → Import → Simulation

**Goal:** the complete workflow from drawing in FreeCAD to a running openEMS
simulation in the GUI — step by step, with a checklist.

**Prerequisites**

| Item | Value |
|------|-------|
| FreeCAD | 1.1.4 (`c:\FreeCAD_1.1.4-Windows-x86_64\`) |
| FreeCADCmd | `c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe` |
| Python | `c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe` |
| Units | **View → Units → mm** (overlays: mm) |
| Document type | save as **FCStd** (carries ports; STEP/STL lose them) |
| GUI import | **Import Geometry…** or **FreeCAD… → Export CAD…** |

**Why FCStd?** Only FreeCAD-native formats (`.fcstd`) and the
`openems_export.json` manifests of the FreeCAD tools carry the port markings
through export. A plain STEP/STL is pure geometry **without** ports.

---

## Overview (the 7 steps)

| # | Step | Where |
|---|------|-------|
| 1 | Units + orientation | FreeCAD |
| 2 | Draw geometry (one solid = one object) | FreeCAD |
| 3 | Mark ports (`PORT_<name>_start` / `_stop`) | FreeCAD |
| 4 | Save as FCStd | FreeCAD |
| 5 | Import into the GUI | GUI |
| 6 | Build the mesh | GUI (**Ctrl+M**) |
| 7 | Run the simulation + evaluate | GUI (**Ctrl+R**) |

---

## 1. Units and orientation

- **View → Units → mm**.
- Orientation is free, but keep it **consistent**; the GUI expects mm (default `drawing_unit=mm`).
- Common antenna orientation: **Z = height**, patch/dipole in the XY plane.

Recommendation: **one solid = one object**, with a clear name (it becomes the
object name in the project later, e.g. `gnd`, `substrate`, `patch`).

---

## 2. Draw the geometry (FreeCAD)

### 2.1 Patch antenna 2.45 GHz (FR4, εr≈4.4, h=1.6 mm)

| Part | Size (mm) | FreeCAD tip |
|------|-----------|-------------|
| Substrate | 60 × 60 × 1.6 | Box, z = 0…1.6 |
| Ground (GND) | 60 × 60 × 1 | Box, z = −1…0 |
| Patch | ~36 × 28 × 1 | Box on substrate, z = 1.6…2.6 |
| Feed point | 5–7 mm from the patch edge | later port definition |

**Names in the object tree:** `gnd`, `substrate`, `patch`.

### 2.2 Wire / Yagi structure (alternative)

- FreeCAD cylinders / Part tools, or **Preset: Yagi Antenna…** in the GUI.
- Yagi coordinate system: **X = elements, Y = boom, Z = up**.
- Driven dipole with a gap → the port sits there.

---

## 3. Mark ports in FreeCAD (recommended)

Each port needs **two points** (variant A, recommended):

| Label (name) | Meaning |
|--------------|---------|
| `PORT_<name>_start` | Start of the port vector |
| `PORT_<name>_stop`  | End of the port vector |

**Naming scheme:** `PORT_` + port name + `_start` / `_stop`
(examples: `PORT_in1_start`, `PORT_in1_stop`, `PORT_probe_start`, `PORT_probe_stop`).

**How to do it in FreeCAD:**

1. Menu **Part → Vertex** (or Draft Point).
2. In the tree, **rename / set the Label**: exactly `PORT_in1_start` or `PORT_in1_stop`.
3. Place the points at the **feed position** (Placement or task edit).
4. **File → Save** as `.FCStd`.

> Important: the **Label** (not only the internal name) must match the scheme —
> both are read.

**Where does the port sit? (patch 2.45 GHz):**

```text
start = (feed_x, feed_y, z_gnd)     # e.g. (-6.0, 0.0, -1.0)
stop  = (feed_x, feed_y, z_patch)   # e.g. (-6.0, 0.0,  1.6)
```

- Vector **perpendicular** between patch and ground (typically **+Z** / −Z).
- Start/stop must be **clearly different** (not identical points).
- The direction is derived from the largest Δ (`x`/`y`/`z`).

**Yagi / dipole (gap):**

```text
start = (gap_x_left,  y0,  z0)
stop  = (gap_x_right, y0,  z0)
```

**Further ports:** one more pair per port, e.g.
`PORT_in1_*` (excitation, excite = true) + `PORT_probe_*` (measurement, excite = false).

PORT points are **not** tessellated as meshes (only read as markers).

> Details for all three variants (points / custom properties / document JSON),
> error table and examples: **[FREECAD_PORTS_EN.md](FREECAD_PORTS_EN.md)**.

---

## 4. Save as FCStd

1. **File → Save As…** → `*.FCStd`
2. Path, e.g.
   `C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\freecad_export\antenna.FCStd`
3. Optionally also: **File → Export…** → **STEP** (`*.step`) — pure geometry
   for other tools, but **without** ports.

---

## 5. Import into the GUI

Start the GUI:

```text
C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI
python app\main.py
```

**Variant A — import (standard):**

1. **Import Geometry…** (File menu)
2. Choose the file: `.fcstd` (preferred, carries ports) or `.step`/`.stp`/`.stl`/`.obj`/`openems_export.json`
   (a folder of per-object STLs, e.g. `stl/`, also works)
3. FreeCADCmd tessellates STEP/FCStd → `.npy` meshes; STL/manifests are loaded
   directly — **one primitive per FreeCAD object**, object names are preserved
4. Objects appear as **stl primitives** (default material: copper / PEC)
5. Check the log: mesh paths, port hint `Ports auto-added`, any WARNINGS

**Variant B — FreeCAD tools in the GUI:**

1. **FreeCAD…** → **Launch FreeCAD** (opens FreeCAD, cwd = work dir) — draw/edit
2. **Export CAD…**
   - Source: `.fcstd` (preferred) or `.step`
   - **Import as:** `STEP` (the app tessellates it later) or
     `STL (one mesh per FreeCAD object)` (uses `openems_export.json`,
     no FreeCAD needed at simulation time)
   - Deflection: 0.1 mm (default)
   - Check **Import meshes into project after export**
3. OK → export → auto-import into the project

### Check the ports

1. Read the log: the `Ports auto-added` hint.
2. **Geometry → Ports / Dumps…** (or **Ctrl+Shift+D**) opens the
   **Ports / Dumps** window.
3. The port(s) appear in the list (e.g. `in1`, R = 50, excite on).
4. Optional: double-click to edit, **Edit Port…** / **Remove Port**.
   Changes apply immediately (no Apply needed — the window hosts the live widgets).

Missing start / identical points → the port is skipped (a log warning may appear).

> Step-by-step details, custom properties, document JSON and the
> error table: **[FREECAD_PORTS_EN.md](FREECAD_PORTS_EN.md)**.

---

## 6. Build the mesh

1. **Mesh → Mesh Parameters…** (or **Ctrl+Shift+M**) — preset, f0/fc,
   start/stop, drawing unit, readout; **Apply** writes to the project.
2. **Mesh → Build CSX + Mesh + Validate** (or **Ctrl+M**) — tessellates and validates.
3. The result is in the **Log panel → Mesh Report** tab (single source after a build).
4. **View → Mesh Viewer** shows the scene; the **slice slider** snaps to mesh lines.

---

## 7. Run the simulation and evaluate

1. **Simulation → Simulation Parameters…** (or **Ctrl+Shift+P**) —
   end_criteria, max_timesteps, boundary ×6 + PML, air margin, NF2FF.
   **Apply** writes to the project.
2. Optional: **Geometry → Fit NF2FF** (fit the radiation-pattern box to the geometry).
3. **Simulation → Run Simulation** (or **Ctrl+R**).
4. Watch the **Log** tab (openEMS run).
5. Result: **Simulation → S-Params Viewer** (S-matrix / S11) and/or the
   NF2FF plot (radiation pattern).

---

## Checklist (overall)

- [ ] FreeCAD: **View → Units → mm**
- [ ] One solid = one object, clearly named (`gnd`, `substrate`, `patch`)
- [ ] Feed points labelled `PORT_in1_start` / `PORT_in1_stop`
- [ ] Vector spans the port aperture (patch ↔ GND or dipole gap)
- [ ] Saved as **FCStd** (not just STEP)
- [ ] GUI: **Import Geometry…** (`.fcstd`) → log: `Ports auto-added`
- [ ] **Geometry → Ports / Dumps…** check (50 Ω, Excite on port 1)
- [ ] **Mesh Parameters…** → **Build CSX + Mesh + Validate (Ctrl+M)** → check Mesh Report
- [ ] **Simulation Parameters…** → **Run Simulation (Ctrl+R)**
- [ ] Evaluate S-Params / NF2FF

---

## Common errors

| Problem | Fix |
|---------|-----|
| Port missing after import | Check label/name: `PORT_in1_start` + `_stop`; use **FCStd** instead of STEP |
| Only `_start` present | Both points needed, same name base |
| Identical points | Start ≠ Stop, vector ≠ 0 |
| Port in the wrong place | Points on the feed gap / patch–GND |
| STEP import without ports | Use **FCStd** (custom props/JSON are often lost with plain STEP) |
| Port not excited | **Ports / Dumps…** → Excite on port 1 |
| Mesh missing at the port | Mesh Parameters: higher preset; check ports **before** the build |
| Domain too large/small | **Simulation Parameters…** → Air margin; then Build + Fit NF2FF |

---

## Related files

| File | Purpose |
|------|---------|
| `docs\FREECAD_PORTS_EN.md` | Port marking in detail (3 variants) |
| `docs\ANLEITUNG_EN.md` | Full workflow + troubleshooting |
| `app\freecad_io\port_marks.py` | FreeCAD → PortDef extraction |
| `app\freecad_io\freecad_mesh.py` | FCStd/STEP → STL/npy tessellation |
| `app\freecad_io\importer.py` | Geometry import |
| `examples\freecad_dipole_ports.py` | Dipole example with PORT markers |
| `examples\run_dipole_ports_example.py` | Generate dipole + verify ports |
| `examples\freecad_add_port_marks.py` | Demo: create port points |
| `tests\dipole_ports_smoke.py` | Dipole-ports smoke test |
| `app\ui\tab_help.py` | GUI help tabs / windows |
