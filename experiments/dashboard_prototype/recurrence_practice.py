"""Pure fail-closed E-Recurrence-2 successful-practice reducer."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)
from experiments.dashboard_prototype.recurrence_common import (
    SUPPORTED_SURFACES,
    RecurrenceExperimentId,
    RecurrenceExperimentProof,
)

_LONDON = ZoneInfo("Europe/London")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TOKEN = re.compile(r"[a-z][a-z0-9_.:-]{0,127}\Z", re.ASCII)
SEQUENCE_FINGERPRINT_VERSION = "v1"
M14_FIRST_OCCURRENCES = 3
M14_FIRST_TASKS = 2
M14_FIRST_DAYS = 2
M14_SECOND_OCCURRENCES = 5
M14_SECOND_TASKS = 3


class PracticeTerminalOutcome(StrEnum):
    """Explicit authoritative terminal outcomes for a practice attempt."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class PracticeOperation:
    """One ordered privacy-safe operation, bound to a canonical task."""

    producer: str
    surface: str
    attempt_id: str
    canonical_task_id: str
    source_time: datetime
    source_order: int
    operation_fingerprint: str
    target_fingerprint: str
    task_class: str
    project_fingerprint: str
    model: str
    enforcement_owner_state: str
    enforcement_owner_version: int
    operation_authoritative: bool
    task_class_authoritative: bool
    registry_authoritative: bool


@dataclass(frozen=True, slots=True)
class PracticeTerminal:
    """The sole terminal authority for one operation attempt."""

    producer: str
    surface: str
    attempt_id: str
    source_time: datetime
    outcome: PracticeTerminalOutcome | None
    terminal_authoritative: bool


@dataclass(frozen=True, slots=True)
class PracticeBoundary:
    """Exact seven-day M14 operation and terminal source population."""

    source_start: datetime
    source_end: datetime
    operations: Iterable[PracticeOperation]
    terminals: Iterable[PracticeTerminal]


@dataclass(frozen=True, slots=True)
class PracticeCandidate:
    """One explicit successful ordered sequence occurrence."""

    producer: str
    surface: str
    attempt_id: str
    canonical_task_id: str
    source_time: datetime
    task_class: str
    project_fingerprint: str
    model: str
    enforcement_owner_state: str
    enforcement_owner_version: int
    sequence_fingerprint_version: str
    sequence_fingerprint: str


@dataclass(frozen=True, slots=True)
class PracticeSequence:
    """Per-sequence M14 recurrence and comparable-task percentages."""

    sequence_fingerprint: str
    producer: str
    project_fingerprint: str
    model: str
    task_class: str
    successful_occurrence_count: int
    successful_task_count: int
    london_day_count: int
    support_percentage: float | None
    success_percentage: float | None
    m14_threshold_met: bool
    registry_owned: bool


@dataclass(frozen=True, slots=True)
class PracticeReduction:
    """Population-conserving practice and sequence accounting."""

    candidates: tuple[PracticeCandidate, ...]
    sequences: tuple[PracticeSequence, ...]
    comparable_task_count: int
    successful_task_count: int
    candidate_count: int
    unknown_terminal_count: int


def reduce_successful_practice(*, boundary: PracticeBoundary) -> PracticeReduction:
    """Reduce complete ordered operation lineages without inferring terminals."""
    _validate_window(boundary.source_start, boundary.source_end)
    operations = _index_operations(boundary)
    terminals = _index_terminals(boundary, operations)
    successes: list[PracticeCandidate] = []
    comparable_tasks: set[tuple[str, str, str, str, str, str]] = set()
    successful_tasks: set[tuple[str, str, str, str, str, str]] = set()
    terminal_sequence_tasks: dict[tuple[str, str, str, str, str], set[tuple[str, str, str]]] = {}
    unknown_terminal_count = 0
    for key, ordered in sorted(operations.items()):
        terminal = terminals.get(key)
        if not _operation_authority(ordered):
            continue
        task_key = (
            ordered[0].producer,
            ordered[0].surface,
            ordered[0].canonical_task_id,
            ordered[0].project_fingerprint,
            ordered[0].model,
            ordered[0].task_class,
        )
        if terminal is None or not terminal.terminal_authoritative or terminal.outcome is None:
            unknown_terminal_count += 1
            continue
        comparable_tasks.add(task_key)
        sequence_key = (
            _sequence_fingerprint(ordered),
            ordered[0].producer,
            ordered[0].project_fingerprint,
            ordered[0].model,
            ordered[0].task_class,
        )
        terminal_sequence_tasks.setdefault(sequence_key, set()).add(task_key[:3])
        if terminal.outcome is PracticeTerminalOutcome.SUCCESS:
            successful_tasks.add(task_key)
            successes.append(_candidate(ordered, terminal))
    sequences = _sequence_reductions(
        successes, comparable_tasks, successful_tasks, terminal_sequence_tasks
    )
    eligible = {
        (
            row.sequence_fingerprint,
            row.producer,
            row.project_fingerprint,
            row.model,
            row.task_class,
        )
        for row in sequences
        if row.m14_threshold_met and not row.registry_owned
    }
    candidates = tuple(
        row
        for row in successes
        if (
            row.sequence_fingerprint,
            row.producer,
            row.project_fingerprint,
            row.model,
            row.task_class,
        )
        in eligible
    )
    return PracticeReduction(
        candidates=candidates,
        sequences=sequences,
        comparable_task_count=len(comparable_tasks),
        successful_task_count=len(successful_tasks),
        candidate_count=len(candidates),
        unknown_terminal_count=unknown_terminal_count,
    )


