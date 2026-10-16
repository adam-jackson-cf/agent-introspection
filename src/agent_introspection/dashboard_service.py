"""The dashboard as a user service: built from a repository checkout and kept running.

``install`` builds the dashboard in the checkout's ``dashboard/`` (``bun install
--frozen-lockfile``, then ``bun run build``) and starts a service that runs ``bun
server.ts`` there: a launchd agent on macOS (kept alive), a systemd user service on
Linux (restarted on failure). The service runs from the checkout given at install, so
pulling or switching branches changes what it serves at its next restart; rerun
``install`` after an update to rebuild and restart it. The service sets only ``PATH``
(the system directories and bun's) and, when one was selected, the config path; the
dashboard reads ClickHouse at ``[signoz] clickhouse_url``. A ClickHouse password is
refused: the server reads one only from its own environment, which a service file
would have to store. An additional machine is refused: its dashboard is the host's.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

from agent_introspection import schedule
from agent_introspection.config import AppConfig, require_host

LABEL: Final = "dev.agent-introspection.dashboard"
UNIT: Final = "agent-introspection-dashboard"
PORT: Final = 4173
PLIST: Final = schedule.LAUNCH_AGENTS / f"{LABEL}.plist"
SERVICE: Final = schedule.SYSTEMD_UNITS / f"{UNIT}.service"
LOG: Final = schedule.LOG.with_name("dashboard.log")
BUILD_TIMEOUT_SECONDS: Final = 600


def _run_bun(command: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=BUILD_TIMEOUT_SECONDS, check=False
    )


def dashboard_directory(repo: Path) -> Path:
    """Return the checkout's dashboard directory; refuse a path that is not a checkout."""
    directory = repo.expanduser().resolve() / "dashboard"
    if not (directory / "package.json").is_file() or not (directory / "server.ts").is_file():
        raise ValueError(
            f"{repo} is not an agent-introspection checkout (no dashboard/package.json "
            "and server.ts): pass --repo <checkout>"
        )
    return directory


def _bun() -> Path:
    found = shutil.which("bun")
    if found is None:
        raise RuntimeError("bun not found on PATH: install Bun (https://bun.sh) first")
    return Path(found)


def _require_no_password(config: AppConfig) -> None:
    signoz = config.signoz
    if signoz.clickhouse_password_command is not None or signoz.clickhouse_password_env:
        raise RuntimeError(
            "the dashboard service cannot run a ClickHouse password command; run the "
            "dashboard by hand (`bun run --cwd dashboard start`) with "
            "INTROSPECTION_CLICKHOUSE_USER and INTROSPECTION_CLICKHOUSE_PASSWORD set"
        )


def build(directory: Path, bun: Path) -> None:
    """Install the dashboard's locked dependencies and build its assets."""
    for step in (("install", "--frozen-lockfile"), ("run", "build")):
        result = _run_bun((str(bun), *step), directory)
        if result.returncode != 0:
            lines = (result.stderr or result.stdout).strip().splitlines()
            raise RuntimeError(f"bun {' '.join(step)} failed: {lines[-1] if lines else ''}")


def _environment(bun: Path, config: Path | None) -> dict[str, str]:
    path = ":".join(dict.fromkeys((str(bun.parent), *schedule.SYSTEM_PATH)))
    selected = {"AGENT_INTROSPECTION_CONFIG": str(config)} if config is not None else {}
    return {"PATH": path, **selected}


def plist(directory: Path, bun: Path, config: Path | None = None) -> bytes:
    """Return the launchd agent that keeps ``bun server.ts`` running in ``directory``."""
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(bun), "server.ts"],
        "WorkingDirectory": str(directory),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": _environment(bun, config),
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    })


def service_unit(directory: Path, bun: Path, config: Path | None = None) -> str:
    """Return the systemd user service that keeps ``bun server.ts`` running."""
    quote = schedule.unit_quote
    environment = "".join(
        f"Environment={quote(f'{name}={value}')}\n"
        for name, value in _environment(bun, config).items()
    )
    return (
        "[Unit]\n"
        "Description=Agent Introspection dashboard\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={quote(str(directory))}\n"
        f"{environment}"
        f"ExecStart={quote(str(bun)).replace('$', '$$')} server.ts\n"
        "Restart=on-failure\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _start_launchd(content: bytes) -> None:
    domain = schedule.launchd_domain()
    if schedule.launchd_loaded(LABEL):
        stopped = schedule.launchctl("bootout", f"{domain}/{LABEL}")
        if stopped.returncode != 0:
            raise RuntimeError(f"could not stop {LABEL}: {stopped.stderr.strip()}")
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_bytes(content)
    started = schedule.launchctl("bootstrap", domain, str(PLIST))
    if started.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {started.stderr.strip()}")


def _start_systemd(content: str) -> None:
    SERVICE.parent.mkdir(parents=True, exist_ok=True)
    SERVICE.write_text(content, encoding="utf-8")
    for command in (("daemon-reload",), ("enable", SERVICE.name), ("restart", SERVICE.name)):
        result = schedule.systemctl(*command)
        if result.returncode != 0:
            raise RuntimeError(
                f"systemctl --user {' '.join(command)} failed: {result.stderr.strip()}"
            )


def install(config: AppConfig, repo: Path, config_path: Path | None = None) -> dict[str, Any]:
    """Build the dashboard in ``repo`` and (re)start its service on ``PORT``."""
    require_host(config, "the dashboard reads the host's ClickHouse")
    _require_no_password(config)
    scheduler = schedule.platform_scheduler()
    directory = dashboard_directory(repo)
    bun = _bun()
    build(directory, bun)
    if scheduler == "launchd":
        _start_launchd(plist(directory, bun, config_path))
    else:
        _start_systemd(service_unit(directory, bun, config_path))
    return {
        "installed": True,
        "scheduler": scheduler,
        "directory": str(directory),
        "url": f"http://127.0.0.1:{PORT}",
    }


def remove() -> dict[str, Any]:
    """Stop the dashboard service and delete its file."""
    if schedule.platform_scheduler() == "launchd":
        if schedule.launchd_loaded(LABEL):
            schedule.launchctl("bootout", f"{schedule.launchd_domain()}/{LABEL}")
        existed = PLIST.exists()
        PLIST.unlink(missing_ok=True)
        return {"removed": existed, "scheduler": "launchd"}
    existed = SERVICE.exists()
    if existed:
        schedule.systemctl("disable", "--now", SERVICE.name)
        SERVICE.unlink()
        schedule.systemctl("daemon-reload")
    return {"removed": existed, "scheduler": "systemd"}


def status() -> dict[str, Any]:
    """Report whether the dashboard service is installed and running (read-only)."""
    scheduler = schedule.SCHEDULERS.get(sys.platform)
    if scheduler == "launchd":
        return {
            "scheduler": scheduler,
            "installed": PLIST.exists(),
            "loaded": schedule.launchd_loaded(LABEL),
        }
    if scheduler == "systemd":
        active = schedule.systemctl("is-active", SERVICE.name).stdout.strip() == "active"
        return {"scheduler": scheduler, "installed": SERVICE.exists(), "loaded": active}
    return {"scheduler": None, "installed": False, "loaded": False}
