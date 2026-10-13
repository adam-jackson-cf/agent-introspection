from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import facts, producers
from agent_introspection.cli import EXIT_VALIDATION, main
from tests.test_producers import FakeClickHouse, FakeService, fake_services


def _config(tmp_path: Path, enabled: tuple[str, ...] = ("claude-code", "codex")) -> Path:
    path = tmp_path / "config.toml"
    listed = ", ".join(f'"{key}"' for key in enabled)
    database = tmp_path / "workflow.sqlite3"
    path.write_text(f'[database]\npath = "{database}"\n\n[harnesses]\nenabled = [{listed}]\n')
    return path


@pytest.fixture
def clickhouse(monkeypatch: pytest.MonkeyPatch) -> FakeClickHouse:
    run = FakeClickHouse(fake_services())
    monkeypatch.setattr(facts, "runner", lambda _config: run)
    return run


@pytest.fixture
def fingerprints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "fingerprints"
    monkeypatch.setattr(producers, "FINGERPRINT_DIR", directory)
    return directory


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[dict[str, Any], str]:
    assert main(list(argv)) == 0
    captured = capsys.readouterr()
    return json.loads(captured.out), captured.err


def test_discover_prints_every_service_classed_with_the_configured_harnesses(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, clickhouse: FakeClickHouse
) -> None:
    result, _err = _run(capsys, "--config", str(_config(tmp_path)), "producers", "discover")

    assert result["days"] == producers.DEFAULT_DAYS
    services = {entry["service"]: entry for entry in result["services"]}
    assert services["codex_exec"]["enabled"] is True
    assert services["toolbox"]["class"] == "unregistered"
    assert result["enabled_not_seen"] == ["codex-app-server", "codex_cli_rs"]


def test_profile_prints_names_and_logs_dropped_names_on_stderr(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    many = FakeService(spans={f"span.{index:03d}": 1 for index in range(201)}, span_rows=201)
    monkeypatch.setattr(facts, "runner", lambda _config: FakeClickHouse({"many": many}))

    result, err = _run(
        capsys, "--config", str(_config(tmp_path)), "producers", "profile", "many", "--days", "2"
    )

    assert (result["service"], result["days"], result["written"]) == ("many", 2, None)
    assert err == "span names: kept the top 200 of 201 by rows; 1 dropped\n"
    assert list(tmp_path.iterdir()) == [tmp_path / "config.toml"]


def test_profile_write_saves_the_draft_as_toml(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, clickhouse: FakeClickHouse
) -> None:
    draft = tmp_path / "toolbox.toml"

    result, _err = _run(
        capsys,
        "--config",
        str(_config(tmp_path)),
        "producers",
        "profile",
        "toolbox",
        "--write",
        str(draft),
    )

    assert result["written"] == str(draft)
    assert tomllib.loads(draft.read_text()) == result["draft"]


def test_profile_write_refuses_an_existing_file_before_any_query(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, clickhouse: FakeClickHouse
) -> None:
    draft = tmp_path / "toolbox.toml"
    draft.write_text("kept")

    with pytest.raises(SystemExit) as raised:
        main([
            "--config",
            str(_config(tmp_path)),
            "producers",
            "profile",
            "toolbox",
            "--write",
            str(draft),
        ])

    assert raised.value.code == EXIT_VALIDATION
    assert "ProducerError" in capsys.readouterr().err
    assert clickhouse.queries == []
    assert draft.read_text() == "kept"


def test_profile_write_force_replaces_an_existing_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, clickhouse: FakeClickHouse
) -> None:
    draft = tmp_path / "toolbox.toml"
    draft.write_text("old")

    _run(
        capsys,
        "--config",
        str(_config(tmp_path)),
        "producers",
        "profile",
        "toolbox",
        "--write",
        str(draft),
        "--force",
    )

    assert tomllib.loads(draft.read_text())["key"] == "toolbox"


def test_fingerprint_defaults_to_the_enabled_harnesses_and_writes_nothing(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    clickhouse: FakeClickHouse,
    fingerprints: Path,
) -> None:
    result, _err = _run(
        capsys, "--config", str(_config(tmp_path, ("claude-code",))), "producers", "fingerprint"
    )

    assert list(result["fingerprints"]) == ["claude-code"]
    assert result["saved"] == []
    assert not fingerprints.exists()


def test_fingerprint_save_writes_one_file_per_named_service(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    clickhouse: FakeClickHouse,
    fingerprints: Path,
) -> None:
    result, _err = _run(
        capsys,
        "--config",
        str(_config(tmp_path)),
        "producers",
        "fingerprint",
        "--save",
        "toolbox",
        "opencode",
    )

    assert sorted(path.name for path in fingerprints.iterdir()) == ["opencode.json", "toolbox.json"]
    assert len(result["saved"]) == 2
    saved = producers.load_fingerprint(fingerprints, "toolbox")
    assert saved is not None
    assert saved["span_names"]["Tool.execute"] == 40


def test_fingerprint_save_refuses_a_name_that_cannot_be_a_file_before_any_query(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    clickhouse: FakeClickHouse,
    fingerprints: Path,
) -> None:
    with pytest.raises(SystemExit) as raised:
        main([
            "--config",
            str(_config(tmp_path)),
            "producers",
            "fingerprint",
            "--save",
            "toolbox",
            "my service",
            "opencode",
        ])

    assert raised.value.code == EXIT_VALIDATION
    assert "cannot name a fingerprint file" in capsys.readouterr().err
    assert clickhouse.queries == []
    assert not fingerprints.exists()


def test_fingerprint_logs_lists_the_limit_cut(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fingerprints: Path,
) -> None:
    monkeypatch.setattr(producers, "FINGERPRINT_LIMIT", 1)
    monkeypatch.setattr(facts, "runner", lambda _config: FakeClickHouse(fake_services()))

    _result, err = _run(
        capsys, "--config", str(_config(tmp_path)), "producers", "fingerprint", "toolbox"
    )

    assert err.splitlines() == [
        "toolbox span_names: 3 names past the limit of 1 dropped",
        "toolbox event_names: 1 names past the limit of 1 dropped",
        "toolbox attribute_keys: 4 names past the limit of 1 dropped",
    ]


def test_a_window_below_one_day_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, clickhouse: FakeClickHouse
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--config", str(_config(tmp_path)), "producers", "discover", "--days", "0"])

    assert raised.value.code == 2
    assert "days must be at least 1" in capsys.readouterr().err
    assert clickhouse.queries == []
