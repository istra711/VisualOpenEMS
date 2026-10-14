"""Launch FreeCAD GUI and export CAD for openEMS from the application."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.paths import app_root

APP_ROOT = app_root()
EXPORT_SCRIPT = APP_ROOT / "examples" / "freecad_export_for_openems.py"
FREECAD_CMD_DEFAULT = Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe")


@dataclass
class FreeCADPaths:
    freecad_cmd: Path | None = None
    freecad_gui: Path | None = None
    work_dir: Path | None = None
    warnings: list[str] | None = None


def resolve_freecad_gui(freecad_cmd: str | Path | None, freecad_gui: str | Path | None = None) -> Path | None:
    """Locate FreeCAD.exe (GUI). Explicit path wins; else derive from FreeCADCmd."""
    if freecad_gui:
        p = Path(freecad_gui)
        if p.exists():
            return p
    cmd = Path(freecad_cmd) if freecad_cmd else None
    candidates: list[Path] = []
    if cmd is not None:
        candidates.extend(
            [
                cmd.parent / "FreeCAD.exe",
                cmd.parent / "freecad.exe",
                cmd.parent / "FreeCAD",
            ]
        )
        # FreeCAD 1.x sometimes uses freecad.exe lowercase
        if cmd.parent.exists():
            for p in cmd.parent.iterdir():
                if p.name.lower() == "freecad.exe" and p.suffix.lower() == ".exe":
                    candidates.append(p)
    for c in candidates:
        try:
            if c.exists() and c.suffix.lower() == ".exe":
                return c
        except OSError:
            continue
    return None


def freecad_paths(
    freecad_cmd: str | Path | None = None,
    freecad_gui: str | Path | None = None,
    work_dir: str | Path | None = None,
) -> FreeCADPaths:
    warnings: list[str] = []
    cmd = Path(freecad_cmd) if freecad_cmd else FREECAD_CMD_DEFAULT
    if not cmd.exists():
        warnings.append(f"FreeCADCmd not found: {cmd}")
        cmd_path: Path | None = None
    else:
        cmd_path = cmd
    gui = resolve_freecad_gui(cmd_path, freecad_gui)
    if gui is None:
        warnings.append(
            "FreeCAD GUI (FreeCAD.exe) not found — set path in Settings "
            "(next to FreeCADCmd or explicit)."
        )
    work = Path(work_dir) if work_dir else None
    return FreeCADPaths(freecad_cmd=cmd_path, freecad_gui=gui, work_dir=work, warnings=warnings)


def launch_freecad(
    freecad_cmd: str | Path | None = None,
    freecad_gui: str | Path | None = None,
    document: str | Path | None = None,
    work_dir: str | Path | None = None,
) -> tuple[bool, str]:
    """Start FreeCAD GUI (optionally with a document). Returns (ok, message)."""
    paths = freecad_paths(freecad_cmd, freecad_gui, work_dir)
    if paths.freecad_gui is None:
        return False, "\n".join(paths.warnings or ["FreeCAD GUI not found."])
    args = [str(paths.freecad_gui)]
    doc = Path(document) if document else None
    if doc is not None and not doc.exists():
        return False, f"Document not found: {doc}"
    if doc is not None:
        args.append(str(doc))
    cwd = None
    if paths.work_dir is not None:
        paths.work_dir.mkdir(parents=True, exist_ok=True)
        cwd = str(paths.work_dir)
    elif doc is not None:
        cwd = str(doc.parent)
    try:
        subprocess.Popen(args, cwd=cwd)
    except OSError as e:
        return False, f"Failed to start FreeCAD: {e}"
    where = f" (cwd={cwd})" if cwd else ""
    opened = f" opened={doc}" if doc is not None else ""
    return True, f"Launched: {paths.freecad_gui}{opened}{where}"


def export_cad_for_openems(
    source: str | Path | None,
    out_dir: str | Path,
    freecad_cmd: str | Path | None = None,
    freecad_gui: str | Path | None = None,
    deflection: float = 0.1,
    timeout: int = 180,
) -> tuple[bool, str, Path]:
    """Run FreeCADCmd export script: STEP + per-object STL + openems_export.json.

    source=None → FreeCADCmd opens no file; user must pass an existing FCStd/STEP.
    Returns (ok, log, out_dir).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = freecad_paths(freecad_cmd, freecad_gui, out)
    if paths.freecad_cmd is None:
        return False, "\n".join(paths.warnings or ["FreeCADCmd not found."]), out
    if not EXPORT_SCRIPT.exists():
        return False, f"Export script missing: {EXPORT_SCRIPT}", out

    # FreeCADCmd runs with cwd=out_dir — a relative source would not be found there
    src = Path(source).expanduser().resolve() if source else None
    if src is not None and not src.exists():
        return False, f"Source CAD not found: {src}", out

    env = os.environ.copy()
    env["FREECAD_OUT_DIR"] = str(out)
    env["FREECAD_DEFLECTION"] = str(float(deflection))
    if src is not None:
        env["FREECAD_SOURCE"] = str(src)
    else:
        env.pop("FREECAD_SOURCE", None)

    code = (
        "import os, runpy\n"
        f"runpy.run_path(r'{EXPORT_SCRIPT}', run_name='__main__')\n"
    )
    try:
        proc = subprocess.run(
            [str(paths.freecad_cmd), "-c", code],
            cwd=str(out),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return False, f"FreeCADCmd export timed out: {e}", out
    log = (proc.stdout or "") + ("\n" + (proc.stderr or "") if proc.stderr else "")
    step = out / f"{src.stem}.step" if src is not None else None
    manifest = out / "openems_export.json"
    ok = manifest.exists() or (step is not None and step.exists())
    if not ok:
        return False, f"Export produced no STEP/manifest.\n{log[-2000:]}", out
    return True, log, out


def list_export_files(out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    if not out.exists():
        return []
    files = sorted(
        p
        for p in out.iterdir()
        if p.suffix.lower() in {".step", ".stp", ".stl", ".obj", ".fcstd", ".json"}
        and p.is_file()
    )
    return files
