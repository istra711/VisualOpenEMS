"""Pure tests: NF2FF evaluation frequency resolution + frequency list parsing."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.project import Project
from app.postproc.nf2ff import parse_freq_list, resolve_eval_frequencies


def test_parse_freq_list_units() -> None:
    assert parse_freq_list("2.4 GHz, 2.45 GHz, 2500 MHz") == [2.4e9, 2.45e9, 2.5e9]
    assert parse_freq_list("2.45GHz") == [2.45e9]  # no space before unit
    assert parse_freq_list("2.4e9 2.5e9") == [2.4e9, 2.5e9]  # space-separated
    assert parse_freq_list("2.4e9; 500 kHz; 1 Hz") == [2.4e9, 5e5, 1.0]
    assert parse_freq_list("abc2.4") == []  # junk before number -> reject
    assert parse_freq_list("-3, 0") == []  # non-positive dropped
    assert parse_freq_list("2.5 XX") == []  # unknown unit dropped
    assert parse_freq_list("") == []


def test_resolve_auto_uses_s11_min() -> None:
    p = Project.new_default("t")
    p.nf2ff.freq_mode = "auto"
    s_table = {"freq": [1e9, 2e9, 3e9], "S11": [0.9, 0.1, 0.5]}
    assert resolve_eval_frequencies(p, s_table) == [2e9]


def test_resolve_auto_fallback_target() -> None:
    p = Project.new_default("t")
    p.mesh.target_frequency = 2.45e9
    assert resolve_eval_frequencies(p, None) == [2.45e9]
    # nan-safe: no finite S11 -> keep fallback
    s_table = {"freq": [2e9], "S11": [float("nan")]}
    assert resolve_eval_frequencies(p, s_table) == [2.45e9]


def test_resolve_count_linspace_mesh_range() -> None:
    p = Project.new_default("t")
    p.mesh.start_frequency = 2.0e9
    p.mesh.stop_frequency = 2.8e9
    p.nf2ff.freq_mode = "count"
    p.nf2ff.freq_count = 5
    f = resolve_eval_frequencies(p, None)
    assert len(f) == 5
    assert abs(f[0] - 2.0e9) < 1e-6
    assert abs(f[2] - 2.4e9) < 1e-6
    assert abs(f[-1] - 2.8e9) < 1e-6


def test_resolve_count_one_point_is_target() -> None:
    p = Project.new_default("t")
    p.mesh.start_frequency = 2.0e9
    p.mesh.stop_frequency = 2.8e9
    p.mesh.target_frequency = 2.45e9
    p.nf2ff.freq_mode = "count"
    p.nf2ff.freq_count = 1
    assert resolve_eval_frequencies(p, None) == [2.45e9]


def test_resolve_list_and_empty_fallback() -> None:
    p = Project.new_default("t")
    p.nf2ff.freq_mode = "list"
    p.nf2ff.frequencies = [2.4e9, 2.5e9]
    assert resolve_eval_frequencies(p, None) == [2.4e9, 2.5e9]
    # empty list falls back to auto (target frequency)
    p.nf2ff.frequencies = []
    p.mesh.target_frequency = 2.3e9
    assert resolve_eval_frequencies(p, None) == [2.3e9]


def test_roundtrip_new_fields_in_project_json() -> None:
    import json

    p = Project.new_default("t")
    p.nf2ff.freq_mode = "count"
    p.nf2ff.freq_count = 21
    p2 = Project.from_dict(json.loads(json.dumps(p.to_dict())))
    assert p2.nf2ff.freq_mode == "count"
    assert p2.nf2ff.freq_count == 21
    # old project JSON without the new keys -> defaults
    d = p.to_dict()
    del d["nf2ff"]["freq_mode"]
    del d["nf2ff"]["freq_count"]
    p3 = Project.from_dict(d)
    assert p3.nf2ff.freq_mode == "auto"
    assert p3.nf2ff.freq_count == 11


if __name__ == "__main__":
    for fn in (
        test_parse_freq_list_units,
        test_resolve_auto_uses_s11_min,
        test_resolve_auto_fallback_target,
        test_resolve_count_linspace_mesh_range,
        test_resolve_count_one_point_is_target,
        test_resolve_list_and_empty_fallback,
        test_roundtrip_new_fields_in_project_json,
    ):
        fn()
        print("OK", fn.__name__)
