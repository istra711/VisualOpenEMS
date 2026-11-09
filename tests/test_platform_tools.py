"""Cross-platform executable discovery helpers."""

from __future__ import annotations

from pathlib import Path

from app.platform_tools import (
    appcsxcad_candidates,
    default_freecad_cmd,
    default_openems_root,
    default_simulation_python,
    find_appcsxcad,
    freecad_cmd_candidates,
    freecad_gui_candidates,
    openems_root_candidates,
)


def test_freecad_candidates_cover_both_naming_conventions():
    cmd_names = {p.name for p in freecad_cmd_candidates()}
    gui_names = {p.name for p in freecad_gui_candidates()}
    assert any("FreeCADCmd" in n for n in cmd_names)
    assert any(n in {"FreeCAD", "freecad", "FreeCAD.exe", "freecad.exe"} for n in gui_names)
    assert isinstance(default_freecad_cmd(), Path)


def test_default_simulation_python_is_an_existing_file():
    python = default_simulation_python()
    assert python.is_file(), python


def test_default_openems_root_is_a_directory_not_a_binary():
    root = default_openems_root()
    assert isinstance(root, Path)
    if any(candidate.is_dir() for candidate in openems_root_candidates()):
        assert root.is_dir(), root
        assert root.suffix.lower() not in {".exe", ".dll"}


def test_openems_root_env_override_wins(tmp_path, monkeypatch):
    root = tmp_path / "openems"
    root.mkdir()
    monkeypatch.setenv("OPENEMS_ROOT", str(root))
    assert default_openems_root() == root


def test_appcsxcad_is_found_in_a_configured_root(tmp_path):
    exe = tmp_path / "AppCSXCAD"
    exe.write_text("", encoding="utf-8")
    assert find_appcsxcad(tmp_path) == exe
    assert exe in appcsxcad_candidates(tmp_path)


def test_appcsxcad_windows_style_binary_is_found(tmp_path):
    exe = tmp_path / "AppCSXCAD.exe"
    exe.write_text("", encoding="utf-8")
    assert find_appcsxcad(tmp_path) == exe
