"""Smoke: per-mark repair actions in the Mesh Viewer (V1-V3), offscreen.

Checks the right-click menu enablement (a genuine `geometrie` cell must not be
repairable), the stored accept/remove rules and a full repair round-trip on a
synthetic grid - no easyMesh, no solver.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

# QApplication BEFORE CSXCAD: importing CSXCAD first crashes (0xC0000005).
_APP = QApplication.instance() or QApplication(sys.argv)

from PySide6.QtWidgets import QDialog, QMenu, QMessageBox  # noqa: E402


def main() -> int:
    try:
        from CSXCAD import ContinuousStructure

        from app.meshing.critical_cells import CriticalCell
        from app.models.project import Project
        from app.ui.mesh_viewer import MeshGeometryViewer
        from app.ui.repair_dialog import RepairDialog

        class _Ev:
            """Minimal matplotlib event: only xdata/ydata are read."""

            def __init__(self, x: float, y: float) -> None:
                self.xdata = x
                self.ydata = y

        # --- synthetic grid: one 10 um cell between a 30 um and a 460 um cell
        CSX = ContinuousStructure()
        grid = CSX.GetGrid()
        grid.SetDeltaUnit(1e-3)
        grid.SetLines("x", [0.0, 1.0, 2.0])
        grid.SetLines("y", [0.0, 1.0, 2.0])
        grid.SetLines("z", [0.0, 0.5, 0.53, 0.54, 1.0, 1.5])

        project = Project.new_default("repair-smoke")
        viewer = MeshGeometryViewer()
        viewer.set_project_context(project=project, primitives=[], ports=[], nf2ff=None)
        viewer.set_csx(CSX)
        viewer.set_critical_enabled(True)

        lo = (0.0, 0.0, 0.53)
        hi = (1.0, 1.0, 0.54)
        mark = CriticalCell(
            "timestep_cell", 2, lo, hi, min_cell=0.01, cause="artefakt", penalty=46.0
        )
        genuine = CriticalCell(
            "timestep_cell", 2, lo, hi, min_cell=0.01, cause="geometrie", penalty=46.0
        )

        # the XZ slice shows z as the vertical axis
        viewer.cmb_view.setCurrentText("XZ slice")
        event = _Ev(0.5 * (lo[0] + hi[0]), 0.5 * (lo[2] + hi[2]))

        # --- menu enablement --------------------------------------------------
        viewer.set_critical_cells([mark])
        menu = QMenu()  # keep alive: the actions belong to the menu
        acts = {v: k for k, v in viewer._critical_menu(menu, event).items()}
        assert set(acts) == {"repair", "remove", "snap", "accept", "explain"}, acts
        assert acts["repair"].isEnabled(), "an artefakt cell must be repairable"
        assert acts["accept"].isEnabled()
        assert not acts["snap"].isEnabled(), "a real 10 um cell is not a duplicate"
        print("  menu: artefakt -> repair enabled, snap disabled: OK")

        viewer.set_critical_cells([genuine])
        menu2 = QMenu()
        acts = {v: k for k, v in viewer._critical_menu(menu2, event).items()}
        assert not acts["repair"].isEnabled(), "geometrie must not be repairable"
        assert not acts["snap"].isEnabled()
        assert acts["explain"].isEnabled()
        print("  menu: geometrie -> repair disabled: OK")

        # --- accept ----------------------------------------------------------
        viewer.set_critical_cells([mark])
        viewer._critical_accept(mark)
        specs = list(getattr(project.mesh, "accepted_cells", None) or [])
        assert len(specs) == 1 and specs[0]["axis"] == "z", specs
        assert abs(float(specs[0]["lo"]) - lo[2]) < 1e-9
        assert mark.accepted is True
        print("  accept stores MeshSpec.accepted_cells: OK")

        # --- remove the offending (unprotected) line --------------------------
        mark.accepted = False
        rems = viewer._lines_dict(project.mesh, "manual_mesh_lines_removed")
        before = len(rems.get("z", []))
        with patch.object(
            QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
        ):
            viewer._critical_remove(mark)
        rems = viewer._lines_dict(project.mesh, "manual_mesh_lines_removed")
        assert len(rems.get("z", [])) == before + 1, rems
        assert any(abs(v - 0.53) < 1e-9 for v in rems["z"]), rems
        print("  remove stores a Mesh Editor removal: OK")

        # --- repair: preview dialog accepted, rule stored, grid changed -------
        grid.SetLines("z", [0.0, 0.5, 0.53, 0.54, 1.0, 1.5])
        viewer._apply_editor_change()
        mark2 = CriticalCell(
            "timestep_cell", 2, lo, hi, min_cell=0.01, cause="artefakt", penalty=46.0
        )
        viewer.set_critical_cells([mark2])
        rules_before = len(getattr(project.mesh, "mesh_repairs", None) or [])
        with patch.object(RepairDialog, "exec", return_value=QDialog.DialogCode.Accepted):
            viewer._critical_repair(mark2)
        rules = list(getattr(project.mesh, "mesh_repairs", None) or [])
        assert len(rules) == rules_before + 1, rules
        rule = rules[-1]
        assert rule["axis"] == "z", rule
        assert rule["lo"] <= lo[2] + 1e-9 and rule["hi"] >= hi[2] - 1e-9, rule
        z_after = sorted(float(v) for v in CSX.GetGrid().GetLines(2))
        assert len(z_after) == 5, z_after  # one line merged away
        cells_after = np.diff(np.asarray(z_after, float))
        assert not any(abs(c - 0.01) < 1e-9 for c in cells_after), cells_after
        assert float(cells_after.min()) >= 0.03 * 0.999, cells_after
        assert mark2 not in viewer._critical
        print("  repair stores MeshSpec.mesh_repairs + re-spaces the grid: OK")

        # --- explain does not crash ------------------------------------------
        with patch.object(QMessageBox, "exec", return_value=0):
            viewer._critical_explain(mark2)
        print("  explain opens without a crash: OK")

        print("REPAIR ACTIONS SMOKE PASS")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
