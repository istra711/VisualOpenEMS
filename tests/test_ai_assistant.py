"""Unit tests for the deterministic AI error assistant (no network)."""

from __future__ import annotations

import json
from pathlib import Path

from app.ai.backend import OpenCodeBackend
from app.ai.context import build_prompt, build_run_context, redact_text
from app.ai.diagnosis import diagnose_run
from app.ai.fixes import FixChange, apply_fix
from app.models.project import PortDef, Project
from app.simulation.report import EngineFacts, Finding, RunReport


def _project() -> Project:
    project = Project.new_default("ai-test")
    project.sim.max_timesteps = 100_000
    project.ports = [
        PortDef(
            name="p1",
            start=[0.0, 0.0, 0.0],
            stop=[0.0, 0.0, 1.0],
            excite_port=False,
        )
    ]
    return project


def _report() -> RunReport:
    return RunReport(
        verdict="warning",
        headline="not converged (max timesteps)",
        trust="use with care",
        exit_code=0,
        converged=False,
        facts=EngineFacts(
            steps=100_000,
            nr_ts=100_000,
            max_ts_reached=True,
            energy_db=-12.0,
        ),
        findings=[
            Finding("warning", "max_ts_reached", "max timesteps reached"),
            Finding("warning", "energy_vs_criteria", "energy did not decay"),
        ],
    )


def test_diagnosis_and_safe_timestep_fix():
    project = _project()
    diagnosis = diagnose_run(project, _report())

    codes = {issue.code for issue in diagnosis.issues}
    assert "max_ts_reached" in codes
    assert "no_excited_port" in codes

    fix = next(f for f in diagnosis.all_fixes() if f.id == "max_timesteps_2x")
    assert fix.changes[0].path == "sim.max_timesteps"
    assert fix.changes[0].new > project.sim.max_timesteps

    changed, errors = apply_fix(project, fix)
    assert not errors
    assert changed.sim.max_timesteps == fix.changes[0].new
    # the source object is untouched by a non-inplace apply
    assert project.sim.max_timesteps == 100_000


def test_max_ts_fact_without_log_finding_still_offers_fix():
    report = RunReport(
        verdict="warning",
        headline="not converged",
        exit_code=0,
        converged=False,
        facts=EngineFacts(
            steps=100_000,
            nr_ts=100_000,
            max_ts_reached=True,
        ),
        findings=[],
    )
    diagnosis = diagnose_run(_project(), report)
    assert any(issue.code == "max_ts_reached" for issue in diagnosis.issues)
    assert any(fix.id == "max_timesteps_2x" for fix in diagnosis.all_fixes())


def test_excite_port_fix_is_offered_and_applied_in_place():
    project = _project()
    diagnosis = diagnose_run(project, _report())
    fix = next(f for f in diagnosis.all_fixes() if f.id == "excite_port_0")

    changed, errors = apply_fix(project, fix, inplace=True)
    assert not errors
    assert changed is project
    assert project.ports[0].excite_port is True


def test_fix_whitelist_rejects_geometry_and_bad_values():
    project = _project()
    bad = FixChange(
        path="mesh.start_frequency",
        label="Sweep start",
        old=project.mesh.start_frequency,
        new=1.0,
    )
    _changed, errors = apply_fix(
        project,
        type(
            "Fix",
            (),
            {"changes": [bad]},
        )(),
    )
    assert errors and "not allowed" in errors[0]

    bad_value = FixChange(
        path="sim.max_timesteps",
        label="max timesteps",
        old=project.sim.max_timesteps,
        new=-1,
    )
    _changed, errors = apply_fix(
        project,
        type(
            "Fix",
            (),
            {"changes": [bad_value]},
        )(),
    )
    assert errors


def test_context_redacts_user_paths_and_tokens():
    text = (
        r"log at C:\Users\Alice\work\proj\sim_log.txt "
        "token=sk-abcdefghijklmnop and ghp_abcdefghijklmnopqrstuvwxyz"
    )
    clean = redact_text(text)
    assert "Alice" not in clean
    assert "sk-abcdefghijklmnop" not in clean
    assert "ghp_" not in clean


def test_prompt_contains_question_report_and_project():
    project = _project()
    diagnosis = diagnose_run(project, _report())
    context = build_run_context(project, project.primitives or [], _report(), diagnosis)
    prompt = build_prompt(
        "Why did S11 fail?",
        context,
        language="de",
        max_chars=26000,
    )
    assert "Why did S11 fail?" in prompt
    assert "max_ts_reached" in prompt
    assert "ai-test" in prompt
    assert "Antworte auf Deutsch." in prompt


def test_opencode_event_parser():
    line = json.dumps(
        {
            "type": "text",
            "sessionID": "ses_abc",
            "part": {"type": "text", "text": "Hello"},
        }
    )
    event = OpenCodeBackend.parse_event_line(line)
    assert event["type"] == "text"
    assert event["part"]["text"] == "Hello"
    assert event["sessionID"] == "ses_abc"
    assert OpenCodeBackend.parse_event_line("not json")["type"] == "raw"


def test_backend_command_resolution_with_explicit_path(tmp_path: Path):
    fake = tmp_path / "opencode.exe"
    fake.write_text("", encoding="utf-8")
    backend = OpenCodeBackend(str(fake))
    assert backend.is_available()
    assert backend.resolved_command() == str(fake)


def test_backend_cmd_shim_resolves_to_real_npm_executable(tmp_path: Path):
    shim = tmp_path / "opencode.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    real = (
        tmp_path
        / "node_modules"
        / "@opencode"
        / "cli"
        / "bin"
        / "opencode.exe"
    )
    real.parent.mkdir(parents=True)
    real.write_text("", encoding="utf-8")
    backend = OpenCodeBackend(str(shim))
    assert backend.resolved_command() == str(real)
