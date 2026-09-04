"""Typed, fail-closed contracts for dashboard prototype proof experiments."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import cast

SCHEMA_VERSION = 1
EXPERIMENT_NAMESPACE = "agent-introspection.dashboard-prototype.v1"
CLEANUP_RUN_ID_SELECTOR = "evidence_bundle.run_id"
SUPPORTED_PRODUCERS = frozenset({"omp", "codex-cli", "codex-app-server"})
CANONICAL_ROW_IDS = frozenset(f"A{number:02d}" for number in range(1, 44))
CANONICAL_ROW_FIELDS = frozenset(
    {
        "route",
        "control",
        "fields_required",
        "calculation_used",
        "fields_exist",
        "required_change",
        "high_level_gap_closure",
    }
)
REQUIRED_EXPERIMENT_REGISTRY = (
    *(f"E-Pipeline-{number}" for number in range(1, 7)),
    *(f"E-Attribution-{number}" for number in range(1, 6)),
    *(f"E-Request-{number}" for number in range(1, 4)),
    *(f"E-Task-{number}" for number in range(0, 5)),
    *(f"E-Recurrence-{number}" for number in range(0, 5)),
)
REQUIRED_MATRIX_KEYS = frozenset(
    {
        "schema_version",
        "experiment_namespace",
        "cleanup_boundary",
        "privacy_allowlist",
        "supported_producers",
        "unsupported_boundaries",
        "experiment_registry",
        "rows",
    }
)
REQUIRED_ROW_KEYS = CANONICAL_ROW_FIELDS | frozenset(
    {
        "row_id",
        "producer",
        "surface",
        "normative_capability_reason",
        "current_field_audit",
        "experiment_id",
        "reducer",
        "proposed_event_family",
        "projection_boundary",
        "selected_range_operator",
        "interval_containment_operator",
        "evaluation_time_policy",
        "evaluation_policy_id",
        "all_version_requirement",
        "version_policy_id",
        "blocked_boundary",
        "remote_query",
        "oracle",
        "result",
        "evidence_bundle",
    }
)
BLOCKED_BOUNDARY_KEYS = frozenset({"missing_boundary", "owner", "owner_surface", "experiment_id"})
BLOCKED_BOUNDARY_OWNERS = frozenset({"producer", "application", "provider"})
CANONICAL_SCHEMA_TYPE_DESCRIPTORS = frozenset({"string", "integer", "number", "boolean", "null"})
EVENT_ID_INPUT_KEYS = frozenset(
    {"experiment_id", "producer", "native_session_id", "event_id_ordinal"}
)
RAW_FIELD_INVENTORY_KEYS = frozenset({"name", "type", "presence", "cardinality", "owner"})
REQUIRED_ADVERSE_CASES = frozenset(
    {
        "endpoint",
        "negative",
        "missing",
        "conflicting",
        "duplicate",
        "out-of-order",
        "version",
        "clock-skew",
    }
)
AUTHORITATIVE_JOIN_SOURCES = frozenset(
    {"native_identity_tuple", "native_session_id", "stable_identity", "event_id", "remote_event_id"}
)
UNSUPPORTED_BOUNDARIES = (
    {
        "producer": "omp",
        "boundary": "Mid-thread workspace change is not exposed.",
        "result": "Not applicable",
    },
    {
        "producer": "codex-cli",
        "boundary": "Notify exposes neither end nor workspace change.",
        "result": "Not applicable",
    },
    {
        "producer": "codex-app-server",
        "boundary": (
            "Mid-thread workspace change is unsupported; resume, clear, compact, "
            "concurrent-project, non-Git, and activity-producing telemetry remain "
            "unproven pending E-Attribution-2."
        ),
        "result": "Blocked",
    },
    {
        "producer": "claude-code",
        "boundary": (
            "Retained hook/session/OTEL identity mismatch; excluded from "
            "supported-producer denominators."
        ),
        "result": "Blocked",
    },
    {
        "producer": "codex-app",
        "boundary": (
            "Unsupported separate identity; Codex Desktop is represented by canonical "
            "producer codex-app-server."
        ),
        "result": "Not applicable",
    },
)
PROHIBITED_PRIVACY_FIELD_NAMES = frozenset(
    {
        "api_key",
        "command_output",
        "credential",
        "credentials",
        "password",
        "prompt",
        "response",
        "secret",
        "secrets",
        "token",
        "transcript",
    }
)
PROHIBITED_PRIVACY_FIELD_NAME_PATTERNS = (
    re.compile(r"^(?:prompt|response)_(?:body|content|message|text)s?$"),
    re.compile(r"^(?:[a-z0-9]+_)?payload$"),
    re.compile(r"^(?:access|api|auth|bearer|refresh|session)_token$"),
    re.compile(r"^token_(?:credential|secret|value)$"),
)
PROHIBITED_SERIALIZED_VALUE_PATTERNS = (
    re.compile(
        r"(?i)\b(?:api[_ -]?key|password|secret|bearer\s+\S+|access[_ -]?token|"
        r"refresh[_ -]?token)\b"
    ),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]+\b"),
)

SQL_STATEMENT_DESCRIPTION_PATTERN = re.compile(
    r"""(?isx)
    \bselect\b .* \bfrom\b
    |
    \b(?:insert\s+into|update\s+\S+\s+set|delete\s+from|merge\s+into)\b
    |
    \b(?:create|alter|drop|truncate)\s+
    (?:table|view|index|schema|database)\b
    """
)

# Versioned digest of rows ordered by (row_id, producer), retaining only Appendix's seven
# canonical fields.
CANONICAL_APPENDIX_V1_SHA256 = "8dc859867939fd206ec7b57e26807f4edbc9c977b94ceaa01939a71ff44fb1c3"
# Versioned digest of the matrix's immutable temporal contract fields.
CANONICAL_TEMPORAL_CONTRACT_V1_SHA256 = (
    "4a1c2dc346fc86dbc0c0f50e6dfd2d20930aa65b7fa04cce3b5d55316c4a52fb"
)


class PrototypeContractError(ValueError):
    """A prototype contract is incomplete, unsafe, or internally inconsistent."""


class ExperimentResult(StrEnum):
    PROVEN = "Proven"
    NOT_APPLICABLE = "Not applicable"
    BLOCKED = "Blocked"
    FAILED = "Failed"


class EvidenceProvenance(StrEnum):
    FRESH_REAL = "fresh-real"
    RETAINED = "retained"
    SYNTHETIC = "synthetic"


class AppendixQueryId(StrEnum):
    POPULATION_COUNT = "population-count-v1"
    ACCEPTED_COUNT = "accepted-count-v1"
    REJECTED_COUNT = "rejected-count-v1"
    DUPLICATE_COUNT = "duplicate-count-v1"
    OUTPUT_COUNT = "output-count-v1"


@dataclass(frozen=True, slots=True)
class JoinIdentityStep:
    source: str
    identity_field: str

    def __post_init__(self) -> None:
        if self.source not in AUTHORITATIVE_JOIN_SOURCES or not _is_nonempty_string(
            self.identity_field
        ):
            raise PrototypeContractError("join chain must use authoritative identity steps")


class AppendixQueryParameter(StrEnum):
    START_EPOCH = "start_epoch"
    END_EPOCH = "end_epoch"
    EXPECTED_COUNT = "expected_count"
    WINDOW_SECONDS = "window_seconds"


class CalculationResultState(StrEnum):
    COMPUTED = "computed"
    NO_DATA = "no-data"
    QUERY_FAILED = "query-failed"


type QueryParameterValue = int | float | bool | None
type CalculationValue = int | float | bool | None


@dataclass(frozen=True, slots=True)
class AppendixQueryReference:
    row_id: str
    query_id: AppendixQueryId
    bound_parameters: Mapping[AppendixQueryParameter, QueryParameterValue]

    def __post_init__(self) -> None:
        if self.row_id not in CANONICAL_ROW_IDS:
            raise PrototypeContractError("Appendix query row ID must be canonical")
        if not isinstance(self.query_id, AppendixQueryId):
            raise PrototypeContractError("Appendix query ID must be allowlisted")
        _validate_bound_parameters(self.bound_parameters)


@dataclass(frozen=True, slots=True)
class CalculationResult:
    state: CalculationResultState
    values: Mapping[str, CalculationValue]

    def __post_init__(self) -> None:
        if not isinstance(self.state, CalculationResultState):
            raise PrototypeContractError("calculation result must use an explicit state")
        _validate_typed_parameter_mapping(self.values, "calculation result values")
        if self.state is CalculationResultState.COMPUTED and not self.values:
            raise PrototypeContractError("computed calculation result requires values")
        if self.state is not CalculationResultState.COMPUTED and self.values:
            raise PrototypeContractError("non-computed calculation result cannot carry values")
        object.__setattr__(self, "values", _freeze(self.values))


@dataclass(frozen=True, slots=True)
class ProofMatrixRow:
    row_id: str
    producer: str
    values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProofMatrix:
    schema_version: int
    experiment_namespace: str
    cleanup_boundary: Mapping[str, str]
    privacy_allowlist: frozenset[str]
    supported_producers: frozenset[str]
    unsupported_boundaries: tuple[object, ...]
    experiment_registry: tuple[str, ...]
    rows: tuple[ProofMatrixRow, ...]


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """Immutable retained evidence for one bounded prototype experiment."""

    experiment_namespace: str
    run_id: str
    experiment_id: str
    hypothesis: str
    producer: str
    native_session_id: str
    surface: str
    installed_version: str
    capability_state: str
    provenance: EvidenceProvenance
    start_time: str
    end_time: str
    source_extraction_boundary: str
    selected_range_membership_operator: str
    interval_containment_operator: str
    evaluation_time: str
    policy_identity: str
    version_policy_identity: str
    all_version_requirement: str
    privacy_allowlist: frozenset[str]
    raw_field_inventory: Sequence[Mapping[str, object]]
    native_identity_tuple: tuple[str, str]
    join_chain: Sequence[JoinIdentityStep]
    canonical_schema: Mapping[str, object]
    event_id_inputs: Sequence[Mapping[str, object]]
    input_count: int
    accepted_count: int
    rejected_count_by_reason: Mapping[str, int]
    duplicate_count: int
    output_count: int
    conservation_equation: str
    local_outbox_event_ids: Sequence[str]
    remote_event_ids: Sequence[str]
    appendix_calculation_query: AppendixQueryReference
    remote_query_reference_id: str
    remote_result: CalculationResult
    oracle_reference_id: str
    oracle_result: CalculationResult
    edge_cases: Sequence[str]
    privacy_review: str
    result: ExperimentResult
    recommendation: str
    unresolved_dependency: str | None

    def __post_init__(self) -> None:
        normalized_allowlist = _normalize_privacy_allowlist(self.privacy_allowlist)
        object.__setattr__(self, "privacy_allowlist", normalized_allowlist)
        _validate_evidence_strings(self)
        _validate_evidence_identity(self)
        _validate_allowlisted_inventory(self.raw_field_inventory, normalized_allowlist)
        if not isinstance(self.appendix_calculation_query, AppendixQueryReference):
            raise PrototypeContractError(
                "Appendix calculation query must be an allowlisted reference"
            )
        if not isinstance(self.remote_result, CalculationResult) or not isinstance(
            self.oracle_result, CalculationResult
        ):
            raise PrototypeContractError("remote and oracle results must be structured")
        _validate_provenance(self)
        _validate_evidence_result(self.result)
        _validate_adverse_cases(self)
        _validate_population_accounting(self)
        _validate_event_id_equality(self)
        _reject_prohibited_serialized_content(self)
        for field in self.__dataclass_fields__:
            object.__setattr__(self, field, _freeze(getattr(self, field)))

    def canonical_json(self) -> str:
        """Serialize this bundle deterministically for immutable evidence retention."""
        return json.dumps(
            _canonicalize(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def load_proof_matrix(path: Path) -> ProofMatrix:
    """Load and validate a schema-version-1 proof matrix from JSON."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrototypeContractError(f"cannot load proof matrix: {path}") from exc
    if not isinstance(payload, Mapping):
        raise PrototypeContractError("proof matrix root must be an object")
    return validate_proof_matrix(payload)


