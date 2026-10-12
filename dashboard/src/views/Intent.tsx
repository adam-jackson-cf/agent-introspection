import { BarList, DailyChart, DataTable, TableView } from "../charts";
import { HARNESSES, type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  RateValue,
  rateSeriesColumns,
  Section,
  SessionLink,
  useSelectedHarnesses,
} from "../components";
import {
  fmtCount,
  fmtPct,
  HARNESS_COLOR,
  HARNESS_LABEL,
  harnessLabel,
  maybe,
  num,
  SERIES,
  shortTime,
} from "../format";

const words = (value: unknown) => String(value ?? "").replaceAll("_", " ");
const harnessOrder = (harness: unknown) =>
  (HARNESSES as readonly unknown[]).indexOf(harness);
/** Labelled tasks whose type is empty get their own row, so shares sum to 100%. */
export const typeLabel = (type: string) => (type === "" ? "untyped" : type);

/** Server rate and sample size of a `<key>_rate` / `<key>_den` column pair. */
const pct = (row: Row, key: string) => (
  <RateValue pct={maybe(row[`${key}_rate`])} n={num(row[`${key}_den`])} />
);

/** Long daily rows to one row per day: each harness's server rate and its n. */
function pivotCorrectedRate(rows: Row[]): Row[] {
  const days = new Map<string, Row>();
  for (const row of rows) {
    const day = String(row.day);
    const out = days.get(day) ?? { day };
    const harness = String(row.harness);
    out[harness] = maybe(row.corrected_rate);
    out[`${harness}_n`] = num(row.corrected_den);
    days.set(day, out);
  }
  return [...days.values()];
}

/** Rows of a view dataset, empty while the server omits it. */
const rowsOf = (data: Record<string, Row[]>, key: string) => data[key] ?? [];

