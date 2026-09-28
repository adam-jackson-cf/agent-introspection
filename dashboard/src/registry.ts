import {
  CANONICAL_ROUTES,
  type CentralApplicationEvidenceBinding,
  type CoverageContract,
  type Proof,
  type RouteDefinition,
  type RouteId,
  type PipelineImplementation,
  type WidgetDefinition,
  type WidgetResult,
} from "./contracts";

type MatrixStatus = Proof["status"];
type MatrixRow = {
  row_id: string;
  producer: string;
  route: string;
  control: string;
  fields_required: string;
  calculation_used: string;
  selected_range_operator: string;
  interval_containment_operator: string;
  evaluation_time_policy: string;
  evaluation_policy_id: string;
  all_version_requirement: string;
  version_policy_id: string;
  normative_capability_reason: string;
  proposed_event_family: string;
  projection_boundary: string;
  experiment_id: string;
  remote_query: { reference_id: string };
  oracle: { reference_id: string };
  result: MatrixStatus;
  evidence_bundle: unknown | null;
  blocked_boundary: {
    missing_boundary: string;
    owner: string;
    owner_surface: string;
    experiment_id: string;
  } | null;
};
type NativeEventWitness =
  | {
      kind: "DerivedEvent";
      scope: string;
      entity_id: string;
      entity_version: number;
      event_sequence: number;
      event_name: string;
    }
  | {
      kind: "CanonicalActivityVersionEvent";
      activity_id: string;
      version: number;
      payload_schema_version: number;
      event_name: string;
    };
type SharedQualification = {
  provenance: "fresh-real";
  result: "Proven";
  deployment_fingerprint: string;
  calculation_implementation_id: string;
  calculation_implementation_sha256: string;
  projection_implementation_id: string;
  projection_implementation_sha256: string;
  evidence_bundle: Record<string, unknown>;
};
type NativeApplicationEvidenceBinding = {
  widget_id: string;
  measure_id: string;
  dependency_id: string;
  producer: string;
  native_identity: {
    producer: string;
    native_session_id: string;
    native_event_id: string;
  };
  calculation: {
    state: "computed";
    query_reference_id: string;
    result_event_id: string;
  };
  projection: {
    state: "computed";
    projection_id: string;
    result_event_id: string;
  };
  qualification: SharedQualification & {
    experiment_id: string;
    projection_event_witness: NativeEventWitness;
    raw_evidence: {
      bounded_start_ns: string;
      bounded_end_ns: string;
      opaque_native_event_ids: string[];
      native_identity_tuple: [string, string];
    };
    selected_range_operator: string;
    interval_containment_operator: string;
    evaluation_policy_id: string;
    version_policy_id: string;
    all_version_requirement: string;
    join_chain: { source: string; identity_field: string }[];
    source_event_ids: string[];
    output_event_ids: string[];
    remote_event_ids: string[];
    oracle_event_ids: string[];
    remote_result: unknown;
    oracle_result: unknown;
    adverse_cases: string[];
    privacy_allowlist: string[];
  };
};
type ApplicationEvidenceBinding =
  | NativeApplicationEvidenceBinding
  | CentralApplicationEvidenceBinding;
type ApplicationEvidence = {
  schema_version: 1;
  bindings: ApplicationEvidenceBinding[];
};
type Matrix = {
  supported_producers: string[];
  rows: MatrixRow[];
  application_evidence: ApplicationEvidence;
};

export type MeasureMetadata = {
  id: string;
  title: string;
  question: string;
  calculation: string;
  cohort: string;
  dimensions: string;
  availability: string;
};

export type Registry = {
  routes: RouteDefinition[];
  widgets: Record<RouteId, WidgetResult[]>;
  measures: MeasureMetadata[];
  controls: CoverageContract[];
};

const applicationCalculationUnavailable =
  "Application calculation is unavailable: no verified typed production query/result contract exists.";

function redactPrivateReferences(value: string): string {
  return value
    .replace(
      /\b(?:[A-Za-z0-9_.-]+\/)*experiments\/dashboard_prototype\/evidence\/[^\s,;]+/g,
      "[private evidence reference]",
    )
    .replace(/\b(?:run_id|native_session_id|event_id)=\S+/g, "$1=[redacted]");
}
const producers = ["omp", "codex-cli", "codex-app-server"] as const;
const centralDependencies: Record<
  string,
  { measureId: string; dependencyId: string }
> = {
  "p1-snapshot": { measureId: "P1", dependencyId: "E-Pipeline-1" },
  "scan-evidence": { measureId: "P1", dependencyId: "E-Pipeline-1" },
  "p2-outcomes": { measureId: "P2", dependencyId: "E-Pipeline-2" },
  "p2-freshness": { measureId: "P2", dependencyId: "E-Pipeline-2" },
  "p3-duration": { measureId: "P3", dependencyId: "E-Pipeline-1" },
  "p3-rows": { measureId: "P3", dependencyId: "E-Pipeline-1" },
  "p3-throughput": { measureId: "P3", dependencyId: "E-Pipeline-1" },
  "p4-source-lag": { measureId: "P4", dependencyId: "E-Pipeline-3" },
  "p10-snapshot": { measureId: "P10", dependencyId: "E-Pipeline-1" },
  "p10-delivery-detail": { measureId: "P10", dependencyId: "E-Pipeline-5" },
  "p11-integrity": { measureId: "P11", dependencyId: "E-Pipeline-4" },
  "p12-ledger": { measureId: "P12", dependencyId: "E-Pipeline-6" },
};
const DOCS = `${import.meta.dir}/../../docs`;
const text = (name: string): Promise<string> =>
  Bun.file(`${DOCS}/${name}`).text();

export const ROUTES: RouteDefinition[] = CANONICAL_ROUTES;

