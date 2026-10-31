"""Verify required Python packages and openEMS integration paths."""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def main() -> int:
    checks = []

    def check(label: str, fn):
        try:
            info = fn()
            checks.append((label, True, str(info)))
        except Exception as e:  # noqa: BLE001
            checks.append((label, False, str(e)))

    check("Python", lambda: sys.version)
    check("openEMS", lambda: __import__("openEMS").__file__)
    check("CSXCAD", lambda: __import__("CSXCAD").__file__)
    check("easyMesh.GenerateMesh", lambda: __import__("easyMesh").GenerateMesh)
    check("openEMS.nf2ff", lambda: __import__("openEMS.nf2ff", fromlist=["nf2ff"]).nf2ff)
    check("PySide6", lambda: __import__("PySide6").__version__)
    check("h5py", lambda: __import__("h5py").__version__)
    check("numpy", lambda: __import__("numpy").__version__)
    check("matplotlib", lambda: __import__("matplotlib").__version__)
    check("scipy", lambda: __import__("scipy").__version__)
    check("skrf", lambda: __import__("skrf").__version__)

    # FreeCAD command
    from app.settings import AppSettings

    s = AppSettings.load()
    fc = Path(s.freecad_cmd)
    check("FreeCADCmd", lambda: (fc, fc.exists()))

    # openEMS binary (optional)
    oe = Path(s.openems_root) / "openEMS.exe"
    check("openEMS.exe", lambda: (oe, oe.exists()))

    failed = 0
    print("=== VisualOpenEMS — environment check ===")
    for label, ok, info in checks:
        status = "OK " if ok else "FAIL"
        print(f"[{status}] {label}: {info}")
        if not ok:
            failed += 1

    # import app modules
    try:
        from app.models.project import Project
        from app.meshing.rules import compute_targets
        from app.meshing.validators import validate_mesh
        from app.csxcad_builder.primitives import patch_antenna_primitives
        from app.simulation.script_gen import generate_run_script
        from app.postproc import sparams, fields, nf2ff

        print("[OK ] app modules importable")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] app modules: {e}")
        failed += 1

    print("=== Result:", "PASS" if failed == 0 else f"{failed} failure(s)", "===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
