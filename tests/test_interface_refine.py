"""Graded refinement at ε (material) interfaces (app.meshing.interface_refine).

Deterministic, CSXCAD-free checks of the pure geometry helpers:

* ``material_jump_sides`` emits BOTH sides of every dielectric jump so the ramp
  is produced into the dielectric and into the surrounding air.
* ``interface_refine_lines`` keeps the cross-interface cell ratio within one
  grading step (continuity) and never refines an already uniform grid.
"""

from __future__ import annotations

from app.meshing.interface_refine import (
    interface_refine_lines,
    material_jump_sides,
)
class _Info:
    """Minimal stand-in for a CSXCAD primitive (kind / start / stop / epsilon)."""

    def __init__(self, kind, start, stop, epsilon=1.0):
        self.kind = kind
        self.start = start
        self.stop = stop
        self.epsilon = epsilon


def _cells(lines):
    return [b - a for a, b in zip(lines, lines[1:])]


def _seq(lines, new):
    return sorted(set(lines) | set(new))


# --- material_jump_sides -------------------------------------------------- #


def test_material_jump_both_sides():
    box = _Info("dielectric", (0, 0, 4.0), (0, 0, 6.0), epsilon=2.0)
    sides = material_jump_sides([box], 2)
    assert (4.0, -1) in sides and (4.0, 1) in sides
    assert (6.0, 1) in sides and (6.0, -1) in sides


def test_adjacent_dielectrics_shared_plane_deduped():
    a = _Info("dielectric", (0, 0, 4.0), (0, 0, 6.0), epsilon=2.0)
    b = _Info("dielectric", (0, 0, 6.0), (0, 0, 8.0), epsilon=3.0)
    sides = material_jump_sides([a, b], 2)
    assert sides.count((6.0, 1)) == 1
    assert sides.count((6.0, -1)) == 1
    assert (4.0, -1) in sides and (8.0, 1) in sides


def test_face_coincident_with_metal_is_skipped():
    box = _Info("dielectric", (0, 0, 4.0), (0, 0, 6.0), epsilon=2.0)
    metal = _Info("metal", (0, 0, 4.0), (0, 0, 6.0))
    assert material_jump_sides([metal, box], 2) == []


def test_sheet_is_not_a_medium():
    sheet = _Info("conducting_sheet", (0, 0, 4.0), (0, 0, 4.0))
    assert material_jump_sides([sheet], 2) == []


# --- inward / outward ramps ----------------------------------------------- #


def test_inward_face_refines_dielectric():
    # fine air cell just below plane 4 would not help without the inward face
    lines = [0.0, 2.0, 3.9, 4.0, 6.0, 8.0, 10.0]
    new = interface_refine_lines(lines, [(4.0, 1)], fine=0.25, growth=2.0, continuity=True)
    assert new  # ramp inserted
    assert all(4.0 < v < 6.0 for v in new)  # inside the dielectric
    cells = _cells(_seq(lines, new))
    i = _seq(lines, new).index(4.0)
    assert cells[i] / cells[i - 1] <= 2.0 + 1e-9  # cross-interface ratio
    assert all(b / a <= 2.0 + 1e-9 for a, b in zip(cells[i - 1 :], cells[i:]))


def test_outward_face_refines_air():
    # fine dielectric cell just above plane 4 -> ramp into the air below
    lines = [0.0, 2.0, 4.0, 4.1, 6.0, 8.0, 10.0]
    new = interface_refine_lines(lines, [(4.0, -1)], fine=0.25, growth=2.0, continuity=True)
    assert new
    assert all(2.0 < v < 4.0 for v in new)  # inside the air region


def test_uniform_grid_not_refined():
    lines = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    new = interface_refine_lines(
        lines, [(4.0, -1), (4.0, 1), (6.0, 1), (6.0, -1)], fine=0.25, growth=2.0, continuity=True
    )
    assert new == []


def test_continuity_keeps_cross_ratio():
    lines = [0.0, 0.25, 0.4, 3.0, 9.0]
    new = interface_refine_lines(lines, [(0.4, 1)], fine=None, growth=2.0, continuity=True)
    assert new
    cells = _cells(_seq(lines, new))
    i = _seq(lines, new).index(0.4)
    assert cells[i] / cells[i - 1] <= 2.0 + 1e-9


def test_no_more_cells_than_grading_ratio():
    lines = [0.0, 2.0, 3.9, 4.0, 6.0, 8.0, 10.0]
    new = interface_refine_lines(lines, [(4.0, 1)], fine=0.25, growth=2.0, continuity=True)
    seq = _seq(lines, new)
    for a, b in zip(_cells(seq), _cells(seq)[1:]):
        assert b / a <= 2.0 + 1e-9


if __name__ == "__main__":  # pragma: no cover - standalone smoke
    import inspect
    import sys

    rc = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and inspect.isfunction(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as exc:  # noqa: PERF203
                rc = 1
                print(f"FAIL {name}: {exc}")
    sys.exit(rc)
