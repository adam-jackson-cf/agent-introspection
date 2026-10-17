from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_introspection import doctor_harness, facts, producers, profiles
from agent_introspection.config import ConfigurationError
from tests.support.doctor_machine import (
    CLAUDE_ENV,
    CODEX_HTTP,
    SECRET,
    Machine,
    build_machine,
    find,
    one,
    replace_settings,
    settings_of,
    telemetry_rows,
    write,
)
from tests.test_producers import FakeClickHouse, FakeService


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


def test_grpc_exporters_satisfy_the_profiles_http_endpoint_and_protocol(machine: Machine) -> None:
    result = machine.report()

    claude = settings_of(one(result, "export_settings", "claude-code"))
    assert claude["OTEL_EXPORTER_OTLP_ENDPOINT"] == ("pass", "satisfied (grpc)")
    assert claude["OTEL_EXPORTER_OTLP_PROTOCOL"] == ("pass", "satisfied (grpc)")
    assert claude["CLAUDE_CODE_ENABLE_TELEMETRY"] == ("pass", "matches")
    codex = {item["path"]: settings_of(item) for item in find(result, "export_settings", "codex")}
    orca = doctor_harness.shown_path(machine.orca / "config.toml", machine.home)
    assert set(codex) == {"~/.codex/config.toml", orca}
    assert codex["~/.codex/config.toml"]["exporter"] == ("pass", "satisfied (grpc)")
    assert codex[orca]["exporter"] == ("pass", "matches")
    details = {item["detail"] for item in find(result, "export_settings", "codex")}
    assert details == {
        "~/.codex/config.toml: satisfied (grpc): exporter, trace_exporter, metrics_exporter",
        f"{orca}: 3 match",
    }


def test_a_grpc_port_with_an_http_protocol_does_not_reach_the_collector(
    machine: Machine,
) -> None:
    replace_settings(machine, {**CLAUDE_ENV, "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"})

    result = machine.report()

    item = one(result, "export_settings", "claude-code")
    assert item["status"] == "fail"
    assert settings_of(item)["OTEL_EXPORTER_OTLP_ENDPOINT"] == (
        "fail",
        "is 'http://localhost:4317', expected 'http://127.0.0.1:4318'",
    )
    assert item["fix"] == "agent-introspection harness configure claude-code"
    assert result["passed"] is False


def test_a_missing_telemetry_switch_fails_and_a_missing_service_name_only_warns(
    machine: Machine,
) -> None:
    env = {
        key: value
        for key, value in CLAUDE_ENV.items()
        if key not in ("CLAUDE_CODE_ENABLE_TELEMETRY", "OTEL_SERVICE_NAME")
    }
    replace_settings(machine, env)
    (machine.orca / "config.toml").write_text(CODEX_HTTP.replace("/v1/traces", "/v1/logs"))

    result = machine.report()

    claude = settings_of(one(result, "export_settings", "claude-code"))
    assert claude["CLAUDE_CODE_ENABLE_TELEMETRY"] == ("fail", "missing")
    assert claude["OTEL_SERVICE_NAME"] == ("warn", "missing")
    orca = next(
        item
        for item in find(result, "export_settings", "codex")
        if item["path"] != "~/.codex/config.toml"
    )
    assert settings_of(orca)["trace_exporter"][0] == "fail"
    # The fix pastes into a shell as one --root argument, with ~ left to expand.
    root = f"~/{machine.orca.relative_to(machine.home).as_posix()}"
    assert " --root ~/" in orca["fix"]
    assert shlex.split(orca["fix"]) == [
        "agent-introspection",
        "harness",
        "configure",
        "codex",
        "--root",
        root,
    ]


def test_only_the_service_name_missing_is_a_warning(machine: Machine) -> None:
    replace_settings(
        machine, {key: v for key, v in CLAUDE_ENV.items() if key != "OTEL_SERVICE_NAME"}
    )

    result = machine.report()

    item = one(result, "export_settings", "claude-code")
    assert item["status"] == "warn"
    assert item["detail"] == (
        "5 match; satisfied (grpc): OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_EXPORTER_OTLP_PROTOCOL; "
        "missing: OTEL_SERVICE_NAME"
    )
    assert result["passed"] is True


