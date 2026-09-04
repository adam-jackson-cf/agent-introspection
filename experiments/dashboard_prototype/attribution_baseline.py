"""Pure E-Attribution-1 reducer for the retained OMP and Codex CLI authority."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from experiments.dashboard_prototype.attribution_common import (
    AttributionExperimentId,
    AttributionExperimentProof,
)
from experiments.dashboard_prototype.contracts import (
    EvidenceProvenance,
    ExperimentResult,
    derive_event_id,
)

_SUPPORTED: Final = frozenset(("omp", "codex-cli"))
_REQUIRED_SCENARIOS: Final = (
    "fresh",
    "resume",
    "end",
    "concurrent_projects",
    "non_git",
    "workspace_change",
)


class CapabilityState(StrEnum):
    PASSED = "passed"
    NOT_EXPOSED = "not_exposed"


@dataclass(frozen=True, slots=True)
class ProducerAuthority:
    """Privacy-safe retained proof for a supported producer's native identity boundary."""

    producer: str
    surface: str
    evidence_id: str
    native_matches_correlation: bool
    project_id: str
    project_name: str
    project_kind: str
    capabilities: Mapping[str, CapabilityState]

    def validate(self) -> None:
        if self.producer not in _SUPPORTED or not self.surface or not self.evidence_id:
            raise ValueError("unsupported or incomplete retained producer authority")
        if not self.native_matches_correlation:
            raise ValueError("native and correlation identities must match exactly")
        if len(self.project_id) != 64 or not self.project_name or self.project_kind != "git":
            raise ValueError("retained project tuple is incomplete")
        if set(self.capabilities) != set(_REQUIRED_SCENARIOS):
            raise ValueError("retained authority must state every scenario capability")


@dataclass(frozen=True, slots=True)
class CanonicalActivityVersion:
    """One immutable-source-time version; identities are already short hashes."""

    activity_id: str
    version: int
    source_time: datetime
    producer: str
    surface: str
    native_session_id: str
    state: str
    project_id: str | None
    reason_code: str | None
    attribution_method: str = "none"

    def validate(self) -> None:
        if not self.activity_id or self.version < 1 or self.source_time.tzinfo is None:
            raise ValueError("activity version lacks immutable identity, version, or source time")
        if self.producer not in _SUPPORTED or not self.surface or not self.native_session_id:
            raise ValueError("activity has an unsupported or incomplete native identity")
        if self.state not in {"attributed", "unresolved"}:
            raise ValueError("activity state must be attributed or unresolved")
        if (self.state == "attributed") != (self.project_id is not None):
            raise ValueError("activity attribution state and project are contradictory")
        if self.state == "unresolved" and not self.reason_code:
            raise ValueError("unresolved activity requires an allowlisted diagnostic")


@dataclass(frozen=True, slots=True)
class AttributionPopulation:
    eligible: int
    attributed: int
    unresolved: int
    distinct_projects: int
    diagnostics: Mapping[str, int]

    @property
    def conserves(self) -> bool:
        return self.eligible == self.attributed + self.unresolved


@dataclass(frozen=True, slots=True)
class AttributionBaselineReduction:
    authorities: tuple[ProducerAuthority, ...]
    latest_activities: tuple[CanonicalActivityVersion, ...]
    p7: AttributionPopulation
    p8: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SourceSessionObservation:
    producer: str
    surface: str
    native_session_id: str
    source_time: datetime


@dataclass(frozen=True, slots=True)
class LifecycleSessionObservation:
    producer: str
    surface: str
    native_session_id: str
    interval_start: datetime
    interval_end: datetime | None
    accepted_event_times: tuple[datetime, ...] = ()


@dataclass(frozen=True, slots=True)
class P5Cohort:
    source_sessions: int
    source_with_lifecycle: int
    lifecycle_sessions: int | None
    lifecycle_with_source: int | None

    @property
    def source_conserves(self) -> bool:
        return 0 <= self.source_with_lifecycle <= self.source_sessions

    @property
    def lifecycle_conserves(self) -> bool:
        return self.lifecycle_sessions is None or (
            self.lifecycle_with_source is not None
            and 0 <= self.lifecycle_with_source <= self.lifecycle_sessions
        )


