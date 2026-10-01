"""Prompt labels from the Jev decision model.

Claude Code (`OTEL_LOG_USER_PROMPTS=1`), Codex (`[otel] log_user_prompt = true`), and
the omp activity extension (an `omp.user_prompt` OTLP log) export each submitted
prompt to SigNoz. ``run`` (part of ``facts sync``) reads prompts that have no label
yet from SigNoz, asks Jev (TypeSafe System One, through OpenRouter's decisions API)
the fixed questions below, and stores only the answers in
``introspection.prompt_labels``: task type, whether the prompt corrects the agent's
previous work and how, and the user's sentiment. The facts loader drops prompt text,
so it stays only in SigNoz (90-day retention); it is redacted and clipped before it
is sent to Jev. A prompt whose decision fails is retried on later runs, at most
``MAX_ATTEMPTS`` times.

Jev's resolved model is pinned; a different version raises ``JevUnavailableError``
until the questions are re-evaluated against it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from agent_introspection.facts import DATABASE, SqlRunner
from agent_introspection.projects import insert_statements

JEV_MODEL = "~typesafe/jev-latest"
# OpenRouter rejects versioned selectors, so the resolved model is checked instead.
JEV_PINNED_MODEL = "typesafe/jev-1.13-20260917"
JEV_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
CLASSIFIER_VERSION = 1
PROMPT_CHAR_LIMIT = 12_000
MAX_ATTEMPTS = 3
WORKERS = 4
TIMEOUT_SECONDS = 15.0

TASK_TYPES = {
    "bugfix": "Fix a defect, error, failing test, or broken behaviour.",
    "feature": "Add new behaviour or a new capability.",
    "refactor": "Restructure, clean up, or migrate code without changing behaviour.",
    "investigate": "Explain, research, diagnose, or explore, without asking for a change.",
    "review": "Review, audit, or assess existing work.",
    "ops": "Run, install, configure, deploy, or operate tools, services, or environments.",
    "docs": "Write or edit documentation, plans, or other prose.",
    "continue": "Approve, acknowledge, or tell the agent to carry on, with no new task.",
}
CORRECTION_KINDS = {
    "none": "The prompt does not correct the agent.",
    "ignored_instruction": (
        "The agent ignored or broke an instruction, rule, or convention it was given."
    ),
    "wrong_approach": "The agent used the wrong method, tool, or command.",
    "wrong_scope": "The agent changed too much, too little, or the wrong files or area.",
    "incomplete": "The agent stopped early or left required work undone.",
    "incorrect_result": "The agent's result is wrong, broken, or does not work.",
}
SENTIMENTS = ("frustrated", "neutral", "satisfied")

QUESTIONS: dict[str, dict[str, Any]] = {
    "task_type": {
        "type": "choice",
        "instructions": "What kind of work does this message ask a coding agent to do?",
        "criteria": TASK_TYPES,
    },
    "correction": {
        "type": "noul",
        "instructions": (
            "Does this message say the agent's previous work was wrong, incomplete, broke "
            "an instruction, or was not what was asked, and ask for it to be changed or redone?"
        ),
    },
    "correction_kind": {
        "type": "choice",
        "instructions": "If the message corrects the agent, what went wrong?",
        "criteria": CORRECTION_KINDS,
    },
    "sentiment": {
        "type": "score",
        "instructions": "How does the user feel about the agent's work, as expressed here?",
        "criteria": [
            "Frustrated, annoyed, or impatient.",
            "Neutral or matter-of-fact.",
            "Satisfied, pleased, or appreciative.",
        ],
    },
}


_SECRETS = (
    (
        re.compile(
            r"\b(?:sk-[\w-]{8,}|gh[pousr]_[\w-]{8,}|github_pat_[\w-]{8,}|AKIA[0-9A-Z]{16})\b"
        ),
        "[REDACTED]",
    ),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"\b(Bearer\s+)[\w~+/-]+(?:\.[\w~+/-]+)*", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"), "[REDACTED]"),
    (
        re.compile(
            r"\b((?:[\w.-]*(?:api[_-]?key|token|secret|password|passwd|authorization))"
            r"\s*(?:[:=]|\bis\b)\s*['\"]?)[^\s'\",;]+",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]",
    ),
)

type Poster = Callable[[bytes, str], dict[str, Any]]


class JevUnavailableError(RuntimeError):
    """Jev cannot answer at all (no key, no credit, or an unexpected model)."""


class DecisionError(RuntimeError):
    """One decision failed; ``reason`` is a short code that carries no prompt text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def redact(text: str) -> str:
    """Remove credentials and keys, following the omp Jev client's redaction."""
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


