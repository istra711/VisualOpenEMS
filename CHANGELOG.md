# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Antenna pattern 3D overhaul** (`app/ui/antenna_viewer.py`): default dBi
  window max−30 dB with "−30 dB" / "Full" buttons, pre-shaded smooth surface
  (no per-frame re-lighting), orthographic projection, adjustable θ/φ
  reference graticule (right-click → "3D reference grid": on/off, strength,
  line width, density), Gmax marker/label, radius modes dB / linear / √G,
  view presets
  (iso/front/top/side) with rotation-preserving redraws, optional wireframe,
  fixed colorbar and 300 dpi PNG/PDF/SVG export. The dBi-slider rebuild is
  debounced so dragging stays responsive.
- **Settings → Antenna 3D viewer**: quality/speed preset "fast", "balanced"
  (default) or "high" (`AppSettings.antenna_3d_quality`); applies immediately
  to an open 3D pattern and persists across restarts.
- **Marker visibility**: a "show" checkbox above the marker lists in the
  Antenna and S-Parameter viewers hides/shows the marker dots/cursors without
  deleting them; S-parameter markers remain in the project data.
- **3D Design mesh plane**: the built simulation mesh can be overlaid as a
  section grid in the 3D Design viewer. The plane is selectable as XY / XZ /
  YZ and moved with a slider (one step per mesh line, snapped to the built
  grid); it follows a mesh rebuild.
- **3D Design mesh-plane colour**: Viewer settings → Mesh colours now exposes
  "3D Design mesh plane"; the default is white (`#ffffff`) and the colour is
  applied live to the docked viewer and its pop-out.
- **High-contrast object mode** (right-click in the Field Viewer and 3D Design
  viewer): geometry is drawn more opaque with a bright 2 px outline and flatter
  lighting so low-contrast objects stand out against the dark background.
  Optional and off by default; the state is copied to the viewer pop-out.
- **Mesh grid opacity** (right-click in 3D Design → "Mesh grid opacity"):
  presets 100 / 90 / 75 / 50 / 25 / 10 % for the section grid; default is now
  25 %. Changes apply live and are copied to the pop-out.

### Fixed

- **Mesh Viewer slice focus for flat polygons**: selecting a copper trace or
  other polygon now focuses the slice at the polygon's real elevation instead of
  the middle of a stale CAD bounding box. A flat trace at z=1.651 mm was
  previously focused at 0.8255 mm ("not in this plane"); it is now visible
  immediately. Polygon highlights also use the true plane for the in-slice test.
- **3D Design mesh-plane visibility**: the mesh section grid is drawn in a
  transparent layer-1 renderer above the design scene, so opaque geometry no
  longer hides it. It shares the main camera and remains visible while rotating,
  panning and zooming.
- **3D Design mesh-plane availability**: opening the 3D Design tab now loads
  the project's recorded mesh (without starting a new build), so the mesh-grid
  controls are enabled immediately; if nothing exists the hint now points to
  the Mesh Viewer ("build mesh first (Mesh Viewer)").
- **Tab switching keeps viewer state**: switching back to the 3D Design tab no
  longer refits the camera, and the Field Viewer only re-pushes its geometry
  when the project/primitives actually changed. Camera zoom/pan, slice, colour
  and mesh settings now survive switching between Antenna, Field Viewer and
  3D Design.
- **Coplanar copper traces**: metal (copper/PEC) geometry receives a negative
  polygon/line depth offset, so zero-thickness traces lying exactly on the
  substrate surface are no longer partly hidden by z-fighting. The trace
  reliably wins the depth test without changing its coordinates.
- **3D Design selection highlight for flat polygons**: the yellow selection
  outline now uses the real polygon plane box (the same box as the tree and
  Mesh Viewer). The stale 0…z CAD box could make a selected trace appear to
  highlight nothing.

## [0.10.0] - 2026-11-07

### Added

- **Animated GIF export** for the Field Viewer, alongside the existing MP4
  export: the "Video…" dialog now also offers `*.gif` (Pillow, no ffmpeg),
  plus a separate Simulation → "Export field animation (GIF)…" action and a
  right-click menu entry. Same current-view rendering and `fps` as MP4.
- **Embedded 3D Design viewer**
  (`app/ui/design_viewer.py`): new *3D Design* tab and Simulation menu action.
  Reuses the Field Viewer's VTK pane for rotating/panning/zooming the project
  geometry (including STL), with per-object colours/transparency/visibility,
  a global geometry opacity, domain-box, port-box and NF2FF-box overlays,
  standard views, fit/reset and a separate pop-out window. Selecting a
  primitive or port in the Project / Geometry tree highlights it with a yellow
  3D outline, matching and toggling with the Mesh Viewer selection. A
  right-click menu offers the X/Y/Z coordinate axes, axis labels and a numeric
  coordinate ruler with adjustable size, anchor location, label size and a
  reset, selectable ruler placement (closest/farthest/outer/static) and tick
  side (outside/inside/both), an optional small fixed-size corner triad in the
  lower-left, an optional tick tape directly on the X/Y/Z arrows (X/Y/Z
  selectable), plus a two-click tape measure that reports the distance in the
  project drawing unit. The side panel is compact (≈145–165 px)
  so the 3D view gets the space. The view is read-only and follows
  geometry/port/NF2FF edits automatically. A small
  scene API (`set_design`, `add_overlay_actor`, `remove_overlay_actors`,
  `set_clip_plane`, `pick_at`) is the base for later measurement, picking and
  section-plane tools.
- **3rd-order hairpin band-pass example, 2440 MHz on FR4**
  (`examples/run_pcb_hairpin_3pol_example.py`,
  `docs/HAIRPIN_3POL_FR4_DE.md`): rebuilds the filter from J. Horton,
  "Designing PCB Filters using MATLAB" (2021) as an openEMS project — 3-pole
  0.1 dB Chebyshev, f0 = 2440 MHz, FBW 10 % (2318–2562 MHz), FR4 (εr 4.5,
  tan δ 0.02, 1.6 mm, 35 µm Cu). The layout follows the MATLAB `filterHairpin`
  rules so the board matches the article's photos: three folded λ/2 resonators
  with **alternating folds**, a **uniform 0.5 mm spacing** (the `design`
  default) and **tapped 50 Ω feed lines** entering from the board edges at
  50 % of the arm length (MATLAB `FeedType = "Tapped"`). 50 Ω line 3.011 mm,
  arm 15.369 mm (FDTD-calibrated `ARM_SCALE` 1.031; the Hammerstad λg is ~3 %
  short here — a single 2-port hairpin peaks at 2.541 GHz at scale 1.0 and at
  2.441 GHz at 1.031), inner arm gap 2.0 mm, feed line 5 mm — visually the
  same meander layout as the printed boards. Writes
  `work/hairpin_fr4_2440_3pol/` (project, `project_run.py`, `port2/`), offers
  `--design` / `--skip-run` / `--mesh-only`, and merges both excitations into
  `sparams/sparams.s2p` on a full run. Mesh: 149 × 95 × 14 = 198 170 cells
  (run script 216 000), Δt = 0.174 ps; the 1e-4 end criterion stops a run
  after ~5 ns (DFT ≈ 185 MHz). Manual mesh lines only at the metal x-faces and
  in z — an interior x/y fill landed next to easyMesh's own lines and created
  5–40 µm cells (Δt 0.017–0.13 ps instead of 0.174 ps). The doc covers the
  dimensions, why the uniform 0.5 mm spacing over-couples (k ≈ 0.25 vs
  0.34/0.14 for 10 %, giving the same broad rippled band as the article's
  measured boards), the tap-position trade-off (0.75 → Qe ≈ 10), mesh
  warnings, validation status (geometry/mesh verified, single resonator on
  2440 MHz, 3-pole probe run shows the split modes) and the article's
  εr-dispersion measurement finding.
- **Optimized hairpin 3-pole variant for 2.44 GHz on FR4**
  (`examples/run_pcb_hairpin_3pol_example.py --optimized`,
  `docs/HAIRPIN_3POL_FR4_DE.md` §11): same look as the article rebuild
  (tapped feeds, alternating folds) but with the coupling and external Q of
  the 0.1 dB Chebyshev prototype instead of the MATLAB defaults — 0.70 mm
  uniform gap between the resonators and the tap at 65 % of the arm
  (FDTD-calibrated arm 17.754 mm, `ARM_SCALE` 1.191). Writes
  `work/hairpin_fr4_2440_3pol_opt/`. Single-excitation FDTD (end criterion
  1e-6): **S11 −20.4 dB / S21 −2.06 dB at 2.44 GHz**, the ISM band
  2.400–2.4835 GHz covered with S11 < −13 dB and 2.1–2.3 dB insertion loss,
  −3 dB band ≈ 2.36–2.59 GHz. The article-faithful default (0.5 mm, mid-arm
  tap) stays available and is documented as over-coupled (S11 ≈ −1 dB,
  S21 ≈ −18 dB at 2.44 GHz). Coupling/tap were tuned against FDTD: gap 1.357
  → 5 % band, 0.70 → ≈ 9 %; a 0.5 mm gap makes the mesh so fine that the run
  becomes impractical (killed after 30 min).
- **CST Studio export-package import (Variant A)**
  (`app/freecad_io/cst_import.py`, `app/ui/cst_import_dialog.py`):
  File -> *Import CST Studio Package...* and Geometry -> same action import a
  CST export folder or ZIP containing `manifest.json` plus one STL per solid.
  The import previews objects, materials, ports, frequency range and boundary
  conditions, converts CST's metre STLs to the project drawing unit, keeps
  names such as `PEC/trace1`, and stores the options in `Project.cst`
  (`CstSpec`). Re-imports replace only primitives from the same package and
  preserve manual material/priority overrides. Floquet/unit-cell ports,
  dispersive/anisotropic materials, CST mesh/schematic/field-monitor data are
  reported as warnings rather than approximated. CST waveguide ports that lie
  on a planar substrate with a thin PEC trace are recognized and imported as
  MSL ports; true hollow-waveguide ports stay `rect_waveguide` and zero-length
  port planes are extended for openEMS. The macro now also exports the CST
  calculation/structure box; the importer converts it into the project's six
  per-side air margins, preserving PEC walls that act as a ground plane when a
  model has no explicit ground layer. The Simulation Parameters dialog now has
  six editable per-side air margins (disabled in auto/global mode) and PCB
  presets for an open board or a sheet-metal enclosure (x/y/zmin=0, editable
  lid height as zmax). A matching VBA macro is included at
  `app/freecad_io/cst_export_macro.bas`; the package schema and limitations
  are documented in `docs/CST_IMPORT_DE.md`. Covered by
  `tests/test_cst_import.py` (folder, ZIP, metre-to-mm/um conversion, ports,
  MSL conversion, boundary distances, materials, re-import and persistence).
- **Gerber/Excellon import foundation** (`app/freecad_io/gerber_import.py`):
  reads a KiCad/Altium/Eagle-style Gerber + drill folder or ZIP with
  `gerbonara`, vectorizes every copper layer via `shapely`, and returns native
  CSXCAD primitive dictionaries (`polygon` sheets/prisms and `cylinder` vias).
  The caller supplies the stackup (board/copper thickness, substrate material,
  via diameter threshold), because Gerber itself carries no z information.
  Covered by `tests/test_gerber_import.py` with a generated 2-layer set,
  copper holes, PTH/NPTH drills and ZIP input.
- **Gerber stackup dialog + project integration** (`app/ui/gerber_import_dialog.py`):
  File → *Import Gerber/Excellon…* and Geometry → same action import a folder,
  ZIP or single file. The dialog collects board/copper thickness, εr/tanδ,
  per-layer z overrides, via threshold, NPTH and precision options. The import
  replaces only primitives from the same source, preserves manual material
  overrides by name, creates/updates the substrate material and stores every
  option in `Project.gerber` (`GerberSpec`) in the `.fcsim.json` file.
- **Gerber phase 3: via spans, slots and solder mask**:
  `GerberSpec.via_spans` assigns blind/buried spans per drill diameter (Gerber
  itself has no per-via layer data); Excellon milled slots (`G85`/routed
  Line/Arc objects) become extruded copper or vacuum polygons; optional top/
  bottom solder-mask layers are imported as a thin dielectric (board outline
  minus mask openings) with their own εr/tanδ. The dialog exposes all three,
  and the importer/tests cover a generated 4-layer stackup with a slot, a
  blind via and mask openings.
- **Native KiCad `.kicad_pcb` import** (`app/freecad_io/kicad_import.py`):
  parses KiCad 6/7/8 S-expressions directly, including copper layers, custom
  stackup (thickness, εr, tanδ), tracks/arcs, SMD/through-hole/NPTH pads,
  filled zones and exact via spans from `(via ... (layers ...))`.  The board is
  centred, optionally Y-mirrored to match Gerber top views, and emitted as the
  same CSXCAD primitives as the Gerber path.  File → *Import KiCad PCB…* opens
  the stackup/options dialog; `Project.kicad` (`KiCadSpec`) stores the settings.
  Tests cover a generated 4-layer board (via span, pad barrels, zone, outline)
  and fallback stackup behaviour. Copper can be imported as zero-thickness
  sheets, extruded prisms, or one STL/Polyhedron per copper layer; the mesh
  mode collapses large filled zones with many cutouts from thousands of
  AddPolygon primitives to one PolyhedronReader primitive per layer.
- **Automatic KiCad port suggestions** (`app/freecad_io/kicad_ports.py`):
  derives lumped/MSL/probe candidates from explicit footprint properties
  (`openems_port`, `openems_port_type`), RF/port-like net names, connector
  footprints, pad/track topology and Edge.Cuts proximity.  A confirmation
  dialog (`app/ui/kicad_ports_dialog.py`) lets the user rename, change the
  type, choose the excited port and drop candidates before they become
  `PortDef` entries.  The KiCad import dialog has an "suggest ports after
  import" option; Geometry → *Ports from KiCad nets…* re-runs the suggestion
  for the current board.
- **Automatic Gerber/Excellon port suggestions** (`app/freecad_io/gerber_ports.py`):
  reads Gerber X2 object attributes (`.Net`, `.ComponentRef`, `.Pin`) and an
  optional IPC-D-356 netlist to derive the same port candidates as the KiCad
  path.  Without net data it falls back to pads near `Edge.Cuts` with a
  deliberately low confidence.  The Gerber import dialog has a "suggest ports
  after import" option; Geometry → *Ports from Gerber…* re-runs it.  The shared
  confirmation dialog lets the user review/rename/change type/excite before the
  candidates become `PortDef` entries.
- **Geometry menu: Add Extruded Polygon** (`app/ui/main_window.py`,
  `app/ui/geometry_editors.py`): opens the polygon point editor with a
  positive extrusion length and the title *Extruded polygon primitive*,
  producing a solid CSXCAD `AddLinPoly` prism instead of a flat `AddPolygon`
  sheet.  The normal *Add Polygon* entry remains the zero-thickness sheet;
  editing an existing polygon keeps its length.  The dialog defaults to
  `extruded_poly1` with 1.0 drawing-unit thickness and still permits switching
  back to a sheet by setting Length to 0.
- **Geometry menu: Wire and Curve primitives** (`app/ui/main_window.py`,
  `app/ui/geometry_editors.py`, `app/meshing/build_task.py`,
  `app/simulation/script_gen.py`): new *Add Wire...* and *Add Curve...*
  entries edit 3-D polylines (`x, y, z` per line, >= 2 points).  Wire emits a
  CSXCAD `AddWire` with an editable radius; Curve emits a zero-thickness
  `AddCurve`.  Both persist as project primitive dicts, contribute their real
  points to the Mesh Viewer/snapping and coordinate diagnostics, are blocked in
  cylindrical mode, and are covered by `tests/sphere_polygon_smoke.py`
  (dialog, mesh-only and FDTD run) plus the Geometry-menu smoke checks.
- **Mesh Viewer: view controls moved to the plot's right-click menu**
  (`app/ui/mesh_viewer.py`): the second toolbar row is gone. *Mesh grid*,
  *Grid strength* (0-100 % presets), *Geometry*, *Ports*, *NF2FF*, *Mesh
  editor* and *Pop-out viewer* now live in the right-click menu of the plot
  area; selecting a grid strength also enables the mesh grid. The hidden
  widgets remain as state/signal targets for saved projects and tests, while
  the visible toolbar keeps only the slice navigation (view, plane, plane
  slider). Help text updated.
