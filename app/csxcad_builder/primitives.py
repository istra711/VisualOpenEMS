"""Create CSXCAD primitives for common RF structures (mm drawing units)."""

from __future__ import annotations

import math
from typing import Any

from app.models.project import (
    BoundarySpec,
    DumpDef,
    MaterialDef,
    MeshSpec,
    PortDef,
    Project,
    SimSpec,
)


def patch_antenna_primitives(
    patch_width: float = 31.7,
    patch_length: float = 39.7,
    substrate_eps: float = 3.38,
    substrate_kappa: float | None = None,
    substrate_width: float = 60.0,
    substrate_length: float = 60.0,
    substrate_thickness: float = 1.524,
    substrate_cells: int = 4,
    feed_pos: float = -6.0,
    feed_r: float = 50.0,
    f0: float = 2.45e9,
    fc: float = 0.5e9,
    ground_thickness: float = 0.25,
) -> tuple[Project, list[dict[str, Any]]]:
    """Return (Project, primitives) for a classic probe-fed patch antenna.

    Coordinates in millimetres (drawing unit mm).
    Tuned on 2026-10-02: 30.2×37.8 mm resonated at 2.57 GHz (S11 −20 dB);
    scaled ×(2.57/2.45)≈1.049 → 31.7×39.7 mm, feed −6.0 mm for f0=2.45 GHz.
    """
    from app.models.project import (
        BoundarySpec,
        MaterialDef,
        MeshSpec,
        NF2FFDef,
        PortDef,
        Project as Proj,
        SimSpec,
        DumpDef,
    )

    if substrate_kappa is None:
        from openEMS.physical_constants import EPS0

        substrate_kappa = 1e-3 * 2 * 3.141592653589793 * f0 * EPS0 * substrate_eps

    p = Proj(name="patch_antenna")
    p.mesh = MeshSpec(
        preset="high",
        drawing_unit="mm",
        start_frequency=f0 - fc,
        stop_frequency=f0 + fc,
        target_frequency=f0,
        refined_cellsize=None,
        cells_per_wavelength=20.0,
        # ground-to-air transition needs some grading headroom
        max_grading_ratio=2.0,
        # resolve ground-plane thickness + substrate interface
        min_cellsize=ground_thickness,
    )
    p.materials = [
        MaterialDef(name="copper", kind="pec"),
        MaterialDef(name="substrate", kind="dielectric", epsilon=substrate_eps, kappa=substrate_kappa),
    ]
    p.ports = [
        PortDef(
            name="in1",
            type="lumped",
            start=[feed_pos, 0.0, 0.0],
            stop=[feed_pos, 0.0, substrate_thickness],
            resistance=feed_r,
            direction="z",
            excite_port=True,
            mesh_dirs="xy",
        )
    ]
    p.dumps = [
        DumpDef(
            name="Et_substrate",
            quantity="E",
            start=[-substrate_width / 2, -substrate_length / 2, substrate_thickness / 2],
            stop=[substrate_width / 2, substrate_length / 2, substrate_thickness / 2],
            frequency_domain=True,
            frequency=f0,
        )
    ]
    # Air margin (εr=1) ≈ λ(f_stop)/4 ≈ 25 mm at 2.95 GHz.
    # easyMesh domain is a bit tighter than structure+2×margin — keep NF2FF conservative.
    air_half = 20.0
    nf_xy = substrate_width / 2 + air_half - 10.0  # 30+20-10 = 40 mm, inside domain
    # z-min must sit below the ground plate (bottom at -ground_thickness) so the
    # integration surface encloses ALL sources. If the mesh has no air gap below
    # the ground, the domain clamp lifts this face to the domain floor (PEC wall,
    # zero flux) — both variants are valid closures.
    p.nf2ff = NF2FFDef(
        enabled=True,
        start=[-nf_xy, -nf_xy, -ground_thickness - 1.0],
        stop=[nf_xy, nf_xy, substrate_thickness + air_half - 8.0],
        center=[0.0, 0.0, substrate_thickness / 2],
        radius=1.0,
        frequencies=[f0],
        theta_steps=91,
        phi_steps=181,
    )
    p.sim = SimSpec(
        end_criteria=1e-5,
        # Excitation ~7k TS at dt≈0.8 ns; need many ring-down periods for patch Q.
        max_timesteps=200_000,
        nf2ff_enabled=True,
        field_dumps_enabled=True,
    )
    # PEC ground plane as metal (needed for port circuit + PEC edges).
    # Thickness = ground_thickness (resolved by min_cellsize); zmin boundary also PEC.
    p.boundary = BoundarySpec(
        xmin="MUR",
        xmax="MUR",
        ymin="MUR",
        ymax="MUR",
        zmin="PEC",
        zmax="MUR",
        pml_size=8,
        air_margin=["auto", "auto", "auto", "auto", "auto", "auto"],
    )

    from openEMS.physical_constants import C0

    mesh_res = C0 / (f0 + fc) / 1e-3 / 20.0

    # Ground metal + substrate + thin patch + probe port.
    # Do NOT use a 0.01 mm ground — that forced ~65× grading and tiny timesteps.
    primitives: list[dict[str, Any]] = [
        {
            "kind": "box",
            "name": "ground",
            "material": "copper",
            "priority": 90,
            "start": [-substrate_width / 2, -substrate_length / 2, -ground_thickness],
            "stop": [substrate_width / 2, substrate_length / 2, 0.0],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "substrate",
            "material": "substrate",
            "priority": 10,
            "start": [-substrate_width / 2, -substrate_length / 2, 0],
            "stop": [substrate_width / 2, substrate_length / 2, substrate_thickness],
        },
        {
            "kind": "box",
            "name": "patch",
            "material": "copper",
            "priority": 100,
            "start": [-patch_width / 2, -patch_length / 2, substrate_thickness],
            "stop": [patch_width / 2, patch_length / 2, substrate_thickness],
            "edges2grid": "xy",
        },
    ]
    p.notes = "patch_antenna_primitives"
    return p, primitives


