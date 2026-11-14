"""Deterministic error diagnosis for finished (or failed) openEMS runs.

This module is deliberately Qt-free and provider-free: it works offline and
forms the authoritative basis that an AI provider is asked to explain.
"""

from __future__ import annotations

from app.ai.fixes import make_change
from app.ai.types import Diagnosis, DiagnosisIssue, FixProposal
from app.models.project import Project
from app.simulation.report import Finding, RunReport


def _max_timesteps_fix(project: Project, reason: str) -> FixProposal | None:
    current = int(getattr(project.sim, "max_timesteps", 0) or 0)
    if current <= 0:
        return None
    target = max(200_000, int(round(current * 2.0 / 10_000.0) * 10_000))
    target = min(target, 20_000_000)
    if target <= current:
        return None
    change = make_change(
        project,
        "sim.max_timesteps",
        target,
        "Maximum timesteps",
    )
    return FixProposal(
        id="max_timesteps_2x",
        title=f"Increase max timesteps to {target:,}",
        description=reason,
        changes=[change],
        requires_rebuild=False,
        requires_rerun=True,
        note=(
            "This is the safest generic fix for a run that hit the timestep "
            "limit. It costs additional simulation time; stop the run if the "
            "energy no longer changes."
        ),
    )


def _end_criteria_fix(project: Project) -> FixProposal | None:
    current = float(getattr(project.sim, "end_criteria", 1e-5) or 1e-5)
    target = min(0.1, max(current * 10.0, 1e-4))
    if abs(target - current) < 1e-15:
        return None
    change = make_change(
        project,
        "sim.end_criteria",
        target,
        "Energy end criterion",
    )
    return FixProposal(
        id="end_criteria_relax",
        title=f"Relax end criteria to {target:g}",
        description=(
            "The solver did not reach the requested energy decay. A less strict "
            "criterion can finish earlier, but the result may be less converged."
        ),
        changes=[change],
        requires_rebuild=False,
        requires_rerun=True,
        note="Prefer increasing max timesteps first if runtime allows it.",
    )


def _excite_port_fix(project: Project) -> FixProposal | None:
    for index, port in enumerate(project.ports):
        if bool(getattr(port, "active", True)) and not bool(
            getattr(port, "excite_port", False)
        ):
            change = make_change(
                project,
                f"ports[{index}].excite_port",
                True,
                f"Excite port '{port.name}'",
            )
            return FixProposal(
                id=f"excite_port_{index}",
                title=f"Excite port '{port.name}'",
                description=(
                    "No active port is excited, so the simulation has no source. "
                    "Enable the excitation flag on the first active port."
                ),
                changes=[change],
                requires_rebuild=True,
                requires_rerun=True,
            )
    return None


def _finding_issue(finding: Finding, project: Project) -> DiagnosisIssue | None:
    """Map known RunReport finding codes to a diagnosis issue."""
    code = str(finding.code or "").strip()
    message = str(finding.message or "").strip()

    if code == "exit_code":
        return DiagnosisIssue(
            code=code,
            severity="error",
            title="The solver exited with an error",
            detail=(
                f"{message}. The S-parameter result, if any, is untrusted. "
                "Check the filtered log for the first traceback or CalcPort error."
            ),
            evidence=[message],
        )
    if code == "no_results":
        return DiagnosisIssue(
            code=code,
            severity="error",
            title="No S-parameter result was written",
            detail=(
                "The solver returned success but produced no sparams file. "
                "Typical causes: no excited port, a port outside the mesh/metal, "
                "a post-processing exception, or an unwritable work folder."
            ),
            evidence=[message],
        )
    if code == "results_not_json":
        return DiagnosisIssue(
            code=code,
            severity="warning",
            title="S-parameters were written in a different format",
            detail=(
                "A Touchstone file exists but no sparams.json, so the passivity "
                "check cannot run. The result itself may still be usable in the "
                "S-Params viewer."
            ),
            evidence=[message],
        )
    if code == "max_ts_reached":
        return DiagnosisIssue(
            code=code,
            severity="warning",
            title="The run hit max timesteps before convergence",
            detail=(
                "The field energy had not decayed to the end criterion when the "
                "timestep budget was exhausted. S-parameters can still be "
                "readable, but resonances and passivity may be inaccurate."
            ),
            evidence=[message],
            fixes=[f for f in (_max_timesteps_fix(project, message),) if f],
        )
    if code == "energy_vs_criteria":
        return DiagnosisIssue(
            code=code,
            severity="warning",
            title="The energy did not reach the requested end criterion",
            detail=(
                "The simulation stopped with more residual energy than requested. "
                "Increase max timesteps, or relax the end criterion if the current "
                "budget is intentionally tight."
            ),
            evidence=[message],
            fixes=[
                f
                for f in (
                    _max_timesteps_fix(project, message),
                    _end_criteria_fix(project),
                )
                if f
            ],
        )
    if code == "passivity_core":
        return DiagnosisIssue(
            code=code,
            severity="warning",
            title="Passivity is violated inside the core band",
            detail=(
                "The measured S-matrix is not physically consistent there. Check "
                "port calibration/placement, mesh resolution at the port and "
                "whether the simulation converged. Treat the affected frequencies "
                "as unreliable until the cause is understood."
            ),
            evidence=[message],
        )
    if code == "passivity_edge":
        return DiagnosisIssue(
            code=code,
            severity="info",
            title="Passivity exceeded only at the sweep edge",
            detail=(
                "This is a known DFT-window artefact at the edges of the sweep; "
                "the in-band result is normally unaffected."
            ),
            evidence=[message],
        )
    if code == "timestep_check":
        return DiagnosisIssue(
            code=code,
            severity="info",
            title="Engine timestep check",
            detail=message,
            evidence=[message],
        )
    if finding.severity in ("error", "warning"):
        return DiagnosisIssue(
            code=code or "finding",
            severity=finding.severity,
            title=code.replace("_", " ").strip() or "Simulation finding",
            detail=message,
            evidence=[message],
        )
    return None


