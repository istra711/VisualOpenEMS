"""Cylindrical (polar) end-to-end smoke: project_run.py in --mesh-only mode.

Verifies the Phase-1 integration without running the FDTD solver:

1. cylindrical project -> script runs, mesh setup says coord_system
   "cylindrical" / easy_mesh_bypassed, mesh validation passes,
   NF2FF is skipped with a clear message. (Free-response: no ports —
   openEMS 0.0.36's LumpedPort is not cylindrical-mesh aware, see below.)
2. same project with a gated primitive (stl) -> script aborts with the
   polar gate message.
3. cartesian control run -> the default path still works (regression).
4. short polar FDTD run (free response, dumps only, no ports) -> the
   openEMS cylindrical operator actually runs.
5. cylindrical project WITH a port -> the port gate aborts with a clear
   message (no NaN S-params from a NaN LumpedPort).

Run: python -u tests/cylindrical_mesh_smoke.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.models.project import MaterialDef, PortDef, Project
from app.settings import AppSettings
from app.simulation.script_gen import generate_run_script

POLAR_PRIMS = [
    {
        "kind": "box",
        "name": "wedge",
        "material": "copper",
        "priority": 20,
        # (r, phi [rad], z) in drawing units (mm)
        "start": [5.0, 0.0, 10.0],
        "stop": [15.0, 1.5707963, 20.0],
        "edges2grid": "xy",
    },
    {
        "kind": "cylinder",
        "name": "post",
        "material": "copper",
        "priority": 15,
        "start": [0.0, 0.0, 0.0],
        "stop": [0.0, 0.0, 40.0],
        "radius": 3.0,
    },
]


def _base_project(coord: str) -> Project:
    p = Project(name=f"cyl_smoke_{coord}")
    p.mesh.drawing_unit = "mm"
    p.mesh.preset = "medium"
    p.mesh.start_frequency = 0.5e9
    p.mesh.stop_frequency = 1.5e9
    p.mesh.target_frequency = 1.0e9
    p.mesh.coord_system = coord  # type: ignore[assignment]
    p.materials = [
        MaterialDef(name="copper", kind="pec"),
        MaterialDef(name="dielectric", kind="dielectric", epsilon=10.0),
    ]
    # Free-response project: no ports (openEMS 0.0.36 LumpedPort is not
    # cylindrical-mesh aware; the port gate test below adds one on purpose).
    p.ports = []
    # NF2FF on + enabled => polar run must print the skip message
    p.sim.nf2ff_enabled = True
    p.sim.max_timesteps = 5000
    p.sim.end_criteria = 1e-3
    p.nf2ff.enabled = True
    return p


def _run(script: Path, work: Path) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-u", str(script), "--mesh-only"],
        cwd=str(work),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _assert(cond: bool, msg: str, log: str) -> None:
    if not cond:
        tail = "\n".join((log or "").splitlines()[-60:])
        raise AssertionError(f"{msg}\n--- log tail ---\n{tail}")


def main() -> int:
    settings = AppSettings.load()
    work = settings.work_path() / "cyl_mesh_smoke"
    work.mkdir(parents=True, exist_ok=True)

    # ---- 1. cylindrical run -------------------------------------------------
    p = _base_project("cylindrical")
    prims = [dict(x) for x in POLAR_PRIMS]
    project_path = p.save(work / "cyl.fcsim.json")
    script = generate_run_script(p, project_path, work, primitives=prims)
    rc, log = _run(script, work)
    print(f"[polar] exit={rc}")
    assert rc == 0, "cylindrical mesh-only run must exit 0"
    _assert('"coord_system": "cylindrical"' in log, "mesh setup must report cylindrical", log)
    _assert('"easy_mesh_bypassed": true' in log, "setup must say easy_mesh_bypassed", log)
    _assert('"n_lines"' in log and '"r"' in log, "setup must report polar line counts", log)
    import re

    _assert(
        re.search(r'"z": \[\s*-', log) is not None,
        "z domain must include the z- air margin (regression: 6-list index)",
        log,
    )
    _assert("mesh validation failed" not in log, "mesh validation must pass", log)
    _assert("[ERROR]" not in log, "validation must have no errors", log)
    _assert(
        "NF2FF skipped" in log and "cylindrical" in log,
        "NF2FF must be skipped with a clear message",
        log,
    )
    _assert(
        "[OK] Mesh checks passed" in log or "[WARNING]" in log,
        "validate_mesh must have run and reported",
        log,
    )
    print("[polar] OK: cylindrical mesh + validation + NF2FF gate")

    # ---- 2. gated primitive (stl with an existing file) must abort ---------
    p2 = _base_project("cylindrical")
    stl_file = work / "tiny_cyl.npy"
    if not stl_file.exists():
        import numpy as np

        np.save(stl_file, np.zeros((1, 3, 3), dtype=float))  # one degenerate tri
    bad = [{"kind": "stl", "name": "cadpart", "material": "copper", "priority": 10,
            "path": str(stl_file), "mesh_path": str(stl_file)}]
    project_path2 = p2.save(work / "cyl_bad.fcsim.json")
    script2 = generate_run_script(p2, project_path2, work, primitives=bad)
    rc2, log2 = _run(script2, work)
    print(f"[gate] exit={rc2}")
    _assert(rc2 != 0, "gated primitive must abort the run", log2)
    _assert(
        "not supported in cylindrical mode" in log2,
        "gate must print the polar unsupported-kind message",
        log2,
    )
    print("[gate] OK: stl primitive rejected in cylindrical mode")

    # ---- 3. cartesian control run ------------------------------------------
    p3 = _base_project("cartesian")
    project_path3 = p3.save(work / "cart.fcsim.json")
    script3 = generate_run_script(p3, project_path3, work, primitives=[dict(x) for x in POLAR_PRIMS])
    rc3, log3 = _run(script3, work)
    print(f"[cartesian] exit={rc3}")
    assert rc3 == 0, "cartesian mesh-only run must still exit 0"
    _assert('"coord_system": "cartesian"' in log3, "setup must report cartesian", log3)
    _assert("mesh validation failed" not in log3, "cartesian validation must pass", log3)
    print("[cartesian] OK: default path unchanged")

    # ---- 4. SHORT polar FDTD run (free response: no ports, no excitation) --
    # Runtime proof for CoordSystem=1 (mesh-only cannot catch operator/run
    # issues). Without a port there is no excitation, so the end criteria is
    # reached almost immediately — the run stays short and must exit 0.
    p4 = _base_project("cylindrical")
    p4.mesh.max_cellsize = 3.0  # mm — enough cells for PML-8 on every face
    p4.sim.max_timesteps = 20_000
    p4.sim.end_criteria = 1e-4
    project_path4 = p4.save(work / "cyl.fcsim.json")
    script4 = generate_run_script(p4, project_path4, work, primitives=[dict(x) for x in POLAR_PRIMS])
    proc4 = subprocess.run(
        [sys.executable, "-u", str(script4)],
        cwd=str(work),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    rc4 = proc4.returncode
    log4 = (proc4.stdout or "") + (proc4.stderr or "")
    print(f"[fdtd] exit={rc4}")
    _assert(rc4 == 0, "short polar FDTD run must exit 0", log4)
    _assert(
        "Simulation finished (FDTD.Run complete)" in log4,
        "FDTD.Run must complete in cylindrical mode",
        log4,
    )
    _assert("NF2FF skipped" in log4, "NF2FF skip must still be printed", log4)
    _assert("Ports are not supported" not in log4, "free-response run has no port gate", log4)
    print("[fdtd] OK: polar FDTD free-response run")

    # ---- 5. port gate: cylindrical + port must abort with a clear message --
    p5 = _base_project("cylindrical")
    p5.ports = [
        PortDef(
            name="feed",
            type="lumped",
            start=[10.0, 0.7853981, 18.0],
            stop=[10.0, 0.7853981, 22.0],
            direction="z",
            resistance=50.0,
            excite_port=True,
        )
    ]
    project_path5 = p5.save(work / "cyl.fcsim.json")
    script5 = generate_run_script(p5, project_path5, work, primitives=[dict(x) for x in POLAR_PRIMS])
    rc5, log5 = _run(script5, work)
    print(f"[port-gate] exit={rc5}")
    _assert(rc5 != 0, "cylindrical run with ports must abort", log5)
    _assert(
        "Ports are not supported in cylindrical" in log5,
        "port gate must print a clear message",
        log5,
    )
    print("[port-gate] OK: ports rejected in cylindrical mode")

    print("CYLINDRICAL MESH SMOKE PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
