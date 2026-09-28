import { expect, test } from "bun:test";
import { buildRegistry, prepareRegistry } from "./registry";

const docs = `${import.meta.dir}/../../docs`;
const measureDoc = await Bun.file(`${docs}/dashboard-measure-v2.md`).text();
const matrix = JSON.parse(
  await Bun.file(`${docs}/dashboard-prototype-proof-matrix.json`).text(),
);
const proofRegister = await Bun.file(
  `${docs}/dashboard-metric-proof-register.md`,
).text();
const centralBindingSource = `
import json
import runpy
fixture = runpy.run_path(${JSON.stringify(`${import.meta.dir}/../../tests/test_prototype_experiments.py`)})["_central_bundle"]()
print(json.dumps({
  "widget_id": "p1-snapshot",
  "measure_id": "P1",
  "dependency_id": "E-Pipeline-1",
  "central_identity": {
    "runtime_instance_id": fixture.runtime_instance_id,
    "scan_run_id": fixture.scan_run_id,
    "database_identity": fixture.database_identity,
  },
  "qualification": {
    "provenance": "fresh-real",
    "result": "Proven",
    "deployment_fingerprint": fixture.deployment_fingerprint,
    "calculation_implementation_id": fixture.calculation_implementation_id,
    "calculation_implementation_sha256": fixture.calculation_implementation_sha256,
    "projection_implementation_id": fixture.projection_implementation_id,
    "projection_implementation_sha256": fixture.projection_implementation_sha256,
    "evidence_bundle": json.loads(fixture.canonical_json()),
  },
}))
`;
const centralFixtureProcess = Bun.spawn(
  ["uv", "run", "python", "-c", centralBindingSource],
  { cwd: `${import.meta.dir}/../..`, stdout: "pipe", stderr: "pipe" },
);
const [centralBindingOutput, centralBindingError, centralBindingExitCode] =
  await Promise.all([
    new Response(centralFixtureProcess.stdout).text(),
    new Response(centralFixtureProcess.stderr).text(),
    centralFixtureProcess.exited,
  ]);
if (centralBindingExitCode !== 0)
  throw new Error(
    `Unable to construct the authoritative central test binding: ${centralBindingError}`,
  );
const centralBinding = JSON.parse(centralBindingOutput);

test("rejects incomplete and conflicting matrix identities", async () => {
  const incomplete = structuredClone(matrix);
  incomplete.rows.pop();
  await expect(
    buildRegistry(measureDoc, incomplete, proofRegister),
  ).rejects.toThrow("129 producer-row obligations");

  const conflicting = structuredClone(matrix);
  conflicting.rows[1].producer = conflicting.rows[0].producer;
  await expect(
    buildRegistry(measureDoc, conflicting, proofRegister),
  ).rejects.toThrow("conflicting producer-row identities");
});

test("accepts canonical static exclusions and rejects malformed status metadata", async () => {
  const registry = await buildRegistry(measureDoc, matrix, proofRegister);
  expect(
    registry.controls.find(
      (row) => row.rowId === "A24" && row.producer === "omp",
    )?.status,
  ).toBe("Not applicable");

  const malformed = structuredClone(matrix);
  malformed.rows.find(
    (row: { row_id: string; producer: string }) =>
      row.row_id === "A24" && row.producer === "omp",
  ).blocked_boundary = {
    missing_boundary: "invented",
    owner: "x",
    experiment_id: "x",
  };
  await expect(
    buildRegistry(measureDoc, malformed, proofRegister),
  ).rejects.toThrow("Not applicable proof matrix row has a blocker");
});

test("propagates independent populations instead of collapsing them into related Appendix rows", async () => {
  const registry = await buildRegistry(measureDoc, matrix, proofRegister);
  const p10Detail = registry.widgets.pipeline.find(
    ({ widget }) => widget.id === "p10-delivery-detail",
  )!;
  const tracePressure = registry.widgets.usage.find(
    ({ widget }) => widget.id === "m2-trace-pressure",
  )!;
  const m15 = registry.widgets.recurrence.find(
    ({ widget }) => widget.id === "m15-recurrence",
  )!;

  expect(p10Detail.widget.matrixRowIds).not.toContain("E-Pipeline-5");
  expect(p10Detail.gate.proofs.map((proof) => proof.independentGate)).toContain(
    "E-Pipeline-5",
  );
  expect(
    tracePressure.gate.proofs.map((proof) => proof.independentGate),
  ).toContain("M2-trace-accounted");
  expect(m15.gate.proofs.map((proof) => proof.independentGate)).toContain(
    "M15-intervention-lineage",
  );
  expect(m15.gate.proofs.map((proof) => proof.rowId)).not.toContain("A43");
  expect(p10Detail.gate.status).toBe("Blocked");
});

