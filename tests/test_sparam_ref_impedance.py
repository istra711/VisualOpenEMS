"""Pure tests for the S-parameter reference impedance (app.postproc.sparams).

A run must reference S11 to the port's own resistance (the AddLumpedPort R), so
a 73 Ω port yields a 73 Ω-referenced S11 instead of being forced to 50 Ω.
"""

from __future__ import annotations

import numpy as np

from app.postproc import sparams as sp


class _FakePort:
    def __init__(self, number: int, inc: np.ndarray, ref: np.ndarray) -> None:
        self.number = number
        self._inc = inc
        self._ref = ref
        self.calls: list = []

    def CalcPort(self, datadir, freqs, ref_impedance=None, **kw):  # noqa: N802
        self.calls.append(ref_impedance)
        self.uf_inc = self._inc
        self.uf_ref = self._ref


class _FakeDef:
    def __init__(self, name: str, excite: bool) -> None:
        self.name = name
        self.excite_port = excite


def _table():
    freqs = np.linspace(1.0e9, 2.0e9, 3)
    inc = np.ones(3, dtype=complex)
    ref = np.full(3, 0.25 + 0.1j, dtype=complex)
    port = _FakePort(1, inc, ref)
    return freqs, port, inc, ref


def test_default_lets_openems_use_port_own_resistance(tmp_path):
    freqs, port, inc, ref = _table()
    table = sp.compute_sparams_from_ports(
        tmp_path, [(_FakeDef("p1", True), port)], freqs
    )
    # no explicit reference -> CalcPort called without ref_impedance
    assert port.calls == [None]
    np.testing.assert_allclose(table["S11"], ref / inc)


def test_explicit_reference_is_forwarded(tmp_path):
    freqs, port, inc, ref = _table()
    sp.compute_sparams_from_ports(
        tmp_path, [(_FakeDef("p1", True), port)], freqs, ref_impedance=73.0
    )
    assert port.calls == [73.0]


def test_export_sparams_accepts_reference_z0(tmp_path):
    freqs, port, inc, ref = _table()
    table = sp.compute_sparams_from_ports(
        tmp_path, [(_FakeDef("p1", True), port)], freqs
    )
    written = sp.export_sparams(table, tmp_path / "sparams", basename="sparams", z0=73.0)
    assert "json" in written and written["json"].exists()
