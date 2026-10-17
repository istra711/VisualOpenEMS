"""Read openEMS HDF5 field dumps and render 2D slices."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.models.project import DumpDef


def _find_h5_files(sim_dir: Path, dump_name: str) -> list[Path]:
    files = list(sim_dir.rglob(f"*{dump_name}*.h5"))
    if not files:
        # openEMS uses et_0.h5 style when dump named Et
        files = list(sim_dir.rglob(f"{dump_name}_*.h5"))
    return sorted(files)


def _field_from_group(grp) -> np.ndarray | None:
    """Field array from an openEMS dump group (``FieldData/FD`` or ``/TD``)."""
    import h5py

    if not isinstance(grp, h5py.Group):
        return None
    names = list(grp.keys())
    # frequency domain: f0_real / f0_imag (optionally f1_… for more frequencies)
    reals = sorted(k for k in names if k.endswith("_real"))
    for key in reals:
        base = key[: -len("_real")]
        im_key = base + "_imag"
        re = np.asarray(grp[key])
        if im_key in grp:
            return re + 1j * np.asarray(grp[im_key])
        return re
    # time domain: t0, t1, … -> last time step
    tkeys = sorted(k for k in names if k.startswith("t") and k[1:].isdigit())
    if tkeys:
        return np.asarray(grp[tkeys[-1]])
    return None


def _vector_magnitude(data: np.ndarray) -> np.ndarray:
    """Reduce a leading xyz component axis to the vector magnitude."""
    data = np.asarray(data)
    if data.ndim >= 2 and data.shape[0] == 3:
        return np.sqrt(np.sum(np.abs(data.astype(complex)) ** 2, axis=0))
    return np.abs(data)


def read_dump_plane(h5_path: Path):
    """Return ``(mesh_dict, field_data, attrs)`` from an openEMS HDF5 dump.

    openEMS 0.0.36 stores ``Mesh/{x,y,z}`` plus the field in
    ``FieldData/FD/f<N>_real|_imag`` (frequency domain) or
    ``FieldData/TD/t<N>`` (time domain, last step). The xyz component axis is
    reduced to the vector magnitude so the caller gets a plain 2D/3D array.
    """
    import h5py

    mesh: dict = {}
    attrs: dict = {}
    field = None
    with h5py.File(h5_path, "r") as f:
        if "Mesh" in f:
            g = f["Mesh"]
            for key in g.keys():
                try:
                    mesh[key] = np.asarray(g[key])
                except Exception:  # noqa: BLE001
                    pass
        try:
            attrs = dict(f.attrs)
        except Exception:  # noqa: BLE001
            pass
        # --- field data -----------------------------------------------------
        root = f["FieldData"] if "FieldData" in f else f
        if isinstance(root, h5py.Group):
            for name in list(root.keys()):
                data = _field_from_group(root[name])
                if data is not None:
                    field = _vector_magnitude(data)
                    break
            if field is None:  # very old layout: datasets directly in the root
                data = _field_from_group(root)
                if data is not None:
                    field = _vector_magnitude(data)
    return mesh, field, attrs


def _complex_mag(data: np.ndarray) -> np.ndarray:
    data = np.asarray(data)
    if np.iscomplexobj(data):
        return np.abs(data)
    # compound {r,i} sometimes returned as structured array
    if data.dtype.fields and set(data.dtype.fields) >= {"r", "i"}:
        return np.sqrt(data["r"] ** 2 + data["i"] ** 2)
    return np.abs(data)


def plot_field_slices(sim_dir: Path | str, dumps: list[DumpDef], out_dir: Path | str) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    sim_dir = Path(sim_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for dump in dumps:
        if not dump.enabled:
            continue
        files = _find_h5_files(sim_dir, dump.name)
        if not files:
            continue
        path = files[0]
        try:
            mesh, field, attrs = read_dump_plane(path)
        except Exception as e:  # noqa: BLE001
            print(f"Could not read dump {dump.name}: {e}")
            continue
        if field is None:
            continue
        mag = _complex_mag(field)
        # squeeze to 2D if possible
        mag = np.squeeze(mag)
        # axis extent/labels from the mesh (drawing units) when lengths match
        extent = None
        xlabel = ylabel = ""
        if mag.ndim == 2 and mesh:
            vals = {k: np.asarray(mesh.get(k, []), dtype=float) for k in "xyz"}
            cols = [(k, v) for k, v in vals.items() if v.size == mag.shape[1]]
            rows = [(k, v) for k, v in vals.items() if v.size == mag.shape[0]]
            if cols and rows:
                (xlabel, xv), (ylabel, yv) = cols[0], rows[0]
                extent = [float(xv.min()), float(xv.max()), float(yv.min()), float(yv.max())]
        fig, ax = plt.subplots(figsize=(6, 5))
        if mag.ndim == 2:
            im = ax.imshow(mag, origin="lower", aspect="auto", extent=extent)
            ax.set_xlabel(xlabel or "x")
            ax.set_ylabel(ylabel or "y")
        elif mag.ndim == 1:
            ax.plot(mag)
            im = None
        else:
            # take middle slice
            mid = mag.shape[0] // 2
            im = ax.imshow(np.squeeze(mag[mid]), origin="lower", aspect="auto")
        ax.set_title(f"{dump.name} ({dump.quantity})")
        if im is not None:
            fig.colorbar(im, ax=ax, fraction=0.046)
        out = out_dir / f"{dump.name}_slice.png"
        fig.tight_layout()
        fig.savefig(out, dpi=120)
        plt.close(fig)
        written.append(out)
    return written
