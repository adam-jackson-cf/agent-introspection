import { DailyChart, DataTable, TableView } from "../charts";
import { type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  Section,
  SessionLink,
  useSelectedHarnesses,
  useVisible,
} from "../components";
import {
  fmtCount,
  fmtPct,
  HARNESS_COLOR,
  HARNESS_LABEL,
  maybe,
  num,
  SERIES,
} from "../format";

/** Pure reshape: one wide row per day, values copied from the server rows. */
function wideByDay(
  rows: Row[],
  pick: (row: Row) => string | null,
  value: (row: Row) => number | null,
): Row[] {
  const days = new Map<string, Row>();
  for (const row of rows) {
    const series = pick(row);
    if (series === null) continue;
    const day = String(row.day);
    days.set(day, { ...days.get(day), day, [series]: value(row) });
  }
  return [...days.values()];
}

export default function Cache({ data }: { data: Record<string, Row[]> }) {
  const selected = useSelectedHarnesses();
  const creationShown = useVisible("usage.cache_creation");
  const kpi = data.kpi ?? [];
  const all = kpi.find((row) => row.harness === "") ?? {};
  const byHarness = kpi.filter((row) => row.harness !== "");
  const daily = data.daily ?? [];
  const partition = daily
    .filter((row) => row.harness === "")
    .map((row) => ({
      day: row.day,
      uncached: num(row.uncached),
      cached: num(row.cached),
      output: num(row.output),
    }));
  const harnesses = selected;
  const utilizationDaily = wideByDay(
    daily,
    (row) => (row.harness === "" ? null : String(row.harness)),
    (row) => maybe(row.cache_rate),
  );
  const partitionSeries = [
    { key: "uncached", label: "Uncached input", color: SERIES[1]! },
    { key: "cached", label: "Cached input", color: SERIES[0]! },
    { key: "output", label: "Output", color: SERIES[2]! },
  ];
  const harnessSeries = harnesses.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Input tokens"
          value={fmtCount(num(all.input))}
          detail={`${fmtCount(num(all.operations))} completed operations`}
          signals={["usage.input_tokens", "usage.operations"]}
        />
        <Kpi
          title="Input cache utilization"
          value={fmtPct(maybe(all.cache_rate))}
          detail={`${fmtCount(num(all.cached))} cached of ${fmtCount(num(all.input))}`}
          signals={["usage.cache_utilization", "usage.cached_input"]}
        />
        <Kpi
          title="Uncached input"
          value={fmtCount(num(all.uncached))}
          detail={
            creationShown
              ? `incl. ${fmtCount(num(all.creation))} written to cache`
              : `${fmtPct(maybe(all.uncached_rate))} of input`
          }
          signals={["usage.uncached_input", "usage.cache_creation"]}
        />
        <Kpi
          title="Output tokens"
          value={fmtCount(num(all.output))}
          detail={`${fmtCount(num(all.sessions))} usage-active sessions`}
          signals={["usage.output_tokens", "usage.active_sessions"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily token consumption"
          subtitle="Mutually exclusive parts; stack height is total processed tokens"
          signals={[
            "usage.uncached_input",
            "usage.cached_input",
            "usage.output_tokens",
          ]}
          span={7}
        >
          <DailyChart
            rows={partition}
            series={partitionSeries}
            kind="stack"
            format={fmtCount}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...partitionSeries.map((entry) => ({
                key: entry.key,
                label: entry.label,
                numeric: true,
              })),
            ]}
            rows={partition}
          />
        </Panel>
        <Panel
          title="Daily input cache utilization"
          subtitle="Cached share of input per day, token-weighted within each harness"
          signals={["usage.cache_utilization"]}
          span={5}
        >
          <DailyChart
            rows={utilizationDaily}
            series={harnessSeries}
            kind="line"
            max={100}
            format={(value) => fmtPct(value, 0)}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...harnessSeries.map((entry) => ({
                key: entry.key,
                label: `${entry.label} %`,
                numeric: true,
              })),
            ]}
            rows={utilizationDaily}
          />
        </Panel>
      </Section>
      <Section title="Breakdowns">
        <Panel
          title="By harness"
          subtitle="Each harness's own totals; All above is their sum"
          signals={[
            "usage.input_tokens",
            "usage.cache_utilization",
            "usage.cache_creation",
            "usage.operations",
            "usage.active_sessions",
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
                key: "input",
                label: "Input",
                numeric: true,
                render: (row) => fmtCount(num(row.input)),
              },
              {
                key: "cached",
                label: "Cached",
                numeric: true,
                render: (row) => fmtCount(num(row.cached)),
              },
              {
                key: "util",
                label: "Utilization",
                numeric: true,
                render: (row) => fmtPct(maybe(row.cache_rate)),
              },
              {
                key: "uncached",
                label: "Uncached",
                numeric: true,
                render: (row) => fmtCount(num(row.uncached)),
              },
              ...(creationShown
                ? [
                    {
                      key: "creation",
                      label: "Cache writes",
                      numeric: true,
                      render: (row: Row) => fmtCount(num(row.creation)),
                    },
                  ]
                : []),
              {
                key: "output",
                label: "Output",
                numeric: true,
                render: (row) => fmtCount(num(row.output)),
              },
              {
                key: "operations",
                label: "Operations",
                numeric: true,
                render: (row) => fmtCount(num(row.operations)),
              },
              { key: "sessions", label: "Sessions", numeric: true },
            ]}
            rows={byHarness}
          />
        </Panel>
        <Panel
          title="By model"
          subtitle="Harness and model, largest input first"
          signals={["usage.input_tokens", "usage.cache_utilization"]}
          span={12}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "provider", label: "Provider" },
              { key: "model", label: "Model" },
              { key: "operations", label: "Operations", numeric: true },
              {
                key: "input",
                label: "Input",
                numeric: true,
                render: (row) => fmtCount(num(row.input)),
              },
              {
                key: "util",
                label: "Utilization",
                numeric: true,
                render: (row) => fmtPct(maybe(row.cache_rate)),
              },
              {
                key: "uncached",
                label: "Uncached",
                numeric: true,
                render: (row) => fmtCount(num(row.uncached)),
              },
              {
                key: "output",
                label: "Output",
                numeric: true,
                render: (row) => fmtCount(num(row.output)),
              },
            ]}
            rows={data.models ?? []}
          />
        </Panel>
      </Section>
      <Section title="Where uncached input goes">
        <Panel
          title="Sessions with the most uncached input"
          subtitle="Top 20 sessions; open one for its tasks and calls"
          signals={["usage.uncached_input", "usage.active_sessions"]}
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
              { key: "models", label: "Models" },
              {
                key: "uncached",
                label: "Uncached",
                numeric: true,
                render: (row) => fmtCount(num(row.uncached)),
              },
              {
                key: "util",
                label: "Utilization",
                numeric: true,
                render: (row) => fmtPct(maybe(row.cache_rate)),
              },
              {
                key: "output",
                label: "Output",
                numeric: true,
                render: (row) => fmtCount(num(row.output)),
              },
              { key: "operations", label: "Operations", numeric: true },
              {
                key: "last_seen",
                label: "Last seen (UTC)",
                render: (row) => String(row.last_seen).slice(0, 16),
              },
            ]}
            rows={data.sessions ?? []}
          />
        </Panel>
      </Section>
    </>
  );
}
