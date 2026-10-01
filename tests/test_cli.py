import json
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import facts, schedule
from agent_introspection.cli import EXIT_CONFIG, EXIT_DATABASE, EXIT_FACTS, EXIT_VALIDATION, main
from agent_introspection.facts import FactsError
from agent_introspection.findings import CORRECTION_DETECTOR_ID
from agent_introspection.proposals import (
    ProposalState,
    TransitionProposalRequest,
    create_proposal,
    transition_proposal,
)
from agent_introspection.review import create_review_session
from agent_introspection.workflow import connect_workflow
from tests.test_drafting import facts_runner, insert_finding
from tests.test_proposals import proposal_database, proposal_input


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'[database]\npath = "{tmp_path / "workflow.sqlite3"}"\n')
    return path


def test_cli_emits_structured_json_and_creates_the_workflow_store(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    assert main(["--config", str(config_file(tmp_path)), "proposal", "list"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"proposals": []}
    assert captured.err == ""
    assert (tmp_path / "workflow.sqlite3").exists()


def test_cli_emits_diagnostics_on_stderr_with_stable_exit_codes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    retired = tmp_path / "retired.toml"
    retired.write_text("[scheduler]\ninterval_seconds = 300\n")
    with pytest.raises(SystemExit) as raised:
        main(["--config", str(retired), "proposal", "list"])
    assert raised.value.code == EXIT_CONFIG
    assert "ConfigurationError" in capsys.readouterr().err

    with pytest.raises(SystemExit) as raised:
        main(["--config", str(config_file(tmp_path)), "proposal", "show", "missing"])
    captured = capsys.readouterr()
    assert raised.value.code == EXIT_VALIDATION
    assert captured.out == ""
    assert "KeyError" in captured.err


def test_facts_failures_exit_with_the_facts_code(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def failing(_statement: str) -> str:
        raise FactsError("Code: 81. DB::Exception: Database introspection does not exist")

    monkeypatch.setattr(facts, "runner", lambda _config: failing)

    with pytest.raises(SystemExit) as raised:
        main(["--config", str(config_file(tmp_path)), "facts", "status"])

    assert raised.value.code == EXIT_FACTS
    assert "does not exist" in capsys.readouterr().err


def test_candidates_export_reports_no_candidates_without_actionable_findings(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = str(config_file(tmp_path))

    assert main(["--config", config, "candidates", "export", "--reserved-model-budget", "10"]) == 0

    assert json.loads(capsys.readouterr().out) == {"status": "no_candidates"}


def test_proposal_create_persists_nothing_when_a_proposal_insert_fails(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = config_file(tmp_path)
    connection = proposal_database(path=tmp_path / "workflow.sqlite3")
    envelope = create_review_session(
        connection, candidates=[{"id": "finding-1"}], reserved_model_budget=100
    )
    connection.execute(
        """
        CREATE TRIGGER fail_proposal_insert
        BEFORE INSERT ON proposals BEGIN
            SELECT RAISE(ABORT, 'proposal store unavailable');
        END
        """
    )
    connection.commit()
    document = {
        "session_id": envelope.session_id,
        "nonce": envelope.nonce,
        "schema_version": envelope.schema_version,
        "payload_hash": envelope.payload_hash,
        "requested_model": envelope.requested_model,
        "requested_effort": envelope.requested_effort,
        "results": [{"candidate_id": "finding-1", "proposal": proposal_input().__dict__}],
        "provenance": {
            "model": envelope.requested_model,
            "effort": envelope.requested_effort,
            "trace_id": "trace-1",
            "token_count": 40,
        },
    }
    source = tmp_path / "output.json"
    source.write_text(json.dumps(document))

    with pytest.raises(SystemExit) as raised:
        main(["--config", str(config), "proposal", "create", "--input-json", str(source)])

    assert raised.value.code == EXIT_DATABASE
    assert "proposal store unavailable" in capsys.readouterr().err
    assert connection.execute(
        "SELECT status, entity_version FROM review_sessions WHERE id = ?", (envelope.session_id,)
    ).fetchone() == ("exported", 1)
    for table in ("model_runs", "proposal_drafts", "proposals", "proposal_events"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    assert connection.execute(
        "SELECT entry_type, amount FROM model_budget_ledger WHERE review_session_id = ?",
        (envelope.session_id,),
    ).fetchall() == [("reserved", 100)]


@pytest.mark.parametrize(
    "evidence",
    [
        {"validation": "failed"},
        {"validation": {"status": "failed", "checks": ["quality command failed"]}},
    ],
)
def test_mark_applied_rejects_unsuccessful_validation_before_any_transition(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, evidence: dict[str, object]
) -> None:
    config = config_file(tmp_path)
    connection = proposal_database(path=tmp_path / "workflow.sqlite3")
    proposal_id = create_proposal(connection, proposal_input())
    transition_proposal(
        connection,
        TransitionProposalRequest(
            proposal_id=proposal_id,
            target_state=ProposalState.APPROVED,
            actor="user",
            evidence={"decision": "approve"},
        ),
    )
    source = tmp_path / "evidence.json"
    source.write_text(json.dumps(evidence))
    arguments = ["--config", str(config), "proposal", "mark-applied", proposal_id]

    with pytest.raises(SystemExit) as raised:
        main([*arguments, "--actor", "user", "--input-json", str(source)])

    assert raised.value.code == EXIT_VALIDATION
    assert "validation evidence" in capsys.readouterr().err
    assert connection.execute(
        "SELECT state, entity_version FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone() == ("approved", 2)

    source.write_text(
        json.dumps({"validation": {"status": "passed", "checks": ["quality command passed"]}})
    )
    assert main([*arguments, "--actor", "user", "--input-json", str(source)]) == 0
    assert json.loads(capsys.readouterr().out) == {"proposal_id": proposal_id, "status": "applied"}
    assert connect_workflow(tmp_path / "workflow.sqlite3").execute(
        "SELECT state FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone() == ("applied",)


def test_mark_applied_leaves_an_approved_proposal_approved_when_applied_fails(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = config_file(tmp_path)
    connection = proposal_database(path=tmp_path / "workflow.sqlite3")
    proposal_id = create_proposal(connection, proposal_input())
    transition_proposal(
        connection,
        TransitionProposalRequest(
            proposal_id=proposal_id,
            target_state=ProposalState.APPROVED,
            actor="user",
            evidence={"decision": "approve"},
        ),
    )
    connection.execute(
        """
        CREATE TRIGGER fail_applied_event
        BEFORE INSERT ON proposal_events WHEN NEW.event_type = 'applied' BEGIN
            SELECT RAISE(ABORT, 'event store unavailable');
        END
        """
    )
    connection.commit()
    source = tmp_path / "evidence.json"
    source.write_text(
        json.dumps({"validation": {"status": "passed", "checks": ["quality command passed"]}})
    )

    with pytest.raises(SystemExit) as raised:
        main([
            "--config",
            str(config),
            "proposal",
            "mark-applied",
            proposal_id,
            "--actor",
            "user",
            "--input-json",
            str(source),
        ])

    assert raised.value.code == EXIT_DATABASE
    assert "event store unavailable" in capsys.readouterr().err
    assert connection.execute(
        "SELECT state, entity_version FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone() == ("approved", 2)
    assert connection.execute(
        "SELECT event_type FROM proposal_events WHERE proposal_id = ? ORDER BY sequence",
        (proposal_id,),
    ).fetchall() == [("created",), ("approved",)]


def test_proposal_create_rejects_a_proposal_for_an_unreviewed_finding(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = config_file(tmp_path)
    connection = proposal_database(path=tmp_path / "workflow.sqlite3")
    envelope = create_review_session(
        connection, candidates=[{"id": "reviewed"}], reserved_model_budget=100
    )
    document = {
        "session_id": envelope.session_id,
        "nonce": envelope.nonce,
        "schema_version": envelope.schema_version,
        "payload_hash": envelope.payload_hash,
        "requested_model": envelope.requested_model,
        "requested_effort": envelope.requested_effort,
        "results": [{"candidate_id": "reviewed", "proposal": proposal_input().__dict__}],
        "provenance": {
            "model": envelope.requested_model,
            "effort": envelope.requested_effort,
            "trace_id": "trace-1",
            "token_count": 40,
        },
    }
    source = tmp_path / "output.json"
    source.write_text(json.dumps(document))

    with pytest.raises(SystemExit) as raised:
        main(["--config", str(config), "proposal", "create", "--input-json", str(source)])

    assert raised.value.code == EXIT_VALIDATION
    assert "candidate_id" in capsys.readouterr().err
    assert connection.execute(
        "SELECT status FROM review_sessions WHERE id = ?", (envelope.session_id,)
    ).fetchone() == ("exported",)
    assert connection.execute("SELECT COUNT(*) FROM proposals").fetchone() == (0,)


def test_schedule_install_passes_the_selected_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = config_file(tmp_path)
    received: list[Path | None] = []

    def schedule_install(selected: Path | None = None) -> dict[str, Any]:
        received.append(selected)
        return {}

    monkeypatch.setattr(schedule, "schedule_install", schedule_install)

    assert main(["--config", str(config), "facts", "schedule", "install"]) == 0
    assert received == [config.resolve()]


def test_facts_install_creates_the_workflow_store(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(facts, "runner", lambda _config: lambda _sql: "")
    received: list[object] = []

    def install(_run: object, enabled: object) -> dict[str, bool]:
        received.append(enabled)
        return {"installed": True}

    monkeypatch.setattr(facts, "install", install)

    config = config_file(tmp_path)
    config.write_text(config.read_text() + '[harnesses]\nenabled = ["claude-code", "codex"]\n')

    assert main(["--config", str(config), "facts", "install"]) == 0
    assert json.loads(capsys.readouterr().out) == {"installed": True}
    assert received == [("claude-code", "codex")]
    assert (tmp_path / "workflow.sqlite3").exists()


def test_candidates_export_picks_the_highest_impact_cluster_with_its_evidence(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = str(config_file(tmp_path))
    connection = connect_workflow(tmp_path / "workflow.sqlite3")
    insert_finding(connection, "a-low", impact=3, last_seen_ns=9)
    insert_finding(connection, "b-high", impact=8, last_seen_ns=2)
    insert_finding(
        connection, "c-correction", impact=6, last_seen_ns=9, detector_id=CORRECTION_DETECTOR_ID
    )
    _, run = facts_runner()
    monkeypatch.setattr(facts, "runner", lambda _config: run)

    assert main(["--config", config, "candidates", "export", "--reserved-model-budget", "10"]) == 0

    review = json.loads(capsys.readouterr().out)["review"]
    assert review["ordered_candidate_ids"] == ["b-high"]
    candidate = review["payload"]["candidates"][0]
    assert candidate["finding"]["subject"]["impact"] == 8
    assert candidate["project_root"] == "~/Projects/example"
    assert "signatures" in candidate["evidence"]


def test_proposal_draft_dry_run_and_evaluate_commands(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = str(config_file(tmp_path))
    connection = connect_workflow(tmp_path / "workflow.sqlite3")
    insert_finding(connection, "a-cluster", impact=3, last_seen_ns=9)
    _, run = facts_runner()
    monkeypatch.setattr(facts, "runner", lambda _config: run)

    argv = ["--config", config, "proposal", "draft", "--reserved-model-budget", "100", "--dry-run"]
    assert main(argv) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dry_run"
    assert result["command_line"].startswith("codex exec --model gpt-5.5")
    assert connection.execute("SELECT COUNT(*) FROM review_sessions").fetchone() == (0,)

    assert main(["--config", config, "proposal", "evaluate", "--now", "2026-10-01T00:00:00Z"]) == 0
    assert json.loads(capsys.readouterr().out) == {"evaluated": [], "skipped": []}

    with pytest.raises(SystemExit) as raised:
        main(["--config", config, "proposal", "evaluate", "--now", "2026-10-01T00:00:00"])
    assert raised.value.code == EXIT_VALIDATION
