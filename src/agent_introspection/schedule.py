"""The scheduled job that runs ``facts sync`` every minute on the SigNoz host.

macOS runs it as a launchd agent and Linux as a systemd user timer; other platforms
are refused, and so is an additional machine (``[machine] role = "additional"``).
Machines without SigNoz run ``spool sync`` from the onboarding skill's own systemd
units instead, whose names differ from these; ``spool_sync_status`` reports them.
"""

from __future__ import annotations

import getpass
import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from agent_introspection import spool
from agent_introspection.config import AppConfig, load_config, require_host

LABEL = "dev.agent-introspection.sync"
# The label earlier releases installed; install and remove retire it.
LEGACY_LABEL = "com.adamjackson.agent-introspection.sync"
LAUNCH_AGENTS = Path.home() / "Library/LaunchAgents"
PLIST = LAUNCH_AGENTS / f"{LABEL}.plist"
LEGACY_PLIST = LAUNCH_AGENTS / f"{LEGACY_LABEL}.plist"
# Distinct from the `agent-introspection-spool-sync` units of machines without SigNoz.
UNIT = "agent-introspection-sync"
SYSTEMD_UNITS = Path.home() / ".config/systemd/user"
SERVICE = SYSTEMD_UNITS / f"{UNIT}.service"
TIMER = SYSTEMD_UNITS / f"{UNIT}.timer"
# The onboarding skill's units that run `spool sync` on an additional machine (Linux).
SPOOL_SYNC_UNIT = "agent-introspection-spool-sync"
SPOOL_SYNC_SERVICE = SYSTEMD_UNITS / f"{SPOOL_SYNC_UNIT}.service"
SPOOL_SYNC_TIMER = SYSTEMD_UNITS / f"{SPOOL_SYNC_UNIT}.timer"
# The `systemctl --user` commands that load the unit files and start the timer.
_START = (("daemon-reload",), ("enable", "--now", TIMER.name))
# A user's timers run without a login session only when lingering is enabled.
LINGER = Path("/var/lib/systemd/linger")
LOG = Path.home() / ".local/share/agent-introspection/sync.log"
# What launchd itself captures (a crash before the sync can log): not rotated, rarely written.
LAUNCHD_LOG = LOG.with_name("sync-launchd.log")
# Job files that install or remove replaced, kept outside LaunchAgents so launchd
# never loads a backup as a second job.
BACKUPS = LOG.with_name("schedule-backups")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_FILES = 3
INTERVAL_SECONDS = 60
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
SCHEDULERS = {"darwin": "launchd", "linux": "systemd"}
# A launchctl or systemctl call that hangs fails instead of stalling the command.
SCHEDULER_TIMEOUT_SECONDS = 30


@dataclass(frozen=True, slots=True)
class RetiredJob:
    """A launchd job under ``LEGACY_LABEL`` that was stopped and whose plist was moved."""

    unloaded: bool
    backup: Path | None

    def report(self) -> dict[str, Any]:
        """Return the JSON-ready description of what was retired."""
        return {
            "label": LEGACY_LABEL,
            "unloaded": self.unloaded,
            "backup": str(self.backup) if self.backup is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ReplacedJob:
    """The job under ``LABEL`` an install replaced: its plist, and whether it was loaded."""

    plist: bytes
    loaded: bool


@dataclass(frozen=True, slots=True)
class TimerState:
    """Whether a systemd user timer is enabled (starts at login) and active (running)."""

    enabled: bool
    active: bool

    def restore(self) -> tuple[tuple[str, ...], ...]:
        """Return the ``systemctl --user`` commands that load the timer in this state."""
        return (
            ("daemon-reload",),
            ("enable" if self.enabled else "disable", TIMER.name),
            ("start" if self.active else "stop", TIMER.name),
        )


def _program_arguments(executable: Path, config: Path | None) -> list[str]:
    selected = ["--config", str(config)] if config is not None else []
    return [str(executable), *selected, "facts", "sync", "--log"]


def plist(executable: Path, path_dirs: Iterable[str], config: Path | None = None) -> bytes:
    """Return the launchd job that runs ``facts sync`` every minute.

    A selected ``config`` is passed through, so the job syncs the same deployment.
    ``--log`` makes the sync write its summary and errors to the rotated ``LOG`` with
    a UTC timestamp, which launchd's own file redirection cannot do.
    """
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": _program_arguments(executable, config),
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path_dirs))},
        "StandardOutPath": str(LAUNCHD_LOG),
        "StandardErrorPath": str(LAUNCHD_LOG),
    })


