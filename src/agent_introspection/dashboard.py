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
REVIEW_ACTIVITY_SNAPSHOT_EVENT = "introspection.review.activity_snapshot"
CURRENT_REVIEW_ACTIVITY_ORDER = """tuple(
    attributes_number['entity.version'],
    attributes_number['event.sequence'],
    attributes_string['event.id']
  )"""
PROJECTION_PANEL_IDS = frozenset(
    {
        "project-identity-coverage",
        "actionable-trends",
        "current-trend-context",
        "observed-signal-mix",
        "detector-signal-yield",
    }
)


def _query(select: str, event_filter: str = "") -> str:
    suffix = f"\n  AND {event_filter}" if event_filter else ""
    return f"{select}\nFROM signoz_logs.distributed_logs_v2\nWHERE {COMMON_FILTER}{suffix}"


def _projection_query(select: str, where_filter: str, query_tail: str = "") -> str:
    return _query(
        select,
        f"{where_filter}\n  AND {ACTIVE_GENERATION_PREDICATE}{query_tail}",
    )


Panel = tuple[str, str, str, str, tuple[int, int, int, int]]

PANELS: tuple[Panel, ...] = (
    (
        "pipeline-health",
        "Pipeline health",
        "table",
        _query(
            """SELECT
  argMax(
    attributes_string['pipeline.state'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS pipeline_state,
  argMax(
    attributes_string['scan.terminal_status'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS terminal_status,
  argMax(
    attributes_string['pipeline.freshness'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS freshness,
  concat(
    argMax(
      attributes_string['logs.query_status'],
      tuple(attributes_number['entity.version'], timestamp)
    ),
    ' / ',
    argMax(
      attributes_string['logs.data_state'],
      tuple(attributes_number['entity.version'], timestamp)
    )
  ) AS logs,
  concat(
    argMax(
      attributes_string['traces.query_status'],
      tuple(attributes_number['entity.version'], timestamp)
    ),
    ' / ',
    argMax(
      attributes_string['traces.data_state'],
      tuple(attributes_number['entity.version'], timestamp)
    )
  ) AS traces,
  formatDateTime(max(fromUnixTimestamp64Nano(timestamp)), '%d %b %H:%i') AS last_snapshot""",
            f"attributes_string['event.name'] = '{PIPELINE_SNAPSHOT_EVENT}'\nHAVING count() > 0",
        ),
        (0, 0, 6, 3),
    ),
    (
        "scan-duration",
        "Scan duration (ms)",
        "graph",
        _query(
            """SELECT
  fromUnixTimestamp64Nano(timestamp) AS ts,
  toFloat64(attributes_number['scan.duration_ms']) AS value""",
            f"attributes_string['event.name'] = '{PIPELINE_SNAPSHOT_EVENT}'\nORDER BY ts",
        ),
        (6, 0, 3, 3),
    ),
    (
        "project-identity-coverage",
        "Project identity coverage",
        "table",
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
  ) AS identity_coverage_pct,
  toFloat64(uniqExactIf(
    attributes_string['entity.id'],
    notEmpty(attributes_string['project.id'])
      AND attributes_string['project.id'] != 'unresolved'
      AND notEmpty(attributes_string['project.name'])
      AND attributes_string['project.name'] != 'unresolved'
  )) AS resolved_observations,
  toFloat64(uniqExact(attributes_string['entity.id'])) AS observed_observations""",
            "attributes_string['event.name'] = 'introspection.observation.detected'",
            "\nHAVING count() > 0",
        ),
        (9, 0, 3, 3),
    ),
    (
        "actionable-trends",
        "Actionable trends requiring review",
        "table",
        _projection_query(
            """SELECT
  left(attributes_string['entity.id'], 8) AS finding,
  argMax(
    attributes_string['finding.category'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS category,
  argMax(
    attributes_string['detector.id'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS detector,
  argMax(
    if(
      empty(attributes_string['project.name']),
      'unresolved',
      attributes_string['project.name']
    ),
    tuple(attributes_number['entity.version'], timestamp)
  ) AS project,
  argMax(
    attributes_number['occurrence.count'],
    tuple(attributes_number['entity.version'], timestamp)
  ) AS occurrences,
  formatDateTime(
    max(fromUnixTimestamp64Nano(timestamp)),
    '%d %b %H:%i'
  ) AS last_evaluated""",
            """attributes_string['event.name'] IN (
  'introspection.trend.evaluated', 'introspection.trend.promoted'
)""",
            """
GROUP BY attributes_string['entity.id']
HAVING argMax(
  attributes_string['trend.state'],
  tuple(attributes_number['entity.version'], timestamp)
) = 'actionable'
ORDER BY occurrences DESC, last_evaluated DESC""",
        ),
        (0, 3, 8, 6),
    ),
    (
        "current-trend-context",
        "Current trend context",
        "bar",
        _projection_query(
            """SELECT
  toStartOfMinute(max(last_evaluated_at)) AS ts,
  trend_state,
  toFloat64(count()) AS value
FROM (
  SELECT
    attributes_string['entity.id'] AS finding_id,
    argMax(
      attributes_string['trend.state'],
      tuple(attributes_number['entity.version'], timestamp)
    ) AS trend_state,
    max(fromUnixTimestamp64Nano(timestamp)) AS last_evaluated_at""",
            """attributes_string['event.name'] IN (
    'introspection.trend.evaluated', 'introspection.trend.promoted'
  )""",
            """
  GROUP BY finding_id
)
GROUP BY trend_state
ORDER BY trend_state""",
        ),
        (8, 3, 4, 6),
    ),
    (
        "observed-signal-mix",
        "Observed signal mix by detector",
        "graph",
        _projection_query(
            """SELECT
  toStartOfDay(fromUnixTimestamp64Nano(timestamp)) AS ts,
  attributes_string['detector.id'] AS detector,
  toFloat64(uniqExact(attributes_string['entity.id'])) AS value""",
            "attributes_string['event.name'] = 'introspection.observation.detected'",
            """
GROUP BY ts, detector
ORDER BY ts, detector""",
        ),
        (0, 9, 6, 4),
    ),
    (
        "detector-signal-yield",
        "Detector signal yield",
        "table",
        _projection_query(
            """SELECT
  detector,
  toFloat64(uniqExactIf(finding_id, trend_state = 'actionable')) AS actionable_findings,
  toFloat64(uniqExact(finding_id)) AS all_findings,
  round(
    100 * toFloat64(uniqExactIf(finding_id, trend_state = 'actionable'))
      / greatest(toFloat64(uniqExact(finding_id)), 1),
    2
  ) AS actionable_yield_pct
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
ORDER BY actionable_yield_pct DESC, detector""",
        ),
        (6, 9, 3, 4),
    ),
    (
        "review-activity",
        "Review activity",
        "table",
        """SELECT
  argMax(
    attributes_number['review.classification.session_count'],
    """
        + CURRENT_REVIEW_ACTIVITY_ORDER
        + """
  ) AS classification_sessions,
  argMax(
    attributes_number['review.proposal.session_count'],
    """
        + CURRENT_REVIEW_ACTIVITY_ORDER
        + """
  ) AS proposal_sessions,
  argMax(
    attributes_number['review.classification.result_count'],
    """
        + CURRENT_REVIEW_ACTIVITY_ORDER
        + """
  ) AS classification_results,
  argMax(
    attributes_number['review.proposal.result_count'],
    """
        + CURRENT_REVIEW_ACTIVITY_ORDER
        + """
  ) AS proposal_results
FROM signoz_logs.distributed_logs_v2
WHERE resource.`service.name`::String = 'agent-introspection'
  AND attributes_string['event.name'] = '"""
        + REVIEW_ACTIVITY_SNAPSHOT_EVENT
        + """'
  AND attributes_string['review.activity.availability'] = 'available'
  AND notEmpty(attributes_string['snapshot.trigger.kind'])
HAVING count() > 0""",
        (9, 9, 3, 4),
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
    widgets = [_widget(*panel[:4]) for panel in PANELS]
    layout = [
        {
            "h": panel[4][3],
            "i": panel[0],
            "moved": False,
            "static": False,
            "w": panel[4][2],
            "x": panel[4][0],
            "y": panel[4][1],
        }
        for panel in PANELS
    ]
    return {
        "description": "Derived agent introspection pipeline, signals, and review activity",
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
    """Report dashboard identity, panel, layout, and query-contract drift."""

    issues: list[str] = []
    if document.get("uuid") != DASHBOARD_UUID:
        issues.append("dashboard identity changed")
    if document.get("schemaVersion") != DASHBOARD_SCHEMA_VERSION:
        issues.append("dashboard schema version changed")
    widgets = document.get("widgets")
    if not isinstance(widgets, list) or len(widgets) != len(PANELS):
        issues.append("dashboard panel set is incomplete")
        return issues
    expected = {panel[0]: panel for panel in PANELS}
    actual = {
        widget.get("id"): widget
        for widget in widgets
        if isinstance(widget, dict) and isinstance(widget.get("id"), str)
    }
    if set(actual) != set(expected):
        issues.append("dashboard panel identities changed")
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
        issues.append("dashboard layout identities changed")

    for panel_id, panel in expected.items():
        _expected_id, expected_title, expected_type, _expected_query, expected_layout = panel
        widget = actual[panel_id]
        if widget.get("title") != expected_title or widget.get("panelTypes") != expected_type:
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
        if panel_id != "review-activity" and (
            not isinstance(query, str) or COMMON_FILTER not in query
        ):
            issues.append(f"panel {panel_id} does not use the common filter")
            continue
        if panel_id == "review-activity" and (not isinstance(query, str) or COMMON_FILTER in query):
            issues.append("review activity current snapshot is time filtered")
            continue
        if expected_type in {"bar", "graph"} and (
            " AS ts" not in query or " AS value" not in query
        ):
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

    def canonical_query(panel_id: str) -> str | None:
        query_data = actual[panel_id].get("query")
        if not isinstance(query_data, dict):
            return None
        clickhouse_queries = query_data.get("clickhouse_sql")
        if (
            not isinstance(clickhouse_queries, list)
            or len(clickhouse_queries) != 1
            or not isinstance(clickhouse_queries[0], dict)
        ):
            return None
        query = clickhouse_queries[0].get("query")
        return query if isinstance(query, str) else None

    pipeline_query = canonical_query("pipeline-health")
    if (
        pipeline_query is None
        or PIPELINE_SNAPSHOT_EVENT not in pipeline_query
        or not all(
            field in pipeline_query
            for field in (
                "pipeline.state",
                "scan.terminal_status",
                "pipeline.freshness",
                "logs.query_status",
                "traces.query_status",
                "HAVING count() > 0",
            )
        )
    ):
        issues.append("pipeline health lacks terminal pipeline evidence")
    duration_query = canonical_query("scan-duration")
    if (
        duration_query is None
        or PIPELINE_SNAPSHOT_EVENT not in duration_query
        or "toFloat64(attributes_number['scan.duration_ms']) AS value" not in duration_query
        or "source.lag" in duration_query
        or "rows.processed" in duration_query
    ):
        issues.append("scan duration is not a duration-only numeric series")
    identity_query = canonical_query("project-identity-coverage")
    if identity_query is None or not all(
        field in identity_query
        for field in (
            "identity_coverage_pct",
            "resolved_observations",
            "observed_observations",
            "HAVING count() > 0",
        )
    ):
        issues.append("project identity coverage lacks canonical columns")
    yield_query = canonical_query("detector-signal-yield")
    if yield_query is None or not all(
        field in yield_query
        for field in ("actionable_findings", "all_findings", "actionable_yield_pct")
    ):
        issues.append("detector signal yield lacks canonical columns")
    review_query = canonical_query("review-activity")
    if (
        review_query is None
        or REVIEW_ACTIVITY_SNAPSHOT_EVENT not in review_query
        or CURRENT_REVIEW_ACTIVITY_ORDER not in review_query
        or "HAVING count() > 0" not in review_query
        or not all(
            field in review_query
            for field in (
                "review.activity.availability",
                "review.classification.session_count",
                "review.proposal.session_count",
                "review.classification.result_count",
                "review.proposal.result_count",
                "snapshot.trigger.kind",
                "classification_sessions",
                "proposal_sessions",
                "classification_results",
                "proposal_results",
            )
        )
    ):
        issues.append("review activity does not use its snapshot event")
    return issues


def render_dashboard_json() -> str:
    return json.dumps(build_dashboard(), indent=2, sort_keys=True) + "\n"


def load_dashboard(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("dashboard JSON must contain an object")
    return value
