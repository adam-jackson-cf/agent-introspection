"""Evidence-only project attribution for derived agent observations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_introspection.identities import ProjectIdentity
from agent_introspection.source import TraceRow


@dataclass(frozen=True, slots=True)
class Attribution:
    """The project identity and exact source-backed method, if any."""

    project_id: str | None
    method: str
    project: ProjectIdentity | None = None


def direct_trace_attribution(trace: TraceRow | None) -> Attribution:
    """Resolve only complete source-emitted project metadata."""

    if (
        trace is None
        or trace.project_id is None
        or trace.project_name is None
        or trace.project_root is None
        or trace.project_kind is None
    ):
        return Attribution(None, "unresolved")
    return Attribution(
        trace.project_id,
        "trace_project",
        ProjectIdentity(
            kind=trace.project_kind,
            root=Path(trace.project_root),
            identity=trace.project_id,
            display_name=trace.project_name,
        ),
    )


def resolve_attribution(*, trace: TraceRow | None) -> Attribution:
    """Use source-emitted project metadata without cross-trace inference."""

    return direct_trace_attribution(trace)