def yagi_antenna_primitives(
    f0: float = 2.45e9,
    fc: float = 0.25e9,
    element_radius: float = 1.0,
    boom_radius: float = 1.5,
    feed_gap: float = 2.0,
    reflector_len: float | None = None,
    driven_len: float | None = None,
    dir1_len: float | None = None,
    dir2_len: float | None = None,
    dir3_len: float | None = None,
    reflector_y: float | None = None,
    dir1_y: float | None = None,
    dir2_y: float | None = None,
    dir3_y: float | None = None,
    boom_pad: float = 20.0,
    boom_eps: float = 4.3,
    boom_tand: float = 0.02,
) -> tuple[Project, list[dict[str, Any]]]:
    """Return (Project, primitives) for a 5-element Yagi-Uda (wire antenna).

    Geometry matches examples/freecad_yagi_model.py (mm drawing units):
      X — element length, Y — boom/propagation, Z — up.
    Driven element is a dipole along X with a central feed gap; lumped 50 Ω
    port spans that gap (direction x). Elements are PEC cylinders, the boom is
    a dielectric GFRP rod: a metal boom passes through y=0 (x∈[-1.5,1.5]) and
    would short the feed gap.

    Default design: 2.45 GHz ISM (λ≈122.4 mm), classic Yagi ratios.
    """
    from openEMS.physical_constants import C0, EPS0

    lam_mm = C0 / f0 * 1e3  # same as freecad script
    if reflector_len is None:
        reflector_len = 0.500 * lam_mm
    if driven_len is None:
        driven_len = 0.475 * lam_mm
    if dir1_len is None:
        dir1_len = 0.440 * lam_mm
    if dir2_len is None:
        dir2_len = 0.415 * lam_mm
    if dir3_len is None:
        dir3_len = 0.390 * lam_mm
    if reflector_y is None:
        reflector_y = -0.25 * lam_mm
    if dir1_y is None:
        dir1_y = 0.20 * lam_mm
    if dir2_y is None:
        dir2_y = 0.39 * lam_mm
    if dir3_y is None:
        dir3_y = 0.58 * lam_mm

    drv_y = 0.0
    drv_half = (driven_len - feed_gap) / 2.0
    drv_l_x0 = -driven_len / 2.0
    drv_r_x0 = feed_gap / 2.0
    boom_y0 = reflector_y - boom_pad
    boom_y1 = dir3_y + boom_pad
    boom_len = boom_y1 - boom_y0

    # Geometry bbox (elements + boom incl. wire radius) — dump/NF2FF derive from it.
    # Note: boom_y0/boom_y1 are offsets from the driven element (y=0), so the bbox
    # centre in y is NOT the origin — half extents must be measured from that centre.
    geom_x_half = max(reflector_len, driven_len, dir1_len, dir2_len, dir3_len) / 2.0 + element_radius
    geom_y0 = boom_y0 - boom_radius
    geom_y1 = boom_y1 + boom_radius
    geom_z_half = max(element_radius, boom_radius)
    nf2ff_center = [0.0, 0.5 * (geom_y0 + geom_y1), 0.0]

    from app.models.project import (
        BoundarySpec,
        GeomAssignment,
        MaterialDef,
        MeshSpec,
        NF2FFDef,
        PortDef,
        Project as Proj,
        SimSpec,
        DumpDef,
    )

    p = Proj(name="yagi_antenna")
    p.mesh = MeshSpec(
        preset="high",
        drawing_unit="mm",
        start_frequency=f0 - fc,
        stop_frequency=f0 + fc,
        target_frequency=f0,
        # resolve Ø2 mm elements
        min_cellsize=element_radius,
        cells_per_wavelength=20.0,
        max_grading_ratio=2.0,
        smooth_metal_edge="one_third_two_thirds",
    )
    # Free-space antenna: MUR on all faces; air margin auto ≈ λ/4 @ f_stop.
    p.boundary = BoundarySpec(
        xmin="MUR",
        xmax="MUR",
        ymin="MUR",
        ymax="MUR",
        zmin="MUR",
        zmax="MUR",
        pml_size=8,
        air_margin=["auto", "auto", "auto", "auto", "auto", "auto"],
    )
    p.materials = [
        MaterialDef(name="copper", kind="pec"),
        # Glass-fibre (GFRP) boom — dielectric so it cannot short the feed gap.
        MaterialDef(
            name="gfrp",
            kind="dielectric",
            epsilon=boom_eps,
            kappa=boom_tand * 2 * 3.141592653589793 * f0 * EPS0 * boom_eps,
            tand=boom_tand,
        ),
    ]
    # CAD/STL import path: the FreeCAD object "boom" maps to the same material
    # (STL primitives are named after the FreeCAD object).
    p.assignments = [
        GeomAssignment(
            object_name="boom",
            role="dielectric",
            material="gfrp",
            priority=40,
            notes="glass-fibre boom — must not short the feed gap",
        )
    ]

    # Lumped port across the driven-element gap (along X)
    p.ports = [
        PortDef(
            name="feed",
            type="lumped",
            start=[-feed_gap / 2.0, drv_y, -element_radius],
            stop=[feed_gap / 2.0, drv_y, element_radius],
            resistance=50.0,
            direction="x",
            excite_port=True,
            mesh_dirs="xyz",
        )
    ]
    # NF2FF must sit strictly INSIDE the FDTD domain — openEMS disables any dump
    # box outside the field domain. easyMesh's graded air region ends up a bit
    # tighter than the requested λ/4, so keep only 70% of it around the structure.
    from app.meshing.rules import air_margin_units, compute_targets

    air_margin = float(air_margin_units(p.mesh, p.boundary.air_margin, compute_targets(p.mesh))[0])
    nf2ff_margin = 0.7 * air_margin
    x_half = geom_x_half + nf2ff_margin
    y_half = 0.5 * (geom_y1 - geom_y0) + nf2ff_margin
    z_half = geom_z_half + nf2ff_margin

    # Field dump: the whole antenna (+ half the NF2FF margin) — must cover the elements.
    dump_margin = 0.5 * nf2ff_margin
    p.dumps = [
        DumpDef(
            name="E_boom",
            quantity="E",
            start=[
                -geom_x_half - dump_margin,
                geom_y0 - dump_margin,
                -(geom_z_half + dump_margin),
            ],
            stop=[
                geom_x_half + dump_margin,
                geom_y1 + dump_margin,
                geom_z_half + dump_margin,
            ],
            frequency_domain=True,
            frequency=f0,
        )
    ]
    p.nf2ff = NF2FFDef(
        enabled=True,
        start=[nf2ff_center[0] - x_half, nf2ff_center[1] - y_half, nf2ff_center[2] - z_half],
        stop=[nf2ff_center[0] + x_half, nf2ff_center[1] + y_half, nf2ff_center[2] + z_half],
        center=nf2ff_center,
        radius=1.0,
        frequencies=[f0],
        theta_steps=91,
        phi_steps=181,
    )
    p.sim = SimSpec(
        end_criteria=1e-5,
        # Short default run (~3 min @ ~150 TS/s); end_criteria stops earlier when
        # converged. Raise max_timesteps if S11/NF2FF look truncated.
        max_timesteps=25_000,
        nf2ff_enabled=True,
        field_dumps_enabled=True,
    )

    copper = {
        "kind": "cylinder",
        "material": "copper",
        "priority": 50,
        "radius": element_radius,
        "edges2grid": "xyz",
    }

    def _elem(name: str, length: float, y: float, x0: float, radius: float | None = None) -> dict:
        return {
            **copper,
            "name": name,
            "radius": radius if radius is not None else element_radius,
            "start": [float(x0), float(y), 0.0],
            "stop": [float(x0 + length), float(y), 0.0],
        }

    # Boom: dielectric GFRP (see p.materials) — a metal boom runs through the
    # feed gap at y=0 and would short the port. Priority below copper (50) so
    # the element/boom junctions stay metal.
    boom = {
        "kind": "cylinder",
        "material": "gfrp",
        "priority": 40,
        "radius": boom_radius,
        "start": [0.0, float(boom_y0), 0.0],
        "stop": [0.0, float(boom_y1), 0.0],
    }

    primitives: list[dict[str, Any]] = [
        {**boom, "name": "boom"},
        _elem("reflector", reflector_len, reflector_y, -reflector_len / 2.0),
        _elem("driven_left", drv_half, drv_y, drv_l_x0),
        _elem("driven_right", drv_half, drv_y, drv_r_x0),
        _elem("director_1", dir1_len, dir1_y, -dir1_len / 2.0),
        _elem("director_2", dir2_len, dir2_y, -dir2_len / 2.0),
        _elem("director_3", dir3_len, dir3_y, -dir3_len / 2.0),
    ]
    p.notes = (
        "yagi_antenna_primitives — FreeCAD model: examples/freecad_yagi_model.py "
        f"(λ={lam_mm:.2f} mm @ {f0/1e9:.3f} GHz)"
    )
    p.primitives = list(primitives)
    return p, primitives


