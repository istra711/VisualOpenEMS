"""Pure tests for the NF2FF fit margin (app.meshing.bounds).

Antenna mode must place the NF2FF far-field box at a configurable multiple of
λ(f_start) (clamped inside the PML); PCB/generic runs keep the small
λ/4-at-f_stop box.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.meshing.bounds import (
    domain_air_margin_units,
    nf2ff_box_center,
    nf2ff_center_offset,
    nf2ff_fit_box,
    nf2ff_fit_margin_units,
)
from app.meshing.rules import compute_targets
from app.models.project import MeshSpec


def _mesh():
    return MeshSpec(
        preset="high",
        drawing_unit="mm",
        start_frequency=1.95e9,
        stop_frequency=2.95e9,
        target_frequency=2.45e9,
        refined_cellsize=1.0,
    )


def _targets():
    return compute_targets(_mesh())


def _boundary():
    return SimpleNamespace(air_margin=["auto"] * 6)


def test_pcb_run_keeps_small_box():
    sim = SimpleNamespace(antenna_mode=False)
    margin = nf2ff_fit_margin_units(_mesh(), sim, _boundary(), _targets())
    # 0.7 * lambda(2.95 GHz)/4 / 1e-3 m  ~= 17.78 mm
    assert margin == pytest.approx(17.78, abs=0.1)


def test_antenna_mode_scales_with_lambda_at_f_start():
    sim = SimpleNamespace(
        antenna_mode=True, antenna_pml_lambda=1.0, antenna_nf2ff_lambda=0.25
    )
    margin = nf2ff_fit_margin_units(_mesh(), sim, _boundary(), _targets())
    # 0.25 * lambda(1.95 GHz) / 1e-3 m  ~= 38.4 mm
    assert margin == pytest.approx(38.44, abs=0.2)


def test_antenna_box_clamped_inside_pml():
    # A huge NF2FF fraction is capped at 0.8x the (PML-distance) air margin.
    sim = SimpleNamespace(
        antenna_mode=True, antenna_pml_lambda=0.5, antenna_nf2ff_lambda=2.0
    )
    mesh = _mesh()
    targets = _targets()
    margin = nf2ff_fit_margin_units(mesh, sim, _boundary(), targets)
    air = domain_air_margin_units(mesh, sim, _boundary(), targets)
    assert margin == pytest.approx(0.8 * air)
    assert margin < air


def test_antenna_default_fraction_is_half_lambda():
    sim = SimpleNamespace(antenna_mode=True)  # no explicit lambdas
    mesh = _mesh()
    targets = _targets()
    air = domain_air_margin_units(mesh, sim, _boundary(), targets)
    margin = nf2ff_fit_margin_units(mesh, sim, _boundary(), targets)
    # default 0.5 * lambda(f_start) == the default air margin, so the 0.8 cap wins
    assert margin == pytest.approx(0.8 * air)


def test_fit_box_has_equal_margin_on_all_faces():
    # z-directed dipole (arms to +-29, radius 1 → bbox +-30 on x/y, +-29 on z)
    lo = [-30.0, -30.0, -29.0]
    hi = [30.0, 30.0, 29.0]
    start, stop = nf2ff_fit_box(lo, hi, margin=61.5)
    assert start == pytest.approx([-91.5, -91.5, -90.5])
    assert stop == pytest.approx([91.5, 91.5, 90.5])
    # equal stand-off on every face (no squeezed z margin)
    for i in range(3):
        assert lo[i] - start[i] == pytest.approx(61.5)
        assert stop[i] - hi[i] == pytest.approx(61.5)


def test_fit_box_is_symmetric_for_symmetric_source():
    lo = [-30.0, -30.0, -29.0]
    hi = [30.0, 30.0, 29.0]
    start, stop = nf2ff_fit_box(lo, hi, margin=40.0)
    for i in range(3):
        assert start[i] == pytest.approx(-stop[i])


# --- NF2FF box/center consistency (the source may be arbitrarily asymmetric) ---


def test_box_center_of_symmetric_box_is_zero():
    assert nf2ff_box_center([-62.5, -62.5, -88.5], [62.5, 62.5, 88.5]) == [0.0, 0.0, 0.0]


def test_center_offset_flags_stale_reference():
    # A box (symmetric about the geometry) whose stored center is left over from
    # an old asymmetric fit: this is exactly the dipole bug.
    start, stop = [-77.9, -77.9, -103.9], [77.9, 77.9, 103.9]
    assert nf2ff_center_offset(start, stop, [0.0, 0.0, 0.0]) == pytest.approx(0.0)
    assert nf2ff_center_offset(start, stop, [0.0, 0.0, 6.15]) == pytest.approx(6.15)


def test_center_offset_does_not_need_source_symmetry():
    # A genuinely asymmetric source (arms on one side only) is fine as long as
    # the box is centred on its own geometry — the check is box geometry only.
    lo = [0.0, 0.0, 0.0]
    hi = [4.0, 4.0, 29.0]
    start, stop = nf2ff_fit_box(lo, hi, margin=60.0)
    center = nf2ff_box_center(start, stop)
    assert center == pytest.approx([2.0, 2.0, 14.5])
    assert nf2ff_center_offset(start, stop, center) == pytest.approx(0.0)
    # the geometry center equals the box center (equal margins)
    assert center == pytest.approx([0.5 * (lo[i] + hi[i]) for i in range(3)])

