import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection import cli
from agent_introspection.cli import EXIT_CAPABILITY, EXIT_CONFIG, main
from agent_introspection.database import connect_database
from agent_introspection.source import ProjectEvidenceRow


def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        f'[database]\npath = "{tmp_path / "introspection.sqlite3"}"\nbusy_timeout_ms = 5000\n'
    )
    return path


def test_cli_emits_structured_json_on_stdout(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    result = main(["--config", str(config_file(tmp_path)), "db", "check"])
    captured = capsys.readouterr()
    assert result == 0
    assert json.loads(captured.out) == {"quick_check": ["ok"]}
    assert captured.err == ""


def test_cli_emits_diagnostics_on_stderr_and_stable_exit_code(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    invalid = tmp_path / "invalid.toml"
    invalid.write_text("unsupported = true\n")
    with pytest.raises(SystemExit) as raised:
        main(["--config", str(invalid), "db", "check"])
    captured = capsys.readouterr()
    assert raised.value.code == EXIT_CONFIG
    assert captured.out == ""
    assert "ConfigurationError" in captured.err


def test_doctor_requires_and_verifies_the_current_source_contract(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = config_file(tmp_path)
    inventory = {
        "contract": {"logs": {"columns": ["timestamp"]}},
        "diagnostics": {"server_timezone": "UTC"},
    }
    monkeypatch.setattr(cli, "ensure_health", lambda **_kwargs: {})
    monkeypatch.setattr(cli, "verify_network_perimeter", lambda **_kwargs: {})
    monkeypatch.setattr(cli, "_client", lambda _config: object())
    monkeypatch.setattr(cli, "discover_source_schema", lambda _client: inventory)

    with pytest.raises(SystemExit) as raised:
        main(["--config", str(config), "doctor"])
    captured = capsys.readouterr()
    assert raised.value.code == EXIT_CAPABILITY
    assert captured.out == ""
    assert "schema drift" in captured.err

    assert main(["--config", str(config), "doctor", "--approve-schema"]) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["schema_approved"] is True

    assert main(["--config", str(config), "doctor"]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["schema_approved"] is True
    assert verified["schema_fingerprint"] == approved["schema_fingerprint"]


def test_scheduled_cli_suppresses_only_a_qualifying_current_utc_slot(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = config_file(tmp_path)
    connection = connect_database(tmp_path / "introspection.sqlite3")
    connection.executemany(
        """
        INSERT INTO scan_runs (id, status, started_at, details_json)
        VALUES (?, ?, ?, '{}')
        """,
        [
            ("success-current", "succeeded", "2026-07-10T12:05:00+00:00"),
            ("failed-next", "failed", "2026-07-10T13:05:00+00:00"),
        ],
    )
    connection.commit()
    connection.close()

    class ControlledClock:
        current = datetime(2026, 7, 10, 12, 30, tzinfo=UTC)

        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return cls.current

    calls: list[str] = []

    def execute_scan(_connection: object, _config: object) -> dict[str, str]:
        calls.append("run")
        return {"status": "executed"}

    monkeypatch.setattr(cli, "datetime", ControlledClock)
    monkeypatch.setattr(cli, "run_scan", execute_scan)

    assert main(["--config", str(config), "scan", "--scheduled"]) == 0
    skipped = json.loads(capsys.readouterr().out)
    assert skipped == {
        "interval_seconds": 3600,
        "qualifying_run_id": "success-current",
        "qualifying_run_started_at": "2026-07-10T12:05:00+00:00",
        "slot_start": "2026-07-10T12:00:00+00:00",
        "status": "already_completed_in_slot",
    }
    assert calls == []

    ControlledClock.current = datetime(2026, 7, 10, 13, 0, tzinfo=UTC)
    assert main(["--config", str(config), "scan", "--scheduled"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "executed"}

    ControlledClock.current = datetime(2026, 7, 10, 13, 30, tzinfo=UTC)
    assert main(["--config", str(config), "scan", "--scheduled"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "executed"}

    ControlledClock.current = datetime(2026, 7, 10, 15, 10, tzinfo=UTC)
    assert main(["--config", str(config), "scan", "--scheduled"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "executed"}
    assert calls == ["run", "run", "run"]


def test_legacy_project_attribution_exposes_only_the_final_command(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = config_file(tmp_path)
    collection = tmp_path / "Projects"
    project = collection / "project"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", project], check=True)
    unresolved_workspace = collection / "unresolved"
    unresolved_workspace.mkdir()
    outside_workspace = tmp_path / "outside"
    config.write_text(config.read_text() + f'\n[attribution]\nproject_roots = ["{collection}"]\n')
    start = datetime(2026, 7, 10, 12, tzinfo=UTC)
    rows = (
        ProjectEvidenceRow(
            timestamp_ns=int((start.timestamp() + 60) * 1_000_000_000),
            log_id="accepted",
            trace_id="trace-accepted",
            producer="codex-cli",
            conversation_id="conversation-accepted",
            tool_workspace=str(project),
        ),
        ProjectEvidenceRow(
            timestamp_ns=int((start.timestamp() + 120) * 1_000_000_000),
            log_id="unsupported",
            trace_id="trace-unsupported",
            producer="omp",
            conversation_id="conversation-unsupported",
            tool_workspace=str(project),
        ),
        ProjectEvidenceRow(
            timestamp_ns=int((start.timestamp() + 180) * 1_000_000_000),
            log_id="invalid",
            trace_id="trace-invalid",
            producer="codex-cli",
            conversation_id="conversation-invalid",
            tool_workspace="",
        ),
        ProjectEvidenceRow(
            timestamp_ns=int((start.timestamp() + 240) * 1_000_000_000),
            log_id="outside",
            trace_id="trace-outside",
            producer="codex-cli",
            conversation_id="conversation-outside",
            tool_workspace=str(outside_workspace),
        ),
        ProjectEvidenceRow(
            timestamp_ns=int((start.timestamp() + 300) * 1_000_000_000),
            log_id="unresolved",
            trace_id="trace-unresolved",
            producer="codex-cli",
            conversation_id="conversation-unresolved",
            tool_workspace=str(unresolved_workspace),
        ),
    )
    received: dict[str, object] = {}

    class Source:
        def project_evidence(self, **kwargs: object) -> list[object]:
            received.update(kwargs)
            return list(rows)

    monkeypatch.setattr(cli, "_client", lambda _config: Source())
    assert (
        main(
            [
                "--config",
                str(config),
                "legacy-project-attribution",
                "run",
                "--start",
                "2026-07-10T12:00:00Z",
                "--end",
                "2026-07-10T13:00:00Z",
                "--approved-by",
                "operator",
            ]
        )
        == 0
    )
    emitted = json.loads(capsys.readouterr().out)
    assert emitted["status"] == "applied"
    assert (
        emitted["accepted"],
        emitted["rejected"],
        emitted["unresolved"],
        emitted["denominator"],
    ) == (
        1,
        3,
        1,
        5,
    )
    assert (
        emitted["denominator"] == emitted["accepted"] + emitted["rejected"] + emitted["unresolved"]
    )
    assert received["start_ns"] < received["end_ns"]