def serialize_evidence_bundle(bundle: EvidenceBundle) -> str:
    return bundle.canonical_json()


def hash_evidence_bundle(bundle: EvidenceBundle) -> str:
    return bundle.content_hash()


def validate_proof_matrix(payload: Mapping[str, object]) -> ProofMatrix:
    _require_exact_keys(payload, REQUIRED_MATRIX_KEYS, "proof matrix")
    _validate_matrix_metadata(payload)
    experiment_registry = _validate_experiment_registry(payload["experiment_registry"])
    rows_payload = payload["rows"]
    if not isinstance(rows_payload, list):
        raise PrototypeContractError("proof matrix rows must be an array")
    normalized_allowlist = _normalize_privacy_allowlist(payload["privacy_allowlist"])
    rows, row_producers = _validate_rows(
        rows_payload, normalized_allowlist, frozenset(experiment_registry)
    )
    _validate_reference_prerequisites(rows, frozenset(experiment_registry))
    if set(row_producers) != CANONICAL_ROW_IDS or any(
        producers != SUPPORTED_PRODUCERS for producers in row_producers.values()
    ):
        raise PrototypeContractError(
            "every canonical row ID must cover every supported producer exactly once"
        )
    _validate_canonical_appendix(rows)
    _validate_canonical_temporal_contract(rows)
    unsupported = payload["unsupported_boundaries"]
    if not isinstance(unsupported, list) or tuple(unsupported) != UNSUPPORTED_BOUNDARIES:
        raise PrototypeContractError("unsupported boundaries must match the checked-in declaration")
    return ProofMatrix(
        SCHEMA_VERSION,
        EXPERIMENT_NAMESPACE,
        dict(cast(Mapping[str, str], payload["cleanup_boundary"])),
        normalized_allowlist,
        frozenset(cast(list[str], payload["supported_producers"])),
        tuple(unsupported),
        experiment_registry,
        tuple(rows),
    )


