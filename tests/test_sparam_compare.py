"""Pure tests for the S-parameter reference/compare helpers."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import skrf as rf

from app.postproc import sparams as sp
from app.postproc.sparams import (
    compare_networks,
    mapped_reference_key,
    normalize_port_map,
    write_comparison_csv,
    write_comparison_report,
)


def _network(freqs, values, z0=50.0):
    s = np.asarray(values, dtype=complex).reshape(len(freqs), 1, 1)
    return rf.Network(f=freqs, s=s, z0=z0)


def _multiport(freqs, nports, values):
    s = np.zeros((len(freqs), nports, nports), dtype=complex)
    for (i, j), value in values.items():
        s[:, i, j] = value
    return rf.Network(f=freqs, s=s, z0=50.0)


def test_port_map_and_mapped_reference_key():
    assert normalize_port_map(None, 2, 4) == [0, 1]
    assert normalize_port_map([2, 4], 2, 4) == [1, 3]
    assert normalize_port_map([0, 9], 2, 4) == [-1, -1]
    assert mapped_reference_key("S21", [1, 3]) == "S42"
    assert mapped_reference_key("S11", [1, -1]) == "S22"
    assert mapped_reference_key("S22", [1, -1]) is None
    assert mapped_reference_key("not-a-port", [0, 1]) is None


def test_compare_networks_uses_port_map_and_reports_metrics():
    freqs = np.linspace(1e9, 3e9, 101)
    current = _multiport(
        freqs,
        2,
        {(0, 0): np.full(101, 0.1), (1, 0): np.full(101, 0.5)},
    )
    reference = _multiport(
        freqs,
        4,
        {(3, 1): np.full(101, 0.4), (1, 1): np.full(101, 0.2)},
    )

    result = compare_networks(current, reference, port_map=[2, 4])
    pair = next(item for item in result["pairs"] if item["key"] == "S21")

    assert result["port_map"] == [1, 3]
    assert pair["ref_key"] == "S42"
    assert pair["points"] == 101
    assert abs(pair["max_abs_db"] - 20 * np.log10(0.5 / 0.4)) < 1e-9
    assert abs(pair["rms_db"] - 20 * np.log10(0.5 / 0.4)) < 1e-9
    assert pair["max_phase_deg"] == 0.0


def test_comparison_csv_and_report_include_mapping(tmp_path):
    freqs = np.linspace(1e9, 2e9, 11)
    current = _multiport(
        freqs,
        2,
        {(0, 0): np.full(11, 0.1), (1, 0): np.full(11, 0.5)},
    )
    reference = _multiport(freqs, 4, {(3, 1): np.full(11, 0.4)})
    meta = {
        "current": "current.s2p",
        "reference": "run-1",
        "reference_path": "run-1/sparams/sparams.s4p",
        "reference_run": "run-1",
        "reference_status": "complete",
    }

    csv_path = write_comparison_csv(
        current,
        reference,
        tmp_path / "comparison.csv",
        port_map=[2, 4],
        meta=meta,
    )
    text = csv_path.read_text(encoding="utf-8")
    assert "# port_map=S1->S2, S2->S4" in text
    assert "freq_Hz,freq_GHz,Sij,ref_Sij" in text
    assert "S21,S42" in text

    report_path = write_comparison_report(
        current,
        reference,
        tmp_path / "comparison.txt",
        port_map=[2, 4],
        meta=meta,
    )
    report = report_path.read_text(encoding="utf-8")
    assert "Port map: S1->S2, S2->S4" in report
    assert "Reference run: run-1" in report
    assert "S21" in report and "S42" in report


def test_align_complex_trace_uses_overlap_only():
    ref_f = np.array([1e9, 2e9, 3e9])
    ref_s = np.array([1.0 + 0.0j, 2.0 + 1.0j, 3.0 + 2.0j])
    target = np.array([0.5e9, 1.0e9, 1.5e9, 3.0e9, 4.0e9])

    out = sp.align_complex_trace(ref_f, ref_s, target)

    assert np.isnan(out[0]) and np.isnan(out[-1])
    assert out[1] == 1.0 + 0.0j
    assert out[2] == 1.5 + 0.5j
    assert out[3] == 3.0 + 2.0j


def test_align_complex_trace_does_not_bridge_gaps():
    ref_f = np.array([1e9, 2e9, 3e9])
    ref_s = np.array([1.0 + 0.0j, np.nan + 0.0j, 3.0 + 0.0j])

    out = sp.align_complex_trace(ref_f, ref_s, np.array([1.5e9, 2.5e9]))

    assert np.isnan(out).all()


def test_align_network_renormalizes_to_current_impedance():
    freqs = np.linspace(1e9, 3e9, 21)
    values = np.exp(-1j * 2 * np.pi * freqs / 1e9)
    ref = _network(freqs, values, z0=75.0)
    expected = sp.renormalize_network(ref, 50.0)

    out = sp.align_network_to_frequencies(ref, freqs, z0=50.0)

    assert out.shape == (len(freqs), 1, 1)
    np.testing.assert_allclose(out[:, 0, 0], expected.s[:, 0, 0])


def test_network_z0_falls_back_to_50():
    freqs = np.linspace(1e9, 2e9, 3)
    net = _network(freqs, np.ones(3), z0=73.0)
    assert sp.network_z0(net) == 73.0
    assert sp.network_z0(object()) == 50.0


def test_project_reference_descriptor_roundtrip(tmp_path):
    from app.models.project import Project

    ref_path = tmp_path / "runs" / "20260101-120000-abcd" / "sparams" / "sparams.s2p"
    ref_path.parent.mkdir(parents=True)
    ref_path.write_text("# Hz S RI R 50\n1 0 0 0 0\n", encoding="utf-8")

    project = Project.new_default("reference-project")
    project.sparam_reference = {
        "path": str(ref_path),
        "label": "reference-project / 20260101-120000-abcd",
        "kind": "run",
        "run_id": "20260101-120000-abcd",
        "status": "complete",
    }
    project_path = tmp_path / "reference-project.fcsim.json"
    project.save(project_path)

    restored = Project.load(project_path)
    assert restored.sparam_reference is not None
    assert restored.sparam_reference["path"] == str(ref_path)
    assert restored.sparam_reference["run_id"] == "20260101-120000-abcd"
    assert restored.sparam_reference["label"].startswith("reference-project / ")


def test_export_json_records_z0_and_loader_ignores_it(tmp_path):
    freqs = np.linspace(1e9, 2e9, 3)
    table = {"freq": freqs, "S11": np.full(3, 0.1 + 0.2j)}
    written = sp.export_sparams(table, tmp_path, basename="sparams", z0=73.0)

    payload = json.loads(Path(written["json"]).read_text(encoding="utf-8"))
    assert payload["z0"] == 73.0

    loaded = sp.load_sparams_json(written["json"])
    assert "z0" not in loaded
    np.testing.assert_allclose(loaded["S11"], table["S11"])
