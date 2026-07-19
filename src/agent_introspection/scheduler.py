"""LaunchAgent installation and cross-process SQLite leases."""

from __future__ import annotations

import os
import plistlib
import re
import sqlite3
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

LABEL = "com.adamjackson.agent-introspection"


@dataclass(frozen=True)
class Lease:
    name: str
    owner_pid: int
    expires_at: str


@dataclass(frozen=True)
class CompletedSlotRun:
    run_id: str
    started_at: str
    slot_start: str


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_lease(
    connection: sqlite3.Connection,
    *,
    name: str = "scan",
    duration: timedelta = timedelta(minutes=15),
) -> Lease:
    """Acquire a lease, reclaiming immediately when its owner PID is absent."""
    now = datetime.now(UTC)
    expires = now + duration
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT owner_pid, expires_at FROM scheduler_leases WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            owner_pid = int(row[0])
            if _pid_exists(owner_pid):
                raise RuntimeError(f"scheduler lease {name!r} is already held")
        connection.execute(
            """
            INSERT INTO scheduler_leases (name, owner_pid, heartbeat_at, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                owner_pid = excluded.owner_pid,
                heartbeat_at = excluded.heartbeat_at,
                expires_at = excluded.expires_at
            """,
            (name, os.getpid(), now.isoformat(), expires.isoformat()),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return Lease(name=name, owner_pid=os.getpid(), expires_at=expires.isoformat())


def heartbeat_lease(
    connection: sqlite3.Connection,
    lease: Lease,
    *,
    duration: timedelta = timedelta(minutes=15),
) -> Lease:
    now = datetime.now(UTC)
    expires = now + duration
    with connection:
        cursor = connection.execute(
            """
            UPDATE scheduler_leases SET heartbeat_at = ?, expires_at = ?
            WHERE name = ? AND owner_pid = ?
            """,
            (now.isoformat(), expires.isoformat(), lease.name, lease.owner_pid),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("scheduler lease ownership was lost")
    return Lease(lease.name, lease.owner_pid, expires.isoformat())


def release_lease(connection: sqlite3.Connection, lease: Lease) -> None:
    with connection:
        cursor = connection.execute(
            "DELETE FROM scheduler_leases WHERE name = ? AND owner_pid = ?",
            (lease.name, lease.owner_pid),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("scheduler lease ownership was lost")


@contextmanager
def scan_lease(connection: sqlite3.Connection, *, duration: timedelta) -> Iterator[Lease]:
    lease = acquire_lease(connection, duration=duration)
    try:
        yield lease
    finally:
        release_lease(connection, lease)


def completed_in_current_slot(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    interval_seconds: int,
) -> CompletedSlotRun | None:
    """Return the qualifying successful scan started in the current UTC slot."""
    if now.tzinfo is None:
        raise ValueError("scheduled scan clock must be timezone-aware")
    if isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError("scheduler interval must be a positive integer")
    now_utc = now.astimezone(UTC)
    slot_epoch = (int(now_utc.timestamp()) // interval_seconds) * interval_seconds
    slot_start = datetime.fromtimestamp(slot_epoch, UTC)
    slot_end = slot_start + timedelta(seconds=interval_seconds)
    rows = connection.execute(
        """
        SELECT id, started_at FROM scan_runs
        WHERE status IN ('succeeded', 'no_data')
        ORDER BY started_at DESC LIMIT 64
        """,
    ).fetchall()
    for row in rows:
        started = datetime.fromisoformat(str(row[1]))
        if started.tzinfo is None:
            raise ValueError("persisted scan start must be timezone-aware")
        if slot_start <= started.astimezone(UTC) < slot_end:
            return CompletedSlotRun(str(row[0]), str(row[1]), slot_start.isoformat())
    return None


def recover_interrupted_scan_runs(
    connection: sqlite3.Connection, *, completed_at: datetime | None = None
) -> tuple[str, ...]:
    """Terminalize scan runs left running by an interrupted process under the acquired lease."""
    terminal_at = completed_at or datetime.now(UTC)
    if terminal_at.tzinfo is None:
        raise ValueError("interrupted scan completion time must be timezone-aware")
    rows = connection.execute(
        "SELECT id FROM scan_runs WHERE status = 'running' ORDER BY started_at"
    ).fetchall()
    recovered = tuple(str(row[0]) for row in rows)
    if not recovered:
        return ()
    with connection:
        connection.execute(
            """
            UPDATE scan_runs
            SET status = 'failed', completed_at = ?, error_code = 'interrupted'
            WHERE status = 'running'
            """,
            (terminal_at.astimezone(UTC).isoformat(),),
        )
    return recovered


def _current_slot_start(*, now: datetime, interval_seconds: int) -> str:
    if now.tzinfo is None:
        raise ValueError("scheduled scan clock must be timezone-aware")
    slot_epoch = (int(now.astimezone(UTC).timestamp()) // interval_seconds) * interval_seconds
    return datetime.fromtimestamp(slot_epoch, UTC).isoformat()


def launch_agent_payload(
    *,
    executable: Path,
    config_path: Path,
    working_directory: Path,
    docker_host: str,
    log_directory: Path,
    interval_seconds: int,
    timezone: str,
) -> dict[str, object]:
    """Build the canonical user LaunchAgent configuration."""
    if interval_seconds != 3_600:
        raise ValueError("LaunchAgent interval must be exactly 3600 seconds")
    return {
        "Label": LABEL,
        "ProgramArguments": [str(executable), "scan", "--scheduled"],
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        "KeepAlive": False,
        "StartCalendarInterval": {"Minute": 0},
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
            "DOCKER_HOST": docker_host,
            "AGENT_INTROSPECTION_CONFIG": str(config_path),
            "LC_ALL": "en_GB.UTF-8",
            "LANG": "en_GB.UTF-8",
            "TZ": timezone,
        },
        "StandardOutPath": str(log_directory / "launchd.stdout.log"),
        "StandardErrorPath": str(log_directory / "launchd.stderr.log"),
        "ProcessType": "Background",
    }


def install_schedule(payload: dict[str, object], *, home: Path | None = None) -> Path:
    """Write, validate and bootstrap the LaunchAgent."""
    root = home or Path.home()
    destination = root / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    destination.parent.mkdir(parents=True, exist_ok=True)
    log_path = Path(str(payload["StandardOutPath"]))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)
    temporary = destination.with_suffix(".plist.tmp")
    temporary.write_bytes(encoded)
    plistlib.loads(temporary.read_bytes())
    temporary.replace(destination)
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(destination)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["launchctl", "bootstrap", domain, str(destination)], check=True)
    return destination


def schedule_status(
    connection: sqlite3.Connection,
    *,
    now: datetime,
    interval_seconds: int,
) -> dict[str, object]:
    """Return launchd state with the persisted scan freshness and lease evidence."""
    if interval_seconds != 3_600:
        raise ValueError("scheduler interval must be exactly 3600 seconds")
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    completed = completed_in_current_slot(connection, now=now, interval_seconds=interval_seconds)
    latest = connection.execute(
        """
        SELECT id, status, started_at, completed_at, error_code
        FROM scan_runs
        WHERE status IN ('succeeded', 'no_data', 'failed')
        ORDER BY completed_at DESC, started_at DESC
        LIMIT 1
        """
    ).fetchone()
    lease = connection.execute(
        "SELECT name, owner_pid, heartbeat_at, expires_at FROM scheduler_leases WHERE name = 'scan'"
    ).fetchone()
    state = re.search(r"^\s*state = (.+)$", result.stdout, flags=re.MULTILINE)
    exit_code = re.search(r"^\s*last exit code = (\d+)$", result.stdout, flags=re.MULTILINE)
    return {
        "installed": result.returncode == 0,
        "label": LABEL,
        "state": state.group(1) if state else None,
        "last_exit_code": int(exit_code.group(1)) if exit_code else None,
        "current_slot": (
            {
                "status": "satisfied",
                "slot_start": completed.slot_start,
                "run_id": completed.run_id,
                "run_started_at": completed.started_at,
            }
            if completed is not None
            else {
                "status": "due",
                "slot_start": _current_slot_start(now=now, interval_seconds=interval_seconds),
            }
        ),
        "latest_terminal": (
            {
                "run_id": str(latest[0]),
                "status": str(latest[1]),
                "started_at": str(latest[2]),
                "completed_at": str(latest[3]),
                "error_code": str(latest[4]) if latest[4] is not None else None,
            }
            if latest is not None
            else None
        ),
        "scan_lease": (
            {
                "owner_pid": int(lease[1]),
                "heartbeat_at": str(lease[2]),
                "expires_at": str(lease[3]),
            }
            if lease is not None
            else None
        ),
    }


def remove_schedule(*, home: Path | None = None) -> bool:
    root = home or Path.home()
    destination = root / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if not destination.exists():
        return False
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}", str(destination)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    destination.unlink()
    return True
