import json
import struct
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_introspection.database import DatabaseError, backup_database, connect_database
from agent_introspection.pipeline_events import read_immutable_events
from agent_introspection.pipeline_integrity import (
    INTEGRITY_AUDIT_EVENT,
    INTEGRITY_INCIDENT_EVENT,
    IntegrityInvariant,
    capture_remote_integrity,
    inspect_canonical_events,
    require_canonical_integrity,
)
from agent_introspection.pipeline_observations import capture_maintenance
from agent_introspection.pipeline_projection import _integrity_panel, _ledger_panel
from agent_introspection.telemetry import CanonicalActivityVersionEvent, DerivedEvent

NOW = 1_700_000_000_000_000_000


def _wire(event: DerivedEvent | CanonicalActivityVersionEvent) -> dict[str, Any]:
    payload = event.payload()
    return {
        "timestamp": str(event.timestamp_ns),
        "body": json.dumps(payload, separators=(",", ":")),
        "strings": {key: value for key, value in payload.items() if isinstance(value, str)},
        "number_bits": {
            key: str(int.from_bytes(struct.pack(">d", float(value)), "big"))
            for key, value in payload.items()
            if key != "timestamp_ns"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        },
        "booleans": {key: value for key, value in payload.items() if isinstance(value, bool)},
    }