def pcb_filter_primitives(
    f0: float = 2.45e9,
    fc: float = 2.15e9,
    line_width: float = 3.0,
    line_half_length: float = 32.0,
    port_offset: float = 30.0,
    stub_length: float | None = None,
    substrate_eps: float = 4.4,
    substrate_tand: float = 0.02,
    substrate_thickness: float = 1.6,
    ground_thickness: float = 0.25,
) -> tuple[Project, list[dict[str, Any]]]:
    """Return (Project, primitives) for a 2-port microstrip notch filter (PCB).

    A 50 ohm microstrip runs along X on an FR4 board (1.6 mm, eps_r 4.4) and
    carries an open-circuit lambda_g/4 stub in the middle. The stub turns into
    a short at f0, so S21 drops sharply there (band-stop) while the line stays
    matched away from the notch.

    Based on openEMS Tutorials/MSL_NotchFilter.py: RO4350B 254 um -> FR4
    1.6 mm, manual meshing -> easyMesh, AddMSLPort -> two lumped 50 ohm edge
    ports (port type supported by the generated script).

    One FDTD run measures one S-matrix column (the excited port's), so the
    complete 2-port data needs two runs — see
    examples/run_pcb_filter_example.py.
    """
    from openEMS.physical_constants import C0, EPS0

    from app.models.project import (
        BoundarySpec,
        DumpDef,
        MaterialDef,
        MeshSpec,
        NF2FFDef,
        PortDef,
        Project as Proj,
        SimSpec,
    )

    hw = line_width / 2.0
    if stub_length is None:
        # open lambda_g/4 stub -> short circuit at the junction (f0 = notch);
        # subtract the open-end fringing length (~0.3 * substrate height),
        # measured correction: keeps the notch on f0 within ~1 percent.
        w_over_h = line_width / substrate_thickness
        eps_eff = (substrate_eps + 1.0) / 2.0 + (substrate_eps - 1.0) / 2.0 / math.sqrt(
            1.0 + 12.0 / w_over_h
        )
        stub_length = C0 / (f0 * math.sqrt(eps_eff)) / 4.0 * 1e3  # mm
        stub_length -= 0.3 * substrate_thickness

    # Board reaches a little past the line ends so both port cells sit under
    # strip + ground (the lumped element is one FDTD cell wide).
    board_x = line_half_length + 2.0
    board_y0 = -(hw + 8.0)
    board_y1 = hw + stub_length + 6.0
    z_top = substrate_thickness
    substrate_kappa = substrate_tand * 2 * 3.141592653589793 * f0 * EPS0 * substrate_eps

    p = Proj(name="pcb_filter")
    p.mesh = MeshSpec(
        preset="high",
        drawing_unit="mm",
        start_frequency=f0 - fc,
        stop_frequency=f0 + fc,
        target_frequency=f0,
        cells_per_wavelength=20.0,
        max_grading_ratio=2.0,
        # sample the 1.6 mm substrate with ~4 cells (also sets the edge halo)
        min_cellsize=0.4,
        smooth_metal_edge="one_third_two_thirds",
    )
    # MUR on the sides/top, PEC directly under the ground plane (as in the
    # openEMS tutorial). Planar circuit: a small fixed air margin is enough.
    p.boundary = BoundarySpec(
        xmin="MUR",
        xmax="MUR",
        ymin="MUR",
        ymax="MUR",
        zmin="PEC",
        zmax="MUR",
        pml_size=8,
        air_margin=[10.0, 10.0, 10.0, 10.0, 0.0, 10.0],
    )
    p.materials = [
        MaterialDef(name="copper", kind="pec"),
        MaterialDef(
            name="fr4",
            kind="dielectric",
            epsilon=substrate_eps,
            kappa=substrate_kappa,
            tand=substrate_tand,
        ),
    ]
    # Lumped edge ports at +-port_offset: vertical gap between ground and the
    # strip, as wide as the line (area lumped ports: y = metal width, x = a
    # zero-thickness sheet at the port plane). Port 2 starts unexcited (run 2
    # flips this).
    p.ports = [
        PortDef(
            name="in1",
            type="lumped",
            start=[-port_offset, -hw, 0.0],
            stop=[-port_offset, hw, z_top],
            resistance=50.0,
            direction="z",
            excite_port=True,
            mesh_dirs="xy",
            lumped_width="y",
        ),
        PortDef(
            name="out1",
            type="lumped",
            start=[port_offset, -hw, 0.0],
            stop=[port_offset, hw, z_top],
            resistance=50.0,
            direction="z",
            excite_port=False,
            mesh_dirs="xy",
            lumped_width="y",
        ),
    ]
    p.dumps = [
        DumpDef(
            name="E_substrate",
            quantity="E",
            start=[-board_x, board_y0, z_top / 2.0],
            stop=[board_x, board_y1, z_top / 2.0],
            frequency_domain=True,
            frequency=f0,
        )
    ]
    p.nf2ff = NF2FFDef(
        enabled=False,
        start=[-board_x - 10.0, board_y0 - 10.0, -1.0],
        stop=[board_x + 10.0, board_y1 + 10.0, z_top + 10.0],
        center=[0.0, 0.5 * (board_y0 + board_y1), z_top / 2.0],
        radius=1.0,
        frequencies=[f0],
        theta_steps=91,
        phi_steps=181,
    )
    p.sim = SimSpec(
        # matched lines ring down quickly; end_criteria usually stops first
        end_criteria=1e-5,
        max_timesteps=50_000,
        nf2ff_enabled=False,
        field_dumps_enabled=True,
    )

    primitives: list[dict[str, Any]] = [
        {
            "kind": "box",
            "name": "ground",
            "material": "copper",
            "priority": 90,
            "start": [-board_x, board_y0, -ground_thickness],
            "stop": [board_x, board_y1, 0.0],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "substrate",
            "material": "fr4",
            "priority": 10,
            "start": [-board_x, board_y0, 0.0],
            "stop": [board_x, board_y1, z_top],
        },
        {
            "kind": "box",
            "name": "msl_line",
            "material": "copper",
            "priority": 100,
            "start": [-line_half_length, -hw, z_top],
            "stop": [line_half_length, hw, z_top],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "msl_stub",
            "material": "copper",
            "priority": 100,
            "start": [-hw, hw, z_top],
            "stop": [hw, hw + stub_length, z_top],
            "edges2grid": "xy",
        },
    ]
    p.notes = (
        "pcb_filter_primitives — microstrip notch filter after openEMS "
        f"Tutorials/MSL_NotchFilter.py (stub {stub_length:.2f} mm, f0={f0/1e9:.3f} GHz)"
    )
    p.primitives = list(primitives)
    return p, primitives


