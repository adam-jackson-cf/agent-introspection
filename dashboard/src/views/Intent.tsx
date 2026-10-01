import { BarList, DailyChart, DataTable, TableView } from "../charts";
import { HARNESSES, type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  Section,
  SessionLink,
  useSelectedHarnesses,
} from "../components";
import {
  effortRank,
  fmtCount,
  fmtPct,
  groupSum,
  HARNESS_COLOR,
  HARNESS_LABEL,
  harnessLabel,
  num,
  pivotDaily,
  ratio,
  SERIES,
  shortTime,
  sumRows,
} from "../format";

const KPI_FIELDS = [
  "tasks",
  "labelled",
  "corrected_n",
  "corrected_den",
  "frustrated_n",
  "frustrated_den",
];
/** Jev's task types in classifier order; unknown types follow. */
const TASK_TYPES = [
  "bugfix",
  "feature",
  "refactor",
  "investigate",
  "review",
  "ops",
  "docs",
  "continue",
];
const typeRank = (type: string) => {
  const index = TASK_TYPES.indexOf(type);
  return index === -1 ? TASK_TYPES.length : index;
};
const words = (value: unknown) => String(value ?? "").replaceAll("_", " ");
const harnessOrder = (harness: unknown) =>
  (HARNESSES as readonly unknown[]).indexOf(harness);

const stringList = (value: unknown) =>
  Array.isArray(value) ? value.map(String) : [];

/**
 * Recombines per-harness rows of one (project, correction kind) into a single
 * row: tasks and sessions are harness-scoped, so they sum.
 */
export function recombineCorrections(rows: Row[]): Row[] {
  const groups = new Map<string, Row[]>();
  for (const row of rows) {
    const key = `${String(row.project)}\u0000${String(row.correction_kind)}`;
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  return [...groups.values()]
    .map((members) => {
      const sums = sumRows(members, ["tasks", "sessions"]);
      return {
        project: members[0]!.project ?? "",
        correction_kind: members[0]!.correction_kind ?? "",
        harnesses: [...members]
          .sort((a, b) => harnessOrder(a.harness) - harnessOrder(b.harness))
          .map((row) => String(row.harness)),
        tasks: sums.tasks!,
        sessions: sums.sessions!,
        task_types: [
          ...new Set(members.flatMap((row) => stringList(row.task_types))),
        ].sort((a, b) => typeRank(a) - typeRank(b)),
        last_seen: members
          .map((row) => String(row.last_seen ?? ""))
          .sort()
          .at(-1)!,
      } as Row;
    })
    .sort(
      (a, b) =>
        num(b.tasks) - num(a.tasks) ||
        String(a.project).localeCompare(String(b.project)),
    );
}

/** Task type × effort over every selected harness: counts sum, ratios divide after. */
export function effortPayoff(rows: Row[]): Row[] {
  const groups = new Map<string, Row[]>();
  for (const row of rows) {
    const key = `${String(row.task_type)}\u0000${String(row.effort)}`;
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  return [...groups.values()]
    .map((members) => ({
      task_type: members[0]!.task_type ?? "",
      effort: members[0]!.effort ?? "",
      ...sumRows(members, [
        "tasks",
        "corrected_n",
        "corrected_den",
        "clean_n",
        "clean_den",
      ]),
    }))
    .sort(
      (a, b) =>
        typeRank(String(a.task_type)) - typeRank(String(b.task_type)) ||
        effortRank(String(a.effort)) - effortRank(String(b.effort)),
    );
}

const pct = (row: Row, key: string) =>
  fmtPct(ratio(num(row[`${key}_n`]), num(row[`${key}_den`])));

export default function Intent({ data }: { data: Record<string, Row[]> }) {
  const selected = useSelectedHarnesses();
  const kpi = data.kpi ?? [];
  const all = sumRows(kpi, KPI_FIELDS);
  const series = selected.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const correctedDaily = pivotDaily(
    data.daily ?? [],
    (row) => String(row.harness),
    ["corrected_n", "corrected_den"],
    (sums) => ratio(sums.corrected_n!, sums.corrected_den!),
  );
  const types = data.task_types ?? [];
  const typeTotals = [...groupSum(types, "task_type", ["tasks"])].sort(
    ([a], [b]) => typeRank(a) - typeRank(b),
  );
  const labelledTypes = typeTotals.reduce(
    (total, [, sums]) => total + sums.tasks!,
    0,
  );
  const typeDetail = (type: string) =>
    types
      .filter((row) => row.task_type === type)
      .sort((a, b) => harnessOrder(a.harness) - harnessOrder(b.harness))
      .map((row) => `${harnessLabel(row.harness)} ${fmtCount(num(row.tasks))}`)
      .join(" · ");
  const kinds = [
    ...groupSum(data.correction_kinds ?? [], "correction_kind", ["tasks"]),
  ].sort(([a], [b]) => a.localeCompare(b));
  const payoff = effortPayoff(data.effort_payoff ?? []);
  const repeated = recombineCorrections(data.repeated ?? []);
  const recurring = repeated.filter(
    (row) => num(row.tasks) >= 2 && num(row.sessions) >= 2,
  );
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Labelled tasks"
          value={fmtPct(ratio(all.labelled!, all.tasks!))}
          detail={`${fmtCount(all.labelled!)} of ${fmtCount(all.tasks!)} tasks have a labelled prompt`}
          signals={["intent.labelled"]}
        />
        <Kpi
          title="Corrected by the next prompt"
          value={fmtPct(ratio(all.corrected_n!, all.corrected_den!))}
          detail={`${fmtCount(all.corrected_n!)} of ${fmtCount(all.corrected_den!)} tasks`}
          signals={["intent.corrected"]}
        />
        <Kpi
          title="Frustrated follow-ups"
          value={fmtPct(ratio(all.frustrated_n!, all.frustrated_den!))}
          detail={`${fmtCount(all.frustrated_n!)} of ${fmtCount(all.frustrated_den!)} labelled next prompts`}
          signals={["intent.frustration"]}
        />
        <Kpi
          title="Repeated corrections"
          value={fmtCount(recurring.length)}
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
              ...series.map((entry) => ({
                key: entry.key,
                label: `${entry.label} %`,
                numeric: true,
              })),
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
            items={typeTotals.map(([type, sums]) => ({
              key: type,
              label: type,
              value: sums.tasks!,
              color: SERIES[0]!,
              detail: `${fmtPct(ratio(sums.tasks!, labelledTypes), 0)} · ${typeDetail(type)}`,
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
              { key: "task_type", label: "Task type" },
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
            items={kinds.map(([kind, sums]) => ({
              key: kind,
              label: words(kind),
              value: sums.tasks!,
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
            rows={data.correction_kinds ?? []}
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
                render: (row) =>
                  fmtPct(ratio(num(row.labelled), num(row.tasks))),
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
            rows={kpi}
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
          subtitle="The latest 25 tasks whose next prompt corrected the agent; open one for its session"
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
            rows={data.recent ?? []}
            empty="No task was corrected by its next prompt in the selected range."
          />
        </Panel>
      </Section>
    </>
  );
}
