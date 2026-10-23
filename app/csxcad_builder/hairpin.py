from __future__ import annotations

import math
from typing import Any

from app.models.project import Project

# --------------------------------------------------------------------------
# Hammerstad-Jensen coupled microstrip (hairpin band-pass preset)
# --------------------------------------------------------------------------
# Reference: E. Hammerstad / O. Jensen even-odd mode model, as summarised in
# M. Steer, "Microwave and RF Design II - Transmission Lines", Sec. 5.6
# (accurate to ~1 % for 0.1 <= w/h <= 10 and s/h > 0.01, epsilon_r <= 128).
_ETA0 = 376.73


def msl_z0_free(u: float) -> float:
    """Free-space characteristic impedance of a single microstrip line (ohm).

    ``u = w/h``; this is the impedance with the dielectric replaced by vacuum
    (Hammerstad-Jensen, Steer eq. 3.5.13).
    """
    f = 6.0 + (2.0 * math.pi - 6.0) * math.exp(-((30.666 / u) ** 0.7528))
    return _ETA0 / (2.0 * math.pi) * math.log(f / u + math.sqrt(1.0 + (2.0 / u) ** 2))


def msl_a_u(u: float) -> float:
    return (
        1.0
        + (1.0 / 49.0) * math.log((u**4 + (u / 52.0) ** 2) / (u**4 + 0.432))
        + (1.0 / 18.7) * math.log(1.0 + (u / 18.1) ** 3)
    )


def msl_b_er(eps_r: float) -> float:
    return 0.564 * ((eps_r - 0.9) / (eps_r + 3.0)) ** 0.053


def msl_eps_eff_u(u: float, eps_r: float) -> float:
    """Effective permittivity of a single microstrip line (``u = w/h``)."""
    return (eps_r + 1.0) / 2.0 + (eps_r - 1.0) / 2.0 * (1.0 + 10.0 / u) ** (
        -msl_a_u(u) * msl_b_er(eps_r)
    )


def msl_z0_u(u: float, eps_r: float) -> float:
    """Characteristic impedance (ohm) of a microstrip line with ``u = w/h``."""
    return msl_z0_free(u) / math.sqrt(msl_eps_eff_u(u, eps_r))