def reduce_p5_directional_cohorts(
    sources: Sequence[SourceSessionObservation],
    lifecycles: Sequence[LifecycleSessionObservation],
    *,
    start: datetime,
    end: datetime,
) -> Mapping[str, P5Cohort]:
    """Reconcile exact producer/surface/native-session membership in a bounded range."""
    cohorts: dict[str, P5Cohort] = {}
    keys = {(row.producer, row.surface) for row in sources} | {
        (row.producer, row.surface) for row in lifecycles
    }
    for producer, surface in sorted(keys):
        relevant_sources = [
            row
            for row in sources
            if (row.producer, row.surface) == (producer, surface) and start < row.source_time <= end
        ]
        relevant_lifecycles = [
            row
            for row in lifecycles
            if (row.producer, row.surface) == (producer, surface)
            and any(start < event_time <= end for event_time in row.accepted_event_times)
        ]
        containing = {
            source.native_session_id
            for source in relevant_sources
            if any(
                lifecycle.native_session_id == source.native_session_id
                and lifecycle.interval_start <= source.source_time
                and (lifecycle.interval_end is None or source.source_time < lifecycle.interval_end)
                for lifecycle in lifecycles
            )
        }
        source_ids = {row.native_session_id for row in relevant_sources}
        lifecycle_ids = {row.native_session_id for row in relevant_lifecycles}
        lifecycle_with_source = {
            lifecycle.native_session_id
            for lifecycle in relevant_lifecycles
            if any(
                source.native_session_id == lifecycle.native_session_id
                and lifecycle.interval_start <= source.source_time
                and (lifecycle.interval_end is None or source.source_time < lifecycle.interval_end)
                for source in relevant_sources
            )
        }
        cohorts[f"{producer}.{surface}"] = P5Cohort(
            len(source_ids),
            len(containing),
            len(lifecycle_ids),
            len(lifecycle_with_source),
        )
    return cohorts


@dataclass(frozen=True, slots=True)
class AttributionBaselineProofInput:
    authorities: Sequence[ProducerAuthority] | None
    activities: Sequence[CanonicalActivityVersion] | None
    sources: Sequence[SourceSessionObservation] | None
    lifecycles: Sequence[LifecycleSessionObservation] | None
    start: datetime | None
    end: datetime | None


RowScalar = str | int | bool | None
_ROW_IDS: Final = frozenset(("A07", "A08", "A09"))
_ROW_EVENT_STAGES: Final = ("source", "reducer", "delivery")


@dataclass(frozen=True, slots=True)
class AttributionRowEvidenceInput:
    """One independently promotable A07--A09 producer/session evidence candidate."""

    row_id: str
    producer: str
    native_session_id: str
    event_id_inputs: Mapping[str, Mapping[str, object]]
    direct_query_parameters: Mapping[str, RowScalar]
    direct_query_parameter_types: Mapping[str, str]
    direct_result: Mapping[str, RowScalar]
    direct_result_types: Mapping[str, str]
    oracle_parameters: Mapping[str, RowScalar]
    oracle_parameter_types: Mapping[str, str]
    oracle_result: Mapping[str, RowScalar]
    oracle_result_types: Mapping[str, str]

    @property
    def event_ids(self) -> Mapping[str, str]:
        return {stage: derive_event_id(self.event_id_inputs[stage]) for stage in _ROW_EVENT_STAGES}

    def validate(self) -> None:
        if self.row_id not in _ROW_IDS or self.producer not in _SUPPORTED:
            raise ValueError("row evidence must target a supported A07--A09 obligation")
        if not isinstance(self.native_session_id, str) or not self.native_session_id:
            raise ValueError("row evidence requires a native session identity")
        if set(self.event_id_inputs) != set(_ROW_EVENT_STAGES):
            raise ValueError("row evidence requires source, reducer, and delivery inputs")
        for ordinal, stage in enumerate(_ROW_EVENT_STAGES):
            event_input = self.event_id_inputs[stage]
            if set(event_input) != {
                "experiment_id",
                "producer",
                "native_session_id",
                "event_id_ordinal",
            }:
                raise ValueError("row event input must have the deterministic identity keys")
            if (
                event_input["experiment_id"] != AttributionExperimentId.BASELINE.value
                or event_input["producer"] != self.producer
                or event_input["native_session_id"] != self.native_session_id
                or not isinstance(event_input["event_id_ordinal"], int)
                or isinstance(event_input["event_id_ordinal"], bool)
                or event_input["event_id_ordinal"] != ordinal
            ):
                raise ValueError("row event input does not bind its exact lineage")
        _validate_row_scalars(
            self.direct_query_parameters, self.direct_query_parameter_types, "direct query"
        )
        _validate_row_scalars(self.direct_result, self.direct_result_types, "direct result")
        _validate_row_scalars(
            self.oracle_parameters, self.oracle_parameter_types, "oracle parameters"
        )
        _validate_row_scalars(self.oracle_result, self.oracle_result_types, "oracle result")


