"""Polarization (axial ratio) and phase-center estimates from NF2FF data.

openEMS itself does not report these quantities; they are derived here from the
complex far field (``E_theta``/``E_phi``) that :mod:`app.postproc.nf2ff` stores
in ``nf2ff.json``. Nothing in this module runs the solver or changes a result.

Conventions (explicit, because both quantities depend on them):

* The fields follow openEMS' ``exp(+j*omega*t)`` / ``exp(-j*k*r)`` convention.
* Axial ratio from the Stokes parameters::

      S0 = |Et|^2 + |Ep|^2
      S1 = |Et|^2 - |Ep|^2
      S2 = 2 Re(Et Ep*)
      S3 = 2 Im(Et Ep*)
      L  = sqrt(S1^2 + S2^2)                  (linear part)
      AR = sqrt((S0 + L) / (S0 - L))

  ``AR >= 1`` (linear), ``AR_dB = 20 log10(AR)``. Linear polarization has
  ``S0 == L`` and therefore an infinite AR (shown as ``inf``, not a fake
  0 dB). ``S3`` only decides the handedness, it does not enter the AR.
* Handedness: ``S3 > 0`` is labelled **RHCP** with the convention above. Verify
  it once against a known circular antenna; pass ``flip_sense=True`` to swap the
  label if your convention differs.
* Phase center: relative to the NF2FF reference point (the box centre openEMS
  used). A displacement ``dr`` shifts the far-field phase by ``-k (r_hat . dr)``;
  a linear least-squares fit over the main beam gives ``dr``. It is an
  **estimate** for a well-defined main beam, not a solver fact.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

C0_MM_S = 299_792_458_000.0  # speed of light in mm/s

#: AR values above this are treated as "linear" in tables (dB)
LINEAR_AR_DB = 40.0


# --------------------------------------------------------------------------- #
# field access
# --------------------------------------------------------------------------- #
def complex_field(payload: dict, key: str, freq_index: int = 0):
    """Return one frequency's complex field (``n_theta, n_phi``) or ``None``.

    ``nf2ff.json`` stores ``E_theta``/``E_phi`` as a list (per frequency) of
    ``{"real": [...], "imag": [...]}`` dicts; older files may hold a plain
    complex array.
    """
    val = payload.get(key)
    if not val:
        return None
    try:
        items = [val] if isinstance(val, dict) else list(val)
        item = items[freq_index]
        if isinstance(item, dict):
            real = np.asarray(item.get("real"), dtype=float)
            imag = np.asarray(item.get("imag"), dtype=float)
            return real + 1j * imag
        return np.asarray(item, dtype=complex)
    except (IndexError, TypeError, ValueError):
        return None


def fields(payload: dict, freq_index: int = 0):
    """(E_theta, E_phi, theta_deg, phi_deg, D_dBi) for one frequency."""
    e_theta = complex_field(payload, "E_theta", freq_index)
    e_phi = complex_field(payload, "E_phi", freq_index)
    theta = _float_array(payload.get("theta_deg"))
    phi = _float_array(payload.get("phi_deg"))
    d_dbi = None
    raw = payload.get("D_dBi")
    if raw is not None:
        try:
            d_dbi = np.asarray(raw[freq_index], dtype=float)
        except (IndexError, TypeError, ValueError):
            d_dbi = None
    return e_theta, e_phi, theta, phi, d_dbi


def _float_array(value) -> np.ndarray | None:
    if value is None:
        return None
    try:
        return np.asarray(value, dtype=float)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# axial ratio
# --------------------------------------------------------------------------- #
def stokes(e_theta, e_phi) -> tuple[np.ndarray, ...]:
    et = np.asarray(e_theta, dtype=complex)
    ep = np.asarray(e_phi, dtype=complex)
    s0 = np.abs(et) ** 2 + np.abs(ep) ** 2
    s1 = np.abs(et) ** 2 - np.abs(ep) ** 2
    s2 = 2.0 * np.real(et * np.conj(ep))
    s3 = 2.0 * np.imag(et * np.conj(ep))
    return s0, s1, s2, s3


def axial_ratio(e_theta, e_phi) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(AR, AR_dB, tilt_deg, sense) per direction.

    ``AR`` is >= 1 (``inf`` for linear polarization), ``sense`` is ``"RHCP"`` /
    ``"LHCP"`` / ``"linear"``. ``tilt_deg`` is the polarization-ellipse tilt
    (-90..90 deg).
    """
    s0, s1, s2, s3 = stokes(e_theta, e_phi)
    # Linear part of the polarization ellipse (S3 is the circular part and
    # does NOT enter the axial ratio; it only decides the handedness).
    linear_part = np.sqrt(s1 * s1 + s2 * s2)
    denom = s0 - linear_part
    with np.errstate(divide="ignore", invalid="ignore"):
        ar = np.sqrt(np.maximum((s0 + linear_part) / denom, 1.0))
    ar = np.where(np.isfinite(ar), ar, np.inf)
    with np.errstate(divide="ignore", invalid="ignore"):
        ar_db = 20.0 * np.log10(ar)
    ar_db = np.where(np.isfinite(ar_db), ar_db, np.inf)
    # a numerically circular field can produce tiny negative denominators
    ar_db = np.where(s0 <= 0.0, np.nan, ar_db)
    tilt = 0.5 * np.degrees(np.arctan2(s2, s1))
    sense = np.where(s3 > 0.0, "RHCP", "LHCP").astype(object)
    linear = (~np.isfinite(ar_db)) | (ar_db >= LINEAR_AR_DB) | (s0 <= 0.0)
    sense = np.where(linear, "linear", sense).astype(object)
    return ar, ar_db, tilt, sense