def _unit_quote(value: str) -> str:
    """Quote one unit-file value, escaping backslashes, quotes, and ``%`` specifiers."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def service_unit(executable: Path, path_dirs: Iterable[str], config: Path | None = None) -> str:
    """Return the systemd user service that runs ``facts sync`` once; its timer repeats it.

    It passes ``config`` and sets ``PATH`` as the launchd job does. ``--log`` writes
    to the rotated ``LOG``; anything else the job prints goes to the user journal.
    """
    # `$` would expand an environment variable in ExecStart; `$$` is a literal one.
    command = " ".join(
        _unit_quote(argument).replace("$", "$$")
        for argument in _program_arguments(executable, config)
    )
    path = _unit_quote(f"PATH={':'.join(dict.fromkeys(path_dirs))}")
    return (
        "[Unit]\n"
        "Description=Agent Introspection facts sync on the SigNoz host\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"Environment={path}\n"
        f"ExecStart={command}\n"
    )


def timer_unit() -> str:
    """Return the systemd user timer that starts the service every minute.

    ``Persistent=true`` runs a start missed while the machine was off or asleep.
    """
    return (
        "[Unit]\n"
        "Description=Run Agent Introspection facts sync every minute\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=minutely\n"
        "AccuracySec=1s\n"
        "Persistent=true\n"
        f"Unit={SERVICE.name}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _rotate(path: Path) -> None:
    """Shift ``sync.log`` to ``sync.log.1`` and so on once it reaches ``LOG_MAX_BYTES``."""
    if not path.exists() or path.stat().st_size < LOG_MAX_BYTES:
        return
    for index in range(LOG_FILES - 1, 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index}"))


def log_line(message: str, path: Path | None = None, now: datetime | None = None) -> None:
    """Append ``message`` to the sync log, each line stamped with its UTC time.

    The log keeps ``LOG_FILES`` files of about ``LOG_MAX_BYTES``. A log that cannot be
    written falls back to stderr, so the line still reaches the scheduler's capture.
    """
    path = path or LOG
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = "".join(f"{stamp} {line}\n" for line in message.splitlines() or [""])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        print(text, end="", file=sys.stderr)


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("launchctl", *args),
        capture_output=True,
        text=True,
        timeout=SCHEDULER_TIMEOUT_SECONDS,
        check=False,
    )


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("systemctl", "--user", *args),
        capture_output=True,
        text=True,
        timeout=SCHEDULER_TIMEOUT_SECONDS,
        check=False,
    )


def _scheduler() -> str:
    """Return the scheduler this platform uses: launchd on macOS, systemd on Linux."""
    scheduler = SCHEDULERS.get(sys.platform)
    if scheduler is None:
        raise RuntimeError(
            "facts schedule supports macOS (launchd) and Linux (systemd user units), "
            f"not {sys.platform}"
        )
    return scheduler


def _tool_directory(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise RuntimeError(f"{name} not found on PATH")
    return str(Path(found).parent)


def _job_path(config: AppConfig) -> list[str]:
    """Return the tool directories the scheduled job needs on PATH.

    A ClickHouse password needs its command's executable, because the scheduler cannot
    pass the password variable to the job.
    Prompt labels need ``[labels] openrouter_key_command``'s executable, because
    OPENROUTER_API_KEY is not set for the job either. Without a key command, omp's
    directory (when omp is installed) lets labelling fall back to omp's stored
    OpenRouter credential (`omp token openrouter`).
    """
    signoz = config.signoz
    required: list[str] = []
    if signoz.clickhouse_password_env is not None:
        raise RuntimeError(
            "the scheduled job cannot receive signoz.clickhouse_password_env; "
            "use signoz.clickhouse_password_command for a scheduled sync"
        )
    elif signoz.clickhouse_password_command is not None:
        required.append(signoz.clickhouse_password_command[0])
    key_command = config.labels_openrouter_key_command
    if key_command is not None:
        required.append(key_command[0])
    tools = [_tool_directory(name) for name in required]
    omp = shutil.which("omp") if key_command is None else None
    return [*tools, *([str(Path(omp).parent)] if omp else [])]


def _backup_path(path: Path) -> Path:
    """Return where ``path`` is backed up: ``BACKUPS/<name>.bak-<UTC time>``."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    return BACKUPS / f"{path.name}.bak-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def schedule_install(config: Path | None = None) -> dict[str, Any]:
    """Write and start the job that runs `facts sync` every minute.

    A job that fails to start restores and restarts the job it replaced. An additional
    machine is refused: it has no ClickHouse for ``facts sync``.
    """
    scheduler = _scheduler()
    loaded = load_config(config)
    require_host(loaded, "the scheduled `facts sync` needs SigNoz's ClickHouse")
    executable = Path(sys.executable).with_name("agent-introspection")
    if not executable.exists():
        raise RuntimeError(f"{executable} not found; cannot schedule a missing executable")
    path_dirs = (*_job_path(loaded), *SYSTEM_PATH)
    if scheduler == "launchd":
        return _install_launchd(executable, path_dirs, config)
    return _install_systemd(executable, path_dirs, config)


