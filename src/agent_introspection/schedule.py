"""The launchd job that runs ``facts sync`` every minute."""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_introspection import spool
from agent_introspection.config import SigNozConfig, load_config

LABEL = "com.adamjackson.agent-introspection.sync"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / ".local/share/agent-introspection/sync.log"
# What launchd itself captures (a crash before the sync can log): not rotated, rarely written.
LAUNCHD_LOG = LOG.with_name("sync-launchd.log")
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_FILES = 3
INTERVAL_SECONDS = 60


def plist(executable: Path, path_dirs: Iterable[str], config: Path | None = None) -> bytes:
    """Return the launchd job that runs ``facts sync`` every minute.

    A selected ``config`` is passed through, so the job syncs the same deployment.
    ``--log`` makes the sync write its summary and errors to the rotated ``LOG`` with
    a UTC timestamp, which launchd's own file redirection cannot do.
    """
    selected = ["--config", str(config)] if config is not None else []
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [str(executable), *selected, "facts", "sync", "--log"],
        "StartInterval": INTERVAL_SECONDS,
        "RunAtLoad": True,
        "EnvironmentVariables": {"PATH": ":".join(dict.fromkeys(path_dirs))},
        "StandardOutPath": str(LAUNCHD_LOG),
        "StandardErrorPath": str(LAUNCHD_LOG),
    })


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
    written falls back to stderr, so the line still reaches launchd's capture.
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
    return subprocess.run(("launchctl", *args), capture_output=True, text=True, check=False)


def _job_path(signoz: SigNozConfig) -> list[str]:
    """Return the tool directories the launchd job needs on PATH.

    Docker mode needs the docker CLI. HTTP mode needs the password command's
    executable, because launchd cannot pass the password variable to the job. omp's
    directory lets prompt labelling read omp's stored OpenRouter credential
    (`omp token openrouter`) when OPENROUTER_API_KEY is not set for the job.
    """
    required: list[str] = []
    if signoz.mode == "docker":
        required.append("docker")
    elif signoz.clickhouse_password_env is not None:
        raise RuntimeError(
            "launchd cannot pass signoz.clickhouse_password_env to the job; "
            "use signoz.clickhouse_password_command for a scheduled sync"
        )
    elif signoz.clickhouse_password_command is not None:
        required.append(signoz.clickhouse_password_command[0])
    tools: list[str] = []
    for name in required:
        found = shutil.which(name)
        if found is None:
            raise RuntimeError(f"{name} not found on PATH")
        tools.append(str(Path(found).parent))
    omp = shutil.which("omp")
    return [*tools, *([str(Path(omp).parent)] if omp else [])]


def schedule_install(config: Path | None = None) -> dict[str, Any]:
    """Write and load the launchd job that runs `facts sync` every minute.

    A failed bootstrap restores and reloads the previously installed job.
    """
    executable = Path(sys.executable).with_name("agent-introspection")
    if not executable.exists():
        raise RuntimeError(f"{executable} not found; cannot schedule a missing executable")
    tools = _job_path(load_config(config).signoz)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    previous = PLIST.read_bytes() if PLIST.exists() else None
    PLIST.write_bytes(plist(executable, (*tools, "/usr/bin", "/bin", "/usr/sbin", "/sbin"), config))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", f"{domain}/{LABEL}")
    loaded = _launchctl("bootstrap", domain, str(PLIST))
    if loaded.returncode != 0:
        message = loaded.stderr.strip() or "launchctl bootstrap failed"
        if previous is None:
            PLIST.unlink(missing_ok=True)
        else:
            PLIST.write_bytes(previous)
            restored = _launchctl("bootstrap", domain, str(PLIST))
            if restored.returncode != 0:
                detail = restored.stderr.strip() or "launchctl bootstrap failed"
                raise RuntimeError(
                    f"new job failed to load ({message}) and the previous job could not be "
                    f"reloaded ({detail}); the schedule is stopped"
                )
        raise RuntimeError(message)
    return {"installed": True, "label": LABEL, "interval_seconds": INTERVAL_SECONDS}


def schedule_remove() -> dict[str, Any]:
    """Unload the launchd job and delete its plist."""
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    existed = PLIST.exists()
    PLIST.unlink(missing_ok=True)
    return {"removed": existed, "label": LABEL}


def schedule_status() -> dict[str, Any]:
    """Report whether the job is loaded and how many spool files wait or were rejected."""
    listed = _launchctl("list", LABEL)
    return {
        "installed": PLIST.exists(),
        "loaded": listed.returncode == 0,
        "inbox_backlog": spool.backlog(),
        "inbox_rejected": spool.rejected_count(),
    }