test("preserves canonical inventory, manifest geometry, and static producer applicability", async () => {
  const registry = await buildRegistry(measureDoc, matrix, proofRegister);
  const widgets = Object.values(registry.widgets).flat();
  const controls = registry.controls as Array<{
    rowId: string;
    producer: string;
    status: string;
  }>;

  expect(registry.measures as Array<{ id: string }>).toHaveLength(38);
  expect(widgets).toHaveLength(55);
  expect(
    widgets.filter(({ widget }) => widget.section === "Supporting evidence"),
  ).toHaveLength(5);
  expect(
    widgets.find(({ widget }) => widget.id === "r3-phases")?.widget.layout,
  ).toEqual([0, 16, 12, 6]);
  expect(controls).toHaveLength(129);
  expect(
    controls.find((row) => row.rowId === "A24" && row.producer === "omp")
      ?.status,
  ).toBe("Not applicable");
  expect(
    controls.find((row) => row.rowId === "A34" && row.producer === "omp")
      ?.status,
  ).toBe("Not applicable");
});

test("publishes row-specific missing-data metadata without private evidence references", async () => {
  const registry = await buildRegistry(measureDoc, matrix, proofRegister);
  const control = registry.controls.find(
    (row) => row.rowId === "A01" && row.producer === "omp",
  )!;
  const widget = registry.widgets.pipeline.find(
    ({ widget }) => widget.id === "p5-correlation",
  )!;

  expect(control.gate).toBe("Blocked");
  expect(control.owner).toBe("application");
  expect(control.ownerSurface).toBe("OMP installed producer surface");
  expect(control.proposedEventFamily).toBe(
    "dashboard_prototype.pipeline_snapshot.v1",
  );
  expect(control.evaluationPolicyId).toBe("A01.evaluation-policy-v1");
  expect(control.versionPolicyId).toBe("A01.version-policy-v1");
  expect(control.projectionState).toBe("Production unavailable");
  if (typeof control.blocker !== "string")
    throw new Error("Blocked coverage must identify its missing authority.");
  expect(control.blocker).toContain("[private evidence reference]");
  expect(control.blocker).not.toContain("experiments/");
  expect(widget.gate.reasons).toContain(control.blocker);
  expect(widget.gate.reasons).not.toContain(control.capabilityReason);
});

type WidgetInventory = Record<
  string,
  {
    widget: {
      id: string;
      measureIds: string[];
      matrixRowIds: string[];
      independentGates: { id: string; measureId: string }[];
    };
  }[]
>;

