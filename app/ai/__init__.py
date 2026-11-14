"""AI support for VisualopenEMS.

Phase 1 is a read-only error assistant: it can read project/run context and
propose a small set of validated parameter changes, but it never writes files
or starts simulations on its own.
"""

from app.ai.types import (
    Diagnosis,
    DiagnosisIssue,
    FixChange,
    FixProposal,
    RunContext,
)

__all__ = [
    "Diagnosis",
    "DiagnosisIssue",
    "FixChange",
    "FixProposal",
    "RunContext",
]
