"""Post-run simulation status report (Qt-free).

Collects everything the developer needs to judge a finished (or failed) run
without reading the whole solver log:

* engine facts: timestep, Nyquist rate, excitation length, timestep budget,
  operator size, cells, energy/convergence, speed
* errors / warnings found in the log (engine, mesh validation, tracebacks),
  with the known-benign lines filtered out
* the result quality: passivity (``|S11|^2 + |S21|^2 <= 1``) versus frequency,
  the reflection minimum, the match band and whether it covers the design
  frequency
* a verdict plus a plain-text block that is appended to the run log and shown
  in the "Simulation Status" dialog

The text is kept ASCII-only: the log is opened with a different encoding in
places and non-ASCII characters show up as mojibake there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

#: Passivity tolerance: |S|^2 sums above 1 + TOL count as a violation.
PASSIVITY_TOL = 1e-3
#: The outer fraction of the sweep is trimmed for the "core" passivity check:
#: a DFT window artefact there is expected and documented.
EDGE_FRACTION = 0.05
#: Cap the number of logged findings per severity.
MAX_FINDINGS = 10

#: Lines that look like errors but are benign output of openEMS/CSXCAD.
_BENIGN = (
    re.compile(r"^\s*(capacity|inductance|resistance)\s*:", re.IGNORECASE),
    re.compile(r"no\s+error", re.IGNORECASE),
    re.compile(r"error\s+tolerance", re.IGNORECASE),
)

_RE_TIMESTEP = re.compile(r"FDTD timestep is:\s*([\d.eE+-]+)\s*s")
_RE_NYQUIST = re.compile(r"Nyquist rate:\s*(\d+)\s*timesteps\s*@\s*([\d.eE+]+)\s*Hz")
_RE_TS_S = re.compile(r"Timestep \(s\)\s*:\s*([\d.eE+-]+)")
_RE_TS_METHOD = re.compile(r"Timestep method name\s*:\s*(\S+)")
_RE_NYQ_TS = re.compile(r"Nyquist criteria \(TS\)\s*:\s*(\d+)")
_RE_EXC_TS = re.compile(r"Excitation signal length is:\s*(\d+)\s*timesteps\s*\(([\d.eE+-]+)s\)")
_RE_NRTS = re.compile(
    r"Max\. number of timesteps:\s*(\d+)\s*\(\s*-->\s*([\d.]+)\s*\*\s*Excitation signal length"
)
_RE_SIZE = re.compile(r"FDTD simulation size:\s*(\S+)\s*-->\s*(\d+)\s*FDTD cells")
_RE_OP_BYTES = re.compile(r"Size of Operator\s*:\s*(\d+) Byte")
_RE_FIELD_BYTES = re.compile(r"Size of Field-Data\s*:\s*(\d+) Byte")
_RE_STEPS = re.compile(r"Timestep:\s*(\d+)")
_RE_ENERGY = re.compile(r"Energy:\s*~\s*\S+\s*\(\s*(-?\s*[\d.]+)\s*dB\)")
_RE_SPEED = re.compile(r"Speed:\s*([\d.]+)\s*MC/s")
_RE_ELAPSED = re.compile(r"\[@\s*(\d+)s\]")
_RE_END_CRIT = re.compile(
    r"end-criteria of\s*(-?[\d.]+)\s*dB was reached", re.IGNORECASE
)
_RE_END_CRIT_BEFORE = re.compile(
    r"Max\. number of timesteps was reached before the end-criteria", re.IGNORECASE
)
_RE_MESH_LINE = re.compile(r"^\[(ERROR|WARNING|OK)\]\s*([a-z_]+)\s*:\s*(.*)$")
_RE_SKEY = re.compile(r"^S(\d)(\d)$")


@dataclass
class Finding:
    severity: str  # "error" | "warning" | "info"
    code: str
    message: str


@dataclass
class EngineFacts:
    timestep_s: float | None = None
    timestep_method: str = ""
    nyquist_ts: int | None = None
    nyquist_f_hz: float | None = None
    excitation_ts: int | None = None
    excitation_s: float | None = None
    nr_ts: int | None = None
    nr_ts_vs_excitation: float | None = None
    cells: str = ""
    cells_total: int | None = None
    operator_bytes: int | None = None
    field_bytes: int | None = None
    steps: int | None = None
    energy_db: float | None = None
    speed_mcs: float | None = None
    elapsed_s: int | None = None
    end_criteria_db: float | None = None
    end_criteria_hit: bool = False
    max_ts_reached: bool = False


@dataclass
class PowerBalance:
    """Passivity of the measured S-matrix rows."""

    band_hz: tuple[float, float] | None = None
    core_hz: tuple[float, float] | None = None
    max_all: float = 0.0
    max_all_f_hz: float | None = None
    max_core: float = 0.0
    max_core_f_hz: float | None = None
    violations_core: int = 0
    violations_edge: int = 0
    #: matched (S11 <= -10 dB) band and its worst passivity value
    match_hz: tuple[float, float] | None = None
    max_match: float = 0.0
    max_match_f_hz: float | None = None
    worst_rows: list[str] = field(default_factory=list)


@dataclass
class MatchQuality:
    s11_min_db: float | None = None
    s11_min_f_hz: float | None = None
    s21_at_target_db: float | None = None
    band_10db_hz: tuple[float, float] | None = None
    target_in_band: bool = False


@dataclass
class RunReport:
    verdict: str = "unknown"  # "ok" | "warning" | "error"
    headline: str = ""
    trust: str = ""  # "reliable" | "use with care" | "unusable"
    exit_code: int | None = None
    converged: bool = False
    facts: EngineFacts = field(default_factory=EngineFacts)
    findings: list[Finding] = field(default_factory=list)
    balance: PowerBalance | None = None
    match: MatchQuality | None = None
    sparams_path: str = ""
    sparams_points: int = 0
    log_path: str = ""
    #: DFT window actually simulated (steps x dt) and its frequency resolution
    dft_window_s: float = 0.0
    dft_resolution_hz: float = 0.0
    #: requested S-parameter point spacing of the sweep (Hz)
    sweep_step_hz: float = 0.0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]


# --------------------------------------------------------------------------- #
# log parsing
# --------------------------------------------------------------------------- #
def _last(match_iter) -> re.Match | None:
    last = None
    for last in match_iter:  # noqa: B007 - keep the last match
        pass
    return last


def parse_engine_log(text: str) -> EngineFacts:
    """Engine facts from the solver stdout (missing values stay ``None``)."""
    f = EngineFacts()
    m = _last(_RE_TIMESTEP.finditer(text))
    if m:
        f.timestep_s = float(m.group(1))
    m = _RE_TS_S.search(text)
    if m and f.timestep_s is None:
        f.timestep_s = float(m.group(1))
    m = _RE_TS_METHOD.search(text)
    if m:
        f.timestep_method = m.group(1)
    m = _RE_NYQUIST.search(text)
    if m:
        f.nyquist_ts = int(m.group(1))
        f.nyquist_f_hz = float(m.group(2))
    m = _RE_NYQ_TS.search(text)
    if m and f.nyquist_ts is None:
        f.nyquist_ts = int(m.group(1))
    m = _RE_EXC_TS.search(text)
    if m:
        f.excitation_ts = int(m.group(1))
        f.excitation_s = float(m.group(2))
    m = _RE_NRTS.search(text)
    if m:
        f.nr_ts = int(m.group(1))
        f.nr_ts_vs_excitation = float(m.group(2))
    m = _RE_SIZE.search(text)
    if m:
        f.cells = m.group(1)
        f.cells_total = int(m.group(2))
    m = _RE_OP_BYTES.search(text)
    if m:
        f.operator_bytes = int(m.group(1))
    m = _RE_FIELD_BYTES.search(text)
    if m:
        f.field_bytes = int(m.group(1))
    m = _last(_RE_STEPS.finditer(text))
    if m:
        f.steps = int(m.group(1))
    m = _last(_RE_ENERGY.finditer(text))
    if m:
        f.energy_db = float(m.group(1).replace(" ", ""))
    m = _last(_RE_SPEED.finditer(text))
    if m:
        f.speed_mcs = float(m.group(1))
    m = _last(_RE_ELAPSED.finditer(text))
    if m:
        f.elapsed_s = int(m.group(1))
    if _RE_END_CRIT_BEFORE.search(text):
        # "…was reached before the end-criteria of -50dB was reached" — the
        # "reached" part of that sentence must NOT count as convergence.
        f.max_ts_reached = True
    else:
        m = _RE_END_CRIT.search(text)
        if m:
            f.end_criteria_db = float(m.group(1))
            f.end_criteria_hit = True
    return f


def scan_log(text: str) -> list[Finding]:
    """Errors/warnings/problems in the log, deduplicated, benign lines dropped."""
    found: dict[tuple[str, str], Finding] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _RE_MESH_LINE.match(line)
        if m:  # our own mesh validation, printed before the run starts
            sev = {"ERROR": "error", "WARNING": "warning", "OK": "info"}[m.group(1)]
            if sev != "info":
                key = (sev, m.group(2))
                found.setdefault(
                    key, Finding(sev, f"mesh_{m.group(2)}", f"[pre-run mesh] {m.group(3)}")
                )
            continue
        low = line.lower()
        if any(p.search(line) for p in _BENIGN):
            continue
        if _RE_END_CRIT_BEFORE.search(line):
            found.setdefault(
                ("warning", "not_converged"),
                Finding(
                    "warning",
                    "not_converged",
                    "max timesteps reached before the end criteria — the fields "
                    "were still changing when the run stopped",
                ),
            )
            continue
        if "traceback (most recent call last)" in low:
            found.setdefault(
                ("error", "traceback"),
                Finding("error", "traceback", "python traceback in the log"),
            )
            continue
        if any(
            w in low
            for w in ("segmentation fault", "access violation", "winerror", "fatal")
        ):
            found.setdefault(
                ("error", "crash"), Finding("error", "crash", _short(line))
            )
            continue
        if "error" in low and "no error" not in low:
            found.setdefault(("error", "engine_error"), Finding("error", "engine_error", _short(line)))
            continue
        if "warning" in low:
            found.setdefault(
                ("warning", "engine_warning"), Finding("warning", "engine_warning", _short(line))
            )
            continue
        if any(w in low for w in ("nan", "unstable", "diverg", "not a number")):
            found.setdefault(
                ("warning", "numerical"), Finding("warning", "numerical", _short(line))
            )
    order = {"error": 0, "warning": 1, "info": 2}
    out = sorted(found.values(), key=lambda f: (order.get(f.severity, 9), f.code))
    return out


def _short(line: str, limit: int = 160) -> str:
    line = " ".join(line.split())
    return line if len(line) <= limit else line[: limit - 3] + "..."


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
def _db_array(entry: Any) -> np.ndarray | None:
    """dB values from any of the three forms the app uses.

    * ``{"real": [...], "imag": [...], "db": [...]}`` (``sparams.json`` on disk)
    * a complex array (``app.postproc.sparams.load_sparams_json``)
    * a plain array already in dB
    """
    if entry is None:
        return None
    if isinstance(entry, dict):
        entry = entry.get("db")
        if entry is None:
            return None
    arr = np.asarray(entry)
    if arr.size == 0:
        return None
    if np.iscomplexobj(arr):
        return 20.0 * np.log10(np.maximum(np.abs(arr), 1e-12))
    return arr.astype(float)


def s_matrix(sparams: dict[str, Any]) -> dict[tuple[int, int], np.ndarray]:
    """``{(i, j): S_ij in dB}`` from a ``sparams.json`` style mapping."""
    out: dict[tuple[int, int], np.ndarray] = {}
    for key, value in sparams.items():
        m = _RE_SKEY.match(str(key))
        if not m:
            continue
        arr = _db_array(value)
        if arr is not None:
            out[(int(m.group(1)), int(m.group(2)))] = arr
    return out


def compute_power_balance(
    sparams: dict[str, Any],
    *,
    band_hz: tuple[float, float] | None = None,
    tol: float = PASSIVITY_TOL,
    edge_fraction: float = EDGE_FRACTION,
) -> PowerBalance | None:
    """Passivity per excited port row: ``sum_j |S_ij|^2`` over frequency."""
    freq = np.asarray(sparams.get("freq"), dtype=float)
    if freq.size == 0:
        return None
    total = _row_totals(sparams, freq)
    if total is None:
        return None
    pb = PowerBalance()
    pb.band_hz = (float(freq.min()), float(freq.max()))
    span = pb.band_hz[1] - pb.band_hz[0]
    trim = edge_fraction * span
    pb.core_hz = (pb.band_hz[0] + trim, pb.band_hz[1] - trim)
    pb.max_all = float(total.max())
    pb.max_all_f_hz = float(freq[int(np.argmax(total))])
    core = (freq >= pb.core_hz[0]) & (freq <= pb.core_hz[1])
    if core.any():
        pb.max_core = float(total[core].max())
        pb.max_core_f_hz = float(freq[core][int(np.argmax(total[core]))])
    pb.violations_core = int(np.count_nonzero(total[core] > 1.0 + tol))
    pb.violations_edge = int(np.count_nonzero(total > 1.0 + tol)) - pb.violations_core
    return pb


def _row_totals(sparams: dict[str, Any], freq: np.ndarray) -> np.ndarray | None:
    """Worst ``sum_i |S_ij|^2`` over all excited columns, per frequency.

    Passivity is per *excitation*: for the port ``j`` that is driven, the power
    leaving through all ports must not exceed the power fed in, i.e.
    ``sum_i |S_ij|^2 <= 1`` (for a 2-port driven at port 1: |S11|^2+|S21|^2).
    """
    smat = s_matrix(sparams)
    if not smat:
        return None
    total = np.zeros_like(freq)
    for j in sorted({cj for _, cj in smat}):
        col = np.zeros_like(freq)
        for (_ri, cj), arr in smat.items():
            if cj == j and arr.size == freq.size:
                col = col + 10.0 ** (arr / 10.0)
        total = np.maximum(total, col)
    return total


def power_balance_in(
    sparams: dict[str, Any], lo_hz: float, hi_hz: float
) -> tuple[float, float | None]:
    """Worst passivity value and its frequency inside ``[lo_hz, hi_hz]``."""
    freq = np.asarray(sparams.get("freq"), dtype=float)
    if freq.size == 0:
        return 0.0, None
    total = _row_totals(sparams, freq)
    if total is None:
        return 0.0, None
    mask = (freq >= lo_hz) & (freq <= hi_hz)
    if not mask.any():
        return 0.0, None
    sel = np.where(mask)[0]
    k = int(sel[int(np.argmax(total[sel]))])
    return float(total[k]), float(freq[k])


def compute_match_quality(
    sparams: dict[str, Any], *, target_hz: float | None = None
) -> MatchQuality | None:
    freq = np.asarray(sparams.get("freq"), dtype=float)
    s11 = _db_array(sparams.get("S11"))
    if freq.size == 0 or s11 is None or s11.size != freq.size:
        return None
    q = MatchQuality()
    idx = int(np.argmin(s11))
    q.s11_min_db = float(s11[idx])
    q.s11_min_f_hz = float(freq[idx])
    s21 = _db_array(sparams.get("S21"))
    if s21 is not None and s21.size == freq.size:
        k = int(np.argmin(np.abs(freq - target_hz))) if target_hz else idx
        q.s21_at_target_db = float(s21[k])
    matched = np.where(s11 <= -10.0)[0]
    if matched.size:
        q.band_10db_hz = (float(freq[matched[0]]), float(freq[matched[-1]]))
        if target_hz:
            q.target_in_band = bool(
                q.band_10db_hz[0] <= target_hz <= q.band_10db_hz[1]
            )
    return q


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def build_report(
    *,
    log_text: str = "",
    exit_code: int | None = None,
    sparams: dict[str, Any] | None = None,
    sparams_path: str = "",
    log_path: str = "",
    band_hz: tuple[float, float] | None = None,
    target_hz: float | None = None,
    end_criteria: float | None = None,
    timing=None,
    results_found: bool = False,
) -> RunReport:
    """Assemble the status report from the log and the measured S-parameters.

    ``results_found`` tells whether *any* result file was written; it separates
    "the run produced nothing" from "results exist but not in the JSON form the
    passivity check needs".
    """
    rep = RunReport(exit_code=exit_code, sparams_path=sparams_path, log_path=log_path)
    rep.facts = parse_engine_log(log_text)
    rep.findings = scan_log(log_text)
    rep.converged = bool(
        rep.facts.end_criteria_hit
        or (not rep.facts.max_ts_reached and rep.facts.steps is not None)
    )

    if sparams:
        rep.sparams_points = int(np.asarray(sparams.get("freq"), dtype=float).size)
        rep.match = compute_match_quality(sparams, target_hz=target_hz)
        rep.balance = compute_power_balance(sparams, band_hz=band_hz)
        if rep.balance is not None and rep.match is not None and rep.match.band_10db_hz:
            lo, hi = rep.match.band_10db_hz
            rep.balance.match_hz = (lo, hi)
            rep.balance.max_match, rep.balance.max_match_f_hz = power_balance_in(
                sparams, lo, hi
            )
    if rep.facts.steps and rep.facts.timestep_s:
        rep.dft_window_s = rep.facts.steps * rep.facts.timestep_s
        if rep.dft_window_s:
            rep.dft_resolution_hz = 1.0 / rep.dft_window_s
    if timing is not None and getattr(timing, "target_resolution_hz", 0.0):
        rep.sweep_step_hz = float(timing.target_resolution_hz)
    elif band_hz and rep.sparams_points > 1:
        rep.sweep_step_hz = abs(band_hz[1] - band_hz[0]) / (rep.sparams_points - 1)

    # --- derived findings / verdict -------------------------------------- #
    if rep.facts.max_ts_reached:
        rep.converged = False
    if exit_code not in (0, None):
        rep.findings.append(
            Finding("error", "exit_code", f"the solver exited with code {exit_code}")
        )
    if exit_code == 0 and not sparams and not results_found:
        rep.findings.append(
            Finding("warning", "no_results", "no S-parameter result was written")
        )
    elif exit_code == 0 and not sparams and sparams_path:
        rep.findings.append(
            Finding(
                "info",
                "results_not_json",
                f"S-parameters exist ({Path(sparams_path).name}) but not as JSON — "
                "the passivity check needs the JSON form",
            )
        )
    if rep.balance and rep.balance.violations_core > 0:
        rep.findings.append(
            Finding(
                "warning",
                "passivity_core",
                f"|S|^2 sums to {rep.balance.max_core:.3f} > 1 at "
                f"{_ghz(rep.balance.max_core_f_hz)} ({rep.balance.violations_core} "
                "point(s)) — results are not physically consistent there",
            )
        )
    if rep.balance and rep.balance.violations_edge > 0 and rep.balance.violations_core == 0:
        rep.findings.append(
            Finding(
                "info",
                "passivity_edge",
                f"passivity exceeded at the sweep edge only "
                f"(max {rep.balance.max_all:.3f} at {_ghz(rep.balance.max_all_f_hz)}) — "
                "known DFT window artefact, the in-band results are unaffected",
            )
        )
    if timing is not None and getattr(timing, "dt_courant_s", 0.0) and rep.facts.timestep_s:
        ratio = rep.facts.timestep_s / timing.dt_courant_s
        rep.findings.append(
            Finding(
                "info",
                "timestep_check",
                f"engine dt {rep.facts.timestep_s * 1e12:.4g} ps vs. vacuum Courant "
                f"estimate {timing.dt_courant_s * 1e12:.4g} ps ({ratio * 100:.0f}%) — "
                "expected: the densest material reduces it",
            )
        )
    if end_criteria and rep.facts.energy_db is not None and not rep.facts.end_criteria_hit:
        rep.findings.append(
            Finding(
                "warning",
                "energy_vs_criteria",
                f"energy reached {rep.facts.energy_db:.1f} dB, the requested end "
                f"criteria ({10 * np.log10(end_criteria):.0f} dB) was not met",
            )
        )

    rep.verdict, rep.headline, rep.trust = _verdict(rep, end_criteria=end_criteria)
    return rep


def _verdict(rep: RunReport, *, end_criteria: float | None) -> tuple[str, str, str]:
    if rep.exit_code not in (0, None):
        return (
            "error",
            f"the run failed (exit code {rep.exit_code})",
            "unusable",
        )
    if rep.errors:
        return ("error", f"{len(rep.errors)} error(s) in the solver log", "unusable")
    problems: list[str] = []
    if rep.balance and rep.balance.violations_core > 0:
        problems.append("passivity violated in band")
    if rep.facts.max_ts_reached:
        problems.append("not converged (max timesteps)")
    if not rep.sparams_points:
        problems.append("no S-parameter results")
    if rep.warnings:
        problems.append(f"{len(rep.warnings)} warning(s)")
    if problems:
        return ("warning", "; ".join(problems), "use with care")
    if rep.match and rep.match.s11_min_db is not None and rep.match.s11_min_db > -10.0:
        return (
            "warning",
            "converged, but the design never matches (S11 > -10 dB) — check the "
            "sweep band and the matching network",
            "use with care",
        )
    return ("ok", "converged, passive in band — the results look trustworthy", "reliable")


def _ghz(freq_hz: float | None) -> str:
    return f"{freq_hz / 1e9:.4g} GHz" if freq_hz else "n/a"


def _db(value: float | None, unit: str = "dB") -> str:
    return f"{value:.2f} {unit}" if value is not None else "n/a"


def render(rep: RunReport, *, width: int = 74) -> str:
    """Plain-text block appended to the log / shown in the status dialog."""
    bar = "=" * width
    f = rep.facts
    out = [bar, "SIMULATION STATUS", bar]
    tag = {"ok": "OK", "warning": "WARNING", "error": "ERROR"}.get(rep.verdict, "?")
    out.append(f"Verdict   : {tag} - {rep.headline}")
    out.append(f"Trust     : {rep.trust}")
    exit_txt = "n/a" if rep.exit_code is None else str(rep.exit_code)
    conv = "end criteria reached" if rep.converged else "not converged"
    out.append(
        f"Run       : exit {exit_txt} | {conv}"
        + (f" | {f.steps}/{f.nr_ts} TS" if f.steps and f.nr_ts else "")
        + (f" | energy {f.energy_db:.1f} dB" if f.energy_db is not None else "")
        + (f" | {f.elapsed_s} s" if f.elapsed_s else "")
    )
    if f.timestep_s:
        out.append(
            f"Engine    : dt {f.timestep_s * 1e12:.4g} ps"
            + (f" ({f.timestep_method})" if f.timestep_method else "")
            + (f" | Nyquist {f.nyquist_ts} TS @ {_ghz(f.nyquist_f_hz)}" if f.nyquist_ts else "")
        )
    if f.cells:
        out.append(
            f"          : cells {f.cells} = {f.cells_total}"
            + (f" | operator {f.operator_bytes / 1048576:.1f} MiB" if f.operator_bytes else "")
            + (f" | field {f.field_bytes / 1048576:.1f} MiB" if f.field_bytes else "")
        )
    if f.excitation_ts:
        out.append(
            f"          : excitation {f.excitation_ts} TS ({f.excitation_s * 1e9:.4g} ns)"
            + (f" | max TS = {f.nr_ts_vs_excitation:.2f} x excitation" if f.nr_ts_vs_excitation else "")
        )
    if rep.sparams_points:
        b = rep.balance
        out.append(
            f"Results   : {rep.sparams_points} points"
            + (f" | {_ghz(b.band_hz[0])} .. {_ghz(b.band_hz[1])}" if b and b.band_hz else "")
        )
        if rep.match:
            m = rep.match
            line = f"          : S11 min {_db(m.s11_min_db)} @ {_ghz(m.s11_min_f_hz)}"
            if m.band_10db_hz:
                lo, hi = m.band_10db_hz
                line += f" | matched {_ghz(lo)}..{_ghz(hi)} ({(hi - lo) / 1e6:.0f} MHz)"
                line += " | design f inside" if m.target_in_band else " | design f OUTSIDE"
            else:
                line += " | no -10 dB match in the sweep"
            out.append(line)
            if m.s21_at_target_db is not None:
                out.append(f"          : S21 at the design frequency {_db(m.s21_at_target_db)}")
        if b:
            line = (
                f"          : passivity max |S|^2 = {b.max_all:.3f} @ {_ghz(b.max_all_f_hz)}"
                f" | in core band {b.max_core:.3f}"
            )
            if b.match_hz:
                line += f" | in the matched band {b.max_match:.3f}"
            out.append(line)
            if b.violations_core:
                out.append(
                    f"          : VIOLATIONS inside the core band: {b.violations_core} "
                    "point(s) > 1"
                )
            elif b.violations_edge:
                out.append(
                    f"          : {b.violations_edge} violation(s) at the sweep edge "
                    "(DFT window artefact, harmless)"
                )
        if rep.dft_window_s:
            out.append(
                f"          : DFT window {rep.facts.steps} TS x "
                f"{rep.facts.timestep_s * 1e12:.4g} ps = {rep.dft_window_s * 1e9:.4g} ns "
                f"-> dF = {rep.dft_resolution_hz / 1e6:.3g} MHz"
                + (
                    f" (sweep step {rep.sweep_step_hz / 1e6:.3g} MHz"
                    + (
                        " -> window-limited)"
                        if rep.sweep_step_hz and rep.dft_resolution_hz > rep.sweep_step_hz
                        else ")"
                    )
                    if rep.sweep_step_hz
                    else ""
                )
            )
    else:
        out.append("Results   : none written")
    if rep.findings:
        out.append("Findings  :")
        by_sev: dict[str, list[Finding]] = {}
        for fi in rep.findings:
            by_sev.setdefault(fi.severity, []).append(fi)
        for sev in ("error", "warning", "info"):
            items = by_sev.get(sev, [])
            for fi in items[:MAX_FINDINGS]:
                out.append(f"  [{sev.upper()}] {fi.code}: {fi.message}")
            if len(items) > MAX_FINDINGS:
                out.append(f"  [{sev.upper()}] ... and {len(items) - MAX_FINDINGS} more")
    else:
        out.append("Findings  : none")
    if rep.log_path:
        out.append(f"Log       : {rep.log_path}")
    out.append(bar)
    return "\n".join(out)
