"""Immutable analysis-generation staging and promotion."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_introspection import detectors, identities, normalization, outcomes, source, trends
from agent_introspection.telemetry import (
    OPERATIONAL_SCOPE,
    DerivedEvent,
    EventQueryClient,
    RemoteEventReference,
    drain_outbox,
    enqueue_events,
    remote_event_ids,
)


class GenerationError(RuntimeError):
    """An analysis generation cannot be staged or activated safely."""


_SCAN_EXTRACTION_PATH = Path(__file__).with_name("scan.py")


@dataclass(frozen=True, slots=True)
class StagedGeneration:
    """Immutable evidence produced by one bounded local projection."""

    generation_id: str
    ordinal: int
    window_start_ns: int
    window_end_ns: int
    semantic_hash: str
    projection_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActivatedGeneration:
    """Evidence that a remotely verified generation became canonical."""

    generation_id: str
    activation_event_id: str
    projection_count: int


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _module_hash(module_path: str | None) -> str:
    if module_path is None:
        raise GenerationError("analysis contract module path is unavailable")
    return _sha256(Path(module_path).read_bytes())


def _semantic_contract(source_contract_fingerprint: str) -> tuple[str, str, str]:
    if len(source_contract_fingerprint) != 64:
        raise ValueError("source contract fingerprint must be a SHA-256 value")
    contract_hashes = {
        "detector_contract_hash": _module_hash(detectors.__file__),
        "identity_contract_hash": _module_hash(identities.__file__),
        "normalization_contract_hash": _module_hash(normalization.__file__),
        "outcome_contract_hash": _module_hash(outcomes.__file__),
        "scan_extraction_contract_hash": _module_hash(str(_SCAN_EXTRACTION_PATH)),
        "source_extraction_contract_hash": _module_hash(source.__file__),
        "trend_contract_hash": _module_hash(trends.__file__),
    }
    semantic_hash = _sha256(
        json.dumps(
            {
                **contract_hashes,
                "source_contract_fingerprint": source_contract_fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    return (
        contract_hashes["detector_contract_hash"],
        contract_hashes["normalization_contract_hash"],
        semantic_hash,
    )


def current_generation_id(connection: sqlite3.Connection) -> str | None:
    """Return the sole remotely verified canonical projection generation."""

    row = connection.execute(
        "SELECT generation_id FROM analysis_generation_current WHERE singleton = 1"
    ).fetchone()
    return str(row[0]) if row is not None else None


def validate_active_generation_contract(
    connection: sqlite3.Connection, *, source_contract_fingerprint: str
) -> str | None:
    """Require the current generation to match the runtime semantic contract."""

    row = connection.execute(
        """
        SELECT generation.id, generation.source_contract_fingerprint, generation.semantic_hash
        FROM analysis_generation_current current
        JOIN analysis_generations generation ON generation.id = current.generation_id
        WHERE current.singleton = 1
        """
    ).fetchone()
    if row is None:
        return None
    generation_id = str(row[0])
    if str(row[1]) != source_contract_fingerprint:
        raise GenerationError("active analysis generation source contract is incompatible")
    _, _, runtime_semantic_hash = _semantic_contract(source_contract_fingerprint)
    if str(row[2]) != runtime_semantic_hash:
        raise GenerationError("active analysis generation semantic contract is incompatible")
    return generation_id


def _projection_events(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    window_start_ns: int,
    window_end_ns: int,
) -> list[DerivedEvent]:
    scope = f"generation:{generation_id}"
    events: list[DerivedEvent] = []
    observation_rows = connection.execute(
        """
        SELECT o.id, o.detector_id, o.project_identity_id, p.canonical_path,
               o.fingerprint, o.occurred_at_ns
        FROM observations o
        LEFT JOIN project_identities p ON p.id = o.project_identity_id
        WHERE o.occurred_at_ns >= ? AND o.occurred_at_ns <= ?
        ORDER BY o.occurred_at_ns, o.id
        """,
        (window_start_ns, window_end_ns),
    ).fetchall()
    for row in observation_rows:
        project_id = str(row[2]) if row[2] is not None else "unresolved"
        project_name = Path(str(row[3])).name if row[3] is not None else "unresolved"
        events.append(
            DerivedEvent(
                scope=scope,
                entity_id=str(row[0]),
                entity_version=1,
                event_sequence=1,
                event_name="introspection.observation.detected",
                attributes={
                    "analysis.generation": generation_id,
                    "detector.id": str(row[1]),
                    "project.id": project_id,
                    "project.name": project_name,
                    "finding.id": str(row[4]),
                },
                timestamp_ns=int(row[5]),
            )
        )
    trend_rows = connection.execute(
        """
        SELECT id, entity_version, trend_state, category, project_identity_id,
               detector_id, occurrence_count, last_seen_ns
        FROM findings
        WHERE last_seen_ns >= ? AND last_seen_ns <= ?
        ORDER BY last_seen_ns, id
        """,
        (window_start_ns, window_end_ns),
    ).fetchall()
    project_names = {
        str(row[0]): Path(str(row[1])).name
        for row in connection.execute("SELECT id, canonical_path FROM project_identities")
    }
    for row in trend_rows:
        project_id = str(row[4]) if row[4] is not None else "unresolved"
        version = int(row[1])
        events.append(
            DerivedEvent(
                scope=scope,
                entity_id=str(row[0]),
                entity_version=version,
                event_sequence=version,
                event_name="introspection.trend.evaluated",
                attributes={
                    "analysis.generation": generation_id,
                    "trend.state": str(row[2]),
                    "finding.category": str(row[3]),
                    "project.id": project_id,
                    "project.name": project_names.get(project_id, "unresolved"),
                    "detector.id": str(row[5]),
                    "finding.id": str(row[0]),
                    "occurrence.count": int(row[6]),
                },
                timestamp_ns=int(row[7]),
            )
        )
    return events


def stage_generation(
    connection: sqlite3.Connection,
    *,
    source_contract_fingerprint: str,
    end_time: datetime | None = None,
) -> StagedGeneration:
    """Stage one seven-day projection from already validated SQLite facts."""

    now = end_time or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("generation end_time must be timezone-aware")
    end_ns = int(now.astimezone(UTC).timestamp() * 1_000_000_000)
    start_ns = max(0, end_ns - int(timedelta(days=7).total_seconds() * 1_000_000_000))
    detector_hash, normalization_hash, semantic_hash = _semantic_contract(
        source_contract_fingerprint
    )
    active_id = current_generation_id(connection)
    if active_id is not None:
        current = connection.execute(
            "SELECT semantic_hash FROM analysis_generations WHERE id = ?", (active_id,)
        ).fetchone()
        if current is None:
            raise GenerationError("current analysis generation has no immutable provenance")
        if str(current[0]) == semantic_hash:
            raise GenerationError("active analysis generation already has this semantic contract")
    generation_id = str(uuid.uuid4())
    projection_events = _projection_events(
        connection,
        generation_id=generation_id,
        window_start_ns=start_ns,
        window_end_ns=end_ns,
    )
    created_at = datetime.now(UTC).isoformat()
    try:
        connection.execute("BEGIN IMMEDIATE")
        ordinal = int(
            connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM analysis_generations"
            ).fetchone()[0]
        )
        connection.execute(
            """
            INSERT INTO analysis_generations (
                id, ordinal, window_start_ns, window_end_ns, source_contract_fingerprint,
                detector_contract_hash, normalization_contract_hash, semantic_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generation_id,
                ordinal,
                start_ns,
                end_ns,
                source_contract_fingerprint,
                detector_hash,
                normalization_hash,
                semantic_hash,
                created_at,
            ),
        )
        enqueue_events(connection, projection_events)
        connection.executemany(
            """
            INSERT INTO analysis_generation_event_links (generation_id, event_id, role)
            VALUES (?, ?, 'projection')
            """,
            [(generation_id, event.event_id) for event in projection_events],
        )
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise
    return StagedGeneration(
        generation_id=generation_id,
        ordinal=ordinal,
        window_start_ns=start_ns,
        window_end_ns=end_ns,
        semantic_hash=semantic_hash,
        projection_event_ids=tuple(event.event_id for event in projection_events),
    )