def build_practice_proof(
    *, run_id: str, source_boundary: str, provenance: EvidenceProvenance, boundary: PracticeBoundary
) -> RecurrenceExperimentProof:
    """Build a local blocked proof; a remote runner alone may establish Proven."""
    reduction = reduce_successful_practice(boundary=boundary)
    return RecurrenceExperimentProof(
        experiment_id=RecurrenceExperimentId.SUCCESSFUL_PRACTICE,
        run_id=run_id,
        result=ExperimentResult.BLOCKED,
        provenance=provenance,
        source_start=boundary.source_start,
        source_end=boundary.source_end,
        source_boundary=source_boundary,
        metrics={
            "comparable_task_count": reduction.comparable_task_count,
            "successful_task_count": reduction.successful_task_count,
            "candidate_count": reduction.candidate_count,
            "unknown_terminal_count": reduction.unknown_terminal_count,
        },
        assertions={"remote_calculation_reconciled": False},
        evidence_ids=(),
        blocked_boundaries=("missing_durable_authority",),
        proposal="Await runner-owned remote equality before proving successful practice.",
    )


def _sequence_reductions(
    successes: list[PracticeCandidate],
    comparable_tasks: set[tuple[str, str, str, str, str, str]],
    successful_tasks: set[tuple[str, str, str, str, str, str]],
    terminal_sequence_tasks: dict[tuple[str, str, str, str, str], set[tuple[str, str, str]]],
) -> tuple[PracticeSequence, ...]:
    grouped: dict[tuple[str, str, str, str, str], list[PracticeCandidate]] = {}
    for row in successes:
        grouped.setdefault(
            (
                row.sequence_fingerprint,
                row.producer,
                row.project_fingerprint,
                row.model,
                row.task_class,
            ),
            [],
        ).append(row)
    return tuple(
        _sequence(
            fingerprint,
            rows,
            comparable_tasks,
            successful_tasks,
            terminal_sequence_tasks[(fingerprint, producer, project, model, task_class)],
        )
        for (fingerprint, producer, project, model, task_class), rows in sorted(grouped.items())
    )


def _sequence(
    fingerprint: str,
    rows: list[PracticeCandidate],
    comparable_tasks: set[tuple[str, str, str, str, str, str]],
    successful_tasks: set[tuple[str, str, str, str, str, str]],
    terminal_tasks: set[tuple[str, str, str]],
) -> PracticeSequence:
    population = (
        rows[0].producer,
        rows[0].project_fingerprint,
        rows[0].model,
        rows[0].task_class,
    )
    population_successful_tasks = {
        task for task in successful_tasks if (task[0], task[3], task[4], task[5]) == population
    }
    task_ids = {(row.producer, row.surface, row.canonical_task_id) for row in rows}
    days = {row.source_time.astimezone(_LONDON).date() for row in rows}
    occurrences = len(rows)
    task_count = len(task_ids)
    threshold = (
        occurrences >= M14_FIRST_OCCURRENCES
        and task_count >= M14_FIRST_TASKS
        and len(days) >= M14_FIRST_DAYS
    ) or (occurrences >= M14_SECOND_OCCURRENCES and task_count >= M14_SECOND_TASKS)
    owner_states = {(row.enforcement_owner_state, row.enforcement_owner_version) for row in rows}
    first = rows[0]
    return PracticeSequence(
        sequence_fingerprint=fingerprint,
        producer=first.producer,
        project_fingerprint=first.project_fingerprint,
        model=first.model,
        task_class=first.task_class,
        successful_occurrence_count=occurrences,
        successful_task_count=task_count,
        london_day_count=len(days),
        support_percentage=_percentage(task_count, len(population_successful_tasks)),
        success_percentage=_percentage(task_count, len(terminal_tasks)),
        m14_threshold_met=threshold,
        registry_owned=owner_states != {("unowned", 1)},
    )


def _index_operations(
    boundary: PracticeBoundary,
) -> dict[tuple[str, str, str], tuple[PracticeOperation, ...]]:
    grouped: dict[tuple[str, str, str], list[PracticeOperation]] = {}
    for operation in boundary.operations:
        _validate_operation(operation, boundary)
        grouped.setdefault(
            (operation.producer, operation.surface, operation.attempt_id), []
        ).append(operation)
    result: dict[tuple[str, str, str], tuple[PracticeOperation, ...]] = {}
    for key, items in grouped.items():
        ordered = tuple(sorted(items, key=lambda item: (item.source_time, item.source_order)))
        if [item.source_order for item in ordered] != list(range(len(ordered))):
            raise PrototypeContractError("practice operation order must be contiguous and complete")
        result[key] = ordered
    return result


