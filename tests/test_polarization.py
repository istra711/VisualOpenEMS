"""Tests for the derived axial-ratio / phase-center post-processing."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.postproc import polarization


def _payload(e_theta, e_phi, freqs=(2.45e9,), theta=None, phi=None, d_dbi=None):
    e_theta = np.asarray(e_theta, dtype=complex)
    e_phi = np.asarray(e_phi, dtype=complex)
    n_t, n_p = e_theta.shape
    theta = np.linspace(0.0, 180.0, n_t) if theta is None else np.asarray(theta, float)
    phi = np.linspace(0.0, 360.0, n_p) if phi is None else np.asarray(phi, float)
    payload = {
        "frequencies": list(freqs),
        "theta_deg": theta.tolist(),
        "phi_deg": phi.tolist(),
        "E_theta": [
            {"real": np.real(e_theta).tolist(), "imag": np.imag(e_theta).tolist()}
        ],
        "E_phi": [
            {"real": np.real(e_phi).tolist(), "imag": np.imag(e_phi).tolist()}
        ],
    }
    if d_dbi is not None:
        payload["D_dBi"] = [np.asarray(d_dbi, float).tolist()]
    return payload


def test_circular_polarization_is_0db_and_sense_flips():
    e_t = np.ones((3, 3), dtype=complex)
    _ar, ar_db, _tilt, sense = polarization.axial_ratio(e_t, -1j * e_t)
    assert np.allclose(ar_db, 0.0, atol=1e-9)
    assert set(sense.ravel()) == {"RHCP"}  # S3 > 0

    _ar, ar_db, _tilt, sense = polarization.axial_ratio(e_t, +1j * e_t)
    assert np.allclose(ar_db, 0.0, atol=1e-9)
    assert set(sense.ravel()) == {"LHCP"}


def test_linear_polarization_has_infinite_ar():
    e_t = np.ones((3, 3), dtype=complex)
    e_p = np.zeros((3, 3), dtype=complex)
    ar, ar_db, _tilt, sense = polarization.axial_ratio(e_t, e_p)
    assert np.all(np.isinf(ar))
    assert np.all(np.isinf(ar_db))
    assert set(sense.ravel()) == {"linear"}


def test_elliptical_ar_and_tilt():
    # |Et| = 1, |Ep| = 0.5 in phase quadrature -> AR = 2 (6.02 dB), tilt 0
    e_t = np.ones((2, 2), dtype=complex)
    e_p = 0.5j * np.ones((2, 2), dtype=complex)
    ar, ar_db, tilt, _sense = polarization.axial_ratio(e_t, e_p)
    assert np.allclose(ar, 2.0, atol=1e-9)
    assert np.allclose(ar_db, 20.0 * math.log10(2.0), atol=1e-9)
    assert np.allclose(tilt, 0.0, atol=1e-9)

    # 45 deg linear: Et = Ep -> infinite AR, tilt 45 deg
    e_t = np.ones((2, 2), dtype=complex)
    e_p = np.ones((2, 2), dtype=complex)
    _ar, ar_db, tilt, sense = polarization.axial_ratio(e_t, e_p)
    assert np.all(np.isinf(ar_db))
    assert np.allclose(tilt, 45.0, atol=1e-9)
    assert set(sense.ravel()) == {"linear"}


def test_phase_center_recovers_a_known_offset():
    freq = 2.45e9
    k = 2.0 * math.pi * freq / polarization.C0_MM_S
    theta = np.linspace(0.0, 180.0, 181)
    phi = np.array([0.0, 90.0])
    dz_true = 5.0
    amp = np.exp(-((theta - 0.0) / 25.0) ** 2)[:, None]
    phase = -k * dz_true * np.cos(np.radians(theta))[:, None]
    e_t = amp * np.exp(1j * phase) * np.ones((1, 2))
    e_p = np.zeros_like(e_t)
    d_dbi = 20.0 * np.log10(np.maximum(amp, 1e-12)) * np.ones((1, 2))

    payload = _payload(e_t, e_p, theta=theta, phi=phi, d_dbi=d_dbi)
    cut = polarization.phase_center_cut(payload, 0, 0.0, beam_db=10.0)
    assert cut is not None
    assert cut["dz_mm"] == pytest.approx(dz_true, abs=0.05)
    assert cut["rms_deg"] < 0.5
    assert cut["component"] == "theta"

    rows = polarization.phase_center_summary(payload, beam_db=10.0)
    assert rows
    assert rows[0]["dz_mm"] == pytest.approx(dz_true, abs=0.05)
    assert abs(rows[0]["dx_mm"]) < 0.05
    assert abs(rows[0]["dy_mm"]) < 0.05


def test_ar_summary_reads_the_payload():
    e_t = np.ones((5, 3), dtype=complex)
    e_p = -1j * np.ones((5, 3), dtype=complex)
    payload = _payload(e_t, e_p, freqs=(2.4e9, 2.5e9))
    payload["E_theta"].append(payload["E_theta"][0])
    payload["E_phi"].append(payload["E_phi"][0])

    rows = polarization.ar_summary(payload)
    assert len(rows) == 2
    assert rows[0]["ar_db"] == pytest.approx(0.0, abs=1e-9)
    assert rows[0]["sense"] == "RHCP"
    assert rows[0]["frequency"] == pytest.approx(2.4e9)
    assert polarization.ar_bandwidth(rows) == 1.0

    lines = polarization.summary_lines(payload)
    assert lines and "Polarization / phase center" in lines[0]


def test_missing_complex_fields_are_reported_not_invented():
    payload = {"frequencies": [2.45e9], "theta_deg": [0, 1], "phi_deg": [0, 1]}
    assert polarization.ar_summary(payload) == []
    pc = polarization.phase_center_summary(payload)
    assert len(pc) == 1 and math.isnan(pc[0]["dz_mm"])
    assert "no complex E_theta/E_phi" in polarization.summary_lines(payload)[0]