def peak_index(e_theta, e_phi, d_dbi=None) -> tuple[int, int]:
    """Index of the pattern peak (directivity, else |E|^2)."""
    if d_dbi is not None:
        try:
            d = np.asarray(d_dbi, dtype=float)
            if d.shape == np.asarray(e_theta).shape and np.isfinite(d).any():
                return tuple(int(v) for v in np.unravel_index(np.nanargmax(d), d.shape))
        except (TypeError, ValueError):
            pass
    power = np.abs(np.asarray(e_theta, complex)) ** 2 + np.abs(
        np.asarray(e_phi, complex)
    ) ** 2
    return tuple(int(v) for v in np.unravel_index(int(np.nanargmax(power)), power.shape))


def ar_summary(payload: dict, flip_sense: bool = False) -> list[dict[str, Any]]:
    """One row per frequency: AR at the pattern peak, sense, tilt, peak angle."""
    freqs = payload.get("frequencies") or []
    rows: list[dict[str, Any]] = []
    for fi, freq in enumerate(freqs):
        e_theta, e_phi, theta, phi, d_dbi = fields(payload, fi)
        if e_theta is None or e_phi is None:
            continue
        _ar, ar_db, tilt, sense = axial_ratio(e_theta, e_phi)
        i_t, i_p = peak_index(e_theta, e_phi, d_dbi)
        ar_val = float(ar_db[i_t, i_p])
        sense_val = str(sense[i_t, i_p])
        if sense_val != "linear" and flip_sense:
            sense_val = "LHCP" if sense_val == "RHCP" else "RHCP"
        rows.append(
            {
                "frequency": float(freq),
                "ar_db": ar_val,
                "ar": math.inf if not math.isfinite(ar_val) else 10 ** (ar_val / 20.0),
                "sense": sense_val,
                "tilt_deg": float(tilt[i_t, i_p]),
                "theta_deg": float(theta[i_t]) if theta is not None else float("nan"),
                "phi_deg": float(phi[i_p]) if phi is not None else float("nan"),
                "peak_dbi": float(d_dbi[i_t, i_p])
                if d_dbi is not None
                else float("nan"),
            }
        )
    return rows


def ar_bandwidth(rows: list[dict[str, Any]], limit_db: float = 3.0) -> float | None:
    """Fraction (0..1) of the evaluated frequencies with AR <= ``limit_db``."""
    if not rows:
        return None
    good = [r for r in rows if math.isfinite(r["ar_db"]) and r["ar_db"] <= limit_db]
    return len(good) / len(rows)


