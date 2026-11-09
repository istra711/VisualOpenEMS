"""Cross-platform executable and installation-path discovery.

The GUI core (PySide6/Qt, numpy, matplotlib, VTK) is platform independent.  The
external tools (FreeCAD, openEMS/AppCSXCAD) and their binary names are not:
Windows uses ``FreeCADCmd.exe`` / ``AppCSXCAD.exe`` while Linux uses
``FreeCADCmd`` / ``AppCSXCAD`` and usually installs them under ``/usr`` or
``/opt``.  Keep those differences here instead of scattering ``.exe`` strings
through the UI and the meshing code.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"


def first_existing(paths) -> Path | None:
    """First existing file from an iterable of paths (``None`` otherwise)."""
    for value in paths:
        if not value:
            continue
        try:
            path = Path(value)
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def which_any(names) -> Path | None:
    """First executable found on ``PATH`` (``None`` otherwise)."""
    for name in names:
        found = shutil.which(str(name))
        if found:
            return Path(found)
    return None


def freecad_cmd_candidates() -> list[Path]:
    """Likely FreeCADCmd locations for the current OS."""
    out: list[Path] = []
    env = os.environ.get("FREECAD_CMD") or os.environ.get("FREECADCMD")
    if env:
        out.append(Path(env))
    if IS_WINDOWS:
        out.extend(
            [
                Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCADCmd.exe"),
                Path(r"C:\Program Files\FreeCAD 1.0\bin\FreeCADCmd.exe"),
                Path(r"C:\Program Files\FreeCAD 0.21\bin\FreeCADCmd.exe"),
                Path(r"C:\Program Files\FreeCAD\bin\FreeCADCmd.exe"),
            ]
        )
    elif IS_MAC:
        out.extend(
            [
                Path("/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd"),
                Path("/Applications/FreeCAD.app/Contents/MacOS/freecadcmd"),
            ]
        )
    else:
        out.extend(
            [
                Path("/usr/bin/FreeCADCmd"),
                Path("/usr/local/bin/FreeCADCmd"),
                Path("/usr/lib/freecad/bin/FreeCADCmd"),
                Path("/usr/lib/freecad-python3/bin/FreeCADCmd"),
                Path("/opt/freecad/bin/FreeCADCmd"),
                Path("/snap/bin/freecadcmd"),
            ]
        )
    return out


def default_freecad_cmd() -> Path:
    """Resolved FreeCADCmd default; falls back to the conventional path."""
    found = first_existing(freecad_cmd_candidates())
    if found is not None:
        return found
    found = which_any(
        ["FreeCADCmd", "freecadcmd", "FreeCADCmd.exe", "freecadcmd.exe"]
    )
    if found is not None:
        return found
    candidates = freecad_cmd_candidates()
    return candidates[0] if candidates else Path("FreeCADCmd")


def freecad_gui_candidates(cmd: str | Path | None = None) -> list[Path]:
    """Likely FreeCAD GUI locations, including siblings of FreeCADCmd."""
    out: list[Path] = []
    env = os.environ.get("FREECAD_GUI") or os.environ.get("FREECAD")
    if env:
        out.append(Path(env))
    if cmd:
        try:
            parent = Path(cmd).parent
        except (TypeError, ValueError):
            parent = None
        if parent is not None:
            out.extend(
                [
                    parent / "FreeCAD",
                    parent / "freecad",
                    parent / "FreeCAD.exe",
                    parent / "freecad.exe",
                ]
            )
    if IS_WINDOWS:
        out.extend(
            [
                Path(r"c:\FreeCAD_1.1.4-Windows-x86_64\bin\FreeCAD.exe"),
                Path(r"C:\Program Files\FreeCAD 1.0\bin\FreeCAD.exe"),
                Path(r"C:\Program Files\FreeCAD 0.21\bin\FreeCAD.exe"),
                Path(r"C:\Program Files\FreeCAD\bin\FreeCAD.exe"),
            ]
        )
    elif IS_MAC:
        out.append(Path("/Applications/FreeCAD.app/Contents/MacOS/FreeCAD"))
    else:
        out.extend(
            [
                Path("/usr/bin/FreeCAD"),
                Path("/usr/bin/freecad"),
                Path("/usr/local/bin/FreeCAD"),
                Path("/usr/local/bin/freecad"),
                Path("/opt/freecad/bin/FreeCAD"),
                Path("/snap/bin/freecad"),
            ]
        )
    return out


def default_freecad_gui(cmd: str | Path | None = None) -> Path:
    found = first_existing(freecad_gui_candidates(cmd))
    if found is not None:
        return found
    found = which_any(["FreeCAD", "freecad", "FreeCAD.exe", "freecad.exe"])
    if found is not None:
        return found
    candidates = freecad_gui_candidates(cmd)
    return candidates[0] if candidates else Path("FreeCAD")


def openems_root_candidates() -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("OPENEMS_ROOT")
    if env:
        out.append(Path(env))
    if IS_WINDOWS:
        out.extend(
            [
                Path(r"c:\openEMS"),
                Path(r"C:\Program Files\openEMS"),
                Path(r"C:\Program Files (x86)\openEMS"),
            ]
        )
    elif IS_MAC:
        out.extend([Path("/Applications/openEMS"), Path("/opt/openEMS")])
    else:
        out.extend(
            [
                Path("/usr"),
                Path("/usr/local"),
                Path("/opt/openEMS"),
                Path("/opt/openems"),
                Path("/snap/openems/current"),
            ]
        )
    return out


def default_openems_root() -> Path:
    """Directory containing the openEMS installation (not a binary path)."""
    candidates = openems_root_candidates()
    for root in candidates:
        try:
            if root.is_dir():
                return root
        except OSError:
            continue
    return candidates[0] if candidates else Path("")


def default_simulation_python() -> Path:
    """Python used for generated openEMS scripts (external interpreter)."""
    env = os.environ.get("SIMULATION_PYTHON") or os.environ.get("OPENEMS_PYTHON")
    if env and Path(env).is_file():
        return Path(env)
    if IS_WINDOWS:
        legacy = Path(r"c:\Python\WPy64-31150\python-3.11.5.amd64\python.exe")
        if legacy.is_file():
            return legacy
    for name in ("python3", "python", "python3.11"):
        found = which_any([name])
        if found is not None:
            return found
    return Path(sys.executable)


def appcsxcad_candidates(openems_root: str | Path | None = None) -> list[Path]:
    out: list[Path] = []
    env = os.environ.get("APPCSXCAD")
    if env:
        out.append(Path(env))
    roots = []
    if openems_root:
        roots.append(Path(openems_root))
    roots.extend(openems_root_candidates())
    for root in roots:
        out.extend(
            [
                root / "AppCSXCAD",
                root / "AppCSXCAD.exe",
                root / "bin" / "AppCSXCAD",
                root / "bin" / "AppCSXCAD.exe",
            ]
        )
    found = which_any(["AppCSXCAD", "appcsxcad", "AppCSXCAD.exe"])
    if found is not None:
        out.append(found)
    return out


def find_appcsxcad(openems_root: str | Path | None = None) -> Path | None:
    return first_existing(appcsxcad_candidates(openems_root))
