from __future__ import annotations

import hashlib
import json
import struct
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from agent_introspection.pipeline_attribution import query_attribution
from agent_introspection.pipeline_projection import PipelineWindow
from agent_introspection.scan import (
    PipelineStream,
    _pipeline_snapshot_event,
    _PipelineSnapshotRequest,
)
from agent_introspection.telemetry import (
    OPERATIONAL_SCOPE,
    CanonicalActivityVersionEvent,
    DerivedEvent,
)

BASE = 1_700_000_000_000_000_000
START = BASE + 1_000_000_000
END = BASE + 2_000_000_000
PROJECT = "1" * 64


def _at(milliseconds: int) -> int:
    return BASE + milliseconds * 1_000_000


def _iso(timestamp: int) -> str:
    return (
        datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=timestamp // 1000)
    ).isoformat()


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


def _activity(activity_id: str, version: int, state: str, at: int = 1500) -> dict[str, Any]:
    attributes: dict[str, str | int] = {
        "activity.producer": "omp",
        "activity.producer_surface": "omp",
        "activity.correlation_id": "private-correlation",
        "activity.detector.id": "tool_failure",
        "activity.detector.version": 1,
        "activity.normalization.version": 1,
        "activity.attribution.state": state,
        "activity.attribution.method": "session_context_interval",
        "agent.project.id": "unresolved" if state == "unresolved" else PROJECT,
        "agent.project.name": "unresolved" if state == "unresolved" else "Project",
    }
    if state == "unresolved":
        attributes["activity.attribution.reason_code"] = "no_context_interval"
    else:
        attributes["activity.attribution.evidence_id"] = "2" * 64
        attributes["activity.attribution.project_identity_id"] = PROJECT
    event = CanonicalActivityVersionEvent(activity_id, version, _at(at), attributes)
    payload = event.payload()
    row = _wire(event)
    row["source_time_ns"] = str(event.timestamp_ns)
    for column, key in {
        "event_id": "event.id",
        "activity_id": "activity.id",
        "version": "activity.version",
        "schema_version": "activity.payload_schema_version",
        "producer": "activity.producer",
        "surface": "activity.producer_surface",
        "correlation_id": "activity.correlation_id",
        "detector_id": "activity.detector.id",
        "detector_version": "activity.detector.version",
        "normalization_version": "activity.normalization.version",
        "attribution_state": "activity.attribution.state",
        "attribution_method": "activity.attribution.method",
        "evidence_id": "activity.attribution.evidence_id",
        "reason_code": "activity.attribution.reason_code",
        "project_identity_id": "activity.attribution.project_identity_id",
        "project_id": "agent.project.id",
        "project_name": "agent.project.name",
        "event_name": "event.name",
    }.items():
        row[column] = payload.get(key, "")
    return row


def _source(at: int, session: str = "private") -> dict[str, object]:
    return {
        "source_id": f"native-{session}-{at}",
        "source_timestamp_ns": str(_at(at)),
        "service_name": "oh-my-pi",
        "session_ids": [],
        "thread_ids": [],
        "legacy_thread_ids": [],
        "conversation_ids": [],
        "gen_ai_conversation_ids": [session],
    }


def _interval(
    start: int, end: int | None, session: str = "private", version: int = 1
) -> DerivedEvent:
    opening = hashlib.sha256(f"{session}:{start}".encode()).hexdigest()
    started = _iso(_at(start))
    ended = "" if end is None else _iso(_at(end))
    fingerprint = hashlib.sha256(
        "\x1f".join(("omp", session, opening, started, ended, PROJECT, "Project")).encode()
    ).hexdigest()
    attributes: dict[str, str | int] = {
        "interval.payload_schema_version": 2,
        "producer": "omp",
        "producer.surface": "omp",
        "session.id": session,
        "interval.opening_event_id": opening,
        "interval.start_ns": str(_at(start)),
        "interval.started_at": started,
        "interval.version": str(version),
        "interval.fingerprint": fingerprint,
        "interval.state": "open" if end is None else "closed",
        "agent.project.id": PROJECT,
        "agent.project.name": "Project",
    }
    if end is not None:
        attributes.update({"interval.end_ns": str(_at(end)), "interval.ended_at": ended})
    return DerivedEvent(
        OPERATIONAL_SCOPE,
        f"lifecycle:omp:{session}:{opening}",
        version,
        1,
        "introspection.session_context.interval.recorded",
        attributes,
        _at(start),
    )