# --------------------------------------------------------------------------- #
# phase center
# --------------------------------------------------------------------------- #
def phase_center_cut(
    payload: dict,
    freq_index: int = 0,
    phi_deg: float = 0.0,
    beam_db: float = 10.0,
    component: str | None = None,
    max_span_deg: float = 45.0,
) -> dict[str, Any] | None:
    """Estimate the phase-center offset from one ``phi`` cut.

    Fits ``psi(theta) = psi0 + a*sin(theta) + b*cos(theta)`` over the main beam
    (``D`` within ``beam_db`` of the cut peak, or ``|E|`` when no directivity is
    stored). ``dz = -b/k`` and the in-cut lateral offset ``-a/k`` (``dx`` for
    phi = 0, ``dy`` for phi = 90). Returns ``None`` when fewer than 5 usable
    points exist.

    ``component``: ``"theta"`` / ``"phi"``; ``None`` picks the stronger one at
    the cut peak (the co-pol component).
    """
    e_theta, e_phi, theta, phi, d_dbi = fields(payload, freq_index)
    if e_theta is None or e_phi is None or theta is None or theta.size < 5:
        return None
    if phi is None or phi.size == 0:
        return None
    freqs = payload.get("frequencies") or []
    try:
        freq = float(freqs[freq_index])
    except (IndexError, TypeError, ValueError):
        return None
    if freq <= 0.0:
        return None

    # nearest phi column (phi is in degrees, may be 0..360)
    p = float(phi_deg) % 360.0
    diffs = np.abs(((phi - p + 180.0) % 360.0) - 180.0)
    j = int(np.argmin(diffs))
    if float(diffs[j]) > 1.0:
        return None

    et = np.asarray(e_theta[:, j], dtype=complex)
    ep = np.asarray(e_phi[:, j], dtype=complex)
    th = np.radians(np.asarray(theta, dtype=float))

    if component not in ("theta", "phi"):
        k_peak = int(np.argmax(np.abs(et) ** 2 + np.abs(ep) ** 2))
        component = "theta" if abs(et[k_peak]) >= abs(ep[k_peak]) else "phi"
    field = et if component == "theta" else ep

    power = np.abs(field) ** 2
    k_peak = int(np.argmax(power))
    if power[k_peak] <= 0.0:
        return None
    if d_dbi is not None and d_dbi.shape == np.asarray(e_theta).shape:
        d_cut = np.asarray(d_dbi[:, j], dtype=float)
        if np.isfinite(d_cut).any():
            limit = float(np.nanmax(d_cut)) - float(beam_db)
            mask = np.isfinite(d_cut) & (d_cut >= limit)
        else:
            mask = power >= power[k_peak] * 10 ** (-beam_db / 10.0)
    else:
        mask = power >= power[k_peak] * 10 ** (-beam_db / 10.0)
    # keep a contiguous window around the peak (a sidelobe above the threshold
    # must not enter the fit)
    idx = np.where(mask)[0]
    if idx.size < 5:
        return None
    # contiguous block containing the peak
    peak_pos = int(np.argmin(np.abs(idx - k_peak)))
    lo = hi = peak_pos
    while lo > 0 and idx[lo - 1] == idx[lo] - 1:
        lo -= 1
    while hi < idx.size - 1 and idx[hi + 1] == idx[hi] + 1:
        hi += 1
    sel = idx[lo : hi + 1]
    if sel.size < 5:
        return None
    # Main-lobe window: a broad pattern (dipole) stays within 10 dB almost to
    # the nulls, where the phase is meaningless. Fit only +-max_span around the
    # peak, which is where a phase center is defined.
    if max_span_deg and max_span_deg > 0:
        keep = (
            np.abs(np.degrees(th[sel]) - np.degrees(th[k_peak]))
            <= float(max_span_deg)
        )
        sel = sel[keep]
        if sel.size < 5:
            return None

    phase = np.unwrap(np.angle(field[sel]))
    th_sel = th[sel]
    weight = np.sqrt(np.maximum(power[sel], 0.0))
    weight = weight / weight.max() if weight.max() > 0 else weight

    # weighted least squares: psi = psi0 + a*sin + b*cos
    design = np.column_stack(
        [np.ones_like(th_sel), np.sin(th_sel), np.cos(th_sel)]
    )
    w = weight
    try:
        coeff, *_ = np.linalg.lstsq(design * w[:, None], phase * w, rcond=None)
    except np.linalg.LinAlgError:
        return None
    psi0, a, b = (float(v) for v in coeff)
    k = 2.0 * math.pi * freq / C0_MM_S  # 1/mm
    dz = -b / k
    lateral = -a / k
    residual = (design @ coeff) - phase
    rms_deg = float(np.degrees(np.sqrt(np.average(residual**2, weights=w**2))))

    # Quality gate: a phase center outside the NF2FF sphere (or a fit with a
    # large residual) is not a phase center but a phase wrap / sidelobe mix.
    r_mm = None
    try:
        r_raw = payload.get("r")
        if r_raw is not None:
            arr = np.asarray(r_raw, dtype=float).reshape(-1)
            if arr.size and np.isfinite(arr[0]):
                r_mm = float(arr[0])
    except (TypeError, ValueError):
        r_mm = None
    limit = r_mm if (r_mm is not None and r_mm > 0) else 5.0 * C0_MM_S / freq
    ok = rms_deg <= 25.0 and abs(dz) <= limit and abs(lateral) <= limit

    return {
        "frequency": freq,
        "phi_deg": float(p),
        "component": component,
        "dz_mm": dz,
        "lateral_mm": lateral,
        "rms_deg": rms_deg,
        "n_points": int(sel.size),
        "beam_db": float(beam_db),
        "theta_min_deg": float(np.degrees(th_sel.min())),
        "theta_max_deg": float(np.degrees(th_sel.max())),
        "ok": bool(ok),
        "limit_mm": float(limit),
    }


