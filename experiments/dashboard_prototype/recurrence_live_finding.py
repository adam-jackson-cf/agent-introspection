"""Fail-closed E-Recurrence-1 reduction from committed immutable captures."""

from __future__ import annotations

import hashlib
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.experiment_live_common import LiveProofRequest
from experiments.dashboard_prototype.recurrence_common import (
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
    RecurrenceLiveEvidence,
    RecurrencePrimitive,
)

_SUPPORTED_PRODUCERS: Final[frozenset[str]] = frozenset({"omp", "codex-cli", "codex-app-server"})
_CAPTURE_OBSERVATION_SQL: Final[str] = (
    "create table finding_membership_observation ( "
    "observation_id text primary key, "
    "scan_run_id text not null, "
    "observed_at_ns integer not null, "
    "evidence_start_ns integer, "
    "evidence_end_ns integer, "
    "evidence_window_definition text, "
    "finding_version_source_id text not null, "
    "membership_source_id text not null, "
    "activity_version_source_id text not null, "
    "activity_source_started_at_ns integer not null, "
    "activity_source_ended_at_ns integer not null, "
    "finding_fingerprint text not null, "
    "detector_id text not null, "
    "detector_version integer not null, "
    "finding_state text not null, "
    "finding_occurrence_count integer not null, "
    "finding_canonical_task_count integer not null, "
    "finding_local_day_count integer not null, "
    "task_membership_state text not null, "
    "native_task_id text, "
    "producer text not null, "
    "activity_version integer not null, "
    "latest_activity_version integer not null, "
    "unique (finding_version_source_id, membership_source_id) ) strict"
)
_CAPTURE_TRIGGERS: Final[frozenset[str]] = frozenset(
    {
        "finding_membership_observation_no_insert",
        "finding_membership_observation_no_update",
        "finding_membership_observation_no_delete",
        "capture_no_insert",
        "capture_no_update",
        "capture_no_delete",
    }
)
_CAPTURE_TRIGGER_SQL: Final[dict[str, str]] = {
    "finding_membership_observation_no_insert": (
        "before insert on finding_membership_observation "
        "begin select raise(abort, 'finding observations are sealed'); end"
    ),
    "finding_membership_observation_no_update": (
        "before update on finding_membership_observation "
        "begin select raise(abort, 'finding observations are immutable'); end"
    ),
    "finding_membership_observation_no_delete": (
        "before delete on finding_membership_observation "
        "begin select raise(abort, 'finding observations cannot be deleted'); end"
    ),
    "capture_no_insert": (
        "before insert on capture begin select raise(abort, 'capture is sealed'); end"
    ),
    "capture_no_update": (
        "before update on capture begin select raise(abort, 'capture is immutable'); end"
    ),
    "capture_no_delete": (
        "before delete on capture begin select raise(abort, 'capture is immutable'); end"
    ),
}


