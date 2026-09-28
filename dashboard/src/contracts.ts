export type RouteId =
  | "pipeline"
  | "provider"
  | "usage"
  | "tools"
  | "recurrence";

export type ResultState =
  | "Loading"
  | "Data"
  | "No data"
  | "Not applicable"
  | "Unavailable"
  | "Integrity failure"
  | "Query/system error";

export type FilterState = {
  start: string;
  end: string;
  provider: string;
  modelRole: "Requested" | "Response";
  model: string;
  project: string;
};

export type IndependentGate = {
  id: string;
  measureId: string;
};

export type WidgetDefinition = {
  id: string;
  title: string;
  measureIds: string[];
  section: string;
  type: string;
  layout: [number, number, number, number];
  contract: string;
  matrixRowIds: string[];
  independentGates: IndependentGate[];
};

export type Proof = {
  producer: string;
  status: "Blocked" | "Proven" | "Not applicable";
  reason: string;
  rowId?: string;
  independentGate?: string;
  authority?: string;
};

export type CoverageContract = {
  rowId: string;
  producer: string;
  control: string;
  route: string;
  status: Proof["status"];
  gate: Proof["status"];
  capabilityReason: string;
  blocker: string | null;
  owner: string | null;
  ownerSurface: string | null;
  proposedEventFamily: string;
  projectionState: "Production unavailable";
  projectionBoundary: string;
  query: string;
  oracle: string;
  selectedRangeOperator: string;
  intervalContainmentOperator: string;
  evaluationPolicy: string;
  evaluationPolicyId: string;
  versionPolicy: string;
  versionPolicyId: string;
};

export type PipelineState =
  | "Data"
  | "No data"
  | "Not applicable"
  | "Unavailable"
  | "Integrity failure"
  | "Query/system error";

export type PipelineScalar = string | number | null;

export type PipelineMetric = {
  label: string;
  value: PipelineScalar;
  unit: string;
  numerator: number | null;
  denominator: number | null;
  sampleCount: number | null;
};

export type PipelinePoint = { at: string; value: number };

export type PipelineSeries = {
  name: string;
  unit: string;
  points: PipelinePoint[];
};

export type PipelinePanel = {
  state: PipelineState;
  population: string;
  timeBasis: string;
  rangeOperator: string;
  metrics: PipelineMetric[];
  columns: string[];
  rows: PipelineScalar[][];
  series: PipelineSeries[];
  reasons: string[];
  provenance: Record<string, string>;
};

export type PipelineImplementation = {
  calculationId: "agent-introspection.pipeline-dashboard";
  calculationSha256: string;
  deployments: {
    fingerprint: string;
    projectionId: "agent-introspection.pipeline-observations";
    projectionSha256: string;
  }[];
};
export type CentralApplicationEvidenceBinding = {
  widget_id: string;
  measure_id: string;
  dependency_id: string;
  central_identity: {
    runtime_instance_id: string;
    scan_run_id: string;
    database_identity: string;
  };
  qualification: {
    provenance: "fresh-real";
    result: "Proven";
    deployment_fingerprint: string;
    calculation_implementation_id: string;
    calculation_implementation_sha256: string;
    projection_implementation_id: string;
    projection_implementation_sha256: string;
    evidence_bundle: Record<string, unknown>;
  };
};

export type PipelineReport = {
  start: string;
  end: string;
  evaluatedAt: string;
  implementation: PipelineImplementation;
  panels: Record<string, PipelinePanel>;
};

export type WidgetResult = {
  widget: WidgetDefinition;
  gate: {
    status: "Blocked" | "Data" | "Not applicable";
    reasons: string[];
    proofs: Proof[];
  };
  measurement?: PipelinePanel;
};

export type RouteDefinition = {
  id: RouteId;
  path: string;
  title: string;
  eyebrow: string;
  subtitle: string;
};

export const CANONICAL_ROUTES: RouteDefinition[] = [
  {
    id: "pipeline",
    path: "/pipeline",
    title: "01 · Pipeline health",
    eyebrow: "01 · Pipeline health",
    subtitle: "Pipeline evidence, scan completion, capture, and attribution.",
  },
  {
    id: "provider",
    path: "/provider",
    title: "02 · Provider health",
    eyebrow: "02 · Provider health",
    subtitle: "Logical request outcomes, attempts, latency, and delivery.",
  },
  {
    id: "usage",
    path: "/usage",
    title: "03a · Model usage · Usage & interaction",
    eyebrow: "03a · Model usage",
    subtitle: "Request accounting and canonical task interaction.",
  },
  {
    id: "tools",
    path: "/tools",
    title: "03b · Model usage · Tool execution",
    eyebrow: "03b · Model usage",
    subtitle: "Tool execution failures, recovery, and repetition.",
  },
  {
    id: "recurrence",
    path: "/recurrence",
    title: "03c · Model usage · Recurrence & interventions",
    eyebrow: "03c · Model usage",
    subtitle: "Recurrence, practices, and intervention evidence.",
  },
];

export type DashboardResponse = {
  route: RouteDefinition;
  filters: FilterState;
  widgets: WidgetResult[];
  queriedAt: string;
  transport: {
    state: "Data" | "Query/system error";
    message: string;
  };
};
