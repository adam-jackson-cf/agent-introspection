import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from experiments.dashboard_prototype.contracts import (
    CANONICAL_APPENDIX_V1_SHA256,
    CANONICAL_TEMPORAL_CONTRACT_V1_SHA256,
    CENTRAL_INTERVAL_CONTAINMENT_OPERATOR,
    CENTRAL_SELECTED_RANGE_OPERATORS,
    CLEANUP_RUN_ID_SELECTOR,
    EXPERIMENT_NAMESPACE,
    AppendixQueryId,
    AppendixQueryParameter,
    AppendixQueryReference,
    CalculationResult,
    CalculationResultState,
    CalculationValue,
    CentralEvidenceBundle,
    EvidenceBundle,
    EvidenceProvenance,
    ExperimentResult,
    JoinIdentityStep,
    PrototypeContractError,
    QueryParameterValue,
    derive_event_id,
    hash_evidence_bundle,
    load_proof_matrix,
    serialize_evidence_bundle,
    validate_proof_matrix,
)

MATRIX_PATH = Path(__file__).parents[1] / "docs/dashboard-prototype-proof-matrix.json"


def _matrix() -> dict[str, Any]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _inventory(name: str) -> dict[str, object]:
    return {
        "name": name,
        "type": "integer",
        "presence": "present",
        "cardinality": "one",
        "owner": "producer",
    }


def _bundle() -> EvidenceBundle:
    event_id_inputs = (
        {
            "experiment_id": "E-Pipeline-1",
            "producer": "omp",
            "native_session_id": "session-1",
            "event_id_ordinal": 0,
        },
        {
            "experiment_id": "E-Pipeline-1",
            "producer": "omp",
            "native_session_id": "session-1",
            "event_id_ordinal": 1,
        },
    )
    event_ids = tuple(derive_event_id(event_input) for event_input in event_id_inputs)
    return EvidenceBundle(
        experiment_namespace=EXPERIMENT_NAMESPACE,
        run_id="run-20260901",
        experiment_id="E-Pipeline-1",
        hypothesis="The reducer conserves bounded input population.",
        producer="omp",
        native_session_id="session-1",
        surface="native session events",
        installed_version="1.0",
        capability_state="supported",
        provenance=EvidenceProvenance.FRESH_REAL,
        start_time="2026-09-01T00:00:00Z",
        end_time="2026-09-01T00:01:00Z",
        source_extraction_boundary="fresh bounded session",
        selected_range_membership_operator="source_time > start AND source_time <= end",
        interval_containment_operator="interval_start <= source_time < interval_end",
        evaluation_time="2026-09-01T00:01:00Z",
        policy_identity="A01.evaluation-policy-v1",
        version_policy_identity="A01.version-policy-v1",
        all_version_requirement=(
            "Use the globally latest valid version per stable identity when the Appendix "
            "calculation is versioned; otherwise retain the complete accepted immutable "
            "event population."
        ),
        privacy_allowlist=frozenset(
            {"experiment_id", "producer", "native_session_id", "event_id_ordinal", "token_counts"}
        ),
        raw_field_inventory=(_inventory("native_session_id"), _inventory("token_counts")),
        native_identity_tuple=("omp", "session-1"),
        join_chain=(
            JoinIdentityStep("native_identity_tuple", "producer,native_session_id"),
            JoinIdentityStep("stable_identity", "canonical_session_id"),
        ),
        canonical_schema={"native_session_id": "string", "token_counts": "integer"},
        event_id_inputs=event_id_inputs,
        input_count=4,
        accepted_count=2,
        rejected_count_by_reason={"missing_timestamp": 1},
        duplicate_count=1,
        output_count=2,
        conservation_equation="4 = 2 + 1 + 1; 2 = 2",
        remote_event_ids=event_ids,
        local_outbox_event_ids=event_ids,
        appendix_calculation_query=AppendixQueryReference(
            row_id="A01",
            query_id=AppendixQueryId.POPULATION_COUNT,
            bound_parameters={
                AppendixQueryParameter.START_EPOCH: 1756684800,
                AppendixQueryParameter.END_EPOCH: 1756684860,
            },
        ),
        remote_query_reference_id="A01.omp.remote-query",
        remote_result=CalculationResult(
            state=CalculationResultState.COMPUTED,
            values={"count": 2, "complete": True, "missing": None},
        ),
        oracle_reference_id="A01.omp.oracle",
        oracle_result=CalculationResult(
            state=CalculationResultState.COMPUTED,
            values={"count": 2, "complete": True, "missing": None},
        ),
        edge_cases=(
            "endpoint",
            "negative",
            "missing",
            "conflicting",
            "duplicate",
            "out-of-order",
            "version",
            "clock-skew",
        ),
        privacy_review="allowlisted fields only",
        result=ExperimentResult.PROVEN,
        recommendation="Do not cut over before the bounded experiment completes.",
        unresolved_dependency=None,
    )


def _central_artifact(content: Mapping[str, object]) -> dict[str, object]:
    return {
        "content_sha256": hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "content": content,
    }