- **Mesh Viewer: Snap sensitivity in the right-click menu**
  (`app/ui/mesh_viewer.py`): the menu now has a checkable *Snap sensitivity*
  submenu.  *Zoom-relative (default)* measures the band against the currently
  visible slice axis, so the snap distance stays the same on screen at every
  zoom level.  The same reference span now also sizes the narrow band that
  selects an existing mesh line (previously domain-relative, which made a
  strongly zoomed slice select a far-away line).  Turning it off switches to
  domain-relative snapping; the
  presets *Off* (no edge snapping), *Fine* (0.5 %), *Normal* (1 %), *Wide*
  (2.5 %, at least 0.05 mm) and *Very wide* (5 %, at least 0.1 mm) then use the
  full simulation domain.  The absolute minimums apply only in domain-relative
  mode.  The chosen value/mode is used by the next Mesh-Editor click and logged
  in the main window.  The submenu is disabled while the Mesh editor is off
  (Port mode never snaps).
- **Mesh Parameters: PCB mesh preset** (`app/ui/mesh_params.py`,
  `app/meshing/rules.py`): a *PCB preset* button next to the mesh-preset combo
  fills frequency-aware mesh overrides for typical circuit boards - high air
  density (20 cells/λ), max cell 0.5 mm (capped at λ/20 for high frequencies),
  refined cell 0.25 mm, min cell 0.05 mm on all axes, grading 1.3, circle/arc
  detection, closely placed edges and graded metal interfaces.  Values are
  converted to the current drawing unit and only written on **Apply**; air
  margin and PML size remain in Simulation Parameters.
- **Mesh Parameters: explanations on every value** (`app/ui/mesh_params.py`):
  the frequency fields (f0, fc, start/stop), excitation/custom waveform,
  drawing unit, coordinate system, all cell-size overrides, grading, edge
  options and smoothing now have tooltips on both the input and its left-hand
  label. The dialog header says so, and the Mesh Parameters help topic repeats
  the hint. This makes f0/fc vs. start/stop, the λ/20 max-cell cap and the
  min/refined/max relationships visible without opening the full help.

### Changed

- **Mesh Parameters: three checkmarked presets** (`app/ui/mesh_params.py`,
  `app/meshing/rules.py`, `app/models/project.py`): the old density dropdown +
  “PCB preset” button is replaced by exclusive checkboxes for **Default**,
  **Antenna** and **PCB**. Default uses 15 cells/λ with automatic sizes and
  grading 2.0; Antenna uses 20 cells/λ, 0.1 mm min cell, grading 1.5, circle/arc
  detection and graded metal interfaces; PCB keeps the frequency-aware 0.5 mm
  max / 0.25 mm refined / 0.05 mm min profile with grading 1.3. The active
  profile is stored as `mesh.mesh_profile`; old projects are recognised by the
  PCB override fingerprint or the antenna-mode flag. The Mesh Parameters help
  text and tooltips were updated accordingly.
- **3D Design ruler follows visible bounds** (`app/ui/design_viewer.py`): when
  the **Domain box** overlay is switched off, the numeric ruler now snaps to
  the union of the visible geometry and port boxes instead of staying on the
  hidden FDTD domain. `_scene_bounds_m()` and `_scene_diagonal()` gained an
  `include_domain` switch; the in-scene triad and camera fit now follow the
  same visible bounds, but the out-of-scene corner marker stays fixed. Empty
  scenes fall back to the domain bounds so the ruler does not disappear.
  Tooltip, 3D Design help and `tests/design_viewer_smoke.py` were updated.
- **3D Design shows exactly one coordinate ruler** (`app/ui/design_viewer.py`):
  the inherited Field-Viewer ``_apply_axes()`` added its own cube ruler/triad
  on top of the design-specific ruler, so a flat PCB showed one axis set at the
  geometry and another at the domain bounds. The design viewer now overrides
  ``_apply_axes()`` (corner marker only) and defensively removes inherited
  ruler/triad actors before redrawing. Domain box on -> one ruler at the
  domain; off -> one ruler at the geometry. Checked by
  `tests/design_viewer_smoke.py` (exactly one ``vtkCubeAxesActor``).
- **3D Design hides overlapping ruler numbers** (`app/ui/design_viewer.py`):
  each axis' numbers and minor ticks are hidden while its projected length is
  shorter than a font-dependent threshold (~8 glyph heights, ~192 px at font
  24). The axis line and title stay visible, so the ruler still shows the
  correct extent. The check runs on redraw, camera fit and after camera
  interaction / window resize; zooming in / enlarging the window brings the
  numbers back. Checked by `tests/design_viewer_z_labels_smoke.py` (small
  viewport hides all axes, 1200×800 shows them again, 0.1 mm Z stays hidden).
- **Field Viewer drops old geometry when projects change**
  (`app/ui/vtk_viewer.py`, `app/ui/main_window.py`): `clear()` now also drops
  the `_geom_prims` / `_geom_actors` ghosts, `set_work_dir()` removes old
  ghosts when the work folder changes, and `_load_last_results()` pushes the
  opened project's primitive list into the Field Viewer before restoring a
  dump. Opening a project without field results therefore no longer leaves the
  previous PCB/antenna visible. Covered by `tests/vtk_geometry_reset_smoke.py`.
- **Animated GIF export uses milliseconds** (`app/ui/vtk_viewer.py`,
  `app/postproc/video_timing.py`): imageio's Pillow GIF writer passes
  ``duration`` to Pillow, which expects **milliseconds**, not seconds. The old
  ``1.0 / fps`` value was rounded to zero, so 5 fps and 10 fps both played at
  Pillow's default ~100 ms. GIF frames now use
  ``max(20, round(1000 / fps))`` ms; MP4 is unchanged. Covered by
  `tests/test_gif_timing.py` and the 10-fps duration assertion in
  `tests/vtk_viewer_smoke.py`.
- **Field Viewer video-export options dialog** (`app/ui/video_export_dialog.py`,
  `app/ui/vtk_viewer.py`): the Video button / menu entries now open a dialog
  for MP4 vs animated GIF, output path, resolution (current view, 1920×1080,
  1280×720, 960×540, 640×480/VGA, 640×360, 480×480, 320×240/QVGA, 1080×1080,
  1080×1350 or custom), fps, target duration, and the
  start/end frame range including every-Nth-frame. The dialog previews the
  resulting frame count and duration; the exporter accepts the range, renders
  each frame at the chosen resolution (letterboxed, no scene crop) and keeps the
  existing MP4/GIF backends. Direct `export_video(path=...)` calls remain
  supported. Covered by the ranged-export assertion in
  `tests/vtk_viewer_smoke.py`.
- **Smart S-parameter markers via right-click** (`app/ui/sparam_viewer.py`):
  right-clicking a plot now opens a marker menu instead of only the analysis
  dialog: add marker at the clicked point, trace minimum/maximum, nearest
  local peak/valley, the two -3 dB points around the primary trace's peak
  (transmission) or null (reflection), band center, clear markers, analysis
  dialog and pop-out graph. Markers added by these functions are tagged in the
  marker list (`S21 max`, `S11 -3dB low`, `S11 valley`, `band center`, …).
  Clicking a marker row now selects/highlights that marker in the plot (bold
  black line; larger gold-edged dot on the Smith chart). A selected marker can
  be used as the reference for a **delta marker** (right-click → “Delta marker
  here (from selected)”): the list shows the reference/target frequency, Δf
  and the dB difference only, and the plot uses a violet dash-dot line /
  diamond. Deleting the reference removes its dependent delta markers.
  Right-clicking a marker row offers Copy marker value / Copy frequency
  (clipboard) / Delete. Marker frequencies, tags and delta references are
  stored in `Project.sparam_markers` and therefore in the project
  `.fcsim.json`; reopening the project restores them. The analysis dialog
  remains available from the same menu. Covered by
  `tests/sparam_marker_smoke.py`.
- **Unsaved Viewer settings prompt** (`app/ui/main_window.py`): closing the
  Viewer settings dialog (Cancel or window close) after changing a control /
  using Preview now asks “Discard these changes?” with Discard/Cancel instead
  of silently reverting. A clean dialog still closes immediately. Implemented
  with a small `_ConfirmDiscardDialog` subclass so both the Cancel button and
  the window close path are covered.
- **Unsaved project prompt when closing the main GUI** (`app/ui/main_window.py`):
  the main window now keeps a serialized baseline of the project (settings,
  primitives, S-parameter markers, …). Closing after a marker was set or a form
  setting was changed asks **Save / Discard / Cancel**. Save writes the project
  (including `sparam_markers`) and only then closes; a cancelled Save As keeps
  the window open. Opening a project without loaded results no longer erases
  its saved markers. Covered by `tests/main_window_close_prompt_smoke.py`.
- **Field Viewer defaults: coordinate ruler + corner triad + isometric view**
  (`app/ui/vtk_viewer.py`, `app/settings.py`): a fresh profile now opens a
  field with the numeric coordinate ruler, the ParaView-style corner triad and
  axis labels (font 24, 2× triad size, labels on the far bbox edge) and the
  **Isometric** standard view. That is the display the user asked to make the
  default; previously the in-scene triad, no ruler and the "keep" view were the
  defaults. Saved `app_settings.json` values still win. `DEFAULT_AXES`
  (corner=True, ruler=True, font=24, scale=2.0, origin_back=True) and
  `AppSettings.field_viewer_view="iso"` changed; `tests/vtk_viewer_smoke.py`
  and `tests/test_settings.py` updated to the new defaults.
- **3D Design viewer uses the same axes defaults as the Field Viewer**
  (`app/ui/design_viewer.py`): corner triad (bottom-left) instead of the
  in-scene arrows, coordinate ruler, X/Y/Z labels, font 24 and 2× size; the
  in-scene arrows come back when "Corner axes (bottom-left)" is unchecked, and
  "Reset coordinate options" restores these defaults. The standard view is
  applied as a mode (isometric by default) on the first render and on explicit
  fits. `tests/design_viewer_smoke.py` updated (corner/ruler/font/scale after
  reset, isometric camera).

### Fixed

- **Coordinate-ruler units and duplicate axes** (`app/ui/vtk_viewer.py`,
  `app/ui/design_viewer.py`): the Field Viewer ruler now follows the project's
  drawing unit (mm for PCB projects instead of a global "m" default); the
  unused axes of the split z / x-y `vtkCubeAxesActor`s are hidden completely,
  so no second x/y ruler appears; and a planar dump (z extent ~ 0) no longer
  draws a bogus `z [m] (×10⁻⁶)` ruler. The 3D Design viewer uses the same
  metre-to-unit mapping for its axis captions and ranges.
- **3D Design tab kept a stale or rolled camera**
  (`app/ui/design_viewer.py`): `_render_design()` always fitted with the
  "keep" preset, so opening the tab (or the first render after a geometry
  import) kept whatever camera direction was there - a top or rolled view
  even though the View combo showed "Isometric". The selected standard view
  is now applied as a mode on the first render and on explicit fits;
  "Fit (keep direction)" still only fits. Regression in
  `tests/design_viewer_smoke.py` (first render with `iso` -> camera
  direction (0.577, 0.577, 0.577)).
- **Field Viewer: axes defaults ignored on a geometry-only scene**
  (`app/ui/vtk_viewer.py`): the triad / corner marker / coordinate ruler were
  only drawn from `_refresh_display`, which returns early without a loaded
  field grid — so after a CAD geometry import none of the configured defaults
  (corner triad, ruler, "iso" view) appeared, although the options were set.
  `_apply_axes()` now handles the geometry-only case: `_scene_bounds()` falls
  back to the ghost actors, the in-scene triad and the ruler are drawn around
  them, `_update_corner_marker()` no longer requires a grid, the ruler actor is
  tracked and replaced instead of stacking on repeated imports, and
  `set_geometry()` applies the configured standard view when no dump is loaded.
  Deeper cause of the same report: the VTK pane itself was only created in
  `load()` (the render window is lazy), so a geometry import without any field
  left the viewer empty and the options had no target at all. `set_geometry()`
  now creates the pane when it is already visible and otherwise keeps the
  primitives for `showEvent()`, which builds the pane and draws ghosts + axes +
  view as soon as the tab is opened. A geometry-only scene also treats the
  “keep” preset as isometric: there is no previous direction to keep, so VTK
  fell back to its top view - that was the remaining “axes there, view wrong”
  symptom (the app had rewritten the stored
  `field_viewer_view` back to “keep” on exit).
  Regression in `tests/vtk_viewer_smoke.py` (geometry-only viewer: ruler bounds,
  in-scene triad, removal; deferred path: import while hidden → `showEvent`
  draws ruler and ghosts).