def _linked_references(
    connection: sqlite3.Connection, generation_id: str, role: str
) -> tuple[RemoteEventReference, ...]:
    rows = connection.execute(
        """
        SELECT outbox.event_id, outbox.payload_json
        FROM analysis_generation_event_links link
        JOIN otlp_outbox outbox ON outbox.event_id = link.event_id
        WHERE link.generation_id = ? AND link.role = ?
        ORDER BY outbox.event_id
        """,
        (generation_id, role),
    ).fetchall()
    references: list[RemoteEventReference] = []
    for row in rows:
        payload = json.loads(str(row[1]))
        event_name = payload.get("event.name")
        timestamp_ns = payload.get("timestamp_ns")
        if (
            not isinstance(event_name, str)
            or isinstance(timestamp_ns, bool)
            or not isinstance(timestamp_ns, int)
        ):
            raise GenerationError("linked generation event has an invalid immutable payload")
        references.append(RemoteEventReference(str(row[0]), event_name, timestamp_ns))
    return tuple(references)


def _ensure_delivered(
    connection: sqlite3.Connection,
    event_ids: tuple[str, ...],
    *,
    endpoint: str,
) -> None:
    if not event_ids:
        return
    pending = set(event_ids)
    while pending:
        placeholders = ",".join("?" for _ in pending)
        rows = connection.execute(
            f"SELECT event_id, status FROM otlp_outbox WHERE event_id IN ({placeholders})",
            tuple(sorted(pending)),
        ).fetchall()
        statuses = {str(row[0]): str(row[1]) for row in rows}
        if set(statuses) != pending:
            raise GenerationError("generation references an unavailable outbox event")
        pending = {event_id for event_id, status in statuses.items() if status != "delivered"}
        if not pending:
            return
        drained = drain_outbox(connection, endpoint=endpoint, limit=500)
        if drained["selected"] == 0 or drained["delivered"] == 0:
            raise GenerationError("generation events remain undelivered")