def _replace_payload(row: dict[str, Any], payload: dict[str, Any]) -> None:
    row["body"] = json.dumps(payload, separators=(",", ":"))
    row["strings"] = {key: value for key, value in payload.items() if isinstance(value, str)}
    row["number_bits"] = {
        key: str(int.from_bytes(struct.pack(">d", float(value)), "big"))
        for key, value in payload.items()
        if key != "timestamp_ns" and isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    row["booleans"] = {key: value for key, value in payload.items() if isinstance(value, bool)}


def _set_payload(row: dict[str, Any], key: str, value: Any) -> None:
    payload = json.loads(row["body"])
    payload[key] = value
    _replace_payload(row, payload)


def _remove_payload(row: dict[str, Any], key: str) -> None:
    payload = json.loads(row["body"])
    payload.pop(key)
    _replace_payload(row, payload)


def _derived(
    entity_id: str,
    version: int = 1,
    *,
    attributes: Mapping[str, str | int | float | bool] | None = None,
) -> dict[str, Any]:
    return _wire(
        DerivedEvent(
            "source-session",
            entity_id,
            version,
            1,
            "introspection.source_session.recorded",
            dict(attributes or {}),
            NOW,
        )
    )


def _canonical(activity_id: str, version: int, project_name: str = "one") -> dict[str, Any]:
    return _wire(
        CanonicalActivityVersionEvent(
            activity_id,
            version,
            NOW,
            {
                "activity.producer": "omp",
                "activity.producer_surface": "omp",
                "activity.correlation_id": f"session-{activity_id}",
                "activity.detector.id": "tool_failure",
                "activity.detector.version": 1,
                "activity.normalization.version": 1,
                "activity.attribution.state": "resolved",
                "activity.attribution.method": "session_context_interval",
                "activity.attribution.project_identity_id": "1" * 64,
                "activity.attribution.evidence_id": "2" * 64,
                "agent.project.id": "1" * 64,
                "agent.project.name": project_name,
            },
        )
    )


def _native(entity: str, version: int = 1, *, session: str = "native-1") -> dict[str, Any]:
    return _derived(
        entity,
        version,
        attributes={
            "source.producer": "omp",
            "source.producer_surface": "omp",
            "source.session.id": session,
        },
    )


def test_identical_physical_duplicates_and_legitimate_histories_are_clean() -> None:
    one = _native("source-one")
    two = _native("source-one", 2)
    negative_lag = _derived("negative-lag", attributes={"pipeline.lag_ms": -1.5})
    changed_project = _canonical("second-project", 1, "two")
    _set_payload(changed_project, "agent.project.id", "3" * 64)
    _set_payload(changed_project, "activity.attribution.project_identity_id", "3" * 64)
    _set_payload(changed_project, "activity.correlation_id", "session-activity")
    rows = [
        one,
        one,
        two,
        negative_lag,
        _canonical("activity", 1),
        _canonical("activity", 2),
        changed_project,
    ]

    assert inspect_canonical_events(rows) == []
    require_canonical_integrity(rows)


def test_detects_all_normative_integrity_classes_before_deduplication() -> None:
    empty = _derived("empty")
    _remove_payload(empty, "event.id")
    divergent_one = _derived("divergent")
    divergent_two = _derived("divergent", attributes={"task.count": 2})
    multiple_one = _derived("multiple")
    multiple_two = _derived("multiple")
    _set_payload(multiple_two, "event.id", "other-id")
    rows = [
        empty,
        divergent_one,
        divergent_two,
        multiple_one,
        multiple_two,
        _canonical("gap", 1),
        _canonical("gap", 3),
        _canonical("project-one", 1, "first"),
        _canonical("project-two", 1, "second"),
        _native("tuple", session="session-a"),
        _native("tuple", 2, session="session-b"),
        _derived("negative", attributes={"rows.processed": -1}),
    ]

    assert {incident.invariant for incident in inspect_canonical_events(rows)} == set(
        IntegrityInvariant
    )
    with pytest.raises(ValueError, match="canonical telemetry integrity"):
        require_canonical_integrity(rows)


@pytest.mark.parametrize("value", [2**53, 10**1000, -1, 1.5, float("inf"), True])
def test_impossible_counts_fail_closed_without_range_allocation(value: object) -> None:
    row = _derived("invalid-count", attributes={"logs.count": 0})
    if isinstance(value, int) and value.bit_length() > 63:
        # Corrupt the body without trying to encode an unrepresentable OTLP integer.
        payload = json.loads(row["body"])
        payload["logs.count"] = value
        row["body"] = json.dumps(payload)
    else:
        _set_payload(row, "logs.count", value)
    assert IntegrityInvariant.NEGATIVE_IMPOSSIBLE_COUNT in {
        incident.invariant for incident in inspect_canonical_events([row])
    }


def test_counts_in_wrong_map_and_incomplete_attribution_are_not_canonical() -> None:
    wrong_map = _derived("wrong-map")
    wrong_map["strings"]["database.bytes"] = "0"
    unresolved = _canonical("unresolved", 1)
    _set_payload(unresolved, "activity.attribution.state", "unresolved")
    no_evidence = _canonical("missing-evidence", 1)
    _remove_payload(no_evidence, "activity.attribution.evidence_id")
    found = {
        item.invariant for item in inspect_canonical_events([wrong_map, unresolved, no_evidence])
    }
    assert found == {IntegrityInvariant.EMPTY_IDENTITY}


def test_lossless_envelope_authority_rejects_corrupt_transport_and_boolean_divergence() -> None:
    original_ns = 1_700_000_000_123_456_789
    row = _derived(
        "lossless", attributes={"historical.nanoseconds": original_ns, "signed.zero": -0.0}
    )
    event = read_immutable_events([row], scope="source-session")[0]
    assert event.numbers["historical.nanoseconds"] == original_ns
    assert (
        event.numbers["historical.nanoseconds"]
        != struct.unpack(
            ">d", int(row["number_bits"]["historical.nanoseconds"]).to_bytes(8, "big")
        )[0]
    )

    missing = deepcopy(row)
    missing.pop("body")
    malformed = deepcopy(row)
    malformed["body"] = '{"event.name":"one","event.name":"two"}'
    corrupt_map = deepcopy(row)
    corrupt_map["number_bits"]["historical.nanoseconds"] = str(
        int(corrupt_map["number_bits"]["historical.nanoseconds"]) ^ 1
    )
    corrupt_zero = deepcopy(row)
    corrupt_zero["number_bits"]["signed.zero"] = "0"
    for invalid in (missing, malformed, corrupt_map, corrupt_zero):
        assert {incident.invariant for incident in inspect_canonical_events([invalid])} == {
            IntegrityInvariant.EMPTY_IDENTITY
        }

    for attribute, first_value, second_value in (
        ("transport.boolean", False, True),
        ("scalar.kind", 1, 1.0),
        ("signed.zero", -0.0, 0.0),
    ):
        first, second = deepcopy(row), deepcopy(row)
        _set_payload(first, attribute, first_value)
        _set_payload(second, attribute, second_value)
        assert IntegrityInvariant.SAME_ID_DIVERGENCE in {
            incident.invariant for incident in inspect_canonical_events([first, second])
        }

    incidents = inspect_canonical_events([missing, malformed, corrupt_map, first, second])
    assert {
        IntegrityInvariant.EMPTY_IDENTITY,
        IntegrityInvariant.SAME_ID_DIVERGENCE,
    } <= {incident.invariant for incident in incidents}


class _Client:
    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows = list(rows)

    def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[Mapping[str, Any]]:
        name = parameters.get("event_name")
        if name is None:
            return self.rows
        return [
            row
            for row in self.rows
            if row["strings"]["event.name"] == name
            and int(parameters["start"]) < int(row["timestamp"]) <= int(parameters["end"])
        ]


def _capture(rows: Sequence[Mapping[str, Any]], scan: str = "scan-1") -> list[DerivedEvent]:
    return capture_remote_integrity(
        _Client(rows),
        scan_run_id=scan,
        runtime_identity="runtime-1",
        observed_at_ns=NOW + 1000,
    )


def _snapshot(events: list[DerivedEvent]) -> dict[str, Any]:
    audit = next(event for event in events if event.event_name == INTEGRITY_AUDIT_EVENT)
    return {
        "scan_run_id": audit.attributes["scan.run_id"],
        "runtime_identity": "runtime-1",
        "completed_at_ns": NOW + 2000,
        "integrity.capture_state": "completed",
        "integrity.observed_at_ns": audit.timestamp_ns,
        "integrity.observation_event_id": audit.event_id,
    }


def _panel(events: list[DerivedEvent], snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    panel, _ = _integrity_panel(
        _Client([_wire(event) for event in events]),
        start_ns=NOW,
        end_ns=NOW + 3000,
        snapshots=snapshots,
    )
    return dict(panel)


def test_remote_audit_coalesces_logical_incidents_and_preserves_detection_time() -> None:
    first = _derived("sensitive-native", attributes={"task.count": -1})
    second = deepcopy(first)
    second["timestamp"] = str(NOW + 1)
    _set_payload(second, "timestamp_ns", NOW + 1)
    events = _capture([first, second, first])
    reversed_events = _capture([first, second, first][::-1])
    incidents = [event for event in events if event.event_name == INTEGRITY_INCIDENT_EVENT]
    audit = events[-1]

    assert events == reversed_events
    assert {event.attributes["incident.invariant"] for event in incidents} == {
        "negative_impossible_count",
        "same_id_divergence",
    }
    assert len({event.event_id for event in events}) == len(events)
    assert set(json.loads(str(audit.attributes["audit.incident_event_ids"]))) == {
        event.event_id for event in incidents
    }
    assert audit.attributes["audit.physical_count"] == 3
    assert all(event.timestamp_ns == NOW + 1000 for event in events)
    assert all("incident.event_source_time_ns" not in event.attributes for event in incidents)
    assert "sensitive-native" not in repr(events)
    assert _panel(events, [_snapshot(events)])["metrics"][0]["value"] == 2


def test_complete_empty_incident_population_is_distinct_from_missing_delivery() -> None:
    clean = _capture([_derived("clean")])
    snapshot = _snapshot(clean)
    measured = _panel(clean, [snapshot])
    assert measured["state"] == "Data"
    assert measured["metrics"][0]["value"] == 0
    assert _panel([], [snapshot])["state"] == "Unavailable"

    violated = _capture([_derived("bad", attributes={"rows.processed": -1})], "scan-2")
    only_audit = [event for event in violated if event.event_name == INTEGRITY_AUDIT_EVENT]
    assert _panel(only_audit, [_snapshot(violated)])["state"] == "Unavailable"
    assert _panel(clean, [snapshot, _snapshot(violated)])["state"] == "Unavailable"


def test_audit_identity_and_declared_membership_must_agree() -> None:
    events = _capture([_derived("bad", attributes={"rows.processed": -1})])
    audit = events[-1]
    broken = DerivedEvent(
        audit.scope,
        audit.entity_id,
        1,
        1,
        audit.event_name,
        {**audit.attributes, "audit.incident_count": 0},
        audit.timestamp_ns,
    )
    assert _panel([*events[:-1], broken], [_snapshot(events)])["state"] == "Integrity failure"


def test_failed_remote_audit_never_publishes_a_completed_zero() -> None:
    class FailedClient:
        def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[Mapping[str, Any]]:
            raise ConnectionError("remote unavailable")

    with pytest.raises(ConnectionError):
        capture_remote_integrity(
            FailedClient(),
            scan_run_id="scan",
            runtime_identity="runtime",
            observed_at_ns=NOW,
        )


def test_integrity_audit_rejects_empty_or_corrupt_incident_metadata() -> None:
    events = _capture([_derived("bad", attributes={"rows.processed": -1})])
    incident = next(event for event in events if event.event_name == INTEGRITY_INCIDENT_EVENT)
    audit = events[-1]
    impossible_empty = DerivedEvent(
        audit.scope,
        audit.entity_id,
        audit.entity_version,
        audit.event_sequence,
        audit.event_name,
        {**audit.attributes, "audit.physical_count": 0},
        audit.timestamp_ns,
    )
    mismatched_surface = DerivedEvent(
        incident.scope,
        incident.entity_id,
        incident.entity_version,
        incident.event_sequence,
        incident.event_name,
        {**incident.attributes, "incident.producer": "omp", "incident.surface": "codex-cli"},
        incident.timestamp_ns,
    )

    assert (
        _panel([*events[:-1], impossible_empty], [_snapshot(events)])["state"]
        == "Integrity failure"
    )
    assert _panel([mismatched_surface, audit], [_snapshot(events)])["state"] == "Integrity failure"


def test_integrity_audits_require_current_snapshot_ownership_and_delivery() -> None:
    events = _capture([_derived("clean")])
    snapshot = _snapshot(events)
    latest = {
        key: value
        for key, value in snapshot.items()
        if not key.startswith("integrity.observation_")
    }
    latest.update(
        scan_run_id="scan-latest",
        completed_at_ns=NOW + 2500,
        **{"integrity.capture_state": "unavailable"},
    )

    assert _panel(events, [snapshot, latest])["state"] == "Unavailable"
    assert _panel(events, [])["state"] == "Unavailable"


def test_optional_offending_timestamp_must_be_safe_and_string_encoded() -> None:
    events = _capture([_derived("bad", attributes={"rows.processed": -1})])
    incident = next(event for event in events if event.event_name == INTEGRITY_INCIDENT_EVENT)
    corrupt = DerivedEvent(
        incident.scope,
        incident.entity_id,
        incident.entity_version,
        incident.event_sequence,
        incident.event_name,
        {**incident.attributes, "incident.event_source_time_ns": incident.timestamp_ns},
        incident.timestamp_ns,
    )

    assert _panel([corrupt, events[-1]], [_snapshot(events)])["state"] == "Integrity failure"


def test_maintenance_preserves_observed_failures_and_requires_canonical_scan_ownership(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ledger.sqlite3"
    connection = connect_database(path)
    try:

        def observe() -> DerivedEvent:
            return capture_maintenance(
                connection,
                scan_run_id="maintenance-scan",
                database_path=path,
                runtime_identity="runtime-1",
            )

        def project(
            event: DerivedEvent, *, owner: bool = True, runtime: str = "runtime-1"
        ) -> dict[str, Any]:
            snapshot = {
                "scan_run_id": "maintenance-scan",
                "runtime_identity": runtime,
                "completed_at_ns": event.timestamp_ns + 1,
            }
            panel, _ = _ledger_panel(
                _Client([_wire(event)]),
                end_ns=event.timestamp_ns + 200,
                evaluated_at_ns=event.timestamp_ns + 300,
                snapshots=[snapshot] if owner else [],
            )
            return dict(panel)

        succeeded_event = observe()
        succeeded = project(succeeded_event)
        assert succeeded["state"] == "Data"
        values = {metric["label"]: metric["value"] for metric in succeeded["metrics"]}
        assert values["backup"] == "succeeded"
        assert values["backup verification"] == "ok"
        assert values["backup bytes"] > 0
        unknown_size = project(
            replace(
                succeeded_event,
                attributes={
                    key: value
                    for key, value in succeeded_event.attributes.items()
                    if key != "backup.bytes"
                },
            )
        )
        assert unknown_size["state"] == "Data"
        assert {metric["label"]: metric["value"] for metric in unknown_size["metrics"]}[
            "backup bytes"
        ] is None

        occupied = tmp_path / "occupied"
        occupied.mkdir()
        with pytest.raises(DatabaseError):
            backup_database(connection, occupied, operation="test-occupied-destination")
        failed = project(observe())
        assert failed["state"] == "Data"
        values = {metric["label"]: metric["value"] for metric in failed["metrics"]}
        assert values["backup"] == "failed"
        assert values["backup verification"] == "not_performed"
        assert values["backup bytes"] is None

        connection.execute("PRAGMA journal_mode = DELETE")
        partial_event = observe()
        partial = project(partial_event)
        assert partial["state"] == "Data"
        values = {metric["label"]: metric["value"] for metric in partial["metrics"]}
        assert values["WAL"] is None
        assert values["quick check"] == "ok"
        assert project(partial_event, owner=False)["state"] == "Unavailable"
        assert project(partial_event, runtime="other-runtime")["state"] == "Unavailable"
        assert project(replace(partial_event, entity_id="orphan"))["state"] == "Integrity failure"
        assert project(replace(partial_event, entity_version=2))["state"] == "Integrity failure"
    finally:
        connection.close()