type Manifest = WidgetDefinition;
const w = (
  id: string,
  title: string,
  measureIds: string[],
  section: string,
  type: string,
  layout: [number, number, number, number],
  contract: string,
  references: string[],
): Manifest => ({
  id,
  title,
  measureIds,
  section,
  type,
  layout,
  contract,
  matrixRowIds: references.filter((reference) =>
    /^A(?:0[1-9]|[1-3]\d|4[0-3])$/.test(reference),
  ),
  independentGates: references
    .filter((reference) => !/^A(?:0[1-9]|[1-3]\d|4[0-3])$/.test(reference))
    .map((id) => ({ id, measureId: measureIds[0] })),
});

const manifests: Record<RouteId, Manifest[]> = {
  pipeline: [
    w(
      "p1-snapshot",
      "P1 · Pipeline snapshot",
      ["P1"],
      "Scan completion/extraction-bound health",
      "one-row table",
      [0, 0, 12, 4],
      "Latest completed snapshot state, completion time, duration, rows, logs, traces, pending, and failed-during-drain. Scan age is excluded.",
      ["A01", "A02"],
    ),
    w(
      "p2-outcomes",
      "P2 · Scan outcomes",
      ["P2"],
      "Scan completion/extraction-bound health",
      "100% stacked bar",
      [0, 4, 6, 5],
      "Terminal scan composition by scan completion time.",
      ["A01", "A03"],
    ),
    w(
      "p2-freshness",
      "P2 · Scan freshness",
      ["P2"],
      "Scan completion/extraction-bound health",
      "one-row table",
      [6, 4, 6, 5],
      "Evaluation time minus latest successful completion plus missed cadence from authoritative schedule configuration.",
      ["A01", "A04"],
    ),
    w(
      "p3-duration",
      "P3 · Scan duration",
      ["P3"],
      "Scan completion/extraction-bound health",
      "p50/p95 graph",
      [0, 9, 6, 5],
      "Per-bucket duration p50/p95 and n.",
      ["A01", "A05"],
    ),
    w(
      "p3-rows",
      "P3 · Rows processed",
      ["P3"],
      "Scan completion/extraction-bound health",
      "p50/p95 graph",
      [6, 9, 6, 5],
      "Per-bucket rows-processed p50/p95 and n; raw rows may be an additional approved series.",
      ["A01", "A05"],
    ),
    w(
      "p3-throughput",
      "P3 · Normalized throughput",
      ["P3"],
      "Scan completion/extraction-bound health",
      "table",
      [0, 14, 12, 4],
      "Rows/s and ms/1,000 rows with n.",
      ["A01", "A05"],
    ),
    w(
      "p10-snapshot",
      "P10 · Outbox snapshot",
      ["P10"],
      "Scan completion/extraction-bound health",
      "table",
      [0, 18, 12, 4],
      "Snapshot-only pending and failed-during-drain counts; overlapping values are never stacked.",
      ["A01", "A02"],
    ),
    w(
      "p10-delivery-detail",
      "P10 · Outbox delivery detail",
      ["P10"],
      "Scan completion/extraction-bound health",
      "diagnostic table",
      [0, 22, 12, 6],
      "Pending age, destination, attempted event identity, and error class only after immutable event-level outbox delivery projection.",
      ["A01", "E-Pipeline-5"],
    ),
    w(
      "p4-source-lag",
      "P4 · Source lag",
      ["P4"],
      "Scan completion/extraction-bound health",
      "diagnostic table",
      [0, 28, 12, 6],
      "Current/p50/p95 lag, n, and negative-skew count by producer/surface/signal; range basis scan completion, lag reference extraction bound.",
      ["A01", "A06"],
    ),
    w(
      "p5-correlation",
      "P5 · Directional correlation",
      ["P5"],
      "Source-time capture and attribution",
      "table",
      [0, 34, 12, 7],
      "Source-to-lifecycle and lifecycle-to-source counts/rates in separate columns with capability state.",
      ["A01", "A07"],
    ),
    w(
      "p6-delay",
      "P6 · Lifecycle delay",
      ["P6"],
      "Source-time capture and attribution",
      "percentile table",
      [0, 41, 12, 5],
      "p50/p95, matched sessions, and clock-skew sessions.",
      ["A01", "E-Attribution-4"],
    ),
    w(
      "p7-coverage",
      "P7 · Attribution coverage",
      ["P7"],
      "Source-time capture and attribution",
      "table",
      [0, 46, 12, 5],
      "Attributed, unresolved, eligible, and attribution percentage.",
      ["A01", "A08"],
    ),
    w(
      "p8-diagnostics",
      "P8 · Attribution diagnostics",
      ["P8"],
      "Source-time capture and attribution",
      "top-50 diagnostic table",
      [0, 51, 12, 6],
      "Method/state/rejection reason; totals conserve P7 eligible activities.",
      ["A01", "A09"],
    ),
    w(
      "p9-transitions",
      "P9 · Reconciliation transitions",
      ["P9"],
      "Source-time capture and attribution",
      "trend",
      [0, 57, 12, 5],
      "Materialized unresolved-to-resolved transitions; rate hidden until all-version denominator proven.",
      ["A01", "E-Attribution-5"],
    ),
    w(
      "p9-evidence",
      "P9 · Reconciliation evidence",
      ["P9"],
      "Source-time capture and attribution",
      "detail table",
      [0, 62, 12, 6],
      "Producer, original reason, method, project, and short activity identity.",
      ["A01", "E-Attribution-5"],
    ),
    w(
      "p11-integrity",
      "P11 · Integrity incidents",
      ["P11"],
      "Source-time capture and attribution",
      "table",
      [0, 68, 12, 5],
      "Only after immutable rejection/integrity projection.",
      ["A01", "E-Pipeline-4"],
    ),
    w(
      "p12-ledger",
      "P12 · Ledger health",
      ["P12"],
      "Source-time capture and attribution",
      "table",
      [0, 73, 12, 5],
      "Only after redacted immutable maintenance projection.",
      ["A01", "E-Pipeline-6"],
    ),
    w(
      "scan-evidence",
      "Canonical scan evidence",
      ["P1"],
      "Supporting evidence",
      "latest-50 table",
      [0, 78, 12, 7],
      "Supporting evidence after canonical snapshot projection.",
      ["A01", "A02"],
    ),
  ],
  provider: [
    w(
      "r1-outcomes",
      "R1 · Final outcomes",
      ["R1"],
      "Request terminal time",
      "100% stacked graph",
      [0, 0, 8, 5],
      "Logical requests whose unique final attempt became terminal in range; range predicate is final-attempt terminal timestamp.",
      ["A10", "A11", "A12", "A13", "A14"],
    ),
    w(
      "r1-summary",
      "R1 · Outcome summary",
      ["R1"],
      "Request terminal time",
      "table",
      [8, 0, 4, 5],
      "Counts/rates over the same terminal logical-request denominator.",
      ["A10", "A11", "A12", "A13", "A15"],
    ),
    w(
      "r8-trend",
      "R8 · Unknown coverage trend",
      ["R8"],
      "Request terminal time",
      "graph",
      [0, 5, 8, 5],
      "Grace-eligible requests by request-start time.",
      ["A10", "A11", "A12", "A13", "A15"],
    ),
    w(
      "r8-summary",
      "R8 · Unknown coverage summary",
      ["R8"],
      "Request terminal time",
      "table",
      [8, 5, 4, 5],
      "Numerator/denominator separate from R1.",
      ["A10", "A11", "A12", "A13", "A15"],
    ),
    w(
      "r2-requests",
      "R2 · Failed logical requests",
      ["R2"],
      "Request terminal time",
      "table",
      [0, 10, 6, 6],
      "Final request error class/status.",
      ["A10", "A11", "A12", "A13", "A16"],
    ),
    w(
      "r2-attempts",
      "R2 · Failed attempts",
      ["R2"],
      "Request terminal time",
      "table",
      [6, 10, 6, 6],
      "Attempt population; never added to request counts.",
      ["A10", "A11", "A12", "A13", "A16"],
    ),
    w(
      "r3-phases",
      "R3 · Latency phases",
      ["R3"],
      "Request terminal time",
      "percentile table",
      [0, 16, 12, 6],
      "Accept, TTFT, stream, and total p50/p95/p99, each with own n; separate negative-duration and out-of-order timestamp counts.",
      ["A10", "A11", "A12", "A13", "A17"],
    ),
    w(
      "r4-retry",
      "R4 · Retry behavior",
      ["R4"],
      "Request terminal time",
      "table",
      [0, 22, 12, 5],
      "Amplification, retried %, recovery %, attempts-before-success p50/p95/n.",
      ["A10", "A11", "A12", "A13", "A18"],
    ),
    w(
      "r5-trend",
      "R5 · Streaming interruption trend",
      ["R5"],
      "Request terminal time",
      "graph",
      [0, 27, 8, 5],
      "Explicit-terminal stream-start attempt population.",
      ["A10", "A11", "A12", "A13", "E-Request-1"],
    ),
    w(
      "r5-summary",
      "R5 · Streaming interruption summary",
      ["R5"],
      "Request terminal time",
      "table",
      [8, 27, 4, 5],
      "Exact numerator/denominator.",
      ["A10", "A11", "A12", "A13", "E-Request-1"],
    ),
    w(
      "r6-conformance",
      "R6 · Model conformance",
      ["R6"],
      "Request terminal time",
      "long-form table",
      [0, 32, 12, 6],
      "Requested model, response model, mismatch requests, denominator, mismatch percentage, and unknown-response count. An accessible matrix replacement requires a separately approved manifest and browser proof.",
      ["A10", "A11", "A12", "A13", "A19"],
    ),
    w(
      "r7-throughput",
      "R7 · Output throughput",
      ["R7"],
      "Request terminal time",
      "percentile table",
      [0, 38, 6, 6],
      "Output tokens/s p50/p95/n for successful streamed attempts.",
      ["A10", "A11", "A12", "A13", "E-Request-2"],
    ),
    w(
      "r7-volume",
      "R7 · Output volume",
      ["R7"],
      "Request terminal time",
      "percentile table",
      [6, 38, 6, 6],
      "Output tokens p50/p95/n for its declared eligible cohort.",
      ["A10", "A11", "A12", "A13", "E-Request-2"],
    ),
    w(
      "provider-evidence",
      "Provider evidence",
      ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"],
      "Supporting evidence",
      "latest-100 table",
      [0, 44, 12, 7],
      "Redacted request/attempt identity, outcome, models, latency, retry, and error.",
      ["A10", "A11", "A12", "A13", "E-Request-3"],
    ),
  ],
  usage: [
    w(
      "m1-provenance",
      "M1 · Provenance summary",
      ["M1"],
      "Request/accounting-time usage",
      "table",
      [0, 0, 12, 4],
      "Request/model provenance coverage; hidden until provider projection carries project.",
      ["A20", "A21", "A22"],
    ),
    w(
      "m2-request-pressure",
      "M2 · Request-accounted token pressure",
      ["M2"],
      "Request/accounting-time usage",
      "table",
      [0, 4, 6, 5],
      "Per-request input, cached-input, output, reasoning, and total tokens; terminal/accounting-time population only. Tool calls are excluded.",
      ["A20", "A21", "A23"],
    ),
    w(
      "m3-friction",
      "M3 · Explicit-friction rate",
      ["M3"],
      "Canonical task source-time interaction",
      "table",
      [6, 4, 6, 5],
      "Capability-comparable task numerator/denominator.",
      ["A20", "A21", "A24"],
    ),
    w(
      "m1-calls",
      "M1 · Calls over time",
      ["M1"],
      "Request/accounting-time usage",
      "graph",
      [0, 9, 12, 5],
      "Logical requests by terminal time.",
      ["A20", "A21", "A25"],
    ),
    w(
      "m2-trace-pressure",
      "M2 · Trace-accounted pressure",
      ["M2"],
      "Canonical task source-time interaction",
      "table",
      [0, 14, 12, 5],
      "Trace episode token/tool-call p50/p95, outliers, and source-time basis; never merged with request accounting.",
      ["A20", "A21", "M2-trace-accounted"],
    ),
    w(
      "m4-recovery",
      "M4 · Friction timing/recovery",
      ["M4"],
      "Canonical task source-time interaction",
      "table",
      [0, 19, 12, 6],
      "Timing columns first; recovery columns only after explicit task-outcome projection.",
      ["A20", "A21", "A26"],
    ),
    w(
      "friction-evidence",
      "Friction evidence",
      ["M3", "M4"],
      "Supporting evidence",
      "latest-100 table",
      [0, 25, 12, 7],
      "Explicit signal, source time, producer, project, task short ID, and explicit outcome.",
      ["A20", "A21", "E-Task-4"],
    ),
  ],
  tools: [
    w(
      "m5-failure",
      "M5 · Tool failure",
      ["M5"],
      "Tool execution",
      "top-50 table",
      [0, 0, 12, 6],
      "Failure fingerprint, failed calls, affected tasks, and task impact after full denominator projection.",
      ["A27", "A28", "A29"],
    ),
    w(
      "m6-recovery",
      "M6 · Recovery",
      ["M6"],
      "Tool execution",
      "table",
      [0, 6, 6, 5],
      "Recovery %, calls/time-to-recovery, unknown task ends.",
      ["A27", "A28", "A30"],
    ),
    w(
      "m7-repeats",
      "M7 · Repeated attempts",
      ["M7"],
      "Tool execution",
      "table",
      [6, 6, 6, 5],
      "Affected tasks, attempt p50/p95, explicit-success-after-repeat.",
      ["A27", "A28", "A31"],
    ),
    w(
      "m8-churn",
      "M8 · Command churn",
      ["M8"],
      "Tool execution",
      "top-50 table",
      [0, 11, 12, 6],
      "Target, distinct commands, affected tasks, terminal outcome.",
      ["A27", "A28", "A32"],
    ),
    w(
      "m9-loops",
      "M9 · Tool loops",
      ["M9"],
      "Tool execution",
      "top-50 table",
      [0, 17, 12, 6],
      "Cycle fingerprint/length/repetitions/operations/recovery.",
      ["A27", "A28", "A33"],
    ),
    w(
      "m10-sandbox",
      "M10 · Sandbox friction",
      ["M10"],
      "Tool execution",
      "table",
      [0, 23, 6, 5],
      "Explicit outcomes and affected-task denominator.",
      ["A27", "A28", "A34"],
    ),
    w(
      "m11-bypass",
      "M11 · Quality-gate bypass",
      ["M11"],
      "Tool execution",
      "table",
      [6, 23, 6, 5],
      "Complete fail-mutate-pass sequence.",
      ["A27", "A28", "A35"],
    ),
    w(
      "tool-evidence",
      "Tool evidence",
      ["M5", "M6", "M7", "M8", "M9", "M10", "M11"],
      "Supporting evidence",
      "latest-100 table",
      [0, 28, 12, 7],
      "Redacted operation/target/failure/outcome and short identities.",
      ["A27", "A28", "E-Task-4"],
    ),
  ],
  recurrence: [
    w(
      "m14-findings",
      "M14 · Actionable findings",
      ["M14"],
      "Recurrence and interventions",
      "ranked table",
      [0, 0, 6, 6],
      "Occurrences, tasks, days, state, window, detector, fingerprint.",
      ["A36", "A37", "A38"],
    ),
    w(
      "m17-practices",
      "M17 · Successful practices",
      ["M17"],
      "Recurrence and interventions",
      "ranked table",
      [6, 0, 6, 6],
      "Support, explicit success, comparison population, owner state, sequence ID.",
      ["A36", "A37", "A39"],
    ),
    w(
      "m13-scope",
      "M13 · Scope recurrence",
      ["M13"],
      "Recurrence and interventions",
      "top-50 table",
      [0, 6, 12, 6],
      "Targets across tasks/days/projects with operation/failure fingerprint.",
      ["A36", "A37", "A40"],
    ),
    w(
      "m16-concentration",
      "M16 · Project concentration",
      ["M16"],
      "Recurrence and interventions",
      "table",
      [0, 12, 12, 6],
      "Distribution and top-project concentration; unresolved outside denominator.",
      ["A36", "A37", "A41"],
    ),
    w(
      "m12-adherence",
      "M12 · Skill adherence",
      ["M12"],
      "Recurrence and interventions",
      "table",
      [0, 18, 6, 6],
      "Applicable, satisfied, unknown observability, versioned rule.",
      ["A36", "A37", "A42"],
    ),
    w(
      "m18-audit",
      "M18 · Enforcement-tier audit",
      ["M18"],
      "Recurrence and interventions",
      "table",
      [6, 18, 6, 6],
      "Selected tier, earlier-tier rejection reasons, state, missing audit.",
      ["A36", "A37", "A43"],
    ),
    w(
      "m15-recurrence",
      "M15 · Pre/post recurrence",
      ["M15"],
      "Recurrence and interventions",
      "table",
      [0, 24, 12, 6],
      "Raw counts, observable-task denominators, equal windows, rates, comparability; no causal label.",
      ["A36", "A37", "M15-intervention-lineage"],
    ),
    w(
      "candidate-evidence",
      "Candidate/intervention evidence",
      ["M12", "M13", "M14", "M15", "M16", "M17", "M18"],
      "Supporting evidence",
      "latest-100 table",
      [0, 30, 12, 7],
      "Redacted identity and state history.",
      ["A36", "A37", "E-Recurrence-4"],
    ),
  ],
};

