"""Pure, read-only terminal-state cadence oracle for E-Pipeline-2."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from experiments.dashboard_prototype.contracts import EvidenceProvenance, ExperimentResult
from experiments.dashboard_prototype.pipeline_common import (
    PipelineExperimentId,
    PipelineExperimentProof,
)

A03_TERMINAL_OUTCOME_SQL = """
SELECT
    toUInt64(
        uniqExactIf(
            attributes_string['event.id'],
            attributes_string['dashboard.terminal_state'] = 'succeeded'
        )
    ) AS succeeded_count,
    toUInt64(
        uniqExactIf(
            attributes_string['event.id'],
            attributes_string['dashboard.terminal_state'] = 'failed'
        )
    ) AS failed_count,
    toUInt64(
        uniqExactIf(
            attributes_string['event.id'],
            attributes_string['dashboard.terminal_state'] = 'no_data'
        )
    ) AS no_data_count,
    toUInt64(uniqExact(attributes_string['event.id'])) AS observed_count,
    toUInt64(
        uniqExactIf(
            attributes_string['event.id'],
            attributes_string['dashboard.terminal_state'] NOT IN (
                'succeeded', 'failed', 'no_data'
            )
        )
    ) AS unclassified_terminal_state_count
FROM signoz_logs.distributed_logs_v2
WHERE attributes_string['event.id'] IN ({event_ids})
  AND timestamp > {start:DateTime64(9, 'UTC')}
  AND timestamp <= {end:DateTime64(9, 'UTC')}
""".strip()


A04_TERMINAL_CADENCE_SQL = """
SELECT
    any(attributes_string['dashboard.schedule_policy_identity'])
        AS policy_identity,
    toUInt64(any(attributes_number['dashboard.schedule_interval_seconds']))
        AS interval_seconds,
    any(attributes_string['dashboard.schedule_timezone']) AS timezone,
    any(attributes_string['dashboard.schedule_anchor_at']) AS anchor_time,
    maxIf(
        timestamp,
        attributes_string['dashboard.terminal_state'] = 'succeeded'
    ) AS latest_successful_completion,
    toUInt64(uniqExact(attributes_string['dashboard.schedule_policy_identity']))
        AS policy_identity_count,
    toUInt64(uniqExact(attributes_number['dashboard.schedule_interval_seconds']))
        AS interval_seconds_count,
    toUInt64(uniqExact(attributes_string['dashboard.schedule_timezone']))
        AS timezone_count,
    toUInt64(uniqExact(attributes_string['dashboard.schedule_anchor_at']))
        AS anchor_time_count
FROM signoz_logs.distributed_logs_v2
WHERE attributes_string['event.id'] IN ({event_ids})
  AND timestamp > {start:DateTime64(9, 'UTC')}
  AND timestamp <= {end:DateTime64(9, 'UTC')}
""".strip()


class DurableTerminalState(StrEnum):
    """Only terminal statuses explicitly persisted by the durable source.

    ``running`` is nonterminal and therefore unsupported in this population.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NO_DATA = "no_data"


@dataclass(frozen=True, slots=True)
class TerminalSchedulePolicy:
    """Authoritative cadence definition retained with the durable observations."""

    policy_identity: str
    interval: timedelta
    timezone: str
    anchor_time: datetime


@dataclass(frozen=True, slots=True)
class TerminalObservation:
    """Allowlisted durable terminal row; no inferred partial or cancellation state."""

    durable_id: str | None
    terminal_state: str | None
    source_time: datetime
    scheduled_time: datetime


@dataclass(frozen=True, slots=True)
class TerminalCadenceCounts:
    succeeded: int
    failed: int
    no_data: int
    observed: int
    expected_slots: int
    missed_cadence: int


@dataclass(frozen=True, slots=True)
class A03TerminalOutcomeResult:
    """Exact terminal-outcome aggregate returned by the A03 remote calculation."""

    succeeded_count: int
    failed_count: int
    no_data_count: int
    observed_count: int
    unclassified_terminal_state_count: int


