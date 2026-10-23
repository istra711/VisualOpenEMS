"""Unit tests for the compact 2.4 GHz hairpin band-pass synthesis (no FDTD)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def test_coupled_microstrip_matches_hammerstad_jensen_example():
    """Steer, Microwave and RF Design II, Sec. 5.6 worked example."""
    from app.csxcad_builder.hairpin import (
        coupled_ms_modes,
        msl_eps_eff_u,
        msl_z0_u,
    )

    z0e, z0o, eps_ee, eps_eo = coupled_ms_modes(1.0, 0.5, 10.0)
    assert abs(z0e - 59.0) < 1.0, z0e
    assert abs(z0o - 37.0) < 1.0, z0o
    assert abs(eps_ee - 7.28) < 0.05, eps_ee
    assert abs(eps_eo - 5.82) < 0.05, eps_eo
    # ... and reduces to the single line when the strips are far apart
    z0e_far, z0o_far, _ee, _eo = coupled_ms_modes(1.0, 50.0, 10.0)
    assert abs(z0e_far - msl_z0_u(1.0, 10.0)) < 0.5, z0e_far
    assert abs(z0o_far - msl_z0_u(1.0, 10.0)) < 0.5, z0o_far
    assert abs(msl_eps_eff_u(1.0, 10.0) - 6.71) < 0.05


def test_msl_width_roundtrip():
    from app.csxcad_builder.hairpin import msl_u_for_z0, msl_z0_u

    for eps_r in (2.2, 6.15, 10.2):
        for z0 in (25.0, 50.0, 75.0):
            u = msl_u_for_z0(z0, eps_r)
            assert abs(msl_z0_u(u, eps_r) - z0) < 1e-6, (eps_r, z0)


def test_hairpin_design_is_symmetric_and_physical():
    from app.csxcad_builder.hairpin import (
        coupled_ms_modes,
        hairpin_bandpass_design,
        msl_u_for_z0,
    )

    d = hairpin_bandpass_design()
    assert abs(d["f0"] - 2.4e9) < 1.0
    assert abs(d["eps_r"] - 10.2) < 1e-9
    # 50 ohm line on RT/duroid 6010 / 1.27 mm
    assert 1.0 < d["w50"] < 1.4, d["w50"]
    assert 40.0 < d["lam_g"] < 55.0, d["lam_g"]
    # folded half-wave resonator: two arms plus the fold fit lambda_g/2
    assert 0.5 * d["lam_g"] * 0.75 < 2.0 * d["arm"] < 0.5 * d["lam_g"], d["arm"]

    gaps = d["gaps"]
    assert len(gaps) == 6
    assert gaps == gaps[::-1], gaps  # symmetric filter
    # 5 resonators -> 4 internal couplings; the middle ones are the weakest
    assert gaps[0] < gaps[1] < gaps[2], gaps
    assert abs(gaps[2] - gaps[3]) < 1e-9, gaps
    assert gaps[3] > gaps[4] > gaps[5], gaps
    assert min(gaps) > 0.15, f"gap below a cheap PCB process: {min(gaps)}"

    # the gaps really realise the requested coupling coefficients
    u = msl_u_for_z0(50.0, d["eps_r"])
    for z_e, z_o, s in zip(d["z0e"], d["z0o"], gaps):
        e, o, _ee, _eo = coupled_ms_modes(u, s / d["h"], d["eps_r"])
        k_want = (z_e - z_o) / (z_e + z_o)
        k_get = (e - o) / (e + o)
        assert abs(k_get - k_want) < 5e-3, (k_want, k_get)
        assert float(z_e) > 50.0 > float(z_o)


def test_hairpin_arm_length_uses_odd_mode():
    """The folded resonator runs in the *odd* mode of its own arm pair."""
    from app.csxcad_builder.hairpin import (
        coupled_ms_modes,
        hairpin_bandpass_design,
    )
    from openEMS.physical_constants import C0

    d = hairpin_bandpass_design()
    _z0e, _z0o, _ee, eps_eo = coupled_ms_modes(
        d["w50"] / d["h"], d["inner_gap"] / d["h"], d["eps_r"]
    )
    # the wavelength really is the odd-mode one, not the single-line one
    assert abs(d["eps_eff"] - eps_eo) < 1e-12
    lam_g = C0 / (d["f0"] * math.sqrt(eps_eo)) * 1e3
    assert abs(d["lam_g"] - lam_g) < 1e-9
    # two arms plus the centre line of the bend form a half wave
    assert abs(2.0 * d["arm"] + d["fold_path"] - lam_g / 2.0) < 0.05
    assert abs(d["fold_path"] - (d["w50"] + d["inner_gap"])) < 1e-12


def test_hairpin_layout_geometry():
    from app.csxcad_builder.hairpin import (
        hairpin_bandpass_design,
        pcb_hairpin_primitives,
    )

    d = hairpin_bandpass_design()
    project, prims = pcb_hairpin_primitives()
    names = [str(p["name"]) for p in prims]
    assert names[:2] == ["ground", "substrate"]
    assert len([n for n in names if n.startswith("res")]) == 15  # 5 x (2 arms + fold)
    assert "feed_in" in names and "feed_out" in names
    assert len(names) == len(set(names)), "primitive names must be unique"

    z_top = 1.27
    top = [p for p in prims if p["name"] not in ("ground", "substrate")]
    for p in top:
        assert abs(p["start"][2] - z_top) < 1e-6, p["name"]
        assert abs(p["stop"][2] - z_top) < 1e-6, p["name"]

    def box(name):
        return next(p for p in prims if p["name"] == name)

    # the metal sits on the substrate, the ground underneath
    sub = box("substrate")
    gnd = box("ground")
    assert abs(sub["start"][2]) < 1e-9 and abs(sub["stop"][2] - 1.27) < 1e-9
    assert gnd["stop"][2] == 0.0 and gnd["start"][2] < 0.0

    # board covers every metal feature with margin
    for p in top:
        assert p["start"][0] > sub["start"][0] and p["stop"][0] < sub["stop"][0], p["name"]
        assert p["start"][1] >= sub["start"][1] and p["stop"][1] < sub["stop"][1], p["name"]

    # five hairpins, each with a fold connecting its two arms at the bottom
    for i in range(1, 6):
        a1, a2, fold = (box(f"res{i}_{part}") for part in ("arm1", "arm2", "fold"))
        gap = a2["start"][0] - a1["stop"][0]
        assert abs(gap - 0.8) < 1e-9, gap
        assert abs(a1["start"][0] - fold["start"][0]) < 1e-9
        assert abs(a2["stop"][0] - fold["stop"][0]) < 1e-9
        assert fold["stop"][1] > 0.0 and fold["stop"][1] < a1["stop"][1]
        assert abs(a1["stop"][1] - a2["stop"][1]) < 1e-9

    # coupling gaps between the resonators are the synthesised ones
    for i in range(1, 5):
        left = box(f"res{i}_arm2")
        right = box(f"res{i + 1}_arm1")
        assert abs((right["start"][0] - left["stop"][0])) > 0.15

    feed_in = box("feed_in")
    assert abs(box("res1_arm1")["start"][0] - feed_in["stop"][0] - d["gaps"][0]) < 1e-9
    feed_out = box("feed_out")
    assert (
        abs(feed_out["start"][0] - box("res5_arm2")["stop"][0] - d["gaps"][5]) < 1e-9
    )
    assert feed_in["stop"][1] > box("res1_arm1")["stop"][1]  # feeds reach the port


def test_hairpin_ports_and_material():
    from app.csxcad_builder.hairpin import (
        hairpin_bandpass_design,
        pcb_hairpin_primitives,
    )

    d = hairpin_bandpass_design()
    project, prims = pcb_hairpin_primitives()
    assert len(project.ports) == 2
    assert [p.name for p in project.ports] == ["in1", "out1"]
    assert project.ports[0].excite_port is True
    assert project.ports[1].excite_port is False
    for port in project.ports:
        assert port.resistance == 50.0
        assert port.direction == "z"
        assert port.lumped_width == "x"  # the line runs along y
        assert abs(port.stop[2] - 1.27) < 1e-9
        assert abs((port.stop[0] - port.start[0]) - 1.186) < 5e-3  # line width
        assert port.mesh_dirs == "xy"

    mats = {m.name: m for m in project.materials}
    assert "copper" in mats and mats["copper"].kind == "pec"
    rt = mats["rtduroid"]
    assert rt.kind == "dielectric"
    assert abs(rt.epsilon - 10.2) < 1e-9
    assert abs(rt.tand - 0.0023) < 1e-9
    # dielectric loss enters as kappa = tand * 2 pi f0 eps0 eps_r
    from openEMS.physical_constants import EPS0

    assert abs(rt.kappa - 0.0023 * 2.0 * math.pi * 2.4e9 * EPS0 * 10.2) < 1e-12

    # mesh keeps the tight coupling gap resolved and the substrate 3 cells thick
    assert abs(project.mesh.min_cellsize - min(d["gaps"]) / 3.0) < 1e-12
    assert abs(project.mesh.min_cellsize_z - 1.27 / 3.0) < 1e-12
    assert project.mesh.smooth_metal_edge == "False"
    assert project.mesh.handle_closely_placed_edges is False
    assert project.mesh.stop_frequency > 2.52e9 and project.mesh.start_frequency < 2.28e9


def test_hairpin_tuning_knobs():
    """arm_scale / gap_scale shift the resonance and the coupling."""
    from app.csxcad_builder.hairpin import hairpin_bandpass_design

    base = hairpin_bandpass_design()
    longer = hairpin_bandpass_design(arm_scale=1.05)
    wider = hairpin_bandpass_design(gap_scale=1.2)
    assert abs(longer["arm"] - base["arm"] * 1.05) < 1e-9
    assert all(w > b for w, b in zip(wider["gaps"], base["gaps"]))