def test_prompt_export_is_reported_on_or_off_and_never_fails(machine: Machine) -> None:
    result = machine.report()

    assert one(result, "prompt_export", "omp")["status"] == "pass"
    claude = one(result, "prompt_export", "claude-code")
    assert claude["status"] == "off"
    assert "OTEL_LOG_USER_PROMPTS" in claude["detail"]
    assert "--prompt-export" in claude["fix"]
    assert result["passed"] is True
    # Claude Code's prompt switch alone also exports assistant responses: a warning.
    replace_settings(machine, {**CLAUDE_ENV, "OTEL_LOG_USER_PROMPTS": "1"})
    partly = one(machine.report(), "prompt_export", "claude-code")
    assert partly["status"] == "warn"
    assert partly["detail"] == (
        "partly on: OTEL_LOG_ASSISTANT_RESPONSES in ~/.claude/settings.json not set"
    )
    assert partly["fix"] == (
        "`agent-introspection harness configure claude-code --prompt-export` sets "
        "OTEL_LOG_ASSISTANT_RESPONSES to '0'; or remove OTEL_LOG_USER_PROMPTS from "
        "~/.claude/settings.json to keep prompt export off"
    )
    replace_settings(
        machine,
        {**CLAUDE_ENV, "OTEL_LOG_USER_PROMPTS": "1", "OTEL_LOG_ASSISTANT_RESPONSES": "0"},
    )
    assert one(machine.report(), "prompt_export", "claude-code")["status"] == "pass"


@pytest.mark.parametrize(
    ("main", "orca", "expected"),
    [
        (
            "false",
            "true",
            (
                "warn",
                "partly on: on in {orca}; "
                "log_user_prompt in ~/.codex/config.toml is false, where the profile writes true",
                "`agent-introspection harness configure codex --prompt-export --root ~/.codex` "
                "sets log_user_prompt to true; or remove log_user_prompt from {orca} "
                "to keep prompt export off",
            ),
        ),
        (
            "true",
            None,
            (
                "warn",
                "partly on: on in ~/.codex/config.toml; log_user_prompt in {orca} not set",
                "`agent-introspection harness configure codex --prompt-export --root "
                "{orca_root}` sets log_user_prompt to true; or remove log_user_prompt from "
                "~/.codex/config.toml to keep prompt export off",
            ),
        ),
        (
            "false",
            None,
            (
                "off",
                "off: log_user_prompt in ~/.codex/config.toml is false, where the profile "
                "writes true; log_user_prompt in {orca} not set",
                "optional: `agent-introspection harness configure codex --prompt-export "
                "--root ~/.codex --root {orca_root}` exports prompt text so `facts sync` can "
                "label prompts; the facts store keeps only the labels",
            ),
        ),
        ("true", "true", ("pass", "on: prompt text is exported for prompt labels", "")),
    ],
    ids=["only-orca-exports", "only-main-exports", "neither-exports", "both-export"],
)
def test_codex_prompt_export_names_every_home_that_exports_or_differs(
    machine: Machine, main: str, orca: str | None, expected: tuple[str, str, str]
) -> None:
    # Codex reads `[otel]` from each of its homes: one that exports prompt text while
    # another does not is never reported as off, and each fix names the homes it writes.
    main_config = machine.home / ".codex/config.toml"
    orca_config = machine.orca / "config.toml"
    for config, value in ((main_config, main), (orca_config, orca)):
        line = "" if value is None else f"log_user_prompt = {value}\n"
        config.write_text(re.sub(r"(?m)^log_user_prompt = \w+\n", line, config.read_text()))
    names = {
        "orca": doctor_harness.shown_path(orca_config, machine.home),
        "orca_root": doctor_harness.shell_path(machine.orca, machine.home),
    }

    result = machine.report()

    item = one(result, "prompt_export", "codex")
    assert (item["status"], item["detail"], item["fix"]) == tuple(
        text.format(**names) for text in expected
    )
    assert result["passed"] is True


@pytest.mark.parametrize("responses", ["1", "false"])
def test_a_prompt_export_value_set_by_hand_is_reported_as_it_is_never_as_unset(
    machine: Machine, responses: str
) -> None:
    # Exporting assistant responses (`1`) is a documented choice: doctor names the
    # value and says what `--prompt-export` would write over it, never "not set".
    replace_settings(
        machine,
        {**CLAUDE_ENV, "OTEL_LOG_USER_PROMPTS": "1", "OTEL_LOG_ASSISTANT_RESPONSES": responses},
    )

    result = machine.report()

    item = one(result, "prompt_export", "claude-code")
    assert item["status"] == "info"
    assert item["detail"] == (
        f"set by hand: OTEL_LOG_ASSISTANT_RESPONSES in ~/.claude/settings.json is "
        f"'{responses}', where the profile writes '0'"
    )
    assert "not set" not in item["detail"]
    assert item["fix"] == (
        "keep a value you set on purpose; `agent-introspection harness configure "
        "claude-code --prompt-export` would set OTEL_LOG_ASSISTANT_RESPONSES to '0'"
    )
    assert result["passed"] is True


