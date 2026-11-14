"""S-parameter extraction, Touchstone export, and plotting from openEMS ports."""

from __future__ import annotations

import copy
from datetime import datetime
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
        if key in ("freq", "z0"):
            continue
        if isinstance(block, dict) and "real" in block:
            out[key] = np.asarray(block["real"], dtype=float) + 1j * np.asarray(block["imag"], dtype=float)
        else:
            out[key] = np.asarray(block, dtype=complex)
    return out


def network_z0(network) -> float:
    """First finite positive reference impedance of a scikit-rf network."""
    try:
        raw = np.asarray(network.z0)
        if np.iscomplexobj(raw):
            raw = raw.real
        values = raw.astype(float).ravel()
        for value in values:
            if np.isfinite(value) and value > 0:
                return float(value)
    except Exception:  # noqa: BLE001
        pass
    return 50.0


def renormalize_network(network, z0: float):
    """Return ``network`` renormalised to ``z0`` without mutating the input."""
    try:
        target = float(z0)
        if not np.isfinite(target) or target <= 0:
            return network
        current = np.asarray(network.z0, dtype=complex)
        if np.allclose(current, target):
            return network
        out = copy.deepcopy(network)
        out.renormalize(target)
        return out
    except Exception:  # noqa: BLE001
        return network


def align_complex_trace(
    reference_freq: np.ndarray,
    reference_values: np.ndarray,
    target_freq: np.ndarray,
) -> np.ndarray:
    """Interpolate one complex trace onto ``target_freq``.

    Only the overlapping frequency range is filled; everything outside stays
    ``nan`` (no extrapolation).  A trace with gaps is left as ``nan`` as a
    whole, because bridging an unmeasured S-matrix cell would invent data.
    """
    ref_f = np.asarray(reference_freq, dtype=float).ravel()
    ref_s = np.asarray(reference_values, dtype=complex).ravel()
    target = np.asarray(target_freq, dtype=float).ravel()
    out = np.full(target.shape, np.nan + 1j * np.nan, dtype=complex)
    if ref_f.size == 0 or ref_s.size != ref_f.size or target.size == 0:
        return out

    order = np.argsort(ref_f)
    ref_f = ref_f[order]
    ref_s = ref_s[order]
    if not np.all(np.isfinite(ref_s)):
        return out

    lo = float(ref_f[0])
    hi = float(ref_f[-1])
    span = max(abs(hi - lo), 1.0)
    tol = span * 1e-12
    mask = (target >= lo - tol) & (target <= hi + tol)
    if not np.any(mask):
        return out

    out[mask] = (
        np.interp(target[mask], ref_f, ref_s.real)
        + 1j * np.interp(target[mask], ref_f, ref_s.imag)
    )
    return out


def align_network_to_frequencies(reference, target_freq, z0: float | None = None) -> np.ndarray:
    """Return the reference S-matrix on ``target_freq``.

    The reference is optionally renormalised to ``z0`` (normally the current
    network's reference impedance).  Cells outside the common frequency range
    are ``nan`` so neither the plot nor a marker can pretend they were measured.
    """
    try:
        s = np.asarray(reference.s, dtype=complex)
    except Exception:  # noqa: BLE001
        return np.empty((0, 0, 0), dtype=complex)
    if s.ndim != 3 or s.shape[0] == 0:
        return np.empty((0, 0, 0), dtype=complex)

    net = renormalize_network(reference, z0) if z0 is not None else reference
    ref_f = np.asarray(net.f, dtype=float)
    ref_s = np.asarray(net.s, dtype=complex)
    target = np.asarray(target_freq, dtype=float).ravel()
    out = np.full(
        (target.size, ref_s.shape[1], ref_s.shape[2]),
        np.nan + 1j * np.nan,
        dtype=complex,
    )
    for i in range(ref_s.shape[1]):
        for j in range(ref_s.shape[2]):
            out[:, i, j] = align_complex_trace(ref_f, ref_s[:, i, j], target)
    return out