function completeApplicationBindings(
  candidate: typeof matrix,
  widgets: WidgetInventory,
) {
  const requirements = new Map<
    string,
    {
      widgetId: string;
      measureId: string;
      dependencyId: string;
      producer: string;
    }
  >();
  for (const { widget } of Object.values(widgets).flat())
    for (const producer of ["omp", "codex-cli", "codex-app-server"])
      for (const dependencyId of [
        ...widget.matrixRowIds,
        ...widget.independentGates
          .filter((gate) => gate.id.startsWith("E-"))
          .map((gate) => gate.id),
      ]) {
        const measureId =
          widget.independentGates.find((gate) => gate.id === dependencyId)
            ?.measureId ?? widget.measureIds[0];
        const requirement = {
          widgetId: widget.id,
          measureId,
          dependencyId,
          producer,
        };
        requirements.set(
          `${requirement.widgetId}:${requirement.measureId}:${requirement.dependencyId}:${producer}`,
          requirement,
        );
      }
  const rows = new Map(
    candidate.rows.map((row: Record<string, any>) => [
      `${row.row_id}:${row.producer}`,
      row,
    ]),
  );
  return Array.from(requirements.values()).map((requirement, index) => {
    const row =
      rows.get(`${requirement.dependencyId}:${requirement.producer}`) ??
      candidate.rows.find(
        (candidate: Record<string, any>) =>
          candidate.row_id === "A01" &&
          candidate.producer === requirement.producer,
      )!;
    const experimentId =
      row.row_id === requirement.dependencyId
        ? row.experiment_id
        : requirement.dependencyId;
    const sessionId = `session-${index}`;
    const nativeEventId = `opaque-native-${index}`;
    const witness = {
      kind: "CanonicalActivityVersionEvent",
      activity_id: `activity-${index}`,
      version: 1,
      payload_schema_version: 1,
      event_name: "dashboard.metric",
    };
    const resultEventId = new Bun.CryptoHasher("sha256")
      .update(`${witness.activity_id}\x1f1\x1f1\x1fdashboard.metric`)
      .digest("hex");
    const outboxEventId = new Bun.CryptoHasher("sha256")
      .update(
        JSON.stringify({
          event_id_ordinal: 0,
          experiment_id: experimentId,
          native_session_id: sessionId,
          producer: requirement.producer,
        }),
      )
      .digest("hex");
    const privacyAllowlist = [
      "event_id_ordinal",
      "experiment_id",
      "native_session_id",
      "producer",
      "token_counts",
    ];
    const typedResult = {
      state: "computed",
      values: { count: 0, complete: false, missing: null },
    };
    const evidenceBundle = {
      experiment_namespace: "agent-introspection.dashboard-prototype.v1",
      run_id: "run-20260901",
      experiment_id: experimentId,
      hypothesis: "The reducer conserves bounded input population.",
      producer: requirement.producer,
      native_session_id: sessionId,
      surface: "native session events",
      installed_version: "1.0",
      capability_state: "supported",
      provenance: "fresh-real",
      start_time: "2026-09-01T00:00:00Z",
      end_time: "2026-09-01T00:01:00Z",
      source_extraction_boundary: "fresh bounded session",
      selected_range_membership_operator: row.selected_range_operator,
      interval_containment_operator: row.interval_containment_operator,
      evaluation_time: "2026-09-01T00:01:00Z",
      policy_identity: row.evaluation_policy_id,
      version_policy_identity: row.version_policy_id,
      all_version_requirement: row.all_version_requirement,
      privacy_allowlist: privacyAllowlist,
      raw_field_inventory: [
        {
          name: "native_session_id",
          type: "integer",
          presence: "present",
          cardinality: "one",
          owner: "producer",
        },
        {
          name: "token_counts",
          type: "integer",
          presence: "present",
          cardinality: "one",
          owner: "producer",
        },
      ],
      native_identity_tuple: [requirement.producer, sessionId],
      join_chain: [
        {
          source: "native_identity_tuple",
          identity_field: "producer,native_session_id",
        },
        { source: "stable_identity", identity_field: "canonical_session_id" },
      ],
      canonical_schema: {
        native_session_id: "string",
        token_counts: "integer",
      },
      event_id_inputs: [
        {
          experiment_id: experimentId,
          producer: requirement.producer,
          native_session_id: sessionId,
          event_id_ordinal: 0,
        },
      ],
      input_count: 1,
      accepted_count: 1,
      rejected_count_by_reason: {},
      duplicate_count: 0,
      output_count: 1,
      conservation_equation: "1 = 1 + 0 + 0; 1 = 1",
      local_outbox_event_ids: [outboxEventId],
      remote_event_ids: [outboxEventId],
      appendix_calculation_query: {
        row_id: row.row_id,
        query_id: "population-count-v1",
        bound_parameters: { start_epoch: 1, end_epoch: 2 },
      },
      remote_query_reference_id: row.remote_query.reference_id,
      remote_result: typedResult,
      oracle_reference_id: row.oracle.reference_id,
      oracle_result: typedResult,
      edge_cases: [
        "endpoint",
        "negative",
        "missing",
        "conflicting",
        "duplicate",
        "out-of-order",
        "version",
        "clock-skew",
      ],
      privacy_review: "allowlisted fields only",
      result: "Proven",
      recommendation:
        "Do not cut over before the bounded experiment completes.",
      unresolved_dependency: null,
    };
    return {
      widget_id: requirement.widgetId,
      measure_id: requirement.measureId,
      dependency_id: requirement.dependencyId,
      producer: requirement.producer,
      native_identity: {
        producer: requirement.producer,
        native_session_id: sessionId,
        native_event_id: nativeEventId,
      },
      calculation: {
        state: "computed",
        query_reference_id: row.remote_query.reference_id,
        result_event_id: resultEventId,
      },
      projection: {
        state: "computed",
        projection_id: "pipeline-observations-v1",
        result_event_id: resultEventId,
      },
      qualification: {
        provenance: "fresh-real",
        result: "Proven",
        experiment_id: experimentId,
        deployment_fingerprint: "a".repeat(64),
        calculation_implementation_id: "agent-introspection.pipeline-dashboard",
        calculation_implementation_sha256: "b".repeat(64),
        projection_implementation_id:
          "agent-introspection.pipeline-observations",
        projection_implementation_sha256: "c".repeat(64),
        projection_event_witness: witness,
        raw_evidence: {
          bounded_start_ns: "1",
          bounded_end_ns: "2",
          opaque_native_event_ids: [nativeEventId],
          native_identity_tuple: [requirement.producer, sessionId],
        },
        selected_range_operator: row.selected_range_operator,
        interval_containment_operator: row.interval_containment_operator,
        evaluation_policy_id: row.evaluation_policy_id,
        version_policy_id: row.version_policy_id,
        all_version_requirement: row.all_version_requirement,
        join_chain: evidenceBundle.join_chain,
        source_event_ids: [nativeEventId],
        output_event_ids: [resultEventId],
        remote_event_ids: [resultEventId],
        oracle_event_ids: [resultEventId],
        remote_result: typedResult,
        oracle_result: typedResult,
        adverse_cases: evidenceBundle.edge_cases,
        privacy_allowlist: privacyAllowlist,
        evidence_bundle: evidenceBundle,
      },
    };
  });
}

