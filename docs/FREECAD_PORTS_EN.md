# Guide: Mark a port in FreeCAD

**Goal:** mark the model in FreeCAD so that the GUI **automatically creates an openEMS port** on import (name, 50 Ω, start/stop, direction).

**Prerequisites**

| Item | Value |
|------|-------|
| FreeCAD | 1.1.4 (`c:\FreeCAD_1.1.4-Windows-x86_64\`) |
| Units | **View → Units → mm** |
| Document type | save as **FCStd** (STEP/STL usually do **not** store ports) |
| GUI import | **Import Geometry…** or **FreeCAD… → Export CAD…** |

**Recommended:** variant A (points `PORT_*`).  
**Alternative:** variant B (custom properties on the solid) or C (document JSON).

---

## Why ports in FreeCAD?

- Geometry **and** feed marking stay in the CAD document.
- On every re-import/export the port does not have to be recreated manually.
- On import/export the GUI reads `ports` and creates **PortDef** entries in the **Ports / Dumps** window (Geometry menu, **Ctrl+Shift+D**).

---

## Variant A — port points (recommended)

### 1. Create feed points

Each port needs **two points**:

| Label (name) | Meaning |
|--------------|---------|
| `PORT_<name>_start` | Start of the port vector |
| `PORT_<name>_stop` | End of the port vector |

**Naming scheme:** `PORT_` + port name + `_start` / `_stop`  
Examples: `PORT_in1_start`, `PORT_in1_stop` · `PORT_probe_start`, `PORT_probe_stop`

**How to do it in FreeCAD:**

1. Menu **Part → Vertex** (or Draft Point).
2. In the tree, **rename / set the Label**: exactly `PORT_in1_start` or `PORT_in1_stop`.
3. Place the points at the **feed position** (Placement or task edit).
4. **File → Save** as `.FCStd`.

> Important: the **Label** (not only the internal name) must match the scheme — both are read.

### 2. Where does the port sit? (patch antenna 2.45 GHz)

Example patch (mm), Z = height, GND at z ≈ −1…0, patch on top:

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

Vector across the gap of the driven element.

### 3. Further ports

One more pair per port, e.g.:

```text
PORT_in1_start  / PORT_in1_stop     # excitation (excite = true)
PORT_probe_start / PORT_probe_stop  # measurement (excite = false)
```

### 4. Import into the GUI

1. Start the GUI: `python app\main.py`
2. **Import Geometry…** → `.fcstd` **or** **FreeCAD… → Export CAD…** (import checkbox)
3. Check the log: the `Ports auto-added` hint
4. **Geometry → Ports / Dumps…** (**Ctrl+Shift+D**): the port(s) appear (e.g. `in1`, R = 50, excite on)
5. Optionally check/change them in the **Ports / Dumps** window (Edit Port… / Remove Port)
6. **Mesh → Mesh Parameters…** → **Build CSX + Mesh + Validate (Ctrl+M)** → **Simulation → Run Simulation (Ctrl+R)**

PORT points are **not** tessellated as meshes (only read as markers).

### 5. Example script (optional)

**2.45 GHz dipole with ready-made ports (recommended as a starting model):**

```text
python examples\run_dipole_ports_example.py
```

Creates under `work\dipole_ports\`:

| File | Content |
|------|---------|
| `dipole_2p45.FCStd` | geometry + `PORT_in1_*` + `openems_ports` JSON + props |
| `dipole_2p45.step` | pure geometry (without port markers) |
| `openems_ports.json` / `openems_export.json` | port definition for import |
| `dipole_params.json` | dimensions (λ, L, gap) |

Geometry: half-wave dipole, wire Ø2 mm, gap 2 mm, port `in1` across the gap (−1 mm → +1 mm on X).

**FCStd only (FreeCADCmd):**

```text
set DIPOLE_OUT_DIR=C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\dipole_ports
FreeCADCmd examples\freecad_dipole_ports.py
```

Smoke test: `tests\dipole_ports_smoke.py`

**General marker demo (create points):**

```text
FreeCADCmd examples\freecad_add_port_marks.py
```

Creates `PORT_in1_start/stop` on the first solid and saves  
`work\freecad_export\port_marks_example.FCStd`.

Env (optional):

```text
set FREECAD_SOURCE=C:\...\my_model.FCStd
set FREECAD_OUT_DIR=C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI\work\freecad_export
```

---

## Variant B — custom properties on the solid

On the object (e.g. `patch`) add a **custom property** via **Add Property** (or the property editor):

| Property | Type | Example |
|----------|------|---------|
| `openems_port_name` | String | `in1` |
| `openems_port_type` | String | `lumped` |
| `openems_port_start` | String / Vector | `-6, 0, -1` or `FreeCAD.Vector(-6,0,-1)` |
| `openems_port_stop` | String / Vector | `-6, 0, 1.6` |
| `openems_port_r` | Float | `50` |
| `openems_port_excite` | Bool | `true` |

- The property name must **exactly** start with `openems_port_…`.
- Types: `lumped` · `msl` · `rect_waveguide` (otherwise `lumped`).
- Save FCStd → import as above.

---

## Variant C — document JSON

Document property `openems_ports` (String) with a JSON list:

```json
[
  {
    "name": "in1",
    "type": "lumped",
    "start": [-6.0, 0.0, -1.0],
    "stop": [-6.0, 0.0, 1.6],
    "resistance": 50.0,
    "direction": "z",
    "excite_port": true
  }
]
```

The FreeCAD-tools tip in the GUI also mentions this path. Variant A is usually faster.

---

## What the GUI does with it

| Field | Source |
|-------|--------|
| Name | Label `PORT_<name>_…` / `openems_port_name` / `name` |
| Start/Stop | Points / props / JSON |
| Direction | `direction` or the settings default or the largest Δ of the vector axis |
| R | CAD value, otherwise **Settings → Port defaults** (default 50 Ω) |
| Excite | CAD value, otherwise settings (default true) |
| Type / mesh dirs | CAD value, otherwise settings |

**Configurable in the GUI:**

1. **Settings… → Port defaults** — type, resistance, excite, mesh dirs, direction  
   (applies to the FreeCAD import and as the seed for *Add Port…*; explicit `openems_port_*` values always win)
2. **Geometry → Ports / Dumps…** (**Ctrl+Shift+D**) — double-click to edit, *Edit Port…* / *Remove Port*

Missing start / identical points → the port is skipped (a log warning may appear).

---

## Common errors

| Problem | Fix |
|---------|-----|
| Port missing in the log / Ports / Dumps | Check label/name: `PORT_in1_start` + `_stop` |
| Only `_start` present | Both points needed, same name base |
| Identical points | Start ≠ Stop, vector ≠ 0 |
| Port in the wrong place | Points on the feed gap / patch–GND |
| STEP import without ports | Use **FCStd** (or custom props/JSON — often lost with plain STEP) |
| Port not excited | GUI → Geometry → Ports / Dumps… → **Excite** on port 1 |
| Mesh missing at the port | Mesh Parameters: higher preset; check ports **before** the build |

---

## Checklist

- [ ] Model unit **mm**
- [ ] Feed points labelled `PORT_in1_start` / `PORT_in1_stop`
- [ ] Vector spans the port aperture (patch ↔ GND or dipole gap)
- [ ] Saved as **FCStd**
- [ ] GUI **Import Geometry…** or **Export CAD…**
- [ ] Log: Ports auto-added
- [ ] Geometry → **Ports / Dumps…** check (50 Ω, Excite)
- [ ] **Build CSX + Mesh + Validate (Ctrl+M)** → **Run Simulation (Ctrl+R)** → check S-Params
- [ ] Full workflow: **[FREECAD_WORKFLOW_EN.md](FREECAD_WORKFLOW_EN.md)**

---

## Related files

| File | Purpose |
|------|---------|
| `app\freecad_io\port_marks.py` | FreeCAD → PortDef extraction |
| `examples\freecad_dipole_ports.py` | Dipole example with PORT markers |
| `examples\run_dipole_ports_example.py` | Generate dipole + verify ports |
| `examples\freecad_export_for_openems.py` | Export + `ports` in the manifest |
| `examples\freecad_add_port_marks.py` | Demo: create port points |
| `tests\dipole_ports_smoke.py` | Dipole-ports smoke test |
| `app\ui\tab_help.py` | GUI help tabs / windows (Geometry / Ports / …) |
| `docs\FREECAD_WORKFLOW_EN.md` | Full workflow FreeCAD → import → simulation |
| `docs\ANLEITUNG_EN.md` | Full workflow FreeCAD → simulation |
