"""Canonical SigNoz dashboard JSON for Agent Introspection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
AND ts_bucket_start BETWEEN $start_timestamp - 1800 AND $end_timestamp
AND resource.`service.name`::String = 'agent-introspection'"""
ACTIVE_GENERATION_MARKER_QUERY = """(
  SELECT argMax(
    attributes_string['analysis.generation'],
    tuple(attributes_number['entity.version'], timestamp)
  )
  FROM signoz_logs.distributed_logs_v2
  WHERE resource.`service.name`::String = 'agent-introspection'
    AND attributes_string['event.name'] = 'introspection.analysis_generation.activated'
)"""
ACTIVE_GENERATION_PREDICATE = (
    """notEmpty(attributes_string['analysis.generation'])
  AND attributes_string['analysis.generation'] = """
    + ACTIVE_GENERATION_MARKER_QUERY
)
PIPELINE_SNAPSHOT_EVENT = "introspection.pipeline.snapshot"

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


def _projection_query(select: str, where_filter: str, query_tail: str = "") -> str:
    return _query(select, f"{where_filter}\n  AND {ACTIVE_GENERATION_PREDICATE}{query_tail}")


Panel = tuple[str, str, str, str, str, tuple[int, int, int, int]]

INSIGHT_PANELS: tuple[Panel, ...] = (
    (
        "project-data-attribution",
        "Project data attribution",
        "table",
        (
            "How much of the active seven-day analysis window is linked to a project, "
            "filtered by the selected display range."
        ),
        _projection_query(
            """SELECT
  round(
    100 * toFloat64(uniqExactIf(
      attributes_string['entity.id'],
      notEmpty(attributes_string['project.id'])
        AND attributes_string['project.id'] != 'unresolved'
        AND notEmpty(attributes_string['project.name'])
        AND attributes_string['project.name'] != 'unresolved'
    )) / greatest(toFloat64(uniqExact(attributes_string['entity.id'])), 1),
    2
  ) AS `Project attribution coverage`,
  toFloat64(uniqExactIf(
    attributes_string['entity.id'],
    notEmpty(attributes_string['project.id'])
      AND attributes_string['project.id'] != 'unresolved'
      AND notEmpty(attributes_string['project.name'])
      AND attributes_string['project.name'] != 'unresolved'
  )) AS `Attributed observations`,
  toFloat64(uniqExact(attributes_string['entity.id'])) AS `All observations`""",
            "attributes_string['event.name'] = 'introspection.observation.detected'",
            "\nHAVING count() > 0",
        ),
        (0, 0, 12, 5),
    ),
    (
        "actionable-trends",
        "Actionable trends",
        "table",
        (
            "Actionable patterns in the active seven-day analysis window, filtered by the "
            "selected display range."
        ),
        _projection_query(
            f"""SELECT
  left(attributes_string['entity.id'], 8) AS `Finding`,
  argMax(
    {_label_sql("attributes_string['finding.category']", DETECTOR_LABELS)},
    tuple(attributes_number['entity.version'], timestamp)
  ) AS `Category`,
  argMax(
    {_label_sql("attributes_string['detector.id']", DETECTOR_LABELS)},
    tuple(attributes_number['entity.version'], timestamp)
  ) AS `Detector`,
  argMax(
    if(
      empty(attributes_string['project.name']),
      'Unresolved',
      attributes_string['project.name']
    ),
    tuple(attributes_number['entity.version'], timestamp)
  ) AS `Project`,
  argMax(
    attributes_number['occurrence.count'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS `Occurrences`,
  formatDateTime(
    max(fromUnixTimestamp64Nano(timestamp)),
    '%d %b %H:%i'
  ) AS `Last evaluated`""",
            """attributes_string['event.name'] IN (
  'introspection.trend.evaluated', 'introspection.trend.promoted'
)""",
            """
GROUP BY attributes_string['entity.id']
HAVING argMax(
  attributes_string['trend.state'],
  tuple(attributes_number['entity.version'], timestamp)
) = 'actionable'
ORDER BY `Occurrences` DESC, `Last evaluated` DESC""",
        ),
        (0, 5, 12, 6),
    ),
    (
        "observed-signals-by-detector",
        "Observed signals by detector",
        "graph",
        (
            "Daily observations by detector in the active seven-day analysis window, filtered "
            "by the selected display range."
        ),
        _projection_query(
            f"""SELECT
  toStartOfDay(fromUnixTimestamp64Nano(timestamp)) AS ts,
  {_label_sql("attributes_string['detector.id']", DETECTOR_LABELS)} AS detector,
  toFloat64(uniqExact(attributes_string['entity.id'])) AS value""",
            "attributes_string['event.name'] = 'introspection.observation.detected'",
            """
GROUP BY ts, detector
ORDER BY ts, detector""",
        ),
        (0, 11, 12, 6),
    ),
    (
        "detector-signal-yield",
        "Detector signal yield",
        "table",
        (
            "Of distinct findings in the active seven-day analysis window, the share that "
            "becomes actionable, filtered by the selected display range."
        ),
        _projection_query(
            f"""SELECT
  {_label_sql("detector", DETECTOR_LABELS)} AS `Detector`,
  toFloat64(uniqExactIf(finding_id, trend_state = 'actionable')) AS `Actionable findings`,
  toFloat64(uniqExact(finding_id)) AS `All findings`,
  round(
    100 * toFloat64(uniqExactIf(finding_id, trend_state = 'actionable'))
      / greatest(toFloat64(uniqExact(finding_id)), 1),
    2
  ) AS `Actionable yield`
FROM (
  SELECT
    attributes_string['entity.id'] AS finding_id,
    argMax(
      attributes_string['detector.id'],
      tuple(attributes_number['entity.version'], timestamp)
    ) AS detector,
    argMax(
      attributes_string['trend.state'],
      tuple(attributes_number['entity.version'], timestamp)
    ) AS trend_state""",
            """attributes_string['event.name'] IN (
    'introspection.trend.evaluated', 'introspection.trend.promoted'
  )""",
            """
  GROUP BY finding_id
)
GROUP BY detector
ORDER BY `Actionable yield` DESC, `Detector`""",
        ),
        (0, 17, 12, 5),
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

PANELS = INSIGHT_PANELS
PROJECTION_PANEL_IDS = frozenset(panel[0] for panel in INSIGHT_PANELS)


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
            if ACTIVE_GENERATION_PREDICATE not in query:
                issues.append(f"projection panel {panel_id} does not select the active generation")
            elif "\nGROUP BY" in query and query.index(ACTIVE_GENERATION_PREDICATE) > query.index(
                "\nGROUP BY"
            ):
                issues.append(f"projection panel {panel_id} filters generation after aggregation")
            if COMMON_FILTER in ACTIVE_GENERATION_MARKER_QUERY:
                issues.append("active generation marker is time filtered")
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
