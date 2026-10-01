import { DailyChart, DataTable, TableView, type Column } from "../charts";
import { HARNESSES, type Row } from "../contracts";
import { HarnessName, Kpi, Panel, Section, useView } from "../components";
import {
  fmtCount,
  fmtPct,
  HARNESS_COLOR,
  HARNESS_LABEL,
  num,
  pivotDaily,
  ratio,
  sumRows,
} from "../format";

/** A signature is localized when at least 80% of its attributed occurrences share one working directory. */
const LOCALIZED = 0.8;

const harnessOrder = (harness: unknown) =>
  (HARNESSES as readonly unknown[]).indexOf(harness);

/**
 * Recombines per-harness failure-cluster rows into one row per cluster (tool
 * family × failure class): occurrences and tasks are the sums of the harness
 * rows (tasks are harness-scoped), days are the cluster's own distinct days, and
 * `breakdown` keeps each harness's rows in registry order, never ranked.
 */
export function recombineClusters(rows: Row[]): Row[] {
  const clusters = new Map<string, Row[]>();
  for (const row of rows) {
    const key = `${String(row.tool_family)}\u0000${String(row.failure_class)}`;
    clusters.set(key, [...(clusters.get(key) ?? []), row]);
  }
  return [...clusters.entries()].map(([key, members]) => {
    const sums = sumRows(members, ["occurrences", "tasks"]);
    const breakdown = [...members].sort(
      (a, b) => harnessOrder(a.harness) - harnessOrder(b.harness),
    );
    return {
      key,
      tool_family: members[0]!.tool_family ?? "",
      failure_class: members[0]!.failure_class ?? "",
      occurrences: sums.occurrences!,
      tasks: sums.tasks!,
      days: num(members[0]!.cluster_days),
      harnesses: breakdown.map((row) => String(row.harness)),
      breakdown: breakdown.map(
        (row) => `${String(row.harness)}:${num(row.occurrences)}`,
      ),
      tools: [
        ...new Set(
          members.flatMap((row) =>
            Array.isArray(row.tools) ? row.tools.map(String) : [],
          ),
        ),
      ].sort(),
      last_seen: members
        .map((row) => String(row.last_seen ?? ""))
        .sort()
        .at(-1)!,
    };
  });
}

/** Each harness's occurrences of one cluster, in registry order. */
function Breakdown({ row }: { row: Row }) {
  const parts = Array.isArray(row.breakdown) ? row.breakdown.map(String) : [];
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

export default function Recurrence({ data }: { data: Record<string, Row[]> }) {
  const { filters } = useView();
  const targets = sumRows(data.target_summary ?? [], ["targets", "recurring"]);
  const summary = data.cluster_summary?.[0] ?? {};
  const actionable = recombineClusters(data.actionable ?? []);
  const recurring = recombineClusters(data.clusters ?? []);
  const concentration = data.concentration ?? [];
  const localized = concentration.filter(
    (row) => num(row.top_count) >= LOCALIZED * num(row.attributed),
  );
  const series = HARNESSES.filter(
    (harness) => filters.harness === "" || harness === filters.harness,
  ).map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const daily = pivotDaily(
    data.daily ?? [],
    (row) => String(row.harness),
    ["failures"],
    (sums) => sums.failures!,
  );
  const weekEnd = filters.end.slice(0, 10);
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Actionable repeats"
          value={fmtCount(actionable.length)}
          detail={`failure clusters over the 7 London days to ${weekEnd}`}
          signals={["recur.actionable"]}
        />
        <Kpi
          title="Recurring failure clusters"
          value={fmtCount(num(summary.recurring))}
          detail={`of ${fmtCount(num(summary.clusters))} clusters, seen in ≥ 2 tasks`}
          signals={["recur.signatures"]}
        />
        <Kpi
          title="Recurring targets"
          value={fmtCount(targets.recurring!)}
          detail={`of ${fmtCount(targets.targets!)} files touched in ≥ 2 tasks`}
          signals={["recur.targets"]}
        />
        <Kpi
          title="Localized signatures"
          value={fmtPct(ratio(localized.length, concentration.length), 0)}
          detail={`${localized.length} of ${concentration.length} signatures with ≥ 80% in one project`}
          signals={["recur.project_concentration"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily tool failures with a failure class"
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
          subtitle="Failure clusters with ≥ 3 occurrences in ≥ 2 tasks on ≥ 2 days, or ≥ 5 occurrences in ≥ 3 tasks, over 7 Europe/London days"
          signals={["recur.actionable"]}
          span={12}
        >
          <DataTable
            columns={CLUSTER_COLUMNS}
            rows={actionable}
            empty="No failure cluster crosses the threshold in the 7 days ending on the window end."
          />
        </Panel>
      </Section>
      <Section title="What recurs across tasks?">
        <Panel
          title="Recurring failure clusters"
          subtitle="Tool family × failure class seen in ≥ 2 tasks over the selected window, across harnesses"
          signals={["recur.signatures"]}
          span={12}
        >
          <DataTable columns={CLUSTER_COLUMNS} rows={recurring} />
        </Panel>
        <Panel
          title="Recurring targets"
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
          />
        </Panel>
        <Panel
          title="Project concentration"
          subtitle="Where each repeated signature occurs; project from the session-context hooks"
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
                  fmtPct(ratio(num(row.top_count), num(row.attributed)), 0),
              },
            ]}
            rows={concentration}
          />
        </Panel>
      </Section>
    </>
  );
}
