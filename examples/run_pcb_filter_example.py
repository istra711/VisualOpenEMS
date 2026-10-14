"""PCB microstrip notch filter example (2-port, FR4) — FreeCAD geometry, 2 runs, merge.

Geometry is built in FreeCAD (examples/freecad_pcb_filter_model.py), exported to
per-object STL meshes and imported as simulation primitives — no analytic boxes:

  1. FreeCADCmd builds work/pcb_filter/pcb_filter.FCStd (named solids + port marks)
  2. export_cad_for_openems -> STEP + one STL per solid + openems_export.json (ports)
  3. load_stl_meshes -> one stl primitive per solid (materials/priorities from the
     pcb_filter_primitives preset skeleton)
  4. one FDTD run per excitation:
       in1  excited -> S11, S21
       out1 excited -> S12, S22
  5. both results merge into work/pcb_filter/simulation/sparams.s2p (+ .json)

Run headless:
  python examples/run_pcb_filter_example.py --skip-run    # model + export + scripts
  python examples/run_pcb_filter_example.py --mesh-only   # geometry + mesh check
  python examples/run_pcb_filter_example.py               # generate + run + merge
  python examples/run_pcb_filter_example.py --analytic    # legacy analytic boxes
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.csxcad_builder.primitives import pcb_filter_primitives  # noqa: E402
from app.postproc import sparams as sp  # noqa: E402
from app.settings import AppSettings  # noqa: E402
from app.simulation.script_gen import generate_run_script  # noqa: E402

FREECAD_CMD_DEFAULT = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")
MODEL_SCRIPT = APP_ROOT / "examples" / "freecad_pcb_filter_model.py"

# FreeCAD solid name -> (role, material, priority) — same values the analytic
# preset uses, so the imported meshes behave exactly like the boxes.
ASSIGNMENTS_BY_NAME: dict[str, tuple[str, str, int]] = {
    "ground": ("pec", "copper", 90),
    "substrate": ("dielectric", "fr4", 10),
    "msl_line": ("pec", "copper", 100),
    "msl_stub": ("pec", "copper", 100),
}
# Mesh-line snapping for the planar metals (analytic preset: edges2grid="xy")
EDGES2GRID_BY_NAME = {"ground", "msl_line", "msl_stub"}


# --------------------------------------------------------------------- FreeCAD
def generate_freecad_model(out_dir: Path, freecad_cmd: Path | None = None) -> dict:
    """Write the model script next to the outputs and run FreeCADCmd on it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    local_script = out_dir / "make_pcb_filter_model.py"
    local_script.write_text(MODEL_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")

    cmd = Path(freecad_cmd) if freecad_cmd else FREECAD_CMD_DEFAULT
    env = os.environ.copy()
    env["PCB_FILTER_OUT_DIR"] = str(out_dir)
    result = {
        "script": str(local_script),
        "freecad_cmd": str(cmd),
        "out_dir": str(out_dir),
        "fcstd": str(out_dir / "pcb_filter.FCStd"),
        "step": str(out_dir / "pcb_filter.step"),
        "params": str(out_dir / "pcb_filter_params.json"),
        "ok": False,
        "log": "",
    }
    if not cmd.exists():
        result["log"] = f"FreeCADCmd not found: {cmd} — model script written, not executed."
        print(result["log"])
        return result

    def _run(code: str) -> subprocess.CompletedProcess:
        # FreeCADCmd does not exec .py as __main__; use -c + runpy, exec fallback
        return subprocess.run(
            [str(cmd), "-c", code],
            cwd=str(out_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    print(f"Running FreeCADCmd: {cmd} -c <pcb filter model>")
    code = (
        "import os, runpy\n"
        f"os.environ['PCB_FILTER_OUT_DIR'] = r'{out_dir}'\n"
        f"runpy.run_path(r'{local_script}', run_name='__main__')\n"
    )
    proc = _run(code)
    result["log"] = (proc.stdout or "") + (proc.stderr or "")
    print(result["log"])
    result["ok"] = proc.returncode == 0 and Path(result["fcstd"]).exists()
    if not result["ok"]:
        code2 = (
            f"import os\nos.environ['PCB_FILTER_OUT_DIR'] = r'{out_dir}'\n"
            f"exec(compile(open(r'{local_script}', encoding='utf-8').read(), "
            f"'make_pcb_filter_model.py', 'exec'))\n"
        )
        proc = _run(code2)
        result["log"] += "\n" + (proc.stdout or "") + (proc.stderr or "")
        print(result["log"])
        result["ok"] = Path(result["fcstd"]).exists()
    return result


def export_freecad_meshes(
    fcstd: Path, out_dir: Path, freecad_cmd: Path | None = None, deflection: float = 0.1
) -> tuple[Path | None, str]:
    """FCStd -> STEP + one STL per solid + openems_export.json (with ports)."""
    from app.freecad_io.freecad_launcher import export_cad_for_openems

    ok, log, out = export_cad_for_openems(
        fcstd, out_dir, freecad_cmd=freecad_cmd, deflection=deflection
    )
    if not ok:
        print(log[-2000:] if log else "export failed")
        return None, log
    return out / "openems_export.json", log


def build_project_from_manifest(manifest: Path, excite_first: bool = True):
    """Project skeleton (mesh/sim/materials/ports) + STL mesh primitives."""
    from app.freecad_io.port_marks import find_manifest_ports, ports_to_port_defs
    from app.freecad_io.stl_import import load_stl_meshes, stl_primitives_from_meshes
    from app.models.project import GeomAssignment, GeometryFile

    project, _ = pcb_filter_primitives()

    mesh_dir = manifest.parent.parent / "meshes"
    res = load_stl_meshes(manifest, out_dir=mesh_dir)
    for w in res.warnings:
        print("WARNING:", w)
    if not res.ok:
        return None, []

    prims: list[dict] = []
    assignments: list[GeomAssignment] = []
    for m in res.objects:
        role, material, priority = ASSIGNMENTS_BY_NAME.get(
            m.name, ("pec", "copper", 50)
        )
        a = GeomAssignment(
            object_name=m.name,
            role=role,
            material=material,
            priority=priority,
            notes=f"imported FreeCAD solid from {manifest.name}",
        )
        if a.role == "ignore":
            continue
        mesh_prims = stl_primitives_from_meshes(
            [m],
            material=material,
            priority=priority,
            source_file=str(manifest),
            source_format="json",
        )
        for p in mesh_prims:
            if m.name in EDGES2GRID_BY_NAME:
                p["edges2grid"] = "xy"
        prims.extend(mesh_prims)
        assignments.append(a)

    # Ports from the FreeCAD export (doc JSON carries direction/excite flags)
    ports = ports_to_port_defs(find_manifest_ports(manifest))
    ports.sort(key=lambda p: (p.name != "in1", p.name))
    if len(ports) >= 2:
        ports = ports[:2]
        project.ports = ports
    else:
        print(
            "WARNING: manifest ports incomplete "
            f"({len(ports)}) - keeping the preset ports"
        )
    project.ports[0].excite_port = bool(excite_first)
    project.ports[1].excite_port = not bool(excite_first)

    project.name = "pcb_filter"
    project.primitives = list(prims)
    project.assignments = assignments
    project.geometry_files = [
        GeometryFile(
            path=str(manifest),
            format="json",
            unit="mm",
            mesh_path=prims[0]["path"] if prims else "",
            object_names=[m.name for m in res.objects],
        )
    ]
    project.notes = (
        (project.notes + "\n" if project.notes else "")
        + f"geometry from FreeCAD: {manifest}"
    )
    return project, prims


def prepare_freecad_project(
    work: Path,
    freecad_cmd: Path | None = None,
    excite_first: bool = True,
    force: bool = False,
):
    """Model (if needed) + export (if needed) + project. Returns (proj, prims, info).

    Reuses pcb_filter.FCStd / openems_export.json when they are up to date, so
    the GUI preset and repeated example runs stay fast.
    """
    info: dict = {"ok": False, "log": ""}
    work.mkdir(parents=True, exist_ok=True)
    fcstd = work / "pcb_filter.FCStd"
    if force or not fcstd.exists():
        model = generate_freecad_model(work, freecad_cmd=freecad_cmd)
        info.update(model)
        if not model["ok"]:
            info["error"] = "FreeCADCmd model failed"
            return None, None, info
    else:
        info.update({"ok": True, "fcstd": str(fcstd), "reused_model": True})
        print(f"Reusing FreeCAD model: {fcstd}")

    export_dir = work / "freecad_export"
    manifest = export_dir / "openems_export.json"
    stale = (
        force
        or not manifest.exists()
        or not Path(info["fcstd"]).exists()
        or Path(info["fcstd"]).stat().st_mtime > manifest.stat().st_mtime
    )
    if stale:
        m, log = export_freecad_meshes(
            Path(info["fcstd"]), export_dir, freecad_cmd=freecad_cmd
        )
        info["log"] += "\n" + log
        if m is None:
            info["ok"] = False
            info["error"] = "STL export failed"
            return None, None, info
    else:
        print(f"Reusing FreeCAD export: {manifest}")

    project, prims = build_project_from_manifest(manifest, excite_first=excite_first)
    if project is None:
        info["ok"] = False
        info["error"] = "mesh import produced no objects"
        return None, None, info
    info.update(
        {
            "ok": True,
            "manifest": str(manifest),
            "objects": [p.get("name") for p in prims],
            "ports": [(p.name, p.start, p.stop) for p in project.ports],
        }
    )
    return project, prims, info


# ------------------------------------------------------------------- runs/IO
def build_run(work: Path, project, primitives) -> Path:
    """Write project + project_run.py for one excitation into `work`."""
    work.mkdir(parents=True, exist_ok=True)
    project_path = project.save(work / "pcb_filter.fcsim.json")
    script = generate_run_script(project, project_path, work, primitives=primitives)
    excited = [p.name for p in project.ports if p.excite_port]
    print(f"Run ({'/'.join(excited) or 'none'} excited): {script}")
    return script


def run_script(python: Path, script: Path, log: Path, extra: list[str]) -> int:
    cmd = [str(python), str(script), *extra]
    print("Running:", " ".join(cmd))
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(
            cmd,
            stdout=fh,
            stderr=subprocess.STDOUT,
            cwd=str(script.parent),
            check=False,
        )
    print(f"  exit code {proc.returncode}, log: {log}")
    return proc.returncode


def _db(val: complex) -> float:
    import numpy as np

    mag = abs(val)
    return float(20.0 * np.log10(max(mag, 1e-12)))


def summarise(table: dict) -> None:
    import numpy as np

    freqs = np.asarray(table.get("freq", []), dtype=float)
    if freqs.size == 0:
        print("SUMMARY: no frequency data")
        return
    print("S-parameter summary (dB):")
    for key, label in (
        ("S11", "reflection in  "),
        ("S22", "reflection out "),
        ("S21", "transmission   "),
        ("S12", "reverse transm."),
    ):
        arr = np.asarray(table.get(key, []), dtype=complex)
        if arr.size != freqs.size or not np.any(np.isfinite(arr)):
            print(f"  {key} {label}: not available")
            continue
        db = 20.0 * np.log10(np.maximum(np.abs(arr), 1e-12))
        i_min = int(np.nanargmin(db))
        print(
            f"  {key} {label}: min {db[i_min]:6.2f} dB @ {freqs[i_min] / 1e9:.3f} GHz"
            f" | at {freqs[0] / 1e9:.3f} GHz: {db[0]:6.2f} dB"
        )
    s21 = np.asarray(table.get("S21", []), dtype=complex)
    s12 = np.asarray(table.get("S12", []), dtype=complex)
    if s21.size == freqs.size and s12.size == freqs.size:
        dev = float(np.nanmax(np.abs(np.abs(s21) - np.abs(s12))))
        print(f"  reciprocity |S21| vs |S12|: max deviation {dev:.4f}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PCB notch filter example (FreeCAD geometry, 2 ports, 2 runs)"
    )
    parser.add_argument("--skip-run", action="store_true", help="generate scripts only")
    parser.add_argument("--mesh-only", action="store_true", help="stop after the mesh build")
    parser.add_argument(
        "--analytic",
        action="store_true",
        help="use the analytic box primitives instead of FreeCAD meshes",
    )
    parser.add_argument(
        "--rebuild", action="store_true", help="regenerate the FreeCAD model + export"
    )
    args = parser.parse_args()

    settings = AppSettings.load()
    work = settings.work_path() / "pcb_filter"
    work_port2 = work / "port2"

    if args.analytic:
        print("ANALYTIC geometry (no FreeCAD) - legacy comparison path")
        project1, prims1 = pcb_filter_primitives()
        project1.ports[0].excite_port = True
        project1.ports[1].excite_port = False
        project2, prims2 = pcb_filter_primitives()
        project2.ports[0].excite_port = False
        project2.ports[1].excite_port = True
    else:
        project1, prims1, info = prepare_freecad_project(
            work, freecad_cmd=Path(settings.freecad_cmd), excite_first=True,
            force=args.rebuild,
        )
        if project1 is None:
            print(f"FAIL: FreeCAD PCB model not available ({info.get('error')})")
            if info.get("log"):
                print(info["log"][-2000:])
            print("Hint: set FreeCADCmd in Settings, or run with --analytic")
            return 1
        print("FreeCAD solids imported:", ", ".join(info.get("objects") or []))
        project2, prims2 = build_project_from_manifest(
            Path(info["manifest"]), excite_first=False
        )
        if project2 is None:
            print("FAIL: could not rebuild the project for run 2")
            return 1

    script1 = build_run(work, project1, prims1)
    script2 = build_run(work_port2, project2, prims2)

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
        table1 = sp.load_sparams_json(sim1 / "sparams.json")
        table2 = sp.load_sparams_json(sim2 / "sparams.json")
        merged = sp.merge_sparam_tables(table1, table2)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: merge failed: {e}")
        return 2

    written = sp.export_sparams(merged, sim1, basename="sparams")
    summarise(merged)
    print("Merged outputs:", {k: str(v) for k, v in written.items()})
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
