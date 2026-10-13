/** Wire contract between the Bun server and the React companion. */
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

/** An L3 detail page lives at `/<view>/<detail>`, under its parent view. */
export const DETAIL_PATHS = ["pipeline/coverage"] as const;
/** Pages that are not views: the generated glossary of signal definitions. */
export const PAGE_PATHS = ["glossary"] as const;
export type ViewPath =
  | ViewId
  | (typeof DETAIL_PATHS)[number]
  | (typeof PAGE_PATHS)[number];

/** Every known harness, generated from the signal registry's `[harnesses]` table. */
export { HARNESSES, type Harness } from "./harnesses.generated";

export type Filters = {
  start: string;
  end: string;
  /** Empty string means All harnesses: the union of the enabled harnesses. */
  harness: Harness | "";
  /**
   * Browser only: a live preset in days. The window always ends now, so Refresh
   * and a reopened link move it forward. Absent for a custom range.
   */
  range?: number;
};

export type Scalar = string | number | boolean | null;
export type Row = Record<string, Scalar | Scalar[]>;

/**
 * `not applicable`: the signal cannot exist for the harness by construction.
 * `not emitted`: the harness has no route; the signal is hidden wherever it is enabled.
 */
export type Alignment =
  | "aligned"
  | "differs"
  | "not applicable"
  | "not emitted";
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
/** A signal left off the dashboard because no harness produces it. */
export type SignalExclusion = {
  signal: string;
  view: string;
  title: string;
  /** Labels of the harnesses that cannot produce it, comma-separated. */
  missing: string;
  reason: string;
};
/** One known harness and whether this machine uses it (`introspection.harnesses`). */
export type HarnessEntry = { harness: Harness; label: string; enabled: number };
/**
 * A harness-scoped signal hidden on this machine: an enabled harness does not emit
 * it. One entry per signal and reason. Derived by the server from the support rows.
 */
export type HiddenSignal = {
  signal: string;
  view: string;
  title: string;
  /** Labels of the enabled harnesses that do not emit it, comma-separated. */
  missing: string;
  note: string;
};
export type Registry = {
  signals: SignalDefinition[];
  support: SignalSupport[];
  routes: SignalRoute[];
  strays: SignalStray[];
  exclusions: SignalExclusion[];
  harnesses: HarnessEntry[];
  hidden: HiddenSignal[];
};

/**
 * A panel chip's state for one enabled harness, from the coverage grid: rows on
 * its route; route missing (no rows while the harness is otherwise active: a
 * possible break); no activity (the harness is idle); no events (a rare-event
 * route with none); not applicable.
 */
export type ContributionState =
  | "rows"
  | "route missing"
  | "no activity"
  | "no events"
  | "not applicable";
export type Contribution = {
  state: ContributionState;
  /** Rows on the harness's own route in the window. */
  rows: number;
  /** UTC day of the route's first row when it falls inside the window, else null. */
  from: string | null;
};
/** Per harness-scoped signal shown here, per enabled harness. */
export type Contributions = Record<
  string,
  Partial<Record<Harness, Contribution>>
>;

export type ViewResponse = {
  view: ViewId;
  filters: Filters;
  queriedAt: string;
  elapsedMs: number;
  /** Where `elapsedMs` went: the registry, the shared route counts, the view's own SQL. */
  timings: { registryMs: number; routeCountsMs: number; sqlMs: number };
  /**
   * Latest successful refresh of the facts this view reads. `stale` is the loader's own
   * test over the loaders and recent refreshes; `historyStale` is the same for the daily
   * history refreshes (older days only); `failing` names the stale refreshers. Null when unknown.
   */
  asOf: {
    at: string;
    stale: boolean;
    historyStale: boolean;
    failing: string[];
  } | null;
  data: Record<string, Row[]>;
  /** Dataset key (a `data` key) to a short message, for each query that failed; its rows are empty. */
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
