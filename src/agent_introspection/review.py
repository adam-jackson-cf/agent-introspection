"""Bounded, provenance-checked model review sessions."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from agent_introspection.telemetry import REVIEW_SCOPE, DerivedEvent, enqueue_events

LUNA_MODEL = "gpt-5.6-luna"
LUNA_EFFORT = "medium"
PROPOSAL_MODEL = "gpt-5.5"
PROPOSAL_EFFORT = "high"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReviewLimits:
    max_items: int
    max_calls: int
    items_per_call: int
    max_input_characters: int
    max_output_characters: int


LUNA_LIMITS = ReviewLimits(40, 4, 10, 24_000, 8_000)
PROPOSAL_LIMITS = ReviewLimits(8, 8, 1, 48_000, 16_000)
COMBINED_CALL_LIMIT = 12
REVIEW_SESSION_CHANGED_EVENT = "introspection.review.session_changed"
REVIEW_ACTIVITY_SNAPSHOT_EVENT = "introspection.review.activity_snapshot"
REVIEW_ACTIVITY_ENTITY_ID = "review-activity"


@dataclass(frozen=True)
class ReviewEnvelope:
    session_id: str
    batch_id: str
    nonce: str
    schema_version: int
    requested_model: str
    requested_effort: str
    ordered_candidate_ids: tuple[str, ...]
    payload_hash: str
    byte_count: int
    reserved_model_budget: int
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "batch_id": self.batch_id,
            "nonce": self.nonce,
            "schema_version": self.schema_version,
            "requested_model": self.requested_model,
            "requested_effort": self.requested_effort,
            "ordered_candidate_ids": list(self.ordered_candidate_ids),
            "payload_hash": self.payload_hash,
            "byte_count": self.byte_count,
            "reserved_model_budget": self.reserved_model_budget,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class TokenUsage:
    """Nullable, source-backed token fields for one accepted review run."""

    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    availability: Literal["complete", "partial", "unavailable"]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _timestamp_ns(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _parse_token_component(provenance: dict[str, Any], field: str) -> int | None:
    value = provenance.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer when available")
    return value


def _token_usage(provenance: dict[str, Any]) -> TokenUsage:
    input_tokens = _parse_token_component(provenance, "input_tokens")
    output_tokens = _parse_token_component(provenance, "output_tokens")
    reasoning_tokens = _parse_token_component(provenance, "reasoning_tokens")
    supplied_total = _parse_token_component(provenance, "total_tokens")
    components = (input_tokens, output_tokens, reasoning_tokens)
    if all(value is not None for value in components):
        total_tokens = sum(value for value in components if value is not None)
        if supplied_total is not None and supplied_total != total_tokens:
            raise ValueError("total_tokens must equal known token components")
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=total_tokens,
            availability="complete",
        )
    if any(value is not None for value in components):
        if supplied_total is not None:
            raise ValueError("total_tokens is unavailable when token components are partial")
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            total_tokens=None,
            availability="partial",
        )
    if supplied_total is not None:
        raise ValueError("total_tokens is unavailable without token components")
    return TokenUsage(
        input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        total_tokens=None,
        availability="unavailable",
    )


def _session_changed_event(
    *,
    session_id: str,
    entity_version: int,
    purpose: str,
    status: Literal["exported", "imported"],
    candidate_count: int,
    timestamp: datetime,
    result_count: int | None = None,
    token_usage: TokenUsage | None = None,
) -> DerivedEvent:
    attributes: dict[str, str | int | float | bool] = {
        "review.purpose": purpose,
        "review.status": status,
        "review.candidate.count": candidate_count,
        "review.token.availability": (
            "not_applicable" if token_usage is None else token_usage.availability
        ),
    }
    if result_count is not None:
        attributes["review.result.count"] = result_count
    if token_usage is not None:
        for attribute, value in (
            ("review.token.input", token_usage.input_tokens),
            ("review.token.output", token_usage.output_tokens),
            ("review.token.reasoning", token_usage.reasoning_tokens),
            ("review.token.total", token_usage.total_tokens),
        ):
            if value is not None:
                attributes[attribute] = value
    return DerivedEvent(
        scope=REVIEW_SCOPE,
        entity_id=session_id,
        entity_version=entity_version,
        event_sequence=entity_version,
        event_name=REVIEW_SESSION_CHANGED_EVENT,
        attributes=attributes,
        timestamp_ns=_timestamp_ns(timestamp),
    )


def _review_activity_counts(connection: sqlite3.Connection) -> tuple[int, int, int, int]:
    session_rows = connection.execute(
        """
        SELECT purpose, COUNT(*)
        FROM review_sessions
        WHERE status = 'imported' AND purpose IN ('classification', 'proposal')
        GROUP BY purpose
        """
    ).fetchall()
    session_counts = {str(row[0]): int(row[1]) for row in session_rows}
    classification_results = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM semantic_classifications classification
            JOIN review_sessions session ON session.id = classification.review_session_id
            WHERE session.status = 'imported' AND session.purpose = 'classification'
            """
        ).fetchone()[0]
    )
    proposal_results = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM proposal_drafts draft
            JOIN review_sessions session ON session.id = draft.review_session_id
            WHERE session.status = 'imported' AND session.purpose = 'proposal'
            """
        ).fetchone()[0]
    )
    return (
        session_counts.get("classification", 0),
        session_counts.get("proposal", 0),
        classification_results,
        proposal_results,
    )


def _review_activity_snapshot_event(
    *,
    entity_version: int,
    trigger_kind: Literal["review_session", "scan_run"],
    classification_session_count: int,
    proposal_session_count: int,
    classification_result_count: int,
    proposal_result_count: int,
    timestamp: datetime,
) -> DerivedEvent:
    return DerivedEvent(
        scope=REVIEW_SCOPE,
        entity_id=REVIEW_ACTIVITY_ENTITY_ID,
        entity_version=entity_version,
        event_sequence=entity_version,
        event_name=REVIEW_ACTIVITY_SNAPSHOT_EVENT,
        attributes={
            "review.activity.availability": "available",
            "review.classification.session_count": classification_session_count,
            "review.proposal.session_count": proposal_session_count,
            "review.classification.result_count": classification_result_count,
            "review.proposal.result_count": proposal_result_count,
            "snapshot.trigger.kind": trigger_kind,
        },
        timestamp_ns=_timestamp_ns(timestamp),
    )


def record_review_activity_snapshot(
    connection: sqlite3.Connection,
    *,
    trigger_kind: Literal["review_session", "scan_run"],
    trigger_id: str,
    trigger_version: int,
    timestamp: datetime,
) -> DerivedEvent:
    """Persist one immutable current-review aggregate and return its derived event."""
    existing = connection.execute(
        """
        SELECT entity_version, trigger_kind, classification_session_count,
               proposal_session_count, classification_result_count, proposal_result_count,
               created_at, id
        FROM review_activity_snapshots
        WHERE trigger_kind = ? AND trigger_id = ? AND trigger_version = ?
        """,
        (trigger_kind, trigger_id, trigger_version),
    ).fetchone()
    if existing is not None:
        existing_timestamp = datetime.fromisoformat(str(existing[6]))
        existing_trigger_kind = str(existing[1])
        if existing_trigger_kind not in {"review_session", "scan_run"}:
            raise RuntimeError("review activity snapshot has an invalid trigger kind")
        valid_trigger_kind: Literal["review_session", "scan_run"] = (
            "review_session" if existing_trigger_kind == "review_session" else "scan_run"
        )
        event = _review_activity_snapshot_event(
            entity_version=int(existing[0]),
            trigger_kind=valid_trigger_kind,
            classification_session_count=int(existing[2]),
            proposal_session_count=int(existing[3]),
            classification_result_count=int(existing[4]),
            proposal_result_count=int(existing[5]),
            timestamp=existing_timestamp,
        )
        if event.event_id != str(existing[7]):
            raise RuntimeError("review activity snapshot identity mismatch")
        return event
    entity_version = int(
        connection.execute(
            "SELECT COALESCE(MAX(entity_version), 0) + 1 FROM review_activity_snapshots"
        ).fetchone()[0]
    )
    (
        classification_session_count,
        proposal_session_count,
        classification_result_count,
        proposal_result_count,
    ) = _review_activity_counts(connection)
    event = _review_activity_snapshot_event(
        entity_version=entity_version,
        trigger_kind=trigger_kind,
        classification_session_count=classification_session_count,
        proposal_session_count=proposal_session_count,
        classification_result_count=classification_result_count,
        proposal_result_count=proposal_result_count,
        timestamp=timestamp,
    )
    connection.execute(
        """
        INSERT INTO review_activity_snapshots (
            id, entity_version, trigger_kind, trigger_id, trigger_version,
            classification_session_count, proposal_session_count,
            classification_result_count, proposal_result_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.event_id,
            entity_version,
            trigger_kind,
            trigger_id,
            trigger_version,
            classification_session_count,
            proposal_session_count,
            classification_result_count,
            proposal_result_count,
            timestamp.isoformat(),
        ),
    )
    return event


