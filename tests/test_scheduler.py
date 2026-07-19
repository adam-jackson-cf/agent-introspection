import os
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_introspection.scheduler import (
    LABEL,
    acquire_lease,
    completed_in_current_slot,
    launch_agent_payload,
    release_lease,
    schedule_status,
)


def lease_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE scheduler_leases "
        "(name TEXT PRIMARY KEY, owner_pid INTEGER, heartbeat_at TEXT, expires_at TEXT)"
    )
    return connection


def test_live_leases_cannot_be_reclaimed() -> None:
    connection = lease_database()
    connection.execute(
        "INSERT INTO scheduler_leases VALUES (?, ?, ?, ?)",
        (
            "scan",
            os.getpid(),
            datetime.now(UTC).isoformat(),
            (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        ),
    )
    connection.commit()
    with pytest.raises(RuntimeError, match="already held"):
        acquire_lease(connection)


def test_absent_pid_is_reclaimed_before_its_lease_expires_and_owner_can_release() -> None:
    connection = lease_database()
    connection.execute(
        "INSERT INTO scheduler_leases VALUES (?, ?, ?, ?)",
        (
            "scan",
            999_999_999,
            datetime.now(UTC).isoformat(),
            (datetime.now(UTC) + timedelta(minutes=59)).isoformat(),
        ),
    )
    connection.commit()
    lease = acquire_lease(connection)
    assert lease.owner_pid == os.getpid()
    release_lease(connection, lease)
    assert connection.execute("SELECT COUNT(*) FROM scheduler_leases").fetchone()[0] == 0


def test_launch_agent_payload_is_canonical_and_absolute(tmp_path: Path) -> None:
    payload = launch_agent_payload(
        executable=tmp_path / "bin" / "agent-introspection",
        config_path=tmp_path / "config.toml",
        working_directory=tmp_path,
        docker_host="unix:///Users/adamjackson/.orbstack/run/docker.sock",
        log_directory=tmp_path / "logs",
        interval_seconds=3_600,
        timezone="Europe/London",
    )
    assert payload["Label"] == LABEL
    assert payload["StartCalendarInterval"] == {"Minute": 0}
    assert "StartInterval" not in payload
    assert payload["RunAtLoad"] is True
    assert payload["KeepAlive"] is False
    assert payload["EnvironmentVariables"]["TZ"] == "Europe/London"


def test_schedule_status_exposes_loaded_state_freshness_and_current_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE scan_runs (
            id TEXT, status TEXT, started_at TEXT, completed_at TEXT, error_code TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE scheduler_leases (
            name TEXT, owner_pid INTEGER, heartbeat_at TEXT, expires_at TEXT
        )
        """
    )
    connection.execute(
        """
        INSERT INTO scan_runs VALUES (
            'success-current', 'succeeded', '2026-07-10T12:05:00+00:00',
            '2026-07-10T12:05:30+00:00', NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO scheduler_leases VALUES (
            'scan', 123, '2026-07-10T12:06:00+00:00', '2026-07-10T13:06:00+00:00'
        )
        """
    )
    monkeypatch.setattr(
        "agent_introspection.scheduler.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, "state = not running\nlast exit code = 0\n", ""
        ),
    )

    status = schedule_status(
        connection,
        now=datetime(2026, 7, 10, 12, 30, tzinfo=UTC),
        interval_seconds=3_600,
    )

    assert status["installed"] is True
    assert status["state"] == "not running"
    assert status["last_exit_code"] == 0
    assert status["current_slot"] == {
        "status": "satisfied",
        "slot_start": "2026-07-10T12:00:00+00:00",
        "run_id": "success-current",
        "run_started_at": "2026-07-10T12:05:00+00:00",
    }
    assert status["latest_terminal"] == {
        "run_id": "success-current",
        "status": "succeeded",
        "started_at": "2026-07-10T12:05:00+00:00",
        "completed_at": "2026-07-10T12:05:30+00:00",
        "error_code": None,
    }
    assert status["scan_lease"] == {
        "owner_pid": 123,
        "heartbeat_at": "2026-07-10T12:06:00+00:00",
        "expires_at": "2026-07-10T13:06:00+00:00",
    }


def test_successful_and_no_data_runs_suppress_only_their_utc_slot() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE scan_runs (id TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL)"
    )
    connection.executemany(
        "INSERT INTO scan_runs VALUES (?, ?, ?)",
        [
            ("failed-current", "failed", "2026-07-10T12:45:00+00:00"),
            ("success-prior", "succeeded", "2026-07-10T11:59:59+00:00"),
            ("no-data-current", "no_data", "2026-07-10T12:00:00+00:00"),
        ],
    )
    completed = completed_in_current_slot(
        connection,
        now=datetime(2026, 7, 10, 12, 59, 59, tzinfo=UTC),
        interval_seconds=3_600,
    )
    assert completed is not None
    assert completed.run_id == "no-data-current"
    assert completed.slot_start == "2026-07-10T12:00:00+00:00"
    assert (
        completed_in_current_slot(
            connection,
            now=datetime(2026, 7, 10, 13, 0, tzinfo=UTC),
            interval_seconds=3_600,
        )
        is None
    )


def test_failed_run_does_not_suppress_retry_and_clock_is_validated() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE scan_runs (id TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO scan_runs VALUES (?, ?, ?)",
        ("failed", "failed", "2026-07-10T12:15:00+00:00"),
    )
    assert (
        completed_in_current_slot(
            connection,
            now=datetime(2026, 7, 10, 12, 30, tzinfo=UTC),
            interval_seconds=3_600,
        )
        is None
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        completed_in_current_slot(
            connection,
            now=datetime(2026, 7, 10, 6, 30),
            interval_seconds=3_600,
        )
    with pytest.raises(ValueError, match="positive integer"):
        completed_in_current_slot(
            connection,
            now=datetime(2026, 7, 10, 6, 30, tzinfo=UTC),
            interval_seconds=0,
        )
