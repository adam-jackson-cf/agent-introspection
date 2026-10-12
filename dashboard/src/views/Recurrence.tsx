import { DailyChart, DataTable, TableView, type Column } from "../charts";
import { HARNESSES, type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  Section,
  ViewLink,
  useView,
  useSelectedHarnesses,
} from "../components";
import { fmtCount, fmtPct, HARNESS_COLOR, HARNESS_LABEL, num } from "../format";

const harnessOrder = (harness: unknown) =>
  (HARNESSES as readonly unknown[]).indexOf(harness);

const stringList = (value: unknown) =>
  Array.isArray(value) ? value.map(String) : [];

/** The Europe/London calendar date of an ISO instant, which the SQL week cuts at. */
export function londonDate(instant: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/London",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(instant));
}

/**
 * Failure-cluster rows arrive finished (one per tool family × failure class);
 * only the per-harness breakdown is put in registry order, never ranked.
 */
export function clusterRows(rows: Row[]): Row[] {
  return rows.map((row) => ({
    ...row,
    breakdown: stringList(row.breakdown).sort(
      (a, b) => harnessOrder(a.split(":")[0]) - harnessOrder(b.split(":")[0]),
    ),
  }));
}

/** Long daily rows (one per day and harness, finished) as one row per day for the chart. */
function dailyWide(rows: Row[]): Row[] {
  const days = new Map<string, Row>();
  for (const row of rows) {
    const day = String(row.day);
    days.set(day, {
      ...days.get(day),
      day,
      [String(row.harness)]: row.failures,
    });
  }
  return [...days.values()].sort((a, b) =>
    String(a.day).localeCompare(String(b.day)),
  );
}

/** Each harness's occurrences of one cluster, in registry order. */
function Breakdown({ row }: { row: Row }) {
  const parts = stringList(row.breakdown);
  return (
    <span className="breakdown">
      {parts.map((part) => {
        const [harness, count] = part.split(":");
        return (
          <span key={part}>
            <HarnessName value={harness} /> {count}
          </span>
        );
      })}
    </span>
  );
}

const CLUSTER_COLUMNS: Column[] = [
  { key: "tool_family", label: "Tool family" },
  { key: "failure_class", label: "Failure class" },
  {
    key: "breakdown",
    label: "Harnesses (count)",
    render: (row) => <Breakdown row={row} />,
  },
  { key: "tools", label: "Tools" },
  { key: "occurrences", label: "Count", numeric: true },
  { key: "tasks", label: "Tasks", numeric: true },
  { key: "days", label: "Days", numeric: true },
  {
    key: "last_seen",
    label: "Last seen (UTC)",
    render: (row) => String(row.last_seen).slice(0, 16),
  },
];

const firstRow = (rows: Row[] | undefined): Row => rows?.[0] ?? {};

function deriveRecurrence(data: Record<string, Row[]>) {
  const concentration = data.concentration ?? [];
  return {
    targets: firstRow(data.target_summary),
    summary: firstRow(data.cluster_summary),
    actionable: clusterRows(data.actionable ?? []),
    recurring: clusterRows(data.clusters ?? []),
    concentration,
    localized: firstRow(concentration),
    daily: dailyWide(data.daily ?? []),
    actionableTotal: num(firstRow(data.actionable_summary).actionable),
    targetsTotal: num(firstRow(data.targets).total_files),
    concentrationTotal: num(firstRow(concentration).total_signatures),
  };
}

