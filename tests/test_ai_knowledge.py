"""Unit tests for the local, read-only AI knowledge layer (no network)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai.context import build_prompt, build_run_context
from app.ai.knowledge import (
    MAX_FILE_BYTES,
    KnowledgeRoot,
    build_knowledge_bundle,
    detect_versions,
    discover_roots,
    invalidate_knowledge_cache,
    knowledge_cache_info,
)
from app.ai.types import KnowledgeBundle, KnowledgeHit, RunContext
from app.models.project import Project


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_knowledge_cache()
    yield
    invalidate_knowledge_cache()


def _tutorial_tree(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "patch_tutorial.py").write_text(
        '"""Patch antenna tutorial.\n\n'
        "Shows AddLumpedPort, a PML boundary and the mesh setup.\n"
        '"""\n\n'
        "def build():\n"
        "    FDTD.SetBoundaryCond(['PML_8', 'PML_8', 'MUR', 'MUR', 'PEC', 'MUR'])\n"
        "    port = FDTD.AddLumpedPort(1, 50, start, stop, 'z', 1.0)\n"
        "    CSX.GetGrid().SetDeltaUnit(1e-3)\n",
        encoding="utf-8",
    )
    (root / "meshing.md").write_text(
        "# Meshing and convergence\n\n"
        "The end criteria stops the FDTD run when the residual energy is small.\n"
        "A finer mesh increases the number of timesteps.\n",
        encoding="utf-8",
    )
    return root


def _roots(path: Path) -> list[KnowledgeRoot]:
    return [KnowledgeRoot("TestDocs", path, "source", 1.2, (".py", ".md"))]


def test_discover_roots_includes_extra_folder(tmp_path):
    extra = tmp_path / "extra-notes"
    extra.mkdir()
    settings = SimpleNamespace(
        openems_root=str(tmp_path / "missing-openems"),
        simulation_python="",
        ai_knowledge_paths=str(extra),
    )
    roots = discover_roots(settings)
    extras = [
        root for root in roots if root.kind == "extra" and root.path == extra
    ]
    assert extras


def test_bundle_returns_cited_local_excerpts(tmp_path):
    root = _tutorial_tree(tmp_path)
    bundle = build_knowledge_bundle(
        "How do I use AddLumpedPort with a PML boundary?",
        roots=_roots(root),
        max_chars=4000,
        force=True,
    )

    assert bundle.enabled
    assert bundle.indexed_files == 2
    assert bundle.indexed_chunks >= 2
    assert bundle.hits
    assert bundle.hits[0].id == "K1"
    assert all(hit.id.startswith("K") for hit in bundle.hits)
    assert all(hit.root == "TestDocs" for hit in bundle.hits)
    assert "read-only" in bundle.summary.lower()
    combined = "\n".join(hit.text for hit in bundle.hits)
    assert "AddLumpedPort" in combined or "PML" in combined
    assert sum(len(hit.text) for hit in bundle.hits) <= 4000


def test_identical_relative_paths_in_two_roots_are_kept(tmp_path):
    roots = []
    for name in ("a", "b"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "README.md").write_text(
            "# PML boundary\n\nThe PML boundary absorbs outgoing waves.\n",
            encoding="utf-8",
        )
        roots.append(
            KnowledgeRoot(name.upper(), folder, "docs", 1.0, (".md",))
        )
    bundle = build_knowledge_bundle(
        "PML boundary", roots=roots, force=True
    )
    assert {hit.root for hit in bundle.hits} == {"A", "B"}


def test_disabled_bundle_and_character_budget(tmp_path):
    root = _tutorial_tree(tmp_path)
    disabled = build_knowledge_bundle(
        "PML", roots=_roots(root), enabled=False
    )
    assert not disabled.enabled
    assert disabled.hits == []
    assert "disabled" in disabled.summary.lower()

    tight = build_knowledge_bundle(
        "PML boundary AddLumpedPort mesh", roots=_roots(root), max_chars=1200
    )
    assert tight.hits
    assert sum(len(hit.text) for hit in tight.hits) <= 1200


