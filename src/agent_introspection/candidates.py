"""Export the highest-impact finding as a bounded review candidate.

A candidate is one actionable finding from a current detector (a failure cluster or
a repeated correction) without a proposal, chosen by impact (then the most recent
occurrence), carrying its evidence pack and the root of the project where it
happened most, so a drafting run can audit that project.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from agent_introspection import evidence
from agent_introspection.facts import DATABASE, SqlRunner, sql_string
from agent_introspection.findings import CORRECTION_DETECTOR_ID, DETECTOR_ID
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
DETECTORS = (DETECTOR_ID, CORRECTION_DETECTOR_ID)

CANDIDATE_QUERY = f"""
SELECT {", ".join(f"f.{field}" for field in _FINDING_FIELDS)}
FROM findings f
LEFT JOIN proposals p ON p.finding_id = f.id
WHERE f.trend_state = 'actionable' AND f.is_active = 1
    AND f.detector_id IN ({", ".join("?" for _ in DETECTORS)})
    AND p.id IS NULL
ORDER BY COALESCE(CAST(json_extract(f.subject, '$.impact') AS INTEGER), 0) DESC,
    f.last_seen_ns DESC, f.id
LIMIT 1
"""


def next_finding(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the highest-impact actionable failure cluster that has no proposal."""
    row = connection.execute(CANDIDATE_QUERY, DETECTORS).fetchone()
    if row is None:
        return None
    finding = dict(zip(_FINDING_FIELDS, row, strict=True))
    finding["subject"] = json.loads(finding["subject"]) if finding["subject"] else {}
    return finding


def project_root(run: SqlRunner, subject: dict[str, Any], pack: dict[str, Any]) -> str | None:
    """Return the most frequent root of the finding's project.

    A correction finding names its project; a failure cluster uses the project of
    its evidence pack with the most failures.
    """
    names = [str(subject["project"])] if subject.get("project") else []
    names += [
        str(entry["project"])
        for entry in pack.get("projects", [])
        if entry.get("project") and entry["project"] != "unattributed"
    ]
    if not names:
        return None
    sql = (
        f"SELECT project_root, count() AS sessions FROM {DATABASE}.session_project "
        f"WHERE project = {sql_string(names[0])} AND project_root != '' "
        "GROUP BY project_root ORDER BY sessions DESC, project_root LIMIT 1 FORMAT JSONEachRow"
    )
    rows = [json.loads(line) for line in run(sql).splitlines() if line]
    return str(rows[0]["project_root"]) if rows else None


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
    """Assemble one bounded candidate: finding fields, evidence pack, and project root."""
    pack = evidence.pack(run, finding["subject"])
    candidate = {
        "id": str(finding["id"]),
        "finding": {key: value for key, value in finding.items() if key != "id"},
        "project_root": project_root(run, finding["subject"], pack),
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
