"""Pure, allowlisted maintenance-ledger reducer for E-Pipeline-6."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,253}$")
_REQUIRED_FIELDS = (
    "database_identity",
    "check_type",
    "completed_at",
    "result",
    "backup_completed_at",
    "database_bytes",
    "wal_bytes",
    "freelist_count",
    "page_count",
    "migration_state",
    "runtime_host",
)

LEDGER_REMOTE_QUERY_ID = "p12-ledger-v3"
"""Stable identifier for the E-Pipeline-6 remote maintenance calculation."""

_REMOTE_RESULT_FIELDS = frozenset(
    {
        "event_id",
        "database_identity",
        "check_type",
        "completed_at_ns",
        "result",
        "backup_completed_at_ns",
        "database_bytes",
        "wal_bytes",
        "freelist_count",
        "page_count",
        "migration_state",
        "runtime_host",
    }
)

LEDGER_REMOTE_SQL = """
WITH selected_observations AS (
    SELECT
        event_id,
        database_identity,
        check_type,
        completed_at_ns,
        result,
        backup_completed_at_ns,
        database_bytes,
        wal_bytes,
        freelist_count,
        page_count,
        migration_state,
        runtime_host,
        row_number() OVER (
            PARTITION BY database_identity, check_type
            ORDER BY completed_at_ns DESC, event_id DESC
        ) AS selection_rank
    FROM (
        SELECT
            attributes_string['event.id'] AS event_id,
            attributes_string['dashboard.database_identity'] AS database_identity,
            attributes_string['dashboard.check_type'] AS check_type,
            toUInt64(timestamp) AS completed_at_ns,
            attributes_string['dashboard.result'] AS result,
            toUInt64(attributes_number['dashboard.backup_completed_at_ns'])
                AS backup_completed_at_ns,
            toUInt64(attributes_number['dashboard.database_bytes']) AS database_bytes,
            toUInt64(attributes_number['dashboard.wal_bytes']) AS wal_bytes,
            toUInt64(attributes_number['dashboard.freelist_count']) AS freelist_count,
            toInt64(attributes_number['dashboard.page_count']) AS page_count,
            attributes_string['dashboard.migration_state'] AS migration_state,
            attributes_string['dashboard.runtime_host'] AS runtime_host
        FROM signoz_logs.distributed_logs_v2
        WHERE timestamp > {maintenance_start_ns:UInt64}
          AND timestamp <= {maintenance_end_ns:UInt64}
          AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
          AND resource.`service.name`::String = 'agent-introspection'
          AND attributes_string['event.name'] = {event_name:String}
          AND attributes_string['dashboard.event_kind'] = 'primitive'
          AND attributes_string['dashboard.experiment_id'] = 'E-Pipeline-6'
          AND attributes_string['dashboard.run_id_hash'] = {run_id_hash:String}
          AND attributes_string['dashboard.query_id'] = {query_id:String}
          AND attributes_string['event.id'] IN ({event_ids})
          AND isFinite(attributes_number['dashboard.backup_completed_at_ns'])
          AND attributes_number['dashboard.backup_completed_at_ns'] >= 0
          AND attributes_number['dashboard.backup_completed_at_ns']
              = floor(attributes_number['dashboard.backup_completed_at_ns'])
          AND isFinite(attributes_number['dashboard.database_bytes'])
          AND attributes_number['dashboard.database_bytes'] >= 0
          AND attributes_number['dashboard.database_bytes']
              = floor(attributes_number['dashboard.database_bytes'])
          AND isFinite(attributes_number['dashboard.wal_bytes'])
          AND attributes_number['dashboard.wal_bytes'] >= 0
          AND attributes_number['dashboard.wal_bytes']
              = floor(attributes_number['dashboard.wal_bytes'])
          AND isFinite(attributes_number['dashboard.freelist_count'])
          AND attributes_number['dashboard.freelist_count'] >= 0
          AND attributes_number['dashboard.freelist_count']
              = floor(attributes_number['dashboard.freelist_count'])
          AND isFinite(attributes_number['dashboard.page_count'])
          AND attributes_number['dashboard.page_count']
              = floor(attributes_number['dashboard.page_count'])
    )
)
SELECT
    event_id,
    database_identity,
    check_type,
    completed_at_ns,
    result,
    backup_completed_at_ns,
    database_bytes,
    wal_bytes,
    freelist_count,
    page_count,
    migration_state,
    runtime_host