function measureMetadata(markdown: string): MeasureMetadata[] {
  const heading = /^### ([PRM]\d+)\. (.+)$/gm;
  const hits = [...markdown.matchAll(heading)];
  const measures = hits.map((hit, index) => {
    const body = markdown.slice(
      hit.index! + hit[0].length,
      hits[index + 1]?.index ?? markdown.length,
    );
    const field = (label: string, next: string[]) => {
      const start = body.match(new RegExp(`\\*\\*${label}:\\*\\*\\s*`));
      if (!start?.index) return "";
      const value = body.slice(start.index + start[0].length);
      const end = value.search(
        new RegExp(`\\n\\*\\*(?:${next.join("|")}):\\*\\*`),
      );
      return value.slice(0, end < 0 ? undefined : end).trim();
    };
    return {
      id: hit[1],
      title: hit[2],
      question: field("Question", ["Calculation", "Calculations", "Cohort"]),
      calculation: field("Calculations?", ["Cohort"]),
      cohort: field("Cohort", ["Dimensions"]),
      dimensions: field("Dimensions", ["Interpretation", "Availability"]),
      availability: field("Availability", []),
    };
  });
  const expected = [
    ...Array.from({ length: 12 }, (_, index) => `P${index + 1}`),
    ...Array.from({ length: 8 }, (_, index) => `R${index + 1}`),
    ...Array.from({ length: 18 }, (_, index) => `M${index + 1}`),
  ];
  if (
    measures.length !== expected.length ||
    measures.some(
      (measure, index) =>
        measure.id !== expected[index] ||
        Object.values(measure).some((value) => !value),
    )
  )
    throw new Error("Canonical measure metadata is incomplete or malformed.");
  return measures;
}

