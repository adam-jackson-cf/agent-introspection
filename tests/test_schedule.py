import plistlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_introspection import facts, schedule
from agent_introspection.config import AppConfig


def test_launchd_job_runs_the_sync_every_minute_with_docker_on_path() -> None:
    document = plistlib.loads(
        schedule.plist(Path("/venv/bin/agent-introspection"), ["/opt/docker/bin", "/usr/bin"])
    )

    assert document["Label"] == schedule.LABEL
    assert document["ProgramArguments"] == [
        "/venv/bin/agent-introspection",
        "facts",
        "sync",
        "--log",
    ]
    assert document["StartInterval"] == 60
    assert document["EnvironmentVariables"]["PATH"] == "/opt/docker/bin:/usr/bin"


def test_launchd_job_keeps_a_selected_configuration() -> None:
    document = plistlib.loads(
        schedule.plist(
            Path("/venv/bin/agent-introspection"), ["/usr/bin"], Path("/etc/ai/custom.toml")
        )
    )

    assert document["ProgramArguments"] == [
        "/venv/bin/agent-introspection",
        "--config",
        "/etc/ai/custom.toml",
        "facts",
        "sync",
        "--log",
    ]


def test_scheduled_job_path_follows_the_connection_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_introspection.config import SigNozConfig

    found = {"docker": "/opt/docker/bin/docker", "security": "/usr/bin/security"}
    monkeypatch.setattr(shutil, "which", found.get)
    assert schedule._job_path(SigNozConfig()) == ["/opt/docker/bin"]
    command = SigNozConfig(
        clickhouse_url="http://localhost:8123", clickhouse_password_command=("security", "-w")
    )
    assert schedule._job_path(command) == ["/usr/bin"]
    variable = SigNozConfig(clickhouse_url="http://localhost:8123", clickhouse_password_env="CH")
    with pytest.raises(RuntimeError, match="clickhouse_password_command"):
        schedule._job_path(variable)


def test_password_command_output_is_the_password_and_never_in_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_introspection.config import ConfigurationError, SigNozConfig

    ok = SigNozConfig(clickhouse_url="https://ch", clickhouse_password_command=("echo", "s3cret"))
    assert facts.clickhouse_password(ok) == "s3cret"
    failing = SigNozConfig(
        clickhouse_url="https://ch", clickhouse_password_command=("sh", "-c", "echo leak; exit 3")
    )
    with pytest.raises(ConfigurationError) as error:
        facts.clickhouse_password(failing)
    assert "leak" not in str(error.value)


def test_failed_bootstrap_restores_and_reloads_the_previous_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess
    import sys

    plist_path = tmp_path / "job.plist"
    plist_path.write_bytes(b"previous")
    (tmp_path / "agent-introspection").write_text("")
    monkeypatch.setattr(schedule, "PLIST", plist_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(schedule, "load_config", lambda _config: AppConfig())
    monkeypatch.setattr(schedule, "_job_path", lambda _signoz: [])
    calls: list[tuple[str, bytes]] = []

    def fake_launchctl(*args: str) -> subprocess.CompletedProcess[str]:
        calls.append((args[0], plist_path.read_bytes()))
        failing = args[0] == "bootstrap" and plist_path.read_bytes() != b"previous"
        return subprocess.CompletedProcess(args, 1 if failing else 0, "", "boom")

    monkeypatch.setattr(schedule, "_launchctl", fake_launchctl)
    with pytest.raises(RuntimeError, match="boom"):
        schedule.schedule_install()
    assert plist_path.read_bytes() == b"previous"
    assert calls[-1] == ("bootstrap", b"previous")


def test_failed_reload_of_the_previous_job_reports_both_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess
    import sys

    plist_path = tmp_path / "job.plist"
    plist_path.write_bytes(b"previous")
    (tmp_path / "agent-introspection").write_text("")
    monkeypatch.setattr(schedule, "PLIST", plist_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(schedule, "load_config", lambda _config: AppConfig())
    monkeypatch.setattr(schedule, "_job_path", lambda _signoz: [])

    def fake_launchctl(*args: str) -> subprocess.CompletedProcess[str]:
        if args[0] != "bootstrap":
            return subprocess.CompletedProcess(args, 0, "", "")
        new = plist_path.read_bytes() != b"previous"
        return subprocess.CompletedProcess(args, 1, "", "new-boom" if new else "old-boom")

    monkeypatch.setattr(schedule, "_launchctl", fake_launchctl)
    with pytest.raises(RuntimeError) as raised:
        schedule.schedule_install()
    text = str(raised.value)
    assert "new-boom" in text
    assert "old-boom" in text
    assert "stopped" in text
    assert plist_path.read_bytes() == b"previous"


def test_missing_executable_is_rejected_before_the_loaded_job_is_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys

    monkeypatch.setattr(schedule, "PLIST", tmp_path / "job.plist")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(schedule, "_launchctl", lambda *a: pytest.fail("launchctl ran"))
    with pytest.raises(RuntimeError, match="not found"):
        schedule.schedule_install()


def test_sync_log_lines_carry_a_utc_timestamp(tmp_path: Path) -> None:
    log = tmp_path / "sync.log"
    now = datetime(2026, 10, 12, 8, 30, 5, tzinfo=UTC)

    schedule.log_line('{"synced":1}', log, now)
    schedule.log_line("FactsError: first\nsecond line", log, now)

    assert log.read_text().splitlines() == [
        '2026-10-12T08:30:05Z {"synced":1}',
        "2026-10-12T08:30:05Z FactsError: first",
        "2026-10-12T08:30:05Z second line",
    ]


def test_sync_log_rotates_by_size_and_keeps_a_bounded_number_of_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "sync.log"
    monkeypatch.setattr(schedule, "LOG_MAX_BYTES", 100)
    for index in range(12):
        schedule.log_line(f"line {index} " + "x" * 40, log)

    files = sorted(path.name for path in tmp_path.iterdir())
    assert files == ["sync.log", "sync.log.1", "sync.log.2"]
    assert len(files) == schedule.LOG_FILES
    kept = "".join((tmp_path / name).read_text() for name in files)
    # The newest lines survive; only the oldest are dropped.
    assert "line 11 " in kept
    assert "line 0 " not in kept
    assert all((tmp_path / name).stat().st_size <= 100 + 80 for name in files)


def test_an_unwritable_sync_log_falls_back_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")

    schedule.log_line("kept", blocked / "sync.log")

    assert "kept" in capsys.readouterr().err
