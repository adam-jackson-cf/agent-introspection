"""``agent-introspection setup``: the whole single-machine install in one command.

The steps run in order, and the first that fails stops the run:

1. ``config``: write the choices given as options into the config file, through the
   line-preserving TOML writer (comments and other keys kept, the file backed up), and
   check the result loads. Only options that were given are written; everything else
   keeps its default. An additional machine is refused: it has its own workflow.
2. ``signoz``: every preflight check, read-only (the connection, version, columns,
   grants, and producer data).
3. ``facts``: ``facts install``, the workflow store, then ``facts backfill``.
4. ``harnesses``: ``harness configure`` for each enabled harness (export settings,
   activity hooks, and, with ``prompt_export``, prompt export).
5. ``schedule``: the minute ``facts sync`` job.
6. ``dashboard``: the dashboard service, built from the checkout (unless skipped).
7. ``doctor``: every check, as ``agent-introspection doctor`` reports it.

Every step is idempotent, so a rerun is a reinstall. ``progress`` hears one line as each
step starts and ends (the CLI prints them to stderr), since ``facts`` alone can take
minutes on a busy SigNoz. ``dry_run`` reports what each step
would do and changes nothing: the config diff, the read-only preflight against the
options given, each harness's planned changes, and the refusals the schedule and
dashboard would hit. It passes when the real run would get through every step; it
runs no ``doctor``, whose report of a machine not yet set up would only fail.
``setup`` never installs SigNoz; that stays the onboarding skill's opt-in guided
script.
"""

from __future__ import annotations

import difflib
import sqlite3
import subprocess
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
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
    DEFAULT_CONFIG_PATH,
    AppConfig,
    ConfigurationError,
    parse_config,
    require_host,
)
from agent_introspection.workflow import connect_workflow

DEFAULT_BACKFILL_DAYS: Final = 90

type Progress = Callable[[str], None]

# What each step does, as the progress line names it while it runs.
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
    """The user's decisions, gathered by the onboarding skill; None keeps a key as it is."""

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
    repo: Path = Path()
    dry_run: bool = False


class SetupError(RuntimeError):
    """A step failed; ``step`` names it and the run stops there."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(detail)
        self.step = step


def _settings(options: SetupOptions) -> dict[str, dict[str, object]]:
    """Return the config values the options set, by table."""
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
    }
    return {table: values for table, values in tables.items() if values}


def write_config(options: SetupOptions) -> tuple[AppConfig, dict[str, Any]]:
    """Write the options' settings into the config file; return the config and a report."""
    path = (options.config_path or DEFAULT_CONFIG_PATH).expanduser()
    before = path.read_text("utf-8") if path.exists() else ""
    text = before
    try:
        for table, values in _settings(options).items():
            text = config_writers.update_table(text, "toml", table, values).text
        config = parse_config(tomllib.loads(text))
        require_host(config, "setup installs the SigNoz host's facts store and schedule")
    except (config_writers.ConfigFileError, tomllib.TOMLDecodeError, ConfigurationError) as exc:
        raise SetupError("config", f"{path}: {exc}") from None
    # A missing file is always written: the later steps load it from this path.
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


def _facts(config: AppConfig, options: SetupOptions) -> dict[str, Any]:
    if options.dry_run:
        return {"planned": ["facts install", f"facts backfill --days {options.backfill_days}"]}
    run = facts.runner(config)
    installed = facts.install(run, config.harnesses_enabled)
    connect_workflow(config.database.path, busy_timeout_ms=config.database.busy_timeout_ms).close()
    return {"install": installed, "backfill": facts.backfill(run, days=options.backfill_days)}


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
        schedule.platform_scheduler()
        schedule.job_path(config)
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
    """Run one step between a start and an end progress line; a failure ends it too."""
    doing = _STEP_DOING[name]
    if name == "facts" and not dry_run:
        doing += " (a long backfill can take minutes)"
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
    """Run every step in order; stop at the first that fails and report how far it got."""
    steps: dict[str, Any] = {}

    def step(name: str, produce: Callable[[], dict[str, Any]]) -> None:
        steps[name] = _announced(name, lambda: _step(name, produce), progress, options.dry_run)

    try:
        config, steps["config"] = _announced(
            "config", lambda: write_config(options), progress, options.dry_run
        )
        step("signoz", lambda: _signoz(config))
        step("facts", lambda: _facts(config, options))
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