FROM selected_observations
WHERE selection_rank = 1
ORDER BY database_identity, check_type, completed_at_ns, event_id
""".strip()


@dataclass(frozen=True, slots=True)
class LedgerRemotePopulationAuthority:
    """Exact immutable IDs and bounded maintenance timestamps authorized for E6."""

    event_ids: tuple[str, ...] | None
    maintenance_start: datetime | None
    maintenance_end: datetime | None

    def missing_inputs(self) -> tuple[str, ...]:
        missing: list[str] = []
        if not self.event_ids or len(set(self.event_ids)) != len(self.event_ids):
            missing.append("immutable_event_ids")
        else:
            for event_id in self.event_ids:
                if not isinstance(event_id, str) or not _SAFE_ID.fullmatch(event_id):
                    missing.append("immutable_event_ids")
                    break
        for value, label in (
            (self.maintenance_start, "maintenance_start"),
            (self.maintenance_end, "maintenance_end"),
        ):
            if not isinstance(value, datetime) or value.tzinfo is None:
                missing.append(label)
        if (
            isinstance(self.maintenance_start, datetime)
            and isinstance(self.maintenance_end, datetime)
            and self.maintenance_start.tzinfo is not None
            and self.maintenance_end.tzinfo is not None
            and self.maintenance_start >= self.maintenance_end
        ):
            missing.append("maintenance_window")
        return tuple(missing)


@dataclass(frozen=True, slots=True)
class LedgerRemoteQuery:
    """Concrete SQL and binding values for one authorized remote calculation."""

    sql: str
    parameters: Mapping[str, str | int]


def build_ledger_remote_query(
    authority: LedgerRemotePopulationAuthority,
    *,
    start_bucket: int,
    end_bucket: int,
    event_name: str,
    run_id_hash: str,
) -> LedgerRemoteQuery | None:
    """Bind the E6 SQL only when exact population authority is available."""
    if authority.missing_inputs():
        return None
    if (
        not isinstance(start_bucket, int)
        or isinstance(start_bucket, bool)
        or not isinstance(end_bucket, int)
        or isinstance(end_bucket, bool)
        or start_bucket > end_bucket
        or not isinstance(event_name, str)
        or not _SAFE_ID.fullmatch(event_name)
        or not isinstance(run_id_hash, str)
        or not _SAFE_ID.fullmatch(run_id_hash)
    ):
        raise PrototypeContractError("invalid E6 remote query binding")
    assert authority.event_ids is not None
    assert authority.maintenance_start is not None
    assert authority.maintenance_end is not None
    parameters: dict[str, str | int] = {
        "maintenance_start_ns": _timestamp_ns(authority.maintenance_start),
        "maintenance_end_ns": _timestamp_ns(authority.maintenance_end),
        "start_bucket": start_bucket,
        "end_bucket": end_bucket,
        "event_name": event_name,
        "run_id_hash": run_id_hash,
        "query_id": LEDGER_REMOTE_QUERY_ID,
    }
    parameters.update(
        {f"event_{ordinal}": event_id for ordinal, event_id in enumerate(authority.event_ids)}
    )
    return LedgerRemoteQuery(
        sql=LEDGER_REMOTE_SQL.replace(
            "{event_ids}",
            ", ".join(f"{{event_{ordinal}:String}}" for ordinal in range(len(authority.event_ids))),
        ),
        parameters=parameters,
    )


@dataclass(frozen=True, slots=True)
class LedgerRemoteResult:
    """One selected, privacy-allowlisted maintenance projection."""

    event_id: str
    database_identity: str
    check_type: LedgerCheckType
    completed_at: datetime
    result: LedgerCheckResult
    backup_completed_at: datetime
    database_bytes: int
    wal_bytes: int
    freelist_count: int
    page_count: int
    migration_state: MigrationState
    runtime_host: str

    def __post_init__(self) -> None:
        _require_remote_identifier(self.event_id, "event ID")
        LedgerObservation(
            database_identity=self.database_identity,
            check_type=self.check_type,
            completed_at=self.completed_at,
            result=self.result,
            backup_completed_at=self.backup_completed_at,
            database_bytes=self.database_bytes,
            wal_bytes=self.wal_bytes,
            freelist_count=self.freelist_count,
            page_count=self.page_count,
            migration_state=self.migration_state,
            runtime_host=self.runtime_host,
        )


@dataclass(frozen=True, slots=True)
class LedgerRemoteOracle:
    """Independent typed local reconciliation result, or its blocking inputs."""

    selected: tuple[LedgerRemoteResult, ...] | None
    blocked_inputs: tuple[str, ...]


def parse_ledger_remote_result(
    rows: Sequence[Mapping[str, object]],
) -> tuple[LedgerRemoteResult, ...]:
    """Parse only the exact allowlisted columns returned by ``LEDGER_REMOTE_SQL``."""
    parsed_by_event_id: dict[str, LedgerRemoteResult] = {}
    for parsed in (_parse_remote_row(row) for row in rows):
        prior = parsed_by_event_id.get(parsed.event_id)
        if prior is not None and prior != parsed:
            raise PrototypeContractError("remote E6 event ID has conflicting payloads")
        parsed_by_event_id.setdefault(parsed.event_id, parsed)
    return tuple(parsed_by_event_id.values())


def reconcile_ledger_remote(
    authority: LedgerRemotePopulationAuthority,
    observations: Sequence[LedgerRemoteResult] | None,
) -> LedgerRemoteOracle:
    """Independently select the latest authorized maintenance row per database/check."""
    missing = authority.missing_inputs()
    if observations is None:
        missing = (*missing, "maintenance_observations")
    if missing:
        return LedgerRemoteOracle(None, tuple(sorted(set(missing))))
    assert authority.event_ids is not None
    assert authority.maintenance_start is not None
    assert authority.maintenance_end is not None
    assert observations is not None
    allowed_ids = frozenset(authority.event_ids)
    observed_ids = tuple(observation.event_id for observation in observations)
    if len(set(observed_ids)) != len(observed_ids) or set(observed_ids) != allowed_ids:
        return LedgerRemoteOracle(None, ("immutable_event_id_coverage",))
    if any(
        not authority.maintenance_start < observation.completed_at <= authority.maintenance_end
        for observation in observations
    ):
        return LedgerRemoteOracle(None, ("maintenance_time_coverage",))
    latest: dict[tuple[str, LedgerCheckType], LedgerRemoteResult] = {}
    for observation in observations:
        identity = (observation.database_identity, observation.check_type)
        prior = latest.get(identity)
        if prior is None or _remote_observation_key(observation) > _remote_observation_key(prior):
            latest[identity] = observation
    return LedgerRemoteOracle(
        tuple(latest[key] for key in sorted(latest)),
        (),
    )


def _parse_remote_row(row: Mapping[str, object]) -> LedgerRemoteResult:
    if set(row) != _REMOTE_RESULT_FIELDS:
        raise PrototypeContractError("remote E6 result must contain only the allowlisted shape")
    return LedgerRemoteResult(
        event_id=_remote_string(row["event_id"], "event ID", _SAFE_ID),
        database_identity=_remote_string(row["database_identity"], "database identity", _SAFE_ID),
        check_type=_remote_check_type(row["check_type"]),
        completed_at=_remote_timestamp(row["completed_at_ns"], "completed time"),
        result=_remote_check_result(row["result"]),
        backup_completed_at=_remote_timestamp(row["backup_completed_at_ns"], "backup time"),
        database_bytes=_remote_integer(row["database_bytes"], "database bytes", nonnegative=True),
        wal_bytes=_remote_integer(row["wal_bytes"], "WAL bytes", nonnegative=True),
        freelist_count=_remote_integer(row["freelist_count"], "freelist count", nonnegative=True),
        page_count=_remote_integer(row["page_count"], "page count", nonnegative=False),
        migration_state=_remote_migration_state(row["migration_state"]),
        runtime_host=_remote_string(row["runtime_host"], "runtime host", _SAFE_HOST),
    )


def _remote_observation_key(observation: LedgerRemoteResult) -> tuple[object, ...]:
    return (observation.completed_at, observation.event_id)


def _remote_string(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PrototypeContractError(f"unsafe remote {label}")
    return value


def _remote_check_type(value: object) -> LedgerCheckType:
    if not isinstance(value, str):
        raise PrototypeContractError("remote check type must be allowlisted")
    try:
        return LedgerCheckType(value)
    except ValueError as error:
        raise PrototypeContractError("remote check type must be allowlisted") from error


def _remote_check_result(value: object) -> LedgerCheckResult:
    if not isinstance(value, str):
        raise PrototypeContractError("remote check result must be allowlisted")
    try:
        return LedgerCheckResult(value)
    except ValueError as error:
        raise PrototypeContractError("remote check result must be allowlisted") from error


def _remote_migration_state(value: object) -> MigrationState:
    if not isinstance(value, str):
        raise PrototypeContractError("remote migration state must be allowlisted")
    try:
        return MigrationState(value)
    except ValueError as error:
        raise PrototypeContractError("remote migration state must be allowlisted") from error


def _remote_integer(value: object, label: str, *, nonnegative: bool) -> int:
    if type(value) is not int or (nonnegative and value < 0):
        raise PrototypeContractError(f"remote {label} must be an integer")
    return value


def _remote_timestamp(value: object, label: str) -> datetime:
    nanoseconds = _remote_integer(value, label, nonnegative=True)
    if nanoseconds % 1_000 != 0:
        raise PrototypeContractError(f"remote {label} must be microsecond-aligned")
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=nanoseconds // 1_000)


def _timestamp_ns(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return (value.astimezone(UTC) - epoch) // timedelta(microseconds=1) * 1_000


def _require_remote_identifier(value: object, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise PrototypeContractError(f"unsafe remote {label}")


class LedgerCheckType(StrEnum):
    INTEGRITY = "integrity"
    MAINTENANCE = "maintenance"


class LedgerCheckResult(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class MigrationState(StrEnum):
    CURRENT = "current"
    PENDING = "pending"
    FAILED = "failed"


class FreePageState(StrEnum):
    COMPUTED = "computed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class LedgerObservation:
    """The complete allowlisted maintenance projection for one completed check."""

    database_identity: str | None
    check_type: LedgerCheckType | None
    completed_at: datetime | None
    result: LedgerCheckResult | None
    backup_completed_at: datetime | None
    database_bytes: int | None
    wal_bytes: int | None
    freelist_count: int | None
    page_count: int | None
    migration_state: MigrationState | None
    runtime_host: str | None

    def __post_init__(self) -> None:
        for text_value, label, pattern in (
            (self.database_identity, "database identity", _SAFE_ID),
            (self.runtime_host, "runtime host", _SAFE_HOST),
        ):
            if text_value is not None and (
                not isinstance(text_value, str) or not pattern.fullmatch(text_value)
            ):
                raise PrototypeContractError(f"unsafe {label}")
        for enum_value, label, allowed_type in (
            (self.check_type, "check type", LedgerCheckType),
            (self.result, "check result", LedgerCheckResult),
            (self.migration_state, "migration state", MigrationState),
        ):
            if enum_value is not None and not isinstance(enum_value, allowed_type):
                raise PrototypeContractError(f"{label} must be allowlisted")
        for integer_value, label, nonnegative in (
            (self.database_bytes, "database bytes", True),
            (self.wal_bytes, "WAL bytes", True),
            (self.freelist_count, "freelist count", True),
            (self.page_count, "page count", False),
        ):
            if integer_value is not None and (
                isinstance(integer_value, bool)
                or not isinstance(integer_value, int)
                or (nonnegative and integer_value < 0)
            ):
                raise PrototypeContractError(f"{label} must be an integer")
        for timestamp_value, label in (
            (self.completed_at, "completed time"),
            (self.backup_completed_at, "backup time"),
        ):
            if timestamp_value is not None and (
                not isinstance(timestamp_value, datetime) or timestamp_value.tzinfo is None
            ):
                raise PrototypeContractError(f"{label} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class FreePagePercentage:
    state: FreePageState
    percent: float | None


@dataclass(frozen=True, slots=True)
class LedgerDatabaseState:
    database_identity: str
    check_type: LedgerCheckType
    completed_at: datetime
    result: LedgerCheckResult
    backup_completed_at: datetime
    database_bytes: int
    wal_bytes: int
    freelist_count: int
    page_count: int
    migration_state: MigrationState
    runtime_host: str
    check_age: timedelta
    backup_age: timedelta
    free_page_percentage: FreePagePercentage
    stale_check: bool
    stale_backup: bool


@dataclass(frozen=True, slots=True)
class LedgerCounts:
    input_observations: int
    in_window_observations: int
    selected_databases: int
    selected_checks: int
    stale_checks: int
    stale_backups: int
    failed_checks: int
    unavailable_free_page_percentages: int
    pending_migrations: int
    failed_migrations: int


@dataclass(frozen=True, slots=True)
class LedgerReduction:
    databases: tuple[LedgerDatabaseState, ...]
    counts: LedgerCounts
    missing_boundaries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LedgerReductionRequest:
    """The complete bounded input for one maintenance-ledger reduction."""

    observations: Sequence[LedgerObservation]
    start: datetime
    end: datetime
    evaluated_at: datetime
    stale_check_after: timedelta
    stale_backup_after: timedelta


def reduce_ledger_observations(request: LedgerReductionRequest) -> LedgerReduction:
    """Select each check type's latest complete row in ``(start, end]`` deterministically."""
    if request.start >= request.end:
        raise PrototypeContractError("ledger source window must have start before end")
    if request.evaluated_at < request.end:
        raise PrototypeContractError("ledger evaluation must not precede source window end")
    if request.stale_check_after < timedelta() or request.stale_backup_after < timedelta():
        raise PrototypeContractError("ledger stale thresholds must be nonnegative")

    missing: list[str] = []
    complete: list[_CompleteLedgerObservation] = []
    if not request.observations:
        missing.append("maintenance_observation")
    for ordinal, observation in enumerate(request.observations):
        absent = [field for field in _REQUIRED_FIELDS if getattr(observation, field) is None]
        missing.extend(f"observation[{ordinal}].{field}" for field in absent)
        if not absent:
            complete.append(_complete_observation(observation))

    latest: dict[tuple[str, LedgerCheckType], _CompleteLedgerObservation] = {}
    for complete_observation in complete:
        if request.start < complete_observation.completed_at <= request.end:
            identity = (
                complete_observation.database_identity,
                complete_observation.check_type,
            )
            prior = latest.get(identity)
            if prior is None or _observation_key(complete_observation) > _observation_key(prior):
                latest[identity] = complete_observation

    databases = tuple(
        _database_state(
            observation,
            request.evaluated_at,
            request.stale_check_after,
            request.stale_backup_after,
        )
        for _, observation in sorted(latest.items())
    )
    counts = LedgerCounts(
        input_observations=len(request.observations),
        in_window_observations=sum(
            1
            for complete_observation in complete
            if request.start < complete_observation.completed_at <= request.end
        ),
        selected_databases=len({database.database_identity for database in databases}),
        selected_checks=len(databases),
        stale_checks=sum(database.stale_check for database in databases),
        stale_backups=sum(database.stale_backup for database in databases),
        failed_checks=sum(database.result is LedgerCheckResult.FAILED for database in databases),
        unavailable_free_page_percentages=sum(
            database.free_page_percentage.state is FreePageState.UNAVAILABLE
            for database in databases
        ),
        pending_migrations=sum(
            database.migration_state is MigrationState.PENDING for database in databases
        ),
        failed_migrations=sum(
            database.migration_state is MigrationState.FAILED for database in databases
        ),
    )
    return LedgerReduction(databases, counts, tuple(sorted(set(missing))))


