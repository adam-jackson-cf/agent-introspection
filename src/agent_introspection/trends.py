"""Deterministic trend evaluation for observation findings."""

from __future__ import annotations

import sqlite3
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from agent_introspection.detectors import FingerprintComponents

LONDON = ZoneInfo("Europe/London")


class TrendState(StrEnum):
    ISOLATED = "isolated"
    EMERGING = "emerging"
    ACTIONABLE = "actionable"
    DORMANT = "dormant"


@dataclass(frozen=True)
class Occurrence:
    observation_id: str
    finding_id: str
    occurred_at_ns: int
    canonical_task_id: str | None

    @property
    def occurred_at(self) -> datetime:
        return datetime.fromtimestamp(self.occurred_at_ns / 1_000_000_000, tz=UTC)


@dataclass(frozen=True)
class TrendEvaluation:
    finding_id: str
    state: TrendState
    occurrence_count: int
    canonical_task_count: int
    local_day_count: int
    window_started_at_ns: int
    window_ended_at_ns: int


@dataclass(frozen=True, slots=True)
class CanonicalActivityOccurrence:
    """The latest attribution-bearing occurrence for one canonical activity."""

    activity_id: str
    attribution_version: int
    attribution_state: str
    project_identity_id: str | None
    detector_id: str
    detector_version: int
    normalization_version: int
    operation_kind: str
    target_kind: str
    normalized_target: str
    normalized_failure_class: str
    occurred_at_ns: int
    canonical_task_id: str | None

    @property
    def fingerprint(self) -> str:
        """Return the canonical membership/project grouping key."""
        project_key = f"{self.attribution_state}:{self.project_identity_id or ''}"
        return FingerprintComponents(
            detector_version=self.detector_version,
            category=self.detector_id,
            project_identity=project_key,
            operation_kind=self.operation_kind,
            target_kind=self.target_kind,
            normalized_target=self.normalized_target,
            normalized_failure_class=self.normalized_failure_class,
        ).digest()