def test_scan_skips_binary_large_and_cache_directories(tmp_path):
    root = tmp_path / "scan"
    root.mkdir()
    (root / "good.md").write_text(
        "# PML boundary\n\nThe PML absorbs outgoing waves.\n", encoding="utf-8"
    )
    (root / ".hidden.py").write_text("PML = 8\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "secret.py").write_text("PML = 8\n", encoding="utf-8")
    (root / "assets").mkdir()
    (root / "assets" / "notes.md").write_text("PML = 8\n", encoding="utf-8")
    (root / "binary.py").write_bytes(b"\x00\x01\x02PML")
    (root / "large.py").write_text("PML\n" * (MAX_FILE_BYTES // 3), encoding="utf-8")
    (root / "image.png").write_bytes(b"\x89PNG\r\n")

    bundle = build_knowledge_bundle(
        "PML boundary", roots=_roots(root), force=True
    )
    assert bundle.indexed_files == 1
    assert len(bundle.hits) == 1
    assert bundle.hits[0].path == "good.md"


def test_cache_is_reused_and_invalidated(tmp_path):
    root = _tutorial_tree(tmp_path)
    build_knowledge_bundle("PML", roots=_roots(root), force=True)
    info = knowledge_cache_info()
    assert info["cached"] is True
    assert info["files"] == 2

    build_knowledge_bundle("PML", roots=_roots(root))
    assert knowledge_cache_info()["files"] == 2
    invalidate_knowledge_cache()
    assert knowledge_cache_info()["cached"] is False


def test_detect_versions_from_metadata_and_wheels(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    (install / "openEMS-0.0.99-cp311-win_amd64.whl").write_bytes(b"")

    site_packages = tmp_path / "site-packages"
    for package, version in (("openEMS", "0.0.99"), ("CSXCAD", "0.6.9")):
        dist = site_packages / f"{package}-{version}.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {package}\nVersion: {version}\n",
            encoding="utf-8",
        )

    app_root = tmp_path / "app"
    third_party = app_root / "third_party" / "easyMesh4openEMS"
    third_party.mkdir(parents=True)
    (third_party / "pyproject.toml").write_text(
        '[project]\nname = "easymesh4openems"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (app_root / "app").mkdir()
    (app_root / "app" / "__init__.py").write_text(
        '__version__ = "9.9.9"\n', encoding="utf-8"
    )

    versions = detect_versions(
        openems_root=install,
        site_packages=site_packages,
        app_root=app_root,
    )
    assert versions["openEMS"] == "0.0.99"
    assert versions["CSXCAD"] == "0.6.9"
    assert versions["easyMesh"] == "1.2.3"
    assert versions["VisualopenEMS"] == "9.9.9"


def test_german_domain_term_maps_to_english_source(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "boundary.md").write_text(
        "# Boundary conditions\n\nThe PML boundary absorbs outgoing waves.\n",
        encoding="utf-8",
    )
    bundle = build_knowledge_bundle(
        "Wie funktioniert eine Randbedingung?",
        roots=_roots(root),
        force=True,
    )
    assert bundle.hits
    assert "PML" in bundle.hits[0].text


def _knowledge_bundle() -> KnowledgeBundle:
    return KnowledgeBundle(
        summary="LOCAL KNOWLEDGE (read-only): 1 excerpt.",
        hits=[
            KnowledgeHit(
                id="K1",
                title="Boundary conditions",
                path="boundary.md",
                line_start=1,
                line_end=8,
                text="PML_8 absorbs outgoing waves at the domain boundary.",
                root="openEMS",
                kind="docs",
                score=9.5,
            )
        ],
        indexed_files=1,
        indexed_chunks=1,
    )


def test_run_context_and_prompt_include_knowledge_citations():
    project = Project.new_default("knowledge-test")
    context = build_run_context(
        project,
        project.primitives or [],
        None,
        None,
        knowledge=_knowledge_bundle(),
    )
    assert context.knowledge and context.knowledge[0].id == "K1"
    prompt = build_prompt(
        "How do I set the PML boundary?", context, language="en"
    )
    assert "[K1]" in prompt
    assert "LOCAL KNOWLEDGE (read-only)" in prompt
    assert "boundary.md" in prompt
    assert "cite" in prompt.lower()


def test_prompt_truncation_keeps_knowledge_anchors():
    huge = "project line\n" * 3000
    context = RunContext(
        project_digest=huge,
        knowledge_summary="LOCAL KNOWLEDGE (read-only): 1 excerpt.",
        knowledge=_knowledge_bundle().hits,
        redaction_note="Paths are redacted.",
    )
    prompt = build_prompt("Why did S11 fail?", context, max_chars=6000)
    assert len(prompt) <= 6000
    assert "[K1]" in prompt
    assert "PML_8" in prompt
    assert "context trimmed" in prompt