def build_ledger_proof(
    reduction: LedgerReduction,
    *,
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
) -> PipelineExperimentProof:
    """Build a fail-closed E-Pipeline-6 proof from a bounded reducer output."""
    assertions = {
        "required_fields_present": not reduction.missing_boundaries,
        "every_selected_check_passed": reduction.counts.failed_checks == 0,
        "every_selected_migration_current": (
            reduction.counts.pending_migrations == 0 and reduction.counts.failed_migrations == 0
        ),
    }
    if reduction.missing_boundaries:
        result = ExperimentResult.BLOCKED
    elif all(assertions.values()) and provenance is EvidenceProvenance.FRESH_REAL:
        result = ExperimentResult.PROVEN
    else:
        result = ExperimentResult.FAILED
    return PipelineExperimentProof(
        experiment_id=PipelineExperimentId.LEDGER,
        run_id=run_id,
        result=result,
        provenance=provenance,
        source_boundary=source_boundary,
        metrics={
            "input_observations": reduction.counts.input_observations,
            "in_window_observations": reduction.counts.in_window_observations,
            "selected_databases": reduction.counts.selected_databases,
            "selected_checks": reduction.counts.selected_checks,
            "failed_checks": reduction.counts.failed_checks,
            "stale_checks": reduction.counts.stale_checks,
            "stale_backups": reduction.counts.stale_backups,
            "unavailable_free_page_percentages": reduction.counts.unavailable_free_page_percentages,
        },
        assertions=assertions,
        evidence_ids=tuple(
            f"{database.database_identity}:{database.check_type.value}"
            for database in reduction.databases
        ),
        blocked_boundaries=reduction.missing_boundaries,
        proposal=(
            "Allowlisted maintenance-ledger reconciliation only; no database access or writes."
        ),
    )


