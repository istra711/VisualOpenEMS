"""Transactional project storage: staging, immutable runs, Save As copies."""

from __future__ import annotations

import json
from pathlib import Path

from app.models.project import Project
from app.storage.run_store import ProjectStore


def _write_run(root: Path) -> None:
    (root / "sparams").mkdir(parents=True, exist_ok=True)
    (root / "sparams" / "sparams.json").write_text("{}", encoding="utf-8")
    (root / "csx.xml").write_text("<xml/>", encoding="utf-8")
    (root / "simulation").mkdir(exist_ok=True)


def test_commit_publishes_immutable_run(tmp_path):
    root = tmp_path / "MyProject"
    store = ProjectStore(root)
    staging = store.new_staging("run")
    _write_run(staging)

    run = store.commit(staging, metadata={"project_id": "abc", "status": "complete"})

    assert run == root / "runs" / run.name
    assert run.is_dir()
    assert not staging.exists(), "staging must be moved, not copied"
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == run.name
    assert manifest["status"] == "complete"
    assert any(f["path"] == "sparams/sparams.json" for f in manifest["files"])
    assert store.list_runs() == [run]


def test_discard_never_touches_committed_run(tmp_path):
    root = tmp_path / "MyProject"
    store = ProjectStore(root)
    staging = store.new_staging("run")
    _write_run(staging)
    run = store.commit(staging)
    other = store.new_staging("run")
    (other / "partial.txt").write_text("x", encoding="utf-8")

    store.discard(other)

    assert not other.exists()
    assert run.is_dir()
    assert (run / "sparams" / "sparams.json").is_file()


def test_discard_refuses_non_staging_path(tmp_path):
    store = ProjectStore(tmp_path / "MyProject")
    protected = tmp_path / "keep.txt"
    protected.write_text("keep", encoding="utf-8")
    try:
        store.discard(protected)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("discard accepted a non-staging path")
    assert protected.exists()


def test_publish_copy_leaves_source_for_save_as(tmp_path):
    old_root = tmp_path / "Old"
    new_root = tmp_path / "New"
    old = ProjectStore(old_root)
    staging = old.new_staging("run")
    _write_run(staging)

    new = ProjectStore(new_root)
    copied = new.publish_copy(staging, metadata={"project_id": "new"})

    assert copied.is_dir()
    assert (copied / "sparams" / "sparams.json").is_file()
    assert staging.is_dir(), "Save As must not destroy the old pending staging"
    manifest = json.loads((copied / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["project_id"] == "new"


def test_active_run_pointer_relative_and_absolute(tmp_path):
    root = tmp_path / "MyProject"
    store = ProjectStore(root)
    staging = store.new_staging("run")
    _write_run(staging)
    run = store.commit(staging)
    rel = store.relative_to_root(root, run)

    assert store.active_run_dir({"active_run": rel}) == run
    assert store.active_run_dir({"active_run": str(run)}) == run
    assert store.active_run_dir({}) is None


def test_project_save_is_atomic_and_keeps_project_id(tmp_path):
    path = tmp_path / "project.fcsim.json"
    project = Project.new_default("atomic")
    project.project_id = "stable-id"
    project.save(path)

    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["project_id"] == "stable-id"
    assert first["storage_layout"] == 2

    # A second save must leave exactly one valid file (no .tmp leftovers).
    project.name = "renamed"
    project.save(path)
    assert [p.name for p in tmp_path.glob("*.tmp")] == []
    second = json.loads(path.read_text(encoding="utf-8"))
    assert second["name"] == "renamed"
    assert second["project_id"] == "stable-id"
