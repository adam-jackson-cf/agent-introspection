from importlib.resources import files

from agent_introspection.dashboard import (
    ACTIVE_GENERATION_MARKER_QUERY,
    ACTIVE_GENERATION_PREDICATE,
    COMMON_FILTER,
    CURRENT_REVIEW_ACTIVITY_ORDER,
    DASHBOARD_UUID,
    PANELS,
    PIPELINE_SNAPSHOT_EVENT,
    PROJECTION_PANEL_IDS,
    REVIEW_ACTIVITY_SNAPSHOT_EVENT,
    build_dashboard,
    render_dashboard_json,
    verify_dashboard,
)


def test_dashboard_has_stable_identity_and_exact_canonical_panel_set() -> None:
    dashboard = build_dashboard()
    assert dashboard["uuid"] == DASHBOARD_UUID
    assert verify_dashboard(dashboard) == []

    expected = {
        "pipeline-health": ("Pipeline health", "table", (0, 0, 6, 3)),
        "scan-duration": ("Scan duration (ms)", "graph", (6, 0, 3, 3)),
        "project-identity-coverage": ("Project identity coverage", "table", (9, 0, 3, 3)),
        "actionable-trends": ("Actionable trends requiring review", "table", (0, 3, 8, 6)),
        "current-trend-context": ("Current trend context", "bar", (8, 3, 4, 6)),
        "observed-signal-mix": ("Observed signal mix by detector", "graph", (0, 9, 6, 4)),
        "detector-signal-yield": ("Detector signal yield", "table", (6, 9, 3, 4)),
        "review-activity": ("Review activity", "table", (9, 9, 3, 4)),
    }
    assert len(dashboard["widgets"]) == len(PANELS) == len(expected)
    layouts = {item["i"]: item for item in dashboard["layout"]}
    for widget in dashboard["widgets"]:
        title, panel_type, layout = expected[widget["id"]]
        assert widget["title"] == title
        assert widget["panelTypes"] == panel_type
        assert tuple(layouts[widget["id"]][key] for key in ("x", "y", "w", "h")) == layout


def test_dashboard_queries_use_their_canonical_metric_and_generation_contracts() -> None:
    dashboard = build_dashboard()
    panels = {widget["id"]: widget for widget in dashboard["widgets"]}
    assert COMMON_FILTER not in ACTIVE_GENERATION_MARKER_QUERY

    for panel_id, widget in panels.items():
        query = widget["query"]["clickhouse_sql"][0]["query"]
        if panel_id == "review-activity":
            assert COMMON_FILTER not in query
        else:
            assert COMMON_FILTER in query
        assert "agent-introspection" in query
        if widget["panelTypes"] in {"bar", "graph"}:
            assert " AS ts" in query
            assert " AS value" in query
            assert widget["query"]["clickhouse_sql"][0]["legend"] == ""
        if panel_id in PROJECTION_PANEL_IDS:
            assert ACTIVE_GENERATION_PREDICATE in query
            if "\nGROUP BY" in query:
                assert query.index(ACTIVE_GENERATION_PREDICATE) < query.index("\nGROUP BY")

    pipeline = panels["pipeline-health"]["query"]["clickhouse_sql"][0]["query"]
    assert PIPELINE_SNAPSHOT_EVENT in pipeline
    assert all(
        field in pipeline
        for field in (
            "pipeline.state",
            "scan.terminal_status",
            "pipeline.freshness",
            "logs.query_status",
            "logs.data_state",
            "traces.query_status",
            "traces.data_state",
            "HAVING count() > 0",
        )
    )
    duration = panels["scan-duration"]["query"]["clickhouse_sql"][0]["query"]
    assert "toFloat64(attributes_number['scan.duration_ms']) AS value" in duration
    assert "source.lag" not in duration
    assert "rows.processed" not in duration
    coverage = panels["project-identity-coverage"]["query"]["clickhouse_sql"][0]["query"]
    assert all(
        field in coverage
        for field in (
            "identity_coverage_pct",
            "resolved_observations",
            "observed_observations",
            "HAVING count() > 0",
        )
    )
    yield_query = panels["detector-signal-yield"]["query"]["clickhouse_sql"][0]["query"]
    assert all(
        field in yield_query
        for field in ("actionable_findings", "all_findings", "actionable_yield_pct")
    )
    review = panels["review-activity"]["query"]["clickhouse_sql"][0]["query"]
    assert REVIEW_ACTIVITY_SNAPSHOT_EVENT in review
    assert CURRENT_REVIEW_ACTIVITY_ORDER in review
    assert "HAVING count() > 0" in review
    assert all(
        field in review
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


def test_checked_in_dashboard_is_generated_from_canonical_builder() -> None:
    asset = files("agent_introspection").joinpath("assets/agent-introspection.json")
    assert asset.read_text() == render_dashboard_json()


def test_dashboard_verifier_reports_identity_panel_presentation_and_layout_drift() -> None:
    dashboard = build_dashboard()
    dashboard["uuid"] = "changed"
    dashboard["widgets"][0]["title"] = "Changed"
    dashboard["layout"][0]["w"] = 1
    dashboard["widgets"].pop()
    issues = verify_dashboard(dashboard)
    assert "dashboard identity changed" in issues
    assert "dashboard panel set is incomplete" in issues


def test_dashboard_verifier_rejects_invalid_query_shapes_and_generation_selection() -> None:
    dashboard = build_dashboard()
    panels = {widget["id"]: widget for widget in dashboard["widgets"]}
    panels["scan-duration"]["query"]["clickhouse_sql"][0]["query"] = panels["scan-duration"][
        "query"
    ]["clickhouse_sql"][0]["query"].replace(" AS value", " AS duration")
    panels["project-identity-coverage"]["query"]["clickhouse_sql"][0]["query"] = panels[
        "project-identity-coverage"
    ]["query"]["clickhouse_sql"][0]["query"].replace(ACTIVE_GENERATION_PREDICATE, "1 = 1")
    panels["pipeline-health"]["query"]["clickhouse_sql"][0]["query"] = panels["pipeline-health"][
        "query"
    ]["clickhouse_sql"][0]["query"].replace("pipeline.state", "pipeline_status")
    issues = verify_dashboard(dashboard)
    assert "visual panel scan-duration lacks ts and value columns" in issues
    assert "scan duration is not a duration-only numeric series" in issues
    assert (
        "projection panel project-identity-coverage does not select the active generation" in issues
    )
    assert "pipeline health lacks terminal pipeline evidence" in issues


def test_dashboard_verifier_reports_a_malformed_query_definition_without_raising() -> None:
    dashboard = build_dashboard()
    pipeline = next(widget for widget in dashboard["widgets"] if widget["id"] == "pipeline-health")
    pipeline["query"]["clickhouse_sql"] = []
    issues = verify_dashboard(dashboard)
    assert "panel pipeline-health has an invalid query definition" in issues
    assert "pipeline health lacks terminal pipeline evidence" in issues
