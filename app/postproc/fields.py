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


def read_dump_plane(h5_path: Path):
    """Return (mesh_dict, field_data) best-effort from openEMS HDF5 dump."""
    import h5py

    with h5py.File(h5_path, "r") as f:
        mesh = {}
        if "Mesh" in f:
            g = f["Mesh"]
            for key in g.keys():
                mesh[key] = np.asarray(g[key])
        # field datasets
        field = None
        for key in f.keys():
            if key in {"Mesh", "Info"}:
                continue
            node = f[key]
            if isinstance(node, h5py.Dataset):
                field = np.asarray(node)
            elif isinstance(node, h5py.Group):
                # nested time or component groups
                for k2 in node.keys():
                    ds = node[k2]
                    if isinstance(ds, h5py.Dataset) and ds.size > 0:
                        field = np.asarray(ds)
                        break
                if field is not None:
                    break
        # attributes
        attrs = {}
        try:
            for k, v in f.attrs.items():
                attrs[k] = v
        except Exception:  # noqa: BLE001
            pass
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
        fig, ax = plt.subplots(figsize=(6, 5))
        if mag.ndim == 2:
            im = ax.imshow(mag, origin="lower", aspect="auto")
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
