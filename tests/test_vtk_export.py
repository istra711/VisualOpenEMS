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
    write_pvd,
    write_series_pvd,
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


def test_reversed_axis_order_is_oriented(tmp_path):
    # openEMS stores the dump spatial axes reversed (z,y,x) vs Mesh/{x,y,z}
    import h5py
    import vtk
    from vtk.util import numpy_support as ns

    nx, ny, nz = 5, 4, 3
    p = tmp_path / "Et-E.h5"
    with h5py.File(p, "w") as f:
        m = f.create_group("Mesh")
        m.create_dataset("x", data=np.linspace(0, 10, nx))
        m.create_dataset("y", data=np.linspace(0, 6, ny))
        m.create_dataset("z", data=np.linspace(0, 3, nz))
        fd = f.create_group("FieldData").create_group("FD")
        rng = np.random.default_rng(7)
        fd.create_dataset("f0_real", data=rng.standard_normal((3, nz, ny, nx)).astype("float32"))
        fd.create_dataset("f0_imag", data=rng.standard_normal((3, nz, ny, nx)).astype("float32"))

    out = dump_to_vtr(p)  # must NOT raise "mesh does not match field"
    r = vtk.vtkXMLRectilinearGridReader()
    r.SetFileName(str(out))
    r.Update()
    g = r.GetOutput()
    assert tuple(g.GetDimensions()) == (nx, ny, nz), g.GetDimensions()
    assert ns.vtk_to_numpy(g.GetPointData().GetArray("magnitude")).shape == (nx * ny * nz,)


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


def test_time_series_numeric_keys(tmp_path):
    """openEMS writes TD steps as 00000000, 00000062, … (not t0)."""
    import h5py

    p = tmp_path / "Et-H.h5"
    nx, ny, nz = 5, 4, 3
    with h5py.File(p, "w") as f:
        m = f.create_group("Mesh")
        m.create_dataset("x", data=np.linspace(0, 10, nx))
        m.create_dataset("y", data=np.linspace(0, 6, ny))
        m.create_dataset("z", data=np.linspace(0, 3, nz))
        tdg = f.create_group("FieldData").create_group("TD")
        for i in (0, 62, 124):
            ds = tdg.create_dataset(f"{i:08d}", data=np.full((3, nx, ny, nz), i, dtype="float32"))
            ds.attrs["time"] = np.array([1.5e-11 * i], dtype="float32")

    _x, _y, _z, steps = read_dump_series(p)
    assert [lab for lab, _f in steps] == ["t0", "t62", "t124"]
    _x, _y, _z, steps, meta = read_dump_series(p, with_meta=True)
    assert [m["time"] for m in meta] == pytest.approx([0.0, 9.3e-10, 1.86e-9], rel=1e-5)
    assert all(m["frequency"] is None for m in meta)

    paths, labels = dump_series_to_vtr(p, tmp_path / "out")
    assert labels == ["t0", "t62", "t124"]
    assert len(paths) == 3
    sidecar = (tmp_path / "out" / "Et-H.series.json").read_text(encoding="utf-8")
    import json

    data = json.loads(sidecar)
    assert len(data["times"]) == 3
    assert data["times"][2] == pytest.approx(1.86e-9, rel=1e-5)
    assert data["frequencies"] == [None, None, None]


def test_fd_frequency_meta(tmp_path):
    """FD dumps carry their frequency as a dataset attribute."""
    import h5py

    p = tmp_path / "Et-E.h5"
    _make_dump(p)
    with h5py.File(p, "a") as f:
        f["FieldData/FD/f0_real"].attrs["frequency"] = np.array([2.23e9], dtype="float32")

    _x, _y, _z, _steps, meta = read_dump_series(p, with_meta=True)
    assert meta[0]["frequency"] == pytest.approx(2.23e9, rel=1e-5)
    assert meta[0]["time"] is None


def test_degenerate_dump_raises(tmp_path):
    import h5py

    p = tmp_path / "empty.h5"
    with h5py.File(p, "w") as f:
        f.create_group("FieldData")
    with pytest.raises(ValueError):
        dump_to_vtr(p)


@pytest.mark.parametrize(
    "name, unit",
    [
        ("Et-E", "V/m"),
        ("Et-E_0007", "V/m"),
        ("Et-H", "A/m"),
        ("E-field", "V/m"),
        ("H-field", "A/m"),
        ("Et", "V/m"),
        ("Ht", "A/m"),
        ("Dt", "As/m²"),
        ("Jt", "A/m²"),
        ("Field", ""),
        ("", ""),
    ],
)
def test_field_unit_from_dump_name(name, unit):
    from app.postproc.vtk_export import field_unit

    assert field_unit(name) == unit


