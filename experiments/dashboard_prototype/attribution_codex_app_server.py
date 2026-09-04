"""Pure E-Attribution-2 reducer for canonical Codex app-server identities."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
)
from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    PrototypeContractError,
)

PRODUCER = "codex-app-server"
SCENARIOS = ("startup", "resume", "clear", "compact")


class LifecycleKind(StrEnum):
    START = "session_start"
    END = "session_end"


@dataclass(frozen=True, slots=True)
class LifecycleObservation:
    event_id: str
    timestamp: datetime
    producer: str
    native_session_id: str
    kind: LifecycleKind
    project_id: str
    project_kind: str
    scenario: str
    hook_native_id_equal: bool


@dataclass(frozen=True, slots=True)
class SourceObservation:
    event_id: str
    timestamp: datetime
    producer: str
    native_session_id: str
    activity: str
    project_id: str = ""
    project_kind: str = ""


@dataclass(frozen=True, slots=True)
class AttributionSelection:
    scenario: str
    lifecycle_id_hash: str
    source_id_hash: str | None
    source_time: datetime | None
    accepted: bool
    gates: tuple[tuple[str, bool], ...] = ()


@dataclass(frozen=True, slots=True)
class AttributionReduction:
    selections: tuple[AttributionSelection, ...]
    blocked_boundaries: tuple[str, ...]
    failed_boundaries: tuple[str, ...]
    assertions: dict[str, bool]


def reduce_codex_app_server(
    lifecycle: Iterable[LifecycleObservation],
    sources: Iterable[SourceObservation],
    scenario_authority: frozenset[str] = frozenset(SCENARIOS),
) -> AttributionReduction:
    """Join only exact canonical tuples and account for every named scenario."""
    lifecycle_rows = tuple(lifecycle)
    source_rows = tuple(sources)
    _validate_rows(lifecycle_rows, source_rows)
    selections: list[AttributionSelection] = []
    blocked: list[str] = []
    failed: list[str] = []
    assertions = {"separate_codex_app_excluded": True}
    for scenario in SCENARIOS:
        if scenario not in scenario_authority:
            selection = AttributionSelection(
                scenario,
                _hash(scenario),
                None,
                None,
                False,
                _blocked_gates(),
            )
            selections.append(selection)
            _record_gates(assertions, scenario, selection)
            assertions[f"{scenario}_authoritative"] = False
            blocked.append(f"{scenario}-scenario-source-authority")
            continue
        rows = [row for row in lifecycle_rows if row.scenario == scenario]
        selection, reason, contradictory = _reduce_scenario(scenario, rows, source_rows)
        selections.append(selection)
        _record_gates(assertions, scenario, selection)
        assertions[f"{scenario}_authoritative"] = selection.accepted
        if contradictory:
            assert reason is not None
            failed.append(reason)
        elif reason is not None:
            blocked.append(reason)
    return AttributionReduction(tuple(selections), tuple(blocked), tuple(failed), assertions)


def build_codex_app_server_proof(
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    reduction: AttributionReduction,
) -> AttributionExperimentProof:
    """Build a privacy-safe proof; incomplete scenario authority is always Blocked."""
    if reduction.failed_boundaries:
        result = ExperimentResult.FAILED
    elif reduction.blocked_boundaries:
        result = ExperimentResult.BLOCKED
    else:
        result = ExperimentResult.PROVEN
    evidence_ids = tuple(
        evidence_id
        for row in reduction.selections
        if row.accepted
        for evidence_id in (row.lifecycle_id_hash, row.source_id_hash)
        if evidence_id is not None
    )
    return AttributionExperimentProof(
        AttributionExperimentId.CODEX_APP_SERVER,
        run_id,
        result,
        provenance,
        source_boundary,
        {"scenario_count": len(SCENARIOS), "accepted_count": len(evidence_ids)},
        reduction.assertions,
        evidence_ids,
        reduction.blocked_boundaries,
        "Canonical codex-app-server attribution requires exact lifecycle and source tuples.",
    )


def _reduce_scenario(
    scenario: str, rows: list[LifecycleObservation], sources: tuple[SourceObservation, ...]
) -> tuple[AttributionSelection, str | None, bool]:
    starts = [row for row in rows if row.kind is LifecycleKind.START]
    ends = [row for row in rows if row.kind is LifecycleKind.END]
    start = starts[0] if len(starts) == 1 else None
    end = ends[0] if len(ends) == 1 else None
    lifecycle_pair = start is not None and end is not None and start.timestamp <= end.timestamp
    native_identity = (
        lifecycle_pair
        and start is not None
        and end is not None
        and start.native_session_id == end.native_session_id
        and start.hook_native_id_equal
        and end.hook_native_id_equal
    )
    stable_project = (
        lifecycle_pair
        and start is not None
        and end is not None
        and (start.project_id, start.project_kind) == (end.project_id, end.project_kind)
    )
    canonical_project = (
        stable_project
        and start is not None
        and bool(start.project_id)
        and start.project_kind in {"git", "non_git"}
    )
    exact_candidates = [
        row
        for row in sources
        if start is not None
        and end is not None
        and row.producer == PRODUCER
        and row.native_session_id == start.native_session_id
        and start.timestamp < row.timestamp <= end.timestamp
    ]
    directional_source = bool(exact_candidates)
    concurrent_isolation = (
        stable_project
        and start is not None
        and all(
            (row.project_id, row.project_kind) == (start.project_id, start.project_kind)
            for row in exact_candidates
        )
    )
    canonical = [
        row
        for row in exact_candidates
        if row.activity not in {"session_task.turn", "startup_prewarm"}
    ]
    canonical_activity = bool(canonical) and concurrent_isolation
    gates = (
        ("lifecycle", lifecycle_pair),
        ("native_identity", native_identity),
        ("stable_project", stable_project),
        ("directional_source", directional_source),
        ("concurrent_project_isolation", concurrent_isolation),
        ("canonical_project_classification", canonical_project),
        ("canonical_activity", canonical_activity),
    )
    accepted = all(value for _, value in gates)
    selected = (
        sorted(canonical, key=lambda row: (row.timestamp, row.event_id))[-1] if accepted else None
    )
    lifecycle_id = _hash(start.event_id) if start is not None else _hash(scenario)
    selection = AttributionSelection(
        scenario,
        lifecycle_id,
        _hash(selected.event_id) if selected is not None else None,
        selected.timestamp if selected is not None else None,
        accepted,
        gates,
    )
    boundary = next(
        (
            (reason, contradictory)
            for failed, reason, contradictory in (
                (not lifecycle_pair, f"{scenario}_lifecycle", len(rows) > 2),
                (not native_identity, f"{scenario}_native_session_id", True),
                (not stable_project, f"{scenario}_stable_project", True),
                (not canonical_project, f"{scenario}_project_classification", False),
                (not directional_source, f"{scenario}_directional_source", False),
                (
                    not concurrent_isolation,
                    f"{scenario}_concurrent_project_isolation",
                    True,
                ),
                (not canonical_activity, f"{scenario}_canonical_activity", False),
            )
            if failed
        ),
        None,
    )
    if boundary is None:
        return selection, None, False
    return selection, boundary[0], boundary[1]


def _blocked_gates() -> tuple[tuple[str, bool], ...]:
    return tuple(
        (name, False)
        for name in (
            "lifecycle",
            "native_identity",
            "stable_project",
            "directional_source",
            "concurrent_project_isolation",
            "canonical_project_classification",
            "canonical_activity",
        )
    )


def _record_gates(
    assertions: dict[str, bool], scenario: str, selection: AttributionSelection
) -> None:
    assertions.update({f"{scenario}_{name}": accepted for name, accepted in selection.gates})


def _validate_rows(
    lifecycle: tuple[LifecycleObservation, ...], sources: tuple[SourceObservation, ...]
) -> None:
    for lifecycle_row in lifecycle:
        if (
            lifecycle_row.producer != PRODUCER
            or lifecycle_row.scenario not in SCENARIOS
            or not lifecycle_row.event_id
            or not lifecycle_row.native_session_id
            or not _aware(lifecycle_row.timestamp)
        ):
            raise PrototypeContractError("noncanonical app-server lifecycle observation")
    for source_row in sources:
        if (
            source_row.producer != PRODUCER
            or not source_row.event_id
            or not source_row.native_session_id
            or not source_row.activity
            or not _aware(source_row.timestamp)
        ):
            raise PrototypeContractError("noncanonical app-server source observation")


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
