import { DailyChart, DataTable, TableView, type Series } from "../charts";
import type { Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  RateValue,
  rateSeriesColumns,
  Section,
  SessionLink,
} from "../components";
import { OTHER_TOKEN } from "../tokens";
import {
  EFFORT_COLOR,
  effortRank,
  fmtCount,
  fmtPct,
  fmtSeconds,
  maybe,
  num,
} from "../format";

const effortSeries = (rows: Row[]): Series[] =>
  [...new Set(rows.map((row) => String(row.effort)))]
    .sort((a, b) => effortRank(a) - effortRank(b))
    .map((effort) => ({
      key: effort,
      label: effort,
      color: EFFORT_COLOR[effort] ?? OTHER_TOKEN,
    }));

/** Server rate beside its sample size; the rate is a column (`<key>_rate`, n = `<key>_den`). */
const pct = (row: Row, key: string) => (
  <RateValue pct={maybe(row[`${key}_rate`])} n={num(row[`${key}_den`])} />
);

const fixed1 = (value: unknown) => {
  const figure = maybe(value);
  return figure === null ? "—" : figure.toFixed(1);
};

/** Long daily rows → one row per day, one column per series (reshape only: values are the server's). */
const byDay = (rows: Row[], cells: (row: Row) => Row): Row[] => {
  const days = new Map<string, Row>();
  for (const row of rows) {
    const day = String(row.day);
    days.set(day, { ...(days.get(day) ?? { day }), ...cells(row) });
  }
  return [...days.values()].sort((a, b) =>
    String(a.day).localeCompare(String(b.day)),
  );
};

function dailyViews(data: Record<string, Row[]>) {
  const reasoning = data.daily_reasoning ?? [];
  const dailyTasks = data.daily_tasks ?? [];
  return {
    reasoningDaily: byDay(reasoning, (row) => ({
      [String(row.effort)]: row.reasoning,
    })),
    mixDaily: byDay(dailyTasks, (row) => ({
      [String(row.effort)]: row.tasks,
    })),
    cleanDaily: byDay(dailyTasks, (row) => ({
      [String(row.effort)]: maybe(row.clean_rate),
      [`${String(row.effort)}_n`]: row.clean_den,
    })),
    durationDaily: byDay(dailyTasks, (row) => ({
      [String(row.effort)]: maybe(row.p50_duration),
    })),
    taskSeries: effortSeries(dailyTasks),
    spendSeries: effortSeries(reasoning),
  };
}