def phase_center_summary(
    payload: dict, beam_db: float = 10.0, max_span_deg: float = 45.0
) -> list[dict[str, Any]]:
    """One row per frequency with dx/dy (from phi=0/90) and dz.

    ``dx``/``dy`` come from the two orthogonal cuts; ``dz`` is the mean of the
    cuts that produced an estimate. Missing cuts give ``nan``.
    """
    freqs = payload.get("frequencies") or []
    rows: list[dict[str, Any]] = []
    for fi, freq in enumerate(freqs):
        cut0 = phase_center_cut(payload, fi, 0.0, beam_db, max_span_deg=max_span_deg)
        cut90 = phase_center_cut(payload, fi, 90.0, beam_db, max_span_deg=max_span_deg)
        ok0 = cut0 is not None and bool(cut0.get("ok", True))
        ok90 = cut90 is not None and bool(cut90.get("ok", True))
        good = [c for c, ok in ((cut0, ok0), (cut90, ok90)) if ok]
        bad = [c for c, ok in ((cut0, ok0), (cut90, ok90)) if c is not None and not ok]
        dzs = [c["dz_mm"] for c in good]
        rms = [c["rms_deg"] for c in good]
        rows.append(
            {
                "frequency": float(freq),
                "dx_mm": float(cut0["lateral_mm"]) if ok0 else float("nan"),
                "dy_mm": float(cut90["lateral_mm"]) if ok90 else float("nan"),
                "dz_mm": float(np.mean(dzs)) if dzs else float("nan"),
                "rms_deg": float(max(rms)) if rms else float("nan"),
                "n_points": int(sum(c["n_points"] for c in good)),
                "component": "+".join(sorted({c["component"] for c in good})),
                "ok": bool(good),
                "note": ""
                if good
                else (
                    "phase not well-defined (residual/offset too large)"
                    if bad
                    else "no main-beam fit"
                ),
            }
        )
    return rows


# --------------------------------------------------------------------------- #
# text summary (log / report)
# --------------------------------------------------------------------------- #
def summary_lines(
    payload: dict, flip_sense: bool = False, beam_db: float = 10.0
) -> list[str]:
    """Human-readable block for the log / report (never claims more than it is)."""
    ar_rows = ar_summary(payload, flip_sense=flip_sense)
    pc_rows = phase_center_summary(payload, beam_db=beam_db)
    if complex_field(payload, "E_theta") is None or complex_field(payload, "E_phi") is None:
        return ["Polarization: no complex E_theta/E_phi in this NF2FF result."]
    if not ar_rows:
        return ["Polarization: no frequencies in this NF2FF result."]
    out = ["Polarization / phase center (derived from E_theta/E_phi):"]
    for ar, pc in zip(ar_rows, pc_rows):
        f_ghz = ar["frequency"] / 1e9
        ar_txt = (
            f"{ar['ar_db']:.1f} dB"
            if math.isfinite(ar["ar_db"])
            else "linear (>40 dB)"
        )
        out.append(
            f"  {f_ghz:8.3f} GHz  AR {ar_txt:>16s}  {ar['sense']:<6s}"
            f"  tilt {ar['tilt_deg']:+6.1f} deg"
            f"  peak {ar['theta_deg']:5.1f}/{ar['phi_deg']:5.1f} deg"
        )
        if pc.get("ok") and math.isfinite(pc["dz_mm"]):
            out.append(
                f"                    phase center dx {pc['dx_mm']:+7.2f}"
                f"  dy {pc['dy_mm']:+7.2f}  dz {pc['dz_mm']:+7.2f} mm"
                f"  (rms {pc['rms_deg']:.1f} deg, {pc['n_points']} pts)"
            )
        else:
            out.append(
                f"                    phase center: {pc.get('note') or 'not available'}"
            )
    out.append(
        "  AR from the Stokes parameters; sense is the sign of S3 (flip if your"
        " convention differs)."
    )
    out.append(
        "  Phase center is a least-squares estimate relative to the NF2FF box"
        " centre, valid in the main beam only."
    )
    return out