def extract_capture(path: Path, request: LiveProofRequest) -> RecurrenceLiveEvidence:
    """Reduce one new-schema committed capture without reading mutable native tables."""
    capture = path.resolve(strict=True)
    connection = sqlite3.connect(f"file:{capture}?mode=ro", uri=True)
    try:
        rows = _captured_members(connection)
    finally:
        connection.close()
    window_exact = _captured_window_exact(rows, request)
    selected = _selected_members(rows, request) if window_exact else ()
    membership_exact = bool(selected) and all(
        row["task_membership_state"] == "qualified"
        and _native_task_id(row["native_task_id"])
        and row["producer"] in _SUPPORTED_PRODUCERS
        for row in selected
    )
    reconciled, members = _captured_counts(selected) if membership_exact else (False, ())
    finding_ids = tuple(sorted({str(row["finding_version_source_id"]) for row in rows}))
    membership_ids = tuple(sorted({str(row["membership_source_id"]) for row in rows}))
    counts = {
        "captured_findings": len(finding_ids),
        "captured_memberships": len(membership_ids),
        **Counter(f"membership.{row['producer']}.{row['task_membership_state']}" for row in rows),
    }
    assertions = {
        "schema_required": True,
        "durable_window_bounds_authoritative": window_exact,
        "membership_task_identity_authoritative": membership_exact,
        "selected_finding_evidence_range_exact": window_exact and bool(selected),
        "selected_membership_denominator_reconciled": membership_exact and reconciled,
        "latest_activity_versions_global": membership_exact
        and all(
            type(row["activity_version"]) is int
            and type(row["latest_activity_version"]) is int
            and row["activity_version"] == row["latest_activity_version"]
            for row in selected
        ),
    }
    blockers = ["missing_durable_authority"]
    if not assertions["latest_activity_versions_global"]:
        blockers.append("missing_global_latest_activity_version_authority")
    if not window_exact:
        blockers.append("missing_immutable_evidence_window")
    if not membership_exact:
        blockers.append("missing_immutable_canonical_task_membership")
    if window_exact and not selected:
        blockers.append("missing_nonempty_qualified_population")
    if membership_exact and not reconciled:
        assertions["contradiction_detected"] = True
    proof = RecurrenceExperimentProof(
        experiment_id=RecurrenceExperimentId.FINDING_PROJECTION,
        run_id=request.run_id,
        result=(
            ExperimentResult.FAILED
            if membership_exact and not reconciled
            else ExperimentResult.BLOCKED
        ),
        provenance=EvidenceProvenance.FRESH_REAL,
        source_start=request.start,
        source_end=request.end,
        source_boundary=request.source_boundary,
        metrics=counts,
        assertions=assertions,
        evidence_ids=tuple(sorted((*finding_ids, *membership_ids))),
        proposal="immutable_finding_projection_required",
        blocked_boundaries=() if membership_exact and not reconciled else tuple(blockers),
    )
    return RecurrenceLiveEvidence(
        proof=proof,
        primitives=_captured_primitives(members) if reconciled else (),
        remote_calculation_id=f"finding-{request.run_id}",
        finding_version_source_ids=finding_ids,
        canonical_task_membership_source_ids=membership_ids,
    )


def _captured_members(connection: sqlite3.Connection) -> tuple[sqlite3.Row, ...]:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if tables != {"capture", "finding_membership_observation"}:
        raise ValueError("finding capture is not the committed 23-column schema")
    triggers = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    if triggers != _CAPTURE_TRIGGERS or not _committed_capture_ddl(connection):
        raise ValueError("finding capture is not the committed 23-column schema")
    metadata = tuple(connection.execute("SELECT scan_run_id, observed_at_ns FROM capture"))
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    rows = tuple(
        cursor.execute(
            "SELECT scan_run_id, observed_at_ns, evidence_start_ns, evidence_end_ns, "
            "evidence_window_definition, finding_version_source_id, membership_source_id, "
            "activity_version_source_id, activity_source_started_at_ns, "
            "activity_source_ended_at_ns, producer, activity_version, latest_activity_version, "
            "finding_fingerprint, finding_occurrence_count, finding_canonical_task_count, "
            "finding_local_day_count, task_membership_state, native_task_id "
            "FROM finding_membership_observation "
        )
    )
    if (
        len(metadata) != 1
        or not rows
        or any((row["scan_run_id"], row["observed_at_ns"]) != tuple(metadata[0]) for row in rows)
    ):
        raise ValueError("finding capture lacks one exact committed observation population")
    return rows


def _normalized_sql(value: str) -> str:
    return " ".join(value.lower().split())


def _committed_capture_ddl(connection: sqlite3.Connection) -> bool:
    schema = {
        str(name): str(sql)
        for name, sql in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'trigger')"
        )
    }
    capture = _normalized_sql(schema.get("capture", ""))
    observations = _normalized_sql(schema.get("finding_membership_observation", ""))
    expected_capture = (
        "create table capture ( scan_run_id text not null, observed_at_ns integer not null ) strict"
    )
    if capture != expected_capture or observations != _CAPTURE_OBSERVATION_SQL:
        return False
    return all(
        _normalized_sql(schema.get(name, "")).removeprefix(f"create trigger {name} ") == statement
        for name, statement in _CAPTURE_TRIGGER_SQL.items()
    )


