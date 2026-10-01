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
  groupSum,
  HARNESS_COLOR,
  HARNESS_LABEL,
  maybe,
  num,
  pivotDaily,
  ratio,
  sumRows,
} from "../format";

const COMPONENTS = ["clean", "interrupted", "steered", "errored", "follow_up"];
const FIELDS = [
  "tasks",
  ...COMPONENTS.flatMap((key) => [`${key}_n`, `${key}_den`]),
];

const flag = (value: unknown) =>
  value === null || value === undefined ? "—" : num(value) === 1 ? "yes" : "no";

export default function Friction({ data }: { data: Record<string, Row[]> }) {
  const selected = useSelectedHarnesses();
  const kpi = data.kpi ?? [];
  const all = sumRows(kpi, FIELDS);
  const rate = (key: string, sums: Record<string, number> = all) =>
    ratio(sums[`${key}_n`]!, sums[`${key}_den`]!);
  const detail = (key: string) =>
    `${fmtCount(all[`${key}_n`]!)} of ${fmtCount(all[`${key}_den`]!)} tasks that observe it`;
  const series = selected.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const daily = data.daily ?? [];
  const cleanDaily = pivotDaily(
    daily,
    (row) => String(row.harness),
    ["clean_n", "clean_den"],
    (sums) => ratio(sums.clean_n!, sums.clean_den!),
  );
  const signals = [...groupSum(daily, "day", ["interrupted_n", "steered_n"])]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, sums]) => ({
      day,
      interrupt: sums.interrupted_n!,
      steer: sums.steered_n!,
    }));
  const recovery = sumRows(data.recovery_summary ?? [], [
    "failed_ops",
    "recovered",
  ]);
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Clean completion"
          value={fmtPct(rate("clean"))}
          detail={detail("clean")}
          signals={["friction.clean_completion"]}
        />
        <Kpi
          title="Interrupted tasks"
          value={fmtPct(rate("interrupted"))}
          detail={detail("interrupted")}
          signals={["friction.interrupt"]}
        />
        <Kpi
          title="Quick follow-up"
          value={fmtPct(rate("follow_up"))}
          detail={detail("follow_up")}
          signals={["friction.quick_follow_up"]}
        />
        <Kpi
          title="Recovery after failure"
          value={fmtPct(ratio(recovery.recovered!, recovery.failed_ops!))}
          detail={`${fmtCount(recovery.recovered!)} of ${fmtCount(recovery.failed_ops!)} failed operations later succeeded (inferred)`}
          signals={["friction.recovery"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily clean completion"
          subtitle="Per harness; each counts only the friction components it observes"
          signals={["friction.clean_completion"]}
          span={7}
        >
          <DailyChart
            rows={cleanDaily}
            series={series}
            kind="line"
            max={100}
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
          title="Daily interrupted and steered tasks"
          subtitle="Tasks started that day with an explicit user interrupt or steer"
          signals={["friction.interrupt", "friction.steer"]}
          span={5}
        >
          <DailyChart
            rows={signals}
            series={[
              { key: "interrupt", label: "Interrupt", color: "#3987e5" },
              { key: "steer", label: "Steer", color: "#d95926" },
            ]}
            kind="stack"
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
                render: (row: Row) =>
                  num(row[`${key}_den`]) > 0
                    ? fmtPct(
                        ratio(num(row[`${key}_n`]), num(row[`${key}_den`])),
                      )
                    : "—",
              })),
            ]}
            rows={kpi}
          />
        </Panel>
        <Panel
          title="Recovery by harness"
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
                render: (row) =>
                  fmtPct(ratio(num(row.recovered), num(row.failed_ops))),
              },
              {
                key: "p50",
                label: "P50 to recover",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.p50_recovery_seconds)),
              },
            ]}
            rows={data.recovery_summary ?? []}
          />
        </Panel>
        <Panel
          title="Operations that fail most"
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
          title="Latest interrupted, steered, or errored tasks"
          signals={["friction.interrupt", "friction.steer", "friction.error"]}
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
                key: "session_id",
                label: "Session",
                render: (row) => (
                  <SessionLink harness={row.harness} session={row.session_id} />
                ),
              },
              { key: "model", label: "Model" },
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
