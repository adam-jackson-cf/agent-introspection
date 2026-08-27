"""Canonical SigNoz dashboard JSON for Agent Introspection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_introspection.project_schema import AGENT_PROJECT_SCHEMA
from agent_introspection.telemetry import CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION

_PROJECT_ATTRIBUTE_KEYS = AGENT_PROJECT_SCHEMA.dashboard_attribute_keys


DASHBOARD_UUID = "576f5068-d183-5cab-88b7-395f65cf1094"
"""The stable nested UUID of the existing Agent Introspection dashboard."""

INSIGHT_DASHBOARD_ROUTE_ID = "019f4da0-4a13-7c62-9ac9-fc6d850d633b"
"""The stable SigNoz entity ID of the Agent Introspection dashboard."""

HEALTH_DASHBOARD_UUID = "0500ebd3-0d77-4294-b2b5-352ba884daa7"
"""The stable nested UUID of the Agent Introspection Health dashboard."""

HEALTH_DASHBOARD_ROUTE_ID = "019f7fb0-6f30-77e0-ad12-6d2e44964a7d"
"""The stable SigNoz entity ID of the Agent Introspection Health dashboard."""

DASHBOARD_SCHEMA_VERSION = 1
COMMON_FILTER = """timestamp BETWEEN $start_timestamp_nano AND $end_timestamp_nano
AND resource.`service.name`::String = 'agent-introspection'"""
CANONICAL_ACTIVITY_EVENT = "introspection.activity.version.recorded"
CANONICAL_ACTIVITY_EVENT_PREDICATE = (
    f"attributes_string['event.name'] = '{CANONICAL_ACTIVITY_EVENT}'"
)
CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_PREDICATE = (
    "toUInt64OrZero(toString("
    "attributes_number['activity.payload_schema_version'])) = "
    f"{CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_VERSION}"
)
CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE = f"""{CANONICAL_ACTIVITY_EVENT_PREDICATE}
  AND {CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_PREDICATE}
  AND notEmpty(attributes_string['activity.id'])
  AND (
    attributes_string['activity.id'],
    attributes_number['activity.version']
  ) IN (
    SELECT
      attributes_string['activity.id'],
      max(attributes_number['activity.version'])
    FROM signoz_logs.distributed_logs_v2
    WHERE {COMMON_FILTER}
      AND {CANONICAL_ACTIVITY_EVENT_PREDICATE}
      AND {CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_PREDICATE}
      AND notEmpty(attributes_string['activity.id'])
    GROUP BY attributes_string['activity.id']
  )"""
CONTEXT_ACCEPTED_EVENT = "introspection.session_context.accepted"
CONTEXT_SUPERSEDED_EVENT = "introspection.session_context.superseded"
PIPELINE_SNAPSHOT_EVENT = "introspection.pipeline.snapshot"
SOURCE_SESSION_EVENT = "introspection.source_session.recorded"
SOURCE_SESSION_EVENT_PREDICATE = f"attributes_string['event.name'] = '{SOURCE_SESSION_EVENT}'"
SOURCE_SESSION_LATEST_VERSION_PREDICATE = f"""{SOURCE_SESSION_EVENT_PREDICATE}
  AND attributes_string['event.scope'] = 'source-session'
  AND notEmpty(attributes_string['entity.id'])
  AND (
    attributes_string['entity.id'],
    attributes_number['entity.version']
  ) IN (
    SELECT
      attributes_string['entity.id'],
      max(attributes_number['entity.version'])
    FROM signoz_logs.distributed_logs_v2
    WHERE {COMMON_FILTER}
      AND {SOURCE_SESSION_EVENT_PREDICATE}
      AND attributes_string['event.scope'] = 'source-session'
      AND notEmpty(attributes_string['entity.id'])
    GROUP BY attributes_string['entity.id']
  )"""

SOURCE_SESSION_PROJECT_ATTRIBUTION_PREDICATE = f"""{SOURCE_SESSION_LATEST_VERSION_PREDICATE}
  AND attributes_string['source.terminal.outcome'] = 'attributed'
  AND notEmpty(attributes_string['agent.project.id'])
  AND notEmpty(attributes_string['agent.project.name'])
  AND notEmpty(attributes_string['agent.project.root'])
  AND notEmpty(attributes_string['agent.project.kind'])"""

DETECTOR_LABELS = {
    "tool_failure": "Tool failure",
    "repeated_attempt": "Repeated attempt",
    "transport_instability": "Transport instability",
    "sandbox_friction": "Sandbox friction",
    "turn_correction": "Turn correction",
    "quality_gate_bypass": "Quality gate bypass",
    "command_churn": "Command churn",
    "tool_loop": "Tool loop",
    "token_outlier": "Token outlier",
    "skill_adherence": "Skill adherence",
    "scope_recurrence": "Scope recurrence",
}
STATUS_LABELS = {
    "succeeded": "Succeeded",
    "no_data": "No data",
    "failed": "Failed",
    "healthy": "Healthy",
    "degraded": "Degraded",
    "unhealthy": "Unhealthy",
}


def _label_sql(value: str, labels: dict[str, str]) -> str:
    """Return a deterministic label expression while retaining unknown values."""

    clauses = ",\n    ".join(f"{value} = '{raw}', '{label}'" for raw, label in labels.items())
    return f"multiIf(\n    {clauses},\n    {value}\n  )"


def _query(select: str, event_filter: str = "") -> str:
    suffix = f"\n  AND {event_filter}" if event_filter else ""
    return f"{select}\nFROM signoz_logs.distributed_logs_v2\nWHERE {COMMON_FILTER}{suffix}"


def _projection_query(select: str, query_tail: str = "") -> str:
    return _query(select, f"{CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE}{query_tail}")


Panel = tuple[str, str, str, str, str, tuple[int, int, int, int]]


def _context_coverage_query(select: str, query_tail: str = "") -> str:
    """Query source activities in the selected range against accepted context."""

    return f"""WITH latest_activities AS (
  SELECT *
  FROM signoz_logs.distributed_logs_v2
  WHERE {COMMON_FILTER}
    AND {CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE}
), accepted_context_authority AS (
  SELECT *
  FROM signoz_logs.distributed_logs_v2
  WHERE resource.`service.name`::String = 'agent-introspection'
    AND attributes_string['event.name'] = 'introspection.session_context.accepted'
    AND attributes_string['event.scope'] = 'session-context'
    AND notEmpty(attributes_string['entity.id'])
), accepted_context_valid_versions AS (
  SELECT
    attributes_string['entity.id'] AS entity_id,
    attributes_number['entity.version'] AS entity_version
  FROM accepted_context_authority
  GROUP BY entity_id, entity_version
  HAVING uniqExact(tuple(
    timestamp,
    attributes_string['event.id'],
    attributes_number['event.sequence'],
    attributes_string['producer'],
    attributes_string['producer.surface'],
    attributes_string['session.id'],
    attributes_string['event.type'],
    attributes_string['agent.project.id'],
    attributes_string['agent.project.name'],
    attributes_string['agent.project.root'],
    attributes_string['agent.project.kind']
  )) = 1
), accepted_context_deliveries AS (
  SELECT
    attributes_string['entity.id'] AS entity_id,
    attributes_number['entity.version'] AS entity_version,
    min(timestamp) AS timestamp,
    attributes_string['producer'] AS producer,
    attributes_string['session.id'] AS session_id
  FROM accepted_context_authority
  WHERE (
    attributes_string['entity.id'], attributes_number['entity.version']
  ) IN (
    SELECT entity_id, entity_version
    FROM accepted_context_valid_versions
  )
  GROUP BY
    entity_id,
    entity_version,
    producer,
    session_id,
    attributes_string['event.type'],
    attributes_string['agent.project.id'],
    attributes_string['agent.project.name']
), latest_accepted_context AS (
  SELECT *
  FROM accepted_context_deliveries
  WHERE (entity_id, entity_version) IN (
    SELECT entity_id, max(entity_version)
    FROM accepted_context_deliveries
    GROUP BY entity_id
  )
), supersession_authority AS (
  SELECT *
  FROM signoz_logs.distributed_logs_v2
  WHERE resource.`service.name`::String = 'agent-introspection'
    AND attributes_string['event.name'] = 'introspection.session_context.superseded'
    AND attributes_string['event.scope'] = 'session-context-supersession'
    AND notEmpty(attributes_string['entity.id'])
), supersession_valid_versions AS (
  SELECT
    attributes_string['entity.id'] AS entity_id,
    attributes_number['entity.version'] AS entity_version
  FROM supersession_authority
  GROUP BY entity_id, entity_version
  HAVING uniqExact(tuple(
    timestamp,
    attributes_string['event.id'],
    attributes_number['event.sequence'],
    attributes_string['replacement.event_id']
  )) = 1
), supersession_deliveries AS (
  SELECT
    attributes_string['entity.id'] AS entity_id,
    attributes_number['entity.version'] AS entity_version
  FROM supersession_authority
  WHERE (
    attributes_string['entity.id'], attributes_number['entity.version']
  ) IN (
    SELECT entity_id, entity_version
    FROM supersession_valid_versions
  )
  GROUP BY
    entity_id,
    entity_version,
    attributes_string['replacement.event_id']
), latest_supersessions AS (
  SELECT entity_id AS original_event_id
  FROM supersession_deliveries
  WHERE (entity_id, entity_version) IN (
    SELECT entity_id, max(entity_version)
    FROM supersession_deliveries
    GROUP BY entity_id
  )
), accepted_context AS (
  SELECT *
  FROM latest_accepted_context
  WHERE entity_id NOT IN (SELECT original_event_id FROM latest_supersessions)
){select}{query_tail}"""


INSIGHT_PANELS: tuple[Panel, ...] = (
    (
        "activity-coverage",
        "Activity coverage",
        "table",
        "Stable canonical activity coverage by producer and surface in the selected "
        "source-event time range.",
        _projection_query(
            """SELECT
  attributes_string['activity.producer'] AS `Producer`,
  attributes_string['activity.producer_surface'] AS `Surface`,
  toFloat64(countIf(
    attributes_string['activity.attribution.state'] = 'resolved'
    AND notEmpty(attributes_string['agent.project.id'])
    AND attributes_string['agent.project.id'] != 'unresolved'
    AND notEmpty(attributes_string['agent.project.name'])
    AND attributes_string['agent.project.name'] != 'unresolved'
  )) AS `Attributed`,
  toFloat64(countIf(attributes_string['activity.attribution.state'] = 'unresolved'))
    AS `Unresolved`,
  toFloat64(
    countIf(
      attributes_string['activity.attribution.state'] = 'resolved'
      AND notEmpty(attributes_string['agent.project.id'])
      AND attributes_string['agent.project.id'] != 'unresolved'
      AND notEmpty(attributes_string['agent.project.name'])
      AND attributes_string['agent.project.name'] != 'unresolved'
    )
    + countIf(attributes_string['activity.attribution.state'] = 'unresolved')
  ) AS `Eligible`""",
            """