def _supersession(original: DerivedEvent, replacement: DerivedEvent) -> DerivedEvent:
    original_id = str(original.attributes["interval.opening_event_id"])
    return DerivedEvent(
        OPERATIONAL_SCOPE,
        f"lifecycle-supersession:{original_id}",
        1,
        1,
        "introspection.session_context.superseded",
        {
            "interval.payload_schema_version": 2,
            "source.entity_id": original_id,
            "replacement.event_id": replacement.attributes["interval.opening_event_id"],
            "supersession.version": "1",
            "producer": "omp",
            "producer.surface": "omp",
            "session.id": "private",
            "source.lifecycle_event": "session_start",
            "replacement.lifecycle_event": "session_start",
        },
        END - 1000,
    )


class Client:
    def __init__(
        self,
        activities: list[dict[str, Any]],
        lifecycle: list[DerivedEvent],
        sources: list[dict[str, object]] | None = None,
        *,
        runtime_identity: str = "runtime",
    ) -> None:
        self.activities = activities
        self.sources = sources or []
        self.unavailable: str | None = None
        self.missing: set[str] = set()
        self.queries: list[tuple[str, Mapping[str, str | int]]] = []
        latest: dict[str, DerivedEvent] = {}
        supersessions = []
        for event in lifecycle:
            if event.event_name.endswith("interval.recorded"):
                prior = latest.get(event.entity_id)
                if prior is None or prior.entity_version < event.entity_version:
                    latest[event.entity_id] = event
            else:
                supersessions.append(event.event_id)
        scan_run_id = f"scan-{runtime_identity}"
        self.population = DerivedEvent(
            OPERATIONAL_SCOPE,
            f"{scan_run_id}:lifecycle-population",
            1,
            1,
            "introspection.session_context.population",
            {
                "interval.payload_schema_version": 2,
                "scan.run_id": scan_run_id,
                "scan.runtime_identity": runtime_identity,
                "lifecycle.observed_at_ns": str(END - 1000),
                "lifecycle.interval_count": len(latest),
                "lifecycle.supersession_count": len(supersessions),
                "lifecycle.interval_event_ids": json.dumps(
                    sorted(event.event_id for event in latest.values())
                ),
                "lifecycle.supersession_event_ids": json.dumps(sorted(supersessions)),
            },
            END - 1000,
        )
        self.lifecycle = [*lifecycle, self.population]
        snapshot = _pipeline_snapshot_event(
            _PipelineSnapshotRequest(
                scan_run_id=scan_run_id,
                end_ns=END,
                terminal_status="succeeded",
                error_class=None,
                logs=PipelineStream("available", "no_data"),
                traces=PipelineStream("available", "no_data"),
                hydration=PipelineStream("available", "no_data"),
                finished_ns=END,
                duration_ms=10,
                rows_processed=0,
                logs_count=0,
                traces_count=0,
                context_events_count=0,
                canonical_activities_count=0,
                pending_after_drain=0,
                failed_during_drain=0,
                runtime_identity=runtime_identity,
                schedule_interval_seconds=300,
                schedule_timezone="Europe/London",
            )
        )
        self.snapshot = replace(
            snapshot,
            attributes={
                **snapshot.attributes,
                "scan.deployment_fingerprint": "f" * 64,
                "scan.projection_fingerprint": "f" * 64,
                "lifecycle.capture_state": "completed",
                "lifecycle.observation_event_id": self.population.event_id,
                "lifecycle.observed_at_ns": str(self.population.timestamp_ns),
                "integrity.capture_state": "completed",
                "integrity.observation_event_id": "audit-reference",
                "integrity.observed_at_ns": str(END - 1000),
            },
        )
        self.snapshots = [self.snapshot]

    def query(self, sql: str, parameters: Mapping[str, str | int]) -> list[dict[str, Any]]:
        self.queries.append((sql, parameters))
        if "canonical_activity_integrity" in sql:
            if self.unavailable == "activity":
                raise ConnectionError("activity query unavailable")
            return self.activities
        if "introspection.pipeline.snapshot" in sql:
            return [_wire(snapshot) for snapshot in self.snapshots]
        if "introspection.session_context.population" in sql:
            if self.unavailable == "lifecycle":
                raise ConnectionError("lifecycle query unavailable")
            evaluated_at = parameters["evaluated_at"]
            assert isinstance(evaluated_at, int)
            return [
                _wire(event)
                for event in self.lifecycle
                if event.event_id not in self.missing and event.timestamp_ns <= evaluated_at
            ]
        return self.sources if "signoz_traces" in sql else []


