"""Longer production-style patch antenna run (headless)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.csxcad_builder.primitives import patch_antenna_primitives
from app.settings import AppSettings
from app.simulation.runner import SimulationRunner
from app.simulation.script_gen import generate_run_script


def main() -> int:
    settings = AppSettings.load()
    # production-ish: finer mesh, long ring-down; geometry from tuned defaults
    project, primitives = patch_antenna_primitives(
        f0=2.45e9,
        fc=0.5e9,
        # defaults: patch 30.2 x 37.8 mm, feed_pos=-5.7 (scaled for ~2.45 GHz)
    )
    project.name = "patch_prod"
    project.mesh.preset = "high"
    project.mesh.cells_per_wavelength = 20.0
    project.mesh.max_grading_ratio = 1.6
    # Patch cavity stores energy; need long ring-down for clean S11 (tutorial uses 1e5 TS)
    project.sim.end_criteria = 1e-5
    project.sim.max_timesteps = 150_000
    project.nf2ff.frequencies = [2.45e9]
    project.nf2ff.theta_steps = 91
    project.nf2ff.phi_steps = 181

    work = settings.work_path() / "patch_prod"
    work.mkdir(parents=True, exist_ok=True)
    project_path = project.save(work / "patch_prod.fcsim.json")
    script = generate_run_script(project, project_path, work, primitives=primitives)
    print("Project:", project_path)
    print("Script:", script)
    print("Mesh preset:", project.mesh.preset, "cells/lambda:", project.mesh.cells_per_wavelength)
    print("Sim:", project.sim.max_timesteps, "TS, end_criteria", project.sim.end_criteria)

    logs: list[str] = []

    def on_log(line: str) -> None:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("utf-8", errors="replace").decode("ascii", errors="replace"), flush=True)
        logs.append(line)

    runner = SimulationRunner(settings, app_root=APP_ROOT)
    t0 = time.time()
    runner.start(script, log_cb=on_log)
    code = runner.wait(timeout=3600)
    dt = time.time() - t0
    ok = code == 0
    print(f"RUN_OK={ok} exit_code={code} elapsed_s={dt:.1f}")
    sim_dir = work  # project folder: results live directly here (sparams/, nf2ff/, fields/)
    print("Simulation dir:", sim_dir, "exists:", sim_dir.exists())
    if sim_dir.exists():
        for p in sorted(sim_dir.rglob("*")):
            if p.is_file():
                print(" ", p.relative_to(sim_dir), p.stat().st_size)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
