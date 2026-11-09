"""Mesh2FreeCAD: open the meshed geometry directly in FreeCAD.

The Mesh menu entry writes the current, built mesh as a **discretised**
geometry (one mesh-cell solid per object) into a throw-away directory, asks
FreeCADCmd for a ``.FCStd`` document and opens that document in the FreeCAD
GUI.  The temporary directory is removed when FreeCAD closes (or, at the
latest, when VisualopenEMS exits).

The heavy lifting lives in :mod:`app.freecad_io.discretized_export`, the
integrated copy of the standalone discretised-export add-on.
"""

from __future__ import annotations

import atexit
import json
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_SANITIZE_GUI_SCRIPT = r'''# -*- coding: utf-8 -*-
"""Create GUI view providers and make every object visible, then fit the view."""

import json
import sys
from pathlib import Path

import FreeCAD as App
import FreeCADGui as Gui

path = Path(r"__PATH__")
marker = Path(r"__MARKER__")
ports_path = r"__PORTS__"

# A headless (FreeCADCmd) document has no GuiDocument.xml.  Opening it here
# creates the view providers; without an explicit Visibility=True FreeCAD
# saves them as hidden, which is why a later GUI start shows no objects.
Gui.showMainWindow()
try:
    Gui.getMainWindow().hide()
except Exception:
    pass

doc = App.openDocument(str(path))

if ports_path and Path(ports_path).is_file():
    import Part

    ports = json.loads(Path(ports_path).read_text(encoding="utf-8"))
    group = doc.addObject("App::DocumentObjectGroup", "OpenEMS_Ports")
    group.Label = "OpenEMS Ports"
    main_group = doc.getObject("VisualOpenEMS")
    if main_group is not None:
        main_group.addObject(group)
    for item in ports:
        name = str(item.get("name") or "port")
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        safe = safe or "port"
        start = [float(v) for v in item.get("start") or []]
        stop = [float(v) for v in item.get("stop") or []]
        if len(start) != 3 or len(stop) != 3:
            continue
        for kind, vec in (("start", start), ("stop", stop)):
            feature = doc.addObject("Part::Feature", f"PORT_{safe}_{kind}")
            feature.Shape = Part.Vertex(App.Vector(*vec))
            feature.Label = f"PORT_{name}_{kind}"
            view = getattr(feature, "ViewObject", None)
            if view is not None:
                try:
                    view.PointColor = (1.0, 0.1, 0.1)
                    view.PointSize = 6.0
                except Exception:
                    pass
            group.addObject(feature)
        line = doc.addObject("Part::Feature", f"PORT_{safe}_line")
        line.Shape = Part.LineSegment(App.Vector(*start), App.Vector(*stop)).toShape()
        line.Label = f"PORT_{name} (line)"
        view = getattr(line, "ViewObject", None)
        if view is not None:
            try:
                view.LineColor = (1.0, 0.35, 0.1)
                view.LineWidth = 2.0
            except Exception:
                pass
        group.addObject(line)

for obj in doc.Objects:
    view = getattr(obj, "ViewObject", None)
    if view is not None:
        view.Visibility = True
try:
    Gui.ActiveDocument.ActiveView.fitAll()
except Exception as exc:
    print(f"fitAll failed: {exc}")

doc.save()
doc.saveAs(str(path))
marker.write_text("ok", encoding="utf-8")
print(f"GUI document prepared: {path}")
App.closeDocument(doc.Name)
sys.exit(0)
'''

from app.freecad_io.discretized_export.exporter import ExportOptions, export
from app.freecad_io.freecad_launcher import resolve_freecad_gui

Progress = Callable[[int, str], None]

#: Temporary export directories that still need to be removed.
_SESSIONS: list["_TempSession"] = []
_SESSIONS_LOCK = threading.Lock()


@dataclass
class Mesh2FreeCADOptions:
    """Export options from the Mesh menu (persisted in the project file)."""

    export_step: bool = False
    fcstd_object_type: str = "mesh"  # "part" | "mesh"
    voxel_grid: bool = True
    voxel_grid_mode: str = "all"  # "boundary" | "all"
    include_sheets: bool = True
    export_ports: bool = True
    keep_temp: bool = False


@dataclass
class Mesh2FreeCADResult:
    ok: bool
    message: str
    log: str = ""
    warnings: list[str] = field(default_factory=list)
    temp_dir: Path | None = None
    fcstd: Path | None = None
    step: Path | None = None
    process: Any | None = None


@dataclass
class _TempSession:
    temp_dir: Path
    process: Any | None = None
    keep: bool = False