def _latest_canonical_occurrences(
    connection: sqlite3.Connection, activity_ids: Iterable[str] | None = None
) -> list[CanonicalActivityOccurrence]:
    """Load canonical activities with exactly their latest attribution versions."""
    identifiers = tuple(sorted(set(activity_ids or ())))
    where = ""
    parameters: tuple[object, ...] = ()
    if identifiers:
        where = f"WHERE a.id IN ({', '.join('?' for _ in identifiers)})"
        parameters = identifiers
    rows = connection.execute(
        f"""
        SELECT a.id, av.version, av.attribution_state, av.project_identity_id,
               a.detector_id, a.detector_version, a.normalization_version,
               a.operation_kind, a.target_kind, a.normalized_target,
               a.normalized_failure_class, a.source_started_at_ns, a.correlation_id
        FROM canonical_activities a
        JOIN canonical_activity_versions av
          ON av.activity_id = a.id
         AND av.version = (
             SELECT MAX(latest.version)
             FROM canonical_activity_versions latest
             WHERE latest.activity_id = a.id
         )
        {where}
        ORDER BY a.id
        """,
        parameters,
    ).fetchall()
    return [
        CanonicalActivityOccurrence(
            activity_id=str(row[0]),
            attribution_version=int(row[1]),
            attribution_state=str(row[2]),
            project_identity_id=str(row[3]) if row[3] is not None else None,
            detector_id=str(row[4]),
            detector_version=int(row[5]),
            normalization_version=int(row[6]),
            operation_kind=str(row[7]),
            target_kind=str(row[8]),
            normalized_target=str(row[9]),
            normalized_failure_class=str(row[10]),
            occurred_at_ns=int(row[11]),
            canonical_task_id=str(row[12]) if row[12] is not None else None,
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class _FindingMembership:
    """Canonical members and metadata for one finding convergence."""

    fingerprint: str
    members: tuple[CanonicalActivityOccurrence, ...]
    updated_at: str


def _requested_canonical_occurrences(
    connection: sqlite3.Connection, activity_ids: Iterable[str]
) -> list[CanonicalActivityOccurrence]:
    """Load requested activities, requiring an attribution version for each."""
    requested_ids = tuple(sorted(set(activity_ids)))
    if not requested_ids:
        return []
    impacted = _latest_canonical_occurrences(connection, requested_ids)
    if len(impacted) != len(requested_ids):
        known = {occurrence.activity_id for occurrence in impacted}
        raise KeyError(
            "canonical activities have no attribution version: "
            + ", ".join(sorted(set(requested_ids) - known))
        )
    return impacted


def _canonical_occurrences_by_fingerprint(
    connection: sqlite3.Connection,
) -> dict[str, list[CanonicalActivityOccurrence]]:
    """Group the latest canonical activity versions by finding fingerprint."""
    by_fingerprint: dict[str, list[CanonicalActivityOccurrence]] = {}
    for occurrence in _latest_canonical_occurrences(connection):
        by_fingerprint.setdefault(occurrence.fingerprint, []).append(occurrence)
    return by_fingerprint


def _finding_id_for_membership(
    connection: sqlite3.Connection, membership: _FindingMembership
) -> str:
    """Return the persisted finding ID, creating its initial row when absent."""
    first = membership.members[0]
    finding_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"agent-introspection:{membership.fingerprint}")
    )
    existing = connection.execute(
        "SELECT id FROM findings WHERE fingerprint = ?", (membership.fingerprint,)
    ).fetchone()
    if existing is not None:
        return str(existing[0])
    connection.execute(
        """
        INSERT INTO findings (
            id, fingerprint, category, project_identity_id, trend_state,
            detector_id, detector_version, first_seen_ns, last_seen_ns,
            occurrence_count, canonical_task_count, local_day_count,
            entity_version, is_active, replaced_by_finding_id, updated_at
        ) VALUES (?, ?, ?, ?, 'isolated', ?, ?, ?, ?, 0, 0, 0, 1, 1, NULL, ?)
        """,
        (
            finding_id,
            membership.fingerprint,
            first.detector_id,
            first.project_identity_id,
            first.detector_id,
            first.detector_version,
            min(member.occurred_at_ns for member in membership.members),
            max(member.occurred_at_ns for member in membership.members),
            membership.updated_at,
        ),
    )
    return finding_id


def _converge_finding_membership(
    connection: sqlite3.Connection, membership: _FindingMembership
) -> None:
    """Assign members to their finding and retire superseded active findings."""
    finding_id = _finding_id_for_membership(connection, membership)
    for member in membership.members:
        prior = connection.execute(
            """
            SELECT cfm.finding_id
            FROM canonical_finding_membership cfm
            JOIN findings f ON f.id = cfm.finding_id
            WHERE cfm.activity_id = ? AND f.is_active = 1
            """,
            (member.activity_id,),
        ).fetchone()
        if prior is not None and str(prior[0]) != finding_id:
            connection.execute(
                """
                UPDATE findings
                SET is_active = 0, replaced_by_finding_id = ?,
                    entity_version = entity_version + 1, updated_at = ?
                WHERE id = ? AND is_active = 1
                """,
                (finding_id, membership.updated_at, str(prior[0])),
            )
        connection.execute(
            """
            INSERT INTO canonical_finding_membership (
                finding_id, activity_id, rationale, created_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(finding_id, activity_id) DO NOTHING
            """,
            (
                finding_id,
                member.activity_id,
                "canonical activity membership",
                membership.updated_at,
            ),
        )


