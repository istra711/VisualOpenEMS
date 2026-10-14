"""Patch antenna example project (mm units).

Run headless:
  python examples/run_patch_example.py
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.csxcad_builder.primitives import patch_antenna_primitives
from app.settings import AppSettings
from app.simulation.script_gen import generate_run_script


def main() -> int:
    project, primitives = patch_antenna_primitives()
    work = AppSettings.load().work_path() / "example_patch"
    work.mkdir(parents=True, exist_ok=True)
    project_path = project.save(work / "patch.fcsim.json")
    script = generate_run_script(project, project_path, work, primitives=primitives)
    print("Project:", project_path)
    print("Script:", script)
    print("Run with the project Simulation Python:")
    print(f"  {AppSettings.load().simulation_python} {script}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