- **Field Viewer: "Hide below" looked dead, display panel too wide**
  (`app/ui/vtk_viewer.py`): the vector threshold interpolated on the
  logarithmic colour axis and, when any sampled point was exactly zero
  (common), fell back to `max*1e-9` as the lower anchor. That stretched the
  cut over ~9 decades — the arrow count did not change at all between 0 % and
  50 %, so the control appeared broken (e.g. 72600-point E dump: 57784 glyph
  points at 0 %, 3 %, 30 % and 50 %). The cut is linear again ("share of the
  peak", as the tooltip says), it is applied whenever points are dropped, and
  a threshold that removes everything now returns no arrows instead of
  silently keeping all of them. Default `min_pct` is 0 (was 5). The docked
  display panel is 200 px wide (was 280–320); every section now uses one
  label column (76 px) and one field width (100 px), so all inputs line up
  (combos, sliders and spin boxes start at the same x). The Slice “Position”
  slider spans the full row with its numeric field on the next line (side by
  side the value box was pushed out of the narrow panel); the “seeds” spin
  moved into a form row, and “field underneath” became “field below” — as
  spanning checkboxes both labels forced the panel body wider than the panel
  and clipped it. The full-width rows
  (Auto/Log checkboxes, the “magnitude …” readout, Rescale) live outside the
  QFormLayout: inside it they were squeezed into 104 px and the readout was
  clipped to “magnitude 0 …”. The pop-out dialog can still
  widen it. Regression in `tests/vtk_viewer_smoke.py`
  (0 % vs 50 % must reduce the glyph point count).
- **Field Viewer flickered on every time/frequency step**
  (`app/ui/vtk_viewer.py`): `_refresh_display` cleared the scene, then
  `_draw_geometry()` rendered the geometry-only scene and the field actor was
  added afterwards with a second render — so every slider step (and every
  playback frame) presented a frame without the field in between. That was
  most visible while dragging the time slider over a series. `_draw_geometry`
  now takes `render=False` when called from a refresh, so a refresh ends with
  exactly one render, and a frame change with the same array set skips the
  array-combo rebuild (`_arrays_match_selection` → `_on_array_changed`).
  Regression in `tests/vtk_viewer_smoke.py`: a counting fake interactor
  asserts exactly one render per refresh and per frame step.
- **Mesh Viewer: STL/Polyhedron objects were drawn as bounding rectangles**
  (`app/ui/mesh_viewer.py`): the viewer loaded only the AABB for
  `PolyhedronReader` shapes, so the hairpin filter solids looked like plain
  rectangles even though openEMS was meshing their real triangles. Slices now
  draw the actual triangle-plane intersection and the Domain overview draws
  the projected triangle mesh. The run path already used `PolyhedronReader`;
  boxes remain rectangles and cylinders circles.
- **Unsaved "untitled" project asked to overwrite an old file**
  (`app/ui/main_window.py`): Save As used `work/untitled.fcsim.json`, so a
  stale file made the native dialog ask for overwrite before the project had a
  name. A nameless project now suggests the first free `untitled[_n].fcsim.json`;
  once saved, its real name is stored and normal overwrite confirmation applies.
- **NF2FF box: automatic fit and explicit Auto/Manual placement**
  (`app/models/project.py`, `app/ui/sim_params.py`,
  `app/ui/main_window.py`): enabling NF2FF now fits the integration box to the
  geometry plus air margin instead of keeping the old/default ±1 mm box. A new
  `Box placement` selector offers **Automatic** and **Manual**; `Edit box…`
  switches to Manual, `Fit to geometry + air margin` switches back to
  Automatic. The mode is persisted with the project.
- **NF2FF frame stayed visible after disabling the option**
  (`app/ui/mesh_viewer.py`, `app/ui/main_window.py`): the viewer drew the
  purple box whenever an NF2FF object existed, regardless of `nf2ff.enabled`.
  It now requires the option to be enabled, and toggling the Simulation
  Parameters / Ports+Dumps checkbox updates the frame immediately without a
  rebuild.
- **Run aborted before FDTD with `FileExistsError: ...\simulation`**
  (`app/simulation/script_gen.py`): the generated script pre-created the
  `simulation` directory, while openEMS's `Run(cleanup=True)` expects to create
  it. On Windows an existing/locked directory therefore aborted the run with
  exit code 1. With cleanup enabled the script now removes the old directory;
  if a viewer still locks it, the run uses a fresh sibling directory instead
  of aborting.
- **Mesh Viewer Reset/Home view**
  (`app/ui/mesh_viewer.py`): the toolbar Home button, right-click *Reset /
  Home view*, and double-click now rebuild the current view with fresh
  autoscaled axes. The previous toolbar home history was tied to the old
  Matplotlib axes and did nothing after a redraw/CSX rebuild.
- **Port placement recognizes PEC simulation-box walls**
  (`app/ui/mesh_viewer.py`): click-to-place lumped/MSL ports now treats PEC
  domain faces as metal layers, so a trace above `zmin = PEC` automatically
  gets the ground-side port length even without an explicit ground primitive.
- **Project tree: multi-select Show/Hide in the Geometry pane**
  (`app/ui/main_window.py`): Ctrl/Shift-click now selects several objects;
  the context menu has **Show selected objects** / **Hide selected objects**,
  updating every selected Prim at once and refreshing the Mesh Viewer once.
- **Live runtime and remaining-time display in the status bar**
  (`app/simulation/progress.py`, `app/ui/main_window.py`): the status bar
  shows the current engine wall-clock runtime (`Laufzeit …`) next to the
  remaining ETA (`ETA ~…`).
- **Live remaining-time estimate in the status bar**
  (`app/simulation/progress.py`, `app/ui/main_window.py`): the engine's
  `Speed … (s/TS)` field is parsed during the run; the status bar now shows
  `ETA ~…` at the bottom right, extrapolated as
  `(max_timesteps - current_timestep) × seconds_per_timestep`.
- **NrTS suggestion from the built mesh and DFT window**
  (`app/meshing/timing.py`, `app/ui/sim_params.py`, `app/ui/main_window.py`):
  the *Max timesteps (NrTS)* row now has a **Suggest** button and a hint. It
  derives the required run length from the smallest cell/timestep and the
  201-point S-parameter sweep (including a safety factor and domain-crossing
  minimum), so a too-short DFT window is visible before the run.
- **CST boundary MSL ports get a matched line extension**
  (`app/freecad_io/cst_import.py`): CST waveguide ports at the calculation-box
  edge absorb at the port plane. openEMS MSL ports do not; a PCB ending at the
  boundary becomes an open-circuited microstrip. The importer now adds a short
  trace extension (1× trace width) and extends the substrate two trace widths
  from the port plane, puts PML on that side, and keeps the CST port plane as
  the reference plane.
- **Late-time energy growth with thin copper / high mesh grading**
  (`app/simulation/script_gen.py`, `app/ui/solver_params.py`): openEMS's
  default `Rennings2` timestep was unstable for the CST hairpin mesh
  (0.0178 mm copper line next to a 0.71 mm cell, grading 39): energy grew back
  to 0 dB, S11 exceeded 1 and `Run All Ports` produced unusable data. "auto"
  now selects **CFL (TimeStepMethod=1)** and the generated script additionally
  forces the actual conservative CFL step (including the dielectric
  `sqrt(eps_r)` slowdown) via `FDTD.SetTimeStep`; Rennings2 remains selectable
  explicitly. A CFL test run decayed to about −16 dB before the timestep cap.
- **A failed run with a generated S-param file was reported as "S-params
  missing"** (`app/ui/main_window.py`): the file is now loaded and clearly
  marked **UNTRUSTED** in the viewer (red banner, also mirrored in the pop-out
  graph), and the status report shows its data instead of "Results: none
  written" (the verdict remains ERROR/not trustworthy). Only a run that
  actually wrote no file shows the missing-file warning.
- **Antenna viewer showed old NF2FF data after a run without NF2FF**
  (`app/ui/main_window.py`): opening the Antenna tab auto-loaded any
  `nf2ff/nf2ff.json` left in the reused project folder, even when the current
  run had NF2FF disabled or failed. It now only auto-loads a file produced by
  the current run (or the result recorded for an explicitly opened project);
  New Project and run start clear the viewer.
- **Old S-parameters stayed visible after an aborted run / in a new project**
  (`app/ui/main_window.py`): starting a run now clears the S-param, NF2FF and
  field viewers plus the recorded result paths before the simulator starts.
  A failed run no longer auto-loads the newest (old) `sparams` file from the
  reused work folder, and partial files from a failed run are not loaded
  automatically. New/untitled projects also suppress auto-loading old files;
  successful runs restore loading. The files themselves are not deleted, so
  they can still be opened manually.
- **CST MSL ports on PEC port faces / thin copper top line**
  (`app/freecad_io/cst_import.py`, `app/simulation/grid_dedupe.py`,
  `app/simulation/msl.py`): CST calculation boxes often put the microstrip
  port plane exactly on a PEC boundary, which openEMS treats as a shorted
  metal wall. The import now sets the affected port side(s) to **MUR** (and
  warns) while keeping the other PEC walls. Separately, grid dedupe removed the
  0.01778 mm copper top line as a "tiny cell"; the MSL strip then sat between
  grid lines and was reported as `Unused primitive`, giving S21 near -100 dB.
  Authored geometry/port lines are now protected. Reversed MSL ports also grow
  their start end now, so `oriented()` keeps the port plane at the edge. The
  sheet-metal-enclosure preset keeps faces carrying an MSL port at MUR instead
  of overwriting them with PEC (`app/ui/sim_params.py`). The port-side air
  margin stays 0: an air gap makes the microstrip end before the absorber and
  traps the energy (open substrate edge).
- **CST MSL ports at the upper domain edge** (`app/simulation/msl.py`,
  `app/meshing/build_task.py`): a zero-length CST/MSL port plane was widened to
  a grid line that differed only by a float32 ULP when the port sat on the
  upper domain edge. The resulting zero-width metal strip was ignored by
  openEMS (and the Mesh Viewer preview could reject the port). The extension
  now ignores near-duplicate lines and moves inward to the previous real grid
  line; the preview applies the same extension as the run generator.
- **Project / Geometry panel: hide only at the left edge; object menu in the
  panel** (`app/ui/main_window.py`): "Hide Project / Geometry panel" was
  removed from the pane's right-click menu and now belongs only to a dedicated
  6 px handle at the extreme left window edge (right-click toggles hide/show;
  Ctrl+B still works). Right-clicking the panel background, its title, empty
  tree space or an item now starts with an **Add** submenu (Box, Cylinder,
  Sphere, Polygon, Extruded Polygon, Wire, Curve, Port, Assignment, Material,
  Field Dump), followed by *Geometries* (primitives and imported files), *Ports*
  and *Fields* (dumps/NF2FF) selection submenus and *Delete selected*. The item
  menu keeps Edit/Duplicate/Remove and the same Add/selection submenus; no hide
  action remains inside the pane. Smoke coverage in `tests/menu_smoke.py`.
- **Gerber import dialog was too tall and did not show the selected folder**
  (`app/ui/gerber_import_dialog.py`, `app/ui/main_window.py`): the whole form
  now sits in a `QScrollArea`; OK/Cancel/Standardwerte stay fixed at the bottom,
  so the dialog works on small screens.  A new **Quelldateien** table lists
  every file from the selected folder/ZIP with its recognised role (top/bottom
  copper, board outline, PTH/NPTH drills, mask, IPC netlist, or "ignoriert"
  for CSV/JSON/pick-and-place data).  Verified with
  `gerber2ems/examples/basic/fab`; the source parameter is passed from the
  File/Geometry import action.  Menu smoke covers the scroll area, fixed
  buttons and the file list.
- **Gerber import failed on fabrication folders with CSV/JSON files**
  (`app/freecad_io/gerber_import.py`): `LayerStack.open()` scanned every file,
  so `*-top-pos.csv` / `*-bottom-pos.csv` were mistaken for copper and
  `basic-B_Cu.gbr` stayed "bottom unknown" ("Cannot figure out gerber file
  mapping").  Folders/ZIPs are now filtered to Gerber/Excellon suffixes before
  gerbonara sees them; if autoguess still fails, an explicit KiCad/Altium/
  Protel filename map (F_Cu/B_Cu, Edge_Cuts, PTH/NPTH, .gtl/.gbl/.gko, masks,
  silk, paste) is used as fallback.  Verified with the real
  `gerber2ems/examples/basic/fab` set (2 copper layers, 2 vias) and covered by
  `tests/test_gerber_import.py` (CSV folder + CSV-in-ZIP regressions).
- **Mesh Viewer: mesh-line clicks are editor-only; removed redundant "Port
  type" submenu** (`app/ui/mesh_viewer.py`): slice clicks no longer add or
  select manual mesh lines unless the **Mesh editor** checkbox is on; leaving
  the editor clears the line highlight, and "Delete selected line(s)" is
  disabled outside the editor. The right-click menu no longer duplicates the
  port type/width choice - it only toggles Port mode, and the Port dialog owns
  type, width and MSL axes. Port mode and Mesh editor are mutually exclusive:
  arming one switches the other off. Mesh-line snapping now uses the real
  contour of nearby polygons (not only their AABB) and only considers geometry
  that intersects the current slice plane. Polygon/triangle contours are only
  interpolated where the slice coordinate actually lies on the segment, so the
  snap can no longer extrapolate beyond a real edge (e.g. to 2.82 instead of
  the 2.70 trace edge). Polyhedron/STL primitives now expose
  their triangle vertices/faces to the Mesh Viewer and to easyMesh, so mesh
  lines and snapping follow the real imported outline instead of only its AABB.
- **KiCad import dialog is scrollable** (`app/ui/kicad_import_dialog.py`):
  the stackup/geometry options moved into a `QScrollArea`; the OK/Cancel
  buttons stay fixed at the bottom, so the dialog works on smaller screens.
- **New Project left the previous mesh/domain visible in the Mesh Viewer**
  (`app/ui/mesh_viewer.py`, `app/ui/main_window.py`): `clear()` reset only the
  data caches, so the last drawn matplotlib figure, cached scene infos and the
  domain outline stayed on screen.  New `MeshGeometryViewer.reset_view()`
  drops the scene/domain/auto-grid/selection/highlight/critical marks, recreates
  both canvases and draws a neutral "No project / mesh loaded" placeholder.
  File -> New Project (and a project with no recorded results) uses it and also
  clears the Mesh Viewer Scene Info tab.  Regression checks in
  `tests/menu_smoke.py` and `tests/mesh_viewer_smoke.py`.

## [0.9.2] - 2026-10-31

### Changed

- **Branding spelling corrected to VisualopenEMS**: application name, window
  title, About dialog, all documentation (Markdown, DOCX, ODT), tool footers and
  the PyInstaller output (`dist/VisualopenEMS.exe`) now use exactly
  **VisualopenEMS**. Internal package/module names, the GitHub repository name and
  existing folder paths stay unchanged for compatibility.

## [0.9.1] - 2026-10-31

### Fixed

- **Streamlines were tiny sticks** (`app/ui/vtk_viewer.py`): `vtkStreamTracer`
  defaults to `CELL_LENGTH_UNIT`, so the metre-based propagation/step values were
  interpreted as fractions of a cell and every line stopped almost immediately.
  The tracer now uses `LENGTH_UNIT`, giving full volume-spanning loops; measured
  6³ seeds: ~127k points in 0.08 s for the WR62 H-field.

## [0.9.0] - 2026-10-31

### Added

- **WR62 waveguide example** (`examples/run_wr62_waveguide_example.py`): straight
  rectangular guide 100 mm between the port planes, a = 15.7988 mm, b = 7.8994 mm,
  TE10 at 10 GHz (f_c = 9.488 GHz, λ_g = 94.9 mm). Four PEC walls, two
  `rect_waveguide` ports 100 mm apart, PML at the x ends, 9.5–11.5 GHz band, an
  E-field dump at 10 GHz and two excitation runs merged into a complete
  `sparams.s2p`. It is also a GUI preset (*Presets → WR62 Waveguide (10 GHz)*)
  built by `app.csxcad_builder.primitives.wr62_waveguide_primitives` with
  **Et, Ht and Jt** field dumps. Verified: S21/S12 ≈ 0 dB and S11/S22 ≈ −34 dB at
  10 GHz.

- **Field Viewer: configurable colour scale** (`app/colormaps.py`,
  `app/ui/vtk_viewer.py`, `app/ui/main_window.py`): the Settings menu has a new
  **Viewer settings…** entry. It now holds all Field Viewer defaults — the
  existing auto-play / standard-view / vectors options (moved out of the main
  Settings dialog, which is short again) plus a colour-map choice (jet, turbo,
  viridis, plasma, inferno, magma, coolwarm, rainbow, greyscale), a reverse
  switch, the number of colour steps (2…256, for discrete bands) and a live
  preview swatch. The colour-bar title is configured there too: left of the bar
  or above it, with X/Y position in normalized viewport coordinates, text size
  and rotation (the old toolbar checkbox moved into this dialog). The bar itself
  is configurable too: X/Y position, width/height (normalized viewport), font
  family (Arial/Times/Courier) and label font size. A **Preview** button applies
  the dialog to the viewer without saving; **Cancel** restores the previous
  state, **Save** keeps the preview. The choice is stored in
  `AppSettings.field_viewer_color_scale` and applied to the VTK lookup table, so
  plot and colour bar switch together; it is mirrored to the pop-out view. The
  default stays the previous blue → red table (jet), so existing screenshots and
  videos are unchanged.

### Changed

- **Field Viewer layout cleaned up** (`app/ui/vtk_viewer.py`): the six flat
  control rows became a top action bar plus a collapsible **display panel** on
  the left (*Data*, *Slice / Iso*, *Colour*, *Vectors*) and a fixed playback bar
  at the bottom. The slice/iso section appears only for the Slice and Iso-surface
  representations (and auto-expands on the first switch); the colour-range
  readout now sits in the *Colour* group instead of the far right of the slice
  row. `Export ▾` opens a menu with *Per-step series* and *Step stride*, so those
  export details no longer occupy the toolbar. Group headers, separators and
  consistent widths replace the previous mix. The panel is 280–320 px wide:
  colour min/max are stacked, geometry opacity is a spin box instead of a slider,
  and the vector rows no longer repeat their labels inside the spin boxes
  (`size` / `arrows` prefixes and the `% min` suffix are gone). No viewer function
  was removed.

  Tests: `tests/vtk_viewer_smoke.py` passes again (its vector-option assertions
  compared the full dict against `DEFAULT_VECTORS`, which the viewer has long
  extended with `min_pct` / `random` / `geom_opacity`; now it checks the default
  keys).

- **Field Viewer playback bar** (`app/ui/vtk_viewer.py`): Play comes first, the
  time slider is capped, and the frame/step and simulation-time labels have
  fixed widths. The variable-length arrow-motion diagnostic moved into its own
  shrinkable label (full text in the tooltip), so the bar no longer jumps when
  the text changes. The label uses a preferred (not ignored) size policy, so the
  arrow-motion text is visible again instead of collapsing to zero width.

- **Field Viewer layout modes (B / C)** (`app/ui/vtk_viewer.py`,
  `app/ui/main_window.py`, `app/settings.py`): Viewer settings now has a
  **Layout** selector. **Display panel (B)** docks the collapsible panel to the
  left of the 3D view (previous behaviour); **Minimal — Display… popup (C)** hides it and
  shows a `Display…` button in the top bar that opens the same panel in its own
  window, giving the viewer the full width. Stored as
  `AppSettings.field_viewer_layout` (`panel` / `minimal`), applied at startup,
  mirrored to the pop-out and honoured by the Settings **Preview** button.

- **Field Viewer defaults and panel polish** (`app/ui/vtk_viewer.py`): the
  geometry-opacity spin box now stretches like the other form fields (no fixed
  64 px width); **Outline** starts off; vector glyphs default to **random**
  sampling (fixed seed) with **Hide below = 5 %** (`DEFAULT_VECTORS` now carries
  `min_pct`, `random` and `geom_opacity`, so these defaults also restore
  correctly from `AppSettings.field_viewer_vectors`).

- **Playback defaults moved to Viewer settings** (`app/ui/vtk_viewer.py`,
  `app/ui/main_window.py`, `app/settings.py`): fps, Loop and Auto-play are no
  longer in the playback bar. They live in the new **Playback** group of
  *Settings → Viewer settings…* and persist as `field_viewer_fps`,
  `field_viewer_loop` and `field_viewer_autoplay`; the bar keeps Play, the time
  slider, frame/step, simulation time and the arrow-motion diagnostic.

- **Streamlines available for Iso-surface too** (`app/ui/vtk_viewer.py`): the
  *Streamlines* checkbox + seed spin moved out of the slice-only container, so
  they are also shown for `Show = Iso-surface` (the tracer always worked on the
  whole volume; only the UI was hidden).

- **Streamlines across the whole volume** (`app/ui/vtk_viewer.py`): the
  *Streamlines* switch seeds **n³ jittered points through the entire volume**
  (default 6³ = 216 lines, adjustable with the new **seeds** spin, 2³…10³)
  instead of the old degenerate seed plane. The tracer uses a long propagation
  (8 × volume diagonal, max 3000 steps, RK2, **length units** —
  `SetIntegrationStepUnit(LENGTH_UNIT)`; VTK's cell-length default made every
  line a tiny stick) so closed loops are traced like in ParaView; the tubes are
  coloured by the selected scalar/LUT and are rebuilt for every time/frequency
  step, so playback shows them moving through the volume.

- **Field Viewer geometry ghosts for all primitive kinds** (`app/ui/vtk_viewer.py`):
  the *Geometry* overlay used to draw only `kind=box` primitives, so imported
  STL/OBJ parts (the normal FreeCAD path) stayed invisible even with the checkbox
  on. It now also draws **stl/obj/npy triangle meshes**, **cylinders**, **spheres**
  and extruded **polygons**; unsupported/missing files are skipped instead of
  breaking the overlay. If a dump is opened without a project, the viewer now
  falls back to `<work>/geometry_primitives.json` / `project*.json` for the
  primitives, and `set_geometry` fits the camera once so ghosts outside the dump
  volume are visible.

- **Axes: origin behind the object** (`app/ui/vtk_viewer.py`): the Axes menu has
  a new **Origin behind object** switch (`DEFAULT_AXES["origin_back"]`). It
  places the in-scene triad on the bounding-box corner farthest from the camera;
  the numeric **coordinate ruler** follows by switching `vtkCubeAxesActor` to
  its furthest-triad fly mode, so both sit behind the field instead of in front
  of it.

- **Axes menu Size / Label font fixed** (`app/ui/vtk_viewer.py`): the size and
  font actions now use `triggered` and keep their `QActionGroup`s alive, so the
  choice reliably reaches `set_axes_option`; picking a font also switches the
  X/Y/Z labels on (otherwise the change is invisible), and the **Corner** marker
  now scales with the Size preset by adjusting its viewport (it ignores
  `SetTotalLength`).

- **Views submenu in the Field Viewer context menu** (`app/ui/vtk_viewer.py`):
  alongside *Axes*, the right-click menu now has **Views** with the standard
  view presets (Fit, Isometric, From ±X/±Y/±Z). Picking one applies it and
  updates the remembered default view.

- **Display popup keeps a usable minimum size** (`app/ui/vtk_viewer.py`): the
  popup defaults to **380 × 760** and refuses to restore a saved geometry below
  **340 × 620**, so it no longer opens as a tiny box that clips the Data section.

- **Field indicator in the tab header** (`app/ui/vtk_viewer.py`,
  `app/ui/tab_help.py`): the loaded field is shown next to the page title, e.g.
  **Field Viewer (J_tee [A/m²])**, instead of in the top action bar. The full
  file/series/array details remain in the label tooltip; the top bar keeps its
  width for the viewer.

- **Viewer settings dialog split into tabs** (`app/ui/main_window.py`,
  `app/settings.py`): the long single page became three tabs — **Viewer**
  (layout, default view/vectors, playback), **Colour** (map, reverse, steps,
  preview) and **Colour bar** (geometry/font + title). The last tab is remembered
  in `AppSettings.viewer_settings_tab`; dialog size/position use a fresh
  `viewer_settings_tabs` geometry key.

- **Window/dialog geometry is remembered** (`app/settings.py`,
  `app/ui/main_window.py`, `app/ui/vtk_viewer.py`): the main window restores its
  geometry, splitter sizes (`main_splitter_sizes` / `log_splitter_sizes`), log and
  project-panel visibility and the last active tab (`current_tab`). The main
  Settings dialog, the Viewer settings dialog and the Field Viewer `Display…`
  popup store size and position in `AppSettings.dialog_geometry` /
  `field_viewer_display_geometry` and restore them on the next open.

## [0.8.0] - 2026-10-29

### Added

- **Field Viewer: ParaView-style extras** (`app/ui/vtk_viewer.py`): **3 planes**
  shows the X, Y and Z slices at once (the selected axis follows the slider, the
  other two cut through the centre of their range), **Contours** draws 2…24
  iso-lines of the selected array on the slices, and **Streamlines** traces field
  lines through the volume with `vtkStreamTracer` and a tube filter, coloured
  from a seed plane through the centre. Streamlines need a 3-component vector
  array; the VTR export writes one (`field`) for dumps that carry xyz
  components, so the switch is enabled exactly when it can work.
- Tests: `tests/vtk_viewer_smoke.py` still passes; the new controls were checked
  headlessly (actor count grows by 2 for the extra planes and by 3 for the
  contours).

### Fixed

- **Streamlines did nothing on real dumps** (`app/ui/vtk_viewer.py`):
  `vtkStreamTracer` has no `SetInitialStepLength`; the call raised
  `AttributeError` and the actor was dropped silently, so the new switch left
  the view unchanged. It is `SetInitialIntegrationStep`; with it the tracer
  builds tubes from the `field` vector array that `dump_to_vtr` already writes
  for dumps with xyz components.

### Changed

- **Version string matches the release** (`app/__init__.py`): it still said
  `0.6.0`, so the About dialog and the window title showed a version two
  releases behind.
- Docs: `docs/ANLEITUNG_DE.md` and `ANLEITUNG_EN.md` are illustrated step by
  step, eighteen figures in `docs/images/`: the cross-section of the target
  geometry, the main window (start and with the preset loaded), the
  configuration dialogs (FreeCAD tools, simulation parameters, mesh parameters,
  material, port, NF2FF box), the script editor, and the evaluation pictures
  (mesh viewer slice and overview, S-parameters, antenna tab, axial-ratio cut,
  3D pattern, Polarization dialog, E-field slice). Only the drawing itself in
  FreeCAD has no picture — a GUI application cannot be captured headless.
  Figure numbers were dropped from the captions (the text never referred to
  them), so inserting a figure no longer renumbers the rest.
- Docs: the guides describe the three new switches (3 planes, Contours,
  Streamlines) in section 7.2, including why streamlines need a dump with the
  `field` vector array; the README lists them in its Field Viewer section.
- Docs: `build_anleitung_docx.py` deletes the target ODT before converting —
  LibreOffice reports success without overwriting an existing file, which had
  left a stale `ANLEITUNG_EN.odt` behind (16 of 18 pictures).
  `examples/build_anleitung_docx.py` renders `![alt](path)` so the Word build
  carries the same figures. The README gained a *Screenshots* section (the table
  of contents promised one that did not exist) plus one picture each in the
  antenna-viewer and field-viewer sections.
- Docs: the guides are also emitted as LibreOffice documents —
  `examples/build_anleitung_docx.py --all --odt` builds both languages and
  converts each DOCX with headless LibreOffice (a throw-away user profile, so an
  open LibreOffice does not block it); `--md` / `--docx` build a single file, and
  the footer follows the source language. `docs/ANLEITUNG_DE.{docx,odt}` and
  `docs/ANLEITUNG_EN.{docx,odt}` are in the repository.

## [0.7.0] - 2026-10-28

### Added

- **Grid export (JSON)** (`app/meshing/grid_export.py`, Mesh → Export grid
  (JSON)…): writes the built mesh lines per axis in millimetres — plus cell
  counts, min/max cell size and the FDTD domain — so a second FDTD solver
  (CST) can run on identical cell boundaries instead of an independently
  meshed model. Read from the Mesh Viewer's CSX, i.e. the grid that was
  actually built. Handles cartesian and cylindrical (rho/phi/z) meshes.
- **ParaView collection (`.pvd`) for field series**
  (`app/postproc/vtk_export.py`): the per-step VTK export now also writes
  `<dump>.pvd`, so ParaView plays a full time/frequency series from one file.
  The slider value is the physical step position from the dump attributes
  (time for TD, frequency for FD), not the step index. The collection is
  rebuilt from the existing `.series.json` when a series is reused, so older
  exports and re-exports stay consistent.
- Units: `UNIT_TO_METERS` / `mm_per_unit()` in `app/models/project.py` are now
  the single source for drawing-unit conversions (CSXCAD delta unit, STEP
  export, grid JSON).
- Tests: `tests/test_grid_export.py` (sorting, dedupe, mm conversion, cell
  statistics, cylindrical axes, invalid unit) and new `.pvd` cases in
  `tests/test_vtk_export.py`.
- Docs: `docs/ANLEITUNG_DE.md` and `ANLEITUNG_EN.md` gained section 7.1 with
  four screenshots (Antenna tab, axial-ratio cut, 3D pattern, Polarization
  dialog) under `docs/images/`; `examples/build_anleitung_docx.py` now renders
  `![alt](path)` so the Word build carries the same figures.
- Docs: new help topic **“Export & Interchange”** (Help menu, also in the F1
  dialog) covering every export path — STEP, grid JSON, CSX XML, VTK/`.pvd`/MP4,
  Touchstone, PNG — with a data/format/consumer table and the limits, plus
  cross-references from the Geometry, Mesh Viewer and Field Viewer topics.
  Section 12 of `docs/ANLEITUNG_DE.md` and `docs/ANLEITUNG_EN.md` carries the
  same content in prose.
- **STEP export for CAD / CST Studio Suite** (`app/freecad_io/step_export.py`,
  `app/freecad_io/freecad_step_runner.py`, File → Export STEP…): writes the
  effective project geometry — analytic boxes, cylinders, spheres and extruded
  polygons plus imported triangle meshes — as an AP214 STEP file in
  **millimetres**, so it opens in CST without a unit guess. Object names are
  preserved as STEP `PRODUCT` names (`Import.export`, not `Part.export`), so the
  solids keep the names they have in the Mesh Viewer. Zero-thickness metal
  sheets (e.g. the patch preset) become planar faces instead of a degenerate
  solid; meshes become shells (solids when watertight). Geometry that cannot be
  represented (points, lines, missing meshes) is reported in the log instead of
  aborting the export.
- Tests: `tests/test_step_export.py` (25 pure tests for units, sheet/prism
  detection, cyclic polygon axes and mesh scaling) and
  `tests/step_export_smoke.py` (FreeCADCmd round-trip: names, mm unit, volumes,
  open-shell preservation, GUI handler, cm → mm).
- **Live preview in the polygon dialog** (`app/ui/primitive_preview.py`): while
  the point list is typed, the dialog draws the polygon in its own plane with
  equal aspect (a 45° edge looks like 45°), numbered vertices, a grid with tick
  values in the project unit and an offset outline for extruded prisms
  (`length > 0`). It repaints on every keystroke and on axis / coordinate /
  length changes; incomplete input (< 3 points) shows the open outline plus a
  hint instead of a broken picture.
- **Antenna tab: axial ratio and phase center** (`app/postproc/polarization.py`,
  `app/ui/polarization_dialog.py`): both are derived from the complex
  `E_theta`/`E_phi` already stored in `nf2ff.json` — no solver re-run. AR comes
  from the Stokes parameters (with CP sense and ellipse tilt; linear
  polarization is reported as "linear" instead of a fake 0 dB). The phase
  center is a least-squares fit over the main beam (phi = 0/90 cuts) relative
  to the NF2FF box centre, with a quality gate (phase residual / offset inside
  the NF2FF sphere) so a meaningless fit is shown as "not well-defined" rather
  than a number. Button "Polarization…" in the Antenna tab, a Help topic, a
  Help-menu entry ("Polarization / phase center…") and Copy-as-TSV.
- Tests: `tests/test_polarization.py` — circular/linear/elliptical AR, a known
  phase-center offset, the payload summary and the missing-field case.
- **Axial-ratio cut in the antenna viewer** (`app/ui/antenna_viewer.py`): plot
  type "Axial Ratio (theta cut)" — AR against θ at a selectable azimuth
  (0…315°, nearest stored φ), computed from the complex `E_theta`/`E_phi` of the
  loaded `nf2ff.json` (no re-run). It draws the 3 dB limit, shades the pass band
  and labels the widest θ that still meets it, counted outward from boresight.
  The azimuth selector lives in the plot toolbar, so the pop-out window carries
  it without sync code, and it is hidden on every other plot type. Smoke test
  extended with a synthetic CP field (AR 0 dB at φ = 0, 6 dB at φ = 180).
- **Preset: GPS L1 patch (CP)** (`app/csxcad_builder/gps_patch.py`,
  Presets → GPS L1 patch (CP)): 44.10 mm square patch on 1.6 mm FR4, two
  opposite corners chamfered 5.0 mm as five 1.0 mm staircase steps, probe
  10.9 mm inset on the patch centre line, 75 mm ground. A converged run (end
  energy −30.5 dB) gives |S11| = −12.4 dB and AR = 1.70 dB (RHCP) at
  1.57542 GHz. The axial ratio is mesh-dependent (2.93 dB on a 0.5 mm grid,
  3.94 dB with a 0.5 mm staircase), and directivity/efficiency spread with the
  NF2FF box size rather than with the antenna — the preset message quotes
  D 7.3–7.9 dBi, gain 1.7–3.2 dBi and efficiency 27–34 % and says why. A
  manufacturing variant with a true 45° chamfer (5.5 mm cut, 0.5 mm grid)
  ships as a project file but is not solved. Tests: `tests/test_gps_patch.py`
  (7 tests) pin the 180° rotational symmetry of the two chamfers, the
  axis-aligned staircase and the centre-line feed.

### Fixed

- **easyMesh aborted on polygons with two parallel 45° edges**
  (`third_party/easyMesh4openEMS/easyMesh/geometryUtils.py`): the bundled copy
  had lost `distance_between_segments`, the helper `handle_diagonal_edges`
  calls to grade the mesh between two diagonal edges. Every chamfered,
  hexagonal or otherwise 45° outline therefore died with
  `AttributeError: module 'easyMesh.geometryUtils' has no attribute
  'distance_between_segments'` and could not be meshed at all. The helper is
  restored verbatim from the upstream package
  (`github.com/MustafaAlchalabi/easyMesh4openEMS`), so such patches build now
  and keep every vertex coordinate on the grid. The GPS patch preset uses the
  polygon geometry this fix enables; the rejected three-box approach stays
  documented in the case study.
  Tests: `tests/test_easymesh_diagonal.py` (helper contract + hexagon build).
- **Mesh Viewer drew every polygon as a rectangle** (`app/meshing/polygon_geometry.py`,
  `app/ui/mesh_viewer.py`): a polygon / extruded polyline primitive was drawn
  from its bounding box, so a hexagon patch, a triangle or a chamfered corner
  looked like a rectangular patch. The viewer now derives the real cut per
  slice — the polygon outline face-on (sheet and prism), a line for an edge-on
  sheet, a filled band for an edge-on prism, and the outline / silhouette in
  the Overview. Works for project primitives and for the CSX infos after a
  mesh build (`PrimitiveInfo.poly_points/poly_axis/poly_coord/poly_length`),
  and the yellow selection highlight follows the outline too. The AABB is
  still used for the domain / outside checks. Objects are drawn in ascending
  CSX priority, so a metal patch (100) stays visible on top of the dielectric
  (10) it sits on instead of disappearing behind it. Tests:
  `tests/test_mesh_viewer_polygon.py` (geometry + CSX introspection) and
  `tests/polygon_viewer_smoke.py` (offscreen render, hexagon + edge-on).
- **3D pattern had no axis labels** (`app/ui/antenna_viewer.py`): the tick
  numbers are hidden on purpose (the colour bar carries the dBi scale), but the
  three spatial directions were never labelled either, so x/y/z could not be
  identified. They are labelled now, with a negative `labelpad` so they sit next
  to their axis instead of far outside the cube. The smoke test pins them.
- **easyMesh on NumPy 1.x** (`app/meshing/easy_wrapper.py`): the bundled
  easyMesh calls `np.atan`, an alias that only exists from NumPy 2.0 on; on
  NumPy 1.26 the missing alias is provided (`np.arctan`), without which any
  polygon with 45° edges aborts the mesh build.

### Changed

- **GPS preset quotes a range instead of a point value**
  (`app/ui/main_window.py`, `app/csxcad_builder/gps_patch.py`): repeating the
  identical geometry with a larger NF2FF box (±68 mm instead of ±55 mm) leaves
  |S11| and the axial ratio unchanged to 0.03 dB but moves the directivity from
  7.29 to 7.89 dBi and the efficiency from 27.3 % to 33.7 %, and rotates the
  azimuth pattern by 180°. The far-field post-processing is therefore not
  converged, so the preset message and the module docstring quote D 7.3–7.9 dBi,
  gain 1.7–3.2 dBi and efficiency 27–34 % and name the reason.
- **Polygon dialog opens compact and remembers its size**
  (`app/ui/geometry_editors.py`, `app/ui/primitive_preview.py`): it used to open
  at a fixed 900×640 on every screen, which covered most of a 1200×760 desktop.
  The default is now 760×520, clamped to the available screen, the live preview
  may shrink to 200×140, and the size the user drags it to is remembered in the
  settings (`dialog_sizes`) and restored the next time the dialog opens.
- **Smaller Windows EXE** (`build_exe.py`): the frozen bundle no longer ships
  the bundled ffmpeg (MP4 export now asks for `imageio-ffmpeg` / an ffmpeg on
  `PATH`), the Qt Quick/Qml/Pdf/Network DLLs and plugins, the software-OpenGL
  fallback (`opengl32sw.dll`) and the unused pure-Python stacks that only enter
  through optional imports (django, astropy, sympy, sqlalchemy, networkx,
  pytest, hypothesis). 248.6 -> **172.3 MB**. `OPENEMS_KEEP_OPENGL_SW=1` keeps
  the software-OpenGL fallback for machines without a GPU driver.

## [0.6.0] - 2026-10-25

### Added

- **Mesh timing / feasibility report** (`app/meshing/timing.py`, Mesh menu
  *Mesh Timing / Feasibility…*): right after *Build Mesh* the app reports the
  smallest cell per axis and the Courant timestep it imposes (plus the lower
  bound inside the densest dielectric), cells per wavelength at f_max, the
  runtime penalty of an over-refined cell against a typical one, the timesteps
  needed to cross the domain and the frequency resolution `1/(max_timesteps·dt)`
  compared with the requested sweep step — each warning names the concrete
  remedy (e.g. how many timesteps would be needed, or which cell to relax). The
  block is part of the mesh report (log panel tab *Mesh Report*) and one
  summary line is logged per build.
- **Post-run simulation status report** (`app/simulation/report.py`, Simulation
  menu *Simulation Status…*): parses the solver log for errors, warnings and the
  engine's own facts (exact timestep, Nyquist rate, excitation length,
  operator/field size, steps/energy/speed), keeps the mesh-validation warnings
  from before the run, cross-checks the mesh timestep estimate against the engine
  value, computes the actual DFT window resolution and the passivity
  `sum_i |S_ij|^2 <= 1` per excitation versus frequency (core band vs. sweep
  edge, where a violation is a DFT window artefact), and ends in a verdict
  (`OK` / `WARNING` / `ERROR`) with a trust rating. The same block is appended to
  the end of the run log; `app/ui/report_dialog.py` shows it with Copy/Save.
- Tests: `tests/test_mesh_timing.py`, `tests/test_sim_report.py`.
- **Field Viewer: arrow-motion readout**: while stepping through a series by
  hand, the step label adds how many arrows flipped compared to the previous
  step ("24% reversed vs. previous step, median cos …"), so "do the vectors
  follow the field?" has a number instead of an impression.
- **Mesh Viewer: critical cells marked (right-click toggle)**
  (`app/meshing/critical_cells.py`, `app/ui/mesh_viewer.py`): only the cells that
  **cost runtime** are drawn — the ones that set the FDTD timestep
  (`cell_below_min`, `very_small_cell`, `timestep_cell` = smallest cell of a
  graded axis) plus, after a run, the **engine's smallest-timestep cell** (parsed
  from `… found at position: 1 : 39;14;0`). Grading jumps and too-large cells are
  *not* marked any more: they are report findings, covered most of the domain and
  hid the cells that matter. Consecutive cells are merged into one region and
  drawn as dashed outlines (no fill) in the slices — a finding whose axis is the
  slice normal is skipped — and as bands + markers in the *Cell sizes* plots.
  Capped at 24 marks, sorted by severity. The overlay is **off by default**:
  the right-click menu entry "Mark critical cells (N)" switches it on, the count
  in the entry shows beforehand whether anything is flagged. One log line per
  build and a block in the mesh report.