function canonicalManifests(markdown: string): Record<RouteId, Manifest[]> {
  const source: Record<
    string,
    Pick<Manifest, "title" | "type" | "layout" | "contract">
  > = {};
  for (const line of markdown.split("\n")) {
    const cells = line.split("|").map((cell) => cell.trim());
    const layout = cells[3]
      ?.replaceAll("`", "")
      .match(/^\((\d+),(\d+),(\d+),(\d+)\)$/);
    if (!layout || !cells[1] || !cells[2] || !cells[4]) continue;
    source[cells[1]] = {
      title: cells[1],
      type: cells[2],
      layout: layout.slice(1).map(Number) as Manifest["layout"],
      contract: cells[4],
    };
  }
  return Object.fromEntries(
    ROUTES.map((route) => [
      route.id,
      manifests[route.id].map((manifest) => {
        const exact = source[manifest.title];
        if (!exact)
          throw new Error(
            `Canonical widget manifest is incomplete for ${manifest.title}.`,
          );
        return { ...manifest, ...exact };
      }),
    ]),
  ) as Record<RouteId, Manifest[]>;
}

function registerRows(
  markdown: string,
): Map<string, { status: MatrixStatus; authority: string }> {
  const rows = new Map<string, { status: MatrixStatus; authority: string }>();
  for (const line of markdown.split("\n")) {
    const cells = line.split("|").map((cell) => cell.trim());
    if (!/^A(?:0[1-9]|[1-3]\d|4[0-3])$/.test(cells[1] ?? "")) continue;
    for (const [offset, producer] of producers.entries()) {
      const status = cells[4 + offset] as MatrixStatus;
      if (!["Blocked", "Proven", "Not applicable"].includes(status))
        throw new Error(
          `Proof register status is malformed for ${cells[1]}:${producer}.`,
        );
      rows.set(`${cells[1]}:${producer}`, {
        status,
        authority: cells[7] ?? "",
      });
    }
  }
  if (rows.size && rows.size !== 129)
    throw new Error("Proof register coverage is incomplete.");
  return rows;
}
function validateApplicationEvidence(
  value: unknown,
  widgetManifests: Record<RouteId, Manifest[]>,
): ApplicationEvidenceBinding[] {
  if (
    !value ||
    typeof value !== "object" ||
    (value as ApplicationEvidence).schema_version !== 1 ||
    !Array.isArray((value as ApplicationEvidence).bindings)
  )
    throw new Error("Application evidence must use schema version 1 bindings.");
  const bindings = (value as ApplicationEvidence).bindings;
  const expectedNative = new Set(
    Object.values(widgetManifests)
      .flat()
      .flatMap((widget) =>
        producers.flatMap((producer) => [
          ...widget.matrixRowIds.map(
            (id) => `${widget.id}:${widget.measureIds[0]}:${id}:${producer}`,
          ),
          ...widget.independentGates
            .filter((gate) => gate.id.startsWith("E-"))
            .map(
              (gate) => `${widget.id}:${gate.measureId}:${gate.id}:${producer}`,
            ),
        ]),
      ),
  );
  const seen = new Set<string>();
  for (const binding of bindings) {
    if (!binding || typeof binding !== "object")
      throw new Error("Application evidence binding is malformed.");
    if ("central_identity" in binding) {
      const expected = centralDependencies[binding.widget_id];
      const identity = `${binding.widget_id}:${binding.measure_id}:${binding.dependency_id}`;
      const qualification = binding.qualification;
      const identityValues = Object.values(binding.central_identity ?? {});
      const exactKeys = (value: object, keys: string[]) =>
        Object.keys(value).length === keys.length &&
        keys.every((key) => Object.hasOwn(value, key));
      if (
        !binding.central_identity ||
        typeof binding.central_identity !== "object" ||
        !qualification ||
        typeof qualification !== "object" ||
        !exactKeys(binding, [
          "widget_id",
          "measure_id",
          "dependency_id",
          "central_identity",
          "qualification",
        ]) ||
        !exactKeys(binding.central_identity, [
          "runtime_instance_id",
          "scan_run_id",
          "database_identity",
        ]) ||
        !exactKeys(qualification, [
          "provenance",
          "result",
          "deployment_fingerprint",
          "calculation_implementation_id",
          "calculation_implementation_sha256",
          "projection_implementation_id",
          "projection_implementation_sha256",
          "evidence_bundle",
        ]) ||
        expected.measureId !== binding.measure_id ||
        expected.dependencyId !== binding.dependency_id ||
        seen.has(`central:${identity}`) ||
        identityValues.length !== 3 ||
        identityValues.some((field) => typeof field !== "string" || !field) ||
        !/^[a-f0-9]{64}$/.test(binding.central_identity.database_identity) ||
        qualification.provenance !== "fresh-real" ||
        qualification.result !== "Proven" ||
        [
          qualification.deployment_fingerprint,
          qualification.calculation_implementation_id,
          qualification.calculation_implementation_sha256,
          qualification.projection_implementation_id,
          qualification.projection_implementation_sha256,
        ].some((field) => typeof field !== "string" || !field) ||
        !qualification.evidence_bundle ||
        typeof qualification.evidence_bundle !== "object" ||
        Array.isArray(qualification.evidence_bundle)
      )
        throw new Error("Central application evidence binding is malformed.");
      seen.add(`central:${identity}`);
      continue;
    }
    const native = binding as NativeApplicationEvidenceBinding;
    const identity = `${native.widget_id}:${native.measure_id}:${native.dependency_id}:${native.producer}`;
    if (
      !producers.includes(native.producer as (typeof producers)[number]) ||
      !expectedNative.has(identity) ||
      seen.has(`native:${identity}`)
    )
      throw new Error(
        "Application evidence must bind one widget dependency per producer.",
      );
    seen.add(`native:${identity}`);
  }
  return bindings;
}

