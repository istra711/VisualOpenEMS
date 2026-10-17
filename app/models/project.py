"""Project data model for FreeCAD -> openEMS simulations."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

from app import PROJECT_EXT

MaterialKind = Literal["pec", "dielectric", "conducting_sheet", "lumped_rlc", "ignore"]
PortType = Literal["lumped", "msl", "rect_waveguide", "waveguide", "probe"]
DumpType = Literal["E", "H", "J", "Jtot", "D", "B", "SAR", "custom"]
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
    # MSL (microstrip) ports only: propagation + excitation axis (x|y|z).
    # ``direction`` above stays the lumped/waveguide normal axis. Empty means
    # "derive from the port box": propagation = longest edge, excitation =
    # shortest edge (openEMS MSL tutorial convention).
    prop_direction: str = ""
    exc_direction: str = ""
    # mesh hints
    mesh_dirs: str = "xyz"
    metal_edge_res: float | None = None


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


@dataclass
class NF2FFDef:
    enabled: bool = False
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

    @property
    def is_cylindrical(self) -> bool:
        return self.coord_system == "cylindrical"

    def unit_to_meters(self) -> float:
        return {"m": 1.0, "mm": 1e-3, "um": 1e-6, "cm": 1e-2}[self.drawing_unit]

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
            "num_lines": self.num_lines,
            "dirs": self.dirs,
            "coord_system": self.coord_system,
            # NOTE: the metal-interface refinement is applied app-side
            # (app/meshing/interface_refine.py) — do NOT forward it to easyMesh
            # (its internal version is smoothed away / breaks metal-in-air).
            "max_grading_ratio": self.max_grading_ratio,
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


@dataclass
class GeometryFile:
    path: str
    format: str = "step"  # step|stp|stl|obj|fcstd
    unit: str = "mm"
    # optional saved triangle mesh (.npy) for STL/OBJ/FCStd imports
    mesh_path: str = ""
    object_names: list[str] = field(default_factory=list)


@dataclass
class Project:
    name: str = "untitled"
    version: int = 1
    geometry_files: list[GeometryFile] = field(default_factory=list)
    materials: list[MaterialDef] = field(default_factory=list)
    assignments: list[GeomAssignment] = field(default_factory=list)
    ports: list[PortDef] = field(default_factory=list)
    dumps: list[DumpDef] = field(default_factory=list)
    nf2ff: NF2FFDef = field(default_factory=NF2FFDef)
    mesh: MeshSpec = field(default_factory=MeshSpec)
    boundary: BoundarySpec = field(default_factory=BoundarySpec)
    sim: SimSpec = field(default_factory=SimSpec)
    freecad_cmd: str = ""
    notes: str = ""
    # optional patch-antenna geometry overrides (GUI dialog); None => example defaults
    patch_params: dict[str, Any] | None = None
    # generic CSX primitives (box/cylinder/polygon/stl dicts) for non-patch projects
    primitives: list[dict[str, Any]] | None = None
    #: artifacts of the last simulation run (absolute paths), written by the
    #: GUI so a re-opened project can restore the Mesh Viewer / S-param Viewer
    #: without rebuilding: {"sim_dir", "csx_xml", "sparams", "nf2ff", "time"}
    results: dict[str, Any] | None = None

    def material_by_name(self, name: str) -> MaterialDef | None:
        for m in self.materials:
            if m.name == name:
                return m
        return None

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
        data["mesh"] = _make(MeshSpec, data.get("mesh", {}))
        data["boundary"] = _make(BoundarySpec, data.get("boundary", {}))
        data["sim"] = _make(SimSpec, data.get("sim", {}))
        pp = data.get("patch_params")
        data["patch_params"] = dict(pp) if isinstance(pp, dict) else None
        prims = data.get("primitives")
        data["primitives"] = list(prims) if isinstance(prims, list) else None
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        if path.suffix == "":
            path = path.with_name(path.name + PROJECT_EXT)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str) -> "Project":
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
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
