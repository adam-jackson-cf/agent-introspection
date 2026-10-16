import type { Row } from "../src/contracts";

const DAY = 86_400_000;
const CORRECTION_DETECTOR = "facts.repeated_correction";
/** The datasets the page never sees: inputs of the before/after windows. */
const DAILY_INPUTS = ["cluster_daily", "task_daily"];

export type InterventionScope = { harness: string; start: string; end: string };

/** Failure-cluster tasks and all tasks per London day. */
type DailyFacts = { clusters: Row[]; tasks: Row[] };

const strings = (value: unknown): string[] | null =>
  Array.isArray(value) ? value.map(String) : null;

/** The Europe/London calendar day of an instant, matching the daily facts buckets. */
const londonDay = (instant: number) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London" }).format(
    instant,
  );

/** The calendar day `offset` days from `day`, both as YYYY-MM-DD. */
const addDays = (day: string, offset: number) =>
  new Date(Date.parse(`${day}T00:00:00Z`) + offset * DAY)
    .toISOString()
    .slice(0, 10);

/** Whole days from `from` to `to` (YYYY-MM-DD). */
const between = (from: string, to: string) =>
  Math.round(
    (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY,
  );

/**
 * Tags a finding or proposal with its subject kind: `cluster`, or `correction` for a
 * finding of the retired repeated-correction detector, kept as dormant history.
 */
const withKind = (row: Row): Row => ({
  ...row,
  kind: row.detector === CORRECTION_DETECTOR ? "correction" : "cluster",
});

/**
 * Findings span harnesses; a harness filter keeps those seen in it (the metric's
 * harnesses, else the finding's). Rows without a harness list (older stores)
 * belong to All only.
 */
const inScope = (row: Row, harness: string) =>
  harness === "" ||
  (strings(row.metric_harnesses) ?? strings(row.harnesses) ?? []).includes(
    harness,
  );

const total = (rows: Row[], key: string) =>
  rows.reduce((sum, row) => sum + Number(row[key]), 0);

/** The range's first and last complete London days (inclusive). */
const firstFull = (instant: number) =>
  londonDay(instant - 1) === londonDay(instant)
    ? addDays(londonDay(instant), 1)
    : londonDay(instant);
const lastFull = (instant: number) =>
  londonDay(instant - 1) === londonDay(instant)
    ? addDays(londonDay(instant), -1)
    : londonDay(instant - 1);

/** Matched events and tasks of one proposal in one window, over its harnesses. */
function windowCounts(
  proposal: Row,
  daily: DailyFacts,
  first: string,
  last: string,
) {
  const harnesses =
    strings(proposal.metric_harnesses) ?? strings(proposal.harnesses);
  const within = (rows: Row[]) =>
    rows.filter((row) => {
      const day = String(row.day);
      return (
        day >= first &&
        day <= last &&
        (harnesses === null || harnesses.includes(String(row.harness)))
      );
    });
  const matched = total(
    within(
      daily.clusters.filter(
        (row) =>
          row.tool_family === proposal.tool_family &&
          row.failure_class === proposal.failure_class,
      ),
    ),
    "tasks",
  );
  const tasks = total(within(daily.tasks), "tasks");
  return { matched, tasks, rate: tasks > 0 ? matched / tasks : null };
}

/**
 * The finding's own task rate in equal runs of whole London days before and after
 * the application day, clipped to the selected range, over the metric's
 * harnesses (the finding's when the metric has none): tasks that hit its failure
 * cluster per task. The application day itself
 * mixes pre and post events, so neither window counts it. `change_pct` is the
 * rate change, NULL when either rate is missing or the earlier rate is 0.
 */
export function prePost(
  proposal: Row,
  daily: DailyFacts,
  start: string,
  end: string,
): Row {
  const appliedDay = londonDay(Date.parse(String(proposal.applied_at)));
  // The shorter side of the range around the application day sets both windows.
  const days = Math.max(
    Math.min(
      between(firstFull(Date.parse(start)), appliedDay),
      between(appliedDay, lastFull(Date.parse(end))),
    ),
    0,
  );
  const pre = windowCounts(
    proposal,
    daily,
    addDays(appliedDay, -days),
    addDays(appliedDay, -1),
  );
  const post = windowCounts(
    proposal,
    daily,
    addDays(appliedDay, 1),
    addDays(appliedDay, days),
  );
  const changed =
    pre.rate === null || post.rate === null || pre.rate === 0
      ? null
      : (100 * (post.rate - pre.rate)) / pre.rate;
  return {
    ...withKind(proposal),
    days,
    pre_matched: pre.matched,
    pre_tasks: pre.tasks,
    pre_rate: pre.rate,
    post_matched: post.matched,
    post_tasks: post.tasks,
    post_rate: post.rate,
    change_pct: changed,
    verdict: proposal.verdict ?? null,
    evaluated_ratio: proposal.evaluated_ratio ?? null,
  };
}

/** Proposals per tier in first-seen order; a missing tier is "not recorded". */
function tierCounts(proposals: Row[]): Row[] {
  const counts = new Map<string, number>();
  for (const row of proposals) {
    const tier = row.tier == null ? "not recorded" : String(row.tier);
    counts.set(tier, (counts.get(tier) ?? 0) + 1);
  }
  return [...counts].map(([tier, count]) => ({ tier, proposals: count }));
}

/** The four KPI counts and the applied verdict parts, one row. */
function kpiRow(
  findings: Row[],
  proposals: Row[],
  applied: Row[],
  inRange: number,
): Row {
  const verdicts = (verdict: string | null) =>
    applied.filter((row) => (row.verdict ?? null) === verdict).length;
  return {
    findings: findings.length,
    actionable: findings.filter((row) => row.state === "actionable").length,
    proposals: proposals.length,
    pending: proposals.filter((row) => row.state === "pending").length,
    applied: applied.length,
    applied_in_range: inRange,
    validated: verdicts("validated"),
    regressed: verdicts("regressed"),
    inconclusive: verdicts("inconclusive"),
    not_evaluated: applied.filter((row) => row.verdict == null).length,
  };
}

/**
 * Turns the ClickHouse daily facts and the SQLite workflow rows into the page's
 * datasets: harness-scoped findings, proposals and decision history, the KPI row,
 * proposals per tier, and the before/after
 * comparison of every proposal applied strictly inside the range.
 */
export function interventionsData(
  clickhouse: Record<string, Row[]>,
  workflow: Record<string, Row[]>,
  scope: InterventionScope,
): Record<string, Row[]> {
  const findings = (workflow.findings ?? [])
    .filter((row) => inScope(row, scope.harness))
    .map(withKind);
  const proposals = (workflow.proposals ?? [])
    .filter((row) => inScope(row, scope.harness))
    .map(withKind);
  const ids = new Set(proposals.map((row) => row.id));
  const applied = proposals.filter((row) => row.applied_at);
  const start = Date.parse(scope.start);
  const end = Date.parse(scope.end);
  const daily: DailyFacts = {
    clusters: clickhouse.cluster_daily ?? [],
    tasks: clickhouse.task_daily ?? [],
  };
  const comparisons = applied
    .filter((row) => {
      const at = Date.parse(String(row.applied_at));
      return at > start && at < end;
    })
    .map((row) => prePost(row, daily, scope.start, scope.end));
  const passed = Object.fromEntries(
    Object.entries(clickhouse).filter(([key]) => !DAILY_INPUTS.includes(key)),
  );
  return {
    ...passed,
    kpis: [kpiRow(findings, proposals, applied, comparisons.length)],
    findings,
    proposals,
    proposal_events: (workflow.proposal_events ?? []).filter((event) =>
      ids.has(event.proposal_id),
    ),
    tiers: tierCounts(proposals),
    comparisons,
  };
}