def normalize_port_map(
    port_map,
    current_nports: int,
    reference_nports: int,
) -> list[int]:
    """Return a 0-based current→reference port map.

    ``port_map`` is the user-facing 1-based list (0 or ``None`` = unmapped).
    ``None``/empty means the natural 1:1 mapping for the common ports.
    The returned list always has ``current_nports`` entries.
    """
    current_nports = max(0, int(current_nports))
    reference_nports = max(0, int(reference_nports))
    identity = [
        i if i < reference_nports else -1 for i in range(current_nports)
    ]
    if port_map is None:
        return identity
    try:
        if len(port_map) == 0:
            return identity
    except TypeError:
        return identity
    out: list[int] = []
    for i in range(current_nports):
        value = (
            port_map[i]
            if i < len(port_map)
            else (i + 1 if i < reference_nports else 0)
        )
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = 0
        out.append(number - 1 if 1 <= number <= reference_nports else -1)
    return out


def mapped_reference_key(key: str, port_map: list[int]) -> str | None:
    """Map a current ``Sij`` key through a 0-based port map."""
    if len(key) != 3 or key[0] != "S" or not key[1].isdigit() or not key[2].isdigit():
        return None
    i = int(key[1]) - 1
    j = int(key[2]) - 1
    if i < 0 or j < 0 or i >= len(port_map) or j >= len(port_map):
        return None
    ri = port_map[i]
    rj = port_map[j]
    if ri < 0 or rj < 0:
        return None
    return f"S{ri + 1}{rj + 1}"


def _trace_has_data(values) -> bool:
    arr = np.asarray(values, dtype=complex)
    if arr.size == 0 or not np.any(np.isfinite(arr)):
        return False
    # Touchstone exports unmeasured cells as 0; sparams.json keeps NaN.
    return bool(np.any(arr != 0))


def _db(values) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(np.abs(np.asarray(values, dtype=complex)), 1e-15))


def _phase_delta_deg(current_values, reference_values) -> np.ndarray:
    cur = np.asarray(current_values, dtype=complex)
    ref = np.asarray(reference_values, dtype=complex)
    out = np.full(cur.shape, np.nan, dtype=float)
    valid = (
        np.isfinite(cur)
        & np.isfinite(ref)
        & (np.abs(cur) > 1e-12)
        & (np.abs(ref) > 1e-12)
    )
    if np.any(valid):
        out[valid] = np.degrees(np.angle(cur[valid] * np.conj(ref[valid])))
    return out


def _format_port_map(port_map: list[int]) -> tuple[str, str]:
    """Human-readable mapped/unmapped port lists (1-based)."""
    mapped = [
        f"S{i + 1}->S{ref + 1}"
        for i, ref in enumerate(port_map)
        if ref >= 0
    ]
    unmapped = [f"S{i + 1}" for i, ref in enumerate(port_map) if ref < 0]
    return (", ".join(mapped) or "none", ", ".join(unmapped))