def _update_finding_trends(
    connection: sqlite3.Connection, *, now: datetime, updated_at: str
) -> list[TrendEvaluation]:
    """Evaluate all active findings and persist only changed trend metrics."""
    rows = connection.execute(
        """
        SELECT f.id, f.trend_state, cfm.activity_id, a.source_started_at_ns, a.correlation_id
        FROM findings f
        LEFT JOIN canonical_finding_membership cfm ON cfm.finding_id = f.id
        LEFT JOIN canonical_activities a ON a.id = cfm.activity_id
        WHERE f.is_active = 1
        """
    ).fetchall()
    occurrences = [
        Occurrence(str(row[2]), str(row[0]), int(row[3]), str(row[4]))
        for row in rows
        if row[2] is not None
    ]
    previous = {str(row[0]) for row in rows if row[1] == TrendState.ACTIONABLE}
    evaluations = evaluate_findings(occurrences, now=now, previously_actionable=previous)
    changed: list[TrendEvaluation] = []
    for evaluation in evaluations:
        current = connection.execute(
            """
            SELECT trend_state, occurrence_count, canonical_task_count, local_day_count
            FROM findings WHERE id = ? AND is_active = 1
            """,
            (evaluation.finding_id,),
        ).fetchone()
        if current is None or tuple(current) == (
            evaluation.state,
            evaluation.occurrence_count,
            evaluation.canonical_task_count,
            evaluation.local_day_count,
        ):
            continue
        connection.execute(
            """
            UPDATE findings SET trend_state = ?, occurrence_count = ?,
                canonical_task_count = ?, local_day_count = ?,
                entity_version = entity_version + 1, updated_at = ?
            WHERE id = ?
            """,
            (
                evaluation.state,
                evaluation.occurrence_count,
                evaluation.canonical_task_count,
                evaluation.local_day_count,
                updated_at,
                evaluation.finding_id,
            ),
        )
        changed.append(evaluation)
    return changed


def recompute_canonical_findings(
    connection: sqlite3.Connection,
    activity_ids: Iterable[str],
    *,
    now: datetime,
) -> list[TrendEvaluation]:
    """Converge canonical memberships, findings, and trends in the caller transaction."""
    if now.tzinfo is None:
        raise ValueError("trend evaluation clock must be timezone-aware")
    if not connection.in_transaction:
        raise ValueError("canonical finding recomputation requires an active transaction")

    impacted = _requested_canonical_occurrences(connection, activity_ids)
    if not impacted:
        return []
    by_fingerprint = _canonical_occurrences_by_fingerprint(connection)
    updated_at = now.astimezone(UTC).isoformat()
    for fingerprint in sorted({occurrence.fingerprint for occurrence in impacted}):
        _converge_finding_membership(
            connection,
            _FindingMembership(
                fingerprint=fingerprint,
                members=tuple(by_fingerprint[fingerprint]),
                updated_at=updated_at,
            ),
        )
    return _update_finding_trends(connection, now=now, updated_at=updated_at)


def evaluate_findings(
    occurrences: list[Occurrence],
    *,
    now: datetime,
    previously_actionable: set[str] | None = None,
) -> list[TrendEvaluation]:
    """Evaluate findings against the canonical seven-day thresholds."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now_utc = now.astimezone(UTC)
    window_start = now_utc - timedelta(days=7)
    grouped: dict[str, list[Occurrence]] = defaultdict(list)
    for occurrence in occurrences:
        if window_start <= occurrence.occurred_at <= now_utc:
            grouped[occurrence.finding_id].append(occurrence)

    prior = previously_actionable or set()
    finding_ids = set(grouped) | prior
    evaluations: list[TrendEvaluation] = []
    for finding_id in sorted(finding_ids):
        rows = grouped.get(finding_id, [])
        tasks = {row.canonical_task_id for row in rows if row.canonical_task_id is not None}
        days = {row.occurred_at.astimezone(LONDON).date() for row in rows}
        count = len(rows)
        actionable = (count >= 3 and len(tasks) >= 2 and len(days) >= 2) or (
            count >= 5 and len(tasks) >= 3
        )
        if actionable:
            state = TrendState.ACTIONABLE
        elif not rows and finding_id in prior:
            state = TrendState.DORMANT
        elif count <= 1:
            state = TrendState.ISOLATED
        else:
            state = TrendState.EMERGING
        evaluations.append(
            TrendEvaluation(
                finding_id=finding_id,
                state=state,
                occurrence_count=count,
                canonical_task_count=len(tasks),
                local_day_count=len(days),
                window_started_at_ns=int(window_start.timestamp() * 1_000_000_000),
                window_ended_at_ns=int(now_utc.timestamp() * 1_000_000_000),
            )
        )
    return evaluations
