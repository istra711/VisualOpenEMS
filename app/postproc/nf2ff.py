"""NF2FF post-processing via openEMS Python bindings.

Produces, for a completed simulation:
  * ``nf2ff/nf2ff_result.h5``  -- openEMS native archive (written by CalcNF2FF)
  * ``nf2ff/nf2ff.json``       -- pattern data for the GUI Antenna tab
  * ``nf2ff/nf2ff_summary.png``-- headless summary (cuts + gain vs frequency)

Directional data: directivity ``D(theta,phi)`` derived from the radial
Poynting density ``P_rad`` and total radiated power ``Prad`` (spherical
integral of ``P_rad``); absolute gain adds the radiation efficiency
``eta = Prad / P_acc`` where ``P_acc`` is the accepted port power from the
port time series (pulse-DFT, same spectral units as ``Prad``, so the ratio
is unitless).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from app.models.project import NF2FFDef, Project

# per-token unit suffixes for frequency list inputs (bare number => Hz)
FREQ_TOKEN_SCALES: dict[str, float] = {
    "hz": 1.0,
    "khz": 1e3,
    "mhz": 1e6,
    "ghz": 1e9,
}

_FREQ_TOKEN_RE = re.compile(
    r"([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([A-Za-z]*)"
)


def parse_freq_list(text: str) -> list[float]:
    """Parse frequencies in Hz from a text like ``"2.4 GHz, 2450 MHz, 2.45e9"``.

    Tokens are separated by commas/semicolons or spaces; each number may carry
    a unit suffix (Hz / kHz / MHz / GHz, case-insensitive, optional space
    before the unit); a bare number is treated as Hz. Invalid segments or
    tokens are dropped silently.
    """
    out: list[float] = []
    for seg in str(text).replace(";", ",").split(","):
        seg = seg.strip()
        if not seg:
            continue
        for num, unit in _freq_tokens(seg):
            try:
                v = float(num)
            except ValueError:
                continue
            if unit:
                scale = FREQ_TOKEN_SCALES.get(unit.lower())
                if scale is None:
                    continue  # unknown unit -> drop token
            else:
                scale = 1.0  # bare number = Hz
            if v > 0:
                out.append(v * scale)
    return out


def _freq_tokens(seg: str) -> list[tuple[str, str]]:
    """Number+unit tokens of one segment; [] if the segment contains junk."""
    entries: list[tuple[str, str]] = []
    pos = 0
    for m in _FREQ_TOKEN_RE.finditer(seg):
        if seg[pos : m.start()].strip():
            return []  # unexpected text between tokens -> reject segment
        entries.append((m.group(1), m.group(2)))
        pos = m.end()
    if seg[pos:].strip():
        return []
    return entries


def resolve_eval_frequencies(
    project: Project, s_table: dict[str, Any] | None = None
) -> list[float]:
    """NF2FF evaluation frequencies (Hz) from the project settings.

    Modes (``project.nf2ff.freq_mode``):
      * ``"auto"``  — S11 minimum from ``s_table`` (fall back to the target
        frequency / mesh stop frequency).
      * ``"count"`` — ``freq_count`` points equally spaced over the mesh
        frequency range (``start_frequency``..``stop_frequency``).
      * ``"list"``  — explicit ``frequencies`` (falls back to auto if empty).
    """
    nf = project.nf2ff
    mode = str(getattr(nf, "freq_mode", "auto") or "auto").lower()
    if mode == "count":
        f0 = float(project.mesh.start_frequency or 0.0)
        f1 = float(project.mesh.stop_frequency or 0.0)
        n = int(getattr(nf, "freq_count", 0) or 0)
        if f1 <= f0:
            f0 = f1 = float(project.mesh.target_frequency or f1 or 1e9)
        if n <= 1:
            return [float(project.mesh.target_frequency or 0.5 * (f0 + f1))]
        return [float(f) for f in np.linspace(f0, f1, n)]
    if mode == "list":
        freqs = [float(f) for f in (nf.frequencies or []) if f]
        if freqs:
            return freqs
    # auto (or list with no entries): S11 minimum, else target/stop frequency
    f_eval = [float(project.mesh.target_frequency or project.mesh.stop_frequency)]
    if s_table:
        s11 = np.asarray(s_table.get("S11", []))
        faxis = np.asarray(s_table.get("freq", []), dtype=float)
        if s11.size and faxis.size and np.any(np.isfinite(s11)):
            mag = np.abs(s11)
            db = 20 * np.log10(np.maximum(mag, 1e-12))
            # nan-safe min; empty/invalid data -> keep default eval freq
            good = np.isfinite(db)
            if np.any(good):
                idx = int(np.nanargmin(np.where(good, db, np.inf)))
                f_eval = [float(faxis[idx])]
    return f_eval


def _to_lists(val: Any) -> Any:
    if isinstance(val, (list, tuple, np.ndarray)):
        return np.asarray(val).tolist()
    return val


def _as_float_array(val: Any, ndim: int | None = None) -> np.ndarray | None:
    """Best-effort convert an nf2ff attribute to a float ndarray."""
    if val is None:
        return None
    try:
        arr = np.asarray(val, dtype=float)
    except Exception:  # noqa: BLE001
        return None
    if ndim is not None and arr.ndim != ndim:
        return None
    return arr


def calc_nf2ff_from_box(
    sim_dir: Path | str,
    nf2ff_box,
    frequencies: list[float],
    nf2ff_def: NF2FFDef,
    out_dir: Path | str,
    pin: list[float] | np.ndarray | None = None,
) -> dict[str, Any]:
    """Call nf2ff_box.CalcNF2FF(sim_path, freq, theta, phi, ...) and harvest results.

    ``theta``/``phi`` are passed to the openEMS binding in **degrees** (the
    binding converts to radians internally via ``np.deg2rad``).

    ``pin`` (optional) is the accepted power per frequency (same spectral
    units as ``Prad``), used to turn directivity into absolute gain:
    ``eta = Prad / Pin``.
    """
    sim_dir = Path(sim_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "nf2ff_result.h5"

    freqs = [float(f) for f in frequencies if f]
    if not freqs:
        freqs = [1e9]

    # openEMS Python binding expects DEGREES for theta/phi (see
    # openEMS/nf2ff.py:130 np.deg2rad(theta); self-check uses rad2deg).
    theta = np.linspace(0.0, 180.0, max(int(nf2ff_def.theta_steps), 8))
    phi = np.linspace(0.0, 360.0, max(int(nf2ff_def.phi_steps), 8))

    result = nf2ff_box.CalcNF2FF(
        str(sim_dir),
        freqs,
        theta,
        phi,
        radius=float(nf2ff_def.radius),
        center=[float(x) for x in nf2ff_def.center],
        outfile=str(out_file),
        read_cached=False,
        verbose=0,
    )

    summary: dict[str, Any] = {"out_file": str(out_file), "frequencies": freqs}
    # Harvest everything openEMS provides. The binding stores E_* as per-freq
    # lists of (n_theta, n_phi) complex arrays and scalars as arrays over freq.
    for attr in (
        "Prad",
        "Dmax",
        "E_norm",
        "E_theta",
        "E_phi",
        "P_rad",
        "theta",
        "phi",
        "freq",
        "r",
    ):
        if hasattr(result, attr):
            try:
                summary[attr] = _to_lists(getattr(result, attr))
            except Exception:  # noqa: BLE001
                pass

    # Raw scalar values (floats) for easy access downstream.
    summary["Prad_arr"] = _as_float_array(summary.get("Prad"))
    summary["Dmax_arr"] = _as_float_array(summary.get("Dmax"))
    summary["freq_arr"] = _as_float_array(summary.get("freq"))

    # Angular grids in DEGREES as stored (result.theta/phi are radians from the
    # H5; the binding's self-check converts via rad2deg). Export degrees for the
    # viewer so no conversion is needed there.
    theta_deg = _as_float_array(summary.get("theta"))
    phi_deg = _as_float_array(summary.get("phi"))
    if theta_deg is not None:
        theta_deg = np.rad2deg(theta_deg) if np.nanmax(theta_deg) <= 2 * np.pi + 1e-6 else theta_deg
    if phi_deg is not None:
        phi_deg = np.rad2deg(phi_deg) if np.nanmax(phi_deg) <= 2 * np.pi + 1e-6 else phi_deg
    summary["theta_deg"] = theta_deg
    summary["phi_deg"] = phi_deg

    # Directivity + gain over the sphere.
    d_dbi, gain_dbi, efficiency, prad_corr = _compute_directivity_gain(result, freqs, pin)
    summary["D_dBi"] = d_dbi
    summary["gain_dBi"] = gain_dbi
    summary["efficiency"] = efficiency

    # openEMS's raw Prad/Dmax attributes can be sign-flipped (Prad < 0 was
    # observed with an interior box bottom face); the spherical integral of
    # P_rad is authoritative and matches the D normalization exactly.
    summary["Prad_openEMS"] = summary.get("Prad")
    summary["Dmax_openEMS"] = summary.get("Dmax")
    if prad_corr is not None and any(v is not None for v in prad_corr):
        prad_arr = np.asarray([np.nan if v is None else v for v in prad_corr], dtype=float)
        summary["Prad"] = _to_lists(prad_arr)
        summary["Prad_arr"] = prad_arr
    if d_dbi is not None and np.size(d_dbi):
        dmax_arr = np.nanmax(np.asarray(d_dbi, dtype=float), axis=(1, 2))
        summary["Dmax"] = _to_lists(dmax_arr)
        summary["Dmax_arr"] = dmax_arr

    # Single scalars for the summary line: prefer the eval frequency closest to
    # the requested list (first entry when single-freq).
    summary["Prad_scalar"] = _first_finite(summary["Prad_arr"])
    summary["Dmax_scalar"] = _first_finite(summary["Dmax_arr"])
    summary["efficiency_scalar"] = _first_finite(
        np.asarray(efficiency, dtype=float) if efficiency is not None else None
    )

    summary["summary"] = {
        "Prad": summary["Prad_scalar"],
        "Dmax": summary["Dmax_scalar"],
        "efficiency": summary["efficiency_scalar"],
        "frequencies": freqs,
        "out_file": str(out_file),
    }
    return summary


# Backwards-compatible alias
calc_nf2ff = calc_nf2ff_from_box


def _first_finite(arr: np.ndarray | None) -> float | None:
    if arr is None:
        return None
    a = np.asarray(arr, dtype=float).reshape(-1)
    if a.size == 0:
        return None
    finite = a[np.isfinite(a)]
    if finite.size == 0:
        return None
    return float(finite[0])


def _compute_directivity_gain(
    result: Any,
    freqs: list[float],
    pin: list[float] | np.ndarray | None,
) -> tuple[
    np.ndarray | None,
    np.ndarray | None,
    list[float | None] | None,
    list[float | None],
]:
    """Directivity (dBi), gain (dBi), efficiency and corrected Prad per freq.

    Directivity from the radial Poynting density ``P_rad`` (openEMS
    ``nf2ff_results.P_rad``, shape [n_freq, n_theta, n_phi]) and total radiated
    power ``Prad``:

        D(theta,phi) = 4*pi * r^2 * S_r(theta,phi) / Prad_total

    openEMS stores ``P_rad`` as the far-field power density S_r (W/m^2 at
    radius r), NOT as the total radiated power.  The total radiated power is
    ``Prad_total = r^2 * sum(P_rad*sin(theta))*dtheta*dphi`` (the r^2 factor
    converts density to power).  The spherical integral of the stored density
    is authoritative because openEMS's own Prad attribute can be sign-flipped
    (negative) while the stored ``P_rad`` density stays positive.  Gain adds
    the radiation efficiency: ``gain = D * eta`` with ``eta = Prad_total /
    Pin`` (``Pin`` = accepted port power, same spectral units).
    """
    try:
        p_rad = np.asarray(getattr(result, "P_rad", []), dtype=float)
    except Exception:  # noqa: BLE001
        return None, None, None, []
    if p_rad.ndim != 3 or p_rad.size == 0:
        return None, None, None, []

    theta = np.asarray(getattr(result, "theta", np.linspace(0, np.pi, p_rad.shape[1])), dtype=float)
    phi = np.asarray(getattr(result, "phi", np.linspace(0, 2 * np.pi, p_rad.shape[2])), dtype=float)
    prad = np.asarray(getattr(result, "Prad", np.full(p_rad.shape[0], np.nan)), dtype=float).reshape(-1)
    # Far-field radius used by the NF2FF transformation (metres).  P_rad is a
    # power *density* at this radius; multiply the spherical integral by r^2
    # to obtain the total radiated power.
    _r_arr = np.asarray(getattr(result, "r", 1.0), dtype=float).reshape(-1)
    radius = float(_r_arr[0]) if _r_arr.size else 1.0
    if not np.isfinite(radius) or radius <= 0:
        radius = 1.0
    n_f = p_rad.shape[0]

    sin_t = np.sin(theta)[:, None]  # (n_theta, 1)
    dtheta = float(theta[1] - theta[0]) if theta.size > 1 else 1.0
    dphi = float(phi[1] - phi[0]) if phi.size > 1 else 1.0

    d_dbi = np.full((n_f, p_rad.shape[1], p_rad.shape[2]), np.nan, dtype=float)
    eff = np.full(n_f, np.nan, dtype=float)
    prad_corr: list[float | None] = []

    for i in range(n_f):
        s_r = p_rad[i]
        # Spherical integral of the density S_r  ->  P_total / r^2
        prad_sph = float(np.nansum(s_r * sin_t) * dtheta * dphi)
        if np.isfinite(prad_sph) and prad_sph > 0:
            # Total radiated power (same spectral units as Pin).
            prad_total = prad_sph * radius**2
            # Directivity: D = 4*pi*S_r / (P_total/r^2) = 4*pi*S_r / prad_sph
            # (the r^2 factor cancels).
            with np.errstate(divide="ignore", invalid="ignore"):
                d_lin = 4.0 * np.pi * s_r / prad_sph
        else:
            # Fallback: openEMS Prad attribute IS the total radiated power.
            prad_total = float(prad[i]) if i < prad.size else np.nan
            if not np.isfinite(prad_total) or prad_total <= 0:
                prad_corr.append(None)
                continue
            # Directivity: D = 4*pi*r^2*S_r / P_total
            with np.errstate(divide="ignore", invalid="ignore"):
                d_lin = 4.0 * np.pi * s_r * radius**2 / prad_total
        prad_corr.append(prad_total)
        d_lin = np.where(np.isfinite(d_lin) & (d_lin > 0), d_lin, np.nan)
        d_dbi[i] = 10.0 * np.log10(d_lin)

        # Efficiency from accepted port power (same spectral units as Prad).
        if pin is not None:
            pin_arr = np.asarray(pin, dtype=float).reshape(-1)
            pin_i = float(pin_arr[i]) if i < pin_arr.size else np.nan
            if np.isfinite(pin_i) and pin_i > 0:
                eff[i] = prad_total / pin_i

    # Gain = D * eta (eta clipped to (0, 1] for physical reasonableness).
    gain_dbi = d_dbi.copy()
    for i in range(n_f):
        if np.isfinite(eff[i]) and eff[i] > 0:
            gain_dbi[i] = d_dbi[i] + 10.0 * np.log10(min(eff[i], 1.0))

    eff_list = [float(x) if np.isfinite(x) else None for x in eff]
    return d_dbi, gain_dbi, eff_list, prad_corr


def export_nf2ff_json(
    result: dict[str, Any],
    out_dir: Path | str,
    basename: str = "nf2ff",
) -> Path:
    """Write the pattern data to ``<out_dir>/<basename>.json`` for the GUI viewer.

    Arrays are stored as nested lists; angles are in degrees. Complex E-fields
    are split into real/imag components per frequency.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{basename}.json"

    freqs = [float(f) for f in (result.get("frequencies") or [])]
    theta_deg = result.get("theta_deg")
    phi_deg = result.get("phi_deg")

    payload: dict[str, Any] = {
        "frequencies": freqs,
        "theta_deg": _to_lists(theta_deg),
        "phi_deg": _to_lists(phi_deg),
        "Prad": _to_lists(result.get("Prad")),
        "Dmax_dBi": _to_lists(result.get("Dmax")),
        "efficiency": result.get("efficiency"),
        "D_dBi": _to_lists(result.get("D_dBi")),
        "gain_dBi": _to_lists(result.get("gain_dBi")),
        "P_rad": _to_lists(result.get("P_rad")),
        "out_file": result.get("out_file"),
    }

    # Complex E-fields -> {real, imag} lists per frequency.
    for key in ("E_theta", "E_phi", "E_norm"):
        val = result.get(key)
        if val is None:
            continue
        try:
            arrs = val if isinstance(val, (list, tuple)) else [val]
            payload[key] = [
                {
                    "real": np.real(np.asarray(a)).tolist(),
                    "imag": np.imag(np.asarray(a)).tolist(),
                }
                for a in arrs
            ]
        except Exception:  # noqa: BLE001
            pass

    json_path.write_text(json.dumps(payload), encoding="utf-8")
    print("Wrote JSON:", json_path)
    return json_path


