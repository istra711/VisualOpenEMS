"""PCB band-pass example: 5 open lambda/2 stubs at 5.6 GHz on Teflon.

A 5-pole Chebyshev band-pass (0.1 dB ripple, ~30 % bandwidth, |S11| <= -20 dB
from 4.85 to 6.35 GHz) built from five open-circuit lambda/2 shunt stubs and
four lambda/4 connecting lines on PTFE.  Open stubs need no vias, the board
stays a single-layer planar circuit.

The geometry comes from `app.csxcad_builder.primitives.pcb_bandpass_stub_primitives`
(substrate 0.254 mm PTFE, eps_r 2.1, tan_d 0.0002, 35 um copper).

  1. project + primitives for both excitations (in1 -> S11/S21, out1 -> S12/S22)
  2. project JSON + project_run.py per excitation under work/pcb_bandpass/
  3. both FDTD runs merge into work/pcb_bandpass/simulation/sparams.s2p (+ .json)

Run headless:
  python examples/run_pcb_bandpass_example.py --skip-run    # project + scripts
  python examples/run_pcb_bandpass_example.py --mesh-only   # geometry + mesh check
  python examples/run_pcb_bandpass_example.py               # run + merge
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.csxcad_builder.primitives import (  # noqa: E402
    PCB_BANDPASS_DESIGN,
    msl_eps_eff,
    msl_open_end,
    msl_width,
    pcb_bandpass_stub_primitives,
)
from app.postproc import sparams as sp  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.simulation.script_gen import generate_run_script  # noqa: E402

F0 = 5.6e9
EPS_R = 2.1
H = 0.254


def design_table() -> None:
    """Print the microstrip layout derived from the synthesis table."""
    from openEMS.physical_constants import C0

    print("PCB band-pass design (PTFE 0.254 mm, eps_r 2.1, tan_d 0.0002)")
    print(f"  f0 = {F0 / 1e9:.3f} GHz, 5-pole Chebyshev, 0.1 dB ripple")
    print(f"  50 ohm feed: {msl_width(50.0, EPS_R, H):.2f} mm wide")
    for name, zs, ks, half in (
        ("stub", PCB_BANDPASS_DESIGN["stub_z"], PCB_BANDPASS_DESIGN["stub_k"], True),
        ("line", PCB_BANDPASS_DESIGN["line_z"], PCB_BANDPASS_DESIGN["line_k"], False),
    ):
        for i, (z, k) in enumerate(zip(zs, ks), start=1):
            ee = msl_eps_eff(z, EPS_R, H)
            lam = C0 / (F0 * (ee ** 0.5)) * 1e3
            ln = k * lam / (2.0 if half else 4.0)
            if half:
                ln -= msl_open_end(z, EPS_R, H)
            print(
                f"  {name} {i}: Z={z:6.2f} ohm  w={msl_width(z, EPS_R, H):5.2f} mm  "
                f"l={ln:5.2f} mm  eps_eff={ee:.3f}"
            )


def build_project(excite_first: bool):
    project, prims = pcb_bandpass_stub_primitives(
        f0=F0, substrate_eps=EPS_R, substrate_thickness=H
    )
    project.ports[0].excite_port = bool(excite_first)
    project.ports[1].excite_port = not bool(excite_first)
    project.primitives = list(prims)
    return project, prims


def build_run(work: Path, project, primitives) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    project_path = project.save(work / "pcb_bandpass.fcsim.json")
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
        band = (freqs >= 4.85e9) & (freqs <= 6.35e9)
        worst = db[band].max() if key in ("S11", "S22") else db[band].min()
        print(
            f"  {key}: at 5.6 GHz {db[np.argmin(np.abs(freqs - F0))]:6.2f} dB | "
            f"in band (4.85-6.35 GHz) worst {worst:6.2f} dB"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="PCB stub band-pass @5.6 GHz on PTFE")
    parser.add_argument("--skip-run", action="store_true", help="project + scripts only")
    parser.add_argument("--mesh-only", action="store_true", help="stop after mesh build")
    args = parser.parse_args()

    design_table()

    settings = AppSettings.load()
    work = settings.work_path() / "pcb_bandpass"
    work_port2 = work / "port2"

    project1, prims1 = build_project(excite_first=True)
    project2, prims2 = build_project(excite_first=False)
    script1 = build_run(work, project1, prims1)
    script2 = build_run(work_port2, project2, prims2)
    print("Project JSON:", work / "pcb_bandpass.fcsim.json")

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

    sim1 = work / "simulation"
    sim2 = work_port2 / "simulation"
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
