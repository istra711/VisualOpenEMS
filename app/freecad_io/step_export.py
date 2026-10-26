"""Export the project geometry to STEP (AP214) for CAD/CST interchange.

openEMS/CSXCAD geometry is stored in *drawing units* (mm/um/m/cm) as analytic
primitives (box/cylinder/sphere/polygon) plus triangle meshes. STEP is the
neutral CAD format CST Studio Suite reads natively, so it is the exchange
format for the VisualOpenEMS <-> CST round-trip.

Two properties make the file usable on the other side:

* **Millimetres.** FreeCAD writes ``SI_UNIT(.MILLI.,.METRE.)``; every
  coordinate is converted from the project's drawing unit first.
* **Names.** FreeCADCmd exports in console mode, where a bare ``Part.export``
  loses the object names (it writes one anonymous product). ``Import.export``
  keeps each ``Label`` as a STEP ``PRODUCT``, so the solids arrive in CST with
  the same names as in the Mesh Viewer.

The STEP file is built by FreeCADCmd — the same executable the STEP *import*
already needs — so this adds no new dependency. ``build_shape_specs`` is plain
Python and unit-testable without FreeCAD; ``export_step`` runs the external
script.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.paths import app_root
from app.winproc import hidden_console_kwargs

APP_ROOT = app_root()
RUNNER_SCRIPT = APP_ROOT / "app" / "freecad_io" / "freecad_step_runner.py"
FREECAD_CMD_DEFAULT = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")

#: Normal axis letter -> index into (x, y, z).
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}

#: A box/primitive thinner than this (relative to its size) counts as flat.
#: Matches the "zero thickness" metal sheets openEMS inflates after meshing.
_FLAT_EPS = 1e-12


def mm_per_unit(unit: str) -> float:
    """Millimetres per drawing unit (mirrors ``MeshSpec.unit_to_meters``)."""
    return {"m": 1.0, "mm": 1e-3, "um": 1e-6, "cm": 1e-2}[str(unit or "mm")] * 1000.0


def _scaled(vec, scale: float) -> list[float]:
    return [float(v) * scale for v in vec]


def _sheet_points(lo, hi, axis_idx: int) -> list[list[float]]:
    """Rectangle corners of a flat box in the cyclic plane of ``axis_idx``.

    CSXCAD stores a polygon's 2D points in the two axes *after* the normal,
    cyclically: z->(x,y), x->(y,z), y->(z,x). Using the same order here means a
    zero-thickness box and a polygon sheet describe the same geometry.
    """
    c0, c1 = (axis_idx + 1) % 3, (axis_idx + 2) % 3
    return [
        [lo[c0], lo[c1]],
        [hi[c0], lo[c1]],
        [hi[c0], hi[c1]],
        [lo[c0], hi[c1]],
    ]


def _mesh_triangles(prim: dict) -> Any:
    """Triangles of an ``kind=stl`` primitive (npy first, then stl/obj), or None."""
    import numpy as np

    npy = prim.get("path") or prim.get("mesh_path") or ""
    stl = prim.get("stl_path") or ""
    if npy and Path(npy).suffix.lower() == ".npy" and Path(npy).exists():
        try:
            return np.load(npy)
        except Exception:  # noqa: BLE001 — fall back to the STL below
            pass
    for candidate in (npy, stl):
        if not candidate or not Path(candidate).exists():
            continue
        if Path(candidate).suffix.lower() in {".stl", ".obj"}:
            from app.freecad_io.freecad_mesh import load_stl_or_obj_triangles

            try:
                return load_stl_or_obj_triangles(candidate)
            except Exception:  # noqa: BLE001
                continue
    return None


def build_shape_specs(
    primitives: list[dict] | None,
    *,
    unit: str = "mm",
    mesh_dir: Path | str | None = None,
) -> tuple[list[dict], list[str]]:
    """Convert project primitives to STEP shape specs in millimetres.

    Returns ``(specs, warnings)``. Specs are plain dicts the FreeCAD runner
    turns into solids/faces; warnings name anything that cannot be represented
    (a point, a line, a missing mesh …) so the GUI can report it.
    """
    scale = mm_per_unit(unit)
    specs: list[dict] = []
    warnings: list[str] = []

    for i, prim in enumerate(primitives or []):
        name = str(prim.get("name") or f"shape{i + 1}")
        kind = str(prim.get("kind") or "box").lower()
        try:
            if kind == "box":
                start = _scaled(prim["start"], scale)
                stop = _scaled(prim["stop"], scale)
                lo = [min(a, b) for a, b in zip(start, stop)]
                hi = [max(a, b) for a, b in zip(start, stop)]
                flat = [
                    k
                    for k in range(3)
                    if hi[k] - lo[k] <= _FLAT_EPS * max(1.0, abs(hi[k]))
                ]
                if len(flat) == 3:
                    warnings.append(f"{name}: box has no extent (point) — skipped")
                    continue
                if len(flat) == 2:
                    warnings.append(
                        f"{name}: box is a line (two zero extents) — skipped"
                    )
                    continue
                if len(flat) == 1:
                    # zero-thickness sheet (openEMS metal plane, e.g. a patch)
                    idx = flat[0]
                    specs.append(
                        {
                            "name": name,
                            "kind": "sheet",
                            "axis": "xyz"[idx],
                            "coord": hi[idx],
                            "points": _sheet_points(lo, hi, idx),
                        }
                    )
                    continue
                specs.append({"name": name, "kind": "box", "start": lo, "stop": hi})

            elif kind == "cylinder":
                start = _scaled(prim["start"], scale)
                stop = _scaled(prim["stop"], scale)
                radius = float(prim.get("radius", 0.0)) * scale
                if radius <= 0:
                    warnings.append(f"{name}: cylinder radius is zero — skipped")
                    continue
                if all(abs(a - b) <= _FLAT_EPS for a, b in zip(start, stop)):
                    warnings.append(f"{name}: cylinder has no length — skipped")
                    continue
                specs.append(
                    {
                        "name": name,
                        "kind": "cylinder",
                        "start": start,
                        "stop": stop,
                        "radius": radius,
                    }
                )

            elif kind == "sphere":
                center = _scaled(prim["center"], scale)
                radius = float(prim.get("radius", 0.0)) * scale
                if radius <= 0:
                    warnings.append(f"{name}: sphere radius is zero — skipped")
                    continue
                specs.append(
                    {"name": name, "kind": "sphere", "center": center, "radius": radius}
                )

            elif kind == "polygon":
                axis = str(prim.get("axis") or "z").lower()
                if axis not in AXIS_INDEX:
                    warnings.append(f"{name}: unknown polygon axis {axis!r} — skipped")
                    continue
                points = [[float(pt[0]) * scale, float(pt[1]) * scale] for pt in prim.get("points") or []]
                if len(points) < 3:
                    warnings.append(f"{name}: polygon needs >= 3 points — skipped")
                    continue
                length = float(prim.get("length") or 0.0) * scale
                specs.append(
                    {
                        "name": name,
                        "kind": "prism" if length > 0 else "sheet",
                        "axis": axis,
                        "coord": float(prim.get("coord") or 0.0) * scale,
                        "length": length,
                        "points": points,
                    }
                )

            elif kind == "stl":
                triangles = _mesh_triangles(prim)
                if triangles is None or not len(triangles):
                    warnings.append(f"{name}: mesh not found or empty — skipped")
                    continue
                if mesh_dir is None:
                    warnings.append(f"{name}: no mesh directory for export — skipped")
                    continue
                import numpy as np

                from app.csxcad_builder.triangle_mesh import write_binary_stl

                mesh_path = Path(mesh_dir) / f"{len(specs)}_{name}.stl"
                write_binary_stl(mesh_path, np.asarray(triangles, dtype=float) * scale)
                specs.append({"name": name, "kind": "mesh", "stl": str(mesh_path)})

            else:
                warnings.append(f"{name}: unsupported primitive kind {kind!r} — skipped")
        except (KeyError, TypeError, ValueError, IndexError) as e:
            warnings.append(f"{name}: {e} — skipped")

    return specs, warnings


def _resolve_freecad_cmd(freecad_cmd: str | Path | None) -> Path | None:
    """Explicit path wins, then the FreeCAD default, then saved settings."""
    cmd = Path(freecad_cmd) if freecad_cmd else FREECAD_CMD_DEFAULT
    if cmd.exists():
        return cmd
    try:
        from app.settings import AppSettings

        saved = Path(AppSettings.load().freecad_cmd)
        if saved.exists():
            return saved
    except Exception:  # noqa: BLE001 — settings are optional here
        pass
    return None


def export_step(
    primitives: list[dict] | None,
    out_path: str | Path,
    *,
    unit: str = "mm",
    freecad_cmd: str | Path | None = None,
    timeout: int = 300,
) -> tuple[bool, str, list[str]]:
    """Write the project geometry as a STEP file in millimetres.

    Returns ``(ok, log, warnings)``. ``warnings`` lists geometry that could not
    be represented (already surfaced when ``ok`` is True as well).
    """
    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out = out.resolve()

    cmd = _resolve_freecad_cmd(freecad_cmd)
    if cmd is None:
        return False, f"FreeCADCmd not found (set it in Settings): {FREECAD_CMD_DEFAULT}", []
    if not RUNNER_SCRIPT.exists():
        return False, f"STEP export script missing: {RUNNER_SCRIPT}", []

    with tempfile.TemporaryDirectory(prefix="vopenems_step_") as tmp:
        # STL primitives need a scaled temp mesh the FreeCAD runner can read,
        # so the whole spec build happens inside the temp dir.
        specs, warnings = build_shape_specs(primitives, unit=unit, mesh_dir=tmp)
        if not specs:
            detail = "\n".join(warnings) if warnings else "No geometry to export."
            return False, "Nothing to export:\n" + detail, warnings

        manifest = Path(tmp) / "shapes.json"
        manifest.write_text(
            json.dumps({"unit": "mm", "shapes": specs}, indent=2), encoding="utf-8"
        )

        env = os.environ.copy()
        env["STEP_MANIFEST"] = str(manifest)
        env["STEP_OUT"] = str(out.resolve())
        code = (
            "import os, runpy\n"
            f"runpy.run_path(r'{RUNNER_SCRIPT}', run_name='__main__')\n"
        )
        try:
            proc = subprocess.run(
                [str(cmd), "-c", code],
                cwd=str(Path(tmp)),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                **hidden_console_kwargs(),
            )
        except subprocess.TimeoutExpired as e:
            return False, f"FreeCADCmd STEP export timed out: {e}", warnings
        except OSError as e:
            return False, f"Could not start FreeCADCmd ({cmd}): {e}", warnings

        log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        for line in log.splitlines():
            if line.startswith("WARNING: "):
                warnings.append(line[len("WARNING: ") :].strip())

    if not out.exists() or out.stat().st_size == 0:
        return False, f"Step export produced no file.\n{log[-2000:]}", warnings
    return True, log, warnings
