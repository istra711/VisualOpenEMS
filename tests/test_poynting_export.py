"""Poynting vector export: 0.5 Re(E x H*) on the shared grid."""

from __future__ import annotations

import h5py
import numpy as np

from app.postproc.vtk_export import dump_to_vtr, poynting_vector


def _write_dump(path, field, freq=1e10):
    x = np.array([0.0, 1.0, 2.0])
    y = np.array([0.0, 1.0])
    z = np.array([0.0, 1.0])
    comp, nx, ny, nz = field.shape
    with h5py.File(path, "w") as f:
        mesh = f.create_group("Mesh")
        mesh["x"] = x
        mesh["y"] = y
        mesh["z"] = z
        fd = f.create_group("FieldData").create_group("FD")
        fd.attrs["frequency"] = np.array([freq])
        fd["f0_real"] = np.real(field)
        fd["f0_imag"] = np.imag(field)
    return path


def test_poynting_is_half_re_cross(tmp_path):
    shape = (3, 3, 2, 2)
    e = np.zeros(shape, dtype=complex)
    h = np.zeros(shape, dtype=complex)
    e[0] = 1.0                 # E = x
    h[1] = 1.0j                # H = j*y  ->  E x H* = (0, 0, -j)? no: H* = -j*y
    e_path = _write_dump(tmp_path / "E_tee.h5", e)
    h_path = _write_dump(tmp_path / "H_tee.h5", h)

    s = poynting_vector(e_path, h_path)
    # E x H* with E=(1,0,0), H*=(0,-1j,0) -> (0,0,-1j); Re -> 0
    assert np.allclose(s[2], 0.0)
    assert s.shape == shape

    # H = +y (real) -> E x H* = (0,0,1) -> 0.5
    h2 = np.zeros(shape, dtype=complex)
    h2[1] = 1.0
    h2_path = _write_dump(tmp_path / "H2.h5", h2)
    s2 = poynting_vector(e_path, h2_path)
    assert np.allclose(s2[2], 0.5)


def test_export_adds_poynting_when_h_dump_sits_next_to_e(tmp_path):
    shape = (3, 3, 2, 2)
    e = np.zeros(shape, dtype=complex)
    h = np.zeros(shape, dtype=complex)
    e[0] = 2.0
    h[1] = 2.0
    _write_dump(tmp_path / "E_tee.h5", e)
    _write_dump(tmp_path / "H_tee.h5", h)

    out = dump_to_vtr(tmp_path / "E_tee.h5", tmp_path / "E_tee.vtr")

    import vtk

    reader = vtk.vtkXMLRectilinearGridReader()
    reader.SetFileName(str(out))
    reader.Update()
    pdata = reader.GetOutput().GetPointData()
    names = {pdata.GetArray(i).GetName() for i in range(pdata.GetNumberOfArrays())}
    assert "poynting" in names
    assert "field" in names
    arr = pdata.GetArray("poynting")
    assert arr.GetNumberOfComponents() == 3
    assert np.isclose(arr.GetTuple3(0)[2], 2.0)   # 0.5 * 2 * 2
