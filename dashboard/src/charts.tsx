import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import type { Row, Scalar } from "./contracts";

export type Series = { key: string; label: string; color: string };
type Format = (value: number | null) => string;

const HEIGHT = 170;
const PAD = { top: 10, right: 8, bottom: 22, left: 48 };

function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(Math.floor(element.getBoundingClientRect().width));
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.floor(entry!.contentRect.width)),
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

/** Rounds an axis maximum up to 1, 2, or 5 × 10ⁿ. */
function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 5, 10].find((candidate) => candidate * power >= value)!;
  return step * power;
}

export function Legend({ series }: { series: Series[] }) {
  if (series.length < 2) return null;
  return (
    <ul className="legend" aria-label="Legend">
      {series.map((entry) => (
        <li key={entry.key}>
          <i style={{ background: entry.color }} aria-hidden="true" />
          {entry.label}
        </li>
      ))}
    </ul>
  );
}

/** Column path with a 4px rounded data end and a square baseline. */
const column = (x: number, y: number, width: number, height: number) => {
  const r = Math.min(4, width / 2, height);
  return `M${x},${y + height}V${y + r}Q${x},${y} ${x + r},${y}H${x + width - r}Q${x + width},${y} ${x + width},${y + r}V${y + height}Z`;
};

/**
 * Daily trend as stacked columns (mutually exclusive parts of one total) or
 * lines (a rate per series). One y-axis. Hovering or focusing a day shows every
 * series value for that day.
 */