def _validate_matrix_metadata(payload: Mapping[str, object]) -> None:
    if payload["schema_version"] != SCHEMA_VERSION:
        raise PrototypeContractError("unsupported proof matrix schema version")
    if payload["experiment_namespace"] != EXPERIMENT_NAMESPACE:
        raise PrototypeContractError("proof matrix must use the exact non-production namespace")
    cleanup = payload["cleanup_boundary"]
    if (
        not isinstance(cleanup, Mapping)
        or set(cleanup) != {"namespace", "run_id"}
        or cleanup.get("namespace") != EXPERIMENT_NAMESPACE
        or cleanup.get("run_id") != CLEANUP_RUN_ID_SELECTOR
    ):
        raise PrototypeContractError(
            "cleanup boundary must match exact namespace and evidence_bundle.run_id"
        )
    allowlist = payload["privacy_allowlist"]
    if not isinstance(allowlist, list):
        raise PrototypeContractError("privacy allowlist must be an array")
    _normalize_privacy_allowlist(allowlist)
    producers = payload["supported_producers"]
    if (
        not isinstance(producers, list)
        or frozenset(producers) != SUPPORTED_PRODUCERS
        or len(producers) != len(SUPPORTED_PRODUCERS)
    ):
        raise PrototypeContractError(
            "supported producers must be exactly the canonical producer set"
        )


