"""PCB hairpin band-pass example: 5 folded resonators at 2.4 GHz on RT/duroid.

A compact 5-pole Chebyshev band-pass (0.1 dB ripple, ~10 % bandwidth,
|S11| well below -10 dB over 2.28-2.52 GHz) built from **five hairpin
resonators** — half-wave resonators folded into a U, edge-coupled over
lambda_g/4 — on RT/duroid 6010 (eps_r 10.2, tan_d 0.0023, 1.27 mm).  Single
layer, no vias, and only ~25 mm long because every resonator is folded.

The geometry comes from `app.csxcad_builder.hairpin.pcb_hairpin_primitives`
(coupled-line synthesis with the Hammerstad-Jensen even/odd mode model).

  1. project + primitives for both excitations (in1 -> S11/S21, out1 -> S12/S22)
  2. project JSON + project_run.py per excitation under work/pcb_hairpin/
  3. both FDTD runs merge into work/pcb_hairpin/sparams/sparams.s2p (+ .json)

Run headless:
  python examples/run_pcb_hairpin_example.py --design      # layout table only
  python examples/run_pcb_hairpin_example.py --skip-run    # project + scripts
  python examples/run_pcb_hairpin_example.py --mesh-only   # geometry + mesh check
  python examples/run_pcb_hairpin_example.py               # run + merge
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.csxcad_builder.hairpin import (  # noqa: E402
    HAIRPIN_BANDPASS_DESIGN,
    coupled_ms_modes,
    hairpin_bandpass_design,
    msl_u_for_z0,
    pcb_hairpin_primitives,
)
from app.postproc import sparams as sp  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.simulation.script_gen import generate_run_script  # noqa: E402

F0 = 2.4e9
F_LO = 2.28e9
F_HI = 2.52e9


def design_table() -> None:
    """Print the coupled-line layout derived from the synthesis."""
    d = hairpin_bandpass_design()
    u = msl_u_for_z0(50.0, d["eps_r"])
    print(
        f"Hairpin band-pass design (RT/duroid 6010, eps_r {d['eps_r']}, "
        f"{d['h']} mm, 35 um copper)"
    )
    print(
        f"  f0 = {d['f0'] / 1e9:.3f} GHz, 5-pole Chebyshev 0.1 dB, "
        f"FBW {d['fbw'] * 100:.0f} %, lambda_g = {d['lam_g']:.2f} mm"
    )
    print(
        f"  50 ohm line: {d['w50']:.3f} mm wide (eps_eff {d['eps_eff']:.3f}),  "
        f"resonator arm {d['arm']:.2f} mm, inner gap {d['inner_gap']:.2f} mm"
    )
    for i, (z_e, z_o, s) in enumerate(zip(d["z0e"], d["z0o"], d["gaps"]), start=1):
        e, o, _ee, _eo = coupled_ms_modes(u, s / d["h"], d["eps_r"])
        k_req = (z_e - z_o) / (z_e + z_o)
        k_get = (e - o) / (e + o)
        print(
            f"  section {i}: Z0e={z_e:6.2f} Z0o={z_o:6.2f} ohm -> "
            f"gap {s:5.3f} mm (w={d['w50']:.3f})  k {k_req:.3f} -> {k_get:.3f}"
        )
    print(f"  layout span: {d['span']:.2f} mm (0.20 lambda_0)")


def build_project(excite_first: bool):
    project, prims = pcb_hairpin_primitives()
    project.ports[0].excite_port = bool(excite_first)
    project.ports[1].excite_port = not bool(excite_first)
    project.primitives = list(prims)
    return project, prims


def build_run(work: Path, project, primitives) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    project_path = project.save(work / "pcb_hairpin.fcsim.json")
    script = generate_run_script(project, project_path, work, primitives=primitives)
    excited = [p.name for p in project.ports if p.excite_port]
    print(f"Run ({'/'.join(excited) or 'none'} excited): {script}")
    return script


def run_script(python: Path, script: Path, log: Path, extra: list[str]) -> int:
    # openEMS refuses to run into an existing output directory
    sim_dir = script.parent / "simulation"
    if sim_dir.exists():
        import shutil

        shutil.rmtree(sim_dir, ignore_errors=True)
    cmd = [str(python), str(script), *extra]
    print("Running:", " ".join(cmd))
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(
            cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(script.parent), check=False
        )
    print(f"  exit code {proc.returncode}, log: {log}")
    return proc.returncode


def _sparams_json(work: Path) -> Path:
    """Locate sparams.json (newer runs write work/sparams/, older work/simulation/)."""
    for candidate in (
        work / "sparams" / "sparams.json",
        work / "simulation" / "sparams.json",
        work / "sparams.json",
    ):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"sparams.json not found under {work}")


def summarise(table: dict) -> None:
    import numpy as np

    freqs = np.asarray(table.get("freq", []), dtype=float)
    if freqs.size == 0:
        print("SUMMARY: no frequency data")
        return
    print("S-parameter summary (dB):")
    for key in ("S11", "S21", "S12", "S22"):
        arr = np.asarray(table.get(key, []), dtype=complex)
        if arr.size != freqs.size or not np.any(np.isfinite(arr)):
            print(f"  {key}: not available")
            continue
        db = 20.0 * np.log10(np.maximum(np.abs(arr), 1e-12))
        band = (freqs >= F_LO) & (freqs <= F_HI)
        worst = db[band].max() if key in ("S11", "S22") else db[band].min()
        print(
            f"  {key}: at 2.4 GHz {db[np.argmin(np.abs(freqs - F0))]:6.2f} dB | "
            f"in band ({F_LO / 1e9:.2f}-{F_HI / 1e9:.2f} GHz) worst {worst:6.2f} dB"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="PCB hairpin band-pass @2.4 GHz, RT/duroid 6010")
    parser.add_argument("--design", action="store_true", help="print the layout table only")
    parser.add_argument("--skip-run", action="store_true", help="project + scripts only")
    parser.add_argument("--mesh-only", action="store_true", help="stop after mesh build")
    args = parser.parse_args()

    design_table()
    if args.design:
        return 0

    settings = AppSettings.load()
    work = settings.work_path() / "pcb_hairpin"
    work_port2 = work / "port2"

    project1, prims1 = build_project(excite_first=True)
    project2, prims2 = build_project(excite_first=False)
    script1 = build_run(work, project1, prims1)
    script2 = build_run(work_port2, project2, prims2)
    print("Project JSON:", work / "pcb_hairpin.fcsim.json")
    print(
        "Material: RT/duroid 6010LM, eps_r "
        f"{HAIRPIN_BANDPASS_DESIGN['eps_r']}, tan_d {HAIRPIN_BANDPASS_DESIGN['tand']}, "
        f"{HAIRPIN_BANDPASS_DESIGN['h']} mm, 35 um copper"
    )

    if args.skip_run:
        print("SKIP-RUN: scripts generated only")
        print(f"Run manually: {settings.simulation_python} {script1}")
        return 0

    extra = ["--mesh-only"] if args.mesh_only else []
    python = Path(settings.simulation_python)
    rc1 = run_script(python, script1, work / "run1.log", extra)
    rc2 = run_script(python, script2, work_port2 / "run2.log", extra)
    if rc1 or rc2:
        print(f"ERROR: simulation failed (run1={rc1}, run2={rc2})")
        return 1
    if args.mesh_only:
        print("MESH ONLY OK (both runs)")
        return 0

    try:
        table1 = sp.load_sparams_json(_sparams_json(work))
        table2 = sp.load_sparams_json(_sparams_json(work_port2))
        merged = sp.merge_sparam_tables(table1, table2)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: merge failed: {e}")
        return 2

    written = sp.export_sparams(merged, work / "sparams", basename="sparams")
    summarise(merged)
    print("Merged outputs:", {k: str(v) for k, v in written.items()})
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
