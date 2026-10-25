"""Critical-cell marks for the Mesh Viewer (app.meshing.critical_cells)."""

from __future__ import annotations

import numpy as np
import pytest

from app.meshing.critical_cells import (
    CriticalCell,
    find_critical_cells,
    parse_engine_cell,
    source_summary,
    summary_line,
)

LINES = {
    "x": np.array([0.0, 1.0, 2.0, 2.0000001, 3.0, 30.0]),
    "y": np.array([0.0, 1.0, 2.0]),
    "z": np.array([0.0, 1.0]),
}
ENGINE_LOG = (
    "Operator::CalcTimestep_Var3: Smallest timestep (2.29369e-13s) "
    "found at position: 1 : 1;0;0"
)


class _Targets:
    max_cell_units = 3.0


def test_tiny_cell_is_flagged_and_spans_the_other_axes() -> None:
    cells = find_critical_cells(LINES, _Targets())
    assert [c.kind for c in cells] == ["very_small_cell"]
    tiny = cells[0]
    assert tiny.axis == 0
    assert tiny.lo[0] == pytest.approx(2.0)
    assert tiny.hi[0] == pytest.approx(2.0000001)
    # a cell of one axis spans the whole domain in the other two
    assert (tiny.lo[1], tiny.hi[1]) == pytest.approx((0.0, 2.0))
    assert (tiny.lo[2], tiny.hi[2]) == pytest.approx((0.0, 1.0))
    assert tiny.cell_size == pytest.approx(1e-7)


def test_grading_jumps_and_large_cells_are_not_marked() -> None:
    # LINES has a 1:9 jump (1 -> 27 mm) and a 27 mm cell above max_cell: both
    # are quality findings of the mesh report, not timestep problems.
    kinds = [c.kind for c in find_critical_cells(LINES, _Targets())]
    assert "grading_jump" not in kinds
    assert "cell_too_large" not in kinds


def test_consecutive_cells_are_merged_into_one_region() -> None:
    lines = {
        "x": np.array([0.0, 0.3, 0.6, 0.9, 5.0]),
        "y": np.array([0.0, 1.0]),
        "z": np.array([0.0, 1.0]),
    }

    class _T:
        max_cell_units = 100.0  # tiny bound = 0.2 -> 0.3 mm is not "very small"

    cells = find_critical_cells(lines, _T(), min_cellsize=1.0)
    assert [c.kind for c in cells] == ["cell_below_min"]
    assert cells[0].lo[0] == pytest.approx(0.0)
    assert cells[0].hi[0] == pytest.approx(0.9)
    assert "3 cells" in cells[0].detail
    assert cells[0].cell_size == pytest.approx(0.3)
    assert "0.3" in cells[0].label


def test_only_cells_near_the_smallest_one_are_marked() -> None:
    # c_min = 0.5 mm. The 2 mm cell is 4x larger — a grading jump the report
    # warns about, but it does not cost timestep, so it is not marked.
    lines = {
        "x": np.array([0.0, 0.5, 1.0, 1.5, 3.5]),
        "y": np.array([0.0, 1.0]),
        "z": np.array([0.0, 1.0]),
    }

    class _T:
        max_cell_units = 100.0

    cells = find_critical_cells(lines, _T())
    assert [c.kind for c in cells] == ["timestep_cell"]
    assert cells[0].lo[0] == pytest.approx(0.0)
    assert cells[0].hi[0] == pytest.approx(1.5)
    assert "3 cells" in cells[0].detail


def test_marks_are_capped() -> None:
    xs = [0.0]
    for _ in range(30):
        xs.extend([xs[-1] + 0.1, xs[-1] + 1.1])
    lines = {
        "x": np.array(xs),
        "y": np.array([0.0, 1.0]),
        "z": np.array([0.0, 1.0]),
    }

    class _T:
        max_cell_units = 10.0  # tiny bound = 0.02 -> 0.1 mm cells are not tiny

    cells = find_critical_cells(lines, _T(), max_marks=5)
    assert len(cells) == 5
    assert all(c.kind == "timestep_cell" for c in cells)


def test_no_lines_no_marks() -> None:
    assert find_critical_cells({}, _Targets()) == []
    assert find_critical_cells({"x": np.array([1.0])}, _Targets()) == []


def test_parse_engine_cell_uses_the_grid() -> None:
    cell = parse_engine_cell(ENGINE_LOG, LINES)
    assert cell is not None
    assert cell.kind == "engine_timestep"
    assert cell.lo[0] == pytest.approx(1.0) and cell.hi[0] == pytest.approx(2.0)
    assert cell.lo[1] == pytest.approx(0.0) and cell.hi[1] == pytest.approx(1.0)
    assert cell.lo[2] == pytest.approx(0.0) and cell.hi[2] == pytest.approx(1.0)
    assert "0.2294" in cell.detail  # dt in ps


def test_parse_engine_cell_clamps_and_rejects_garbage() -> None:
    clamped = parse_engine_cell(ENGINE_LOG.replace("1;0;0", "999;999;999"), LINES)
    assert clamped is not None
    assert clamped.hi[0] == pytest.approx(30.0)  # last cell
    assert parse_engine_cell("no timestep here", LINES) is None
    assert parse_engine_cell(ENGINE_LOG, {}) is None


def test_summary_line_and_label() -> None:
    cells = find_critical_cells(LINES, _Targets())
    line = summary_line(cells)
    assert line.startswith("Critical cells:")
    assert "very_small_cell" in line
    assert summary_line([]) == "Critical cells: none"
    cell = CriticalCell("very_small_cell", 0, (0.0, 0.0, 0.0), (1e-7, 1.0, 1.0))
    assert "very_small_cell" in cell.label and "x=" in cell.label
    assert cell.cell_size == pytest.approx(1e-7)


# --- P2: which source forced the cell? -------------------------------------- #

P2_LINES = {
    "x": np.array([0.0, 5.0, 5.000001, 10.0]),
    "y": np.array([0.0, 1.0]),
    "z": np.array([0.0, 1.0]),
}


def test_cells_are_attributed_to_both_faces() -> None:
    sources = {"x": [(5.0, "box:feed"), (5.000001, "manual")]}
    cells = find_critical_cells(P2_LINES, _Targets(), sources=sources)
    tiny = next(c for c in cells if c.kind == "very_small_cell")
    assert tiny.source == "box:feed + manual"
    assert "box:feed" in tiny.label


def test_attribution_can_be_switched_off() -> None:
    cells = find_critical_cells(P2_LINES, _Targets(), sources=None)
    assert cells and all(c.source == "" for c in cells)


def test_cells_without_a_source_stay_unattributed() -> None:
    sources = {"x": [(999.0, "box:far")]}
    cells = find_critical_cells(P2_LINES, _Targets(), sources=sources)
    tiny = next(c for c in cells if c.kind == "very_small_cell")
    assert tiny.source == ""
    text = "\n".join(source_summary(cells))
    assert "easyMesh (no authored source)" in text


def test_source_summary_lists_counts() -> None:
    sources = {"x": [(5.0, "box:feed"), (5.000001, "manual")]}
    cells = find_critical_cells(P2_LINES, _Targets(), sources=sources)
    text = "\n".join(source_summary(cells))
    assert "Fine-cell sources" in text
    assert "box:feed + manual" in text
    assert source_summary([]) == []