def msl_u_for_z0(z0: float, eps_r: float) -> float:
    """``u = w/h`` for a wanted characteristic impedance (bisection)."""
    if z0 <= 0.0:
        raise ValueError("characteristic impedance must be > 0")
    lo, hi = 0.05, 100.0
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        if msl_z0_u(mid, eps_r) > z0:  # narrow line -> higher impedance
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def coupled_ms_modes(
    u: float, g: float, eps_r: float
) -> tuple[float, float, float, float]:
    """Even/odd mode data of a symmetric coupled microstrip pair.

    ``u = w/h``, ``g = s/h``.  Returns ``(Z0e, Z0o, eps_ee, eps_eo)``.
    """
    z01 = msl_z0_free(u)
    a_u, b_er = msl_a_u(u), msl_b_er(eps_r)
    phi_u = 0.8645 * u**0.172
    psi = 1.0 + g / 1.45 + g**2.09 / 3.95
    alpha = 0.5 * math.exp(-g)
    m = (
        0.2175
        + (4.113 + (20.36 / g) ** 6) ** -0.251
        + (1.0 / 323.0) * math.log(g**10 / (1.0 + (g / 13.8) ** 10))
    )
    phi_e = phi_u / (psi * (alpha * u**m + (1.0 - alpha) * u**-m))
    mu = g * math.exp(-g) + u * (20.0 + g * g) / (10.0 + g * g)
    eps_ee = (eps_r + 1.0) / 2.0 + (eps_r - 1.0) / 2.0 * (1.0 + 10.0 / mu) ** (
        -a_u * b_er
    )
    z0e = z01 / (1.0 - z01 * phi_e / _ETA0) / math.sqrt(eps_ee)

    theta = 1.729 + 1.175 * math.log(1.0 + 0.627 / (g + 0.327 * g**2.17))
    beta = (
        0.2306
        + (1.0 / 301.8) * math.log(g**10 / (1.0 + (g / 3.73) ** 10))
        + (1.0 / 5.3) * math.log(1.0 + 0.646 * g**1.175)
    )
    n = (1.0 / 17.7 + math.exp(-6.424 - 0.76 * math.log(g) - (g / 0.23) ** 5)) * math.log(
        (10.0 + 68.3 * g * g) / (1.0 + 32.5 * g**3.093)
    )
    phi_o = phi_e - (theta / psi) * math.exp(beta * u**n * math.log(u))

    p = math.exp(-0.745 * g**0.295) / math.cosh(g**0.68)
    r = 1.0 + 0.15 * (1.0 - math.exp(1.0 - (eps_r - 1.0) ** 2 / 8.2) / (1.0 + g**-6))
    fo1 = 1.0 - math.exp(
        -0.179 * g**0.15 - 0.328 * g**r / math.log(math.e + (g / 7.0) ** 2.8)
    )
    q = math.exp(-1.366 - g)
    fo = fo1 * math.exp(
        p * math.log(u) + q * math.sin(math.pi * math.log(u) / math.log(10.0))
    )
    eps_eo = (eps_r + 1.0) / 2.0 + (eps_r - 1.0) / 2.0 * fo * (1.0 + 10.0 / u) ** (
        -a_u * b_er
    )
    z0o = z01 / (1.0 - z01 * phi_o / _ETA0) / math.sqrt(eps_eo)
    return z0e, z0o, eps_ee, eps_eo


def coupled_ms_gap(u: float, eps_r: float, z0e: float, z0o: float) -> float:
    """``g = s/h`` that gives the wanted even/odd split for a fixed width ``u``."""
    k = (z0e - z0o) / (z0e + z0o)
    lo, hi = 1e-4, 100.0
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        e, o, _ee, _eo = coupled_ms_modes(u, mid, eps_r)
        if (e - o) / (e + o) > k:  # gap too small -> too much coupling
            lo = mid
        else:
            hi = mid
    return math.sqrt(lo * hi)


def coupled_ms_synthesis(
    z0e: float, z0o: float, eps_r: float, h: float
) -> tuple[float, float]:
    """Synthesise a coupled microstrip pair: returns ``(w, s)`` in mm.

    Solves for the strip width **and** the gap so that both mode impedances are
    met (the system impedance ``sqrt(Z0e Z0o)`` fixes the width, the split
    fixes the gap).
    """
    k = (z0e - z0o) / (z0e + z0o)
    lo, hi = 0.05, 100.0
    for _ in range(200):
        u = math.sqrt(lo * hi)
        g = coupled_ms_gap(u, eps_r, z0e, z0o)
        e, o, _ee, _eo = coupled_ms_modes(u, g, eps_r)
        if math.sqrt(e * o) > math.sqrt(z0e * z0o) * (1.0 + k * 0.0):
            lo = u  # system impedance too high -> wider strip
        else:
            hi = u
    u = math.sqrt(lo * hi)
    return u * h, coupled_ms_gap(u, eps_r, z0e, z0o) * h


# 5-pole Chebyshev, 0.1 dB ripple: low-pass prototype element values
HAIRPIN_G = (1.1468, 1.3712, 1.9750, 1.3712, 1.1468)

#: compact hairpin band-pass on RT/duroid 6010 (eps_r 10.2), f0 = 2.4 GHz
HAIRPIN_BANDPASS_DESIGN: dict[str, Any] = {
    "f0": 2.4e9,
    "fbw": 0.10,  # 10 % -> 2.28 ... 2.52 GHz
    "eps_r": 10.2,
    "tand": 0.0023,
    "h": 1.27,  # 50 mil RT/duroid 6010LM
    "inner_gap": 0.8,  # gap between the two arms of one hairpin
    "arm_scale": 1.0,  # length trim (simulation tuning)
    "gap_scale": 1.0,  # coupling gap trim (simulation tuning)
}