def schedule_remove() -> dict[str, Any]:
    """Stop the job and delete its files; on macOS also retire the legacy job."""
    if _scheduler() == "launchd":
        return _remove_launchd()
    return _remove_systemd()


def schedule_status() -> dict[str, Any]:
    """Report whether the job is installed and loaded, and the spool's backlog.

    ``loaded`` means launchd has the job, or the systemd timer is active.
    """
    status = _launchd_status() if _scheduler() == "launchd" else _systemd_status()
    return status | {
        "inbox_backlog": spool.backlog(),
        "inbox_rejected": spool.rejected_count(),
    }


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchd_loaded(label: str) -> bool:
    return _launchctl("list", label).returncode == 0


def _bootout(domain: str, label: str, *, loaded: bool) -> str | None:
    """Stop the job under ``label``; return why a loaded one could not be, else None.

    Stopping a job that is not loaded fails harmlessly, so only a loaded job's failure
    is returned: that job is still running, and the caller must not go on as if it
    were stopped.
    """
    stopped = _launchctl("bootout", f"{domain}/{label}")
    if loaded and stopped.returncode != 0:
        return stopped.stderr.strip() or "launchctl bootout failed"
    return None


def _retire_legacy_job(domain: str) -> RetiredJob | None:
    """Stop the job under ``LEGACY_LABEL`` and move its plist to ``BACKUPS``.

    Returns None when no legacy job is installed or loaded. A loaded job that cannot
    be stopped is an error, so two jobs never sync at once.
    """
    loaded = _launchd_loaded(LEGACY_LABEL)
    if not (loaded or LEGACY_PLIST.exists()):
        return None
    if (detail := _bootout(domain, LEGACY_LABEL, loaded=loaded)) is not None:
        raise RuntimeError(f"could not stop the legacy job {LEGACY_LABEL}: {detail}")
    backup = None
    if LEGACY_PLIST.exists():
        backup = _backup_path(LEGACY_PLIST)
        shutil.move(LEGACY_PLIST, backup)
    return RetiredJob(unloaded=loaded, backup=backup)


def _install_launchd(
    executable: Path, path_dirs: Iterable[str], config: Path | None
) -> dict[str, Any]:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    previous = PLIST.read_bytes() if PLIST.exists() else None
    domain = _launchd_domain()
    retired = _retire_legacy_job(domain)
    PLIST.write_bytes(plist(executable, path_dirs, config))
    # Whether the job being replaced runs: a job the user stopped stays stopped if
    # the new one fails to load and the old one is put back.
    was_loaded = _launchd_loaded(LABEL)
    if (detail := _bootout(domain, LABEL, loaded=was_loaded)) is not None:
        # The job still runs as it was loaded: put its plist back as it was too.
        if previous is None:
            PLIST.unlink()
        else:
            PLIST.write_bytes(previous)
        raise RuntimeError(_still_running(detail, retired))
    loaded = _launchctl("bootstrap", domain, str(PLIST))
    if loaded.returncode != 0:
        message = loaded.stderr.strip() or "launchctl bootstrap failed"
        replaced = ReplacedJob(previous, was_loaded) if previous is not None else None
        _restore_launchd_job(domain, replaced, retired, message)
    return {
        "installed": True,
        "scheduler": "launchd",
        "label": LABEL,
        "interval_seconds": INTERVAL_SECONDS,
        "legacy": retired.report() if retired is not None else None,
    }


