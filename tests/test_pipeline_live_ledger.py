import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from experiments.dashboard_prototype.contracts import ExperimentResult
from experiments.dashboard_prototype.pipeline_ledger import (
    LedgerCheckResult,
    LedgerCheckType,
    MigrationState,
)
from experiments.dashboard_prototype.pipeline_live_common import (
    LiveExperimentEvidence,
    LiveProofRequest,
)
from experiments.dashboard_prototype.pipeline_live_ledger import (
    LedgerMaintenancePrimitive,
    extract,
)

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(hours=10)


def request() -> LiveProofRequest:
    return LiveProofRequest(run_id="ledger-live-run", start=START, end=END)


def authority(
    evidence: LiveExperimentEvidence,
) -> tuple[LedgerMaintenancePrimitive, ...]:
    return cast(tuple[LedgerMaintenancePrimitive, ...], evidence.primitives)


def maintenance_connection(*, extra_column: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    suffix = ", ignored TEXT NOT NULL DEFAULT 'no'" if extra_column else ""
    connection.execute(
        "CREATE TABLE maintenance_observations ("
        "event_id TEXT NOT NULL, database_identity TEXT NOT NULL, "
        "check_type TEXT NOT NULL, completed_at TEXT NOT NULL, result TEXT NOT NULL, "
        "backup_completed_at TEXT NOT NULL, database_bytes INTEGER NOT NULL, "
        "wal_bytes INTEGER NOT NULL, freelist_count INTEGER NOT NULL, "
        "page_count INTEGER NOT NULL, migration_state TEXT NOT NULL, "
        f"runtime_host TEXT NOT NULL{suffix})"
    )
    return connection


@dataclass(frozen=True, slots=True)
class MaintenanceObservationRow:
    event_id: str
    database_identity: str
    completed_at: datetime
    result: str = "passed"
    check_type: str = "integrity"
    backup_completed_at: datetime | None = None
    database_bytes: int = 400
    wal_bytes: int = 20
    freelist_count: int = 10
    page_count: int = 100
    migration_state: str = "current"
    runtime_host: str = "db-host-1"


def insert_observation(
    connection: sqlite3.Connection, observation: MaintenanceObservationRow
) -> None:
    connection.execute(
        "INSERT INTO maintenance_observations "
        "(event_id, database_identity, check_type, completed_at, result, "
        "backup_completed_at, database_bytes, wal_bytes, freelist_count, "
        "page_count, migration_state, runtime_host) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            observation.event_id,
            observation.database_identity,
            observation.check_type,
            observation.completed_at.isoformat(),
            observation.result,
            (
                observation.backup_completed_at or observation.completed_at - timedelta(minutes=30)
            ).isoformat(),
            observation.database_bytes,
            observation.wal_bytes,
            observation.freelist_count,
            observation.page_count,
            observation.migration_state,
            observation.runtime_host,
        ),
    )


def test_absent_or_nonallowlisted_projection_blocks_exact_boundary() -> None:
    absent = extract(sqlite3.connect(":memory:"), request())
    extra = extract(maintenance_connection(extra_column=True), request())

    for evidence in (absent, extra):
        assert evidence.proof.result is ExperimentResult.BLOCKED
        assert evidence.proof.blocked_boundaries == ("maintenance_observation",)
        assert evidence.primitives == ()
        assert evidence.remote_query_id is None


def test_complete_projection_emits_full_original_population_as_typed_authority() -> None:
    connection = maintenance_connection()
    rows = (
        MaintenanceObservationRow(
            "ledger-event-older",
            "/private/older.sqlite",
            START + timedelta(hours=1),
        ),
        MaintenanceObservationRow("ledger-event-at-start", "/private/at-start.sqlite", START),
        MaintenanceObservationRow("ledger-event-at-end", "/private/at-end.sqlite", END),
        MaintenanceObservationRow(
            "ledger-event-latest",
            "/private/older.sqlite",
            END - timedelta(hours=1),
        ),
    )
    for row in rows:
        insert_observation(connection, row)

    evidence = extract(connection, request())
    primitives = authority(evidence)

    assert evidence.proof.result is ExperimentResult.PROVEN
    assert evidence.remote_query_id == "p12-ledger-v3"
    assert [row.event_id for row in primitives] == [
        "ledger-event-older",
        "ledger-event-latest",
        "ledger-event-at-end",
    ]
    assert [row.completed_at for row in primitives] == [
        START + timedelta(hours=1),
        END - timedelta(hours=1),
        END,
    ]
    first = primitives[0]
    assert first.experiment_id.value == "E-Pipeline-6"
    assert first.database_identity == hashlib.sha256(b"/private/older.sqlite").hexdigest()
    assert first.runtime_host == hashlib.sha256(b"db-host-1").hexdigest()
    assert first.check_type is LedgerCheckType.INTEGRITY
    assert first.result is LedgerCheckResult.PASSED
    assert first.backup_completed_at == START + timedelta(minutes=30)
    assert (
        first.database_bytes,
        first.wal_bytes,
        first.freelist_count,
        first.page_count,
        first.migration_state,
    ) == (400, 20, 10, 100, MigrationState.CURRENT)
    assert evidence.proof.metrics["input_observations"] == 3
    assert all("/private/" not in repr(row) for row in primitives)


def test_primitive_retains_failed_kind_and_all_count_fields_without_reduction() -> None:
    connection = maintenance_connection()
    inserted = MaintenanceObservationRow(
        "ledger-event-maintenance",
        "database-a",
        END - timedelta(hours=1),
        result="failed",
        check_type="maintenance",
        backup_completed_at=END - timedelta(hours=2),
        database_bytes=999,
        wal_bytes=88,
        freelist_count=77,
        page_count=-1,
        migration_state="failed",
        runtime_host="host-2",
    )
    insert_observation(connection, inserted)

    evidence = extract(connection, request())
    assert evidence.proof.result is ExperimentResult.FAILED
    assert evidence.remote_query_id == "p12-ledger-v3"
    (primitive,) = authority(evidence)
    assert primitive.event_id == inserted.event_id
    assert primitive.check_type is LedgerCheckType.MAINTENANCE
    assert primitive.result is LedgerCheckResult.FAILED
    assert primitive.backup_completed_at == inserted.backup_completed_at
    assert (
        primitive.database_bytes,
        primitive.wal_bytes,
        primitive.freelist_count,
        primitive.page_count,
        primitive.migration_state,
    ) == (999, 88, 77, -1, MigrationState.FAILED)


def test_missing_immutable_event_id_blocks_direct_authority_without_leakage() -> None:
    connection = maintenance_connection()
    insert_observation(
        connection,
        MaintenanceObservationRow("bad event id", "/private/database.sqlite", END),
    )

    evidence = extract(connection, request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == ("immutable_observation_id",)
    assert evidence.primitives == ()
    assert evidence.remote_query_id is None
    assert "/private/" not in evidence.proof.canonical_json()


def test_duplicate_immutable_event_id_blocks_direct_authority() -> None:
    connection = maintenance_connection()
    insert_observation(
        connection,
        MaintenanceObservationRow("ledger-event-1", "database-a", END - timedelta(hours=2)),
    )
    insert_observation(
        connection,
        MaintenanceObservationRow("ledger-event-1", "database-b", END - timedelta(hours=1)),
    )

    evidence = extract(connection, request())

    assert evidence.proof.result is ExperimentResult.BLOCKED
    assert evidence.proof.blocked_boundaries == ("immutable_observation_id",)
    assert evidence.primitives == ()
    assert evidence.remote_query_id is None
