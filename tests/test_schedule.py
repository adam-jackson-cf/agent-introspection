import plistlib
import shutil
from pathlib import Path

import pytest

from agent_introspection import facts, schedule


def test_launchd_job_runs_the_sync_every_minute_with_docker_on_path() -> None:
    document = plistlib.loads(
        schedule.plist(Path("/venv/bin/agent-introspection"), ["/opt/docker/bin", "/usr/bin"])
    )

    assert document["Label"] == schedule.LABEL
    assert document["ProgramArguments"] == [
        "/venv/bin/agent-introspection",
        "facts",
        "sync",
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
