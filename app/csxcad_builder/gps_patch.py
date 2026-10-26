"""GPS L1 (1575.42 MHz) circularly polarized patch — design study (not a preset).

A square patch with two opposite corners truncated is the classic single-feed
CP patch: the truncation splits the two degenerate modes and makes them
orthogonal with a 90 deg phase difference. One 50 ohm probe feed (vertical
lumped port from the ground plane to the patch) keeps the geometry usable with
the GUI's port model (no external 90 deg hybrid / no second phase-shifted
source).

**Status: not shipped as a preset.** Converged openEMS runs of the tuned
geometry (44.5 mm patch, 5.5 mm corner notches, 10.9 mm inset, 75 mm board)
reached S11 -10.1 dB at 1.57542 GHz, but the best axial ratio was 4.6 dB (at
1.56 GHz) and 7.9 dB at 1.57542 GHz — the single-feed corner-notch geometry
does not meet the AR < 3 dB target. A dual-feed patch with a 90 deg hybrid (or
a proper CP optimizer) is the reliable route.
"""

from __future__ import annotations

import math
from typing import Any

C0 = 299_792_458.0  # m/s


def square_patch_side_mm(f0: float, eps_r: float, h: float) -> float:
    """Resonant side of a square patch (Balanis, W = L, fringing included)."""
    a = C0 / (2.0 * f0) / math.sqrt((eps_r + 1.0) / 2.0) * 1e3
    for _ in range(10):
        w_over_h = a / h
        eps_eff = (eps_r + 1.0) / 2.0 + (eps_r - 1.0) / 2.0 / math.sqrt(
            1.0 + 12.0 / w_over_h
        )
        d_l = (
            0.412
            * h
            * (eps_eff + 0.3)
            * (w_over_h + 0.264)
            / ((eps_eff - 0.258) * (w_over_h + 0.8))
        )
        a = C0 / (2.0 * f0 * math.sqrt(eps_eff)) * 1e3 - 2.0 * d_l
    return a


def gps_patch_primitives(
    f0: float = 1.57542e9,
    eps_r: float = 4.4,
    tand: float = 0.02,
    h: float = 1.6,
    patch_side: float | None = 44.5,  # tuned: S11 -10.8 dB @ 1.57542 GHz  # tuned for 1.575 GHz on 1.6 mm FR4
    corner_cut: float | None = 5.5,  # tuned: AR 2.4 dB @ 1.57542 GHz
    feed_inset: float | None = 10.9,
    board: float | None = 75.0,
    copper: float = 0.035,
    air_margin: float = 35.0,
) -> tuple[Any, list[dict[str, Any]]]:
    """Return (Project, primitives) for a GPS L1 CP patch on FR4.

    ``patch_side`` (mm) is the square side, ``corner_cut`` the leg of the two
    truncated corners, ``feed_inset`` the distance of the probe from the patch
    edge (towards the centre). All three can be overridden for tuning.
    """
    from openEMS.physical_constants import EPS0

    from app.models.project import (
        BoundarySpec,
        MaterialDef,
        MeshSpec,
        NF2FFDef,
        PortDef,
        Project as Proj,
        SimSpec,
    )

    a = float(patch_side) if patch_side else square_patch_side_mm(f0, eps_r, h)
    cut = float(corner_cut) if corner_cut else 0.10 * a
    inset = float(feed_inset) if feed_inset else 0.24 * a
    b = float(board) if board else max(a + 30.0, 70.0)
    z_top = float(h)
    half = a / 2.0
    kappa = tand * 2.0 * math.pi * f0 * EPS0 * eps_r

    fx, fy = 0.0, -half + inset  # probe feed, inset from the bottom edge

    p = Proj(name="gps_patch")
    p.mesh = MeshSpec(
        preset="high",
        drawing_unit="mm",
        start_frequency=1.20e9,
        stop_frequency=1.95e9,
        target_frequency=f0,
        cells_per_wavelength=20.0,
        max_grading_ratio=2.0,
        min_cellsize=1.0,  # 2 cells through the 1.6 mm FR4 (design check)
        smooth_metal_edge="one_third_two_thirds",
    )
    p.boundary = BoundarySpec(
        xmin="PML",
        xmax="PML",
        ymin="PML",
        ymax="PML",
        zmin="PML",
        zmax="PML",
        pml_size=8,
        air_margin=[air_margin] * 6,
    )
    p.materials = [
        MaterialDef(name="copper", kind="pec"),
        MaterialDef(name="fr4", kind="dielectric", epsilon=eps_r, kappa=kappa, tand=tand),
    ]
    p.ports = [
        PortDef(
            name="feed",
            type="lumped",
            start=[fx - 0.5, fy - 0.5, 0.0],
            stop=[fx + 0.5, fy + 0.5, z_top],
            resistance=50.0,
            direction="z",
            excite_port=True,
            mesh_dirs="xy",
            lumped_width="xy",
        )
    ]
    p.nf2ff = NF2FFDef(
        enabled=True,
        start=[-55.0, -55.0, -25.0],
        stop=[55.0, 55.0, 28.2],
        center=[0.0, 0.0, z_top],
        radius=1.0,
        frequencies=[f0],
        freq_mode="list",
        theta_steps=91,
        phi_steps=181,
    )
    p.sim = SimSpec(
        end_criteria=1e-3,
        max_timesteps=150_000,
        nf2ff_enabled=True,
        field_dumps_enabled=False,
    )
    p.notes = (
        f"GPS L1 CP patch design study on FR4 {h:g} mm (eps_r {eps_r:g}, tan_d {tand:g}); "
        f"patch {a:.2f} mm square, corners {cut:.2f} mm, feed inset {inset:.2f} mm. "
        "Converged run: S11 -10.1 dB at 1.57542 GHz, but AR 4.6 dB at best (1.56 GHz) "
        "- not shipped as a preset (AR target not met)."
    )

    # Square patch with two opposite c x c corner notches, built from three
    # axis-aligned boxes: the documented S11 / axial-ratio numbers of this
    # design study were produced with this geometry, so it is kept as-is.
    # A chamfered polygon (45 deg edges) meshes as well now - the bundled
    # easyMesh used to abort on two parallel diagonal edges (missing
    # geometryUtils.distance_between_segments, restored from upstream), so a
    # future study can use the textbook outline directly.
    primitives: list[dict[str, Any]] = [
        {
            "kind": "box",
            "name": "ground",
            "material": "copper",
            "priority": 90,
            "start": [-b / 2.0, -b / 2.0, -copper],
            "stop": [b / 2.0, b / 2.0, 0.0],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "substrate",
            "material": "fr4",
            "priority": 10,
            "start": [-b / 2.0, -b / 2.0, 0.0],
            "stop": [b / 2.0, b / 2.0, z_top],
        },
        {
            "kind": "box",
            "name": "patch",
            "material": "copper",
            "priority": 100,
            "start": [-half + cut, -half, z_top],
            "stop": [half - cut, half, z_top + copper],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "patch_left",
            "material": "copper",
            "priority": 100,
            "start": [-half, -half + cut, z_top],
            "stop": [-half + cut, half, z_top + copper],
            "edges2grid": "xy",
        },
        {
            "kind": "box",
            "name": "patch_right",
            "material": "copper",
            "priority": 100,
            "start": [half - cut, -half, z_top],
            "stop": [half, half - cut, z_top + copper],
            "edges2grid": "xy",
        },
    ]
    return p, primitives