def _project_checks(
    project: Project, *, skip_low_timesteps: bool = False
) -> list[DiagnosisIssue]:
    issues: list[DiagnosisIssue] = []
    active = [p for p in project.ports if bool(getattr(p, "active", True))]
    excited = [p for p in active if bool(getattr(p, "excite_port", False))]

    if not active:
        issues.append(
            DiagnosisIssue(
                code="no_active_ports",
                severity="error",
                title="No active port",
                detail=(
                    "An openEMS simulation needs at least one active port. Add a "
                    "port or enable an existing one in the project tree."
                ),
            )
        )
    elif not excited:
        issues.append(
            DiagnosisIssue(
                code="no_excited_port",
                severity="warning",
                title="No port is excited",
                detail=(
                    "All active ports are switched to 'not excited', so there is "
                    "no source in the simulation."
                ),
                fixes=[f for f in (_excite_port_fix(project),) if f],
            )
        )

    timesteps = int(getattr(project.sim, "max_timesteps", 0) or 0)
    if not skip_low_timesteps and 0 < timesteps < 150_000:
        change = make_change(
            project, "sim.max_timesteps", 200_000, "Maximum timesteps"
        )
        issues.append(
            DiagnosisIssue(
                code="low_max_timesteps",
                severity="warning",
                title=f"max_timesteps={timesteps:,} is below the usual floor",
                detail=(
                    "The application itself warns below 150,000 timesteps. For "
                    "most antenna simulations this is too short for convergence."
                ),
                evidence=[f"project.sim.max_timesteps={timesteps}"],
                fixes=[
                    FixProposal(
                        id="max_timesteps_minimum",
                        title="Raise max timesteps to 200,000",
                        description=(
                            "A conservative starting budget for a first run; "
                            "increase it further if the energy still does not decay."
                        ),
                        changes=[change],
                        requires_rebuild=False,
                        requires_rerun=True,
                    )
                ],
            )
        )

    target = project.mesh.target_frequency
    low = min(project.mesh.start_frequency, project.mesh.stop_frequency)
    high = max(project.mesh.start_frequency, project.mesh.stop_frequency)
    if target and low and high and not (low <= target <= high):
        issues.append(
            DiagnosisIssue(
                code="target_outside_sweep",
                severity="warning",
                title="Design frequency is outside the S-parameter sweep",
                detail=(
                    f"target_frequency={target:.6g} Hz is outside "
                    f"{low:.6g}..{high:.6g} Hz. The S-11 minimum at the design "
                    "frequency cannot be evaluated from this result."
                ),
                evidence=[f"target={target:.6g} Hz", f"sweep={low:.6g}..{high:.6g} Hz"],
            )
        )

    if project.nf2ff.enabled and not getattr(project.sim, "nf2ff_enabled", False):
        issues.append(
            DiagnosisIssue(
                code="nf2ff_flag_mismatch",
                severity="warning",
                title="NF2FF box is enabled but simulation NF2FF is off",
                detail=(
                    "The antenna pattern will not be written. Enable NF2FF in the "
                    "simulation settings and make sure the box fits inside the "
                    "domain with the air margin."
                ),
            )
        )
    return issues