def activate_generation(
    connection: sqlite3.Connection,
    *,
    generation_id: str,
    client: EventQueryClient,
    endpoint: str,
) -> ActivatedGeneration:
    """Promote a staged generation only after local and remote delivery proof."""

    generation = connection.execute(
        "SELECT 1 FROM analysis_generations WHERE id = ?", (generation_id,)
    ).fetchone()
    if generation is None:
        raise KeyError(generation_id)
    existing = connection.execute(
        """
        SELECT activation_event_id FROM analysis_generation_activations
        WHERE generation_id = ?
        """,
        (generation_id,),
    ).fetchone()
    if existing is not None:
        return ActivatedGeneration(
            generation_id,
            str(existing[0]),
            len(_linked_references(connection, generation_id, "projection")),
        )
    projections = _linked_references(connection, generation_id, "projection")
    _ensure_delivered(connection, tuple(event.event_id for event in projections), endpoint=endpoint)
    if remote_event_ids(client, projections) != {event.event_id for event in projections}:
        raise GenerationError("generation projections are not remotely verifiable")
    marker_row = connection.execute(
        """
        SELECT outbox.event_id, outbox.payload_json
        FROM analysis_generation_event_links link
        JOIN otlp_outbox outbox ON outbox.event_id = link.event_id
        WHERE link.generation_id = ? AND link.role = 'activation'
        """,
        (generation_id,),
    ).fetchone()
    if marker_row is None:
        marker = DerivedEvent(
            scope=OPERATIONAL_SCOPE,
            entity_id=generation_id,
            entity_version=1,
            event_sequence=1,
            event_name="introspection.analysis_generation.activated",
            attributes={"analysis.generation": generation_id},
            timestamp_ns=time_ns(),
        )
        marker_event_id = marker.event_id
        marker_reference = RemoteEventReference(
            marker.event_id,
            marker.event_name,
            marker.timestamp_ns,
        )
        with connection:
            enqueue_events(connection, [marker])
            connection.execute(
                """
                INSERT INTO analysis_generation_event_links (generation_id, event_id, role)
                VALUES (?, ?, 'activation')
                """,
                (generation_id, marker.event_id),
            )
    else:
        marker_event_id = str(marker_row[0])
        marker_payload = json.loads(str(marker_row[1]))
        marker_name = marker_payload.get("event.name")
        marker_timestamp = marker_payload.get("timestamp_ns")
        if (
            marker_payload.get("analysis.generation") != generation_id
            or marker_name != "introspection.analysis_generation.activated"
            or isinstance(marker_timestamp, bool)
            or not isinstance(marker_timestamp, int)
        ):
            raise GenerationError("analysis generation activation marker is invalid")
        marker_reference = RemoteEventReference(
            marker_event_id,
            marker_name,
            marker_timestamp,
        )
    _ensure_delivered(connection, (marker_event_id,), endpoint=endpoint)
    if remote_event_ids(client, (marker_reference,)) != {marker_event_id}:
        raise GenerationError("analysis generation activation is not remotely verifiable")
    activated_at = datetime.now(UTC).isoformat()
    with connection:
        connection.execute(
            """
            INSERT INTO analysis_generation_activations (
                generation_id, activation_event_id, activated_at
            ) VALUES (?, ?, ?)
            """,
            (generation_id, marker_event_id, activated_at),
        )
        connection.execute(
            """
            INSERT INTO analysis_generation_current (
                singleton, generation_id, activation_event_id, activated_at
            ) VALUES (1, ?, ?, ?)
            ON CONFLICT(singleton) DO UPDATE SET
                generation_id = excluded.generation_id,
                activation_event_id = excluded.activation_event_id,
                activated_at = excluded.activated_at
            """,
            (generation_id, marker_event_id, activated_at),
        )
    return ActivatedGeneration(generation_id, marker_event_id, len(projections))


def time_ns() -> int:
    """Return a UTC event timestamp without exposing wall-clock implementation details."""

    return int(datetime.now(UTC).timestamp() * 1_000_000_000)