def _epoch_ns(value: datetime) -> int:
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _exact_ns(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _captured_window_exact(rows: tuple[sqlite3.Row, ...], request: LiveProofRequest) -> bool:
    start, end = (
        instant.astimezone(ZoneInfo("Europe/London")) for instant in (request.start, request.end)
    )
    requested = (_epoch_ns(request.start), _epoch_ns(request.end))
    return (
        start.time() == end.time() == time.min
        and (end.date() - start.date()).days == 7
        and all(
            _exact_ns(row["evidence_start_ns"]) is not None
            and _exact_ns(row["evidence_end_ns"]) is not None
            and int(row["evidence_start_ns"]) < int(row["evidence_end_ns"])
            and row["evidence_window_definition"] == "europe_london_calendar_7d"
            and (row["evidence_start_ns"], row["evidence_end_ns"]) == requested
            for row in rows
        )
    )


def _selected_members(
    rows: tuple[sqlite3.Row, ...], request: LiveProofRequest
) -> tuple[sqlite3.Row, ...]:
    start_ns, end_ns = _epoch_ns(request.start), _epoch_ns(request.end)
    selected: list[sqlite3.Row] = []
    for row in rows:
        timestamp_ns = _exact_ns(row["activity_source_started_at_ns"])
        ended_ns = _exact_ns(row["activity_source_ended_at_ns"])
        if timestamp_ns is None or ended_ns is None or timestamp_ns > ended_ns:
            raise ValueError("finding capture has an invalid activity window timestamp")
        if start_ns < timestamp_ns <= end_ns:
            selected.append(row)
    return tuple(selected)


def _native_task_id(value: object) -> str | None:
    return (
        value
        if isinstance(value, str) and value.startswith("thread:") and value != "thread:"
        else None
    )


def _captured_counts(
    rows: tuple[sqlite3.Row, ...],
) -> tuple[bool, tuple[tuple[sqlite3.Row, str, str], ...]]:
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[str(row["finding_version_source_id"])].append(row)
    reconciled = True
    members: list[tuple[sqlite3.Row, str, str]] = []
    for group in groups.values():
        occurrences: dict[str, sqlite3.Row] = {}
        tasks: dict[str, sqlite3.Row] = {}
        days: dict[str, sqlite3.Row] = {}
        for row in group:
            occurrence = str(row["activity_version_source_id"])
            task = f"{row['producer']}:{row['native_task_id']}"
            timestamp_ns = _exact_ns(row["activity_source_started_at_ns"])
            if timestamp_ns is None:
                return False, ()
            day = (
                datetime.fromtimestamp(timestamp_ns // 1_000_000_000, UTC)
                .astimezone(ZoneInfo("Europe/London"))
                .date()
                .isoformat()
            )
            occurrences.setdefault(occurrence, row)
            tasks.setdefault(task, row)
            days.setdefault(day, row)
        expected = {
            (
                row["finding_occurrence_count"],
                row["finding_canonical_task_count"],
                row["finding_local_day_count"],
            )
            for row in group
        }
        actual = (len(occurrences), len(tasks), len(days))
        reconciled &= expected == {actual}
        members.extend((row, "occurrence_count", identity) for identity, row in occurrences.items())
        members.extend((row, "canonical_task_count", identity) for identity, row in tasks.items())
        members.extend((row, "local_day_count", identity) for identity, row in days.items())
    return reconciled, tuple(members)


def _captured_primitives(
    members: tuple[tuple[sqlite3.Row, str, str], ...],
) -> tuple[RecurrencePrimitive, ...]:
    primitives: list[RecurrencePrimitive] = []
    for row, metric, identity in members:
        timestamp_ns = int(row["activity_source_started_at_ns"])
        primitives.append(
            RecurrencePrimitive(
                experiment_id=RecurrenceExperimentId.FINDING_PROJECTION,
                source_time=datetime.fromtimestamp(timestamp_ns // 1_000_000_000, UTC),
                source_time_ns=timestamp_ns,
                ordinal=len(primitives),
                dimensions={
                    "stable_identity": f"{row['finding_fingerprint']}.{metric}",
                    "event_id": hashlib.sha256(identity.encode()).hexdigest(),
                    "capability_state": "supported",
                },
                measures={"reducer_counts": 1.0},
            )
        )
    return tuple(primitives)