GROUP BY `Producer`, `Surface`
ORDER BY `Eligible` DESC, `Producer`, `Surface`""",
        ),
        (0, 0, 12, 5),
    ),
    (
        "attribution-diagnostics",
        "Attribution diagnostics",
        "table",
        "Stable canonical activity attribution method and rejection reason in the selected "
        "source-event time range.",
        _projection_query(
            """SELECT
  attributes_string['activity.attribution.method'] AS `Attribution method`,
  if(
    attributes_string['activity.attribution.state'] = 'resolved',
    'none',
    attributes_string['activity.attribution.reason_code']
  ) AS `Rejection reason`,
  toFloat64(count()) AS `Eligible`""",
            """
GROUP BY `Attribution method`, `Rejection reason`
ORDER BY `Eligible` DESC, `Attribution method`, `Rejection reason`""",
        ),
        (0, 5, 12, 5),
    ),
    (
        "source-session-project-attribution",
        "Source session project attribution",
        "table",
        "Exact source-session project attribution in the selected source-event time range.",
        _query(
            """SELECT DISTINCT
  attributes_string['source.producer'] AS `Producer`,
  attributes_string['source.session.id'] AS `Session ID`,
  attributes_string['agent.project.id'] AS `Project ID`,
  attributes_string['agent.project.name'] AS `Project name`,
  attributes_string['agent.project.root'] AS `Project root`,
  attributes_string['agent.project.kind'] AS `Project kind`""",
            f"""{SOURCE_SESSION_PROJECT_ATTRIBUTION_PREDICATE}