def _validate_experiment_registry(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or tuple(value) != REQUIRED_EXPERIMENT_REGISTRY:
        raise PrototypeContractError(
            "experiment registry must exactly match the canonical proof experiments"
        )
    return tuple(value)


def _validate_blocked_boundary(value: object, row: Mapping[str, object]) -> None:
    if not isinstance(value, Mapping) or set(value) != BLOCKED_BOUNDARY_KEYS:
        raise PrototypeContractError("Blocked rows require an exact structured boundary")
    if (
        not _is_nonempty_string(value["missing_boundary"])
        or value["owner"] not in BLOCKED_BOUNDARY_OWNERS
        or not _is_nonempty_string(value["owner_surface"])
        or value["experiment_id"] != row["experiment_id"]
    ):
        raise PrototypeContractError("Blocked boundary must bind owner and decisive experiment")


def _validate_rows(
    rows_payload: Sequence[object],
    matrix_allowlist: frozenset[str],
    experiment_registry: frozenset[str],
) -> tuple[list[ProofMatrixRow], dict[str, set[str]]]:
    rows: list[ProofMatrixRow] = []
    seen: set[tuple[str, str]] = set()
    row_producers: dict[str, set[str]] = {}
    for raw_row in rows_payload:
        if not isinstance(raw_row, Mapping):
            raise PrototypeContractError("proof matrix row must be an object")
        _require_exact_keys(raw_row, REQUIRED_ROW_KEYS, "proof matrix row")
        row_id, producer = raw_row["row_id"], raw_row["producer"]
        if row_id not in CANONICAL_ROW_IDS or producer not in SUPPORTED_PRODUCERS:
            raise PrototypeContractError("row ID and producer must be canonical")
        _validate_row_result(raw_row, matrix_allowlist, experiment_registry)
        identity = (row_id, producer)
        if identity in seen:
            raise PrototypeContractError("duplicate row ID and producer")
        seen.add(identity)
        row_producers.setdefault(row_id, set()).add(producer)
        rows.append(
            ProofMatrixRow(
                row_id=row_id,
                producer=producer,
                values=cast(Mapping[str, object], _freeze(dict(raw_row))),
            )
        )
    return rows, row_producers


def _validate_canonical_appendix(rows: Sequence[ProofMatrixRow]) -> None:
    appendix = [
        {
            "row_id": row.row_id,
            "producer": row.producer,
            **{field: row.values[field] for field in sorted(CANONICAL_ROW_FIELDS)},
        }
        for row in sorted(rows, key=lambda row: (row.row_id, row.producer))
    ]
    digest = hashlib.sha256(
        json.dumps(
            appendix,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if digest != CANONICAL_APPENDIX_V1_SHA256:
        raise PrototypeContractError("Appendix seven-field content does not match canonical v1")


def _validate_canonical_temporal_contract(rows: Sequence[ProofMatrixRow]) -> None:
    temporal_contract = [
        {
            "row_id": row.row_id,
            "producer": row.producer,
            **{
                field: row.values[field]
                for field in (
                    "selected_range_operator",
                    "interval_containment_operator",
                    "evaluation_time_policy",
                    "all_version_requirement",
                )
            },
        }
        for row in sorted(rows, key=lambda row: (row.row_id, row.producer))
    ]
    digest = hashlib.sha256(
        json.dumps(
            temporal_contract,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if digest != CANONICAL_TEMPORAL_CONTRACT_V1_SHA256:
        raise PrototypeContractError("temporal contract content does not match canonical v1")


def _validate_row_result(
    row: Mapping[str, object],
    matrix_allowlist: frozenset[str],
    experiment_registry: frozenset[str],
) -> None:
    result = row["result"]
    if not _is_nonempty_string(result) or result not in {state.value for state in ExperimentResult}:
        raise PrototypeContractError("row result is invalid")
    _validate_matrix_reference(row, "remote_query")
    _validate_matrix_reference(row, "oracle")
    _validate_row_policy_and_boundary(row, result)
    experiment_id = row["experiment_id"]
    if not _is_nonempty_string(experiment_id) or experiment_id not in experiment_registry:
        raise PrototypeContractError("row experiment ID must name a registry experiment")
    if result == ExperimentResult.NOT_APPLICABLE.value:
        if row["evidence_bundle"] is not None:
            raise PrototypeContractError("Not applicable rows cannot make a proof claim")
        return
    if result == ExperimentResult.BLOCKED.value and row["evidence_bundle"] is None:
        return
    evidence = _deserialize_matrix_evidence(row["evidence_bundle"])
    expected_result = ExperimentResult(result)
    if evidence.result is not expected_result:
        raise PrototypeContractError("matrix evidence result must equal row result")
    if (
        evidence.experiment_id != experiment_id
        or evidence.producer != row["producer"]
        or evidence.native_identity_tuple != (row["producer"], evidence.native_session_id)
    ):
        raise PrototypeContractError("matrix evidence must match the decisive row coordinates")
    _validate_row_evidence_bindings(row, evidence, matrix_allowlist)


def _validate_row_policy_and_boundary(row: Mapping[str, object], result: str) -> None:
    row_id = cast(str, row["row_id"])
    if (
        row["evaluation_policy_id"] != f"{row_id}.evaluation-policy-v1"
        or row["version_policy_id"] != f"{row_id}.version-policy-v1"
    ):
        raise PrototypeContractError("row policy IDs must be deterministic")
    if not _is_nonempty_string(row["normative_capability_reason"]):
        if result == ExperimentResult.BLOCKED.value:
            raise PrototypeContractError(
                "Blocked rows require a decisive experiment and missing boundary"
            )
        raise PrototypeContractError(f"{result} rows require a normative reason")
    if result == ExperimentResult.BLOCKED.value:
        _validate_blocked_boundary(row["blocked_boundary"], row)
    elif row["blocked_boundary"] is not None:
        raise PrototypeContractError("non-Blocked rows require a null blocked boundary")


def _validate_matrix_reference(row: Mapping[str, object], kind: str) -> None:
    reference = row[kind]
    if not isinstance(reference, Mapping):
        raise PrototypeContractError(f"{kind} must be a typed reference object")
    _require_exact_keys(
        reference,
        frozenset(
            {
                "reference_id",
                "experiment_id",
                "prerequisite_experiment_ids",
                "description",
            }
        ),
        kind,
    )
    row_id, producer, experiment_id = row["row_id"], row["producer"], row["experiment_id"]
    reference_kind = {"remote_query": "remote-query", "oracle": "oracle"}[kind]
    if reference["reference_id"] != f"{row_id}.{producer}.{reference_kind}":
        raise PrototypeContractError(f"{kind} reference ID must be deterministic")
    if reference["experiment_id"] != experiment_id:
        raise PrototypeContractError(f"{kind} must name the decisive experiment")
    prerequisites = reference["prerequisite_experiment_ids"]
    if (
        not isinstance(prerequisites, list)
        or len(prerequisites) != len(set(prerequisites))
        or any(not _is_nonempty_string(item) or item == experiment_id for item in prerequisites)
    ):
        raise PrototypeContractError(f"{kind} prerequisites must be unique non-self experiment IDs")
    description = reference["description"]
    if not _is_nonempty_string(description) or SQL_STATEMENT_DESCRIPTION_PATTERN.search(
        description
    ):
        raise PrototypeContractError(f"{kind} description must be declarative and SQL-free")


def _validate_reference_prerequisites(
    rows: Sequence[ProofMatrixRow], experiment_registry: frozenset[str]
) -> None:
    for row in rows:
        for kind in ("remote_query", "oracle"):
            prerequisites = cast(Mapping[str, object], row.values[kind])[
                "prerequisite_experiment_ids"
            ]
            if any(item not in experiment_registry for item in cast(Sequence[str], prerequisites)):
                raise PrototypeContractError(f"{kind} prerequisite must name a registry experiment")


def _deserialize_matrix_evidence(value: object) -> EvidenceBundle:
    if not isinstance(value, Mapping):
        raise PrototypeContractError("evidence rows require a structured evidence bundle")
    fields = frozenset(EvidenceBundle.__dataclass_fields__)
    _require_exact_keys(value, fields, "matrix evidence bundle")
    try:
        query = value["appendix_calculation_query"]
        remote = value["remote_result"]
        oracle = value["oracle_result"]
        chain = value["join_chain"]
        if not all(isinstance(item, Mapping) for item in (query, remote, oracle)):
            raise TypeError
        if not isinstance(chain, (list, tuple)):
            raise TypeError
        return EvidenceBundle(
            **{
                **value,
                "provenance": EvidenceProvenance(value["provenance"]),
                "result": ExperimentResult(value["result"]),
                "privacy_allowlist": _normalize_privacy_allowlist(value["privacy_allowlist"]),
                "appendix_calculation_query": AppendixQueryReference(
                    row_id=query["row_id"],
                    query_id=AppendixQueryId(query["query_id"]),
                    bound_parameters={
                        AppendixQueryParameter(key): item
                        for key, item in query["bound_parameters"].items()
                    },
                ),
                "remote_result": CalculationResult(
                    state=CalculationResultState(remote["state"]), values=remote["values"]
                ),
                "oracle_result": CalculationResult(
                    state=CalculationResultState(oracle["state"]), values=oracle["values"]
                ),
                "native_identity_tuple": tuple(value["native_identity_tuple"]),
                "join_chain": tuple(
                    JoinIdentityStep(item["source"], item["identity_field"]) for item in chain
                ),
            }
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise PrototypeContractError(
            "matrix evidence bundle is not a valid structured bundle"
        ) from exc


def _validate_row_evidence_bindings(
    row: Mapping[str, object],
    evidence: EvidenceBundle,
    matrix_allowlist: frozenset[str],
) -> None:
    if not evidence.privacy_allowlist <= matrix_allowlist:
        raise PrototypeContractError("evidence privacy allowlist must be a matrix allowlist subset")
    if evidence.appendix_calculation_query.row_id != row["row_id"]:
        raise PrototypeContractError("Appendix query row ID must bind to the matrix row")
    remote_reference = cast(Mapping[str, object], row["remote_query"])
    oracle_reference = cast(Mapping[str, object], row["oracle"])
    if (
        evidence.remote_query_reference_id != remote_reference["reference_id"]
        or evidence.oracle_reference_id != oracle_reference["reference_id"]
    ):
        raise PrototypeContractError("evidence references must bind to matrix references")
    if (
        evidence.selected_range_membership_operator != row["selected_range_operator"]
        or evidence.interval_containment_operator != row["interval_containment_operator"]
    ):
        raise PrototypeContractError("evidence time operators must bind to the matrix Time range")
    if (
        evidence.policy_identity != row["evaluation_policy_id"]
        or evidence.version_policy_identity != row["version_policy_id"]
        or evidence.all_version_requirement != row["all_version_requirement"]
    ):
        raise PrototypeContractError(
            "evidence policies must bind to row policy IDs and requirements"
        )


def _validate_evidence_strings(bundle: EvidenceBundle) -> None:
    values = (
        bundle.experiment_namespace,
        bundle.run_id,
        bundle.experiment_id,
        bundle.hypothesis,
        bundle.native_session_id,
        bundle.surface,
        bundle.installed_version,
        bundle.capability_state,
        bundle.start_time,
        bundle.end_time,
        bundle.source_extraction_boundary,
        bundle.selected_range_membership_operator,
        bundle.interval_containment_operator,
        bundle.evaluation_time,
        bundle.policy_identity,
        bundle.version_policy_identity,
        bundle.all_version_requirement,
        bundle.conservation_equation,
        bundle.remote_query_reference_id,
        bundle.oracle_reference_id,
        bundle.privacy_review,
        bundle.recommendation,
    )
    if not all(_is_nonempty_string(value) for value in values):
        raise PrototypeContractError("evidence standard requires nonempty strings")


def _validate_evidence_identity(bundle: EvidenceBundle) -> None:
    if bundle.experiment_namespace != EXPERIMENT_NAMESPACE:
        raise PrototypeContractError("evidence must use the exact experiment namespace")
    if not _is_exact_run_id(bundle.run_id):
        raise PrototypeContractError("evidence must use an exact bounded run ID")
    if bundle.producer not in SUPPORTED_PRODUCERS:
        raise PrototypeContractError(f"unsupported evidence producer: {bundle.producer}")
    if not isinstance(bundle.native_identity_tuple, tuple) or bundle.native_identity_tuple != (
        bundle.producer,
        bundle.native_session_id,
    ):
        raise PrototypeContractError(
            "native identity must exactly equal producer and native session ID"
        )
    if (
        not isinstance(bundle.join_chain, (list, tuple))
        or not bundle.join_chain
        or any(not isinstance(step, JoinIdentityStep) for step in bundle.join_chain)
        or bundle.join_chain[0]
        != JoinIdentityStep("native_identity_tuple", "producer,native_session_id")
    ):
        raise PrototypeContractError("join chain must anchor the native identity tuple first")
    _validate_canonical_schema(bundle.canonical_schema, bundle.privacy_allowlist)
    _validate_event_id_inputs(bundle)
    start_time = _parse_timestamp(bundle.start_time)
    end_time = _parse_timestamp(bundle.end_time)
    evaluation_time = _parse_timestamp(bundle.evaluation_time)
    if start_time >= end_time or evaluation_time < end_time:
        raise PrototypeContractError("evidence timestamps must be ordered bounded instants")


def _validate_provenance(bundle: EvidenceBundle) -> None:
    if not isinstance(bundle.provenance, EvidenceProvenance):
        raise PrototypeContractError("evidence provenance must be explicit")
    if bundle.result is ExperimentResult.PROVEN:
        if bundle.provenance is not EvidenceProvenance.FRESH_REAL:
            raise PrototypeContractError("Proven evidence requires fresh-real provenance")
        if (
            bundle.remote_result.state is not CalculationResultState.COMPUTED
            or bundle.oracle_result.state is not CalculationResultState.COMPUTED
        ):
            raise PrototypeContractError(
                "Proven evidence requires computed remote and oracle results"
            )
        if not _calculation_results_match_exactly(bundle.remote_result, bundle.oracle_result):
            raise PrototypeContractError(
                "Proven evidence requires exact remote and oracle equality"
            )


def _validate_adverse_cases(bundle: EvidenceBundle) -> None:
    cases = (
        frozenset(bundle.edge_cases)
        if isinstance(bundle.edge_cases, (list, tuple, frozenset, set))
        else frozenset()
    )
    if len(cases) != len(bundle.edge_cases) or not cases <= REQUIRED_ADVERSE_CASES:
        raise PrototypeContractError("adverse cases must be an allowlisted semantic set")
    if bundle.result is ExperimentResult.PROVEN and cases != REQUIRED_ADVERSE_CASES:
        raise PrototypeContractError("Proven evidence requires the full adverse-case set")


def _validate_evidence_result(result: object) -> None:
    if not isinstance(result, ExperimentResult):
        raise PrototypeContractError("evidence result must be an allowed result state")


def _validate_population_accounting(bundle: EvidenceBundle) -> None:
    counts = (
        bundle.input_count,
        bundle.accepted_count,
        bundle.duplicate_count,
        bundle.output_count,
    )
    if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts):
        raise PrototypeContractError("population counts must be nonnegative integers")
    if not isinstance(bundle.rejected_count_by_reason, Mapping) or any(
        not _is_nonempty_string(reason)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for reason, count in bundle.rejected_count_by_reason.items()
    ):
        raise PrototypeContractError("rejected counts must be keyed by nonnegative integer reasons")
    rejected = sum(bundle.rejected_count_by_reason.values())
    if bundle.input_count != bundle.accepted_count + rejected + bundle.duplicate_count:
        raise PrototypeContractError("input must equal accepted + rejected + duplicates")
    if bundle.output_count != bundle.accepted_count:
        raise PrototypeContractError("output must equal accepted")
    expected = _conservation_equation(
        bundle.input_count,
        bundle.accepted_count,
        rejected,
        bundle.duplicate_count,
        bundle.output_count,
    )
    if bundle.conservation_equation != expected:
        raise PrototypeContractError("conservation equation does not match population counts")


def _validate_event_id_equality(bundle: EvidenceBundle) -> None:
    if not isinstance(bundle.local_outbox_event_ids, (list, tuple)) or not isinstance(
        bundle.remote_event_ids, (list, tuple)
    ):
        raise PrototypeContractError("event IDs must be ordered sequences")
    local = tuple(bundle.local_outbox_event_ids)
    remote = tuple(bundle.remote_event_ids)
    if not _is_ordered_strings(local) or not _is_ordered_strings(remote):
        raise PrototypeContractError("event IDs must be ordered strings")
    if local != remote:
        raise PrototypeContractError("local outbox and remote event IDs must match exactly")
    if len(local) != bundle.output_count or len(remote) != bundle.output_count:
        raise PrototypeContractError("event ID populations must equal output count")
    if len(set(local)) != len(local):
        raise PrototypeContractError("event IDs must be unique")
    derived = tuple(derive_event_id(inputs) for inputs in bundle.event_id_inputs)
    if local != derived:
        raise PrototypeContractError("local event IDs must equal their derived input hashes")


def _validate_allowlisted_inventory(
    inventory: Sequence[Mapping[str, object]],
    privacy_allowlist: frozenset[str],
) -> None:
    if not privacy_allowlist or any(
        not _is_nonempty_string(name) or _is_prohibited_privacy_field_name(name)
        for name in privacy_allowlist
    ):
        raise PrototypeContractError("evidence privacy allowlist must be bound and safe")
    for field in inventory:
        if not isinstance(field, Mapping) or set(field) != RAW_FIELD_INVENTORY_KEYS:
            raise PrototypeContractError("raw field inventory objects must use the exact schema")
        if not _is_nonempty_string(field["name"]) or field["name"] not in privacy_allowlist:
            raise PrototypeContractError("raw field inventory field must be privacy allowlisted")
        if any(
            not _is_nonempty_string(field[key])
            for key in ("type", "presence", "cardinality", "owner")
        ):
            raise PrototypeContractError("raw field inventory metadata must be nonempty strings")


def _validate_canonical_schema(schema: object, privacy_allowlist: frozenset[str]) -> None:
    if (
        not isinstance(schema, Mapping)
        or not schema
        or any(
            not isinstance(name, str)
            or name not in privacy_allowlist
            or descriptor not in CANONICAL_SCHEMA_TYPE_DESCRIPTORS
            for name, descriptor in schema.items()
        )
    ):
        raise PrototypeContractError("canonical schema must use only allowlisted typed fields")


def derive_event_id(inputs: Mapping[str, object]) -> str:
    """Derive an output event ID from its canonical identity input."""
    return hashlib.sha256(
        json.dumps(
            _canonicalize(inputs),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def _validate_event_id_inputs(bundle: EvidenceBundle) -> None:
    inputs = bundle.event_id_inputs
    if not isinstance(inputs, (list, tuple)) or len(inputs) != bundle.output_count:
        raise PrototypeContractError("event ID inputs must conserve the output population")
    for ordinal, event_input in enumerate(inputs):
        if not isinstance(event_input, Mapping) or set(event_input) != EVENT_ID_INPUT_KEYS:
            raise PrototypeContractError("event ID inputs must use the exact schema")
        if (
            event_input["experiment_id"] != bundle.experiment_id
            or event_input["producer"] != bundle.producer
            or event_input["native_session_id"] != bundle.native_session_id
            or not isinstance(event_input["event_id_ordinal"], int)
            or isinstance(event_input["event_id_ordinal"], bool)
            or event_input["event_id_ordinal"] != ordinal
        ):
            raise PrototypeContractError(
                "event ID inputs must bind bundle identity and exact ordinal"
            )


def _calculation_results_match_exactly(
    remote: CalculationResult, oracle: CalculationResult
) -> bool:
    return (
        remote.state is oracle.state
        and remote.values.keys() == oracle.values.keys()
        and all(
            type(remote.values[key]) is type(oracle.values[key])
            and remote.values[key] == oracle.values[key]
            for key in remote.values
        )
    )


def _validate_typed_parameter_mapping(
    value: Mapping[str, QueryParameterValue],
    label: str,
) -> None:
    if not isinstance(value, Mapping) or any(
        not _is_nonempty_string(key) or not _is_scalar(item) for key, item in value.items()
    ):
        raise PrototypeContractError(
            f"{label} must contain only named numeric, boolean, or null values"
        )


def _validate_bound_parameters(
    value: Mapping[AppendixQueryParameter, QueryParameterValue],
) -> None:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, AppendixQueryParameter) or not _is_scalar(item)
        for key, item in value.items()
    ):
        raise PrototypeContractError(
            "Appendix query parameters must contain only named numeric, boolean, or null values"
        )


def _is_scalar(value: object) -> bool:
    return (
        value is None
        or isinstance(value, bool)
        or (
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
    )


def _reject_prohibited_serialized_content(value: object) -> None:
    if isinstance(
        value, (EvidenceBundle, AppendixQueryReference, CalculationResult, JoinIdentityStep)
    ):
        for field in value.__dataclass_fields__:
            _reject_prohibited_serialized_content(getattr(value, field))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if _is_prohibited_privacy_field_name(key):
                raise PrototypeContractError("serialized evidence contains prohibited content")
            _reject_prohibited_serialized_content(item)
    elif isinstance(value, (list, tuple, frozenset, set)):
        for item in value:
            _reject_prohibited_serialized_content(item)
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in PROHIBITED_SERIALIZED_VALUE_PATTERNS
    ):
        raise PrototypeContractError("serialized evidence contains prohibited content")


def _conservation_equation(
    input_count: int,
    accepted_count: int,
    rejected_count: int,
    duplicate_count: int,
    output_count: int,
) -> str:
    return (
        f"{input_count} = {accepted_count} + {rejected_count} + {duplicate_count}; "
        f"{output_count} = {accepted_count}"
    )


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PrototypeContractError("evidence timestamps must be RFC 3339 instants") from exc
    if parsed.tzinfo is None:
        raise PrototypeContractError("evidence timestamps must include a timezone")
    return parsed


def _require_exact_keys(value: Mapping[str, object], required: frozenset[str], label: str) -> None:
    if set(value) != required:
        raise PrototypeContractError(f"{label} keys do not match the schema")


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _is_exact_run_id(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value))


def _is_ordered_nonempty_strings(value: Sequence[object]) -> bool:
    return (
        isinstance(value, (list, tuple))
        and bool(value)
        and all(_is_nonempty_string(item) for item in value)
    )


def _is_ordered_strings(value: Sequence[object]) -> bool:
    return isinstance(value, (list, tuple)) and all(_is_nonempty_string(item) for item in value)


def _normalize_privacy_name(value: str) -> str:
    words = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", words).lower().replace("-", "_")


def _normalize_privacy_allowlist(value: object) -> frozenset[str]:
    if not isinstance(value, (list, tuple, frozenset, set)):
        raise PrototypeContractError("privacy allowlist must be an unordered collection")
    if any(not isinstance(name, str) for name in value):
        raise PrototypeContractError("privacy allowlist must contain names")
    normalized = frozenset(_normalize_privacy_name(name) for name in value)
    if not normalized or any(_is_prohibited_privacy_field_name(name) for name in normalized):
        raise PrototypeContractError("privacy allowlist must be unique, nonempty, and safe")
    return normalized


def _is_prohibited_privacy_field_name(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = _normalize_privacy_name(value)
    return normalized in PROHIBITED_PRIVACY_FIELD_NAMES or any(
        pattern.fullmatch(normalized) for pattern in PROHIBITED_PRIVACY_FIELD_NAME_PATTERNS
    )


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise PrototypeContractError("retained mappings must use string keys")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (frozenset, set)):
        return frozenset(_freeze(item) for item in value)
    return value


def _canonicalize(value: object) -> object:
    if isinstance(
        value, (EvidenceBundle, AppendixQueryReference, CalculationResult, JoinIdentityStep)
    ):
        return {field: _canonicalize(getattr(value, field)) for field in value.__dataclass_fields__}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (frozenset, set)):
        return sorted((_canonicalize(item) for item in value), key=_canonical_sort_key)
    return value


def _canonical_sort_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
