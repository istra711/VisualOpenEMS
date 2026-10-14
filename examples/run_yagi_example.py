"""Yagi-Uda antenna example (2.45 GHz) — FreeCAD model + openEMS project.

Headless:
  python examples/run_yagi_example.py

Generates:
  work/yagi_antenna/yagi_2p45.FCStd / .step / .stl / yagi_params.json  (via FreeCADCmd)
  work/yagi_antenna/yagi.fcsim.json
  work/yagi_antenna/project_run.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.csxcad_builder.primitives import yagi_antenna_primitives
from app.settings import AppSettings
from app.simulation.script_gen import generate_run_script

FREECAD_CMD_DEFAULT = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")
YAGI_SCRIPT = APP_ROOT / "examples" / "freecad_yagi_model_param.py"


def generate_freecad_model(out_dir: Path, freecad_cmd: Path | None = None) -> dict:
    """Write FreeCAD sources + run FreeCADCmd; return paths dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    # Copy generator script next to outputs for reproducibility
    local_script = out_dir / "make_yagi_model.py"
    local_script.write_text(YAGI_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")

    cmd = freecad_cmd or FREECAD_CMD_DEFAULT
    env = os.environ.copy()
    env["YAGI_OUT_DIR"] = str(out_dir)
    result = {
        "script": str(local_script),
        "freecad_cmd": str(cmd),
        "out_dir": str(out_dir),
        "fcstd": str(out_dir / "yagi_2p45_param.FCStd"),
        "step": str(out_dir / "yagi_2p45_param.step"),
        "stl": str(out_dir / "yagi_2p45_param.stl"),
        "params": str(out_dir / "yagi_params.json"),
        "ok": False,
        "log": "",
    }
    if not Path(cmd).exists():
        result["log"] = f"FreeCADCmd not found: {cmd} — model script written, not executed."
        print(result["log"])
        return result
    # FreeCADCmd does not exec .py as __main__; use -c + exec(open(...))
    code = (
        "import os, runpy\n"
        f"os.environ['YAGI_OUT_DIR'] = r'{out_dir}'\n"
        f"runpy.run_path(r'{local_script}', run_name='__main__')\n"
    )
    print(f"Running FreeCADCmd: {cmd} -c <yagi model>")
    proc = subprocess.run(
        [str(cmd), "-c", code],
        cwd=str(out_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    result["log"] = (proc.stdout or "") + (proc.stderr or "")
    print(result["log"])
    result["ok"] = proc.returncode == 0 and Path(result["step"]).exists()
    if not result["ok"]:
        # fallback: exec the file contents directly (no __file__)
        local_script.write_text(
            YAGI_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
        )
        code2 = (
            f"import os\nos.environ['YAGI_OUT_DIR'] = r'{out_dir}'\n"
            f"exec(compile(open(r'{local_script}', encoding='utf-8').read(), "
            f"'make_yagi_model.py', 'exec'))\n"
        )
        proc = subprocess.run(
            [str(cmd), "-c", code2],
            cwd=str(out_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        result["log"] += "\n" + (proc.stdout or "") + (proc.stderr or "")
        print(result["log"])
        result["ok"] = Path(result["step"]).exists() or Path(result["fcstd"]).exists()
    return result


def main() -> int:
    project, primitives = yagi_antenna_primitives()
    work = AppSettings.load().work_path() / "yagi_antenna"
    work.mkdir(parents=True, exist_ok=True)

    model_info = generate_freecad_model(work)
    if model_info["ok"] and Path(model_info["step"]).exists():
        from app.models.project import GeometryFile

        # Keep FreeCAD/STEP available for import; primitives remain the sim source.
        step = Path(model_info["step"])
        project.geometry_files.append(
            GeometryFile(
                path=str(step),
                format="step",
                unit="mm",
                object_names=[
                    "boom",
                    "reflector",
                    "driven_left",
                    "driven_right",
                    "director_1",
                    "director_2",
                    "director_3",
                ],
            )
        )

    project_path = project.save(work / "yagi.fcsim.json")
    script = generate_run_script(project, project_path, work, primitives=primitives)

    print("Yagi project:", project_path)
    print("Simulation script:", script)
    print("FreeCAD model dir:", work)
    for k in ("fcstd", "step", "stl", "params"):
        print(f"  {k}: {model_info[k]}")
    print("Run simulation with:")
    print(f"  {AppSettings.load().simulation_python} {script}")
    print("Or open the GUI -> Preset: Yagi Antenna... / Import STEP")
    return 0 if model_info["ok"] or model_info["freecad_cmd"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
