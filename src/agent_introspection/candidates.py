"""Export the highest-impact finding as a bounded review candidate.

A candidate is one actionable failure cluster without a proposal, chosen by impact
(then the most recent occurrence), carrying its evidence pack.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_introspection import evidence
from agent_introspection.facts import SqlRunner
from agent_introspection.findings import DETECTOR_ID
from agent_introspection.review import PROPOSAL_LIMITS, PURPOSE, create_review_session

_FINDING_FIELDS = (
    "id",
    "category",
    "trend_state",
    "fingerprint",
    "detector_id",
    "first_seen_ns",
    "last_seen_ns",
    "occurrence_count",
    "canonical_task_count",
    "local_day_count",
    "subject",
)

CANDIDATE_QUERY = f"""
SELECT {", ".join(f"f.{field}" for field in _FINDING_FIELDS)}
FROM findings f
LEFT JOIN proposals p ON p.finding_id = f.id
WHERE f.trend_state = 'actionable' AND f.is_active = 1
    AND f.detector_id = ?
    AND p.id IS NULL
ORDER BY COALESCE(CAST(json_extract(f.subject, '$.impact') AS INTEGER), 0) DESC,
    f.last_seen_ns DESC, f.id
LIMIT 1
"""


def next_finding(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the highest-impact actionable failure cluster that has no proposal."""
    row = connection.execute(CANDIDATE_QUERY, (DETECTOR_ID,)).fetchone()
    if row is None:
        return None
    finding = dict(zip(_FINDING_FIELDS, row, strict=True))
    finding["subject"] = json.loads(finding["subject"]) if finding["subject"] else {}
    return finding


def _size(candidate: dict[str, Any]) -> int:
    payload = {"purpose": PURPOSE, "candidates": [candidate]}
    return len(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def fit_candidate(candidate: dict[str, Any], limit: int) -> dict[str, Any]:
    """Drop trailing rows from the largest evidence lists until the payload fits ``limit``.

    Dropped row counts are recorded under ``evidence.trimmed`` so the review knows the
    pack is partial. Raises ``ValueError`` when no evidence rows are left to drop.
    """
    pack = candidate["evidence"]
    trimmed: dict[str, int] = {}
    while _size(candidate) > limit:
        lists = [key for key, value in pack.items() if isinstance(value, list) and value]
        if not lists:
            raise ValueError("review candidate exceeds the input character limit")
        largest = max(lists, key=lambda key: len(json.dumps(pack[key])))
        pack[largest] = pack[largest][:-1]
        trimmed[largest] = trimmed.get(largest, 0) + 1
        pack["trimmed"] = dict(sorted(trimmed.items()))
    return candidate


def build_candidate(run: SqlRunner, finding: dict[str, Any]) -> dict[str, Any]:
    """Assemble one bounded candidate: finding fields and evidence pack."""
    pack = evidence.pack(run, finding["subject"])
    candidate = {
        "id": str(finding["id"]),
        "finding": {key: value for key, value in finding.items() if key != "id"},
        "evidence": pack,
    }
    return fit_candidate(candidate, PROPOSAL_LIMITS.max_input_characters)


def export(
    connection: sqlite3.Connection,
    run: SqlRunner,
    *,
    reserved_model_budget: int,
    batch_id: str | None = None,
) -> dict[str, Any]:
    """Reserve one review session for the next candidate, or report that none is due."""
    finding = next_finding(connection)
    if finding is None:
        return {"status": "no_candidates"}
    envelope = create_review_session(
        connection,
        candidates=[build_candidate(run, finding)],
        reserved_model_budget=reserved_model_budget,
        batch_id=batch_id,
    )
    return {"status": "exported", "review": envelope.as_dict()}
