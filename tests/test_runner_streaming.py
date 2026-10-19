"""The runner must stream the child's output (unbuffered), incl. post-processing."""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.settings import AppSettings  # noqa: E402
from app.simulation.runner import SimulationRunner  # noqa: E402

SCRIPT = """
import time
print("phase 1")
time.sleep(0.4)
print("phase 2")
time.sleep(0.4)
print("DONE")
"""


def test_runner_streams_before_exit():
    tmp = Path(tempfile.mkdtemp(prefix="runner_stream_"))
    script = tmp / "s.py"
    script.write_text(SCRIPT, encoding="utf-8")

    settings = AppSettings()
    settings.simulation_python = sys.executable
    runner = SimulationRunner(settings, app_root=ROOT)

    seen: list[str] = []
    start = time.monotonic()
    first_line_at: list[float] = []

    def on_log(line: str) -> None:
        seen.append(line)
        if not first_line_at:
            first_line_at.append(time.monotonic() - start)

    runner.start(script, log_cb=on_log)
    code = runner.wait(timeout=60)
    total = time.monotonic() - start

    assert code == 0, code
    assert "phase 1" in seen and "phase 2" in seen and "DONE" in seen, seen
    # the first line must arrive well before the script finishes (i.e. streamed,
    # not flushed as one block at exit)
    assert first_line_at and first_line_at[0] < total - 0.2, (first_line_at, total)
