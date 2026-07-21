from importlib.resources import files
from typing import Any

from agent_introspection.dashboard import (
    ACTIVE_GENERATION_MARKER_QUERY,
    ACTIVE_GENERATION_PREDICATE,
    COMMON_FILTER,
    DASHBOARD_UUID,
    DETECTOR_LABELS,
    HEALTH_DASHBOARD_UUID,
    HEALTH_PANELS,
    INSIGHT_PANELS,
    PIPELINE_SNAPSHOT_EVENT,
    PROJECTION_PANEL_IDS,
    STATUS_LABELS,
    build_dashboard,
    build_health_dashboard,
    render_dashboard_json,
    render_health_dashboard_json,
    verify_dashboard,
    verify_health_dashboard,
)


def _panels(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {widget["id"]: widget for widget in document["widgets"]}


def test_insight_dashboard_has_stable_identity_and_only_agreed_panels() -> None:
    dashboard = build_dashboard()
    assert dashboard["uuid"] == DASHBOARD_UUID
    assert verify_dashboard(dashboard) == []
    assert dashboard["title"] == "Agent Introspection"
    assert dashboard["description"] == "Observed agent behaviours in the selected display range."

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
        assert "seven-day analysis window" in widget["description"]
        assert "selected display range" in widget["description"]


def test_health_dashboard_has_stable_identity_and_agreed_operational_panels() -> None:
    dashboard = build_health_dashboard()
    assert dashboard["uuid"] == HEALTH_DASHBOARD_UUID
    assert verify_health_dashboard(dashboard) == []
    assert dashboard["title"] == "Agent Introspection Health"

    expected = {
        "pipeline-health": ("Pipeline health", "table", (0, 0, 12, 4)),
        "recent-scan-runs": ("Recent scan runs", "table", (0, 4, 12, 6)),
    }
    assert len(dashboard["widgets"]) == len(HEALTH_PANELS) == len(expected)
    layouts = {item["i"]: item for item in dashboard["layout"]}
    for widget in dashboard["widgets"]:
        title, panel_type, layout = expected[widget["id"]]
        assert widget["title"] == title
        assert widget["panelTypes"] == panel_type
        assert tuple(layouts[widget["id"]][key] for key in ("x", "y", "w", "h")) == layout
        assert widget["description"]


def test_dashboard_queries_use_global_time_active_generation_and_plain_language_labels() -> None:
    insight = _panels(build_dashboard())
    health = _panels(build_health_dashboard())
    assert COMMON_FILTER not in ACTIVE_GENERATION_MARKER_QUERY

    for panel_id, widget in {**insight, **health}.items():
        query = widget["query"]["clickhouse_sql"][0]["query"]
        assert COMMON_FILTER in query
        assert "agent-introspection" in query
        if widget["panelTypes"] == "graph":
            assert " AS ts" in query
            assert " AS value" in query
            assert widget["query"]["clickhouse_sql"][0]["legend"] == ""
        if panel_id in PROJECTION_PANEL_IDS:
            assert ACTIVE_GENERATION_PREDICATE in query
            if "\nGROUP BY" in query:
                assert query.index(ACTIVE_GENERATION_PREDICATE) < query.index("\nGROUP BY")

    pipeline = health["pipeline-health"]["query"]["clickhouse_sql"][0]["query"]
    assert PIPELINE_SNAPSHOT_EVENT in pipeline
    assert all(
        label in pipeline
        for label in ("`Pipeline state`", "`Last completed scan`", "`Last scan duration`")
    )
    assert "terminal_status" not in pipeline
    assert "freshness" not in pipeline
    assert "logs.query_status" not in pipeline
    assert "traces.query_status" not in pipeline

    recent_scans = health["recent-scan-runs"]["query"]["clickhouse_sql"][0]["query"]
    assert all(
        label in recent_scans
        for label in ("`Started at`", "`Duration`", "`Outcome`", "`Rows processed`", "LIMIT 24")
    )
    assert "ORDER BY timestamp DESC" in recent_scans

    attribution = insight["project-data-attribution"]["query"]["clickhouse_sql"][0]["query"]
    assert all(
        label in attribution
        for label in (
            "`Project attribution coverage`",
            "`Attributed observations`",
            "`All observations`",
        )
    )
    assert "HAVING count() > 0" in attribution
    assert "identity_coverage_pct" not in attribution

    actionable = insight["actionable-trends"]["query"]["clickhouse_sql"][0]["query"]
    assert all(
        label in actionable
        for label in (
            "`Finding`",
            "`Category`",
            "`Detector`",
            "`Project`",
            "`Occurrences`",
            "`Last evaluated`",
        )
    )
    for raw, label in DETECTOR_LABELS.items():
        assert raw in actionable
        assert label in actionable

    observed = insight["observed-signals-by-detector"]["query"]["clickhouse_sql"][0]["query"]
    for raw, label in DETECTOR_LABELS.items():
        assert raw in observed
        assert label in observed

    for raw, label in STATUS_LABELS.items():
        assert raw in pipeline or raw in recent_scans
        assert label in pipeline or label in recent_scans


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
    issues = verify_dashboard(insight)
    assert "insight dashboard identity changed" in issues
    assert "insight dashboard panel set is incomplete" in issues

    health = build_health_dashboard()
    health["uuid"] = "fabricated"
    issues = verify_health_dashboard(health)
    assert "health dashboard identity changed" in issues


def test_dashboard_verifier_rejects_invalid_query_shapes_and_generation_selection() -> None:
    dashboard = build_dashboard()
    panels = _panels(dashboard)
    panels["observed-signals-by-detector"]["query"]["clickhouse_sql"][0]["query"] = panels[
        "observed-signals-by-detector"
    ]["query"]["clickhouse_sql"][0]["query"].replace(" AS value", " AS observations")
    panels["project-data-attribution"]["query"]["clickhouse_sql"][0]["query"] = panels[
        "project-data-attribution"
    ]["query"]["clickhouse_sql"][0]["query"].replace(ACTIVE_GENERATION_PREDICATE, "1 = 1")
    issues = verify_dashboard(dashboard)
    assert "visual panel observed-signals-by-detector lacks ts and value columns" in issues
    assert (
        "projection panel project-data-attribution does not select the active generation" in issues
    )


def test_dashboard_verifier_reports_a_malformed_query_definition_without_raising() -> None:
    dashboard = build_health_dashboard()
    pipeline = _panels(dashboard)["pipeline-health"]
    pipeline["query"]["clickhouse_sql"] = []
    issues = verify_health_dashboard(dashboard)
    assert "panel pipeline-health has an invalid query definition" in issues
