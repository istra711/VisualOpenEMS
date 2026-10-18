"""Per-tab help content + dialog for the main window tabs (English UI)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

TAB_ORDER = [
    "Geometry",
    "Mesh Viewer",
    "S-Params",
    "Antenna",
    "Antenna Simulation",
    "Cylindrical Mesh",
    "VTK",
]

TAB_HELP: dict[str, str] = {
    "Geometry": """GEOMETRY — project tree, primitives, imported CAD

Left panel (always visible)
  • Project / Geometry list — primitives, materials, ports, dumps, assignments.
  • Add Box / Cylinder / Sphere / Polygon Primitive — openEMS primitives in
    mm (project units). Polygon/sphere need the cartesian coordinate system.
  • Add Assignment — bind FreeCAD mesh names to materials / boundary roles.
  • Add Material — PEC, dielectric, conducting sheet, lumped RLC, or ignore.
  • Add Port — lumped / MSL / waveguide port (also in the Ports / Dumps window).
  • Edit / Fit NF2FF box — far-field box around the antenna.

This tab (right)
  • Imported objects — names + AABB after Import Geometry or FreeCAD export.
  • Materials — material list currently used by the project.

Workflow
  1. FreeCAD… → launch FreeCAD or Export CAD (FCStd/STEP → meshes + openems_export.json).
  2. Import Geometry… / Load project (Open…) — meshes appear in the list.
  3. FreeCAD port marks (PORT_in1_start/stop or openems_port_* props) auto-fill Ports.
  4. Next: Mesh preset + Build, then Simulation Parameters, Ports / Dumps (Geometry menu), Run.

Presets (Project menu — they replace the current project)
  • Preset: Patch Antenna… — 2.45 GHz microstrip patch, one port.
  • Preset: Yagi Antenna… — 2.45 GHz Yagi-Uda + FreeCAD model (FCStd/STEP/STL).
  • Preset: PCB Filter… — 2-port microstrip notch filter on FR4, stop band at
    2.45 GHz (examples/run_pcb_filter_example.py measures both S-matrix columns).

Units: project geometry is always millimetres unless you change Drawing unit on Mesh.

Tips
  • Double-check that every mesh name has a material assignment (or a sensible ignore).
  • Port start/stop must sit between conductor and ground / through the feed.
  • After FreeCAD export, ports are auto-added — review them in the Ports / Dumps window (Geometry menu).
  • Imported solids that form an axis-aligned box become openEMS boxes (exact metal
    edges); every other shape stays an STL polyhedron (PolyhedronReader).
""",
    "Mesh Viewer": """MESH VIEWER — slices, cell sizes, 3D export

Views (tabs inside the plot area)
  • XY / XZ / YZ — 2D slice with mesh grid, primitive AABBs, ports, NF2FF box.
  • Cell sizes — per-axis cell-size plots along the domain.
  • Domain overview — bounding box and mesh resolution summary.

Navigation
  • Mouse wheel — zoom at cursor (works even without the toolbar zoom).
  • Double-click — reset / home view.
  • Toolbar — matplotlib pan/zoom/home/save.
  • Pop-out — open the viewer in a large separate window (1200×800).
  • Slice slider — move the cutting plane (snaps to the nearest mesh line on the slice axis).

Mesh editor (right-hand panel) — manual mesh lines
  • Pick an axis (x/y/z), type a coordinate and "Add line" to force a grid line.
  • In a slice: a single left-click in FREE space adds a line there (on the
    editor axis; if that axis is the slice normal, the first in-plane axis is
    used); a click ON a line (within a narrow band, ~0.6 % of the axis) selects
    that line instead — highlighted orange. Shift+click always adds, so you can
    place several lines in a row. The axis combo follows the click. A click near
    a geometry edge (within ~1 % of the axis) snaps the NEW line exactly onto
    that edge (primitive AABB faces + ports).
  • Lines can only be placed INSIDE the FDTD domain: the coordinate spin is
    limited to the domain range and a click/value outside is clamped to the
    domain edge (an out-of-domain line is also ignored on build).
  • The list shows the current lines: blank = from easyMesh, "+" (green) = added
    by you, "−" (red) = an auto line you removed. Select a row and
    "Remove selected" (drop a '+', or delete an auto line) /
    "Restore selected" (undo a removal). Select several with Shift (range) or
    Ctrl (toggle); Del / Backspace removes the selection.
  • Right-click in the plot area opens a menu: toggle the Mesh editor, delete
    the selected line(s), pop the viewer out (like the Antenna / S-Params
    viewers), reset the view.
  • Edits are applied to the CURRENT mesh immediately (the grid is re-derived
    from the last auto mesh + your overrides — no easyMesh re-run, the view is
    not reset), and stored in the project, so they survive everything.
  • Click "Build Mesh" (Ctrl+M) to rebuild from scratch and save csx.xml (the
    result is validated: cell sizes / grading / CFL): the overrides are applied
    again on the fresh auto mesh. "Rebuild from scratch…" discards ALL manual
    lines (with a confirmation) and rebuilds with easyMesh.
  • Robustness: the overrides store absolute coordinates while easyMesh rebuilds
    the grid each time. A removal therefore drops the grid line NEAREST the
    stored coordinate (within a local-cell tolerance), so a slightly changed mesh
    still matches; a removal that finds no line is reported as a WARNING in the
    mesh report (the applied counts are listed there too) — it is never applied
    to the wrong place silently.
  • Cartesian meshes only; a very small manual cell shrinks the CFL timestep.
  • Mesh menu → "New Scene (no mesh)…": shows the geometry, the FDTD domain box
    and the NF2FF box on an EMPTY grid (no easyMesh run, nothing written to
    disk). Start from the domain corners and add every mesh line by hand.

