"""Export openEMS HDF5 field dumps to VTK rectilinear grids (``.vtr``).

openEMS stores a dump as ``Mesh/{x,y,z}`` (the grid coordinates) plus the field
in ``FieldData/FD/f<N>_real|_imag`` (frequency domain) or ``FieldData/TD/t<N>``
(time domain), with the xyz components as the leading axis: ``(3, nx, ny, nz)``.

The exporter writes one ``.vtr`` per dump with the (real) vector field and its
magnitude as point data, so ParaView / the built-in VTK viewer can show it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from app.postproc.fields import _field_from_group, _td_keys


def read_dump_arrays(h5_path: Path | str):
    """Return ``(x, y, z, field)`` from an openEMS dump HDF5.

    ``field`` keeps the openEMS layout ``(3, nx, ny, nz)`` (real or complex) or
    is a plain ``(nx, ny, nz)`` array, and is ``None`` when the dump is empty.
    """
    import h5py

    x = y = z = None
    field: Any = None
    with h5py.File(h5_path, "r") as f:
        mesh = f["Mesh"] if "Mesh" in f else None
        if mesh is not None:
            x = np.asarray(mesh["x"], dtype=float).ravel()
            y = np.asarray(mesh["y"], dtype=float).ravel()
            z = np.asarray(mesh["z"], dtype=float).ravel()
        root = f["FieldData"] if "FieldData" in f else f
        if isinstance(root, h5py.Group):
            for name in list(root.keys()):
                data = _field_from_group(root[name])
                if data is not None:
                    field = data
                    break
            if field is None:
                field = _field_from_group(root)
    field = _orient_field(x, y, z, field)
    return x, y, z, field


def _attr_float(obj, name: str) -> float | None:
    """First scalar value of an HDF5 attribute, or ``None``."""
    try:
        if name not in obj.attrs:
            return None
        val = np.asarray(obj.attrs[name], dtype=float).ravel()
        return float(val[0]) if val.size else None
    except Exception:  # noqa: BLE001
        return None


def read_dump_series(h5_path: Path | str, with_meta: bool = False):
    """Return ``(x, y, z, steps)`` with ``steps = [(label, field), ...]``.

    Covers *all* stored steps so the viewer can animate:
    ``FieldData/FD/f<N>_real|_imag`` (labels ``f0``, ``f1``, …, complex) and/or
    ``FieldData/TD/t<N>`` (labels ``t0``, ``t1``, …, real).

    With ``with_meta=True`` a fifth list is returned: one dict per step with the
    physical ``time`` (seconds, TD dumps) or ``frequency`` (Hz, FD dumps), as
    stored by openEMS in the dataset attributes; unavailable values are ``None``.
    """
    import h5py

    x = y = z = None
    steps: list[tuple[str, np.ndarray]] = []
    meta: list[dict] = []
    with h5py.File(h5_path, "r") as f:
        mesh = f["Mesh"] if "Mesh" in f else None
        if mesh is not None:
            x = np.asarray(mesh["x"], dtype=float).ravel()
            y = np.asarray(mesh["y"], dtype=float).ravel()
            z = np.asarray(mesh["z"], dtype=float).ravel()
        root = f["FieldData"] if "FieldData" in f else f
        groups = [root[name] for name in root.keys()] if isinstance(root, h5py.Group) else [root]

        def _num(key: str) -> int:
            digits = "".join(c for c in key if c.isdigit())
            return int(digits) if digits else 0

        for g in groups:
            names = list(g.keys())
            reals = sorted((k for k in names if k.endswith("_real")), key=_num)
            for key in reals:
                base = key[: -len("_real")]
                ds = g[key]
                re = np.asarray(ds)
                field = re + 1j * np.asarray(g[base + "_imag"]) if (base + "_imag") in g else re
                steps.append((base, field))
                meta.append({
                    "time": _attr_float(ds, "time"),
                    "frequency": _attr_float(ds, "frequency"),
                })
            for key in _td_keys(names):  # "00000000" (openEMS) or "t0"
                label = key if not key.isdigit() else f"t{int(key)}"
                ds = g[key]
                steps.append((label, np.asarray(ds)))
                meta.append({
                    "time": _attr_float(ds, "time"),
                    "frequency": _attr_float(ds, "frequency"),
                })
    steps = [(lab, _orient_field(x, y, z, f)) for lab, f in steps]
    if with_meta:
        return x, y, z, steps, meta
    return x, y, z, steps


def _orient_field(x, y, z, field):
    """Re-order a dump field to ``(3, nx, ny, nz)`` matching the mesh arrays.

    openEMS stores the raw dump data with the spatial axes in the REVERSED
    order (``z, y, x``) relative to the ``Mesh/{x,y,z}`` coordinate arrays (this
    is the ``--legacyHDF5Dumps`` order; the default is reversed too). Detect it
    from the shapes and transpose, so the VTK coordinates line up with the data.
    """
    if field is None:
        return None
    f = np.asarray(field)
    spatial = f.shape[1:] if (f.ndim == 4 and f.shape[0] == 3) else f.shape
    dims = (x.size, y.size, z.size)
    if tuple(spatial) == dims:
        return f
    if tuple(spatial) == dims[::-1]:
        if f.ndim == 4:
            return np.ascontiguousarray(f.transpose(0, 3, 2, 1))
        return np.ascontiguousarray(f.transpose(2, 1, 0))
    return f  # unknown layout — let _check_grid report the mismatch


def _magnitude(field: np.ndarray) -> np.ndarray:
    """Vector magnitude over the leading xyz axis (or |scalar|)."""
    f = np.asarray(field)
    if f.ndim >= 2 and f.shape[0] == 3:
        base = f.astype(complex) if np.iscomplexobj(f) else f.astype(float)
        return np.sqrt(np.sum(np.abs(base) ** 2, axis=0))
    return np.abs(f.astype(complex)) if np.iscomplexobj(f) else np.abs(f)


def _flat(arr: np.ndarray) -> np.ndarray:
    """VTK point order: x fastest -> Fortran order of a (nx,ny,nz) array."""
    return np.ascontiguousarray(np.asarray(arr, dtype=float).ravel(order="F"))


def _check_grid(h5_name: str, x, y, z, field):
    if field is None:
        raise ValueError(f"{h5_name}: no field data in the dump")
    if x is None or y is None or z is None or x.size < 1 or y.size < 1 or z.size < 1:
        raise ValueError(f"{h5_name}: no mesh in the dump")
    comp = field.shape[0] == 3 and field.ndim == 4
    if comp:
        nx, ny, nz = field.shape[1], field.shape[2], field.shape[3]
    else:
        nx, ny, nz = field.shape[-3], field.shape[-2], field.shape[-1]
    if nx * ny * nz <= 1:
        raise ValueError(
            f"{h5_name}: dump is a single point — set a box (Edit Dump: "
            "start != stop) and run again"
        )
    if (x.size, y.size, z.size) != (nx, ny, nz):
        raise ValueError(
            f"{h5_name}: mesh {x.size}x{y.size}x{z.size} does not match "
            f"field {nx}x{ny}x{nz}"
        )
    return comp, nx, ny, nz


def _write_vtr(vtk, ns, x, y, z, field, out_path: Path) -> Path:
    comp, nx, ny, nz = _check_grid(out_path.name, x, y, z, field)
    grid = vtk.vtkRectilinearGrid()
    grid.SetDimensions(nx, ny, nz)
    grid.SetXCoordinates(ns.numpy_to_vtk(np.ascontiguousarray(x), deep=True))
    grid.SetYCoordinates(ns.numpy_to_vtk(np.ascontiguousarray(y), deep=True))
    grid.SetZCoordinates(ns.numpy_to_vtk(np.ascontiguousarray(z), deep=True))

    scal = ns.numpy_to_vtk(_flat(_magnitude(field)), deep=True)
    scal.SetName("magnitude")
    grid.GetPointData().SetScalars(scal)
    if comp:
        re = np.real(field)
        vec = np.column_stack([_flat(re[0]), _flat(re[1]), _flat(re[2])])
        varr = ns.numpy_to_vtk(vec, deep=True)
        varr.SetName("field")
        grid.GetPointData().AddArray(varr)
        grid.GetPointData().SetActiveVectors("field")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = vtk.vtkXMLRectilinearGridWriter()
    writer.SetFileName(str(out_path))
    writer.SetInputData(grid)
    writer.SetDataModeToBinary()
    if not writer.Write():
        raise RuntimeError(f"VTK write failed: {out_path}")
    return out_path


def dump_to_vtr(h5_path: Path | str, out_path: Path | str | None = None) -> Path:
    """Convert one openEMS dump HDF5 to a VTK ``.vtr`` rectilinear grid.

    Point data: the scalar ``magnitude`` (active) and, when the dump has xyz
    components, the vector array ``field`` (real part). Raises ``ValueError``
    for an empty/degenerate dump (no mesh or no field data).
    """
    import vtk
    from vtk.util import numpy_support as ns

    h5_path = Path(h5_path)
    x, y, z, field = read_dump_arrays(h5_path)
    if out_path is None:
        out_path = h5_path.with_suffix(".vtr")
    return _write_vtr(vtk, ns, x, y, z, field, Path(out_path))


def dump_series_to_vtr(
    h5_path: Path | str, out_dir: Path | str | None = None
) -> tuple[list[Path], list[str]]:
    """Write one ``.vtr`` per stored step (for animation) plus a sidecar.

    Files: ``<stem>_0000.vtr``, ``<stem>_0001.vtr`` … and ``<stem>.series.json``
    (the ordered step labels). Returns ``(paths, labels)``. Empty dumps raise
    ``ValueError``.
    """
    import json

    import vtk
    from vtk.util import numpy_support as ns

    h5_path = Path(h5_path)
    x, y, z, steps, meta = read_dump_series(h5_path, with_meta=True)
    if not steps:
        raise ValueError(f"{h5_path.name}: no field data in the dump")
    if out_dir is None:
        out_dir = h5_path.parent
    out_dir = Path(out_dir)
    paths: list[Path] = []
    labels: list[str] = []
    for i, (label, field) in enumerate(steps):
        out = out_dir / f"{h5_path.stem}_{i:04d}.vtr"
        _write_vtr(vtk, ns, x, y, z, field, out)
        paths.append(out)
        labels.append(str(label))
    (out_dir / f"{h5_path.stem}.series.json").write_text(
        json.dumps(
            {
                "labels": labels,
                "files": [p.name for p in paths],
                # physical step positions (openEMS dataset attributes)
                "times": [m.get("time") for m in meta],
                "frequencies": [m.get("frequency") for m in meta],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return paths, labels


def export_dumps_to_vtk(
    sim_dir: Path | str, out_dir: Path | str | None = None, per_step: bool = False
) -> list[Path]:
    """Convert every field dump (``*.h5`` without ``nf2ff``) in ``sim_dir``.

    Returns the written ``.vtr`` paths. With ``per_step`` each dump also gets one
    ``<stem>_NNNN.vtr`` file per stored step (+ ``<stem>.series.json``) so the
    viewer can animate. Empty/degenerate dumps are skipped (error printed).
    """
    sim_dir = Path(sim_dir)
    if out_dir is None:
        out_dir = sim_dir.parent / "vtk"
    out_dir = Path(out_dir)
    written: list[Path] = []
    for h5 in sorted(sim_dir.glob("*.h5")):
        if "nf2ff" in h5.name.lower():
            continue
        try:
            single = dump_to_vtr(h5, out_dir / (h5.stem + ".vtr"))
            written.append(single)
            if per_step:
                paths, _labels = dump_series_to_vtr(h5, out_dir)
                written.extend(paths)
        except Exception as e:  # noqa: BLE001 — keep going over the other dumps
            print(f"VTK export skipped {h5.name}: {e}")
    return written
