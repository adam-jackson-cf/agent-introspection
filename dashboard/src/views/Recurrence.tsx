import { DailyChart, DataTable, TableView } from "../charts";
import { HARNESSES, type Row } from "../contracts";
import { HarnessName, Kpi, Panel, Section, useView } from "../components";
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

/** A signature is localized when at least 80% of its attributed occurrences share one working directory. */
const LOCALIZED = 0.8;

export default function Recurrence({ data }: { data: Record<string, Row[]> }) {
  const { filters } = useView();
  const targets = sumRows(data.target_summary ?? [], ["targets", "recurring"]);
  const signatures = sumRows(data.signature_summary ?? [], [
    "signatures",
    "recurring",
  ]);
  const actionable = data.actionable ?? [];
  const concentration = data.concentration ?? [];
  const localized = concentration.filter(
    (row) => num(row.top_count) >= LOCALIZED * num(row.attributed),
  );
  const series = HARNESSES.filter(
    (harness) => filters.harness === "" || harness === filters.harness,
  ).map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const daily = pivotDaily(
    data.daily ?? [],
    (row) => String(row.harness),
    ["failures"],
    (sums) => sums.failures!,
  );
  const weekEnd = filters.end.slice(0, 10);
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Actionable repeats"
          value={fmtCount(actionable.length)}
          detail={`signatures over the 7 London days to ${weekEnd}`}
          signals={["recur.actionable"]}
        />
        <Kpi
          title="Recurring failure signatures"
          value={fmtCount(signatures.recurring!)}
          detail={`of ${fmtCount(signatures.signatures!)} signatures seen in ≥ 2 tasks`}
          signals={["recur.signatures"]}
        />
        <Kpi
          title="Recurring targets"
          value={fmtCount(targets.recurring!)}
          detail={`of ${fmtCount(targets.targets!)} files touched in ≥ 2 tasks`}
          signals={["recur.targets"]}
        />
        <Kpi
          title="Localized signatures"
          value={fmtPct(ratio(localized.length, concentration.length), 0)}
          detail={`${localized.length} of ${concentration.length} signatures with ≥ 80% in one project`}
          signals={["recur.project_concentration"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily tool failures with a signature"
          subtitle="Europe/London days, stacked by harness"
          signals={["recur.signatures"]}
          span={12}
        >
          <DailyChart
            rows={daily}
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
            rows={daily}
          />
        </Panel>
      </Section>
      <Section title="What crosses the evidence threshold?">
        <Panel
          title="Actionable repeats"
          subtitle="≥ 3 occurrences in ≥ 2 tasks on ≥ 2 days, or ≥ 5 occurrences in ≥ 3 tasks, over 7 Europe/London days"
          signals={["recur.actionable"]}
          span={12}
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
              { key: "days", label: "Days", numeric: true },
              {
                key: "last_seen",
                label: "Last seen (UTC)",
                render: (row) => String(row.last_seen).slice(0, 16),
              },
            ]}
            rows={actionable}
            empty="No failure signature crosses the threshold in the 7 days ending on the window end."
          />
        </Panel>
      </Section>
      <Section title="What recurs across tasks?">
        <Panel
          title="Recurring failure signatures"
          subtitle="Seen in ≥ 2 tasks over the selected window"
          signals={["recur.signatures"]}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "signature", label: "Signature" },
              { key: "occurrences", label: "Count", numeric: true },
              { key: "tasks", label: "Tasks", numeric: true },
              { key: "days", label: "Days", numeric: true },
            ]}
            rows={data.signatures ?? []}
          />
        </Panel>
        <Panel
          title="Recurring targets"
          subtitle="Files touched in ≥ 2 tasks, with failures on them"
          signals={["recur.targets"]}
        >
          <DataTable
            columns={[
              { key: "target", label: "File" },
              { key: "harnesses", label: "Harnesses" },
              { key: "tasks", label: "Tasks", numeric: true },
              { key: "days", label: "Days", numeric: true },
              { key: "calls", label: "Calls", numeric: true },
              { key: "failed", label: "Failed", numeric: true },
            ]}
            rows={data.targets ?? []}
          />
        </Panel>
        <Panel
          title="Project concentration"
          subtitle="Where each repeated signature occurs (working directory as project)"
          signals={["recur.project_concentration"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "signature", label: "Signature" },
              { key: "occurrences", label: "Count", numeric: true },
              { key: "workdirs", label: "Projects", numeric: true },
              { key: "top_workdir", label: "Top project" },
              {
                key: "share",
                label: "Top share",
                numeric: true,
                render: (row) =>
                  fmtPct(ratio(num(row.top_count), num(row.attributed)), 0),
              },
            ]}
            rows={concentration}
          />
        </Panel>
      </Section>
    </>
  );
}
