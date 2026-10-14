# FreeCAD → openEMS GUI

Standalone desktop application that takes 3D structures from FreeCAD (STEP/STL/OBJ),
builds an openEMS/CSXCAD model, enforces meshing rules (with easyMesh automation),
runs FDTD simulations via Python bindings, and post-processes S-parameters,
field dumps, and NF2FF far-field patterns.

## Requirements

| Component | Path / Version |
|---|---|
| Python (solver + GUI) | `c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe` |
| openEMS Python | 0.0.36 (already installed in WinPython) |
| CSXCAD Python | 0.6.3 (already installed in WinPython) |
| FreeCAD (CAD only) | `c:\FreeCAD_1.1.4-Windows-x86_64\` |
| openEMS solver | `c:\openEMS\` (optional path for AppCSXCAD / nf2ff binary) |

Python packages: `requirements.txt` + local `easyMesh4openEMS`.

## Setup

```powershell
cd C:\Users\istra\Documents\claude_ps\FreeCAD_OpenEMS_GUI
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe -m pip install -r requirements.txt
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe -m pip install .\third_party\easyMesh4openEMS
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\verify_imports.py
```

## Launch GUI

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe app\main.py
```

### S-parameter viewer

Menu **Simulation → S-Params Viewer** or tab **S-Params**:

- Loads Touchstone (`.s1p`/`.s2p`…) or app `sparams.json` via scikit-rf
- Plot types: Smith chart, |S| dB, |S| mag, Phase, VSWR, Re/Im
- Click on the plot to drop **markers**; table shows f, |S|, dB, phase, VSWR
- Mouse **cursor** readout in the info bar; **Go to S-min** jumps to the dip
- After a simulation finishes the viewer auto-opens with the new results

### Simulation script editor (Simulation → Script…)

Run Simulation always writes `work/<project>/project_run.py` (that file *is* the simulation).

- **Script…** opens it in a modeless editor: **Save**, **Revert**, **Regenerate from project**
- Editing then Running asks whether to regenerate (your edits are copied to
  `project_run_edited.py`) or to run your edited script as-is
- Run an edited script by hand: `<python> work\<project>\project_run.py`
- `project_run.py.lastgen` holds the pristine generated copy used to detect edits

### Preset: PCB Filter (2-port example)

Menu **Project → Preset: PCB Filter…** builds a 2.45 GHz microstrip notch filter on FR4
(1.6 mm, 50 Ω line + open λg/4 stub, two lumped edge ports). One run measures one
S-matrix column, so the example runs both excitations and merges them:

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe examples\run_pcb_filter_example.py
```

Geometry comes from **FreeCAD** (FCStd → per-object STL, 35 µm copper); flags:
`--rebuild` (regenerate model), `--skip-run` / `--mesh-only`, `--analytic`
(legacy analytic box primitives for comparison).

Output: `work\pcb_filter\simulation\{sparams.json, sparams.s2p, sparams.png}` —
FreeCAD run: stop band **−29.99 dB at 2.450 GHz** (design target), passband
−0.72 dB at 0.3 GHz; the analytic preset (`--analytic`) gives −28.96 dB at
2.429 GHz — the 0.9 % shift is the real 35 µm copper vs. the tutorial's
zero-thickness strip.

Notes:

- FreeCAD solids that are **axis-aligned boxes** are emitted as openEMS boxes
  (identical metal edges on the mesh lines); everything else stays an STL
  `PolyhedronReader`. openEMS does **not** simulate in-memory `Polyhedron`
  primitives (they are reported as *Unused primitive*).
- `[WARNING] grading_jump: Axis z 9.14` is expected: the 35 µm copper forces
  0.035 mm cells next to the 0.32 mm substrate cells (warning only, exit code 0).
- `Max. number of timesteps was reached` is expected here — the stop band is
  stable (0.019 dB change going 50 000 → 150 000 steps, `work\pcb_filter_conv`).

## Workflow

1. Model your structure in **FreeCAD 1.1.4**.
2. Export **STEP** (preferred), or **STL** — one mesh per object (recommended when you skip FreeCAD at run time).
3. In this app: import geometry → assign materials/ports → Auto-Mesh → Mesh Check.
   **Import Geometry…** accepts `.step/.fcstd` (tessellated via FreeCADCmd), a single `.stl/.obj`, an `openems_export.json` manifest, or a folder of per-object STLs.
   **FreeCAD… → Export CAD…** has an **Import as** option: `STEP` or `STL (one mesh per FreeCAD object)`.
4. Configure simulation (frequency, boundary, dumps, NF2FF box) → Run.
5. Inspect S-parameters, field slices, and the NF2FF far-field pattern (Antenna tab: 3D gain, E/H cuts, directivity/gain/efficiency).

Headless STL import example:

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe examples\run_stl_import_example.py
```

### FreeCAD headless conversion (optional)

If you keep the project as `.FCStd`, set FreeCADCmd in Settings:

```text
c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe
```

The app can convert FCStd → STEP via FreeCADCmd before import.

## Meshing rules enforced by the app

- Hard validation limit: max cell ≤ lambda_min/10 (openEMS rule of thumb)
- Generation target from easyMesh preset: low ≈ lambda/10, medium ≈ lambda/12, high ≈ lambda/16
- Smooth grading (neighbor ratio ≤ max_grading_ratio, default 2)
- 1/3–2/3 metal edge rule (easyMesh)
- 2D metals / conducting sheets on exact mesh lines
- Port alignment (1D → one mesh line; 2D → one cell)
- PML cells after air margin
- Drawing-unit sanity checks

Materials and geometry are always created via `CSX.AddMetal` / `CSX.AddMaterial` so easyMesh auto-registers primitives in the shared mesh-setup dict. Empty placeholder properties from `enhance_csx` are never reused.

## Project files

- `.fcsim.json` — project definition (geometry map, materials, ports, mesh, sim, dumps, NF2FF)
- Generated `project_run.py` — reproducible simulation script (no GUI required)
- `project_run.py.lastgen` + `project_run_edited.py` — pristine generated copy and the
  backup of your hand-edited script (see **Script…** above)

## Tests

```powershell
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\verify_imports.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\test_models.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\freecad_import.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\smoke_patch.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\stl_import_smoke.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\script_editor_smoke.py
c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe tests\menu_smoke.py
```

Qt-dependent smokes run with `QT_QPA_PLATFORM=offscreen` (menu_smoke, mainwindow_sparam_smoke, mesh_viewer_smoke, sim_params_smoke, script_editor_smoke, tab_help_smoke, tree_edit_smoke).

Expected smoke outputs under `work\smoke_patch\simulation\`: `sparams.png`, `sparams.json`, `sparams.s1p` (Touchstone), `fields\`, `nf2ff\nf2ff_summary.png`.

## License notes

- openEMS: GPLv3
- CSXCAD: LGPLv3
- easyMesh4openEMS: GPL-3.0 (third_party); local changes: `docs/EASYMESH_CHANGES_DE.md` / `docs/EASYMESH_CHANGES_EN.md`
- Docs (EN): `docs/ANLEITUNG_EN.md`, `docs/FREECAD_WORKFLOW_EN.md`, `docs/FREECAD_PORTS_EN.md`
- This application code: choose a license when publishing
