/** Wire contract between the Bun server and the React companion. */

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

/** Every harness in registry order; the selector offers All plus each. */
export const HARNESSES = [
  "oh-my-pi",
  "codex-app-server",
  "codex_cli_rs",
  "codex_exec",
  "claude-code",
] as const;
export type Harness = (typeof HARNESSES)[number];

export type Filters = {
  start: string;
  end: string;
  /** Empty string means All harnesses. */
  harness: Harness | "";
};

export type Scalar = string | number | boolean | null;
export type Row = Record<string, Scalar | Scalar[]>;

/** `not applicable`: the signal cannot exist for the harness by construction. */
export type Alignment = "aligned" | "differs" | "not applicable";
/** Where a route predicate runs: `introspection.spans`, `.logs`, or `.hook_rows`. */
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
  /** `events`: zero rows while the harness is active is a valid observation. */
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
/** A signal left off the dashboard because at least one harness cannot produce it. */
export type SignalExclusion = {
  signal: string;
  view: string;
  title: string;
  /** Labels of the harnesses that cannot produce it, comma-separated. */
  missing: string;
  reason: string;
};
export type Registry = {
  signals: SignalDefinition[];
  support: SignalSupport[];
  routes: SignalRoute[];
  strays: SignalStray[];
  exclusions: SignalExclusion[];
};

/** Rows on each harness's own route of a signal in the selected window. */
export type Contributions = Record<string, Partial<Record<Harness, number>>>;

export type ViewResponse = {
  view: ViewId;
  filters: Filters;
  queriedAt: string;
  elapsedMs: number;
  data: Record<string, Row[]>;
  contributions: Contributions;
};

export type SessionResponse = {
  harness: Harness;
  session: string;
  queriedAt: string;
  data: Record<string, Row[]>;
};