def hairpin_bandpass_design(
    f0: float | None = None,
    fbw: float | None = None,
    eps_r: float | None = None,
    h: float | None = None,
    inner_gap: float | None = None,
    arm_scale: float | None = None,
    gap_scale: float | None = None,
) -> dict[str, Any]:
    """Synthesise a 5-pole hairpin band-pass (dimensions in mm).

    Standard coupled-line band-pass synthesis: the low-pass prototype gives the
    J-inverters, each inverter fixes a pair of even/odd mode impedances, and the
    Hammerstad-Jensen coupled-line model turns those into a strip width and a
    gap.  Folding every half-wave resonator into a hairpin halves the physical
    length (the arms of neighbouring hairpins form the coupled sections).

    The returned dict carries the per-section widths/gaps, the resonator arm
    length and the derived layout coordinates used by
    :func:`pcb_hairpin_primitives`.
    """
    from openEMS.physical_constants import C0

    d0 = HAIRPIN_BANDPASS_DESIGN
    f0 = float(d0["f0"] if f0 is None else f0)
    fbw = float(d0["fbw"] if fbw is None else fbw)
    eps_r = float(d0["eps_r"] if eps_r is None else eps_r)
    h = float(d0["h"] if h is None else h)
    inner_gap = float(d0["inner_gap"] if inner_gap is None else inner_gap)
    arm_scale = float(d0["arm_scale"] if arm_scale is None else arm_scale)
    gap_scale = float(d0["gap_scale"] if gap_scale is None else gap_scale)

    n = len(HAIRPIN_G)  # 5 resonators
    g = (1.0, *HAIRPIN_G, 1.0)

    # --- J-inverters (normalised to Y0) -------------------------------------
    pi_d = math.pi * fbw
    j = [math.sqrt(pi_d / (2.0 * g[0] * g[1]))]
    j += [pi_d / (2.0 * math.sqrt(g[i] * g[i + 1])) for i in range(1, n)]
    j.append(math.sqrt(pi_d / (2.0 * g[n] * g[n + 1])))

    # --- coupled-line sections ---------------------------------------------
    # All strips keep the 50 ohm width w50 (a uniform resonator line, as in the
    # classic hairpin layout); the gap alone sets the even/odd split.
    wall = 50.0
    u50 = msl_u_for_z0(wall, eps_r)
    w50 = u50 * h
    # The two arms of one hairpin are a tightly coupled pair carrying opposite
    # currents, so the folded resonator runs in the *odd* mode - not on a single
    # 50 ohm line.  Using the single-line eps_eff here makes the arms ~15 %
    # too short (verified against the FDTD: 2.80 GHz instead of 2.40 GHz).
    _z0e, _z0o, eps_ee, eps_eo = coupled_ms_modes(w50 / h, inner_gap / h, eps_r)
    eps_eff = eps_eo
    lam_g = C0 / (f0 * math.sqrt(eps_eff)) * 1e3

    z0e: list[float] = []
    z0o: list[float] = []
    widths: list[float] = []
    gaps: list[float] = []
    for x in j:
        e = wall * (1.0 + x + x * x)
        o = wall * (1.0 - x + x * x)
        w_exact, _s_exact = coupled_ms_synthesis(e, o, eps_r, h)
        z0e.append(e)
        z0o.append(o)
        widths.append(w_exact)
        gaps.append(coupled_ms_gap(u50, eps_r, e, o) * h * gap_scale)

    # --- resonator arms -----------------------------------------------------
    # Folded half-wave resonator: 2 * arm + fold path == lambda_g/2.  The
    # electrical length of the fold follows the centre line of the bend
    # (w50 + inner_gap), not its outer edge (2 * w50 + inner_gap).  The arms are
    # also the coupled sections towards the neighbouring resonator.
    fold = 2.0 * w50 + inner_gap  # outer width of the U (layout)
    fold_path = w50 + inner_gap  # electrical length through the bend
    arm = max(1.0, (lam_g / 2.0 - fold_path) / 2.0) * arm_scale

    # --- layout along x (left to right): feed - hairpin - ... - hairpin - feed
    hairpins: list[tuple[float, float, float]] = []  # (arm1_x0, arm2_x0, arm2_x1)
    x = 0.0
    for i in range(n):
        left = x
        right = x + w50 + inner_gap
        hairpins.append((left, right, right + w50))
        if i < n - 1:
            x = right + w50 + gaps[i + 1]
    feed_in_x0 = hairpins[0][0] - gaps[0] - w50
    feed_out_x1 = hairpins[-1][2] + gaps[n] + w50

    return {
        "f0": f0,
        "fbw": fbw,
        "eps_r": eps_r,
        "h": h,
        "lam_g": lam_g,
        "eps_eff": eps_eff,
        "w50": w50,
        "inner_gap": inner_gap,
        "arm": arm,
        "fold": fold,
        "fold_path": fold_path,
        "j": j,
        "z0e": z0e,
        "z0o": z0o,
        "widths": widths,
        "gaps": gaps,
        "hairpins": hairpins,
        "feed_in_x0": feed_in_x0,
        "feed_out_x1": feed_out_x1,
        "span": feed_out_x1 - feed_in_x0,
    }


