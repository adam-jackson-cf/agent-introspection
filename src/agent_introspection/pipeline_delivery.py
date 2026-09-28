"""Bounded final-drain capture and snapshot-bound, remote-only P10 measurements."""

from __future__ import annotations

import calendar
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from agent_introspection.pipeline_contracts import PipelineMetric, PipelinePanel, PipelineState
from agent_introspection.pipeline_events import ImmutableEvent, read_immutable_events
from agent_introspection.telemetry import (
    DerivedEvent,
    EventQueryClient,
    drain_outbox,
    drain_outbox_event_ids,
    enqueue_events,
)

_DELIVERY_SCOPE = "pipeline-delivery"
_COMPLETION = "introspection.pipeline.delivery.final_drain"
_MEMBER = "introspection.pipeline.delivery.event"
_ATTEMPT = "introspection.pipeline.delivery.attempt"
_SAFE_ERROR = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_QUERY = """SELECT timestamp, body, attributes_string AS strings,
       mapApply((key, value) -> (key, toString(reinterpretAsUInt64(value))),
                attributes_number) AS number_bits, attributes_bool AS booleans
    FROM agent_introspection.events
    WHERE attributes_string['event.scope'] = 'pipeline-delivery'
      AND attributes_string['delivery.scan_run_id'] = {scan_run_id:String}"""