# --------------------------------------------------------------------------
# Microstrip synthesis (Hammerstad) used by the band-pass preset
# --------------------------------------------------------------------------
def msl_width(z0: float, eps_r: float, h: float) -> float:
    """Microstrip width (mm) for a characteristic impedance `z0` (ohm)."""
    if z0 >= 44.0 - 2.0 * eps_r:  # narrow line (W/h <= 2)
        a = z0 / 60.0 * math.sqrt((eps_r + 1.0) / 2.0) + (eps_r - 1.0) / (
            eps_r + 1.0
        ) * (0.23 + 0.11 / eps_r)
        w_over_h = 8.0 * math.exp(a) / (math.exp(2.0 * a) - 2.0)
    else:  # wide line (W/h > 2)
        b = 377.0 * math.pi / (2.0 * z0 * math.sqrt(eps_r))
        w_over_h = 2.0 / math.pi * (
            b
            - 1.0
            - math.log(2.0 * b - 1.0)
            + (eps_r - 1.0) / (2.0 * eps_r)
            * (math.log(b - 1.0) + 0.39 - 0.61 / eps_r)
        )
    return w_over_h * h


def msl_eps_eff(z0: float, eps_r: float, h: float) -> float:
    """Effective permittivity of a microstrip line (Hammerstad, no thickness)."""
    w_over_h = msl_width(z0, eps_r, h) / h
    return (eps_r + 1.0) / 2.0 + (eps_r - 1.0) / 2.0 / math.sqrt(
        1.0 + 12.0 / w_over_h
    )