export default function Recurrence({ data }: { data: Record<string, Row[]> }) {
  const { filters } = useView();
  const selected = useSelectedHarnesses();
  const {
    targets,
    summary,
    actionable,
    recurring,
    concentration,
    localized,
    daily,
    actionableTotal,
    targetsTotal,
    concentrationTotal,
  } = deriveRecurrence(data);
  const series = selected.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const weekEnd = londonDate(filters.end);
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Actionable repeats"
          source="actionable_summary"
          value={fmtCount(actionableTotal)}
          detail={`failure clusters over the 7 London days to ${weekEnd}`}
          signals={["recur.actionable"]}
        />
        <Kpi
          title="Recurring failure clusters"
          source="cluster_summary"
          value={fmtCount(num(summary.recurring))}
          detail={`of ${fmtCount(num(summary.clusters))} clusters, seen in ≥ 2 tasks`}
          signals={["recur.signatures"]}
        />
        <Kpi
          title="Recurring targets"
          source="target_summary"
          value={fmtCount(num(targets.recurring))}
          detail={`of ${fmtCount(num(targets.targets))} files touched in ≥ 2 tasks`}
          signals={["recur.targets"]}
        />
        <Kpi
          title="Localized signatures"
          source="concentration"
          value={fmtPct(
            typeof localized.localized_rate === "number"
              ? localized.localized_rate
              : null,
            0,
          )}
          detail={`${num(localized.localized_signatures)} of ${num(localized.shown)} signatures with ≥ 80% in one project`}
          signals={["recur.project_concentration"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily tool failures with a failure class"
          source="daily"
          subtitle="Europe/London days, stacked by harness; evaluation workspaces excluded"
          signals={["recur.signatures"]}
          span={12}
        >
          <DailyChart
            rows={daily}
            series={series}
            kind="stack"
            format={fmtCount}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...series.map((entry) => ({
                key: entry.key,
                label: entry.label,
                numeric: true,
              })),
            ]}
            rows={daily}
          />
        </Panel>
      </Section>
      <Section title="What crosses the evidence threshold?">
        <Panel
          title="Actionable repeats"
          source={["actionable", "actionable_summary"]}
          subtitle="Failure clusters with ≥ 3 occurrences in ≥ 2 tasks on ≥ 2 days, or ≥ 5 occurrences in ≥ 3 tasks, over 7 Europe/London days; classes naming only an error type or header are never actionable"
          signals={["recur.actionable"]}
          span={12}
        >
          <DataTable
            columns={CLUSTER_COLUMNS}
            rows={actionable}
            empty="No failure cluster crosses the threshold in the 7 days ending on the window end."
            total={actionableTotal}
          />
        </Panel>
      </Section>
      <Section title="What recurs across tasks?">
        <Panel
          title="Recurring failure clusters"
          source={["clusters", "cluster_summary"]}
          subtitle="Tool family × failure class seen in ≥ 2 tasks over the selected window, across harnesses"
          signals={["recur.signatures"]}
          span={12}
        >
          <DataTable
            columns={CLUSTER_COLUMNS}
            rows={recurring}
            total={num(summary.recurring)}
          />
          <ViewLink view="tools">Open Tool failures</ViewLink>
        </Panel>
        <Panel
          title="Recurring targets"
          source={["targets", "target_summary"]}
          subtitle="Files touched in ≥ 2 tasks, with failures on them"
          signals={["recur.targets"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "target", label: "File" },
              { key: "harnesses", label: "Harnesses" },
              { key: "tasks", label: "Tasks", numeric: true },
              { key: "days", label: "Days", numeric: true },
              { key: "calls", label: "Calls", numeric: true },
              { key: "failed", label: "Failed", numeric: true },
            ]}
            rows={data.targets ?? []}
            total={targetsTotal}
          />
          <ViewLink view="guardrails">
            Open Verification and guardrails
          </ViewLink>
        </Panel>
        <Panel
          title="Project concentration"
          source="concentration"
          subtitle="Where each repeated signature occurs (first 80 characters shown, grouped by a hash of the full line); project from the harness session stores"
          signals={["recur.project_concentration"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "signature", label: "Signature" },
              { key: "occurrences", label: "Count", numeric: true },
              { key: "projects", label: "Projects", numeric: true },
              { key: "top_project", label: "Top project" },
              {
                key: "share",
                label: "Top share",
                numeric: true,
                render: (row) =>
                  fmtPct(
                    typeof row.top_share === "number" ? row.top_share : null,
                    0,
                  ),
              },
            ]}
            rows={concentration}
            total={concentrationTotal}
          />
        </Panel>
      </Section>
    </>
  );
}