def _make_td_dump(path, steps, nx=5, ny=4, nz=3):
    """TD dump with ``steps`` time samples named like openEMS (00000000, …)."""
    import h5py

    with h5py.File(path, "w") as f:
        m = f.create_group("Mesh")
        m.create_dataset("x", data=np.linspace(0.0, 10.0, nx))
        m.create_dataset("y", data=np.linspace(0.0, 6.0, ny))
        m.create_dataset("z", data=np.linspace(0.0, 3.0, nz))
        tdg = f.create_group("FieldData").create_group("TD")
        for i in range(steps):
            ds = tdg.create_dataset(
                f"{i * 62:08d}", data=np.full((3, nx, ny, nz), i + 1, dtype="float32")
            )
            ds.attrs["time"] = np.array([1e-11 * i], dtype="float32")


def test_count_dump_steps(tmp_path):
    from app.postproc.vtk_export import count_dump_steps

    p = tmp_path / "Et-H.h5"
    _make_td_dump(p, 3)
    assert count_dump_steps(p) == 3
    assert count_dump_steps(tmp_path / "missing.h5") == 0


def test_export_reports_progress_and_finishes(tmp_path):
    sim = tmp_path / "simulation"
    sim.mkdir()
    _make_td_dump(sim / "Et-H.h5", 3)
    calls: list[tuple[int, int, str]] = []

    out = export_dumps_to_vtk(
        sim, per_step=True, progress=lambda d, t, text: calls.append((d, t, text))
    )

    assert len(out) == 4  # 1 static grid + 3 steps
    assert calls, "progress must be reported"
    assert calls[-1][0] == calls[-1][1] == 4  # done reaches the announced total
    assert all(t == 4 for _d, t, _text in calls)


def test_export_is_incremental(tmp_path):
    sim = tmp_path / "simulation"
    sim.mkdir()
    _make_td_dump(sim / "Et-H.h5", 2)
    first = export_dumps_to_vtk(sim, per_step=True)
    mtimes = {p: p.stat().st_mtime_ns for p in first}
    assert len(first) == 3

    calls: list[str] = []
    second = export_dumps_to_vtk(
        sim, per_step=True, progress=lambda _d, _t, text: calls.append(text)
    )

    assert sorted(p.name for p in second) == sorted(p.name for p in first)
    assert all(p.stat().st_mtime_ns == mtimes[p] for p in first)  # nothing rewritten
    assert all("up to date" in text for text in calls)


def test_series_stride_keeps_every_nth_step(tmp_path):
    import json

    p = tmp_path / "Et-H.h5"
    _make_td_dump(p, 6)
    paths, labels = dump_series_to_vtr(p, tmp_path / "out", step_stride=2)

    assert labels == ["t0", "t124", "t248"]
    assert [q.name for q in paths] == ["Et-H_0000.vtr", "Et-H_0001.vtr", "Et-H_0002.vtr"]
    data = json.loads((tmp_path / "out" / "Et-H.series.json").read_text(encoding="utf-8"))
    assert data["stride"] == 2
    assert data["times"] == pytest.approx([0.0, 2e-11, 4e-11], rel=1e-5)


def test_export_stride_is_incremental_and_stride_aware(tmp_path):
    sim = tmp_path / "simulation"
    sim.mkdir()
    _make_td_dump(sim / "Et-H.h5", 6)

    first = export_dumps_to_vtk(sim, per_step=True, step_stride=2)
    assert len(first) == 4  # 1 static grid + 3 steps
    mtimes = {p: p.stat().st_mtime_ns for p in first}

    calls: list[str] = []
    again = export_dumps_to_vtk(
        sim, per_step=True, step_stride=2,
        progress=lambda _d, _t, text: calls.append(text),
    )
    assert sorted(p.name for p in again) == sorted(p.name for p in first)
    assert all(p.stat().st_mtime_ns == mtimes[p] for p in first)
    assert all("up to date" in text for text in calls)

    # a different stride must re-export the whole series (sidecar records it)
    full = export_dumps_to_vtk(sim, per_step=True, step_stride=1)
    step_files = sorted(q.name for q in full if q.name != "Et-H.vtr")
    assert len(full) == 7  # 1 static grid + 6 steps
    assert step_files == [f"Et-H_{i:04d}.vtr" for i in range(6)]


