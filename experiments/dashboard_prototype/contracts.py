"""Typed, fail-closed contracts for dashboard prototype proof experiments."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

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
        "application_evidence",
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
APPLICATION_EVIDENCE_KEYS = frozenset({"schema_version", "bindings"})
APPLICATION_EVIDENCE_BINDING_KEYS = frozenset(
    {
        "widget_id",
        "measure_id",
        "dependency_id",
        "producer",
        "native_identity",
        "calculation",
        "projection",
        "qualification",
    }
)
CENTRAL_APPLICATION_EVIDENCE_BINDING_KEYS = frozenset(
    {"widget_id", "measure_id", "dependency_id", "central_identity", "qualification"}
)
APPLICATION_NATIVE_IDENTITY_KEYS = frozenset({"producer", "native_session_id", "native_event_id"})
CENTRAL_IDENTITY_KEYS = frozenset({"runtime_instance_id", "scan_run_id", "database_identity"})
APPLICATION_CALCULATION_KEYS = frozenset({"state", "query_reference_id", "result_event_id"})
APPLICATION_PROJECTION_KEYS = frozenset({"state", "projection_id", "result_event_id"})
APPLICATION_QUALIFICATION_KEYS = frozenset(
    {
        "provenance",
        "result",
        "experiment_id",
        "deployment_fingerprint",
        "calculation_implementation_id",
        "calculation_implementation_sha256",
        "projection_implementation_id",
        "projection_implementation_sha256",
        "projection_event_witness",
        "raw_evidence",
        "selected_range_operator",
        "interval_containment_operator",
        "evaluation_policy_id",
        "version_policy_id",
        "all_version_requirement",
        "join_chain",
        "source_event_ids",
        "output_event_ids",
        "remote_event_ids",
        "oracle_event_ids",
        "remote_result",
        "oracle_result",
        "adverse_cases",
        "privacy_allowlist",
        "evidence_bundle",
    }
)
CENTRAL_APPLICATION_QUALIFICATION_KEYS = frozenset(
    {
        "provenance",
        "result",
        "deployment_fingerprint",
        "calculation_implementation_id",
        "calculation_implementation_sha256",
        "projection_implementation_id",
        "projection_implementation_sha256",
        "evidence_bundle",
    }
)
BLOCKED_BOUNDARY_OWNERS = frozenset({"producer", "application", "provider"})
CANONICAL_SCHEMA_TYPE_DESCRIPTORS = frozenset({"string", "integer", "number", "boolean", "null"})
CENTRAL_APPLICATION_BINDINGS = frozenset(
    {
        ("p1-snapshot", "P1", "E-Pipeline-1"),
        ("scan-evidence", "P1", "E-Pipeline-1"),
        ("p2-outcomes", "P2", "E-Pipeline-2"),
        ("p2-freshness", "P2", "E-Pipeline-2"),
        ("p3-duration", "P3", "E-Pipeline-1"),
        ("p3-rows", "P3", "E-Pipeline-1"),
        ("p3-throughput", "P3", "E-Pipeline-1"),
        ("p4-source-lag", "P4", "E-Pipeline-3"),
        ("p10-snapshot", "P10", "E-Pipeline-1"),
        ("p10-delivery-detail", "P10", "E-Pipeline-5"),
        ("p11-integrity", "P11", "E-Pipeline-4"),
        ("p12-ledger", "P12", "E-Pipeline-6"),
    }
)
CENTRAL_INTERVAL_CONTAINMENT_OPERATOR = (
    "Not applicable: this row does not use lifecycle containment."
)
CENTRAL_SELECTED_RANGE_OPERATORS = {
    widget: (
        "start < completion <= end"
        if widget == "p10-delivery-detail"
        else "start < timestamp <= end"
    )
    for widget, _, _ in CENTRAL_APPLICATION_BINDINGS
}
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


@dataclass(frozen=True, slots=True)
class CentralEvidenceBundle:
    """Immutable proof of one bounded central scan/report calculation."""

    schema_version: int
    widget_id: str
    measure_id: str
    dependency_id: str
    runtime_instance_id: str
    scan_run_id: str
    database_identity: str
    capture_population: str
    bounded_start_ns: str
    bounded_end_ns: str
    selected_range_operator: str
    interval_containment_operator: str
    evaluation_policy_id: str
    version_policy_id: str
    all_version_requirement: str
    retained_artifacts: Mapping[str, Mapping[str, object]]
    source_input_ids: Sequence[str]
    output_event_ids: Sequence[str]
    remote_event_ids: Sequence[str]
    oracle_event_ids: Sequence[str]
    source_output_lineage: Sequence[Mapping[str, str]]
    projection_event_witnesses: Sequence[Mapping[str, object]]
    displayed_calculation: Mapping[str, object]
    remote_calculation: Mapping[str, object]
    oracle_calculation: Mapping[str, object]
    privacy_allowlist: Sequence[str]
    raw_field_inventory: Sequence[Mapping[str, object]]
    adverse_case_artifacts: Mapping[str, str]
    deployment_fingerprint: str
    calculation_implementation_id: str
    calculation_implementation_sha256: str
    projection_implementation_id: str
    projection_implementation_sha256: str
    unresolved_dependencies: Sequence[str]

    def __post_init__(self) -> None:
        _validate_central_evidence_bundle(self)
        for field in self.__dataclass_fields__:
            object.__setattr__(self, field, _freeze(getattr(self, field)))

    def canonical_json(self) -> str:
        return json.dumps(
            _canonicalize(self), sort_keys=True, separators=(",", ":"), ensure_ascii=False
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
    _validate_application_evidence(
        payload["application_evidence"], rows, frozenset(experiment_registry)
    )
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


def _validate_central_application_binding(
    binding: Mapping[str, object],
    rows: Sequence[ProofMatrixRow],
    seen: set[tuple[str, str, str, str]],
) -> None:
    if set(binding) != CENTRAL_APPLICATION_EVIDENCE_BINDING_KEYS:
        raise PrototypeContractError(
            "central application evidence binding must use the exact schema"
        )
    widget_id, measure_id, dependency_id = (
        binding["widget_id"],
        binding["measure_id"],
        binding["dependency_id"],
    )
    if not all(_is_nonempty_string(item) for item in (widget_id, measure_id, dependency_id)):
        raise PrototypeContractError("central application evidence binding identity is invalid")
    central_identity = binding["central_identity"]
    if (
        not isinstance(central_identity, Mapping)
        or set(central_identity) != CENTRAL_IDENTITY_KEYS
        or not _is_nonempty_string(central_identity["runtime_instance_id"])
        or not _is_nonempty_string(central_identity["scan_run_id"])
        or not isinstance(central_identity["database_identity"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", central_identity["database_identity"])
    ):
        raise PrototypeContractError(
            "central application evidence requires authoritative identities"
        )
    identity = (cast(str, widget_id), cast(str, measure_id), cast(str, dependency_id), "central")
    if identity in seen:
        raise PrototypeContractError("application evidence bindings must be unique")
    seen.add(identity)
    qualification = binding["qualification"]
    if (
        not isinstance(qualification, Mapping)
        or set(qualification) != CENTRAL_APPLICATION_QUALIFICATION_KEYS
        or qualification["provenance"] != "fresh-real"
        or qualification["result"] != "Proven"
    ):
        raise PrototypeContractError("central qualification requires fresh-real Proven evidence")
    if (widget_id, measure_id, dependency_id) not in CENTRAL_APPLICATION_BINDINGS:
        raise PrototypeContractError("central application binding is not an approved measurement")
    _validate_application_implementation_identity(qualification)
    evidence = _deserialize_central_evidence_bundle(qualification["evidence_bundle"])
    central_only = dependency_id in {"E-Pipeline-4", "E-Pipeline-5", "E-Pipeline-6"}
    # Central-only experiments inherit the shared Time range policy, not native qualification.
    if (
        evidence.widget_id != widget_id
        or evidence.measure_id != measure_id
        or evidence.dependency_id != dependency_id
        or evidence.runtime_instance_id != central_identity["runtime_instance_id"]
        or evidence.scan_run_id != central_identity["scan_run_id"]
        or evidence.database_identity != central_identity["database_identity"]
        or {evidence.all_version_requirement}
        != {
            row.values["all_version_requirement"]
            for row in rows
            if (
                row.row_id == "A01"
                if central_only
                else row.values["experiment_id"] == dependency_id
            )
        }
        or any(
            evidence_value != qualification[key]
            for key, evidence_value in (
                ("deployment_fingerprint", evidence.deployment_fingerprint),
                ("calculation_implementation_id", evidence.calculation_implementation_id),
                ("calculation_implementation_sha256", evidence.calculation_implementation_sha256),
                ("projection_implementation_id", evidence.projection_implementation_id),
                ("projection_implementation_sha256", evidence.projection_implementation_sha256),
            )
        )
    ):
        raise PrototypeContractError("central qualification evidence bundle is not authoritative")


def _validate_application_evidence(
    value: object,
    rows: Sequence[ProofMatrixRow],
    experiment_registry: frozenset[str],
) -> None:
    """Validate native application qualification without promoting prototype evidence."""
    if not isinstance(value, Mapping) or set(value) != APPLICATION_EVIDENCE_KEYS:
        raise PrototypeContractError("application evidence must use the exact structured contract")
    if value["schema_version"] != 1 or not isinstance(value["bindings"], list):
        raise PrototypeContractError("application evidence must declare schema version 1 bindings")
    row_references = {
        (row.row_id, row.producer): cast(Mapping[str, object], row.values["remote_query"])[
            "reference_id"
        ]
        for row in rows
    }
    seen: set[tuple[str, str, str, str]] = set()
    for binding in value["bindings"]:
        if (
            isinstance(binding, Mapping)
            and set(binding) == CENTRAL_APPLICATION_EVIDENCE_BINDING_KEYS
        ):
            _validate_central_application_binding(binding, rows, seen)
        else:
            _validate_application_binding(binding, rows, experiment_registry, row_references, seen)


def _validate_application_binding(
    binding: object,
    rows: Sequence[ProofMatrixRow],
    experiment_registry: frozenset[str],
    row_references: Mapping[tuple[str, str], object],
    seen: set[tuple[str, str, str, str]],
) -> None:
    if not isinstance(binding, Mapping) or set(binding) != APPLICATION_EVIDENCE_BINDING_KEYS:
        raise PrototypeContractError("application evidence binding must use the exact schema")
    widget_id = binding["widget_id"]
    measure_id = binding["measure_id"]
    dependency_id = binding["dependency_id"]
    producer = binding["producer"]
    if (
        not all(
            _is_nonempty_string(item) for item in (widget_id, measure_id, dependency_id, producer)
        )
        or producer not in SUPPORTED_PRODUCERS
    ):
        raise PrototypeContractError("application evidence binding identity is invalid")
    identity = (widget_id, measure_id, dependency_id, producer)
    if identity in seen:
        raise PrototypeContractError("application evidence bindings must be unique")
    seen.add(identity)
    native_identity = _validate_application_native_identity(binding, producer)
    expected_event_id = _validate_projection_binding(binding)
    _validate_application_qualification(
        binding, native_identity, expected_event_id, rows, experiment_registry
    )
    expected_reference = row_references.get((dependency_id, producer))
    calculation = cast(Mapping[str, object], binding["calculation"])
    if expected_reference is not None:
        if calculation.get("query_reference_id") != expected_reference:
            raise PrototypeContractError(
                "application calculation must bind the matrix query reference"
            )
    elif dependency_id not in experiment_registry or not _is_nonempty_string(
        calculation.get("query_reference_id")
    ):
        raise PrototypeContractError(
            "application evidence dependency must bind a matrix row or independent gate"
        )


def _validate_application_native_identity(
    binding: Mapping[str, object], producer: object
) -> Mapping[str, object]:
    native_identity = binding["native_identity"]
    if (
        not isinstance(native_identity, Mapping)
        or set(native_identity) != APPLICATION_NATIVE_IDENTITY_KEYS
        or native_identity.get("producer") != producer
        or not all(
            _is_nonempty_string(native_identity.get(key))
            for key in ("native_session_id", "native_event_id")
        )
    ):
        raise PrototypeContractError("application evidence must bind immutable native identity")
    return native_identity


def _validate_projection_binding(binding: Mapping[str, object]) -> str:
    calculation = binding["calculation"]
    projection = binding["projection"]
    if (
        not isinstance(calculation, Mapping)
        or set(calculation) != APPLICATION_CALCULATION_KEYS
        or calculation.get("state") != "computed"
        or not _is_nonempty_string(calculation.get("result_event_id"))
    ):
        raise PrototypeContractError(
            "application evidence requires a computed projection calculation result"
        )
    if (
        not isinstance(projection, Mapping)
        or set(projection) != APPLICATION_PROJECTION_KEYS
        or projection.get("state") != "computed"
        or not _is_nonempty_string(projection.get("projection_id"))
        or projection.get("result_event_id") != calculation["result_event_id"]
    ):
        raise PrototypeContractError("application evidence requires a computed projected result")
    return cast(str, calculation["result_event_id"])


def _validate_application_qualification(
    binding: Mapping[str, object],
    native_identity: Mapping[str, object],
    result_event_id: str,
    rows: Sequence[ProofMatrixRow],
    experiment_registry: frozenset[str],
) -> None:
    qualification = binding["qualification"]
    if (
        not isinstance(qualification, Mapping)
        or set(qualification) != APPLICATION_QUALIFICATION_KEYS
    ):
        raise PrototypeContractError("application qualification must use the exact schema")
    if qualification.get("provenance") != "fresh-real" or qualification.get("result") != "Proven":
        raise PrototypeContractError(
            "application qualification requires fresh-real Proven evidence"
        )
    _validate_application_implementation_identity(qualification)
    witness_id = _validate_projection_event_witness(qualification["projection_event_witness"])
    if witness_id != result_event_id:
        raise PrototypeContractError(
            "application qualification projected witness identity is invalid"
        )
    if native_identity["native_event_id"] == result_event_id:
        raise PrototypeContractError(
            "application qualification native and projected event identities must be distinct"
        )
    _validate_raw_native_evidence(qualification, native_identity)
    _validate_application_populations(qualification, result_event_id)
    _validate_application_join_chain(qualification)
    _validate_application_typed_results(qualification)
    dependency_id = cast(str, binding["dependency_id"])
    producer = cast(str, binding["producer"])
    evidence = _deserialize_matrix_evidence(qualification["evidence_bundle"])
    _validate_application_evidence_links(qualification, evidence)
    row = next(
        (item for item in rows if item.row_id == dependency_id and item.producer == producer), None
    )
    if row is None:
        if (
            dependency_id not in experiment_registry
            or qualification["experiment_id"] != dependency_id
        ):
            raise PrototypeContractError(
                "application qualification independent obligation is not canonical"
            )
    else:
        if qualification["experiment_id"] != row.values["experiment_id"]:
            raise PrototypeContractError("application qualification must bind the row experiment")
        _validate_row_evidence_bindings(row.values, evidence, evidence.privacy_allowlist)
    if (
        evidence.provenance is not EvidenceProvenance.FRESH_REAL
        or evidence.result is not ExperimentResult.PROVEN
        or evidence.experiment_id != qualification["experiment_id"]
        or evidence.producer != producer
        or evidence.native_session_id != native_identity["native_session_id"]
        or dict(evidence.remote_result.values) != qualification["remote_result"]["values"]
        or dict(evidence.oracle_result.values) != qualification["oracle_result"]["values"]
    ):
        raise PrototypeContractError(
            "application qualification evidence bundle is not authoritative"
        )


def _validate_application_evidence_links(
    qualification: Mapping[str, object], evidence: EvidenceBundle
) -> None:
    if (
        qualification["selected_range_operator"] != evidence.selected_range_membership_operator
        or qualification["interval_containment_operator"] != evidence.interval_containment_operator
        or qualification["evaluation_policy_id"] != evidence.policy_identity
        or qualification["version_policy_id"] != evidence.version_policy_identity
        or qualification["all_version_requirement"] != evidence.all_version_requirement
        or tuple(cast(Sequence[object], qualification["privacy_allowlist"]))
        != tuple(sorted(evidence.privacy_allowlist))
        or qualification["join_chain"] != _canonicalize(evidence.join_chain)
    ):
        raise PrototypeContractError(
            "application qualification must exactly bind evidence policies and joins"
        )


def _validate_application_implementation_identity(qualification: Mapping[str, object]) -> None:
    hashes = (
        "deployment_fingerprint",
        "calculation_implementation_sha256",
        "projection_implementation_sha256",
    )
    if (
        any(
            not isinstance(qualification.get(key), str)
            or not re.fullmatch(r"[a-f0-9]{64}", str(qualification[key]))
            for key in hashes
        )
        or qualification.get("calculation_implementation_id")
        != "agent-introspection.pipeline-dashboard"
        or qualification.get("projection_implementation_id")
        != "agent-introspection.pipeline-observations"
    ):
        raise PrototypeContractError("application qualification implementation identity is invalid")


def _validate_projection_event_witness(witness: object) -> str:
    if not isinstance(witness, Mapping):
        raise PrototypeContractError(
            "application qualification requires a projection event witness"
        )
    keys: tuple[str, ...]
    text_keys: tuple[str, ...]
    integer_keys: tuple[str, ...]
    if witness.get("kind") == "DerivedEvent":
        keys, text_keys, integer_keys = (
            ("kind", "scope", "entity_id", "entity_version", "event_sequence", "event_name"),
            ("scope", "entity_id", "event_name"),
            ("entity_version", "event_sequence"),
        )
        expected = str(
            uuid.UUID(
                hashlib.sha256(
                    "\x1f".join(str(witness.get(key, "")) for key in keys[1:]).encode()
                ).hexdigest()[:32]
            )
        )
    elif witness.get("kind") == "CanonicalActivityVersionEvent":
        keys, text_keys, integer_keys = (
            ("kind", "activity_id", "version", "payload_schema_version", "event_name"),
            ("activity_id", "event_name"),
            ("version", "payload_schema_version"),
        )
        expected = hashlib.sha256(
            "\x1f".join(str(witness.get(key, "")) for key in keys[1:]).encode()
        ).hexdigest()
    else:
        raise PrototypeContractError("application qualification witness kind is invalid")
    if (
        set(witness) != set(keys)
        or not all(_is_nonempty_string(witness.get(key)) for key in text_keys)
        or any(
            not isinstance(witness.get(key), int)
            or isinstance(witness.get(key), bool)
            or witness[key] < (0 if key == "event_sequence" else 1)
            for key in integer_keys
        )
    ):
        raise PrototypeContractError("application qualification projection witness is invalid")
    return expected


def _validate_raw_native_evidence(
    qualification: Mapping[str, object], native_identity: Mapping[str, object]
) -> None:
    raw = qualification["raw_evidence"]
    if (
        not isinstance(raw, Mapping)
        or set(raw)
        != {
            "bounded_start_ns",
            "bounded_end_ns",
            "opaque_native_event_ids",
            "native_identity_tuple",
        }
        or not all(
            isinstance(raw.get(key), str) and re.fullmatch(r"(?:0|[1-9]\d*)", raw[key])
            for key in ("bounded_start_ns", "bounded_end_ns")
        )
        or int(cast(str, raw["bounded_start_ns"])) >= int(cast(str, raw["bounded_end_ns"]))
        or tuple(raw.get("native_identity_tuple", ()))
        != (native_identity["producer"], native_identity["native_session_id"])
        or not _is_ordered_nonempty_strings(raw.get("opaque_native_event_ids", ()))
        or native_identity["native_event_id"] not in raw["opaque_native_event_ids"]
    ):
        raise PrototypeContractError(
            "application qualification requires bounded raw native evidence"
        )


def _validate_application_populations(
    qualification: Mapping[str, object], result_event_id: str
) -> None:
    populations = ("source_event_ids", "output_event_ids", "remote_event_ids", "oracle_event_ids")
    population_values: dict[str, list[object]] = {}
    for field in populations:
        value = qualification.get(field)
        if (
            not isinstance(value, list)
            or not _is_ordered_nonempty_strings(value)
            or len(set(value)) != len(value)
        ):
            raise PrototypeContractError(
                "application qualification population conservation is invalid"
            )
        population_values[field] = value
    raw = cast(Mapping[str, object], qualification["raw_evidence"])
    if (
        set(population_values["source_event_ids"])
        != set(cast(Sequence[object], raw["opaque_native_event_ids"]))
        or any(
            set(population_values[field]) != set(population_values["output_event_ids"])
            for field in ("remote_event_ids", "oracle_event_ids")
        )
        or result_event_id not in population_values["output_event_ids"]
        or bool(
            set(population_values["source_event_ids"]) & set(population_values["output_event_ids"])
        )
        or any(not _is_projected_event_id(item) for item in population_values["output_event_ids"])
    ):
        raise PrototypeContractError("application qualification population conservation is invalid")


def _is_projected_event_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return bool(re.fullmatch(r"[a-f0-9]{64}", value))


def _validate_application_join_chain(qualification: Mapping[str, object]) -> None:
    chain = qualification["join_chain"]
    if (
        not isinstance(chain, list)
        or len(chain) < 2
        or chain[0]
        != {"source": "native_identity_tuple", "identity_field": "producer,native_session_id"}
        or any(
            not isinstance(step, Mapping)
            or set(step) != {"source", "identity_field"}
            or not _is_nonempty_string(step.get("source"))
            or not _is_nonempty_string(step.get("identity_field"))
            for step in chain
        )
    ):
        raise PrototypeContractError(
            "application qualification requires an anchored native join chain"
        )


def _validate_application_typed_results(qualification: Mapping[str, object]) -> None:
    adverse_cases = qualification["adverse_cases"]
    privacy_allowlist = qualification["privacy_allowlist"]
    remote, oracle = qualification["remote_result"], qualification["oracle_result"]
    if (
        not isinstance(adverse_cases, list)
        or set(adverse_cases) != REQUIRED_ADVERSE_CASES
        or len(adverse_cases) != len(REQUIRED_ADVERSE_CASES)
        or not isinstance(privacy_allowlist, list)
        or not _is_ordered_nonempty_strings(privacy_allowlist)
        or remote != oracle
        or not isinstance(remote, Mapping)
        or set(remote) != {"state", "values"}
        or remote.get("state") != "computed"
        or not isinstance(remote.get("values"), Mapping)
        or not remote["values"]
        or any(not _is_scalar(value) for value in remote["values"].values())
    ):
        raise PrototypeContractError("application qualification requires complete typed evidence")


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


def _deserialize_central_evidence_bundle(value: object) -> CentralEvidenceBundle:
    if not isinstance(value, Mapping):
        raise PrototypeContractError("central evidence requires a structured bundle")
    _require_exact_keys(
        value, frozenset(CentralEvidenceBundle.__dataclass_fields__), "central bundle"
    )
    try:
        return CentralEvidenceBundle(**cast(Any, dict(value)))
    except (TypeError, ValueError) as exc:
        raise PrototypeContractError("central evidence bundle is invalid") from exc


def _validate_central_evidence_bundle(bundle: CentralEvidenceBundle) -> None:
    if (
        bundle.schema_version != 1
        or bundle.capture_population != "fresh-real-recording"
        or not all(
            _is_nonempty_string(value)
            for value in (
                bundle.widget_id,
                bundle.measure_id,
                bundle.dependency_id,
                bundle.runtime_instance_id,
                bundle.scan_run_id,
                bundle.selected_range_operator,
                bundle.interval_containment_operator,
                bundle.evaluation_policy_id,
                bundle.version_policy_id,
                bundle.all_version_requirement,
            )
        )
        or (bundle.widget_id, bundle.measure_id, bundle.dependency_id)
        not in CENTRAL_APPLICATION_BINDINGS
        or not re.fullmatch(r"[a-f0-9]{64}", bundle.database_identity)
        or not all(
            isinstance(value, str) and re.fullmatch(r"(?:0|[1-9]\d*)", value)
            for value in (bundle.bounded_start_ns, bundle.bounded_end_ns)
        )
        or int(bundle.bounded_start_ns) >= int(bundle.bounded_end_ns)
        or bundle.selected_range_operator != CENTRAL_SELECTED_RANGE_OPERATORS.get(bundle.widget_id)
        or bundle.interval_containment_operator != CENTRAL_INTERVAL_CONTAINMENT_OPERATOR
        or bundle.evaluation_policy_id != bundle.calculation_implementation_id
        or bundle.version_policy_id != bundle.projection_implementation_id
        or tuple(bundle.unresolved_dependencies)
    ):
        raise PrototypeContractError("central evidence identity, bound, or dependency is invalid")
    _validate_central_artifacts(bundle)
    _validate_central_populations(bundle)
    _validate_central_calculations(bundle)
    _validate_allowlisted_inventory(
        bundle.raw_field_inventory, _normalize_privacy_allowlist(bundle.privacy_allowlist)
    )
    if not _is_ordered_nonempty_strings(bundle.privacy_allowlist):
        raise PrototypeContractError("central evidence privacy inventory is invalid")
    _validate_central_adverse_cases(bundle)
    implementation = {
        "deployment_fingerprint": bundle.deployment_fingerprint,
        "calculation_implementation_id": bundle.calculation_implementation_id,
        "calculation_implementation_sha256": bundle.calculation_implementation_sha256,
        "projection_implementation_id": bundle.projection_implementation_id,
        "projection_implementation_sha256": bundle.projection_implementation_sha256,
    }
    _validate_application_implementation_identity(implementation)
    if bundle.retained_artifacts["implementation_witnesses"]["content"] != implementation:
        raise PrototypeContractError("central implementation witness is stale or conflicting")


def _validate_central_adverse_cases(bundle: CentralEvidenceBundle) -> None:
    if set(bundle.adverse_case_artifacts) != REQUIRED_ADVERSE_CASES:
        raise PrototypeContractError("central evidence requires observed adverse-case artifacts")
    for category, reference in bundle.adverse_case_artifacts.items():
        content = bundle.retained_artifacts[reference]["content"]
        if (
            not isinstance(content, Mapping)
            or set(content)
            != {
                "category",
                "population",
                "scenario_id",
                "input_manifest_sha256",
                "observed_outcome",
                "expected_outcome",
            }
            or content["category"] != category
            or content["population"] != "robustness"
            or not _is_nonempty_string(content["scenario_id"])
            or not isinstance(content["input_manifest_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", content["input_manifest_sha256"])
            or not _is_nonempty_string(content["expected_outcome"])
            or content["observed_outcome"] != content["expected_outcome"]
        ):
            raise PrototypeContractError(
                "central evidence requires matching observed adverse-case artifacts"
            )


def _validate_central_artifacts(bundle: CentralEvidenceBundle) -> None:
    required = {
        "central_capture",
        "projection_manifest",
        "displayed_calculation",
        "remote_calculation",
        "oracle_calculation",
        "privacy_inventory",
        "implementation_witnesses",
        *bundle.adverse_case_artifacts.values(),
    }
    if set(bundle.retained_artifacts) != required:
        raise PrototypeContractError("central evidence must retain the complete artifact manifest")
    for artifact_id, artifact in bundle.retained_artifacts.items():
        if (
            not _is_nonempty_string(artifact_id)
            or not isinstance(artifact, Mapping)
            or set(artifact) != {"content_sha256", "content"}
            or not isinstance(artifact["content_sha256"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", artifact["content_sha256"])
            or not isinstance(artifact["content"], Mapping)
            or hashlib.sha256(
                json.dumps(
                    _canonicalize(artifact["content"]),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode("utf-8")
            ).hexdigest()
            != artifact["content_sha256"]
        ):
            raise PrototypeContractError("central retained artifact hash is invalid")
    capture = _canonicalize(bundle.retained_artifacts["central_capture"]["content"])
    projection = _canonicalize(bundle.retained_artifacts["projection_manifest"]["content"])
    if (
        capture
        != {
            "runtime_instance_id": bundle.runtime_instance_id,
            "scan_run_id": bundle.scan_run_id,
            "database_identity": bundle.database_identity,
            "bounded_start_ns": bundle.bounded_start_ns,
            "bounded_end_ns": bundle.bounded_end_ns,
            "source_input_ids": list(bundle.source_input_ids),
        }
        or projection
        != {
            "output_event_ids": list(bundle.output_event_ids),
            "source_output_lineage": list(bundle.source_output_lineage),
        }
        or _canonicalize(bundle.retained_artifacts["privacy_inventory"]["content"])
        != {
            "privacy_allowlist": list(bundle.privacy_allowlist),
            "raw_field_inventory": list(bundle.raw_field_inventory),
        }
    ):
        raise PrototypeContractError("central artifact content does not bind this report")


def _validate_central_populations(bundle: CentralEvidenceBundle) -> None:
    populations = (
        bundle.source_input_ids,
        bundle.output_event_ids,
        bundle.remote_event_ids,
        bundle.oracle_event_ids,
    )
    if (
        any(
            not _is_ordered_nonempty_strings(population) or len(set(population)) != len(population)
            for population in populations
        )
        or set(bundle.remote_event_ids) != set(bundle.output_event_ids)
        or set(bundle.oracle_event_ids) != set(bundle.output_event_ids)
    ):
        raise PrototypeContractError("central event manifest equality is invalid")
    lineage = bundle.source_output_lineage
    if (
        not isinstance(lineage, (list, tuple))
        or not lineage
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"source_input_id", "output_event_id"}
            or item["source_input_id"] not in bundle.source_input_ids
            or item["output_event_id"] not in bundle.output_event_ids
            for item in lineage
        )
        or {item["source_input_id"] for item in lineage} != set(bundle.source_input_ids)
        or {item["output_event_id"] for item in lineage} != set(bundle.output_event_ids)
        or len({(item["source_input_id"], item["output_event_id"]) for item in lineage})
        != len(lineage)
        or set(bundle.source_input_ids) & set(bundle.output_event_ids)
    ):
        raise PrototypeContractError("central source-to-output lineage is invalid")
    witnesses = bundle.projection_event_witnesses
    if (
        not isinstance(witnesses, (list, tuple))
        or len(witnesses) != len(bundle.output_event_ids)
        or any(
            not isinstance(item, Mapping)
            or set(item) != {"output_event_id", "witness"}
            or item["output_event_id"] not in bundle.output_event_ids
            or _validate_projection_event_witness(item["witness"]) != item["output_event_id"]
            for item in witnesses
        )
        or {item["output_event_id"] for item in witnesses} != set(bundle.output_event_ids)
    ):
        raise PrototypeContractError("central immutable projection witnesses are invalid")


def _validate_central_calculations(bundle: CentralEvidenceBundle) -> None:
    displayed = _central_display_panel(bundle)
    for name, calculation in (
        ("remote_calculation", bundle.remote_calculation),
        ("oracle_calculation", bundle.oracle_calculation),
    ):
        if (
            not isinstance(calculation, Mapping)
            or set(calculation) != {"reference_id", "bound_parameters", "result"}
            or not _is_nonempty_string(calculation["reference_id"])
            or not isinstance(calculation["bound_parameters"], Mapping)
            or not isinstance(calculation["result"], Mapping)
            or set(calculation["result"]) != {"state", "panel_json"}
            or calculation["result"]["state"] != "computed"
            or calculation["result"]["panel_json"] != displayed
            or bundle.retained_artifacts[name]["content"] != calculation
        ):
            raise PrototypeContractError(
                "central displayed, remote and oracle calculations must match exactly"
            )
        _validate_central_calculation_bounds(bundle, calculation["bound_parameters"])
    if (
        bundle.remote_calculation["reference_id"] == bundle.oracle_calculation["reference_id"]
        or bundle.remote_calculation["bound_parameters"]
        != bundle.oracle_calculation["bound_parameters"]
    ):
        raise PrototypeContractError("central remote and oracle calculations must match exactly")


def _validate_central_calculation_bounds(
    bundle: CentralEvidenceBundle, parameters: Mapping[str, object]
) -> None:
    evaluated = parameters.get("evaluated_at_ns")
    measurement_start = parameters.get("measurement_start_ns")
    if (
        not isinstance(evaluated, str)
        or not re.fullmatch(r"(?:0|[1-9]\d*)", evaluated)
        or int(evaluated) < int(bundle.bounded_end_ns)
        or (
            measurement_start is not None
            and (
                not isinstance(measurement_start, str)
                or not re.fullmatch(r"(?:0|[1-9]\d*)", measurement_start)
            )
        )
        or parameters
        != {
            "start_ns": bundle.bounded_start_ns,
            "end_ns": bundle.bounded_end_ns,
            "evaluated_at_ns": evaluated,
            "measurement_start_ns": measurement_start,
            "selected_range_operator": bundle.selected_range_operator,
            "interval_containment_operator": bundle.interval_containment_operator,
            "evaluation_policy_id": bundle.evaluation_policy_id,
            "version_policy_id": bundle.version_policy_id,
            "all_version_requirement": bundle.all_version_requirement,
        }
    ):
        raise PrototypeContractError("central calculation bounds and policies must bind the report")


def _central_display_panel(bundle: CentralEvidenceBundle) -> str:
    display = bundle.displayed_calculation
    if (
        not isinstance(display, Mapping)
        or set(display) != {"widget_id", "panel_json"}
        or display["widget_id"] != bundle.widget_id
        or not isinstance(display["panel_json"], str)
        or bundle.retained_artifacts["displayed_calculation"]["content"] != display
    ):
        raise PrototypeContractError("central displayed calculation must bind the complete panel")
    try:
        panel = json.loads(display["panel_json"])
        if _central_json(panel) != display["panel_json"]:
            raise PrototypeContractError("central displayed panel requires canonical JSON")
    except (ValueError, TypeError) as exc:
        raise PrototypeContractError(
            "central displayed panel requires finite canonical JSON"
        ) from exc
    if (
        not isinstance(panel, dict)
        or set(panel)
        != {
            "columns",
            "metrics",
            "population",
            "provenance",
            "rangeOperator",
            "reasons",
            "rows",
            "series",
            "state",
            "timeBasis",
        }
        or panel["state"] != "Data"
        or panel["rangeOperator"] != bundle.selected_range_operator
        or any(not isinstance(panel[key], list) for key in ("columns", "metrics", "rows", "series"))
        or not any(panel[key] for key in ("metrics", "rows", "series"))
    ):
        raise PrototypeContractError("central displayed calculation requires a complete Data panel")
    return display["panel_json"]


def _central_json(value: object) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


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
        value,
        (
            EvidenceBundle,
            CentralEvidenceBundle,
            AppendixQueryReference,
            CalculationResult,
            JoinIdentityStep,
        ),
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


def _main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, Mapping):
            raise PrototypeContractError("proof matrix root must be an object")
        validate_proof_matrix(payload)
    except Exception:
        print("proof matrix validation failed")
        return 1
    print("proof matrix validation succeeded")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
