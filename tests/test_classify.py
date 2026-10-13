import io
import json
import subprocess
import urllib.error
import urllib.request
from collections.abc import Sequence
from email.message import Message
from typing import Any

import pytest

from agent_introspection import classify, cli_facts
from agent_introspection.config import parse_config

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
    result = classify.run(ch, key=lambda: "k", post=post)

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
    result = classify.run(ch, key=lambda: "k", post=post)

    assert (result["labelled"], result["failed"]) == (1, 1)
    assert "unexpected_answers" in ch.statements[-1]


def test_failed_decisions_are_recorded_with_a_reason_and_retried_later() -> None:
    def post(_body: bytes, _key: str) -> dict[str, Any]:
        return jev_response(answers={})

    ch = FakeClickHouse(pending("a", attempts=1))
    result = classify.run(ch, key=lambda: "k", post=post)

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
    def no_key() -> str:
        pytest.fail("the key was asked for with nothing to label")

    assert classify.run(FakeClickHouse(), key=no_key)["pending"] == 0


def test_unexpected_model_stops_the_run() -> None:
    with pytest.raises(classify.JevUnavailableError):
        classify.run(
            FakeClickHouse(pending("a")),
            key=lambda: "k",
            post=lambda _b, _k: jev_response(model="other/model"),
        )


@pytest.mark.parametrize(
    ("status", "error"), [(403, classify.JevUnavailableError), (500, RuntimeError)]
)
def test_http_poster_closes_the_error_response_and_withholds_its_body(
    monkeypatch: pytest.MonkeyPatch, status: int, error: type[Exception]
) -> None:
    body = io.BytesIO(b"echoed prompt text")

    def failing(request: object, timeout: float) -> None:
        raise urllib.error.HTTPError("http://jev", status, "err", Message(), body)

    monkeypatch.setattr(urllib.request, "urlopen", failing)

    with pytest.raises(error, match=str(status)) as raised:
        classify.http_poster(b"{}", "key")
    assert "echoed" not in str(raised.value)
    assert body.closed


class KeyCommands:
    """A stand-in for the key command seam; ``outcomes`` maps argv[0] to its result."""

    def __init__(self, **outcomes: subprocess.CompletedProcess[str] | Exception) -> None:
        self.outcomes = outcomes
        self.ran: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.ran.append(tuple(command))
        outcome = self.outcomes[command[0]]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def printed(key: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess((), code, key, "stderr text")


def test_the_key_comes_from_the_environment_then_the_command_then_omp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = KeyCommands(security=printed("command-key\n"), omp=printed("omp-key\n"))
    monkeypatch.setattr(classify, "_run_key_command", commands)
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")

    assert classify.api_key(("security", "-w")) == "env-key"
    assert commands.ran == []

    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert classify.api_key(("security", "-w")) == "command-key"
    assert classify.api_key() == "omp-key"
    assert commands.ran == [("security", "-w"), classify.OMP_KEY_COMMAND]


@pytest.mark.parametrize(
    "outcome",
    [
        printed("leaked-key", code=3),
        printed("  \n"),
        OSError("not found"),
        subprocess.TimeoutExpired("security", 10, output="leaked-key"),
    ],
)
def test_a_failing_key_command_is_an_error_without_its_output_or_an_omp_fallback(
    monkeypatch: pytest.MonkeyPatch, outcome: subprocess.CompletedProcess[str] | Exception
) -> None:
    commands = KeyCommands(security=outcome, omp=printed("omp-key"))
    monkeypatch.setattr(classify, "_run_key_command", commands)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(classify.JevUnavailableError, match="openrouter_key_command") as raised:
        classify.api_key(("security", "-w"))
    assert "leaked" not in str(raised.value)
    assert "stderr" not in str(raised.value)
    assert commands.ran == [("security", "-w")]


def test_no_key_source_names_every_way_to_provide_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classify, "_run_key_command", KeyCommands(omp=OSError("no omp")))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(classify.JevUnavailableError) as raised:
        classify.api_key()
    for source in ("OPENROUTER_API_KEY", "[labels] openrouter_key_command", "omp"):
        assert source in str(raised.value)


def test_labels_resolve_the_key_with_the_configured_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[Sequence[str] | None] = []

    def run(_run_sql: object, *, days: int, limit: int, key: classify.KeySource) -> dict[str, Any]:
        return {"key": key(), "days": days, "limit": limit}

    def api_key(key_command: Sequence[str] | None = None) -> str:
        asked.append(key_command)
        return "k"

    monkeypatch.setattr(classify, "run", run)
    monkeypatch.setattr(classify, "api_key", api_key)
    config = parse_config({"labels": {"openrouter_key_command": ["security", "-w"]}})

    labels = cli_facts._labels(FakeClickHouse(), config, days=5, limit=7)
    assert labels == {"key": "k", "days": 5, "limit": 7}
    assert asked == [("security", "-w")]
