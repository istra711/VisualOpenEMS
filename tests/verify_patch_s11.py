"""Quick verification: patch S11 after ground/air-margin/NF2FF fixes."""

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
    project, primitives = patch_antenna_primitives()
    project.name = "patch_verify"
    # shorter than full 200k for a smoke check; enough for a resonance dip
    project.sim.max_timesteps = 80_000
    project.sim.end_criteria = 1e-5
    project.nf2ff.frequencies = [2.45e9]

    work = settings.work_path() / "patch_verify"
    work.mkdir(parents=True, exist_ok=True)
    project_path = project.save(work / "project.fcsim.json")
    script = generate_run_script(project, project_path, work, primitives=primitives)
    print("Script:", script)
    print("ground prims:", [p for p in primitives if p["name"] == "ground"])
    print("nf2ff:", project.nf2ff.start, "->", project.nf2ff.stop)
    print("mesh min_cellsize:", project.mesh.min_cellsize)

    logs: list[str] = []

    def on_log(line: str) -> None:
        print(line, flush=True)
        logs.append(line)

    runner = SimulationRunner(settings, app_root=APP_ROOT)
    t0 = time.time()
    runner.start(script, log_cb=on_log)
    code = runner.wait(timeout=1800)
    dt = time.time() - t0
    print(f"exit={code} elapsed={dt:.1f}s")

    # Analyze log + S11
    text = "\n".join(logs)
    for needle in [
        "PEC edges",
        "in z direction",
        "Energy:",
        "end-criteria",
        "outside the field domain",
        "Mesh checks",
        "uf_inc",
        "Wrote Touchstone",
        "NF2FF summary",
        "Prad",
    ]:
        for line in logs:
            if needle.lower() in line.lower():
                print("LOG>", line[:200])

    import numpy as np
    import skrf as rf

    touch = work / "sparams" / "sparams.s1p"
    if not touch.exists():
        print("FAIL: no touchstone")
        return 2
    n = rf.Network(str(touch))
    s = n.s[:, 0, 0]
    db = 20 * np.log10(np.maximum(np.abs(s), 1e-15))
    i = int(np.nanargmin(db))
    print(f"S11 min {db[i]:.2f} dB @ {n.f[i]/1e9:.3f} GHz")
    print(f"S11@2.45 {db[np.argmin(np.abs(n.f - 2.45e9))]:.2f} dB")
    print(f"S11@2.47 {db[np.argmin(np.abs(n.f - 2.47e9))]:.2f} dB")

    # Energy must have built (not stuck at -75 dB during run)
    energy_lines = [ln for ln in logs if "Energy:" in ln]
    if energy_lines:
        print("last energy line:", energy_lines[-1][:160])

    ok_s11 = db[i] < -8.0  # solid resonance dip
    ok_target = abs(n.f[i] / 1e9 - 2.45) < 0.15  # near 2.45 GHz
    ok_nf2ff = "outside the field domain" not in text
    ok_mesh = "[ERROR]" not in text and "mesh validation failed" not in text
    print(f"CHECK s11_dip={ok_s11} near_245={ok_target} nf2ff_inside={ok_nf2ff} mesh_ok={ok_mesh}")
    if ok_s11 and ok_target and ok_nf2ff and ok_mesh:
        print("VERIFY PASS")
        return 0
    print("VERIFY FAIL")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
