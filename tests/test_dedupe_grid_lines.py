"""Grid-line dedupe: the float32 ULP floor (P1, ``ulp_align``)."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("CSXCAD")

from CSXCAD import ContinuousStructure  # noqa: E402

from app.simulation.grid_dedupe import dedupe_grid_lines  # noqa: E402

#: A sub-resolution cluster at the *end* of the axis: the last line is a domain
#: edge (protected in both modes), so the only difference between the modes is
#: whether the 1e-8 pair merges into the 20 mm line.
LINES = [0.0, 20.0, 20.00000001, 20.00000002]


def _csx(values=LINES) -> ContinuousStructure:
    csx = ContinuousStructure()
    csx.GetGrid().SetLines(0, [float(v) for v in values])
    csx.GetGrid().SetLines(1, [0.0, 10.0])
    csx.GetGrid().SetLines(2, [0.0, 10.0])
    return csx


def _x(csx) -> np.ndarray:
    return np.sort(np.asarray(csx.GetGrid().GetLines("x"), dtype=float))


def test_ulp_floor_merges_sub_resolution_pairs() -> None:
    csx = _csx()
    dedupe_grid_lines(csx, ulp_align=True)
    x = _x(csx)
    assert len(x) == 3  # 0 / 20 / 20.00000002 (domain edge) - the pair is gone
    assert x[1] == pytest.approx(20.0)


def test_ulp_floor_can_be_switched_off() -> None:
    csx = _csx()
    dedupe_grid_lines(csx, ulp_align=False)
    assert len(_x(csx)) == len(LINES)  # legacy: the sub-resolution pair survives


def test_ulp_floor_does_not_touch_separated_lines() -> None:
    csx = _csx([0.0, 1.0, 2.0, 3.0])
    dedupe_grid_lines(csx, ulp_align=True)
    assert len(_x(csx)) == 4


def test_exact_duplicates_still_merge_in_both_modes() -> None:
    for flag in (True, False):
        csx = _csx([0.0, 5.0, 5.0, 9.0])
        dedupe_grid_lines(csx, ulp_align=flag)
        assert len(_x(csx)) == 3, flag  # the exact duplicate collapses
