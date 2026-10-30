"""Straight WR62 rectangular waveguide, 100 mm long, TE10 at 10 GHz.

WR62 cross-section (broad wall a x narrow wall b):
    a = 15.80 mm, b = 7.90 mm
    f_c(TE10) = c / (2a) = 9.49 GHz
    lambda_guide at 10 GHz ~ 95 mm

The model is an air-filled guide with four 2 mm PEC walls. Port planes are
100 mm apart (x = -50 ... +50 mm), the walls extend 20 mm beyond each port into
the PML so the excitation/measurement planes are not at the domain edge.

  1. build project + primitives (analytic boxes, no FreeCAD needed)
  2. write work/wr62_waveguide/project.fcsim.json + project_run.py
  3. optional FDTD run -> work/wr62_waveguide/simulation/sparams.{json,s2p,png}
  4. optional E-field frequency dump at 10 GHz (open in the Field Viewer)

Run headless:
  python examples/run_wr62_waveguide_example.py --skip-run    # project + script
  python examples/run_wr62_waveguide_example.py --mesh-only   # geometry + mesh
  python examples/run_wr62_waveguide_example.py               # full run + summary
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.models.project import Project  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.simulation.script_gen import generate_run_script  # noqa: E402

# WR62 dimensions and design point
WG_A = 15.7988  # mm, broad wall (y for a guide along x)
WG_B = 7.8994   # mm, narrow wall (z)
LENGTH = 100.0  # mm between the two port planes
WALL = 2.0      # mm PEC wall thickness
EXTRA = 20.0    # mm wall beyond each port (towards the PML)
F0 = 10.0e9     # Hz
F_START = 9.5e9
F_STOP = 11.5e9


def design_table() -> None:
    c0 = 299_792_458.0
    fc = c0 / (2.0 * WG_A * 1e-3)
    lam0 = c0 / F0
    lamg = lam0 / ((1.0 - (fc / F0) ** 2) ** 0.5)
    print("WR62 straight waveguide")
    print(f"  cross-section: {WG_A:.4f} x {WG_B:.4f} mm")
    print(f"  length (port to port): {LENGTH:.1f} mm")
    print(f"  f_c(TE10) = {fc / 1e9:.3f} GHz, f0 = {F0 / 1e9:.3f} GHz")
    print(f"  lambda0 = {lam0 * 1e3:.2f} mm, lambda_g = {lamg * 1e3:.2f} mm")


def build_project(excite: str = "in") -> tuple[Project, list[dict]]:
    """Delegate to the GUI preset builder (same E/H/J dumps)."""
    from app.csxcad_builder.primitives import wr62_waveguide_primitives

    return wr62_waveguide_primitives(f0=F0, length=LENGTH, excite=excite)


def load_table(work: Path):
    from app.postproc import sparams as sp

    candidates = (
        work / "sparams" / "sparams.json",
        work / "simulation" / "sparams.json",
        work / "sparams.json",
    )
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return None
    return sp.load_sparams_json(path)


def summarise(table: dict | None) -> None:
    import numpy as np

    if not table:
        print("SUMMARY: sparams.json not found")
        return
    freqs = np.asarray(table.get("freq", []), dtype=float)
    if freqs.size == 0:
        print("SUMMARY: no frequency data")
        return
    i0 = int(np.argmin(np.abs(freqs - F0)))
    print("S-parameter summary (dB):")
    for key in ("S11", "S21", "S12", "S22"):
        arr = np.asarray(table.get(key, []), dtype=complex)
        if arr.size != freqs.size or not np.any(np.isfinite(arr)):
            print(f"  {key}: not available")
            continue
        db = 20.0 * np.log10(np.maximum(np.abs(arr), 1e-12))
        band = (freqs >= F_START) & (freqs <= F_STOP)
        if key in ("S11", "S22"):
            print(f"  {key}: at 10 GHz {db[i0]:6.2f} dB | worst in band {db[band].max():6.2f} dB")
        else:
            print(f"  {key}: at 10 GHz {db[i0]:6.2f} dB | min in band {db[band].min():6.2f} dB")


def run_script(python: Path, script: Path, work: Path, mesh_only: bool) -> int:
    sim_dir = work / "simulation"
    if sim_dir.exists():
        shutil.rmtree(sim_dir, ignore_errors=True)
    cmd = [str(python), str(script)]
    if mesh_only:
        cmd.append("--mesh-only")
    print("Running:", " ".join(cmd))
    log = work / ("mesh.log" if mesh_only else "run.log")
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        rc = subprocess.run(
            cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(work), check=False
        ).returncode
    print(f"  exit code {rc}, log: {log}")
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Straight WR62 waveguide, 100 mm, TE10 at 10 GHz"
    )
    parser.add_argument("--skip-run", action="store_true", help="project + run script only")
    parser.add_argument("--mesh-only", action="store_true", help="stop after mesh build")
    args = parser.parse_args()

    design_table()
    settings = AppSettings.load()
    work = settings.work_path() / "wr62_waveguide"
    work_port2 = work / "port_out"
    work.mkdir(parents=True, exist_ok=True)
    work_port2.mkdir(parents=True, exist_ok=True)

    project1, prims1 = build_project("in")
    project_path1 = project1.save(work / "wr62_waveguide.fcsim.json")
    script1 = generate_run_script(project1, project_path1, work, primitives=prims1)
    project2, prims2 = build_project("out")
    project_path2 = project2.save(work_port2 / "wr62_waveguide.fcsim.json")
    script2 = generate_run_script(project2, project_path2, work_port2, primitives=prims2)
    print("Project:", project_path1)
    print("Run scripts:", script1, "|", script2)

    if args.skip_run:
        print("SKIP-RUN: projects and scripts generated only")
        return 0

    python = Path(settings.simulation_python)
    rc1 = run_script(python, script1, work, args.mesh_only)
    rc2 = run_script(python, script2, work_port2, args.mesh_only)
    if rc1 or rc2:
        print(f"ERROR: simulation failed (in={rc1}, out={rc2})")
        return 1
    if args.mesh_only:
        print("MESH ONLY OK (both runs)")
        return 0

    table1 = load_table(work)
    table2 = load_table(work_port2)
    if table1 is None or table2 is None:
        print("ERROR: sparams.json missing after the runs")
        return 2
    from app.postproc import sparams as sp

    merged = sp.merge_sparam_tables(table1, table2)
    written = sp.export_sparams(merged, work / "sparams", basename="sparams")
    print("Merged outputs:", {k: str(v) for k, v in written.items()})
    summarise(merged)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