function applicationEvidence(
  bindings: ApplicationEvidenceBinding[],
  implementation?: PipelineImplementation,
): ApplicationEvidenceBinding[] {
  if (!implementation || implementation.deployments.length === 0) return [];
  return bindings.filter((binding) => {
    const qualification = binding.qualification;
    if (
      implementation.calculationId !==
        qualification.calculation_implementation_id ||
      implementation.calculationSha256 !==
        qualification.calculation_implementation_sha256
    )
      return false;
    return implementation.deployments.every(
      (deployment) =>
        deployment.fingerprint === qualification.deployment_fingerprint &&
        deployment.projectionId ===
          qualification.projection_implementation_id &&
        deployment.projectionSha256 ===
          qualification.projection_implementation_sha256,
    );
  });
}
async function validateAuthoritativeApplicationEvidence(
  matrix: Matrix,
  signal?: AbortSignal,
): Promise<void> {
  const rejected = () =>
    new Error(
      "Application qualification was rejected by the authoritative validator.",
    );
  if (signal?.aborted) throw rejected();
  const child = Bun.spawn(
    ["uv", "run", "python", "-m", "experiments.dashboard_prototype.contracts"],
    {
      cwd: `${import.meta.dir}/../..`,
      stdin: "pipe",
      stdout: "pipe",
      stderr: "pipe",
    },
  );
  const readers = [child.stdout.getReader(), child.stderr.getReader()];
  let capturedBytes = 0;
  const capture = async (reader: ReadableStreamDefaultReader<Uint8Array>) => {
    while (true) {
      const next = await reader.read();
      if (next.done) return;
      capturedBytes += next.value.byteLength;
      if (capturedBytes > 4096) throw rejected();
    }
  };
  const terminateAndReap = async () => {
    const cancellation = Promise.all(
      readers.map(async (reader) => {
        try {
          await reader.cancel();
        } finally {
          reader.releaseLock();
        }
      }),
    );
    let killFailure: unknown;
    try {
      child.kill("SIGKILL");
    } catch (failure) {
      killFailure = failure;
    }
    const [cancelled, exited] = await Promise.allSettled([
      cancellation,
      child.exited,
    ]);
    if (killFailure) throw killFailure;
    if (cancelled.status === "rejected") throw cancelled.reason;
    if (exited.status === "rejected") throw exited.reason;
  };
  let timeoutId: Timer | undefined;
  let abort: (() => void) | undefined;
  let failure: unknown;
  try {
    const timeout = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => reject(rejected()), 5_000);
    });
    const aborted = new Promise<never>((_, reject) => {
      abort = () => reject(rejected());
      signal?.addEventListener("abort", abort, { once: true });
    });
    child.stdin.write(JSON.stringify(matrix));
    child.stdin.end();
    const [exitCode] = await Promise.race([
      Promise.all([child.exited, capture(readers[0]), capture(readers[1])]),
      timeout,
      aborted,
    ]);
    if (exitCode !== 0 || signal?.aborted) throw rejected();
  } catch (error) {
    failure = error;
  } finally {
    clearTimeout(timeoutId);
    if (abort) signal?.removeEventListener("abort", abort);
  }
  if (failure) {
    try {
      await terminateAndReap();
    } catch (lifecycleFailure) {
      throw new Error(rejected().message, {
        cause: new AggregateError([failure, lifecycleFailure]),
      });
    }
    throw new Error(rejected().message, { cause: failure });
  }
  for (const reader of readers) reader.releaseLock();
}
async function validate(
  matrix: Matrix,
  measures: MeasureMetadata[],
  register: Map<string, { status: MatrixStatus; authority: string }>,
  widgetManifests: Record<RouteId, Manifest[]>,
  signal?: AbortSignal,
): Promise<void> {
  if (matrix.supported_producers.join("|") !== producers.join("|"))
    throw new Error("Supported producer contract is malformed.");
  if (matrix.rows.length !== 129)
    throw new Error("Proof matrix must contain 129 producer-row obligations.");
  const identities = new Set<string>();
  const rowIds = new Set<string>();
  for (const row of matrix.rows) {
    const identity = `${row.row_id}:${row.producer}`;
    if (
      identities.has(identity) ||
      !producers.includes(row.producer as (typeof producers)[number])
    )
      throw new Error("Proof matrix has conflicting producer-row identities.");
    identities.add(identity);
    rowIds.add(row.row_id);
    const required = [
      row.row_id,
      row.control,
      row.fields_required,
      row.calculation_used,
      row.selected_range_operator,
      row.interval_containment_operator,
      row.evaluation_time_policy,
      row.evaluation_policy_id,
      row.all_version_requirement,
      row.version_policy_id,
      row.normative_capability_reason,
      row.proposed_event_family,
      row.projection_boundary,
      row.remote_query?.reference_id,
      row.oracle?.reference_id,
    ];
    if (required.some((value) => typeof value !== "string" || !value.trim()))
      throw new Error(`Proof matrix metadata is incomplete for ${identity}.`);
    if (!["Blocked", "Proven", "Not applicable"].includes(row.result))
      throw new Error(`Proof matrix status is malformed for ${identity}.`);
    if (
      row.result === "Blocked" &&
      (!row.blocked_boundary?.missing_boundary.trim() ||
        !row.blocked_boundary.owner.trim() ||
        !row.blocked_boundary.owner_surface.trim())
    )
      throw new Error(
        `Blocked proof matrix row lacks a complete missing-data boundary for ${identity}.`,
      );
    if (row.result === "Not applicable" && row.blocked_boundary !== null)
      throw new Error(
        `Not applicable proof matrix row has a blocker for ${identity}.`,
      );
    if (row.result === "Proven" && row.evidence_bundle === null)
      throw new Error(
        `Proven proof matrix row lacks evidence for ${identity}.`,
      );
    const registered = register.get(identity);
    if (registered && registered.status !== row.result)
      throw new Error(
        `Proof register status conflicts with matrix for ${identity}.`,
      );
  }
  const expectedRowIds = Array.from(
    { length: 43 },
    (_, index) => `A${String(index + 1).padStart(2, "0")}`,
  );
  if (
    rowIds.size !== expectedRowIds.length ||
    identities.size !== 129 ||
    expectedRowIds.some(
      (rowId) =>
        !rowIds.has(rowId) ||
        producers.some((producer) => !identities.has(`${rowId}:${producer}`)),
    )
  )
    throw new Error("Proof matrix coverage is incomplete.");
  for (const widget of Object.values(widgetManifests).flat())
    for (const rowId of widget.matrixRowIds)
      if (!rowIds.has(rowId))
        throw new Error(
          `Widget ${widget.id} references an unknown Appendix row.`,
        );
  const titles = new Map(
    measures.map((measure) => [measure.id, measure.title]),
  );
  if (titles.size !== 38)
    throw new Error("Canonical measure identities are conflicting.");
  for (const widget of Object.values(widgetManifests).flat())
    for (const id of widget.measureIds)
      if (!titles.has(id))
        throw new Error(`Widget ${widget.id} references an unknown measure.`);
  const widgets = Object.values(widgetManifests).flat();
  if (
    widgets.length !== 55 ||
    widgets.filter((widget) => widget.section === "Supporting evidence")
      .length !== 5
  )
    throw new Error("Full widget manifest is incomplete.");
  for (const route of ROUTES)
    for (const widget of widgetManifests[route.id]) {
      const [x, y, width, height] = widget.layout;
      if (
        x < 0 ||
        y < 0 ||
        width < 1 ||
        height < 1 ||
        x + width > 12 ||
        widgetManifests[route.id].some(
          (other) =>
            other !== widget &&
            x < other.layout[0] + other.layout[2] &&
            x + width > other.layout[0] &&
            y < other.layout[1] + other.layout[3] &&
            y + height > other.layout[1],
        )
      )
        throw new Error("Widget geometry is malformed.");
    }

  if (
    Array.isArray(matrix.application_evidence?.bindings) &&
    matrix.application_evidence.bindings.length
  )
    await validateAuthoritativeApplicationEvidence(matrix, signal);
}

