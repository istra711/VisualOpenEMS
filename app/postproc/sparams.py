"""S-parameter extraction, Touchstone export, and plotting from openEMS ports."""

from __future__ import annotations

from pathlib import Path

import numpy as np


def compute_sparams_from_ports(
    sim_dir: Path | str,
    port_objects: list,
    freqs: np.ndarray,
    ref_impedance: float | None = None,
) -> dict[str, np.ndarray]:
    """Use openEMS port objects (from FDTD.AddLumpedPort / AddMSLPort).

    port_objects: list of (Project.PortDef, port_obj)

    One FDTD run measures exactly ONE S-matrix column: the one of the excited
    port. openEMS convention (see Tutorials/MSL_NotchFilter.py)::

        s_21 = port2.uf_ref / port1.uf_inc

    i.e. every port's uf_ref is divided by the *excited* port's uf_inc, which
    also yields the diagonal entry (S11 for an excitation at port 1). The
    remaining entries need a second run with another port excited — merge the
    runs with :func:`merge_sparam_tables`.

    ``ref_impedance`` is the S-parameter reference (normalisation) impedance.
    When ``None`` (default), openEMS uses each port's own resistance — the R
    passed to ``AddLumpedPort`` — so a 73 Ω port yields a 73 Ω-referenced S11
    (matching CST's port impedance). Passing an explicit value renormalises
    every port to it.
    """
    sim_dir = Path(sim_dir)
    freqs = np.asarray(freqs, dtype=float)
    out: dict[str, np.ndarray] = {"freq": freqs}
    if not port_objects:
        return out

    waves: list[tuple[int, str, np.ndarray, np.ndarray]] = []
    for idx, (port_def, pobj) in enumerate(port_objects, start=1):
        name = getattr(port_def, "name", None) or f"port_{getattr(pobj, 'number', idx)}"
        try:
            try:
                if ref_impedance is None:
                    # let openEMS use the port's own R (the AddLumpedPort value)
                    pobj.CalcPort(str(sim_dir), freqs)
                else:
                    pobj.CalcPort(str(sim_dir), freqs, ref_impedance=ref_impedance)
            except TypeError:
                pobj.CalcPort(str(sim_dir), freqs)
            uf_ref = np.asarray(pobj.uf_ref, dtype=complex)
            uf_inc = np.asarray(pobj.uf_inc, dtype=complex)
            print(
                f"Port {name}: |uf_inc|max={np.max(np.abs(uf_inc)):.3e} "
                f"|uf_ref|max={np.max(np.abs(uf_ref)):.3e}"
            )
            waves.append((idx, name, uf_inc, uf_ref))
        except Exception as e:  # noqa: BLE001
            print(f"ERROR: CalcPort failed for port {name}: {e}")
            out[f"S_{name}"] = np.full(len(freqs), np.nan, dtype=complex)

    n_ports = len(port_objects)
    exc_pos = _excited_wave_position(port_objects, waves)
    exc_col: int | None = None
    if exc_pos is None:
        print("ERROR: excited port has no usable waves (CalcPort failed) — no S-params")
    else:
        exc_idx, exc_name, exc_inc, _exc_ref = waves[exc_pos]
        if not np.any(np.isfinite(exc_inc)) or np.max(np.abs(exc_inc)) <= 0:
            print(f"ERROR: uf_inc of excited port {exc_name} is empty or zero")
        else:
            exc_col = exc_idx
            if len(waves) > 1:
                print(f"S-param column measured for excitation at port {exc_name} (S*n{exc_col})")
            for idx, name, _uf_inc, uf_ref in waves:
                s = uf_ref / exc_inc
                out[f"S{idx}{exc_col}"] = s
                out[f"S_{name}"] = s
                if not np.any(np.isfinite(s)):
                    print(f"ERROR: S-params for port {name} are all non-finite")

    if n_ports > 1:
        # Cells this run cannot measure stay NaN: they need another run with a
        # different excited port (merge_sparam_tables combines the columns).
        nan_row = np.full(len(freqs), np.nan, dtype=complex)
        for row in range(1, n_ports + 1):
            for col in range(1, n_ports + 1):
                out.setdefault(f"S{row}{col}", nan_row.copy())
        col_txt = f"S*n{exc_col}" if exc_col else "none"
        print(f"NOTE: {n_ports}-port project — this run measured column {col_txt} only")
    return out


def _excited_wave_position(port_objects: list, waves: list) -> int | None:
    """Position in `waves` of the excited port (excite_port flag, uf_inc fallback)."""
    by_number = {idx: pos for pos, (idx, _n, _i, _r) in enumerate(waves)}
    flagged = [
        port_no
        for port_no, (port_def, _pobj) in enumerate(port_objects, start=1)
        if getattr(port_def, "excite_port", False)
    ]
    if flagged:
        # a flagged port whose CalcPort failed must not silently fall back to
        # another port — that would report the wrong S-matrix column.
        for port_no in flagged:
            if port_no in by_number:
                return by_number[port_no]
        return None
    if not waves:
        return None
    # no excitation flag set: the source port carries the only large uf_inc,
    # terminated (matched) ports have uf_inc ~ 0.
    return max(range(len(waves)), key=lambda p: float(np.max(np.abs(waves[p][2]))))


