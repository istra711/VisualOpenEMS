"""Parse the openEMS FDTD engine's stdout progress lines (Qt-free).

The engine prints one line per report interval::

    [@        4s] Timestep:          888 || Speed:   35.6 MC/s (4.618e-03 s/TS)
        || Energy: ~6.74e-19 (- 0.00dB)

and, before starting, the timestep budget::

    Max. number of timesteps: 200000 ( --> 19.8669 * Excitation signal length)

``EngineProgress`` accumulates those lines into a fraction + a status string
used by the GUI progress bar (residual energy in dB is the run's end criterion).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_RE_TIMESTEP = re.compile(r"Timestep:\s*(\d+)")
_RE_ENERGY = re.compile(r"Energy:\s*~\s*([-\d.eE+]+)\s*\(\s*(-?\s*[\d.]+)\s*dB\)")
_RE_ELAPSED = re.compile(r"\[@\s*([0-9]+m[0-9]+s|[0-9]+s)\s*\]")
_RE_MAX_TS = re.compile(r"Max\.\s*number of timesteps:\s*(\d+)")
_RE_CRITERIA = re.compile(r"end[-\s]?criteria", re.IGNORECASE)


@dataclass
class EngineProgress:
    """Running FDTD progress assembled from engine stdout lines."""

    max_timesteps: int | None = None
    timestep: int = 0
    energy_db: float | None = None
    elapsed: str = ""
    end_criteria_hit: bool = False
    stopped_before_criteria: bool = False

    def reset(self) -> None:
        self.max_timesteps = None
        self.timestep = 0
        self.energy_db = None
        self.elapsed = ""
        self.end_criteria_hit = False
        self.stopped_before_criteria = False

    def feed(self, line: str) -> bool:
        """Update from one stdout line; True if anything changed."""
        if not line:
            return False
        changed = False

        m = _RE_MAX_TS.search(line)
        if m:
            value = int(m.group(1))
            if value > 0 and value != self.max_timesteps:
                self.max_timesteps = value
                changed = True

        m = _RE_TIMESTEP.search(line)
        if m:
            value = int(m.group(1))
            if value != self.timestep:
                self.timestep = value
                changed = True

        m = _RE_ENERGY.search(line)
        if m:
            value = float(m.group(2).replace(" ", ""))
            if value != self.energy_db:
                self.energy_db = value
                changed = True

        m = _RE_ELAPSED.search(line)
        if m:
            if m.group(1) != self.elapsed:
                self.elapsed = m.group(1)
                changed = True

        if _RE_CRITERIA.search(line):
            low = line.lower()
            if "before" in low:
                if not self.stopped_before_criteria:
                    self.stopped_before_criteria = True
                    changed = True
            elif "reached" in low:
                if not self.end_criteria_hit:
                    self.end_criteria_hit = True
                    changed = True

        return changed

    @property
    def percent(self) -> int:
        if self.end_criteria_hit:
            return 100
        if self.max_timesteps:
            frac = self.timestep / self.max_timesteps
            return max(0, min(100, int(round(frac * 100))))
        return 0

    @property
    def active(self) -> bool:
        return self.timestep > 0 or self.max_timesteps is not None

    def text(self) -> str:
        parts: list[str] = []
        if self.max_timesteps:
            parts.append(f"TS {self.timestep}/{self.max_timesteps}")
        elif self.timestep:
            parts.append(f"TS {self.timestep}")
        if self.energy_db is not None:
            parts.append(f"Energy {self.energy_db:.1f} dB")
        if self.elapsed:
            parts.append(f"@{self.elapsed}")
        if self.end_criteria_hit:
            parts.append("converged")
        elif self.stopped_before_criteria:
            parts.append("max TS reached")
        return "   ".join(parts)
