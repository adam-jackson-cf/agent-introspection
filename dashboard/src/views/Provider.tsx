import { DailyChart, DataTable, TableView } from "../charts";
import { type Row } from "../contracts";
import {
  HarnessName,
  Kpi,
  Panel,
  RateValue,
  rateSeriesColumns,
  Section,
  useSelectedHarnesses,
  useVisible,
  ViewLink,
} from "../components";
import {
  fmtCount,
  fmtPct,
  fmtSeconds,
  HARNESS_COLOR,
  HARNESS_LABEL,
  maybe,
  num,
} from "../format";

/** The server's All row (ROLLUP over harness) carries harness ''. */
const isAll = (row: Row) => row.harness === "";

/** "Measured as" text from the server's counts for each harness's retry route. */
function retryRoute(row: Row): string {
  const harness = String(row.harness);
  if (harness.startsWith("codex"))
    return `${fmtCount(num(row.attempts))} sampling attempts over ${fmtCount(num(row.steps))} steps`;
  if (harness === "oh-my-pi") return "auto-retry starts (activity hook)";
  return `requests with attempt > 1 · ${fmtCount(num(row.failed_attempts))} failed attempts · ${fmtCount(num(row.exhausted))} exhausted`;
}

/** Long daily rows to one row per day: error rate (and its n) and TTFT per harness. */
function dailyByDay(daily: Row[]): { errors: Row[]; ttft: Row[] } {
  const errors: Record<string, Row> = {};
  const ttft: Record<string, Row> = {};
  for (const row of daily) {
    const day = String(row.day);
    const harness = String(row.harness);
    errors[day] = {
      ...(errors[day] ?? { day }),
      [harness]: maybe(row.error_rate),
      [`${harness}_n`]: num(row.rated_calls),
    };
    ttft[day] = { ...(ttft[day] ?? { day }), [harness]: maybe(row.ttft_p50) };
  }
  return { errors: Object.values(errors), ttft: Object.values(ttft) };
}

function providerModel(data: Record<string, Row[]>) {
  const kpi = data.kpi ?? [];
  const retryAll = data.retries ?? [];
  const daily = dailyByDay(data.daily ?? []);
  return {
    all: kpi.find(isAll) ?? {},
    retries: retryAll
      .filter((row) => !isAll(row))
      .map((row) => ({
        harness: row.harness,
        extra: row.extra,
        route: retryRoute(row),
      })),
    extraAttempts: num(retryAll.find(isAll)?.extra),
    errorDaily: daily.errors,
    ttftDaily: daily.ttft,
    latency: data.latency ?? [],
  };
}

export default function Provider({ data }: { data: Record<string, Row[]> }) {
  const selected = useSelectedHarnesses();
  const disconnectsShown = useVisible("provider.stream_disconnects");
  const throughputShown = useVisible("provider.throughput");
  const conformanceShown = useVisible("provider.model_conformance");
  const { all, retries, extraAttempts, errorDaily, ttftDaily, latency } =
    providerModel(data);
  const series = selected.map((harness) => ({
    key: harness,
    label: HARNESS_LABEL[harness],
    color: HARNESS_COLOR[harness],
  }));
  return (
    <>
      <Section title="At a glance">
        <Kpi
          title="Model calls"
          value={fmtCount(num(all.calls))}
          detail={`${fmtCount(num(all.failed))} failed · ${fmtCount(num(all.cancelled))} cancelled by the user`}
          signals={["provider.calls"]}
        />
        <Kpi
          title="Call error rate"
          value={fmtPct(maybe(all.error_rate), 2)}
          detail={`failed / model calls not cancelled by the user · n=${fmtCount(num(all.rated_calls))}`}
          n={num(all.rated_calls)}
          signals={["provider.error_rate"]}
        />
        {disconnectsShown ? (
          <Kpi
            title="Stream disconnects"
            value={fmtCount(num(all.disconnects))}
            detail={`of ${fmtCount(num(all.disconnect_den))} response streams`}
            signals={["provider.stream_disconnects"]}
          />
        ) : (
          <Kpi
            title="Retries"
            value={fmtCount(extraAttempts)}
            detail="attempts beyond the first per logical call"
            signals={["provider.retries"]}
          />
        )}
        <Kpi
          title="Unknown outcomes"
          value={fmtCount(num(all.unknown))}
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
              ...rateSeriesColumns(series),
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
            rows={ttftDaily}
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
            rows={ttftDaily}
          />
        </Panel>
      </Section>
      <Section title="Where does provider time go?">
        <Panel
          title={
            throughputShown
              ? "Latency, time to first token, and throughput by model"
              : "Latency and time to first token by model"
          }
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
              ...(throughputShown
                ? [
                    {
                      key: "tps_p50",
                      label: "P50 tokens/s",
                      numeric: true,
                      render: (row: Row) =>
                        maybe(row.tps_p50) === null
                          ? "—"
                          : num(row.tps_p50).toFixed(0),
                    },
                  ]
                : []),
            ]}
            rows={latency}
          />
        </Panel>
      </Section>
      <Section
        title={
          conformanceShown
            ? "Errors, retries, and conformance"
            : "Errors and retries"
        }
      >
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
          <ViewLink view="friction">Open Friction</ViewLink>
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
                render: (row) => (
                  <RateValue
                    pct={maybe(row.mismatch_rate)}
                    n={num(row.with_response_model)}
                    digits={2}
                  />
                ),
              },
            ]}
            rows={data.conformance ?? []}
            empty="No calls report a served model in the selected range."
          />
        </Panel>
      </Section>
    </>
  );
}