def _load_pin_spectral(sim_dir: Path | str, freqs: list[float]) -> list[float] | None:
    """Accepted power P_acc(f) = 0.5*Re(U(f)*conj(I(f))) from port time series.

    Applies the openEMS pulse-DFT convention (``2 * sum(x*exp(-j2pi*f*t))*dt``,
    see ``openEMS.utilities.DFT_time2freq``), so P_acc lives in the same
    spectral units as the nf2ff ``Prad`` (W*s^2 for a pulse run) and the ratio
    ``eta = Prad / P_acc`` is a physical, unitless radiation efficiency.

    Uses the driven port (largest |U| at the first eval frequency). Returns
    None when no usable ``port_ut_*``/``port_it_*`` pair exists — eta/gain
    stay undefined instead of being computed from mismatched units.
    """
    sim_dir = Path(sim_dir)
    pairs: list[tuple[Path, Path]] = []
    for ut_path in sorted(sim_dir.glob("port_ut_*")):
        suffix = ut_path.name[len("port_ut_") :]
        it_path = sim_dir / f"port_it_{suffix}"
        if it_path.exists():
            pairs.append((ut_path, it_path))
    if not pairs or not freqs:
        return None

    f_arr = [float(f) for f in freqs]
    best: list[float] | None = None
    best_mag = -1.0
    for ut_path, it_path in pairs:
        try:
            ut = np.loadtxt(ut_path, comments="%")
            it = np.loadtxt(it_path, comments="%")
        except Exception:  # noqa: BLE001
            continue
        if ut.ndim != 2 or it.ndim != 2 or ut.shape[0] < 3 or it.shape[0] != ut.shape[0]:
            continue
        t = ut[:, 0]
        dts = np.diff(t)
        dt = float(np.mean(dts))
        if not np.isfinite(dt) or dt <= 0:
            continue

        def _dft(x: np.ndarray, f: float) -> complex:
            return complex(2.0 * np.sum(x * np.exp(-1j * 2.0 * np.pi * f * t)) * dt)

        try:
            p_acc = [float(0.5 * np.real(_dft(ut[:, 1], f) * np.conj(_dft(it[:, 1], f)))) for f in f_arr]
        except Exception:  # noqa: BLE001
            continue
        if not all(np.isfinite(p_acc)):
            continue
        mag = abs(_dft(ut[:, 1], f_arr[0]))
        if mag > best_mag:
            best_mag = mag
            best = p_acc
    return best