def test_series_export_removes_stale_steps(tmp_path):
    import json
    import os

    sim = tmp_path / "simulation"
    sim.mkdir()
    h5 = sim / "Et-H.h5"
    _make_td_dump(h5, 3)
    export_dumps_to_vtk(sim, per_step=True)
    vtk_dir = tmp_path / "vtk"
    assert (vtk_dir / "Et-H_0002.vtr").exists()

    # re-run with fewer stored steps -> the old tail must not linger in the slider
    _make_td_dump(h5, 2)
    future = h5.stat().st_mtime + 5.0
    os.utime(h5, (future, future))
    export_dumps_to_vtk(sim, per_step=True)

    assert not (vtk_dir / "Et-H_0002.vtr").exists()
    data = json.loads((vtk_dir / "Et-H.series.json").read_text(encoding="utf-8"))
    assert data["files"] == ["Et-H_0000.vtr", "Et-H_0001.vtr"]


def test_series_cancel_leaves_no_trusted_sidecar(tmp_path):
    sim = tmp_path / "simulation"
    sim.mkdir()
    h5 = sim / "Et-H.h5"
    _make_td_dump(h5, 3)
    vtk_dir = tmp_path / "vtk"

    checks = {"n": 0}

    def stop() -> bool:
        checks["n"] += 1
        return checks["n"] > 1  # one step written, then cancel

    paths, labels = dump_series_to_vtr(h5, vtk_dir, should_stop=stop)
    assert len(paths) == 1 and labels == ["t0"]
    assert not (vtk_dir / "Et-H.series.json").exists()

    # a later full run regenerates the complete series (no half-trusted state)
    paths, labels = dump_series_to_vtr(h5, vtk_dir)
    assert labels == ["t0", "t62", "t124"]
    assert (vtk_dir / "Et-H.series.json").exists()


# --- ParaView .pvd collection ------------------------------------------------


def test_write_pvd_is_valid_collection(tmp_path):
    out = write_pvd(
        tmp_path / "x.pvd",
        [(0.0, "a_0000.vtr"), (1.5e-9, "a 0001.vtr")],
    )
    text = out.read_text(encoding="utf-8")
    assert 'type="Collection"' in text
    assert 'timestep="0"' in text
    assert 'timestep="1.5e-09"' in text
    assert 'file="a_0000.vtr"' in text
    # file names are escaped, not injected into the XML
    assert 'file="a 0001.vtr"' in text


def test_write_pvd_escapes_file_names(tmp_path):
    out = write_pvd(tmp_path / "x.pvd", [(0.0, 'a&b<c>.vtr')])
    text = out.read_text(encoding="utf-8")
    assert "a&amp;b&lt;c&gt;.vtr" in text


def test_series_writes_pvd_with_physical_times(tmp_path):
    h5 = tmp_path / "Et-E.h5"
    _make_td_dump(h5, 3)
    out = tmp_path / "out"
    dump_series_to_vtr(h5, out)

    pvd = out / "Et-E.pvd"
    assert pvd.exists()
    text = pvd.read_text(encoding="utf-8")
    # one DataSet per written step, in order
    assert text.count("<DataSet") == 3
    assert "Et-E_0000.vtr" in text and "Et-E_0002.vtr" in text
    # timesteps come from the dump's time attribute, not the step index
    assert 'timestep="0"' in text
    assert '[0, 0, 0]' not in text


def test_series_pvd_rebuilt_for_old_series(tmp_path):
    """A series exported before .pvd existed gets its collection on re-export."""
    h5 = tmp_path / "Et-H.h5"
    _make_td_dump(h5, 2)
    out = tmp_path / "out"
    dump_series_to_vtr(h5, out)
    pvd = out / "Et-H.pvd"
    assert pvd.exists()
    pvd.unlink()

    # reuse path: series is up to date, only the collection is missing
    sim = tmp_path / "simulation"
    sim.mkdir()
    (sim / "Et-H.h5").write_bytes(h5.read_bytes())
    import os

    now = (out / "Et-H.series.json").stat().st_mtime
    os.utime(sim / "Et-H.h5", (now - 60, now - 60))  # sidecar stays newer
    export_dumps_to_vtk(sim, out, per_step=True)
    assert pvd.exists()
    assert pvd.read_text(encoding="utf-8").count("<DataSet") == 2


def test_write_series_pvd_without_sidecar_returns_none(tmp_path):
    assert write_series_pvd(tmp_path, tmp_path / "nope.h5") is None