def _still_running(detail: str, retired: RetiredJob | None) -> str:
    """Return the error for a loaded job under ``LABEL`` that could not be stopped.

    It names what already changed: the legacy job install retires first stays retired,
    since putting it back would leave two jobs syncing at once.
    """
    message = f"could not stop the job {LABEL}: {detail}; it is still loaded, its plist unchanged"
    if retired is None:
        return message
    moved = f", its plist moved to {retired.backup}" if retired.backup is not None else ""
    return f"{message}; the legacy job {LEGACY_LABEL} was retired first{moved}"


def _restore_launchd_job(
    domain: str, previous: ReplacedJob | None, retired: RetiredJob | None, message: str
) -> NoReturn:
    """Put back the job a failed install replaced, then raise ``message``.

    That is the previous job under ``LABEL``, else the legacy job install retired.
    Either is reloaded only when it was loaded before install: a job the user had
    stopped stays stopped. ``message`` then names what was put back.
    """
    restored: Path | None = None
    reload = True
    if previous is not None:
        PLIST.write_bytes(previous.plist)
        restored = PLIST
        reload = previous.loaded
    else:
        PLIST.unlink(missing_ok=True)
        if retired is not None and retired.backup is not None:
            shutil.move(retired.backup, LEGACY_PLIST)
            restored = LEGACY_PLIST
            reload = retired.unloaded
    if restored is None:
        raise RuntimeError(message)
    if not reload:
        raise RuntimeError(f"{message}; {restored.name} is back in place, not loaded, as it was")
    reloaded = _launchctl("bootstrap", domain, str(restored))
    if reloaded.returncode != 0:
        detail = reloaded.stderr.strip() or "launchctl bootstrap failed"
        raise RuntimeError(
            f"new job failed to load ({message}) and the previous job could not be "
            f"reloaded ({detail}); the schedule is stopped"
        )
    raise RuntimeError(f"{message}; {restored.name} is back in place and loaded")


def _remove_launchd() -> dict[str, Any]:
    domain = _launchd_domain()
    if (detail := _bootout(domain, LABEL, loaded=_launchd_loaded(LABEL))) is not None:
        raise RuntimeError(_still_running(detail, None))
    existed = PLIST.exists()
    PLIST.unlink(missing_ok=True)
    retired = _retire_legacy_job(domain)
    return {
        "removed": existed,
        "scheduler": "launchd",
        "label": LABEL,
        "legacy": retired.report() if retired is not None else None,
    }


def _launchd_status() -> dict[str, Any]:
    legacy_loaded = _launchd_loaded(LEGACY_LABEL)
    legacy_installed = LEGACY_PLIST.exists()
    return {
        "scheduler": "launchd",
        "label": LABEL,
        "installed": PLIST.exists(),
        "loaded": _launchd_loaded(LABEL),
        # A job left by an earlier release; `facts schedule install` retires it.
        "legacy": (
            {"label": LEGACY_LABEL, "installed": legacy_installed, "loaded": legacy_loaded}
            if legacy_installed or legacy_loaded
            else None
        ),
    }


def _systemctl_failure(*commands: tuple[str, ...]) -> str | None:
    """Run each ``systemctl --user`` command in turn; return the first failure's message."""
    for command in commands:
        result = _systemctl(*command)
        if result.returncode != 0:
            return result.stderr.strip() or f"systemctl --user {' '.join(command)} failed"
    return None


