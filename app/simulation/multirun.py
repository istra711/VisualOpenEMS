"""Plan and merge sequential multi-excitation runs (full S-matrix).

One FDTD run measures exactly one S-matrix column (the excited port's). A
complete N-port S-matrix therefore needs N runs — each with a different port
excited — whose partial ``sparams.json`` files are merged afterwards. The GUI
driver uses :func:`plan_port_runs` to write one run per port under
``<project>/multi/port<n>/`` and :func:`merge_port_runs` to combine them into
the project's normal ``<project>/sparams/sparams.*`` location.

The module is Qt-free and openEMS-free (it only shells the existing script
generator / post-processing), so it is unit-testable headless.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.models.project import Project


@dataclass
class PortRunPlan:
    """One excitation run of the multi-run driver."""

    port_index: int  # 0-based position in project.ports
    port_number: int  # 1-based openEMS port number
    port_name: str
    work_dir: Path  # per-run WORK_DIR (script writes simulation/ sparams/ nf2ff/)
    project_path: Path
    script_path: Path


def multi_run_root(base_work: Path | str) -> Path:
    return Path(base_work) / "multi"


def plan_port_runs(
    project: Project,
    primitives: list[dict] | None,
    base_work: Path | str,
    *,
    only_ports: list[int] | None = None,
) -> list[PortRunPlan]:
    """Deep-copy ``project`` once per port and write the per-port run scripts.

    In every copy exactly one port is excited (``excite_port``), so the run
    measures that port's S-matrix column. ``only_ports`` limits the set to the
    given 1-based port numbers (default: all ports).

    Raises ``ValueError`` when fewer than two ports are selected.
    """
    from app.simulation.script_gen import generate_run_script

    ports = list(project.ports)
    numbers = list(only_ports) if only_ports is not None else list(range(1, len(ports) + 1))
    if len(numbers) < 2:
        raise ValueError("multi-run needs at least 2 ports")

    root = multi_run_root(base_work)
    plans: list[PortRunPlan] = []
    for number in numbers:
        if number < 1 or number > len(ports):
            raise ValueError(f"port number {number} out of range (1..{len(ports)})")
        run_proj = copy.deepcopy(project)
        for j, p in enumerate(run_proj.ports, start=1):
            p.excite_port = j == number
        run_dir = root / f"port{number}"
        run_dir.mkdir(parents=True, exist_ok=True)
        proj_path = run_proj.save(run_dir / f"port{number}.fcsim.json")
        script = generate_run_script(run_proj, proj_path, run_dir, primitives=primitives)
        plans.append(
            PortRunPlan(
                port_index=number - 1,
                port_number=number,
                port_name=ports[number - 1].name,
                work_dir=run_dir,
                project_path=proj_path,
                script_path=script,
            )
        )
    return plans


def port_sparams_json(plan: PortRunPlan) -> Path | None:
    """The partial S-param table of a finished run (new layout, then legacy)."""
    for cand in (
        plan.work_dir / "sparams" / "sparams.json",
        plan.work_dir / "simulation" / "sparams.json",
    ):
        if cand.exists():
            return cand
    return None


def merge_port_runs(base_work: Path | str, plans: list[PortRunPlan]) -> dict:
    """Merge the per-port tables and export under ``<base_work>/sparams``.

    Returns ``{"table": merged, "written": {name: path}, "missing": [numbers]}``.
    Raises ``RuntimeError`` when no run produced a usable ``sparams.json``.
    """
    from app.postproc import sparams as sp

    tables: list[dict] = []
    missing: list[int] = []
    for plan in plans:
        path = port_sparams_json(plan)
        if path is None:
            missing.append(plan.port_number)
            continue
        try:
            tables.append(sp.load_sparams_json(path))
        except Exception:  # noqa: BLE001 — one bad run must not kill the merge
            missing.append(plan.port_number)
    if not tables:
        raise RuntimeError("no per-port sparams.json found — all runs failed?")
    merged = tables[0] if len(tables) == 1 else sp.merge_sparam_tables(*tables)
    out_dir = Path(base_work) / "sparams"
    written = sp.export_sparams(merged, out_dir, basename="sparams")
    return {
        "table": merged,
        "written": {k: str(v) for k, v in written.items()},
        "missing": missing,
    }


def summarise_table(table: dict, keys: list[str] | None = None) -> list[str]:
    """Human-readable ``min |Sij|`` lines (dB @ GHz) for the GUI log."""
    freqs = np.asarray(table.get("freq", []), dtype=float)
    if freqs.size == 0:
        return ["S-params: no frequency data"]
    if keys is None:
        keys = sorted(
            k
            for k in table
            if len(k) == 3 and k[0] == "S" and k[1].isdigit() and k[2].isdigit()
        )
    lines: list[str] = []
    for key in keys:
        arr = np.asarray(table.get(key, []), dtype=complex)
        if arr.size != freqs.size or not np.any(np.isfinite(arr)):
            continue
        db = 20.0 * np.log10(np.maximum(np.abs(arr), 1e-12))
        i = int(np.nanargmin(db))
        lines.append(f"  {key}: min {db[i]:.2f} dB @ {freqs[i] / 1e9:.3f} GHz")
    return lines or ["S-params: no matrix cells"]
