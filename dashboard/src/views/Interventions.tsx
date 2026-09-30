import { DailyChart, DataTable, TableView } from "../charts";
import type { Row } from "../contracts";
import { HarnessName, Kpi, Panel, Section, useView } from "../components";
import { fmtCount, groupSum, num, ratio, SERIES, shortTime } from "../format";

const DAY = 86_400_000;

/** Registry formula text for a signal the producers cannot support. */
function Unsupported({ signal }: { signal: string }) {
  const { registry } = useView();
  const definition = registry.signals.find((entry) => entry.signal === signal);
  return (
    <div className="not-emitted" role="status">
      <b>Unsupported</b>
      <p>{definition?.formula}</p>
    </div>
  );
}

/**
 * Occurrences of the target signature per attributed task in equal windows
 * before and after the application time, clipped to the selected range.
 */
function prePost(
  proposal: Row,
  signatures: Row[],
  tasks: Row[],
  start: string,
  end: string,
): Row {
  const applied = Date.parse(String(proposal.applied_at));
  const span = Math.min(applied - Date.parse(start), Date.parse(end) - applied);
  const window = (from: number, to: number) => {
    const inRange = (row: Row) => {
      const day = Date.parse(`${String(row.day)}T00:00:00Z`);
      return day >= from && day < to;
    };
    const occurrences = signatures
      .filter(
        (row) =>
          row.harness === proposal.harness &&
          row.signature === proposal.signature &&
          inRange(row),
      )
      .reduce((total, row) => total + num(row.occurrences), 0);
    const taskCount = tasks
      .filter((row) => row.harness === proposal.harness && inRange(row))
      .reduce((total, row) => total + num(row.tasks), 0);
    return {
      occurrences,
      tasks: taskCount,
      rate: taskCount > 0 ? occurrences / taskCount : null,
    };
  };
  const days = Math.floor(span / DAY);
  const pre = window(applied - days * DAY, applied);
  const post = window(applied, applied + days * DAY);
  return {
    target: proposal.target,
    tier: proposal.tier,
    applied_at: proposal.applied_at,
    days,
    pre_occurrences: pre.occurrences,
    pre_tasks: pre.tasks,
    pre_rate: pre.rate,
    post_occurrences: post.occurrences,
    post_tasks: post.tasks,
    post_rate: post.rate,
  };
}

export default function Interventions({
  data,
}: {
  data: Record<string, Row[]>;
}) {
  const { filters } = useView();
  // Findings promoted from the facts carry a harness; older findings are shown under All.
  const findings = (data.findings ?? []).filter(
    (row) => filters.harness === "" || row.harness === filters.harness,
  );
  const proposals = (data.proposals ?? []).filter(
    (row) => filters.harness === "" || row.harness === filters.harness,
  );
  const applied = proposals.filter((row) => row.applied_at);
  const signatures = data.signature_daily ?? [];
  const tasks = data.task_daily ?? [];
  const comparisons = applied
    .filter((row) => {
      const at = Date.parse(String(row.applied_at));
      return at > Date.parse(filters.start) && at < Date.parse(filters.end);
    })
    .map((row) => prePost(row, signatures, tasks, filters.start, filters.end));
  const failuresByDay = groupSum(signatures, "day", ["occurrences"]);
  const tasksByDay = groupSum(tasks, "day", ["tasks"]);
  const baseline = [...tasksByDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, sums]) => ({
      day,
      rate: ratio(failuresByDay.get(day)?.occurrences ?? 0, sums.tasks!),
    }));
  const tiers = [
    ...groupSum(
      proposals.map((row) => ({ ...row, n: 1 })),
      "tier",
      ["n"],
    ),
  ].map(([tier, sums]) => ({
    tier: tier === "null" ? "not recorded" : tier,
    proposals: sums.n,
  }));
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
          detail="each gets a pre/post recurrence comparison"
          signals={["intervene.post_recurrence"]}
        />
        <Kpi
          title="Rule adherence"
          value="Unsupported"
          detail="no versioned rule registry"
          signals={["intervene.rule_adherence"]}
        />
      </Section>
      <Section title="Did an intervention reduce what it targeted?">
        <Panel
          title="Post-intervention recurrence"
          subtitle="Target signature per attributed task, equal windows before and after application"
          signals={["intervene.post_recurrence"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "target", label: "Target" },
              { key: "tier", label: "Tier" },
              {
                key: "applied_at",
                label: "Applied (UTC)",
                render: (row) => shortTime(row.applied_at),
              },
              { key: "days", label: "Window days", numeric: true },
              { key: "pre_occurrences", label: "Before", numeric: true },
              { key: "pre_tasks", label: "Tasks before", numeric: true },
              { key: "post_occurrences", label: "After", numeric: true },
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
            ]}
            rows={comparisons}
            empty="No intervention has been applied in the selected range; the comparison starts once a proposal reaches applied."
          />
        </Panel>
        <Panel
          title="Failure recurrence baseline"
          subtitle="Signature failures per attributed task per Europe/London day, the rate interventions are compared against"
          signals={["intervene.post_recurrence"]}
          span={12}
        >
          <DailyChart
            rows={baseline}
            series={[
              {
                key: "rate",
                label: "Failures per 100 tasks",
                color: SERIES[0]!,
              },
            ]}
            kind="line"
            format={(value) => (value === null ? "—" : value.toFixed(0))}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              { key: "rate", label: "Per 100 tasks", numeric: true },
            ]}
            rows={baseline}
          />
        </Panel>
      </Section>
      <Section title="Findings and proposals">
        <Panel
          title="Active findings"
          subtitle="Promoted every minute from recurring failure signatures (7 Europe/London days)"
          signals={["intervene.findings"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) =>
                  row.harness ? <HarnessName value={row.harness} /> : "—",
              },
              {
                key: "tool",
                label: "Tool",
                render: (row) => String(row.tool ?? row.category),
              },
              {
                key: "signature",
                label: "Signature",
                render: (row) => String(row.signature ?? "—"),
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
          title="Enforcement-tier audit"
          subtitle="Proposals by selected tier"
          signals={["intervene.tier_audit"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "tier", label: "Tier" },
              { key: "proposals", label: "Proposals", numeric: true },
            ]}
            rows={tiers}
            empty="No proposals yet; `agent-introspection candidates export` drafts one for the next actionable finding."
          />
        </Panel>
      </Section>
      <Section title="What the producers cannot support">
        <Panel title="Rule adherence" signals={["intervene.rule_adherence"]}>
          <Unsupported signal="intervene.rule_adherence" />
        </Panel>
        <Panel
          title="Uncodified practice recurrence"
          signals={["intervene.practice_recurrence"]}
        >
          <Unsupported signal="intervene.practice_recurrence" />
        </Panel>
      </Section>
    </>
  );
}
