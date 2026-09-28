import { DailyChart, DataTable, TableView } from "../charts";
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
  num,
  pivotDaily,
  ratio,
  sumRows,
} from "../format";

export default function Tools({ data }: { data: Record<string, Row[]> }) {
  const { filters } = useView();
  const kpi = sumRows(data.kpi ?? [], [
    "calls",
    "explicit",
    "failed",
    "unknown",
    "tasks",
    "failed_tasks",
    "unattributed",
  ]);
  const repeats = sumRows(data.repeat_summary ?? [], [
    "repeat_tasks",
    "tasks",
    "repeated_attempts",
  ]);
  const daily = data.daily ?? [];
  const series = HARNESSES.filter(
    (harness) => filters.harness === "" || harness === filters.harness,
  ).map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const rateDaily = pivotDaily(
    daily,
    (row) => String(row.harness),
    ["failed", "explicit"],
    (sums) => ratio(sums.failed!, sums.explicit!),
  );
  const callsDaily = pivotDaily(
    daily,
    (row) => String(row.harness),
    ["calls"],
    (sums) => sums.calls!,
  );
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Tool calls"
          value={fmtCount(kpi.calls!)}
          detail={`${fmtCount(kpi.unknown!)} without an explicit outcome`}
          signals={["tools.calls"]}
        />
        <Kpi
          title="Tool failure rate"
          value={fmtPct(ratio(kpi.failed!, kpi.explicit!))}
          detail={`${fmtCount(kpi.failed!)} failed of ${fmtCount(kpi.explicit!)} with an outcome`}
          signals={["tools.failure_rate"]}
        />
        <Kpi
          title="Tasks affected by failures"
          value={fmtPct(ratio(kpi.failed_tasks!, kpi.tasks!))}
          detail={`${fmtCount(kpi.failed_tasks!)} of ${fmtCount(kpi.tasks!)} tool-using tasks`}
          signals={["tools.tasks_affected"]}
        />
        <Kpi
          title="Tasks with repeated attempts"
          value={fmtPct(ratio(repeats.repeat_tasks!, repeats.tasks!))}
          detail={`${fmtCount(repeats.repeat_tasks!)} tasks · ${fmtCount(repeats.repeated_attempts!)} identical calls`}
          signals={["tools.repeats"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily tool failure rate"
          subtitle="Failed / calls with an explicit outcome, per harness"
          signals={["tools.failure_rate"]}
          span={7}
        >
          <DailyChart
            rows={rateDaily}
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
            rows={rateDaily}
          />
        </Panel>
        <Panel
          title="Daily tool calls"
          subtitle="Stacked by harness"
          signals={["tools.calls"]}
          span={5}
        >
          <DailyChart
            rows={callsDaily}
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
            rows={callsDaily}
          />
        </Panel>
      </Section>
      <Section title="Which tools and failures repeat?">
        <Panel
          title="Failures by tool"
          subtitle="Most failures first; failed tasks count distinct tasks"
          signals={["tools.failure_rate", "tools.tasks_affected"]}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "tool", label: "Tool" },
              { key: "calls", label: "Calls", numeric: true },
              { key: "failed", label: "Failed", numeric: true },
              {
                key: "rate",
                label: "Rate",
                numeric: true,
                render: (row) =>
                  fmtPct(ratio(num(row.failed), num(row.explicit))),
              },
              { key: "failed_tasks", label: "Failed tasks", numeric: true },
            ]}
            rows={data.by_tool ?? []}
          />
        </Panel>
        <Panel
          title="Failure signatures"
          subtitle="Normalized first error line (digits → N, home → ~)"
          signals={["tools.failure_signature"]}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "tool", label: "Tool" },
              { key: "signature", label: "Signature" },
              { key: "occurrences", label: "Count", numeric: true },
              { key: "tasks", label: "Tasks", numeric: true },
              {
                key: "example_session",
                label: "Example",
                render: (row) => (
                  <SessionLink
                    harness={row.harness}
                    session={row.example_session}
                  />
                ),
              },
            ]}
            rows={data.signatures ?? []}
          />
        </Panel>
      </Section>
      <Section title="Repeats and loops">
        <Panel
          title="Repeated attempts"
          subtitle="Same tool and identical arguments ≥ 2 times in one task"
          signals={["tools.repeats"]}
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
              { key: "tool", label: "Tool" },
              {
                key: "command",
                label: "Command",
                render: (row) => String(row.command) || "—",
              },
              { key: "attempts", label: "Attempts", numeric: true },
              { key: "failed", label: "Failed", numeric: true },
            ]}
            rows={data.repeats ?? []}
          />
        </Panel>
        <Panel
          title="Tool loops"
          subtitle="≥ 3 consecutive identical calls in one task"
          signals={["tools.loops"]}
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
              { key: "tool", label: "Tool" },
              {
                key: "command",
                label: "Command",
                render: (row) => String(row.command) || "—",
              },
              { key: "length", label: "Run", numeric: true },
              { key: "failed", label: "Failed", numeric: true },
              {
                key: "started",
                label: "Started (UTC)",
                render: (row) => String(row.started).slice(0, 16),
              },
            ]}
            rows={data.loops ?? []}
            empty="No runs of three or more identical consecutive calls."
          />
        </Panel>
      </Section>
    </>
  );
}