def msl_open_end(z0: float, eps_r: float, h: float) -> float:
    """Open-end fringing extension (mm) of a microstrip open stub."""
    ee = msl_eps_eff(z0, eps_r, h)
    w_over_h = msl_width(z0, eps_r, h) / h
    return (
        0.412
        * h
        * (ee + 0.3)
        * (w_over_h + 0.264)
        / ((ee - 0.258) * (w_over_h + 0.8))
    )


# 5-pole Chebyshev band-pass, 0.1 dB ripple, f0 = 5.6 GHz, FBW ~ 30 %.
# Synthesised from the exact commensurate network (five open lambda/2 shunt
# stubs + four lambda/4 inverter lines): |S11| <= -20 dB over 4.85-6.35 GHz.
PCB_BANDPASS_DESIGN: dict[str, Any] = {
    "stub_z": [50.83, 15.00, 68.03, 15.00, 50.83],
    "stub_k": [0.927, 0.984, 1.140, 0.984, 0.927],  # x lambda_g/2
    "line_z": [43.85, 61.01, 61.01, 43.85],
    "line_k": [1.189, 0.800, 0.800, 1.189],  # x lambda_g/4
}


def pcb_bandpass_stub_primitives(
    f0: float = 5.6e9,
    substrate_eps: float = 2.1,
    substrate_tand: float = 0.0002,
    substrate_thickness: float = 0.254,
    ground_thickness: float = 0.25,
    feed_length: float = 7.0,
    port_margin: float = 1.5,
    design: dict[str, Any] | None = None,
) -> tuple[Project, list[dict[str, Any]]]:
    """Return (Project, primitives) for a PCB band-pass (stub filter).

    Five **open-circuit lambda/2 shunt stubs** hang off a 50 ohm microstrip
    line and are separated by lambda/4 connecting lines (impedance inverters).
    That is a classic 5-pole Chebyshev band-pass: 0.1 dB ripple, f0 = 5.6 GHz,
    about 30 % bandwidth (|S11| <= -20 dB from 4.85 to 6.35 GHz).  Open stubs
    need no vias, so the board stays a single-layer planar circuit.

    Substrate: PTFE ("Teflon") 0.254 mm, eps_r = 2.1, tan_d = 0.0002, 35 um
    copper.  Line/stub widths and lengths are synthesised with Hammerstad's
    microstrip formulas from the impedance table in `PCB_BANDPASS_DESIGN`
    (fit of the exact commensurate network to the Chebyshev target).

    Two lumped 50 ohm edge ports (in1/out1) terminate the feed lines.  One
    FDTD run measures one S-matrix column; see
    examples/run_pcb_bandpass_example.py for the two-run merge.
    """
    from openEMS.physical_constants import C0, EPS0

    from app.models.project import (
        BoundarySpec,
        DumpDef,
        MaterialDef,
        MeshSpec,
        NF2FFDef,
        PortDef,
        Project as Proj,
        SimSpec,
    )

    d = design or PCB_BANDPASS_DESIGN
    z_stub = list(d["stub_z"])
    k_stub = list(d["stub_k"])
    z_line = list(d["line_z"])
    k_line = list(d["line_k"])
    n_stub = len(z_stub)
    if len(z_line) != n_stub - 1:
        raise ValueError("band-pass design needs n-1 connecting lines")

    def lam_g(z0: float) -> float:
        return C0 / (f0 * math.sqrt(msl_eps_eff(z0, substrate_eps, substrate_thickness))) * 1e3

    w_feed = msl_width(50.0, substrate_eps, substrate_thickness)
    w_stub = [msl_width(z, substrate_eps, substrate_thickness) for z in z_stub]
    w_line = [msl_width(z, substrate_eps, substrate_thickness) for z in z_line]
    # lambda/2 open stubs: subtract the open-end fringing length
    l_stub = [
        k * lam_g(z) / 2.0 - msl_open_end(z, substrate_eps, substrate_thickness)
        for z, k in zip(z_stub, k_stub)
    ]
    l_line = [k * lam_g(z) / 4.0 for z, k in zip(z_line, k_line)]

    # stub centres along X (first stub at -span/2)
    span = sum(l_line)
    x_stub = [-span / 2.0]
    for ln in l_line:
        x_stub.append(x_stub[-1] + ln)
    x_port = span / 2.0 + feed_length

    board_x = x_port + port_margin
    hw_feed = w_feed / 2.0
    board_y0 = -(hw_feed + 6.0)
    board_y1 = max(l_stub) + 5.0
    z_top = substrate_thickness
    kappa = substrate_tand * 2.0 * math.pi * f0 * EPS0 * substrate_eps

    p = Proj(name="pcb_bandpass")
    p.mesh = MeshSpec(
        preset="high",
        drawing_unit="mm",
        start_frequency=4.0e9,
        stop_frequency=7.2e9,
        target_frequency=f0,
        cells_per_wavelength=20.0,
        max_grading_ratio=2.0,
        # 0.5 mm narrowest stub -> at least 3 cells across
        min_cellsize=0.15,
        # three cells through the 0.254 mm substrate (E-field is mostly there)
        min_cellsize_z=substrate_thickness / 3.0,
        # easyMesh's edge smoothing/merging creates a ~4 um Y cell between the
        # feed edge and the wider line edge (kills the CFL time step), so keep
        # both off for this dense planar layout.
        smooth_metal_edge="False",
        handle_closely_placed_edges=False,
    )
    p.boundary = BoundarySpec(
        xmin="MUR",
        xmax="MUR",
        ymin="MUR",
        ymax="MUR",
        zmin="PEC",
        zmax="MUR",
        pml_size=8,
        air_margin=[10.0, 10.0, 10.0, 10.0, 0.0, 12.0],
    )
    p.materials = [
        MaterialDef(name="copper", kind="pec"),
        MaterialDef(
            name="ptfe",
            kind="dielectric",
            epsilon=substrate_eps,
            kappa=kappa,
            tand=substrate_tand,
        ),
    ]
    p.ports = [
        PortDef(
            name="in1",
            type="lumped",
            start=[-x_port, -hw_feed, 0.0],
            stop=[-x_port, hw_feed, z_top],
            resistance=50.0,
            direction="z",
            excite_port=True,
            mesh_dirs="xy",
            lumped_width="y",
        ),
        PortDef(
            name="out1",
            type="lumped",
            start=[x_port, -hw_feed, 0.0],
            stop=[x_port, hw_feed, z_top],
            resistance=50.0,
            direction="z",
            excite_port=False,
            mesh_dirs="xy",
            lumped_width="y",
        ),
    ]
    p.dumps = [
        DumpDef(
            name="E_substrate",
            quantity="E",
            start=[-board_x, board_y0, z_top / 2.0],
            stop=[board_x, board_y1, z_top / 2.0],
            frequency_domain=True,
            frequency=f0,
        )
    ]
    p.nf2ff = NF2FFDef(
        enabled=False,
        start=[-board_x - 10.0, board_y0 - 10.0, -1.0],
        stop=[board_x + 10.0, board_y1 + 10.0, z_top + 10.0],
        center=[0.0, 0.5 * (board_y0 + board_y1), z_top / 2.0],
        radius=1.0,
        frequencies=[f0],
        theta_steps=91,
        phi_steps=181,
    )
    p.sim = SimSpec(
        end_criteria=1e-5,
        max_timesteps=80_000,
        nf2ff_enabled=False,
        field_dumps_enabled=True,
    )

    primitives: list[dict[str, Any]] = [
        {
            "kind": "box",
            "name": "ground",
            "material": "copper",
            "priority": 90,
            "start": [-board_x, board_y0, -ground_thickness],
            "stop": [board_x, board_y1, 0.0],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "substrate",
            "material": "ptfe",
            "priority": 10,
            "start": [-board_x, board_y0, 0.0],
            "stop": [board_x, board_y1, z_top],
        },
        {
            "kind": "box",
            "name": "feed_in",
            "material": "copper",
            "priority": 100,
            "start": [-x_port, -hw_feed, z_top],
            "stop": [x_stub[0], hw_feed, z_top],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "feed_out",
            "material": "copper",
            "priority": 100,
            "start": [x_stub[-1], -hw_feed, z_top],
            "stop": [x_port, hw_feed, z_top],
            "edges2grid": "xy",
        },
    ]
    for i, (x0, x1, w) in enumerate(zip(x_stub[:-1], x_stub[1:], w_line), start=1):
        primitives.append(
            {
                "kind": "box",
                "name": f"line_{i}{i + 1}",
                "material": "copper",
                "priority": 100,
                # overlap both stub centres so the T-junctions are solid
                "start": [x0, -w / 2.0, z_top],
                "stop": [x1, w / 2.0, z_top],
                "edges2grid": "xy",
            }
        )
    for i, (x, w, ln) in enumerate(zip(x_stub, w_stub, l_stub), start=1):
        primitives.append(
            {
                "kind": "box",
                "name": f"stub_{i}",
                "material": "copper",
                "priority": 100,
                "start": [x - w / 2.0, 0.0, z_top],
                "stop": [x + w / 2.0, ln, z_top],
                "edges2grid": "xy",
            }
        )

    p.notes = (
        "pcb_bandpass_stub_primitives — 5-pole Chebyshev band-pass made of five "
        f"open lambda/2 stubs on PTFE {substrate_thickness * 1e3:.0f} um "
        f"(eps_r {substrate_eps}), f0={f0 / 1e9:.2f} GHz, ~30 % bandwidth "
        "(4.85-6.35 GHz), no vias. Stub widths: "
        + ", ".join(f"{w:.2f}" for w in w_stub)
        + " mm."
    )
    p.primitives = list(primitives)
    return p, primitives