- `tests/test_critical_cells.py` — 13 checks (thresholds, caps, engine-cell
  parsing with clamping, labels, source attribution).
- **Fine cells are attributed to their source (P2, switchable, default on)** —
  `MeshSpec.diagnose_cell_sources`: every cell the report flags now names the
  primitive / port / manual line at its faces (`box:feed + manual`), in the Mesh
  Viewer marks and in the report section "Fine-cell sources"; cells without an
  authored source are reported as easyMesh's own refinement (circle/arc
  detection, graded interface, PML). Off = marks without a source and an explicit
  "Fine-cell attribution off" line. Checkbox in *Mesh Parameters*.

- `tests/vtk_vectors_smoke.py` — standalone check of the vector display
  (exclusive mode, orientation from probe glyphs, series-wide length
  reference).
- Docs: `docs/DIAGNOSTICS_CHANGES_DE.md` / `docs/DIAGNOSTICS_CHANGES_EN.md` —
  the detailed change log of all four packages (mesh timing, run status, Field
  Viewer vectors, coordinate-quantization Phase 0 incl. the measurements over
  the 15 real projects).

- **Coordinate-quantization probe — plan Phase 0, read-only**
  (`app/meshing/coord_diag.py`): the mesh report now *measures* what the
  "Coordinate Quantization Plan" has to decide on, without changing anything —
  near-duplicate grid lines bucketed by distance (1e-9 … 1e-3 units), authored
  coordinate pairs below the planned lattice tolerance (tagged with their source:
  box / cylinder / polygon / stl / port / dump / NF2FF / manual line), every
  authored coordinate's distance to the next line **as a fraction of the local
  cell** (ports above 15 % are flagged, NF2FF/dump corners stay informational)
  and the smallest cell against the requested `min_cellsize`. One summary line
  per build goes to the log; the block is part of the Mesh Report tab and of the
  "Mesh Timing / Feasibility" window. The planned auto lattice is computed with
  the *corrected* rule (`q <= c_min/1000`, i.e. `N = ceil(3 - log10(c_min))`) —
  the plan's own formula contradicts its worked examples.
  Measured over the 15 real projects in `work/`: **0 near-duplicate line pairs,
  0 authored pairs below tolerance, 0 ports off the grid** (see CHANGELOG of the
  Phase-0 report), which puts the plan's "micro cell" premise in perspective.
