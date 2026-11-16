"""Reusable user project templates for VisualopenEMS.

A template is a normal ``.fcsim.json`` project file in a separate template
folder, with one extra top-level metadata object::

    {
      "visualopenems_template": {
        "format_version": 1,
        "name": "2.4 GHz patch on FR4",
        "description": "...",
        "created": "2026-11-16T...",
        "app_version": "0.12.0"
      },
      ... normal project data ...
    }

Because the rest is a regular project JSON, templates are portable and can be
opened with **File → Open** as well.  Project paths are stored relative to the
template folder, exactly like regular project files.

Templates intentionally exclude simulation results, marker state and the
S-parameter comparison reference; they are a starting point, not a result
archive.
"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from app import __version__
from app.models.project import (
    Project,
    project_from_dict_for_folder,
    project_to_dict_for_folder,
)
from app.paths import settings_path

TEMPLATE_KEY = "visualopenems_template"
TEMPLATE_FORMAT_VERSION = 1
TEMPLATE_SUFFIX = ".fcsim.json"


class TemplateError(ValueError):
    """Invalid or unreadable project template."""


@dataclass(frozen=True)
class TemplateInfo:
    """Metadata shown in the template manager without loading the project."""

    path: Path
    name: str
    description: str = ""
    created: str = ""
    app_version: str = ""
    project_name: str = ""
    ports: int = 0
    primitives: int = 0
    materials: int = 0
    variables: int = 0
    has_sweep: bool = False
    has_results: bool = False
    start_frequency: float = 0.0
    stop_frequency: float = 0.0
    target_frequency: float | None = None
    mesh_preset: str = ""
    error: str = ""

    @property
    def valid(self) -> bool:
        return not self.error

    @property
    def stem(self) -> str:
        name = self.path.name
        return name[: -len(TEMPLATE_SUFFIX)] if name.endswith(TEMPLATE_SUFFIX) else self.path.stem


@dataclass
class LoadedTemplate:
    """A parsed template plus its project and primitives."""

    path: Path
    info: TemplateInfo
    project: Project
    primitives: list[dict]

    def summary(self) -> str:
        lines = [f"Template: {self.info.name}"]
        if self.info.description:
            lines.append(self.info.description)
        lines.append(template_summary(self.info))
        if self.info.project_name and self.info.project_name != self.info.name:
            lines.append(f"Stored project name: {self.info.project_name}")
        if self.info.created:
            lines.append(f"Created: {self.info.created}")
        return "\n".join(line for line in lines if line)


def templates_dir(settings: Any = None, *, create: bool = False) -> Path:
    """Resolve the user template folder from settings (or the default)."""
    raw = getattr(settings, "template_dir", "") if settings is not None else ""
    if not str(raw or "").strip():
        raw = str(settings_path().parent / "templates")
    folder = Path(os.path.expandvars(os.path.expanduser(str(raw))))
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def sanitize_template_name(name: str) -> str:
    """A safe, readable file stem for a template display name."""
    text = str(name or "").strip()
    text = re.sub(r"\.fcsim\.json$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[^\w._ -]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip(" ._-")
    return (text or "project_template")[:80]


def template_path(folder: Path | str, name: str) -> Path:
    """Destination path for a template name inside ``folder``."""
    return Path(folder) / (sanitize_template_name(name) + TEMPLATE_SUFFIX)


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _read_raw(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TemplateError(f"Cannot read template: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TemplateError(f"Invalid template JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError("Template JSON must contain an object.")
    # A missing mesh/name pair is almost certainly not a project file.  Keep
    # the check permissive so old/hand-written projects still work.
    project = _project_payload(data)
    if not isinstance(project.get("mesh"), dict) and not isinstance(project.get("ports"), list):
        raise TemplateError("This file does not look like a VisualopenEMS project.")
    return data


def _meta_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    meta = raw.get(TEMPLATE_KEY)
    if isinstance(meta, dict):
        return meta
    # Compatibility with a possible wrapper format used by external tools.
    if isinstance(raw.get("template"), dict) and isinstance(raw.get("project"), dict):
        return dict(raw["template"])
    return {}


def _project_payload(raw: dict[str, Any]) -> dict[str, Any]:
    meta = raw.get(TEMPLATE_KEY)
    if isinstance(raw.get("project"), dict) and not isinstance(meta, dict):
        return dict(raw["project"])
    return raw


def _created_text(meta: dict[str, Any], path: Path) -> str:
    value = str(meta.get("created") or "").strip()
    if value:
        return value
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        return stamp.isoformat(timespec="seconds")
    except OSError:
        return ""


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _info_from_raw(raw: dict[str, Any], path: Path) -> TemplateInfo:
    meta = _meta_from_raw(raw)
    try:
        format_version = int(meta.get("format_version", 1) or 1)
    except (TypeError, ValueError):
        format_version = 1
    if format_version > TEMPLATE_FORMAT_VERSION:
        raise TemplateError(
            f"Template format {format_version} is newer than this application "
            f"(supported: {TEMPLATE_FORMAT_VERSION})."
        )
    project = _project_payload(raw)
    mesh = project.get("mesh") if isinstance(project.get("mesh"), dict) else {}
    name = str(meta.get("name") or project.get("name") or path.name).strip()
    if name.endswith(TEMPLATE_SUFFIX):
        name = name[: -len(TEMPLATE_SUFFIX)]
    name = name or path.name
    description = str(meta.get("description") or "").strip()
    ports = project.get("ports") if isinstance(project.get("ports"), list) else []
    prims = project.get("primitives") if isinstance(project.get("primitives"), list) else []
    mats = project.get("materials") if isinstance(project.get("materials"), list) else []
    variables = project.get("variables") if isinstance(project.get("variables"), list) else []
    target = mesh.get("target_frequency")
    return TemplateInfo(
        path=path,
        name=name,
        description=description,
        created=_created_text(meta, path),
        app_version=str(meta.get("app_version") or "").strip(),
        project_name=str(project.get("name") or "").strip(),
        ports=len(ports),
        primitives=len(prims),
        materials=len(mats),
        variables=len(variables),
        has_sweep=isinstance(project.get("sweep"), dict),
        has_results=bool(project.get("results")),
        start_frequency=_float(mesh.get("start_frequency")),
        stop_frequency=_float(mesh.get("stop_frequency")),
        target_frequency=_float(target) if target not in (None, "") else None,
        mesh_preset=str(mesh.get("preset") or ""),
    )


def _without_results(project: Project, primitives: Iterable[dict]) -> Project:
    clean = copy.deepcopy(project)
    clean.primitives = [copy.deepcopy(item) for item in primitives if isinstance(item, dict)]
    clean.results = None
    clean.sparam_reference = None
    clean.sparam_markers = []
    clean.project_id = ""  # a newly loaded template gets a fresh id
    return clean


def save_project_template(
    folder: Path | str,
    project: Project,
    primitives: Iterable[dict],
    name: str,
    description: str = "",
    *,
    overwrite: bool = False,
) -> TemplateInfo:
    """Write ``project`` as a reusable template and return its metadata.

    Raises ``FileExistsError`` when the destination exists and ``overwrite`` is
    false.  The caller owns the overwrite confirmation.
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("The template name must not be empty.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = template_path(folder, name)
    if path.exists() and not overwrite:
        raise FileExistsError(path)

    clean = _without_results(project, primitives)
    payload = project_to_dict_for_folder(clean, folder, relative=True)
    payload[TEMPLATE_KEY] = {
        "format_version": TEMPLATE_FORMAT_VERSION,
        "name": name,
        "description": str(description or "").strip(),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app_version": __version__,
    }
    _atomic_json_write(path, payload)
    return _info_from_raw(payload, path)


def load_project_template(path: Path | str) -> LoadedTemplate:
    """Load a template and return a fresh project (no results/reference)."""
    path = Path(path)
    raw = _read_raw(path)
    info = _info_from_raw(raw, path)
    project = project_from_dict_for_folder(_project_payload(raw), path.parent)
    project.project_id = uuid.uuid4().hex
    project.results = None
    project.sparam_reference = None
    project.sparam_markers = []
    primitives = [
        dict(item)
        for item in (project.primitives or [])
        if isinstance(item, dict)
    ]
    project.primitives = list(primitives)
    return LoadedTemplate(path=path, info=info, project=project, primitives=primitives)


def list_project_templates(folder: Path | str | None = None) -> list[TemplateInfo]:
    """List templates in ``folder`` (invalid files are shown with an error)."""
    if folder is None:
        folder = templates_dir()
    folder = Path(folder)
    if not folder.is_dir():
        return []
    out: list[TemplateInfo] = []
    try:
        paths = sorted(
            (
                path
                for path in folder.glob("*" + TEMPLATE_SUFFIX)
                if path.is_file() and not path.name.startswith(".")
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        return []
    for path in paths:
        try:
            out.append(_info_from_raw(_read_raw(path), path))
        except TemplateError as exc:
            out.append(
                TemplateInfo(
                    path=path,
                    name=path.name,
                    created=_created_text({}, path),
                    error=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not hide others
            out.append(
                TemplateInfo(
                    path=path,
                    name=path.name,
                    created=_created_text({}, path),
                    error=str(exc),
                )
            )
    out.sort(key=lambda info: (not info.valid, info.name.casefold()))
    return out


def delete_project_template(path: Path | str) -> None:
    """Delete one template file (the user has already confirmed)."""
    Path(path).unlink()


def template_summary(info: TemplateInfo) -> str:
    """One-line content summary for list/preview headers."""
    parts: list[str] = []
    if info.ports:
        parts.append(f"{info.ports} port{'s' if info.ports != 1 else ''}")
    if info.primitives:
        parts.append(f"{info.primitives} primitive{'s' if info.primitives != 1 else ''}")
    if info.materials:
        parts.append(f"{info.materials} material{'s' if info.materials != 1 else ''}")
    if info.variables:
        parts.append(f"{info.variables} variable{'s' if info.variables != 1 else ''}")
    if info.has_sweep:
        parts.append("sweep")
    if info.start_frequency > 0 and info.stop_frequency > 0:
        parts.append(
            f"{info.start_frequency / 1e9:g}–{info.stop_frequency / 1e9:g} GHz"
        )
    if info.target_frequency:
        parts.append(f"f0 {info.target_frequency / 1e9:g} GHz")
    if info.mesh_preset:
        parts.append(f"mesh {info.mesh_preset}")
    return " · ".join(parts) if parts else "Empty project"


__all__ = [
    "LoadedTemplate",
    "TEMPLATE_FORMAT_VERSION",
    "TEMPLATE_KEY",
    "TEMPLATE_SUFFIX",
    "TemplateError",
    "TemplateInfo",
    "delete_project_template",
    "list_project_templates",
    "load_project_template",
    "sanitize_template_name",
    "save_project_template",
    "template_path",
    "template_summary",
    "templates_dir",
]