def _plane_cut(
    cut2d: np.ndarray, th: np.ndarray, ph: np.ndarray, phi0: float
) -> tuple[np.ndarray, np.ndarray]:
    """Theta cut at azimuth phi0, completed to a full 360 degree circle.

    The phi0 half (theta 0..180) plus the phi0+180 mirror half (theta 180..0)
    together form one plane through the antenna.
    """
    ip = int(np.argmin(np.abs(ph - phi0)))
    target = (phi0 + 180.0) % 360.0
    wrap = (ph - target + 180.0) % 360.0 - 180.0
    ip_back = int(np.argmin(np.abs(wrap)))
    front = np.asarray(cut2d[:, ip], dtype=float)
    back = np.asarray(cut2d[:, ip_back], dtype=float)
    ang = np.concatenate([np.deg2rad(th), np.deg2rad(360.0 - th[::-1])])
    vals = np.concatenate([front, back[::-1]])
    return ang, vals


def _radial_ticks(ax, vmin: float, vmax: float) -> None:
    """Nice radial (dB) tick steps: about five labels for any range."""
    from matplotlib.ticker import MultipleLocator

    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        return
    steps = (0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0)
    target = (vmax - vmin) / 5.0
    step = next((s for s in steps if s >= target), steps[-1])
    ax.yaxis.set_major_locator(MultipleLocator(step))