def compare_networks(
    current,
    reference,
    *,
    port_map=None,
    z0: float | None = None,
) -> dict:
    """Compare ``current`` with ``reference`` on the current frequency axis.

    Returns per-current-Sij metrics over the common, finite data.  The reference
    is renormalised to the current Z0 and interpolated only inside the overlap
    (see :func:`align_network_to_frequencies`).  ``port_map`` uses the
    user-facing 1-based numbering.
    """
    if current is None or reference is None:
        raise ValueError("compare_networks: current and reference are required")
    cur_s = np.asarray(current.s, dtype=complex)
    freq = np.asarray(current.f, dtype=float)
    target_z0 = network_z0(current) if z0 is None else float(z0)
    ref_s = align_network_to_frequencies(reference, freq, z0=target_z0)
    port_map_0 = normalize_port_map(
        port_map, cur_s.shape[1], int(getattr(reference, "nports", 0))
    )

    pairs: list[dict] = []
    for i in range(cur_s.shape[1]):
        for j in range(cur_s.shape[2]):
            key = f"S{i + 1}{j + 1}"
            ref_key = mapped_reference_key(key, port_map_0)
            pair: dict = {
                "key": key,
                "ref_key": ref_key,
                "points": 0,
                "max_abs_db": np.nan,
                "f_at_max_db": np.nan,
                "rms_db": np.nan,
                "max_phase_deg": np.nan,
                "max_complex": np.nan,
                "s_cur": None,
                "s_ref": None,
                "delta_db": None,
                "delta_phase": None,
            }
            if ref_key is None:
                pair["reason"] = "unmapped"
                pairs.append(pair)
                continue
            try:
                ref_i = int(ref_key[1]) - 1
                ref_j = int(ref_key[2]) - 1
                if (
                    ref_s.ndim != 3
                    or ref_s.shape[1] <= ref_i
                    or ref_s.shape[2] <= ref_j
                ):
                    pair["reason"] = "reference missing"
                    pairs.append(pair)
                    continue
                cur_values = np.asarray(cur_s[:, i, j], dtype=complex)
                ref_values = np.asarray(ref_s[:, ref_i, ref_j], dtype=complex)
            except Exception:  # noqa: BLE001
                pair["reason"] = "reference missing"
                pairs.append(pair)
                continue

            if not _trace_has_data(cur_values) or not _trace_has_data(ref_values):
                pair["reason"] = "no data"
                pairs.append(pair)
                continue

            valid = np.isfinite(cur_values) & np.isfinite(ref_values)
            if not np.any(valid):
                pair["reason"] = "no overlap"
                pairs.append(pair)
                continue

            delta_db = _db(cur_values) - _db(ref_values)
            delta_phase = _phase_delta_deg(cur_values, ref_values)
            complex_delta = np.abs(cur_values - ref_values)
            valid_db = delta_db[valid]
            abs_db = np.abs(valid_db)
            idx = int(np.argmax(abs_db))
            pair.update(
                {
                    "points": int(np.count_nonzero(valid)),
                    "max_abs_db": float(abs_db[idx]),
                    "f_at_max_db": float(freq[valid][idx]),
                    "rms_db": float(np.sqrt(np.mean(valid_db**2))),
                    "max_phase_deg": (
                        float(np.nanmax(np.abs(delta_phase)))
                        if np.any(np.isfinite(delta_phase))
                        else np.nan
                    ),
                    "max_complex": float(np.max(complex_delta[valid])),
                    "s_cur": cur_values,
                    "s_ref": ref_values,
                    "delta_db": delta_db,
                    "delta_phase": delta_phase,
                }
            )
            pairs.append(pair)

    try:
        cur_f = np.asarray(current.f, dtype=float)
        ref_f = np.asarray(reference.f, dtype=float)
        lo = max(float(np.min(cur_f)), float(np.min(ref_f)))
        hi = min(float(np.max(cur_f)), float(np.max(ref_f)))
        overlap = (lo, hi) if lo <= hi else None
    except Exception:  # noqa: BLE001
        overlap = None

    return {
        "freq": freq,
        "pairs": pairs,
        "port_map": port_map_0,
        "z0": target_z0,
        "reference_z0": network_z0(reference),
        "overlap": overlap,
        "current_nports": int(cur_s.shape[1]),
        "reference_nports": int(getattr(reference, "nports", 0)),
    }


def _comparison_header(result: dict, meta: dict | None) -> list[str]:
    meta = dict(meta or {})
    lines = [
        "# VisualopenEMS S-parameter comparison",
        f"# generated={datetime.now().isoformat(timespec='seconds')}",
    ]
    for key in (
        "current",
        "current_path",
        "reference",
        "reference_path",
        "reference_run",
        "reference_status",
    ):
        value = str(meta.get(key) or "").strip()
        if value:
            lines.append(f"# {key}={value}")
    mapped, unmapped = _format_port_map(result["port_map"])
    lines.append(f"# port_map={mapped}")
    if unmapped:
        lines.append(f"# unmapped_current_ports={unmapped}")
    overlap = result.get("overlap")
    if overlap is not None:
        lines.append(
            f"# overlap_GHz={overlap[0] / 1e9:.9g}-{overlap[1] / 1e9:.9g}"
        )
    lines.append(f"# current_Z0={result['z0']:g}")
    lines.append(f"# reference_Z0={result['reference_z0']:g}")
    return lines


