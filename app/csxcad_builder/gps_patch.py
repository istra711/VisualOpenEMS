"""GPS L1 (1575.42 MHz) circularly polarized patch — preset.

A square patch with two opposite corners chamfered is the classic single-feed
CP patch: the chamfer splits the two degenerate modes and makes them
orthogonal with a 90 deg phase difference. One 50 ohm probe feed (vertical
lumped port from the ground plane to the patch) keeps the geometry usable with
the GUI's port model (no external 90 deg hybrid / no second phase-shifted
source).

Two design rules matter here and are easy to get wrong:

* **Feed on the patch centre line, not on a diagonal.** The modal field of the
  dominant mode is ``sin(pi*x/a)`` - maximum at the radiating edges, zero in
  the middle. A probe at ``(0, y0)`` therefore couples to both split modes with
  equal amplitude; a probe on a diagonal couples to only one of them and the
  antenna radiates linear polarization (measured: axial ratio 25 ... 39 dB).
* **Model the chamfer as a staircase, not as a 45 deg edge.** CSXCAD rasterises
  a polygon with a point-in-polygon test at the cell centre
  (``CSPrimPolygon::IsInside``). With 5 mm cells a true 45 deg edge falls
  inside a single cell and becomes all-metal or all-air; axis-aligned steps of
  at least one mesh cell put the outline on the mesh lines and keep the
  removed area within ``c * step / 2`` of the ideal triangle. The top-right
  chamfer is the 180 deg rotation of the bottom-left one: an independently
  drawn second staircase is a mirror, its removed area differs, and the lost
  two-fold symmetry costs axial ratio.

Measured with openEMS (converged to -30 dB energy), FR4 1.6 mm, 75 mm ground,
44.10 mm patch, 5.0 mm chamfer (5 steps of 1.0 mm):

==========================  =====================
quantity                    value
==========================  =====================
|S11| @ 1575.42 MHz         -12.4 dB
axial ratio @ 1575.42 MHz   1.70 dB (RHCP)
axial ratio minimum         0.93 dB @ 1570.0 MHz
directivity                 7.29 dBi
radiation efficiency        27.3 %
S11 minimum                 -19.1 dB @ 1597.5 MHz
==========================  =====================

The pattern peak sits at theta ~ 22 deg (the single feed tilts the beam);
directivity at boresight is 6.12 dBi.
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


def chamfered_patch_points(
    a: float, cut: float, step: float = 1.0
) -> list[list[float]]:
    """Outline of a square ``a`` x ``a`` with two opposite corners chamfered.

    The bottom-left and top-right corners are cut by ``cut`` mm, written as a
    staircase with axis-aligned edges of at least ``step`` mm (see the module
    docstring for why the 45 deg edge is not used). The polygon is returned
    counter-clockwise in mm, starting at the bottom edge next to the
    bottom-left chamfer.
    """
    h = a / 2.0
    n = max(1, int(cut / step))  # floor: keeps every step >= ``step`` mm
    s = cut / n

    bottom_left: list[list[float]] = []  # (-h, -h+cut) -> (-h+cut, -h)
    for k in range(n):
        bottom_left.append([-h + k * s, -h + cut - k * s])
        bottom_left.append([-h + (k + 1) * s, -h + cut - k * s])
    bottom_left.append([-h + cut, -h])

    top_right = [[-x, -y] for x, y in bottom_left]  # (h, h-cut) -> (h-cut, h)

    return bottom_left + [[h, -h]] + top_right + [[-h, h]]


def chamfer_removed_area(a: float, cut: float, step: float = 1.0) -> float:
    """Metal area removed by one chamfer (mm^2) — the ideal triangle is c^2/2."""
    pts = chamfered_patch_points(a, cut, step)
    area = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        area += x0 * y1 - x1 * y0
    return (a * a - abs(area) / 2.0) / 2.0


def gps_patch_primitives(
    f0: float = 1.57542e9,
    eps_r: float = 4.4,
    tand: float = 0.02,
    h: float = 1.6,
    patch_side: float | None = 44.10,  # tuned: AR dip at 1.570 GHz
    corner_cut: float | None = 5.0,  # tuned: AR 1.70 dB @ 1.57542 GHz
    feed_inset: float | None = 10.9,  # tuned: S11 -12.4 dB @ 1.57542 GHz
    stair_step: float = 1.0,  # chamfer staircase edge (>= mesh min cell)
    board: float | None = 75.0,
    copper: float = 0.035,
    air_margin: float = 35.0,
) -> tuple[Any, list[dict[str, Any]]]:
    """Return (Project, primitives) for the GPS L1 CP patch on FR4.

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

    fx, fy = 0.0, -half + inset  # probe on the centre line, inset from the edge

    p = Proj(name="gps_patch")
    p.mesh = MeshSpec(
        preset="high",
        drawing_unit="mm",
        start_frequency=1.20e9,
        stop_frequency=1.95e9,
        target_frequency=f0,
        cells_per_wavelength=20.0,
        max_grading_ratio=2.0,
        min_cellsize=1.0,  # 2 cells through the 1.6 mm FR4
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
        f"GPS L1 CP patch preset on FR4 {h:g} mm (eps_r {eps_r:g}, tan_d {tand:g}); "
        f"{a:.2f} mm square, {cut:.2f} mm chamfer on two opposite corners "
        f"({stair_step:g} mm staircase), centre-line probe feed {inset:.2f} mm "
        "inset. Converged run: S11 -12.4 dB, AR 1.70 dB (RHCP) at 1.57542 GHz, "
        "D 7.29 dBi, efficiency 27.3 %."
    )

    # Chamfered outline as a polygon (staircase, see module docstring).
    points = chamfered_patch_points(a, cut, stair_step)
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
            "kind": "polygon",
            "name": "patch",
            "material": "copper",
            "priority": 100,
            "axis": "z",
            "coord": z_top,
            "length": copper,
            "points": points,
            "start": [-half, -half, z_top],
            "stop": [half, half, z_top + copper],
        },
    ]
    return p, primitives