def create_review_session(
    connection: sqlite3.Connection,
    *,
    kind: Literal["classification", "proposal"],
    candidates: list[dict[str, Any]],
    reserved_model_budget: int,
    batch_id: str | None = None,
) -> ReviewEnvelope:
    """Create and reserve one bounded review session."""
    purpose = kind
    limits = LUNA_LIMITS if purpose == "classification" else PROPOSAL_LIMITS
    model = LUNA_MODEL if purpose == "classification" else PROPOSAL_MODEL
    effort = LUNA_EFFORT if purpose == "classification" else PROPOSAL_EFFORT
    if not 0 < len(candidates) <= limits.items_per_call:
        raise ValueError(f"candidate count per call must be between 1 and {limits.items_per_call}")
    ids = tuple(str(candidate["id"]) for candidate in candidates)
    if len(set(ids)) != len(ids):
        raise ValueError("candidate IDs must be unique")
    payload = {"purpose": purpose, "candidates": candidates}
    raw = _canonical_bytes(payload)
    if len(raw.decode()) > limits.max_input_characters:
        raise ValueError("review payload exceeds input character limit")
    if reserved_model_budget <= 0:
        raise ValueError("reserved model budget must be positive")
    envelope = ReviewEnvelope(
        session_id=str(uuid.uuid4()),
        batch_id=batch_id or str(uuid.uuid4()),
        nonce=secrets.token_urlsafe(32),
        schema_version=SCHEMA_VERSION,
        requested_model=model,
        requested_effort=effort,
        ordered_candidate_ids=ids,
        payload_hash=hashlib.sha256(raw).hexdigest(),
        byte_count=len(raw),
        reserved_model_budget=reserved_model_budget,
        payload=payload,
    )
    now = datetime.now(UTC).isoformat()
    with connection:
        prior_rows = connection.execute(
            """
            SELECT purpose, ordered_candidate_ids_json
            FROM review_sessions WHERE batch_id = ?
            """,
            (envelope.batch_id,),
        ).fetchall()
        if len(prior_rows) >= COMBINED_CALL_LIMIT:
            raise RuntimeError("combined model call limit exhausted")
        same_kind = [row for row in prior_rows if row[0] == purpose]
        if len(same_kind) >= limits.max_calls:
            raise RuntimeError(f"{purpose} model call limit exhausted")
        prior_candidate_count = sum(len(json.loads(row[1])) for row in same_kind)
        if prior_candidate_count + len(candidates) > limits.max_items:
            raise RuntimeError(f"{kind} candidate limit exhausted")
        connection.execute(
            """
            INSERT INTO review_sessions (
                id, batch_id, nonce, schema_version, purpose, requested_model, requested_effort,
                ordered_candidate_ids_json, payload_hash, byte_count,
                reserved_model_budget, status, entity_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'exported', 1, ?)
            """,
            (
                envelope.session_id,
                envelope.batch_id,
                envelope.nonce,
                envelope.schema_version,
                purpose,
                model,
                effort,
                json.dumps(ids),
                envelope.payload_hash,
                envelope.byte_count,
                reserved_model_budget,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO model_budget_ledger (
                id, review_session_id, entry_type, amount, created_at
            ) VALUES (?, ?, 'reserved', ?, ?)
            """,
            (str(uuid.uuid4()), envelope.session_id, reserved_model_budget, now),
        )
        timestamp = datetime.fromisoformat(now)
        session_event = _session_changed_event(
            session_id=envelope.session_id,
            entity_version=1,
            purpose=purpose,
            status="exported",
            candidate_count=len(ids),
            timestamp=timestamp,
        )
        connection.execute(
            """
            INSERT INTO review_session_events (
                id, review_session_id, entity_version, status, review_run_id, created_at
            ) VALUES (?, ?, 1, 'exported', NULL, ?)
            """,
            (session_event.event_id, envelope.session_id, now),
        )
        activity_snapshot = record_review_activity_snapshot(
            connection,
            trigger_kind="review_session",
            trigger_id=envelope.session_id,
            trigger_version=1,
            timestamp=timestamp,
        )
        enqueue_events(connection, [session_event, activity_snapshot])
    return envelope


def validate_model_output(
    connection: sqlite3.Connection,
    document: dict[str, Any],
    *,
    provenance: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Validate response envelope and recorded SigNoz model provenance."""
    required = {
        "session_id",
        "nonce",
        "schema_version",
        "payload_hash",
        "requested_model",
        "requested_effort",
        "results",
    }
    missing = required - document.keys()
    if missing:
        raise ValueError(f"missing model output fields: {sorted(missing)}")
    row = connection.execute(
        """
        SELECT nonce, schema_version, requested_model, requested_effort,
               ordered_candidate_ids_json, payload_hash, purpose, status, reserved_model_budget
        FROM review_sessions WHERE id = ?
        """,
        (document["session_id"],),
    ).fetchone()
    if row is None:
        raise ValueError("unknown review session")
    expected = {
        "nonce": row[0],
        "schema_version": row[1],
        "requested_model": row[2],
        "requested_effort": row[3],
        "payload_hash": row[5],
    }
    for key, value in expected.items():
        if document[key] != value:
            raise ValueError(f"model output {key} mismatch")
    if row[7] != "exported":
        raise ValueError("review session has already been imported")
    if provenance.get("model") != row[2] or provenance.get("effort") != row[3]:
        raise ValueError("model provenance mismatch")
    if not provenance.get("trace_id") or not provenance.get("token_count"):
        raise ValueError("model provenance is incomplete")
    if int(provenance["token_count"]) > int(row[8]):
        raise ValueError("model token budget exceeded")
    results = document["results"]
    if not isinstance(results, list):
        raise ValueError("results must be a list")
    expected_ids = json.loads(row[4])
    actual_ids = [result.get("candidate_id") for result in results]
    if actual_ids != expected_ids:
        raise ValueError("model output candidate IDs or ordering mismatch")
    if row[6] not in {"classification", "proposal"}:
        raise ValueError("review session purpose cannot import results")
    limits = LUNA_LIMITS if row[6] == "classification" else PROPOSAL_LIMITS
    if len(_canonical_bytes(document).decode()) > limits.max_output_characters:
        raise ValueError("model output exceeds accepted character limit")
    return str(row[6]), results


def import_model_output(
    connection: sqlite3.Connection,
    document: dict[str, Any],
    *,
    provenance: dict[str, Any],
) -> None:
    """Persist validated model output and consume its reserved budget."""
    purpose, results = validate_model_output(connection, document, provenance=provenance)
    token_usage = _token_usage(provenance)
    now = datetime.now(UTC).isoformat()
    timestamp = datetime.fromisoformat(now)
    with connection:
        run_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO model_runs (
                id, review_session_id, model, effort, trace_id, input_tokens,
                output_tokens, reasoning_tokens, total_tokens, token_availability,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)
            """,
            (
                run_id,
                document["session_id"],
                provenance["model"],
                provenance["effort"],
                provenance["trace_id"],
                token_usage.input_tokens,
                token_usage.output_tokens,
                token_usage.reasoning_tokens,
                token_usage.total_tokens,
                token_usage.availability,
                now,
            ),
        )
        table = "semantic_classifications" if purpose == "classification" else "proposal_drafts"
        for result in results:
            connection.execute(
                f"INSERT INTO {table} "
                "(id, review_session_id, candidate_id, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    document["session_id"],
                    result["candidate_id"],
                    json.dumps(result, sort_keys=True, separators=(",", ":")),
                    now,
                ),
            )
        connection.execute(
            """
            UPDATE review_sessions
            SET status = 'imported', imported_at = ?, entity_version = 2
            WHERE id = ?
            """,
            (now, document["session_id"]),
        )
        connection.execute(
            """
            INSERT INTO model_budget_ledger (
                id, review_session_id, entry_type, amount, created_at
            ) VALUES (?, ?, 'consumed', ?, ?)
            """,
            (str(uuid.uuid4()), document["session_id"], -int(provenance["token_count"]), now),
        )
        session_event = _session_changed_event(
            session_id=str(document["session_id"]),
            entity_version=2,
            purpose=purpose,
            status="imported",
            candidate_count=len(results),
            result_count=len(results),
            token_usage=token_usage,
            timestamp=timestamp,
        )
        connection.execute(
            """
            INSERT INTO review_session_events (
                id, review_session_id, entity_version, status, review_run_id, created_at
            ) VALUES (?, ?, 2, 'imported', ?, ?)
            """,
            (session_event.event_id, document["session_id"], run_id, now),
        )
        activity_snapshot = record_review_activity_snapshot(
            connection,
            trigger_kind="review_session",
            trigger_id=str(document["session_id"]),
            trigger_version=2,
            timestamp=timestamp,
        )
        enqueue_events(connection, [session_event, activity_snapshot])