def write_comparison_csv(
    current,
    reference,
    out_path: Path | str,
    *,
    port_map=None,
    meta: dict | None = None,
) -> Path:
    """Write current/reference/delta values as a long-format CSV."""
    result = compare_networks(current, reference, port_map=port_map)
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".csv":
        out_path = out_path.with_suffix(".csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = _comparison_header(result, meta)
    lines.append(
        "freq_Hz,freq_GHz,Sij,ref_Sij,S_cur_dB,S_ref_dB,delta_dB,"
        "S_cur_deg,S_ref_deg,delta_deg,S_cur_re,S_cur_im,S_ref_re,S_ref_im"
    )
    freq = np.asarray(result["freq"], dtype=float)
    for pair in result["pairs"]:
        if pair.get("s_cur") is None or pair.get("s_ref") is None:
            continue
        cur = np.asarray(pair["s_cur"], dtype=complex)
        ref = np.asarray(pair["s_ref"], dtype=complex)
        for idx, f_hz in enumerate(freq):
            cur_val = complex(cur[idx])
            ref_val = complex(ref[idx])
            cur_finite = bool(np.isfinite(cur_val))
            ref_finite = bool(np.isfinite(ref_val))
            db_cur = float(_db([cur_val])[0]) if cur_finite else np.nan
            db_ref = float(_db([ref_val])[0]) if ref_finite else np.nan
            if cur_finite and ref_finite:
                delta_db = db_cur - db_ref
            else:
                delta_db = np.nan
            phase_cur = (
                float(np.degrees(np.angle(cur_val))) if cur_finite else np.nan
            )
            phase_ref = (
                float(np.degrees(np.angle(ref_val))) if ref_finite else np.nan
            )
            if (
                cur_finite
                and ref_finite
                and abs(cur_val) > 1e-12
                and abs(ref_val) > 1e-12
            ):
                delta_phase = float(
                    np.degrees(np.angle(cur_val * np.conj(ref_val)))
                )
            else:
                delta_phase = np.nan
            row = [
                f_hz,
                f_hz / 1e9,
                pair["key"],
                pair["ref_key"],
                db_cur,
                db_ref,
                delta_db,
                phase_cur,
                phase_ref,
                delta_phase,
                cur_val.real if cur_finite else np.nan,
                cur_val.imag if cur_finite else np.nan,
                ref_val.real if ref_finite else np.nan,
                ref_val.imag if ref_finite else np.nan,
            ]
            lines.append(
                ",".join(
                    str(v) if isinstance(v, str) else _fmt_sparam(v)
                    for v in row
                )
            )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def write_comparison_report(
    current,
    reference,
    out_path: Path | str,
    *,
    port_map=None,
    meta: dict | None = None,
) -> Path:
    """Write a human-readable comparison summary (Markdown/text)."""
    result = compare_networks(current, reference, port_map=port_map)
    out_path = Path(out_path)
    if out_path.suffix.lower() not in (".txt", ".md"):
        out_path = out_path.with_suffix(".txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    meta = dict(meta or {})
    lines = [
        "VisualopenEMS S-parameter comparison",
        "====================================",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
    ]
    for key, label in (
        ("current", "Current"),
        ("current_path", "Current path"),
        ("reference", "Reference"),
        ("reference_path", "Reference path"),
        ("reference_run", "Reference run"),
        ("reference_status", "Reference status"),
    ):
        value = str(meta.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    lines.append(f"Current Z0: {result['z0']:g} Ω")
    ref_z0_note = ""
    if not np.isclose(result["reference_z0"], result["z0"]):
        ref_z0_note = " (renormalised to current for comparison)"
    lines.append(
        f"Reference Z0: {result['reference_z0']:g} Ω{ref_z0_note}"
    )
    overlap = result.get("overlap")
    if overlap is not None:
        lines.append(
            "Overlap: "
            f"{overlap[0] / 1e9:.6g}–{overlap[1] / 1e9:.6g} GHz"
        )
    else:
        lines.append("Overlap: none")
    mapped, unmapped = _format_port_map(result["port_map"])
    lines.append(f"Port map: {mapped}")
    if unmapped:
        lines.append(f"Unmapped current ports: {unmapped}")
    lines.append("")
    lines.append(
        "Sij  Ref   Points  max|ΔdB|  f@max[GHz]  RMS ΔdB  "
        "max Δphase[°]  max|ΔS|"
    )
    lines.append("-" * 78)
    for pair in result["pairs"]:
        if pair.get("points", 0) <= 0:
            reason = pair.get("reason") or "no data"
            lines.append(
                f"{pair['key']:<4} {str(pair.get('ref_key') or '—'):<5} "
                f"{0:>6}  ({reason})"
            )
            continue
        lines.append(
            f"{pair['key']:<4} {pair['ref_key']:<5} {pair['points']:>6}  "
            f"{pair['max_abs_db']:>8.3f}  "
            f"{pair['f_at_max_db'] / 1e9:>10.4f}  "
            f"{pair['rms_db']:>7.3f}  "
            f"{pair['max_phase_deg']:>12.2f}  "
            f"{pair['max_complex']:>8.4f}"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


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
    lines.append("! VisualopenEMS — S-parameters")
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


def network_to_sparam_dict(network) -> dict[str, np.ndarray]:
    """Convert a scikit-rf ``Network`` into the app's ``Sij`` dict format."""
    if network is None:
        raise ValueError("No S-parameter network to export")
    s = np.asarray(network.s, dtype=complex)
    if s.ndim != 3:
        raise ValueError("S-parameter matrix must have shape (freq, port, port)")
    n = int(s.shape[1])
    out: dict[str, np.ndarray] = {
        "freq": np.asarray(network.f, dtype=float),
    }
    for i in range(n):
        for j in range(n):
            out[f"S{i + 1}{j + 1}"] = s[:, i, j]
    return out


def _network_z0(network) -> float:
    try:
        z0 = float(np.real(np.asarray(network.z0, dtype=complex).ravel()[0]))
    except Exception:  # noqa: BLE001
        z0 = 50.0
    return z0 if np.isfinite(z0) and z0 > 0 else 50.0


def export_network_touchstone(
    network,
    out_path: Path | str,
    comments: list[str] | None = None,
) -> Path:
    """Write a scikit-rf ``Network`` as a Touchstone ``.sNp`` file."""
    data = network_to_sparam_dict(network)
    return write_touchstone(
        data,
        out_path,
        z0=_network_z0(network),
        comments=list(comments or []),
    )


def _fmt_sparam(value: float) -> str:
    value = float(value)
    return "nan" if not np.isfinite(value) else f"{value:.12g}"


def _sparam_table_columns(network) -> tuple[np.ndarray, list[tuple[str, np.ndarray]]]:
    data = network_to_sparam_dict(network)
    freqs = np.asarray(data.pop("freq"), dtype=float)
    columns: list[tuple[str, np.ndarray]] = []
    for key, values in data.items():
        columns.append((key, np.asarray(values, dtype=complex)))
    return freqs, columns


def write_sparam_csv(network, out_path: Path | str) -> Path:
    """Write frequency and every Sij as dB / phase / real / imaginary (CSV)."""
    freqs, columns = _sparam_table_columns(network)
    out_path = Path(out_path)
    if out_path.suffix.lower() != ".csv":
        out_path = out_path.with_suffix(".csv")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    header = ["freq_Hz", "freq_GHz"]
    for key, _s in columns:
        header.extend(
            [f"{key}_dB", f"{key}_deg", f"{key}_real", f"{key}_imag"]
        )
    lines = [",".join(header)]
    for i, freq in enumerate(freqs):
        row = [_fmt_sparam(freq), _fmt_sparam(freq / 1e9)]
        for _key, s in columns:
            value = complex(s[i])
            mag = abs(value)
            db = 20.0 * np.log10(max(mag, 1e-30))
            phase = np.degrees(np.angle(value))
            row.extend(
                [
                    _fmt_sparam(db),
                    _fmt_sparam(phase),
                    _fmt_sparam(value.real),
                    _fmt_sparam(value.imag),
                ]
            )
        lines.append(",".join(row))
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def write_sparam_ascii(network, out_path: Path | str) -> Path:
    """Write an ASCII table (frequency in GHz, Sij dB/phase/re/im)."""
    freqs, columns = _sparam_table_columns(network)
    out_path = Path(out_path)
    if out_path.suffix.lower() not in (".txt", ".dat", ".asc"):
        out_path = out_path.with_suffix(".txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    header = ["# freq_GHz"]
    for key, _s in columns:
        header.extend(
            [f"{key}_dB", f"{key}_deg", f"{key}_real", f"{key}_imag"]
        )
    lines = [" ".join(header)]
    for i, freq in enumerate(freqs):
        row = [_fmt_sparam(freq / 1e9)]
        for _key, s in columns:
            value = complex(s[i])
            mag = abs(value)
            db = 20.0 * np.log10(max(mag, 1e-30))
            phase = np.degrees(np.angle(value))
            row.extend(
                [
                    _fmt_sparam(db),
                    _fmt_sparam(phase),
                    _fmt_sparam(value.real),
                    _fmt_sparam(value.imag),
                ]
            )
        lines.append("\t".join(row))
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

    s_json = {
        "freq": np.asarray(sparams.get("freq", []), dtype=float).tolist(),
        "z0": float(z0),
    }
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
