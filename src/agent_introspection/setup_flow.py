from __future__ import annotations

import difflib
import sqlite3
import subprocess
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from agent_introspection import (
    cli_doctor,
    config_writers,
    dashboard_service,
    doctor,
    facts,
    harness_config,
    preflight,
    schedule,
)
from agent_introspection.config import (
    DEFAULT_HARNESSES,
    AppConfig,
    ConfigurationError,
    default_config_path,
    parse_config,
    require_host,
    retired_paths,
)
from agent_introspection.workflow import connect_workflow

DEFAULT_BACKFILL_DAYS: Final = 90

type Progress = Callable[[str], None]

_STEP_DOING: Final = {
    "config": "writing the config",
    "signoz": "checking SigNoz",
    "facts": "installing the facts store and backfilling",
    "harnesses": "configuring the harnesses",
    "schedule": "scheduling the sync",
    "dashboard": "building and starting the dashboard",
    "doctor": "running doctor",
}


@dataclass(frozen=True)
class SetupOptions:
    config_path: Path | None = None
    clickhouse_url: str | None = None
    clickhouse_user: str | None = None
    clickhouse_password_command: tuple[str, ...] | None = None
    otlp_endpoint: str | None = None
    harnesses: tuple[str, ...] | None = None
    prompt_export: bool = False
    openrouter_key_command: tuple[str, ...] | None = None
    backfill_days: int = DEFAULT_BACKFILL_DAYS
    dashboard: bool = True
    dashboard_port: int | None = None
    repo: Path = Path()
    dry_run: bool = False


class SetupError(RuntimeError):
    def __init__(self, step: str, detail: str) -> None:
        super().__init__(detail)
        self.step = step


def _settings(options: SetupOptions) -> dict[str, dict[str, object]]:
    signoz = {
        "clickhouse_url": options.clickhouse_url,
        "clickhouse_user": options.clickhouse_user,
        "clickhouse_password_command": (
            list(options.clickhouse_password_command)
            if options.clickhouse_password_command
            else None
        ),
        "otlp_endpoint": options.otlp_endpoint,
    }
    tables: dict[str, dict[str, object]] = {
        "signoz": {key: value for key, value in signoz.items() if value is not None},
        "harnesses": {"enabled": list(options.harnesses)} if options.harnesses else {},
        "labels": (
            {"openrouter_key_command": list(options.openrouter_key_command)}
            if options.openrouter_key_command
            else {}
        ),
        "dashboard": {"port": options.dashboard_port} if options.dashboard_port else {},
    }
    return {table: values for table, values in tables.items() if values}


def _upgraded(document: dict[str, Any], options: SetupOptions) -> tuple[SetupOptions, list[str]]:
    notes: list[str] = []
    dashboard = document.get("dashboard")
    signoz = document.get("signoz")
    moved = dashboard.get("clickhouse_url") if isinstance(dashboard, dict) else None
    has_url = isinstance(signoz, dict) and "clickhouse_url" in signoz
    if isinstance(moved, str) and options.clickhouse_url is None and not has_url:
        options = replace(options, clickhouse_url=moved)
        notes.append("[dashboard] clickhouse_url moved to [signoz] clickhouse_url")
    harnesses = document.get("harnesses")
    unset = not (isinstance(harnesses, dict) and "enabled" in harnesses)
    if (
        document
        and unset
        and options.harnesses is None
        and harness_config.omp_plugin_installed(Path.home())
    ):
        options = replace(options, harnesses=(*DEFAULT_HARNESSES, "oh-my-pi"))
        notes.append("omp kept: its plugin is installed, and omp is no longer enabled by default")
    return options, notes


def write_config(options: SetupOptions) -> tuple[AppConfig, dict[str, Any]]:
    path = (options.config_path or default_config_path()).expanduser().resolve()
    before = path.read_text("utf-8") if path.exists() else ""
    text = before
    retired: list[tuple[str, ...]] = []
    notes: list[str] = []
    try:
        document = tomllib.loads(before)
        options, notes = _upgraded(document, options)
        retired = retired_paths(document)
        if retired:
            text = config_writers.remove_toml_keys(text, retired)
        for table, values in _settings(options).items():
            text = config_writers.update_table(text, "toml", table, values).text
        config = parse_config(tomllib.loads(text))
        require_host(config, "setup installs the SigNoz host's facts store and schedule")
        facts.enabled_harnesses(facts.load_registry(), config.harnesses_enabled)
    except (config_writers.ConfigFileError, tomllib.TOMLDecodeError, ConfigurationError) as exc:
        raise SetupError("config", f"{path}: {exc}") from None
    changed = text != before or not path.exists()
    backup: Path | None = None
    if changed and not options.dry_run:
        try:
            mode = path.stat().st_mode & 0o777 if path.exists() else config_writers.NEW_FILE_MODE
            backup = config_writers.backup(path) if path.exists() else None
            config_writers.atomic_write(path, text, mode)
        except OSError as exc:
            raise SetupError("config", f"{path}: {exc}") from None
    diff = "".join(
        difflib.unified_diff(before.splitlines(True), text.splitlines(True), str(path), str(path))
    )
    report = {
        "path": str(path),
        "action": ("add" if not before else "change") if changed else "unchanged",
        "backup": str(backup) if backup else None,
        "diff": diff,
        "harnesses": list(config.harnesses_enabled),
        "retired_removed": [".".join(item) for item in retired],
        "notes": notes,
    }
    return config, report


