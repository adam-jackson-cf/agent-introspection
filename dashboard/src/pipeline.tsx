import { isRecord } from "./type-guards";
import type {
  PipelineImplementation,
  PipelineMetric,
  PipelinePanel,
  PipelineReport,
  PipelineScalar,
  PipelineState,
} from "./contracts";
import { useState, type CSSProperties } from "react";

const pipelineStates: Record<PipelineState, true> = {
  Data: true,
  "No data": true,
  "Not applicable": true,
  Unavailable: true,
  "Integrity failure": true,
  "Query/system error": true,
};
const exactKeys = (value: Record<string, unknown>, keys: string[]) =>
  Object.keys(value).length === keys.length &&
  keys.every((key) => key in value);
const isUtc = (value: unknown): value is string =>
  typeof value === "string" &&
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/.test(value) &&
  !Number.isNaN(Date.parse(value));
const isScalar = (value: unknown): value is PipelineScalar =>
  value === null ||
  typeof value === "string" ||
  (typeof value === "number" && Number.isFinite(value));
const isCount = (value: unknown) =>
  value === null ||
  (typeof value === "number" && Number.isSafeInteger(value) && value >= 0);

function isMetric(value: unknown): value is PipelineMetric {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      "label",
      "value",
      "unit",
      "numerator",
      "denominator",
      "sampleCount",
    ])
  )
    return false;
  if (
    typeof value.numerator === "number" &&
    typeof value.denominator === "number" &&
    value.numerator > value.denominator
  )
    return false;
  return (
    typeof value.label === "string" &&
    isScalar(value.value) &&
    typeof value.unit === "string" &&
    isCount(value.numerator) &&
    isCount(value.denominator) &&
    isCount(value.sampleCount)
  );
}

export function isPipelinePanel(value: unknown): value is PipelinePanel {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      "state",
      "population",
      "timeBasis",
      "rangeOperator",
      "metrics",
      "columns",
      "rows",
      "series",
      "reasons",
      "provenance",
    ])
  )
    return false;
  const { columns } = value;
  if (!Array.isArray(columns)) return false;
  return (
    typeof value.state === "string" &&
    pipelineStates[value.state as PipelineState] === true &&
    ["population", "timeBasis", "rangeOperator"].every(
      (key) => typeof value[key] === "string",
    ) &&
    Array.isArray(value.metrics) &&
    value.metrics.every(isMetric) &&
    columns.every((item) => typeof item === "string") &&
    Array.isArray(value.rows) &&
    value.rows.every(
      (row) =>
        Array.isArray(row) &&
        row.length === columns.length &&
        row.every(isScalar),
    ) &&
    Array.isArray(value.series) &&
    value.series.every(
      (series) =>
        isRecord(series) &&
        exactKeys(series, ["name", "unit", "points"]) &&
        typeof series.name === "string" &&
        typeof series.unit === "string" &&
        Array.isArray(series.points) &&
        series.points.every(
          (point) =>
            isRecord(point) &&
            exactKeys(point, ["at", "value"]) &&
            isUtc(point.at) &&
            typeof point.value === "number" &&
            Number.isFinite(point.value),
        ),
    ) &&
    Array.isArray(value.reasons) &&
    value.reasons.every((reason) => typeof reason === "string") &&
    isRecord(value.provenance) &&
    Object.values(value.provenance).every((entry) => typeof entry === "string")
  );
}

