from importlib.resources import files

from agent_introspection.dashboard import (
    COMMON_FILTER,
    DASHBOARD_UUID,
    PANELS,
    build_dashboard,
    render_dashboard_json,
    verify_dashboard,
)


def test_dashboard_has_stable_identity_complete_panel_set_and_common_filter() -> None:
    dashboard = build_dashboard()
    assert dashboard["uuid"] == DASHBOARD_UUID
    assert len(dashboard["widgets"]) == len(PANELS) == 13
    assert verify_dashboard(dashboard) == []
    layout = {item["i"]: item for item in dashboard["layout"]}
    assert {panel for panel, item in layout.items() if item["y"] == 0} == {
        "outbox-backlog",
        "scan-health",
        "sqlite-health",
    }
    assert layout["actionable-trends"]["y"] < layout["observations"]["y"]
    assert layout["observations"]["y"] < layout["pending-proposals"]["y"]
    for widget in dashboard["widgets"]:
        query = widget["query"]["clickhouse_sql"][0]["query"]
        assert COMMON_FILTER in query
        assert "agent-introspection" in query
        assert widget["panelTypes"] in {"bar", "graph", "pie", "table", "value"}
        if widget["panelTypes"] in {"bar", "graph"}:
            assert " AS ts" in query
            assert widget["query"]["clickhouse_sql"][0]["legend"] == ""
        if widget["panelTypes"] == "graph":
            assert " AS value" in query

    panels = {widget["id"]: widget for widget in dashboard["widgets"]}
    performance = panels["scan-performance"]["query"]["clickhouse_sql"][0]["query"]
    assert "toFloat64(attributes_number['scan.duration_ms']) AS value" in performance
    assert "source.lag_ms" not in performance
    assert "rows.processed" not in performance
    health = panels["scan-health"]["query"]["clickhouse_sql"][0]["query"]
    assert all(field in health for field in ("duration_ms", "source_lag_ms", "rows_processed"))


def test_checked_in_dashboard_is_generated_from_canonical_builder() -> None:
    asset = files("agent_introspection").joinpath("assets/agent-introspection.json")
    assert asset.read_text() == render_dashboard_json()


def test_dashboard_verifier_reports_identity_panel_and_filter_drift() -> None:
    dashboard = build_dashboard()
    dashboard["uuid"] = "changed"
    dashboard["widgets"][0]["query"]["clickhouse_sql"][0]["query"] = "SELECT 1"
    dashboard["widgets"].pop()
    issues = verify_dashboard(dashboard)
    assert "dashboard identity changed" in issues
    assert "dashboard panel set is incomplete" in issues


def test_dashboard_verifier_rejects_invalid_graph_and_scan_operations() -> None:
    dashboard = build_dashboard()
    panels = {widget["id"]: widget for widget in dashboard["widgets"]}
    panels["observations"]["query"]["clickhouse_sql"][0]["query"] = panels["observations"]["query"][
        "clickhouse_sql"
    ][0]["query"].replace(" AS value", " AS count")
    panels["scan-health"]["query"]["clickhouse_sql"][0]["query"] = panels["scan-health"]["query"][
        "clickhouse_sql"
    ][0]["query"].replace(" AS rows_processed", " AS rows")
    issues = verify_dashboard(dashboard)
    assert "graph panel observations lacks ts and value columns" in issues
    assert "scan health lacks current operational columns" in issues