@dataclass(frozen=True, slots=True)
class A04TerminalCadenceResult:
    """Exact schedule and freshness aggregate returned by the A04 remote calculation."""

    policy_identity: str
    interval_seconds: int
    timezone: str
    anchor_time: str
    latest_successful_completion: datetime | None
    policy_identity_count: int
    interval_seconds_count: int
    timezone_count: int
    anchor_time_count: int


@dataclass(frozen=True, slots=True)
class A04TerminalCadenceOracle:
    """Independent local evaluation of the Appendix cadence formula."""

    scan_age_seconds: int | None
    missed_cadence_count: int | None
    terminal_scan_count: int
    blocked_boundaries: tuple[str, ...]


def parse_a03_terminal_outcome_result(row: Mapping[str, object]) -> A03TerminalOutcomeResult:
    """Parse one typed A03 aggregate row; absent or noninteger fields are invalid."""
    result = A03TerminalOutcomeResult(
        succeeded_count=_required_count(row, "succeeded_count"),
        failed_count=_required_count(row, "failed_count"),
        no_data_count=_required_count(row, "no_data_count"),
        observed_count=_required_count(row, "observed_count"),
        unclassified_terminal_state_count=_required_count(row, "unclassified_terminal_state_count"),
    )
    if result.observed_count != (
        result.succeeded_count
        + result.failed_count
        + result.no_data_count
        + result.unclassified_terminal_state_count
    ):
        raise ValueError("A03 terminal population does not reconcile")
    return result


def parse_a04_terminal_cadence_result(row: Mapping[str, object]) -> A04TerminalCadenceResult:
    """Parse one typed A04 aggregate row without coercing remote scalar values."""
    latest_successful_completion = row.get("latest_successful_completion")
    if latest_successful_completion is not None and (
        not isinstance(latest_successful_completion, datetime)
        or latest_successful_completion.tzinfo is None
        or latest_successful_completion.utcoffset() is None
    ):
        raise ValueError("latest_successful_completion must be timezone-aware datetime or null")
    result = A04TerminalCadenceResult(
        policy_identity=_required_text(row, "policy_identity"),
        interval_seconds=_required_count(row, "interval_seconds"),
        timezone=_required_timezone(row, "timezone"),
        anchor_time=_required_timestamp_text(row, "anchor_time"),
        latest_successful_completion=latest_successful_completion,
        policy_identity_count=_required_count(row, "policy_identity_count"),
        interval_seconds_count=_required_count(row, "interval_seconds_count"),
        timezone_count=_required_count(row, "timezone_count"),
        anchor_time_count=_required_count(row, "anchor_time_count"),
    )
    if (
        result.interval_seconds <= 0
        or result.policy_identity_count != 1
        or result.interval_seconds_count != 1
        or result.timezone_count != 1
        or result.anchor_time_count != 1
    ):
        raise ValueError("A04 schedule authority must be singular and positive")
    return result


def oracle_a03_terminal_outcomes(
    observations: tuple[TerminalObservation, ...], *, start: datetime, end: datetime
) -> A03TerminalOutcomeResult | tuple[str, ...]:
    """Independently count the exhaustive bounded terminal-state population for A03."""
    if not _ordered_aware_window(start, end):
        return ("ordered timezone-aware source boundary",)
    selected = tuple(row for row in observations if start < row.source_time <= end)
    authority = _terminal_authority_boundaries(selected)
    supported = frozenset(state.value for state in DurableTerminalState)
    unclassified = tuple(
        sorted(
            {
                row.terminal_state
                for row in selected
                if isinstance(row.terminal_state, str) and row.terminal_state not in supported
            }
        )
    )
    if authority or unclassified:
        return (
            *authority,
            *(f"unsupported durable terminal state:{state}" for state in unclassified),
        )
    unique_by_id: dict[str, TerminalObservation] = {}
    for row in selected:
        assert row.durable_id is not None
        prior = unique_by_id.setdefault(row.durable_id, row)
        if prior.terminal_state != row.terminal_state:
            return ("durable terminal state authority",)
    unique = tuple(unique_by_id.values())
    return A03TerminalOutcomeResult(
        succeeded_count=sum(row.terminal_state == DurableTerminalState.SUCCEEDED for row in unique),
        failed_count=sum(row.terminal_state == DurableTerminalState.FAILED for row in unique),
        no_data_count=sum(row.terminal_state == DurableTerminalState.NO_DATA for row in unique),
        observed_count=len(unique),
        unclassified_terminal_state_count=0,
    )


