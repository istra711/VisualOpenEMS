"""Optional STEP export through FreeCADCmd.

The add-on itself only writes STL.  If STEP is requested, FreeCADCmd converts
the per-object STL files into one STEP file with one named PRODUCT per object.
This is the same mechanism VisualopenEMS uses for its normal STEP export, but
implemented here without importing or modifying the main application.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

from app.platform_tools import default_freecad_cmd, freecad_cmd_candidates

DEFAULT_FREECAD = default_freecad_cmd()

RUNNER = r'''
import json
import os
from pathlib import Path

import FreeCAD as App
import Import
import Mesh
import Part

manifest_path = os.environ.get("STEP_MANIFEST", "")
out_path = os.environ.get("STEP_OUT", "")
if not manifest_path or not out_path:
    raise SystemExit("STEP_MANIFEST and STEP_OUT must be set")

data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
shapes = data.get("shapes") or []
doc = App.newDocument("VisualOpenEMS_Discretized_STEP")
built = 0
for index, item in enumerate(shapes):
    name = str(item.get("name") or f"object_{index + 1}")
    stl = Path(str(item.get("stl") or ""))
    if not stl.is_file():
        print(f"WARNING: {name}: STL not found: {stl}")
        continue
    mesh = Mesh.Mesh(str(stl))
    if not mesh.CountFacets:
        print(f"WARNING: {name}: STL has no facets")
        continue
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, 0.0)
    if shape.isClosed():
        try:
            shape = Part.makeSolid(shape)
        except Exception as exc:
            print(f"WARNING: {name}: makeSolid failed ({exc}); exporting shell")
    obj = doc.addObject("Part::Feature", f"shape_{index}")
    obj.Shape = shape
    obj.Label = name
    built += 1

if not built:
    raise SystemExit("no shape could be built")

doc.recompute()
try:
    Import.export(doc.Objects, out_path)
except Exception as exc:
    print(f"WARNING: Import.export failed ({exc}); labels may be lost")
    Part.export(doc.Objects, out_path)
print(f"STEP written: {out_path} ({built}/{len(shapes)} shapes)")
'''


def find_freecad_cmd(explicit: str | Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("FREECAD_CMD")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.extend(freecad_cmd_candidates())
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def export_step_from_stls(
    items: Iterable[tuple[str, str | Path]],
    out_path: str | Path,
    *,
    freecad_cmd: str | Path | None = None,
    timeout: int = 900,
) -> tuple[bool, str, list[str]]:
    """Convert named STL files to one STEP file with FreeCADCmd."""
    items = [(str(name), Path(path)) for name, path in items]
    if not items:
        return False, "no STL input", ["STEP export: no STL input"]

    cmd = find_freecad_cmd(freecad_cmd)
    if cmd is None:
        return (
            False,
            "FreeCADCmd not found",
            [
                "STEP not written: FreeCADCmd not found. Set --freecad or "
                "the FREECAD_CMD environment variable."
            ],
        )

    out = Path(out_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vopenems_step_") as tmp:
        manifest = Path(tmp) / "shapes.json"
        manifest.write_text(
            json.dumps(
                {
                    "unit": "mm",
                    "shapes": [
                        {"name": name, "stl": str(path.resolve())}
                        for name, path in items
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        runner = Path(tmp) / "runner.py"
        runner.write_text(RUNNER, encoding="utf-8")

        env = os.environ.copy()
        env["STEP_MANIFEST"] = str(manifest)
        env["STEP_OUT"] = str(out)
        kwargs: dict = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                [str(cmd), str(runner)],
                cwd=str(Path(tmp)),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                **kwargs,
            )
        except subprocess.TimeoutExpired:
            return False, "FreeCADCmd timed out", [f"STEP not written: timeout after {timeout}s"]
        except OSError as exc:
            return False, f"could not start FreeCADCmd: {exc}", [
                f"STEP not written: {exc}"
            ]

        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        for line in log.splitlines():
            if line.startswith("WARNING: "):
                warnings.append("STEP: " + line[len("WARNING: ") :].strip())

    if proc.returncode != 0:
        return False, log, warnings + [f"STEP not written (FreeCADCmd exit {proc.returncode})"]
    if not out.is_file() or out.stat().st_size == 0:
        return False, log, warnings + ["STEP not written: output file is empty"]
    return True, log, warnings
