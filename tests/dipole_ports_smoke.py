"""Smoke: FreeCAD dipole model + PORT marks → extractable ports (no GUI)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FREECAD_CMD = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")
SCRIPT = ROOT / "examples" / "freecad_dipole_ports.py"
OUT = ROOT / "work" / "dipole_ports_smoke"
FCSTD = OUT / "dipole_2p45.FCStd"


def main() -> int:
    if not FREECAD_CMD.exists():
        print(f"SKIP: FreeCADCmd missing: {FREECAD_CMD}")
        return 0
    if not SCRIPT.exists():
        print(f"FAIL: missing script {SCRIPT}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    local = OUT / SCRIPT.name
    local.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    env = os.environ.copy()
    env["DIPOLE_OUT_DIR"] = str(OUT)
    code = (
        "import os, runpy\n"
        f"os.environ['DIPOLE_OUT_DIR'] = r'{OUT}'\n"
        f"runpy.run_path(r'{local}', run_name='__main__')\n"
    )
    proc = subprocess.run(
        [str(FREECAD_CMD), "-c", code],
        cwd=str(OUT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    log = (proc.stdout or "") + (proc.stderr or "")
    print(log[-2000:])
    assert proc.returncode == 0, log[-2000:]
    assert FCSTD.exists(), FCSTD
    assert (OUT / "openems_ports.json").exists()
    assert (OUT / "openems_export.json").exists()

    from app.freecad_io.port_marks import (
        find_manifest_ports,
        freecad_extract_ports_code,
        parse_ports_payload,
        ports_to_port_defs,
    )

    helper = freecad_extract_ports_code()
    # FreeCADCmd -c can crash on large scripts — write a real file
    extract_script = OUT / "_extract_ports.py"
    extract_script.write_text(
        "import json\n"
        "ns = {}\n"
        f"exec({helper!r}, ns)\n"
        "import FreeCAD as App\n"
        f"doc = App.open(r'{FCSTD}')\n"
        "ports = ns['extract_openems_ports'](doc)\n"
        "print('PORTS_JSON=' + json.dumps(ports))\n",
        encoding="utf-8",
    )
    code = (
        "import runpy\n"
        f"runpy.run_path(r'{extract_script}', run_name='__main__')\n"
    )
    proc = subprocess.run(
        [str(FREECAD_CMD), "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = proc.stdout or ""
    err = proc.stderr or ""
    line = next((ln for ln in out.splitlines() if ln.startswith("PORTS_JSON=")), "")
    assert line, f"no PORTS_JSON\n{out}\n{err}"
    ports = parse_ports_payload(json.loads(line.split("=", 1)[1]))
    print("ports:", ports)
    assert ports, ports
    names = {p["name"] for p in ports}
    assert "in1" in names, names
    p0 = next(p for p in ports if p["name"] == "in1")
    # gap is ±1 mm on X
    assert abs(p0["start"][0] + 1.0) < 0.25, p0
    assert abs(p0["stop"][0] - 1.0) < 0.25, p0
    assert p0["direction"] == "x", p0
    assert abs(float(p0["resistance"]) - 50.0) < 1e-6, p0
    defs = ports_to_port_defs(ports)
    assert defs and defs[0].name == "in1"
    assert defs[0].excite_port

    mports = find_manifest_ports(str(OUT / "openems_export.json"))
    assert any(p.get("name") == "in1" for p in mports), mports

    print("DIPOLE PORTS SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
