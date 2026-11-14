"""Project-level variable-sweep definition (Phase A)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SweepSpec:
    """One-variable sweep stored with the project (not a CSX primitive).

    ``values`` is always the explicit, resolved list of sweep values.  The
    dialog converts start/stop/points or a user list into this form so the run
    is reproducible even if the dialog defaults change later.
    """

    enabled: bool = False
    #: Legacy/primary variable name.  New projects also store ``target_kind``
    #: and ``target`` so a port excitation phase can be swept as well.
    variable: str = ""
    target_kind: str = "variable"  # "variable" | "port_phase"
    target: str = ""
    values: list[float] = field(default_factory=list)
    full_smatrix: bool = True
    collect_nf2ff: bool = False
    collect_fields: bool = False
    stop_on_error: bool = False
    #: Phase A always rebuilds geometry/mesh per point.  Kept in the schema so
    #: later versions can add "reuse_if_unchanged" without a project migration.
    mesh_policy: str = "rebuild_per_point"

    def target_name(self) -> str:
        return str(self.target or self.variable or "").strip()

    def is_phase(self) -> bool:
        return str(self.target_kind or "variable") == "port_phase"

    def is_runnable(self) -> bool:
        return (
            bool(self.enabled)
            and bool(self.target_name())
            and bool(self.values)
        )

    def summary(self) -> str:
        name = self.target_name() or "?"
        if not self.values:
            return f"{name} (no values)"
        first = self.values[0]
        last = self.values[-1]
        text = f"{name} {first:g}"
        if len(self.values) > 1:
            text += f"→{last:g}"
        if self.is_phase():
            text = f"phase {text}°"
        return f"{text} ({len(self.values)} point{'s' if len(self.values) != 1 else ''})"
