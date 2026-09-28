"""Fail-closed remote projection of immutable Pipeline observations."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from agent_introspection.pipeline_contracts import (
    PipelineDeployment,
    PipelineMetric,
    PipelinePanel,
    PipelinePoint,
    PipelineSeries,
    PipelineState,
    Scalar,
)
from agent_introspection.pipeline_delivery import query_delivery_detail
from agent_introspection.pipeline_events import (
    ImmutableEvent,
    read_event_ids,
    read_immutable_events,
)
from agent_introspection.pipeline_integrity import (
    INTEGRITY_AUDIT_EVENT,
    INTEGRITY_INCIDENT_EVENT,
    IntegrityInvariant,
)
from agent_introspection.pipeline_observations import SOURCE_LAG_COHORTS
from agent_introspection.telemetry import (
    OPERATIONAL_SCOPE,
    EventQueryClient,
)

SnapshotCache = dict[tuple[int, int, int], list[dict[str, Any]]]


_INCIDENT_ORIGINS = frozenset(
    {
        "unknown",
        "introspection.activity.version.recorded",
        "introspection.source_session.recorded",
        "introspection.session_context.interval.recorded",
        "introspection.session_context.superseded",
        "introspection.session_context.population",
        "introspection.pipeline.snapshot",
        "introspection.pipeline.source_lag",
        "introspection.pipeline.ledger_maintenance",
        "introspection.pipeline.delivery.final_drain",
        "introspection.pipeline.delivery.event",
        "introspection.pipeline.delivery.attempt",
    }
)

SNAPSHOT_EVENT = "introspection.pipeline.snapshot"
_SCHEMA = 2
_SNAPSHOT_PANELS = (
    "p1-snapshot",
    "p2-outcomes",
    "p2-freshness",
    "p3-duration",
    "p3-rows",
    "p3-throughput",
    "p4-source-lag",
    "p10-snapshot",
    "p10-delivery-detail",
    "scan-evidence",
)
_SNAPSHOT_QUERY = """
WITH candidates AS (
    SELECT DISTINCT attributes_string['entity.id'] AS scan_id
    FROM agent_introspection.events
    WHERE attributes_string['event.name'] = 'introspection.pipeline.snapshot'
      AND ((toUInt64OrNull(attributes_string['scan.completed_at_ns']) > {start:UInt64}
        AND toUInt64OrNull(attributes_string['scan.completed_at_ns']) <= {end:UInt64})
        OR (timestamp > {start:UInt64} AND timestamp <= {end:UInt64}))
)
SELECT timestamp, body, attributes_string AS strings,
       mapApply((key, value) -> (key, toString(reinterpretAsUInt64(value))),
                attributes_number) AS number_bits,
       attributes_bool AS booleans
FROM agent_introspection.events
WHERE attributes_string['event.name'] = 'introspection.pipeline.snapshot'
  AND attributes_string['entity.id'] IN candidates
"""
_OBSERVATION_QUERY = """WITH candidates AS (
    SELECT DISTINCT attributes_string['entity.id'] AS entity_id
    FROM agent_introspection.events
    WHERE attributes_string['event.name'] = {event_name:String}
      AND timestamp > {start:UInt64} AND timestamp <= {end:UInt64}
)
SELECT timestamp, body, attributes_string AS strings,
       mapApply((key, value) -> (key, toString(reinterpretAsUInt64(value))),
                attributes_number) AS number_bits, attributes_bool AS booleans
FROM agent_introspection.events
WHERE attributes_string['event.name'] = {event_name:String}
  AND attributes_string['entity.id'] IN candidates"""
_MAINTENANCE_QUERY = """WITH candidates AS (
    SELECT DISTINCT attributes_string['entity.id'] AS entity_id
    FROM agent_introspection.events
    WHERE attributes_string['event.name'] = 'introspection.pipeline.ledger_maintenance'
      AND timestamp > {start:UInt64} AND timestamp <= {upper:UInt64}
)
SELECT timestamp, body, attributes_string AS strings,
       mapApply((key, value) -> (key, toString(reinterpretAsUInt64(value))),
                attributes_number) AS number_bits, attributes_bool AS booleans
