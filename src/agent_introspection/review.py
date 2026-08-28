"""Bounded, provenance-checked model review sessions."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

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
    return envelope


def _required_output_fields(document: dict[str, Any]) -> None:
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


def _review_session_row(connection: sqlite3.Connection, session_id: object) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT nonce, schema_version, requested_model, requested_effort,
               ordered_candidate_ids_json, payload_hash, purpose, status, reserved_model_budget
        FROM review_sessions WHERE id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError("unknown review session")
    return cast(sqlite3.Row, row)


def _validate_output_identity(document: dict[str, Any], row: sqlite3.Row) -> None:
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


def _validate_model_provenance(provenance: dict[str, Any], row: sqlite3.Row) -> None:
    if provenance.get("model") != row[2] or provenance.get("effort") != row[3]:
        raise ValueError("model provenance mismatch")
    if not provenance.get("trace_id") or not provenance.get("token_count"):
        raise ValueError("model provenance is incomplete")
    if int(provenance["token_count"]) > int(row[8]):
        raise ValueError("model token budget exceeded")


def _validated_model_results(document: dict[str, Any], row: sqlite3.Row) -> list[dict[str, Any]]:
    results = document["results"]
    if not isinstance(results, list):
        raise ValueError("results must be a list")
    expected_ids = json.loads(str(row[4]))
    actual_ids = [result.get("candidate_id") for result in results]
    if actual_ids != expected_ids:
        raise ValueError("model output candidate IDs or ordering mismatch")
    purpose = row[6]
    if purpose not in {"classification", "proposal"}:
        raise ValueError("review session purpose cannot import results")
    limits = LUNA_LIMITS if purpose == "classification" else PROPOSAL_LIMITS
    if len(_canonical_bytes(document).decode()) > limits.max_output_characters:
        raise ValueError("model output exceeds accepted character limit")
    return results


def validate_model_output(
    connection: sqlite3.Connection,
    document: dict[str, Any],
    *,
    provenance: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Validate response envelope and recorded SigNoz model provenance."""
    _required_output_fields(document)
    row = _review_session_row(connection, document["session_id"])
    _validate_output_identity(document, row)
    _validate_model_provenance(provenance, row)
    results = _validated_model_results(document, row)
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
