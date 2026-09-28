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
    "disconnects",
    "disconnect_den",
    "mismatched",
    "with_response_model",
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
          title="Stream disconnects"
          value={fmtCount(all.disconnects!)}
          detail={`of ${fmtCount(all.disconnect_den!)} response streams`}
          signals={["provider.stream_disconnects"]}
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
          title="Latency, time to first token, and throughput by model"
          subtitle="Codex latency is the sampling step (includes retries and tool draining); its calls are response streams"
          signals={["provider.latency", "provider.ttft", "provider.throughput"]}
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
              {
                key: "tps_p50",
                label: "P50 tokens/s",
                numeric: true,
                render: (row) =>
                  maybe(row.tps_p50) === null
                    ? "—"
                    : num(row.tps_p50).toFixed(0),
              },
            ]}
            rows={latency}
          />
        </Panel>
      </Section>
      <Section title="Errors, retries, and conformance">
        <Panel
          title="Call errors by class"
          signals={["provider.error_rate", "provider.stream_disconnects"]}
        >
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
              { key: "steps", label: "Sampling steps", numeric: true },
              { key: "attempts", label: "Attempts", numeric: true },
              { key: "extra", label: "Extra attempts", numeric: true },
            ]}
            rows={codexRetries}
            empty="No Codex sampling steps in the selected range."
          />
          {claudeKpi && (
            <DataTable
              columns={[
                {
                  key: "harness",
                  label: "Harness",
                  render: (row) => <HarnessName value={row.harness} />,
                },
                { key: "retried", label: "Retried requests", numeric: true },
                {
                  key: "failed_attempts",
                  label: "Failed attempts",
                  numeric: true,
                },
                { key: "exhausted", label: "Retries exhausted", numeric: true },
              ]}
              rows={[
                {
                  harness: "claude-code",
                  retried: claudeKpi.retried ?? 0,
                  failed_attempts: claudeRetries[0]?.failed_attempts ?? 0,
                  exhausted: claudeRetries[0]?.exhausted ?? 0,
                },
              ]}
            />
          )}
        </Panel>
        <Panel
          title="Model conformance"
          subtitle="Calls whose served model differs from the requested model"
          signals={["provider.model_conformance"]}
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
                key: "with_response_model",
                label: "Calls with a served model",
                numeric: true,
              },
              { key: "mismatched", label: "Mismatched", numeric: true },
              {
                key: "rate",
                label: "Mismatch rate",
                numeric: true,
                render: (row) =>
                  fmtPct(
                    ratio(num(row.mismatched), num(row.with_response_model)),
                    2,
                  ),
              },
            ]}
            rows={kpi.filter((row) => num(row.with_response_model) > 0)}
            empty="No calls report a served model in the selected range."
          />
        </Panel>
      </Section>
    </>
  );
}