- `tests/test_coord_diag.py` — 21 checks incl. the corrected auto-quantum rule
  and the cell-fraction classification.

- **Critical cells can be repaired (V1, opt-in, preview first)**
  (`app/meshing/repair.py`, `app/ui/repair_dialog.py`): right-click a mark in the
  Mesh Viewer → *Repair this x/y/z region…* shows what would change (lines
  removed, new smallest cell, dt before/after, expected cell count) before
  anything is written; OK stores a rule in `MeshSpec.mesh_repairs` and the mesh
  is rebuilt with it. The repair merges a too-small cell into its **smaller**
  neighbour, relaxes the grading only around the merged cells and splits cells
  above the max-cell limit — deterministic, and it never removes an authored line
  (domain edges, primitive/port/dump/NF2FF faces, manual lines). A cell with both
  faces authored is reported as `geometry` and not repairable.
- **Min cell size can be enforced by the repair pass (V2, default off)**
  (`MeshSpec.enforce_min_cellsize`, checkbox *Mesh Parameters → "Enforce min cell
  size (repair)"*): `Min cell size` becomes a hard floor on every axis,
  `Min cell size (Z)` overrides the layer thickness. While the switch is on the
  floor is no longer passed to easyMesh — measured on `Filter_new_test1`,
  easyMesh alone reacted to `min=0.1` with cells ×2.42 and dt only ×1.35, with
  the decoupled floor the project gives dt ×1.45 at cells ×0.98. The report lists
  requested vs. achieved per axis and names the reason when a genuine gap keeps
  a smaller cell.
- **Per-mark actions in the Mesh Viewer (V3)** (`app/ui/mesh_viewer.py`): the
  right-click submenu *Critical cells (N open)* now offers **Repair…**,
  **Remove the offending line…**, **Snap onto the neighbour line…**,
  **Accept region (do not repair)** and **Explain…**, each enabled only where it
  is honest (repair disabled for `geometry`, snap only for duplicate-scale
  slivers, remove only when a face is an auto line). Accepted regions stay
  visible in grey but count as accepted instead of open findings.
- **The same options in the Mesh menu** (`app/ui/main_window.py`): the
  *Critical cells (N open)* submenu mirrors the viewer's right-click menu —
  *Mark critical cells* (overlay, checkable), *Repair the worst open region…*,
  *Remove the offending line…*, *Snap onto the neighbour line…*, *Accept region
  (do not repair)*, *Explain…* and *Enforce min cell size (repair)* (checkable,
  mirrors the Mesh Parameters checkbox). Without a cursor the target is the
  worst open finding; the enablement comes from the same viewer helper as the
  right-click menu, so the two can never disagree.
- **Help: new topic "Mesh Repair"** (`app/ui/tab_help.py`): documents the cause
  codes, the five per-mark actions, the preview, the protection rules, the
  global floor, the storage fields and the limits. Reachable from F1 (topic
  list), the repair preview's Help button, Help → *Mesh repair (critical
  cells)…* and the Mesh Viewer / Mesh Parameters help, which link to it.
- Tests: `tests/test_mesh_repair.py` (34 checks: merge/relax/split, protection
  incl. float32 vs. float64, preview, cause/penalty/accepted, per-axis floors,
  the easyMesh decoupling), `tests/repair_actions_smoke.py` (menu enablement,
  accept, remove, repair round-trip, explain).

### Fixed

- **Field Viewer: the vector glyphs no longer fight with the field plot**: the
  arrows were drawn on top of the coloured slice/surface/volume, which covered
  every arrow pointing into the opaque part. The arrows now *are* the
  representation (ParaView style); a new **"field underneath"** checkbox brings
  the overlay back on request (`app/ui/vtk_viewer.py`,
  `DEFAULT_VECTORS["overlay"]`).
- **Field Viewer: arrow orientation is forced onto the glyph input**
  (`SetActiveVectors`): when a filter chain or an older export had lost the
  active-vector flag, `vtkGlyph3D` kept every arrow pointing in the same
  direction while only the length followed the field.
- **Field Viewer: arrow lengths are comparable across frames**: the reference
  |field| is measured once over the whole series (sampled, cached) instead of
  being recomputed per frame, so an arrow of the same strength keeps its length
  and the real amplitude change over time stays visible instead of the arrows
  "breathing" with each frame's own percentile.

### Changed

- **One coordinate grid for every geometry source (P1 of the quantization
  plan; switchable, default on)** — `MeshSpec.align_coordinates`: triangle-mesh
  vertices (STEP/STL/FCStd) now go through the same float32 rule that boxes,
  ports, dumps and the NF2FF box already use (`quantize.align_coordinates()`)
  instead of being rounded to a 0.1 µm decimal grid. A vertex and a box face
  that are meant to be equal are now **bit-identical**, which removes the only
  measured source inconsistency (up to 0.1 µm = 3× to 830× the float32
  resolution) without touching the mesh. Off = legacy behaviour (0.1 µm
  rounding); the switch is in *Mesh Parameters* → "Align coordinates (float32
  grid)", persisted per project, and old project files load with the default
  (on). Stored `.npy` meshes keep their historical rounding until re-imported.
- **`dedupe_grid_lines` gets a float32-ULP floor** (`ulp_align`, 8 ULP of the
  largest coordinate) so sub-resolution line pairs merge even where the
  `frac·cell` rule stays below that noise; it never merges less than before and
  is a no-op in all 15 measured projects. The function moved out of the
  `SCRIPT_TEMPLATE` string into `app/simulation/grid_dedupe.py`, so the code
  that actually runs is unit-testable; the generated script imports it like the
  other `app.*` modules.
- **Honest "below min cell size" diagnostics (V4)**
  (`app/meshing/critical_cells.py`, `app/meshing/coord_diag.py`): the
  `cell_below_min` mark now compares against the **user-set** `min_cellsize` /
  `min_cellsize_z` only (`requested_min_cellsize`); the derived fallbacks
  (`refined_cellsize`, `target_cell_units`) are targets, and using them as a
  minimum flagged 162 ordinary cells / 5.08 mm in a real project without
  `min_cellsize`. Every mark also names its cause (`geometry` > `requested` >
  `artifact`, `engine` for solver-reported cells), its runtime penalty (axis
  median / cell) and whether it is accepted; the mesh report lists requested vs.
  achieved per axis and states when the remaining cells are bounded by authored
  coordinates instead of claiming a repair.
- Tests: new `tests/test_dedupe_grid_lines.py`; `tests/test_quantize.py` and
  `tests/test_axis_aligned_box.py` extended (bit-identity, legacy offset,
  ULP tolerance).

## [0.5.10] - 2026-10-23

### Added

- **Hairpin band-pass preset for 2.4 GHz on RT/duroid 6010LM**
  (`app/csxcad_builder/hairpin.py`, GUI menu *Preset → PCB Hairpin Bandpass
  (2.4 GHz)…*, `examples/run_pcb_hairpin_example.py`): a compact 5-pole
  Chebyshev band-pass (0.1 dB ripple, 10 % bandwidth = 2.28–2.52 GHz) built
  from five folded lambda/2 resonators. Copper area 25.1 x 14.3 mm, no vias,
  no stubs. The synthesis drives the arms in the *odd* mode of the arm pair
  (eps_eff = 5.946) and derives the six coupling gaps (0.316 … 1.753 mm) from
  the Hammerstad-Jensen even/odd model via the J-inverters of the Chebyshev
  prototype; strip width is uniform at 1.186 mm (50 ohm). Design table,
  synthesis notes and validation status are documented in
  `docs/ANLEITUNG_DE.md` / `docs/ANLEITUNG_EN.md` section 4.

### Fixed

- **Hairpin preset could never converge**: `pcb_hairpin_primitives` shipped
  `end_criteria=1e-5` with `max_timesteps=120_000`. With tan_d = 0.0023
  (Q0 ~ 435) the stop-band ring-down needs millions of steps, so the run always
  hit the cap while the energy was still high and wrote truncated, meaningless
  S-parameters. The budget is now `end_criteria=1e-4` / `max_timesteps=600_000`
  and the reason is documented next to the setting.
- **Manual mesh lines were appended unsorted** (`app/meshing/manual_mesh.py`):
  the in-memory CSXCAD grid could end up out of order, which corrupts the FDTD
  cell sizes. The grid is now sorted after every edit.
- **Near-duplicate grid lines collapsed to zero-size cells**
  (`app/simulation/script_gen.py`): `dedupe_grid_lines` now merges lines within
  a fraction of the local cell size instead of only exact duplicates.

## [0.5.9] - 2026-10-23

### Fixed

- **Mesh Viewer: objects vanished from a slice sitting exactly on their face**
  (`app/ui/mesh_viewer.py`, `tests/mesh_viewer_smoke.py`): CSXCAD returns the
  primitive bounds float32-rounded, so the top face of a PCB metal at 0.254 mm
  came back as 0.2540000081 mm. The slice plane at 0.254 mm therefore counted
  as "outside" that object and the traces were drawn only as the dotted project
  outlines — clicking them in the Geometry window highlighted them, but the
  copper itself stayed invisible. The plane test now uses an absolute plus
  relative tolerance (same idea as the existing `_close` helper), so a plane on
  a face still draws the object.

## [0.5.8] - 2026-10-23

### Changed

- **Mesh Viewer: object colour/transparency/visibility now live in the object
  dialogs** (`app/ui/object_display.py`, `app/ui/geometry_editors.py`,
  `app/ui/main_window.py`, `app/models/project.py`): the **Objects:** row in the
  viewer toolbar (drop-down + colour button + transparency slider added in
  0.5.6/0.5.7) was removed. Instead every geometry object carries its own
  appearance, edited where the object is edited — the **Box / Cylinder / Sphere
  / Polygon** dialogs and the **Material assignment** dialog as well as the
  imported-mesh dialog now end with a **Mesh Viewer display** group: **Colour…**
  (with **Automatic** = the per-kind colour), **Transparency** (0–100 %, default
  30 %) and **Visible**. Visibility hides the object in the Mesh Viewer only —
  it stays part of the simulation. Ports are never affected. The settings are
  stored per object name in the project (`object_display`) and therefore travel
  with the `.fcsim.json` file; the viewer maps every drawn CSX primitive back to
  the object it came from (two properties with the same name are kept apart).
  The now unused `mesh_viewer_objects` GUI setting was dropped.

## [0.5.7] - 2026-10-22

### Added

- **Mesh Viewer: colour and transparency per object** (`app/ui/mesh_viewer.py`,
  `app/ui/tab_help.py`): the **Objects:** row now starts with a drop-down that
  lists every geometry object (primitives, dielectric boxes, imported CAD).
  *All objects* keeps the previous behaviour and sets the default for
  everything; picking one object applies **Object colour…**, **Reset** and the
  **Transparency** slider only to that object. All other objects keep the
  default (or the *All objects* setting), so e.g. a substrate can be made
  almost invisible while the metal stays solid. **Reset** restores one object;
  with *All objects* it clears every per-object override and the 30 % default.
  Ports (lumped elements, excitation, probes) are excluded from the list and
  keep their port colour. Overrides are stored per object name in
  `mesh_viewer_objects["objects"]`, survive a restart and mirror to the
  pop-out.