function isSha256(value: unknown): value is string {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

export function isPipelineImplementation(
  value: unknown,
): value is PipelineImplementation {
  return (
    isRecord(value) &&
    exactKeys(value, ["calculationId", "calculationSha256", "deployments"]) &&
    value.calculationId === "agent-introspection.pipeline-dashboard" &&
    isSha256(value.calculationSha256) &&
    Array.isArray(value.deployments) &&
    value.deployments.every(
      (deployment) =>
        isRecord(deployment) &&
        exactKeys(deployment, [
          "fingerprint",
          "projectionId",
          "projectionSha256",
        ]) &&
        isSha256(deployment.fingerprint) &&
        deployment.projectionId ===
          "agent-introspection.pipeline-observations" &&
        isSha256(deployment.projectionSha256),
    )
  );
}

export function isPipelineReport(
  value: unknown,
  panelIds?: readonly string[],
): value is PipelineReport {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      "start",
      "end",
      "evaluatedAt",
      "implementation",
      "panels",
    ]) ||
    !isUtc(value.start) ||
    !isUtc(value.end) ||
    !isUtc(value.evaluatedAt) ||
    !isPipelineImplementation(value.implementation) ||
    !isRecord(value.panels)
  )
    return false;
  const { panels } = value;
  const ids = Object.keys(panels);
  return (
    Date.parse(value.start) < Date.parse(value.end) &&
    ids.every((id) => isPipelinePanel(panels[id])) &&
    (panelIds === undefined ||
      (ids.length === panelIds.length && panelIds.every((id) => id in panels)))
  );
}

const valueText = (value: PipelineScalar) =>
  value === null ? "—" : String(value);
const numberFormat = new Intl.NumberFormat("en", {
  maximumSignificantDigits: 3,
  notation: "compact",
});
const formatValue = (value: PipelineScalar, unit = ""): string => {
  if (value === null) return "—";
  if (typeof value === "string")
    return unit === "UTC" && isUtc(value) ? value.slice(11, 16) : value;
  if (unit === "ms" || unit === "seconds" || unit === "ms/1,000 rows") {
    const milliseconds = unit === "seconds" ? value * 1_000 : value;
    const scale =
      Math.abs(milliseconds) >= 86_400_000
        ? ([86_400_000, "d"] as const)
        : Math.abs(milliseconds) >= 3_600_000
          ? ([3_600_000, "h"] as const)
          : Math.abs(milliseconds) >= 60_000
            ? ([60_000, "m"] as const)
            : Math.abs(milliseconds) >= 1_000
              ? ([1_000, "s"] as const)
              : ([1, "ms"] as const);
    return `${numberFormat.format(milliseconds / scale[0])}${scale[1]}${unit === "ms/1,000 rows" ? " / 1k rows" : ""}`;
  }
  if (unit === "bytes") {
    const scale =
      Math.abs(value) >= 1024 ** 3
        ? ([1024 ** 3, "GiB"] as const)
        : Math.abs(value) >= 1024 ** 2
          ? ([1024 ** 2, "MiB"] as const)
          : Math.abs(value) >= 1024
            ? ([1024, "KiB"] as const)
            : ([1, "B"] as const);
    return `${numberFormat.format(value / scale[0])} ${scale[1]}`;
  }
  return `${numberFormat.format(value)}${unit === "percent" ? "%" : unit === "rows/s" ? " rows/s" : ""}`;
};
const tone = (metric: PipelineMetric) => {
  if (metric.value === null) return "";
  if (metric.unit === "state")
    return metric.value === "ok" || metric.value === "succeeded"
      ? "ok"
      : "warn";
  if (["pending", "pending events", "missed cadence"].includes(metric.label))
    return metric.value === 0 ? "ok" : "warn";
  if (
    [
      "failure",
      "failed during drain",
      "drain failure rate",
      "incidents",
    ].includes(metric.label)
  )
    return metric.value === 0 ? "ok" : "bad";
  return metric.label === "success" ? "ok" : "";
};
const snapshotLabels: Record<string, string> = {
  completion: "Completed",
  duration: "Duration",
  rows: "Rows processed",
  logs: "Logs",
  traces: "Traces",
  pending: "Outbox pending",
  "failed during drain": "Drain failed",
};

