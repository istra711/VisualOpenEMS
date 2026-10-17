"""Smoke: GUI multi-excitation driver (Simulation → Run All Ports).

A fake runner writes one partial sparams.json per port; the driver must chain
the runs and merge them into the full S-matrix under <project>/sparams.
Standalone (offscreen Qt).
"""

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

import numpy as np  # noqa: E402


def _write_partial(path: Path, nports: int, excited: int) -> None:
    """Run with port `excited` measures column `excited`: S<=n><excited>."""
    freq = list(np.linspace(1e9, 3e9, 5))
    data = {"freq": freq}
    for row in range(1, nports + 1):
        arr = np.full(5, 0.1 * (row + excited) + 0.05j, dtype=complex)
        data[f"S{row}{excited}"] = {
            "real": arr.real.tolist(),
            "imag": arr.imag.tolist(),
            "db": (20 * np.log10(np.abs(arr))).tolist(),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class FakeRunner:
    """Immediately 'completes' each run and writes its partial sparams.json."""

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
        _write_partial(plan.work_dir / "sparams" / "sparams.json", nports, plan.port_number)
        self.last_exit = 0
        if log_cb:
            log_cb("fake run done")

    def abort(self) -> None:  # pragma: no cover - not exercised
        pass


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.models.project import PortDef
    from app.settings import AppSettings
    from app.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="multirun_smoke_"))

    settings = AppSettings.load()
    settings.work_dir = str(tmp)
    win = MainWindow(settings)
    win.project.name = "mrtest"
    win.primitives = [
        {
            "kind": "box",
            "name": "board",
            "material": "copper",
            "start": [0.0, 0.0, 0.0],
            "stop": [10.0, 5.0, 1.6],
        }
    ]
    win.project.ports = [
        PortDef(name="in1", type="lumped", start=[0, 0, 0], stop=[0, 0, 1], direction="z"),
        PortDef(name="out1", type="lumped", start=[5, 0, 0], stop=[5, 0, 1], direction="z"),
    ]
    win._refresh_ports()

    fake = FakeRunner(win)
    win.runner = fake

    # action exists in the Simulation menu
    texts = [a.text() for m in win.menuBar().actions() if m.text().replace("&", "") == "Simulation"
             for a in m.menu().actions()]
    assert any("Run All Ports" in t for t in texts), texts

    win._on_run_all_ports()
    # drive the run chain to completion
    guard = 0
    while win._multi_plans is not None and guard < 20:
        win._poll_multi_run()
        guard += 1
    assert win._multi_plans is None, "multi-run did not finish"
    assert len(fake.started) == 2, fake.started

    work = tmp / "mrtest"
    merged = work / "sparams" / "sparams.json"
    assert merged.exists(), f"merged sparams.json missing under {work}"
    table = json.loads(merged.read_text(encoding="utf-8"))
    for key in ("S11", "S21", "S12", "S22"):
        assert key in table, (key, list(table))
    assert (work / "sparams" / "sparams.s2p").exists(), "complete .s2p not written"

    win.close()
    print("MULTIRUN SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