ORDER BY `Producer`, `Session ID`, `Project ID`, `Project name`, `Project root`, `Project kind`""",
        ),
        (0, 10, 12, 6),
    ),
    (
        "context-to-telemetry-delay",
        "Context-to-telemetry delay",
        "graph",
        "Context-to-telemetry delay for matched sessions in the selected source-event time range.",
        _context_coverage_query(
            """SELECT
  toStartOfDay(fromUnixTimestamp64Nano(activity.timestamp)) AS ts,
  toFloat64(avg(
    dateDiff(
      'millisecond',
      fromUnixTimestamp64Nano(context.timestamp),
      fromUnixTimestamp64Nano(activity.timestamp)
    )
  )) AS value
FROM latest_activities AS activity
INNER JOIN accepted_context AS context
  ON activity.attributes_string['activity.producer'] = context.producer
  AND activity.attributes_string['activity.correlation_id']
    = context.session_id""",
            """
GROUP BY ts
ORDER BY ts""",
        ),
        (0, 16, 12, 5),
    ),
    (
        "late-context-reconciliations",
        "Late-context reconciliations",
        "table",
        "Canonical higher activity versions resolved from late context in the selected "
        "source-event time range.",
        _query(
            """SELECT
  attributes_string['activity.producer'] AS `Producer`,
  attributes_string['activity.producer_surface'] AS `Surface`,
  toFloat64(count()) AS `Late-context reconciliations`""",
            f"""{CANONICAL_ACTIVITY_EVENT_PREDICATE}
  AND {CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_PREDICATE}
  AND attributes_number['activity.version'] > 1
  AND attributes_string['activity.attribution.state'] = 'resolved'
  AND attributes_string['activity.attribution.method'] = 'session_context_interval'
