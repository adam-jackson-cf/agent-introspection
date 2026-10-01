import { DailyChart, DataTable, TableView } from "../charts";
import { HARNESSES, type Row } from "../contracts";
import { HarnessName, Kpi, Panel, Section, useView } from "../components";
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

export default function Provider({ data }: { data: Record<string, Row[]> }) {
  const { filters } = useView();
  const kpi = data.kpi ?? [];
  const all = sumRows(kpi, [
    "calls",
    "failed",
    "cancelled",
    "unknown",
    "retried",
    "attempt_den",
  ]);
  const sampling = data.codex_sampling ?? [];
  const codexRetries = [
    ...groupSum(sampling, "harness", ["steps", "attempts"]),
  ].map(([harness, sums]) => ({
    harness,
    steps: sums.steps!,
    attempts: sums.attempts!,
    extra: Math.max(0, sums.attempts! - sums.steps!),
  }));
  const claudeRetries = data.claude_retries ?? [];
  const claudeKpi = kpi.find((row) => row.harness === "claude-code");
  const ompKpi = kpi.find((row) => row.harness === "oh-my-pi");
  const ompRetries = num(data.omp_retries?.[0]?.retries);
  // One row per harness with its own retry route; zero is a valid observation
  // for a harness with model calls, and a harness without calls has no row.
  const retries: Row[] = [
    ...codexRetries.map((row) => ({
      harness: row.harness,
      extra: row.extra,
      route: `${fmtCount(row.attempts)} sampling attempts over ${fmtCount(row.steps)} steps`,
    })),
    ...(ompKpi
      ? [
          {
            harness: "oh-my-pi",
            extra: ompRetries,
            route: "auto-retry starts (activity hook)",
          },
        ]
      : []),
    ...(claudeKpi
      ? [
          {
            harness: "claude-code",
            extra: num(claudeKpi.retried),
            route: `requests with attempt > 1 · ${fmtCount(num(claudeRetries[0]?.failed_attempts))} failed attempts · ${fmtCount(num(claudeRetries[0]?.exhausted))} exhausted`,
          },
        ]
      : []),
  ];
  const extraAttempts = retries.reduce(
    (total, row) => total + num(row.extra),
    0,
  );
  const series = HARNESSES.filter(
    (harness) => filters.harness === "" || harness === filters.harness,
  ).map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  const daily = data.daily ?? [];
  const errorDaily = pivotDaily(
    daily,
    (row) => String(row.harness),
    ["failed", "calls"],
    (sums) => ratio(sums.failed!, sums.calls!),
  );
  const ttftDaily = daily.reduce<Record<string, Row>>((days, row) => {
    const day = String(row.day);
    days[day] = {
      ...(days[day] ?? { day }),
      [String(row.harness)]: maybe(row.ttft_p50),
    };
    return days;
  }, {});
  const latency: Row[] = [
    ...(data.latency ?? []).filter(
      (row) => !String(row.harness).startsWith("codex"),
    ),
    ...(data.latency ?? [])
      .filter((row) => String(row.harness).startsWith("codex"))
      .map((row) => {
        const step = sampling.find(
          (entry) => entry.harness === row.harness && entry.model === row.model,
        );
        return {
          ...row,
          p50: step?.p50 ?? null,
          p95: step?.p95 ?? null,
        } as Row;
      }),
  ].sort((a, b) => num(b.calls) - num(a.calls));
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Model calls"
          value={fmtCount(all.calls!)}
          detail={`${fmtCount(all.failed!)} failed · ${fmtCount(all.cancelled!)} cancelled by the user`}
          signals={["provider.calls"]}
        />
        <Kpi
          title="Call error rate"
          value={fmtPct(ratio(all.failed!, all.calls! - all.cancelled!), 2)}
          detail="failed / model calls not cancelled by the user"
          signals={["provider.error_rate"]}
        />
        <Kpi
          title="Retries"
          value={fmtCount(extraAttempts)}
          detail="attempts beyond the first per logical call"
          signals={["provider.retries"]}
        />
        <Kpi
          title="Unknown outcomes"
          value={fmtCount(all.unknown!)}
          detail="calls with no success flag, error, or finish reason"
          signals={["provider.unknown_outcomes"]}
        />
      </Section>
      <Section title="Daily trend">
        <Panel
          title="Daily call error rate"
          subtitle="Per harness"
          signals={["provider.error_rate"]}
        >
          <DailyChart
            rows={errorDaily}
            series={series}
            kind="line"
            format={(value) => fmtPct(value, 1)}
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
            rows={errorDaily}
          />
        </Panel>
        <Panel
          title="Daily median time to first token"
          subtitle="Per harness"
          signals={["provider.ttft"]}
        >
          <DailyChart
            rows={Object.values(ttftDaily).sort((a, b) =>
              String(a.day).localeCompare(String(b.day)),
            )}
            series={series}
            kind="line"
            format={fmtSeconds}
          />
          <TableView
            columns={[
              { key: "day", label: "Day" },
              ...series.map((entry) => ({
                key: entry.key,
                label: `${entry.label} (s)`,
                numeric: true,
              })),
            ]}
            rows={Object.values(ttftDaily)}
          />
        </Panel>
      </Section>
      <Section title="Where does provider time go?">
        <Panel
          title="Latency and time to first token by model"
          subtitle="Codex latency is the sampling step (includes retries and tool draining); its calls are response streams"
          signals={["provider.latency", "provider.ttft"]}
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
              {
                key: "calls",
                label: "Calls",
                numeric: true,
                render: (row) => fmtCount(num(row.calls)),
              },
              {
                key: "p50",
                label: "P50 latency",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.p50)),
              },
              {
                key: "p95",
                label: "P95 latency",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.p95)),
              },
              {
                key: "ttft_p50",
                label: "P50 TTFT",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.ttft_p50)),
              },
              {
                key: "ttft_p95",
                label: "P95 TTFT",
                numeric: true,
                render: (row) => fmtSeconds(maybe(row.ttft_p95)),
              },
            ]}
            rows={latency}
          />
        </Panel>
      </Section>
      <Section title="Errors and retries">
        <Panel title="Call errors by class" signals={["provider.error_rate"]}>
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              { key: "model", label: "Model" },
              { key: "error_class", label: "Class" },
              { key: "n", label: "Calls", numeric: true },
              {
                key: "last_seen",
                label: "Last seen (UTC)",
                render: (row) => String(row.last_seen).slice(0, 16),
              },
            ]}
            rows={data.errors ?? []}
            empty="No failed model calls in the selected range."
          />
        </Panel>
        <Panel
          title="Retries"
          subtitle="Attempts beyond the first"
          signals={["provider.retries"]}
        >
          <DataTable
            columns={[
              {
                key: "harness",
                label: "Harness",
                render: (row) => <HarnessName value={row.harness} />,
              },
              {
                key: "extra",
                label: "Extra attempts",
                numeric: true,
                render: (row) => fmtCount(num(row.extra)),
              },
              { key: "route", label: "Measured as" },
            ]}
            rows={retries}
            empty="No model calls in the selected range."
          />
        </Panel>
      </Section>
    </>
  );
}
