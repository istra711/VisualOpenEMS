"""Transactional storage for project runs.

A project's data folder gets three areas::

    <project>/staging/<kind>-<timestamp>-<id>/   # not visible as a result
    <project>/runs/<run-id>/                     # immutable, committed run
    <project>/meshes/<mesh-id>/                  # optional mesh snapshots

Editing/building/running never touches an already committed run.  A run is
written into staging and only becomes a project result when the user explicitly
commits it.  The project JSON stores the active run as a relative path, so a
project can be moved/copied without rewriting result paths.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STAGING_DIR = "staging"
RUNS_DIR = "runs"
MESHES_DIR = "meshes"
MANIFEST_NAME = "manifest.json"
LAYOUT_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str = "") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    token = uuid.uuid4().hex[:6]
    return f"{prefix}{stamp}-{token}" if prefix else f"{stamp}-{token}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON next to its target and atomically replace the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _file_inventory(root: Path) -> list[dict[str, Any]]:
    """Portable, cheap manifest of the files in a staged folder."""
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == MANIFEST_NAME:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size": int(stat.st_size),
                "mtime": float(stat.st_mtime),
            }
        )
    return files


class ProjectStore:
    """Manage staging and immutable run folders below one project data folder."""

    def __init__(self, project_root: Path | str) -> None:
        self.root = Path(project_root)
        self.staging_root = self.root / STAGING_DIR
        self.runs_root = self.root / RUNS_DIR
        self.meshes_root = self.root / MESHES_DIR

    # ----------------------------------------------------------- staging
    def new_staging(self, kind: str = "run") -> Path:
        """Create a fresh project-local staging folder on the same volume."""
        self.staging_root.mkdir(parents=True, exist_ok=True)
        path = self.staging_root / _new_id(f"{kind}-")
        path.mkdir(parents=True, exist_ok=False)
        return path

    def list_staging(self) -> list[Path]:
        if not self.staging_root.is_dir():
            return []
        return sorted(p for p in self.staging_root.iterdir() if p.is_dir())

    def discard(self, staging: Path | str) -> None:
        """Delete one staging folder; committed runs are never touched."""
        path = Path(staging)
        try:
            path.resolve().relative_to(self.staging_root.resolve())
        except (OSError, ValueError):
            raise ValueError(f"refusing to delete non-staging path: {path}")
        if path.exists():
            shutil.rmtree(path)

    # ------------------------------------------------------------- commit
    def _destination(self, kind: str, run_id: str) -> Path:
        base = self.runs_root if kind == "run" else self.meshes_root
        return base / run_id

    def _manifest(
        self,
        run_id: str,
        kind: str,
        metadata: dict[str, Any] | None,
        files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "layout_version": LAYOUT_VERSION,
            "run_id": run_id,
            "kind": kind,
            "created": _utc_now(),
            "status": "complete",
        }
        if metadata:
            payload.update(metadata)
        payload["files"] = files
        return payload

    def commit(
        self,
        staging: Path | str,
        *,
        kind: str = "run",
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Path:
        """Publish ``staging`` as a new immutable run/mesh folder.

        The staging folder is moved (same volume) after its manifest has been
        written.  A crash before the rename leaves the old active run untouched;
        a failure after the rename leaves a valid, complete run folder.
        """
        staging = Path(staging)
        if not staging.is_dir():
            raise FileNotFoundError(f"staging folder does not exist: {staging}")
        run_id = str(run_id or _new_id())
        dest = self._destination(kind, run_id)
        if dest.exists():
            raise FileExistsError(f"target already exists: {dest}")

        files = _file_inventory(staging)
        manifest = self._manifest(run_id, kind, metadata, files)
        _atomic_write_json(staging / MANIFEST_NAME, manifest)

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            staging.rename(dest)
        except OSError:
            # A staging folder should live on the same volume.  If a caller
            # passes a foreign path, copy instead of failing the commit.
            shutil.copytree(staging, dest)
            shutil.rmtree(staging)
        return dest

    def publish_copy(
        self,
        source: Path | str,
        *,
        kind: str = "run",
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> Path:
        """Copy a staging folder into this store (used by Save As)."""
        source = Path(source)
        if not source.is_dir():
            raise FileNotFoundError(f"source folder does not exist: {source}")
        run_id = str(run_id or _new_id())
        dest = self._destination(kind, run_id)
        if dest.exists():
            raise FileExistsError(f"target already exists: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, dest)
        files = _file_inventory(dest)
        manifest = self._manifest(run_id, kind, metadata, files)
        _atomic_write_json(dest / MANIFEST_NAME, manifest)
        return dest

    # ------------------------------------------------------------- lookup
    def list_runs(self) -> list[Path]:
        if not self.runs_root.is_dir():
            return []
        return sorted(
            (p for p in self.runs_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )

    def active_run_dir(self, results: dict[str, Any] | None) -> Path | None:
        """Resolve ``results['active_run']`` (relative or legacy absolute)."""
        raw = str((results or {}).get("active_run") or "").strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            # In-memory values are normally absolute.  A raw project JSON may
            # pass a path relative to the JSON's parent (``<base>/runs/...``),
            # while legacy results may be relative to the data folder.
            candidates = [self.root / path, self.root.parent / path]
            for candidate in candidates:
                if candidate.is_dir():
                    return candidate
            return None
        return path if path.is_dir() else None

    @staticmethod
    def relative_to_root(root: Path | str, path: Path | str) -> str:
        """Project-JSON value for a run folder (relative when possible)."""
        root = Path(root)
        path = Path(path)
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except (OSError, ValueError):
            return str(path)
