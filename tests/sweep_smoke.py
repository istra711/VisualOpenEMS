"""Smoke: Phase-A variable sweep (tree, dialog, driver, results bundle)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402
import numpy as np  # noqa: E402


def _write_partial(path: Path, nports: int, excited: int, value: float) -> None:
    freq = list(np.linspace(1e9, 3e9, 5))
    data = {"freq": freq}
    for row in range(1, nports + 1):
        arr = np.full(5, value * (row + excited) / 10.0 + 0.02j, dtype=complex)
        data[f"S{row}{excited}"] = {
            "real": arr.real.tolist(),
            "imag": arr.imag.tolist(),
            "db": (20 * np.log10(np.abs(arr))).tolist(),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class FakeRunner:
    def __init__(self, win):
        self.win = win
        self.last_exit = None
        self.log_path = None
        self.started: list[str] = []

    def is_running(self) -> bool:
        return False

    def start(self, script_path, log_cb=None) -> None:
        plan = self.win._multi_plans[self.win._multi_pos]
        self.started.append(str(script_path))
        nports = len(self.win.project.ports)
        value = 1.0
        # The sweep point value is visible in the plan label; use the point's
        # project snapshot for a deterministic value.
        point = self.win._sweep_points[plan.point_index]
        value = float(point.values.get("L", 1.0))
        if plan.port_number is not None:
            _write_partial(
                plan.work_dir / "sparams" / "sparams.json",
                nports,
                int(plan.port_number),
                value,
            )
        else:
            _write_partial(plan.work_dir / "sparams" / "sparams.json", nports, 1, value)
        self.last_exit = 0
        if log_cb:
            log_cb("fake sweep run done")

    def abort(self) -> None:  # pragma: no cover
        pass


def main() -> int:
    from app.models.project import PortDef, Project, VariableDef
    from app.models.sweep import SweepSpec
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow
    from app.ui.sweep_dialog import SweepDialog
    from app.ui.sweep_results import SweepResultsDialog

    app = QApplication.instance() or QApplication([])
    tmp = Path(tempfile.mkdtemp(prefix="sweep_smoke_"))

    settings = AppSettings.load()
    settings.work_dir = str(tmp)
    win = MainWindow(settings)
    win.project.name = "sweeptest"
    win._current_file_path = tmp / "sweeptest.fcsim.json"
    win.project.variables = [VariableDef(name="L", value=10.0)]
    win.primitives = [
        {
            "kind": "box",
            "name": "body",
            "material": "copper",
            "start": [0.0, 0.0, 0.0],
            "stop": [10.0, 5.0, 1.0],
            "expressions": {"stop.0": "L"},
        }
    ]
    win.project.primitives = list(win.primitives)
    win.project.ports = [
        PortDef(name="p1", start=[0, 0, 0], stop=[0, 0, 1], direction="z"),
        PortDef(name="p2", start=[5, 0, 0], stop=[5, 0, 1], direction="z"),
    ]
    win._refresh_ports()
    win.project.sweep = SweepSpec(
        enabled=True,
        variable="L",
        values=[10.0, 12.0],
        full_smatrix=True,
    )
    win._refresh_tree()

    # The tree has the project-level Sweep entry, not a geometry primitive.
    labels = [win.tree.item(i).text() for i in range(win.tree.count())]
    if not any(text.startswith("Sweep:") for text in labels):
        print("FAIL: Sweep tree entry missing:", labels)
        return 1
    if not any("Sweep: on" in text for text in labels):
        print("FAIL: Sweep tree entry does not show its state:", labels)
        return 1

    # Dialog produces the explicit value list used by the run.
    dlg = SweepDialog(
        win.project,
        win.primitives,
        usage=win._variable_usage_counts(),
        parent=win,
    )
    dlg.cmb_variable.setCurrentText("L")
    dlg.rad_range.setChecked(True)
    dlg.ed_start.setText("10")
    dlg.ed_stop.setText("14")
    dlg.ed_points.setText("3")
    dlg._refresh()
    spec = dlg.spec()
    if spec.values != [10.0, 12.0, 14.0]:
        print("FAIL: sweep dialog values:", spec.values)
        return 2
    dlg.close()

    phase_dlg = SweepDialog(
        win.project,
        win.primitives,
        usage=win._variable_usage_counts(),
        parent=win,
    )
    phase_dlg.cmb_target_kind.setCurrentIndex(
        phase_dlg.cmb_target_kind.findData("port_phase")
    )
    phase_dlg.cmb_port.setCurrentText("p1")
    phase_dlg.ed_start.setText("0")
    phase_dlg.ed_stop.setText("90")
    phase_dlg.ed_points.setText("3")
    phase_dlg._refresh()
    phase_dlg.show()
    app.processEvents()
    if phase_dlg.width() > 520:
        print("FAIL: sweep dialog starts too wide:", phase_dlg.width())
        return 2
    if phase_dlg.txt_warning.geometry().bottom() > phase_dlg.bb.geometry().top():
        print("FAIL: sweep warning box overlaps the button row")
        return 2
    phase_spec = phase_dlg.spec()
    if phase_spec.target_kind != "port_phase" or phase_spec.target != "p1":
        print("FAIL: phase target not stored:", phase_spec)
        return 2
    if not phase_spec.collect_nf2ff or phase_spec.full_smatrix:
        print("FAIL: phase target must force NF2FF and disable full S-matrix")
        return 2
    phase_dlg.close()

    fake = FakeRunner(win)
    win.runner = fake
    win._on_run_sweep()
    guard = 0
    while win._multi_plans is not None and guard < 30:
        win._poll_multi_run()
        guard += 1
    if win._multi_plans is not None:
        print("FAIL: sweep driver did not finish")
        return 3

    staging = win._staged_run_dir
    if staging is None:
        print("FAIL: sweep did not keep a staged run")
        return 4
    sweep_json = staging / "sweep.json"
    if not sweep_json.is_file():
        print("FAIL: sweep.json missing")
        return 4
    payload = json.loads(sweep_json.read_text(encoding="utf-8"))
    if payload.get("status") != "complete":
        print("FAIL: sweep status:", payload.get("status"))
        return 4
    if len(payload.get("points") or []) != 2:
        print("FAIL: sweep point count:", payload.get("points"))
        return 4
    for point in payload["points"]:
        if point.get("status") != "complete":
            print("FAIL: point status:", point)
            return 4
        if not (staging / point["sparams"]).is_file():
            print("FAIL: point S-params missing:", point.get("sparams"))
            return 4

    # Results dialog loads the bundle and can draw the overlay.
    res = SweepResultsDialog(sweep_json, viewer=win.sparam_viewer, parent=win)
    res._redraw()
    if res.cmb_trace.count() == 0:
        print("FAIL: sweep results dialog has no traces")
        return 5
    res.close()

    # Commit keeps the sweep pointer in the active project results.
    run = win._commit_staged_run()
    if run is None or (run / "sweep.json").is_file() is False:
        print("FAIL: committed sweep run has no sweep.json")
        return 6
    if not (win.project.results or {}).get("sweep"):
        print("FAIL: sweep pointer missing from project results")
        return 6
    if not (win.project.results or {}).get("sparams"):
        print("FAIL: primary point S-params missing from project results")
        return 6

    win.close()
    print("SWEEP SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