def _validate_row_scalars(
    values: Mapping[str, RowScalar], declared_types: Mapping[str, str], label: str
) -> None:
    if not values or set(values) != set(declared_types):
        raise ValueError(f"{label} requires values and exact scalar types")
    for key, value in values.items():
        actual = (
            "null"
            if value is None
            else "boolean"
            if isinstance(value, bool)
            else "integer"
            if isinstance(value, int)
            else "string"
            if isinstance(value, str)
            else None
        )
        if actual is None or declared_types[key] != actual:
            raise ValueError(f"{label} scalar type does not match {key}")


def row_event_id_inputs(
    producer: str, native_session_id: str
) -> Mapping[str, Mapping[str, object]]:
    """Return the fixed source/reducer/delivery identities for one row bundle."""
    return {
        stage: {
            "experiment_id": AttributionExperimentId.BASELINE.value,
            "producer": producer,
            "native_session_id": native_session_id,
            "event_id_ordinal": ordinal,
        }
        for ordinal, stage in enumerate(_ROW_EVENT_STAGES)
    }


def build_row_evidence_inputs(
    activities: Sequence[CanonicalActivityVersion] | None,
    sources: Sequence[SourceSessionObservation] | None,
    lifecycles: Sequence[LifecycleSessionObservation] | None,
    *,
    start: datetime,
    end: datetime,
) -> tuple[AttributionRowEvidenceInput, ...]:
    """Build only independently authoritative row candidates; absent inputs yield no rows."""
    rows: list[AttributionRowEvidenceInput] = []
    if sources is not None and lifecycles is not None:
        for source in sources:
            if not start < source.source_time <= end:
                continue
            matched = any(
                lifecycle.producer == source.producer
                and lifecycle.surface == source.surface
                and lifecycle.native_session_id == source.native_session_id
                and lifecycle.interval_start <= source.source_time
                and (lifecycle.interval_end is None or source.source_time < lifecycle.interval_end)
                for lifecycle in lifecycles
            )
            if matched:
                rows.append(
                    _row_evidence(
                        "A07",
                        source.producer,
                        source.native_session_id,
                        {"source_time": source.source_time.isoformat()},
                        {
                            "source_sessions": 1,
                            "source_with_lifecycle": 1,
                            "lifecycle_sessions": 1,
                            "lifecycle_with_source": 1,
                        },
                    )
                )
    if activities is not None:
        for activity in _latest_activities(activities, start, end):
            query = {"source_time": activity.source_time.isoformat()}
            rows.append(
                _row_evidence(
                    "A08",
                    activity.producer,
                    activity.native_session_id,
                    query,
                    {
                        "eligible": 1,
                        "attributed": int(activity.state == "attributed"),
                        "unresolved": int(activity.state == "unresolved"),
                        "distinct_projects": int(activity.project_id is not None),
                    },
                )
            )
            rows.append(
                _row_evidence(
                    "A09",
                    activity.producer,
                    activity.native_session_id,
                    query,
                    {
                        "eligible": 1,
                        "unresolved": int(activity.state == "unresolved"),
                        "diagnostic_count": int(activity.state == "unresolved"),
                        "diagnostic": activity.reason_code,
                    },
                )
            )
    return tuple(rows)