@pytest.mark.parametrize(
    ("prompts", "responses", "detail"),
    [
        (
            "0",
            None,
            "off: OTEL_LOG_USER_PROMPTS in ~/.claude/settings.json is '0', where the profile "
            "writes '1'; OTEL_LOG_ASSISTANT_RESPONSES in ~/.claude/settings.json not set",
        ),
        # Switched off after `--prompt-export`: the redaction setting it wrote stays.
        (
            "0",
            "0",
            "off: OTEL_LOG_USER_PROMPTS in ~/.claude/settings.json is '0', where the profile "
            "writes '1'",
        ),
        (None, "0", "off: OTEL_LOG_USER_PROMPTS in ~/.claude/settings.json not set"),
        (
            None,
            "1",
            "off: OTEL_LOG_USER_PROMPTS in ~/.claude/settings.json not set; "
            "OTEL_LOG_ASSISTANT_RESPONSES in ~/.claude/settings.json is '1', where the profile "
            "writes '0'",
        ),
    ],
    ids=["switch-0", "switch-0-partner-0", "switch-unset-partner-0", "switch-unset-partner-1"],
)
def test_prompt_export_is_off_while_its_switch_is_off_whatever_its_partner_holds(
    machine: Machine, prompts: str | None, responses: str | None, detail: str
) -> None:
    # Claude Code exports no prompt text unless OTEL_LOG_USER_PROMPTS is on, so its
    # partner never makes the harness "partly on"; a partner set by hand is still named.
    settings = {"OTEL_LOG_USER_PROMPTS": prompts, "OTEL_LOG_ASSISTANT_RESPONSES": responses}
    replace_settings(
        machine, {**CLAUDE_ENV, **{key: v for key, v in settings.items() if v is not None}}
    )

    result = machine.report()

    item = one(result, "prompt_export", "claude-code")
    assert (item["status"], item["detail"]) == ("off", detail)
    assert item["fix"].startswith(
        "optional: `agent-introspection harness configure claude-code --prompt-export` "
    )
    assert result["passed"] is True


def test_unreadable_config_files_fail_and_skip_their_settings(machine: Machine) -> None:
    (machine.home / ".claude/settings.json").write_text("{not json")
    (machine.home / ".omp/.env").unlink()

    result = machine.report()

    claude = one(result, "config_files", "claude-code")
    assert claude["status"] == "fail"
    assert claude["detail"] == "cannot be read: ~/.claude/settings.json (ConfigFileError)"
    assert find(result, "export_settings", "claude-code") == []
    assert one(result, "activity_hooks", "claude-code")["status"] == "fail"
    omp = one(result, "config_files", "omp")
    assert omp["detail"] == "no file at ~/.omp/.env"
    assert omp["fix"] == "agent-introspection harness configure omp"


def test_missing_hooks_a_bare_shim_and_a_foreign_plugin_link_fail(machine: Machine) -> None:
    path = machine.home / ".claude/settings.json"
    settings = json.loads(path.read_text())
    del settings["hooks"]["Stop"]
    settings["hooks"]["PreToolUse"][-1]["matcher"] = "Bash"
    path.write_text(json.dumps(settings))
    (machine.home / doctor_harness.MANAGED_RUNTIME / doctor_harness.SHIM).chmod(0o644)
    link = machine.home / doctor_harness.OMP_PLUGIN_LINK
    link.unlink()
    link.symlink_to(machine.home)

    result = machine.report()

    claude = one(result, "activity_hooks", "claude-code")
    assert claude["status"] == "fail"
    assert claude["detail"].endswith("events do not run the managed shim: PreToolUse, Stop")
    assert (
        claude["fix"]
        == "`agent-introspection harness configure claude-code` (with --dry-run first)"
    )
    assert one(result, "activity_shim", "omp")["detail"].endswith("is not executable")
    omp = one(result, "activity_hooks", "omp")
    assert "does not resolve to ~/.local/lib/agent-introspection" in omp["detail"]
    assert omp["fix"] == "`agent-introspection harness configure omp` (with --dry-run first)"