test("does not let native producer evidence promote central scan widgets", async () => {
  const candidate = structuredClone(matrix);
  const baseline = await buildRegistry(measureDoc, candidate, proofRegister);
  const bindings = completeApplicationBindings(candidate, {
    pipeline: baseline.widgets.pipeline.filter(
      ({ widget }) => widget.id === "p1-snapshot",
    ),
  });
  for (const row of candidate.rows.filter((row: { row_id: string }) =>
    ["A01", "A02"].includes(row.row_id),
  )) {
    const binding = bindings.find(
      (candidate) =>
        candidate.dependency_id === row.row_id &&
        candidate.producer === row.producer,
    )!;
    row.result = "Proven";
    row.evidence_bundle = binding.qualification.evidence_bundle;
    row.blocked_boundary = null;
  }
  candidate.application_evidence = { schema_version: 1, bindings };
  const candidateRegister = proofRegister
    .split("\n")
    .map((line) => {
      const cells = line.split("|");
      if (["A01", "A02"].includes(cells[1]?.trim()))
        for (const column of [4, 5, 6]) cells[column] = " Proven ";
      return cells.join("|");
    })
    .join("\n");
  const registry = await buildRegistry(
    measureDoc,
    candidate,
    candidateRegister,
    {
      calculationId: "agent-introspection.pipeline-dashboard",
      calculationSha256: "b".repeat(64),
      deployments: [
        {
          fingerprint: "a".repeat(64),
          projectionId: "agent-introspection.pipeline-observations",
          projectionSha256: "c".repeat(64),
        },
      ],
    },
  );
  expect(
    registry.widgets.pipeline.find(({ widget }) => widget.id === "p1-snapshot")
      ?.gate.status,
  ).toBe("Blocked");
});

