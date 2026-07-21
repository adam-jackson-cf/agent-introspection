from __future__ import annotations

from datetime import UTC, datetime

from agent_introspection.attribution import (
    Attribution,
    direct_trace_attribution,
    resolve_attribution,
)
from agent_introspection.source import TraceRow


def _trace(
    *,
    project_id: str | None = "project-1",
    project_name: str | None = "Agent Introspection",
    project_root: str | None = "/workspace/agent-introspection",
    project_kind: str | None = "git",
) -> TraceRow:
    return TraceRow(
        trace_id="trace-1",
        turn_id=None,
        thread_id="thread-1",
        project_id=project_id,
        project_name=project_name,
        project_root=project_root,
        project_kind=project_kind,
        started_at=datetime(2026, 7, 20, tzinfo=UTC),
        ended_at=datetime(2026, 7, 20, 1, tzinfo=UTC),
        total_tokens=1,
        tool_calls=0,
    )


def test_direct_trace_attribution_uses_complete_source_project_metadata() -> None:
    attribution = direct_trace_attribution(_trace())

    assert attribution.method == "trace_project"
    assert attribution.project_id == "project-1"
    assert attribution.project is not None
    assert attribution.project.display_name == "Agent Introspection"
    assert attribution.project.root.as_posix() == "/workspace/agent-introspection"


def test_missing_source_project_metadata_is_unresolved_without_cross_trace_inference() -> None:
    assert direct_trace_attribution(_trace(project_name=None)) == Attribution(None, "unresolved")
    assert resolve_attribution(trace=None) == Attribution(None, "unresolved")
