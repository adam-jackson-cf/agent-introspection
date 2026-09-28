"""Experiment-only immutable scan-time observations for E-Recurrence-1."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agent_introspection.scan import RecurrenceFindingScanObservation


def new_capture_path(root: Path, *, now: datetime | None = None) -> Path:
    """Allocate a UTC-and-UUID-named experiment capture path without creating it."""
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("capture time must be timezone-aware")
    instant = instant.astimezone(UTC)
    stamp = instant.strftime("%Y%m%dT%H%M%S%fZ")
    return root / f"recurrence-finding-{stamp}-{uuid.uuid4()}.sqlite"


def capture_callback(path: Path) -> Callable[[tuple[RecurrenceFindingScanObservation, ...]], None]:
    """Return the scanner observer that persists nonempty immutable capture batches."""

    def capture(observations: tuple[RecurrenceFindingScanObservation, ...]) -> None:
        if observations:
            persist(path, observations)

    return capture


def persist(path: Path, observations: Sequence[RecurrenceFindingScanObservation]) -> None:
    """Create one fail-if-exists SQLite capture from exact scan-time observations."""
    rows = tuple(observations)
    if not rows:
        raise ValueError("finding capture requires an observed population")
    if path.exists():
        raise FileExistsError("finding capture path already exists")
    if path.suffix != ".sqlite":
        raise ValueError("finding capture must use a SQLite path")
    if any(not isinstance(row, RecurrenceFindingScanObservation) for row in rows):
        raise TypeError("finding capture rows must be scan observations")
    grouped = {(row.scan_run_id, _epoch_ns(row.observed_at)) for row in rows}
    if len(grouped) != 1:
        raise ValueError("one capture batch must have one scan run and observation instant")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE capture (
              scan_run_id TEXT NOT NULL,
              observed_at_ns INTEGER NOT NULL
            ) STRICT;
            CREATE TABLE finding_membership_observation (
              observation_id TEXT PRIMARY KEY,
              scan_run_id TEXT NOT NULL,
              observed_at_ns INTEGER NOT NULL,
              evidence_start_ns INTEGER,
              evidence_end_ns INTEGER,
              evidence_window_definition TEXT,
              finding_version_source_id TEXT NOT NULL,
              membership_source_id TEXT NOT NULL,
              activity_version_source_id TEXT NOT NULL,
              activity_source_started_at_ns INTEGER NOT NULL,
              activity_source_ended_at_ns INTEGER NOT NULL,
              finding_fingerprint TEXT NOT NULL,
              detector_id TEXT NOT NULL,
              detector_version INTEGER NOT NULL,
              finding_state TEXT NOT NULL,
              finding_occurrence_count INTEGER NOT NULL,
              finding_canonical_task_count INTEGER NOT NULL,
              finding_local_day_count INTEGER NOT NULL,
              task_membership_state TEXT NOT NULL,
              native_task_id TEXT,
              producer TEXT NOT NULL,
              activity_version INTEGER NOT NULL,
              latest_activity_version INTEGER NOT NULL,
              UNIQUE (finding_version_source_id, membership_source_id)
            ) STRICT;
            CREATE TRIGGER finding_membership_observation_no_update
            BEFORE UPDATE ON finding_membership_observation
            BEGIN SELECT RAISE(ABORT, 'finding observations are immutable'); END;
            CREATE TRIGGER finding_membership_observation_no_delete
            BEFORE DELETE ON finding_membership_observation
            BEGIN SELECT RAISE(ABORT, 'finding observations cannot be deleted'); END;
            """
        )
        scan_run_id, observed_at_ns = grouped.pop()
        connection.execute(
            "INSERT INTO capture VALUES (?, ?)",
            (scan_run_id, observed_at_ns),
        )
        connection.executemany(
            """
            INSERT INTO finding_membership_observation VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            tuple(
                _row_values(row) for row in sorted(rows, key=lambda item: item.membership_source_id)
            ),
        )
        connection.commit()
        connection.executescript(
            "CREATE TRIGGER finding_membership_observation_no_insert "
            "BEFORE INSERT ON finding_membership_observation "
            "BEGIN SELECT RAISE(ABORT, 'finding observations are sealed'); END;"
            "CREATE TRIGGER capture_no_insert BEFORE INSERT ON capture "
            "BEGIN SELECT RAISE(ABORT, 'capture is sealed'); END;"
            "CREATE TRIGGER capture_no_update BEFORE UPDATE ON capture "
            "BEGIN SELECT RAISE(ABORT, 'capture is immutable'); END;"
            "CREATE TRIGGER capture_no_delete BEFORE DELETE ON capture "
            "BEGIN SELECT RAISE(ABORT, 'capture is immutable'); END;"
        )
    finally:
        connection.close()


def _row_values(row: RecurrenceFindingScanObservation) -> tuple[object, ...]:
    identity = _digest(
        (
            row.finding_version_source_id,
            row.membership_source_id,
            row.activity_version_source_id,
            row.task_membership_state,
            row.native_task_id,
            row.evidence_window_definition,
        )
    )
    return (
        identity,
        row.scan_run_id,
        _epoch_ns(row.observed_at),
        row.evidence_start_ns,
        row.evidence_end_ns,
        row.evidence_window_definition,
        row.finding_version_source_id,
        row.membership_source_id,
        row.activity_version_source_id,
        row.activity_source_started_at_ns,
        row.activity_source_ended_at_ns,
        row.finding_fingerprint,
        row.detector_id,
        row.detector_version,
        row.finding_state,
        row.finding_occurrence_count,
        row.finding_canonical_task_count,
        row.finding_local_day_count,
        row.task_membership_state,
        row.native_task_id,
        row.producer,
        row.activity_version,
        row.latest_activity_version,
    )


def _epoch_ns(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value.astimezone(UTC) - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _digest(value: tuple[object, ...]) -> str:
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()
