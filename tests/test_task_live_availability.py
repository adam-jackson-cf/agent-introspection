import sqlite3
from datetime import UTC, datetime, timedelta, timezone

from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.task_availability import TaskRouteState
from experiments.dashboard_prototype.task_live_availability import extract

START = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _request() -> LiveProofRequest:
    return LiveProofRequest("task-availability", START, START + timedelta(minutes=1))


def _database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE canonical_activities (
          id TEXT, producer TEXT, producer_surface TEXT, correlation_id TEXT,
          source_started_at_ns INTEGER, source_ended_at_ns INTEGER, detector_id TEXT,
          detector_version INTEGER, normalization_version INTEGER, source_membership_hash TEXT,
          operation_kind TEXT, target_kind TEXT, normalized_target TEXT,
          normalized_failure_class TEXT
        );
        CREATE TABLE canonical_activity_versions (
          activity_id TEXT, version INTEGER, attribution_state TEXT,
          project_identity_id TEXT, attribution_method TEXT
        );
        """
    )
    return connection


def _insert_activity(connection: sqlite3.Connection, **values: str | datetime) -> None:
    activity_id = str(values.get("activity_id", "raw-activity-id"))
    producer = str(values.get("producer", "codex-cli"))
    surface = str(values.get("surface", "codex-cli"))
    detector = str(values.get("detector", "tool_failure"))
    ended_at = values.get("ended_at", START + timedelta(seconds=1))
    assert isinstance(ended_at, datetime)
    connection.execute(
        "INSERT INTO canonical_activities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            activity_id,
            producer,
            surface,
            "raw-native-session-id",
            int(START.timestamp() * 1_000_000_000),
            int(ended_at.timestamp() * 1_000_000_000),
            detector,
            1,
            1,
            "a" * 64,
            "tool",
            "target",
            "normalized",
            "failure",
        ),
    )
    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?, ?)",
        (activity_id, 1, "resolved", "raw-project-identity", "context"),
    )


def _route(evidence, producer: str, measure: str):
    return next(
        row
        for row in evidence.primitives
        if row.dimensions["producer"] == producer and row.dimensions["measure"] == measure
    )


def test_codex_cli_partial_availability_is_blocked_and_privacy_safe() -> None:
    connection = _database()
    _insert_activity(connection)
    evidence = extract(connection, _request())

    assert evidence.proof.result.value == "Blocked"
    assert _route(evidence, "codex-cli", "M5").dimensions["route_state"] == "authoritative"
    assert _route(evidence, "codex-cli", "M2").dimensions["route_state"] == "unavailable"
    assert _route(evidence, "omp", "M5").dimensions["route_state"] == "ambiguous"
    rendered = str(evidence)
    assert "raw-activity-id" not in rendered
    assert "raw-native-session-id" not in rendered
    assert "raw-project-identity" not in rendered


def test_wrong_detector_cannot_open_a_route() -> None:
    connection = _database()
    _insert_activity(connection, detector="tool_loop")
    evidence = extract(connection, _request())

    assert _route(evidence, "codex-cli", "M5").dimensions["route_state"] == "ambiguous"
    assert _route(evidence, "codex-cli", "M9").dimensions["route_state"] == "authoritative"


def test_wrong_surface_and_window_edges_do_not_establish_a_route() -> None:
    connection = _database()
    _insert_activity(connection, activity_id="at-start", ended_at=START)
    _insert_activity(connection, activity_id="wrong-surface", surface="omp")
    _insert_activity(connection, activity_id="at-end", ended_at=START + timedelta(minutes=1))
    offset = timezone(timedelta(hours=5))
    request = LiveProofRequest(
        "task-availability-offset",
        START.astimezone(offset),
        (START + timedelta(minutes=1)).astimezone(offset),
    )
    evidence = extract(connection, request)

    route = _route(evidence, "codex-cli", "M5")
    assert route.dimensions["route_state"] == "authoritative"
    assert route.measures["activity_population"] == 1


def test_project_activity_route_reconciles_latest_resolved_and_unresolved_versions() -> None:
    connection = _database()
    _insert_activity(connection, activity_id="unresolved")
    connection.execute(
        "INSERT INTO canonical_activity_versions VALUES (?, ?, ?, ?, ?)",
        ("unresolved", 2, "unresolved", None, "none"),
    )
    _insert_activity(connection, activity_id="resolved")

    evidence = extract(connection, _request())

    route = _route(evidence, "codex-cli", "M16")
    assert route.dimensions["route_state"] == "authoritative"
    assert route.measures["activity_population"] == 2
    assert route.measures["resolved_count"] == 1
    assert route.measures["unresolved_count"] == 1


def test_missing_required_schema_leaves_routes_unavailable_not_unsupported() -> None:
    evidence = extract(sqlite3.connect(":memory:"), _request())
    assert _route(evidence, "codex-cli", "M5").dimensions["route_state"] == "unavailable"
    assert (
        _route(evidence, "omp", "M3").dimensions["route_state"] == TaskRouteState.UNSUPPORTED.value
    )
