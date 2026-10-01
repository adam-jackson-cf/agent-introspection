import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import candidates, drafting
from agent_introspection.drafting import CodexResult, DraftingError, DraftRequest
from agent_introspection.findings import CORRECTION_DETECTOR_ID, DETECTOR_ID
from agent_introspection.review import PROPOSAL_LIMITS, create_review_session
from tests.conftest import OpenWorkflow
from tests.test_proposals import proposal_input


def insert_finding(
    connection: sqlite3.Connection,
    finding_id: str,
    *,
    impact: int,
    last_seen_ns: int,
    detector_id: str = DETECTOR_ID,
) -> None:
    subject = {
        "tool_family": "shell",
        "failure_class": "not_found",
        "harnesses": ["claude-code", "codex_exec"],
        "tools": ["exec_command"],
        "examples": ["cat: <path>: No such file"],
        "impact": impact,
    }
    connection.execute(
        """
        INSERT INTO findings (
            id, fingerprint, category, trend_state, detector_id, detector_version,
            first_seen_ns, last_seen_ns, occurrence_count, canonical_task_count,
            local_day_count, entity_version, updated_at, subject
        ) VALUES (?, ?, 'tool_failure_cluster', 'actionable', ?, 1, 1, ?, 6, 3, 2, 1, 'now', ?)
        """,
        (finding_id, finding_id[0] * 64, detector_id, last_seen_ns, json.dumps(subject)),
    )
    connection.commit()


def facts_runner(
    root: str = "~/Projects/example", signature_rows: int = 2
) -> tuple[list[str], Any]:
    statements: list[str] = []

    def run(sql: str) -> str:
        statements.append(sql)
        if "AS sessions" in sql:
            return json.dumps({"project_root": root, "sessions": 4}) + "\n"
        if "'unattributed'" in sql:
            return json.dumps({"project": "example", "failures": 5, "tasks": 3}) + "\n"
        if "failure_signature AS signature" in sql:
            return "".join(
                json.dumps({"signature": f"sig-{index}-" + "x" * 400, "failures": 1}) + "\n"
                for index in range(signature_rows)
            )
        return ""

    return statements, run