PIPELINE_DELIVERY_MIGRATION_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE pipeline_final_drains (
        drain_id TEXT PRIMARY KEY CHECK(length(drain_id) = 36),
        scan_run_id TEXT NOT NULL UNIQUE REFERENCES scan_runs(id),
        started_at_ns TEXT NOT NULL CHECK(
            started_at_ns NOT GLOB '*[^0-9]*' AND substr(started_at_ns, 1, 1) BETWEEN '1' AND '9'),
        completed_at_ns TEXT CHECK(
            completed_at_ns NOT GLOB '*[^0-9]*' AND completed_at_ns GLOB '[1-9]*'),
        max_batches INTEGER NOT NULL CHECK(max_batches > 0),
        batch_limit INTEGER NOT NULL CHECK(batch_limit > 0)
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE pipeline_final_drain_selected_events (
        drain_id TEXT NOT NULL REFERENCES pipeline_final_drains(drain_id),
        event_id TEXT NOT NULL REFERENCES otlp_outbox(event_id),
        created_at_ns TEXT NOT NULL CHECK(
            created_at_ns NOT GLOB '*[^0-9]*' AND substr(created_at_ns, 1, 1) BETWEEN '1' AND '9'),
        destination TEXT NOT NULL,
        event_type TEXT NOT NULL,
        PRIMARY KEY(drain_id, event_id)
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE pipeline_final_drain_attempts (
        drain_id TEXT NOT NULL REFERENCES pipeline_final_drains(drain_id),
        event_id TEXT NOT NULL REFERENCES otlp_outbox(event_id),
        attempt_ordinal INTEGER NOT NULL CHECK(attempt_ordinal > 0),
        attempted_at_ns TEXT NOT NULL CHECK(
            attempted_at_ns NOT GLOB '*[^0-9]*' AND attempted_at_ns GLOB '[1-9]*'),
        outcome TEXT NOT NULL CHECK(outcome IN ('delivered', 'failed')),
        error_class TEXT CHECK(error_class NOT GLOB '*[^A-Za-z0-9_]*'
            AND substr(error_class, 1, 1) GLOB '[A-Za-z]' AND length(error_class) <= 64),
        PRIMARY KEY(drain_id, event_id, attempt_ordinal),
        FOREIGN KEY(drain_id, event_id)
            REFERENCES pipeline_final_drain_selected_events(drain_id, event_id),
        CHECK((outcome = 'delivered' AND error_class IS NULL)
            OR (outcome = 'failed' AND error_class IS NOT NULL))
    ) STRICT, WITHOUT ROWID""",
    """CREATE TABLE pipeline_final_drain_pending_events (
        drain_id TEXT NOT NULL REFERENCES pipeline_final_drains(drain_id),
        event_id TEXT NOT NULL REFERENCES otlp_outbox(event_id),
        created_at_ns TEXT NOT NULL CHECK(
            created_at_ns NOT GLOB '*[^0-9]*' AND substr(created_at_ns, 1, 1) BETWEEN '1' AND '9'),
        destination TEXT NOT NULL,
        event_type TEXT NOT NULL,
        PRIMARY KEY(drain_id, event_id)
    ) STRICT, WITHOUT ROWID""",
)


@dataclass(frozen=True)
class FinalDrainResult:
    drain_id: str
    selected_events: int
    delivered_events: int
    pending_events: int
    failed_events: int


def _created_at_ns(created_at: str) -> str:
    value = datetime.fromisoformat(created_at)
    if value.tzinfo is None:
        raise ValueError("outbox creation timestamp must have a timezone")
    return str(calendar.timegm(value.utctimetuple()) * 1_000_000_000 + value.microsecond * 1_000)


def _event_type(payload_json: str) -> str:
    value = json.loads(payload_json).get("event.name")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise ValueError("outbox event lacks a valid event type")
    return value


def _destination(endpoint: str) -> str:
    target = urlsplit(endpoint)
    if target.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("final drain requires the configured loopback destination")
    return urlunsplit((target.scheme, target.netloc.rsplit("@", 1)[-1], target.path, "", ""))


def _drain_prior_observations(
    connection: sqlite3.Connection, endpoint: str, limit: int, max_batches: int
) -> None:
    """Drain measurement records separately, without recursively observing their delivery."""
    for _ in range(max_batches):
        rows = connection.execute(
            """SELECT event_id FROM otlp_outbox
            WHERE status = 'pending' AND next_attempt_at <= ?
              AND json_extract(payload_json, '$."event.scope"') = ?
            ORDER BY created_at, event_id LIMIT ?""",
            (datetime.now(UTC).isoformat(), _DELIVERY_SCOPE, limit),
        ).fetchall()
        if not rows:
            break
        result = drain_outbox_event_ids(
            connection, [str(row[0]) for row in rows], endpoint=endpoint
        )
        if result["delivered"] == 0:
            break


@dataclass
class _DrainCapture:
    connection: sqlite3.Connection
    drain_id: str
    destination: str
    selected: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    delivered: set[str] = field(default_factory=set)

    def record(
        self,
        rows: Sequence[tuple[str, str, int]],
        delivered: bool,
        attempted_at_ns: str,
        error_class: str | None,
    ) -> None:
        if (delivered and error_class is not None) or (
            not delivered and (error_class is None or not _SAFE_ERROR.fullmatch(error_class))
        ):
            raise ValueError("invalid delivery outcome classification")
        for event_id, payload_json, _ in rows:
            created_at = self.connection.execute(
                "SELECT created_at FROM otlp_outbox WHERE event_id = ?", (event_id,)
            ).fetchone()[0]
            self.connection.execute(
                "INSERT OR IGNORE INTO pipeline_final_drain_selected_events VALUES (?, ?, ?, ?, ?)",
                (
                    self.drain_id,
                    event_id,
                    _created_at_ns(str(created_at)),
                    self.destination,
                    _event_type(payload_json),
                ),
            )
            ordinal = self.connection.execute(
                "SELECT COUNT(*) + 1 FROM pipeline_final_drain_attempts "
                "WHERE drain_id = ? AND event_id = ?",
                (self.drain_id, event_id),
            ).fetchone()[0]
            self.connection.execute(
                "INSERT INTO pipeline_final_drain_attempts VALUES (?, ?, ?, ?, ?, ?)",
                (
                    self.drain_id,
                    event_id,
                    ordinal,
                    attempted_at_ns,
                    "delivered" if delivered else "failed",
                    error_class,
                ),
            )
            self.selected.add(event_id)
            (self.delivered if delivered else self.failed).add(event_id)

    def complete(self) -> FinalDrainResult:
        with self.connection:
            pending = self.connection.execute(
                """SELECT event_id, payload_json, created_at FROM otlp_outbox
                WHERE status = 'pending'
                  AND json_extract(payload_json, '$."event.scope"') IS NOT ?""",
                (_DELIVERY_SCOPE,),
            ).fetchall()
            self.connection.executemany(
                "INSERT INTO pipeline_final_drain_pending_events VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        self.drain_id,
                        str(event_id),
                        _created_at_ns(str(created_at)),
                        self.destination,
                        _event_type(str(payload)),
                    )
                    for event_id, payload, created_at in pending
                ],
            )
            updated = self.connection.execute(
                "UPDATE pipeline_final_drains SET completed_at_ns = ? "
                "WHERE drain_id = ? AND completed_at_ns IS NULL",
                (str(time.time_ns()), self.drain_id),
            )
            if updated.rowcount != 1:
                raise ValueError("final drain completion conflict")
        return FinalDrainResult(
            self.drain_id, len(self.selected), len(self.delivered), len(pending), len(self.failed)
        )


def capture_final_drain(
    connection: sqlite3.Connection,
    *,
    scan_run_id: str,
    endpoint: str,
    limit: int = 500,
    max_batches: int = 20,
) -> FinalDrainResult:
    """Record attempts in the same transaction as the outbox outcome update."""
    if not scan_run_id or limit <= 0 or max_batches <= 0:
        raise ValueError("final drain requires scan ID and positive bounds")
    capture = _DrainCapture(connection, str(uuid.uuid4()), _destination(endpoint))
    _drain_prior_observations(connection, endpoint, limit, max_batches)
    with connection:
        connection.execute(
            "INSERT INTO pipeline_final_drains VALUES (?, ?, ?, NULL, ?, ?)",
            (capture.drain_id, scan_run_id, str(time.time_ns()), max_batches, limit),
        )
    for _ in range(max_batches):
        result = drain_outbox(
            connection, endpoint=endpoint, limit=limit, attempt_observer=capture.record
        )
        if result["selected"] == 0 or result["delivered"] == 0:
            break
    return capture.complete()


@dataclass(frozen=True)
class _Projection:
    drain_id: str
    scan_run_id: str
    completed_at_ns: int

    def event(
        self,
        name: str,
        identity: str,
        ordinal: int,
        attributes: dict[str, str | int | float | bool],
    ) -> DerivedEvent:
        return DerivedEvent(
            scope=_DELIVERY_SCOPE,
            entity_id=identity,
            entity_version=1,
            event_sequence=ordinal,
            event_name=name,
            timestamp_ns=self.completed_at_ns,
            attributes={
                "delivery.payload_schema_version": 2,
                "delivery.drain_id": self.drain_id,
                "delivery.scan_run_id": self.scan_run_id,
                "delivery.completed_at_ns": str(self.completed_at_ns),
                **attributes,
            },
        )


def _member_projections(
    connection: sqlite3.Connection, projection: _Projection
) -> list[DerivedEvent]:
    rows = connection.execute(
        """SELECT event_id, created_at_ns, destination, event_type,
                  MAX(selected), MAX(pending)
        FROM (
            SELECT event_id, created_at_ns, destination, event_type, 1 selected, 0 pending
            FROM pipeline_final_drain_selected_events WHERE drain_id = ?
            UNION ALL
            SELECT event_id, created_at_ns, destination, event_type, 0 selected, 1 pending
            FROM pipeline_final_drain_pending_events WHERE drain_id = ?
        ) GROUP BY event_id, created_at_ns, destination, event_type ORDER BY event_id""",
        (projection.drain_id, projection.drain_id),
    ).fetchall()
    return [
        projection.event(
            _MEMBER,
            f"{projection.drain_id}/{event_id}",
            0,
            {
                "delivery.event_id": str(event_id),
                "delivery.created_at_ns": str(created),
                "delivery.destination": str(destination),
                "delivery.event_type": str(event_type),
                "delivery.selected": int(selected),
                "delivery.pending": int(pending),
            },
        )
        for event_id, created, destination, event_type, selected, pending in rows
    ]


def enqueue_final_drain_projection(
    connection: sqlite3.Connection, result: FinalDrainResult
) -> tuple[str, ...]:
    """Project every immutable member and attempt; apply display caps only after reduction."""
    row = connection.execute(
        "SELECT scan_run_id, completed_at_ns FROM pipeline_final_drains WHERE drain_id = ?",
        (result.drain_id,),
    ).fetchone()
    if row is None or row[1] is None:
        raise ValueError("completed final drain is required for projection")
    projection = _Projection(result.drain_id, str(row[0]), int(row[1]))
    members = _member_projections(connection, projection)
    attempts = connection.execute(
        """SELECT event_id, attempt_ordinal, attempted_at_ns, outcome, error_class
        FROM pipeline_final_drain_attempts WHERE drain_id = ?
        ORDER BY event_id, attempt_ordinal""",
        (result.drain_id,),
    ).fetchall()
    events = members + [
        projection.event(
            _ATTEMPT,
            f"{result.drain_id}/{event_id}",
            int(ordinal),
            {
                "delivery.event_id": str(event_id),
                "delivery.attempt_ordinal": int(ordinal),
                "delivery.attempted_at_ns": str(at),
                "delivery.outcome": str(outcome),
                "delivery.error_class": "" if error is None else str(error),
            },
        )
        for event_id, ordinal, at, outcome, error in attempts
    ]
    events.append(
        projection.event(
            _COMPLETION,
            result.drain_id,
            0,
            {
                "delivery.selected_count": result.selected_events,
                "delivery.delivered_count": result.delivered_events,
                "delivery.pending_count": result.pending_events,
                "delivery.failed_count": result.failed_events,
                "delivery.member_count": len(members),
                "delivery.attempt_count": len(attempts),
            },
        )
    )
    return tuple(enqueue_events(connection, events))


def _read_events(rows: Sequence[Mapping[str, Any]], scan_run_id: str) -> list[ImmutableEvent]:
    events = read_immutable_events(rows, scope=_DELIVERY_SCOPE)
    for event in events:
        if (
            event.count("entity.version") != 1
            or event.count("delivery.payload_schema_version") != 2
            or event.text("delivery.scan_run_id") != scan_run_id
            or event.nanoseconds("delivery.completed_at_ns") != event.timestamp_ns
        ):
            raise ValueError("invalid immutable delivery identity")
    return events


@dataclass
class _DeliveryPopulation:
    completion: ImmutableEvent
    members: dict[str, ImmutableEvent] = field(default_factory=dict)
    attempts: dict[str, dict[int, ImmutableEvent]] = field(default_factory=dict)

    def include(self, event: ImmutableEvent) -> None:
        drain_id = self.completion.text("delivery.drain_id")
        if (
            event.text("delivery.drain_id") != drain_id
            or event.timestamp_ns != self.completion.timestamp_ns
        ):
            raise ValueError("conflicting immutable drain completion")
        kind = event.text("event.name")
        if kind == _COMPLETION:
            return
        event_id = event.text("delivery.event_id")
        if event.text("entity.id") != f"{drain_id}/{event_id}":
            raise ValueError("delivery membership identity mismatch")
        if kind == _MEMBER:
            if event_id in self.members or event.count("event.sequence") != 0:
                raise ValueError("multiple delivery member identities")
            self.members[event_id] = event
        elif kind == _ATTEMPT:
            ordinal = event.count("delivery.attempt_ordinal")
            attempts = self.attempts.setdefault(event_id, {})
            if ordinal < 1 or ordinal in attempts or ordinal != event.count("event.sequence"):
                raise ValueError("multiple delivery attempt identities")
            attempts[ordinal] = event
        else:
            raise ValueError("unsupported immutable delivery event")

    def validate_attempts(self, event_id: str, member: ImmutableEvent) -> tuple[bool, bool]:
        attempts = self.attempts.get(event_id, {})
        if sorted(attempts) != list(range(1, len(attempts) + 1)):
            raise ValueError("delivery attempt ordinal gap")
        if bool(attempts) != bool(member.count("delivery.selected")):
            raise ValueError("incomplete delivery attempt population")
        failed = delivered = False
        previous_at = member.nanoseconds("delivery.created_at_ns")
        for _, attempt in sorted(attempts.items()):
            at = attempt.nanoseconds("delivery.attempted_at_ns")
            outcome = attempt.text("delivery.outcome")
            error = attempt.text("delivery.error_class", empty=True)
            if not previous_at <= at <= self.completion.timestamp_ns or delivered:
                raise ValueError("impossible delivery attempt chronology")
            if outcome not in {"delivered", "failed"} or (
                (outcome == "delivered" and error != "")
                or (outcome == "failed" and not _SAFE_ERROR.fullmatch(error))
            ):
                raise ValueError("invalid delivery attempt outcome")
            failed |= outcome == "failed"
            delivered |= outcome == "delivered"
            previous_at = at
        if delivered and member.count("delivery.pending"):
            raise ValueError("delivered event remains pending")
        return failed, delivered

    def totals(self) -> dict[str, int]:
        if set(self.attempts) - set(self.members):
            raise ValueError("delivery attempt outside member population")
        totals = {
            "selected": 0,
            "delivered": 0,
            "pending": 0,
            "failed": 0,
            "member": len(self.members),
            "attempt": sum(map(len, self.attempts.values())),
        }
        for event_id, member in self.members.items():
            selected, pending = member.count("delivery.selected"), member.count("delivery.pending")
            if selected not in {0, 1} or pending not in {0, 1} or not (selected or pending):
                raise ValueError("invalid delivery membership flags")
            if member.nanoseconds("delivery.created_at_ns") > self.completion.timestamp_ns:
                raise ValueError("pending event created after drain completion")
            member.text("delivery.destination")
            member.text("delivery.event_type")
            failed, delivered = self.validate_attempts(event_id, member)
            totals["selected"] += selected
            totals["pending"] += pending
            totals["failed"] += failed
            totals["delivered"] += delivered
        if any(
            value != self.completion.count(f"delivery.{key}_count") for key, value in totals.items()
        ):
            raise ValueError("incomplete immutable delivery population")
        return totals


def _population(events: list[ImmutableEvent]) -> _DeliveryPopulation:
    completions = [event for event in events if event.text("event.name") == _COMPLETION]
    if len(completions) != 1:
        raise ValueError("expected exactly one immutable completion per scan")
    completion = completions[0]
    if (
        completion.text("entity.id") != completion.text("delivery.drain_id")
        or completion.count("event.sequence") != 0
    ):
        raise ValueError("invalid drain completion entity")
    result = _DeliveryPopulation(completion)
    for event in events:
        result.include(event)
    return result


def _metric(
    label: str,
    value: int | float | None,
    unit: str,
    numerator: int | None = None,
    denominator: int | None = None,
) -> PipelineMetric:
    return {
        "label": label,
        "value": value,
        "unit": unit,
        "numerator": numerator,
        "denominator": denominator,
        "sampleCount": denominator,
    }


def _panel(state: PipelineState, reason: str = "") -> PipelinePanel:
    return {
        "state": state,
        "population": "immutable event population of one completed scan final drain",
        "timeBasis": "canonical scan completion time",
        "rangeOperator": "start < completion <= end",
        "metrics": [],
        "columns": [],
        "rows": [],
        "series": [],
        "reasons": [reason] if reason else [],
        "provenance": {"source": "remote-only"},
    }


def _render_population(population: _DeliveryPopulation, completed_at_ns: int) -> PipelinePanel:
    totals = population.totals()
    if population.completion.timestamp_ns > completed_at_ns:
        raise ValueError("drain completed after canonical scan completion")
    pending_created = [
        member.nanoseconds("delivery.created_at_ns")
        for member in population.members.values()
        if member.count("delivery.pending")
    ]
    age = (completed_at_ns - min(pending_created)) / 1_000_000_000 if pending_created else None
    rate = 100 * totals["failed"] / totals["selected"] if totals["selected"] else None
    panel = _panel("Data" if population.members else "No data")
    panel["metrics"] = [
        _metric("pending events", totals["pending"], "events"),
        _metric("oldest pending age", age, "seconds", denominator=totals["pending"]),
        _metric("drain failure rate", rate, "percent", totals["failed"], totals["selected"]),
    ]
    panel["columns"] = [
        "destination",
        "event type",
        "event identity",
        "pending",
        "attempts",
        "errors",
    ]
    for event_id, member in sorted(
        population.members.items(),
        key=lambda item: (
            -item[1].count("delivery.pending"),
            item[1].nanoseconds("delivery.created_at_ns"),
            item[0],
        ),
    )[:50]:
        attempts = population.attempts.get(event_id, {})
        errors = sorted(
            {
                attempt.text("delivery.error_class", empty=True)
                for attempt in attempts.values()
                if attempt.text("delivery.outcome") == "failed"
            }
        )
        panel["rows"].append(
            [
                member.text("delivery.destination"),
                member.text("delivery.event_type"),
                event_id,
                member.count("delivery.pending"),
                len(attempts),
                ", ".join(errors) or None,
            ]
        )
    panel["provenance"].update(
        {
            "drainId": population.completion.text("delivery.drain_id"),
            "scanRunId": population.completion.text("delivery.scan_run_id"),
        }
    )
    if len(population.members) > 50:
        panel["reasons"].append(
            "Displaying 50 event identities; metrics include the complete drain."
        )
    return panel


def query_delivery_detail(
    client: EventQueryClient, *, scan_run_id: str, completed_at_ns: int
) -> PipelinePanel:
    """Use only the latest already-validated canonical snapshot's immutable drain."""
    if not scan_run_id or type(completed_at_ns) is not int or completed_at_ns <= 0:
        raise ValueError("validated canonical scan identity and completion are required")
    try:
        rows = list(client.query(_QUERY, {"scan_run_id": scan_run_id}))
    except Exception:
        return _panel("Query/system error", "Remote final-drain query failed; outcome unknown.")
    try:
        events = _read_events(rows, scan_run_id)
    except (KeyError, TypeError, ValueError, OverflowError):
        return _panel("Integrity failure", "Invalid immutable delivery identity or payload.")
    if not events:
        return _panel("Unavailable", "No immutable final-drain projection for the selected scan.")
    try:
        return _render_population(_population(events), completed_at_ns)
    except (KeyError, TypeError, ValueError, OverflowError):
        return _panel(
            "Integrity failure", "Incomplete or conflicting immutable delivery population."
        )