def _database_state(
    observation: _CompleteLedgerObservation,
    evaluated_at: datetime,
    stale_check_after: timedelta,
    stale_backup_after: timedelta,
) -> LedgerDatabaseState:
    page_count = observation.page_count
    free_page_percentage = (
        FreePagePercentage(
            FreePageState.COMPUTED,
            100 * observation.freelist_count / page_count,
        )
        if page_count > 0
        else FreePagePercentage(FreePageState.UNAVAILABLE, None)
    )
    check_age = evaluated_at - observation.completed_at
    backup_age = evaluated_at - observation.backup_completed_at
    return LedgerDatabaseState(
        database_identity=observation.database_identity,
        check_type=observation.check_type,
        completed_at=observation.completed_at,
        result=observation.result,
        backup_completed_at=observation.backup_completed_at,
        database_bytes=observation.database_bytes,
        wal_bytes=observation.wal_bytes,
        freelist_count=observation.freelist_count,
        page_count=page_count,
        migration_state=observation.migration_state,
        runtime_host=observation.runtime_host,
        check_age=check_age,
        backup_age=backup_age,
        free_page_percentage=free_page_percentage,
        stale_check=check_age >= stale_check_after,
        stale_backup=backup_age >= stale_backup_after,
    )


def _observation_key(observation: _CompleteLedgerObservation) -> tuple[object, ...]:
    """Provide deterministic latest-row selection for equal completion timestamps."""
    return (
        observation.completed_at,
        observation.check_type,
        observation.result,
        observation.backup_completed_at,
        observation.database_bytes,
        observation.wal_bytes,
        observation.freelist_count,
        observation.page_count,
        observation.migration_state,
        observation.runtime_host,
    )


