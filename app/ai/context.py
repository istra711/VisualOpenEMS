"""Build a small, redacted project/run context for the AI assistant."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.ai.types import Diagnosis, RunContext
from app.models.project import Project

_USER_PATH = re.compile(r"([A-Za-z]:[\\/]Users[\\/])[^\\/\s]+", re.IGNORECASE)
_TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(api[_ -]?key\s*[=:]\s*)\S+"),
)
_LOG_KEYS = re.compile(
    r"\b(error|warning|warn|failed|failure|exception|traceback|"
    r"calcport|mesh|pml|nan|diverg|abort|exit code|"
    r"timestep|nyquist|energy|converg|sparams|s-param|postproc)\b",
    re.IGNORECASE,
)


def redact_text(text: str) -> str:
    """Remove the most obvious secrets and user-specific absolute paths."""
    out = str(text or "")
    home = str(Path.home())
    if home:
        out = out.replace(home, "%USERPROFILE%")
    out = _USER_PATH.sub(r"\1<user>", out)
    for pattern in _TOKEN_PATTERNS:
        if pattern.pattern.lower().startswith("(?i)(authorization"):
            out = pattern.sub(r"\1[REDACTED]", out)
        elif pattern.pattern.lower().startswith("(?i)(api"):
            out = pattern.sub(r"\1[REDACTED]", out)
        else:
            out = pattern.sub("[REDACTED]", out)
    return out


def _num(value: Any, unit: str = "") -> str:
    if value is None:
        return "n/a"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return f"{value}{unit}"
    if abs(number) >= 1e6 or (number != 0 and abs(number) < 1e-3):
        return f"{number:.6g}{unit}"
    return f"{number:.6g}{unit}"


def project_digest(project: Project, primitives: list[dict] | None = None) -> str:
    """Compact, human-readable project summary (never the full project JSON)."""
    m = project.mesh
    s = project.sim
    lines = [
        f"Project: {project.name or '(unnamed)'}",
        f"Drawing unit: {m.drawing_unit}",
        f"Frequency sweep: {_num(m.start_frequency, ' Hz')} .. "
        f"{_num(m.stop_frequency, ' Hz')}",
        f"Target frequency: {_num(m.target_frequency, ' Hz')}",
        f"Boundary: {', '.join(project.boundary.as_openems_list())}; "
        f"PML {project.boundary.pml_size}; air margin "
        f"{project.boundary.air_margin}",
        f"Mesh preset: {m.preset}; cells/lambda {_num(m.cells_per_wavelength)}; "
        f"max cell {_num(m.max_cellsize)}; min cell {_num(m.min_cellsize)}; "
        f"refined cell {_num(m.refined_cellsize)}",
        f"Simulation: max_timesteps={s.max_timesteps}; "
        f"end_criteria={s.end_criteria:g}; "
        f"field_dumps={s.field_dumps_enabled}; nf2ff={s.nf2ff_enabled}",
        f"NF2FF: enabled={project.nf2ff.enabled}; box_mode={project.nf2ff.box_mode}; "
        f"start={project.nf2ff.start}; stop={project.nf2ff.stop}; "
        f"freq_mode={project.nf2ff.freq_mode}; theta/phi="
        f"{project.nf2ff.theta_steps}/{project.nf2ff.phi_steps}",
        f"Counts: {len(primitives or project.primitives or [])} primitive(s), "
        f"{len(project.ports)} port(s), {len(project.dumps)} dump(s), "
        f"{len(project.variables)} variable(s), {len(project.materials)} material(s)",
    ]

    if project.variables:
        lines.append("Variables:")
        for variable in project.variables[:40]:
            expr = f" = {variable.expression}" if variable.expression else ""
            lines.append(
                f"  {variable.name} = {_num(variable.value)}"
                f"{expr}" + (f"  # {variable.comment}" if variable.comment else "")
            )

    if project.ports:
        lines.append("Ports:")
        for index, port in enumerate(project.ports):
            lines.append(
                f"  [{index}] {port.name}: type={port.type}; "
                f"active={port.active}; excite={port.excite_port}; "
                f"phase={port.excite_phase_deg}; R={port.resistance}; "
                f"start={port.start}; stop={port.stop}; dir={port.direction}; "
                f"reverse={port.reverse}"
            )

    if project.dumps:
        lines.append("Dumps:")
        for dump in project.dumps[:30]:
            lines.append(
                f"  {dump.name}: quantity={dump.quantity}; enabled={dump.enabled}; "
                f"freq_domain={dump.frequency_domain}"
            )

    items = primitives if primitives is not None else (project.primitives or [])
    if items:
        lines.append("Primitives:")
        for prim in list(items)[:40]:
            if not isinstance(prim, dict):
                lines.append(f"  {prim!r}")
                continue
            lines.append(
                "  "
                + json.dumps(
                    {
                        key: prim.get(key)
                        for key in (
                            "kind",
                            "name",
                            "material",
                            "start",
                            "stop",
                            "center",
                            "radius",
                            "expressions",
                        )
                        if key in prim
                    },
                    ensure_ascii=True,
                    default=str,
                )
            )
        if len(items) > 40:
            lines.append(f"  ... and {len(items) - 40} more primitive(s)")

    if project.sweep is not None:
        lines.append(f"Sweep: {project.sweep.summary()}; enabled={project.sweep.enabled}")

    return redact_text("\n".join(lines))


def read_log_excerpt(
    path: Path | str | None,
    *,
    max_lines: int = 160,
    max_chars: int = 18000,
) -> str:
    """Filter a simulation log to relevant lines, with line numbers."""
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    hits: list[int] = []
    for index, line in enumerate(raw):
        if _LOG_KEYS.search(line):
            hits.append(index)
    # Keep a little context around each hit, then append the tail.
    wanted: list[int] = []
    for index in hits:
        for near in range(max(0, index - 1), min(len(raw), index + 2)):
            if near not in wanted:
                wanted.append(near)
    tail_start = max(0, len(raw) - min(30, max_lines // 3))
    for index in range(tail_start, len(raw)):
        if index not in wanted:
            wanted.append(index)
    wanted = sorted(wanted)[:max_lines]
    lines: list[str] = []
    previous = -2
    for index in wanted:
        if index != previous + 1:
            lines.append("...")
        line = redact_text(raw[index].rstrip())
        if len(line) > 300:
            line = line[:297] + "..."
        lines.append(f"{index + 1:6d} | {line}")
        previous = index
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n... [truncated]"
    return text


def read_script_excerpt(
    path: Path | str | None, *, max_lines: int = 140, max_chars: int = 14000
) -> str:
    if not path:
        return ""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    wanted: list[int] = list(range(min(40, len(raw))))
    keys = re.compile(
        r"(FDTD|Run\(|AddLumpedPort|AddMSLPort|AddRectWaveGuidePort|"
        r"SetBoundary|SetGaussExcite|AddNF2FF|CalcPort|Export|"
        r"max_timesteps|EndCriteria|PML|mesh|SetMesh)",
        re.IGNORECASE,
    )
    for index, line in enumerate(raw):
        if keys.search(line) and index not in wanted:
            wanted.append(index)
    wanted = sorted(wanted)[:max_lines]
    lines: list[str] = []
    previous = -2
    for index in wanted:
        if index != previous + 1:
            lines.append("...")
        line = redact_text(raw[index].rstrip())
        if len(line) > 260:
            line = line[:257] + "..."
        lines.append(f"{index + 1:5d} | {line}")
        previous = index
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n... [truncated]"
    return text


def build_run_context(
    project: Project,
    primitives: list[dict] | None,
    report: Any | None,
    diagnosis: Diagnosis | None,
    *,
    log_path: Path | str | None = None,
    script_path: Path | str | None = None,
    include_script: bool = True,
    max_log_lines: int = 160,
) -> RunContext:
    report_text = ""
    if report is not None:
        try:
            from app.simulation.report import render

            report_text = render(report)
        except Exception:  # noqa: BLE001 - context must never break the UI
            report_text = ""
    return RunContext(
        project_digest=project_digest(project, primitives),
        report_text=redact_text(report_text),
        log_excerpt=read_log_excerpt(log_path, max_lines=max_log_lines),
        script_excerpt=(
            read_script_excerpt(script_path) if include_script else ""
        ),
        diagnosis_text=redact_text(diagnosis.to_text()) if diagnosis else "",
        redaction_note=(
            "Paths under the user profile and obvious API tokens are redacted. "
            "Absolute project/work paths may still be present."
        ),
    )


def build_prompt(
    question: str,
    context: RunContext,
    *,
    language: str = "auto",
    max_chars: int = 26000,
) -> str:
    language_line = {
        "de": "Antworte auf Deutsch.",
        "en": "Answer in English.",
    }.get(str(language or "auto").lower(), "Antworte in der Sprache der Frage.")

    intro = (
        "You are the AI error assistant inside VisualopenEMS, a Windows GUI that "
        "prepares FreeCAD geometry for openEMS FDTD simulations.\n"
        "Use ONLY the context below. If information is missing, say so instead of "
        "inventing it. Do not use tools, do not read or write files, and do not "
        "claim that you ran a simulation.\n"
        "Your job: rank the most likely causes, cite concrete evidence lines, "
        "and give minimal next checks or parameter changes. The deterministic "
        "diagnosis is authoritative for known findings; explain it, and only "
        "contradict it when the evidence clearly supports that.\n"
        f"{language_line}\n"
        "Keep the answer practical and structured: likely cause, evidence, "
        "next check, optional fix. Distinguish 'certain' from 'probable'."
    )
    body = (
        f"USER QUESTION\n{question.strip() or 'Explain the last run.'}\n\n"
        f"CONTEXT\n{context.display_text()}"
    )
    prompt = f"{intro}\n\n{body}"
    if len(prompt) > max_chars:
        # Keep the instructions and the user question; trim the middle of the
        # context instead of the question.
        head = intro + "\n\nUSER QUESTION\n" + (question.strip() or "Explain the last run.")
        room = max(1000, max_chars - len(head) - 100)
        prompt = head + "\n\nCONTEXT (truncated)\n" + body[-room:]
    return prompt