@pytest.mark.parametrize(
    ("change", "detail"),
    [
        (lambda link, managed: link.unlink(), "does not exist"),
        (lambda link, managed: (managed / "activity.ts").unlink(), "the plugin lacks activity.ts"),
        (
            lambda link, managed: (link.unlink(), link.symlink_to(managed / "gone")),
            "is a broken link",
        ),
    ],
)
def test_omp_plugin_problems(
    machine: Machine, change: Callable[[Path, Path], object], detail: str
) -> None:
    managed = machine.home / doctor_harness.MANAGED_RUNTIME / "omp"
    change(machine.home / doctor_harness.OMP_PLUGIN_LINK, managed)

    omp = one(machine.report(), "activity_hooks", "omp")

    assert omp["status"] == "fail"
    assert omp["detail"].endswith(detail)


def test_an_omp_plugin_older_than_the_cli_warns(machine: Machine) -> None:
    managed = machine.home / doctor_harness.MANAGED_RUNTIME / "omp"
    (managed / "activity.ts").write_text("export default {}\n")

    omp = one(machine.report(), "activity_hooks", "omp")

    assert (omp["status"], omp["detail"]) == (
        "warn",
        "the plugin is out of date: activity.ts differ from what this CLI ships",
    )


def test_a_missing_shim_fails_both_hook_adapters(machine: Machine) -> None:
    (machine.home / doctor_harness.MANAGED_RUNTIME / doctor_harness.SHIM).unlink()

    result = machine.report()

    assert one(result, "activity_shim", "claude-code")["status"] == "fail"
    assert one(result, "activity_shim", "omp")["detail"].endswith("does not exist")


@pytest.mark.parametrize(
    ("installed", "expected", "detail", "fix"),
    [
        ("2.1.285", "pass", "claude 2.1.285 (verified)", ""),
        ("99.0.0", "info", "claude 99.0.0 is newer than the verified 2.1.285", "doctor --drift"),
        ("1.0.0", "info", "claude 1.0.0 is not a verified version (2.1.285 is)", "update"),
        ("", "warn", "claude printed no version", "claude --version"),
    ],
)
def test_binary_versions_are_compared_with_the_verified_ones(
    installed: str, expected: str, detail: str, fix: str
) -> None:
    profile = replace(profiles.resolve("claude-code"), verified_versions=("2.1.285",))
    probes = doctor_harness.HarnessProbes(
        which=lambda _binary: "/bin/claude",
        version=lambda _argv: installed,
        environ={},
        home=Path("/home/user"),
    )

    found = doctor_harness.binary_check(profile, probes)

    assert (found["status"], found["detail"]) == (expected, detail)
    assert fix in found["fix"]


def test_a_binary_off_path_or_hanging_warns() -> None:
    profile = profiles.resolve("omp")

    def hangs(argv: Sequence[str]) -> str:
        raise subprocess.TimeoutExpired(list(argv), 5)

    probes = doctor_harness.HarnessProbes(
        which=lambda _binary: None, version=hangs, environ={}, home=Path("/home/user")
    )
    assert doctor_harness.binary_check(profile, probes)["detail"] == "omp is not on PATH"

    found = doctor_harness.binary_check(profile, replace(probes, which=lambda _b: "/bin/omp"))
    assert (found["status"], found["detail"]) == ("warn", "omp printed no version (TimeoutExpired)")


def test_installed_version_runs_the_binary_and_reads_its_version() -> None:
    version = doctor_harness.installed_version([sys.executable, "--version"])

    assert version == ".".join(map(str, sys.version_info[:3]))


def test_idle_harnesses_warn_and_a_failed_telemetry_query_fails(machine: Machine) -> None:
    machine.telemetry["oh-my-pi"] = (0, 0)

    omp = one(machine.report(), "telemetry", "omp")
    assert omp["status"] == "warn"
    assert omp["detail"].startswith("no rows in 24 h; last seen")

    run = machine.run_sql()

    def no_telemetry(sql: str) -> str:
        if "rows_24h" in sql:
            raise facts.FactsError("Code: 60. Unknown table")
        answer: str = run(sql)
        return answer

    result = machine.report(replace(machine.probes, connect=lambda _config: no_telemetry))

    telemetry = one(result, "telemetry", "omp")
    assert (telemetry["status"], telemetry["detail"]) == (
        "fail",
        "the telemetry query raised FactsError",
    )
    assert not find(result, "session_store")