test("qualifies central bindings independently and only for their matched report", async () => {
  const candidate = structuredClone(matrix);
  candidate.application_evidence = {
    schema_version: 1,
    bindings: [centralBinding],
  };
  const implementation = {
    calculationId: "agent-introspection.pipeline-dashboard" as const,
    calculationSha256: "b".repeat(64),
    deployments: [
      {
        fingerprint: "a".repeat(64),
        projectionId: "agent-introspection.pipeline-observations" as const,
        projectionSha256: "c".repeat(64),
      },
    ],
  };
  const qualified = await buildRegistry(
    measureDoc,
    candidate,
    proofRegister,
    implementation,
  );
  const snapshot = qualified.widgets.pipeline.find(
    ({ widget }) => widget.id === "p1-snapshot",
  )!;
  expect(snapshot.gate.status).toBe("Data");
  expect(snapshot.gate.proofs[0].producer).toBe(
    "application evidence validator",
  );
  expect(
    candidate.rows.find(
      (row: { row_id: string; producer: string }) =>
        row.row_id === "A01" && row.producer === "codex-app-server",
    )?.result,
  ).toBe("Blocked");
  const stale = await buildRegistry(measureDoc, candidate, proofRegister, {
    ...implementation,
    calculationSha256: "e".repeat(64),
  });
  expect(
    stale.widgets.pipeline.find(({ widget }) => widget.id === "p1-snapshot")
      ?.gate.status,
  ).toBe("Blocked");
  const mixed = await buildRegistry(measureDoc, candidate, proofRegister, {
    ...implementation,
    deployments: [
      ...implementation.deployments,
      { ...implementation.deployments[0], fingerprint: "f".repeat(64) },
    ],
  });
  expect(
    mixed.widgets.pipeline.find(({ widget }) => widget.id === "p1-snapshot")
      ?.gate.status,
  ).toBe("Blocked");
  const malformed = structuredClone(candidate);
  malformed.rows.pop();
  await expect(
    buildRegistry(measureDoc, malformed, proofRegister, implementation),
  ).rejects.toThrow("Proof matrix must contain 129 producer-row obligations.");
  const noncentral = structuredClone(candidate);
  noncentral.application_evidence.bindings[0].widget_id = "p5-correlation";
  await expect(
    buildRegistry(measureDoc, noncentral, proofRegister, implementation),
  ).rejects.toThrow(
    "Application qualification was rejected by the authoritative validator.",
  );
});

test("does not publish a reusable registry when preparation is cancelled", async () => {
  const controller = new AbortController();
  const preparing = prepareRegistry(controller.signal);
  queueMicrotask(() => controller.abort());

  await expect(preparing).rejects.toThrow(
    "Application qualification was rejected by the authoritative validator.",
  );

  const fresh = await prepareRegistry();
  expect(
    fresh
      .resolve()
      .widgets.pipeline.find(({ widget }) => widget.id === "p2-outcomes")?.gate
      .status,
  ).toBe("Blocked");
});

test("resolves prepared evidence independently for each implementation", async () => {
  const prepared = await prepareRegistry();
  const unqualified = prepared.resolve();
  const qualification = (
    matrix.application_evidence.bindings[0] as {
      qualification: {
        calculation_implementation_id: "agent-introspection.pipeline-dashboard";
        calculation_implementation_sha256: string;
        deployment_fingerprint: string;
        projection_implementation_id: "agent-introspection.pipeline-observations";
        projection_implementation_sha256: string;
      };
    }
  ).qualification;
  const implementation = {
    calculationId: qualification.calculation_implementation_id,
    calculationSha256: qualification.calculation_implementation_sha256,
    deployments: [
      {
        fingerprint: qualification.deployment_fingerprint,
        projectionId: qualification.projection_implementation_id,
        projectionSha256: qualification.projection_implementation_sha256,
      },
    ],
  };
  const qualified = prepared.resolve(implementation);
  const unqualifiedAfter = prepared.resolve();
  const empty = prepared.resolve({ ...implementation, deployments: [] });
  const divergent = prepared.resolve({
    ...implementation,
    deployments: [
      ...implementation.deployments,
      { ...implementation.deployments[0], fingerprint: "f".repeat(64) },
    ],
  });

  expect(
    unqualified.widgets.pipeline.find(
      ({ widget }) => widget.id === "p2-outcomes",
    )?.gate.status,
  ).toBe("Blocked");
  expect(
    qualified.widgets.pipeline.find(({ widget }) => widget.id === "p2-outcomes")
      ?.gate.status,
  ).toBe("Data");
  for (const registry of [unqualifiedAfter, empty, divergent])
    expect(
      registry.widgets.pipeline.find(
        ({ widget }) => widget.id === "p2-outcomes",
      )?.gate.status,
    ).toBe("Blocked");
});
