"""Half-wave dipole + FreeCAD PORT marks (2.45 GHz) — model + GUI export path.

Headless:
  python examples/run_dipole_ports_example.py

Generates:
  work/dipole_ports/dipole_2p45.FCStd / .step / stl / openems_ports.json
  work/dipole_ports/freecad_export/openems_export.json  (via FreeCAD export helper)

Then verify ports are extracted from the FCStd (no GUI required).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

FREECAD_CMD_DEFAULT = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")
MODEL_SCRIPT = APP_ROOT / "examples" / "freecad_dipole_ports.py"
OUT_DIR = APP_ROOT / "work" / "dipole_ports"
FCSTD = OUT_DIR / "dipole_2p45.FCStd"


def run_freecad_script(script: Path, out_dir: Path, freecad_cmd: Path | None = None) -> dict:
    cmd = freecad_cmd or FREECAD_CMD_DEFAULT
    out_dir.mkdir(parents=True, exist_ok=True)
    local = out_dir / script.name
    local.write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    env = os.environ.copy()
    env["DIPOLE_OUT_DIR"] = str(out_dir)
    code = (
        "import os, runpy\n"
        f"os.environ['DIPOLE_OUT_DIR'] = r'{out_dir}'\n"
        f"runpy.run_path(r'{local}', run_name='__main__')\n"
    )
    if not Path(cmd).exists():
        return {"ok": False, "log": f"FreeCADCmd not found: {cmd}", "out_dir": out_dir}
    proc = subprocess.run(
        [str(cmd), "-c", code],
        cwd=str(out_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = (proc.stdout or "") + (proc.stderr or "")
    return {
        "ok": proc.returncode == 0,
        "log": log,
        "out_dir": out_dir,
        "fcstd": out_dir / "dipole_2p45.FCStd",
    }


def export_via_gui_helper(fcstd: Path, out_dir: Path, freecad_cmd: Path | None = None) -> dict:
    """Run examples/freecad_export_for_openems.py on the dipole FCStd."""
    cmd = freecad_cmd or FREECAD_CMD_DEFAULT
    exp_out = out_dir / "freecad_export"
    exp_out.mkdir(parents=True, exist_ok=True)
    script = APP_ROOT / "examples" / "freecad_export_for_openems.py"
    env = os.environ.copy()
    env["FREECAD_SOURCE"] = str(fcstd)
    env["FREECAD_OUT_DIR"] = str(exp_out)
    env["FREECAD_DEFLECTION"] = "0.1"
    code = (
        "import os, runpy\n"
        f"os.environ['FREECAD_SOURCE'] = r'{fcstd}'\n"
        f"os.environ['FREECAD_OUT_DIR'] = r'{exp_out}'\n"
        f"runpy.run_path(r'{script}', run_name='__main__')\n"
    )
    if not Path(cmd).exists():
        return {"ok": False, "log": f"FreeCADCmd not found: {cmd}", "out_dir": exp_out}
    proc = subprocess.run(
        [str(cmd), "-c", code],
        cwd=str(exp_out),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return {
        "ok": proc.returncode == 0,
        "log": (proc.stdout or "") + (proc.stderr or ""),
        "out_dir": exp_out,
    }


def verify_ports(fcstd: Path) -> list[dict]:
    """Extract ports via FreeCADCmd + freecad_extract_ports_code (GUI-side helper)."""
    from app.freecad_io.port_marks import freecad_extract_ports_code, parse_ports_payload

    helper = freecad_extract_ports_code()
    code = (
        "import json, runpy\n"
        f"ns = {{}}\n"
        "exec(" + repr(helper) + ", ns)\n"
        "import FreeCAD as App\n"
        f"doc = App.open(r'{fcstd}')\n"
        "ports = ns['extract_openems_ports'](doc)\n"
        "print('PORTS_JSON=' + json.dumps(ports))\n"
    )
    cmd = FREECAD_CMD_DEFAULT
    if not Path(cmd).exists():
        raise RuntimeError(f"FreeCADCmd not found: {cmd}")
    proc = subprocess.run(
        [str(cmd), "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    line = next((ln for ln in out.splitlines() if ln.startswith("PORTS_JSON=")), "")
    if not line:
        raise RuntimeError(f"port extract failed rc={proc.returncode}\n{out}\n{err}")
    payload = json_loads(line.split("=", 1)[1])
    return parse_ports_payload(payload)


def json_loads(s: str):
    import json

    return json.loads(s)


def main() -> int:
    print("=== Dipole FreeCAD ports example ===")
    print("model:", MODEL_SCRIPT)
    r1 = run_freecad_script(MODEL_SCRIPT, OUT_DIR)
    print(r1["log"])
    if not r1["ok"]:
        print("FAIL: FreeCAD model generation")
        return 1
    if not FCSTD.exists():
        print(f"FAIL: missing {FCSTD}")
        return 1
    print("FCStd:", FCSTD)

    r2 = export_via_gui_helper(FCSTD, OUT_DIR)
    print(r2["log"][-1500:] if r2["log"] else "")
    print("export ok=", r2["ok"], "out=", r2["out_dir"])

    ports = verify_ports(FCSTD)
    print("extracted ports:", ports)
    if not ports:
        print("FAIL: no ports extracted from FCStd")
        return 1
    names = {str(p.get("name")) for p in ports}
    if "in1" not in names:
        print(f"FAIL: expected port in1, got {names}")
        return 1
    p0 = next(p for p in ports if p.get("name") == "in1")
    assert abs(float(p0["start"][0]) - (-1.0)) < 0.2, p0
    assert abs(float(p0["stop"][0]) - (1.0)) < 0.2, p0

    # Manifest path (STEP import without FreeCAD)
    from app.freecad_io.port_marks import find_manifest_ports

    manifest = r2["out_dir"] / "openems_export.json"
    if manifest.exists():
        mports = find_manifest_ports(str(manifest))
        print("manifest ports:", mports)
        assert any(p.get("name") == "in1" for p in mports), mports
    else:
        # model writes its own manifest next to FCStd
        mports = find_manifest_ports(str(OUT_DIR / "openems_export.json"))
        print("sidecar ports:", mports)
        assert any(p.get("name") == "in1" for p in mports), mports

    print("DIPOLE PORTS EXAMPLE OK")
    print("GUI: Import Geometry ->", FCSTD)
    print("  or FreeCAD... -> Export CAD... -> source:", FCSTD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