@dataclass(frozen=True, slots=True)
class _CompleteLedgerObservation:
    database_identity: str
    check_type: LedgerCheckType
    completed_at: datetime
    result: LedgerCheckResult
    backup_completed_at: datetime
    database_bytes: int
    wal_bytes: int
    freelist_count: int
    page_count: int
    migration_state: MigrationState
    runtime_host: str


def _complete_observation(observation: LedgerObservation) -> _CompleteLedgerObservation:
    assert observation.database_identity is not None
    assert observation.check_type is not None
    assert observation.completed_at is not None
    assert observation.result is not None
    assert observation.backup_completed_at is not None
    assert observation.database_bytes is not None
    assert observation.wal_bytes is not None
    assert observation.freelist_count is not None
    assert observation.page_count is not None
    assert observation.migration_state is not None
    assert observation.runtime_host is not None
    return _CompleteLedgerObservation(
        database_identity=observation.database_identity,
        check_type=observation.check_type,
        completed_at=observation.completed_at,
        result=observation.result,
        backup_completed_at=observation.backup_completed_at,
        database_bytes=observation.database_bytes,
        wal_bytes=observation.wal_bytes,
        freelist_count=observation.freelist_count,
        page_count=observation.page_count,
        migration_state=observation.migration_state,
        runtime_host=observation.runtime_host,
    )
