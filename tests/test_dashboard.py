from importlib.resources import files
from typing import Any

from agent_introspection.dashboard import (
    CANONICAL_ACTIVITY_EVENT,
    CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE,
    CANONICAL_ACTIVITY_PAYLOAD_SCHEMA_PREDICATE,
    COMMON_FILTER,
    DASHBOARD_UUID,
    HEALTH_DASHBOARD_UUID,
    INSIGHT_PANELS,
    PIPELINE_SNAPSHOT_EVENT,
    PROJECTION_PANEL_IDS,
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
        "project-data-attribution": ("Project data attribution", "table", (0, 0, 12, 5)),
        "actionable-trends": ("Actionable trends", "table", (0, 5, 12, 6)),
        "observed-signals-by-detector": ("Observed signals by detector", "graph", (0, 11, 12, 6)),
        "detector-signal-yield": ("Detector signal yield", "table", (0, 17, 12, 5)),
    }
    assert len(dashboard["widgets"]) == len(INSIGHT_PANELS) == len(expected)
    layouts = {item["i"]: item for item in dashboard["layout"]}
    for widget in dashboard["widgets"]:
        title, panel_type, layout = expected[widget["id"]]
        assert widget["title"] == title
        assert widget["panelTypes"] == panel_type
        assert tuple(layouts[widget["id"]][key] for key in ("x", "y", "w", "h")) == layout
        assert "selected display range" in widget["description"]


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


def test_attribution_diagnostics_require_complete_project_pair_and_report_outcomes() -> None:
    attribution = _panels(build_dashboard())["project-data-attribution"]["query"]["clickhouse_sql"][
        0
    ]["query"]
    for key in AGENT_PROJECT_SCHEMA.dashboard_attribute_keys.values():
        assert f"attributes_string['{key}']" in attribution
    assert "`Attribution state`" in attribution
    assert "`Rejection reason`" in attribution
    assert "`Producer surface`" in attribution
    assert "`Resolved activities`" in attribution
    assert "`Unresolved activities`" in attribution
    assert "activity.attribution.reason_code" in attribution
    assert "activity.producer_surface" in attribution


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
    panels["observed-signals-by-detector"]["query"]["clickhouse_sql"][0]["query"] = panels[
        "observed-signals-by-detector"
    ]["query"]["clickhouse_sql"][0]["query"].replace(" AS value", " AS activities")
    panels["project-data-attribution"]["query"]["clickhouse_sql"][0]["query"] = panels[
        "project-data-attribution"
    ]["query"]["clickhouse_sql"][0]["query"].replace(
        CANONICAL_ACTIVITY_LATEST_VERSION_PREDICATE, "1 = 1"
    )
    issues = verify_dashboard(dashboard)
    assert "visual panel observed-signals-by-detector lacks ts and value columns" in issues
    assert (
        "projection panel project-data-attribution does not select latest activity version"
        in issues
    )


def test_dashboard_verifier_reports_a_malformed_query_definition_without_raising() -> None:
    dashboard = build_health_dashboard()
    _panels(dashboard)["pipeline-health"]["query"]["clickhouse_sql"] = []
    assert "panel pipeline-health has an invalid query definition" in verify_health_dashboard(
        dashboard
    )