def _signoz(config: AppConfig) -> dict[str, Any]:
    result = preflight.report(lambda: config, facts.runner)
    if not result["passed"]:
        failing = [
            f"{item['name']}: {item['detail']}"
            for item in result["checks"]
            if item["status"] == "fail"
        ]
        raise SetupError("signoz", "; ".join(failing))
    return {"passed": True}


def _facts(config: AppConfig, options: SetupOptions, progress: Progress) -> dict[str, Any]:
    if options.dry_run:
        return {"planned": ["facts install", f"facts backfill --days {options.backfill_days}"]}
    run = facts.runner(config)
    installed = facts.install(run, config.harnesses_enabled)
    connect_workflow(config.database.path, busy_timeout_ms=config.database.busy_timeout_ms).close()
    backfilled = facts.backfill(run, days=options.backfill_days, progress=progress)
    return {"install": installed, "backfill": backfilled}


def _harnesses(config: AppConfig, options: SetupOptions) -> dict[str, Any]:
    configure = harness_config.ConfigureOptions(
        dry_run=options.dry_run,
        prompt_export=options.prompt_export,
        config_path=options.config_path,
    )
    return {
        key: harness_config.configure(key, config.collector_endpoint, configure)
        for key in harness_config.profile_keys(config.harnesses_enabled)
    }


def _schedule(config: AppConfig, options: SetupOptions) -> dict[str, Any]:
    if options.dry_run:
        schedule.check(config)
        return {"planned": "facts schedule install"}
    return schedule.schedule_install(options.config_path)


def _dashboard(config: AppConfig, options: SetupOptions) -> dict[str, Any]:
    if not options.dashboard:
        return {"skipped": "--no-dashboard"}
    signoz = config.signoz
    if signoz.clickhouse_password_command is not None or signoz.clickhouse_password_env:
        return {
            "skipped": "a ClickHouse password: run the dashboard by hand "
            "(`bun run --cwd dashboard start`) with INTROSPECTION_CLICKHOUSE_PASSWORD set"
        }
    _scheduler, directory, _bun = dashboard_service.check(config, options.repo)
    if options.dry_run:
        return {"planned": f"dashboard install --repo {directory.parent}"}
    return dashboard_service.install(config, options.repo, options.config_path)


def _doctor(options: SetupOptions) -> dict[str, Any]:
    report = doctor.report(
        doctor.Options(config_path=options.config_path), cli_doctor.default_probes()
    )
    return {
        "passed": report["passed"],
        "summary": report["summary"],
        "not_passing": [item for item in report["checks"] if item["status"] in ("fail", "warn")],
    }


def _silent(_line: str) -> None:
    return


def _announced[T](name: str, run: Callable[[], T], progress: Progress, dry_run: bool) -> T:
    doing = _STEP_DOING[name]
    if name == "facts" and not dry_run:
        doing += " (a long backfill can take minutes; progress follows)"
    progress(f"setup: {name}: {doing}...")
    started = time.monotonic()
    outcome = "failed"
    try:
        result = run()
        outcome = "done"
        return result
    finally:
        progress(f"setup: {name}: {outcome} in {time.monotonic() - started:.1f}s")


def _step(name: str, run: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return run()
    except SetupError:
        raise
    except (
        OSError,
        RuntimeError,
        ValueError,
        facts.FactsError,
        sqlite3.Error,
        subprocess.SubprocessError,
    ) as exc:
        raise SetupError(name, f"{type(exc).__name__}: {exc}") from None


def run(options: SetupOptions, progress: Progress = _silent) -> dict[str, Any]:
    steps: dict[str, Any] = {}

    def step(name: str, produce: Callable[[], dict[str, Any]]) -> None:
        steps[name] = _announced(name, lambda: _step(name, produce), progress, options.dry_run)

    try:
        config, steps["config"] = _announced(
            "config", lambda: write_config(options), progress, options.dry_run
        )
        step("signoz", lambda: _signoz(config))
        step("facts", lambda: _facts(config, options, progress))
        step("harnesses", lambda: _harnesses(config, options))
        step("schedule", lambda: _schedule(config, options))
        step("dashboard", lambda: _dashboard(config, options))
    except SetupError as exc:
        return {
            "dry_run": options.dry_run,
            "passed": False,
            "stopped_at": exc.step,
            "error": str(exc),
            "steps": steps,
        }
    if options.dry_run:
        return {"dry_run": True, "passed": True, "steps": steps}
    checked = _announced("doctor", lambda: _doctor(options), progress, dry_run=False)
    return {
        "dry_run": options.dry_run,
        "passed": checked["passed"],
        "steps": steps,
        "doctor": checked,
    }
