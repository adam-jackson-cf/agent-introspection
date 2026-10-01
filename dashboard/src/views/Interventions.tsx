import { DailyChart, DataTable, TableView } from "../charts";
import type { Row } from "../contracts";
import { HarnessName, Kpi, Panel, Section, useView } from "../components";
import {
  fmtCount,
  fmtPct,
  groupSum,
  harnessLabel,
  num,
  ratio,
  SERIES,
  shortTime,
} from "../format";

const DAY = 86_400_000;
const CLUSTER_DETECTOR = "facts.failure_cluster";
const CORRECTION_DETECTOR = "facts.repeated_correction";
const DETECTOR_LABEL: Record<string, string> = {
  [CLUSTER_DETECTOR]: "Failure cluster",
  [CORRECTION_DETECTOR]: "Repeated correction",
};

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

const strings = (value: unknown): string[] | null =>
  Array.isArray(value) ? value.map(String) : null;

/** Whether a finding or proposal subject is a repeated correction (else a failure cluster). */
const isCorrection = (row: Row) =>
  row.detector === CORRECTION_DETECTOR ||
  (row.project != null && row.correction_kind != null);

/** A finding's subject as one readable line. */
export const subjectLabel = (row: Row): string =>
  isCorrection(row)
    ? `${String(row.project ?? "—")} · ${String(row.correction_kind ?? "—").replaceAll("_", " ")}`
    : row.tool_family != null
      ? `${String(row.tool_family)} · ${String(row.failure_class ?? "—")}`
      : String(row.category ?? "—");

/** The structured success metric as one readable line; null for a legacy free-text metric. */
export const metricLabel = (row: Row): string | null =>
  row.metric == null
    ? null
    : `${String(row.metric)} ≤ ${num(row.max_ratio)}× baseline · ${num(row.evaluation_days)} d after vs ${num(row.baseline_days)} d before${
        strings(row.metric_harnesses)
          ? ` · ${strings(row.metric_harnesses)!.map(harnessLabel).join(", ")}`
          : ""
      }`;

export type DailyFacts = {
  /** Failure-cluster tasks per London day, harness, tool family, and failure class. */
  clusters: Row[];
  /** Labelled tasks and corrected tasks per London day, harness, project, and correction kind. */
  corrections: Row[];
  /** Tasks per London day and harness. */
  tasks: Row[];
};

/**
 * The finding's own task rate in equal runs of whole London days before and after
 * the application day, clipped to the selected range, over the metric's
 * harnesses (the finding's when the metric has none): tasks that hit its failure
 * cluster per task, or labelled tasks in its project whose next prompt corrected
 * the agent the same way per labelled task there. The application day itself
 * mixes pre and post events, so neither window counts it.
 */
