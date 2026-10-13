import { DailyChart, DataTable, TableView } from "../charts";
import { type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  Section,
  SessionLink,
  useSelectedHarnesses,
} from "../components";
import {
  fmtCount,
  fmtPct,
  fmtSeconds,
  HARNESS_COLOR,
  HARNESS_LABEL,
  harnessLabel,
  maybe,
  num,
  orDash,
  SERIES,
} from "../format";

const COMPONENTS = ["clean", "interrupted", "steered", "errored", "follow_up"];

const flag = (value: unknown) =>
  value === null || value === undefined ? "—" : num(value) === 1 ? "yes" : "no";

function pivotCleanDaily(daily: Row[]): Row[] {
  const out: Row[] = [];
  for (const row of daily) {
    const day = String(row.day);
    let entry = out.find((candidate) => candidate.day === day);
    if (!entry) out.push((entry = { day }));
    entry[String(row.harness)] = maybe(row.clean_rate);
  }
  return out;
}

export default function Friction({ data }: { data: Record<string, Row[]> }) {
  const selected = useSelectedHarnesses();
  const kpi = data.kpi ?? [];
  // The server returns one All row (harness '') beside the per-harness rows.
  const all = kpi.find((row) => row.harness === "") ?? {};
  const harnessRows = kpi.filter((row) => row.harness !== "");
  const rate = (key: string) => maybe(all[`${key}_rate`]);
  const detail = (key: string) =>
    `${fmtCount(num(all[`${key}_n`]))} of ${fmtCount(num(all[`${key}_den`]))} tasks that observe it`;
  const series = selected.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const cleanDaily = pivotCleanDaily(data.daily ?? []);
  const signals = data.daily_signals ?? [];
  const recoveryRows = data.recovery_summary ?? [];
  const recovery = recoveryRows.find((row) => row.harness === "") ?? {};
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Clean completion"
          source="kpi"
          value={fmtPct(rate("clean"))}
          detail={detail("clean")}
          signals={["friction.clean_completion"]}
        />
        <Kpi
          title="Interrupted tasks"
          source="kpi"
          value={fmtPct(rate("interrupted"))}
          detail={detail("interrupted")}
          signals={["friction.interrupt"]}
        />
        <Kpi
          title="Quick follow-up"
          source="kpi"
          value={fmtPct(rate("follow_up"))}
          detail={detail("follow_up")}
          signals={["friction.quick_follow_up"]}
        />
        <Kpi
          title="Recovery after failure"
          source="recovery_summary"
          value={fmtPct(maybe(recovery.recovered_rate))}
          detail={`${fmtCount(num(recovery.recovered))} of ${fmtCount(num(recovery.failed_ops))} failed operations later succeeded (inferred)`}
          signals={["friction.recovery"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily clean completion"
          source="daily"
          subtitle="Per harness; each counts only the friction components it observes"
          signals={["friction.clean_completion"]}
          span={7}
        >
          <DailyChart
            rows={cleanDaily}
            series={series}
            kind="line"
            max={100}
            fixed
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
            rows={cleanDaily}
          />
        </Panel>
        <Panel
          title="Daily interrupt and steer signals"
          source="daily_signals"
          subtitle="Tasks started that day with an explicit user interrupt, and tasks with a steer; a task with both counts in each line"
          signals={["friction.interrupt", "friction.steer"]}
          span={5}
        >
          <DailyChart
            rows={signals}
            series={[
              { key: "interrupt", label: "Interrupt", color: SERIES[0]! },
              { key: "steer", label: "Steer", color: SERIES[1]! },
            ]}
            kind="line"
            format={fmtCount}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              { key: "interrupt", label: "Interrupts", numeric: true },
              { key: "steer", label: "Steers", numeric: true },
            ]}
            rows={signals}
          />
        </Panel>
      </Section>
      <Section title="Which friction does each harness observe?">
        <Panel
          title="Friction components by harness"
          source="kpi"
          subtitle="— means the harness does not emit that component; it is never counted as zero"
          signals={[
            "friction.clean_completion",
            "friction.interrupt",
            "friction.steer",
            "friction.error",
            "friction.quick_follow_up",
          ]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "tasks", label: "Tasks", numeric: true },
              {
                key: "observed",
                label: "Observes",
                render: (row) => String(row.observed) || "—",
              },
              ...COMPONENTS.map((key) => ({
                key,
                label: {
                  clean: "Clean",
                  interrupted: "Interrupted",
                  steered: "Steered",
                  errored: "Errored",
                  follow_up: "Follow-up",
                }[key]!,
                numeric: true,
                render: (row: Row) => fmtPct(maybe(row[`${key}_rate`])),
              })),
            ]}
            rows={harnessRows}
          />
        </Panel>
        <Panel
          title="Recovery by harness"
          source="recovery_summary"
          subtitle="A failed operation recovers when the same operation later succeeds in the task"
          signals={["friction.recovery"]}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "failed_ops", label: "Failed ops", numeric: true },
              { key: "recovered", label: "Recovered", numeric: true },
              {
                key: "rate",
                label: "Rate",
                numeric: true,
                render: (row) => fmtPct(maybe(row.recovered_rate)),
              },
              {
                key: "p50",
                label: "P50 to recover",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.p50_recovery_seconds)),
              },
            ]}
            rows={recoveryRows.filter((row) => row.harness !== "")}
          />
        </Panel>
        <Panel
          title="Operations that fail most"
          source="recovery_ops"
          subtitle="Failed operation groups and how many recovered"
          signals={["friction.recovery"]}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "operation", label: "Operation" },
              { key: "failed_ops", label: "Failed", numeric: true },
              { key: "recovered", label: "Recovered", numeric: true },
            ]}
            rows={data.recovery_ops ?? []}
          />
        </Panel>
      </Section>
      <Section title="Tasks with explicit friction">
        <Panel
          title="Where to look"
          source="where_to_look"
          subtitle="Failure classes in tasks the user interrupted or steered, or that errored, by project; generic error types left out"
          signals={[
            "tools.failure_signature",
            "friction.interrupt",
            "friction.steer",
            "friction.error",
          ]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "project", label: "Project" },
              { key: "failure_class", label: "Failure class" },
              { key: "tasks", label: "Tasks", numeric: true },
              { key: "failures", label: "Failures", numeric: true },
              {
                key: "harnesses",
                label: "Harnesses",
                render: (row) =>
                  (Array.isArray(row.harnesses) ? row.harnesses : [])
                    .map(harnessLabel)
                    .join(", ") || "—",
              },
              {
                key: "last_seen",
                label: "Last seen (UTC)",
                render: (row) => String(row.last_seen).slice(0, 16),
              },
            ]}
            rows={data.where_to_look ?? []}
            empty="No failure class with a Git project in friction tasks in the selected range."
          />
        </Panel>
        <Panel
          title="Latest interrupted, steered, or errored tasks"
          source="tasks"
          signals={[
            "friction.interrupt",
            "friction.steer",
            "friction.error",
            "tools.failure_signature",
          ]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              {
                key: "project",
                label: "Project",
                render: (row) => orDash(row.project),
              },
              {
                key: "task_type",
                label: "Type",
                render: (row) => orDash(row.task_type),
              },
              {
                key: "session_id",
                label: "Session",
                render: (row) => (
                  <SessionLink harness={row.harness} session={row.session_id} />
                ),
              },
              { key: "model", label: "Model" },
              {
                key: "effort",
                label: "Effort",
                render: (row) => orDash(row.effort),
              },
              {
                key: "failing_tool",
                label: "Failing tool",
                render: (row) => orDash(row.failing_tool),
              },
              {
                key: "failure_class",
                label: "Failure class",
                render: (row) => orDash(row.failure_class),
              },
              {
                key: "started",
                label: "Started (UTC)",
                render: (row) => String(row.started).slice(0, 16),
              },
              {
                key: "duration_seconds",
                label: "Duration",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.duration_seconds)),
              },
              {
                key: "interrupted",
                label: "Interrupted",
                render: (row) => flag(row.interrupted),
              },
              {
                key: "steered",
                label: "Steered",
                render: (row) => flag(row.steered),
              },
              {
                key: "errored",
                label: "Errored",
                render: (row) => flag(row.errored),
              },
              {
                key: "quick_follow_up",
                label: "Follow-up",
                render: (row) => flag(row.quick_follow_up),
              },
            ]}
            rows={data.tasks ?? []}
          />
        </Panel>
      </Section>
    </>
  );
}