function registerMeasureAuthorities(markdown: string): Record<string, string> {
  const authorities: Record<string, string> = {};
  for (const line of markdown.split("\n")) {
    const cells = line.split("|").map((cell) => cell.trim());
    if (/^[PRM]\d+$/.test(cells[1] ?? "") && cells[4])
      authorities[cells[1]] = cells[4];
  }
  return authorities;
}

function gate(
  widget: WidgetDefinition,
  rows: MatrixRow[],
  register: Map<string, { status: MatrixStatus; authority: string }>,
  measureAuthorities: Record<string, string>,
  bindings: ApplicationEvidenceBinding[],
): WidgetResult["gate"] {
  const central = centralDependencies[widget.id];
  if (central) {
    const qualified = bindings.some(
      (binding) =>
        "central_identity" in binding &&
        binding.widget_id === widget.id &&
        binding.measure_id === central.measureId &&
        binding.dependency_id === central.dependencyId,
    );
    return {
      status: qualified ? "Data" : "Blocked",
      reasons: qualified ? [] : [applicationCalculationUnavailable],
      proofs: [
        {
          independentGate: central.dependencyId,
          producer: "application evidence validator",
          status: qualified ? "Proven" : "Blocked",
          reason: qualified
            ? "Central application evidence binding matches this report implementation."
            : applicationCalculationUnavailable,
          authority: "application evidence validator",
        },
      ],
    };
  }
  const proofs: Proof[] = [];
  for (const rowId of widget.matrixRowIds)
    for (const row of rows.filter((candidate) => candidate.row_id === rowId)) {
      const registered = register.get(`${row.row_id}:${row.producer}`);
      proofs.push({
        rowId,
        producer: row.producer,
        status: row.result,
        reason:
          row.result === "Blocked"
            ? redactPrivateReferences(row.blocked_boundary!.missing_boundary)
            : row.normative_capability_reason,
        ...(registered?.authority ? { authority: registered.authority } : {}),
      });
    }
  const boundDependencies = new Set(
    bindings
      .filter(
        (binding): binding is NativeApplicationEvidenceBinding =>
          !("central_identity" in binding),
      )
      .map(
        (binding) =>
          `${binding.widget_id}:${binding.measure_id}:${binding.dependency_id}:${binding.producer}`,
      ),
  );
  for (const independentGate of widget.independentGates) {
    const reason = measureAuthorities[independentGate.measureId];
    if (!reason)
      throw new Error(
        `Proof register lacks an authority for independent gate ${independentGate.id}.`,
      );
    for (const producer of producers) {
      const qualified = boundDependencies.has(
        `${widget.id}:${widget.measureIds[0]}:${independentGate.id}:${producer}`,
      );
      proofs.push({
        independentGate: independentGate.id,
        producer,
        status: qualified ? "Proven" : "Blocked",
        reason: qualified ? reason : applicationCalculationUnavailable,
      });
    }
  }
  const blocked = proofs.filter((proof) => proof.status === "Blocked");
  const applicable = proofs.filter(
    (proof) => proof.status !== "Not applicable",
  );
  const calculationAvailable = applicable.every((proof) =>
    boundDependencies.has(
      `${widget.id}:${widget.measureIds[0]}:${proof.rowId ?? proof.independentGate!}:${proof.producer}`,
    ),
  );
  return {
    status: blocked.length
      ? "Blocked"
      : !applicable.length
        ? "Not applicable"
        : calculationAvailable
          ? "Data"
          : "Blocked",
    reasons: [
      ...new Set(blocked.map((proof) => proof.reason)),
      ...(applicable.length && !blocked.length && !calculationAvailable
        ? [applicationCalculationUnavailable]
        : []),
    ],
    proofs,
  };
}

