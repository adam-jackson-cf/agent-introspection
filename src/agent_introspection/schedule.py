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
LEGACY_LABEL = "com.adamjackson.agent-introspection.sync"
UNIT = "agent-introspection-sync"
SERVICE_NAME = f"{UNIT}.service"
TIMER_NAME = f"{UNIT}.timer"
SPOOL_SYNC_UNIT = "agent-introspection-spool-sync"
_START = (("daemon-reload",), ("enable", "--now", TIMER_NAME))
LINGER = Path("/var/lib/systemd/linger")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_FILES = 3
INTERVAL_SECONDS = 60
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")
SCHEDULERS = {"darwin": "launchd", "linux": "systemd"}
SCHEDULER_TIMEOUT_SECONDS = 30


def launch_agents_dir() -> Path:
    return Path.home() / "Library/LaunchAgents"


def plist_file() -> Path:
    return launch_agents_dir() / f"{LABEL}.plist"


def legacy_plist_file() -> Path:
    return launch_agents_dir() / f"{LEGACY_LABEL}.plist"


def systemd_units_dir() -> Path:
    return Path.home() / ".config/systemd/user"


def service_file() -> Path:
    return systemd_units_dir() / SERVICE_NAME


def timer_file() -> Path:
    return systemd_units_dir() / TIMER_NAME


def spool_sync_service_file() -> Path:
    return systemd_units_dir() / f"{SPOOL_SYNC_UNIT}.service"


def spool_sync_timer_file() -> Path:
    return systemd_units_dir() / f"{SPOOL_SYNC_UNIT}.timer"


def log_file() -> Path:
    return Path.home() / ".local/share/agent-introspection/sync.log"


def launchd_log_file() -> Path:
    return log_file().with_name("sync-launchd.log")


def backups_dir() -> Path:
    return log_file().with_name("schedule-backups")


def platform_name() -> str:
    return sys.platform


def _python_executable() -> str:
    return sys.executable


def _which(name: str) -> str | None:
    return shutil.which(name)


def _linger_file_exists(user: str) -> bool:
    return (LINGER / user).exists()


@dataclass(frozen=True, slots=True)
class RetiredJob:
    unloaded: bool
    backup: Path | None

    def report(self) -> dict[str, Any]:
        return {
            "label": LEGACY_LABEL,
            "unloaded": self.unloaded,
            "backup": str(self.backup) if self.backup is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ReplacedJob:
    plist: bytes
    loaded: bool


@dataclass(frozen=True, slots=True)
class TimerState:
    enabled: bool
    active: bool

    def restore(self) -> tuple[tuple[str, ...], ...]:
        return (
            ("daemon-reload",),
            ("enable" if self.enabled else "disable", TIMER_NAME),
            ("start" if self.active else "stop", TIMER_NAME),
        )


def _program_arguments(executable: Path, config: Path | None) -> list[str]:
    selected = ["--config", str(config)] if config is not None else []
    return [str(executable), *selected, "facts", "sync", "--log"]


def plist(executable: Path, path_dirs: Iterable[str], config: Path | None = None) -> bytes:
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": _program_arguments(executable, config),
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path_dirs))},
        "StandardOutPath": str(launchd_log_file()),
        "StandardErrorPath": str(launchd_log_file()),
    })


def unit_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    return f'"{escaped}"'


def service_unit(executable: Path, path_dirs: Iterable[str], config: Path | None = None) -> str:
    command = " ".join(
        unit_quote(argument).replace("$", "$$")
        for argument in _program_arguments(executable, config)
    )
    path = unit_quote(f"PATH={':'.join(dict.fromkeys(path_dirs))}")
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
    return (
        "[Unit]\n"
        "Description=Run Agent Introspection facts sync every minute\n"
        "\n"
        "[Timer]\n"
        "OnCalendar=minutely\n"
        "AccuracySec=1s\n"
        "Persistent=true\n"
        f"Unit={SERVICE_NAME}\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _rotate(path: Path) -> None:
    if not path.exists() or path.stat().st_size < LOG_MAX_BYTES:
        return
    for index in range(LOG_FILES - 1, 0, -1):
        source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index}"))


def log_line(message: str, path: Path | None = None, now: datetime | None = None) -> None:
    path = path or log_file()
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = "".join(f"{stamp} {line}\n" for line in message.splitlines() or [""])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)
    except OSError:
        print(text, end="", file=sys.stderr)


def launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("launchctl", *args),
        capture_output=True,
        text=True,
        timeout=SCHEDULER_TIMEOUT_SECONDS,
        check=False,
    )


def systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("systemctl", "--user", *args),
        capture_output=True,
        text=True,
        timeout=SCHEDULER_TIMEOUT_SECONDS,
        check=False,
    )