GROUP BY `Producer`, `Surface`
ORDER BY `Late-context reconciliations` DESC, `Producer`, `Surface`""",
        ),
        (0, 21, 12, 5),
    ),
)

HEALTH_PANELS: tuple[Panel, ...] = (
    (
        "pipeline-health",
        "Pipeline health",
        "table",
        "Current pipeline state from the latest completed scan in the selected display range.",
        _query(
            f"""SELECT
  argMax(
    {_label_sql("attributes_string['pipeline.state']", STATUS_LABELS)},
    tuple(attributes_number['entity.version'], timestamp)
  ) AS `Pipeline state`,
  formatDateTime(
    max(fromUnixTimestamp64Nano(timestamp)),
    '%d %b %H:%i'
  ) AS `Last completed scan`,
  concat(
    toString(round(argMax(
      attributes_number['scan.duration_ms'],
      tuple(attributes_number['entity.version'], timestamp)
    ) / 1000, 2)),
    ' s'
  ) AS `Last scan duration`""",
            f"attributes_string['event.name'] = '{PIPELINE_SNAPSHOT_EVENT}'\nHAVING count() > 0",
        ),
        (0, 0, 12, 4),
    ),
    (
        "recent-scan-runs",
        "Recent scan runs",
        "table",
        "Up to 24 completed scans in the selected display range, newest first.",
        _query(
            f"""SELECT
  formatDateTime(fromUnixTimestamp64Nano(timestamp), '%d %b %H:%i') AS `Started at`,
  concat(
    toString(round(toFloat64(attributes_number['scan.duration_ms']) / 1000, 2)),
    ' s'
  ) AS `Duration`,
  {_label_sql("attributes_string['scan.terminal_status']", STATUS_LABELS)} AS `Outcome`,
  toFloat64(attributes_number['rows.processed']) AS `Rows processed`""",
            (
                f"attributes_string['event.name'] = '{PIPELINE_SNAPSHOT_EVENT}'"
                "\nORDER BY timestamp DESC\nLIMIT 24"
            ),
        ),
        (0, 4, 12, 6),
    ),
)
PROJECTION_PANEL_IDS = frozenset(
    panel[0]
    for panel in INSIGHT_PANELS
    if panel[0] not in {"late-context-reconciliations", "source-session-project-attribution"}
)
PANELS = INSIGHT_PANELS


def _widget(
    panel_id: str, title: str, panel_type: str, description: str, query: str
) -> dict[str, Any]:
    return {
        "description": description,
        "id": panel_id,
        "panelTypes": panel_type,
        "query": {
            "builder": {"queryData": [], "queryFormulas": []},
            "clickhouse_sql": [
                {
                    "disabled": False,
                    "legend": "" if panel_type == "graph" else title,
                    "name": "A",
                    "query": query,
                }
            ],
            "queryType": "clickhouse_sql",
        },
        "timePreferance": "GLOBAL_TIME",
        "title": title,
    }


def _build_dashboard(
    *,
    title: str,
    description: str,
    panels: tuple[Panel, ...],
    uuid: str | None,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "description": description,
        "layout": [
            {
                "h": panel[5][3],
                "i": panel[0],
                "moved": False,
                "static": False,
                "w": panel[5][2],
                "x": panel[5][0],
                "y": panel[5][1],
            }
            for panel in panels
        ],
        "panelMap": {},
        "tags": ["agent-introspection", "codex"],
        "title": title,
        "uploadedGrafana": False,
        "variables": {},
        "version": "v5",
        "widgets": [_widget(*panel[:5]) for panel in panels],
        "schemaVersion": DASHBOARD_SCHEMA_VERSION,
        "locked": True,
    }
    if uuid is not None:
        document["uuid"] = uuid
    return document


def build_dashboard() -> dict[str, Any]:
    """Build the stable existing insight dashboard."""

    return _build_dashboard(
        title="Agent Introspection",
        description="Observed agent behaviours in the selected display range.",
        panels=INSIGHT_PANELS,
        uuid=DASHBOARD_UUID,
    )


def build_health_dashboard() -> dict[str, Any]:
    """Build the bootstrap-safe Health dashboard without inventing its identity."""

    return _build_dashboard(
        title="Agent Introspection Health",
        description="Hourly Agent Introspection pipeline health in the selected display range.",
        panels=HEALTH_PANELS,
        uuid=HEALTH_DASHBOARD_UUID,
    )


def _verify_dashboard(
    document: dict[str, Any],
    *,
    panels: tuple[Panel, ...],
    expected_uuid: str | None,
    identity_name: str,
) -> list[str]:
    issues: list[str] = []
    if expected_uuid is None:
        if "uuid" in document:
            issues.append(f"{identity_name} dashboard identity is not bootstrapped")
    elif document.get("uuid") != expected_uuid:
        issues.append(f"{identity_name} dashboard identity changed")
    if document.get("schemaVersion") != DASHBOARD_SCHEMA_VERSION:
        issues.append(f"{identity_name} dashboard schema version changed")
    widgets = document.get("widgets")
    if not isinstance(widgets, list) or len(widgets) != len(panels):
        issues.append(f"{identity_name} dashboard panel set is incomplete")
        return issues
    expected = {panel[0]: panel for panel in panels}
    actual = {
        widget.get("id"): widget
        for widget in widgets
        if isinstance(widget, dict) and isinstance(widget.get("id"), str)
    }
    if set(actual) != set(expected):
        issues.append(f"{identity_name} dashboard panel identities changed")
        return issues
    layouts = document.get("layout")
    layout_by_id = (
        {
            layout["i"]: layout
            for layout in layouts
            if isinstance(layout, dict) and isinstance(layout.get("i"), str)
        }
        if isinstance(layouts, list)
        else {}
    )
    if set(layout_by_id) != set(expected):
        issues.append(f"{identity_name} dashboard layout identities changed")
    for panel_id, panel in expected.items():
        _expected_id, title, panel_type, description, _query_text, expected_layout = panel
        widget = actual[panel_id]
        if (
            widget.get("title") != title
            or widget.get("panelTypes") != panel_type
            or widget.get("description") != description
        ):
            issues.append(f"panel {panel_id} presentation changed")
        layout = layout_by_id.get(panel_id)
        if (
            layout is None
            or tuple(layout.get(key) for key in ("x", "y", "w", "h")) != expected_layout
        ):
            issues.append(f"panel {panel_id} layout changed")
        queries = widget.get("query", {}).get("clickhouse_sql", [])
        if len(queries) != 1 or not isinstance(queries[0], dict):
            issues.append(f"panel {panel_id} has an invalid query definition")
            continue
        query = queries[0].get("query", "")
        if not isinstance(query, str) or COMMON_FILTER not in query:
            issues.append(f"panel {panel_id} does not use the common filter")
            continue
        if panel_type == "graph" and (" AS ts" not in query or " AS value" not in query):
            issues.append(f"visual panel {panel_id} lacks ts and value columns")
        if panel_id in PROJECTION_PANEL_IDS:
            if CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE not in query:
                issues.append(
                    f"projection panel {panel_id} does not select latest activity version"
                )
            elif "\nGROUP BY" in query and query.index(
                CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE
            ) > query.index("\nGROUP BY"):
                issues.append(
                    f"projection panel {panel_id} filters activity version after aggregation"
                )
        elif panel_id == "source-session-project-attribution":
            if SOURCE_SESSION_LATEST_VERSION_PREDICATE not in query:
                issues.append("source-session panel does not select latest source-session version")
            elif "\nORDER BY" in query and query.index(
                SOURCE_SESSION_LATEST_VERSION_PREDICATE
            ) > query.index("\nORDER BY"):
                issues.append("source-session panel filters source-session version after ordering")
            elif SOURCE_SESSION_PROJECT_ATTRIBUTION_PREDICATE not in query:
                issues.append(
                    "source-session panel does not restrict to complete attributed projects"
                )
    return issues


def verify_dashboard(document: dict[str, Any]) -> list[str]:
    """Report insight-dashboard identity, presentation, layout, and query drift."""

    return _verify_dashboard(
        document, panels=INSIGHT_PANELS, expected_uuid=DASHBOARD_UUID, identity_name="insight"
    )


def verify_health_dashboard(document: dict[str, Any]) -> list[str]:
    """Report Health-dashboard bootstrap, presentation, layout, and query drift."""

    return _verify_dashboard(
        document, panels=HEALTH_PANELS, expected_uuid=HEALTH_DASHBOARD_UUID, identity_name="health"
    )


def render_dashboard_json() -> str:
    return json.dumps(build_dashboard(), indent=2, sort_keys=True) + "\n"


def render_health_dashboard_json() -> str:
    return json.dumps(build_health_dashboard(), indent=2, sort_keys=True) + "\n"


def load_dashboard(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("dashboard JSON must contain an object")
    return value