def _row_evidence(
    row_id: str,
    producer: str,
    native_session_id: str,
    query: Mapping[str, RowScalar],
    result: Mapping[str, RowScalar],
) -> AttributionRowEvidenceInput:
    parameters = {
        "producer": producer,
        "native_session_id": native_session_id,
        **query,
    }
    types = {key: _row_scalar_type(value) for key, value in parameters.items()}
    result_types = {key: _row_scalar_type(value) for key, value in result.items()}
    row = AttributionRowEvidenceInput(
        row_id,
        producer,
        native_session_id,
        row_event_id_inputs(producer, native_session_id),
        parameters,
        types,
        dict(result),
        result_types,
        dict(parameters),
        dict(types),
        dict(result),
        dict(result_types),
    )
    row.validate()
    return row


def _row_scalar_type(value: RowScalar) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, str):
        return "string"
    raise ValueError("row values must be scalar")


def _validated_authorities(
    authorities: Sequence[ProducerAuthority],
) -> tuple[ProducerAuthority, ...]:
    by_producer: dict[str, ProducerAuthority] = {}
    for authority in authorities:
        authority.validate()
        if authority.producer in by_producer:
            raise ValueError("retained authority producer identity is not immutable")
        by_producer[authority.producer] = authority
    if set(by_producer) != _SUPPORTED:
        raise ValueError("retained authority must cover exactly OMP and Codex CLI")
    return tuple(sorted(by_producer.values(), key=lambda value: value.producer))


def _latest_activities(
    activities: Sequence[CanonicalActivityVersion], start: datetime, end: datetime
) -> tuple[CanonicalActivityVersion, ...]:
    latest: dict[str, CanonicalActivityVersion] = {}
    for activity in activities:
        activity.validate()
        previous = latest.get(activity.activity_id)
        if start < activity.source_time <= end:
            if previous is not None and previous.source_time != activity.source_time:
                raise ValueError("activity versions must retain one immutable source time")
            if previous is None or activity.version > previous.version:
                latest[activity.activity_id] = activity
    return tuple(sorted(latest.values(), key=lambda row: (row.source_time, row.activity_id)))


def _population(
    selected: Sequence[CanonicalActivityVersion],
) -> AttributionPopulation:
    attributed = sum(row.state == "attributed" for row in selected)
    diagnostics: dict[str, int] = {}
    for row in selected:
        if row.state == "unresolved":
            assert row.reason_code is not None
            diagnostics[row.reason_code] = diagnostics.get(row.reason_code, 0) + 1
    return AttributionPopulation(
        len(selected),
        attributed,
        len(selected) - attributed,
        len({row.project_id for row in selected if row.project_id}),
        diagnostics,
    )


def reduce_attribution_baseline(
    authorities: Sequence[ProducerAuthority],
    activities: Sequence[CanonicalActivityVersion],
    *,
    start: datetime,
    end: datetime,
) -> AttributionBaselineReduction:
    """Select latest valid activity versions in ``start < source_time <= end``."""
    if start >= end:
        raise ValueError("attribution range requires start before end")
    retained = _validated_authorities(authorities)
    selected = _latest_activities(activities, start, end)
    p7 = _population(selected)
    return AttributionBaselineReduction(
        retained,
        selected,
        p7,
        dict(sorted(p7.diagnostics.items())),
    )


def _complete_p5_cohorts(
    cohorts: Mapping[str, P5Cohort],
    authorities: Sequence[ProducerAuthority],
) -> dict[str, P5Cohort]:
    complete = dict(cohorts)
    for authority in authorities:
        complete.setdefault(
            f"{authority.producer}.{authority.surface}",
            P5Cohort(0, 0, 0, 0),
        )
    return complete


