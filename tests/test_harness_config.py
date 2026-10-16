from __future__ import annotations

import dataclasses
import json
import os
import re
import subprocess
import tomllib
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import doctor_harness, facts, harness_config, profiles
from agent_introspection.cli import main
from tests.support.doctor_machine import installer

_SENTINEL = "sk-sentinel-0123456789"
_CLAUDE_EXPORT = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_SERVICE_NAME": "claude-code",
}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty HOME, with no harness config directory variables set."""
    directory = tmp_path / "home"
    directory.mkdir()
    monkeypatch.setenv("HOME", str(directory))
    for name in ("CODEX_HOME", "CLAUDE_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)
    return directory


def _config(tmp_path: Path, text: str = "") -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def _harness(
    capsys: pytest.CaptureFixture[str], config: Path, *argv: str
) -> tuple[dict[str, Any], str]:
    assert main(["--config", str(config), "harness", *argv]) == 0
    out = capsys.readouterr().out
    return json.loads(out), out


def _actions(item: dict[str, Any]) -> dict[str, str]:
    return {change["key"]: change["action"] for change in item["changes"]}


def _claude_settings(home: Path) -> Path:
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]},
                "env": {"OPENROUTER_API_KEY": _SENTINEL, "OTEL_METRICS_EXPORTER": "otlp"},
            },
            indent=4,
        )
        + "\n"
    )
    os.chmod(settings, 0o640)
    return settings


def test_configure_claude_code_sets_only_managed_keys_with_a_backup(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    settings = _claude_settings(home)
    original = settings.read_text()
    config = _config(tmp_path)

    result, out = _harness(capsys, config, "configure", "claude-code")

    assert _SENTINEL not in out
    [item] = result["files"]
    assert (item["path"], item["exists"], item["written"]) == (str(settings), True, True)
    assert _actions(item) == {
        **dict.fromkeys(_CLAUDE_EXPORT, "add"),
        "OTEL_METRICS_EXPORTER": "unchanged",
        "OTEL_LOG_USER_PROMPTS": "unchanged",
        "OTEL_LOG_ASSISTANT_RESPONSES": "unchanged",
    }
    prompts = [change for change in item["changes"] if change["group"] == "prompt_export"]
    assert [(p["key"], p["opt_in"], p["on"], p["old"]) for p in prompts] == [
        ("OTEL_LOG_USER_PROMPTS", True, False, None),
        ("OTEL_LOG_ASSISTANT_RESPONSES", True, False, None),
    ]
    written = json.loads(settings.read_text())
    assert written["env"] == {"OPENROUTER_API_KEY": _SENTINEL, **_CLAUDE_EXPORT}
    assert (written["theme"], written["hooks"]) == ("dark", json.loads(original)["hooks"])
    assert settings.read_text().startswith('{\n    "theme": "dark",')
    assert settings.stat().st_mode & 0o777 == 0o640
    assert Path(item["backup"]).read_text() == original
    assert "install_activity.py" in result["next_steps"][0]
    assert result["next_steps"][1].startswith("Restart running Claude Code sessions")

    again, _ = _harness(capsys, config, "configure", "claude-code")

    [second] = again["files"]
    assert (second["written"], second["backup"]) == (False, None)
    assert set(_actions(second).values()) == {"unchanged"}
    assert [path.name for path in settings.parent.glob("settings.json.bak-*")] == [
        Path(item["backup"]).name
    ]
    assert len(again["next_steps"]) == 1


def test_prompt_export_is_written_only_when_opted_in(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    settings = _claude_settings(home)
    config = _config(tmp_path)
    _harness(capsys, config, "configure", "claude-code")

    result, _ = _harness(capsys, config, "configure", "claude-code", "--prompt-export")

    [item] = result["files"]
    # Prompts are exported with assistant responses kept redacted: Claude Code's
    # response switch follows the prompt switch when unset.
    assert {key: action for key, action in _actions(item).items() if action != "unchanged"} == {
        "OTEL_LOG_USER_PROMPTS": "add",
        "OTEL_LOG_ASSISTANT_RESPONSES": "add",
    }
    env = json.loads(settings.read_text())["env"]
    assert (env["OTEL_LOG_USER_PROMPTS"], env["OTEL_LOG_ASSISTANT_RESPONSES"]) == ("1", "0")
    report, _ = _harness(capsys, config, "configure", "claude-code")
    prompts = [c for c in report["files"][0]["changes"] if c["group"] == "prompt_export"]
    assert [change["on"] for change in prompts] == [True, True]


def test_dry_run_reports_the_changes_and_writes_nothing(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    config = _config(tmp_path)

    result, _ = _harness(capsys, config, "configure", "claude-code", "--dry-run")

    [item] = result["files"]
    assert result["dry_run"] is True
    assert (item["path"], item["exists"]) == (str(home / ".claude" / "settings.json"), False)
    assert (item["written"], item["backup"], item["notes"]) == (False, None, [])
    assert set(_actions(item).values()) == {"add", "unchanged"}
    assert list(home.iterdir()) == []

    _harness(capsys, config, "configure", "claude-code")

    created = home / ".claude" / "settings.json"
    assert json.loads(created.read_text())["env"] == _CLAUDE_EXPORT
    assert created.stat().st_mode & 0o777 == 0o600
    assert [path.name for path in created.parent.iterdir()] == ["settings.json"]


def test_parts_of_a_managed_value_the_profile_does_not_manage_are_never_shown(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    odd = [{"endpoint": "http://localhost:4318", "token": _SENTINEL}, "plain"]
    settings.write_text(json.dumps({"env": {"OTEL_EXPORTER_OTLP_ENDPOINT": odd}}))

    result, out = _harness(capsys, _config(tmp_path), "configure", "claude-code", "--dry-run")

    assert _SENTINEL not in out
    endpoint = result["files"][0]["changes"][5]
    assert (endpoint["key"], endpoint["action"]) == ("OTEL_EXPORTER_OTLP_ENDPOINT", "change")
    assert endpoint["old"] == [
        {"endpoint": "http://localhost:4318", "token": "(not shown)"},
        "plain",
    ]


_CREDENTIALED = f"https://user:{_SENTINEL}@collector.example:4318/?token={_SENTINEL}"
_ENDPOINT_KEY = "OTEL_EXPORTER_OTLP_ENDPOINT"
_CODEX_EXPORTER = f'{{ otlp-http = {{ endpoint = "{_CREDENTIALED}", protocol = "binary" }} }}'
# Per harness: the file holding a credentialed managed URL, its text, and the key.
_CREDENTIALED_FILES = {
    "claude-code": (
        ".claude/settings.json",
        json.dumps({"env": {_ENDPOINT_KEY: _CREDENTIALED}}),
        _ENDPOINT_KEY,
    ),
    "omp": (".omp/.env", f"{_ENDPOINT_KEY}={_CREDENTIALED}\n", _ENDPOINT_KEY),
    "codex": (".codex/config.toml", f"[otel]\nexporter = {_CODEX_EXPORTER}\n", "exporter"),
}


@pytest.mark.parametrize("harness", list(_CREDENTIALED_FILES))
def test_a_managed_url_is_shown_as_doctor_shows_it_without_credentials(
    harness: str, capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    path, text, key = _CREDENTIALED_FILES[harness]
    settings = home / path
    settings.parent.mkdir(parents=True)
    settings.write_text(text)
    config = _config(tmp_path)

    planned, dry_out = _harness(capsys, config, "configure", harness, "--dry-run")
    written, out = _harness(capsys, config, "configure", harness)

    assert _SENTINEL not in dry_out + out
    shown = doctor_harness.shown_text(_CREDENTIALED)
    assert shown == "https://collector.example:4318/"
    for result in (planned, written):
        change = next(c for c in result["files"][0]["changes"] if c["key"] == key)
        assert change["action"] == "change"
        if key == "exporter":
            assert change["old"] == {"otlp-http": {"endpoint": shown, "protocol": "binary"}}
        else:
            assert change["old"] == shown
            # doctor names the same value the same way.
            assert doctor_harness.shown_value(_CREDENTIALED) == repr(shown)


def test_claude_config_dir_wins_and_a_symlinked_file_is_written_through(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    (dotfiles / "settings.json").write_text("{}\n")
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / "settings.json").symlink_to(dotfiles / "settings.json")
    _claude_settings(home)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    result, _ = _harness(capsys, _config(tmp_path), "configure", "claude-code")

    assert [item["path"] for item in result["files"]] == [str(dotfiles / "settings.json")]
    assert (config_dir / "settings.json").is_symlink()
    assert json.loads((dotfiles / "settings.json").read_text())["env"] == _CLAUDE_EXPORT


def test_a_set_config_directory_is_configured_before_its_harness_has_written_there(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The harness reads only the directory its variable names, so a file left at the
    # default location is not the one to configure.
    legacy = _claude_settings(home)
    before = legacy.read_text()
    claude_dir = tmp_path / "claude-config"
    claude_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_dir))
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    (home / ".codex").mkdir()
    config = _config(tmp_path)

    claude, _ = _harness(capsys, config, "configure", "claude-code")
    codex, _ = _harness(capsys, config, "configure", "codex", "--dry-run")

    [item] = claude["files"]
    assert (item["path"], item["exists"]) == (str((claude_dir / "settings.json").resolve()), False)
    assert json.loads((claude_dir / "settings.json").read_text())["env"] == _CLAUDE_EXPORT
    assert legacy.read_text() == before
    assert [item["path"] for item in codex["files"]] == [
        str((codex_home / "config.toml").resolve()),
        str((home / ".codex" / "config.toml").resolve()),
    ]


_CODEX_CONFIG = """\
# Codex settings
model = "gpt-5"
notify = ["notify-send", "codex"]