## [0.5.6] - 2026-10-22

### Added

- **Mesh Viewer: object colour + transparency** (`app/ui/mesh_viewer.py`,
  `app/settings.py`, `app/ui/main_window.py`): a new **Objects:** row under the
  viewer toolbar. **Object colour…** picks one colour for all geometry objects
  (primitives, dielectric boxes, imported CAD) — handy when the per-kind
  colours are hard to see or many objects overlap; **Kind colours** restores the
  defaults (metal orange, dielectric blue, ports red). The new **Transparency**
  slider (0–100 %, default 30 %) makes the object faces see-through so the mesh
  grid and objects behind them stay visible, while the object outlines keep a
  higher opacity so the shapes remain readable. Both settings apply to the
  slices, the Domain Overview and the pop-out, and are remembered across
  sessions (`mesh_viewer_objects`).

## [0.5.5] - 2026-10-22

### Added

- **Field Viewer: vector glyphs (ParaView-style arrows)**
  (`app/ui/vtk_viewer.py`, `app/ui/main_window.py`, `app/settings.py`):
  new **Vectors** checkbox plus *size*, *arrows* and *same length* controls.
  Arrows are drawn for the vector field (the exporter's 3-component point
  array `field`, i.e. real(E/H); loaded VTK files may bring their own vector
  array) and follow the current representation: **Slice** → arrows in the cut
  plane, **Surface / Wireframe** → on the outside of the volume,
  **Iso-surface** → on the contour. During playback the arrows move with the
  field, so rotating or travelling fields become visible instead of only a
  changing colour. The points are decimated **deterministically**
  (`vtkMaskPoints`, every N-th point, `max_arrows` default 2000) so the
  pattern does not flicker between frames. **same length** (default) shows
  the direction only; unticked, the length follows |E| with the 99th
  percentile mapped to the *size* length (one hot spot cannot shrink all
  other arrows). Arrows are coloured by the selected array. Settings persist
  across sessions (`field_viewer_vectors`), mirror to the pop-out, and have a
  default in Settings → Field Viewer.

### Fixed

- **Field Viewer: "Wireframe" representation crashed**
  (`app/ui/vtk_viewer.py`): `vtkDataSetMapper` has no
  `SetRepresentationToWireframe()`; the representation is now set on the
  actor property, so Wireframe actually renders.

## [0.5.4] - 2026-10-22

### Added

- **PCB band-pass example: 5.6 GHz, five open stubs, Teflon**
  (`app/csxcad_builder/primitives.py`, `examples/run_pcb_bandpass_example.py`,
  `app/ui/main_window.py`): new preset **Preset: PCB Bandpass (5.6 GHz)…**
  (menu *Presets*) and a headless example. A 5-pole Chebyshev band-pass
  (0.1 dB ripple) on PTFE (`eps_r` 2.1, `tan_d` 0.0002, 0.254 mm, 35 µm copper):
  five **open lambda/2 shunt stubs** on a 50 ohm line, separated by four
  **lambda/4 inverter lines** — no vias, single-sided planar. Line/stub widths
  and lengths are synthesised with Hammerstad's microstrip formulas from
  `PCB_BANDPASS_DESIGN` (impedances from a fit of the exact commensurate
  network to the Chebyshev target). Two lumped 50 ohm edge ports; the example
  runs both excitations and merges to `sparams.s2p`. Simulated (openEMS):
  **|S11| < -10 dB from 4.90 to 6.45 GHz**, insertion loss -0.1 … -0.3 dB,
  rejection -42 dB at 4.0 GHz. The preset mesh uses `min_cellsize_z`
  (three cells through the 0.254 mm substrate) and turns off
  `smooth_metal_edge` / `handle_closely_placed_edges` — easyMesh otherwise
  produced a 4 µm Y cell that shrank the CFL step by 8x. Docs:
  `docs/ANLEITUNG_DE.md` / `ANLEITUNG_EN.md` (new subsection with the design
  table and the measured result).
- **Ports: "Reverse" option** (`app/models/project.py`,
  `app/ui/geometry_editors.py`, `app/ui/main_window.py`,
  `app/ui/mesh_viewer.py`, `app/simulation/script_gen.py`): the Port dialog has
  a **Reverse (swap Start/Stop)** checkbox (`PortDef.reverse`, default off).
  The run script then swaps Start and Stop for that port: openEMS derives the
  port polarity from `sign(stop - start)`, so a lumped port's excitation and
  current probe change sign and an MSL port gets the opposite propagation sense
  — the port is measured the other way round. The box does not move (the
  project keeps its original Start/Stop); the port list, the project tree and
  the Mesh Viewer info panel mark it "[rev]". MSL ports are reversed *after*
  the zero-length one-cell extension, so a sheet port still gets its cell.
  rect_waveguide ports ignore it (their mode direction is the Direction field)
  and the generated script says so.
  Tests: `tests/test_msl_port.py`
  (`orient_port_box` + generated script), `tests/port_defaults_smoke.py`
  (dialog round-trip, `[rev]` markers, defaults off).
- **Lumped ports: "Line" or "Area = metal width"** (`app/models/project.py`,
  `app/ui/mesh_viewer.py`, `app/ui/main_window.py`,
  `app/ui/geometry_editors.py`): a lumped port can be a 1-D line or an **area
  (sheet) port** that is as wide as the metal it was placed on. The Mesh
  Viewer's right-click menu → **Port type** now offers **Lumped (line)**,
  **Lumped, width along X/Y** and **MSL, width along X/Y** (the in-plane axes
  follow the slice); an area port spans the clicked metal along the chosen axis,
  keeps the click as a zero-thickness sheet towards the other in-plane axis and
  still runs from the clicked metal's face to the next metal's face. The Port
  dialog has a **Width (lumped)** combo (Line / Area along x/y/z) that rebuilds
  the box when the mode changes — line → collapse to the middle, area → widen to
  the metal (the clicked primitive is stored as `PortDef.metal`) — while an
  unchanged mode keeps hand-edited Start/Stop values. `PortDef` gained
  `lumped_width` ("line" or an axis letter) and `metal` (old project files load
  unchanged); the port list/tree mark area ports with "area along Y". openEMS
  only sees the box, so the generated script is unchanged and the Resistance
  stays the **total** box resistance (a 50 Ω wide port is still 50 Ω).
  Tests: `tests/mesh_port_place_smoke.py` (5 menu entries, area box = metal
  width along Y / click as a sheet along X / same substrate length, list
  marker), `tests/port_defaults_smoke.py` (dialog round-trip for lumped/MSL,
  line <-> area conversion, hand edits kept, width along the excitation
  direction rejected).
- **Selecting an object in the Geometry list shows it in the Mesh Viewer**
  (`app/ui/mesh_viewer.py`, `app/ui/main_window.py`): a click on an entry in the
  left Project / Geometry list (or a material in the Geometry tab) not only
  highlights the object, it also **puts the slice plane on the middle of its
  height** — e.g. an XY slice with an object spanning z = 0…5 mm moves the plane
  to z = 2.5 mm, so the object really is visible in that slice
  (`focus_highlight()`, clamped to the FDTD domain by the plane spinbox).
  Programmatic list rebuilds (which re-select the same row after every edit) do
  not move the plane again — only a click in the list does. The highlight now
  also counts elements in the *neighbouring cell* of the plane (like the port
  overlay), so an object between two mesh lines shows up too.
  Tests: `tests/mesh_port_place_smoke.py` (strip -> 1.0175, ground -> 0.0175,
  visible in the slice, plane untouched by the port placement that rebuilds the
  list).

- **Ports can be switched off ("Active")** (`app/models/project.py`,
  `app/ui/geometry_editors.py`, `app/ui/main_window.py`,
  `app/ui/mesh_viewer.py`, `app/simulation/{script_gen,multirun}.py`): the Port
  dialog has an **Active (use in the simulation)** checkbox and the Ports /
  Dumps window an **Active on/off** button for the selected port
  (`PortDef.active`, default on). An inactive port stays in the project — the
  list and the project tree mark it `[off]`, the Mesh Viewer draws it grey and
  dashed — but it is not written to the simulation at all: no
  `AddLumpedPort`/`AddMSLPort`, **no port number** (the remaining active ports
  are numbered 1, 2, 3 … consecutively, so S-param columns stay consistent),
  ignored by `validate_mesh`/`generate_mesh` and by "Run All Ports" (which also
  never excites an inactive port). The log lists skipped ports.
  Tests: `tests/test_multirun.py` (skip + renumber + no excitation + generated
  script), `tests/port_defaults_smoke.py` (dialog round-trip, `[off]` markers,
  toggle button + log).

- **Mesh Viewer: the Project / Geometry selection is highlighted**
  (`app/ui/mesh_viewer.py`, `app/ui/main_window.py`): selecting an entry in the
  left Project / Geometry list (or a material in the Geometry tab's list) now
  outlines that object in the viewer — a thick yellow outline in the slice and in
  the Overview, and the slice title shows `[selected: <entry>]`. Matching is by
  the object's box (primitives, ports, NF2FF, dumps) and by name (imported CAD
  objects / materials; the CSX `copper#1` form matches `copper`), so an entry that
  does not reach the current slice plane says `- not in this plane` instead of
  silently showing nothing. Clearing the selection clears the highlight
  (`set_highlight()`/`clear_highlight()`, kept across redraws and refreshes).
  Tests: `tests/mesh_viewer_smoke.py` (box + name matching, outline drawn,
  cleared), `tests/mesh_port_place_smoke.py` (tree selection, material list,
  title notes).

- **Mesh Viewer: "Refresh view (keep zoom)" + toggling the line selection**
  (`app/ui/mesh_viewer.py`): the plot's right-click menu gained **Refresh view
  (keep zoom)** — it redraws the current view from the current mesh data with the
  view type, zoom and pan untouched and the mesh-line selection dropped
  (`refresh_view()`, which also rebuilds the editor list). And a slice click ON an
  already selected line now **undoes** the selection (click again to select it
  back), so the orange highlight can be cleared without touching the editor list.
  Tests: `tests/mesh_viewer_smoke.py` (double-click on a line toggles, refresh
  keeps the exact axes limits, drops the selection, still draws the mesh).

- **Mesh Viewer: "Port mode" builds the port from the metal surfaces**
  (`app/ui/mesh_viewer.py`, `app/ui/main_window.py`, `app/ui/tab_help.py`):
  a click in a slice now creates a **lumped** or an **MSL** port whose box is
  derived from the geometry instead of a zero-length point:
  - the port runs into the screen (along the slice normal) and its **length is
    found automatically**: from the **bottom face of the clicked metal** to the
    **top face of the next metal surface below** it — for a microstrip line that
    is exactly the substrate height down to the ground plane. Is there no metal
    below, the next one above is used (its bottom face); the log names both
    metals. No second metal at the click → the old behaviour (click = Start =
    Stop) plus a hint.
  - the right-click menu gained **"Port type"**: *Lumped* (default) or
    *MSL, width along X/Y/Z* — the entries follow the in-plane axes of the slice
    you are in (XY → X/Y, XZ → X/Z, YZ → Y/Z). An MSL port is as wide as the
    **clicked metal** along the chosen axis and **one mesh cell long** along the
    other in-plane axis (the line, propagation direction — openEMS needs it for
    its propagation sense and its strip); a **lumped** port stays a line without
    any width. Propagation/excitation direction are prefilled for
    `AddMSLPort`; picking an MSL entry also arms port mode. (A *manually* made
    MSL port with zero length along the line is widened by one grid cell at run
    time — see `extend_zero_propagation` — since openEMS would otherwise get a
    zero propagation sense.)
  - the Port dialog opens with the computed box (Start/Stop/Direction/prop/exc
    for MSL) and stays editable; the clicked coordinates stay unsnapped.
  Tests: `tests/mesh_port_place_smoke.py` (strip-over-ground scene: lumped span
  with no width, MSL width + one cell along the line, menu entries per slice,
  fallback, log hints), `tests/test_msl_port.py` (a zero-length MSL box needs an
  explicit propagation axis; `extend_zero_propagation` widens it by one grid
  cell); pure-logic helper `_is_metal()`.

- **Field Viewer: field info + selectable standard view**
  (`app/ui/vtk_viewer.py`, `app/settings.py`, `app/ui/main_window.py`):
  - the top row now shows **which field is on screen** — the loaded file name
    without the frame number, e.g. `Field: E_substrate [V/m]` (bold; the
    tooltip adds the file name, the number of frames of the series and the
    array). New helper `_field_kind()` (``E_substrate_0118`` → ``E_substrate``,
    also used for the pop-out window title `Field Viewer - E_substrate`);
  - a **"View" box** picks the standard view of the 3D pane: Isometric, From
    ±X/±Y/±Z or "Fit (keep direction)" (the old Reset view behaviour — camera
    direction untouched, only fitted). Picking one applies it at once **and** is
    remembered (`settings.field_viewer_view`), so every field loaded afterwards
    starts in that view; the pop-out mirrors the choice, and the camera maths
    lives in the testable `preset_camera()` helper (centre + axis direction +
    up vector, fitted by `ResetCamera()`). The Settings dialog's Field Viewer
    group got the same combo as "Default view". The new controls (View box, field
    info) and the Video…/Pop out/Reset view buttons sit **left-aligned** with the
    rest of their row — they used to be pushed to the right edge.
  Tests: `tests/vtk_viewer_smoke.py` (`_field_kind`, label text/tooltip, preset
  camera maths incl. empty scene, combo → callback), `tests/test_settings.py`
  (default "keep" + round-trip + old settings without the key).