def build_attribution_baseline_proof(
    *,
    run_id: str,
    provenance: EvidenceProvenance,
    source_boundary: str,
    proof_input: AttributionBaselineProofInput,
) -> AttributionExperimentProof:
    """Build E-Attribution-1 proof; missing activity authority blocks P7/P8 only."""
    blocked: list[str] = []
    if not proof_input.authorities:
        blocked.append("retained_producer_authority")
    if proof_input.start is None or proof_input.end is None:
        blocked.append("source_time_range")
    assertions = {"fresh_real_provenance": provenance is EvidenceProvenance.FRESH_REAL}
    if blocked:
        return AttributionExperimentProof(
            AttributionExperimentId.BASELINE,
            run_id,
            ExperimentResult.BLOCKED,
            provenance,
            source_boundary,
            {},
            assertions,
            (),
            tuple(blocked),
            "Retain exact supported-producer authority before attribution cutover.",
        )
    assert proof_input.authorities is not None
    assert proof_input.start is not None
    assert proof_input.end is not None
    try:
        reduction = reduce_attribution_baseline(
            proof_input.authorities,
            proof_input.activities or (),
            start=proof_input.start,
            end=proof_input.end,
        )
    except ValueError as error:
        return AttributionExperimentProof(
            AttributionExperimentId.BASELINE,
            run_id,
            ExperimentResult.FAILED,
            provenance,
            source_boundary,
            {},
            {**assertions, "retained_authority_valid": False},
            (),
            (),
            f"Do not cut over: {error}.",
        )
    p5 = (
        reduce_p5_directional_cohorts(
            proof_input.sources or (),
            proof_input.lifecycles or (),
            start=proof_input.start,
            end=proof_input.end,
        )
        if proof_input.sources is not None and proof_input.lifecycles is not None
        else {}
    )
    p5 = _complete_p5_cohorts(p5, reduction.authorities)
    expected_p5_pairs = {
        f"{authority.producer}.{authority.surface}" for authority in reduction.authorities
    }
    assertions.update(
        {
            "retained_authority_valid": True,
            "p5_directional_capabilities_complete": set(p5) == expected_p5_pairs,
            "p5_population_conserves": all(
                cohort.source_conserves and cohort.lifecycle_conserves for cohort in p5.values()
            ),
            "p7_population_conserves": reduction.p7.conserves,
            "p8_uses_p7_population": sum(reduction.p8.values()) == reduction.p7.unresolved,
        }
    )
    metrics: dict[str, str | int | bool | None] = {
        "p7_eligible": reduction.p7.eligible,
        "p7_attributed": reduction.p7.attributed,
        "p7_unresolved": reduction.p7.unresolved,
        "p7_distinct_projects": reduction.p7.distinct_projects,
        "p8_diagnostic_groups": len(reduction.p8),
    }
    for authority in reduction.authorities:
        for scenario, state in sorted(authority.capabilities.items()):
            metrics[f"p5.{authority.producer}.{authority.surface}.{scenario}"] = state.value
    for key, cohort in p5.items():
        metrics[f"p5.{key}.source_sessions"] = cohort.source_sessions
        metrics[f"p5.{key}.source_with_lifecycle"] = cohort.source_with_lifecycle
        metrics[f"p5.{key}.lifecycle_sessions"] = cohort.lifecycle_sessions
        metrics[f"p5.{key}.lifecycle_with_source"] = cohort.lifecycle_with_source
    evidence_ids = tuple(authority.evidence_id for authority in reduction.authorities)
    if proof_input.sources is None or proof_input.lifecycles is None:
        return AttributionExperimentProof(
            AttributionExperimentId.BASELINE,
            run_id,
            ExperimentResult.BLOCKED,
            provenance,
            source_boundary,
            metrics,
            assertions,
            evidence_ids,
            ("current_source_membership",),
            "Retained identity is valid; block P5 until durable source membership exists.",
        )
    if proof_input.activities is None:
        return AttributionExperimentProof(
            AttributionExperimentId.BASELINE,
            run_id,
            ExperimentResult.BLOCKED,
            provenance,
            source_boundary,
            metrics,
            assertions,
            evidence_ids,
            ("current_activity_authority",),
            "Retained P5 is valid; block P7/P8 until current activity authority is available.",
        )
    result = ExperimentResult.PROVEN if all(assertions.values()) else ExperimentResult.FAILED
    return AttributionExperimentProof(
        AttributionExperimentId.BASELINE,
        run_id,
        result,
        provenance,
        source_boundary,
        metrics,
        assertions,
        evidence_ids,
        (),
        (
            "Use exact producer/native-session authority and a common latest-version "
            "population for P7/P8."
        ),
    )
