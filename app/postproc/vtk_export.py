"""Export openEMS HDF5 field dumps to VTK rectilinear grids (``.vtr``).

openEMS stores a dump as ``Mesh/{x,y,z}`` (the grid coordinates) plus the field
in ``FieldData/FD/f<N>_real|_imag`` (frequency domain) or ``FieldData/TD/t<N>``
(time domain), with the xyz components as the leading axis: ``(3, nx, ny, nz)``.

The exporter writes one ``.vtr`` per dump with the (real) vector field and its
magnitude as point data, so ParaView / the built-in VTK viewer can show it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

from app.postproc.fields import _field_from_group, _td_keys

#: openEMS dumps store raw SI values; the field kind only shows in the name.
FIELD_UNITS = {
    "E": "V/m",
    "H": "A/m",
    "D": "As/m²",
    "J": "A/m²",
}


def field_unit(name: str) -> str:
    """Best-effort SI unit of an openEMS dump from its name ("" if unknown).

    openEMS HDF5 dumps carry no unit attribute — only the dump name says which
    field it is ("Et-E" / "Et-H" / "Et" / "Ht" …). The last name segment wins,
    then earlier ones, so titles like "Et-E" and "E-field" both map to V/m.
    """
    base = re.sub(r"_\d+$", "", Path(name).stem)  # drop per-step "_0000"
    for token in reversed([t for t in re.split(r"[-_\s.]+", base) if t]):
        unit = FIELD_UNITS.get(token[0].upper(), "")
        if unit:
            return unit
    return ""


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


def _iter_step_datasets(root):
    """Yield ``(label, real_dataset, imag_dataset_or_None)`` one step at a time.

    Mirrors :func:`read_dump_series` (FD steps first, then TD steps) but hands
    out the HDF5 datasets instead of loaded arrays, so the series export can
    stream one step at a time instead of holding every step in RAM.
    """
    if hasattr(root, "keys"):
        names = list(root.keys())
        if names and all(hasattr(root[n], "keys") for n in names):
            groups = [root[n] for n in names]  # FieldData/{FD,TD}
        else:
            groups = [root]  # datasets directly under FieldData
    else:  # pragma: no cover - defensive
        groups = [root]

    def _num(key: str) -> int:
        digits = "".join(c for c in key if c.isdigit())
        return int(digits) if digits else 0

    for g in groups:
        try:
            gnames = list(g.keys())
        except AttributeError:  # a dataset used as the "group"
            continue
        for key in sorted((k for k in gnames if k.endswith("_real")), key=_num):
            base = key[: -len("_real")]
            yield base, g[key], (g[base + "_imag"] if (base + "_imag") in g else None)
        for key in _td_keys(gnames):  # "00000000" (openEMS) or "t0"
            label = key if not key.isdigit() else f"t{int(key)}"
            yield label, g[key], None


def count_dump_steps(h5_path: Path | str) -> int:
    """Number of stored field steps in a dump (0 when empty/unreadable)."""
    import h5py

    try:
        with h5py.File(h5_path, "r") as f:
            root = f["FieldData"] if "FieldData" in f else f
            return sum(1 for _ in _iter_step_datasets(root))
    except Exception:  # noqa: BLE001
        return 0


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
        for label, ds, imag_ds in _iter_step_datasets(root):
            re = np.asarray(ds)
            field = re + 1j * np.asarray(imag_ds) if imag_ds is not None else re
            steps.append((label, _orient_field(x, y, z, field)))
            meta.append({
                "time": _attr_float(ds, "time"),
                "frequency": _attr_float(ds, "frequency"),
            })
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
    """VTK point order: x fastest -> Fortran order of a (nx,ny,nz) array.

    Always float32: the dumps are float32 anyway, and the half-size arrays keep
    the per-step series from ballooning (one file per time step).
    """
    return np.ascontiguousarray(np.asarray(arr, dtype=np.float32).ravel(order="F"))


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


def _write_vtr(vtk, ns, x, y, z, field, out_path: Path, extra=None) -> Path:
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
        varr = ns.numpy_to_vtk(np.ascontiguousarray(vec, dtype=np.float32), deep=True)
        varr.SetName("field")
        grid.GetPointData().AddArray(varr)
        grid.GetPointData().SetActiveVectors("field")

    for name, arr in (extra or {}).items():
        v = np.real(np.asarray(arr))
        if v.shape[0] != 3:
            continue
        vec = np.column_stack([_flat(v[0]), _flat(v[1]), _flat(v[2])])
        varr = ns.numpy_to_vtk(np.ascontiguousarray(vec, dtype=np.float32), deep=True)
        varr.SetName(str(name))
        grid.GetPointData().AddArray(varr)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = vtk.vtkXMLRectilinearGridWriter()
    writer.SetFileName(str(out_path))
    writer.SetInputData(grid)
    writer.SetDataModeToBinary()
    if not writer.Write():
        raise RuntimeError(f"VTK write failed: {out_path}")
    return out_path


def poynting_vector(e_path: Path | str, h_path: Path | str):
    """Time-averaged Poynting vector ``0.5 * Re(E x H*)`` of two dumps.

    Both dumps must share the grid (same ``Mesh/{x,y,z}``); the result keeps
    the openEMS layout ``(3, nx, ny, nz)``.
    """
    x, y, z, e = read_dump_arrays(e_path)
    xh, yh, zh, h = read_dump_arrays(h_path)
    if (len(x), len(y), len(z)) != (len(xh), len(yh), len(zh)):
        raise ValueError("E and H dumps have different grid sizes")
    if not (np.allclose(x, xh) and np.allclose(y, yh) and np.allclose(z, zh)):
        raise ValueError("E and H dumps have different grid coordinates")
    if e is None or h is None:
        raise ValueError("E or H dump has no field data")
    return 0.5 * np.real(np.cross(e, np.conj(h), axis=0))


def _poynting_partner(h5_path: Path) -> Path | None:
    """Sibling H dump for an E dump (``E_tee.h5`` -> ``H_tee.h5``)."""
    stem = h5_path.stem
    idx = stem.find("E")
    if idx < 0:
        return None
    for name in (stem[:idx] + "H" + stem[idx + 1:], stem.replace("E", "H", 1)):
        cand = h5_path.with_name(name + h5_path.suffix)
        if cand.exists() and cand != h5_path:
            return cand
    return None


def dump_to_vtr(
    h5_path: Path | str,
    out_path: Path | str | None = None,
    with_poynting: bool = True,
) -> Path:
    """Convert one openEMS dump HDF5 to a VTK ``.vtr`` rectilinear grid.

    Point data: the scalar ``magnitude`` (active) and, when the dump has xyz
    components, the vector array ``field`` (real part). When ``with_poynting``
    is set and a sibling H dump with the same grid exists (``E_tee.h5`` next to
    ``H_tee.h5``), the vector array ``poynting`` (``0.5 Re(E x H*)``) is added
    as well. Raises ``ValueError`` for an empty/degenerate dump (no mesh or no
    field data).
    """
    import vtk
    from vtk.util import numpy_support as ns

    h5_path = Path(h5_path)
    x, y, z, field = read_dump_arrays(h5_path)
    if out_path is None:
        out_path = h5_path.with_suffix(".vtr")
    extra = None
    if with_poynting and h5_path.stem.find("E") >= 0:
        partner = _poynting_partner(h5_path)
        if partner is not None:
            try:
                extra = {"poynting": poynting_vector(h5_path, partner)}
            except ValueError:
                extra = None
    return _write_vtr(vtk, ns, x, y, z, field, Path(out_path), extra=extra)


def _is_fresh(out_path: Path, src: Path) -> bool:
    """True when ``out_path`` exists and is at least as new as ``src``."""
    try:
        return out_path.exists() and out_path.stat().st_mtime >= src.stat().st_mtime
    except OSError:
        return False


def write_pvd(out_path: Path | str, entries: list[tuple[float, str]]) -> Path:
    """Write a ParaView ``.pvd`` time collection.

    ``entries`` is ``[(timestep, file_name), ...]``. ParaView loads the file as
    *one* dataset with an animation slider, which is the reason to prefer it
    over the ``.series.json`` sidecar (that one only the built-in viewer reads).

    ``timestep`` doubles as the frequency axis for frequency-domain dumps:
    ParaView only needs a monotonic scalar, and for an FD dump the frequency is
    the meaningful slider value.
    """
    from xml.sax.saxutils import quoteattr

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0"?>',
        '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">',
        "  <Collection>",
    ]
    for step, name in entries:
        lines.append(
            f"    <DataSet timestep={quoteattr(f'{float(step):.12g}')} "
            f'group="" part="0" file={quoteattr(str(name))}/>'
        )
    lines += ["  </Collection>", "</VTKFile>", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _pvd_steps(labels: list[str], times: list, freqs: list) -> list[float]:
    """Slider positions for a series: time (TD) > frequency (FD) > index."""
    out: list[float] = []
    for i in range(len(labels)):
        time = times[i] if i < len(times) else None
        freq = freqs[i] if i < len(freqs) else None
        value = time if time is not None else freq
        out.append(float(value) if value is not None else float(i))
    return out


def write_series_pvd(out_dir: Path | str, h5_path: Path | str) -> Path | None:
    """(Re)write ``<stem>.pvd`` from an existing series sidecar.

    Reads the ``.series.json`` that :func:`dump_series_to_vtr` writes, so the
    collection can be rebuilt for series exported by an older version (and a
    ParaView collection never goes stale after a re-export).
    """
    import json

    out_dir = Path(out_dir)
    stem = Path(h5_path).stem
    sidecar = out_dir / f"{stem}.series.json"
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = [str(name) for name in data.get("files") or []]
    labels = [str(lab) for lab in data.get("labels") or []]
    if not files or len(files) != len(labels):
        return None
    times = list(data.get("times") or [])
    freqs = list(data.get("frequencies") or [])
    steps = _pvd_steps(labels, times, freqs)
    return write_pvd(out_dir / f"{stem}.pvd", list(zip(steps, files)))


def _load_series_state(
    h5_path: Path, out_dir: Path, step_stride: int = 1
) -> tuple[list[Path] | None, list[str]]:
    """Return the up-to-date series files/labels of a dump (``(None, [])`` if stale).

    A series counts as up to date when its sidecar is newer than the dump,
    every file it lists still exists and it was written with the same
    ``step_stride`` — so a re-run (newer HDF5), a deleted ``.vtr`` or a changed
    stride triggers a re-export, while repeated "Generate VTK" clicks are free.
    """
    import json

    sidecar = out_dir / f"{h5_path.stem}.series.json"
    if not _is_fresh(sidecar, h5_path):
        return None, []
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if max(1, int(data.get("stride", 1) or 1)) != max(1, int(step_stride)):
            return None, []
        files = [out_dir / str(name) for name in data.get("files") or []]
        labels = [str(lab) for lab in data.get("labels") or []]
    except (OSError, ValueError, TypeError, AttributeError):
        return None, []
    if not files or len(files) != len(labels) or not all(p.exists() for p in files):
        return None, []
    return files, labels


def dump_series_to_vtr(
    h5_path: Path | str,
    out_dir: Path | str | None = None,
    on_step: Any = None,
    should_stop: Any = None,
    step_stride: int = 1,
) -> tuple[list[Path], list[str]]:
    """Write one ``.vtr`` per stored step (for animation) plus a sidecar.

    Files: ``<stem>_0000.vtr``, ``<stem>_0001.vtr`` … and ``<stem>.series.json``
    (the ordered step labels). Returns ``(paths, labels)``. Empty dumps raise
    ``ValueError``. ``step_stride`` keeps only every N-th stored step (1 = all),
    which cuts the disk usage and the time-slider frame count roughly by N.

    The steps are streamed from the HDF5 file one at a time (a TD dump can hold
    hundreds of time steps — loading them all at once needs gigabytes of RAM).
    ``on_step(label)`` is called after every written file; ``should_stop()`` is
    polled between steps. On cancellation no sidecar is written, so the next run
    regenerates the whole series instead of trusting a truncated one.
    """
    import json

    import h5py
    import vtk
    from vtk.util import numpy_support as ns

    h5_path = Path(h5_path)
    stride = max(1, int(step_stride))
    if out_dir is None:
        out_dir = h5_path.parent
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    labels: list[str] = []
    times: list[float | None] = []
    freqs: list[float | None] = []
    cancelled = False
    with h5py.File(h5_path, "r") as f:
        mesh = f["Mesh"] if "Mesh" in f else None
        if mesh is not None:
            x = np.asarray(mesh["x"], dtype=float).ravel()
            y = np.asarray(mesh["y"], dtype=float).ravel()
            z = np.asarray(mesh["z"], dtype=float).ravel()
        else:
            x = y = z = None
        root = f["FieldData"] if "FieldData" in f else f
        for index, (label, ds, imag_ds) in enumerate(_iter_step_datasets(root)):
            if index % stride:  # keep every N-th stored step (0, N, 2N, …)
                continue
            if should_stop is not None and should_stop():
                cancelled = True
                break
            field = np.asarray(ds)
            if imag_ds is not None:
                field = field + 1j * np.asarray(imag_ds)
            field = _orient_field(x, y, z, field)
            out = out_dir / f"{h5_path.stem}_{len(paths):04d}.vtr"
            _write_vtr(vtk, ns, x, y, z, field, out)
            del field
            paths.append(out)
            labels.append(str(label))
            times.append(_attr_float(ds, "time"))
            freqs.append(_attr_float(ds, "frequency"))
            if on_step is not None:
                on_step(labels[-1])
    if not paths:
        raise ValueError(f"{h5_path.name}: no field data in the dump")

    # Drop leftover steps of an earlier (longer) run so the time slider does not
    # show phantom frames. Only the exporter's own ``<stem>_NNNN.vtr`` pattern —
    # and never while cancelled (the partial set is just the new prefix).
    if not cancelled:
        keep = {p.name for p in paths}
        stale = [
            p
            for p in out_dir.glob(f"{h5_path.stem}_[0-9][0-9][0-9][0-9].vtr")
            if p.name not in keep
        ]
        for old in stale:
            try:
                old.unlink()
            except OSError:
                pass

    if not cancelled:
        (out_dir / f"{h5_path.stem}.series.json").write_text(
            json.dumps(
                {
                    "labels": labels,
                    "files": [p.name for p in paths],
                    # physical step positions (openEMS dataset attributes)
                    "times": times,
                    "frequencies": freqs,
                    # kept steps: 0, N, 2N, … of the stored dump (1 = all)
                    "stride": stride,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # ParaView collection (animation in ParaView); the .series.json stays the
        # sidecar for the built-in viewer.
        write_series_pvd(out_dir, h5_path)
    return paths, labels


def export_dumps_to_vtk(
    sim_dir: Path | str,
    out_dir: Path | str | None = None,
    per_step: bool = False,
    progress: Any = None,
    should_stop: Any = None,
    step_stride: int = 1,
) -> list[Path]:
    """Convert every field dump (``*.h5`` without ``nf2ff``) in ``sim_dir``.

    Returns the ``.vtr`` paths (one static grid per dump, plus the per-step
    series when ``per_step`` is set). With ``per_step`` each dump also gets one
    ``<stem>_NNNN.vtr`` file per stored step (+ ``<stem>.series.json``) so the
    viewer can animate. ``step_stride`` exports only every N-th step (1 = all).

    Incremental: outputs that are newer than their dump are reused, so a second
    click only converts changed dumps. ``progress(done, total, text)`` is called
    while working and ``should_stop()`` is polled between files/steps (a long TD
    dump can mean hundreds of files — the GUI shows a cancelable dialog).
    """
    sim_dir = Path(sim_dir)
    stride = max(1, int(step_stride))
    if out_dir is None:
        out_dir = sim_dir.parent / "vtk"
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dumps = [h5 for h5 in sorted(sim_dir.glob("*.h5")) if "nf2ff" not in h5.name.lower()]

    # exact work count: one static grid per dump + one file per stored step
    plan: list[tuple[Path, list[Path] | None, list[str], int, bool]] = []
    total = 0
    for h5 in dumps:
        single = out_dir / (h5.stem + ".vtr")
        fresh_single = _is_fresh(single, h5)
        series, labels = _load_series_state(h5, out_dir, stride) if per_step else (None, [])
        if labels:
            steps = len(labels)
        elif per_step:
            steps = (count_dump_steps(h5) + stride - 1) // stride
        else:
            steps = 0
        plan.append((h5, series, labels, steps, fresh_single))
        total += 1 + steps

    state = {"done": 0}

    def _advance(count: int, text: str) -> None:
        state["done"] = min(total, state["done"] + count)
        if progress is not None:
            progress(state["done"], total, text)

    def _stop() -> bool:
        return bool(should_stop is not None and should_stop())

    written: list[Path] = []
    for h5, series, labels, steps, fresh_single in plan:
        if _stop():
            break
        single = out_dir / (h5.stem + ".vtr")
        try:
            if not fresh_single:
                dump_to_vtr(h5, single)
                _advance(1, f"{h5.name}: grid → {single.name}")
            else:
                _advance(1, f"{h5.name}: grid up to date")
            written.append(single)
            if not per_step:
                continue
            if series is not None:
                written.extend(series)
                # the .pvd collection may come from an older export (or be missing)
                write_series_pvd(out_dir, h5)
                _advance(len(series), f"{h5.name}: {len(series)} step files up to date")
                continue
            series, labels = dump_series_to_vtr(
                h5,
                out_dir,
                on_step=lambda label, h5=h5: _advance(1, f"{h5.name}: step {label} written"),
                should_stop=should_stop,
                step_stride=stride,
            )
            written.extend(series)
        except Exception as e:  # noqa: BLE001 — keep going over the other dumps
            print(f"VTK export skipped {h5.name}: {e}")
            continue
        if _stop():
            break
    return written