def store(open_workflow: OpenWorkflow, tmp_path: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection = open_workflow(tmp_path / "workflow.sqlite3")
    insert_finding(connection, "a-low", impact=5, last_seen_ns=10)
    insert_finding(connection, "b-old", impact=9, last_seen_ns=1)
    insert_finding(connection, "c-new", impact=9, last_seen_ns=5)
    insert_finding(connection, "d-other", impact=100, last_seen_ns=9, detector_id="retired")
    return connection


def test_next_finding_is_the_highest_impact_most_recent_cluster_without_a_proposal(
    tmp_path: Path,
    open_workflow: OpenWorkflow,
) -> None:
    connection = store(open_workflow, tmp_path)
    insert_finding(connection, "e-proposed", impact=50, last_seen_ns=9)
    connection.execute(
        "INSERT INTO proposals VALUES ('p1', 'e-proposed', 'pending', '{}', 'now', 'now', 1)"
    )
    connection.commit()

    finding = candidates.next_finding(connection)

    assert finding is not None
    assert finding["id"] == "c-new"
    assert finding["subject"]["impact"] == 9


def test_candidate_carries_the_finding_evidence_and_most_frequent_project_root(
    tmp_path: Path,
    open_workflow: OpenWorkflow,
) -> None:
    connection = store(open_workflow, tmp_path)
    statements, run = facts_runner()

    exported = candidates.export(connection, run, reserved_model_budget=1000)

    candidate = exported["review"]["payload"]["candidates"][0]
    assert candidate["id"] == "c-new"
    assert candidate["finding"]["detector_id"] == DETECTOR_ID
    assert candidate["project_root"] == "~/Projects/example"
    assert candidate["evidence"]["projects"][0]["project"] == "example"
    assert "trimmed" not in candidate["evidence"]
    assert any("project = 'example'" in sql for sql in statements)


def test_oversized_evidence_is_trimmed_to_the_review_input_limit(
    tmp_path: Path, open_workflow: OpenWorkflow
) -> None:
    connection = store(open_workflow, tmp_path)
    _, run = facts_runner(signature_rows=200)
    finding = candidates.next_finding(connection)
    assert finding is not None

    candidate = candidates.build_candidate(run, finding)

    payload = json.dumps(
        {"purpose": "proposal", "candidates": [candidate]}, sort_keys=True, separators=(",", ":")
    )
    assert len(payload) <= PROPOSAL_LIMITS.max_input_characters
    assert candidate["evidence"]["trimmed"]["signatures"] > 0
    assert candidate["evidence"]["signatures"]
    create_review_session(connection, candidates=[candidate], reserved_model_budget=10)


def test_candidate_that_cannot_fit_is_rejected() -> None:
    candidate = {"id": "x", "finding": {"subject": "y" * 60_000}, "evidence": {"daily": []}}
    with pytest.raises(ValueError, match="input character limit"):
        candidates.fit_candidate(candidate, PROPOSAL_LIMITS.max_input_characters)


def test_dry_run_prints_the_command_without_reserving_a_session(
    tmp_path: Path, open_workflow: OpenWorkflow
) -> None:
    connection = store(open_workflow, tmp_path)
    _, run = facts_runner(root=str(tmp_path))

    def forbidden(_command: list[str], _prompt: str) -> CodexResult:
        raise AssertionError("dry run must not run codex")

    result = drafting.draft(connection, run, DraftRequest(1000, dry_run=True), codex=forbidden)

    assert result["status"] == "dry_run"
    assert result["review"]["ordered_candidate_ids"] == ["c-new"]
    command = result["command"]
    assert command[:4] == ["codex", "exec", "--model", "gpt-5.5"]
    for flag in (
        'model_reasoning_effort="high"',
        'otel.exporter="none"',
        'otel.trace_exporter="none"',
        'otel.metrics_exporter="none"',
    ):
        assert command[command.index(flag) - 1] == "-c"
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("-C") + 1] == str(tmp_path)
    assert "--json" in command
    assert command[command.index("--output-schema") + 1] == drafting.SCHEMA_PLACEHOLDER
    assert "Established tools of a project first." in result["prompt"]
    assert result["review"]["nonce"] in result["prompt"]
    assert connection.execute("SELECT COUNT(*) FROM review_sessions").fetchone() == (0,)
    assert connection.execute("SELECT COUNT(*) FROM model_budget_ledger").fetchone() == (0,)


def test_working_directory_falls_back_when_the_project_root_is_missing(tmp_path: Path) -> None:
    review: dict[str, Any] = {"payload": {"candidates": [{"project_root": str(tmp_path / "gone")}]}}
    assert drafting.working_directory(review, tmp_path) == tmp_path
    review = {"payload": {"candidates": [{"project_root": None}]}}
    assert drafting.working_directory(review, tmp_path) == tmp_path