export function prePost(
  proposal: Row,
  daily: DailyFacts,
  start: string,
  end: string,
): Row {
  const applied = Date.parse(String(proposal.applied_at));
  const appliedDay = londonDay(applied);
  const harnesses =
    strings(proposal.metric_harnesses) ?? strings(proposal.harnesses);
  const inHarnesses = (row: Row) =>
    harnesses === null || harnesses.includes(String(row.harness));
  const correction = isCorrection(proposal);
  const window = (first: string, last: string) => {
    const inRange = (row: Row) => {
      const day = String(row.day);
      return day >= first && day <= last && inHarnesses(row);
    };
    const sum = (rows: Row[], key: string) =>
      rows.filter(inRange).reduce((total, row) => total + num(row[key]), 0);
    const matched = correction
      ? sum(
          daily.corrections.filter(
            (row) =>
              row.project === proposal.project &&
              row.correction_kind === proposal.correction_kind,
          ),
          "corrected",
        )
      : sum(
          daily.clusters.filter(
            (row) =>
              row.tool_family === proposal.tool_family &&
              row.failure_class === proposal.failure_class,
          ),
          "tasks",
        );
    const taskCount = correction
      ? sum(
          daily.corrections.filter((row) => row.project === proposal.project),
          "tasks",
        )
      : sum(daily.tasks, "tasks");
    return {
      matched,
      tasks: taskCount,
      rate: taskCount > 0 ? matched / taskCount : null,
    };
  };
  // Whole London days on each side: the range's first and last complete days,
  // exclusive of the application day, and the shorter side sets both windows.
  const firstFull = (instant: number) =>
    londonDay(instant - 1) === londonDay(instant)
      ? addDays(londonDay(instant), 1)
      : londonDay(instant);
  const lastFull = (instant: number) =>
    londonDay(instant - 1) === londonDay(instant)
      ? addDays(londonDay(instant), -1)
      : londonDay(instant - 1);
  const between = (from: string, to: string) =>
    Math.round(
      (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY,
    );
  const days = Math.max(
    Math.min(
      between(firstFull(Date.parse(start)), appliedDay),
      between(appliedDay, lastFull(Date.parse(end))),
    ),
    0,
  );
  const pre = window(addDays(appliedDay, -days), addDays(appliedDay, -1));
  const post = window(addDays(appliedDay, 1), addDays(appliedDay, days));
  return {
    target: proposal.target,
    subject: subjectLabel(proposal),
    tier: proposal.tier,
    applied_at: proposal.applied_at,
    days,
    pre_matched: pre.matched,
    pre_tasks: pre.tasks,
    pre_rate: pre.rate,
    post_matched: post.matched,
    post_tasks: post.tasks,
    post_rate: post.rate,
    verdict: proposal.verdict ?? null,
    evaluated_ratio: proposal.evaluated_ratio ?? null,
  };
}

const HarnessList = ({ value }: { value: unknown }) => {
  const list = strings(value);
  if (!list || list.length === 0) return <>—</>;
  return (
    <span className="breakdown">
      {list.map((harness) => (
        <HarnessName key={harness} value={harness} />
      ))}
    </span>
  );
};

const fmtRatio = (value: unknown) =>
  value === null || value === undefined ? "—" : `${num(value).toFixed(2)}×`;

const dailyFacts = (data: Record<string, Row[]>): DailyFacts => ({
  clusters: data.cluster_daily ?? [],
  corrections: data.correction_daily ?? [],
  tasks: data.task_daily ?? [],
});

const BASELINE_SERIES = [
  {
    key: "failures",
    label: "Cluster failures per 100 tasks",
    color: SERIES[0]!,
  },
  {
    key: "corrected",
    label: "Corrections per 100 labelled tasks",
    color: SERIES[1]!,
  },
];

/** Cluster failures and corrections per day, as rates over that day's tasks. */
function baselineByDay(daily: DailyFacts) {
  const failuresByDay = groupSum(daily.clusters, "day", ["occurrences"]);
  const correctedByDay = groupSum(daily.corrections, "day", [
    "corrected",
    "tasks",
  ]);
  const tasksByDay = groupSum(daily.tasks, "day", ["tasks"]);
  return [...new Set([...tasksByDay.keys(), ...correctedByDay.keys()])]
    .sort()
    .map((day) => ({
      day,
      failures: ratio(
        failuresByDay.get(day)?.occurrences ?? 0,
        tasksByDay.get(day)?.tasks ?? 0,
      ),
      corrected: ratio(
        correctedByDay.get(day)?.corrected ?? 0,
        correctedByDay.get(day)?.tasks ?? 0,
      ),
    }));
}

const tierCounts = (proposals: Row[]) =>
  [
    ...groupSum(
      proposals.map((row) => ({ ...row, n: 1 })),
      "tier",
      ["n"],
    ),
  ].map(([tier, sums]) => ({
    tier: tier === "null" ? "not recorded" : tier,
    proposals: sums.n,
  }));
/** Decision events belonging to the given (in-scope) proposals. */
export const historyFor = (proposals: Row[], events: Row[]): Row[] => {
  const ids = new Set(proposals.map((row) => row.id));
  return events.filter((event) => ids.has(event.proposal_id));
};

export default function Interventions({
  data,
}: {
  data: Record<string, Row[]>;
}) {
  const { filters } = useView();
  // Findings span harnesses; a harness filter keeps those seen in it. Findings
  // without a harness list (older stores) are shown under All only.
  const inScope = (row: Row) =>
    filters.harness === "" ||
    (strings(row.metric_harnesses) ?? strings(row.harnesses) ?? []).includes(
      filters.harness,
    );
  const findings = (data.findings ?? []).filter(inScope);
  const proposals = (data.proposals ?? []).filter(inScope);
  const history = historyFor(proposals, data.proposal_events ?? []);
  const applied = proposals.filter((row) => row.applied_at);
  const corrections = findings.filter(
    (row) => row.detector === CORRECTION_DETECTOR,
  );
  const daily = dailyFacts(data);
  const comparisons = applied
    .filter((row) => {
      const at = Date.parse(String(row.applied_at));
      return at > Date.parse(filters.start) && at < Date.parse(filters.end);
    })
    .map((row) => prePost(row, daily, filters.start, filters.end));
  const verdicts = groupSum(
    applied
      .filter((row) => row.verdict != null)
      .map((row) => ({ verdict: row.verdict, n: 1 })),
    "verdict",
    ["n"],
  );
  const verdictCount = (verdict: string) => verdicts.get(verdict)?.n ?? 0;
  const baseline = baselineByDay(daily);
  const tiers = tierCounts(proposals);
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Active findings"
          value={fmtCount(findings.length)}
          detail={`${findings.filter((row) => row.state === "actionable").length} actionable`}
          signals={["intervene.findings"]}
        />
        <Kpi
          title="Proposals"
          value={fmtCount(proposals.length)}
          detail={`${proposals.filter((row) => row.state === "pending").length} pending`}
          signals={["intervene.tier_audit"]}
        />
        <Kpi
          title="Applied interventions"
          value={fmtCount(applied.length)}
          detail={`${fmtCount(verdictCount("validated"))} validated · ${fmtCount(verdictCount("regressed"))} regressed · ${fmtCount(verdictCount("inconclusive"))} inconclusive`}
          signals={["intervene.post_recurrence"]}
        />
        <Kpi
          title="Repeated corrections"
          value={fmtCount(corrections.length)}
          detail="practices users keep correcting, not yet codified"
          signals={["intervene.practice_recurrence"]}
        />
      </Section>
      <Section title="Did an intervention reduce what it targeted?">
        <Panel
          title="Post-intervention recurrence"
          subtitle="The finding's own cluster or correction per task, equal windows before and after application; the verdict is the one `facts sync` recorded"
          signals={["intervene.post_recurrence"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "target", label: "Target" },
              { key: "subject", label: "Finding" },
              { key: "tier", label: "Tier" },
              {
                key: "applied_at",
                label: "Applied (UTC)",
                render: (row) => shortTime(row.applied_at),
              },
              { key: "days", label: "Window days", numeric: true },
              { key: "pre_matched", label: "Before", numeric: true },
              { key: "pre_tasks", label: "Tasks before", numeric: true },
              { key: "post_matched", label: "After", numeric: true },
              { key: "post_tasks", label: "Tasks after", numeric: true },
              {
                key: "change",
                label: "Rate change",
                numeric: true,
                render: (row) =>
                  row.pre_rate === null ||
                  row.post_rate === null ||
                  num(row.pre_rate) === 0
                    ? "—"
                    : `${((100 * (num(row.post_rate) - num(row.pre_rate))) / num(row.pre_rate)).toFixed(0)}%`,
              },
              {
                key: "verdict",
                label: "Verdict",
                render: (row) =>
                  row.verdict == null ? "not evaluated" : String(row.verdict),
              },
              {
                key: "evaluated_ratio",
                label: "Recorded ratio",
                numeric: true,
                render: (row) => fmtRatio(row.evaluated_ratio),
              },
            ]}
            rows={comparisons}
            empty="No intervention has been applied in the selected range; the comparison starts once a proposal reaches applied."
          />
        </Panel>
        <Panel
          title="Recurrence baseline"
          subtitle="Per Europe/London day: failure-cluster failures per 100 attributed tasks, and next-prompt corrections per 100 labelled tasks with a project"
          signals={["intervene.post_recurrence"]}
          span={12}
        >
          <DailyChart
            rows={baseline}
            series={BASELINE_SERIES}
            kind="line"
            format={(value) => (value === null ? "—" : value.toFixed(0))}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...BASELINE_SERIES.map((entry) => ({
                key: entry.key,
                label: entry.label,
                numeric: true,
              })),
            ]}
            rows={baseline}
          />
        </Panel>
      </Section>
      <Section title="Findings and proposals">
        <Panel
          title="Active findings"
          subtitle="Failure clusters and repeated corrections promoted by `facts sync` (7 Europe/London days)"
          signals={["intervene.findings"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "detector",
                label: "Detector",
                render: (row) =>
                  DETECTOR_LABEL[String(row.detector)] ?? String(row.detector),
              },
              {
                key: "subject",
                label:
                  "Tool family · failure class, or project · correction kind",
                render: (row) => subjectLabel(row),
              },
              {
                key: "harnesses",
                label: "Harnesses",
                render: (row) => <HarnessList value={row.harnesses} />,
              },
              {
                key: "impact",
                label: "Impact",
                numeric: true,
                render: (row) =>
                  row.impact == null ? "—" : fmtCount(num(row.impact)),
              },
              { key: "state", label: "State" },
              { key: "occurrences", label: "Count", numeric: true },
              { key: "tasks", label: "Tasks", numeric: true },
              { key: "days", label: "Days", numeric: true },
              {
                key: "last_seen",
                label: "Last seen (UTC)",
                render: (row) => shortTime(row.last_seen),
              },
            ]}
            rows={findings}
            empty="No findings recorded."
          />
        </Panel>
        <Panel
          title="Proposals"
          subtitle="Each proposal's structured success metric and its latest recorded evaluation"
          signals={["intervene.post_recurrence", "intervene.tier_audit"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "target", label: "Target" },
              {
                key: "subject",
                label: "Finding",
                render: (row) => subjectLabel(row),
              },
              { key: "tier", label: "Tier" },
              { key: "state", label: "State" },
              {
                key: "metric",
                label: "Success metric",
                render: (row) => metricLabel(row) ?? "free text (legacy)",
              },
              {
                key: "applied_at",
                label: "Applied (UTC)",
                render: (row) => shortTime(row.applied_at),
              },
              {
                key: "verdict",
                label: "Verdict",
                render: (row) =>
                  row.verdict == null ? "not evaluated" : String(row.verdict),
              },
              {
                key: "evaluated_ratio",
                label: "Ratio",
                numeric: true,
                render: (row) => fmtRatio(row.evaluated_ratio),
              },
              {
                key: "rates",
                label: "Baseline → evaluation rate",
                numeric: true,
                render: (row) =>
                  row.verdict == null
                    ? "—"
                    : `${fmtPct(row.baseline_rate == null ? null : 100 * num(row.baseline_rate))} → ${fmtPct(row.evaluation_rate == null ? null : 100 * num(row.evaluation_rate))}`,
              },
            ]}
            rows={proposals}
            empty="No proposals yet; `agent-introspection candidates export` drafts one for the next actionable finding."
          />
        </Panel>
        <Panel
          title="Approval history"
          subtitle="Each decision event on the proposals above: who, when, and the recorded reason"
          signals={["intervene.tier_audit"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "at",
                label: "When (UTC)",
                render: (row) => shortTime(row.at),
              },
              { key: "target", label: "Target" },
              { key: "event", label: "Event" },
              {
                key: "actor",
                label: "Actor",
                render: (row) => (row.actor == null ? "—" : String(row.actor)),
              },
              {
                key: "summary",
                label: "Reason / evidence",
                render: (row) =>
                  row.summary == null ? "—" : String(row.summary),
              },
            ]}
            rows={history}
            empty="No proposal decisions recorded."
          />
        </Panel>
        <Panel
          title="Enforcement-tier audit"
          subtitle="Proposals by selected tier"
          signals={["intervene.tier_audit"]}
        >
          <DataTable
            columns={[
              { key: "tier", label: "Tier" },
              { key: "proposals", label: "Proposals", numeric: true },
            ]}
            rows={tiers}
            empty="No proposals yet."
          />
        </Panel>
        <Panel
          title="Uncodified practice recurrence"
          subtitle="Repeated-correction findings: the same correction in one project across sessions"
          signals={["intervene.practice_recurrence"]}
        >
          <DataTable
            columns={[
              { key: "project", label: "Project" },
              {
                key: "correction_kind",
                label: "Correction",
                render: (row) =>
                  String(row.correction_kind ?? "—").replaceAll("_", " "),
              },
              {
                key: "harnesses",
                label: "Harnesses",
                render: (row) => <HarnessList value={row.harnesses} />,
              },
              { key: "task_types", label: "Task types" },
              { key: "sessions", label: "Sessions", numeric: true },
              { key: "impact", label: "Tasks", numeric: true },
              { key: "state", label: "State" },
            ]}
            rows={corrections}
            empty="No correction repeats in one project across sessions."
          />
        </Panel>
      </Section>
    </>
  );
}
