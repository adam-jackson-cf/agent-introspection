import json
from typing import Any

import pytest

from agent_introspection import classify

PROMPT = "no, you ignored me: use uv, not pip. token=supersecret123"


def jev_response(**overrides: Any) -> dict[str, Any]:
    response = {
        "model": classify.JEV_PINNED_MODEL,
        "answers": {
            "task_type": {"type": "choice", "choice": "ops", "confidence": 0.9},
            "correction": {"type": "noul", "noul": 0.95},
            "correction_kind": {
                "type": "choice",
                "choice": "ignored_instruction",
                "confidence": 0.8,
            },
            "sentiment": {"type": "score", "score": 0, "confidence": 0.7},
        },
        "usage": {"cost": 0.000035},
    }
    return response | overrides


def pending(key: str, attempts: int = 0) -> dict[str, Any]:
    return {
        "harness": "claude-code",
        "prompt_key": key,
        "session_id": "s1",
        "prompt_id": "p1",
        "ts": "2026-09-30 12:00:00.000000000",
        "prompt": PROMPT,
        "attempts": attempts,
    }


class FakeClickHouse:
    def __init__(self, *rows: dict[str, Any]) -> None:
        self.rows = rows
        self.statements: list[str] = []

    def __call__(self, sql: str) -> str:
        self.statements.append(sql)
        if "FROM signoz_logs.distributed_logs_v2" in sql:
            return "\n".join(json.dumps(row) for row in self.rows)
        return ""


def test_run_labels_prompts_and_stores_only_answers() -> None:
    sent: list[bytes] = []

    def post(body: bytes, _key: str) -> dict[str, Any]:
        sent.append(body)
        return jev_response()

    ch = FakeClickHouse(pending("a"))
    result = classify.run(ch, key="k", post=post)

    assert result == {"pending": 1, "labelled": 1, "failed": 0, "cost_usd": 0.000035}
    insert = ch.statements[-1]
    assert insert.startswith("INSERT INTO introspection.prompt_labels")
    assert "ignored_instruction" in insert
    assert "uv, not pip" not in insert
    assert "supersecret123" not in sent[0].decode()
    assert "supersecret123" not in insert


def test_malformed_score_fails_only_its_prompt() -> None:
    def post(body: bytes, _key: str) -> dict[str, Any]:
        bad = json.loads(body)["state"]["message"] == "bad"
        answers = jev_response()["answers"]
        if bad:
            answers["sentiment"]["score"] = "0"
        return jev_response(answers=answers)

    good, broken = pending("a"), pending("b") | {"prompt": "bad"}
    ch = FakeClickHouse(good, broken)
    result = classify.run(ch, key="k", post=post)

    assert (result["labelled"], result["failed"]) == (1, 1)
    assert "unexpected_answers" in ch.statements[-1]


def test_failed_decisions_are_recorded_with_a_reason_and_retried_later() -> None:
    def post(_body: bytes, _key: str) -> dict[str, Any]:
        return jev_response(answers={})

    ch = FakeClickHouse(pending("a", attempts=1))
    result = classify.run(ch, key="k", post=post)

    assert result["failed"] == 1
    row = json.loads(ch.statements[-1].split("Float64', '", 1)[1].rsplit("') SETTINGS", 1)[0])
    assert (row["status"], row["reason"], row["attempts"]) == ("failed", "unexpected_answers", 2)
    assert row["task_type"] == ""


def test_pending_query_skips_placeholders_labelled_and_exhausted_prompts() -> None:
    query = classify.PENDING_QUERY.format(
        days=2, limit=10, database="introspection", max_attempts=classify.MAX_ATTEMPTS
    )
    assert "REDACTED" in query
    assert "coalesce(l.done, 0) = 0" in query
    assert "coalesce(l.attempts, 0) < 3" in query
    for event in ("'user_prompt'", "'codex.user_prompt'", "'omp.user_prompt'"):
        assert event in query


def test_no_pending_prompts_needs_no_key() -> None:
    assert classify.run(FakeClickHouse(), key=None)["pending"] == 0


def test_unexpected_model_stops_the_run() -> None:
    with pytest.raises(classify.JevUnavailableError):
        classify.run(
            FakeClickHouse(pending("a")),
            key="k",
            post=lambda _b, _k: jev_response(model="other/model"),
        )
