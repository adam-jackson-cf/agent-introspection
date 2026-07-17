"""LaunchAgent installation and cross-process SQLite leases."""

from __future__ import annotations

import os
import plistlib
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
    """Acquire a lease, reclaiming only when both PID is absent and lease expired."""
    now = datetime.now(UTC)
    expires = now + duration
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT owner_pid, expires_at FROM scheduler_leases WHERE name = ?", (name,)
        ).fetchone()
        if row is not None:
            owner_pid = int(row[0])
            expired = datetime.fromisoformat(str(row[1])) <= now
            if _pid_exists(owner_pid) or not expired:
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
    return {
        "Label": LABEL,
        "ProgramArguments": [str(executable), "scan", "--scheduled"],
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        "KeepAlive": False,
        "StartInterval": interval_seconds,
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


def schedule_status() -> dict[str, object]:
    result = subprocess.run(
        ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return {"installed": result.returncode == 0, "label": LABEL}


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
