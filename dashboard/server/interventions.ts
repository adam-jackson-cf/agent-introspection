import type { Row } from "../src/contracts";

const DAY = 86_400_000;
const CORRECTION_DETECTOR = "facts.repeated_correction";
const DAILY_INPUTS = ["cluster_daily", "task_daily"];
const OUTCOME_FIELDS = [
  "verdict",
  "evaluated_ratio",
  "baseline_rate",
  "evaluation_rate",
];

export type InterventionScope = { harness: string; start: string; end: string };

type DailyFacts = { clusters: Row[]; tasks: Row[] };

const strings = (value: unknown): string[] | null =>
  Array.isArray(value) ? value.map(String) : null;

const londonDay = (instant: number) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/London" }).format(
    instant,
  );

const addDays = (day: string, offset: number) =>
  new Date(Date.parse(`${day}T00:00:00Z`) + offset * DAY)
    .toISOString()
    .slice(0, 10);

const between = (from: string, to: string) =>
  Math.round(
    (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY,
  );

const withKind = (row: Row): Row => ({
  ...row,
  kind: row["detector"] === CORRECTION_DETECTOR ? "correction" : "cluster",
});

const inScope = (row: Row, harness: string) =>
  harness === "" ||
  (
    strings(row["metric_harnesses"]) ??
    strings(row["harnesses"]) ??
    []
  ).includes(harness);

const total = (rows: Row[], key: string) =>
  rows.reduce((sum, row) => sum + Number(row[key]), 0);

const firstFull = (instant: number) =>
  londonDay(instant - 1) === londonDay(instant)
    ? addDays(londonDay(instant), 1)
    : londonDay(instant);
const lastFull = (instant: number) =>
  londonDay(instant - 1) === londonDay(instant)
    ? addDays(londonDay(instant), -1)
    : londonDay(instant - 1);

function windowCounts(
  proposal: Row,
  daily: DailyFacts,
  first: string,
  last: string,
) {
  const harnesses =
    strings(proposal["metric_harnesses"]) ?? strings(proposal["harnesses"]);
  const within = (rows: Row[]) =>
    rows.filter((row) => {
      const day = String(row["day"]);
      return (
        day >= first &&
        day <= last &&
        (harnesses === null || harnesses.includes(String(row["harness"])))
      );
    });
  const matched = total(
    within(
      daily.clusters.filter(
        (row) =>
          row["tool_family"] === proposal["tool_family"] &&
          row["failure_class"] === proposal["failure_class"],
      ),
    ),
    "tasks",
  );
  const tasks = total(within(daily.tasks), "tasks");
  return { matched, tasks, rate: tasks > 0 ? matched / tasks : null };
}

const rateChange = (before: number | null, after: number | null) =>
  before === null || after === null || before === 0
    ? null
    : (100 * (after - before)) / before;

export function prePost(
  proposal: Row,
  daily: DailyFacts,
  start: string,
  end: string,
): Row {
  const appliedDay = londonDay(Date.parse(String(proposal["applied_at"])));
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
  const tagged = withKind(proposal);
  const unmeasured = { matched: null, tasks: null, rate: null };
  const [before, after] =
    tagged["kind"] === "cluster" ? [pre, post] : [unmeasured, unmeasured];
  return {
    ...tagged,
    days,
    pre_matched: before.matched,
    pre_tasks: before.tasks,
    pre_rate: before.rate,
    post_matched: after.matched,
    post_tasks: after.tasks,
    post_rate: after.rate,
    change_pct: rateChange(before.rate, after.rate),
    verdict: proposal["verdict"] ?? null,
    evaluated_ratio: proposal["evaluated_ratio"] ?? null,
  };
}

function tierCounts(proposals: Row[]): Row[] {
  const counts = new Map<string, number>();
  for (const row of proposals) {
    const tier = row["tier"] == null ? "not recorded" : String(row["tier"]);
    counts.set(tier, (counts.get(tier) ?? 0) + 1);
  }
  return [...counts].map(([tier, count]) => ({ tier, proposals: count }));
}

function kpiRow(
  findings: Row[],
  proposals: Row[],
  applied: Row[],
  inRange: number,
): Row {
  const verdicts = (verdict: string | null) =>
    applied.filter((row) => (row["verdict"] ?? null) === verdict).length;
  return {
    findings: findings.length,
    actionable: findings.filter((row) => row["state"] === "actionable").length,
    proposals: proposals.length,
    pending: proposals.filter((row) => row["state"] === "pending").length,
    applied: applied.length,
    applied_in_range: inRange,
    validated: verdicts("validated"),
    regressed: verdicts("regressed"),
    inconclusive: verdicts("inconclusive"),
    not_evaluated: applied.filter((row) => row["verdict"] == null).length,
  };
}

export function interventionsData(
  clickhouse: Record<string, Row[]>,
  workflow: Record<string, Row[]>,
  scope: InterventionScope,
  outcomes: boolean,
): Record<string, Row[]> {
  const findings = (workflow["findings"] ?? [])
    .filter((row) => inScope(row, scope.harness))
    .map(withKind);
  const proposals = (workflow["proposals"] ?? [])
    .filter((row) => inScope(row, scope.harness))
    .map(withKind);
  const ids = new Set(proposals.map((row) => row["id"]));
  const events = (workflow["proposal_events"] ?? []).filter((event) =>
    ids.has(event["proposal_id"]),
  );
  if (!outcomes) {
    const counts = kpiRow(findings, proposals, [], 0);
    return {
      kpis: [
        {
          findings: counts["findings"],
          actionable: counts["actionable"],
          proposals: counts["proposals"],
          pending: counts["pending"],
        },
      ],
      findings,
      proposals: proposals.map((row) =>
        Object.fromEntries(
          Object.entries(row).filter(([key]) => !OUTCOME_FIELDS.includes(key)),
        ),
      ),
      proposal_events: events.filter((event) => event["event"] !== "evaluated"),
      tiers: tierCounts(proposals),
    };
  }
  const applied = proposals.filter((row) => row["applied_at"]);
  const start = Date.parse(scope.start);
  const end = Date.parse(scope.end);
  const daily: DailyFacts = {
    clusters: clickhouse["cluster_daily"] ?? [],
    tasks: clickhouse["task_daily"] ?? [],
  };
  const comparisons = applied
    .filter((row) => {
      const at = Date.parse(String(row["applied_at"]));
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
    proposal_events: events,
    tiers: tierCounts(proposals),
    comparisons,
  };
}
