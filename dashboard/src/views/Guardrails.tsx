import { BarList, DailyChart, DataTable, TableView } from "../charts";
import { type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  rateSeriesColumns,
  Section,
  SessionLink,
  ViewLink,
  useSelectedHarnesses,
  useVisible,
} from "../components";
import { OTHER_TOKEN } from "../tokens";
import {
  fmtCount,
  fmtPct,
  HARNESS_COLOR,
  HARNESS_LABEL,
  harnessLabel,
  isHarness,
  maybe,
  num,
} from "../format";

/** The All row of a summary query (`harness === ''`); empty result reads as no data. */
const allRow = (rows: Row[] | undefined): Row =>
  rows?.find((row) => row.harness === "") ?? {};

/** Reshapes long daily rows into one row per day with a column per harness (values copied from `field`, plus `<harness>_n` from `nField`). */
function wideDaily(rows: Row[], field: string, nField?: string): Row[] {
  const days = new Map<string, Row>();
  for (const row of rows) {
    const day = String(row.day);
    const out = days.get(day) ?? { day };
    const key = String(row.harness);
    out[key] = row[field] ?? null;
    if (nField) out[`${key}_n`] = row[nField];
    days.set(day, out);
  }
  return [...days.values()].sort((a, b) =>
    String(a.day).localeCompare(String(b.day)),
  );
}

/**
 * Sandbox denials where every enabled harness has a sandbox; elsewhere the slot
 * shows who decides approvals.
 */
function SandboxOrDecisionsKpi(props: {
  sandboxShown: boolean;
  sandbox: Row;
  decisions: Row;
}) {
  const { sandboxShown, sandbox, decisions } = props;
  if (sandboxShown)
    return (
      <Kpi
        title="Sandbox denials"
        value={fmtCount(num(sandbox.denied))}
        detail={`${fmtCount(num(sandbox.outcomes))} sandbox outcomes`}
        signals={["guard.sandbox"]}
      />
    );
  return (
    <Kpi
      title="Approval decisions"
      value={fmtCount(num(decisions.decided))}
      detail={`${fmtCount(num(decisions.by_user))} decided by the user`}
      signals={["guard.decisions"]}
    />
  );
}

/** Sandbox panels pair with the approval panels only where every enabled harness has a sandbox. */
const sandboxLayout = (shown: boolean) =>
  shown
    ? { span: 6 as const, approvals: "Who approves, and what is denied?" }
    : { span: 12 as const, approvals: "Who approves?" };

function guardrailsModel(data: Record<string, Row[]>) {
  return {
    decisions: data.decisions ?? [],
    decisionKpi: allRow(data.decision_kpi),
    sandboxKpi: allRow(data.sandbox_kpi),
    bypass: allRow(data.bypass),
    churn: allRow(data.churn_summary),
    rejectionDaily: wideDaily(
      data.decisions_daily ?? [],
      "rejection_rate",
      "decisions",
    ),
    deniedDaily: wideDaily(data.sandbox_daily ?? [], "denied"),
    sandbox: data.sandbox ?? [],
    sources: data.decision_sources ?? [],
  };
}

export default function Guardrails({ data }: { data: Record<string, Row[]> }) {
  const selected = useSelectedHarnesses();
  const sandboxShown = useVisible("guard.sandbox");
  const {
    decisions,
    decisionKpi,
    sandboxKpi,
    bypass,
    churn,
    rejectionDaily,
    deniedDaily,
    sandbox,
    sources,
  } = guardrailsModel(data);
  const series = selected.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const layout = sandboxLayout(sandboxShown);
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Rejected tool calls"
          value={fmtPct(maybe(decisionKpi.rejection_rate), 2)}
          detail={`${fmtCount(num(decisionKpi.rejected))} of ${fmtCount(num(decisionKpi.decided))} approval decisions`}
          n={num(decisionKpi.decided)}
          signals={["guard.rejections", "guard.decisions"]}
        />
        <SandboxOrDecisionsKpi
          sandboxShown={sandboxShown}
          sandbox={sandboxKpi}
          decisions={decisionKpi}
        />
        <Kpi
          title="Quality-gate bypass"
          value={fmtCount(num(bypass.bypass))}
          detail={`commands in ${fmtCount(num(bypass.tasks))} tasks, of ${fmtCount(num(bypass.commands))} shell commands`}
          signals={["guard.gate_bypass"]}
        />
        <Kpi
          title="Command churn"
          value={fmtCount(num(churn.tasks))}
          detail={`tasks with ≥ 3 distinct commands on one file (${fmtCount(num(churn.pairs))} files)`}
          signals={["guard.command_churn"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily rejection rate"
          subtitle="Rejected / all approval decisions, per harness"
          signals={["guard.rejections"]}
          span={layout.span}
        >
          <DailyChart
            rows={rejectionDaily}
            series={series}
            kind="line"
            format={(value) => fmtPct(value, 2)}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...rateSeriesColumns(series),
            ]}
            rows={rejectionDaily}
          />
          <ViewLink view="interventions">Open Interventions</ViewLink>
        </Panel>
        <Panel
          title="Daily sandbox denials"
          subtitle="Stacked by harness"
          signals={["guard.sandbox"]}
        >
          <DailyChart
            rows={deniedDaily}
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
            rows={deniedDaily}
          />
        </Panel>
      </Section>
      <Section title={layout.approvals}>
        <Panel
          title="Approval decisions by source"
          subtitle="Config policy, the user, or the automated approval reviewer"
          signals={["guard.decisions"]}
          span={layout.span}
        >
          <BarList
            items={sources.map((entry) => ({
              key: `${String(entry.harness)} · ${String(entry.source_label)}`,
              label: `${harnessLabel(entry.harness)} · ${String(entry.source_label)}`,
              value: num(entry.n),
              color: isHarness(entry.harness)
                ? HARNESS_COLOR[entry.harness]
                : OTHER_TOKEN,
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
        <Panel title="Sandbox outcomes by tool" signals={["guard.sandbox"]}>
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "outcome", label: "Outcome" },
              { key: "tool", label: "Tool" },
              { key: "n", label: "Outcomes", numeric: true },
            ]}
            rows={sandbox}
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
                render: (row) => String(row.command) || "—",
              },
              { key: "outcome", label: "Outcome" },
            ]}
            rows={data.bypass_calls ?? []}
            total={num(bypass.bypass)}
            empty="No quality-gate bypass in the selected range."
          />
          <ViewLink view="tools">Open Tool failures</ViewLink>
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
            total={num(churn.pairs)}
            empty="No file was hit by three or more distinct commands in one task."
          />
          <ViewLink view="tools">Open Tool failures</ViewLink>
        </Panel>
      </Section>
    </>
  );
}
