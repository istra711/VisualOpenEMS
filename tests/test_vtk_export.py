"""Tests for the HDF5 field-dump -> VTK (.vtr) exporter."""

from __future__ import annotations

import numpy as np
import pytest
from pathlib import Path

from app.postproc.vtk_export import (
    dump_series_to_vtr,
    dump_to_vtr,
    export_dumps_to_vtk,
    read_dump_arrays,
    read_dump_series,
)


def _make_dump(path: Path, nx=5, ny=4, nz=3) -> None:
    import h5py

    x = np.linspace(0.0, 10.0, nx)
    y = np.linspace(0.0, 6.0, ny)
    z = np.linspace(0.0, 3.0, nz)
    rng = np.random.default_rng(0)
    re = rng.standard_normal((3, nx, ny, nz)).astype(np.float32)
    im = rng.standard_normal((3, nx, ny, nz)).astype(np.float32)
    with h5py.File(path, "w") as f:
        m = f.create_group("Mesh")
        m.create_dataset("x", data=x)
        m.create_dataset("y", data=y)
        m.create_dataset("z", data=z)
        fd = f.create_group("FieldData").create_group("FD")
        fd.create_dataset("f0_real", data=re)
        fd.create_dataset("f0_imag", data=im)


def test_read_dump_arrays(tmp_path):
    p = tmp_path / "Et-E.h5"
    _make_dump(p)
    x, y, z, field = read_dump_arrays(p)
    assert x.size == 5 and y.size == 4 and z.size == 3
    assert field.shape == (3, 5, 4, 3)
    assert np.iscomplexobj(field)


def test_dump_to_vtr_roundtrip(tmp_path):
    import vtk
    from vtk.util import numpy_support as ns

    p = tmp_path / "Et-E.h5"
    _make_dump(p)
    out = dump_to_vtr(p)
    assert out.exists() and out.suffix == ".vtr"

    r = vtk.vtkXMLRectilinearGridReader()
    r.SetFileName(str(out))
    r.Update()
    grid = r.GetOutput()
    assert grid.GetNumberOfPoints() == 5 * 4 * 3
    pd = grid.GetPointData()
    mag = ns.vtk_to_numpy(pd.GetArray("magnitude"))
    assert mag.shape == (60,)
    assert np.all(mag >= 0)
    vec = ns.vtk_to_numpy(pd.GetArray("field"))
    assert vec.shape == (60, 3)
    # the scalar magnitude is |complex field|; the exported vector is its real part
    _, _, _, field = read_dump_arrays(p)
    exp = np.sqrt(np.sum(np.abs(field) ** 2, axis=0)).ravel(order="F").astype(float)
    assert np.allclose(mag, exp, rtol=1e-4, atol=1e-5)
    re = np.real(field)
    exp_vec = np.column_stack([re[c].ravel(order="F") for c in range(3)])
    assert np.allclose(vec, exp_vec, rtol=1e-4, atol=1e-5)


def test_export_dir_skips_empty(tmp_path):
    import h5py

    sim = tmp_path / "simulation"
    sim.mkdir()
    _make_dump(sim / "Et-E.h5")
    # a degenerate dump (single point) must be skipped, not abort the batch
    with h5py.File(sim / "Et-H.h5", "w") as f:
        m = f.create_group("Mesh")
        for a in "xyz":
            m.create_dataset(a, data=[0.0])
        f.create_group("FieldData").create_group("FD")
    out = export_dumps_to_vtk(sim)
    names = sorted(p.name for p in out)
    assert names == ["Et-E.vtr"]


def test_time_series_export(tmp_path):
    import h5py

    p = tmp_path / "Et-E.h5"
    nx, ny, nz = 5, 4, 3
    with h5py.File(p, "w") as f:
        m = f.create_group("Mesh")
        m.create_dataset("x", data=np.linspace(0, 10, nx))
        m.create_dataset("y", data=np.linspace(0, 6, ny))
        m.create_dataset("z", data=np.linspace(0, 3, nz))
        tdg = f.create_group("FieldData").create_group("TD")
        for i in range(3):
            tdg.create_dataset(f"t{i}", data=np.zeros((3, nx, ny, nz), dtype="float32"))

    _x, _y, _z, steps = read_dump_series(p)
    assert [lab for lab, _f in steps] == ["t0", "t1", "t2"]

    paths, labels = dump_series_to_vtr(p, tmp_path / "out")
    assert labels == ["t0", "t1", "t2"]
    assert len(paths) == 3
    assert (tmp_path / "out" / "Et-E_0000.vtr").exists()
    assert (tmp_path / "out" / "Et-E.series.json").exists()


def test_degenerate_dump_raises(tmp_path):
    import h5py

    p = tmp_path / "empty.h5"
    with h5py.File(p, "w") as f:
        f.create_group("FieldData")
    with pytest.raises(ValueError):
        dump_to_vtr(p)