def wr62_waveguide_primitives(
    f0: float = 10.0e9,
    length: float = 100.0,
    excite: str = "in",
    extra: float = 20.0,
    wall: float = 2.0,
) -> tuple[Project, list[dict]]:
    """Straight WR62 rectangular waveguide with E/H/J field dumps.

    Port planes are ``length`` mm apart; the PEC walls extend ``extra`` mm
    beyond each port towards the x-PML. TE10 at ``f0`` (10 GHz by default);
    WR62 is 15.7988 x 7.8994 mm, f_c = 9.488 GHz.
    """
    a, b = 15.7988, 7.8994
    f_start, f_stop = 9.5e9, 11.5e9
    project = Project(name="wr62_waveguide")
    project.materials = [
        MaterialDef(name="pec", kind="pec", color=(0.75, 0.6, 0.35, 1.0))
    ]
    project.mesh = MeshSpec(
        preset="medium",
        drawing_unit="mm",
        start_frequency=f_start,
        stop_frequency=f_stop,
        target_frequency=f0,
        min_cellsize=0.25,
        max_cellsize=1.0,
        max_grading_ratio=1.5,
        cells_per_wavelength=15.0,
    )
    project.boundary = BoundarySpec(
        xmin="PML", xmax="PML",
        ymin="PEC", ymax="PEC",
        zmin="PEC", zmax="PEC",
        pml_size=8, air_margin=[0.0] * 6,
    )
    project.sim = SimSpec(
        end_criteria=1e-4, max_timesteps=200000,
        field_dumps_enabled=True, verbose=2,
    )
    project.ports = [
        PortDef(
            name="in", type="rect_waveguide",
            start=[-length / 2.0, 0.0, 0.0],
            stop=[-length / 2.0 + 2.0, a, b],
            direction="x", excite_port=(excite == "in"),
            wg_a=a, wg_b=b, wg_mode="TE10", mesh_dirs="xyz",
        ),
        PortDef(
            name="out", type="rect_waveguide",
            start=[length / 2.0, 0.0, 0.0],
            stop=[length / 2.0 - 2.0, a, b],
            direction="x", excite_port=(excite == "out"),
            wg_a=a, wg_b=b, wg_mode="TE10", mesh_dirs="xyz",
        ),
    ]
    dump_lo = [-length / 2.0, 0.0, 0.0]
    dump_hi = [length / 2.0, a, b]
    project.dumps = [
        DumpDef(
            name=kind, quantity=kind[0],
            start=list(dump_lo), stop=list(dump_hi),
            frequency_domain=True, frequency=f0, frequencies=[f0],
        )
        for kind in ("Et", "Ht", "Jt")
    ]
    x0, x1 = -length / 2.0 - extra, length / 2.0 + extra
    y0, y1 = -wall, a + wall
    z0, z1 = -wall, b + wall
    primitives = [
        {"kind": "box", "name": "wall_y_min", "material": "pec", "priority": 1,
         "start": [x0, y0, z0], "stop": [x1, 0.0, z1]},
        {"kind": "box", "name": "wall_y_max", "material": "pec", "priority": 1,
         "start": [x0, a, z0], "stop": [x1, y1, z1]},
        {"kind": "box", "name": "wall_z_min", "material": "pec", "priority": 1,
         "start": [x0, y0, z0], "stop": [x1, y1, 0.0]},
        {"kind": "box", "name": "wall_z_max", "material": "pec", "priority": 1,
         "start": [x0, y0, b], "stop": [x1, y1, z1]},
    ]
    project.notes = (
        f"WR62 straight waveguide, a={a} mm, b={b} mm, TE10 at {f0 / 1e9:.2f} GHz, "
        f"length={length:.0f} mm; E/H/J field dumps."
    )
    project.primitives = list(primitives)
    return project, primitives
