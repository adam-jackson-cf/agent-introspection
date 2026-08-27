from importlib.resources import files
from typing import Any

from agent_introspection.dashboard import (
    CANONICAL_ACTIVITY_EVENT,
    CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE,
    CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_PREDICATE,
    COMMON_FILTER,
    CONTEXT_ACCEPTED_EVENT,
    CONTEXT_SUPERSEDED_EVENT,
    DASHBOARD_UUID,
    HEALTH_DASHBOARD_UUID,
    INSIGHT_PANELS,
    PIPELINE_SNAPSHOT_EVENT,
    PROJECTION_PANEL_IDS,
    SOURCE_SESSION_EVENT,
    SOURCE_SESSION_LATEST_VERSION_PREDICATE,
    SOURCE_SESSION_PROJECT_ATTRIBUTION_PREDICATE,
    build_dashboard,
    build_health_dashboard,
    render_dashboard_json,
    render_health_dashboard_json,
    verify_dashboard,
    verify_health_dashboard,
)
from agent_introspection.project_schema import AGENT_PROJECT_SCHEMA


def _panels(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {widget["id"]: widget for widget in document["widgets"]}


def test_insight_dashboard_has_stable_identity_and_only_agreed_panels() -> None:
    dashboard = build_dashboard()
    assert dashboard["uuid"] == DASHBOARD_UUID
    assert verify_dashboard(dashboard) == []
    assert dashboard["title"] == "Agent Introspection"

    expected = {
        "activity-coverage": ("Activity coverage", "table", (0, 0, 12, 5)),
        "attribution-diagnostics": ("Attribution diagnostics", "table", (0, 5, 12, 5)),
        "source-session-project-attribution": (
            "Source session project attribution",
            "table",
            (0, 10, 12, 6),
        ),
        "context-to-telemetry-delay": (
            "Context-to-telemetry delay",
            "graph",
            (0, 16, 12, 5),
        ),
        "late-context-reconciliations": (
            "Late-context reconciliations",
            "table",
            (0, 21, 12, 5),
        ),
    }
    assert len(dashboard["widgets"]) == len(INSIGHT_PANELS) == len(expected)
    layouts = {item["i"]: item for item in dashboard["layout"]}
    for widget in dashboard["widgets"]:
        title, panel_type, layout = expected[widget["id"]]
        assert widget["title"] == title
        assert widget["panelTypes"] == panel_type
        assert tuple(layouts[widget["id"]][key] for key in ("x", "y", "w", "h")) == layout
        if widget["id"] == "source-session-project-attribution":
            assert widget["description"] == (
                "Exact source-session project attribution in the selected source-event time range."
            )
        else:
            assert "selected source-event time range" in widget["description"]


def test_health_dashboard_has_stable_identity_and_agreed_operational_panels() -> None:
    dashboard = build_health_dashboard()
    assert dashboard["uuid"] == HEALTH_DASHBOARD_UUID
    assert verify_health_dashboard(dashboard) == []
    expected = {
        "pipeline-health": ("Pipeline health", "table", (0, 0, 12, 4)),
        "recent-scan-runs": ("Recent scan runs", "table", (0, 4, 12, 6)),
    }
    layouts = {item["i"]: item for item in dashboard["layout"]}
    for widget in dashboard["widgets"]:
        title, panel_type, layout = expected[widget["id"]]
        assert (widget["title"], widget["panelTypes"]) == (title, panel_type)
        assert tuple(layouts[widget["id"]][key] for key in ("x", "y", "w", "h")) == layout


def test_insight_queries_select_latest_canonical_activity_versions_in_source_time_bounds() -> None:
    insight = _panels(build_dashboard())
    assert "ts_bucket_start" not in COMMON_FILTER
    assert CANONICAL_ACTIVITY_EVENT in CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE
    assert CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_PREDICATE in (
        CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE
    )
    assert (
        "max(attributes_number['activity.version'])" in CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE
    )
    assert (
        "GROUP BY attributes_string['activity.id']" in CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE
    )

    for panel_id in PROJECTION_PANEL_IDS:
        query = insight[panel_id]["query"]["clickhouse_sql"][0]["query"]
        assert COMMON_FILTER in query
        assert CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE in query
        assert "attributes_string['activity.id']" in query
        assert "attributes_number['activity.version']" in query
        assert "introspection.observation.detected" not in query
        if insight[panel_id]["panelTypes"] == "graph":
            assert " AS ts" in query
            assert " AS value" in query


def test_attribution_diagnostics_cover_canonical_coverage_method_and_reason() -> None:
    panels = _panels(build_dashboard())
    coverage = panels["activity-coverage"]["query"]["clickhouse_sql"][0]["query"]
    diagnostics = panels["attribution-diagnostics"]["query"]["clickhouse_sql"][0]["query"]

    for key in AGENT_PROJECT_SCHEMA.dashboard_attribute_keys.values():
        assert f"attributes_string['{key}']" in coverage
    for metric in ("`Attributed`", "`Unresolved`", "`Eligible`"):
        assert metric in coverage
    assert coverage.count("countIf(") == 4
    assert "activity.attribution.method" in diagnostics
    assert "activity.attribution.reason_code" in diagnostics
    assert "`Attribution method`" in diagnostics
    assert "`Rejection reason`" in diagnostics


def test_source_session_attribution_and_context_diagnostics_use_exact_source_events() -> None:
    panels = _panels(build_dashboard())
    delay = panels["context-to-telemetry-delay"]["query"]["clickhouse_sql"][0]["query"]
    reconciliations = panels["late-context-reconciliations"]["query"]["clickhouse_sql"][0]["query"]

    source_attribution = panels["source-session-project-attribution"]["query"]["clickhouse_sql"][0][
        "query"
    ]
    for column, attribute in (
        ("Producer", "source.producer"),
        ("Session ID", "source.session.id"),
        ("Project ID", "agent.project.id"),
        ("Project name", "agent.project.name"),
        ("Project root", "agent.project.root"),
        ("Project kind", "agent.project.kind"),
    ):
        assert f"attributes_string['{attribute}'] AS `{column}`" in source_attribution
    assert COMMON_FILTER in source_attribution
    assert SOURCE_SESSION_EVENT in source_attribution
    assert SOURCE_SESSION_LATEST_VERSION_PREDICATE in source_attribution
    assert SOURCE_SESSION_PROJECT_ATTRIBUTION_PREDICATE in source_attribution
    assert "attributes_string['source.terminal.outcome'] = 'attributed'" in source_attribution
    for attribute in (
        "agent.project.id",
        "agent.project.name",
        "agent.project.root",
        "agent.project.kind",
    ):
        assert f"notEmpty(attributes_string['{attribute}'])" in source_attribution
    assert "max(attributes_number['entity.version'])" in source_attribution
    assert "GROUP BY attributes_string['entity.id']" in source_attribution

    displayed_columns = (
        "Producer",
        "Session ID",
        "Project ID",
        "Project name",
        "Project root",
        "Project kind",
    )
    projected_rows = (
        ("codex", "session-1", "project-1", "Project", "/repo", "git"),
        ("codex", "session-1", "project-1", "Project", "/repo", "git"),
    )
    assert len({row for row in projected_rows}) == 1
    source_select, _ = source_attribution.split("\nFROM ", maxsplit=1)
    assert source_select.startswith("SELECT DISTINCT\n")
    assert (
        tuple(
            line.split(" AS `", maxsplit=1)[1].removesuffix("`,").removesuffix("`")
            for line in source_select.splitlines()[1:]
        )
        == displayed_columns
    )
    for forbidden in (
        "coalesce(",
        "source.producer_surface",
        "thread.id",
        "thread_id",
        "gen_ai.conversation.id",
        "activity.",
        "context.",
    ):
        assert forbidden not in source_attribution.lower()
    assert "dateDiff(" in delay
    assert " AS ts" in delay
    assert " AS value" in delay
    assert "attributes_number['activity.version'] > 1" in reconciliations
    assert "session_context_interval" in reconciliations


def test_context_coverage_query_keeps_context_history_authoritative_and_fail_closed() -> None:
    panels = _panels(build_dashboard())
    query = panels["context-to-telemetry-delay"]["query"]["clickhouse_sql"][0]["query"]
    activity_cte, context_ctes = query.split("\n), accepted_context_authority AS (", maxsplit=1)

    assert COMMON_FILTER in activity_cte
    assert COMMON_FILTER not in context_ctes
    assert CONTEXT_ACCEPTED_EVENT in context_ctes
    assert CONTEXT_SUPERSEDED_EVENT in context_ctes
    assert context_ctes.count("resource.`service.name`::String = 'agent-introspection'") == 2
    assert "attributes_string['event.scope'] = 'session-context'" in context_ctes
    assert "attributes_string['event.scope'] = 'session-context-supersession'" in context_ctes
    assert "notEmpty(attributes_string['entity.id'])" in context_ctes

    # Foreign-service deliveries never enter either authority CTE.
    assert "accepted_context_authority AS (" in query
    assert "supersession_authority AS (" in query
    # Repeated immutable deliveries collapse to the same grouped context row.
    assert "accepted_context_valid_versions" in context_ctes
    assert "accepted_context_deliveries" in context_ctes
    assert "min(timestamp) AS timestamp" in context_ctes
    assert (
        "GROUP BY\n    entity_id,\n    entity_version,\n    producer,\n    session_id,"
        in context_ctes
    )
    # A different immutable payload for the same entity/version is excluded.
    assert "HAVING uniqExact(tuple(" in context_ctes
    assert ")) = 1" in context_ctes
    assert "supersession_valid_versions" in context_ctes
    assert "supersession_deliveries" in context_ctes
    assert "latest_supersessions" in context_ctes


def test_health_queries_keep_operational_event_semantics() -> None:
    health = _panels(build_health_dashboard())
    for widget in health.values():
        assert COMMON_FILTER in widget["query"]["clickhouse_sql"][0]["query"]
    pipeline = health["pipeline-health"]["query"]["clickhouse_sql"][0]["query"]
    assert PIPELINE_SNAPSHOT_EVENT in pipeline


def test_checked_in_dashboard_assets_are_generated_from_canonical_builders() -> None:
    assets = files("agent_introspection").joinpath("assets")
    assert assets.joinpath("agent-introspection.json").read_text() == render_dashboard_json()
    assert (
        assets.joinpath("agent-introspection-health.json").read_text()
        == render_health_dashboard_json()
    )


def test_dashboard_verifiers_report_identity_presentation_layout_and_query_drift() -> None:
    insight = build_dashboard()
    insight["uuid"] = "changed"
    insight["widgets"][0]["description"] = "Changed"
    insight["layout"][0]["w"] = 1
    insight["widgets"].pop()
    assert "insight dashboard identity changed" in verify_dashboard(insight)
    assert "insight dashboard panel set is incomplete" in verify_dashboard(insight)

    health = build_health_dashboard()
    health["uuid"] = "fabricated"
    assert "health dashboard identity changed" in verify_health_dashboard(health)


def test_dashboard_verifier_rejects_invalid_query_shapes_and_latest_version_selection() -> None:
    dashboard = build_dashboard()
    panels = _panels(dashboard)
    panels["context-to-telemetry-delay"]["query"]["clickhouse_sql"][0]["query"] = panels[
        "context-to-telemetry-delay"
    ]["query"]["clickhouse_sql"][0]["query"].replace(" AS value", " AS activities")
    panels["activity-coverage"]["query"]["clickhouse_sql"][0]["query"] = panels[
        "activity-coverage"
    ]["query"]["clickhouse_sql"][0]["query"].replace(
        CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE, "1 = 1"
    )
    panels["source-session-project-attribution"]["query"]["clickhouse_sql"][0]["query"] = panels[
        "source-session-project-attribution"
    ]["query"]["clickhouse_sql"][0]["query"].replace(
        SOURCE_SESSION_LATEST_VERSION_PREDICATE, "1 = 1"
    )
    issues = verify_dashboard(dashboard)
    assert "visual panel context-to-telemetry-delay lacks ts and value columns" in issues
    assert "projection panel activity-coverage does not select latest activity version" in issues
    assert "source-session panel does not select latest source-session version" in issues


def test_dashboard_verifier_reports_a_malformed_query_definition_without_raising() -> None:
    dashboard = build_health_dashboard()
    _panels(dashboard)["pipeline-health"]["query"]["clickhouse_sql"] = []
    assert "panel pipeline-health has an invalid query definition" in verify_health_dashboard(
        dashboard
    )