def platform_scheduler() -> str:
    platform = platform_name()
    scheduler = SCHEDULERS.get(platform)
    if scheduler is None:
        raise RuntimeError(
            "facts schedule supports macOS (launchd) and Linux (systemd user units), "
            f"not {platform}"
        )
    return scheduler


def _tool_directory(name: str) -> str:
    found = _which(name)
    if found is None:
        raise RuntimeError(f"{name} not found on PATH")
    return str(Path(found).parent)


def job_path(config: AppConfig) -> list[str]:
    signoz = config.signoz
    required: list[str] = []
    if signoz.clickhouse_password_env is not None:
        raise RuntimeError(
            "the scheduled job cannot receive signoz.clickhouse_password_env; "
            "use signoz.clickhouse_password_command for a scheduled sync"
        )
    if signoz.clickhouse_password_command is not None:
        required.append(signoz.clickhouse_password_command[0])
    if config.labels_openrouter_key_command is not None:
        required.append(config.labels_openrouter_key_command[0])
    return [_tool_directory(name) for name in required]


def _backup_path(path: Path) -> Path:
    backups = backups_dir()
    backups.mkdir(parents=True, exist_ok=True)
    return backups / f"{path.name}.bak-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def check(loaded: AppConfig) -> tuple[str, Path, tuple[str, ...]]:
    scheduler = platform_scheduler()
    require_host(loaded, "the scheduled `facts sync` needs SigNoz's ClickHouse")
    executable = Path(_python_executable()).with_name("agent-introspection")
    if not executable.exists():
        raise RuntimeError(f"{executable} not found; cannot schedule a missing executable")
    return scheduler, executable, (*job_path(loaded), *SYSTEM_PATH)


def schedule_install(config: Path | None = None) -> dict[str, Any]:
    scheduler, executable, path_dirs = check(load_config(config))
    if scheduler == "launchd":
        return _install_launchd(executable, path_dirs, config)
    return _install_systemd(executable, path_dirs, config)


def schedule_remove() -> dict[str, Any]:
    if platform_scheduler() == "launchd":
        return _remove_launchd()
    return _remove_systemd()


def schedule_status() -> dict[str, Any]:
    status = _launchd_status() if platform_scheduler() == "launchd" else _systemd_status()
    return status | {
        "inbox_backlog": spool.backlog(),
        "inbox_rejected": spool.rejected_count(),
    }


def launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def launchd_loaded(label: str) -> bool:
    return launchctl("list", label).returncode == 0


def _bootout(domain: str, label: str, *, loaded: bool) -> str | None:
    stopped = launchctl("bootout", f"{domain}/{label}")
    if loaded and stopped.returncode != 0:
        return stopped.stderr.strip() or "launchctl bootout failed"
    return None


def _retire_legacy_job(domain: str) -> RetiredJob | None:
    legacy = legacy_plist_file()
    loaded = launchd_loaded(LEGACY_LABEL)
    if not (loaded or legacy.exists()):
        return None
    if (detail := _bootout(domain, LEGACY_LABEL, loaded=loaded)) is not None:
        raise RuntimeError(f"could not stop the legacy job {LEGACY_LABEL}: {detail}")
    backup = None
    if legacy.exists():
        backup = _backup_path(legacy)
        shutil.move(legacy, backup)
    return RetiredJob(unloaded=loaded, backup=backup)


def _install_launchd(
    executable: Path, path_dirs: Iterable[str], config: Path | None
) -> dict[str, Any]:
    job = plist_file()
    job.parent.mkdir(parents=True, exist_ok=True)
    previous = job.read_bytes() if job.exists() else None
    domain = launchd_domain()
    retired = _retire_legacy_job(domain)
    job.write_bytes(plist(executable, path_dirs, config))
    was_loaded = launchd_loaded(LABEL)
    if (detail := _bootout(domain, LABEL, loaded=was_loaded)) is not None:
        if previous is None:
            job.unlink()
        else:
            job.write_bytes(previous)
        raise RuntimeError(_still_running(detail, retired))
    loaded = launchctl("bootstrap", domain, str(job))
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
    message = f"could not stop the job {LABEL}: {detail}; it is still loaded, its plist unchanged"
    if retired is None:
        return message
    moved = f", its plist moved to {retired.backup}" if retired.backup is not None else ""
    return f"{message}; the legacy job {LEGACY_LABEL} was retired first{moved}"