def model_document(prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
    envelope = json.loads(prompt.split("Review envelope:\n", 1)[1].split("\n", 1)[0])
    proposal = proposal_input().__dict__ | {
        "finding_id": envelope["ordered_candidate_ids"][0],
        "occurrence_count": 6,
        "task_count": 3,
        "day_count": 2,
    }
    document = {
        key: envelope[key]
        for key in (
            "session_id",
            "nonce",
            "schema_version",
            "payload_hash",
            "requested_model",
            "requested_effort",
        )
    }
    document["results"] = [
        {"candidate_id": envelope["ordered_candidate_ids"][0], "proposal": proposal}
    ]
    assert schema["properties"]["nonce"]["enum"] == [envelope["nonce"]]
    assert set(schema["required"]) == set(schema["properties"])
    assert set(schema["required"]) == {*document}
    proposal_schema = schema["properties"]["results"]["items"]["properties"]["proposal"]
    assert set(proposal_schema["required"]) == set(proposal)
    return document


def fake_codex(calls: list[list[str]], *, fail: bool = False) -> drafting.CodexRunner:
    def run(command: list[str], prompt: str) -> CodexResult:
        calls.append(command)
        schema = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
        events: list[dict[str, Any]] = [{"type": "thread.started", "thread_id": "thread-7"}]
        if fail:
            events.append({"type": "turn.failed", "error": {"message": "usage limit"}})
            return CodexResult(1, "\n".join(json.dumps(e) for e in events), "")
        text = json.dumps(model_document(prompt, schema))
        events += [
            {"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "draft"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 700, "cached_input_tokens": 200, "output_tokens": 90},
            },
        ]
        stdout = "Reading prompt from stdin...\n" + "\n".join(json.dumps(e) for e in events)
        return CodexResult(0, stdout, "")

    return run


def test_draft_imports_the_codex_answer_with_its_thread_and_usage(
    tmp_path: Path, open_workflow: OpenWorkflow
) -> None:
    connection = store(open_workflow, tmp_path)
    connection.execute(
        "UPDATE findings SET subject = json_set(subject, '$.harnesses', json('[\"codex_exec\"]'))"
    )
    connection.commit()
    _, run = facts_runner(root=str(tmp_path))
    calls: list[list[str]] = []

    result = drafting.draft(connection, run, DraftRequest(5000), codex=fake_codex(calls))

    assert result["status"] == "created"
    assert result["trace_id"] == "thread-7"
    assert result["token_count"] == 790
    assert result["cwd"] == str(tmp_path)
    assert len(calls) == 1
    assert connection.execute("SELECT finding_id, state FROM proposals").fetchall() == [
        ("c-new", "pending")
    ]
    assert connection.execute(
        "SELECT trace_id, input_tokens, output_tokens, total_tokens, token_availability "
        "FROM model_runs"
    ).fetchall() == [("thread-7", 700, 90, None, "partial")]
    assert connection.execute(
        "SELECT amount FROM model_budget_ledger WHERE entry_type = 'consumed'"
    ).fetchall() == [(-790,)]


def test_failed_codex_run_creates_nothing_and_leaves_the_session_exported(
    tmp_path: Path,
    open_workflow: OpenWorkflow,
) -> None:
    connection = store(open_workflow, tmp_path)
    _, run = facts_runner()

    with pytest.raises(DraftingError, match="usage limit"):
        drafting.draft(connection, run, DraftRequest(5000), codex=fake_codex([], fail=True))

    assert connection.execute("SELECT status FROM review_sessions").fetchall() == [("exported",)]
    assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone() == (0,)


def test_metric_harnesses_must_come_from_the_finding(
    tmp_path: Path, open_workflow: OpenWorkflow
) -> None:
    connection = store(open_workflow, tmp_path)
    _, run = facts_runner()
    calls: list[list[str]] = []
    connection.execute(
        "UPDATE findings SET subject = json_set(subject, '$.harnesses', json('[\"omp\"]'))"
    )
    connection.commit()

    with pytest.raises(ValueError, match="not in the finding"):
        drafting.draft(connection, run, DraftRequest(5000), codex=fake_codex(calls))

    assert connection.execute("SELECT status FROM review_sessions").fetchall() == [("exported",)]
    assert connection.execute("SELECT COUNT(*) FROM model_runs").fetchone() == (0,)


def test_parse_events_requires_a_thread_and_usage() -> None:
    parsed = drafting.parse_events('not json\n{"type": "item.completed", "item": {}}\n')
    assert parsed.message is None
    with pytest.raises(DraftingError, match="thread id"):
        drafting.provenance(parsed)
    parsed.thread_id = "t"
    with pytest.raises(DraftingError, match="token usage"):
        drafting.provenance(parsed)


def test_correction_candidate_uses_its_own_project_root(
    tmp_path: Path, open_workflow: OpenWorkflow
) -> None:
    connection = store(open_workflow, tmp_path)
    insert_finding(connection, "f-correction", impact=20, last_seen_ns=1)
    subject = {
        "project": "corrected-project",
        "correction_kind": "ignored",
        "harnesses": ["omp"],
        "task_types": [],
        "sessions": 2,
        "impact": 20,
    }
    connection.execute(
        "UPDATE findings SET detector_id = ?, subject = ? WHERE id = 'f-correction'",
        (CORRECTION_DETECTOR_ID, json.dumps(subject)),
    )
    connection.commit()
    statements, run = facts_runner()

    exported = candidates.export(connection, run, reserved_model_budget=1000)

    review = exported["review"]
    assert review["ordered_candidate_ids"] == ["f-correction"]
    assert any("project = 'corrected-project'" in sql for sql in statements)
    metric = drafting.output_schema(review)["properties"]["results"]["items"]["properties"][
        "proposal"
    ]["properties"]["predicted_success_metric"]["properties"]
    assert metric["metric"]["enum"] == ["correction_task_rate"]
    assert metric["harnesses"]["items"]["enum"] == ["omp"]