def _install_systemd(
    executable: Path, path_dirs: Iterable[str], config: Path | None
) -> dict[str, Any]:
    SYSTEMD_UNITS.mkdir(parents=True, exist_ok=True)
    units = {SERVICE: service_unit(executable, path_dirs, config), TIMER: timer_unit()}
    previous = {path: path.read_bytes() for path in units if path.exists()}
    # The replaced timer's state: one the user stopped or disabled stays so if the new
    # one fails to start and the old units are put back.
    state = _timer_state(TIMER) if TIMER in previous else None
    backups: list[str] = []
    for path in previous:
        backup = _backup_path(path)
        shutil.copy2(path, backup)
        backups.append(str(backup))
    for path, text in units.items():
        path.write_text(text, encoding="utf-8")
    failure = _systemctl_failure(*_START)
    if failure is not None:
        _restore_systemd_units(previous, state, failure)
    return {
        "installed": True,
        "scheduler": "systemd",
        "unit": TIMER.name,
        "interval_seconds": INTERVAL_SECONDS,
        "backups": backups,
        "linger": _lingering(),
    }


def _restore_systemd_units(
    previous: Mapping[Path, bytes], state: TimerState | None, message: str
) -> NoReturn:
    """Put back the units a failed install replaced, then raise ``message``.

    ``state`` is the replaced timer's, or None when there was no timer, in which case
    the new timer is disabled. A replaced timer is enabled and started again only if
    it was before: one the user stopped or disabled stays so. ``message`` then names
    the state the timer is back in.
    """
    if state is None:
        _systemctl("disable", "--now", TIMER.name)
    for path in (SERVICE, TIMER):
        if path in previous:
            path.write_bytes(previous[path])
        else:
            path.unlink(missing_ok=True)
    if state is None:
        _systemctl("daemon-reload")
        raise RuntimeError(message)
    detail = _systemctl_failure(*state.restore())
    if detail is not None:
        raise RuntimeError(
            f"new job failed to start ({message}) and the previous job could not be "
            f"restarted ({detail}); the schedule is stopped"
        )
    if not state.active:
        raise RuntimeError(f"{message}; {TIMER.name} is back in place, not active, as it was")
    raise RuntimeError(f"{message}; {TIMER.name} is back in place and active")


def _remove_systemd() -> dict[str, Any]:
    """Stop and delete the units; a timer that could not be stopped keeps its units.

    Stopping a timer that is neither enabled nor active fails harmlessly. An enabled or
    active one that cannot be stopped (for example, an unreachable user bus) is still
    scheduled, so its units stay and the removal is refused, as for launchd.
    """
    before = _timer_state(TIMER)
    stopped = _systemctl("disable", "--now", TIMER.name)
    if stopped.returncode != 0 and (before.enabled or before.active):
        detail = stopped.stderr.strip() or "systemctl disable failed"
        raise RuntimeError(
            f"could not stop the timer {TIMER.name}: {detail}; it is still scheduled, "
            "its units unchanged"
        )
    existed = SERVICE.exists() or TIMER.exists()
    SERVICE.unlink(missing_ok=True)
    TIMER.unlink(missing_ok=True)
    _systemctl("daemon-reload")
    return {"removed": existed, "scheduler": "systemd", "unit": TIMER.name}


def _lingering() -> bool:
    """Return whether systemd keeps this user's timers running with no login session."""
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        return False
    return (LINGER / user).exists()


def _timer_state(timer: Path) -> TimerState:
    return TimerState(
        enabled=_systemctl("is-enabled", timer.name).stdout.strip() == "enabled",
        active=_systemctl("is-active", timer.name).returncode == 0,
    )


def _timer_status(service: Path, timer: Path) -> dict[str, Any]:
    state = _timer_state(timer)
    return {
        "scheduler": "systemd",
        "unit": timer.name,
        "installed": service.exists() and timer.exists(),
        "enabled": state.enabled,
        "loaded": state.active,
        "linger": _lingering(),
    }


def _systemd_status() -> dict[str, Any]:
    return _timer_status(SERVICE, TIMER)


def spool_sync_status() -> dict[str, Any]:
    """Report the timer that runs ``spool sync`` on an additional machine (read-only).

    Only Linux has bundled units (the onboarding skill's systemd user units), reported
    as ``schedule_status`` reports the host's job. On any other platform ``scheduler``
    is None: no spool-sync job is bundled there.
    """
    if SCHEDULERS.get(sys.platform) != "systemd":
        return {"scheduler": None, "platform": sys.platform}
    return _timer_status(SPOOL_SYNC_SERVICE, SPOOL_SYNC_TIMER)