def _restore_launchd_job(
    domain: str, previous: ReplacedJob | None, retired: RetiredJob | None, message: str
) -> NoReturn:
    job = plist_file()
    restored: Path | None = None
    reload = True
    if previous is not None:
        job.write_bytes(previous.plist)
        restored = job
        reload = previous.loaded
    else:
        job.unlink(missing_ok=True)
        if retired is not None and retired.backup is not None:
            legacy = legacy_plist_file()
            shutil.move(retired.backup, legacy)
            restored = legacy
            reload = retired.unloaded
    if restored is None:
        raise RuntimeError(message)
    if not reload:
        raise RuntimeError(f"{message}; {restored.name} is back in place, not loaded, as it was")
    reloaded = launchctl("bootstrap", domain, str(restored))
    if reloaded.returncode != 0:
        detail = reloaded.stderr.strip() or "launchctl bootstrap failed"
        raise RuntimeError(
            f"new job failed to load ({message}) and the previous job could not be "
            f"reloaded ({detail}); the schedule is stopped"
        )
    raise RuntimeError(f"{message}; {restored.name} is back in place and loaded")


def _remove_launchd() -> dict[str, Any]:
    domain = launchd_domain()
    if (detail := _bootout(domain, LABEL, loaded=launchd_loaded(LABEL))) is not None:
        raise RuntimeError(_still_running(detail, None))
    job = plist_file()
    existed = job.exists()
    job.unlink(missing_ok=True)
    retired = _retire_legacy_job(domain)
    return {
        "removed": existed,
        "scheduler": "launchd",
        "label": LABEL,
        "legacy": retired.report() if retired is not None else None,
    }


def _launchd_status() -> dict[str, Any]:
    legacy_loaded = launchd_loaded(LEGACY_LABEL)
    legacy_installed = legacy_plist_file().exists()
    return {
        "scheduler": "launchd",
        "label": LABEL,
        "installed": plist_file().exists(),
        "loaded": launchd_loaded(LABEL),
        "legacy": (
            {"label": LEGACY_LABEL, "installed": legacy_installed, "loaded": legacy_loaded}
            if legacy_installed or legacy_loaded
            else None
        ),
    }


def _systemctl_failure(*commands: tuple[str, ...]) -> str | None:
    for command in commands:
        result = systemctl(*command)
        if result.returncode != 0:
            return result.stderr.strip() or f"systemctl --user {' '.join(command)} failed"
    return None


def _install_systemd(
    executable: Path, path_dirs: Iterable[str], config: Path | None
) -> dict[str, Any]:
    service, timer = service_file(), timer_file()
    service.parent.mkdir(parents=True, exist_ok=True)
    units = {service: service_unit(executable, path_dirs, config), timer: timer_unit()}
    previous = {path: path.read_bytes() for path in units if path.exists()}
    state = _timer_state(timer) if timer in previous else None
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
        "unit": TIMER_NAME,
        "interval_seconds": INTERVAL_SECONDS,
        "backups": backups,
        "linger": _lingering(),
    }


def _restore_systemd_units(
    previous: Mapping[Path, bytes], state: TimerState | None, message: str
) -> NoReturn:
    if state is None:
        systemctl("disable", "--now", TIMER_NAME)
    for path in (service_file(), timer_file()):
        if path in previous:
            path.write_bytes(previous[path])
        else:
            path.unlink(missing_ok=True)
    if state is None:
        systemctl("daemon-reload")
        raise RuntimeError(message)
    detail = _systemctl_failure(*state.restore())
    if detail is not None:
        raise RuntimeError(
            f"new job failed to start ({message}) and the previous job could not be "
            f"restarted ({detail}); the schedule is stopped"
        )
    if not state.active:
        raise RuntimeError(f"{message}; {TIMER_NAME} is back in place, not active, as it was")
    raise RuntimeError(f"{message}; {TIMER_NAME} is back in place and active")


def _remove_systemd() -> dict[str, Any]:
    service, timer = service_file(), timer_file()
    before = _timer_state(timer)
    stopped = systemctl("disable", "--now", TIMER_NAME)
    if stopped.returncode != 0 and (before.enabled or before.active):
        detail = stopped.stderr.strip() or "systemctl disable failed"
        raise RuntimeError(
            f"could not stop the timer {TIMER_NAME}: {detail}; it is still scheduled, "
            "its units unchanged"
        )
    existed = service.exists() or timer.exists()
    service.unlink(missing_ok=True)
    timer.unlink(missing_ok=True)
    systemctl("daemon-reload")
    return {"removed": existed, "scheduler": "systemd", "unit": TIMER_NAME}


def _lingering() -> bool:
    try:
        user = getpass.getuser()
    except (OSError, KeyError):
        return False
    return _linger_file_exists(user)


def _timer_state(timer: Path) -> TimerState:
    return TimerState(
        enabled=systemctl("is-enabled", timer.name).stdout.strip() == "enabled",
        active=systemctl("is-active", timer.name).returncode == 0,
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
    return _timer_status(service_file(), timer_file())


def spool_sync_status() -> dict[str, Any]:
    platform = platform_name()
    if SCHEDULERS.get(platform) != "systemd":
        return {"scheduler": None, "platform": platform}
    return _timer_status(spool_sync_service_file(), spool_sync_timer_file())