FROM agent_introspection.events
WHERE attributes_string['event.name'] = 'introspection.pipeline.ledger_maintenance'
  AND attributes_string['entity.id'] IN candidates"""
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class _QueryError(RuntimeError):
    """The remote request failed before immutable payload validation."""


class _IncompleteProjectionError(ValueError):
    """A required immutable observation population has not arrived."""


def _panel(
    state: PipelineState,
    population: str,
    *,
    metrics: list[PipelineMetric] | None = None,
    table: tuple[list[str], list[list[Scalar]]] | None = None,
    reasons: list[str] | None = None,
) -> PipelinePanel:
    return {
        "state": state,
        "population": population,
        "timeBasis": "scan completion time",
        "rangeOperator": "start < timestamp <= end",
        "metrics": metrics or [],
        "columns": table[0] if table else [],
        "rows": table[1] if table else [],
        "series": [],
        "reasons": reasons or [],
        "provenance": {"event": SNAPSHOT_EVENT, "schema": str(_SCHEMA)},
    }


def _metric(
    label: str,
    value: Scalar,
    unit: str,
    ratio: tuple[int, int] | None = None,
    sample_count: int | None = None,
) -> PipelineMetric:
    return {
        "label": label,
        "value": value,
        "unit": unit,
        "numerator": ratio[0] if ratio else None,
        "denominator": ratio[1] if ratio else None,
        "sampleCount": sample_count,
    }


def _percentile(values: list[float | int], fraction: float) -> float | int | None:
    if not values:
        return None
    return sorted(values)[math.ceil(fraction * len(values)) - 1]


def _percentiles(values: list[float | int], unit: str) -> list[PipelineMetric]:
    return (
        [
            _metric(label, _percentile(values, fraction), unit, sample_count=len(values))
            for label, fraction in (("p50", 0.5), ("p95", 0.95))
        ]
        if values
        else []
    )


def _utc(timestamp_ns: int) -> str:
    seconds, nanos = divmod(timestamp_ns, 1_000_000_000)
    return (
        datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%S")
        + f".{nanos // 1_000_000:03d}Z"
    )


def _query(
    client: EventQueryClient, sql: str, parameters: Mapping[str, str | int]
) -> list[ImmutableEvent]:
    try:
        rows = list(client.query(sql, parameters))
    except Exception as exc:
        raise _QueryError("remote Pipeline query failed") from exc
    events = read_immutable_events(rows, scope=OPERATIONAL_SCOPE)
    for event in events:
        if event.count("pipeline.payload_schema_version") != _SCHEMA:
            raise ValueError("unsupported immutable Pipeline payload schema")
        if event.count("entity.version") != 1 or event.count("event.sequence") != 1:
            raise ValueError("invalid immutable Pipeline observation version")
    return events


def _snapshot_drain(event: ImmutableEvent) -> dict[str, Any]:
    state = event.text("outbox.drain_state")
    fields = {
        "pending_after_drain": "outbox.pending_after_drain",
        "failed_during_drain": "outbox.failed_during_drain",
    }
    if state == "completed":
        return {"drain_state": state, **{name: event.count(key) for name, key in fields.items()}}
    if (
        state != "unavailable"
        or event.text("scan.terminal_status") != "failed"
        or any(key in event.numbers or key in event.strings for key in fields.values())
    ):
        raise ValueError("invalid final-drain capture state")
    return {"drain_state": state, **dict.fromkeys(fields)}


def _snapshot_population(
    event: ImmutableEvent, *, count_key: str, state_key: str, available: bool
) -> int | None:
    count_present = count_key in event.numbers
    if count_key in event.strings:
        raise ValueError("snapshot population count is in the wrong attribute map")
    state = event.text(state_key)
    if available:
        if state not in {"records", "no_data"} or not count_present:
            raise ValueError("observed snapshot population is incomplete")
        count = event.count(count_key)
        if (state == "records") != bool(count):
            raise ValueError("snapshot population state disagrees with its count")
        return count
    if state != "unknown" or count_present:
        raise ValueError("unobserved snapshot population declares a count")
    return None


def _snapshot(event: ImmutableEvent) -> dict[str, Any]:
    status = event.text("scan.terminal_status")
    logs_available = event.text("logs.query_status") == "available"
    traces_available = event.text("traces.query_status") == "available"
    hydration_available = event.text("hydration.query_status") == "available"
    if (
        event.text("logs.query_status") not in {"available", "unknown"}
        or event.text("traces.query_status") not in {"available", "unknown"}
        or event.text("hydration.query_status") not in {"available", "unknown"}
        or (
            hydration_available and event.text("hydration.data_state") not in {"records", "no_data"}
        )
        or (not hydration_available and event.text("hydration.data_state") != "unknown")
    ):
        raise ValueError("invalid source acquisition state")
    if "pipeline.error_class" in event.numbers:
        raise ValueError("snapshot error class is in the wrong attribute map")
    row: dict[str, Any] = {
        "logs_count": _snapshot_population(
            event,
            count_key="logs.count",
            state_key="logs.data_state",
            available=logs_available,
        ),
        "traces_count": _snapshot_population(
            event,
            count_key="traces.count",
            state_key="traces.data_state",
            available=traces_available,
        ),
        "context_events_count": _snapshot_population(
            event,
            count_key="context.events_count",
            state_key="context.data_state",
            available=status != "failed" or event.text("context.data_state") != "unknown",
        ),
        "canonical_activities_count": _snapshot_population(
            event,
            count_key="canonical.activities_count",
            state_key="canonical.activities_data_state",
            available=(
                status != "failed" or event.text("canonical.activities_data_state") != "unknown"
            ),
        ),
        "schedule_interval_seconds": event.count("scan.schedule_interval_seconds"),
    }
    row["rows_processed"] = _snapshot_population(
        event,
        count_key="rows.processed",
        state_key="rows.data_state",
        available=logs_available and traces_available,
    )
    row.update(_snapshot_drain(event))
    row.update(
        {
            "scan_run_id": event.text("entity.id"),
            "event_id": event.text("event.id"),
            "completed_at_ns": event.nanoseconds("scan.completed_at_ns"),
            "extraction_bound_ns": event.nanoseconds("scan.extraction_bound_ns"),
            "duration_ms": event.number("scan.duration_ms"),
            "terminal_status": status,
            "runtime_identity": event.text("scan.runtime_identity"),
            "schedule_timezone": event.text("scan.schedule_timezone"),
            "error_class": event.strings.get("pipeline.error_class"),
        }
    )
    ZoneInfo(row["schedule_timezone"])
    if (
        event.text("event.name") != SNAPSHOT_EVENT
        or row["completed_at_ns"] != event.timestamp_ns
        or row["completed_at_ns"] < row["extraction_bound_ns"]
        or row["duration_ms"] < 0
        or row["schedule_interval_seconds"] < 1
        or row["terminal_status"] not in {"succeeded", "failed", "no_data"}
        or (
            row["rows_processed"] is not None
            and row["rows_processed"] != row["logs_count"] + row["traces_count"]
        )
    ):
        raise ValueError("invalid canonical snapshot time, outcome or population")
    if status != "failed" and (
        not logs_available
        or not traces_available
        or not hydration_available
        or any(
            row[key] is None
            for key in (
                "rows_processed",
                "logs_count",
                "traces_count",
                "context_events_count",
                "canonical_activities_count",
            )
        )
    ):
        raise ValueError("successful scan has an unavailable population")
    if status == "failed":
        event.text("pipeline.error_class")
    elif row["error_class"] is not None:
        raise ValueError("non-failed scan declares a failure class")
    row.update(_snapshot_observations(event))
    for key in ("scan.deployment_fingerprint", "scan.projection_fingerprint"):
        value = event.text(key)
        if _SHA256.fullmatch(value) is None:
            raise ValueError("invalid scanner implementation fingerprint")
        row[key] = value
    return row


def _snapshot_observations(event: ImmutableEvent) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for prefix in ("lifecycle", "integrity"):
        state = event.text(f"{prefix}.capture_state")
        fields = (f"{prefix}.observation_event_id", f"{prefix}.observed_at_ns")
        result[f"{prefix}.capture_state"] = state
        if state == "completed":
            result[fields[0]] = event.text(fields[0])
            result[fields[1]] = event.nanoseconds(fields[1])
            if result[fields[1]] > event.timestamp_ns:
                raise ValueError("observation follows its terminal scan")
        elif (
            state != "unavailable"
            or event.text("scan.terminal_status") != "failed"
            or any(key in event.strings or key in event.numbers for key in fields)
        ):
            raise ValueError("invalid scan observation capture state")
    return result


def query_pipeline_snapshots(
    client: EventQueryClient,
    *,
    start_ns: int,
    end_ns: int,
    snapshot_cache: SnapshotCache | None = None,
) -> list[dict[str, Any]]:
    """Validate complete immutable histories before completion-range selection."""
    if start_ns >= end_ns:
        raise ValueError("start_ns must be before end_ns")
    cache_key = (id(client), start_ns, end_ns)
    if snapshot_cache is not None and (snapshots := snapshot_cache.get(cache_key)) is not None:
        return snapshots
    events = _query(client, _SNAPSHOT_QUERY, {"start": start_ns, "end": end_ns})
    snapshots = [_snapshot(event) for event in events]
    if len({row["scan_run_id"] for row in snapshots}) != len(snapshots):
        raise ValueError("scan has multiple immutable snapshot identities")
    snapshots = sorted(
        (row for row in snapshots if start_ns < row["completed_at_ns"] <= end_ns),
        key=lambda row: (row["completed_at_ns"], row["event_id"]),
    )
    if snapshot_cache is not None:
        snapshot_cache[cache_key] = snapshots
    return snapshots


def _freshness(rows: list[dict[str, Any]], evaluated_at_ns: int) -> PipelinePanel:
    population = "latest successful schema-2 scan"
    successful = [row for row in rows if row["terminal_status"] == "succeeded"]
    if not successful:
        return _panel("No data", population)
    latest = successful[-1]
    age_ns = evaluated_at_ns - latest["completed_at_ns"]
    if age_ns < 0:
        return _panel(
            "Integrity failure", population, reasons=["scan completion is after evaluation time"]
        )
    policies = {
        (
            row["runtime_identity"],
            row["schedule_interval_seconds"],
            row["schedule_timezone"],
        )
        for row in rows
    }
    metrics = [_metric("scan age", age_ns / 1_000_000, "ms")]
    if len(policies) == 1:
        cadence_ns = latest["schedule_interval_seconds"] * 1_000_000_000
        metrics.append(_metric("missed cadence", max(0, age_ns // cadence_ns - 1), "scans"))
        return _panel("Data", population, metrics=metrics)
    metrics.append(_metric("missed cadence", None, "scans"))
    return _panel(
        "Unavailable",
        population,
        metrics=metrics,
        reasons=["a single fixed cadence is not configured for the selected scanner identity"],
    )


def _bucketed_series(
    rows: list[dict[str, Any]],
    *,
    name: str,
    unit: str,
    value: Any,
    maximum_buckets: int = 50,
) -> list[PipelineSeries]:
    """Return bounded chronological means without inventing values for empty buckets."""
    eligible = [(row, value(row)) for row in rows]
    eligible = [(row, measurement) for row, measurement in eligible if measurement is not None]
    if not eligible:
        return []
    bucket_size = math.ceil(len(eligible) / maximum_buckets)
    points: list[PipelinePoint] = []
    for offset in range(0, len(eligible), bucket_size):
        bucket = eligible[offset : offset + bucket_size]
        points.append(
            {
                "at": _utc(bucket[-1][0]["completed_at_ns"]),
                "value": sum(measurement for _, measurement in bucket) / len(bucket),
            }
        )
    return [{"name": name, "unit": unit, "points": points}]


def _bucketed_table(
    rows: list[dict[str, Any]], *, value: Any, maximum_buckets: int = 50
) -> list[list[Scalar]]:
    """Return bounded chronological table rows for a second incompatible unit."""
    eligible = [(row, value(row)) for row in rows]
    eligible = [(row, measurement) for row, measurement in eligible if measurement is not None]
    bucket_size = math.ceil(len(eligible) / maximum_buckets) if eligible else 1
    return [
        [
            _utc(bucket[-1][0]["completed_at_ns"]),
            sum(measurement for _, measurement in bucket) / len(bucket),
        ]
        for offset in range(0, len(eligible), bucket_size)
        for bucket in [eligible[offset : offset + bucket_size]]
    ]


def _scan_panels(rows: list[dict[str, Any]], evaluated_at_ns: int) -> dict[str, PipelinePanel]:
    latest = rows[-1]
    successful = [row for row in rows if row["terminal_status"] == "succeeded"]
    outcomes = Counter(row["terminal_status"] for row in rows)
    durations = [row["duration_ms"] for row in successful]
    processed = [row["rows_processed"] for row in successful]
    throughput = [
        1000 * row["rows_processed"] / row["duration_ms"]
        for row in successful
        if row["duration_ms"] > 0
    ]
    cost = [
        1000 * row["duration_ms"] / row["rows_processed"]
        for row in successful
        if row["rows_processed"] > 0
    ]
    panels = {
        "p1-snapshot": _panel(
            "Data",
            "latest canonical schema-2 scan snapshot",
            metrics=[
                _metric("terminal status", latest["terminal_status"], "state"),
                _metric("completion", _utc(latest["completed_at_ns"]), "UTC"),
                _metric("duration", latest["duration_ms"], "ms"),
                _metric("error class", latest["error_class"], "state"),
                *[
                    _metric(label, latest[field], unit)
                    for label, field, unit in (
                        ("rows", "rows_processed", "rows"),
                        ("logs", "logs_count", "events"),
                        ("traces", "traces_count", "events"),
                        ("context events", "context_events_count", "events"),
                        ("canonical activities", "canonical_activities_count", "events"),
                        ("pending", "pending_after_drain", "events"),
                        ("failed during drain", "failed_during_drain", "events"),
                    )
                ],
            ],
        ),
        "p2-outcomes": _panel(
            "Data",
            "complete canonical schema-2 scan snapshots",
            metrics=[
                *[
                    _metric(
                        label,
                        100 * outcomes[status] / len(rows),
                        "percent",
                        (outcomes[status], len(rows)),
                    )
                    for label, status in (("success", "succeeded"), ("failure", "failed"))
                ],
                _metric("no data", outcomes["no_data"], "scans"),
            ],
            table=(
                ["runtime", "status", "error class", "scans"],
                [
                    [*key, count]
                    for key, count in sorted(
                        Counter(
                            (row["runtime_identity"], row["terminal_status"], row["error_class"])
                            for row in rows
                        ).items()
                    )
                ],
            ),
        ),
        "p2-freshness": _freshness(rows, evaluated_at_ns),
        "p3-duration": _panel(
            "Data" if durations else "No data",
            "successful schema-2 scans",
            metrics=_percentiles(durations, "ms"),
        ),
        "p3-rows": _panel(
            "Data" if processed else "No data",
            "successful schema-2 scans",
            metrics=_percentiles(processed, "rows"),
        ),
        "p3-throughput": _panel(
            "Data" if throughput or cost else "No data",
            "successful scans with positive duration or row count",
            metrics=[
                *_percentiles(throughput, "rows/s"),
                *_percentiles(cost, "ms/1,000 rows"),
            ],
            table=(
                ["completedAt", "bucketed mean milliseconds per 1,000 rows"],
                _bucketed_table(
                    successful,
                    value=lambda row: (
                        1000 * row["duration_ms"] / row["rows_processed"]
                        if row["rows_processed"] > 0
                        else None
                    ),
                ),
            ),
        ),
        "p10-snapshot": _panel(
            "Data" if latest["drain_state"] == "completed" else "Unavailable",
            "latest canonical schema-2 scan snapshot",
            metrics=[
                _metric("pending", latest["pending_after_drain"], "events"),
                _metric("failed during drain", latest["failed_during_drain"], "events"),
            ],
            reasons=(
                []
                if latest["drain_state"] == "completed"
                else ["final drain could not be observed"]
            ),
        ),
        "scan-evidence": _panel(
            "Data",
            "latest 50 canonical schema-2 scan snapshots",
            table=(
                ["scan", "status", "completedAtNs", "rows", "runtime"],
                [
                    [
                        row["scan_run_id"],
                        row["terminal_status"],
                        str(row["completed_at_ns"]),
                        row["rows_processed"],
                        row["runtime_identity"],
                    ]
                    for row in rows[-50:]
                ],
            ),
        ),
    }
    panels["p3-duration"]["series"] = _bucketed_series(
        successful,
        name="bucketed mean scan duration",
        unit="ms",
        value=lambda row: row["duration_ms"],
    )
    panels["p3-rows"]["series"] = _bucketed_series(
        successful,
        name="bucketed mean rows processed",
        unit="rows",
        value=lambda row: row["rows_processed"],
    )
    panels["p3-throughput"]["series"] = _bucketed_series(
        successful,
        name="bucketed mean rows per second",
        unit="rows/s",
        value=lambda row: (
            1000 * row["rows_processed"] / row["duration_ms"] if row["duration_ms"] > 0 else None
        ),
    )
    return panels


def _failed_panel(error: Exception, population: str) -> PipelinePanel:
    if isinstance(error, _QueryError):
        state: PipelineState = "Query/system error"
        reason = "remote Pipeline query failed"
    elif isinstance(error, _IncompleteProjectionError):
        state, reason = "Unavailable", "required immutable observation population is incomplete"
    else:
        state, reason = "Integrity failure", "immutable Pipeline observation validation failed"
    return _panel(state, population, reasons=[reason])


@dataclass(frozen=True, slots=True)
class PipelineWindow:
    """Requested display interval and immutable observation evaluation bound."""

    start_ns: int
    end_ns: int
    evaluated_at_ns: int


def project_pipeline(
    client: EventQueryClient,
    *,
    window: PipelineWindow,
    attribution_scan_ids: set[str],
    measurement_start_ns: int | None = None,
    snapshot_cache: SnapshotCache | None = None,
) -> tuple[dict[str, PipelinePanel], list[PipelineDeployment]]:
    start_ns = window.start_ns
    end_ns = window.end_ns
    evaluated_at_ns = window.evaluated_at_ns
    if measurement_start_ns is not None and (
        isinstance(measurement_start_ns, bool)
        or not isinstance(measurement_start_ns, int)
        or not 0 < measurement_start_ns < 2**64
    ):
        raise ValueError("measurement_start_ns must be a positive integer nanosecond timestamp")
    measurement_start = measurement_start_ns or 0
    if end_ns <= measurement_start:
        unavailable = {
            key: _panel(
                "Unavailable",
                "fresh measurement cohort",
                reasons=["requested range precedes the fresh measurement start"],
            )
            for key in _SNAPSHOT_PANELS
        }
        unavailable["p11-integrity"] = _panel(
            "Unavailable",
            "fresh measurement cohort",
            reasons=["requested range precedes the fresh measurement start"],
        )
        unavailable["p12-ledger"] = _panel(
            "Unavailable",
            "fresh measurement cohort",
            reasons=["requested range precedes the fresh measurement start"],
        )
        return unavailable, []
    contributing_start = max(start_ns, measurement_start)
    deployments: list[PipelineDeployment] = []
    all_snapshots: list[dict[str, Any]] | None = None
    selected_scan_ids: set[str] = set()
    try:
        all_snapshots = query_pipeline_snapshots(
            client,
            start_ns=measurement_start,
            end_ns=evaluated_at_ns,
            snapshot_cache=snapshot_cache,
        )
        snapshots = [row for row in all_snapshots if start_ns < row["completed_at_ns"] <= end_ns]
        selected_scan_ids = {row["scan_run_id"] for row in snapshots}
    except Exception as exc:
        panels = {
            key: _failed_panel(exc, "canonical schema-2 scan snapshots") for key in _SNAPSHOT_PANELS
        }
    else:
        if snapshots:
            panels = _scan_panels(snapshots, evaluated_at_ns)
            latest = snapshots[-1]
            panels["p10-delivery-detail"] = (
                query_delivery_detail(
                    client,
                    scan_run_id=latest["scan_run_id"],
                    completed_at_ns=latest["completed_at_ns"],
                )
                if latest["drain_state"] == "completed"
                else _panel(
                    "Unavailable",
                    "selected snapshot final drain",
                    reasons=["final drain could not be observed"],
                )
            )
            panels["p4-source-lag"] = _source_lag_panel(client, snapshots=snapshots)
        else:
            panels = {
                key: _panel(
                    "Unavailable",
                    "canonical schema-2 scan snapshots",
                    reasons=["no schema-2 snapshots in selected range"],
                )
                for key in _SNAPSHOT_PANELS
            }
    integrity, integrity_scan_ids = _integrity_panel(
        client,
        start_ns=contributing_start,
        end_ns=end_ns,
        snapshots=all_snapshots,
        candidate_start_ns=measurement_start,
    )
    panels["p11-integrity"] = integrity
    ledger, ledger_scan_ids = _ledger_panel(
        client,
        end_ns=end_ns,
        evaluated_at_ns=evaluated_at_ns,
        snapshots=all_snapshots,
        candidate_start_ns=measurement_start,
    )
    panels["p12-ledger"] = ledger
    if all_snapshots is not None:
        contributing_scan_ids = (
            selected_scan_ids | attribution_scan_ids | integrity_scan_ids | ledger_scan_ids
        )
        by_scan_id = {snapshot["scan_run_id"]: snapshot for snapshot in all_snapshots}
        if not contributing_scan_ids <= by_scan_id.keys():
            error = _IncompleteProjectionError("contributing scan snapshot has not arrived")
            for key in _SNAPSHOT_PANELS:
                panels[key] = _failed_panel(error, "canonical schema-2 scan snapshots")
        else:
            deployments = [
                {
                    "fingerprint": deployment,
                    "projectionId": "agent-introspection.pipeline-observations",
                    "projectionSha256": projection,
                }
                for deployment, projection in sorted(
                    {
                        (
                            by_scan_id[scan_id]["scan.deployment_fingerprint"],
                            by_scan_id[scan_id]["scan.projection_fingerprint"],
                        )
                        for scan_id in contributing_scan_ids
                    }
                )
            ]
    return panels, deployments


def _observations(
    client: EventQueryClient, name: str, start_ns: int, end_ns: int
) -> list[ImmutableEvent]:
    return _query(
        client, _OBSERVATION_QUERY, {"event_name": name, "start": start_ns, "end": end_ns}
    )


def _lag_observation(event: ImmutableEvent, snapshot: dict[str, Any]) -> tuple[str, float | None]:
    if (
        event.timestamp_ns != snapshot["completed_at_ns"]
        or event.nanoseconds("scan.completed_at_ns") != snapshot["completed_at_ns"]
        or event.nanoseconds("scan.extraction_bound_ns") != snapshot["extraction_bound_ns"]
        or event.text("source.capability") != "supported"
    ):
        raise ValueError("source-lag snapshot binding is invalid")
    state = event.text("source.lag_state")
    measurement_keys = ("source.latest_timestamp_ns", "source.lag_ns")
    present = [key for key in measurement_keys if key in event.strings]
    if any(key in event.numbers for key in measurement_keys):
        raise ValueError("source-lag measurement is in the wrong attribute map")
    if state == "absent":
        if present:
            raise ValueError("absent source-lag observation contains a measurement")
        return state, None
    if set(present) != set(measurement_keys):
        raise ValueError("available source-lag observation is missing a measurement")
    latest = event.nanoseconds("source.latest_timestamp_ns")
    lag = event.nanoseconds("source.lag_ns", minimum=-(2**64))
    if latest > snapshot["completed_at_ns"]:
        raise ValueError("source-lag maximum is after scan completion")
    if lag != snapshot["extraction_bound_ns"] - latest:
        raise ValueError("source-lag arithmetic is invalid")
    if state != ("clock_skew" if lag < 0 else "available"):
        raise ValueError("source-lag state disagrees with its value")
    return state, lag / 1_000_000 if lag >= 0 else None


def _lag_rows(events: list[ImmutableEvent], scans: dict[str, dict[str, Any]]) -> list[list[Scalar]]:
    expected = set(SOURCE_LAG_COHORTS)
    by_scan: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    by_cohort: dict[tuple[str, str, str], list[tuple[int, str, float | None]]] = defaultdict(list)
    for event in events:
        scan_id = event.text("scan.run_id")
        if scan_id not in scans:
            continue
        cohort = tuple(
            event.text(key) for key in ("source.producer", "source.surface", "source.signal")
        )
        if len(cohort) != 3 or cohort not in expected or cohort in by_scan[scan_id]:
            raise ValueError("invalid or repeated source-lag cohort")
        key = cohort[0], cohort[1], cohort[2]
        sequence = SOURCE_LAG_COHORTS.index(key) + 1
        if event.text("entity.id") != f"{scan_id}:lag:{sequence}":
            raise ValueError("source-lag entity does not identify its declared cohort")
        state, value = _lag_observation(event, scans[scan_id])
        by_scan[scan_id].add(key)
        by_cohort[key].append((event.timestamp_ns, state, value))
    if set(by_scan) != set(scans) or any(cohorts != expected for cohorts in by_scan.values()):
        raise _IncompleteProjectionError("missing source-lag cohort")
    rows: list[list[Scalar]] = []
    for cohort, observations in sorted(by_cohort.items()):
        values: list[float | int] = [value for _, _, value in observations if value is not None]
        current = max(observations, key=lambda row: row[0])
        rows.append(
            [
                *cohort,
                current[2],
                _percentile(values, 0.5),
                _percentile(values, 0.95),
                len(values),
                sum(state == "clock_skew" for _, state, _ in observations),
                sum(state == "absent" for _, state, _ in observations),
                current[1],
            ]
        )
    return rows


def _source_lag_panel(
    client: EventQueryClient, *, snapshots: list[dict[str, Any]]
) -> PipelinePanel:
    population = "complete supported producer/surface/signal observations"
    scans = {row["scan_run_id"]: row for row in snapshots if row["terminal_status"] == "succeeded"}
    if not scans:
        return _panel("No data", population)
    try:
        events = _observations(
            client,
            "introspection.pipeline.source_lag",
            snapshots[0]["completed_at_ns"] - 1,
            snapshots[-1]["completed_at_ns"],
        )
        rows = _lag_rows(events, scans)
    except Exception as exc:
        return _failed_panel(exc, population)
    panel = _panel(
        "Data",
        population,
        table=(
            [
                "producer",
                "surface",
                "signal",
                "current lag (ms)",
                "p50 (ms)",
                "p95 (ms)",
                "sample count",
                "clock skew",
                "absent",
                "current state",
            ],
            rows,
        ),
    )
    panel["provenance"]["event"] = "introspection.pipeline.source_lag"
    return panel


def _audit_members(
    audits: list[ImmutableEvent], events: list[ImmutableEvent]
) -> tuple[int, Counter[tuple[str, str, str, str, str]]]:
    by_id = {event.text("event.id"): event for event in events}
    claimed: set[str] = set()
    scans: set[str] = set()
    examined = 0
    counts: Counter[tuple[str, str, str, str, str]] = Counter()
    for audit in audits:
        scan = audit.text("scan.run_id")
        runtime = audit.text("scan.runtime_identity")
        if (
            scan in scans
            or audit.text("entity.id") != f"{scan}:integrity-audit"
            or audit.text("audit.state") != "completed"
            or audit.count("pipeline.payload_schema_version") != _SCHEMA
            or audit.count("entity.version") != 1
            or audit.count("event.sequence") != 1
            or audit.nanoseconds("audit.upper_bound_ns") != audit.timestamp_ns
            or audit.timestamp_ns != audit.nanoseconds("audit.upper_bound_ns")
            or "audit.physical_count" in audit.strings
            or "audit.incident_count" in audit.strings
            or "audit.incident_event_ids" in audit.numbers
        ):
            raise ValueError("invalid completed integrity audit")
        scans.add(scan)
        physical = audit.count("audit.physical_count")
        members = read_event_ids(audit, "audit.incident_event_ids", "audit.incident_count")
        if not physical and members:
            raise ValueError("empty integrity audit declares incidents")
        if claimed & members:
            raise ValueError("integrity incident belongs to multiple audits")
        if not members <= by_id.keys():
            raise _IncompleteProjectionError("integrity audit incident population has not arrived")
        claimed.update(members)
        examined += physical
        for identity in members:
            event = by_id[identity]
            _validate_incident(event, audit)
            counts[
                (
                    event.text("incident.invariant"),
                    event.text("incident.producer", empty=True),
                    event.text("incident.surface", empty=True),
                    event.text("incident.origin"),
                    runtime,
                )
            ] += 1
    if claimed != by_id.keys():
        raise ValueError("integrity incident population has unclaimed members")
    return examined, counts


def _validate_incident(event: ImmutableEvent, audit: ImmutableEvent) -> None:
    IntegrityInvariant(event.text("incident.invariant"))
    producer = event.text("incident.producer", empty=True)
    surface = event.text("incident.surface", empty=True)
    origin = event.text("incident.origin")
    offending_time = "incident.event_source_time_ns"
    if (
        event.count("pipeline.payload_schema_version") != _SCHEMA
        or event.text("incident.class") != "integrity_failure"
        or event.nanoseconds("incident.source_time_ns") != event.timestamp_ns
        or event.nanoseconds("incident.observed_at_ns") != event.timestamp_ns
        or event.timestamp_ns != audit.timestamp_ns
        or event.text("scan.run_id") != audit.text("scan.run_id")
        or event.text("scan.runtime_identity") != audit.text("scan.runtime_identity")
        or event.count("entity.version") != 1
        or event.count("event.sequence") != 1
        or producer not in {"", "omp", "codex-cli", "codex-app-server"}
        or surface not in {"", "omp", "codex-cli", "codex-app-server"}
        or (bool(producer) != bool(surface))
        or (producer and surface != producer)
        or origin not in _INCIDENT_ORIGINS
        or offending_time in event.numbers
    ):
        raise ValueError("integrity incident disagrees with completed audit")
    if offending_time in event.strings and event.nanoseconds(offending_time) > audit.timestamp_ns:
        raise ValueError("integrity incident offending time is after its audit")


def _integrity_panel(
    client: EventQueryClient,
    *,
    start_ns: int,
    end_ns: int,
    snapshots: list[dict[str, Any]] | None,
    candidate_start_ns: int = 0,
) -> tuple[PipelinePanel, set[str]]:
    population = "canonical events examined by complete immutable scan audits"
    try:
        if snapshots is None:
            raise _IncompleteProjectionError("scan audit completion population is unavailable")
        audits = _observations(client, INTEGRITY_AUDIT_EVENT, candidate_start_ns, end_ns)
        events = _observations(client, INTEGRITY_INCIDENT_EVENT, candidate_start_ns, end_ns)
        by_id = {event.text("event.id"): event for event in audits}
        audit_owners: dict[str, dict[str, Any]] = {}
        selected_snapshots = [
            snapshot for snapshot in snapshots if start_ns < snapshot["completed_at_ns"] <= end_ns
        ]
        for snapshot in snapshots:
            if snapshot["integrity.capture_state"] != "completed":
                if snapshot in selected_snapshots:
                    raise _IncompleteProjectionError("a selected scan could not complete its audit")
                continue
            identity = snapshot["integrity.observation_event_id"]
            observed = snapshot["integrity.observed_at_ns"]
            audit = by_id.get(identity)
            if audit is None:
                if snapshot in selected_snapshots:
                    raise _IncompleteProjectionError("a completed scan audit has not arrived")
                continue
            if (
                audit.timestamp_ns != observed
                or audit.text("scan.run_id") != snapshot["scan_run_id"]
                or audit.text("scan.runtime_identity") != snapshot["runtime_identity"]
                or identity in audit_owners
            ):
                raise ValueError("integrity audit disagrees with completed scan")
            audit_owners[identity] = snapshot
        selected_audits = [audit for audit in audits if start_ns < audit.timestamp_ns <= end_ns]
        if any(audit.text("event.id") not in audit_owners for audit in selected_audits):
            raise _IncompleteProjectionError("integrity audit completion snapshot has not arrived")
        selected_members = {
            identity
            for audit in selected_audits
            for identity in read_event_ids(
                audit, "audit.incident_event_ids", "audit.incident_count"
            )
        }
        selected_events = [event for event in events if event.text("event.id") in selected_members]
        examined, counts = _audit_members(selected_audits, selected_events)
        panel = _panel(
            "Data" if examined else "No data",
            population,
            metrics=[
                _metric("incidents", len(selected_events), "incidents"),
                _metric("examined canonical events", examined, "physical events"),
                _metric("completed audits", len(selected_audits), "audits"),
            ],
            table=(
                ["invariant", "producer", "surface", "origin", "runtime", "incidents"],
                [[*key, count] for key, count in sorted(counts.items())],
            ),
        )
    except Exception as exc:
        return _failed_panel(exc, population), set()
    panel["timeBasis"] = "integrity incident detection time"
    panel["provenance"]["event"] = INTEGRITY_AUDIT_EVENT
    return panel, {audit_owners[audit.text("event.id")]["scan_run_id"] for audit in selected_audits}


def _optional_count(event: ImmutableEvent, key: str) -> int | None:
    if key in event.strings:
        raise ValueError("maintenance measurement is in the wrong attribute map")
    return event.count(key) if key in event.numbers else None


def _capture_measurement(event: ImmutableEvent, key: str, capture: str) -> int | None:
    value = _optional_count(event, key)
    error_key = f"{key}.error_class"
    error = event.strings.get(error_key)
    if error is not None and (not isinstance(error, str) or not error):
        raise ValueError("maintenance capture error is invalid")
    if value is not None and error is not None:
        raise ValueError("maintenance capture contradicts its error")
    if capture == "complete" and (value is None or error is not None):
        raise ValueError("complete maintenance capture is missing a measurement")
    if capture == "partial" and value is None and error is None:
        raise ValueError("partial maintenance capture is missing its capture error")
    return value


def _backup_metrics(
    event: ImmutableEvent, evaluated_at_ns: int, capture: str
) -> list[PipelineMetric]:
    state = event.text("backup.state")
    fields = ("backup.completed_at_ns", "backup.event_id", "backup.verification")
    present = {
        key for key in (*fields, "backup.bytes") if key in event.strings or key in event.numbers
    }
    error = event.strings.get("backup.error_class")
    if error is not None and (not isinstance(error, str) or not error):
        raise ValueError("backup capture error is invalid")
    if state == "unknown":
        if present or error is not None:
            raise ValueError("unknown backup contradicts captured backup fields")
        return [_metric("backup", state, "state")]
    if state == "unavailable":
        if present or error is None or capture != "partial":
            raise ValueError("unavailable backup capture is invalid")
        return [_metric("backup", state, "state")]
    if state not in {"succeeded", "failed"} or error is not None:
        raise ValueError("backup outcome is invalid")
    if not set(fields) <= present:
        raise ValueError("completed backup is missing a required field")
    completed = event.nanoseconds("backup.completed_at_ns")
    verification = event.text("backup.verification")
    event.text("backup.event_id")
    bytes_written = _optional_count(event, "backup.bytes")
    if (
        completed > event.timestamp_ns
        or completed > evaluated_at_ns
        or verification not in {"ok", "failed", "not_performed"}
        or (state == "succeeded" and verification != "ok")
    ):
        raise ValueError("backup outcome or completion is invalid")
    return [
        _metric("backup", state, "state"),
        _metric("backup age", (evaluated_at_ns - completed) / 1_000_000, "ms"),
        _metric("backup bytes", bytes_written, "bytes"),
        _metric("backup verification", verification, "state"),
    ]


def _maintenance_metrics(event: ImmutableEvent, evaluated_at_ns: int) -> list[PipelineMetric]:
    if (
        event.text("entity.id") != f"{event.text('scan.run_id')}:maintenance"
        or event.count("entity.version") != 1
        or event.count("event.sequence") != 1
    ):
        raise ValueError("maintenance observation has invalid scan ownership")
    capture = event.text("database.capture_state")
    allowed_errors = {
        "database.page_count.error_class",
        "database.freelist_count.error_class",
        "database.bytes.error_class",
        "database.wal_bytes.error_class",
        "database.migration_version.error_class",
        "database.check_error_class",
        "backup.error_class",
    }
    error_keys = {
        key
        for key in event.strings
        if key.endswith(".error_class") or key == "database.check_error_class"
    }
    if error_keys - allowed_errors:
        raise ValueError("maintenance capture declares an unknown error field")
    if capture not in {"complete", "partial"} or (capture == "complete" and error_keys):
        raise ValueError("maintenance capture state is invalid")
    if capture == "partial" and not error_keys:
        raise ValueError("partial maintenance capture has no capture error")
    pages = _capture_measurement(event, "database.page_count", capture)
    free = _capture_measurement(event, "database.freelist_count", capture)
    database_bytes = _capture_measurement(event, "database.bytes", capture)
    wal_bytes = _capture_measurement(event, "database.wal_bytes", capture)
    migration = _capture_measurement(event, "database.migration_version", capture)
    result, check = event.text("database.check_result"), event.text("database.check_type")
    checked_at = event.nanoseconds("database.check_completed_at_ns")
    check_error = event.strings.get("database.check_error_class")
    if (
        (pages is not None and free is not None and free > pages)
        or result not in {"ok", "failed"}
        or check not in {"quick_check", "integrity_check"}
        or checked_at > event.timestamp_ns
        or event.timestamp_ns > evaluated_at_ns
        or (result == "ok" and check_error is not None)
        or (result == "failed" and not isinstance(check_error, str))
        or (result == "failed" and not check_error)
    ):
        raise ValueError("maintenance check outcome or page population is invalid")
    return [
        _metric(f"{check.replace('_', ' ')}", result, "state"),
        _metric("check age", (evaluated_at_ns - checked_at) / 1_000_000, "ms"),
        _metric("database", database_bytes, "bytes"),
        _metric("WAL", wal_bytes, "bytes"),
        _metric(
            "free pages",
            100 * free / pages if free is not None and pages else None,
            "percent",
            (free, pages) if free is not None and pages is not None else None,
        ),
        _metric("migration", migration, "version"),
        *_backup_metrics(event, evaluated_at_ns, capture),
        _metric("observation", _utc(event.timestamp_ns), "UTC"),
    ]


def _ledger_panel(
    client: EventQueryClient,
    *,
    end_ns: int,
    evaluated_at_ns: int,
    snapshots: list[dict[str, Any]] | None,
    candidate_start_ns: int = 0,
) -> tuple[PipelinePanel, set[str]]:
    population = "latest completed immutable maintenance observation"
    try:
        if snapshots is None:
            raise _IncompleteProjectionError("maintenance ownership snapshots are unavailable")
        upper = min(end_ns, evaluated_at_ns)
        events = _query(client, _MAINTENANCE_QUERY, {"start": candidate_start_ns, "upper": upper})
        validated = [(event, _maintenance_metrics(event, evaluated_at_ns)) for event in events]
        if not validated:
            return _panel(
                "Unavailable",
                population,
                reasons=["no completed maintenance observation at the evaluation bound"],
            ), set()
        latest: dict[str, tuple[ImmutableEvent, list[PipelineMetric]]] = {}
        for observation, values in validated:
            identity = observation.text("database.identity")
            previous = latest.get(identity)
            if previous is None or (
                observation.timestamp_ns,
                observation.text("event.id"),
            ) > (previous[0].timestamp_ns, previous[0].text("event.id")):
                latest[identity] = observation, values
        snapshots_by_scan = {snapshot["scan_run_id"]: snapshot for snapshot in snapshots}
        owner_scan_ids: set[str] = set()
        for observation, _ in latest.values():
            owner = snapshots_by_scan.get(observation.text("scan.run_id"))
            if (
                owner is None
                or owner["runtime_identity"] != observation.text("scan.runtime_identity")
                or owner["completed_at_ns"] < observation.timestamp_ns
            ):
                raise _IncompleteProjectionError(
                    "selected maintenance observation owner snapshot has not arrived"
                )
            owner_scan_ids.add(owner["scan_run_id"])
        metric_labels = [
            "quick check",
            "integrity check",
            "check age",
            "backup",
            "backup age",
            "database",
            "WAL",
            "free pages",
            "migration",
        ]
        panel = _panel(
            "Data",
            population,
            metrics=next(iter(latest.values()))[1] if len(latest) == 1 else [],
            table=(
                ["database", "checkType", "runtime", *metric_labels],
                [
                    [
                        identity,
                        observation.text("database.check_type"),
                        observation.text("scan.runtime_identity"),
                        *[
                            next((item["value"] for item in values if item["label"] == label), None)
                            for label in metric_labels
                        ],
                    ]
                    for identity, (observation, values) in sorted(latest.items())
                ],
            ),
            reasons=(
                ["Some maintenance fields could not be observed"]
                if any(
                    item[0].text("database.capture_state") == "partial" for item in latest.values()
                )
                else []
            ),
        )
    except Exception as exc:
        return _failed_panel(exc, population), set()
    panel["timeBasis"] = "maintenance observation completion time"
    panel["provenance"]["event"] = "introspection.pipeline.ledger_maintenance"
    return panel, owner_scan_ids
