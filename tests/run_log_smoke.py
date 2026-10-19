"""Smoke: run log has START banner, elapsed step lines and an END banner.

Run: python -u tests/run_log_smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from PySide6.QtWidgets import QApplication

from app.settings import AppSettings
from app.ui.main_window import MainWindow


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(AppSettings.load())

    win._begin_run_log("Test run")
    win._run_step("phase A")
    win._end_run_log()
    win._drain_log_queue()

    lines = win.log_view.toPlainText().splitlines()
    start = [l for l in lines if l.startswith("########### START (")]
    assert start, lines[-8:]
    assert start[-1].endswith("#############"), start[-1]
    assert "Run: Test run" in lines

    steps = [l for l in lines if "Step: phase A" in l]
    assert steps, lines[-8:]
    assert steps[-1].startswith("--- [+") and " s]" in steps[-1], steps[-1]
    assert steps[-1].endswith("] Step: phase A ---"), steps[-1]
    assert lines[lines.index(steps[-1]) - 1] == "", "blank line before the step"

    end = [l for l in lines if l.startswith("################ END (")]
    assert end, lines[-8:]
    assert "duration" in end[-1] and end[-1].endswith("###############"), end[-1]
    print("[run-log] START:", start[-1])
    print("[run-log] STEP :", steps[-1])
    print("[run-log] END  :", end[-1])

    # outside a session the END banner is not repeated
    before = len([l for l in lines if l.startswith("################ END (")])
    win._end_run_log()
    win._drain_log_queue()
    after = len(
        [l for l in win.log_view.toPlainText().splitlines() if l.startswith("################ END (")]
    )
    assert after == before, "END banner must not repeat without an active session"

    # a step outside a session still logs, just without the delta
    win._run_step("standalone")
    win._drain_log_queue()
    assert "--- Step: standalone ---" in win.log_view.toPlainText().splitlines()
    print("[run-log] no duplicate END, standalone step OK")

    win.close()
    print("RUN LOG SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