def _remove_dir(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # noqa: BLE001 — cleanup must never raise
        pass


def _finish_session(session: _TempSession) -> None:
    with _SESSIONS_LOCK:
        if session in _SESSIONS:
            _SESSIONS.remove(session)
    if not session.keep:
        _remove_dir(session.temp_dir)


def schedule_cleanup(
    temp_dir: str | Path,
    process: Any | None = None,
    *,
    keep: bool = False,
) -> _TempSession:
    """Remove ``temp_dir`` once ``process`` exits (or immediately if it is None).

    ``process`` only needs a ``wait()`` method, so tests can pass a dummy.  The
    session is registered for :func:`cleanup_all` as well, which runs when the
    main window closes.
    """
    session = _TempSession(temp_dir=Path(temp_dir), process=process, keep=bool(keep))
    with _SESSIONS_LOCK:
        _SESSIONS.append(session)

    if keep:
        return session

    if process is None:
        _finish_session(session)
        return session

    def _wait_and_remove() -> None:
        try:
            process.wait()
        except Exception:  # noqa: BLE001 — still try to remove the files
            pass
        _finish_session(session)

    threading.Thread(
        target=_wait_and_remove, name="mesh2freecad-cleanup", daemon=True
    ).start()
    return session


def cleanup_all() -> None:
    """Remove every temporary export directory still registered.

    Called from the main window's ``closeEvent`` and from ``atexit``.  Files
    that FreeCAD still has open are skipped by ``shutil.rmtree`` — the next
    call (or the atexit handler) tries again.
    """
    with _SESSIONS_LOCK:
        sessions = list(_SESSIONS)
    for session in sessions:
        if not session.keep:
            _remove_dir(session.temp_dir)
        with _SESSIONS_LOCK:
            if session in _SESSIONS:
                _SESSIONS.remove(session)


def _project_snapshot(
    project: Any,
    primitives: list[dict],
    csx_path: Path,
    temp_dir: Path,
    name: str,
) -> Path:
    """Write the live project as JSON the discretised exporter can read."""
    data = project.to_dict()
    data["name"] = name
    data["primitives"] = [dict(p) for p in primitives]
    data["results"] = {
        "project_dir": str(temp_dir),
        "sim_dir": str(temp_dir),
        "csx_xml": str(csx_path),
    }
    path = temp_dir / f"{name}.fcsim.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _port_points(project: Any) -> list[dict[str, Any]]:
    """Start/stop points of every project port, in project drawing units."""
    out: list[dict[str, Any]] = []
    for index, port in enumerate(getattr(project, "ports", None) or []):
        try:
            start = [float(v) for v in getattr(port, "start", [])]
            stop = [float(v) for v in getattr(port, "stop", [])]
        except (TypeError, ValueError):
            continue
        if len(start) != 3 or len(stop) != 3 or start == stop:
            continue
        out.append(
            {
                "name": str(getattr(port, "name", "") or f"port{index + 1}"),
                "type": str(getattr(port, "type", "") or ""),
                "start": start,
                "stop": stop,
            }
        )
    return out


def _ensure_visible_document(
    fcstd: Path,
    freecad_gui: Path,
    *,
    ports: list[dict[str, Any]] | None = None,
    timeout: int = 120,
) -> tuple[bool, str]:
    """Add GUI view providers and set every object visible, then fit the view.

    A document written by FreeCADCmd has no ``GuiDocument.xml``; when FreeCAD
    later creates the view providers itself, they default to hidden.  This
    short GUI console pass saves them explicitly visible and optionally adds
    the port start/stop points.
    """
    tmp = Path(tempfile.mkdtemp(prefix="vopenems_mesh2freecad_gui_"))
    try:
        script = tmp / "show_document.py"
        marker = tmp / "done"
        ports_file = tmp / "ports.json"
        if ports:
            ports_file.write_text(json.dumps(ports, indent=2), encoding="utf-8")
        script.write_text(
            _SANITIZE_GUI_SCRIPT.replace("__PATH__", str(fcstd))
            .replace("__MARKER__", str(marker))
            .replace("__PORTS__", str(ports_file) if ports else ""),
            encoding="utf-8",
        )
        code = f"exec(open(r'{script}').read())"
        try:
            proc = subprocess.run(
                [str(freecad_gui), "-c", code],
                cwd=str(tmp),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, f"FreeCAD GUI preparation timed out after {timeout}s"
        except OSError as exc:
            return False, f"Could not start FreeCAD GUI ({freecad_gui}): {exc}"
        log = (proc.stdout or "") + (
            "\n" + proc.stderr if proc.stderr else ""
        )
        if marker.is_file():
            # saveAs() keeps the previous headless document as <name>.FCStd1
            try:
                Path(str(fcstd) + "1").unlink(missing_ok=True)
            except OSError:
                pass
            return True, log
        return False, log[-1500:] or "FreeCAD GUI preparation produced no result"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _open_document(target: Path, freecad_cmd: Any, freecad_gui: Any) -> tuple[Any | None, str]:
    gui = resolve_freecad_gui(freecad_cmd, freecad_gui)
    if gui is None:
        return None, "FreeCAD GUI binary not found — set it in Settings."
    try:
        proc = subprocess.Popen([str(gui), str(target)], cwd=str(target.parent))
    except OSError as exc:
        return None, f"Could not start FreeCAD ({gui}): {exc}"
    return proc, f"Opened in FreeCAD: {target}"


def export_and_open(
    project: Any,
    primitives: list[dict],
    csx: Any,
    *,
    options: Mesh2FreeCADOptions | None = None,
    freecad_cmd: Any = None,
    freecad_gui: Any = None,
    name: str = "project",
    progress: Progress | None = None,
) -> Mesh2FreeCADResult:
    """Export the current mesh to a temporary FCStd and open it in FreeCAD.

    ``csx`` is the CSXCAD model currently shown in the Mesh Viewer (the grid
    that was actually built).  Returns a result; never raises for expected
    errors (missing FreeCAD, empty geometry …).
    """
    opts = options or Mesh2FreeCADOptions()
    warnings: list[str] = []

    if csx is None:
        return Mesh2FreeCADResult(False, "No mesh loaded — build the mesh first.")
    if not primitives:
        return Mesh2FreeCADResult(
            False,
            "No geometry to export. Import a CAD file or add primitives first.",
        )
    unit = str(getattr(getattr(project, "mesh", None), "drawing_unit", "mm") or "mm")
    if unit.lower() != "mm":
        return Mesh2FreeCADResult(
            False,
            f"Mesh2FreeCAD currently exports millimetre projects only "
            f"(drawing unit is {unit!r}).",
        )

    temp_dir = Path(tempfile.mkdtemp(prefix="vopenems_mesh2freecad_"))
    try:
        from app.meshing.preview import export_csx_xml

        csx_path = export_csx_xml(csx, temp_dir / "csx.xml")
        snapshot = _project_snapshot(project, primitives, csx_path, temp_dir, name)
        result = export(
            ExportOptions(
                project_path=snapshot,
                output_dir=temp_dir,
                mesh_xml=csx_path,
                include_sheets=bool(opts.include_sheets),
                export_step=bool(opts.export_step),
                export_fcstd=True,
                fcstd_object_type=(
                    opts.fcstd_object_type
                    if opts.fcstd_object_type in {"part", "mesh"}
                    else "part"
                ),
                export_voxel_grid=bool(opts.voxel_grid),
                voxel_grid_mode=(
                    opts.voxel_grid_mode
                    if opts.voxel_grid_mode in {"boundary", "all"}
                    else "boundary"
                ),
                freecad_cmd=freecad_cmd,
                progress=progress,
            )
        )
        warnings.extend(result.warnings)
    except Exception as exc:  # noqa: BLE001 — report, never crash the GUI
        if not opts.keep_temp:
            _remove_dir(temp_dir)
        return Mesh2FreeCADResult(
            False,
            f"Mesh2FreeCAD export failed: {type(exc).__name__}: {exc}",
            warnings=warnings,
        )

    target = result.fcstd_file or result.step_file
    if target is None:
        if not opts.keep_temp:
            _remove_dir(temp_dir)
        detail = "\n".join(warnings) if warnings else result.log[-1500:]
        return Mesh2FreeCADResult(
            False,
            "Mesh2FreeCAD did not produce a FreeCAD document.\n" + detail,
            log=result.log,
            warnings=warnings,
        )

    port_points = _port_points(project) if opts.export_ports else []
    gui = resolve_freecad_gui(freecad_cmd, freecad_gui)
    if result.fcstd_file is not None and gui is not None:
        if progress is not None:
            progress(
                -1,
                "Preparing FreeCAD view providers (object visibility"
                + (f", {len(port_points)} port(s)" if port_points else "")
                + ")...",
            )
        visible, vis_log = _ensure_visible_document(
            result.fcstd_file, gui, ports=port_points
        )
        if visible:
            if progress is not None:
                progress(-1, "FreeCAD document prepared: objects visible, view fitted.")
        else:
            warnings.append(
                "Could not prepare the FreeCAD view providers — objects may "
                f"open hidden: {vis_log.strip()[-500:]}"
            )
            if progress is not None:
                progress(-1, "Warning: FreeCAD visibility preparation failed.")

    process, message = _open_document(target, freecad_cmd, freecad_gui)
    if process is None:
        if not opts.keep_temp:
            _remove_dir(temp_dir)
        return Mesh2FreeCADResult(
            False,
            message,
            log=result.log,
            warnings=warnings,
            temp_dir=temp_dir,
            fcstd=result.fcstd_file,
            step=result.step_file,
        )

    schedule_cleanup(temp_dir, process, keep=bool(opts.keep_temp))
    kept = " (temporary files kept)" if opts.keep_temp else ""
    exported = f"{len(result.objects)} object(s)"
    if port_points:
        exported += f", {len(port_points)} port(s)"
    return Mesh2FreeCADResult(
        True,
        f"{message} — {exported}{kept}",
        log=result.log,
        warnings=warnings,
        temp_dir=temp_dir,
        fcstd=result.fcstd_file,
        step=result.step_file,
        process=process,
    )


atexit.register(cleanup_all)