export default function Effort({ data }: { data: Record<string, Row[]> }) {
  const usage = data.usage ?? [];
  const outcomes = data.outcomes ?? [];
  const spend: Row = data.spend?.[0] ?? {};
  const summary: Row = data.summary?.[0] ?? {};
  const {
    reasoningDaily,
    mixDaily,
    cleanDaily,
    durationDaily,
    taskSeries,
    spendSeries,
  } = dailyViews(data);
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Reasoning tokens"
          value={fmtCount(num(spend.reasoning))}
          detail={`across ${fmtCount(num(spend.calls))} model calls (Claude Code: turns)`}
          signals={["effort.reasoning_tokens"]}
        />
        <Kpi
          title="Reasoning share of output"
          value={fmtPct(maybe(spend.reasoning_share_rate))}
          detail="token-weighted over producers that emit reasoning"
          signals={["effort.reasoning_share"]}
        />
        <Kpi
          title="Tasks"
          value={fmtCount(num(summary.tasks))}
          detail={`${fmtPct(maybe(summary.clean_rate))} clean completion · n=${fmtCount(num(summary.clean_den))}`}
          signals={["task.count", "friction.clean_completion"]}
        />
        <Kpi
          title="Heavy-effort task share"
          value={fmtPct(maybe(summary.heavy_rate))}
          detail="high or xhigh requested"
          signals={["effort.heavy_share", "effort.level"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily reasoning tokens by effort"
          subtitle="Model calls (Claude Code per turn); unset means the provider default, not no reasoning"
          signals={["effort.reasoning_tokens", "effort.level"]}
        >
          <DailyChart
            rows={reasoningDaily}
            series={spendSeries}
            kind="stack"
            format={fmtCount}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...spendSeries.map((entry) => ({
                key: entry.key,
                label: entry.label,
                numeric: true,
              })),
            ]}
            rows={reasoningDaily}
          />
        </Panel>
        <Panel
          title="Daily task mix by effort"
          subtitle="Tasks started per day"
          signals={["task.count", "effort.level"]}
        >
          <DailyChart
            rows={mixDaily}
            series={taskSeries}
            kind="stack"
            format={fmtCount}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...taskSeries.map((entry) => ({
                key: entry.key,
                label: entry.label,
                numeric: true,
              })),
            ]}
            rows={mixDaily}
          />
        </Panel>
        <Panel
          title="Daily clean completion by effort"
          subtitle="Noisy on low-volume days; a friction proxy, not a quality score"
          signals={["friction.clean_completion", "effort.level"]}
        >
          <DailyChart
            rows={cleanDaily}
            series={taskSeries}
            kind="line"
            max={100}
            format={(value) => fmtPct(value, 0)}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...rateSeriesColumns(taskSeries),
            ]}
            rows={cleanDaily}
          />
        </Panel>
        <Panel
          title="Daily median task duration by effort"
          signals={["task.duration", "effort.level"]}
        >
          <DailyChart
            rows={durationDaily}
            series={taskSeries}
            kind="line"
            format={fmtSeconds}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...taskSeries.map((entry) => ({
                key: entry.key,
                label: `${entry.label} (s)`,
                numeric: true,
              })),
            ]}
            rows={durationDaily}
          />
        </Panel>
      </Section>
      <Section title="Does heavier effort earn its cost?">
        <Panel
          title="Task outcomes by reasoning effort"
          subtitle="Correlational: harder tasks may attract higher effort. Compare within one harness and model. Reasoning share here is over the output of tasks that report reasoning, so it can differ from the spend table."
          signals={[
            "task.count",
            "friction.clean_completion",
            "friction.interrupt",
            "friction.quick_follow_up",
            "friction.error",
            "task.duration",
            "task.model_steps",
            "task.reasoning_tokens",
            "task.delegations",
            "effort.reasoning_share",
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
              { key: "effort", label: "Effort" },
              { key: "tasks", label: "Tasks", numeric: true },
              {
                key: "clean",
                label: "Clean",
                numeric: true,
                render: (row) => pct(row, "clean"),
              },
              {
                key: "interrupted",
                label: "Interrupted",
                numeric: true,
                render: (row) => pct(row, "interrupted"),
              },
              {
                key: "follow_up",
                label: "Follow-up",
                numeric: true,
                render: (row) => pct(row, "follow_up"),
              },
              {
                key: "errored",
                label: "Errored",
                numeric: true,
                render: (row) => pct(row, "errored"),
              },
              {
                key: "p50_duration",
                label: "P50 time",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.p50_duration)),
              },
              {
                key: "p90_duration",
                label: "P90 time",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.p90_duration)),
              },
              {
                key: "steps",
                label: "Steps / task",
                numeric: true,
                render: (row) => fixed1(row.steps_per_task),
              },
              {
                key: "delegations",
                label: "Delegations / task",
                numeric: true,
                render: (row) => fixed1(row.delegations_per_task),
              },
              {
                key: "reasoning",
                label: "Reasoning / task",
                numeric: true,
                render: (row) => fmtCount(maybe(row.reasoning_per_task)),
              },
              {
                key: "share",
                label: "Reasoning share",
                numeric: true,
                render: (row) => fmtPct(maybe(row.reasoning_share_rate)),
              },
            ]}
            rows={outcomes}
          />
        </Panel>
        <Panel
          title="Reasoning spend by effort"
          subtitle="Model calls that report reasoning tokens; a Claude Code call is one turn, whose thinking tokens the Stop hook sums"
          signals={["effort.reasoning_tokens", "effort.reasoning_share"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "effort", label: "Effort" },
              {
                key: "calls",
                label: "Calls",
                numeric: true,
                render: (row) => fmtCount(num(row.calls)),
              },
              {
                key: "reasoning",
                label: "Reasoning",
                numeric: true,
                render: (row) => fmtCount(num(row.reasoning)),
              },
              {
                key: "of_all",
                label: "Of all reasoning",
                numeric: true,
                render: (row) => fmtPct(maybe(row.of_all_rate)),
              },
              {
                key: "per_call",
                label: "Per call",
                numeric: true,
                render: (row) => fmtCount(num(row.per_call)),
              },
              {
                key: "share",
                label: "Share of output",
                numeric: true,
                render: (row) => fmtPct(maybe(row.reasoning_share_rate)),
              },
            ]}
            rows={usage}
          />
        </Panel>
        <Panel
          title="Model × effort"
          subtitle="Holds the model fixed when comparing effort (≥ 3 tasks)"
          signals={[
            "task.count",
            "friction.clean_completion",
            "task.duration",
            "task.reasoning_tokens",
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
              { key: "model", label: "Model" },
              { key: "effort", label: "Effort" },
              { key: "tasks", label: "Tasks", numeric: true },
              {
                key: "clean",
                label: "Clean",
                numeric: true,
                render: (row) => pct(row, "clean"),
              },
              {
                key: "p50_duration",
                label: "P50 time",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.p50_duration)),
              },
              {
                key: "avg_steps",
                label: "Steps",
                numeric: true,
                render: (row) => num(row.avg_steps).toFixed(1),
              },
              {
                key: "reasoning",
                label: "Reasoning / step",
                numeric: true,
                render: (row) => fmtCount(maybe(row.reasoning_per_step)),
              },
            ]}
            rows={data.model_effort ?? []}
          />
        </Panel>
      </Section>
      <Section title="Heaviest reasoning tasks">
        <Panel
          title="Top 25 tasks by reasoning tokens"
          signals={[
            "task.reasoning_tokens",
            "task.duration",
            "friction.clean_completion",
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
                key: "session_id",
                label: "Session",
                render: (row) => (
                  <SessionLink harness={row.harness} session={row.session_id} />
                ),
              },
              { key: "model", label: "Model" },
              { key: "effort", label: "Effort" },
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
              { key: "model_steps", label: "Steps", numeric: true },
              {
                key: "reasoning_tokens",
                label: "Reasoning",
                numeric: true,
                render: (row) => fmtCount(num(row.reasoning_tokens)),
              },
              {
                key: "output_tokens",
                label: "Output",
                numeric: true,
                render: (row) => fmtCount(maybe(row.output_tokens)),
              },
              {
                key: "clean_completion",
                label: "Clean",
                render: (row) =>
                  row.clean_completion === null
                    ? "—"
                    : num(row.clean_completion) === 1
                      ? "yes"
                      : "no",
              },
            ]}
            rows={data.heaviest ?? []}
          />
        </Panel>
      </Section>
    </>
  );
}
