import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection import cli
from agent_introspection.cli import EXIT_CAPABILITY, EXIT_CONFIG, main
from agent_introspection.database import connect_database
from agent_introspection.generations import ActivatedGeneration, StagedGeneration


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


def test_analysis_generation_commands_require_preflight_and_emit_evidence(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = config_file(tmp_path)
    source = object()
    inventory = {"contract": {}, "diagnostics": {}}
    monkeypatch.setattr(cli, "verify_network_perimeter", lambda **_kwargs: {})
    monkeypatch.setattr(cli, "_client", lambda _config: source)
    monkeypatch.setattr(cli, "discover_source_schema", lambda _source: inventory)
    monkeypatch.setattr(
        cli,
        "enforce_approved_schema",
        lambda _connection, _inventory: "a" * 64,
    )
    monkeypatch.setattr(
        cli,
        "stage_generation",
        lambda _connection, *, source_contract_fingerprint: StagedGeneration(
            generation_id="generation-1",
            ordinal=1,
            window_start_ns=1,
            window_end_ns=2,
            semantic_hash=source_contract_fingerprint,
            projection_event_ids=("event-1",),
        ),
    )
    assert main(["--config", str(config), "analysis-generation", "stage"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "generation_id": "generation-1",
        "ordinal": 1,
        "projection_events": 1,
        "semantic_hash": "a" * 64,
        "status": "staged",
        "window_end_ns": 2,
        "window_start_ns": 1,
    }

    monkeypatch.setattr(
        cli,
        "activate_generation",
        lambda _connection, *, generation_id, client, endpoint: ActivatedGeneration(
            generation_id=generation_id,
            activation_event_id=f"{client is source}:{endpoint.endswith('/v1/logs')}",
            projection_count=1,
        ),
    )
    assert main(["--config", str(config), "analysis-generation", "activate", "generation-1"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "activation_event_id": "True:True",
        "generation_id": "generation-1",
        "projection_events": 1,
        "status": "activated",
    }