Frames (overview + slices)
  • Black dashed — FDTD domain (geometry + air margin, on mesh lines); size it on
    Sim Params → Boundary conditions (see that tab's help).
  • Purple dash-dot — NF2FF box; updated right away after Fit/Edit NF2FF
    (build the mesh first — without a built mesh nothing is drawn).
  • Colored rectangles — primitive bounding boxes; red dot — port center.
  • Red outline + “OUTSIDE DOMAIN” note (red text, overview panel and slice
    title) — that primitive sticks out of the black frame; openEMS would clip
    it silently. Fix: bigger air margin (Sim Params) or smaller geometry,
    then Build Mesh.

AppCSXCAD
  • “Open in AppCSXCAD” exports CSX XML and launches the 3D viewer for a true mesh review.

Tips
  • The “Mesh Viewer Scene Info” tab (in the log panel, next to Log / Mesh Report)
    shows the same info text — useful while you watch the log or the report.
  • Check that the mesh lines pass through ports and thin substrates (cell size there).
  • Very large air margins shrink useful resolution — see Simulation Parameters.
  • The viewer holds the last built CSX — rebuild after geometry or mesh changes.
""",
    "Simulation Parameters": """SIMULATION PARAMETERS — standalone window (Ctrl+Shift+P)

Opened from Simulation menu → Simulation Parameters…. Two tabs; the dialog
keeps its own copy until you Apply. (This window replaces the former Sim
Params tab — the same fields are here.)

Tab Simulation-Parameter
  Computed (read-only)
    • λ_air at f0, effective max timesteps, boundary notes.
    • Patch / geometry readout when using the built-in patch preset.
  Overrides
    • Max timesteps, end criteria (S-param stability).
    • Boundary xmin/xmax/ymin/ymax/zmin/zmax (PEC / PML / periodic…).
    • Air margin around geometry + min cell size.
    • Apply Patch Params — feeds / patch size for the patch antenna example.

  Antenna simulation (group below Boundary conditions)
    • "Antenna simulation mode" — turns on the FDTD antenna checklist
      (Help → Antenna Simulation): mesh density ≥ 15–20 cells/λ, air margin
      = PML distance × λ(f_start) on all faces (the auto λ/4-at-f_stop rule
      is too small for antennas), NF2FF far-field enabled.
    • Mesh density — low (10) / medium (15) / high (20) / very_high (25)
      cells per λ; written to the project mesh preset on Apply.
    • PML distance — multiple of λ at f_start, keep 0.50 … 1.00.
    • While the mode is on the normal Air-margin controls are disabled and
      re-derived from the band; turning the mode off hands control back to
      them (values already applied stay as they are).

Tab NF2FF-Parameter
  • Enable far-field (NF2FF) — creates the recording box + far-field post.
  • Evaluation frequencies — Mode:
      - Auto (S11 minimum): one frequency at the S11 dip.
      - N points (mesh range): N points equally spaced over the mesh
        frequency range (start/stop frequency from the Mesh tab), e.g.
        11 points from 2 to 2.8 GHz → gain vs frequency curve.
      - Frequency list: explicit entries, each with unit Hz / kHz / MHz /
        GHz (bare number = Hz), e.g. "2.4 GHz, 2.45 GHz, 2500 MHz".
    After Run: Antenna tab → Plot combo → "Gain vs Frequency".
  • NF2FF θ / φ steps — angular resolution of cuts and the 3D pattern.
  • Fit to geometry + air margin / Edit box… — far-field box editor.

Domain size (black dashed frame in the Mesh Viewer)
  • There is no direct box editor: domain = geometry bbox + air margin + cell
    rounding. This is where you control it.
  • "Air margin auto (λ/4 free-space)" — ON: λ/4 at the STOP frequency, always
    εr=1 (2.7 GHz → 27.76 mm per face). Changing Stop also changes the margin.
  • Auto OFF → "Air margin (all faces)" — fixed mm value for all 6 faces.
    In the project JSON: boundary.air_margin = [xmin,xmax,ymin,ymax,zmin,zmax]
    (one value per face; the GUI writes the same value to all six).
  • PML size — each PML face gets PML_size+1 extra cells outside the air region
    (bigger domain). No effect on faces set to MUR.
  • Mesh preset — air cells grow up to λ/10…λ/40, so the outer frame line snaps
    to cells (a few mm off, slightly different per face).
  • After changing any of this: Build Mesh, then Fit NF2FF to Geometry so the
    far-field box stays inside the domain. Too small → "outside the field
    domain" warnings, dumps/NF2FF clamped; too large → more cells (RAM/time).

Buttons
  • Apply — writes every value into the project, mirrors them back into the
    (hidden) Sim Params panel used by run/save, and refreshes
    work/<project>/project_run.py when it already exists (a visible Script
    editor reloads it unless you have unsaved edits there).
  • Close — discards anything you edited but did not apply.
  • Apply patch (rebuild primitives) — rebuilds the patch antenna with the
    dialog's feed / patch values; the window re-syncs to the new project.
  • Fit NF2FF / Edit NF2FF — far-field box around the current geometry.

Notes
  • Second click on the menu entry re-uses the same window (it re-syncs).
  • Values always stored in Hz (the display unit is display only).

Tips
  • Effective timestep already accounts for CFL + cell size — don't force tiny max_ts without need.
  • PML on the radiation sides;PEC often fine on ground.
  • Auto air margin = λ/4 at the STOP frequency (not geometry-based) — uncheck it
    if you need a fixed, reproducible domain size.
  • Change max_timesteps / end_criteria here before a long run. The end
    criterion is entered in dB (0 dB = energy at start; the run stops when the
    residual energy has dropped by this much, e.g. -50 dB = linear 1e-5).
  • Threads (numThreads): 0 = Auto (all cores). Set e.g. 4 to leave cores free
    or to limit CPU load on shared machines — passed to FDTD.Run(numThreads=N).

Run script (toolbar: Script…)
  • Run Simulation writes work/<project>/project_run.py (plus project_copy.json,
    geometry_primitives.json) — that file is what actually runs openEMS.
  • Script… opens it for viewing/editing: Save, Revert, Regenerate from project.
  • Apply in this window (or in Mesh Parameters) also refreshes an existing
    script, so the editor always shows the current project state.
  • Run regenerates the script; hand-edits are copied to project_run_edited.py and
    you are asked whether to regenerate or run your edited script as-is.
  • Run an edited script by hand:  <simulation python> work/<project>/project_run.py

Progress (status bar)
  • During a run the status bar shows TWO bars + a TS/energy label parsed from
    the openEMS output, plus elapsed time and "converged" / "max TS reached".
    – TS bar: timesteps done / max timesteps.
    – Energy bar: residual-energy convergence toward the end criterion
      (EndCriteria, e.g. 1e-5 → -50 dB) — the run's actual stopping condition,
      which the timestep count alone does not decide.
    Both reset on each run.

Run All Ports (Simulation menu → Ctrl+Shift+R)
  • One FDTD run per port (each port excited once) → the complete S-matrix for
    multi-port structures. Runs land in work/<project>/multi/port<n>/ and are
    merged into work/<project>/sparams/sparams.json + .sNp. Needs ≥ 2 ports
    (fewer falls back to a normal single run).
""",
    "Mesh Parameters": """MESH PARAMETERS — standalone window (Ctrl+Shift+M)

Opened from Mesh menu → Mesh Parameters…. All mesh settings in one place;
the dialog is lazy — edits never touch the project until you Apply.

Fields
  • Mesh preset — low / medium / high / very_high (lambda sampling).
  • Frequency unit — display only (Hz/kHz/MHz/GHz); values stored in Hz.
  • Center f0 / Corner fc — Gauss excitation; editing f0/fc sets
    start/stop = f0 ± fc.
  • Start / Stop — simulation band for mesh λ and S-params (independent
    overrides; editing these clamps f0 into the band and sets fc).
  • Drawing unit — project length unit for new primitives (default mm).
  • Coordinate system — cartesian (x/y/z, easyMesh) or cylindrical
    (openEMS CoordSystem=1, rho/phi/z). See Help → Cylindrical Mesh for
    the supported primitives, degree-based phi editing and limits.

Readout (live, before Apply)
  • λ_air at f_max, max/target cell size, current preset + frequencies.
  • Computed from the draft buffer, not from the project.

Buttons
  • Apply — writes into the project (f0 ± fc mirror), mirrors the values into
    the hidden mesh form, and refreshes work/<project>/project_run.py when it
    already exists.
  • Close — discards unapplied changes.

Notes
  • Second click on the menu entry re-uses the same window (it re-syncs).
  • Build Mesh (Ctrl+M / Mesh menu) reads the project, so
    Apply first, then Build. The build runs in a background thread — the UI
    stays responsive, the button is disabled until it finishes and the log
    panel shows progress.
  • Domain size (black dashed frame in the Mesh Viewer) is NOT here — it
    comes from air margin / boundary: Sim Params → Boundary conditions.
  • Hard cell limit — after meshing, every cell (including the PML region
    easyMesh stretches) is capped at λ_air(f_max)/10. Cells slightly larger
    than that used to abort the run with "cell_too_large"; they are now split
    automatically, so any preset builds a valid grid (the preset still sets the
    characteristic air cellsize).
""",
    "Cylindrical Mesh": """CYLINDRICAL MESH — openEMS CoordSystem=1 (rho/phi/z)

What it is
  • Mesh Parameters → Coordinate system = cylindrical switches the solver
    and the mesher to openEMS cylindrical coordinates (rho, phi, z).
    easyMesh is bypassed entirely — a dedicated polar mesher fills the
    grid (no easyMesh fork changes); phi always spans the full turn
    0..2π (closed cylinder) and the rho=0 axis is always a mesh line.

Geometry in cylindrical mode
  • Allowed primitives: box (= wedge in rho/phi/z) and cylinder.
    The editors show rho/phi/z suffixes; phi is shown in DEGREES and
    stored as radians in the project.
  • NOT supported: STL / FreeCAD imports, polygons, polyhedra — the run
    aborts with a clear message. Use the Cartesian system for those.
  • Ports: the x/y/z direction and mesh-dir letters keep their meaning
    x=rho, y=phi, z (openEMS always uses x/y/z letters, also here).

Boundaries & margins
  • Boundary rows are relabelled rho-/rho+/phi-/phi+/z-/z+; the phi rows
    are disabled (they do not exist on a full-turn cylinder).
  • Air margin: only rho+ and z-/z+ count. rho- is the axis (no margin),
    phi+/- are unused — explicit values there are ignored (noted in the
    mesh report).

Results & limits (v1)
  • Field dumps work; the dump domain check runs on rho/phi/z extents.
  • Ports / S-params are NOT supported in cylindrical mode with the
    installed openEMS build: its LumpedPort is not cylindrical-mesh aware
    (Capacity=NaN poisons the whole field). Add/Edit Port is disabled and
    a run with ports aborts with a clear message. Free-response runs
    (no excitation, e.g. cavity analyses with dumps) still work.
    The cylindrical-aware CoaxialPort exists only in openEMS >= 0.37.
  • NF2FF far-field is NOT supported in cylindrical mode — checkbox and
    Fit/Edit stay disabled and the run prints a skip message.
  • Validation labels the axes rho/phi/z and checks the phi axis against
    lambda via ARC length at the outer radius.
  • Best for: cylinders, coax-like feeds and rotationally symmetric
    structures where a cartesian grid would staircase curved walls.
""",
    "Ports / Dumps": """PORTS / DUMPS — excitation, field dumps, NF2FF

Opened from Geometry menu → Ports / Dumps… (Ctrl+Shift+D) — a modeless window
with live editors: changes apply immediately (no Apply/Close round-trip).

Ports
  • Double-click a port (or Edit Port…) — name, type (lumped / MSL / rect waveguide),
    resistance, direction, start/stop, mesh dirs, excite flag.
  • The port's resistance is also the S11 reference (normalisation) impedance:
    a 73 Ω lumped port yields a 73 Ω-referenced S11 and the .s1p header records
    z0=73 (a 50 Ω port keeps the old 50 Ω result). Match it to the reference
    impedance of the tool you compare against (e.g. a CST "Discrete Port").
  • MSL (microstrip) ports also expose Propagation dir / Excitation dir: leave them on
    (auto) to derive both axes from the port box (propagation = longest edge, excitation
    = shortest); set them explicitly for unusual orientations. MSL ports are built after
    the mesh (the strip + port need the grid lines).
  • Rule of thumb — which port type:
      Antenna / wire / diode-style feed (a lumped source across a gap)
        → Lumped port  =  CST "Discrete (Face) Port".
      Feed on a microstrip line / planar circuit (guided mode, defined Z0)
        → MSL port     =  CST "Waveguide / MSL Port".
    A lumped port is a quasi-lumped source (S-params referenced to its Resistance);
    an MSL port names the guided mode at the line cross-section (S-params
    referenced to the line impedance). Use the MSL port only where a real line
    with a defined characteristic impedance exists.
  • Remove Port — delete the selected entry.
  • FreeCAD PORT_in1_start/stop or openems_port_* properties are imported automatically.
  • Settings → Port defaults — type, R, excite, mesh dirs used when FreeCAD omits them
    (PORT points are geometry only). Explicit openems_port_* values always win.
  • Excite exactly one port — one FDTD run measures ONE S-matrix column (S11 + S21 for a
    2-port). Other columns = more runs with a different port excited ("Excite" flag),
    merged afterwards: examples/run_pcb_filter_example.py does this for the 2-port
    PCB filter preset (toolbar: Preset: PCB Filter…).

Dumps
  • Enable field dumps — writes E/H/J fields for AppCSXCAD / Paraview.
  • Add Field Dump… — region + quantity (E, H, J, SAR, …).

NF2FF
  • Enable NF2FF box — far-field box for pattern results.
  • Edit / Fit NF2FF to Geometry — box around the antenna. Fit keeps an equal
    margin on all six faces (symmetric about the geometry) so the far-field
    pattern is not tilted/egg-shaped, and sets the evaluation centre to the box
    centre (nf2ff.center = (start+stop)/2) — an off-centre reference distorts
    the pattern even with a symmetric box. In antenna mode the margin is
    NF2FF box × λ(f_start). (Only one box: the FDTD domain, with the NF2FF box
    inside it; the box must stay inside the domain.)

Tips
  • Port vector direction must cross the intended gap (feed) and be meshed.
  • Resistance 50 Ω default — change globally in Settings if your system differs.
  • After editing ports, rebuild the mesh before Run.
""",
    "S-Params": """S-PARAMS — results, Smith chart, markers

After a successful Run
  • Loads Touchstone (sparams.sNp) + simulation JSON from the work folder.
  • Loaded files lists each entry as subpath relative to the work dir (full
    path in the tooltip); click an entry to load it.
  • Marker style picks which format the marker list shows (dB, |S|,
    Phase, Γ, Z, Y, VSWR, RL — or All formats for every format at once).
  • Plot |S| in dB (and other formats), Smith chart.
  • Marker: click the plot or use Go to min (deepest S11 / selected param).
  • Cursor / markers show frequency + value; right-click a marker in the
    list to delete it (Clear markers removes all).
  • Pop-out S-Graph — separate window for a large plot.

Tips
  • No data → run the simulation first; empty folder → set work dir in Settings.
  • Marker minimum near 2.45 GHz (or your f0) is the usual resonance check.
  • Logs tab / Log panel show export paths when Touchstone files are written.
  • Multi-port (n > 1): one run fills one column — the rest stay empty (NaN) in
    sparams.json until every run is merged; Touchstone is written only when the
    matrix is complete (viewer reads .sNp and .json).
""",
    "Antenna": """ANTENNA — far-field pattern, directivity, gain

After a successful Run (with NF2FF enabled)
  • Loads the project folder's nf2ff/nf2ff.json written by the NF2FF
    post-processing (<work>/<project>/nf2ff/nf2ff.json).
  • Loaded files — lists each file as subpath relative to the work dir plus
    file name (e.g. myproj/nf2ff/nf2ff.json); tooltip = full path.
  • Details — header shows the current file name in parentheses; below it
    Dmax, Gmax, eta, Prad and theta/phi points for the selected frequency.
  • Plots (combo): E-Plane (phi=90), H-Plane (phi=0), Azimuth (theta=90),
    Gain vs Frequency, Radiated Efficiency vs Frequency, 3D Pattern.
  • Radiated Efficiency vs Frequency — eta(f) in % from the exported
    efficiency array (Prad/P_acc); without it eta derives from gain/Dmax.
  • Plane cuts show the full 360° plane (phi0 half + phi0+180 mirror half);
    dB radial tick labels are placed at 90°.
  • Freq combo — select the evaluation frequency when several were computed.
  • 3D checkbox — show the 3D pattern (downsampled for large grids); the
    dedicated "3D Pattern" plot type always shows it (also in the pop-out
    window). "3D field" selects Gain or Dmax. Absolute dBi scaling: the
    "dBi min" slider sets the lower bound (max = pattern max); the window
    maps to both the surface radius (0 at min, 1 at max) and the color scale
    (colorbar in dBi, extend arrow when data lies below the window). Raise
    the slider to zoom the color gradient into a dBi range of interest.
    "cmap" (top button row, next to the 3D checkbox) selects the colormap of
    the surface and colorbar (viridis, plasma, inferno, turbo, jet, coolwarm,
    gray); it only appears while the 3D view is active and is mirrored in
    the pop-out window's top row.
    The 3D axes show no numbers — the colorbar is the dBi scale.
  • Markers — left-click a 2D plot to drop a marker (snaps to the nearest data
    point: angle + dBi on the cuts, frequency + dBi / % on the vs-f plots; the
    3D view takes no markers). All markers are listed on the left with their
    plot name; right-click a row → Delete marker, or use Clear markers (also
    in the pop-out window).
  • Opening angle (HPBW) — half-power beamwidth of the current polar cut:
    angular width of the main lobe at −3 dB below the pattern peak
    (interpolated). Drawn as the grey −3 dB circle + crimson edge lines;
    value in the Details block and the plot title. Pattern-only (gain or
    directivity) — no S-parameters.
  • VRV (F/B) — front-to-back ratio (Vorwärts-Rückwärts-Verhältnis) of the
    current polar cut: Gmax − gain at Gmax+180°, in dB and as linear ratio.
    Orange diamond marks the back lobe. Pattern-only, no S-parameters.
  • Pop out graph — separate window for a large plot; its top row mirrors the
    tab (Plot, Freq, cmap), so plot type and frequency stay selectable there.
  • Analysis menu (right-click any plot) — r min / r max / r step / label
    offset / Opening angle (HPBW) / VRV apply to the polar cuts; Clear markers
    and Pop out graph work on every plot. On the 3D and vs-frequency plots the
    polar-only controls are greyed out.

Enabling NF2FF (far-field)
  • Simulation Parameters (Ctrl+Shift+P) → NF2FF-Parameter tab → Enable
    far-field (NF2FF), or Ports / Dumps window → "Enable NF2FF box".
  • Both the Sim Params flag and the NF2FF box must be on for the run to
    create the recording box.
  • Evaluation frequencies — same tab: Auto (S11 minimum), N points
    equally spaced over the mesh frequency range (gain vs frequency curve),
    or an explicit list (Hz/kHz/MHz/GHz per entry).
  • Fit NF2FF to Geometry after mesh changes so the box stays inside the
    FDTD domain (too small → clamped, Prad ~ 0).

Values
  • Dmax [dBi] — peak directivity: how strongly the antenna concentrates the
    radiated power in its best direction, referenced to an isotropic radiator
    (0 dBi). Radiation pattern only, no losses.
  • gain [dBi] — usable power in that direction:
    gain = Dmax + 10*log10(eta), eta = Prad/Pin (radiated / accepted power;
    Pin taken spectrally from the port voltage/current). gain <= Dmax.
  • Prad [W*s^2] — total radiated power at the evaluation frequency
    (pulse-DFT spectral units; the ratios eta and gain are unaffected).
  • eta / radiated efficiency — fraction of the accepted power that is
    radiated (0..1, plotted as %); gain = Dmax + 10*log10(eta).
  • theta 0..180 deg, phi 0..360 deg; polar cuts are in dBi.

Tips
  • No data → run the simulation first with NF2FF enabled.
  • If Prad ~ 0 or Dmax looks wrong, the NF2FF box is likely clamped outside
    the domain — Fit NF2FF to Geometry, rebuild, and re-run.
  • If the pattern is tilted / "egg"-shaped or Dmax is too high, the NF2FF box
    centre is off: the evaluation sphere must be centred on the box, i.e.
    nf2ff.center = (start+stop)/2. Re-run "Fit NF2FF to Geometry" (the run also
    warns and snaps a stale centre back). See Antenna Simulation → NF2FF box.
  • Summary PNG (nf2ff_summary.png) is written next to nf2ff.json.
""",
    "VTK": """VTK — field dumps as a 3D VTK file (.vtr)

Turns the openEMS HDF5 field dumps into VTK rectilinear grids and shows them in
an embedded 3D view (no external ParaView needed).

Buttons
  • "Export project dumps → VTK": converts every dump in the current project's
    simulation/ folder (*.h5, excluding nf2ff_*) into <project>/vtk/*.vtr and
    loads the first one.
  • "Open .vtr…": open any VTK file — .vtr, .vti, .vtu, .vtp, .vts (XML) or a
    legacy .vtk (e.g. a ParaView example); the reader is picked by extension.
  • Representation: "Surface" (default — the dataset coloured by the scalar,
    like ParaView), "Iso-surface" (a contour of the scalar) or "Wireframe".
  • "Outline": domain box. "Iso-level" slider: contour level (Iso-surface only).
  • A colour bar shows the scalar range.
  • "Reset view": fit the camera to the data. Mouse: rotate (left), pan
    (middle/shift), zoom (wheel).

What it writes (per dump)
  • point data "magnitude" = |vector field| (the active scalar), and
  • point data "field" = the vector (real part) for glyphs/streamlines.

Notes
  • A dump must cover a volume or plane (Edit Dump: start != stop). A single
    point yields a useless 1-point grid and is skipped.
  • The files are plain .vtr — they open in ParaView too.
  • Needs a working OpenGL (a normal desktop session); in a headless/remote
    session the 3D view may be unavailable but the .vtr files are still written.
""",
    "Antenna Simulation": """ANTENNA SIMULATION — settings that keep FDTD results trustworthy

FDTD antenna results are sensitive to the mesh, the boundary placement and
the material data. The checks below are the usual causes of a shifted
resonance frequency or a wrong efficiency / gain. Enable Simulation
Parameters (Ctrl+Shift+P) → "Antenna simulation mode" to apply the mesh
density, PML distance and NF2FF defaults of this checklist in one click.
While it is ON it OVERRIDES the Mesh-tab resolution: the density selected in
the antenna group is enforced with the reference wavelength taken at f_max
(the highest band frequency), so the whole band is resolved — not just the
design frequency.

1. Mesh density — numerical dispersion
   A coarse grid makes waves travel artificially slowly in the solver, which
   pushes the resonance frequency DOWN.
   • Rule of thumb: at least 15–20 cells per wavelength (λ) at the HIGHEST
     frequency you simulate. (Mesh presets: low ≈ 10, medium ≈ 15,
     high ≈ 20, very_high ≈ 25, extreme ≈ 40 cells/λ — pick "extreme" in the
     antenna group for a CST-grade convergence check.)
   • Use the one-third/two-thirds rule for smooth transitions between cell
     sizes (Mesh → Smooth metal edge = one_third_two_thirds).
   • "Refine mesh at metal interfaces (graded)" (Mesh Parameters, on for new
     projects) inserts graded lines on the air/substrate side of every metal
     face — finest next to the metal, coarsening with distance by the Max
     grading ratio. Use it when the field concentrates at a surface; turn it
     off if the added cells are not worth the runtime.
   • Avoid relying on pure automatic meshing alone — it often under-resolves
     the critical edges of the radiator. Check the result in the Mesh Viewer.
   • Where a small feature forces a very small cell (feed gap, thin wire,
     high-εr layer), the Courant-stable time step shrinks with it and the run
     needs far more steps — resolve the feature, but do not refine the whole
     domain to the same size.

2. Edge resolution — sub-pixel effect
   openEMS snaps geometry to the Yee grid. If the resonant structure is not
   exactly on mesh lines it is rounded to the nearest cell; if the structure
   is thereby interpreted as slightly LARGER, the resonance shifts down.
   • Place explicit mesh lines exactly on the physical edges of the
     resonant structure (Mesh Overrides → Num lines (edge gaps), and
     Smooth metal edge / closely-placed-edges handling).
   • Verify in the Mesh Viewer that grid lines pass through the patch edges,
     the feed gap and thin substrates — the "Domain overview" and cell-size
     plots show where you are coarse.

3. Substrate permittivity (εr)
   εr is normally entered as a constant, but real substrates (FR4, Rogers…)
   are dispersive — the value depends on frequency.
   • Check that the εr you assigned is correct for YOUR TARGET FREQUENCY.
   • Too high an εr shortens the effective wavelength in the material and
     makes the structure resonate LOWER.
   • Losses matter too: a missing loss tangent (tand / kappa) inflates Q and
     efficiency, and a metal modelled as a perfect electric conductor (PEC)
     reports 100 % radiation efficiency by definition.

4. Boundary distance — PML too close
   If the absorbing boundaries sit too close to the radiator, its near field
   interacts with them and adds parasitic capacitance, again pulling the
     resonance down.
   • Keep at least λ/2 to λ — measured at the LOWEST frequency of the band —
     between the structure and the simulation boundary.
   • Simulation Parameters → "Air margin auto" uses λ/4 at the STOP
     frequency, which is smaller than λ/2 at f_start. Antenna simulation
     mode replaces it with the λ/2 rule (configurable as a multiple of λ).
   • The black dashed frame in the Mesh Viewer is the domain; too close →
     wrong resonance, too far → far more cells (RAM and runtime).
    • Prefer PML on the radiation sides; PEC is usually fine on a ground
      plane / symmetry face.

5. NF2FF box — far-field integration surface
   The NF2FF post-processing integrates the tangential fields over a closed
   box around the radiator. That surface must sit in the FAR FIELD — roughly
   λ/2 or more from the structure. Inside the reactive near field (~λ/2π) the
   Prad / Dmax / gain come out wrong even though the plot looks plausible.
   • Simulation Parameters → "Antenna simulation mode" → "NF2FF box" sets the
     distance in λ at f_start; "Fit to geometry + air margin" applies it and
     clamps the box so it stays strictly inside the PML.
   • PCB / non-antenna runs keep the small λ/4-at-f_stop box: there the box is
     just an energy/field probe, not a far-field surface, so tight is fine.
   • The box must enclose ALL sources and keep all six faces; a box that is
     too small (or clamped to a PEC domain face) yields Prad≈0 or a wrong Dmax.
   • Centre rule (important): openEMS expands the far field on a sphere around
     nf2ff.center. That centre MUST equal the box centre (start+stop)/2 —
     otherwise the surface integral is evaluated off-centre and the pattern
     comes out distorted even though the plot looks fine (a dipole then reads
     e.g. Dmax 3.3 dBi / 45° beamwidth instead of 2.1 dBi / 78°).
     "Fit NF2FF to Geometry" sets the centre to the box centre; a run warns and
     snaps it back if a project still carries a stale centre.
   • Equal margins: give the box the SAME stand-off on all six faces (centred
     on the geometry). A one-sided box sits close to the structure on the near
     faces (reactive near field) and tilts the pattern. This is a recommendation
     (fit does it for you); the centre rule above is the hard requirement.
   • Only one box: the FDTD domain (with its PML) and, inside it, the NF2FF box.
     The NF2FF box must stay clearly inside the domain — too close to the PML
     and reflections contaminate the pattern.

General FDTD limitations to keep in mind
   • Cost and memory — the whole 3D volume (antenna + free space) is
     discretized; electrically large or fine-featured models can run from
     hours to days. Use threads (Simulation Parameters) and start coarse.
   • Open-boundary approximation — antennas radiate into infinite space but
     FDTD needs a finite box; imperfect absorbers reflect energy back into
     the domain and contaminate low-frequency or high-Q results.
   • Staircasing — curved or diagonal geometry (circular patches, wire
     bends) is approximated by rectilinear cells, which shifts resonance and
     gain unless you refine or use conformal/sub-gridded FDTD.
   • Fine feature vs. domain size — a tiny feed gap drives Δx down, Δt down
     (Courant) and the step count up; keep the fine cells local.
   • Narrowband vs. time domain — for simple, high-Q, narrowband metal
     antennas a frequency-domain solver (MoM / FEM) is often much faster,
     because it only solves the frequencies of interest instead of marching
     through thousands of time steps. FDTD pays off when you need wideband
     S-parameters, transients or field distributions in one run.

Good antenna workflow in this app
   1. Mesh → preset "high" or better (≥ 20 cells/λ) and build; review the
      grid in the Mesh Viewer before running.
   2. Simulation Parameters → boundary: PML on radiation faces, air margin
      ≥ λ/2 at f_start (Antenna simulation mode does this for you).
   3. Assign the real substrate εr (and tand) for your target frequency.
   4. Run, then check S-Params: the S11 minimum should sit at the design
      frequency; if it is low, re-check items 1–4 above.
   5. Antenna tab (NF2FF enabled) → directivity, gain, efficiency, pattern.
""",
}

_DEFAULT_HELP = """No help defined for this tab yet."""


def help_for(tab_title: str) -> str:
    return TAB_HELP.get(tab_title, _DEFAULT_HELP)


class TabHelpDialog(QDialog):
    """Dialog showing help for one tab; combo switches tabs without reopening."""

    def __init__(self, parent: QWidget | None, initial_tab: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Help")
        self.resize(640, 520)
        root = QVBoxLayout(self)

        row = QComboBox()
        row.addItems(TAB_ORDER)
        if initial_tab in TAB_ORDER:
            row.setCurrentText(initial_tab)
        elif initial_tab:
            # unknown tab name — still show its content if present
            row.addItem(initial_tab)
            row.setCurrentText(initial_tab)
        root.addWidget(QLabel("Tab"))
        root.addWidget(row)

        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.viewer.setFont(self.viewer.font())
        root.addWidget(self.viewer, 1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        root.addWidget(bb)

        row.currentTextChanged.connect(self._reload)
        self._combo = row
        self._reload(row.currentText())

    def _reload(self, title: str) -> None:
        self.viewer.setPlainText(help_for(title))


def show_tab_help(parent: QWidget | None, tab_title: str) -> None:
    dlg = TabHelpDialog(parent, tab_title)
    dlg.exec()


def make_help_button(tab_title: str, on_click) -> QPushButton:
    btn = QPushButton("Help")
    btn.setToolTip(f"Help for the {tab_title} tab")
    btn.clicked.connect(on_click)
    return btn


def inject_help_button(page: QWidget, tab_title: str, parent: QWidget | None) -> bool:
    """Insert a Help button at the top of a tab page that already has a layout.

    SimParamsPanel / SParamViewer / MeshGeometryViewer are panels — this adds
    a slim header row. Returns True if a button was inserted.
    """
    layout = page.layout()
    if layout is None:
        return False

    from PySide6.QtWidgets import QHBoxLayout

    header = QWidget()
    hl = QHBoxLayout(header)
    hl.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(tab_title)
    lbl.setStyleSheet("font-weight: bold;")
    hl.addWidget(lbl)
    hl.addStretch(1)
    hl.addWidget(make_help_button(tab_title, lambda _=False, t=tab_title: show_tab_help(parent, t)))

    # Insert as first child where possible
    if hasattr(layout, "insertWidget"):
        try:
            layout.insertWidget(0, header)
        except Exception:  # noqa: BLE001
            layout.addWidget(header)
    else:
        layout.addWidget(header)
    return True