def _report(client: Client) -> dict[str, Any]:
    panels, _ = query_attribution(client, window=PipelineWindow(START, END, END))
    return panels


def _omp_row(panel: dict[str, Any]) -> list[object]:
    return next(row for row in panel["rows"] if row[0] == "omp")


def test_uses_complete_history_and_never_exposes_native_identity() -> None:
    report = _report(
        Client([_activity("activity", 1, "unresolved"), _activity("activity", 2, "resolved")], [])
    )
    coverage = {metric["label"]: metric["value"] for metric in report["p7-coverage"]["metrics"]}
    assert coverage["Eligible activities"] == 1
    assert coverage["Attributed activities"] == 1
    assert report["p9-transitions"]["metrics"][0]["value"] == 1
    assert "private-correlation" not in repr(report)


def test_rejects_malformed_post_cutover_activity_history() -> None:
    client = Client(
        [_activity("activity", 1, "unresolved"), _activity("activity", 2, "resolved", at=1600)],
        [],
    )

    report, _ = query_attribution(
        client,
        window=PipelineWindow(START, END, END),
        measurement_start_ns=_at(1200),
    )

    assert {report[key]["state"] for key in ("p7-coverage", "p8-diagnostics", "p9-evidence")} == {
        "Integrity failure",
    }


def test_p5_and_p6_use_raw_sources_and_directional_interval_cohorts() -> None:
    report = _report(
        Client(
            [],
            [_interval(900, 1300), _interval(1700, None, "lifecycle-only")],
            [_source(1100), _source(1150)],
        )
    )
    assert _omp_row(report["p5-correlation"]) == ["omp", "omp", "interval", 1, 1, 0, 1, 0, 1]
    assert report["p6-delay"]["rows"] == [
        ["codex-cli", "codex-cli", None, None, None, None],
        ["omp", "omp", 1, 200.0, 200.0, 0],
    ]
    assert "private" not in repr(report)


def test_first_source_and_any_source_are_distinct_at_half_open_interval_bounds() -> None:
    report = _report(
        Client(
            [],
            [_interval(1200, 1500)],
            [
                _source(1000),
                _source(1100),
                _source(1200),
                _source(1500),
                _source(2001),
            ],
        )
    )
    assert _omp_row(report["p5-correlation"]) == ["omp", "omp", "interval", 1, 0, 1, 1, 1, 0]
    assert report["p6-delay"]["rows"] == [
        ["codex-cli", "codex-cli", None, None, None, None],
        ["omp", "omp", 0, None, None, 0],
    ]
    at_end = _report(Client([], [_interval(1200, 1500)], [_source(1500)]))
    assert _omp_row(at_end["p5-correlation"])[3:] == [1, 0, 1, 1, 0, 1]
    upper = _report(Client([], [_interval(1200, None)], [_source(2000)]))
    assert _omp_row(upper["p5-correlation"])[3:] == [1, 1, 0, 1, 1, 0]


def test_legitimate_supersession_matches_only_replacement_authority() -> None:
    original, replacement = _interval(900, None), _interval(1050, None)
    edge = _supersession(original, replacement)
    report = _report(Client([], [original, replacement, edge], [_source(1100)]))
    assert _omp_row(report["p5-correlation"])[3:] == [1, 1, 0, 1, 1, 0]
    assert report["p6-delay"]["rows"] == [
        ["codex-cli", "codex-cli", None, None, None, None],
        ["omp", "omp", 1, 50.0, 50.0, 0],
    ]
    missing = _report(Client([], [original, edge], [_source(1100)]))
    assert missing["p5-correlation"]["state"] == "Unavailable"


def test_latest_population_seal_prevents_fallback_to_stale_interval_versions() -> None:
    opened, closed = _interval(900, None), _interval(900, 1050, version=2)
    client = Client([], [opened, closed], [_source(1100)])
    assert _omp_row(_report(client)["p5-correlation"])[3:6] == [1, 0, 1]
    client.missing = {closed.event_id}
    assert _report(client)["p5-correlation"]["state"] == "Unavailable"
    client.missing = {client.population.event_id}
    assert _report(client)["p6-delay"]["state"] == "Unavailable"


