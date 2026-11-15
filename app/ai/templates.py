"""Project templates used by the Phase-2 generator.

The factories are deliberately pure Python functions that return a Project and
its primitive list.  They do not touch the main window, the work directory or
any file, so the generator can build and validate a candidate before the user
decides to create it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.models.project import Project


@dataclass(frozen=True)
class TemplateParam:
    key: str
    label: str
    default: Any
    kind: str = "float"  # float | int | choice | text
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    decimals: int = 4
    step: float = 0.01
    choices: tuple[str, ...] = ()
    help: str = ""

    def coerce(self, value: Any) -> Any:
        if self.kind == "choice":
            text = str(value)
            return text if text in self.choices else self.default
        if self.kind == "text":
            return str(value)
        if self.kind == "int":
            number = int(round(float(value)))
        else:
            number = float(value)
        if self.minimum is not None and number < self.minimum:
            number = self.minimum
        if self.maximum is not None and number > self.maximum:
            number = self.maximum
        return int(number) if self.kind == "int" else float(number)


@dataclass
class GeneratedProject:
    template_id: str
    title: str
    project: Project
    primitives: list[dict]
    parameters: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        p = self.project
        lines = [
            f"Template: {self.title} ({self.template_id})",
            f"Project: {p.name}",
            f"Frequency: {p.mesh.start_frequency / 1e9:.6g} .. "
            f"{p.mesh.stop_frequency / 1e9:.6g} GHz; "
            f"target {((p.mesh.target_frequency or 0.0) / 1e9):.6g} GHz",
            f"Mesh preset: {p.mesh.preset}; drawing unit: {p.mesh.drawing_unit}",
            f"Ports: {len(p.ports)}; primitives: {len(self.primitives)}; "
            f"dumps: {len(p.dumps)}",
        ]
        if p.ports:
            lines.append(
                "Port list: "
                + ", ".join(
                    f"{port.name}{' [excited]' if port.excite_port else ''}"
                    for port in p.ports
                )
            )
        if self.parameters:
            lines.append("Parameters:")
            for key, value in self.parameters.items():
                lines.append(f"  {key} = {value}")
        if self.notes:
            lines.append("Notes / assumptions:")
            lines.extend(f"  - {note}" for note in self.notes)
        return "\n".join(lines)


@dataclass(frozen=True)
class ProjectTemplate:
    id: str
    title: str
    description: str
    parameters: tuple[TemplateParam, ...]
    factory: Callable[[dict[str, Any]], tuple[Project, list[dict], list[str]]]
    help: str = ""

    def defaults(self) -> dict[str, Any]:
        return {param.key: param.default for param in self.parameters}

    def coerce(self, values: dict[str, Any] | None) -> dict[str, Any]:
        incoming = dict(values or {})
        out: dict[str, Any] = {}
        for param in self.parameters:
            raw = incoming.get(param.key, param.default)
            out[param.key] = param.coerce(raw)
        return out

    def unknown_keys(self, values: dict[str, Any] | None) -> list[str]:
        known = {param.key for param in self.parameters}
        return sorted(str(key) for key in (values or {}) if str(key) not in known)

    def build(self, values: dict[str, Any] | None = None) -> GeneratedProject:
        params = self.coerce(values)
        project, primitives, notes = self.factory(params)
        project.primitives = list(primitives)
        return GeneratedProject(
            template_id=self.id,
            title=self.title,
            project=project,
            primitives=list(primitives),
            parameters=params,
            notes=list(notes),
        )


# --------------------------------------------------------------------------- #
# factories
# --------------------------------------------------------------------------- #
def _patch_factory(values: dict[str, Any]) -> tuple[Project, list[dict], list[str]]:
    from app.csxcad_builder.primitives import patch_antenna_primitives

    project, primitives = patch_antenna_primitives(
        f0=float(values["f0"]) * 1e9,
        fc=float(values["bandwidth"]) * 1e9 * 0.5,
        substrate_eps=float(values["eps_r"]),
        substrate_thickness=float(values["thickness"]),
        patch_width=float(values["patch_width"]),
        patch_length=float(values["patch_length"]),
        feed_pos=float(values["feed_pos"]),
        feed_r=float(values["feed_r"]),
    )
    project.name = "generated_patch"
    notes = [
        "Patch dimensions are starting values for the requested substrate.",
        "Tune patch_width, patch_length and feed_pos with a sweep/optimizer.",
        "NF2FF is enabled; verify that the box fits inside the mesh domain.",
    ]
    return project, primitives, notes


def _yagi_factory(values: dict[str, Any]) -> tuple[Project, list[dict], list[str]]:
    from app.csxcad_builder.primitives import yagi_antenna_primitives

    project, primitives = yagi_antenna_primitives(
        f0=float(values["f0"]) * 1e9,
        fc=float(values["bandwidth"]) * 1e9 * 0.5,
        element_radius=float(values["element_radius"]),
        boom_radius=float(values["boom_radius"]),
        feed_gap=float(values["feed_gap"]),
    )
    project.name = "generated_yagi"
    return project, primitives, [
        "Yagi element lengths are scaled from f0 by the classic template ratios.",
        "The driven element gap is the lumped 50 ohm port.",
        "The boom is dielectric so it does not short the driven element.",
    ]


def _pcb_filter_factory(
    values: dict[str, Any],
) -> tuple[Project, list[dict], list[str]]:
    from app.csxcad_builder.primitives import pcb_filter_primitives

    project, primitives = pcb_filter_primitives(
        f0=float(values["f0"]) * 1e9,
        substrate_eps=float(values["eps_r"]),
        substrate_tand=float(values["tand"]),
        substrate_thickness=float(values["thickness"]),
        line_width=float(values["line_width"]),
        line_half_length=float(values["line_half_length"]),
    )
    project.name = "generated_pcb_filter"
    return project, primitives, [
        "Two-port microstrip notch filter; run all ports for the complete S-matrix.",
        "The feed line/stub geometry is an initial 50 ohm design.",
    ]


def _pcb_bandpass_factory(
    values: dict[str, Any],
) -> tuple[Project, list[dict], list[str]]:
    from app.csxcad_builder.primitives import pcb_bandpass_stub_primitives

    project, primitives = pcb_bandpass_stub_primitives(
        f0=float(values["f0"]) * 1e9,
        substrate_eps=float(values["eps_r"]),
        substrate_tand=float(values["tand"]),
        substrate_thickness=float(values["thickness"]),
    )
    project.name = "generated_pcb_bandpass"
    if len(project.ports) >= 2:
        project.ports[0].excite_port = True
        project.ports[1].excite_port = False
    return project, primitives, [
        "Five-pole open-stub band-pass; run all ports to complete the S-matrix.",
        "The synthesis is an initial Chebyshev design, not a guarantee of the target bandwidth.",
    ]


def _hairpin_factory(values: dict[str, Any]) -> tuple[Project, list[dict], list[str]]:
    from app.csxcad_builder.hairpin import pcb_hairpin_primitives

    project, primitives = pcb_hairpin_primitives(
        f0=float(values["f0"]) * 1e9,
        fbw=float(values["fbw"]),
        substrate_eps=float(values["eps_r"]),
        substrate_thickness=float(values["thickness"]),
    )
    project.name = "generated_hairpin"
    if len(project.ports) >= 2:
        project.ports[0].excite_port = True
        project.ports[1].excite_port = False
    return project, primitives, [
        "Folded half-wave resonators; the layout is compact but tightly coupled.",
        "The synthesis assumes the stated relative bandwidth.",
    ]


def _gps_factory(values: dict[str, Any]) -> tuple[Project, list[dict], list[str]]:
    from app.csxcad_builder.gps_patch import gps_patch_primitives

    project, primitives = gps_patch_primitives(
        f0=float(values["f0"]) * 1e9,
        eps_r=float(values["eps_r"]),
        h=float(values["thickness"]),
        patch_side=float(values["patch_side"]),
        corner_cut=float(values["corner_cut"]),
        feed_inset=float(values["feed_inset"]),
    )
    project.name = "generated_gps_patch"
    return project, primitives, [
        "Circularly polarised patch; the two chamfers create the two orthogonal modes.",
        "The CP bandwidth is narrow; use a NF2FF frequency list for the axial ratio.",
    ]


def _wr62_factory(values: dict[str, Any]) -> tuple[Project, list[dict], list[str]]:
    from app.csxcad_builder.primitives import wr62_waveguide_primitives

    project, primitives = wr62_waveguide_primitives(
        f0=float(values["f0"]) * 1e9,
        length=float(values["length"]),
        excite=str(values["excite"]),
    )
    project.name = "generated_wr62"
    return project, primitives, [
        "Straight WR62 guide with two rectangular waveguide ports.",
        "The TE10 cutoff is 9.488 GHz; keep f0 above cutoff.",
    ]


# --------------------------------------------------------------------------- #
# catalog
# --------------------------------------------------------------------------- #
_TEMPLATES: dict[str, ProjectTemplate] = {}


def _register(template: ProjectTemplate) -> None:
    _TEMPLATES[template.id] = template


_register(
    ProjectTemplate(
        id="patch",
        title="Probe-fed patch antenna",
        description="Classic rectangular patch with ground plane, substrate and "
        "one lumped probe feed. Best starting point for a 2.4/5.8 GHz antenna.",
        parameters=(
            TemplateParam("f0", "Design frequency", 2.45, unit="GHz", minimum=0.1, maximum=100.0),
            TemplateParam("bandwidth", "Sweep bandwidth", 1.0, unit="GHz", minimum=0.01, maximum=20.0),
            TemplateParam("eps_r", "Substrate epsilon_r", 3.38, minimum=1.0, maximum=50.0),
            TemplateParam("thickness", "Substrate thickness", 1.524, unit="mm", minimum=0.01, maximum=20.0),
            TemplateParam("patch_width", "Patch width", 31.7, unit="mm", minimum=1.0, maximum=1000.0),
            TemplateParam("patch_length", "Patch length", 39.7, unit="mm", minimum=1.0, maximum=1000.0),
            TemplateParam("feed_pos", "Feed position (x)", -6.0, unit="mm", minimum=-1000.0, maximum=1000.0),
            TemplateParam("feed_r", "Feed resistance", 50.0, unit="ohm", minimum=1.0, maximum=1000.0),
        ),
        factory=_patch_factory,
        help="Patch dimensions stay editable as normal project primitives.",
    )
)

_register(
    ProjectTemplate(
        id="yagi",
        title="Yagi-Uda antenna",
        description="Five-element wire Yagi with reflector, driven dipole and "
        "three directors. Uses a dielectric boom and one 50 ohm feed.",
        parameters=(
            TemplateParam("f0", "Design frequency", 2.45, unit="GHz", minimum=0.1, maximum=100.0),
            TemplateParam("bandwidth", "Sweep bandwidth", 0.5, unit="GHz", minimum=0.01, maximum=20.0),
            TemplateParam("element_radius", "Element radius", 1.0, unit="mm", minimum=0.01, maximum=20.0),
            TemplateParam("boom_radius", "Boom radius", 1.5, unit="mm", minimum=0.01, maximum=30.0),
            TemplateParam("feed_gap", "Driven-element gap", 2.0, unit="mm", minimum=0.1, maximum=50.0),
        ),
        factory=_yagi_factory,
    )
)

_register(
    ProjectTemplate(
        id="pcb_filter",
        title="Microstrip notch filter",
        description="Two-port 50 ohm line with an open lambda/4 stub (band-stop). "
        "Useful for a quick PCB feed-line check.",
        parameters=(
            TemplateParam("f0", "Notch frequency", 2.45, unit="GHz", minimum=0.1, maximum=100.0),
            TemplateParam("eps_r", "Substrate epsilon_r", 4.4, minimum=1.0, maximum=50.0),
            TemplateParam("tand", "Loss tangent", 0.02, minimum=0.0, maximum=1.0, decimals=5),
            TemplateParam("thickness", "Substrate thickness", 1.6, unit="mm", minimum=0.01, maximum=20.0),
            TemplateParam("line_width", "50 ohm line width", 3.0, unit="mm", minimum=0.01, maximum=50.0),
            TemplateParam("line_half_length", "Line half length", 32.0, unit="mm", minimum=1.0, maximum=1000.0),
        ),
        factory=_pcb_filter_factory,
    )
)

_register(
    ProjectTemplate(
        id="pcb_bandpass",
        title="Open-stub band-pass filter",
        description="Five-pole Chebyshev band-pass as five open half-wave stubs "
        "with quarter-wave inverters.",
        parameters=(
            TemplateParam("f0", "Centre frequency", 5.6, unit="GHz", minimum=0.1, maximum=100.0),
            TemplateParam("eps_r", "Substrate epsilon_r", 2.1, minimum=1.0, maximum=50.0),
            TemplateParam("tand", "Loss tangent", 0.0002, minimum=0.0, maximum=1.0, decimals=6),
            TemplateParam("thickness", "Substrate thickness", 0.254, unit="mm", minimum=0.01, maximum=20.0),
        ),
        factory=_pcb_bandpass_factory,
    )
)

_register(
    ProjectTemplate(
        id="hairpin",
        title="Hairpin band-pass filter",
        description="Compact five-pole folded-resonator band-pass on a high-epsilon "
        "laminate. No vias required.",
        parameters=(
            TemplateParam("f0", "Centre frequency", 2.4, unit="GHz", minimum=0.1, maximum=100.0),
            TemplateParam("fbw", "Relative bandwidth", 0.10, minimum=0.01, maximum=0.5, decimals=4),
            TemplateParam("eps_r", "Substrate epsilon_r", 10.2, minimum=1.0, maximum=50.0),
            TemplateParam("thickness", "Substrate thickness", 1.27, unit="mm", minimum=0.01, maximum=20.0),
        ),
        factory=_hairpin_factory,
    )
)

_register(
    ProjectTemplate(
        id="gps_patch",
        title="GPS L1 circularly polarised patch",
        description="Chamfered square patch on FR4 with a single probe feed and "
        "a narrow axial-ratio band around 1.57542 GHz.",
        parameters=(
            TemplateParam("f0", "Design frequency", 1.57542, unit="GHz", minimum=0.1, maximum=100.0),
            TemplateParam("eps_r", "Substrate epsilon_r", 4.4, minimum=1.0, maximum=50.0),
            TemplateParam("thickness", "Substrate thickness", 1.6, unit="mm", minimum=0.01, maximum=20.0),
            TemplateParam("patch_side", "Patch side", 44.10, unit="mm", minimum=1.0, maximum=1000.0),
            TemplateParam("corner_cut", "Corner chamfer", 5.0, unit="mm", minimum=0.0, maximum=100.0),
            TemplateParam("feed_inset", "Feed inset", 10.9, unit="mm", minimum=0.0, maximum=1000.0),
        ),
        factory=_gps_factory,
    )
)

_register(
    ProjectTemplate(
        id="wr62",
        title="WR62 rectangular waveguide",
        description="Straight WR62 guide with TE10 excitation and E/H/J field dumps.",
        parameters=(
            TemplateParam("f0", "Design frequency", 10.0, unit="GHz", minimum=9.6, maximum=20.0),
            TemplateParam("length", "Port spacing", 100.0, unit="mm", minimum=5.0, maximum=1000.0),
            TemplateParam("excite", "Excited port", "in", kind="choice", choices=("in", "out")),
        ),
        factory=_wr62_factory,
    )
)


def templates() -> list[ProjectTemplate]:
    return list(_TEMPLATES.values())


def template(template_id: str) -> ProjectTemplate | None:
    return _TEMPLATES.get(str(template_id or "").strip())


def template_ids() -> list[str]:
    return list(_TEMPLATES)


def build_template(template_id: str, values: dict[str, Any] | None = None) -> GeneratedProject:
    tpl = template(template_id)
    if tpl is None:
        raise ValueError(f"unknown template '{template_id}'")
    unknown = tpl.unknown_keys(values)
    if unknown:
        raise ValueError(
            f"unknown parameter(s) for template '{template_id}': "
            + ", ".join(unknown)
        )
    return tpl.build(values)