export default function Intent({ data }: { data: Record<string, Row[]> }) {
  const selected = useSelectedHarnesses();
  const kpi = rowsOf(data, "kpi");
  // The rollup row (harness '') is All, computed by the server.
  const all = kpi.find((row) => row.harness === "") ?? {};
  const byHarness = kpi.filter((row) => row.harness !== "");
  const series = selected.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const correctedDaily = pivotCorrectedRate(rowsOf(data, "daily"));
  const types = rowsOf(data, "task_types");
  const typeMix = rowsOf(data, "task_type_mix");
  const typeDetail = (type: string) =>
    types
      .filter((row) => row.task_type === type)
      .sort((a, b) => harnessOrder(a.harness) - harnessOrder(b.harness))
      .map((row) => `${harnessLabel(row.harness)} ${fmtCount(num(row.tasks))}`)
      .join(" · ");
  const kindTotals = rowsOf(data, "correction_kind_totals");
  const payoff = rowsOf(data, "effort_payoff");
  const repeated = rowsOf(data, "repeated");
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Labelled tasks"
          value={fmtPct(maybe(all.labelled_rate))}
          detail={`${fmtCount(num(all.labelled))} of ${fmtCount(num(all.tasks))} tasks have a labelled prompt`}
          n={num(all.tasks)}
          signals={["intent.labelled"]}
        />
        <Kpi
          title="Corrected by the next prompt"
          value={fmtPct(maybe(all.corrected_rate))}
          detail={`${fmtCount(num(all.corrected_n))} of ${fmtCount(num(all.corrected_den))} tasks`}
          n={num(all.corrected_den)}
          signals={["intent.corrected"]}
        />
        <Kpi
          title="Frustrated follow-ups"
          value={fmtPct(maybe(all.frustrated_rate))}
          detail={`${fmtCount(num(all.frustrated_n))} of ${fmtCount(num(all.frustrated_den))} labelled next prompts`}
          n={num(all.frustrated_den)}
          signals={["intent.frustration"]}
        />
        <Kpi
          title="Repeated corrections"
          value={fmtCount(num(data.repeated_count?.[0]?.repeated))}
          detail="project × correction kind in ≥ 2 tasks and ≥ 2 sessions"
          signals={["intent.correction_kind"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily corrected-by-next-prompt rate"
          subtitle="Tasks whose next prompt within 10 minutes corrected the agent, per harness"
          signals={["intent.corrected"]}
          span={12}
        >
          <DailyChart
            rows={correctedDaily}
            series={series}
            kind="line"
            max={100}
            format={(value) => fmtPct(value, 0)}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...rateSeriesColumns(series),
            ]}
            rows={correctedDaily}
          />
        </Panel>
      </Section>
      <Section title="What do users ask for, and what do they correct?">
        <Panel
          title="Task type mix"
          subtitle="Labelled tasks by Jev task type; each harness's count underneath"
          signals={["intent.task_type"]}
        >
          <BarList
            items={typeMix.map((row) => ({
              key: String(row.task_type),
              label: typeLabel(String(row.task_type)),
              value: num(row.tasks),
              color: SERIES[0]!,
              detail: `${fmtPct(maybe(row.share_rate), 0)} · ${typeDetail(String(row.task_type))}`,
            }))}
            format={fmtCount}
          />
          <TableView
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              {
                key: "task_type",
                label: "Task type",
                render: (row) => typeLabel(String(row.task_type)),
              },
              { key: "tasks", label: "Tasks", numeric: true },
            ]}
            rows={types}
          />
        </Panel>
        <Panel
          title="Correction kinds"
          subtitle="What the next prompt corrected"
          signals={["intent.correction_kind"]}
        >
          <BarList
            items={kindTotals.map((row) => ({
              key: String(row.correction_kind),
              label: words(row.correction_kind),
              value: num(row.tasks),
              color: SERIES[1]!,
            }))}
            format={fmtCount}
          />
          <TableView
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              {
                key: "correction_kind",
                label: "Correction",
                render: (row) => words(row.correction_kind),
              },
              { key: "tasks", label: "Tasks", numeric: true },
            ]}
            rows={rowsOf(data, "correction_kinds")}
          />
        </Panel>
        <Panel
          title="By harness"
          subtitle="Each harness's own counts; All above is their sum"
          signals={[
            "intent.labelled",
            "intent.corrected",
            "intent.frustration",
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
              { key: "labelled", label: "Labelled", numeric: true },
              {
                key: "labelled_share",
                label: "Labelled share",
                numeric: true,
                render: (row) => (
                  <RateValue
                    pct={maybe(row.labelled_rate)}
                    n={num(row.tasks)}
                  />
                ),
              },
              {
                key: "corrected",
                label: "Corrected next",
                numeric: true,
                render: (row) => pct(row, "corrected"),
              },
              {
                key: "frustrated",
                label: "Frustrated next",
                numeric: true,
                render: (row) => pct(row, "frustrated"),
              },
            ]}
            rows={byHarness}
          />
        </Panel>
      </Section>
      <Section title="Does heavier effort reduce corrections?">
        <Panel
          title="Effort payoff by task type"
          subtitle="Compare efforts within one task type, never harnesses. Correlational: harder tasks may attract higher effort."
          signals={["intent.effort_payoff", "effort.level"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "task_type", label: "Task type" },
              { key: "effort", label: "Effort" },
              { key: "tasks", label: "Tasks", numeric: true },
              {
                key: "corrected",
                label: "Corrected next",
                numeric: true,
                render: (row) => pct(row, "corrected"),
              },
              {
                key: "clean",
                label: "Clean completion",
                numeric: true,
                render: (row) => pct(row, "clean"),
              },
            ]}
            rows={payoff}
          />
        </Panel>
      </Section>
      <Section title="Which corrections repeat?">
        <Panel
          title="Repeated corrections by project"
          subtitle="Project × correction kind over the selected window; ≥ 2 tasks in ≥ 2 sessions is a repeat. Evaluation workspaces excluded."
          signals={["intent.correction_kind", "intent.corrected"]}
          span={12}
        >
          <DataTable
            columns={[
              { key: "project", label: "Project" },
              {
                key: "correction_kind",
                label: "Correction",
                render: (row) => words(row.correction_kind),
              },
              {
                key: "harnesses",
                label: "Harnesses",
                render: (row) => (
                  <span className="breakdown">
                    {(Array.isArray(row.harnesses) ? row.harnesses : []).map(
                      (harness) => (
                        <HarnessName key={String(harness)} value={harness} />
                      ),
                    )}
                  </span>
                ),
              },
              { key: "tasks", label: "Tasks", numeric: true },
              { key: "sessions", label: "Sessions", numeric: true },
              { key: "task_types", label: "Task types" },
              {
                key: "last_seen",
                label: "Last seen (UTC)",
                render: (row) => shortTime(row.last_seen),
              },
            ]}
            rows={repeated}
            empty="No corrected task has a project in the selected range."
          />
        </Panel>
        <Panel
          title="Recent corrected tasks"
          subtitle="The latest 25 tasks whose next prompt corrected the agent; open one for its session. Evaluation workspaces excluded."
          signals={["intent.corrected", "intent.task_type"]}
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
              {
                key: "started",
                label: "Started (UTC)",
                render: (row) => String(row.started).slice(0, 16),
              },
              {
                key: "task_type",
                label: "Task type",
                render: (row) => String(row.task_type) || "—",
              },
              { key: "effort", label: "Effort" },
              {
                key: "correction_kind",
                label: "Correction",
                render: (row) => words(row.correction_kind) || "—",
              },
              {
                key: "sentiment_next",
                label: "Next sentiment",
                render: (row) => String(row.sentiment_next) || "—",
              },
              {
                key: "project",
                label: "Project",
                render: (row) => String(row.project ?? "") || "—",
              },
            ]}
            rows={rowsOf(data, "recent")}
            empty="No task was corrected by its next prompt in the selected range."
          />
        </Panel>
      </Section>
    </>
  );
}
