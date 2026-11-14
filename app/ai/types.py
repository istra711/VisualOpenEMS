"""Plain data types shared by the AI assistant, diagnosis and UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FixChange:
    """One validated project-field change proposed by the assistant."""

    path: str
    label: str
    old: Any
    new: Any
    risk: str = "low"  # low | medium


@dataclass
class FixProposal:
    """A small, reviewable change set.  Applying is always a user action."""

    id: str
    title: str
    description: str
    changes: list[FixChange] = field(default_factory=list)
    requires_rebuild: bool = False
    requires_rerun: bool = True
    note: str = ""

    def diff_text(self) -> str:
        lines = [f"Proposed fix: {self.title}", ""]
        for change in self.changes:
            lines.append(f"{change.label}:")
            lines.append(f"  {change.path}: {change.old!r} -> {change.new!r}")
            if change.risk != "low":
                lines.append(f"  risk: {change.risk}")
        if self.note:
            lines.extend(["", self.note])
        if self.requires_rebuild:
            lines.extend(["", "A mesh rebuild is required after applying."])
        if self.requires_rerun:
            lines.extend(["", "Re-run the simulation to verify the result."])
        return "\n".join(lines)


@dataclass
class DiagnosisIssue:
    """One probable cause with evidence and optional safe fix proposals."""

    code: str
    severity: str  # error | warning | info
    title: str
    detail: str
    evidence: list[str] = field(default_factory=list)
    fixes: list[FixProposal] = field(default_factory=list)


@dataclass
class Diagnosis:
    """Deterministic diagnosis of one run (works without any AI provider)."""

    verdict: str = "unknown"
    headline: str = ""
    issues: list[DiagnosisIssue] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    context_note: str = ""

    def all_fixes(self) -> list[FixProposal]:
        seen: set[str] = set()
        out: list[FixProposal] = []
        for issue in self.issues:
            for fix in issue.fixes:
                if fix.id in seen:
                    continue
                seen.add(fix.id)
                out.append(fix)
        return out

    def to_text(self) -> str:
        lines = [
            "DETERMINISTIC RUN DIAGNOSIS",
            f"Verdict: {self.verdict} - {self.headline}",
        ]
        if self.context_note:
            lines.append(self.context_note)
        for issue in self.issues:
            lines.append(
                f"[{issue.severity.upper()}] {issue.code}: {issue.title}"
            )
            if issue.detail:
                lines.append(f"  {issue.detail}")
            for item in issue.evidence[:8]:
                lines.append(f"  evidence: {item}")
            for fix in issue.fixes:
                lines.append(f"  fix: {fix.title}")
        if not self.issues:
            lines.append("No known failure pattern was detected in the context.")
        return "\n".join(lines)


@dataclass
class RunContext:
    """Everything the assistant is allowed to see for one run."""

    project_digest: str
    report_text: str = ""
    log_excerpt: str = ""
    script_excerpt: str = ""
    diagnosis_text: str = ""
    redaction_note: str = ""

    def display_text(self) -> str:
        parts = ["PROJECT CONTEXT", self.project_digest]
        if self.report_text:
            parts.extend(["", "RUN REPORT", self.report_text])
        if self.diagnosis_text:
            parts.extend(["", self.diagnosis_text])
        if self.log_excerpt:
            parts.extend(["", "FILTERED SIMULATION LOG", self.log_excerpt])
        if self.script_excerpt:
            parts.extend(["", "GENERATED SCRIPT (excerpt)", self.script_excerpt])
        if self.redaction_note:
            parts.extend(["", self.redaction_note])
        return "\n".join(parts)