def pcb_hairpin_primitives(
    f0: float | None = None,
    fbw: float | None = None,
    substrate_eps: float | None = None,
    substrate_tand: float | None = None,
    substrate_thickness: float | None = None,
    ground_thickness: float = 0.25,
    feed_extra: float = 2.5,
    port_margin: float = 2.0,
    board_margin: float = 3.0,
    inner_gap: float | None = None,
    arm_scale: float | None = None,
    gap_scale: float | None = None,
) -> tuple[Project, list[dict[str, Any]]]:
    """Return (Project, primitives) for a compact 5-pole hairpin band-pass.

    A 5-pole Chebyshev band-pass (0.1 dB ripple, f0 = 2.4 GHz, ~10 % bandwidth)
    built from **five folded half-wave (hairpin) resonators** on RT/duroid 6010
    (eps_r 10.2, tan_d 0.0023, 1.27 mm, 35 um copper).  The resonators are
    edge-coupled: the facing arms of neighbouring hairpins form lambda_g/4
    coupled-line sections, the input/output are coupled to the first/last
    resonator over the same length — no vias, single-layer planar, and roughly
    a quarter wavelength (~22 mm) long because every resonator is folded.

    The coupled-line dimensions come from the Hammerstad-Jensen even-odd mode
    model in :func:`hairpin_bandpass_design`.  ``arm_scale`` / ``gap_scale``
    trim the resonance and the coupling (defaults from
    `HAIRPIN_BANDPASS_DESIGN`).

    Two lumped 50 ohm edge ports (in1/out1) terminate the feed lines.  One
    FDTD run measures one S-matrix column; see
    examples/run_pcb_hairpin_example.py for the two-run merge.
    """
    from openEMS.physical_constants import C0, EPS0

    from app.models.project import (
        BoundarySpec,
        DumpDef,
        MaterialDef,
        MeshSpec,
        NF2FFDef,
        PortDef,
        SimSpec,
    )

    des = hairpin_bandpass_design(
        f0=f0,
        fbw=fbw,
        eps_r=substrate_eps,
        h=substrate_thickness,
        inner_gap=inner_gap,
        arm_scale=arm_scale,
        gap_scale=gap_scale,
    )
    f0 = des["f0"]
    eps_r = des["eps_r"]
    h = des["h"]
    tand = float(
        HAIRPIN_BANDPASS_DESIGN["tand"] if substrate_tand is None else substrate_tand
    )

    w50 = des["w50"]
    arm = des["arm"]
    inner_gap = des["inner_gap"]
    hairpins = des["hairpins"]
    gaps = des["gaps"]
    z_top = h
    feed_len = arm + feed_extra

    # --- board ---------------------------------------------------------------
    x0 = des["feed_in_x0"] - port_margin - board_margin
    x1 = des["feed_out_x1"] + port_margin + board_margin
    y0 = -board_margin
    y1 = feed_len + board_margin
    hw = w50 / 2.0

    kappa = tand * 2.0 * math.pi * f0 * EPS0 * eps_r

    p = Project(name="pcb_hairpin")
    p.mesh = MeshSpec(
        preset="high",
        drawing_unit="mm",
        start_frequency=0.67 * f0,
        stop_frequency=1.33 * f0,
        target_frequency=f0,
        cells_per_wavelength=20.0,
        max_grading_ratio=2.0,
        # three cells across the tightest coupling gap (and the hairpin arms)
        min_cellsize=max(0.02, min(des["gaps"]) / 3.0),
        min_cellsize_z=h / 3.0,
        # easyMesh's edge smoothing/merging can create micro cells between the
        # closely spaced coupled lines (kills the CFL step) -> keep both off
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
        air_margin=[20.0, 20.0, 20.0, 20.0, 0.0, 25.0],
    )
    p.materials = [
        MaterialDef(name="copper", kind="pec"),
        MaterialDef(
            name="rtduroid",
            kind="dielectric",
            epsilon=eps_r,
            kappa=kappa,
            tand=tand,
        ),
    ]

    # --- metal ---------------------------------------------------------------
    primitives: list[dict[str, Any]] = [
        {
            "kind": "box",
            "name": "ground",
            "material": "copper",
            "priority": 90,
            "start": [x0, y0, -ground_thickness],
            "stop": [x1, y1, 0.0],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "substrate",
            "material": "rtduroid",
            "priority": 10,
            "start": [x0, y0, 0.0],
            "stop": [x1, y1, z_top],
        },
        {
            "kind": "box",
            "name": "feed_in",
            "material": "copper",
            "priority": 100,
            "start": [des["feed_in_x0"], 0.0, z_top],
            "stop": [des["feed_in_x0"] + w50, feed_len, z_top],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "feed_out",
            "material": "copper",
            "priority": 100,
            "start": [des["feed_out_x1"] - w50, 0.0, z_top],
            "stop": [des["feed_out_x1"], feed_len, z_top],
            "edges2grid": "xy",
        },
    ]
    for i, (left, right, right_end) in enumerate(hairpins, start=1):
        primitives += [
            {
                "kind": "box",
                "name": f"res{i}_arm1",
                "material": "copper",
                "priority": 100,
                "start": [left, 0.0, z_top],
                "stop": [left + w50, arm, z_top],
                "edges2grid": "xy",
            },
            {
                "kind": "box",
                "name": f"res{i}_arm2",
                "material": "copper",
                "priority": 100,
                "start": [right, 0.0, z_top],
                "stop": [right_end, arm, z_top],
                "edges2grid": "xy",
            },
            {
                "kind": "box",
                "name": f"res{i}_fold",
                "material": "copper",
                "priority": 100,
                "start": [left, 0.0, z_top],
                "stop": [right_end, w50, z_top],
                "edges2grid": "xy",
            },
        ]

    # --- explicit grid lines for the coupled region --------------------------
    # easyMesh does not put grid lines on the x-faces of these zero-thickness
    # metal sheets, so the 0.32 mm coupling gaps and the 1.19 mm arms would be
    # swallowed by the coarse air grid (the whole layout then acts as one solid
    # metal block and nothing gets through). Every metal face — plus a few lines
    # inside each gap — is therefore placed explicitly; the Mesh Editor shows
    # them and the user can change them.
    def _faces(axis: int) -> list[float]:
        return sorted(
            {
                v
                for pr in primitives
                if pr["name"] not in ("ground", "substrate")
                for v in (pr["start"][axis], pr["stop"][axis])
            }
        )

    def _fill(edges: list[float], max_cell: float) -> list[float]:
        out: list[float] = list(edges)
        for a, b in zip(edges, edges[1:]):
            n = max(1, int(math.ceil((b - a) / max_cell - 1e-9)))
            out += [a + (b - a) * k / n for k in range(1, n)]
        return sorted(out)

    p.mesh.manual_mesh_lines = {
        "x": _fill(_faces(0), 0.40),
        "y": _fill(_faces(1), 0.45),
        "z": _fill([-ground_thickness, 0.0, z_top], h / 3.0),
    }

    # --- ports ---------------------------------------------------------------
    p.ports = [
        PortDef(
            name="in1",
            type="lumped",
            start=[des["feed_in_x0"], feed_len, 0.0],
            stop=[des["feed_in_x0"] + w50, feed_len, z_top],
            resistance=50.0,
            direction="z",
            excite_port=True,
            mesh_dirs="xy",
            lumped_width="x",
            metal="feed_in",
        ),
        PortDef(
            name="out1",
            type="lumped",
            start=[des["feed_out_x1"] - w50, feed_len, 0.0],
            stop=[des["feed_out_x1"], feed_len, z_top],
            resistance=50.0,
            direction="z",
            excite_port=False,
            mesh_dirs="xy",
            lumped_width="x",
            metal="feed_out",
        ),
    ]
    p.dumps = [
        DumpDef(
            name="E_substrate",
            quantity="E",
            start=[x0, y0, z_top / 2.0],
            stop=[x1, y1, z_top / 2.0],
            frequency_domain=True,
            frequency=f0,
        )
    ]
    p.nf2ff = NF2FFDef(
        enabled=False,
        start=[x0 - 10.0, y0 - 10.0, -1.0],
        stop=[x1 + 10.0, y1 + 10.0, z_top + 10.0],
        center=[0.0, 0.5 * (y0 + y1), z_top / 2.0],
        radius=1.0,
        frequencies=[f0],
        theta_steps=91,
        phi_steps=181,
    )
    # Simulation budget: the hairpin is a narrow-band coupled-resonator filter.
    # Two constraints set the run length, not the excitation pulse:
    #   * the DFT resolution is 1/T_sim, so the ~240 MHz passband with its
    #     ~50 MHz mode spacing needs T_sim >= ~60 ns (>= ~1.2 M steps),
    #   * the stopband ring-down is set by the unloaded Q of the substrate
    #     (tan_d = 0.0023 -> Q0 ~ 435 -> tau ~ 58 ns), so an end criteria of
    #     1e-5 (-50 dB) would need several million steps.
    # The preset therefore stops at -40 dB (1e-4) and caps at 600 k steps:
    # enough for a usable S-parameter trace, far below "mathematically exact".
    p.sim = SimSpec(
        end_criteria=1e-4,
        max_timesteps=600_000,
        nf2ff_enabled=False,
        field_dumps_enabled=True,
    )

    p.notes = (
        "pcb_hairpin_primitives — 5-pole Chebyshev hairpin band-pass, 0.1 dB "
        f"ripple, on RT/duroid 6010 (eps_r {eps_r}, tan_d {tand}, "
        f"{h} mm, 35 um copper), f0={f0 / 1e9:.2f} GHz, "
        f"{des['fbw'] * 100:.0f} % bandwidth. Arm length {arm:.2f} mm, "
        f"50 ohm width {w50:.3f} mm, coupling gaps "
        + "/".join(f"{s:.3f}" for s in gaps)
        + " mm, no vias."
    )
    p.primitives = list(primitives)
    return p, primitives