def oracle_a04_terminal_cadence(
    observations: tuple[TerminalObservation, ...],
    policy: TerminalSchedulePolicy,
    *,
    start: datetime,
    end: datetime,
    evaluated_at: datetime,
) -> A04TerminalCadenceOracle:
    """Independently calculate A04 scan age and missed cadence from schedule authority."""
    missing = _missing_boundaries(policy, start, end, evaluated_at)
    selected = tuple(row for row in observations if start < row.source_time <= end)
    missing.extend(_terminal_authority_boundaries(selected))
    supported = frozenset(state.value for state in DurableTerminalState)
    unknown = sorted(
        {
            row.terminal_state
            for row in selected
            if isinstance(row.terminal_state, str) and row.terminal_state not in supported
        }
    )
    missing.extend(f"unsupported durable terminal state:{state}" for state in unknown)
    if missing:
        return A04TerminalCadenceOracle(None, None, len(selected), tuple(missing))
    try:
        ZoneInfo(policy.timezone)
    except ZoneInfoNotFoundError:
        return A04TerminalCadenceOracle(
            None, None, len(selected), ("authoritative schedule timezone",)
        )
    latest_success = max(
        (
            row.source_time
            for row in selected
            if row.terminal_state == DurableTerminalState.SUCCEEDED
        ),
        default=None,
    )
    if latest_success is None:
        return A04TerminalCadenceOracle(None, None, len(selected), ())
    scan_age_seconds = int((evaluated_at - latest_success).total_seconds())
    interval_seconds = int(policy.interval.total_seconds())
    missed_cadence_count = max(0, scan_age_seconds // interval_seconds - 1)
    return A04TerminalCadenceOracle(scan_age_seconds, missed_cadence_count, len(selected), ())


@dataclass(frozen=True, slots=True)
class TerminalCadenceReduction:
    counts: TerminalCadenceCounts
    freshness_seconds: int | None
    stale_boundary: datetime
    is_stale: bool
    unsupported_states: tuple[str, ...]
    duplicate_slots: tuple[datetime, ...]
    unexpected_slots: tuple[datetime, ...]


@dataclass(frozen=True, slots=True)
class TerminalCadenceProofRequest:
    """Bounded durable inputs required to construct a terminal cadence proof."""

    run_id: str
    provenance: EvidenceProvenance
    source_boundary: str
    observations: tuple[TerminalObservation, ...]
    policy: TerminalSchedulePolicy
    start: datetime
    end: datetime
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class _TerminalCadenceProofContent:
    request: TerminalCadenceProofRequest
    result: ExperimentResult
    metrics: dict[str, int | bool | str | None]
    assertions: dict[str, bool]
    evidence_ids: tuple[str, ...]
    blocked_boundaries: tuple[str, ...]


def reduce_terminal_cadence(
    observations: tuple[TerminalObservation, ...],
    policy: TerminalSchedulePolicy,
    *,
    start: datetime,
    end: datetime,
    evaluated_at: datetime,
) -> TerminalCadenceReduction:
    """Reduce canonical `(start, end]` source membership into cadence facts."""
    expected = _expected_slots(policy, start=start, evaluated_at=evaluated_at)
    accepted = tuple(row for row in observations if start < row.source_time <= end)
    succeeded = sum(row.terminal_state == DurableTerminalState.SUCCEEDED for row in accepted)
    failed = sum(row.terminal_state == DurableTerminalState.FAILED for row in accepted)
    no_data = sum(row.terminal_state == DurableTerminalState.NO_DATA for row in accepted)
    supported_states = frozenset(state.value for state in DurableTerminalState)
    unsupported = tuple(
        sorted(
            {
                row.terminal_state
                for row in accepted
                if isinstance(row.terminal_state, str)
                and row.terminal_state not in supported_states
            }
        )
    )
    slot_counts: dict[datetime, int] = {}
    for row in accepted:
        slot_counts[row.scheduled_time] = slot_counts.get(row.scheduled_time, 0) + 1
    duplicate_slots = tuple(sorted(slot for slot, count in slot_counts.items() if count > 1))
    unexpected_slots = tuple(sorted(slot for slot in slot_counts if slot not in expected))
    latest_successful = max(
        (
            row.source_time
            for row in accepted
            if row.terminal_state == DurableTerminalState.SUCCEEDED
        ),
        default=None,
    )
    if latest_successful is None:
        freshness = None
        missed_cadence = len(expected)
    else:
        freshness = int((evaluated_at - latest_successful).total_seconds())
        missed_cadence = max(0, int(freshness // policy.interval.total_seconds()) - 1)
    stale_boundary = evaluated_at - policy.interval
    return TerminalCadenceReduction(
        counts=TerminalCadenceCounts(
            succeeded=succeeded,
            failed=failed,
            no_data=no_data,
            observed=len(accepted),
            expected_slots=len(expected),
            missed_cadence=missed_cadence,
        ),
        freshness_seconds=freshness,
        stale_boundary=stale_boundary,
        is_stale=latest_successful is None or latest_successful <= stale_boundary,
        unsupported_states=unsupported,
        duplicate_slots=duplicate_slots,
        unexpected_slots=unexpected_slots,
    )


def build_terminal_cadence_proof(
    request: TerminalCadenceProofRequest,
) -> PipelineExperimentProof:
    """Build a fail-closed proof from bounded durable terminal observations."""
    missing = _missing_boundaries(request.policy, request.start, request.end, request.evaluated_at)
    if "authoritative schedule interval" in missing or "authoritative schedule timezone" in missing:
        return _proof(
            _TerminalCadenceProofContent(
                request,
                ExperimentResult.BLOCKED,
                {},
                {},
                (),
                ("valid authoritative schedule interval/timezone",),
            )
        )
    if request.provenance is not EvidenceProvenance.FRESH_REAL:
        missing.append("fresh-real durable provenance")
    missing.extend(_terminal_authority_boundaries(request.observations))
    if missing:
        return _proof(
            _TerminalCadenceProofContent(
                request,
                ExperimentResult.BLOCKED,
                _incomplete_terminal_metrics(request.observations, request.start, request.end),
                {},
                (),
                tuple(missing),
            )
        )
    try:
        reduction = reduce_terminal_cadence(
            request.observations,
            request.policy,
            start=request.start,
            end=request.end,
            evaluated_at=request.evaluated_at,
        )
    except (ValueError, ZoneInfoNotFoundError):
        return _proof(
            _TerminalCadenceProofContent(
                request,
                ExperimentResult.BLOCKED,
                {},
                {},
                (),
                ("valid authoritative schedule interval/timezone",),
            )
        )
    if reduction.unsupported_states:
        return _proof(
            _TerminalCadenceProofContent(
                request,
                ExperimentResult.BLOCKED,
                _metrics(reduction),
                {},
                (),
                tuple(
                    f"unsupported durable terminal state:{state}"
                    for state in reduction.unsupported_states
                ),
            )
        )
    assertions = {
        "terminal_inventory_reconciles": reduction.counts.observed
        == reduction.counts.succeeded + reduction.counts.failed + reduction.counts.no_data,
        "authoritative_schedule_policy_present": bool(request.policy.policy_identity),
        "authoritative_schedule_interval_present": request.policy.interval > timedelta(0),
        "schedule_slots_are_unique": not reduction.duplicate_slots,
        "observations_match_authoritative_slots": not reduction.unexpected_slots,
        "cadence_has_no_missed_slots": reduction.counts.missed_cadence == 0,
        "freshness_is_within_interval": not reduction.is_stale,
    }
    result = ExperimentResult.PROVEN if all(assertions.values()) else ExperimentResult.FAILED
    return _proof(
        _TerminalCadenceProofContent(
            request,
            result,
            _metrics(reduction),
            assertions,
            tuple(
                row.durable_id
                for row in request.observations
                if request.start < row.source_time <= request.end
                and isinstance(row.durable_id, str)
            ),
            (),
        )
    )


def _expected_slots(
    policy: TerminalSchedulePolicy, *, start: datetime, evaluated_at: datetime
) -> frozenset[datetime]:
    if policy.interval <= timedelta(0):
        raise ValueError("schedule interval must be positive")
    ZoneInfo(policy.timezone)
    if any(value.tzinfo is None for value in (policy.anchor_time, start, evaluated_at)):
        raise ValueError("schedule times must be timezone-aware")
    slots: set[datetime] = set()
    slot = policy.anchor_time
    while slot <= start:
        slot += policy.interval
    while slot <= evaluated_at:
        slots.add(slot)
        slot += policy.interval
    return frozenset(slots)


def _missing_boundaries(
    policy: TerminalSchedulePolicy, start: datetime, end: datetime, evaluated_at: datetime
) -> list[str]:
    missing: list[str] = []
    if not isinstance(policy.policy_identity, str) or not policy.policy_identity:
        missing.append("authoritative schedule policy identity")
    if not isinstance(policy.interval, timedelta) or policy.interval <= timedelta(0):
        missing.append("authoritative schedule interval")
    if not isinstance(policy.timezone, str) or not policy.timezone:
        missing.append("authoritative schedule timezone")
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or evaluated_at.tzinfo is None
        or not start < end
        or evaluated_at < end
    ):
        missing.append("ordered timezone-aware source/evaluation boundary")
    return missing


def _terminal_authority_boundaries(
    observations: tuple[TerminalObservation, ...],
) -> list[str]:
    missing_terminal_authority = any(
        not isinstance(row.terminal_state, str) or not row.terminal_state for row in observations
    )
    missing_identity_authority = any(
        not isinstance(row.durable_id, str) or not row.durable_id for row in observations
    )
    boundaries: list[str] = []
    if missing_terminal_authority:
        boundaries.append("durable terminal state authority")
    if missing_identity_authority:
        boundaries.append("durable terminal identity authority")
    return boundaries


def _incomplete_terminal_metrics(
    observations: tuple[TerminalObservation, ...], start: datetime, end: datetime
) -> dict[str, int | bool | str | None]:
    accepted = tuple(row for row in observations if start < row.source_time <= end)
    supported = frozenset(state.value for state in DurableTerminalState)
    return {
        "observed_count": len(accepted),
        "unclassified_terminal_state_count": sum(
            row.terminal_state not in supported for row in accepted
        ),
    }


def _metrics(reduction: TerminalCadenceReduction) -> dict[str, int | bool | str | None]:
    return {
        "succeeded_count": reduction.counts.succeeded,
        "failed_count": reduction.counts.failed,
        "no_data_count": reduction.counts.no_data,
        "observed_count": reduction.counts.observed,
        "expected_slot_count": reduction.counts.expected_slots,
        "missed_cadence_count": reduction.counts.missed_cadence,
        "freshness_seconds": reduction.freshness_seconds,
        "stale_boundary": reduction.stale_boundary.isoformat(),
        "is_stale": reduction.is_stale,
    }


def _proof(content: _TerminalCadenceProofContent) -> PipelineExperimentProof:
    assertions = content.assertions or {"required_boundaries_present": False}
    return PipelineExperimentProof(
        PipelineExperimentId.TERMINAL_CADENCE,
        content.request.run_id,
        content.result,
        content.request.provenance,
        content.request.source_boundary,
        content.metrics,
        assertions,
        content.evidence_ids,
        content.blocked_boundaries,
        "Read-only terminal cadence inventory",
    )


def _required_count(row: Mapping[str, object], name: str) -> int:
    value = row.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _required_text(row: Mapping[str, object], name: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _required_timezone(row: Mapping[str, object], name: str) -> str:
    value = _required_text(row, name)
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"{name} must be an IANA timezone") from error
    return value


def _required_timestamp_text(row: Mapping[str, object], name: str) -> str:
    value = _required_text(row, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO timezone-aware timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be an ISO timezone-aware timestamp")
    return value


def _ordered_aware_window(start: datetime, end: datetime) -> bool:
    return (
        start.tzinfo is not None
        and end.tzinfo is not None
        and start.utcoffset() is not None
        and end.utcoffset() is not None
        and start < end
    )