- **Hide the left "Project / Geometry" pane** (`app/ui/main_window.py`):
  right-click anywhere in the pane — the empty area below the tree, the tree
  background or its title — and pick *Hide Project / Geometry panel* (the
  tree's item menu offers it at the bottom too, after Edit/Duplicate/Remove).
  `View` → **Project / Geometry Panel** (**Ctrl+B**) shows/hides it as well and
  is the way back once it is hidden; the state is remembered
  (`app/settings.py`: `project_panel_visible`) and the splitter width is
  restored on show. Tests: `tests/menu_smoke.py` (`test_project_panel_toggle`,
  Ctrl+B shortcut, context-menu entry); docs: `ANLEITUNG_DE/EN.md`,
  `tab_help` (Geometry topic).

- **Selectable FDTD excitation waveform** (`app/simulation/excitation.py`,
  `app/models/project.py`, `app/simulation/script_gen.py`,
  `app/ui/{main_window,mesh_params,sim_params,tab_help}.py`): the Mesh tab
  (and Mesh Parameters window) gained an **Excitation** combo next to
  f0/fc — *Gauss* (broadband, f0/fc, default and unchanged), *Sinus*
  (`SetSinusExcite(f0)`, narrowband: S-params only meaningful near f0),
  *Step* (`SetStepExcite(f_max)`) and *Dirac impulse*
  (`SetDiracExcite(f_max)`), plus *Custom waveform (CSV)* when the installed
  openEMS provides `SetCustomExcite` (0.0.36 does **not**, so the entry is
  hidden there). The choice is stored as `mesh.excitation` /
  `mesh.excitation_file` (old projects load as Gauss), applied by the generated
  `project_run.py` through one shared helper, mirrored in the Mesh Parameters
  window and the Sim Params readout, with fc disabled for non-Gauss kinds
  (Start/Stop still define mesh + S-param band). Custom CSV: two columns
  (time [s], amplitude), `,`/`;`/tab/space separated, optional header,
  `#`/`%` comments, validated (>= 2 rows, time starts at 0 and increases).
  Tests: `tests/test_excitation.py` (19 cases) + excitation checks in
  `tests/generic_workflow_smoke.py`.

- **Open a project from the command line** (`app/main.py`): a plain file
  argument (``python main.py work/My_Project.json`` — also with the EXE, e.g.
  *Open with*) is opened right after the window shows; non-file arguments and
  Qt options are ignored so the GUI always starts. Test:
  `tests/test_main_cli.py`.

- **Mesh Viewer: "Port mode" — place a port by clicking in a slice**
  (`app/ui/mesh_viewer.py`, `app/ui/main_window.py`, `app/ui/tab_help.py`): the
  plot-area right-click menu gained a checkable **Port mode (click in a slice to
  place)** entry. While it is on, a left click anywhere in an XY/XZ/YZ slice
  reports the **unsnapped** coordinates (the two in-plane axes from the click,
  the third from the current slice plane), draws a **red dot** with its (x, y)
  label and opens the existing **Port dialog** with the clicked point prefilled
  in **Start and Stop** (drawing units) — name/type/resistance/direction/
  mesh-dirs stay editable, and Start/Stop must differ (otherwise a warning and
  the dialog re-opens). On OK the port is appended to the project, immediately
  drawn in the slices (`MeshGeometryViewer.set_ports`) and listed in the
  component tree + the Ports / Dumps window; the red marker is removed when the
  dialog closes. Port mode shows a crosshair cursor and logs a hint; the normal
  line-editing click behaviour returns when it is switched off.
  Test: `tests/mesh_port_place_smoke.py` (toggle, unsnapped placement, marker
  lifetime, list/overlay update, no manual-line side effect).

- **Field Viewer: slice position readout + numeric input**
  (`app/ui/vtk_viewer.py`): next to the Slice slider there is now a label with
  the exact position as number and unit (`z = 12.345 mm`) and a spin box for
  **direct numeric input** (mm, Enter/focus-out applies; range follows the data
  extent). A typed value is used exactly (`_slice_pos_exact`) instead of the
  slider's 1/1000 quantisation, is clamped to the data extent and moves the
  slider along; dragging the slider takes over again (and a slice-axis change
  re-derives the position from the slider). The slice controls use mm while the
  3D axes keep metres (tooltips + Help tab say so). Smoke:
  `tests/vtk_viewer_smoke.py` (readout while dragging, exact typed value, clamp,
  slider/axis taking over, tooltips).

- **Field Viewer: physical unit of the displayed values**
  (`app/postproc/vtk_export.py`, `app/ui/vtk_viewer.py`): openEMS dumps carry
  no unit attribute, so the unit is derived from the dump name
  (``field_unit()``: E ? V/m, H ? A/m, D ? As/m?, J ? A/m?; per-step suffixes
  and titles like "E-field" are handled, unknown names stay unit-less). The
  colour bar title now shows it (`magnitude [V/m]`), the status line next to
  the sliders repeats range + unit, and the "Array" combo tooltip names it.
  Test: `tests/test_vtk_export.py::test_field_unit_from_dump_name` +
  `tests/vtk_viewer_smoke.py` (loaded Et-E ? V/m, bar title, status text).

### Changed

- **Mesh Viewer: finer slice-plane slider** (`app/ui/mesh_viewer.py`): the
  plane slider now spans **2000 steps** across the domain (2× the old 0..1000)
  and its widget got a wider minimum (320 px) so dragging is less twitchy.
  Holding **Ctrl** while dragging switches to the raw slider resolution —
  **10× finer (20000 steps)** — **and turns the mesh-line snapping off**, so the
  finer steps are actually usable (normal mode kept snapping, which made both
  modes land on the same mesh line: the fine mode had no visible effect).
  Ctrl is read from the slider's mouse events (event filter) with
  `QApplication.keyboardModifiers()` as fallback.
- **Mesh Viewer: readouts + tooltips for both sliders**
  (`app/ui/mesh_viewer.py`): a live hint next to the plane slider shows the
  Ctrl mode ("· fine" while Ctrl is held) — the position itself is only in the
  spinbox, no duplicate "z = 12.345 mm" label; a label next to the grid slider
  shows its strength ("35%", "(grid off)" while hidden).
  Both sliders and labels have explicit tooltips (plane: snapping / Ctrl mode /
  needs a built mesh; grid: opacity + line width, only while "Show mesh grid").
  Smoke: `tests/mesh_viewer_smoke.py` (range, quantisation to every 10th step,
  Ctrl keeps the 1-step position AND skips snapping, label texts/tooltips).
- **Field Viewer: duplicate slice-position label removed**
  (`app/ui/vtk_viewer.py`): the label next to the slice spinbox repeated the
  value the spinbox already shows (`z = 1.650 mm`); the spinbox is now the only
  readout (the axis combo already shows X/Y/Z and is kept in sync by the
  slider).

### Fixed

- **Highlighting in the Mesh Viewer did not follow the "Project / Geometry"
  selection** (`app/ui/mesh_viewer.py`, `tests/mesh_viewer_smoke.py`): clicking
  an entry updated the stored highlight but not the picture, so the slice kept
  the outline and the `[selected: …]` title of the *previously* clicked object
  (report: `Prim: msl_line` selected in the list, `Prim: msl_stub` still
  highlighted — same for the boards). Cause: `focus_highlight()` moved the slice
  plane onto the object's middle with `spn_plane.setValue(mid)` and relied on
  its `valueChanged` signal for the redraw. The plane spinbox rounds to 4
  decimals and the CSX boxes are float32-rounded, so that `setValue()` was
  usually a no-op → no signal → no redraw. Now the slice is always repainted
  when the value does not actually change — and also when the selection matches
  no drawn element (e.g. an imported CAD entry) or when Overview / Cell sizes is
  shown. Test: `tests/mesh_viewer_smoke.py` (two clicks on the same plane must
  swap the title; a non-matching selection must refresh it too).

- **A port placed by a click in the Mesh Viewer aborted the run
  ("mesh validation failed")** (`app/meshing/port_align.py` (new),
  `app/meshing/easy_wrapper.py`, `app/meshing/build_task.py`,
  `app/meshing/validators.py`): port mode deliberately keeps the clicked
  coordinate (no snapping, no manual mesh lines). easyMesh registers a port as an
  ordinary — usually zero-length — polygon edge, so
  `handle_closely_placed_edges`/`SmoothMeshLines` could delete that coordinate
  and leave an interpolated line a few microns away (in `work/pcb_notch_ports1`
  the clicked `y=0.0987` was merged into the auto line at `0.0882`). openEMS then
  snaps the port box onto the nearest grid lines — a line port silently moves,
  an area port even changes its width — and the validator aborted the whole run
  with `Port 'in3': y=0.0987 is not on a mesh line`. New pass
  `port_align.ensure_port_lines()` runs after easyMesh (and again after the
  Mesh-Editor overrides) and re-asserts every coordinate of every **active**
  port box: a nearby interpolated line is moved onto the port coordinate (no
  tiny cell), otherwise a line is inserted; metal/dielectric faces are never
  moved. Only a port face within 15 % of a local cell of a *geometry* face is
  left to openEMS' snap and now produces the **warning** `port_snapped` instead
  of an error. Switched-off ports are no longer validated (the run script drops
  them before meshing) and `build_task` skips them as well, so the preview mesh
  matches the simulated one. Tests: `tests/test_port_align.py` (new),
  `tests/test_models.py` (inactive port, sub-cell snap).

- **Main window: the "Project / Geometry" pane could not be resized any more**
  (`app/ui/mesh_viewer.py`, `app/ui/vtk_viewer.py`, `app/ui/sparam_viewer.py`,
  `app/ui/antenna_viewer.py`): not the splitter was the problem — the tab pages
  *behind* it defined the minimum width of the whole window. The Mesh Viewer
  control bar was a single ~2040 px row and the Field Viewer's ~1670 px, so the
  horizontal splitter was over-constrained and refused every drag (the left pane
  sat at its own minimum). Now the bars are split over two rows and the longest
  button/label texts are short (the tooltips keep the full wording), which drops
  the page minima to: Mesh Viewer 2054 → 930 px, Field Viewer 1676 → 793 px,
  S-Params 1008 → 798 px, Antenna 1140 → 1014 px. Dragging the divider is smooth
  again over its whole range. Renamed (tooltips/help updated): "Show mesh grid"
  → "Mesh grid", "Show geometry" → "Geometry", "Show ports" → "Ports",
  "Show NF2FF" → "NF2FF", "Pop-out viewer…" → "Pop-out…",
  "Representation" → "Show", "Auto (data range)" → "Auto",
  "Rescale to data range" → "Rescale", "Open Touchstone / JSON…" → "Open…",
  "Open nf2ff.json…" → "Open…". Also: the Field Viewer slice spinbox spans
  ±1 km instead of ±1e9 mm (a wider range only widened the box), and the
  slice/iso-level sliders no longer stretch over the whole row — they are capped
  at 320/200 px (the slice slider was ~740 px at a 1600 px window), with the
  leftover space collecting in front of the range/unit readout at the right edge.

- **Right-click in the log area** (`app/ui/main_window.py`): the log-area menu
  (show/hide the log panel, log tabs, auto-scroll, Save/Pop out log) was only on
  the thin status bar below — a ~25 px strip, partly covered by the progress
  bars, so it was easy to miss. It now opens anywhere in the log: panel
  background, tab bar, the Clear/Pop-out/Save/Auto-scroll buttons, the line
  counter and the divider between the tabs and the log. In the log text the
  standard **Copy / Select All** entries are kept above the options. The menu
  also gained a checkable **Show Project / Geometry panel** entry next to
  "Show log panel" (Ctrl+B), and the status-bar tooltip mentions both panels.
  Tests: `tests/run_log_smoke.py` (menu entries incl. Copy, both panel toggles
  really hide/show).

- **Field Viewer: Auto / Log scale / Rescale corrupted each other**
  (`app/ui/vtk_viewer.py`, `app/ui/number_field.py`): the colour limits were a
  `QDoubleSpinBox` with 6 decimals, so field magnitudes like `1.26e-9` were
  displayed as `0.000000` and a manual limit below ~5e-7 could not be entered
  at all (it silently became 0) — log scaling looked broken. On top of that
  the min/max fields were rewritten with the *current frame's* range on every
  time step (manual limits did not survive playback), the log minimum was
  taken per frame (colours drifted while playing), and "Rescale to data range"
  used the current frame instead of the series. Now:
  - the limits use a new scientific field (`app/ui/number_field.py`,
    `SciSpinBox`; text helpers in `app/numfmt.py`): shows `%.6g`
    (`0.095199`, `1.264e-09`) and accepts `1.5e-9` as well as `1,5e-9`;
  - **Auto follows the current frame** (like ParaView's "Rescale to Data
    Range"): every frame uses the full colour table, so changes over time stay
    visible. A range over the whole series is wrong for openEMS data — the
    excitation pulse (frames ~15–22, max 34.7) is 100× larger than the rest
    (median frame max 0.28), so all later slices landed in the bottom 1 % of
    the table and looked uniformly blue;
  - in log mode **Auto starts at the 1 % percentile of the positive values of
    the frame** (at least `1e-7 × maximum`, because real dumps contain
    denormal magnitudes such as 5e-35 next to 35). The plain minimum — a single
    near-zero cell — stretched the table over up to 30 decades and was the
    second reason for the one-colour look. Values below the floor are drawn in
    the lowest colour; the floor is shown in the field;
  - **manual**: the values are the user's range and are no longer overwritten
    by a frame change; untick Auto to freeze the range that was last shown, so
    one scale stays for all frames. In log mode a minimum of 0 is replaced by
    the percentile floor (and shown), so the numbers match the colour bar;
  - **"Rescale to data range"** uses the current frame and is disabled while
    Auto is on (that is exactly what Auto does);
  - the series-wide range scan (`_series_range_for`) is gone — loading a series
    no longer reads every frame up front (that froze the GUI for ~23 s on a
    236-frame series).
  Smoke: `tests/vtk_viewer_smoke.py` (per-frame Auto range, percentile log
  floor + noise guard, freezing on Auto-off, limits survive frame changes,
  scientific input incl. comma/invalid text, rescale to the current frame,
  button gating) + `tests/test_number_field.py` (format/parse round trip).

- **Field Viewer: "Export project dumps → VTK" froze the GUI on large dumps**
  (`app/postproc/vtk_export.py`, `app/ui/vtk_viewer.py`): the export ran
  synchronously, wrote a per-step `.vtr` for **every** stored time step of
  **every** dump and loaded the whole series into RAM first — a 282-step
  dump meant ~4 GB of files and minutes of a window that looked hung (no
  progress, no cancel, and killing it left a half-written series). Now:
  - a modal **progress dialog** (file/step text, Cancel) keeps the window
    repainting and stops cleanly between steps (`should_stop` in
    `export_dumps_to_vtk` / `dump_series_to_vtr`); a cancelled series gets no
    `.series.json`, so the next run rebuilds it instead of trusting the partial
    one;
  - the series is **streamed** from the HDF5 file one step at a time (no
    gigabyte RAM spike) and the VTK arrays are written as **float32** (about
    half the file size);
  - **incremental**: outputs newer than their dump are reused, so a second
    click is instant — only changed dumps are converted;
  - leftover `<stem>_NNNN.vtr` steps of an earlier, longer run are removed after
    a successful series export (no phantom frames in the time slider);
  - new **"per-step" checkbox** next to the button (default on, keeps the time
    slider / video export); untick it for a quick static conversion, and
    **"every N. step"** (1…50) keeps only every Nth stored sample — a 470-frame
    12 ns run drops to ~118 frames (~1/4 the disk space) while the Time slider
    still spans the full run. The stride is stored in the sidecar, so changing
    it re-exports the series.
  Tests: `tests/test_vtk_export.py` (progress/total, incremental, stale-step
  cleanup, cancel, stride) + export-button check in `tests/vtk_viewer_smoke.py`.

- **Zero-thickness PEC sheets could vanish from the simulation**
  (`app/csxcad_builder/quantize.py`, `app/meshing/build_task.py`,
  `app/simulation/script_gen.py`): a zero-thickness metal box (ground plane,
  MSL line/stub) is only applied when the grid plane lies *inside* the box.
  float32 quantization puts 1.6 at 1.6000000238 and the mesher can return the
  line a few 1e-15 above that, so CSXCAD silently skipped the primitive
  ("Warning: Unused primitive") — the model lost that metal (a ground plane at
  0 mm then gave S21 = -80 dB). Sheet boxes are now widened by 1 nm per side
  **after** the mesh is generated (the mesher still sees the authored,
  zero-thickness geometry), so the plane cannot fall through the grid. Tests:
  `tests/test_quantize.py`.

- **S-Param Viewer: broken y-scale on traces that were never simulated**
  (`app/ui/sparam_viewer.py`): a single-excitation S-parameter file stores the
  other S-matrix cells as placeholders (NaN in `sparams.json`, 0 in Touchstone).
  Selecting such a trace (e.g. **S12/S22** after exciting only port 1) plotted
  -300 dB data with no usable autoscale, so the plot looked like a broken
  scaling. Empty traces are now detected (`_trace_has_data`), the plot shows a
  message with the reason and the fix (*Simulation ▸ Run All Ports (full
  S-matrix)*), the info line lists the missing traces ("· no data: S12, S22"),
  marker list/cursor report "no data", *Go to min* is a no-op, and Smith-chart
  start/end/labels skip NaN samples. NaN-safe marker rows also fixed the
  literal `\n` in Smith marker labels. Smoke:
  `tests/sparam_viewer_smoke.py` (NaN fixture `work/pcb_notch_ports`).

## [0.5.3] - 2026-10-19

### Fixed

- **`max_grading_ratio = 1.0` produced an invalid mesh and aborted the run**
  (`app/models/project.py`, `app/meshing/{easy_wrapper,build_task,interface_refine,polar}.py`,
  `app/simulation/script_gen.py`, `app/ui/mesh_params.py`): the Mesh Parameters
  field *Max grading ratio* allowed **1.0**. CSXCAD's cell cap
  (`SmoothMeshLines(axis, max_cell, ratio)`) needs ``ratio > 1`` and raised an
  exception with an **empty message**, so the cap did nothing, an oversized PML
  cell survived (e.g. 14.26 mm where lambda/10 = 10.16 mm) and the generated run
  script aborted with ``[ERROR] cell_too_large ... aborting simulation``
  (exit code 2). The spin box now starts at **1.05** and
  `MeshSpec.effective_max_grading_ratio()` clamps to 1.05…10 (0/None -> 2.0) and
  is used by easyMesh, the cell cap, the validator, the interface refinement and
  the polar mesher. The cap now logs ``{e!r}`` instead of grabbing an empty
  ``str(e)``.
  Tests: `tests/test_models.py` (clamping) + `tests/mesh_thread_smoke.py`
  (a real build with `num_lines = 1` and `max_grading_ratio = 1.0`).

## [0.5.2] - 2026-10-19

### Fixed

- **Mesh build died with "zero-size array to reduction operation minimum which
  has no identity"** (`app/models/project.py`, `app/ui/mesh_params.py`,
  `app/ui/mesh_worker.py`): the Mesh Parameters field *Num lines (edge gaps)*
  allowed **1**, and easyMesh samples each edge gap with ``num_lines`` points
  before diffing them — with 0/1 the diff is empty, so numpy aborted the whole
  build with that cryptic error (instead of meshing). The spin box now starts at
  **2** and `MeshSpec.effective_num_lines()` clamps the value to 2..50
  (0/None -> 5) so old projects / hand-edited JSON cannot hit it either.
  Additionally the worker now logs the **full traceback** of a failed mesh build
  (only the message was shown before), and the GUI-side finish errors do too.
  Tests: `tests/test_models.py` (clamping) +
  `tests/mesh_thread_smoke.py` (a real build with `num_lines = 1`).

- **VTK popped up its own "vtkOutputWindow" on the corner marker**
  (`app/ui/vtk_viewer.py`): `_update_corner_marker` called
  `InteractiveOff()` **before** `SetEnabled(1)`, so VTK warned "*Set interactor
  and Enabled before changing interaction*" and — with no console (the bundled
  EXE) — opened a native output window over the GUI. The widget is now enabled
  first and the interaction switched off afterwards (new helper
  `configure_marker_widget`). In addition VTK's messages are redirected to a
  **`vtk_messages.log`** (next to `app_settings.json`, path printed once in the
  log) via `install_vtk_output_window`, so any future VTK warning lands in that
  file instead of a pop-up. Smoke: `tests/vtk_viewer_smoke.py` (no warning in
  the correct order, redirect captures a deliberately wrong order).

