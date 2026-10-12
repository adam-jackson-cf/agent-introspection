import { DailyChart, DataTable, TableView } from "../charts";
import { type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  RateValue,
  rateSeriesColumns,
  Section,
  SessionLink,
  ViewLink,
  useSelectedHarnesses,
  useView,
} from "../components";
import {
  fmtCount,
  fmtPct,
  HARNESS_COLOR,
  HARNESS_LABEL,
  maybe,
  num,
} from "../format";

/** The server's All row: the rollup row, harness = ''. */
const allRow = (rows: Row[] | undefined): Row =>
  (rows ?? []).find((row) => row.harness === "") ?? {};

/** Long daily rows to one row per day, a column per harness; values are copied. */
function reshapeDaily(rows: Row[], valueKey: string, nKey?: string): Row[] {
  const days = new Map<string, Row>();
  for (const row of rows) {
    const day = String(row.day);
    const out = days.get(day) ?? { day };
    const harness = String(row.harness);
    out[harness] = maybe(row[valueKey]);
    if (nKey) out[`${harness}_n`] = num(row[nKey]);
    days.set(day, out);
  }
  return [...days.values()].sort((a, b) =>
    String(a.day).localeCompare(String(b.day)),
  );
}

export default function Tools({ data }: { data: Record<string, Row[]> }) {
  const selected = useSelectedHarnesses();
  const { minRateN } = useView();
  const kpi = allRow(data.kpi);
  const repeats = allRow(data.repeat_summary);
  const daily = data.daily ?? [];
  const series = selected.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const rateDaily = reshapeDaily(daily, "failure_rate", "explicit");
  const callsDaily = reshapeDaily(daily, "calls");
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Tool calls"
          source="kpi"
          value={fmtCount(num(kpi.calls))}
          detail={`${fmtCount(num(kpi.unknown))} without an explicit outcome`}
          signals={["tools.calls"]}
        />
        <Kpi
          title="Tool failure rate"
          source="kpi"
          value={fmtPct(maybe(kpi.failure_rate))}
          detail={`${fmtCount(num(kpi.failed))} failed of ${fmtCount(num(kpi.explicit))} with an outcome`}
          n={num(kpi.explicit)}
          signals={["tools.failure_rate"]}
        />
        <Kpi
          title="Tasks affected by failures"
          source="kpi"
          value={fmtPct(maybe(kpi.tasks_affected_rate))}
          detail={`${fmtCount(num(kpi.failed_tasks))} of ${fmtCount(num(kpi.tasks))} tasks with an attributed tool call`}
          n={num(kpi.tasks)}
          signals={["tools.tasks_affected"]}
        />
        <Kpi
          title="Tasks with repeated attempts"
          source="repeat_summary"
          value={fmtPct(maybe(repeats.repeat_rate))}
          detail={`${fmtCount(num(repeats.repeat_tasks))} of ${fmtCount(num(repeats.tasks))} tasks with comparable calls (not waiting or polling) · ${fmtCount(num(repeats.repeated_attempts))} identical calls`}
          n={num(repeats.tasks)}
          signals={["tools.repeats"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily tool failure rate"
          source="daily"
          subtitle="Failed / calls with an explicit outcome, per harness; a hollow marker has fewer outcomes than the minimum sample, and its n is in the readout"
          signals={["tools.failure_rate"]}
          span={7}
        >
          <DailyChart
            rows={rateDaily}
            series={series}
            kind="line"
            markers={{ minN: minRateN }}
            format={(value) => fmtPct(value, 0)}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...rateSeriesColumns(series),
            ]}
            rows={rateDaily}
          />
        </Panel>
        <Panel
          title="Daily tool calls"
          source="daily"
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
          source="by_tool"
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
                render: (row) => (
                  <RateValue
                    pct={maybe(row.failure_rate)}
                    n={num(row.explicit)}
                  />
                ),
              },
              { key: "failed_tasks", label: "Failed tasks", numeric: true },
            ]}
            rows={data.by_tool ?? []}
          />
        </Panel>
        <Panel
          title="Failure signatures"
          source="signatures"
          subtitle="Normalized error line, first 80 characters shown (digits → N, home → ~); grouped by a hash of the full line"
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
          <ViewLink view="friction">Open Friction</ViewLink>
          <ViewLink view="recurrence">Open Recurring failures</ViewLink>
        </Panel>
      </Section>
      <Section title="Repeats and loops">
        <Panel
          title="Repeated attempts"
          source="repeats"
          subtitle="Same tool and identical arguments ≥ 2 times in one task, most failed first; the session link is one of the task's sessions"
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
                key: "example_session",
                label: "Session",
                render: (row) => (
                  <SessionLink
                    harness={row.harness}
                    session={row.example_session}
                  />
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
          source="loops"
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
          <ViewLink view="friction">Open Friction</ViewLink>
          <ViewLink view="recurrence">Open Recurring failures</ViewLink>
        </Panel>
      </Section>
    </>
  );
}