function MeasurementTable({
  panel,
  compact = false,
  raw = false,
}: {
  panel: PipelinePanel;
  compact?: boolean;
  raw?: boolean;
}) {
  const [requestedPage, setPage] = useState(0);
  const lastPage = Math.max(0, Math.ceil(panel.rows.length / 20) - 1);
  const page = Math.min(requestedPage, lastPage);
  const columns = compact
    ? ["producer", "signal", "current lag (ms)", "p95 (ms)", "sample count"]
    : panel.columns;
  const indexes = columns.map((column) => panel.columns.indexOf(column));
  const labels: Record<string, string> = {
    "current lag (ms)": "Current",
    "p95 (ms)": "p95",
    "sample count": "n",
  };
  const cellText = (value: PipelineScalar, column: string) => {
    if (raw) return valueText(value);
    if (
      column.endsWith("(ms)") ||
      column === "bucketed mean milliseconds per 1,000 rows"
    )
      return formatValue(
        value,
        column.endsWith("(ms)") ? "ms" : "ms/1,000 rows",
      );
    if (
      column === "completedAtNs" &&
      typeof value === "string" &&
      /^\d+$/.test(value)
    )
      return new Date(Number(BigInt(value) / 1_000_000n))
        .toISOString()
        .replace("T", " ");
    if (
      typeof value === "string" &&
      /^(?:[a-f0-9]{64}|[a-f0-9-]{36})$/.test(value)
    )
      return `${value.slice(0, 8)}…`;
    if (typeof value === "string" && isUtc(value))
      return value.replace("T", " ");
    const metric = panel.metrics.find(
      (metric) =>
        metric.label === column && typeof metric.value === typeof value,
    );
    if (metric) return formatValue(value, metric.unit);
    return typeof value === "number" ? formatValue(value) : valueText(value);
  };
  return (
    <div className="measurement-table-group">
      <div
        className={`measurement-table${compact ? " lag-table" : ""}`}
        tabIndex={0}
        role="region"
        aria-label={`${panel.population} table`}
      >
        <table>
          <thead>
            <tr>
              {columns.map((column, index) => (
                <th key={`${column}-${index}`}>
                  {compact ? (labels[column] ?? column) : column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {panel.rows.slice(page * 20, (page + 1) * 20).map((row, index) => (
              <tr key={index}>
                {indexes.map((cell, index) => {
                  const column = columns[index]!;
                  const value = row[cell] ?? null;
                  const state =
                    compact && column === "current lag (ms)"
                      ? row[panel.columns.indexOf("current state")]
                      : null;
                  return (
                    <td
                      key={`${column}-${index}`}
                      className={typeof value === "number" ? "num" : undefined}
                    >
                      <span
                        tabIndex={0}
                        title={`${column}: ${valueText(value)}${compact && column === "producer" ? ` · surface: ${valueText(row[panel.columns.indexOf("surface")] ?? null)}` : ""}`}
                      >
                        {cellText(value, column)}
                      </span>
                      {state && state !== "available" && (
                        <small className="warn">{state}</small>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {panel.rows.length > 20 && (
        <nav className="table-pages" aria-label={`${panel.population} pages`}>
          <button
            type="button"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            Previous
          </button>
          <span>
            Rows {page * 20 + 1}–{Math.min((page + 1) * 20, panel.rows.length)}{" "}
            of {panel.rows.length}
          </span>
          <button
            type="button"
            disabled={page === lastPage}
            onClick={() => setPage(page + 1)}
          >
            Next
          </button>
        </nav>
      )}
    </div>
  );
}

function MeasurementChart({
  series,
}: {
  series: PipelinePanel["series"][number];
}) {
  if (series.points.length === 0)
    return <p className="measurement-basis">{series.name}: no observations.</p>;
  const points = [...series.points].sort(
    (a, b) => Date.parse(a.at) - Date.parse(b.at),
  );
  const start = Date.parse(points[0]!.at);
  const end = Date.parse(points[points.length - 1]!.at);
  const low = Math.min(0, ...points.map((point) => point.value));
  const high = Math.max(0, ...points.map((point) => point.value));
  const coordinates = points.map((point) => ({
    ...point,
    x:
      end === start
        ? 300
        : 40 + ((Date.parse(point.at) - start) / (end - start)) * 550,
    y: high === low ? 65 : 110 - ((point.value - low) / (high - low)) * 100,
  }));
  return (
    <figure className="measurement-chart">
      <figcaption>
        <i />
        {series.name} <span>· {points.length} observations</span>
      </figcaption>
      <svg
        viewBox="0 0 600 145"
        role="img"
        aria-label={`${series.name} · ${series.unit} · ${points.length} observations`}
      >
        {[10, 60, 110].map((y) => (
          <line key={y} x1="40" x2="590" y1={y} y2={y} className="gridline" />
        ))}
        <text x="0" y="15">
          {formatValue(high, series.unit)}
        </text>
        <text x="0" y="110">
          {formatValue(low, series.unit)}
        </text>
        {points.length > 1 && (
          <polyline
            points={coordinates
              .map((point) => `${point.x},${point.y}`)
              .join(" ")}
            className="sparkline"
          />
        )}
        {coordinates.map((point, index) => (
          <circle key={index} cx={point.x} cy={point.y} r="2.5" tabIndex={0}>
            <title>
              {`${point.at} · ${valueText(point.value)} ${series.unit}`}
            </title>
          </circle>
        ))}
        <text x="40" y="138">
          {points[0]!.at.slice(11, 16)} UTC
        </text>
        <text x="590" y="138" textAnchor="end">
          {points[points.length - 1]!.at.slice(11, 16)} UTC
        </text>
      </svg>
    </figure>
  );
}
const stateCopy: Record<Exclude<PipelineState, "Data">, string> = {
  "No data": "No data for the declared population and range.",
  "Not applicable": "Not applicable to this population.",
  Unavailable: "Unavailable after the deployed collection boundary.",
  "Integrity failure": "Results withheld because integrity checks failed.",
  "Query/system error": "Query failed — outcome unknown.",
};

export function PipelineMeasurement({
  panel,
  widgetId,
  details = false,
}: {
  panel: PipelinePanel;
  widgetId: string;
  details?: boolean;
}) {
  if (panel.state !== "Data")
    return (
      <div className="measurement-state">
        <b>{panel.state}</b>
        <span>{panel.reasons.join(" ") || stateCopy[panel.state]}</span>
      </div>
    );
  if (details)
    return (
      <div className="measurement-detail">
        <p className="measurement-basis">
          {panel.population} · {panel.timeBasis} · {panel.rangeOperator}
        </p>
        {panel.metrics.length > 0 && (
          <dl className="exact-metrics">
            {panel.metrics.map((metric, index) => (
              <div key={index}>
                <dt>{metric.label}</dt>
                <dd>
                  {valueText(metric.value)} {metric.unit}
                  {(metric.numerator !== null ||
                    metric.denominator !== null) && (
                    <>
                      {" "}
                      · {metric.numerator ?? "—"} / {metric.denominator ?? "—"}
                    </>
                  )}
                  {metric.sampleCount !== null && (
                    <>
                      {" "}
                      · n={metric.sampleCount}
                      {metric.sampleCount < 30 ? " · Low sample" : ""}
                    </>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        )}
        {panel.rows.length > 0 && <MeasurementTable panel={panel} raw />}
        {panel.series.map((series, index) => (
          <details key={index}>
            <summary>{series.name} · exact observations</summary>
            <MeasurementTable
              panel={{
                ...panel,
                columns: ["UTC", series.unit],
                rows: series.points.map((point) => [point.at, point.value]),
              }}
              raw
            />
          </details>
        ))}
        <div className="measurement-provenance">
          <b>Production provenance</b>
          {Object.entries(panel.provenance).map(([key, value]) => (
            <span key={key}>
              {key}: {value}
            </span>
          ))}
        </div>
        {panel.reasons.map((reason) => (
          <p key={reason}>{reason}</p>
        ))}
      </div>
    );
  const snapshot = widgetId === "p1-snapshot";
  const outcomes = widgetId === "p2-outcomes";
  const metrics = snapshot
    ? Object.keys(snapshotLabels).flatMap((label) =>
        panel.metrics.filter((metric) => metric.label === label),
      )
    : panel.metrics;
  const percentages = outcomes
    ? panel.metrics.filter((metric) => metric.unit === "percent")
    : [];
  const terminal = snapshot
    ? panel.metrics.find((metric) => metric.label === "terminal status")
    : undefined;
  return (
    <div className="measurement">
      {metrics.length > 0 && !outcomes && (
        <div
          className={`metric-grid${panel.series.length ? " chart-metrics" : ""}`}
          style={
            { "--metric-columns": Math.min(metrics.length, 7) } as CSSProperties
          }
        >
          {metrics.map((metric, index) => (
            <div
              className="metric"
              key={`${metric.label}-${metric.unit}-${index}`}
              tabIndex={0}
              title={`${metric.label}: ${valueText(metric.value)} ${metric.unit}`}
            >
              <strong className={tone(metric)}>
                {formatValue(metric.value, metric.unit)}
              </strong>
              <span>
                {snapshot ? snapshotLabels[metric.label] : metric.label}
                {snapshot && metric.label === "completion" && terminal && (
                  <>
                    {" "}
                    ·{" "}
                    <span className={tone(terminal)}>
                      {valueText(terminal.value)}
                    </span>
                  </>
                )}
              </span>
              {metric.sampleCount !== null && (
                <small>
                  n={metric.sampleCount}
                  {metric.sampleCount < 30 ? " · Low sample" : ""}
                </small>
              )}
              {(metric.numerator !== null || metric.denominator !== null) && (
                <small>
                  {metric.numerator ?? "—"} / {metric.denominator ?? "—"}
                </small>
              )}
            </div>
          ))}
        </div>
      )}
      {percentages.length > 0 && (
        <div className="outcome-composition">
          <p className="measurement-basis">
            Terminal scans · denominator {percentages[0]!.denominator ?? "—"}
          </p>
          <div className="legend">
            {metrics.map((metric) => (
              <span
                key={metric.label}
                tabIndex={0}
                title={`${metric.label}: ${valueText(metric.value)} ${metric.unit} · ${metric.numerator ?? "—"} / ${metric.denominator ?? "—"}`}
              >
                <i
                  className={
                    metric.label === "success"
                      ? "ok"
                      : metric.label === "failure"
                        ? "bad"
                        : "warn"
                  }
                />
                {metric.label} · {formatValue(metric.value, metric.unit)}
                {metric.unit !== "percent" ? ` ${metric.unit}` : ""}
              </span>
            ))}
          </div>
          <div className="stack" aria-label="Terminal scan composition">
            {percentages.map((metric) =>
              typeof metric.value === "number" && metric.value > 0 ? (
                <span
                  key={metric.label}
                  className={metric.label === "success" ? "ok" : "bad"}
                  style={{ width: `${metric.value}%` }}
                  title={`${metric.label}: ${metric.value}%`}
                />
              ) : null,
            )}
          </div>
        </div>
      )}
      {panel.series.map((series, index) => (
        <MeasurementChart key={`${series.name}-${index}`} series={series} />
      ))}
      {panel.rows.length > 0 && !outcomes && (
        <MeasurementTable
          panel={panel}
          compact={widgetId === "p4-source-lag"}
        />
      )}
      {outcomes && panel.rows.length > 0 && (
        <MeasurementTable
          panel={{
            ...panel,
            columns: panel.columns.filter((column) => column !== "runtime"),
            rows: panel.rows.map((row) =>
              row.filter((_, index) => panel.columns[index] !== "runtime"),
            ),
          }}
        />
      )}
    </div>
  );
}