def load_sparams_json(path: Path | str) -> dict[str, np.ndarray]:
    """Read an sparams.json written by :func:`export_sparams` back into arrays."""
    import json

    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, np.ndarray] = {"freq": np.asarray(data.get("freq", []), dtype=float)}
    for key, block in data.items():
        if key == "freq":
            continue
        if isinstance(block, dict) and "real" in block:
            out[key] = np.asarray(block["real"], dtype=float) + 1j * np.asarray(block["imag"], dtype=float)
        else:
            out[key] = np.asarray(block, dtype=complex)
    return out


def merge_sparam_tables(*tables: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Merge partial S-tables (one per excited port) into a single table.

    Each run supplies the column of its excited port, e.g. run A -> S11/S21 and
    run B -> S12/S22. Frequency grids must match; a cell seen twice is kept when
    both values agree (within tolerance), otherwise the first finite one wins.

    ``S_<portname>`` alias keys are dropped — they duplicate matrix cells and
    are expected to differ between runs.
    """
    if not tables:
        raise ValueError("merge_sparam_tables: no tables given")
    base_freq = np.asarray(tables[0].get("freq", []), dtype=float)
    if base_freq.size == 0:
        raise ValueError("merge_sparam_tables: first table has no freq axis")
    out: dict[str, np.ndarray] = {"freq": base_freq}

    for t_i, table in enumerate(tables, start=1):
        freqs = np.asarray(table.get("freq", []), dtype=float)
        if freqs.shape != base_freq.shape or not np.allclose(freqs, base_freq, rtol=1e-9, atol=1e-3):
            raise ValueError(
                f"merge_sparam_tables: frequency grid mismatch in table {t_i} "
                f"({freqs.size} pts vs {base_freq.size} pts)"
            )
        for key, val in table.items():
            if key == "freq" or key.startswith("S_"):
                continue
            arr = np.asarray(val, dtype=complex)
            if arr.shape != base_freq.shape:
                raise ValueError(f"merge_sparam_tables: '{key}' length {arr.shape} != {base_freq.shape}")
            if key not in out:
                out[key] = arr.copy()
                continue
            old = out[key]
            both = np.isfinite(old) & np.isfinite(arr)
            if np.any(both) and not np.allclose(old[both], arr[both], rtol=1e-6, atol=1e-9):
                print(f"WARNING: merge_sparam_tables: '{key}' differs between runs — keeping first")
            fill = ~np.isfinite(old) & np.isfinite(arr)
            old[fill] = arr[fill]
            out[key] = old
    return out


def compute_sparams(sim_dir: Path | str, ports, mesh) -> dict[str, np.ndarray]:
    """Fallback without port objects: scan for touchstone or raw files."""
    sim_dir = Path(sim_dir)
    freqs = np.linspace(mesh.start_frequency, mesh.stop_frequency, 201)
    out: dict[str, np.ndarray] = {"freq": freqs}

    for touch in sim_dir.glob("*.s*p"):
        try:
            import skrf as rf

            ntwk = rf.Network(str(touch))
            for i in range(ntwk.nports):
                for j in range(ntwk.nports):
                    out[f"S{i+1}{j+1}"] = ntwk[f"S{i+1}{j+1}"].s[:, 0, 0]
            out["freq"] = ntwk.f
            return out
        except Exception:  # noqa: BLE001
            continue

    for p in ports:
        out[f"S_{getattr(p, 'name', 'port')}"] = np.full(len(freqs), np.nan, dtype=complex)
    return out


def _collect_s_matrix(sparams: dict[str, np.ndarray]) -> tuple[np.ndarray, np.ndarray, int] | None:
    """Build (freq, S[n,n]) complex matrix from s_table keys like S11, S21, ..."""
    freqs = np.asarray(sparams.get("freq", []), dtype=float)
    if freqs.size == 0:
        return None
    n = 0
    for key in sparams:
        if key == "freq":
            continue
        # S11, S21, S_in1 is not a matrix cell — only pure Sij
        if len(key) == 3 and key[0] == "S" and key[1].isdigit() and key[2].isdigit():
            n = max(n, int(key[1]), int(key[2]))
    if n == 0:
        # single-port tables often use S11 only
        if "S11" in sparams:
            n = 1
        else:
            return None
    s = np.full((freqs.size, n, n), np.nan, dtype=complex)
    for key, val in sparams.items():
        if key == "freq" or len(key) != 3 or key[0] != "S":
            continue
        if not (key[1].isdigit() and key[2].isdigit()):
            continue
        i = int(key[1]) - 1
        j = int(key[2]) - 1
        arr = np.asarray(val, dtype=complex)
        if arr.size == freqs.size:
            s[:, i, j] = arr
    return freqs, s, n


def write_touchstone(
    sparams: dict[str, np.ndarray],
    out_path: Path | str,
    z0: float = 50.0,
    comments: list[str] | None = None,
) -> Path:
    """Write S-parameters as Touchstone (.s1p / .s2p / ...).

    Always called after simulation post-processing so results are portable.
    """
    collected = _collect_s_matrix(sparams)
    if collected is None:
        raise ValueError("No S-parameter frequency data to write as Touchstone")
    freqs, s_mat, n = collected
    nonfinite = ~np.isfinite(s_mat)
    if nonfinite.any():
        # Single-excitation runs only fill one matrix column — keep the file
        # readable instead of writing NaN.
        comments = list(comments or []) + [
            f"WARNING: {int(nonfinite.sum())} unmeasured entries written as 0 "
            "(run one excitation per port and merge)"
        ]
        s_mat = np.nan_to_num(s_mat, nan=0.0, posinf=0.0, neginf=0.0)
    out_path = Path(out_path)
    if out_path.suffix.lower() != f".s{n}p":
        out_path = out_path.with_suffix(f".s{n}p")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import skrf as rf

        # f is in Hz; write_touchstone uses the Network's frequency unit
        ntwk = rf.Network(f=freqs, s=s_mat, z0=z0)
        ntwk.write_touchstone(str(out_path))
        return out_path
    except Exception as e:  # noqa: BLE001
        print(f"skrf touchstone write failed ({e}); using built-in writer")

    lines: list[str] = []
    lines.append("! FreeCAD OpenEMS GUI — S-parameters")
    lines.append(f"! ports={n} z0={z0} ohm")
    for c in comments or []:
        lines.append(f"! {c}")
    lines.append(f"# Hz S R {z0:g}")
    for i, f in enumerate(freqs):
        parts = [f"{f:.10e}"]
        # Touchstone order for n-port: S11 S21 ... Sn1 S12 S22 ... (column-major)
        for j in range(n):
            for i_row in range(n):
                val = s_mat[i, i_row, j]
                mag = abs(val)
                ang = np.degrees(np.angle(val))
                parts.append(f"{mag:.10e}")
                parts.append(f"{ang:.10e}")
        lines.append(" ".join(parts))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def export_sparams(sparams: dict[str, np.ndarray], out_dir: Path | str, basename: str = "sparams", z0: float = 50.0) -> dict[str, Path]:
    """Write JSON + Touchstone + PNG for a completed S-param table.

    ``z0`` is the reference impedance recorded in the Touchstone header (use the
    port resistance so a 73 Ω port is not mislabelled as a 50 Ω file).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    import json

    s_json = {"freq": np.asarray(sparams.get("freq", []), dtype=float).tolist()}
    for k, v in sparams.items():
        if k == "freq":
            continue
        arr = np.asarray(v, dtype=complex)
        s_json[k] = {
            "real": np.real(arr).tolist(),
            "imag": np.imag(arr).tolist(),
            "db": (20 * np.log10(np.maximum(np.abs(arr), 1e-12))).tolist(),
        }
    json_path = out_dir / f"{basename}.json"
    try:
        json_path.write_text(json.dumps(s_json), encoding="utf-8")
        written["json"] = json_path
        print("Wrote JSON:", json_path)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: JSON export failed: {e}")

    try:
        collected = _collect_s_matrix(sparams)
        if collected is None:
            print("Touchstone skipped: no S-matrix cells (sparams.json only)")
        elif not np.all(np.isfinite(collected[1])):
            # Touchstone cannot express "not measured"; a partial .s2p would
            # show fake 0 dB traces. sparams.json keeps NaN gaps instead.
            print(
                "Touchstone skipped: S-matrix incomplete (single excitation) — "
                "run one excitation per port and merge (see merge_sparam_tables)"
            )
        else:
            touch = write_touchstone(sparams, out_dir / basename, z0=z0, comments=[f"source={out_dir}"])
            written["touchstone"] = touch
            print("Wrote Touchstone:", touch)
    except Exception as e:  # noqa: BLE001
        print("ERROR: Touchstone export failed:", e)

    try:
        written["png"] = plot_sparams(sparams, out_dir / f"{basename}.png")
    except Exception as e:  # noqa: BLE001
        print("S-param plot failed:", e)

    return written


def plot_sparams(sparams: dict[str, np.ndarray], out_path: Path | str) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    freqs = np.asarray(sparams.get("freq", np.array([])), dtype=float)
    keys = [k for k in sparams if k.startswith("S") and k not in {"freq", "f0"}]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for k in keys:
        y = np.asarray(sparams[k])
        if y.size == 0 or np.all(np.isnan(y)):
            continue
        db = 20 * np.log10(np.maximum(np.abs(y), 1e-12))
        ax.plot(freqs[: len(db)] / 1e9, db, label=k)
    ax.set_xlabel("Frequency [GHz]")
    ax.set_ylabel("|S| [dB]")
    ax.set_title("S-parameters")
    ax.grid(True, alpha=0.3)
    if keys:
        ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path