async function prepareRegistryInputs(
  measureDoc: string,
  matrixInput: unknown,
  proofRegister: string,
  signal?: AbortSignal,
): Promise<{
  resolve(implementation?: PipelineImplementation): Registry;
}> {
  const measures = measureMetadata(measureDoc);
  const matrix = matrixInput as Matrix;
  const register = registerRows(proofRegister);
  const widgetManifests = canonicalManifests(measureDoc);
  await validate(matrix, measures, register, widgetManifests, signal);
  const evidence = validateApplicationEvidence(
    matrix.application_evidence,
    widgetManifests,
  );
  const measureAuthorities = registerMeasureAuthorities(proofRegister);
  return {
    resolve(implementation?: PipelineImplementation): Registry {
      const bindings = applicationEvidence(evidence, implementation);
      const widgets = Object.fromEntries(
        ROUTES.map((route) => [
          route.id,
          widgetManifests[route.id].map((widget) => ({
            widget,
            gate: gate(
              widget,
              matrix.rows,
              register,
              measureAuthorities,
              bindings,
            ),
          })),
        ]),
      ) as Record<RouteId, WidgetResult[]>;
      return { routes: ROUTES, widgets, measures, controls: coverage(matrix) };
    },
  };
}

export async function buildRegistry(
  measureDoc: string,
  matrixInput: unknown,
  proofRegister = "",
  implementation?: PipelineImplementation,
): Promise<Registry> {
  return (
    await prepareRegistryInputs(measureDoc, matrixInput, proofRegister)
  ).resolve(implementation);
}

