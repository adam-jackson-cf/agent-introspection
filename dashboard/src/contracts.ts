import type { Harness } from "./harnesses.generated";

export const VIEW_IDS = [
  "pipeline",
  "cache",
  "effort",
  "tools",
  "friction",
  "guardrails",
  "provider",
  "recurrence",
  "intent",
  "interventions",
] as const;
export type ViewId = (typeof VIEW_IDS)[number];

export const DETAIL_PATHS = ["pipeline/coverage"] as const;
export const PAGE_PATHS = ["glossary"] as const;
export type ViewPath =
  | ViewId
  | (typeof DETAIL_PATHS)[number]
  | (typeof PAGE_PATHS)[number];

export { HARNESSES, type Harness } from "./harnesses.generated";

export type Filters = {
  start: string;
  end: string;
  harness: Harness | "";
  range?: number | undefined;
};

export type Scalar = string | number | boolean | null;
export type Row = Record<string, Scalar | Scalar[] | undefined>;

export type Alignment =
  | "aligned"
  | "differs"
  | "not applicable"
  | "not emitted";
export type RouteSource = "spans" | "logs" | "hooks";

export type SignalDefinition = {
  signal: string;
  view: ViewId;
  view_title: string;
  title: string;
  question: string;
  unit: string;
  formula: string;
  scope: "harness" | "system";
};
export type SignalSupport = {
  signal: string;
  harness: Harness;
  harness_label: string;
  route: string;
  alignment: Alignment;
  note: string;
};
export type SignalRoute = {
  route: string;
  harness: Harness;
  harness_label: string;
  source: RouteSource;
  match: string;
  expect: "rows" | "events";
  description: string;
};
export type SignalStray = {
  stray: string;
  harness: Harness;
  source: RouteSource;
  match: string;
  reason: string;
};
export type SignalExclusion = {
  signal: string;
  view: string;
  title: string;
  missing: string;
  reason: string;
};
export type HarnessEntry = { harness: Harness; label: string; enabled: number };
export type HiddenSignal = {
  signal: string;
  view: string;
  title: string;
  missing: string;
  note: string;
};
export type Features = { intervention_outcomes: boolean };
export type Registry = {
  signals: SignalDefinition[];
  support: SignalSupport[];
  routes: SignalRoute[];
  strays: SignalStray[];
  exclusions: SignalExclusion[];
  harnesses: HarnessEntry[];
  hidden: HiddenSignal[];
  features: Features;
};

export type ContributionState =
  | "rows"
  | "route missing"
  | "no activity"
  | "no events"
  | "not applicable";
export type Contribution = {
  state: ContributionState;
  rows: number;
  from: string | null;
};
export type Contributions = Record<
  string,
  Partial<Record<Harness, Contribution>>
>;

export type ViewResponse = {
  view: ViewId;
  filters: Filters;
  queriedAt: string;
  elapsedMs: number;
  timings: { registryMs: number; routeCountsMs: number; sqlMs: number };
  asOf: {
    at: string;
    stale: boolean;
    historyStale: boolean;
    failing: string[];
  } | null;
  data: Record<string, Row[]>;
  errors: Record<string, string>;
  thresholds: { minRateN: number };
  contributions: Contributions;
};

export type SessionResponse = {
  harness: Harness;
  session: string;
  queriedAt: string;
  data: Record<string, Row[]>;
};
