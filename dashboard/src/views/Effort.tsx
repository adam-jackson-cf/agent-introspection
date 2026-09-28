import { DailyChart, DataTable, TableView, type Series } from "../charts";
import type { Row } from "../contracts";
import { HarnessName, Kpi, Panel, Section, SessionLink } from "../components";
import {
  EFFORT_COLOR,
  effortRank,
  fmtCount,
  fmtPct,
  fmtSeconds,
  maybe,
  num,
  pivotDaily,
  ratio,
  sumRows,
} from "../format";

const byEffort = (a: Row, b: Row) =>
  String(a.harness).localeCompare(String(b.harness)) ||
  effortRank(String(a.effort)) - effortRank(String(b.effort));

const effortSeries = (rows: Row[]): Series[] =>
  [...new Set(rows.map((row) => String(row.effort)))]
    .sort((a, b) => effortRank(a) - effortRank(b))
    .map((effort) => ({
      key: effort,
      label: effort,
      color: EFFORT_COLOR[effort] ?? "#6b7280",
    }));

const pct = (row: Row, key: string) =>
  fmtPct(ratio(num(row[`${key}_n`]), num(row[`${key}_den`])));

export default function Effort({ data }: { data: Record<string, Row[]> }) {
  const usage = data.usage ?? [];
  const outcomes = data.outcomes ?? [];
  const spend = sumRows(usage, ["reasoning", "output", "calls"]);
  const tasks = sumRows(outcomes, ["tasks", "heavy", "clean_n", "clean_den"]);
  const reasoningDaily = pivotDaily(
    data.daily_reasoning ?? [],
    (row) => String(row.effort),
    ["reasoning"],
    (sums) => sums.reasoning!,
  );
  const dailyTasks = data.daily_tasks ?? [];
  const mixDaily = pivotDaily(
    dailyTasks,
    (row) => String(row.effort),
    ["tasks"],
    (sums) => sums.tasks!,
  );
  const cleanDaily = pivotDaily(
    dailyTasks,
    (row) => String(row.effort),
    ["clean_n", "clean_den"],
    (sums) => ratio(sums.clean_n!, sums.clean_den!),
  );
  const durationDaily = dailyTasks.reduce<Record<string, Row>>((days, row) => {
    const day = String(row.day);
    days[day] = {
      ...(days[day] ?? { day }),
      [String(row.effort)]: maybe(row.p50_duration),
    };
    return days;
  }, {});
  const taskSeries = effortSeries(dailyTasks);
  const spendSeries = effortSeries(data.daily_reasoning ?? []);
  const totalReasoning = spend.reasoning!;
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Reasoning tokens"
          value={fmtCount(totalReasoning)}
          detail={`across ${fmtCount(spend.calls!)} model calls`}
          signals={["effort.reasoning_tokens"]}
        />
        <Kpi
          title="Reasoning share of output"
          value={fmtPct(ratio(totalReasoning, spend.output!))}
          detail="token-weighted over producers that emit reasoning"
          signals={["effort.reasoning_share"]}
        />
        <Kpi
          title="Tasks"
          value={fmtCount(tasks.tasks!)}
          detail={`${fmtPct(ratio(tasks.clean_n!, tasks.clean_den!))} clean completion`}
          signals={["task.count", "friction.clean_completion"]}
        />
        <Kpi
          title="Heavy-effort task share"
          value={fmtPct(ratio(tasks.heavy!, tasks.tasks!))}
          detail="high or xhigh requested"
          signals={["effort.heavy_share", "effort.level"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily reasoning tokens by effort"
          subtitle="Model calls; unset means the provider default, not no reasoning"
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
              ...taskSeries.map((entry) => ({
                key: entry.key,
                label: `${entry.label} %`,
                numeric: true,
              })),
            ]}
            rows={cleanDaily}
          />
        </Panel>
        <Panel
          title="Daily median task duration by effort"
          signals={["task.duration", "effort.level"]}
        >
          <DailyChart
            rows={Object.values(durationDaily).sort((a, b) =>
              String(a.day).localeCompare(String(b.day)),
            )}
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
            rows={Object.values(durationDaily)}
          />
        </Panel>
      </Section>
      <Section title="Does heavier effort earn its cost?">
        <Panel
          title="Task outcomes by reasoning effort"
          subtitle="Correlational: harder tasks may attract higher effort. Compare within one harness and model."
          signals={[
            "task.count",
            "friction.clean_completion",
            "friction.interrupt",
            "friction.quick_follow_up",
            "friction.error",
            "task.duration",
            "task.model_steps",
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
                render: (row) =>
                  (num(row.steps) / Math.max(1, num(row.tasks))).toFixed(1),
              },
              {
                key: "reasoning",
                label: "Reasoning / task",
                numeric: true,
                render: (row) =>
                  num(row.reasoning_den) > 0
                    ? fmtCount(num(row.reasoning) / num(row.reasoning_den))
                    : "—",
              },
              {
                key: "share",
                label: "Reasoning share",
                numeric: true,
                render: (row) =>
                  num(row.reasoning_den) > 0
                    ? fmtPct(ratio(num(row.reasoning), num(row.output)))
                    : "—",
              },
            ]}
            rows={[...outcomes].sort(byEffort)}
          />
        </Panel>
        <Panel
          title="Reasoning spend by effort"
          subtitle="Model calls that report reasoning tokens"
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
                render: (row) =>
                  fmtPct(ratio(num(row.reasoning), totalReasoning)),
              },
              {
                key: "per_call",
                label: "Per call",
                numeric: true,
                render: (row) =>
                  fmtCount(num(row.reasoning) / Math.max(1, num(row.calls))),
              },
              {
                key: "share",
                label: "Share of output",
                numeric: true,
                render: (row) =>
                  fmtPct(ratio(num(row.reasoning), num(row.output))),
              },
            ]}
            rows={[...usage].sort(byEffort)}
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
                render: (row) =>
                  num(row.reasoning_den) > 0
                    ? fmtCount(
                        num(row.reasoning) /
                          Math.max(1, num(row.avg_steps) * num(row.tasks)),
                      )
                    : "—",
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