## [0.5.1] - 2026-10-19

### Added

- **Field Viewer: MP4 video export of field sequences** (`app/ui/vtk_viewer.py`,
  `app/ui/main_window.py`, `build_exe.py`): a new **Video…** button (also
  Simulation → *Export field video (MP4)…* and the viewer's right-click menu)
  renders the loaded time series to an H.264 **MP4** with the **current view
  settings** — representation, selected array, slice/iso position, colour range
  and log scale, outline, axes options and camera — at the **fps** value from
  the playback controls. Frames are grabbed from the VTK window
  (`vtkWindowToImageFilter`, cropped to even dimensions) and encoded with
  `imageio` + `imageio-ffmpeg`; a progress dialog shows frame *i/n* and can be
  cancelled (partial file removed). Afterwards the viewer is restored to the
  frame/array/colour it showed before. The button is enabled only when a series
  is loaded. `requirements.txt` lists `vtk`, `imageio`, `imageio-ffmpeg`;
  `build_exe.py` bundles imageio's ffmpeg (it was in the exclude list).
  Smoke: `tests/vtk_viewer_smoke.py` (default name, no-GL refusal, top-down
  even-sized frame conversion, button state).

- **Field Viewer: configurable axes** (`app/ui/vtk_viewer.py`,
  `app/ui/main_window.py`, `app/settings.py`, `app/ui/tab_help.py`): the
  orientation axes are explicitly X red / Y green / Z blue, and a new
  **View → Axes** submenu (also in the Field Viewer right-click menu) exposes
  *Show axes*, *Corner marker (ParaView style)* (small fixed triad in the
  lower-left corner instead of the in-scene one), *Labels X/Y/Z*, *Label font*
  (8–24 pt), *Size* (0.5× / 1× / 2× / XXL 4× / XXXL 8×, scales the in-scene
  triad) and
  *Coordinate ruler (numbers)* (numeric x/y/z ticks in metres around the
  dataset bounds via `vtkCubeAxesActor`). The choices persist in
  `AppSettings.field_viewer_axes`, are applied at startup and are mirrored
  between the Field Viewer tab and its pop-out (changes in either direction).
  Tests: `tests/vtk_viewer_smoke.py` (colours/labels/font, menu state, ruler)
  + `tests/menu_smoke.py` (View → Axes wiring + persistence).

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
  batch export; a single `<name>.vtr` (first step) is kept. The tab and the
  menu entry are named **Field Viewer**. Tests:
  `tests/test_vtk_export.py` (+ time-series) and `tests/vtk_viewer_smoke.py`.

- **Field dumps: CSXCAD AddDump parameters in the GUI** (`app/models/project.py`
  `DumpDef`, `app/ui/geometry_editors.py` `DumpDialog`, `app/simulation/script_gen.py`):
  the "Add Field Dump…" dialog now exposes **File type** (HDF5 `.h5` default, or
  **VTK `.vtr` written directly by openEMS**), **Interpolation** (None / Node /
  Cell = CSXCAD `dump_mode`), and an optional **Frequencies** list for
  frequency-domain dumps (comma-separated, overrides the single Frequency).
  `DumpDef` gained `file_type`, `dump_mode`, `frequencies` (old projects load
  with defaults); the run script passes them to `CSX.AddDump(...)`, and SAR dumps
  use the 20..22 type range. The dialog also has **"Fit to geometry"** and
  **"Use NF2FF box"** buttons that fill Start/Stop (the structure bbox + margin,
  resp. the NF2FF integration box) — so a dump is never left as a single point.
  Also a **Sub-sampling** (x/y/z) row (time-domain only, disabled for
  frequency-domain) to record every Nth step — bounds the number of timesteps
  so a **temporal** field animation stays manageable. `DumpDef` gained
  `sub_sampling`; the run script passes it to `CSX.AddDump`. The Field Viewer
  series detection now also accepts openEMS' non-zero-padded `.vtr` series
  (`Et-E_0.vtr`, …) and sorts numerically. Test `tests/dump_params_smoke.py`;
  help updated.

- **Field Viewer film mode** (`app/ui/vtk_viewer.py`): a per-step series now
  plays as a movie — **fps** (1–60, default 4), **Loop** on/off (off stops on
  the last frame), **Auto-play** after loading/exporting, restart from step 0
  when Play is pressed on the last frame, and a **fixed colour range for the
  whole sequence** (computed once per array, cached) so the animation does not
  flicker frame-to-frame. Tests in `tests/vtk_viewer_smoke.py`.

- **Live log for the post-processing phase** (`app/simulation/runner.py`,
  `app/simulation/script_gen.py`): the child is now started with `-u` +
  `PYTHONUNBUFFERED=1`, so the run script's prints appear immediately instead of
  sitting in an 8 kB block buffer until the process exits (the log looked frozen
  after the solver finished). The run script now reports the three post-processing
  stages explicitly — `[1/3] S-parameters …`, `CalcPort done — writing JSON /
  Touchstone / plot`, `[2/3] Field slices … (N image(s))`, `[3/3] NF2FF …` (or
  "no NF2FF box - skipped"), all `flush=True`. Test
  `tests/test_runner_streaming.py` (asserts the first line arrives before exit).

### Fixed

- **Time-domain dumps were not recognised as a series** (`app/postproc/fields.py`,
  `app/postproc/vtk_export.py`): openEMS stores TD steps as ``00000000``,
  ``00000062``, … (zero-padded step index), but the reader only accepted ``t0``,
  ``t1``, … → ``read_dump_series`` returned 0 steps, so "Export project dumps to
  VTK" produced a single frame and the Field Viewer had nothing to animate. New
  ``_td_keys`` accepts both namings (numeric order) and labels numeric keys as
  ``t0``, ``t62``, … Test `tests/test_vtk_export.py`
  (`test_time_series_numeric_keys`).
- **"Sub-sampling" in the dump dialog was documented/used as a time step**: it is
  CSXCAD's *spatial* `sub_sampling` (every Nth mesh line), e.g. 10 on a 15×15×30
  grid gives a 2×2×3 dump. Renamed to **Spatial sub-sampling** with the correct
  tooltip/help; no longer disabled for frequency-domain dumps.

- **"Log scale" had no visible effect** (`app/ui/vtk_viewer.py`): field magnitudes
  normally reach exactly 0, so the auto colour range started at 0 and the
  ``lo > 0`` guard silently fell back to linear. ``_effective_range`` now
  substitutes the smallest positive field value (fallback ``hi*1e-9``) for the
  LUT/mapper while log is on, so the checkbox really switches to log10.
  Smoke covers it.

- **Field Viewer pop-out** (`app/ui/vtk_viewer.py`, `app/ui/main_window.py`): a
  "Pop out" toolbar button (and Simulation -> "Field Viewer (pop-out)…") opens
  the 3D view in its own window; "Dock back", closing the window or clicking
  the button again closes it. The pop-out hosts a **second viewer instance**
  that is loaded with the same file and mirrors representation, array, slice,
  colour range/log scale and playback settings. Re-parenting the native
  `QVTKRenderWindowInteractor` between top-level windows left a stale native
  window behind (duplicated controls/pane in the tab after closing), so the
  widget is no longer moved. Test in `tests/vtk_viewer_smoke.py` (pop-out,
  close via the window, assert the tab layout is unchanged).

- **Opening a project without results empties the viewers**
  (`app/ui/main_window.py`, `app/ui/sparam_viewer.py`,
  `app/ui/antenna_viewer.py`, `app/ui/vtk_viewer.py`): `_load_last_results` now
  always reconciles every result viewer with the opened project — S-Params,
  Antenna/NF2FF and the Field Viewer are restored when the project has data for
  them (the Field Viewer now also picks up the newest `<project>/vtk/*.vtr`
  series) and **cleared otherwise**, so a project whose results were deleted no
  longer shows the previous project's diagrams. New viewers get a `clear()`
  method; **New project** clears them too. Smoke:
  `tests/project_results_reload_smoke.py` (clears + restores the field dump).

  Fixed a false positive of that rule: the legacy ``<work>/<Project.name>/
  simulation`` S-param fallback used the *saved* project name, so a project
  whose ``name`` was a leftover like "untitled" loaded an unrelated
  ``<work>/untitled/simulation/sparams.s1p`` even though its own results were
  deleted. The fallback now applies only to projects that record no results at
  all (and never for generic names). The restore log prints full paths, and
  opening the Antenna tab no longer auto-loads the newest NF2FF of the whole
  work dir (only this project's own).

  The Antenna tab's "Loaded files" list now starts **empty**: `set_work_dir`
  only remembers the directory, and the list is filled exclusively by
  "Scan work dir" (or when a file is opened explicitly). `antenna_viewer_smoke`
  asserts the empty start and that `set_work_dir` does not rescan.

- **S-Params "Scan work dir" searched only the project folder**
  (`app/ui/sparam_viewer.py`, `app/ui/main_window.py`): the S-param tab is
  scoped to the current project (`set_work_dir(project_dir)`), so *Scan work
  dir* found nothing after a project without results was opened. The button now
  uses `scan_root` (the global work directory, kept in sync by Settings) via a
  dedicated `_on_scan_workdir()` slot, so it lists the S-params of **all**
  projects; the loaded file stays selected without being reloaded.
  `find_latest_sparam_files` also grew to two nested levels and now finds files
  *directly* in a project folder and in multi-run sub-folders
  (`<work>/<proj>/<sub>/simulation/`) — one result per directory, `sparams.s1p`
  preferred. Test: `tests/mainwindow_sparam_smoke.py` (scan finds another
  project's S-params while the scope stays empty).

- **Run log: START banner, elapsed step lines, END banner** (`app/runlog.py`,
  `app/ui/main_window.py`): every Run Simulation / Run All Ports opens a log
  session with

  ```
  ########### START (14:03:07) #############
  Run: Run Simulation

  --- [+0.1 s] Step: Preparing run (project, work dir, geometry) ---

  --- [+0.3 s] Step: Generating run script ---

  --- [+2.4 s] Step: Starting openEMS (FDTD) ---
  ...
  --- [+1 m 23.4 s] Step: openEMS finished — loading results ---
  ################ END (14:04:30, duration 1 m 23.4 s) ###############
  ```

  Every step line is marked (`--- … ---`) and preceded by a blank line.
  Elapsed times are measured from the START time and formatted as
  `12.3 s` / `1 m 23.4 s` / `1 h 02 m 03.4 s`; the END banner carries the
  wall-clock end time and the total duration. The session is closed after the
  results are loaded into the viewers, on abort, on a failed start and after a
  failed/aborted multi-run merge (only once — a late poll cannot repeat it).
  Run All Ports logs each excitation as a step. The busy check now runs before
  the run is prepared. Fixed the S-params-missing dialog (`{sim}` → `{work}`).
  Tests: `tests/test_run_log.py` (format) + `tests/run_log_smoke.py` (sequence,
  no duplicate END).

- **Field Viewer started "empty" until you zoomed in** (`app/ui/vtk_viewer.py`):
  the orientation triad was a fixed 10×10×10-unit actor. For the metre-sized
  (or smaller) openEMS domains it dominated `ResetCamera()`, so the field filled
  only a few pixels. The triad now scales with the dataset (18 % of the
  bounding-box diagonal) and sits at the lower corner, and
  `ResetCameraClippingRange()` keeps the near/far planes tight after a reset.
  Test: `tests/vtk_viewer_smoke.py` (`axes_total_length`).

- **Bottom-edge right-click menu for the log area** (`app/ui/main_window.py`):
  a right-click on the status bar (or on its engine/progress widgets at the
  bottom right) opens a menu with **Show log panel** (checkable), the log-area
  tabs (*Log*, *Mesh Report*, *Mesh Viewer Scene Info*), *Auto-scroll*,
  *Save log…* and *Pop out log…*. Choosing a tab also re-shows a hidden panel;
  the status bar carries a tooltip pointing at the menu.

- **Auto-play defaults to OFF + configurable in Settings** (`app/settings.py`,
  `app/ui/main_window.py`, `app/ui/vtk_viewer.py`): the Field Viewer no longer
  starts the animation on its own; the "Auto-play" checkbox (and its default,
  remembered across sessions as `field_viewer_autoplay`) can be set in
  **Settings → Settings… → Field Viewer**. The **Tools menu was renamed to
  Settings**. The step counter now reads `3/153 (step 124)` instead of `(t124)`
  so it cannot be mistaken for a time without a unit; the physical time next to
  it always carries its unit (`t = 84.63 ps`, `t = 1.5 ns`, `f = 2.23 GHz`).

- **Field Viewer: physical time on the slider + right-click menu**
  (`app/postproc/vtk_export.py`, `app/ui/vtk_viewer.py`): the frame's simulation
  time is read from the openEMS HDF5 dataset attribute (`time`, seconds) and
  shown next to the step counter in an automatic ps/ns unit (e.g. `t = 84.63 ps`);
  frequency-domain steps show `f = 2.23 GHz` instead. `read_dump_series(...,
  with_meta=True)` returns the per-step time/frequency and the VTK export writes
  them into `<name>.series.json` (`times`, `frequencies`), so older sidecars
  simply omit the label. A **right-click menu** on the controls/background offers
  Open…, Export dumps → VTK, Pop out/Dock view and Reset view; it is suppressed
  inside the 3D pane (VTK navigation uses the right button there). Smoke covers
  the label formatting and the pane exclusion.

- **Solver Parameters dialog** (`app/ui/solver_params.py`,
  `app/models/project.py` `SimSpec`, `app/simulation/script_gen.py`,
  Simulation menu): new **Solver Parameters…** action exposing the openEMS FDTD
  **initialization** (OverSampling, TimeStepFactor, TimeStepMethod, MaxTime) and
  the **Run()** options (verbose, engine, cleanup, setup_only, disable_dumps,
  dump_statistics, debug_material/-PEC/-boxes/-operator/-CSX,
  showProbeDiscretization, legacyHDF5Dumps, cell_constant_material), per the
  openEMS "Simulation" concepts page. `SimSpec` gained the fields (old projects
  load with defaults = previous behaviour); the run script passes them to
  `openEMS(**init)` / `FDTD.Run(**run_kw)` and stops after the setup for
  `setup_only`. Test `tests/solver_params_smoke.py`; help updated.

### Fixed

- **VTK export produced nothing for real field dumps (axis order)**
  (`app/postproc/vtk_export.py`): openEMS stores the dump field with the spatial
  axes REVERSED (``z, y, x``) relative to the ``Mesh/{x,y,z}`` coordinate arrays,
  so a real dump (field ``(3, nz, ny, nx)`` vs mesh ``nx,ny,nz``) failed the
  ``mesh does not match field`` check and was skipped — only single-point (all
  axes 1) or synthetic same-order dumps ever converted, which is why the Field
  Viewer showed empty/„no dumps". ``_orient_field`` now detects and transposes
  the reversed layout (single dumps and time series); verified on
  ``Dipol_CST_mesh_test`` (``Et-E.vtr`` 162 kB, ``Et-H.vtr`` 179 kB).
  Test `tests/test_vtk_export.py` (+ reversed-axis).

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
[0.5.1]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.1
[0.5.2]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.2
[0.5.3]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.3
[0.5.4]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.4
[0.5.5]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.5
[0.5.6]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.6
[0.5.7]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.7
[0.5.8]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.8
[0.5.9]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.9
[0.5.10]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.5.10
[0.6.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.6.0
[Unreleased]: https://github.com/istra711/VisualOpenEMS/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.10.0
[0.9.2]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.9.2
[0.9.1]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.9.1
[0.9.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.9.0
[0.8.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.8.0
[0.7.0]: https://github.com/istra711/VisualOpenEMS/releases/tag/v0.7.0
