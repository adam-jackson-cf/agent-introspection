import { BarList, DailyChart, DataTable, TableView } from "../charts";
import { HARNESSES, type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  Section,
  SessionLink,
  useView,
} from "../components";
import {
  fmtCount,
  fmtPct,
  HARNESS_COLOR,
  HARNESS_LABEL,
  harnessLabel,
  isHarness,
  num,
  pivotDaily,
  ratio,
  sumRows,
} from "../format";

export default function Guardrails({ data }: { data: Record<string, Row[]> }) {
  const { filters } = useView();
  const decisions = data.decisions ?? [];
  const decided = sumRows(decisions, ["n"]).n!;
  const rejected = sumRows(
    decisions.filter((row) => num(row.rejected) === 1),
    ["n"],
  ).n!;
  const byUser = sumRows(
    decisions.filter((row) =>
      String(row.source).toLowerCase().startsWith("user"),
    ),
    ["n"],
  ).n!;
  const bypass = sumRows(data.bypass ?? [], ["commands", "bypass", "tasks"]);
  const churn = sumRows(data.churn_summary ?? [], ["tasks", "pairs"]);
  const series = HARNESSES.filter(
    (harness) => filters.harness === "" || harness === filters.harness,
  ).map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const rejectionDaily = pivotDaily(
    data.decisions_daily ?? [],
    (row) => String(row.harness),
    ["rejected", "decisions"],
    (sums) => ratio(sums.rejected!, sums.decisions!),
  );
  const sources = [
    ...decisions
      .reduce((map, row) => {
        const key = `${String(row.harness)} · ${String(row.source) || "unknown"}`;
        map.set(key, {
          harness: row.harness,
          source: row.source,
          n: (map.get(key)?.n ?? 0) + num(row.n),
        });
        return map;
      }, new Map<string, { harness: unknown; source: unknown; n: number }>())
      .entries(),
  ].sort((a, b) => b[1].n - a[1].n);
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Rejected tool calls"
          value={fmtPct(ratio(rejected, decided))}
          detail={`${fmtCount(rejected)} of ${fmtCount(decided)} approval decisions`}
          signals={["guard.rejections", "guard.decisions"]}
        />
        <Kpi
          title="Approval decisions"
          value={fmtCount(decided)}
          detail={`${fmtCount(byUser)} decided by the user`}
          signals={["guard.decisions"]}
        />
        <Kpi
          title="Quality-gate bypass"
          value={fmtCount(bypass.bypass!)}
          detail={`commands in ${fmtCount(bypass.tasks!)} tasks, of ${fmtCount(bypass.commands!)} shell commands`}
          signals={["guard.gate_bypass"]}
        />
        <Kpi
          title="Command churn"
          value={fmtCount(churn.tasks!)}
          detail={`tasks with ≥ 3 distinct commands on one file (${fmtCount(churn.pairs!)} files)`}
          signals={["guard.command_churn"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily rejection rate"
          subtitle="Rejected / all approval decisions, per harness"
          signals={["guard.rejections"]}
          span={12}
        >
          <DailyChart
            rows={rejectionDaily}
            series={series}
            kind="line"
            format={(value) => fmtPct(value, 0)}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...series.map((entry) => ({
                key: entry.key,
                label: `${entry.label} %`,
                numeric: true,
              })),
            ]}
            rows={rejectionDaily}
          />
        </Panel>
      </Section>
      <Section title="Who approves?">
        <Panel
          title="Approval decisions by source"
          subtitle="Config policy, the user, or the automated approval reviewer"
          signals={["guard.decisions"]}
          span={12}
        >
          <BarList
            items={sources.map(([key, entry]) => ({
              key,
              label: `${harnessLabel(entry.harness)} · ${String(entry.source) || "unknown"}`,
              value: entry.n,
              color: isHarness(entry.harness)
                ? HARNESS_COLOR[entry.harness]
                : "#6b7280",
            }))}
            format={fmtCount}
          />
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "decision", label: "Decision" },
              { key: "source", label: "Source" },
              { key: "n", label: "Decisions", numeric: true },
            ]}
            rows={decisions}
          />
        </Panel>
      </Section>
      <Section title="Exemplars">
        <Panel
          title="Quality-gate bypass commands"
          subtitle="--no-verify, HUSKY=0, SKIP=, or --no-gpg-sign; the command text itself is not stored"
          signals={["guard.gate_bypass"]}
        >
          <DataTable
            columns={[
              {
                key: "at",
                label: "At (UTC)",
                render: (row) => String(row.at).slice(0, 16),
              },
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              {
                key: "session_id",
                label: "Session",
                render: (row) => (
                  <SessionLink harness={row.harness} session={row.session_id} />
                ),
              },
              {
                key: "command",
                label: "Command",
                render: (row) =>
                  `${String(row.command_head)} ${String(row.command_sub)}`.trim(),
              },
              { key: "outcome", label: "Outcome" },
            ]}
            rows={data.bypass_calls ?? []}
            empty="No quality-gate bypass in the selected range."
          />
        </Panel>
        <Panel
          title="Command churn"
          subtitle="Files hit by ≥ 3 distinct tool commands in one task"
          signals={["guard.command_churn"]}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              {
                key: "session_id",
                label: "Session",
                render: (row) => (
                  <SessionLink harness={row.harness} session={row.session_id} />
                ),
              },
              { key: "target", label: "File" },
              { key: "commands", label: "Commands", numeric: true },
              { key: "calls", label: "Calls", numeric: true },
              { key: "failed", label: "Failed", numeric: true },
            ]}
            rows={data.churn ?? []}
            empty="No file was hit by three or more distinct commands in one task."
          />
        </Panel>
      </Section>
    </>
  );
}
