"""Stable SigNoz dashboard JSON for derived introspection telemetry."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DASHBOARD_UUID = "576f5068-d183-5cab-88b7-395f65cf1094"
DASHBOARD_SCHEMA_VERSION = 1
COMMON_FILTER = """timestamp BETWEEN $start_timestamp_nano AND $end_timestamp_nano
AND ts_bucket_start BETWEEN $start_timestamp - 1800 AND $end_timestamp
AND resource.`service.name`::String = 'agent-introspection'"""


def _query(select: str, event_filter: str = "") -> str:
    suffix = f"\n  AND {event_filter}" if event_filter else ""
    return f"{select}\nFROM signoz_logs.distributed_logs_v2\nWHERE {COMMON_FILTER}{suffix}"


PANELS: tuple[tuple[str, str, str, str], ...] = (
    (
        "scan-health",
        "Latest scan health and source availability",
        "table",
        _query(
            """SELECT
  argMax(
    attributes_string['scan.status'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS scan_status,
  argMax(
    attributes_string['source.availability'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS source_availability,
  formatDateTime(
    max(fromUnixTimestamp64Nano(timestamp)),
    '%d %b %H:%i'
  ) AS last_scan,
  argMax(
    attributes_number['scan.duration_ms'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS duration_ms,
  argMax(
    attributes_number['source.lag_ms'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS source_lag_ms,
  argMax(
    attributes_number['rows.processed'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS rows_processed""",
            "attributes_string['event.name'] = 'introspection.scan.completed'",
        ),
    ),
    (
        "observations",
        "Observations by detector and project",
        "graph",
        _query(
            """SELECT
  toStartOfDay(fromUnixTimestamp64Nano(timestamp)) AS ts,
  attributes_string['detector.id'] AS detector,
  if(
    empty(attributes_string['project.name']),
    left(attributes_string['project.id'], 12),
    attributes_string['project.name']
  ) AS project,
  toFloat64(uniqExact(attributes_string['event.id'])) AS value""",
            """attributes_string['event.name'] = 'introspection.observation.detected'
GROUP BY ts, detector, project
ORDER BY ts, value DESC
LIMIT 8 BY ts""",
        ),
    ),
    (
        "trend-state",
        "Current trend-state distribution",
        "bar",
        _query(
            """SELECT
  toStartOfMinute(now()) AS ts,
  trend_state,
  toFloat64(count()) AS value
FROM (
  SELECT
    attributes_string['entity.id'] AS finding_id,
    argMax(
      attributes_string['trend.state'],
      tuple(attributes_number['entity.version'], timestamp)
    ) AS trend_state""",
            """attributes_string['event.name'] IN (
    'introspection.trend.evaluated', 'introspection.trend.promoted'
  )
  GROUP BY finding_id
)
GROUP BY trend_state""",
        ),
    ),
    (
        "actionable-trends",
        "Actionable trends",
        "table",
        _query(
            """SELECT
  left(attributes_string['entity.id'], 8) AS finding,
  argMax(
    attributes_string['finding.category'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS category,
  argMax(
    if(
      empty(attributes_string['project.name']),
      left(attributes_string['project.id'], 12),
      attributes_string['project.name']
    ),
    tuple(attributes_number['entity.version'], timestamp)
  ) AS project,
  argMax(
    attributes_number['occurrence.count'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS occurrences""",
            """attributes_string['event.name'] IN (
  'introspection.trend.evaluated', 'introspection.trend.promoted'
)
GROUP BY attributes_string['entity.id']
HAVING argMax(
  attributes_string['trend.state'],
  tuple(attributes_number['entity.version'], timestamp)
) = 'actionable'
ORDER BY occurrences DESC""",
        ),
    ),
    (
        "pending-proposals",
        "Pending proposal age and scope",
        "table",
        _query(
            """SELECT
  attributes_string['entity.id'] AS proposal_id,
  argMax(
    attributes_string['proposal.scope'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS scope,
  dateDiff('hour', min(fromUnixTimestamp64Nano(timestamp)), now()) AS age_hours""",
            """attributes_string['event.name'] = 'introspection.proposal.state_changed'
GROUP BY proposal_id
HAVING argMax(
  attributes_string['proposal.state'],
  tuple(attributes_number['entity.version'], timestamp)
) = 'pending'
ORDER BY age_hours DESC""",
        ),
    ),
    (
        "proposal-outcomes",
        "Proposal outcomes by intervention type",
        "bar",
        _query(
            """SELECT
  toStartOfMinute(now()) AS ts,
  attributes_string['intervention.type'] AS intervention_type,
  attributes_string['proposal.state'] AS outcome,
  toFloat64(uniqExact(attributes_string['entity.id'])) AS value""",
            """attributes_string['event.name'] = 'introspection.proposal.state_changed'
GROUP BY intervention_type, outcome""",
        ),
    ),
    (
        "detector-ratios",
        "Detector promotion and approval ratios",
        "table",
        _query(
            """SELECT
  attributes_string['detector.id'] AS detector,
  countIf(
    attributes_string['event.name'] = 'introspection.trend.promoted'
  ) / greatest(uniqExact(attributes_string['finding.id']), 1) AS promotion_ratio,
  countIf(
    attributes_string['proposal.state'] = 'approved'
  ) / greatest(
    uniqExactIf(
      attributes_string['entity.id'],
      attributes_string['event.name'] = 'introspection.proposal.state_changed'
    ),
    1
  ) AS approval_ratio""",
            """attributes_string['event.name'] IN (
  'introspection.observation.detected',
  'introspection.trend.promoted',
  'introspection.proposal.state_changed'
)
GROUP BY detector""",
        ),
    ),
    (
        "post-application-recurrence",
        "Post-application recurrence",
        "table",
        _query(
            """SELECT
  attributes_string['finding.id'] AS finding_id,
  uniqExact(attributes_string['event.id']) AS recurrence_count,
  max(fromUnixTimestamp64Nano(timestamp)) AS latest_recurrence""",
            """attributes_bool['post_application'] = true
  AND attributes_string['event.name'] = 'introspection.observation.detected'
GROUP BY finding_id
ORDER BY recurrence_count DESC""",
        ),
    ),
    (
        "model-usage",
        "Model calls, provenance and token usage",
        "table",
        _query(
            """SELECT
  attributes_string['model'] AS model,
  attributes_string['reasoning.effort'] AS effort,
  uniqExact(attributes_string['trace.id']) AS calls,
  sum(attributes_number['token.total']) AS total_tokens""",
            """attributes_string['event.name'] = 'introspection.model.run'
GROUP BY model, effort
ORDER BY total_tokens DESC""",
        ),
    ),
    (
        "scan-performance",
        "Scan duration (ms)",
        "graph",
        _query(
            """SELECT
  fromUnixTimestamp64Nano(timestamp) AS ts,
  toFloat64(attributes_number['scan.duration_ms']) AS value""",
            "attributes_string['event.name'] = 'introspection.scan.completed'\nORDER BY ts",
        ),
    ),
    (
        "outbox-backlog",
        "Outbox backlog",
        "value",
        _query(
            """SELECT argMax(
  attributes_number['outbox.pending'],
  tuple(attributes_number['entity.version'], timestamp)
) AS value""",
            "attributes_string['event.name'] = 'introspection.outbox.snapshot'",
        ),
    ),
    (
        "sqlite-health",
        "SQLite integrity, size and backup age",
        "table",
        _query(
            """SELECT
  argMax(
    attributes_string['sqlite.integrity'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS integrity,
  argMax(
    attributes_number['sqlite.size_bytes'],
    tuple(attributes_number['entity.version'], timestamp)
  ) / 1048576 AS size_mib,
  argMax(
    attributes_number['sqlite.backup_age_seconds'],
    tuple(attributes_number['entity.version'], timestamp)
  ) / 3600 AS backup_age_hours""",
            "attributes_string['event.name'] = 'introspection.sqlite.health'",
        ),
    ),
    (
        "project-concentration",
        "Project concentration",
        "bar",
        _query(
            """SELECT
  toStartOfMinute(now()) AS ts,
  if(
    empty(attributes_string['project.name']),
    left(attributes_string['project.id'], 12),
    attributes_string['project.name']
  ) AS project,
  toFloat64(uniqExact(attributes_string['event.id'])) AS value""",
            """attributes_string['event.name'] = 'introspection.observation.detected'
GROUP BY project
ORDER BY value DESC""",
        ),
    ),
)


def _widget(panel_id: str, title: str, panel_type: str, query: str) -> dict[str, Any]:
    legend = "" if panel_type in {"bar", "graph"} else title
    return {
        "id": panel_id,
        "panelTypes": panel_type,
        "query": {
            "builder": {"queryData": [], "queryFormulas": []},
            "clickhouse_sql": [{"disabled": False, "legend": legend, "name": "A", "query": query}],
            "queryType": "clickhouse_sql",
        },
        "timePreferance": "GLOBAL_TIME",
        "title": title,
    }


def build_dashboard() -> dict[str, Any]:
    widgets = [_widget(*panel) for panel in PANELS]
    panel_layout = {
        "scan-health": (0, 0, 6, 3),
        "outbox-backlog": (6, 0, 2, 3),
        "sqlite-health": (8, 0, 4, 3),
        "actionable-trends": (0, 3, 8, 6),
        "trend-state": (8, 3, 4, 6),
        "observations": (0, 9, 8, 5),
        "project-concentration": (8, 9, 4, 5),
        "detector-ratios": (0, 14, 6, 5),
        "scan-performance": (6, 14, 6, 5),
        "pending-proposals": (0, 19, 6, 4),
        "proposal-outcomes": (6, 19, 6, 4),
        "model-usage": (0, 23, 6, 4),
        "post-application-recurrence": (6, 23, 6, 4),
    }
    layout = [
        {
            "h": panel_layout[panel[0]][3],
            "i": panel[0],
            "moved": False,
            "static": False,
            "w": panel_layout[panel[0]][2],
            "x": panel_layout[panel[0]][0],
            "y": panel_layout[panel[0]][1],
        }
        for panel in PANELS
    ]
    return {
        "description": "Derived agent introspection telemetry, trends, proposals and operations",
        "layout": layout,
        "panelMap": {},
        "tags": ["agent-introspection", "codex"],
        "title": "Agent Introspection",
        "uploadedGrafana": False,
        "uuid": DASHBOARD_UUID,
        "variables": {},
        "version": "v5",
        "widgets": widgets,
        "schemaVersion": DASHBOARD_SCHEMA_VERSION,
        "locked": True,
    }


def verify_dashboard(document: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if document.get("uuid") != DASHBOARD_UUID:
        issues.append("dashboard identity changed")
    if document.get("schemaVersion") != DASHBOARD_SCHEMA_VERSION:
        issues.append("dashboard schema version changed")
    widgets = document.get("widgets")
    if not isinstance(widgets, list) or len(widgets) != len(PANELS):
        issues.append("dashboard panel set is incomplete")
        return issues
    expected_ids = {panel[0] for panel in PANELS}
    actual_ids = {widget.get("id") for widget in widgets if isinstance(widget, dict)}
    if actual_ids != expected_ids:
        issues.append("dashboard panel identities changed")
    for widget in widgets:
        queries = widget.get("query", {}).get("clickhouse_sql", [])
        if len(queries) != 1 or COMMON_FILTER not in queries[0].get("query", ""):
            issues.append(f"panel {widget.get('id')} does not use the common filter")
            continue
        query = queries[0]["query"]
        if widget.get("panelTypes") == "graph" and (
            " AS ts" not in query or " AS value" not in query
        ):
            issues.append(f"graph panel {widget.get('id')} lacks ts and value columns")
        if widget.get("id") == "scan-performance" and (
            "toFloat64(attributes_number['scan.duration_ms']) AS value" not in query
            or "source.lag_ms" in query
            or "rows.processed" in query
        ):
            issues.append("scan performance is not a duration-only numeric series")
        if widget.get("id") == "scan-health" and not all(
            field in query for field in ("duration_ms", "source_lag_ms", "rows_processed")
        ):
            issues.append("scan health lacks current operational columns")
    return issues


def render_dashboard_json() -> str:
    return json.dumps(build_dashboard(), indent=2, sort_keys=True) + "\n"


def load_dashboard(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("dashboard JSON must contain an object")
    return value
