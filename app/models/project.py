"""Project data model for FreeCAD -> openEMS simulations."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

from app import PROJECT_EXT

MaterialKind = Literal["pec", "dielectric", "conducting_sheet", "lumped_rlc", "ignore"]
PortType = Literal["lumped", "msl", "rect_waveguide", "waveguide", "probe"]
DumpType = Literal[
    "E", "H", "J", "Jtot", "D", "B", "SAR", "SAR1g", "SAR10g", "SARraw", "custom"
]

#: Metres per drawing unit — the single source for every unit conversion
#: (CSXCAD delta unit, STEP export, grid JSON).
UNIT_TO_METERS = {"m": 1.0, "cm": 1e-2, "mm": 1e-3, "um": 1e-6}


def mm_per_unit(unit: str) -> float:
    """Millimetres per drawing unit (``m``/``cm``/``mm``/``um``)."""
    try:
        return UNIT_TO_METERS[str(unit or "mm").lower()] * 1000.0
    except KeyError:
        raise ValueError(
            f"unsupported unit {unit!r} (expected m, cm, mm or um)"
        ) from None
MeshPreset = Literal["low", "medium", "high", "very_high", "extreme", "custom"]
BoundaryKind = Literal["PEC", "PMC", "MUR", "PML"]

#: Mesh density (cells per free-space wavelength) per mesh preset.
#: Mirrors easyMesh's mesh_resolution -> max_cellsize_air mapping
#: (easyMesh/meshingUtils.py) so the GUI dropdown, the antenna override and
#: the mesher (cartesian + polar) all agree.
CELLS_PER_WAVELENGTH_BY_PRESET: dict[str, float] = {
    "low": 10.0,
    "medium": 15.0,
    "high": 20.0,
    "very_high": 25.0,
    "extreme": 40.0,
}


@dataclass
class VariableDef:
    """A named drawing-unit value used by geometry expressions."""

    name: str
    value: float = 0.0
    comment: str = ""


@dataclass
class MaterialDef:
    name: str
    kind: MaterialKind = "pec"
    epsilon: float = 1.0
    permeability: float = 1.0
    kappa: float = 0.0  # conductivity S/m
    tand: float = 0.0
    color: tuple[float, float, float, float] = (0.8, 0.5, 0.2, 1.0)


@dataclass
class GeomAssignment:
    """Maps a FreeCAD/exported object to an EM role."""

    object_name: str
    role: MaterialKind = "ignore"
    material: str = ""  # MaterialDef.name when role is dielectric/pec/etc
    priority: int = 10
    notes: str = ""


@dataclass
class PortDef:
    name: str
    type: PortType = "lumped"
    # start/stop in drawing units (model coordinates)
    start: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    stop: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    # lumped: R, L, C; waveguide: excitation settings
    resistance: float = 50.0
    inductance: float = 0.0
    capacitance: float = 0.0
    direction: str = "x"  # x|y|z for waveguide ports
    excite_port: bool = True
    #: Excitation phase in degrees at the project's target frequency.  Used only
    #: for multi-port runs where several ports are excited at once (e.g. the two
    #: orthogonal feeds of a CP patch driven by an ideal 90 deg hybrid).
    #: openEMS implements it as an equivalent non-negative excitation delay.
    excite_phase_deg: float = 0.0
    #: False = the port is switched off: it is not written to the simulation
    #: (no AddLumpedPort/MSLPort), does not get a port number and is ignored by
    #: the S-param / multi-run bookkeeping (kept in the project, greyed out)
    active: bool = True
    # MSL (microstrip) ports only: propagation + excitation axis (x|y|z).
    # ``direction`` above stays the lumped/waveguide normal axis. Empty means
    # "derive from the port box": propagation = longest edge, excitation =
    # shortest edge (openEMS MSL tutorial convention).
    prop_direction: str = ""
    exc_direction: str = ""
    # mesh hints
    mesh_dirs: str = "xyz"
    metal_edge_res: float | None = None
    # Lumped ports only: ``"line"`` = a 1-D port with no width, or an in-plane
    # axis letter ("x"/"y"/"z") = as wide as the metal the port was placed on
    # along that axis (an area/sheet port, e.g. the strip width of a microstrip
    # cross-section). openEMS only sees ``start``/``stop`` - the box itself
    # carries the width; the GUI uses this to rebuild the box when the mode is
    # changed (Port dialog, "Lumped, width along X/Y" in the Mesh Viewer).
    lumped_width: str = "line"
    #: Name of the metal primitive the port was placed on (Mesh Viewer port
    #: mode) - used to re-derive the metal width and shown in the log. Empty
    #: for ports typed in by hand or imported from FreeCAD.
    metal: str = ""
    #: rect_waveguide only: cross-section in drawing units (mm when the project
    #: is in mm). ``wg_a`` is the extent along the first transverse axis - the
    #: axis that follows ``direction`` in the cycle x -> y -> z -> x - and
    #: ``wg_b`` along the second. For a guide along z that is a = x, b = y;
    #: along x it is a = y, b = z; along y it is a = z, b = x. openEMS wants
    #: metres, the run script converts with the project unit.
    wg_a: float = 0.0
    wg_b: float = 0.0
    #: rect_waveguide only: four-character TE mode name ("TE10").
    wg_mode: str = "TE10"
    #: Reverse the port: Start and Stop are swapped when the run script is
    #: generated. openEMS derives the port polarity from the sign of
    #: (stop - start) (`LumpedPort.direction` -> excitation and current probe,
    #: `MSLPort.direction` -> propagation sense), so this flips the port
    #: direction without moving its box. The geometry stays as drawn.
    reverse: bool = False
    #: Named-variable expressions that produced the numeric start/stop (and
    #: optionally wg_a/wg_b) fields. Keys use ``start.0``, ``stop.1``, ``wg_a``;
    #: values are strings or ``{"expr": ..., "scale": ...}`` for scaled fields.
    expressions: dict[str, Any] = field(default_factory=dict)

    def oriented(
        self, start: list[float] | None = None, stop: list[float] | None = None
    ) -> tuple[list[float], list[float]]:
        """``(start, stop)`` as written to openEMS — swapped when ``reverse`` is set.

        openEMS derives a port's polarity / propagation sense from the sign of
        ``stop - start`` (``LumpedPort.direction`` drives the excitation and the
        current probe, ``MSLPort.direction`` the propagation sense), so swapping
        the two ends reverses the port without moving its box. ``start``/``stop``
        may be passed explicitly (the generator hands in the quantised or
        cell-extended box).
        """
        st = list(self.start if start is None else start)
        sp = list(self.stop if stop is None else stop)
        return (sp, st) if self.reverse else (st, sp)


@dataclass
class DumpDef:
    name: str
    quantity: DumpType = "E"
    start: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    stop: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    frequency_domain: bool = True
    frequency: float = 2.4e9
    sub_sampling: list[int] = field(default_factory=lambda: [1, 1, 1])
    enabled: bool = True
    #: CSXCAD AddDump file type: 0 = VTK (.vtr), 1 = HDF5 (default)
    file_type: int = 1
    #: interpolation: 0 = none, 1 = node (default), 2 = cell
    dump_mode: int = 1
    #: optional explicit list of frequencies for a frequency-domain dump
    #: (overrides ``frequency`` when set)
    frequencies: list[float] | None = None
    #: numeric CSXCAD dump type used when ``quantity == "custom"``
    #: (e.g. 12 = J, 21 = 1 g SAR, 29 = raw SAR).  Defaults to 10 (E field).
    custom_dump_type: int = 10
    #: optional CSXCAD spatial dump resolution (drawing units) per axis.
    #: ``None`` = use the mesh; values > 0 resample the dump box.
    opt_resolution: list[float] | None = None
    #: Named-variable expressions for ``start``/``stop`` (keys ``start.0`` etc.).
    expressions: dict[str, Any] = field(default_factory=dict)


@dataclass
class NF2FFDef:
    enabled: bool = False
    # "auto" = fit the integration box to the geometry + air margin whenever
    # NF2FF is switched on; "manual" = keep the explicit start/stop box.
    box_mode: str = "auto"
    start: list[float] = field(default_factory=lambda: [-1.0, -1.0, -1.0])
    stop: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    center: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    radius: float = 1.0
    # evaluation frequencies (Hz); used when freq_mode == "list"
    frequencies: list[float] = field(default_factory=list)
    # "auto" = S11 minimum; "count" = N points over the mesh frequency range
    # (start_frequency..stop_frequency); "list" = explicit frequencies above
    freq_mode: str = "auto"
    # number of equally spaced points when freq_mode == "count"
    freq_count: int = 11
    theta_steps: int = 181
    phi_steps: int = 361
    #: Named-variable expressions for ``start``/``stop`` (keys ``start.0`` etc.).
    expressions: dict[str, Any] = field(default_factory=dict)


@dataclass
class BoundarySpec:
    xmin: BoundaryKind = "PML"
    xmax: BoundaryKind = "PML"
    ymin: BoundaryKind = "PML"
    ymax: BoundaryKind = "PML"
    zmin: BoundaryKind = "PML"
    zmax: BoundaryKind = "PML"
    pml_size: int = 8
    # air margin in drawing units; 'auto' => lambda/4 heuristic
    air_margin: list[float | str] = field(
        default_factory=lambda: ["auto", "auto", "auto", "auto", "auto", "auto"]
    )

    def as_openems_list(self) -> list[str]:
        kinds = [self.xmin, self.xmax, self.ymin, self.ymax, self.zmin, self.zmax]
        out: list[str] = []
        for k in kinds:
            if k == "PML":
                out.append(f"PML_{self.pml_size}")
            else:
                out.append(k)
        return out


@dataclass
class MeshSpec:
    preset: MeshPreset = "medium"
    drawing_unit: str = "mm"  # mm|um|m
    start_frequency: float = 1e9
    stop_frequency: float = 3e9
    target_frequency: float | None = None  # antenna design frequency
    refined_cellsize: float | None = None
    min_cellsize: float | None = None
    max_cellsize: float | None = None
    min_cellsize_z: float | None = None  # separate Z min cellsize
    smooth_metal_edge: str = "one_third_two_thirds"  # False|one_third_two_thirds|extra_lines
    use_circle_detection: bool = False
    use_arc_detection: bool = False
    handle_closely_placed_edges: bool = True
    max_grading_ratio: float = 2.0
    cells_per_wavelength: float = 10.0
    num_lines: int = 5  # extra mesh lines per edge gap
    dirs: str = "xyz"  # mesh axes: xyz|xy|xz|yz|x|y|z
    # Coordinate alignment (default on): every geometry coordinate goes through
    # the same float32 rule (app/csxcad_builder/quantize.py::align_coordinates)
    # that boxes/ports/dumps/NF2FF already use, so triangle-mesh vertices and box
    # faces that should be equal are bit-identical - no second grid line, no
    # sliver cell. Off restores the legacy 0.1 um decimal rounding of triangle
    # meshes (and the strict dedupe tolerance). Old projects miss the key and
    # load with the default (on).
    align_coordinates: bool = True
    # Fine-cell attribution (P2, default on): every cell the report flags is
    # attributed to the primitive / port / manual line that forced it
    # (app/meshing/critical_cells.attribute_cells), shown in the Mesh Viewer
    # marks and in the mesh report ("Fine-cell sources"). Off = skip the
    # attribution (the marks stay, without a source).
    diagnose_cell_sources: bool = True
    # Manual mesh lines (Mesh Editor): per-axis coordinates (drawing units) to
    # ADD on top of the easyMesh-generated grid, and auto lines to REMOVE.
    # Applied app-side AFTER easyMesh (and the cap), so a user edit wins. Keys
    # x/y/z. Cartesian only; None/empty = plain automatic mesh.
    manual_mesh_lines: dict[str, list[float]] | None = None
    manual_mesh_lines_removed: dict[str, list[float]] | None = None
    # Mesh repairs (app/meshing/repair.py): deterministic re-spacing rules for
    # regions whose cells only cost runtime. Each entry is
    # {"axis": "z", "lo": <coord>, "hi": <coord>, "floor": <min cell>,
    #  "expand": 1}. Applied AFTER the manual lines in generate_mesh, so the
    # preview and the solver script build the same grid. A rule never removes
    # an authored coordinate (primitive/port/dump/NF2FF/manual line).
    mesh_repairs: list[dict] | None = None
    # Enforce the requested min_cellsize as a hard lower bound (V2, default
    # off): every cell below it whose faces are not both authored coordinates
    # is merged into its smaller neighbour. Off = easyMesh decides.
    enforce_min_cellsize: bool = False
    # Regions the user marked "do not repair" (e.g. a genuine hairpin gap):
    # [{"axis": "z", "lo": ..., "hi": ..., "note": "geometry gap"}]. The marks
    # stay visible but are reported as accepted instead of open findings.
    accepted_cells: list[dict] | None = None
    # Graded refinement on the vacuum/dielectric side of metal (PEC) faces:
    # finest cell at the interface (min_cellsize), growing by max_grading_ratio
    # into the surrounding air/dielectric. Applied app-side after easyMesh
    # (app/meshing/interface_refine.py); the easyMesh-internal transition pass
    # is not used anymore. Off for old projects, on for new ones.
    add_graded_mesh_lines_at_material_transitions: bool = False
    # Mesh/FDTD coordinate system: "cartesian" (easyMesh, default) or
    # "cylindrical" (polar (r, alpha, z) mesher; openEMS CoordSystem=1).
    # Old project JSONs miss the key and load with the default.
    coord_system: Literal["cartesian", "cylindrical"] = "cartesian"
    # FDTD excitation waveform (app/simulation/excitation.py):
    #   gauss  -> SetGaussExcite(f0, fc)     broadband, default
    #   sinus  -> SetSinusExcite(f0)         narrowband (S-params only near f0)
    #   step   -> SetStepExcite(f_max)       broadband step
    #   dirac  -> SetDiracExcite(f_max)      broadband impulse
    #   custom -> SetCustomExcite(time, values) from excitation_file (CSV,
    #             time in seconds) — only if the installed openEMS has it.
    excitation: str = "gauss"
    excitation_file: str = ""
    # High-level Mesh Parameters profile (UI checkmarks). None = old project;
    # the UI resolves it from the override fingerprint / antenna mode.
    mesh_profile: Literal["default", "antenna", "pcb"] | None = None

    @property
    def is_cylindrical(self) -> bool:
        return self.coord_system == "cylindrical"

    def unit_to_meters(self) -> float:
        return UNIT_TO_METERS[self.drawing_unit]

    def effective_max_grading_ratio(self) -> float:
        """``max_grading_ratio`` clamped to a workable range (1.05 .. 10).

        A ratio of exactly 1.0 disables grading AND makes CSXCAD's
        ``SmoothMeshLines(axis, max_cell, 1.0)`` (the lambda/10 cell cap) fail,
        so an oversized PML cell survives and the mesh validator aborts the
        whole run with "cell_too_large".
        """
        try:
            ratio = float(self.max_grading_ratio or 2.0)
        except (TypeError, ValueError):
            ratio = 2.0
        if ratio != ratio:  # NaN
            ratio = 2.0
        return max(1.05, min(10.0, ratio))

    def effective_num_lines(self) -> int:
        """easyMesh ``num_lines`` clamped to a workable range (>= 2).

        easyMesh samples each edge gap with ``num_lines`` points and diffs them;
        ``0``/``1`` gives an empty diff, so the whole build dies with a cryptic
        "zero-size array to reduction operation minimum" error (numpy). Old
        projects and hand-edited values can still carry 1 -> clamp here.
        """
        try:
            n = int(self.num_lines or 5)
        except (TypeError, ValueError):
            n = 5
        return max(2, min(50, n))

    def global_setup(self, boundary: BoundarySpec) -> dict[str, Any]:
        setup: dict[str, Any] = {
            "start_frequency": self.start_frequency,
            "stop_frequency": self.stop_frequency,
            "drawing_unit": self.unit_to_meters(),
            "mesh_resolution": self.preset if self.preset != "custom" else "medium",
            "boundary_distance": list(boundary.air_margin),
            "smooth_metal_edge": self.smooth_metal_edge
            if self.smooth_metal_edge != "False"
            else False,
            "use_circle_detection": self.use_circle_detection,
            "use_arc_detection": self.use_arc_detection,
            "handle_closely_placed_edges": self.handle_closely_placed_edges,
            # easyMesh inserts ``num_lines`` samples per edge gap and then diffs
            # them; with 0 or 1 the diff is empty and numpy aborts the whole
            # mesh build with "zero-size array to reduction operation minimum".
            "num_lines": self.effective_num_lines(),
            "dirs": self.dirs,
            "coord_system": self.coord_system,
            # NOTE: the metal-interface refinement is applied app-side
            # (app/meshing/interface_refine.py) — do NOT forward it to easyMesh
            # (its internal version is smoothed away / breaks metal-in-air).
            # Clamped: a ratio of 1.0 breaks CSXCAD's SmoothMeshLines cap.
            "max_grading_ratio": self.effective_max_grading_ratio(),
        }
        if self.target_frequency is not None:
            setup["target_frequency"] = self.target_frequency
        if self.refined_cellsize is not None:
            setup["refined_cellsize"] = self.refined_cellsize
        if self.min_cellsize is not None:
            setup["min_cellsize"] = self.min_cellsize
        if self.min_cellsize_z is not None:
            setup["min_cellsize_z"] = self.min_cellsize_z
        if self.max_cellsize is not None:
            setup["max_cellsize"] = self.max_cellsize
        return setup


@dataclass
class SimSpec:
    end_criteria: float = 1e-5
    max_timesteps: int = 1_000_000
    max_time: float | None = None  # seconds; None => use end criteria
    nf2ff_enabled: bool = True
    field_dumps_enabled: bool = True
    num_threads: int = 0  # 0 => all
    output_dir: str = ""  # relative to project work dir if empty
    # Antenna simulation mode (Simulation Parameters → "Antenna simulation"):
    # enforces >=15-20 cells/lambda (mesh preset), PML distance
    # antenna_pml_lambda * lambda(f_start) as air margin, and NF2FF on.
    antenna_mode: bool = False
    antenna_pml_lambda: float = 0.5  # 0.5 => lambda/2 at f_start (hints: lambda/2..lambda)
    # NF2FF integration box distance from the structure, in wavelengths at
    # f_start (lowest band frequency). Only used by "Fit NF2FF to Geometry"
    # while antenna_mode is on; PCB runs keep the small lambda/4-at-f_stop box.
    antenna_nf2ff_lambda: float = 0.5  # 0.5 => lambda/2 (far-field), capped by the PML
    # Mesh density (cells per free-space wavelength at f_max) enforced while
    # antenna_mode is on. None => fall back to ANTENNA_DENSITY_BY_PRESET[mesh.preset].
    antenna_cells_per_wavelength: float | None = None
    # --- Solver options (openEMS init + Run(); see the openEMS "Simulation"
    # concepts page). Defaults reproduce the previous behaviour. ---
    verbose: int = 3  # Run(verbose=…): 0..3
    engine: str = ""  # Run(engine=…): "" = default (fastest)
    setup_only: bool = False  # Run(setup_only=True): preprocess, do not run FDTD
    cleanup: bool = True  # Run(cleanup=…): wipe old outputs first
    disable_dumps: bool = False
    dump_statistics: bool = False
    debug_material: bool = False
    debug_pec: bool = False
    debug_boxes: bool = False
    debug_operator: bool = False
    debug_csx: bool = False
    show_probe_discretization: bool = False
    legacy_hdf5_dumps: bool = False
    over_sampling: int | None = None  # openEMS(OverSampling=…)
    time_step_factor: float | None = None  # openEMS(TimeStepFactor=…) 0<x<=1
    time_step_method: int | None = None  # openEMS(TimeStepMethod=…) 1=CFL 3=Rennings
    cell_constant_material: bool = False  # openEMS(CellConstantMaterial=1)


@dataclass
class Mesh2FreeCADSpec:
    """Mesh → Mesh2FreeCAD export settings (stored in the project file).

    The export writes a temporary discretised FreeCAD document.  These options
    choose what that document contains; they are project-specific because the
    right choice depends on the model size and on the objects in the project.
    """

    export_step: bool = False
    fcstd_object_type: str = "mesh"  # part | mesh
    voxel_grid: bool = True
    voxel_grid_mode: str = "all"  # boundary | all
    include_sheets: bool = True
    export_ports: bool = True
    keep_temp: bool = False


@dataclass
class GerberSpec:
    """Gerber/Excellon import settings stored with the project.

    Gerber carries no stackup information, so these values are user input.
    They are reused on re-import and keep the generated primitives
    reproducible.
    """

    source: str = ""
    board_thickness_mm: float = 1.6
    copper_thickness_mm: float = 0.035
    copper_as_sheet: bool = True
    via_plating_radius_mm: float = 0.025
    via_max_diameter_mm: float = 0.8
    include_npth: bool = False
    arc_error_mm: float = 0.01
    simplify_mm: float = 0.0
    min_area_mm2: float = 0.005
    clip_to_outline: bool = True
    create_substrate: bool = True
    substrate_material: str = "substrate"
    substrate_epsilon: float = 4.4
    substrate_tand: float = 0.02
    #: optional solder mask as a thin dielectric layer (top/bottom)
    include_mask: bool = False
    solder_mask_thickness_mm: float = 0.02
    solder_mask_epsilon: float = 3.3
    solder_mask_tand: float = 0.02
    solder_mask_material: str = "solder_mask"
    #: optional side -> z override, e.g. {"top": 0.8, "inner_1": 0.4}
    layer_z: dict[str, float] = field(default_factory=dict)
    #: drill diameter key ("0.4000") -> [from_side, to_side]; missing = through
    via_spans: dict[str, list[str]] = field(default_factory=dict)
    #: open the port-suggestion dialog after a successful import
    suggest_ports: bool = True

    def import_options(self):
        """Return a :class:`GerberImportOptions` matching this spec."""
        from app.freecad_io.gerber_import import GerberImportOptions

        return GerberImportOptions(
            board_thickness_mm=float(self.board_thickness_mm),
            copper_thickness_mm=float(self.copper_thickness_mm),
            copper_as_sheet=bool(self.copper_as_sheet),
            via_plating_radius_mm=float(self.via_plating_radius_mm),
            via_max_diameter_mm=float(self.via_max_diameter_mm),
            include_npth=bool(self.include_npth),
            arc_error_mm=float(self.arc_error_mm),
            simplify_mm=float(self.simplify_mm),
            min_area_mm2=float(self.min_area_mm2),
            clip_to_outline=bool(self.clip_to_outline),
            create_substrate=bool(self.create_substrate),
            substrate_material=str(self.substrate_material or "substrate"),
            layer_z={str(k): float(v) for k, v in (self.layer_z or {}).items()},
            include_mask=bool(self.include_mask),
            solder_mask_thickness_mm=float(self.solder_mask_thickness_mm),
            solder_mask_epsilon=float(self.solder_mask_epsilon),
            solder_mask_tand=float(self.solder_mask_tand),
            solder_mask_material=str(
                self.solder_mask_material or "solder_mask"
            ),
            via_spans={
                str(k): [str(v[0]), str(v[1])]
                for k, v in (self.via_spans or {}).items()
                if isinstance(v, (list, tuple)) and len(v) == 2
            },
        )


@dataclass
class KiCadSpec:
    """Native KiCad board import settings stored with the project.

    The .kicad_pcb file carries the real stackup, via spans and nets; these
    values only control how the board is turned into openEMS primitives and
    provide fallbacks when a board has no custom stackup.
    """

    source: str = ""
    use_stackup: bool = True
    board_thickness_mm: float = 1.6
    copper_thickness_mm: float = 0.035
    copper_as_sheet: bool = True
    #: "" = derive from copper_as_sheet; "sheet" | "prism" | "mesh"
    copper_mode: str = ""
    substrate_material: str = "kicad_substrate"
    substrate_epsilon: float = 4.4
    substrate_tand: float = 0.02
    via_plating_radius_mm: float = 0.025
    include_npth: bool = False
    include_zones: bool = True
    include_unfilled_zones: bool = False
    include_pad_barrels: bool = True
    include_tracks: bool = True
    include_smd_pads: bool = True
    include_vias: bool = True
    flip_y: bool = True
    #: open the port-suggestion dialog after a successful import
    suggest_ports: bool = True
    arc_error_mm: float = 0.01
    simplify_mm: float = 0.0
    min_area_mm2: float = 0.005
    clip_to_outline: bool = True
    create_substrate: bool = True

    def import_options(self):
        """Return a :class:`KiCadImportOptions` matching this spec."""
        from app.freecad_io.kicad_import import KiCadImportOptions

        return KiCadImportOptions(
            use_stackup=bool(self.use_stackup),
            board_thickness_mm=float(self.board_thickness_mm),
            copper_thickness_mm=float(self.copper_thickness_mm),
            copper_as_sheet=bool(self.copper_as_sheet),
            copper_mode=str(self.copper_mode or ""),
            substrate_material=str(self.substrate_material or "kicad_substrate"),
            substrate_epsilon=float(self.substrate_epsilon),
            substrate_tand=float(self.substrate_tand),
            via_plating_radius_mm=float(self.via_plating_radius_mm),
            include_npth=bool(self.include_npth),
            include_zones=bool(self.include_zones),
            include_unfilled_zones=bool(self.include_unfilled_zones),
            include_pad_barrels=bool(self.include_pad_barrels),
            include_tracks=bool(self.include_tracks),
            include_smd_pads=bool(self.include_smd_pads),
            include_vias=bool(self.include_vias),
            flip_y=bool(self.flip_y),
            arc_error_mm=float(self.arc_error_mm),
            simplify_mm=float(self.simplify_mm),
            min_area_mm2=float(self.min_area_mm2),
            clip_to_outline=bool(self.clip_to_outline),
            create_substrate=bool(self.create_substrate),
        )


@dataclass
class CstSpec:
    """CST Studio export-package import settings stored with the project.

    Variant A reads a CST export package (``manifest.json`` plus one STL per
    solid).  The package is the interchange format; the proprietary ``.cst``
    container itself is never parsed.  These options mirror the import dialog
    and make a re-import reproducible.
    """

    source: str = ""
    include_materials: bool = True
    include_ports: bool = True
    include_frequency: bool = True
    include_boundaries: bool = True
    #: Import zero-thickness/sheet objects too (CST ``sheet`` solids)
    include_sheets: bool = True
    #: Mirror Y (CST exports are z-up; this is only for mirrored CAD models)
    flip_y: bool = False
    #: Ports created by this package on the last import.  A re-import from the
    #: same source replaces exactly these entries; manually added ports are kept.
    port_names: list[str] = field(default_factory=list)

    def import_options(self, target_unit: str = "mm"):
        """Return :class:`CstImportOptions` matching this spec."""
        from app.freecad_io.cst_import import CstImportOptions

        return CstImportOptions(
            include_materials=bool(self.include_materials),
            include_ports=bool(self.include_ports),
            include_frequency=bool(self.include_frequency),
            include_boundaries=bool(self.include_boundaries),
            include_sheets=bool(self.include_sheets),
            flip_y=bool(self.flip_y),
            target_unit=str(target_unit or "mm"),
        )


@dataclass
class GeometryFile:
    path: str
    format: str = "step"  # step|stp|stl|obj|fcstd
    unit: str = "mm"
    # optional saved triangle mesh (.npy) for STL/OBJ/FCStd imports
    mesh_path: str = ""
    object_names: list[str] = field(default_factory=list)


def _path_for_json(value: Any, base: Path, *, relative: bool) -> Any:
    """Convert one path string; non-strings and empty strings pass through."""
    if not isinstance(value, str) or not value.strip():
        return value
    p = Path(value)
    if relative:
        if not p.is_absolute():
            return Path(value).as_posix()
        try:
            rel = os.path.relpath(os.fspath(p), os.fspath(base))
        except ValueError:  # different Windows drive: cannot be relative
            return value
        return Path(rel).as_posix()
    # load: relative paths are anchored at the project JSON's folder
    return str(p if p.is_absolute() else base / p)


def _map_project_paths(data: dict[str, Any], base: Path, *, relative: bool) -> None:
    """Make every project-owned path portable or absolute.

    ``relative=True`` is used before writing: paths under ``<base>`` become
    ``data/...`` instead of ``C:\\Users\\...``.  ``relative=False`` resolves
    those entries again when the JSON is loaded from its (possibly moved)
    folder.  Executable paths (``freecad_cmd``) are intentionally untouched.
    """
    results = data.get("results")
    if isinstance(results, dict):
        for key, value in list(results.items()):
            if key != "time":
                results[key] = _path_for_json(value, base, relative=relative)

    for geom in data.get("geometry_files") or []:
        if isinstance(geom, dict):
            for key in ("path", "mesh_path"):
                if key in geom:
                    geom[key] = _path_for_json(geom[key], base, relative=relative)

    for prim in data.get("primitives") or []:
        if isinstance(prim, dict):
            for key in ("path", "mesh_path"):
                if key in prim:
                    prim[key] = _path_for_json(prim[key], base, relative=relative)

    for section in ("sim", "nf2ff", "gerber", "kicad", "cst"):
        block = data.get(section)
        if not isinstance(block, dict):
            continue
        for key in ("excitation_file", "output_dir", "source"):
            if key in block:
                block[key] = _path_for_json(block[key], base, relative=relative)


@dataclass
class Project:
    name: str = "untitled"
    version: int = 1
    #: Stable identity independent of the folder/file name.  Used to keep
    #: staged/committed run metadata tied to the right project after a Save As.
    project_id: str = ""
    #: Storage layout: 2 = staging/runs/meshes, 1/0 = legacy flat results.
    storage_layout: int = 2
    geometry_files: list[GeometryFile] = field(default_factory=list)
    materials: list[MaterialDef] = field(default_factory=list)
    assignments: list[GeomAssignment] = field(default_factory=list)
    ports: list[PortDef] = field(default_factory=list)
    dumps: list[DumpDef] = field(default_factory=list)
    nf2ff: NF2FFDef = field(default_factory=NF2FFDef)
    mesh: MeshSpec = field(default_factory=MeshSpec)
    boundary: BoundarySpec = field(default_factory=BoundarySpec)
    sim: SimSpec = field(default_factory=SimSpec)
    #: Mesh → Mesh2FreeCAD export options (persisted with the project)
    mesh2freecad: Mesh2FreeCADSpec = field(default_factory=Mesh2FreeCADSpec)
    #: Gerber/Excellon stackup + import options (persisted with the project)
    gerber: GerberSpec = field(default_factory=GerberSpec)
    #: Native KiCad board import options (persisted with the project)
    kicad: KiCadSpec = field(default_factory=KiCadSpec)
    #: CST Studio export-package import options (persisted with the project)
    cst: CstSpec = field(default_factory=CstSpec)
    freecad_cmd: str = ""
    notes: str = ""
    # optional patch-antenna geometry overrides (GUI dialog); None => example defaults
    patch_params: dict[str, Any] | None = None
    # generic CSX primitives (box/cylinder/polygon/stl dicts) for non-patch projects
    primitives: list[dict[str, Any]] | None = None
    #: Named drawing-unit values available to geometry fields.  Primitives keep
    #: their numeric values plus an ``expressions`` map; ports/dumps/NF2FF have
    #: the same map as a dataclass field.
    variables: list[VariableDef] = field(default_factory=list)
    #: Mesh Viewer appearance per object (edited in the object dialogs):
    #: ``{name: {"color": [r, g, b] | None, "transparency": 0-100,
    #: "visible": bool}}`` — ``color=None`` = the per-kind default colours
    object_display: dict[str, Any] = field(default_factory=dict)
    #: artifacts of the last simulation run, written by the GUI so a re-opened
    #: project can restore the Mesh Viewer / S-param Viewer without rebuilding:
    #: {"project_dir", "sim_dir", "csx_xml", "sparams", "nf2ff", "time"}.
    #: On disk these are relative to the project JSON; in memory they are
    #: absolute (Project.load resolves them against the JSON's current folder).
    results: dict[str, Any] | None = None
    #: S-parameter viewer markers persisted with the project:
    #: [{"f": Hz, "tag": str, "delta_ref": Hz | None}, ...]
    sparam_markers: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not str(self.project_id or "").strip():
            self.project_id = uuid.uuid4().hex

    def material_by_name(self, name: str) -> MaterialDef | None:
        for m in self.materials:
            if m.name == name:
                return m
        return None

    def variable_values(self) -> dict[str, float]:
        """Valid named variables as ``{name: numeric value}``."""
        from app.models.variables import variable_values_from_defs

        return variable_values_from_defs(self.variables)

    def effective_mesh(self) -> MeshSpec:
        """MeshSpec that is actually meshed/simulated with.

        While ``sim.antenna_mode`` is on, the antenna checklist owns the mesh
        resolution and overrides the per-project mesh settings: the reference
        wavelength is taken at ``f_max`` (highest band frequency, not the design
        frequency) so the whole band is resolved, and the air cell size is
        capped at ``wavelength(f_max) / density``. ``refined_cellsize`` is only
        tightened, never coarsened, so a finer per-feature refinement survives.
        Returns ``self.mesh`` unchanged when the mode is off.
        """
        import copy

        m = self.mesh
        if not getattr(self.sim, "antenna_mode", False):
            return m
        out = copy.deepcopy(m)
        density = getattr(self.sim, "antenna_cells_per_wavelength", None)
        if not density or density <= 0.0:
            density = CELLS_PER_WAVELENGTH_BY_PRESET.get(getattr(m, "preset", "medium"), 15.0)
        f_max = max(float(m.start_frequency or 0.0), float(m.stop_frequency or 0.0))
        if f_max > 0.0:
            from app.meshing.rules import wavelength_m

            # easyMesh derives its AIR cell size from ``target_frequency``
            # (easyMesh/meshingUtils.py) — point that meshing reference at f_max
            # so the antenna density applies to the TOP of the band, not just to
            # the design frequency (which made the mesh coarser than intended).
            out.target_frequency = f_max
            cell = wavelength_m(f_max, eps_r=1.0) / m.unit_to_meters() / density
            if out.max_cellsize is None or out.max_cellsize > cell:
                out.max_cellsize = cell
            if out.refined_cellsize is None or out.refined_cellsize > cell:
                out.refined_cellsize = cell
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        data = dict(data)

        def _make(dc_cls, d: dict):
            """Construct dataclass, silently dropping unknown keys (old project files)."""
            known = {f.name for f in fields(dc_cls)}  # type: ignore[attr-defined]
            return dc_cls(**{k: v for k, v in d.items() if k in known})

        data["geometry_files"] = [_make(GeometryFile, g) for g in data.get("geometry_files", [])]
        data["materials"] = [_make(MaterialDef, m) for m in data.get("materials", [])]
        data["assignments"] = [
            _make(GeomAssignment, a) for a in data.get("assignments", [])
        ]
        data["ports"] = [_make(PortDef, p) for p in data.get("ports", [])]
        data["dumps"] = [_make(DumpDef, d) for d in data.get("dumps", [])]
        data["nf2ff"] = _make(NF2FFDef, data.get("nf2ff", {}))
        for port in data["ports"]:
            if not isinstance(getattr(port, "expressions", None), dict):
                port.expressions = {}
        for dump in data["dumps"]:
            if not isinstance(getattr(dump, "expressions", None), dict):
                dump.expressions = {}
        if not isinstance(getattr(data["nf2ff"], "expressions", None), dict):
            data["nf2ff"].expressions = {}
        var_data = data.get("variables")
        if isinstance(var_data, list):
            variables: list[VariableDef] = []
            for item in var_data:
                if isinstance(item, dict):
                    variables.append(_make(VariableDef, item))
                elif isinstance(item, VariableDef):
                    variables.append(item)
            data["variables"] = variables
        elif isinstance(var_data, dict):
            # tolerate a simple legacy name -> value mapping
            data["variables"] = [
                VariableDef(name=str(name), value=value)
                for name, value in var_data.items()
            ]
        else:
            data["variables"] = []
        data["mesh"] = _make(MeshSpec, data.get("mesh", {}))
        data["boundary"] = _make(BoundarySpec, data.get("boundary", {}))
        data["sim"] = _make(SimSpec, data.get("sim", {}))
        data["mesh2freecad"] = _make(
            Mesh2FreeCADSpec, data.get("mesh2freecad", {})
        )
        gerber_data = data.get("gerber")
        data["gerber"] = _make(
            GerberSpec, gerber_data if isinstance(gerber_data, dict) else {}
        )
        if not isinstance(data["gerber"].layer_z, dict):
            data["gerber"].layer_z = {}
        if not isinstance(data["gerber"].via_spans, dict):
            data["gerber"].via_spans = {}
        kicad_data = data.get("kicad")
        data["kicad"] = _make(
            KiCadSpec, kicad_data if isinstance(kicad_data, dict) else {}
        )
        cst_data = data.get("cst")
        data["cst"] = _make(
            CstSpec, cst_data if isinstance(cst_data, dict) else {}
        )
        if not isinstance(data["cst"].port_names, list):
            data["cst"].port_names = []
        pp = data.get("patch_params")
        data["patch_params"] = dict(pp) if isinstance(pp, dict) else None
        prims = data.get("primitives")
        data["primitives"] = list(prims) if isinstance(prims, list) else None
        od = data.get("object_display")
        data["object_display"] = dict(od) if isinstance(od, dict) else {}
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        if path.suffix == "":
            path = path.with_name(path.name + PROJECT_EXT)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        # The JSON is the anchor: store project-owned paths relative to its
        # folder so the whole project can be copied/moved without rewriting.
        _map_project_paths(data, path.parent, relative=True)
        # Never truncate a good project file when a write is interrupted.
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        return path

    @classmethod
    def load(cls, path: Path | str) -> "Project":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        # Resolve relative paths against the JSON's *current* location.
        _map_project_paths(data, path.parent, relative=False)
        return cls.from_dict(data)

    @classmethod
    def new_default(cls, name: str = "untitled") -> "Project":
        p = cls(name=name)
        # New projects refine the mesh at metal/vacuum interfaces by default
        # (graded: fine at the metal, coarsening into the air/dielectric).
        p.mesh.add_graded_mesh_lines_at_material_transitions = True
        p.materials = [
            MaterialDef(name="copper", kind="pec", color=(0.85, 0.55, 0.2, 1.0)),
            MaterialDef(
                name="substrate",
                kind="dielectric",
                epsilon=3.38,
                color=(0.2, 0.4, 0.8, 0.6),
            ),
            MaterialDef(
                name="rogers_ro5880",
                kind="dielectric",
                epsilon=2.2,
                color=(0.2, 0.4, 0.8, 0.6),
            ),
            MaterialDef(name="vacuum", kind="dielectric", epsilon=1.0),
            MaterialDef(
                name="conducting_sheet",
                kind="conducting_sheet",
                kappa=5.8e7,
                color=(0.9, 0.7, 0.2, 1.0),
            ),
        ]
        return p