def test_telemetry_counts_use_one_query_and_ignore_unknown_rows() -> None:
    executed: list[str] = []

    def run(sql: str) -> str:
        executed.append(sql)
        rows: str = telemetry_rows({"claude-code": (2, 4), "stray": (9, 9)}, 1_000)
        return rows

    counts = doctor_harness.telemetry_counts(run, ["claude-code", "codex_exec"])

    assert counts == {
        "claude-code": {"spans": 2, "logs": 4, "last_seen": 1_000},
        "codex_exec": {"spans": 0, "logs": 0, "last_seen": 0},
    }
    assert len(executed) == 1
    assert doctor_harness.telemetry_counts(run, []) == {}
    assert len(executed) == 1


def test_shown_values_drop_credentials_queries_and_headers() -> None:
    shown = doctor_harness.shown_value

    assert shown(f"https://u:{SECRET}@host:4318/v1?k={SECRET}#f") == "'https://host:4318/v1'"
    assert shown({"otlp-http": {"endpoint": "http://h/v1/logs", "headers": {"k": SECRET}}}) == (
        "otlp-http http://h/v1/logs"
    )
    assert shown({"a": 1, "b": 2}) == "a table of a, b"
    assert shown(True) == "true"
    assert shown(3) == "3"
    assert shown(["x"]) == "list"
    assert shown("x" * 100) == "'" + "x" * 57 + "...'"
    assert shown("http://host:bad/") == "'http://host/'"
    assert doctor_harness.age(30) == "0 min"
    assert doctor_harness.age(3 * 3600) == "3 h"
    assert doctor_harness.age(3 * 86_400) == "3 d"


def test_config_paths_skip_unset_variables_and_take_the_first_path_whose_variables_are_set(
    tmp_path: Path,
) -> None:
    claude = profiles.resolve("claude-code").config_files[0]
    home = tmp_path / "home"
    other = tmp_path / "other"
    write(home / ".claude/settings.json", "{}")
    other.mkdir()
    probes = doctor_harness.HarnessProbes(
        which=lambda _binary: None, version=lambda _argv: "", environ={}, home=home
    )

    assert doctor_harness.config_paths(claude, probes) == [home / ".claude/settings.json"]
    # A set CLAUDE_CONFIG_DIR replaces ~/.claude for Claude Code, even before it holds a
    # settings.json: that file is the one to read, and it is missing.
    chosen = replace(probes, environ={"CLAUDE_CONFIG_DIR": str(other)})
    assert doctor_harness.config_paths(claude, chosen) == [other / "settings.json"]
    assert doctor_harness.load_file(claude, other / "settings.json").error == "missing"
    write(other / "settings.json", "{}")
    assert doctor_harness.config_paths(claude, chosen) == [other / "settings.json"]
    assert doctor_harness.shown_path(other, home) == str(other)


def test_guarded_turns_any_exception_into_a_failing_check() -> None:
    def explode() -> dict[str, Any]:
        raise ConfigurationError(SECRET)

    assert doctor_harness.guarded("x", explode, harness="h") == [
        {
            "name": "x",
            "status": "fail",
            "detail": "the check raised ConfigurationError",
            "fix": doctor_harness.RAISED_FIX,
            "harness": "h",
        }
    ]
    assert doctor_harness.worst([]) == "pass"
    assert doctor_harness.worst(["off", "warn", "info"]) == "warn"


def test_doctor_drift_suggests_profiling_only_services_that_have_a_name(
    machine: Machine, tmp_path: Path
) -> None:
    clickhouse = FakeClickHouse({
        "": FakeService(span_rows=3),
        " ": FakeService(log_rows=1),
        "toolbox": FakeService(span_rows=9),
    })

    def drift(_run: facts.SqlRunner, _saved: Path | None, services: Sequence[str]) -> Any:
        return producers.drift_report(clickhouse, tmp_path / "fingerprints", services)

    result = machine.report(replace(machine.probes, drift_report=drift), drift=True)

    fixes = " ".join(item["fix"] for item in find(result, "drift"))
    assert re.findall(r"producers profile ([^`]*)`", fixes) == ["toolbox"]