[otel]
environment = "dev"
log_user_prompt = true
exporter = { otlp-grpc = { endpoint = "http://localhost:4317", headers = { auth = "@KEY@" } } }
trace_exporter = { otlp-grpc = { endpoint = "http://localhost:4317" } }
metrics_exporter = { otlp-grpc = { endpoint = "http://127.0.0.1:4317" } }

# trusted projects
[projects."/work/repo"]
trust_level = "trusted"
""".replace("@KEY@", _SENTINEL)


def _codex_home(home: Path, text: str = _CODEX_CONFIG) -> Path:
    config = home / ".codex" / "config.toml"
    config.parent.mkdir()
    config.write_text(text)
    return config


def test_codex_grpc_exporters_satisfy_the_profile_and_stay_untouched(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    codex = _codex_home(home)

    result, out = _harness(capsys, _config(tmp_path), "configure", "codex")

    assert _SENTINEL not in out
    [item] = result["files"]
    assert _actions(item) == {
        "exporter": "satisfied",
        "trace_exporter": "satisfied",
        "metrics_exporter": "satisfied",
        "log_user_prompt": "unchanged",
    }
    exporter = item["changes"][0]
    assert exporter["transport"] == "grpc"
    assert exporter["old"] == {
        "otlp-grpc": {"endpoint": "http://localhost:4317", "headers": "(not shown)"}
    }
    assert (item["written"], item["backup"]) == (False, None)
    assert codex.read_text() == _CODEX_CONFIG
    assert result["next_steps"] == []


def test_force_rewrites_codex_exporters_to_otlp_http_and_keeps_other_keys(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    codex = _codex_home(home)

    result, out = _harness(capsys, _config(tmp_path), "configure", "codex", "--force")

    assert _SENTINEL not in out
    [item] = result["files"]
    assert set(_actions(item).values()) == {"change", "unchanged"}
    assert item["notes"] == []
    document = tomllib.loads(codex.read_text())
    assert document["otel"] == {
        "environment": "dev",
        "log_user_prompt": True,
        **{
            key: {
                "otlp-http": {
                    "endpoint": f"http://127.0.0.1:4318/v1/{signal}",
                    "protocol": "binary",
                }
            }
            for key, signal in (
                ("exporter", "logs"),
                ("trace_exporter", "traces"),
                ("metrics_exporter", "metrics"),
            )
        },
    }
    text = codex.read_text()
    assert text.startswith('# Codex settings\nmodel = "gpt-5"\nnotify = ["notify-send", "codex"]\n')
    assert text.endswith('\n# trusted projects\n[projects."/work/repo"]\ntrust_level = "trusted"\n')
    assert Path(item["backup"]).read_text() == _CODEX_CONFIG


def test_codex_without_otel_gets_the_table_appended(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    head = '# Codex settings\nmodel = "gpt-5"\n\n[tui]\nnotifications = true\n'
    codex = _codex_home(home, head)

    result, _ = _harness(capsys, _config(tmp_path), "configure", "codex", "--prompt-export")

    assert set(_actions(result["files"][0]).values()) == {"add"}
    text = codex.read_text()
    assert text.startswith(head + "\n[otel]\n")
    assert tomllib.loads(text)["otel"]["log_user_prompt"] is True


def test_codex_split_into_sub_tables_is_regenerated_with_its_other_keys(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    codex = _codex_home(
        home,
        '[otel]\nenvironment = "dev" # or prod\n\n# the logs exporter\n'
        '[otel.exporter.otlp-grpc]\nendpoint = "http://localhost:4317"\n\n'
        "[tui]\nnotifications = true\n",
    )

    result, _ = _harness(capsys, _config(tmp_path), "configure", "codex", "--force")

    text = codex.read_text()
    document = tomllib.loads(text)
    assert document["otel"]["environment"] == "dev"
    assert document["otel"]["exporter"] == {
        "otlp-http": {"endpoint": "http://127.0.0.1:4318/v1/logs", "protocol": "binary"}
    }
    assert document["tui"] == {"notifications": True}
    assert text.startswith('[otel]\n# or prod\nenvironment = "dev"\n# the logs exporter\nexporter')
    [note] = result["files"][0]["notes"]
    assert note.startswith("[otel] could not be edited line by line")
    assert note.endswith("its 2 comment line(s) now sit above the key each described")


def test_codex_is_configured_in_every_existing_root_and_each_extra_root(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orca = tmp_path / "orca" / "home"
    orca.mkdir(parents=True)
    (orca / "config.toml").write_text(_CODEX_CONFIG)
    monkeypatch.setenv("CODEX_HOME", str(orca))
    (home / ".codex").mkdir()
    extra = tmp_path / "extra-home"
    extra.mkdir()

    result, _ = _harness(
        capsys, _config(tmp_path), "configure", "codex", "--root", str(extra), str(orca)
    )

    paths = [item["path"] for item in result["files"]]
    assert paths == [
        str((orca / "config.toml").resolve()),
        str((home / ".codex" / "config.toml").resolve()),
        str((extra / "config.toml").resolve()),
    ]
    assert [item["written"] for item in result["files"]] == [False, True, True]
    assert (orca / "config.toml").read_text() == _CODEX_CONFIG
    assert "exporter" in tomllib.loads((home / ".codex" / "config.toml").read_text())["otel"]


def _refused(capsys: pytest.CaptureFixture[str], config: Path, *argv: str) -> str:
    with pytest.raises(SystemExit) as raised:
        main(["--config", str(config), "harness", *argv])
    assert raised.value.code == 50
    captured = capsys.readouterr()
    assert captured.out == ""
    return captured.err


def test_configure_refuses_a_missing_root_a_candidate_and_an_unknown_name(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    config = _config(tmp_path)

    missing = _refused(capsys, config, "configure", "codex", "--root", str(tmp_path / "none"))
    candidate = _refused(capsys, config, "configure", "opencode")
    unknown = _refused(capsys, config, "configure", "aider")

    assert "is not a directory" in missing
    assert "opencode is a candidate producer, not a harness" in candidate
    assert "producers profile" in candidate
    assert "harness impact opencode" in candidate
    assert "unknown harness profile aider" in unknown
    assert list(home.iterdir()) == []


def test_an_unparseable_config_file_is_refused_unchanged(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    codex = _codex_home(home, "[otel\n")

    error = _refused(capsys, _config(tmp_path), "configure", "codex")

    assert f"{codex}: not valid TOML" in error
    assert codex.read_text() == "[otel\n"
    (home / ".claude" / "settings.json").mkdir(parents=True)
    assert "settings.json is not a file" in _refused(
        capsys, _config(tmp_path), "configure", "claude-code"
    )


def test_omp_dotenv_lines_are_updated_in_place(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    env = home / ".omp" / ".env"
    env.parent.mkdir()
    original = (
        "# omp environment\n"
        f"OPENROUTER_API_KEY={_SENTINEL}\n"
        "export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317\n"
        "OTEL_EXPORTER_OTLP_PROTOCOL=grpc\n"
    )
    env.write_text(original)

    result, out = _harness(capsys, _config(tmp_path), "configure", "omp", "--prompt-export")

    assert _SENTINEL not in out
    assert _actions(result["files"][0]) == {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "change",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "change",
        "OTEL_SERVICE_NAME": "add",
        "AGENT_INTROSPECTION_OMP_PROMPT_EXPORT": "add",
    }
    assert env.read_text() == (
        "# omp environment\n"
        f"OPENROUTER_API_KEY={_SENTINEL}\n"
        "export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318\n"
        "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf\n"
        "OTEL_SERVICE_NAME=oh-my-pi\n"
        "AGENT_INTROSPECTION_OMP_PROMPT_EXPORT=1\n"
    )
    assert "omp plugin link" in result["next_steps"][0]


def test_every_harness_exports_to_the_configured_collector_over_one_transport(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    endpoint = "http://127.0.0.1:14318"
    config = _config(tmp_path, f'[signoz]\notlp_endpoint = "{endpoint}"\n')

    results = [
        _harness(capsys, config, "configure", key, "--dry-run")[0]
        for key in ("claude-code", "codex", "omp")
    ]

    assert {result["otlp_endpoint"] for result in results} == {endpoint}
    new = {
        result["harness"]: {item["key"]: item["new"] for item in result["files"][0]["changes"]}
        for result in results
    }
    # Every exporter is OTLP/HTTP to the configured collector's port.
    for key in ("claude-code", "omp"):
        assert new[key]["OTEL_EXPORTER_OTLP_ENDPOINT"] == endpoint
        assert new[key]["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    for setting, signal in (
        ("exporter", "logs"),
        ("trace_exporter", "traces"),
        ("metrics_exporter", "metrics"),
    ):
        assert new["codex"][setting] == {
            "otlp-http": {"endpoint": f"{endpoint}/v1/{signal}", "protocol": "binary"}
        }


def test_an_additional_machine_points_each_harness_at_the_host_collector(
    capsys: pytest.CaptureFixture[str], home: Path, tmp_path: Path
) -> None:
    host = "http://signoz-host.example:4318"
    config = _config(
        tmp_path,
        f'[machine]\nrole = "additional"\nexport_endpoint = "{host}/"\n'
        # The host's own setting, copied over, does not apply here.
        '[signoz]\notlp_endpoint = "http://127.0.0.1:4318"\n',
    )
    _codex_home(
        home,
        "[otel]\n"
        'exporter = { otlp-grpc = { endpoint = "http://signoz-host.example:4317" } }\n'
        'trace_exporter = { otlp-grpc = { endpoint = "http://localhost:4317" } }\n',
    )

    claude, _ = _harness(capsys, config, "configure", "claude-code", "--dry-run")
    codex, _ = _harness(capsys, config, "configure", "codex", "--dry-run")

    assert claude["otlp_endpoint"] == codex["otlp_endpoint"] == host
    changes = {change["key"]: change["new"] for change in claude["files"][0]["changes"]}
    assert changes["OTEL_EXPORTER_OTLP_ENDPOINT"] == host
    assert changes["OTEL_EXPORTER_OTLP_PROTOCOL"] == "http/protobuf"
    [item] = codex["files"]
    # gRPC to the host reaches its collector; a local exporter reaches nothing here.
    assert _actions(item) == {
        "exporter": "satisfied",
        "trace_exporter": "change",
        "metrics_exporter": "add",
        "log_user_prompt": "unchanged",
    }
    metrics = next(change for change in item["changes"] if change["key"] == "metrics_exporter")
    assert metrics["new"] == {"otlp-http": {"endpoint": f"{host}/v1/metrics", "protocol": "binary"}}


def test_config_targets_need_a_path_whose_variables_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_SUCH_HOME", raising=False)
    config_file = profiles.ConfigFile(
        id="settings", paths=("$NO_SUCH_HOME/x.json",), format="json", table="", roots="each"
    )

    with pytest.raises(ValueError, match="settings: no path to configure"):
        harness_config.config_targets(config_file)


_BRACED = profiles.ConfigFile(
    id="config",
    paths=("${TOOL_HOME}/config.toml", "~/.tool/config.toml"),
    format="toml",
    table="",
    roots="each",
)


def _path_specs() -> list[profiles.ConfigFile]:
    bundled = [spec for profile in profiles.load_profiles() for spec in profile.config_files]
    return [*bundled, _BRACED, dataclasses.replace(_BRACED, roots="first")]


@pytest.mark.parametrize("variables", ["unset", "set", "set_before_the_harness_wrote"])
@pytest.mark.parametrize("spec", _path_specs(), ids=lambda spec: f"{spec.paths[0]}:{spec.roots}")
def test_doctor_and_configure_resolve_a_profiles_config_paths_alike(
    spec: profiles.ConfigFile,
    variables: str,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every variable the paths name points into tmp_path or is unset, so no file is
    # ever created outside the test's directories. `set_before_the_harness_wrote`: a set
    # variable names a directory with no file yet, and only the default location holds
    # one.
    variables_set = variables != "unset"
    variable_files = variables == "set"
    names = set(re.findall(r"\$\{?(\w+)", " ".join(spec.paths)))
    environ = {name: str(tmp_path / name.lower()) for name in names if variables_set}
    for name in names:
        monkeypatch.delenv(name, raising=False)
    for name, value in environ.items():
        Path(value).mkdir()
        monkeypatch.setenv(name, value)
    for raw in spec.paths:
        path = profiles.expand_config_path(raw, environ, home)
        if path is None:
            continue
        assert path.is_relative_to(tmp_path), path
        path.parent.mkdir(parents=True, exist_ok=True)
        if variable_files or "$" not in raw:
            path.touch()
    probes = doctor_harness.HarnessProbes(
        which=lambda _binary: None, version=lambda _argv: "", environ=environ, home=home
    )

    found = [path.resolve() for path in doctor_harness.config_paths(spec, probes)]

    assert found
    assert found == harness_config.config_targets(spec)
    named = profiles.expand_config_path(spec.paths[0], environ, home)
    if variables_set and "$" in spec.paths[0]:
        # The path a set variable names is always among them, existing or not.
        assert named is not None
        assert found[0] == named.resolve()


@pytest.mark.parametrize("config_dir_set", [False, True])
def test_the_hooks_installer_edits_the_settings_file_configure_writes(
    config_dir_set: bool,
    capsys: pytest.CaptureFixture[str],
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _claude_settings(home)
    if config_dir_set:
        config_dir = tmp_path / "claude-config"
        config_dir.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    [target] = harness_config.config_targets(profiles.resolve("claude-code").config_files[0])

    assert installer().main(["--dry-run", "--runtime-dir", str(tmp_path / "runtime")]) == 0

    assert capsys.readouterr().out.startswith(f"--- {target}\n")


def _fake_binary(directory: Path, name: str, output: str) -> Path:
    """Write an executable that records its arguments, prints ``output``, and exits."""
    binary = directory / name
    binary.write_text(
        f'#!/bin/sh\necho "$@" > "{directory / (name + ".ran")}"\nprintf "{output}"\nexit 0\n'
    )
    binary.chmod(0o755)
    return binary


def test_harness_list_reports_status_enablement_the_binary_and_its_version(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    claude = _fake_binary(binaries, "claude", "2.1.285 (Claude Code)\\n")
    _fake_binary(binaries, "omp", "omp/0.0.1\\n")
    _fake_binary(binaries, "opencode", "")
    monkeypatch.setenv("PATH", str(binaries))
    config = _config(tmp_path, '[harnesses]\nenabled = ["claude-code", "codex_exec"]\n')

    result, _ = _harness(capsys, config, "list")

    listed = {item["key"]: item for item in result["harnesses"]}
    assert result["enabled"] == ["codex_exec", "claude-code"]
    assert list(listed) == ["claude-code", "codex", "omp", "opencode"]
    assert (listed["claude-code"]["binary_found"], listed["claude-code"]["binary_path"]) == (
        True,
        str(claude),
    )
    assert listed["codex"]["binary_found"] is False
    assert (listed["codex"]["enabled"], listed["codex"]["enabled_keys"]) == (True, ["codex_exec"])
    assert listed["omp"]["enabled"] is False
    assert (listed["opencode"]["status"], listed["opencode"]["enabled"]) == ("candidate", False)
    versions = {
        key: (item["installed_version"], item["version_verified"], item.get("version_note"))
        for key, item in listed.items()
    }
    assert versions == {
        "claude-code": ("2.1.285", True, None),
        "codex": (None, None, None),
        "omp": ("0.0.1", False, None),
        "opencode": (None, None, "`opencode --version` printed no version"),
    }
    assert (binaries / "claude.ran").read_text() == "--version\n"


def test_harness_list_no_version_runs_no_binary(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    _fake_binary(binaries, "claude", "2.1.285\\n")
    monkeypatch.setenv("PATH", str(binaries))

    result, _ = _harness(capsys, _config(tmp_path), "list", "--no-version")

    claude = next(item for item in result["harnesses"] if item["key"] == "claude-code")
    assert claude["binary_found"] is True
    assert (claude["installed_version"], claude["version_note"]) == (None, "not run (--no-version)")
    assert not (binaries / "claude.ran").exists()
    no_arguments = dataclasses.replace(profiles.resolve("claude-code"), version_args=())
    assert harness_config.listed_version(no_arguments, "/no/such/claude", run=True) == {
        "installed_version": None,
        "version_verified": None,
        "version_note": "not run (the profile names no version_args)",
    }


def test_each_version_probe_is_bounded_and_a_failure_is_reported(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for name in ("claude", "codex"):
        _fake_binary(binaries, name, "1.0.0\\n")
    monkeypatch.setenv("PATH", str(binaries))
    calls: list[dict[str, Any]] = []

    def hangs(argv: Sequence[str], **options: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": list(argv), **options})
        raise subprocess.TimeoutExpired(argv, options["timeout"])

    monkeypatch.setattr(subprocess, "run", hangs)

    result, _ = _harness(capsys, _config(tmp_path), "list")

    notes = {item["key"]: item.get("version_note") for item in result["harnesses"]}
    assert notes["claude-code"] == "`claude --version` failed (TimeoutExpired)"
    assert notes["codex"] == "`codex --version` failed (TimeoutExpired)"
    assert [call["argv"][1:] for call in calls] == [["--version"], ["--version"]]
    assert all(0 < call["timeout"] <= 5 for call in calls)
    assert all(call["stdin"] == subprocess.DEVNULL for call in calls)


def test_impact_of_a_candidate_lists_what_it_would_hide(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    result, _ = _harness(capsys, _config(tmp_path), "impact", "opencode")

    assert (result["candidate"], result["status"]) == ("opencode", "candidate")
    assert result["hidden"]
    assert len(result["visible_after"]) == len(result["visible_now"]) - len(result["hidden"])
    assert list(result["projection"]) == ["opencode"]
    assert sum(result["projection"]["opencode"].values()) == len(facts.load_registry().requires)
    assert "already_enabled" not in result


def test_impact_of_an_enabled_harness_shows_what_it_keeps_hidden(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    result, _ = _harness(capsys, _config(tmp_path), "impact", "codex_exec")

    assert result["already_enabled"] == ["codex-app-server", "codex_cli_rs", "codex_exec"]
    assert result["hidden"] == []
    assert result["visible_after"] == result["visible_now"]
    assert list(result["projection"]) == ["codex-app-server", "codex_cli_rs", "codex_exec"]
    assert result["hidden_now"]
    assert all(set(row) == {"signal", "title", "missing", "note"} for row in result["hidden_now"])


def test_impact_of_a_registered_harness_not_enabled_here(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = _config(tmp_path, '[harnesses]\nenabled = ["codex", "oh-my-pi"]\n')

    result, _ = _harness(capsys, config, "impact", "claude-code")

    assert result["enabled"] == ["oh-my-pi", "codex-app-server", "codex_cli_rs", "codex_exec"]
    assert "already_enabled" not in result
    assert {row["signal"] for row in result["hidden"]} <= set(result["visible_now"])


def test_impact_reads_a_draft_profile_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    draft = tmp_path / "draft.toml"
    draft.write_text(files(profiles.PACKAGE).joinpath("opencode.toml").read_text())
    config = _config(tmp_path)

    result, _ = _harness(capsys, config, "impact", str(draft))
    missing = _refused(capsys, config, "impact", str(tmp_path / "absent.toml"))

    assert result["candidate"] == "opencode"
    assert "no profile file at" in missing
