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

from app.postproc.fields import _field_from_group


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
    return x, y, z, field


def _magnitude(field: np.ndarray) -> np.ndarray:
    """Vector magnitude over the leading xyz axis (or |scalar|)."""
    f = np.asarray(field)
    if f.ndim >= 2 and f.shape[0] == 3:
        base = f.astype(complex) if np.iscomplexobj(f) else f.astype(float)
        return np.sqrt(np.sum(np.abs(base) ** 2, axis=0))
    return np.abs(f.astype(complex)) if np.iscomplexobj(f) else np.abs(f)


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
    if field is None:
        raise ValueError(f"{h5_path.name}: no field data in the dump")
    if x is None or y is None or z is None or x.size < 1 or y.size < 1 or z.size < 1:
        raise ValueError(f"{h5_path.name}: no mesh in the dump")

    comp = field.shape[0] == 3 and field.ndim == 4
    if comp:
        nx, ny, nz = field.shape[1], field.shape[2], field.shape[3]
    else:
        nx, ny, nz = field.shape[-3], field.shape[-2], field.shape[-1]
    if nx * ny * nz <= 1:
        raise ValueError(
            f"{h5_path.name}: dump is a single point — set a box (Edit Dump: "
            "start != stop) and run again"
        )
    if (x.size, y.size, z.size) != (nx, ny, nz):
        raise ValueError(
            f"{h5_path.name}: mesh {x.size}x{y.size}x{z.size} does not match "
            f"field {nx}x{ny}x{nz}"
        )

    def _flat(arr2d: np.ndarray) -> np.ndarray:
        # VTK point order: x fastest -> Fortran order of (nx,ny,nz)
        return np.ascontiguousarray(np.asarray(arr2d, dtype=float).ravel(order="F"))

    grid = vtk.vtkRectilinearGrid()
    grid.SetDimensions(nx, ny, nz)
    grid.SetXCoordinates(ns.numpy_to_vtk(np.ascontiguousarray(x), deep=True))
    grid.SetYCoordinates(ns.numpy_to_vtk(np.ascontiguousarray(y), deep=True))
    grid.SetZCoordinates(ns.numpy_to_vtk(np.ascontiguousarray(z), deep=True))

    mag = _magnitude(field)
    scal = ns.numpy_to_vtk(_flat(mag), deep=True)
    scal.SetName("magnitude")
    grid.GetPointData().SetScalars(scal)

    if comp:
        re = np.real(field)
        vec = np.column_stack([_flat(re[0]), _flat(re[1]), _flat(re[2])])
        varr = ns.numpy_to_vtk(vec, deep=True)
        varr.SetName("field")
        grid.GetPointData().AddArray(varr)
        grid.GetPointData().SetActiveVectors("field")

    if out_path is None:
        out_path = h5_path.with_suffix(".vtr")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = vtk.vtkXMLRectilinearGridWriter()
    writer.SetFileName(str(out_path))
    writer.SetInputData(grid)
    writer.SetDataModeToBinary()
    if not writer.Write():
        raise RuntimeError(f"VTK write failed: {out_path}")
    return out_path


def export_dumps_to_vtk(
    sim_dir: Path | str, out_dir: Path | str | None = None
) -> list[Path]:
    """Convert every field dump (``*.h5`` without ``nf2ff``) in ``sim_dir``.

    Returns the written ``.vtr`` paths. Dumps that are empty/degenerate are
    skipped (their error is printed).
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
            written.append(dump_to_vtr(h5, out_dir / (h5.stem + ".vtr")))
        except Exception as e:  # noqa: BLE001 — keep going over the other dumps
            print(f"VTK export skipped {h5.name}: {e}")
    return written