def clip(text: str, limit: int = PROMPT_CHAR_LIMIT) -> str:
    """Keep the head and a short tail of long text, with a visible marker."""
    if len(text) <= limit:
        return text
    tail = min(limit // 5, 2_000)
    head = limit - tail
    return f"{text[:head]}\n…[{len(text) - head - tail} characters omitted]…\n{text[-tail:]}"


def api_key() -> str:
    """Return the OpenRouter key from the environment or omp's stored credential."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    missing = "no OpenRouter key: set OPENROUTER_API_KEY or log in to omp"
    try:
        result = subprocess.run(
            ["omp", "token", "openrouter"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JevUnavailableError(missing) from exc
    key = result.stdout.strip()
    if result.returncode != 0 or not key:
        raise JevUnavailableError(missing)
    return key


def http_poster(body: bytes, key: str) -> dict[str, Any]:
    """POST one decision request; HTTP errors carry no provider body (it can echo input)."""
    request = urllib.request.Request(
        JEV_ENDPOINT,
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return dict(json.load(response))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 402, 403):
            raise JevUnavailableError(
                f"OpenRouter refused the key or credit (HTTP {exc.code})"
            ) from None
        raise RuntimeError(f"Jev returned HTTP {exc.code}") from None


def label(answers: dict[str, Any], cost: float) -> dict[str, Any]:
    """Flatten Jev's answers into the ``prompt_label`` hook-event attributes."""
    task_type = answers["task_type"]
    sentiment = answers["sentiment"]
    return {
        "task_type": task_type["choice"],
        "task_type_confidence": float(task_type["confidence"]),
        "correction": float(answers["correction"]["noul"]),
        "correction_kind": answers["correction_kind"]["choice"],
        "sentiment": SENTIMENTS[int(sentiment["score"])],
        "sentiment_confidence": float(sentiment["confidence"]),
        "classifier_model": JEV_PINNED_MODEL,
        "classifier_version": CLASSIFIER_VERSION,
        "cost_usd": cost,
    }


def _answers_match(answers: dict[str, Any]) -> bool:
    for question_id, question in QUESTIONS.items():
        given = answers.get(question_id)
        if not isinstance(given, dict) or given.get("type") != question["type"]:
            return False
        if question["type"] == "choice" and given.get("choice") not in question["criteria"]:
            return False
        if question["type"] == "score" and not 0 <= given.get("score", -1) < len(
            question["criteria"]
        ):
            return False
    return True


def decide(harness: str, prompt: str, key: str, post: Poster) -> dict[str, Any]:
    """Ask Jev about one prompt and return its label attributes.

    Raises ``JevUnavailableError`` when Jev cannot answer at all and
    ``DecisionError`` when this one decision failed. Neither message carries text.
    """
    state = {"harness": harness, "message": clip(redact(prompt))}
    body = json.dumps({"model": JEV_MODEL, "state": state, "questions": QUESTIONS}).encode()
    try:
        response = post(body, key)
    except JevUnavailableError:
        raise
    except (RuntimeError, OSError, ValueError) as exc:
        raise DecisionError("request_failed") from exc
    if response.get("model") != JEV_PINNED_MODEL:
        raise JevUnavailableError("Jev resolved to an unexpected model; re-evaluate the questions")
    answers = response.get("answers", {})
    if not isinstance(answers, dict) or not _answers_match(answers):
        raise DecisionError("unexpected_answers")
    try:
        return label(answers, float(response.get("usage", {}).get("cost", 0.0)))
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise DecisionError("unexpected_answers") from exc


# Prompt events per harness. Codex keys sessions by `conversation.id`; Claude Code and
# the omp extension by `session.id`. A redacted placeholder means the producer's
# prompt export is off, so it is not a prompt.
PENDING_QUERY = """
SELECT p.harness AS harness, p.prompt_key AS prompt_key, p.session_id AS session_id,
    p.prompt_id AS prompt_id, p.ts AS ts, p.prompt AS prompt,
    toUInt32(coalesce(l.attempts, 0)) AS attempts
FROM (
    SELECT
        resource.`service.name`::String AS harness,
        id AS prompt_key,
        if(startsWith(harness, 'codex'), attributes_string['conversation.id'],
            attributes_string['session.id']) AS session_id,
        attributes_string['prompt.id'] AS prompt_id,
        toString(fromUnixTimestamp64Nano(toInt64(timestamp), 'UTC')) AS ts,
        attributes_string['prompt'] AS prompt
    FROM signoz_logs.distributed_logs_v2
    WHERE timestamp > toUInt64(toUnixTimestamp(now() - INTERVAL {days} DAY)) * 1000000000
        AND (
            (resource.`service.name`::String = 'claude-code'
                AND attributes_string['event.name'] = 'user_prompt')
            OR (resource.`service.name`::String
                    IN ('codex-app-server', 'codex_cli_rs', 'codex_exec')
                AND attributes_string['event.name'] = 'codex.user_prompt')
            OR (resource.`service.name`::String = 'oh-my-pi'
                AND attributes_string['event.name'] = 'omp.user_prompt')
        )
        AND attributes_string['prompt'] != ''
        AND NOT match(attributes_string['prompt'], '^[\\[<]?REDACTED[\\]>]?$')
) AS p
LEFT JOIN (
    SELECT prompt_key, max(attempts) AS attempts, max(status = 'labelled') AS done
    FROM {database}.prompt_labels FINAL GROUP BY prompt_key
) AS l ON l.prompt_key = p.prompt_key
WHERE coalesce(l.done, 0) = 0 AND coalesce(l.attempts, 0) < {max_attempts}
ORDER BY p.ts DESC
LIMIT {limit}
FORMAT JSONEachRow
"""


# Label columns of a failed decision, so every row inserts with the same columns.
UNLABELLED: dict[str, Any] = {
    "task_type": "",
    "task_type_confidence": 0.0,
    "correction": 0.0,
    "correction_kind": "",
    "sentiment": "",
    "sentiment_confidence": 0.0,
    "classifier_model": JEV_PINNED_MODEL,
    "classifier_version": CLASSIFIER_VERSION,
    "cost_usd": 0.0,
}


def _row(prompt: dict[str, Any], key: str, post: Poster) -> dict[str, Any]:
    """Label one pending prompt; the row records a failure's reason instead of labels."""
    base = {
        "harness": prompt["harness"],
        "session_id": prompt["session_id"],
        "prompt_key": prompt["prompt_key"],
        "prompt_id": prompt["prompt_id"],
        "ts": prompt["ts"],
        "prompt_length": len(prompt["prompt"]),
        "attempts": int(prompt["attempts"]) + 1,
        "classified_at": datetime.now(UTC).isoformat(),
    }
    try:
        labels = decide(prompt["harness"], prompt["prompt"], key, post)
    except DecisionError as exc:
        return base | {"status": "failed", "reason": exc.reason} | UNLABELLED
    return base | {"status": "labelled", "reason": ""} | labels


def run(
    run_sql: SqlRunner,
    *,
    days: int = 2,
    limit: int = 60,
    key: str | None = None,
    post: Poster = http_poster,
) -> dict[str, Any]:
    """Label up to ``limit`` unlabelled prompts from the last ``days`` days of SigNoz."""
    query = PENDING_QUERY.format(
        days=int(days), limit=int(limit), database=DATABASE, max_attempts=MAX_ATTEMPTS
    )
    prompts = [json.loads(line) for line in run_sql(query).splitlines() if line]
    if not prompts:
        return {"pending": 0, "labelled": 0, "failed": 0, "cost_usd": 0.0}
    key = key if key is not None else api_key()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        rows = list(pool.map(lambda prompt: _row(prompt, key, post), prompts))
    run_sql(";\n".join(insert_statements("prompt_labels", rows)))
    labelled = [row for row in rows if row["status"] == "labelled"]
    return {
        "pending": len(prompts),
        "labelled": len(labelled),
        "failed": len(rows) - len(labelled),
        "cost_usd": round(sum(float(row["cost_usd"]) for row in labelled), 6),
    }