def _central_bundle(
    binding: tuple[str, str, str] = ("p1-snapshot", "P1", "E-Pipeline-1"),
) -> CentralEvidenceBundle:
    widget_id, measure_id, dependency_id = binding
    source_ids = ["central-input-1"]
    witness = {
        "kind": "DerivedEvent",
        "scope": "pipeline",
        "entity_id": "scan-1",
        "entity_version": 1,
        "event_sequence": 0,
        "event_name": "introspection.pipeline.snapshot",
    }
    output_ids = [
        str(
            UUID(
                hashlib.sha256(
                    "\x1f".join(
                        (
                            witness["scope"],
                            witness["entity_id"],
                            "1",
                            "0",
                            witness["event_name"],
                        )
                    ).encode()
                ).hexdigest()[:32]
            )
        )
    ]
    lineage = [{"source_input_id": source_ids[0], "output_event_id": output_ids[0]}]
    policies = {
        "selected_range_operator": CENTRAL_SELECTED_RANGE_OPERATORS[widget_id],
        "interval_containment_operator": CENTRAL_INTERVAL_CONTAINMENT_OPERATOR,
        "evaluation_policy_id": "agent-introspection.pipeline-dashboard",
        "version_policy_id": "agent-introspection.pipeline-observations",
        "all_version_requirement": _matrix()["rows"][0]["all_version_requirement"],
    }
    panel = {
        "state": "Data",
        "rangeOperator": policies["selected_range_operator"],
        "population": "canonical scans",
        "provenance": "test fixture",
        "timeBasis": "scan completion time",
        "reasons": [],
        "metrics": [
            {
                "label": "count",
                "value": 1.0,
                "unit": "rows",
                "sampleCount": 1,
                "numerator": None,
                "denominator": None,
            }
        ],
        "rows": [["kind", -0.0, None, False, 9007199254740993]],
        "columns": ["label", "value", "missing", "boolean", "large integer"],
        "series": [{"name": "count", "points": [["1", 1.0]]}],
    }
    displayed = {
        "widget_id": widget_id,
        "panel_json": json.dumps(panel, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    }
    remote_calculation = {
        "reference_id": "pipeline.central.remote-query",
        "bound_parameters": {
            "start_ns": "1",
            "end_ns": "2",
            "evaluated_at_ns": "2",
            "measurement_start_ns": None,
            **policies,
        },
        "result": {"state": "computed", "panel_json": displayed["panel_json"]},
    }
    oracle_calculation = {**remote_calculation, "reference_id": "pipeline.central.oracle"}
    implementation = {
        "deployment_fingerprint": "a" * 64,
        "calculation_implementation_id": "agent-introspection.pipeline-dashboard",
        "calculation_implementation_sha256": "b" * 64,
        "projection_implementation_id": "agent-introspection.pipeline-observations",
        "projection_implementation_sha256": "c" * 64,
    }
    adverse = {
        case: f"adverse-{case}"
        for case in sorted(
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
    }
    artifacts = {
        "central_capture": _central_artifact(
            {
                "runtime_instance_id": "runtime-1",
                "scan_run_id": "scan-1",
                "database_identity": "d" * 64,
                "bounded_start_ns": "1",
                "bounded_end_ns": "2",
                "source_input_ids": source_ids,
            }
        ),
        "projection_manifest": _central_artifact(
            {"output_event_ids": output_ids, "source_output_lineage": lineage}
        ),
        "displayed_calculation": _central_artifact(displayed),
        "remote_calculation": _central_artifact(remote_calculation),
        "oracle_calculation": _central_artifact(oracle_calculation),
        "privacy_inventory": _central_artifact(
            {
                "privacy_allowlist": ["token_counts"],
                "raw_field_inventory": [_inventory("token_counts")],
            }
        ),
        "implementation_witnesses": _central_artifact(implementation),
        **{
            artifact_id: _central_artifact(
                {
                    "category": case,
                    "population": "robustness",
                    "scenario_id": f"{case}-scenario",
                    "input_manifest_sha256": "e" * 64,
                    "observed_outcome": "rejected",
                    "expected_outcome": "rejected",
                }
            )
            for case, artifact_id in adverse.items()
        },
    }
    return CentralEvidenceBundle(
        schema_version=1,
        widget_id=widget_id,
        measure_id=measure_id,
        dependency_id=dependency_id,
        runtime_instance_id="runtime-1",
        scan_run_id="scan-1",
        database_identity="d" * 64,
        capture_population="fresh-real-recording",
        bounded_start_ns="1",
        bounded_end_ns="2",
        **policies,
        retained_artifacts=artifacts,
        source_input_ids=source_ids,
        output_event_ids=output_ids,
        remote_event_ids=output_ids,
        oracle_event_ids=output_ids,
        source_output_lineage=lineage,
        projection_event_witnesses=[{"output_event_id": output_ids[0], "witness": witness}],
        displayed_calculation=displayed,
        remote_calculation=remote_calculation,
        oracle_calculation=oracle_calculation,
        privacy_allowlist=["token_counts"],
        raw_field_inventory=[_inventory("token_counts")],
        adverse_case_artifacts=adverse,
        unresolved_dependencies=[],
        **implementation,
    )


def test_central_bundle_rejects_identity_manifest_and_dependency_failures() -> None:
    bundle = _central_bundle()
    with pytest.raises(PrototypeContractError, match="identity"):
        replace(bundle, database_identity="not-a-sha")
    with pytest.raises(PrototypeContractError, match="manifest equality"):
        replace(bundle, remote_event_ids=["different-event"])
    with pytest.raises(PrototypeContractError, match="dependency"):
        replace(bundle, unresolved_dependencies=["unresolved"])

    artifacts = dict(bundle.retained_artifacts)
    del artifacts["projection_manifest"]
    with pytest.raises(PrototypeContractError, match="artifact manifest"):
        replace(bundle, retained_artifacts=artifacts)
    with pytest.raises(PrototypeContractError, match="stale or conflicting"):
        replace(bundle, deployment_fingerprint="e" * 64)
    for coordinate, value in (
        ("entity_version", 0),
        ("event_sequence", -1),
        ("event_sequence", False),
    ):
        witnesses = json.loads(bundle.canonical_json())["projection_event_witnesses"]
        witnesses[0]["witness"][coordinate] = value
        with pytest.raises(PrototypeContractError, match="projection witness"):
            replace(bundle, projection_event_witnesses=witnesses)

    # Rehash modified artifacts so each rejection defends semantics, not a stale hash.
    mutations = [
        (("remote_calculation", "bound_parameters", "start_ns"), "0", "bounds"),
        (("remote_calculation", "bound_parameters", "evaluated_at_ns"), "1", "bounds"),
        (("remote_calculation", "bound_parameters", "measurement_start_ns"), "-1", "bounds"),
        (("remote_calculation", "bound_parameters", "evaluation_policy_id"), "unknown", "bounds"),
        (
            ("retained_artifacts", "adverse-negative", "content", "observed_outcome"),
            "accepted",
            "adverse",
        ),
        (
            ("retained_artifacts", "adverse-version", "content", "input_manifest_sha256"),
            "invalid",
            "adverse",
        ),
    ]
    for keys, value, message in mutations:
        altered = json.loads(bundle.canonical_json())
        target = altered
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
        for name in ("displayed_calculation", "remote_calculation", "oracle_calculation"):
            altered["retained_artifacts"][name] = _central_artifact(altered[name])
        for name, artifact in altered["retained_artifacts"].items():
            altered["retained_artifacts"][name] = _central_artifact(artifact["content"])
        with pytest.raises(PrototypeContractError, match=message):
            CentralEvidenceBundle(**altered)

    # Whole-panel equality preserves kinds, signed zero, exact integers and all
    # displayed populations without a duplicate scalar map or JavaScript numbers.
    panel_mutations = [
        (("metrics", 0, "value"), 1),
        (("rows", 0, 1), 0.0),
        (("rows", 0, 4), 9007199254740992),
        (("metrics", 0, "sampleCount"), 2),
        (("series", 0, "points", 0, 1), 2.0),
    ]
    for keys, value in panel_mutations:
        altered = json.loads(bundle.canonical_json())
        panel = json.loads(altered["oracle_calculation"]["result"]["panel_json"])
        target = panel
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
        altered["oracle_calculation"]["result"]["panel_json"] = json.dumps(
            panel, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        altered["retained_artifacts"]["oracle_calculation"] = _central_artifact(
            altered["oracle_calculation"]
        )
        with pytest.raises(PrototypeContractError, match="match exactly"):
            CentralEvidenceBundle(**altered)


def test_matrix_accepts_only_a_complete_authoritative_central_binding() -> None:
    payload = _matrix()
    payload["application_evidence"]["bindings"] = [
        {
            "widget_id": bundle.widget_id,
            "measure_id": bundle.measure_id,
            "dependency_id": bundle.dependency_id,
            "central_identity": {
                "runtime_instance_id": bundle.runtime_instance_id,
                "scan_run_id": bundle.scan_run_id,
                "database_identity": bundle.database_identity,
            },
            "qualification": {
                "provenance": "fresh-real",
                "result": "Proven",
                "deployment_fingerprint": bundle.deployment_fingerprint,
                "calculation_implementation_id": bundle.calculation_implementation_id,
                "calculation_implementation_sha256": bundle.calculation_implementation_sha256,
                "projection_implementation_id": bundle.projection_implementation_id,
                "projection_implementation_sha256": bundle.projection_implementation_sha256,
                "evidence_bundle": json.loads(bundle.canonical_json()),
            },
        }
        for bundle in (_central_bundle(), _central_bundle(("p12-ledger", "P12", "E-Pipeline-6")))
    ]
    validate_proof_matrix(payload)
    payload["application_evidence"]["bindings"][0]["qualification"]["deployment_fingerprint"] = (
        "f" * 64
    )
    with pytest.raises(PrototypeContractError, match="authoritative"):
        validate_proof_matrix(payload)


def _application_binding() -> dict[str, object]:
    row = _matrix()["rows"][0]
    witness = {
        "kind": "CanonicalActivityVersionEvent",
        "activity_id": "activity-1",
        "version": 1,
        "payload_schema_version": 1,
        "event_name": "dashboard.metric",
    }
    result_event_id = hashlib.sha256(
        "\x1f".join(
            (
                witness["activity_id"],
                str(witness["version"]),
                str(witness["payload_schema_version"]),
                witness["event_name"],
            )
        ).encode()
    ).hexdigest()
    typed_zero = CalculationResult(
        CalculationResultState.COMPUTED,
        {"count": 0, "complete": False, "missing": None},
    )
    evidence = replace(
        _bundle(),
        selected_range_membership_operator=row["selected_range_operator"],
        interval_containment_operator=row["interval_containment_operator"],
        policy_identity=row["evaluation_policy_id"],
        version_policy_identity=row["version_policy_id"],
        all_version_requirement=row["all_version_requirement"],
        remote_result=typed_zero,
        oracle_result=typed_zero,
    )
    qualification = {
        "provenance": "fresh-real",
        "result": "Proven",
        "experiment_id": row["experiment_id"],
        "deployment_fingerprint": "a" * 64,
        "calculation_implementation_id": "agent-introspection.pipeline-dashboard",
        "calculation_implementation_sha256": "b" * 64,
        "projection_implementation_id": "agent-introspection.pipeline-observations",
        "projection_implementation_sha256": "c" * 64,
        "projection_event_witness": witness,
        "raw_evidence": {
            "bounded_start_ns": "1",
            "bounded_end_ns": "2",
            "opaque_native_event_ids": ["opaque-native-event-1"],
            "native_identity_tuple": ["omp", "session-1"],
        },
        "selected_range_operator": row["selected_range_operator"],
        "interval_containment_operator": row["interval_containment_operator"],
        "evaluation_policy_id": row["evaluation_policy_id"],
        "version_policy_id": row["version_policy_id"],
        "all_version_requirement": row["all_version_requirement"],
        "join_chain": [
            {"source": "native_identity_tuple", "identity_field": "producer,native_session_id"},
            {"source": "stable_identity", "identity_field": "canonical_session_id"},
        ],
        "source_event_ids": ["opaque-native-event-1"],
        "output_event_ids": [result_event_id],
        "remote_event_ids": [result_event_id],
        "oracle_event_ids": [result_event_id],
        "remote_result": {
            "state": "computed",
            "values": {"count": 0, "complete": False, "missing": None},
        },
        "oracle_result": {
            "state": "computed",
            "values": {"count": 0, "complete": False, "missing": None},
        },
        "adverse_cases": [
            "endpoint",
            "negative",
            "missing",
            "conflicting",
            "duplicate",
            "out-of-order",
            "version",
            "clock-skew",
        ],
        "privacy_allowlist": [
            "event_id_ordinal",
            "experiment_id",
            "native_session_id",
            "producer",
            "token_counts",
        ],
        "evidence_bundle": json.loads(serialize_evidence_bundle(evidence)),
    }
    return {
        "widget_id": "pipeline-health",
        "measure_id": "accepted-count",
        "dependency_id": row["row_id"],
        "producer": "omp",
        "native_identity": {
            "producer": "omp",
            "native_session_id": "session-1",
            "native_event_id": "opaque-native-event-1",
        },
        "calculation": {
            "state": "computed",
            "query_reference_id": row["remote_query"]["reference_id"],
            "result_event_id": result_event_id,
        },
        "projection": {
            "state": "computed",
            "projection_id": "pipeline-observations-v1",
            "result_event_id": result_event_id,
        },
        "qualification": qualification,
    }


def test_loads_checked_in_canonical_matrix() -> None:
    matrix = load_proof_matrix(MATRIX_PATH)

    assert len(matrix.rows) == 129
    assert {row.row_id for row in matrix.rows} == {f"A{number:02d}" for number in range(1, 44)}
    assert {row.producer for row in matrix.rows} == {
        "omp",
        "codex-cli",
        "codex-app-server",
    }


def test_application_qualification_requires_full_distinct_native_projection_evidence() -> None:
    payload = _matrix()
    payload["application_evidence"]["bindings"] = [_application_binding()]

    validate_proof_matrix(payload)

    incomplete = copy.deepcopy(payload)
    del incomplete["application_evidence"]["bindings"][0]["qualification"]["evidence_bundle"][
        "recommendation"
    ]
    with pytest.raises(PrototypeContractError):
        validate_proof_matrix(incomplete)

    conflated = copy.deepcopy(payload)
    binding = conflated["application_evidence"]["bindings"][0]
    result_event_id = binding["projection"]["result_event_id"]
    binding["native_identity"]["native_event_id"] = result_event_id
    qualification = binding["qualification"]
    qualification["raw_evidence"]["opaque_native_event_ids"] = [result_event_id]
    qualification["source_event_ids"] = [result_event_id]
    with pytest.raises(PrototypeContractError, match="distinct"):
        validate_proof_matrix(conflated)


def test_matrix_requires_exact_experiment_registry_and_retains_it() -> None:
    matrix = validate_proof_matrix(_matrix())

    assert matrix.experiment_registry == (
        "E-Pipeline-1",
        "E-Pipeline-2",
        "E-Pipeline-3",
        "E-Pipeline-4",
        "E-Pipeline-5",
        "E-Pipeline-6",
        "E-Attribution-1",
        "E-Attribution-2",
        "E-Attribution-3",
        "E-Attribution-4",
        "E-Attribution-5",
        "E-Request-1",
        "E-Request-2",
        "E-Request-3",
        "E-Task-0",
        "E-Task-1",
        "E-Task-2",
        "E-Task-3",
        "E-Task-4",
        "E-Recurrence-0",
        "E-Recurrence-1",
        "E-Recurrence-2",
        "E-Recurrence-3",
        "E-Recurrence-4",
    )

    for registry in (
        _matrix()["experiment_registry"][:-1],
        [*_matrix()["experiment_registry"], "E-invented"],
        list(reversed(_matrix()["experiment_registry"])),
    ):
        invalid = _matrix()
        invalid["experiment_registry"] = registry
        with pytest.raises(PrototypeContractError, match="experiment registry"):
            validate_proof_matrix(invalid)


def test_rejects_missing_duplicate_or_noncanonical_matrix_rows() -> None:
    missing = _matrix()
    missing["rows"] = missing["rows"][:-1]
    duplicate = _matrix()
    duplicate["rows"].append(copy.deepcopy(duplicate["rows"][0]))
    noncanonical = _matrix()
    noncanonical["rows"][0]["row_id"] = "volume"

    with pytest.raises(PrototypeContractError, match="every canonical row ID"):
        validate_proof_matrix(missing)
    with pytest.raises(PrototypeContractError, match="duplicate"):
        validate_proof_matrix(duplicate)
    with pytest.raises(PrototypeContractError, match="canonical"):
        validate_proof_matrix(noncanonical)


def test_rejects_changed_canonical_appendix_content() -> None:
    payload = _matrix()
    payload["rows"][0]["route"] = "altered"

    with pytest.raises(PrototypeContractError, match="seven-field"):
        validate_proof_matrix(payload)


def test_canonical_appendix_digest_has_an_independent_golden_value() -> None:
    assert CANONICAL_APPENDIX_V1_SHA256 == (
        "8dc859867939fd206ec7b57e26807f4edbc9c977b94ceaa01939a71ff44fb1c3"
    )
    assert CANONICAL_TEMPORAL_CONTRACT_V1_SHA256 == (
        "4a1c2dc346fc86dbc0c0f50e6dfd2d20930aa65b7fa04cce3b5d55316c4a52fb"
    )


def test_temporal_contract_digest_rejects_each_immutable_temporal_field() -> None:
    for field in (
        "selected_range_operator",
        "interval_containment_operator",
        "evaluation_time_policy",
        "all_version_requirement",
    ):
        payload = _matrix()
        payload["rows"][0][field] = "altered"
        with pytest.raises(PrototypeContractError, match="temporal contract"):
            validate_proof_matrix(payload)


def test_evidence_requires_exact_ordered_native_identity_and_join_chain() -> None:
    with pytest.raises(PrototypeContractError, match="native identity"):
        replace(_bundle(), native_identity_tuple=("session-1", "omp"))
    with pytest.raises(PrototypeContractError, match="join chain"):
        replace(_bundle(), join_chain=())
    with pytest.raises(PrototypeContractError, match="native identity"):
        replace(_bundle(), native_identity_tuple=("codex-cli", "session-1"))
    with pytest.raises(PrototypeContractError, match="native identity"):
        replace(_bundle(), native_identity_tuple=("omp", "other-session"))
    with pytest.raises(PrototypeContractError, match="anchor"):
        replace(
            _bundle(),
            join_chain=(JoinIdentityStep("native_session_id", "native_session_id"),),
        )


def test_evidence_canonicalization_respects_semantic_set_and_ordered_chain() -> None:
    original = _bundle()
    reordered_mapping = replace(
        original,
        canonical_schema={"token_counts": "integer", "native_session_id": "string"},
        event_id_inputs=tuple(
            {
                "event_id_ordinal": ordinal,
                "native_session_id": "session-1",
                "producer": "omp",
                "experiment_id": "E-Pipeline-1",
            }
            for ordinal in range(2)
        ),
    )
    reordered_allowlist = replace(
        original,
        privacy_allowlist=(
            "token_counts",
            "event_id_ordinal",
            "producer",
            "experiment_id",
            "native_session_id",
        ),
    )
    different_allowlist = replace(
        original,
        privacy_allowlist=frozenset(
            {
                "event_id",
                "event_id_ordinal",
                "experiment_id",
                "producer",
                "native_session_id",
                "token_counts",
            }
        ),
    )
    ordered_identity = replace(
        original,
        join_chain=(
            *original.join_chain,
            JoinIdentityStep("event_id", "event_id"),
        ),
    )
    reordered_identity = replace(
        original,
        join_chain=(
            original.join_chain[0],
            JoinIdentityStep("event_id", "event_id"),
            original.join_chain[1],
        ),
    )

    assert serialize_evidence_bundle(original) == serialize_evidence_bundle(reordered_mapping)
    assert hash_evidence_bundle(original) == hash_evidence_bundle(reordered_mapping)
    assert hash_evidence_bundle(original) == hash_evidence_bundle(reordered_allowlist)
    duplicate_allowlist = replace(
        original,
        privacy_allowlist=(
            "experiment_id",
            "producer",
            "native_session_id",
            "event_id_ordinal",
            "token_counts",
            "native_session_id",
        ),
    )
    assert hash_evidence_bundle(original) == hash_evidence_bundle(duplicate_allowlist)
    assert hash_evidence_bundle(original) != hash_evidence_bundle(different_allowlist)
    assert ordered_identity.content_hash() != reordered_identity.content_hash()
    with pytest.raises(PrototypeContractError, match="canonical schema"):
        replace(original, canonical_schema={"arbitrary": "string"})


def test_evidence_rejects_unsafe_schema_and_event_id_maps() -> None:
    with pytest.raises(PrototypeContractError, match="canonical schema"):
        replace(_bundle(), canonical_schema={"sample_text": "string"})
    with pytest.raises(PrototypeContractError, match="canonical schema"):
        replace(_bundle(), canonical_schema={"native_session_id": "arbitrary"})
    with pytest.raises(PrototypeContractError, match="event ID inputs"):
        replace(_bundle(), event_id_inputs={"native_session_id": "session-1"})
    with pytest.raises(PrototypeContractError, match="event ID inputs"):
        replace(
            _bundle(),
            event_id_inputs=(
                {
                    "experiment_id": "E-Pipeline-1",
                    "producer": "omp",
                    "native_session_id": "session-1",
                    "event_id_ordinal": "1",
                },
                {
                    "experiment_id": "E-Pipeline-1",
                    "producer": "omp",
                    "native_session_id": "session-1",
                    "event_id_ordinal": 1,
                },
            ),
        )


def test_evidence_requires_population_conserving_unique_matching_event_ids() -> None:
    with pytest.raises(PrototypeContractError, match="event ID populations"):
        replace(_bundle(), local_outbox_event_ids=("event-1",), remote_event_ids=("event-1",))
    with pytest.raises(PrototypeContractError, match="match exactly"):
        replace(_bundle(), remote_event_ids=("event-2", "event-1"))
    with pytest.raises(PrototypeContractError, match="event IDs must be unique"):
        replace(
            _bundle(),
            local_outbox_event_ids=("event-1", "event-1"),
            remote_event_ids=("event-1", "event-1"),
        )
    with pytest.raises(PrototypeContractError, match="input must equal"):
        replace(_bundle(), accepted_count=1)
    single_event_input = _bundle().event_id_inputs[:1]
    single_event_id = (derive_event_id(single_event_input[0]),)
    with pytest.raises(PrototypeContractError, match="output must equal"):
        replace(
            _bundle(),
            output_count=1,
            event_id_inputs=single_event_input,
            local_outbox_event_ids=single_event_id,
            remote_event_ids=single_event_id,
        )
    with pytest.raises(PrototypeContractError, match="conservation equation"):
        replace(_bundle(), conservation_equation="4 = 2 + 1 + 1; 2 = 1")
    with pytest.raises(PrototypeContractError, match="rejected counts"):
        replace(_bundle(), rejected_count_by_reason={"missing_timestamp": -1})


def test_event_id_inputs_bind_every_output_to_exact_derived_identity() -> None:
    bundle = _bundle()
    with pytest.raises(PrototypeContractError, match="conserve the output"):
        replace(bundle, event_id_inputs=bundle.event_id_inputs[:1])
    with pytest.raises(PrototypeContractError, match="exact ordinal"):
        replace(
            bundle,
            event_id_inputs=(
                {**bundle.event_id_inputs[0], "event_id_ordinal": 1},
                bundle.event_id_inputs[1],
            ),
        )
    with pytest.raises(PrototypeContractError, match="exact ordinal"):
        replace(
            bundle,
            event_id_inputs=(
                {**bundle.event_id_inputs[0], "event_id_ordinal": 0.0},
                bundle.event_id_inputs[1],
            ),
        )
    with pytest.raises(PrototypeContractError, match="bind bundle identity"):
        replace(
            bundle,
            event_id_inputs=(
                {**bundle.event_id_inputs[0], "native_session_id": "other"},
                bundle.event_id_inputs[1],
            ),
        )
    with pytest.raises(PrototypeContractError, match="derived input hashes"):
        replace(
            bundle,
            local_outbox_event_ids=("arbitrary-1", "arbitrary-2"),
            remote_event_ids=("arbitrary-1", "arbitrary-2"),
        )


def test_proven_requires_fresh_real_provenance() -> None:
    for provenance in (EvidenceProvenance.RETAINED, EvidenceProvenance.SYNTHETIC):
        with pytest.raises(PrototypeContractError, match="fresh-real"):
            replace(_bundle(), provenance=provenance)
    assert replace(
        _bundle(),
        result=ExperimentResult.BLOCKED,
        provenance=EvidenceProvenance.RETAINED,
    )
    assert replace(_bundle(), result=ExperimentResult.FAILED)
    assert replace(_bundle(), result=ExperimentResult.NOT_APPLICABLE)


def test_inventory_is_exact_bound_to_allowlist_and_recursively_safe() -> None:
    with pytest.raises(PrototypeContractError, match="exact schema"):
        replace(_bundle(), raw_field_inventory=({"name": "native_session_id"},))
    with pytest.raises(PrototypeContractError, match="exact schema"):
        replace(
            _bundle(),
            raw_field_inventory=({**_inventory("native_session_id"), "sample_value": "raw"},),
        )
    with pytest.raises(PrototypeContractError, match="privacy allowlisted"):
        replace(_bundle(), raw_field_inventory=(_inventory("other"),))
    with pytest.raises(PrototypeContractError, match="privacy allowlist"):
        replace(_bundle(), privacy_allowlist=frozenset({"APIKey"}))
    with pytest.raises(PrototypeContractError, match="canonical schema"):
        replace(_bundle(), canonical_schema={"nested": {"payload": "arbitrary"}})
    with pytest.raises(PrototypeContractError, match="prohibited content"):
        replace(_bundle(), recommendation="use bearer sk-secret")
    with pytest.raises(PrototypeContractError, match="privacy allowlist"):
        replace(_bundle(), privacy_allowlist=frozenset({"apiKey"}))
    with pytest.raises(PrototypeContractError, match="privacy allowlist"):
        replace(_bundle(), privacy_allowlist=frozenset({"commandOutput"}))


def test_query_and_oracle_are_structured_and_scalar_only() -> None:
    with pytest.raises(PrototypeContractError, match="Appendix query row ID"):
        AppendixQueryReference("A44", AppendixQueryId.POPULATION_COUNT, {})
    assert AppendixQueryReference(
        "A01",
        AppendixQueryId.ACCEPTED_COUNT,
        {AppendixQueryParameter.EXPECTED_COUNT: 2},
    )
    with pytest.raises(PrototypeContractError, match="allowlisted"):
        AppendixQueryReference(
            "A01",
            cast(AppendixQueryId, "unallowlisted-query"),
            {},
        )
    with pytest.raises(PrototypeContractError, match="named numeric"):
        AppendixQueryReference(
            "A01",
            AppendixQueryId.POPULATION_COUNT,
            cast(
                Mapping[AppendixQueryParameter, QueryParameterValue],
                {"query": "SELECT *"},
            ),
        )
    with pytest.raises(PrototypeContractError, match="numeric, boolean, or null"):
        CalculationResult(
            CalculationResultState.COMPUTED,
            cast(Mapping[str, CalculationValue], {"payload": {"count": 2}}),
        )
    with pytest.raises(PrototypeContractError, match="non-computed"):
        CalculationResult(CalculationResultState.NO_DATA, {"count": 0})
    with pytest.raises(PrototypeContractError, match="allowlisted reference"):
        replace(
            _bundle(),
            appendix_calculation_query=cast(AppendixQueryReference, "SELECT count()"),
        )
    with pytest.raises(PrototypeContractError, match="remote and oracle equality"):
        replace(
            _bundle(),
            remote_result=CalculationResult(
                CalculationResultState.COMPUTED,
                {"count": 2.0, "complete": True, "missing": None},
            ),
        )
    with pytest.raises(PrototypeContractError, match="structured"):
        replace(_bundle(), oracle_result=cast(CalculationResult, {"count": 2}))
    with pytest.raises(
        PrototypeContractError, match="remote and oracle results must be structured"
    ):
        replace(_bundle(), remote_result=cast(CalculationResult, {"count": 2}))


def test_evidence_requires_typed_results_cases_joins_and_bounded_times() -> None:
    with pytest.raises(PrototypeContractError, match="full adverse-case"):
        replace(_bundle(), edge_cases=("endpoint",))
    with pytest.raises(PrototypeContractError, match="allowlisted semantic"):
        replace(_bundle(), edge_cases=(*_bundle().edge_cases, "transcript"))
    with pytest.raises(PrototypeContractError, match="authoritative identity"):
        JoinIdentityStep("cwd", "id")
    with pytest.raises(PrototypeContractError, match="RFC 3339"):
        replace(_bundle(), start_time="not-a-time")
    with pytest.raises(PrototypeContractError, match="ordered bounded"):
        replace(_bundle(), end_time="2026-09-01T00:00:00Z")
    with pytest.raises(PrototypeContractError, match="remote and oracle equality"):
        replace(
            _bundle(),
            remote_result=CalculationResult(CalculationResultState.COMPUTED, {"count": 1}),
        )


def test_proven_requires_computed_exactly_typed_results() -> None:
    bundle = _bundle()
    for state in (CalculationResultState.NO_DATA, CalculationResultState.QUERY_FAILED):
        result = CalculationResult(state, {})
        with pytest.raises(PrototypeContractError, match="computed remote and oracle"):
            replace(bundle, remote_result=result, oracle_result=result)
    with pytest.raises(PrototypeContractError, match="remote and oracle equality"):
        replace(
            bundle,
            oracle_result=CalculationResult(
                CalculationResultState.COMPUTED,
                {"count": 2.0, "complete": True, "missing": None},
            ),
        )


def test_matrix_evidence_binds_privacy_references_and_time_contract() -> None:
    payload = _matrix()
    row = payload["rows"][0]
    row["result"] = ExperimentResult.PROVEN.value
    row["blocked_boundary"] = None
    event_id_inputs = tuple(
        {
            "experiment_id": row["experiment_id"],
            "producer": row["producer"],
            "native_session_id": "session-1",
            "event_id_ordinal": ordinal,
        }
        for ordinal in range(2)
    )
    event_ids = tuple(derive_event_id(event_input) for event_input in event_id_inputs)
    evidence = replace(
        _bundle(),
        experiment_id=row["experiment_id"],
        producer=row["producer"],
        event_id_inputs=event_id_inputs,
        local_outbox_event_ids=event_ids,
        remote_event_ids=event_ids,
        selected_range_membership_operator=row["selected_range_operator"],
        interval_containment_operator=row["interval_containment_operator"],
        policy_identity=row["evaluation_policy_id"],
        version_policy_identity=row["version_policy_id"],
        all_version_requirement=row["all_version_requirement"],
    )
    row["evidence_bundle"] = json.loads(serialize_evidence_bundle(evidence))
    validate_proof_matrix(payload)
    for mutate, error in (
        (lambda evidence: evidence.update({"policy_identity": "wrong"}), "policies"),
        (lambda evidence: evidence.update({"version_policy_identity": "wrong"}), "policies"),
        (lambda evidence: evidence.update({"all_version_requirement": "wrong"}), "policies"),
    ):
        invalid = copy.deepcopy(payload)
        mutate(invalid["rows"][0]["evidence_bundle"])
        with pytest.raises(PrototypeContractError, match=error):
            validate_proof_matrix(invalid)

    for field, value, error in (
        ("remote_query_reference_id", "other", "matrix references"),
        (
            "privacy_allowlist",
            ["native_session_id", "token_counts", "outside"],
            "matrix allowlist subset",
        ),
    ):
        invalid = copy.deepcopy(payload)
        invalid["rows"][0]["evidence_bundle"][field] = value
        with pytest.raises(PrototypeContractError, match=error):
            validate_proof_matrix(invalid)
    invalid = copy.deepcopy(payload)
    invalid["rows"][0]["evidence_bundle"]["appendix_calculation_query"]["row_id"] = "A02"
    with pytest.raises(PrototypeContractError, match="query row ID"):
        validate_proof_matrix(invalid)
    invalid = copy.deepcopy(payload)
    invalid["rows"][0]["evidence_bundle"]["selected_range_membership_operator"] = "wrong"
    with pytest.raises(PrototypeContractError, match="time operators"):
        validate_proof_matrix(invalid)


def test_matrix_metadata_and_result_rules_remain_fail_closed() -> None:
    unsafe = _matrix()
    unsafe["cleanup_boundary"] = {"namespace": EXPERIMENT_NAMESPACE, "run_id": "run-*"}
    with pytest.raises(PrototypeContractError, match=r"evidence_bundle\.run_id"):
        validate_proof_matrix(unsafe)
    for cleanup in (
        {},
        {"namespace": EXPERIMENT_NAMESPACE},
        {"namespace": EXPERIMENT_NAMESPACE, "run_id": CLEANUP_RUN_ID_SELECTOR, "extra": "x"},
        {"namespace": EXPERIMENT_NAMESPACE, "run_id": "run-1"},
    ):
        invalid = _matrix()
        invalid["cleanup_boundary"] = cleanup
        with pytest.raises(PrototypeContractError, match=r"evidence_bundle\.run_id"):
            validate_proof_matrix(invalid)

    proven = _matrix()
    for row in proven["rows"]:
        row["result"] = ExperimentResult.PROVEN.value
        row["evidence_bundle"] = None
        row["blocked_boundary"] = None
    with pytest.raises(PrototypeContractError, match="structured evidence"):
        validate_proof_matrix(proven)


def test_matrix_references_are_typed_deterministic_and_declarative() -> None:
    for row in _matrix()["rows"]:
        for key, suffix in (("remote_query", "remote-query"), ("oracle", "oracle")):
            assert row[key]["reference_id"] == f"{row['row_id']}.{row['producer']}.{suffix}"

    mutations = (
        ("reference_id", "wrong", "deterministic"),
        ("experiment_id", "E-other", "decisive"),
        ("prerequisite_experiment_ids", ["E-other", "E-other"], "unique"),
        ("description", "SELECT * FROM events", "SQL-free"),
    )
    for key, value, error in mutations:
        payload = _matrix()
        payload["rows"][0]["remote_query"][key] = value
        with pytest.raises(PrototypeContractError, match=error):
            validate_proof_matrix(payload)

    payload = _matrix()
    row = payload["rows"][0]
    row["remote_query"]["prerequisite_experiment_ids"] = [row["experiment_id"]]
    with pytest.raises(PrototypeContractError, match="non-self"):
        validate_proof_matrix(payload)

    declarative = _matrix()
    declarative["rows"][0]["remote_query"]["description"] = (
        "Capability-absence audit; no remote query is executed."
    )
    validate_proof_matrix(declarative)

    for statement in (
        "INSERT INTO events VALUES (1)",
        "UPDATE events SET state = 'complete'",
        "DELETE FROM events",
        "MERGE INTO events USING source ON events.id = source.id",
    ):
        payload = _matrix()
        payload["rows"][0]["remote_query"]["description"] = statement
        with pytest.raises(PrototypeContractError, match="SQL-free"):
            validate_proof_matrix(payload)


def test_matrix_rejects_unknown_registry_references_and_changed_unsupported_boundaries() -> None:
    invented = _matrix()
    invented["rows"][0]["remote_query"]["prerequisite_experiment_ids"] = ["E-invented"]
    with pytest.raises(PrototypeContractError, match="registry experiment"):
        validate_proof_matrix(invented)
    unknown_decisive = _matrix()
    row = unknown_decisive["rows"][0]
    row["experiment_id"] = "E-invented"
    row["blocked_boundary"]["experiment_id"] = "E-invented"
    for reference_kind in ("remote_query", "oracle"):
        row[reference_kind]["experiment_id"] = "E-invented"
    with pytest.raises(PrototypeContractError, match="registry experiment"):
        validate_proof_matrix(unknown_decisive)
    for unsupported in ([], _matrix()["unsupported_boundaries"][:-1]):
        invalid = _matrix()
        invalid["unsupported_boundaries"] = unsupported
        with pytest.raises(PrototypeContractError, match="unsupported boundaries"):
            validate_proof_matrix(invalid)


def test_matrix_result_states_require_validated_structured_evidence() -> None:
    payload = _matrix()
    row = payload["rows"][0]
    row["result"] = ExperimentResult.PROVEN.value
    row["blocked_boundary"] = None
    row["evidence_bundle"] = json.loads(
        serialize_evidence_bundle(
            replace(
                _bundle(),
                experiment_id=row["experiment_id"],
                producer=row["producer"],
                selected_range_membership_operator=row["selected_range_operator"],
                interval_containment_operator=row["interval_containment_operator"],
            )
        )
    )
    validate_proof_matrix(payload)
    bare = copy.deepcopy(payload)
    bare["rows"][0]["evidence_bundle"] = "not proof"
    with pytest.raises(PrototypeContractError, match="structured"):
        validate_proof_matrix(bare)
    mismatch = copy.deepcopy(payload)
    evidence_bundle = mismatch["rows"][0]["evidence_bundle"]
    evidence_bundle["experiment_id"] = "E-other"
    for event_input in evidence_bundle["event_id_inputs"]:
        event_input["experiment_id"] = "E-other"
    event_ids = [derive_event_id(event_input) for event_input in evidence_bundle["event_id_inputs"]]
    evidence_bundle["local_outbox_event_ids"] = event_ids
    evidence_bundle["remote_event_ids"] = event_ids
    with pytest.raises(PrototypeContractError, match="decisive row coordinates"):
        validate_proof_matrix(mismatch)
    not_applicable = _matrix()
    not_applicable["rows"][0]["result"] = ExperimentResult.NOT_APPLICABLE.value
    not_applicable["rows"][0]["blocked_boundary"] = None
    not_applicable["rows"][0]["evidence_bundle"] = {}
    with pytest.raises(PrototypeContractError, match="cannot make a proof claim"):
        validate_proof_matrix(not_applicable)


def test_matrix_requires_typed_blocked_boundaries_and_null_elsewhere() -> None:
    blocked = _matrix()
    row = blocked["rows"][0]
    row["blocked_boundary"] = {
        "missing_boundary": "Missing authoritative session end.",
        "owner": "unknown",
        "owner_surface": "native hook",
        "experiment_id": row["experiment_id"],
    }
    with pytest.raises(PrototypeContractError, match="owner"):
        validate_proof_matrix(blocked)
    non_blocked = _matrix()
    row = non_blocked["rows"][1]
    row["result"] = ExperimentResult.NOT_APPLICABLE.value
    row["blocked_boundary"] = {
        "missing_boundary": "not relevant",
        "owner": "producer",
        "owner_surface": "native hook",
        "experiment_id": row["experiment_id"],
    }
    with pytest.raises(PrototypeContractError, match="null blocked boundary"):
        validate_proof_matrix(non_blocked)


def test_matrix_result_transitions_remain_fail_closed() -> None:
    failed = _matrix()
    row = failed["rows"][0]
    row["result"] = ExperimentResult.FAILED.value
    row["blocked_boundary"] = None
    row["evidence_bundle"] = json.loads(
        serialize_evidence_bundle(
            replace(
                _bundle(),
                experiment_id=row["experiment_id"],
                producer=row["producer"],
                result=ExperimentResult.FAILED,
                selected_range_membership_operator=row["selected_range_operator"],
                interval_containment_operator=row["interval_containment_operator"],
            )
        )
    )
    validate_proof_matrix(failed)

    blocked = _matrix()
    blocked["rows"][0]["normative_capability_reason"] = ""
    with pytest.raises(PrototypeContractError, match="missing boundary"):
        validate_proof_matrix(blocked)

    retained_blocked = _matrix()
    row = retained_blocked["rows"][0]
    row["evidence_bundle"] = json.loads(
        serialize_evidence_bundle(
            replace(
                _bundle(),
                experiment_id=row["experiment_id"],
                producer=row["producer"],
                result=ExperimentResult.BLOCKED,
                provenance=EvidenceProvenance.RETAINED,
                selected_range_membership_operator=row["selected_range_operator"],
                interval_containment_operator=row["interval_containment_operator"],
            )
        )
    )
    validate_proof_matrix(retained_blocked)
