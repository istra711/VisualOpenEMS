"""FDTD excitation waveforms (``Project.mesh.excitation``).

Shared by the run-script generator (``project_run.py``) and the mesh build so
the waveform selected in the GUI is what the solver actually uses. openEMS
builds up to 0.0.36 expose only

    SetGaussExcite(f0, fc)
    SetSinusExcite(f0)
    SetStepExcite(f_max)
    SetDiracExcite(f_max)

``SetCustomExcite(time, values)`` exists in newer builds only; the CSV option is
therefore advertised only when the installed openEMS has it
(:func:`custom_supported`).
"""

from __future__ import annotations

from pathlib import Path

# kind -> combo label
EXCITATIONS: dict[str, str] = {
    "gauss": "Gauss (broadband, f0/fc)",
    "sinus": "Sinus (narrowband, f0)",
    "step": "Step (broadband, f_max)",
    "dirac": "Dirac impulse (broadband, f_max)",
}
CUSTOM_KIND = "custom"
CUSTOM_LABEL = "Custom waveform (CSV, time in s)"

#: kinds whose bandwidth is set by f0/fc (fc spin only matters for Gauss)
GAUSS_ONLY_FC = {"gauss"}


def custom_supported() -> bool:
    """True if the installed openEMS provides ``SetCustomExcite``."""
    try:
        from openEMS import openEMS as _fdtd  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        return False
    return hasattr(_fdtd, "SetCustomExcite")


def available_kinds() -> list[str]:
    """Selectable kinds for this installation (CSV only if supported)."""
    kinds = list(EXCITATIONS)
    if custom_supported():
        kinds.append(CUSTOM_KIND)
    return kinds


def kind_label(kind: str) -> str:
    kind = normalize_kind(kind)
    if kind == CUSTOM_KIND:
        return CUSTOM_LABEL
    return EXCITATIONS.get(kind, EXCITATIONS["gauss"])


def normalize_kind(kind: object) -> str:
    """Map any stored value to a known kind (unknown/empty -> ``gauss``)."""
    text = str(kind or "").strip().lower()
    if text == CUSTOM_KIND:
        return CUSTOM_KIND if custom_supported() else "gauss"
    if text in EXCITATIONS:
        return text
    return "gauss"


def resolve_excitation_file(mesh, base_dir: Path | str | None = None) -> Path | None:
    """Absolute path of ``mesh.excitation_file`` (relative -> base_dir)."""
    raw = str(getattr(mesh, "excitation_file", "") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    return path


def read_custom_waveform(path: Path | str) -> tuple[list[float], list[float]]:
    """Read a two-column waveform (time [s], amplitude) from a text/CSV file.

    Accepts ``,``/``;``/tab/space separated columns, ``#``/``%`` comments and an
    optional header line. Amplitude is arbitrary (openEMS normalises it); the
    time axis must be strictly increasing and start at 0.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ValueError(f"cannot read excitation file {path}: {e}") from e

    times: list[float] = []
    values: list[float] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", "%", "//")):
            continue
        for sep in (";", "\t", ","):
            if sep in line:
                parts = [p.strip() for p in line.split(sep)]
                break
        else:
            parts = line.split()
        parts = [p for p in parts if p]
        if len(parts) < 2:
            continue
        try:
            t = float(parts[0])
            v = float(parts[1])
        except ValueError:
            # header row ("time, amplitude") — skip it, but only before data
            if not times:
                continue
            raise ValueError(
                f"{path.name}:{lineno}: expected two numbers, got {line!r}"
            ) from None
        if not (t == t and v == v) or t in (float("inf"), float("-inf")):
            raise ValueError(f"{path.name}:{lineno}: non-finite value")
        times.append(t)
        values.append(v)

    if len(times) < 2:
        raise ValueError(f"{path.name}: need at least 2 waveform samples")
    mono = all(b > a for a, b in zip(times, times[1:]))
    if not mono:
        raise ValueError(
            f"{path.name}: time column must be strictly increasing (sorted, no duplicates)"
        )
    if times[0] != 0.0:
        raise ValueError(f"{path.name}: time column must start at 0 s")
    return times, values


def apply_excitation(fdtd, mesh, f0: float, fc: float, base_dir=None) -> str:
    """Configure ``fdtd`` from ``mesh.excitation``; returns the kind actually used.

    ``f0`` is the design/centre frequency, ``fc`` the Gauss corner frequency.
    Step/Dirac take the band's highest frequency (``f_max``), Sinus takes f0.
    A custom waveform falls back to Gauss (with a printed warning) when the
    installed openEMS lacks ``SetCustomExcite`` or the file cannot be read.
    """
    kind = normalize_kind(getattr(mesh, "excitation", "gauss"))
    f_max = max(
        float(getattr(mesh, "start_frequency", 0.0) or 0.0),
        float(getattr(mesh, "stop_frequency", 0.0) or 0.0),
    )
    if f_max <= 0.0:
        f_max = float(f0)

    if kind == "sinus":
        fdtd.SetSinusExcite(float(f0))
        return kind
    if kind == "step":
        fdtd.SetStepExcite(float(f_max))
        return kind
    if kind == "dirac":
        fdtd.SetDiracExcite(float(f_max))
        return kind
    if kind == CUSTOM_KIND:
        path = resolve_excitation_file(mesh, base_dir)
        if not custom_supported() or path is None or not path.exists():
            print(
                "WARNING: custom excitation unavailable "
                f"(file={path}, openEMS has SetCustomExcite={custom_supported()}) "
                "- falling back to Gauss"
            )
        else:
            times, values = read_custom_waveform(path)
            fdtd.SetCustomExcite(times, values)
            print(f"Custom excitation: {path.name}, {len(times)} samples")
            return kind

    fdtd.SetGaussExcite(float(f0), float(fc))
    return "gauss"


def describe_excitation(mesh, f0: float, fc: float) -> str:
    """One-line description for logs / readouts."""
    kind = normalize_kind(getattr(mesh, "excitation", "gauss"))
    f_max = max(
        float(getattr(mesh, "start_frequency", 0.0) or 0.0),
        float(getattr(mesh, "stop_frequency", 0.0) or 0.0),
    )
    if kind == "sinus":
        return f"Sinus excite (f0 = {f0:g} Hz) — S-params meaningful near f0 only"
    if kind == "step":
        return f"Step excite (f_max = {f_max:g} Hz)"
    if kind == "dirac":
        return f"Dirac impulse excite (f_max = {f_max:g} Hz)"
    if kind == CUSTOM_KIND:
        name = Path(str(getattr(mesh, "excitation_file", "") or "")).name or "?"
        return f"Custom excite ({name})"
    return f"Gauss excite (f0 = {f0:g} Hz, fc = {fc:g} Hz)"
