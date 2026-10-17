"""Smoke: field-dump post-processing (read_dump_plane / plot_field_slices).

Regression: the reader only descended one HDF5 level (``FieldData`` -> ``FD``
is a group, not a dataset), so ``fields/`` stayed empty even for projects with
field dumps. Uses synthetic HDF5 files (no FDTD run).

Run: python -u tests/field_dump_postproc_smoke.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402


def _write_fd_dump(path: Path, name: str) -> None:
    """Frequency-domain dump: FieldData/FD/f0_real + f0_imag (3,1,4,5)."""
    import h5py

    re = np.full((3, 1, 4, 5), 2.0, dtype="f4")
    im = np.full((3, 1, 4, 5), 1.0, dtype="f4")
    with h5py.File(path, "w") as f:
        mesh = f.create_group("Mesh")
        mesh.create_dataset("x", data=np.linspace(0.0, 40.0, 5, dtype="f4"))
        mesh.create_dataset("y", data=np.linspace(-10.0, 20.0, 4, dtype="f4"))
        mesh.create_dataset("z", data=np.array([1.5], dtype="f4"))
        fd = f.create_group("FieldData").create_group("FD")
        fd.create_dataset("f0_real", data=re)
        fd.create_dataset("f0_imag", data=im)


def _write_td_dump(path: Path) -> None:
    """Time-domain dump: FieldData/TD/t0, t1 (last step must be used)."""
    import h5py

    with h5py.File(path, "w") as f:
        mesh = f.create_group("Mesh")
        for k, n in (("x", 5), ("y", 4), ("z", 1)):
            mesh.create_dataset(k, data=np.arange(n, dtype="f4"))
        td = f.create_group("FieldData").create_group("TD")
        td.create_dataset("t0", data=np.ones((3, 1, 4, 5), dtype="f4"))
        td.create_dataset("t1", data=np.full((3, 1, 4, 5), 3.0, dtype="f4"))


def main() -> int:
    from app.models.project import DumpDef
    from app.postproc.fields import plot_field_slices, read_dump_plane

    tmp = Path(tempfile.mkdtemp(prefix="field_dump_smoke_"))
    try:
        # --- frequency domain: vector magnitude sqrt(3*(2^2+1^2)) -----------
        fd = tmp / "sim_fd"
        fd.mkdir()
        _write_fd_dump(fd / "Eplane.h5", "Eplane")
        mesh, field, _attrs = read_dump_plane(fd / "Eplane.h5")
        assert field is not None, "frequency-domain dump not read"
        assert field.shape == (1, 4, 5), field.shape
        want = float(np.sqrt(3.0 * (2.0**2 + 1.0**2)))
        assert abs(float(np.nanmax(field)) - want) < 1e-6, (np.nanmax(field), want)
        assert set(mesh) == {"x", "y", "z"} and len(mesh["x"]) == 5
        print(f"[read] FD dump OK (|E|max={np.nanmax(field):.4f}, mesh {sorted(mesh)})")

        # --- time domain: last time step ------------------------------------
        td = tmp / "sim_td"
        td.mkdir()
        _write_td_dump(td / "Etime.h5")
        _m, field_td, _a = read_dump_plane(td / "Etime.h5")
        assert field_td is not None and abs(float(field_td.max()) - 3.0 * np.sqrt(3)) < 1e-5, (
            None if field_td is None else float(field_td.max())
        )
        print("[read] TD dump OK (last time step, vector magnitude)")

        # --- plotting --------------------------------------------------------
        dump = DumpDef(name="Eplane", quantity="E", frequency_domain=True, frequency=2.45e9)
        out = tmp / "out"
        written = plot_field_slices(fd, [dump], out)
        assert len(written) == 1 and written[0].exists(), written
        assert written[0].name == "Eplane_slice.png", written[0].name
        assert written[0].stat().st_size > 5000, written[0].stat().st_size
        print(f"[plot] wrote {written[0].name} ({written[0].stat().st_size} B)")

        # disabled dump / missing file -> nothing written, no crash
        out2 = tmp / "out2"
        assert plot_field_slices(fd, [DumpDef(name="Eplane", enabled=False)], out2) == []
        assert plot_field_slices(fd, [DumpDef(name="Nope")], out2) == []
        print("[plot] disabled/missing dumps skipped OK")

        print("FIELD DUMP POSTPROC SMOKE PASS")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
