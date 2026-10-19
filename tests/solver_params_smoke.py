"""Smoke: Solver Parameters dialog + wiring into the generated run script."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.models.project import Project
    from app.ui.solver_params import SolverParamsDialog

    app = QApplication.instance() or QApplication(sys.argv)

    p = Project.new_default("solver_smoke")
    p.sim.engine = "sse"
    p.sim.verbose = 1
    p.sim.setup_only = True
    p.sim.disable_dumps = True

    dlg = SolverParamsDialog(None, p)
    # loads current values
    assert dlg.cmb_engine.currentText() == "sse", dlg.cmb_engine.currentText()
    assert dlg.spn_verbose.value() == 1
    assert dlg._flags["setup_only"].isChecked()
    assert dlg._flags["disable_dumps"].isChecked()
    assert not dlg._flags["debug_material"].isChecked()
    # the dialog has a Help button (opens the "Solver Parameters" help topic)
    from PySide6.QtWidgets import QPushButton

    assert any(b.text() == "Help" for b in dlg.findChildren(QPushButton)), "no Help button"
    from app.ui.tab_help import TAB_HELP, TAB_ORDER

    assert "Solver Parameters" in TAB_ORDER and "Solver Parameters" in TAB_HELP

    # change + accept
    dlg.cmb_engine.setCurrentText("multithreaded")
    dlg.spn_verbose.setValue(0)
    dlg._flags["debug_material"].setChecked(True)
    dlg._flags["setup_only"].setChecked(False)
    dlg.spn_over.setValue(4)
    dlg.spn_tsf.setValue(0.5)
    dlg.cmb_tsm.setCurrentText("1 (CFL)")
    dlg.spn_max_time.setValue(12.5)  # ns
    dlg._accept()

    assert p.sim.engine == "multithreaded", p.sim.engine
    assert p.sim.verbose == 0
    assert p.sim.debug_material is True
    assert p.sim.setup_only is False
    assert p.sim.over_sampling == 4
    assert abs(p.sim.time_step_factor - 0.5) < 1e-9
    assert p.sim.time_step_method == 1
    assert abs(p.sim.max_time - 12.5e-9) < 1e-15
    print("  dialog <-> SimSpec OK")

    # JSON round trip keeps the new fields
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "s.fcsim.json"
        p.save(path)
        q = Project.load(path)
    assert q.sim.engine == "multithreaded" and q.sim.over_sampling == 4
    assert abs(q.sim.max_time - 12.5e-9) < 1e-15
    print("  project JSON round trip OK")

    # the generated run script wires them in
    from app.simulation.script_gen import generate_run_script

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        proj_path = work / "s.fcsim.json"
        p.save(proj_path)
        script = generate_run_script(p, proj_path, work, primitives=[])
        txt = Path(script).read_text(encoding="utf-8")
    for needle in ("OverSampling", "TimeStepFactor", "TimeStepMethod", "SetMaxTime",
                   "run_kw[\"engine\"]", "setup_only", "Solver options:"):
        assert needle in txt, f"run script misses {needle!r}"
    print("  generated run script contains the solver options")

    print("SOLVER PARAMS SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