export async function prepareRegistry(signal?: AbortSignal): Promise<{
  resolve(implementation?: PipelineImplementation): Registry;
}> {
  const [measureDoc, matrixText, proofRegister] = await Promise.all([
    text("dashboard-measure-v2.md"),
    text("dashboard-prototype-proof-matrix.json"),
    text("dashboard-metric-proof-register.md"),
  ]);
  return await prepareRegistryInputs(
    measureDoc,
    JSON.parse(matrixText),
    proofRegister,
    signal,
  );
}

export function coverage(matrix: Matrix): CoverageContract[] {
  return matrix.rows.map((row) => ({
    rowId: row.row_id,
    producer: row.producer,
    control: row.control,
    route: row.route,
    status: row.result,
    gate: row.result,
    capabilityReason: row.normative_capability_reason,
    blocker: row.blocked_boundary
      ? redactPrivateReferences(row.blocked_boundary.missing_boundary)
      : null,
    owner: row.blocked_boundary?.owner ?? null,
    ownerSurface: row.blocked_boundary?.owner_surface ?? null,
    proposedEventFamily: row.proposed_event_family,
    projectionState: "Production unavailable",
    projectionBoundary: redactPrivateReferences(row.projection_boundary),
    query: row.remote_query.reference_id,
    oracle: row.oracle.reference_id,
    selectedRangeOperator: row.selected_range_operator,
    intervalContainmentOperator: row.interval_containment_operator,
    evaluationPolicy: row.evaluation_time_policy,
    evaluationPolicyId: row.evaluation_policy_id,
    versionPolicy: row.all_version_requirement,
    versionPolicyId: row.version_policy_id,
  }));
}
