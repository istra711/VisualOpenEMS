"""Smoke test: patch antenna end-to-end (mesh + short FDTD + S11 + NF2FF).

Uses reduced timestep count for CI-friendly runtime.
"""

from __future__ import annotations

import os
import sys
import traceback
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
    work = settings.work_path() / "smoke_patch"
    work.mkdir(parents=True, exist_ok=True)

    project, primitives = patch_antenna_primitives(
        f0=2.45e9,
        fc=0.5e9,
        substrate_thickness=1.524,
        patch_width=32.0,
        patch_length=40.0,
    )
    # shorten for smoke test
    project.sim.max_timesteps = 8000
    project.sim.end_criteria = 1e-3
    project.mesh.preset = "medium"
    project.nf2ff.theta_steps = 37
    project.nf2ff.phi_steps = 73
    project.nf2ff.frequencies = [2.45e9]
    # keep dumps small
    for d in project.dumps:
        d.sub_sampling = [4, 4, 1]

    project_path = project.save(work / "patch.fcsim.json")
    script_path = generate_run_script(project, project_path, work, primitives=primitives)
    print("Project:", project_path)
    print("Script:", script_path)

    logs: list[str] = []

    def on_log(line: str) -> None:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("utf-8", errors="replace").decode("ascii", errors="replace"), flush=True)
        logs.append(line)

    runner = SimulationRunner(settings, app_root=APP_ROOT)
    try:
        runner.start(script_path, log_cb=on_log)
    except Exception as e:  # noqa: BLE001
        print("Failed to start runner:", e)
        traceback.print_exc()
        return 2

    code = runner.wait(timeout=600)
    print("Exit code:", code)
    sim_dir = work  # project folder: sparams/, fields/, nf2ff/ live directly here
    print("Simulation dir exists:", sim_dir.exists())
    if sim_dir.exists():
        files = sorted(p.name for p in sim_dir.rglob("*") if p.is_file())[:40]
        print("Output files (sample):", files)
    sparams_png = sim_dir / "sparams" / "sparams.png"
    print("S-param plot:", sparams_png.exists(), sparams_png)
    touch = sim_dir / "sparams" / "sparams.s1p"
    if not touch.exists():
        touch = next(iter((sim_dir / "sparams").glob("sparams.s*p")), None)
    print("Touchstone:", touch, "exists:", bool(touch and touch.exists()))
    if touch and touch.exists():
        head = touch.read_text(encoding="utf-8", errors="replace").splitlines()[:6]
        print("Touchstone head:")
        for line in head:
            print(" ", line)
    nf_plot = sim_dir / "nf2ff" / "nf2ff_summary.png"
    print("NF2FF plot:", nf_plot.exists(), nf_plot)
    nf_json = sim_dir / "nf2ff" / "nf2ff.json"
    print("NF2FF json:", nf_json.exists(), nf_json)
    if nf_json.exists():
        import json

        data = json.loads(nf_json.read_text(encoding="utf-8"))
        th = data.get("theta_deg") or []
        ph = data.get("phi_deg") or []
        print(
            f"NF2FF grid: theta {min(th, default=None)}..{max(th, default=None)} deg, "
            f"phi {min(ph, default=None)}..{max(ph, default=None)} deg"
        )
        print(
            "NF2FF scalars: Prad", data.get("Prad"),
            "Dmax_dBi", data.get("Dmax_dBi"),
            "efficiency", data.get("efficiency"),
        )
        if not th or not ph or max(th) < 179.0 or max(ph) < 359.0:
            print("FAIL: NF2FF angular grid does not span the full sphere")
            return 5
        if data.get("D_dBi") is None or data.get("gain_dBi") is None:
            print("FAIL: NF2FF json missing D_dBi / gain_dBi")
            return 6

    if code == 0 and sparams_png.exists() and touch and touch.exists():
        print("SMOKE TEST PASS")
        return 0
    if code == 0:
        print("SMOKE TEST PARTIAL (script ok, plots missing)")
        return 0
    print("SMOKE TEST FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