def _index_terminals(
    boundary: PracticeBoundary,
    operations: dict[tuple[str, str, str], tuple[PracticeOperation, ...]],
) -> dict[tuple[str, str, str], PracticeTerminal]:
    terminals: dict[tuple[str, str, str], PracticeTerminal] = {}
    for terminal in boundary.terminals:
        _validate_terminal(terminal, boundary)
        key = (terminal.producer, terminal.surface, terminal.attempt_id)
        if key not in operations or key in terminals:
            raise PrototypeContractError(
                "practice terminal must have one matching operation lineage"
            )
        if terminal.source_time <= operations[key][-1].source_time:
            raise PrototypeContractError("practice terminal must follow ordered operations")
        terminals[key] = terminal
    return terminals


def _candidate(
    operations: tuple[PracticeOperation, ...], terminal: PracticeTerminal
) -> PracticeCandidate:
    if terminal.outcome is not PracticeTerminalOutcome.SUCCESS:
        raise PrototypeContractError("successful practice candidate requires explicit success")
    first = operations[0]
    return PracticeCandidate(
        first.producer,
        first.surface,
        first.attempt_id,
        first.canonical_task_id,
        terminal.source_time,
        first.task_class,
        first.project_fingerprint,
        first.model,
        first.enforcement_owner_state,
        first.enforcement_owner_version,
        SEQUENCE_FINGERPRINT_VERSION,
        _sequence_fingerprint(operations),
    )


def _sequence_fingerprint(operations: tuple[PracticeOperation, ...]) -> str:
    payload = "|".join(
        f"{row.operation_fingerprint}:{row.target_fingerprint}" for row in operations
    )
    return hashlib.sha256(f"{SEQUENCE_FINGERPRINT_VERSION}|{payload}".encode()).hexdigest()


def _operation_authority(operations: tuple[PracticeOperation, ...]) -> bool:
    first = operations[0]
    return all(
        row.operation_authoritative
        and row.task_class_authoritative
        and row.registry_authoritative
        and row.canonical_task_id == first.canonical_task_id
        and row.task_class == first.task_class
        and row.enforcement_owner_state == first.enforcement_owner_state
        and row.enforcement_owner_version == first.enforcement_owner_version
        for row in operations
    )


def _validate_window(start: datetime, end: datetime) -> None:
    if not _aware(start) or not _aware(end):
        raise PrototypeContractError("practice boundary must be timezone-aware")
    local_start = start.astimezone(_LONDON)
    local_end = end.astimezone(_LONDON)
    if (
        start.astimezone(UTC) >= end.astimezone(UTC)
        or local_start.timetz().replace(tzinfo=None) != datetime.min.time()
        or local_end.timetz().replace(tzinfo=None) != datetime.min.time()
        or local_end.date() - local_start.date() != timedelta(days=7)
    ):
        raise PrototypeContractError("practice boundary must span seven London local-midnight days")


def _validate_operation(row: PracticeOperation, boundary: PracticeBoundary) -> None:
    if (row.producer, row.surface) not in SUPPORTED_SURFACES:
        raise PrototypeContractError("practice producer/surface is unsupported")
    if (
        not _aware(row.source_time)
        or not boundary.source_start < row.source_time <= boundary.source_end
    ):
        raise PrototypeContractError("practice operation falls outside source boundary")
    if (
        isinstance(row.source_order, bool)
        or not isinstance(row.source_order, int)
        or row.source_order < 0
    ):
        raise PrototypeContractError("practice source order must be nonnegative")
    for value in (
        row.attempt_id,
        row.canonical_task_id,
        row.task_class,
        row.model,
        row.enforcement_owner_state,
    ):
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise PrototypeContractError("practice identity and taxonomy must be privacy-safe")
    if not all(
        _SHA256.fullmatch(value)
        for value in (
            row.operation_fingerprint,
            row.target_fingerprint,
            row.project_fingerprint,
        )
    ):
        raise PrototypeContractError(
            "practice operation, target, and project must be SHA-256 fingerprints"
        )
    if (
        isinstance(row.enforcement_owner_version, bool)
        or not isinstance(row.enforcement_owner_version, int)
        or row.enforcement_owner_version < 1
    ):
        raise PrototypeContractError("practice enforcement owner version must be positive")


def _validate_terminal(row: PracticeTerminal, boundary: PracticeBoundary) -> None:
    if (row.producer, row.surface) not in SUPPORTED_SURFACES or not _aware(row.source_time):
        raise PrototypeContractError("practice terminal authority is unsupported")
    if not boundary.source_start < row.source_time <= boundary.source_end:
        raise PrototypeContractError("practice terminal falls outside source boundary")
    if row.outcome is not None and not isinstance(row.outcome, PracticeTerminalOutcome):
        raise PrototypeContractError("practice terminal outcome must be explicit or absent")


def _aware(value: datetime) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _percentage(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator * 100.0 / denominator