def diagnose_run(
    project: Project,
    report: RunReport | None,
    *,
    note: str = "",
) -> Diagnosis:
    """Build the deterministic diagnosis from a RunReport and the project."""
    issues: list[DiagnosisIssue] = []
    checked: list[str] = [
        "project topology",
        "excited ports",
        "timestep budget",
        "S-parameter sweep range",
        "NF2FF flags",
    ]
    if report is None:
        issues.extend(_project_checks(project))
        return Diagnosis(
            verdict="unknown",
            headline="No simulation report is available for this session.",
            issues=issues,
            checked=checked,
            context_note=note or "Only project-level checks were possible.",
        )

    seen: set[str] = set()
    for finding in report.findings:
        issue = _finding_issue(finding, project)
        if issue is None:
            continue
        key = f"{issue.code}:{issue.title}"
        if key in seen:
            continue
        seen.add(key)
        issues.append(issue)

    # Some important conditions are engine facts, not log findings: the
    # report's max_ts_reached flag and the energy/end-criterion comparison.
    if report.facts.max_ts_reached and not any(
        issue.code == "max_ts_reached" for issue in issues
    ):
        issue = _finding_issue(
            Finding(
                "warning",
                "max_ts_reached",
                f"the engine reached {report.facts.steps or report.facts.nr_ts} "
                "timesteps",
            ),
            project,
        )
        if issue is not None:
            issues.append(issue)
    if (
        report.facts.energy_db is not None
        and not report.facts.end_criteria_hit
        and not any(issue.code == "energy_vs_criteria" for issue in issues)
    ):
        issue = _finding_issue(
            Finding(
                "warning",
                "energy_vs_criteria",
                f"energy reached {report.facts.energy_db:.1f} dB before the "
                "end criterion",
            ),
            project,
        )
        if issue is not None:
            issues.append(issue)

    issues.extend(
        _project_checks(
            project,
            skip_low_timesteps=bool(report.facts.max_ts_reached)
            or any(f.code == "max_ts_reached" for f in report.findings),
        )
    )

    if report.exit_code not in (0, None) and not any(
        issue.code == "exit_code" for issue in issues
    ):
        issues.append(
            DiagnosisIssue(
                code="exit_code",
                severity="error",
                title="The solver exited with an error",
                detail=f"The simulation ended with exit code {report.exit_code}.",
                evidence=[f"exit code {report.exit_code}"],
            )
        )

    order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda item: (order.get(item.severity, 9), item.code))
    headline = report.headline or (
        "run failed" if report.verdict == "error" else "run completed"
    )
    if (
        report.match
        and report.match.s11_min_db is not None
        and report.match.s11_min_db > -10.0
        and not any(issue.code == "no_match" for issue in issues)
    ):
        issues.append(
            DiagnosisIssue(
                code="no_match",
                severity="warning",
                title="The design never reaches -10 dB return loss",
                detail=(
                    "S11 stays above -10 dB over the simulated sweep. Check the "
                    "resonance length, feed position and the sweep band; a match "
                    "outside the simulated range is also possible."
                ),
                evidence=[
                    f"S11 minimum {report.match.s11_min_db:.2f} dB"
                    + (
                        f" at {report.match.s11_min_f_hz / 1e9:.4g} GHz"
                        if report.match.s11_min_f_hz
                        else ""
                    )
                ],
            )
        )
    if (
        not report.converged
        and not any(
            issue.code in ("max_ts_reached", "energy_vs_criteria")
            for issue in issues
        )
    ):
        issues.append(
            DiagnosisIssue(
                code="not_converged",
                severity="warning",
                title="The run did not converge",
                detail=(
                    "The report marks the run as not converged, but no specific "
                    "timestep/energy finding was available. Check the solver log "
                    "and the requested end criterion."
                ),
            )
        )
    issues.sort(key=lambda item: (order.get(item.severity, 9), item.code))
    return Diagnosis(
        verdict=report.verdict,
        headline=headline,
        issues=issues,
        checked=checked,
        context_note=note,
    )
