"""Excitation waveform helpers (project.mesh.excitation) — Qt-free tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.project import MeshSpec, Project
from app.simulation import excitation as exc


def _mesh(**kw):
    base = dict(
        start_frequency=1e9,
        stop_frequency=3e9,
        excitation="gauss",
        excitation_file="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeFdtd:
    def __init__(self):
        self.calls = []

    def SetGaussExcite(self, f0, fc):
        self.calls.append(("gauss", f0, fc))

    def SetSinusExcite(self, f0):
        self.calls.append(("sinus", f0))

    def SetStepExcite(self, f_max):
        self.calls.append(("step", f_max))

    def SetDiracExcite(self, f_max):
        self.calls.append(("dirac", f_max))

    def SetCustomExcite(self, times, values):
        self.calls.append(("custom", tuple(times), tuple(values)))


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "gauss"),
        ("", "gauss"),
        ("gauss", "gauss"),
        ("SINUS", "sinus"),
        (" Step ", "step"),
        ("dirac", "dirac"),
        ("bogus", "gauss"),
    ],
)
def test_normalize_kind(raw, expected):
    assert exc.normalize_kind(raw) == expected


def test_meshspec_defaults_and_roundtrip(tmp_path):
    p = Project.new_default("exc")
    assert p.mesh.excitation == "gauss"
    assert p.mesh.excitation_file == ""
    p.mesh.excitation = "sinus"
    path = p.save(tmp_path / "exc.json")
    q = Project.load(path)
    assert q.mesh.excitation == "sinus"

    # old project files without the keys load with the Gauss default
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    data["mesh"].pop("excitation", None)
    data["mesh"].pop("excitation_file", None)
    (tmp_path / "old.json").write_text(json.dumps(data), encoding="utf-8")
    old = Project.load(tmp_path / "old.json")
    assert old.mesh.excitation == "gauss"
    assert old.mesh.excitation_file == ""
    assert isinstance(old.mesh, MeshSpec)


def test_apply_gauss_is_default_and_uses_f0_fc():
    f = _FakeFdtd()
    kind = exc.apply_excitation(f, _mesh(), f0=2.4e9, fc=0.5e9)
    assert kind == "gauss"
    assert f.calls == [("gauss", 2.4e9, 0.5e9)]


def test_apply_sinus_step_dirac():
    for kind, expected in (
        ("sinus", ("sinus", 2.4e9)),
        ("step", ("step", 3e9)),
        ("dirac", ("dirac", 3e9)),
    ):
        f = _FakeFdtd()
        used = exc.apply_excitation(f, _mesh(excitation=kind), f0=2.4e9, fc=0.5e9)
        assert used == kind
        assert f.calls == [expected]


def test_apply_custom_reads_csv(tmp_path, monkeypatch):
    csv = tmp_path / "wave.csv"
    csv.write_text(
        "# custom\n"
        "time,amplitude\n"
        "0,0\n"
        "1e-10,1\n"
        "2e-10,0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(exc, "custom_supported", lambda: True)
    f = _FakeFdtd()
    used = exc.apply_excitation(
        f, _mesh(excitation="custom", excitation_file=str(csv)), f0=2.4e9, fc=0.5e9
    )
    assert used == "custom"
    assert f.calls == [("custom", (0.0, 1e-10, 2e-10), (0.0, 1.0, 0.0))]


def test_apply_custom_falls_back_to_gauss_without_support(monkeypatch):
    monkeypatch.setattr(exc, "custom_supported", lambda: False)
    f = _FakeFdtd()
    used = exc.apply_excitation(
        f, _mesh(excitation="custom", excitation_file="missing.csv"), f0=2.4e9, fc=0.5e9
    )
    assert used == "gauss"
    assert f.calls == [("gauss", 2.4e9, 0.5e9)]


def test_read_custom_waveform_semicolon_and_comments(tmp_path):
    p = tmp_path / "w.txt"
    p.write_text("% comment\n0; 0\n1e-9; 2\n2e-9; 0\n", encoding="utf-8")
    times, values = exc.read_custom_waveform(p)
    assert times == [0.0, 1e-9, 2e-9]
    assert values == [0.0, 2.0, 0.0]


@pytest.mark.parametrize(
    "text,fragment",
    [
        ("0,0\n", "at least 2"),
        ("1e-9,0\n2e-9,1\n", "start at 0"),
        ("0,0\n2e-9,1\n1e-9,0\n", "strictly increasing"),
        ("0,0\n1e-9,abc\n", "expected two numbers"),
    ],
)
def test_read_custom_waveform_errors(tmp_path, text, fragment):
    p = tmp_path / "bad.csv"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=fragment):
        exc.read_custom_waveform(p)


def test_describe_excitation_mentions_kind():
    assert "Sinus" in exc.describe_excitation(_mesh(excitation="sinus"), 2.4e9, 0.5e9)
    assert "Step" in exc.describe_excitation(_mesh(excitation="step"), 2.4e9, 0.5e9)
    assert "Gauss" in exc.describe_excitation(_mesh(), 2.4e9, 0.5e9)


def test_run_script_template_uses_excitation_helper():
    from app.simulation.script_gen import SCRIPT_TEMPLATE

    assert "from app.simulation.excitation import apply_excitation" in SCRIPT_TEMPLATE
    assert "apply_excitation(FDTD, mesh" in SCRIPT_TEMPLATE
    assert "FDTD.SetGaussExcite" not in SCRIPT_TEMPLATE