def _polar_slot(fig, slot):
    """Replace a cartesian grid slot with a polar axes in the same position.

    plt.subplots() already created a cartesian axes in the slot; adding a polar
    axes on top would leave the cartesian frame (0..1 ticks) visible behind it.
    """
    spec = slot.get_subplotspec()
    slot.remove()
    return fig.add_subplot(spec, projection="polar")


def plot_nf2ff(result: dict[str, Any], out_dir: Path | str) -> Path:
    """Write a headless summary PNG: E/H-plane cuts + gain/directivity vs freq."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "nf2ff_summary.png"

    freqs = np.asarray(result.get("frequencies", []), dtype=float).reshape(-1)
    theta_deg = result.get("theta_deg")
    phi_deg = result.get("phi_deg")
    dmax = _as_float_array(result.get("Dmax"))
    if dmax is None:
        dmax = _as_float_array(result.get("Dmax_dBi"))
    prad = _as_float_array(result.get("Prad"))
    eff = result.get("efficiency")
    d_dbi = result.get("D_dBi")
    gain_dbi = result.get("gain_dBi")

    # Prefer gain if present, else directivity, else raw |E_norm| cut.
    pat = gain_dbi if gain_dbi is not None else d_dbi
    has_pattern = pat is not None and theta_deg is not None and phi_deg is not None

    nrows = 2 if has_pattern else 1
    fig, axes = plt.subplots(nrows, 2, figsize=(10, 4 * nrows), squeeze=False)
    ax_d, ax_p = axes[0]

    def _freq_axis(n: int) -> np.ndarray:
        if freqs.size == n:
            return freqs
        return np.arange(n, dtype=float)

    # Directivity / gain vs frequency.
    if dmax is not None and dmax.size:
        f = _freq_axis(dmax.size)
        fdisp = f / 1e9 if np.nanmax(f) > 1e6 else f
        ax_d.plot(fdisp, dmax, "o-", label="Dmax [dBi]")
    if gain_dbi is not None:
        g = np.asarray(gain_dbi, dtype=float)
        gm = np.nanmax(g.reshape(g.shape[0], -1), axis=1)
        if gm.size:
            f = _freq_axis(gm.size)
            fdisp = f / 1e9 if np.nanmax(f) > 1e6 else f
            ax_d.plot(fdisp, gm, "s-", label="Gain max [dBi]")
    ax_d.set_xlabel("Frequency [GHz]" if (freqs.size and np.nanmax(freqs) > 1e6) else "Frequency")
    ax_d.set_ylabel("dBi")
    ax_d.set_title("Directivity / Gain")
    ax_d.grid(True, alpha=0.3)
    if dmax is not None or gain_dbi is not None:
        ax_d.legend(loc="best", fontsize=8)

    # Prad + efficiency vs frequency.
    if prad is not None and prad.size:
        f = _freq_axis(prad.size)
        fdisp = f / 1e9 if np.nanmax(f) > 1e6 else f
        ax_p.plot(fdisp, prad, "s-", color="tab:orange", label="Prad [W*s^2]")
    ax_p.set_xlabel("Frequency [GHz]" if (freqs.size and np.nanmax(freqs) > 1e6) else "Frequency")
    ax_p.set_ylabel("Prad [W*s^2]")
    ax_p.set_title("Radiated power")
    ax_p.grid(True, alpha=0.3)
    if eff is not None:
        e = np.asarray([x if x is not None else np.nan for x in eff], dtype=float).reshape(-1)
        if e.size and np.any(np.isfinite(e)):
            f = _freq_axis(e.size)
            fdisp = f / 1e9 if np.nanmax(f) > 1e6 else f
            ax2 = ax_p.twinx()
            ax2.plot(fdisp, e, "^--", color="tab:green", label="eta")
            ax2.set_ylabel("Efficiency eta")
            ax2.set_ylim(0, 1.05)

    # Polar cuts on the second row (gain/directivity in dB, normalised to max).
    if has_pattern:
        pat_arr = np.asarray(pat, dtype=float)
        th = np.asarray(theta_deg, dtype=float).reshape(-1)
        ph = np.asarray(phi_deg, dtype=float).reshape(-1)
        fi = 0
        if pat_arr.ndim == 3 and pat_arr.shape[0] > 0:
            cut2d = pat_arr[fi]
        else:
            cut2d = pat_arr
        cut2d = np.asarray(cut2d, dtype=float)
        label = "Gain [dBi]" if gain_dbi is not None else "Directivity [dBi]"

        # E-plane: theta cut at phi=90, completed with the phi=270 mirror half.
        if cut2d.ndim == 2 and cut2d.shape == (th.size, ph.size) and ph.size:
            ang_e, cut_e = _plane_cut(cut2d, th, ph, 90.0)
            axe = _polar_slot(fig, axes[1][0])
            axe.plot(ang_e, cut_e)
            axe.set_title(f"E-Plane (phi=90) {label}", fontsize=9)
            axe.set_theta_zero_location("N")
            axe.set_theta_direction(-1)
            _radial_ticks(axe, float(np.nanmin(cut_e)), float(np.nanmax(cut_e)))
            axe.set_rlabel_position(90)

        # H-plane: theta cut at phi=0, completed with the phi=180 mirror half.
        if cut2d.ndim == 2 and cut2d.shape == (th.size, ph.size) and ph.size:
            ang_h, cut_h = _plane_cut(cut2d, th, ph, 0.0)
            axh = _polar_slot(fig, axes[1][1])
            axh.plot(ang_h, cut_h)
            axh.set_title(f"H-Plane (phi=0) {label}", fontsize=9)
            axh.set_theta_zero_location("N")
            axh.set_theta_direction(-1)
            _radial_ticks(axh, float(np.nanmin(cut_h)), float(np.nanmax(cut_h)))
            axh.set_rlabel_position(90)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    return out_path


def compute_and_export(
    sim_dir: Path | str,
    nf2ff_box,
    frequencies: list[float],
    nf2ff_def: NF2FFDef,
    out_dir: Path | str,
) -> dict[str, Any]:
    """End-to-end NF2FF postproc: calc + JSON + PNG; eta from P_acc (port DFT)."""
    out_dir = Path(out_dir)
    pin = _load_pin_spectral(sim_dir, frequencies)
    result = calc_nf2ff_from_box(sim_dir, nf2ff_box, frequencies, nf2ff_def, out_dir, pin=pin)
    try:
        export_nf2ff_json(result, out_dir)
    except Exception as e:  # noqa: BLE001
        print("NF2FF JSON export warning:", e)
    try:
        plot_nf2ff(result, out_dir)
    except Exception as e:  # noqa: BLE001
        print("NF2FF plot warning:", e)
    return result