export function DailyChart({
  rows,
  series,
  kind,
  format,
  max,
}: {
  rows: Row[];
  series: Series[];
  kind: "stack" | "line";
  format: Format;
  max?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const [hover, setHover] = useState<number | null>(null);
  const value = (row: Row, key: string) => {
    const raw = row[key];
    return raw === null || raw === undefined || Array.isArray(raw)
      ? null
      : Number(raw);
  };
  const totals = rows.map((row) =>
    kind === "stack"
      ? series.reduce((sum, entry) => sum + (value(row, entry.key) ?? 0), 0)
      : Math.max(0, ...series.map((entry) => value(row, entry.key) ?? 0)),
  );
  const top = max ?? niceMax(Math.max(0, ...totals));
  const innerWidth = Math.max(0, width - PAD.left - PAD.right);
  const innerHeight = HEIGHT - PAD.top - PAD.bottom;
  const band = rows.length > 0 ? innerWidth / rows.length : 0;
  const barWidth = Math.max(2, Math.min(24, band * 0.7));
  const x = (index: number) => PAD.left + band * index + band / 2;
  const y = (amount: number) =>
    PAD.top + innerHeight - (Math.min(amount, top) / top) * innerHeight;
  const labelEvery = Math.max(
    1,
    Math.ceil(rows.length / Math.max(1, innerWidth / 70)),
  );
  return (
    <div className="chart" ref={ref}>
      <Legend series={series} />
      {width > 0 && (
        <svg
          width={width}
          height={HEIGHT}
          role="img"
          aria-label={`Daily ${series.map((entry) => entry.label).join(", ")}`}
          onMouseLeave={() => setHover(null)}
        >
          {[0, 0.5, 1].map((fraction) => (
            <g key={fraction}>
              <line
                className={fraction === 0 ? "baseline" : "gridline"}
                x1={PAD.left}
                x2={width - PAD.right}
                y1={y(top * fraction)}
                y2={y(top * fraction)}
              />
              <text x={PAD.left - 6} y={y(top * fraction) + 3} textAnchor="end">
                {format(top * fraction)}
              </text>
            </g>
          ))}
          {rows.map((row, index) =>
            index % labelEvery === 0 ? (
              <text
                key={`label-${String(row.day)}`}
                x={x(index)}
                y={HEIGHT - 6}
                textAnchor="middle"
              >
                {String(row.day).slice(5)}
              </text>
            ) : null,
          )}
          {kind === "stack"
            ? rows.map((row, index) => {
                let base = 0;
                const parts = series
                  .map((entry) => ({
                    entry,
                    amount: value(row, entry.key) ?? 0,
                  }))
                  .filter((part) => part.amount > 0);
                return parts.map((part, partIndex) => {
                  const y0 = y(base);
                  base += part.amount;
                  const y1 = y(base);
                  const gap = partIndex < parts.length - 1 ? 2 : 0;
                  const height = Math.max(0, y0 - y1 - gap);
                  const left = x(index) - barWidth / 2;
                  return partIndex === parts.length - 1 ? (
                    <path
                      key={`${index}-${part.entry.key}`}
                      d={column(left, y1, barWidth, height)}
                      fill={part.entry.color}
                    />
                  ) : (
                    <rect
                      key={`${index}-${part.entry.key}`}
                      x={left}
                      y={y1 + gap}
                      width={barWidth}
                      height={height}
                      fill={part.entry.color}
                    />
                  );
                });
              })
            : series.map((entry) => {
                const points = rows
                  .map((row, index) => [index, value(row, entry.key)] as const)
                  .filter(([, amount]) => amount !== null);
                return (
                  <g key={entry.key}>
                    <polyline
                      className="series-line"
                      stroke={entry.color}
                      points={points
                        .map(([index, amount]) => `${x(index)},${y(amount!)}`)
                        .join(" ")}
                    />
                    {points.length === 1 && (
                      <circle
                        cx={x(points[0]![0])}
                        cy={y(points[0]![1]!)}
                        r={4}
                        fill={entry.color}
                      />
                    )}
                    {hover !== null &&
                      points
                        .filter(([index]) => index === hover)
                        .map(([index, amount]) => (
                          <circle
                            key={index}
                            className="marker"
                            cx={x(index)}
                            cy={y(amount!)}
                            r={4}
                            fill={entry.color}
                          />
                        ))}
                  </g>
                );
              })}
          {hover !== null && kind === "line" && (
            <line
              className="crosshair"
              x1={x(hover)}
              x2={x(hover)}
              y1={PAD.top}
              y2={PAD.top + innerHeight}
            />
          )}
          {rows.map((row, index) => (
            <rect
              key={`hit-${String(row.day)}`}
              className="hit"
              x={PAD.left + band * index}
              y={PAD.top}
              width={band}
              height={innerHeight}
              tabIndex={0}
              aria-label={`${String(row.day)}: ${series
                .map(
                  (entry) => `${entry.label} ${format(value(row, entry.key))}`,
                )
                .join(", ")}`}
              onMouseEnter={() => setHover(index)}
              onFocus={() => setHover(index)}
              onBlur={() => setHover(null)}
            />
          ))}
        </svg>
      )}
      {hover !== null && rows[hover] && (
        <div
          className="tooltip"
          style={{
            left: Math.min(
              Math.max(x(hover) - 80, 0),
              Math.max(0, width - 170),
            ),
          }}
          role="status"
        >
          <b>{String(rows[hover]!.day)}</b>
          {series.map((entry) => (
            <span key={entry.key}>
              <i style={{ background: entry.color }} aria-hidden="true" />
              {entry.label}
              <em>{format(value(rows[hover]!, entry.key))}</em>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export type BarItem = {
  key: string;
  label: ReactNode;
  value: number | null;
  color: string;
  detail?: ReactNode;
};

/** Horizontal bars from one baseline with the value at the tip. */
export function BarList({
  items,
  format,
  max,
}: {
  items: BarItem[];
  format: Format;
  max?: number;
}) {
  const top = max ?? Math.max(0, ...items.map((item) => item.value ?? 0));
  return (
    <ul className="bar-list">
      {items.map((item) => (
        <li
          key={item.key}
          title={item.value === null ? "No value" : format(item.value)}
        >
          <span className="bar-label">{item.label}</span>
          <span className="bar-track">
            {item.value !== null && top > 0 && (
              <span
                className="bar"
                style={{
                  width: `${Math.max(0.5, (100 * item.value) / top)}%`,
                  background: item.color,
                }}
              />
            )}
            <span className="bar-value">{format(item.value)}</span>
          </span>
          {item.detail && <span className="bar-detail">{item.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

export type Column = {
  key: string;
  label: string;
  numeric?: boolean;
  render?: (row: Row) => ReactNode;
};

const cell = (value: Scalar | Scalar[] | undefined): ReactNode =>
  value === null || value === undefined
    ? "—"
    : Array.isArray(value)
      ? value.join(", ")
      : typeof value === "number"
        ? new Intl.NumberFormat("en", { maximumFractionDigits: 2 }).format(
            value,
          )
        : String(value);

export function DataTable({
  columns,
  rows,
  empty = "No rows for the selected range.",
}: {
  columns: Column[];
  rows: Row[];
  empty?: string;
}) {
  if (rows.length === 0) return <p className="empty">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                className={column.numeric ? "num" : undefined}
              >
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={column.numeric ? "num" : undefined}
                >
                  {column.render ? column.render(row) : cell(row[column.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The chart's data as a table, for exact values and non-visual reading. */
export function TableView({
  columns,
  rows,
}: {
  columns: Column[];
  rows: Row[];
}) {
  return (
    <details className="table-view">
      <summary>View as table</summary>
      <DataTable columns={columns} rows={rows} />
    </details>
  );
}