def test_lifecycle_requires_each_runtime_latest_population() -> None:
    primary = Client([], [_interval(900, None)], [_source(1100)], runtime_identity="primary")
    secondary = Client([], [_interval(900, None)], runtime_identity="secondary")
    primary.lifecycle.extend(secondary.lifecycle)
    primary.snapshots.extend(secondary.snapshots)

    assert _omp_row(_report(primary)["p5-correlation"])[3:6] == [1, 1, 0]
    _, owners = query_attribution(primary, window=PipelineWindow(START, END, END))
    assert owners == {"scan-primary", "scan-secondary"}
    primary.missing = {secondary.population.event_id}
    panels, owners = query_attribution(primary, window=PipelineWindow(START, END, END))
    assert panels["p5-correlation"]["state"] == "Unavailable"
    assert owners == set()
    assert _report(primary)["p5-correlation"]["state"] == "Unavailable"

    primary.missing = set()
    for change in (
        {"lifecycle.interval_count": 2},
        {
            "lifecycle.interval_event_ids": json.dumps([_interval(900, None).event_id] * 2),
            "lifecycle.interval_count": 2,
        },
        {
            "lifecycle.interval_event_ids": json.dumps(
                [_interval(900, None).event_id, "invalid-uuid"]
            ),
            "lifecycle.interval_count": 2,
        },
    ):
        primary.lifecycle = [
            replace(event, attributes={**secondary.population.attributes, **change})
            if event.event_id == secondary.population.event_id
            else event
            for event in primary.lifecycle
        ]
        panels, owners = query_attribution(primary, window=PipelineWindow(START, END, END))
        assert panels["p5-correlation"]["state"] == "Integrity failure"
        assert panels["p6-delay"]["state"] == "Integrity failure"
        assert owners == set()


def test_future_lifecycle_observations_do_not_influence_past_evaluation() -> None:
    client = Client([], [_interval(900, None)], [_source(1100)])
    client.lifecycle.append(_interval(2500, None, "future"))

    report = _report(client)

    assert report["p5-correlation"]["state"] == "Data"
    assert _omp_row(report["p5-correlation"])[3:6] == [1, 1, 0]


def test_cli_point_context_retains_explicit_unavailable_capability_rows() -> None:
    report = _report(
        Client(
            [],
            [],
            [
                {
                    **_source(1100),
                    "service_name": "codex-cli",
                    "thread_ids": ["cli-session"],
                    "gen_ai_conversation_ids": [],
                }
            ],
        )
    )

    assert report["p5-correlation"]["rows"] == [
        ["codex-cli", "codex-cli", "Not applicable", 1, None, None, None, None, None]
    ]
    assert report["p6-delay"]["rows"] == [["codex-cli", "codex-cli", None, None, None, None]]


def test_retained_overlaps_and_divergent_duplicate_payloads_fail_closed() -> None:
    first = _interval(900, None)
    overlap = _report(Client([], [first, _interval(1200, None)], [_source(1300)]))
    assert overlap["p5-correlation"]["state"] == "Integrity failure"
    divergent = replace(first, attributes={**first.attributes, "agent.project.name": "Changed"})
    duplicate = _report(Client([], [first, divergent], [_source(1300)]))
    assert duplicate["p6-delay"]["state"] == "Integrity failure"


def test_activity_and_lifecycle_query_failures_remain_independent() -> None:
    client = Client([_activity("activity", 1, "resolved")], [_interval(900, None)], [_source(1100)])
    client.unavailable = "activity"
    report = _report(client)
    assert report["p7-coverage"]["state"] == "Query/system error"
    assert report["p5-correlation"]["state"] == "Data"
    client.unavailable = "lifecycle"
    report = _report(client)
    assert report["p7-coverage"]["state"] == "Data"
    assert report["p5-correlation"]["state"] == "Query/system error"


def test_fresh_measurement_makes_wholly_pre_cutover_attribution_unavailable() -> None:
    client = Client([], [_interval(900, None)], [_source(1100)])

    panels, owners = query_attribution(
        client,
        window=PipelineWindow(START, END, END),
        measurement_start_ns=END,
    )

    assert owners == set()
    assert {panel["state"] for panel in panels.values()} == {"Unavailable"}
    assert client.queries == []
